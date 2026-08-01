"""Tests for short-lived gate and inbox retention policy."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import GateAnchor, create_gate
from agents_remember.controlplane.store import GateStore
from agents_remember.observer.paths import observer_logs_root
from agents_remember.observer.snapshots import read_agent_pickups, read_gates


class InteractionRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_an_open_gate_past_24h_leaves_the_projection_then_leaves_the_log(self) -> None:
        """Two claims that used to be one, because one of them changed processes.

        This test read ``read_gates`` -- the dashboard projection -- and then asserted the gate
        log itself was empty. That second assertion was true only because the projection tick
        PHYSICALLY REWROTE every gate log as a side effect of rendering, and that rewrite is
        what leaf 260731-EFA-L5 removed: it ran in the process that owns nothing here, and it
        is where 11.50% of appended gate snapshots were being lost.

        So "assert emptiness instead of absence" would not repair this line, it would restate
        the removed behaviour: after a projection the snapshot is legitimately still on disk.
        The physical-prune proof therefore moves to the owner that now performs it,
        ``GateStore.compact`` in the MCP process, and the projection gains the assertion its
        new contract needs and never had -- that reading changed nothing. Both halves are
        proven here rather than one, so the pair is stronger than the claim it replaces.
        """
        store = GateStore(observer_logs_root(self.root))
        store.append(
            create_gate(
                "agent-question",
                gate_id="G1",
                now="2026-06-01T10:00:00+00:00",
                anchor=GateAnchor(lifecycle_id="L1"),
            )
        )
        log = store.log_path("L1")
        before = log.read_bytes()
        now = datetime(2026, 6, 2, 10, 0, 1, tzinfo=UTC)

        gates = read_gates(self.root, now=now)

        # The aged-out gate leaves the rendered set...
        self.assertEqual(gates, [])
        # ...and the projection wrote nothing: same bytes, same record still readable.
        self.assertEqual(log.read_bytes(), before)
        self.assertEqual([record.id for record in store.read("L1")], ["G1"])

        removed = store.compact("L1", now=now)

        # Reclamation is the owner's, and it is what actually takes the record off disk.
        self.assertEqual(removed, 1)
        self.assertEqual(store.read("L1"), [])
        # Emptied, never unlinked (R5): an appender holding this path open must not be left
        # writing into an inode that no longer has a name.
        self.assertTrue(log.is_file())
        self.assertEqual(log.read_bytes(), b"")

    def test_read_agent_pickups_projects_pending_entries(self) -> None:
        store = OperatorInboxStore(observer_logs_root(self.root))
        store.append(
            create_operator_inbox_entry(
                InboxMessage(ask="Continue?", response="Approved.", gate_id="G1"),
                entry_id="fresh",
                now="2026-06-01T10:00:00+00:00",
                routing=InboxRouting(address=InboxAddress(lifecycle_id="L1", agent_id=None)),
                poster=InboxPoster(created_by="developer", created_via="dashboard"),
            )
        )
        store.append(
            create_operator_inbox_entry(
                InboxMessage(ask="Continue?", response="Approved.", gate_id="G2"),
                entry_id="stale",
                now="2026-06-01T09:50:00+00:00",
                routing=InboxRouting(address=InboxAddress(lifecycle_id="L2", agent_id=None)),
                poster=InboxPoster(created_by="developer", created_via="dashboard"),
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
