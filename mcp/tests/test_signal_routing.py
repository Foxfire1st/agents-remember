"""Tests for R4 hierarchical routing derivation (260707-HFX2-L1)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.signal_routing import RoutedOwner, derive_signal_owner
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry

T1 = "2026-06-23T10:00:00+00:00"


class SignalRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.catalog = TerminalCatalog(Path(tmp.name) / "terminal-sessions.json")

    def _upsert(self, **overrides: object) -> None:
        base: dict[str, object] = {
            "id": "entry",
            "label": "Entry",
            "kind": "harness",
            "harness": "claude",
            "lifecycle_id": "L1",
            "cwd": Path("/tmp"),
            "tmux_name": "ar-entry",
            "command": ("claude",),
            "created_at": T1,
            "last_attached_at": T1,
            "status": "running",
        }
        base.update(overrides)
        self.catalog.upsert(TerminalCatalogEntry(**base))  # type: ignore[arg-type]

    def test_worker_signal_routes_to_its_manager(self) -> None:
        self._upsert(
            id="worker-1",
            spawn_role="worker",
            spawned_by_session="manager-1",
            spawned_by_lifecycle="L-manager",
        )
        owner = derive_signal_owner(self.catalog, sender_agent_id="worker-1", message_kind="turn-report")
        self.assertEqual(owner, RoutedOwner(role="manager", agent_id="manager-1", lifecycle_id="L-manager"))

    def test_manager_signal_routes_to_orchestrator(self) -> None:
        self._upsert(
            id="manager-1",
            spawn_role="manager",
            spawned_by_session="orchestrator-1",
            spawned_by_lifecycle="L-orchestrator",
        )
        owner = derive_signal_owner(self.catalog, sender_agent_id="manager-1", message_kind="escalation")
        self.assertEqual(
            owner, RoutedOwner(role="orchestrator", agent_id="orchestrator-1", lifecycle_id="L-orchestrator")
        )

    def test_no_layer_is_addressed_its_grandchildrens_noise(self) -> None:
        # A worker's signal never routes past its manager to the orchestrator, even though the
        # manager's own spawned_by_session IS the orchestrator -- routing reads the SENDER's own
        # provenance, one hop, never chases the chain further.
        self._upsert(
            id="manager-1",
            spawn_role="manager",
            spawned_by_session="orchestrator-1",
            spawned_by_lifecycle="L-orchestrator",
        )
        self._upsert(
            id="worker-1",
            spawn_role="worker",
            spawned_by_session="manager-1",
            spawned_by_lifecycle="L-manager",
        )
        owner = derive_signal_owner(self.catalog, sender_agent_id="worker-1", message_kind="turn-report")
        self.assertEqual(owner.agent_id, "manager-1")
        self.assertNotEqual(owner.agent_id, "orchestrator-1")

    def test_decision_item_routes_to_architect_regardless_of_provenance(self) -> None:
        self._upsert(id="orchestrator-1", spawn_role="orchestrator")
        owner = derive_signal_owner(
            self.catalog, sender_agent_id="orchestrator-1", message_kind="decision-item"
        )
        self.assertEqual(owner, RoutedOwner(role="architect"))

    def test_unknown_sender_derives_no_route(self) -> None:
        owner = derive_signal_owner(self.catalog, sender_agent_id="ghost", message_kind="message")
        self.assertEqual(owner, RoutedOwner())

    def test_sender_with_no_owner_role_mapping_derives_no_route(self) -> None:
        # An orchestrator's OWN signal (e.g. to the developer/architect) has no further "owner" --
        # the caller's explicit recipient_role stands.
        self._upsert(id="orchestrator-1", spawn_role="orchestrator", spawned_by_session=None)
        owner = derive_signal_owner(self.catalog, sender_agent_id="orchestrator-1", message_kind="message")
        self.assertEqual(owner, RoutedOwner())

    def test_no_sender_agent_id_derives_no_route(self) -> None:
        owner = derive_signal_owner(self.catalog, sender_agent_id=None, message_kind="message")
        self.assertEqual(owner, RoutedOwner())


if __name__ == "__main__":
    unittest.main()
