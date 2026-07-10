"""Tests for R4 hierarchical routing derivation (260707-HFX2-L1)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.signal_routing import (
    RoutedOwner,
    derive_signal_owner,
    derive_skip_level_owner,
    is_seat_dead,
    leaf_chain_has_progress,
)
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
        self._upsert(id="manager-1", lifecycle_id="L-manager", spawn_role="manager")
        self._upsert(
            id="worker-1",
            spawn_role="worker",
            spawned_by_session="manager-1",
            spawned_by_lifecycle="L-manager",
        )
        owner = derive_signal_owner(self.catalog, sender_agent_id="worker-1", message_kind="turn-report")
        self.assertEqual(owner, RoutedOwner(role="manager", agent_id="manager-1", lifecycle_id="L-manager"))

    def test_reviewer_signal_resolves_current_manager_instead_of_stale_binding(self) -> None:
        leaf_key = "repo-a/260707_master/leaf-9"
        self._upsert(
            id="manager-old",
            spawn_role="manager",
            status="terminated",
            leaf_key="repo-a/260707_master/manager-anchor",
        )
        self._upsert(
            id="manager-current",
            lifecycle_id="L-manager-current",
            spawn_role="manager",
            leaf_key="repo-a/260707_master/current-manager-anchor",
        )
        self._upsert(
            id="reviewer-1",
            spawn_role="reviewer",
            spawned_by_session="manager-old",
            leaf_key=leaf_key,
        )

        owner = derive_signal_owner(
            self.catalog,
            sender_agent_id="reviewer-1",
            message_kind="turn-report",
            leaf_key=leaf_key,
        )

        self.assertEqual(
            owner,
            RoutedOwner(
                role="manager",
                agent_id="manager-current",
                lifecycle_id="L-manager-current",
            ),
        )

    def test_stale_manager_binding_never_falls_directly_to_orchestrator(self) -> None:
        self._upsert(id="orchestrator-1", spawn_role="orchestrator")
        self._upsert(
            id="manager-old",
            spawn_role="manager",
            status="terminated",
            spawned_by_session="orchestrator-1",
        )
        self._upsert(
            id="worker-1",
            spawn_role="worker",
            spawned_by_session="manager-old",
            leaf_key="repo-a/260707_master/leaf-9",
        )

        owner = derive_signal_owner(
            self.catalog,
            sender_agent_id="worker-1",
            message_kind="escalation",
            leaf_key="repo-a/260707_master/leaf-9",
        )

        self.assertEqual(owner, RoutedOwner(role="manager"))

    def test_unbound_reviewer_completion_counts_as_leaf_chain_progress(self) -> None:
        leaf_key = "repo-a/260707_master/leaf-9"
        self._upsert(
            id="manager-current",
            spawn_role="manager",
            leaf_key="repo-a/260707_master/current-manager-anchor",
        )
        self._upsert(
            id="worker-1",
            spawn_role="worker",
            spawned_by_session="manager-current",
            leaf_key=leaf_key,
        )
        self._upsert(
            id="reviewer-1",
            spawn_role="reviewer",
            spawned_by_session="manager-current",
            leaf_key=None,
            replacement_for_leaf=leaf_key,
            status="landed",
            landed_at="2026-06-23T10:05:00+00:00",
        )

        self.assertTrue(
            leaf_chain_has_progress(
                self.catalog,
                leaf_key=leaf_key,
                subject_agent_id="worker-1",
                since=T1,
            )
        )

    def test_declared_unbound_replacement_counts_as_chain_progress(self) -> None:
        leaf_key = "repo-a/260707_master/leaf-9"
        production_cwd = Path("/workspace")
        self._upsert(
            id="manager-current",
            spawn_role="manager",
            leaf_key="repo-a/260707_master/manager-anchor",
        )
        self._upsert(
            id="worker-dead",
            spawn_role="worker",
            spawned_by_session="manager-current",
            leaf_key=leaf_key,
            cwd=production_cwd,
        )
        self._upsert(
            id="worker-replacement",
            spawn_role="worker",
            spawned_by_session="manager-current",
            leaf_key=None,
            cwd=production_cwd,
            replacement_for_leaf=leaf_key,
            status="running",
            turn_state="working",
        )

        self.assertTrue(
            leaf_chain_has_progress(
                self.catalog,
                leaf_key=leaf_key,
                subject_agent_id="worker-dead",
                since=T1,
            )
        )

    def test_unbound_worker_on_parallel_leaf_never_suppresses_this_leaf(self) -> None:
        leaf_key = "repo-a/260707_master/leaf-9"
        self._upsert(id="manager-current", spawn_role="manager")
        self._upsert(
            id="worker-dead",
            spawn_role="worker",
            spawned_by_session="manager-current",
            leaf_key=leaf_key,
            cwd=Path("/workspace"),
        )
        self._upsert(
            id="parallel-worker",
            spawn_role="worker",
            spawned_by_session="manager-current",
            leaf_key=None,
            cwd=Path("/workspace"),
            replacement_for_leaf="repo-a/260707_master/leaf-10",
            status="running",
            turn_state="working",
        )

        self.assertFalse(
            leaf_chain_has_progress(
                self.catalog,
                leaf_key=leaf_key,
                subject_agent_id="worker-dead",
                since=T1,
            )
        )

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


class SkipLevelOwnerTests(unittest.TestCase):
    """R2/R4 (260707-HFX2-L4): the two-hop owner's-owner walk, past dead intermediates."""

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

    def _chain(self, *, manager_status: str = "running", orchestrator_status: str = "running") -> None:
        self._upsert(id="orchestrator-1", spawn_role="orchestrator", status=orchestrator_status)
        self._upsert(
            id="manager-1",
            spawn_role="manager",
            spawned_by_session="orchestrator-1",
            spawned_by_lifecycle="L-orchestrator",
            status=manager_status,
        )
        self._upsert(
            id="worker-1",
            spawn_role="worker",
            spawned_by_session="manager-1",
            spawned_by_lifecycle="L-manager",
        )

    def test_live_chain_lands_on_the_owners_owner(self) -> None:
        self._chain()
        owner = derive_skip_level_owner(self.catalog, sender_agent_id="worker-1", message_kind="escalation")
        self.assertEqual(owner.agent_id, "orchestrator-1")
        self.assertEqual(owner.role, "orchestrator")

    def test_dead_intermediate_manager_is_skipped_not_addressed(self) -> None:
        self._chain(manager_status="terminated")
        owner = derive_skip_level_owner(self.catalog, sender_agent_id="worker-1", message_kind="escalation")
        # Still lands on the orchestrator -- the dead manager is never itself the rung-2 target.
        self.assertEqual(owner.agent_id, "orchestrator-1")

    def test_dead_grandparent_walks_further_but_hits_the_hierarchy_ceiling(self) -> None:
        # The orchestrator has no owner-role mapping of its own -- dead or alive, the walk cannot
        # climb past it (the developer is the top rung, never modeled in catalog provenance).
        self._chain(orchestrator_status="terminated")
        owner = derive_skip_level_owner(self.catalog, sender_agent_id="worker-1", message_kind="escalation")
        self.assertEqual(owner, RoutedOwner())

    def test_unknown_sender_derives_no_skip_level_route(self) -> None:
        owner = derive_skip_level_owner(self.catalog, sender_agent_id="ghost", message_kind="escalation")
        self.assertEqual(owner, RoutedOwner())

    def test_no_second_hop_session_still_resolves_a_role_only_address(self) -> None:
        # A manager spawned with no recorded spawner session id -- the ROLE the mapping resolves
        # to (orchestrator) is still known even though there is no live session id for it; the
        # walk returns that role-only address rather than fabricating or discarding it.
        self._upsert(id="manager-1", spawn_role="manager", spawned_by_session=None)
        self._upsert(
            id="worker-1",
            spawn_role="worker",
            spawned_by_session="manager-1",
            spawned_by_lifecycle="L-manager",
        )
        owner = derive_skip_level_owner(self.catalog, sender_agent_id="worker-1", message_kind="escalation")
        self.assertEqual(owner, RoutedOwner(role="orchestrator"))


class IsSeatDeadTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.catalog = TerminalCatalog(Path(tmp.name) / "terminal-sessions.json")

    def test_unknown_agent_is_dead(self) -> None:
        self.assertTrue(is_seat_dead(self.catalog, "ghost"))

    def test_none_agent_is_dead(self) -> None:
        self.assertTrue(is_seat_dead(self.catalog, None))

    def test_running_agent_is_not_dead(self) -> None:
        self.catalog.upsert(
            TerminalCatalogEntry(
                id="a",
                label="A",
                kind="harness",
                harness="claude",
                lifecycle_id=None,
                cwd=Path("/tmp"),
                tmux_name="ar-a",
                command=("claude",),
                created_at=T1,
                last_attached_at=T1,
                status="running",
            )
        )
        self.assertFalse(is_seat_dead(self.catalog, "a"))


if __name__ == "__main__":
    unittest.main()
