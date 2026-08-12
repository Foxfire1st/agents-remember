"""Task-document-first signal routing and replacement regression tests (EFA-L19)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.signal_routing import (
    RoutedOwner,
    StructuralRoutingError,
    derive_manager_owner,
    derive_signal_owner,
    is_seat_dead,
    task_chain_has_progress,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.terminal_catalog import TerminalCatalog

T1 = "2026-06-23T10:00:00+00:00"
SPRINT = TaskDocumentRef(repository="repo-a", path="sprint/task.json")
MASTER = TaskDocumentRef(repository="repo-a", path="master/task.json")
LEAF = TaskDocumentRef(repository="repo-a", path="master/leaf-1.json")
PARALLEL_LEAF = TaskDocumentRef(repository="repo-a", path="master/leaf-2.json")


class _Hierarchy:
    def parent(self, ref: TaskDocumentRef) -> TaskDocumentRef | None:
        return {
            LEAF: MASTER,
            PARALLEL_LEAF: MASTER,
            MASTER: SPRINT,
            SPRINT: None,
        }[ref]


class SignalRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.catalog = TerminalCatalog(Path(tmp.name) / "terminal-sessions.json")
        self.hierarchy = _Hierarchy()

    def _upsert(self, session_id: str, **overrides: object) -> None:
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
        self.catalog.upsert(TerminalCatalogEntry(**base))  # type: ignore[arg-type]

    def test_worker_signal_routes_to_current_manager_without_spawn_ids(self) -> None:
        self._upsert("manager", task_document_ref=MASTER, seat_role="manager")
        self._upsert("worker", task_document_ref=LEAF, seat_role="worker")

        owner = derive_signal_owner(
            self.catalog,
            self.hierarchy,
            sender_agent_id="worker",
            message_kind="turn-report",
        )

        self.assertEqual(
            owner,
            RoutedOwner(
                role="manager",
                task_document_ref=MASTER,
                agent_id="manager",
            ),
        )

    def test_reviewer_signal_reaches_replacement_manager(self) -> None:
        self._upsert(
            "manager-old",
            task_document_ref=MASTER,
            seat_role="manager",
            status="terminated",
        )
        self._upsert("manager-new", task_document_ref=MASTER, seat_role="manager")
        self._upsert("reviewer", task_document_ref=LEAF, seat_role="reviewer")

        owner = derive_signal_owner(
            self.catalog,
            self.hierarchy,
            sender_agent_id="reviewer",
            message_kind="turn-report",
        )

        self.assertEqual(owner.agent_id, "manager-new")
        self.assertEqual(owner.task_document_ref, MASTER)

    def test_missing_manager_never_falls_through_to_orchestrator(self) -> None:
        self._upsert("orchestrator", task_document_ref=SPRINT, seat_role="orchestrator")
        self._upsert("worker", task_document_ref=LEAF, seat_role="worker")

        owner = derive_signal_owner(
            self.catalog,
            self.hierarchy,
            sender_agent_id="worker",
            message_kind="escalation",
        )

        self.assertEqual(owner, RoutedOwner(role="manager", task_document_ref=MASTER))

    def test_manager_routes_to_current_orchestrator(self) -> None:
        self._upsert("orchestrator", task_document_ref=SPRINT, seat_role="orchestrator")
        self._upsert("manager", task_document_ref=MASTER, seat_role="manager")

        owner = derive_signal_owner(
            self.catalog,
            self.hierarchy,
            sender_agent_id="manager",
            message_kind="master-handover",
        )

        self.assertEqual(owner.agent_id, "orchestrator")
        self.assertEqual(owner.task_document_ref, SPRINT)

    def test_sprint_roles_follow_the_approved_direct_parent_ladder(self) -> None:
        self._upsert("architect", task_document_ref=SPRINT, seat_role="architect")
        self._upsert("orchestrator", task_document_ref=SPRINT, seat_role="orchestrator")
        for role, expected in (
            ("orchestrator", "architect"),
            ("strategist", "architect"),
            ("designer", "architect"),
            ("system-specialist", "orchestrator"),
        ):
            with self.subTest(role=role):
                self._upsert(role, task_document_ref=SPRINT, seat_role=role)
                owner = derive_signal_owner(
                    self.catalog,
                    self.hierarchy,
                    sender_agent_id=role,
                    message_kind="message",
                )
                self.assertEqual(owner.agent_id, expected)

    def test_decision_item_routes_to_sprint_architect(self) -> None:
        self._upsert("architect", task_document_ref=SPRINT, seat_role="architect")
        self._upsert("worker", task_document_ref=LEAF, seat_role="worker")

        owner = derive_signal_owner(
            self.catalog,
            self.hierarchy,
            sender_agent_id="worker",
            message_kind="decision-item",
        )

        self.assertEqual(owner.agent_id, "architect")
        self.assertEqual(owner.task_document_ref, SPRINT)

    def test_ambiguity_refuses_instead_of_selecting_first(self) -> None:
        self._upsert("manager-a", task_document_ref=MASTER, seat_role="manager")
        self._upsert("manager-b", task_document_ref=MASTER, seat_role="manager")

        with self.assertRaises(StructuralRoutingError):
            derive_manager_owner(
                self.catalog,
                self.hierarchy,
                task_document_ref=LEAF,
            )

    def test_staged_replacement_counts_as_scoped_progress(self) -> None:
        self._upsert("worker-old", task_document_ref=LEAF, seat_role="worker")
        self._upsert(
            "worker-new",
            replacement_for_task_document_ref=LEAF,
            seat_role="worker",
            turn_state="working",
        )

        self.assertTrue(
            task_chain_has_progress(
                self.catalog,
                self.hierarchy,
                task_document_ref=LEAF,
                subject_agent_id="worker-old",
                since=T1,
            )
        )

    def test_parallel_leaf_never_suppresses_this_leaf(self) -> None:
        self._upsert("worker-old", task_document_ref=LEAF, seat_role="worker")
        self._upsert(
            "parallel",
            replacement_for_task_document_ref=PARALLEL_LEAF,
            seat_role="worker",
            turn_state="working",
        )

        self.assertFalse(
            task_chain_has_progress(
                self.catalog,
                self.hierarchy,
                task_document_ref=LEAF,
                subject_agent_id="worker-old",
                since=T1,
            )
        )

    def test_unknown_or_unbound_sender_derives_no_route(self) -> None:
        self._upsert("free-chat", seat_role="chat")
        for sender in ("ghost", "free-chat", None):
            with self.subTest(sender=sender):
                self.assertEqual(
                    derive_signal_owner(
                        self.catalog,
                        self.hierarchy,
                        sender_agent_id=sender,
                        message_kind="message",
                    ),
                    RoutedOwner(),
                )


class IsSeatDeadTests(unittest.TestCase):
    def test_only_a_running_catalog_occupant_is_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "terminal-sessions.json")
            catalog.upsert(
                TerminalCatalogEntry(
                    id="live",
                    label="live",
                    kind="harness",
                    harness="claude",
                    lifecycle_id=None,
                    cwd=Path("/tmp"),
                    tmux_name="ar-live",
                    command=("claude",),
                    created_at=T1,
                    last_attached_at=T1,
                    status="running",
                )
            )
            self.assertFalse(is_seat_dead(catalog, "live"))
            self.assertTrue(is_seat_dead(catalog, "ghost"))
            self.assertTrue(is_seat_dead(catalog, None))


if __name__ == "__main__":
    unittest.main()
