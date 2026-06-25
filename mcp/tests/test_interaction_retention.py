"""Tests for short-lived gate and inbox retention policy."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.controlplane.operator_inbox_records import create_operator_inbox_entry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import create_gate
from agents_remember.controlplane.store import GateStore
from agents_remember.observer.paths import observer_logs_root
from agents_remember.observer.snapshots import read_agent_pickups, read_gates


class InteractionRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_read_gates_prunes_open_gates_after_24h(self) -> None:
        store = GateStore(observer_logs_root(self.root))
        store.append(
            create_gate(
                kind="agent-question",
                lifecycle_id="L1",
                gate_id="G1",
                now="2026-06-01T10:00:00+00:00",
            )
        )

        gates = read_gates(
            self.root,
            now=datetime(2026, 6, 2, 10, 0, 1, tzinfo=UTC),
        )

        self.assertEqual(gates, [])
        self.assertEqual(store.read("L1"), [])

    def test_read_agent_pickups_projects_pending_entries(self) -> None:
        store = OperatorInboxStore(observer_logs_root(self.root))
        store.append(
            create_operator_inbox_entry(
                entry_id="fresh",
                now="2026-06-01T10:00:00+00:00",
                lifecycle_id="L1",
                agent_id=None,
                gate_id="G1",
                ask="Continue?",
                response="Approved.",
                created_by="developer",
                created_via="dashboard",
            )
        )
        store.append(
            create_operator_inbox_entry(
                entry_id="stale",
                now="2026-06-01T09:50:00+00:00",
                lifecycle_id="L2",
                agent_id=None,
                gate_id="G2",
                ask="Continue?",
                response="Approved.",
                created_by="developer",
                created_via="dashboard",
            )
        )

        pickups = read_agent_pickups(
            self.root,
            now=datetime(2026, 6, 1, 10, 0, 1, tzinfo=UTC),
        )

        by_entry = {pickup.entryId: pickup for pickup in pickups}
        self.assertEqual(by_entry["fresh"].state, "waiting-for-agent")
        self.assertEqual(by_entry["stale"].state, "check-chat")
        self.assertEqual(by_entry["fresh"].gateId, "G1")


if __name__ == "__main__":
    unittest.main()
