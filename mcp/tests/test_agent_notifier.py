"""Tests for the deterministic agent-notifier sweep (260707-HFX2-L2).

Predicate unit tests over store/pane fixtures (R6), plus one sweep integration test that seeds
drift across every predicate family and asserts the expected action set -- no model in the loop
anywhere: every fixture is a plain store write or a fake pane capturer.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import (
    UTC,
    datetime,
)
from pathlib import Path
from typing import cast

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.serving.agent_notifier import (
    evaluate_inbox_findings,
    evaluate_pane_findings,
)
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    TerminalSessionKind,
    TerminalSessionStatus,
)
from agents_remember.serving.terminal_paste import (
    PasteResult,
    TerminalPaster,
)

NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)


def _entry(
    session_id: str,
    *,
    kind: TerminalSessionKind = "harness",
    status: TerminalSessionStatus = "running",
    leaf_key: str | None = None,
) -> TerminalCatalogEntry:
    """A seat row. Turn state comes from the row's own ``with_turn_state``; anything else
    from ``replace(...)`` -- ``TerminalCatalogEntry`` already carries every field, so the
    builder supplies only what identifies the seat rather than mirroring the row's shape.
    """
    return TerminalCatalogEntry(
        id=session_id,
        label=f"Chat {session_id}",
        kind=kind,
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at="2026-07-08T00:00:00+00:00",
        last_attached_at="2026-07-08T00:00:00+00:00",
        status=status,
        leaf_key=leaf_key,
    )


class _FakeHost:
    """The minimal ``deliver_inbox_entry`` seam: every catalog session is reachable."""

    def has_session(self, _tmux_name: str) -> bool:
        return True

    def terminate(self, _sid: str, *, tmux_name: str | None = None) -> None:
        pass


def _fake_paster() -> TerminalPaster:
    """An already-log-confirmed delivery seam for agent-notifier orchestration tests."""

    class _AcceptedPaster:
        # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_agent_notifier.py:101).
        def paste(  # pragma: no cover
            self,
            _tmux_name: str,
            _text: str,
            *,
            submit: bool = False,
            **_kwargs: object,
        ) -> PasteResult:
            return PasteResult(delivered=True, submitted=submit)

    return cast(TerminalPaster, _AcceptedPaster())


class PanePredicateTests(unittest.TestCase):
    def test_mid_turn_pane_fires_a_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(_entry("s1"))
            findings = evaluate_pane_findings(catalog, pane_capturer=lambda _n: "esc to interrupt")
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].kind, "pane-signal")
            self.assertEqual(findings[0].detail, "mid-turn")
            self.assertEqual(findings[0].session_id, "s1")

    def test_normal_pane_fires_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(_entry("s1"))
            findings = evaluate_pane_findings(catalog, pane_capturer=lambda _n: "all clear")
            self.assertEqual(findings, [])

    def test_terminal_kind_rows_are_never_pane_classified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(_entry("s1", kind="terminal"))
            findings = evaluate_pane_findings(catalog, pane_capturer=lambda _n: "esc to interrupt")
            self.assertEqual(findings, [])


class InboxPredicateTests(unittest.TestCase):
    def test_pending_row_with_no_next_attempt_is_immediately_redeliverable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OperatorInboxStore(Path(tmp))
            entry = create_operator_inbox_entry(
                InboxMessage(ask="ask", response="resp"),
                entry_id="e1",
                now=NOW.isoformat(),
                routing=InboxRouting(address=InboxAddress(lifecycle_id=None, agent_id="s1")),
                poster=InboxPoster(created_by="system", created_via="cli"),
            )
            store.append(entry)
            findings = evaluate_inbox_findings(store, now=NOW)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].source_id, "e1")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
