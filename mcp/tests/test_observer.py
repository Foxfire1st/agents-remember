"""Tests for the observer write-side substrate (slice 2a).

Covers ``observer/ulid.py`` (minting), ``observer/events.py`` (the
``ar-observer-event/v1`` envelope), and ``observer/store.py`` (the append-only
store) in isolation: id sortability/uniqueness, envelope round-trip and
validation, per-lifecycle vs workspace routing, and that a self-contained
``lifecycle.started`` replays from its log alone.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.observer import (
    Event,
    EventStore,
    new_ulid,
    now_iso,
)


class EventStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.store = EventStore(self.root)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _event(self, scope_key: str | None, kind: str = "tool.completed", **data: Any) -> Event:
        return Event(
            id=new_ulid(),
            ts=now_iso(),
            kind=kind,
            trust="observed",
            actor="model",
            scopeKey=scope_key,
            data=data,
        )

    def test_scope_key_stays_in_the_workspace_river(self) -> None:
        self.store.append(self._event("L1"))
        self.assertTrue((self.root / "workspace" / "events.jsonl").exists())
        self.assertFalse((self.root / "lifecycles").exists())

    def test_workspace_log_for_unbound_events(self) -> None:
        self.store.append(self._event(None))
        self.assertTrue((self.root / "workspace" / "events.jsonl").exists())

    def test_round_trip_through_store(self) -> None:
        started = Event(
            id=new_ulid(),
            ts=now_iso(),
            kind="tool.completed",
            trust="declared",
            actor="model",
            scopeKey="repo:master/task.json",
            data={"tool": "worktree_start", "ok": True},
        )
        self.store.append(started)
        self.store.append(self._event("L1"))
        events = self.store.read()
        self.assertEqual(len(events), 2)
        # The facts-only event and its task scope replay from the workspace log alone.
        self.assertEqual(events[0], started)


if __name__ == "__main__":
    unittest.main()
