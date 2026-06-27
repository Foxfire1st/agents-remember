"""Tests for the dashboard terminal-session catalog."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    terminal_catalog_path,
)


def _entry(session_id: str, *, created_at: str = "2026-06-26T00:00:00Z") -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=f"Terminal {session_id}",
        kind="terminal",
        harness=None,
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("bash",),
        created_at=created_at,
        last_attached_at=created_at,
        status="running",
    )


class TerminalCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_terminal_catalog_path_is_under_dashboard_logs(self) -> None:
        self.assertEqual(
            terminal_catalog_path(self.tmp),
            self.tmp / "logs" / "dashboard" / "terminal-sessions.json",
        )

    def test_upsert_persists_json_rows_sorted_by_creation(self) -> None:
        self.catalog.upsert(_entry("b", created_at="2026-06-26T00:00:02Z"))
        self.catalog.upsert(_entry("a", created_at="2026-06-26T00:00:01Z"))

        self.assertEqual([entry.id for entry in self.catalog.list()], ["a", "b"])
        raw = json.loads(self.catalog.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema"], "ar-dashboard-terminal-sessions/v1")
        self.assertEqual([row["id"] for row in raw["sessions"]], ["a", "b"])

    def test_status_transitions_keep_exited_visible_and_filter_terminated(self) -> None:
        self.catalog.upsert(_entry("a"))
        self.catalog.upsert(_entry("b"))

        self.catalog.mark_exited("a")
        self.catalog.mark_terminated("b", "2026-06-26T00:01:00Z")

        self.assertEqual([entry.id for entry in self.catalog.list()], ["a"])
        self.assertEqual([entry.id for entry in self.catalog.list(include_terminated=True)], ["a", "b"])
        exited = self.catalog.get("a")
        terminated = self.catalog.get("b")
        assert exited is not None
        assert terminated is not None
        self.assertEqual(exited.status, "exited")
        self.assertEqual(terminated.terminated_at, "2026-06-26T00:01:00Z")

    def test_mark_attached_restores_running_status(self) -> None:
        self.catalog.upsert(_entry("a"))
        self.catalog.mark_exited("a")

        updated = self.catalog.mark_attached("a", "2026-06-26T00:02:00Z")

        assert updated is not None
        self.assertEqual(updated.status, "running")
        self.assertEqual(updated.last_attached_at, "2026-06-26T00:02:00Z")

    def test_mark_exited_does_not_downgrade_terminated_session(self) -> None:
        self.catalog.upsert(_entry("a"))
        self.catalog.mark_terminated("a", "2026-06-26T00:03:00Z")

        updated = self.catalog.mark_exited("a")

        assert updated is not None
        self.assertEqual(updated.status, "terminated")
        self.assertEqual(updated.terminated_at, "2026-06-26T00:03:00Z")
        self.assertEqual(self.catalog.list(), [])


if __name__ == "__main__":
    unittest.main()
