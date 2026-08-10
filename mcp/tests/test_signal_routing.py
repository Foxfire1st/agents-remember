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
    derive_leaf_manager_owner,
    derive_signal_owner,
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
        owner = derive_signal_owner(
            self.catalog, sender_agent_id="worker-1", message_kind="turn-report"
        )
        self.assertEqual(
            owner, RoutedOwner(role="manager", agent_id="manager-1", lifecycle_id="L-manager")
        )

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
        owner = derive_signal_owner(
            self.catalog, sender_agent_id="manager-1", message_kind="escalation"
        )
        self.assertEqual(
            owner,
            RoutedOwner(
                role="orchestrator", agent_id="orchestrator-1", lifecycle_id="L-orchestrator"
            ),
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
        owner = derive_signal_owner(
            self.catalog, sender_agent_id="worker-1", message_kind="turn-report"
        )
        self.assertEqual(owner.agent_id, "manager-1")
        self.assertNotEqual(owner.agent_id, "orchestrator-1")

    def test_decision_item_routes_to_its_sprint_architect_with_an_exact_address(self) -> None:
        leaf_key = "repo-a/sprint-a/leaf-1"
        self._upsert(id="orchestrator-1", spawn_role="orchestrator", leaf_key=leaf_key)
        self._upsert(
            id="architect-a",
            spawn_role="architect",
            seat_role="architect",
            spawn_repo="repo-a",
            spawn_sprint="sprint-a",
        )
        owner = derive_signal_owner(
            self.catalog, sender_agent_id="orchestrator-1", message_kind="decision-item"
        )
        self.assertEqual(
            owner, RoutedOwner(role="architect", agent_id="architect-a", lifecycle_id="L1")
        )

    def test_unknown_sender_derives_no_route(self) -> None:
        owner = derive_signal_owner(self.catalog, sender_agent_id="ghost", message_kind="message")
        self.assertEqual(owner, RoutedOwner())

    def test_sender_with_no_owner_role_mapping_derives_no_route(self) -> None:
        # An orchestrator's OWN signal (e.g. to the developer/architect) has no further "owner" --
        # the caller's explicit recipient_role stands.
        self._upsert(id="orchestrator-1", spawn_role="orchestrator", spawned_by_session=None)
        owner = derive_signal_owner(
            self.catalog, sender_agent_id="orchestrator-1", message_kind="message"
        )
        self.assertEqual(owner, RoutedOwner())

    def test_no_sender_agent_id_derives_no_route(self) -> None:
        owner = derive_signal_owner(self.catalog, sender_agent_id=None, message_kind="message")
        self.assertEqual(owner, RoutedOwner())

    def test_pair_bound_worker_credit_and_manager_address_hold_at_two_fleet_sizes(self) -> None:
        leaf_key = "repo-a/260707_master/leaf-9"
        for fleet_size in (3, 30):
            with self.subTest(fleet_size=fleet_size), tempfile.TemporaryDirectory() as tmp:
                catalog = TerminalCatalog(Path(tmp) / "terminal-sessions.json")

                def add(
                    session_id: str,
                    _catalog: TerminalCatalog = catalog,
                    **overrides: object,
                ) -> None:
                    base: dict[str, object] = {
                        "id": session_id,
                        "label": session_id,
                        "kind": "harness",
                        "harness": "claude",
                        "lifecycle_id": None,
                        "cwd": Path("/tmp"),
                        "tmux_name": f"ar-{session_id}",
                        "command": ("claude",),
                        "created_at": T1,
                        "last_attached_at": T1,
                        "status": "running",
                    }
                    base.update(overrides)
                    _catalog.upsert(TerminalCatalogEntry(**base))  # type: ignore[arg-type]

                add("manager", leaf_key=leaf_key, seat_role="manager")
                add(
                    "worker",
                    leaf_key=leaf_key,
                    seat_role="worker",
                    spawn_role="worker",
                    spawned_by_session="manager",
                    turn_state="working",
                )
                add(
                    "reviewer",
                    leaf_key=leaf_key,
                    seat_role="reviewer",
                    spawn_role="reviewer",
                    spawned_by_session="manager",
                )
                for index in range(fleet_size - 3):
                    add(
                        f"filler-{index}",
                        leaf_key=f"repo-a/other/leaf-{index}",
                        seat_role=f"role-{index}",
                    )

                self.assertTrue(
                    leaf_chain_has_progress(
                        catalog,
                        leaf_key=leaf_key,
                        subject_agent_id="reviewer",
                        since=T1,
                    )
                )
                self.assertEqual(
                    derive_leaf_manager_owner(
                        catalog,
                        sender_agent_id="worker",
                        leaf_key=leaf_key,
                    ).agent_id,
                    "manager",
                )


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
