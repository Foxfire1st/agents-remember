"""LOCR-R24 guards: the retired expectation kinds stay parse-only.

``turn-report-by`` and ``ack-by`` remain members of the ``ExpectationKind`` Literal so historical
``ar-expectation-row/v2`` records still validate, and for nothing else. They carry no writer, no
evaluator, no notifier action, no deadline behavior, and no settings/SLA membership: the settings
vocabulary lists only the active, settings-settable kinds, and the Literal is the deliberate
parse-only superset.

Removing the values instead requires a separately approved durable-row migration and is explicitly
outside LOCR-R24, so these guards exist to make an accidental resurrection -- or a silent removal
-- loud rather than a schema change nobody reviewed.
"""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast, get_args
from unittest import mock

import agents_remember
from agents_remember.controlplane.agent_notifier_signals import AgentNotifierSignalCooldownStore
from agents_remember.controlplane.expectation_rows import (
    EXPECTATION_RETENTION_SECONDS,
    EXPECTATION_ROW_SCHEMA,
    ExpectationKind,
    ExpectationRow,
    ExpectationRowStore,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.kernel._agentic_settings_core import (
    DEFAULT_EXPECTATION_SLA_SECONDS,
    KNOWN_EXPECTATION_KINDS,
    ExpectationSettings,
)
from agents_remember.kernel.agentic_settings import (
    AgenticSettingsError,
    agentic_settings_path,
    load_agentic_settings,
)
from agents_remember.observer.store import EventStore
from agents_remember.serving.agent_notifier import AgentNotifierContext, run_agent_notifier_sweep
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.agent_notifier_models import ActionKind, FindingKind
from agents_remember.serving.inbox_reclamation import TmuxSessionNameSnapshot
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_paste import TerminalPaster

PACKAGE_ROOT = Path(agents_remember.__file__).resolve().parent

# The two values the packet retires: parsable durable records, zero runtime behavior.
RETIRED_EXPECTATION_KINDS = ("turn-report-by", "ack-by")
# The kinds that keep their dispatch/deadline behavior and stay settings-settable.
ACTIVE_EXPECTATION_KINDS = frozenset({"briefed-by", "verdict-by"})

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)

# The address every stored row of that era carries. The last writer of these rows
# (``mcp/tools/terminal.py::_write_spawn_expectation_rows``, removed at 827b4501) addressed each one
# to an agent, a lifecycle, a leaf document and a seat role, and named the kind in ``note``.
LEGACY_LEAF_KEY = (
    "260831_lifecycle-owned-completion-relay/24_legacy-expectation-kinds-parse-only.json"
)
LEGACY_SUBJECT_AGENT_ID = "legacy-worker-session"


def _legacy_row_json(
    *,
    row_id: str,
    kind: str,
    ts: str,
    state: str = "pending",
    subject_agent_id: str = LEGACY_SUBJECT_AGENT_ID,
) -> str:
    """One durable ``ar-expectation-row/v2`` line exactly as a historical log carries it.

    "Exactly" includes the address. A stored row was written to a named agent, lifecycle, leaf
    document and seat role, with ``note=f"{kind}: {leaf}"``, and an evaluator may deliver only to
    rows that carry such an address -- so a fixture of unaddressed rows cannot see that evaluator.
    """

    payload: dict[str, object] = {
        "schema": EXPECTATION_ROW_SCHEMA,
        "id": row_id,
        "ts": ts,
        "kind": kind,
        "state": state,
        "createdAt": ts,
        "dueAt": (datetime.fromisoformat(ts) + timedelta(hours=1)).isoformat(),
        "sourceId": f"source-{row_id}",
        "subjectAgentId": subject_agent_id,
        "subjectLifecycleId": f"lifecycle-{subject_agent_id}",
        "taskDocumentRef": {"repository": "agents-remember", "path": LEGACY_LEAF_KEY},
        "seatRole": "worker",
        "note": f"{kind}: {LEGACY_LEAF_KEY}",
    }
    if state == "missed":
        payload["missedAt"] = ts
    return json.dumps(payload)


def _write_expectation_log(observer_root: Path, lines: list[str]) -> None:
    path = ExpectationRowStore(observer_root).log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _retired_kind_literals_in_source(source: str) -> list[str]:
    """Retired-kind string constants that are real code, never a docstring.

    A shipped module may legitimately *explain* the retirement in prose -- ``dispatch_brief`` does --
    and prose is not a writer, so a constant that IS a docstring is excluded. The synthetic control
    below is what pins that exclusion rather than merely exercising it: its docstrings are the bare
    kind names, so an exclusion that stopped working would return each of them a second time.

    Stated limit, measured in the same control: this scan compares whole string constants. A kind
    assembled at runtime (``"turn-report" + "-by"``) is a different shape and is invisible here, and
    no claim in this module says otherwise; the reviewed writer inventory covers that shape.
    """

    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in RETIRED_EXPECTATION_KINDS
        and id(node) not in docstrings
    ]


def _shipped_retired_kind_literals() -> list[str]:
    """``<relative path>:<literal>`` for every retired-kind literal in the shipped runtime."""

    found: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        for literal in _retired_kind_literals_in_source(path.read_text(encoding="utf-8")):
            found.append(f"{path.relative_to(PACKAGE_ROOT)}:{literal}")
    return found


class LegacyExpectationKindParseTests(unittest.TestCase):
    """Clause 1: a stored v2 row of either retired kind still reads back."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.observer_root = Path(self.tmp.name) / "logs" / "observer"

    def test_a_stored_v2_row_of_either_retired_kind_still_parses(self) -> None:
        stamps = [NOW.isoformat(), (NOW - timedelta(days=30)).isoformat()]
        _write_expectation_log(
            self.observer_root,
            [
                _legacy_row_json(row_id=f"legacy-{kind}", kind=kind, ts=stamp)
                for kind, stamp in zip(RETIRED_EXPECTATION_KINDS, stamps, strict=True)
            ],
        )
        store = ExpectationRowStore(self.observer_root)

        rows = store.read()
        expected_ids = sorted(f"legacy-{kind}" for kind in RETIRED_EXPECTATION_KINDS)
        self.assertEqual(sorted(row.id for row in rows), expected_ids)
        self.assertEqual(sorted(row.kind for row in rows), sorted(RETIRED_EXPECTATION_KINDS))
        self.assertEqual(sorted(row.state for row in rows), ["pending", "pending"])
        # The tolerant projection read answers to the same model, so it parses them too.
        self.assertEqual(
            sorted(row.kind for row in store.read_for_projection()),
            sorted(RETIRED_EXPECTATION_KINDS),
        )
        self.assertEqual(sorted(store.current()), expected_ids)
        # The row model itself accepts each retired kind, so a legacy row validates even when it
        # is handed straight to the record rather than through the store's read.
        direct = [
            ExpectationRow.model_validate_json(
                _legacy_row_json(row_id=f"direct-{kind}", kind=kind, ts=NOW.isoformat())
            )
            for kind in RETIRED_EXPECTATION_KINDS
        ]
        self.assertEqual(sorted(row.kind for row in direct), sorted(RETIRED_EXPECTATION_KINDS))


class LegacyExpectationKindVocabularyTests(unittest.TestCase):
    """Clause 2: the settings vocabulary and the default SLAs exclude both retired kinds."""

    def test_the_settings_vocabulary_and_default_slas_exclude_both_retired_kinds(self) -> None:
        self.assertEqual(KNOWN_EXPECTATION_KINDS, ACTIVE_EXPECTATION_KINDS)
        self.assertEqual(set(DEFAULT_EXPECTATION_SLA_SECONDS), ACTIVE_EXPECTATION_KINDS)
        self.assertEqual(
            set(get_args(ExpectationKind)),
            ACTIVE_EXPECTATION_KINDS | set(RETIRED_EXPECTATION_KINDS),
        )
        for retired in RETIRED_EXPECTATION_KINDS:
            self.assertNotIn(retired, KNOWN_EXPECTATION_KINDS)
            self.assertNotIn(retired, DEFAULT_EXPECTATION_SLA_SECONDS)

    def test_an_sla_lookup_for_a_retired_kind_has_no_default_to_fall_back_on(self) -> None:
        settings = ExpectationSettings()
        # Positive control: the lookup resolves for an active kind, so the failures below are
        # about the retired kinds and not about a broken accessor.
        self.assertEqual(
            settings.sla_for("briefed-by"), DEFAULT_EXPECTATION_SLA_SECONDS["briefed-by"]
        )
        for retired in RETIRED_EXPECTATION_KINDS:
            with self.assertRaises(KeyError):
                settings.sla_for(retired)

    def test_settings_refuse_an_sla_default_for_a_retired_kind(self) -> None:
        for retired in RETIRED_EXPECTATION_KINDS:
            root = Path(self._temp_root()) / "ar-coordination"
            self._write_settings(root, {retired: 60})
            with self.assertRaises(AgenticSettingsError) as caught:
                load_agentic_settings(root)
            self.assertIn(retired, str(caught.exception))
            self.assertIn("not a known expectation kind", str(caught.exception))
            # Positive control: the same file shape loads once the key is an active kind.
            self._write_settings(root, {"briefed-by": 90})
            self.assertEqual(load_agentic_settings(root).expectations.sla_for("briefed-by"), 90.0)

    def _temp_root(self) -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name

    @staticmethod
    def _write_settings(root: Path, defaults: dict[str, float]) -> None:
        path = agentic_settings_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"orchestration": {"expectations": {"defaults": defaults}}}),
            encoding="utf-8",
        )


class LegacyExpectationKindWriterGuardTests(unittest.TestCase):
    """Clauses 4-5 (structural half): no shipped code names a retired kind but the Literal."""

    def test_the_literal_sweep_sees_code_and_ignores_docstring_prose(self) -> None:
        # The docstrings are the BARE kind names, not sentences that merely mention one: a
        # whole-sentence docstring passes with the exclusion deleted, and so pins nothing.
        source = (
            '"""ack-by"""\n'
            "\n"
            "\n"
            "class Holder:\n"
            '    """turn-report-by"""\n'
            "\n"
            '    KINDS = ("ack-by", "turn-report-by")\n'
        )
        self.assertEqual(_retired_kind_literals_in_source(source), ["ack-by", "turn-report-by"])
        # The limit named in the helper's docstring, measured instead of asserted in prose: the
        # same writer with a concatenated kind is a BinOp and this scan reports nothing.
        self.assertEqual(
            _retired_kind_literals_in_source('KINDS = ("turn-report" + "-by",)\n'),
            [],
        )

    def test_no_shipped_module_writes_or_evaluates_a_retired_kind_literal(self) -> None:
        self.assertEqual(
            sorted(_shipped_retired_kind_literals()),
            sorted(
                f"controlplane/expectation_rows.py:{kind}" for kind in RETIRED_EXPECTATION_KINDS
            ),
        )

    def test_the_notifier_finding_and_action_vocabularies_stay_free_of_retired_kinds(self) -> None:
        self.assertEqual(set(get_args(FindingKind)) & set(RETIRED_EXPECTATION_KINDS), set())
        self.assertEqual(set(get_args(ActionKind)) & set(RETIRED_EXPECTATION_KINDS), set())


class LegacyExpectationKindNotifierTests(unittest.TestCase):
    """Clause 5 (behavioral half): one real sweep neither marks, writes, nor delivers a row."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "ar-coordination"
        self.observer_root = self.root / "logs" / "observer"
        self.root.mkdir(parents=True)

    def _context(self) -> AgentNotifierContext:
        return AgentNotifierContext(
            catalog=TerminalCatalog(Path(self.tmp.name) / "catalog.json"),
            host=cast(TerminalHost, mock.Mock()),
            paster=cast(TerminalPaster, mock.Mock()),
            inbox_store=OperatorInboxStore(self.observer_root),
            expectation_store=ExpectationRowStore(self.observer_root),
            signal_cooldown_store=AgentNotifierSignalCooldownStore(self.observer_root),
            event_store=EventStore(self.observer_root),
            heartbeat_store=AgentNotifierHeartbeatStore(self.observer_root),
            coordination_root=self.root,
            stale_seat_seconds=60.0,
            redeliver_rate_limit_seconds=900.0,
            tmux_name_snapshotter=lambda: TmuxSessionNameSnapshot(frozenset(), "tmux-no-server"),
        )

    def test_the_sweep_neither_marks_nor_writes_a_retired_row(self) -> None:
        pending_ts = (NOW - timedelta(hours=6)).isoformat()
        stale_ts = (NOW - timedelta(seconds=EXPECTATION_RETENTION_SECONDS * 4)).isoformat()
        _write_expectation_log(
            self.observer_root,
            [
                # Overdue and still pending: no deadline behavior may touch it.
                _legacy_row_json(row_id="legacy-pending", kind="turn-report-by", ts=pending_ts),
                # Terminal and past the retention window: the sweep's own compaction reclaims it,
                # which is what proves the sweep really read this log rather than ignoring it.
                _legacy_row_json(
                    row_id="legacy-terminal", kind="ack-by", ts=stale_ts, state="missed"
                ),
            ],
        )
        store = ExpectationRowStore(self.observer_root)
        self.assertEqual(len(store.read()), 2)
        # Delivery is the third prohibited verb of clause 5, and it is visible only in the
        # operator inbox: an evaluator that delivers "an expectation deadline passed" leaves this
        # expectation log exactly as it found it, so the assertions below cannot see it.
        inbox_store = OperatorInboxStore(self.observer_root)
        inbox_before = inbox_store.current()
        self.assertEqual(inbox_before, {})

        result = run_agent_notifier_sweep(self._context(), now=NOW)

        # Both rows in the log are retired-kind rows addressed to a seat, so an inbox row here
        # would be the retired relay delivering again. Read folded AND unfolded: a delivered row
        # that a later snapshot superseded would still leave a snapshot in the log.
        self.assertEqual(inbox_store.current(), inbox_before)
        self.assertEqual(inbox_store.read(), [])
        rows = store.read()
        self.assertEqual([row.id for row in rows], ["legacy-pending"])
        self.assertEqual(rows[0].kind, "turn-report-by")
        self.assertEqual(rows[0].state, "pending")
        self.assertEqual(rows[0].dueAt, (NOW - timedelta(hours=5)).isoformat())
        rendered = json.dumps(
            {
                "findings": [asdict(finding) for finding in result.findings],
                "actions": [asdict(action) for action in result.actions],
            },
            default=str,
        )
        for retired in RETIRED_EXPECTATION_KINDS:
            self.assertNotIn(retired, rendered)
        self.assertNotIn("legacy-pending", rendered)
        self.assertNotIn("legacy-terminal", rendered)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
