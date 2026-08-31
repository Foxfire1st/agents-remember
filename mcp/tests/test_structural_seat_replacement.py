"""ARSPAWN-L2: canonical dispatch idempotency and replacement forcing matrix."""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.structural.agent_tools import (
    StructuralAgentRuntime,
    dispatch_agent_tool,
    message_child_tool,
    message_parent_tool,
    retire_child_tool,
)
from agents_remember.application.terminal_tools import (
    RetiredSpawnInputs,
    SpawnedBy,
    SpawnOverrides,
    SpawnSeat,
    spawn_agent_session_tool,
)
from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.seats import current_seat_occupant
from agents_remember.kernel.primitives.observer_paths import observer_root
from agents_remember.models.conversations.control_wire import SubmissionReceipt
from agents_remember.models.structural.agent import (
    DispatchAgentRequest,
    RetireChildRequest,
    StructuralMessageRequest,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.inbox_delivery import (
    InboxDeliveryLog,
    deliver_inbox_entry,
    target_session_for_entry,
)
from agents_remember.serving.retire import SeatClosure, retire_entry
from agents_remember.serving.structural_dispatch import exclusive_structural_dispatch_lock
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from structural_seat_test_support import (
    FakeHost,
    detected_harness,
    structural_config,
    write_structural_settings,
    write_structural_topology,
)


class StructuralSeatReplacementTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sprint, self.master, self.leaf = write_structural_topology(self.root)
        write_structural_settings(self.root)
        self.config = structural_config(self.root)
        self.catalog = TerminalCatalog(terminal_catalog_path(self.root))
        self.host = FakeHost()
        self.overrides = SpawnOverrides(host=self.host, which=detected_harness)  # type: ignore[arg-type]
        readiness_wait = mock.patch(
            "agents_remember.serving.dispatch_brief.DISPATCH_BRIEF_READINESS_WAIT_SECONDS",
            0.0,
        )
        readiness_wait.start()
        self.addCleanup(readiness_wait.stop)

    def _runtime(
        self, session_id: str | None = None, role: str | None = None
    ) -> StructuralAgentRuntime:
        environ = (
            {"AR_HOSTED_SESSION_ID": session_id, "AR_SPAWN_ROLE": role or ""}
            if session_id is not None
            else {}
        )
        return StructuralAgentRuntime(
            host=self.host,  # type: ignore[arg-type]
            spawn_overrides=self.overrides,
            environ=environ,
        )

    def _current(self, document: TaskDocumentRef, role: str) -> TerminalCatalogEntry:
        occupant = current_seat_occupant(self.catalog.list(), document=document, role=role)
        assert occupant is not None
        return occupant

    def _dispatch(
        self,
        document: TaskDocumentRef,
        role: str,
        brief: str,
        *,
        caller_id: str | None = None,
        caller_role: str | None = None,
    ) -> dict[str, object]:
        return dispatch_agent_tool(
            self.config,
            DispatchAgentRequest(
                task_document_ref=document,
                role=role,  # type: ignore[arg-type]
                brief=brief,
            ),
            self._runtime(caller_id, caller_role),
        )

    def _post_worker_message(
        self,
        worker: TerminalCatalogEntry,
        *,
        ask: str,
        response: str,
    ) -> OperatorInboxEntry:
        posted = message_parent_tool(
            self.config,
            StructuralMessageRequest(ask=ask, response=response),
            self._runtime(worker.id, "worker"),
        )
        self.assertTrue(posted["ok"])
        return next(
            row
            for row in OperatorInboxStore(observer_root(self.config)).current().values()
            if row.ask == ask
        )

    def _retire_manager(self, orchestrator: TerminalCatalogEntry, *, reason: str) -> None:
        retired = retire_child_tool(
            self.config,
            RetireChildRequest(
                task_document_ref=self.master,
                role="manager",
                reason=reason,
            ),
            self._runtime(orchestrator.id, "orchestrator"),
        )
        self.assertTrue(retired["ok"])

    def _assert_retired_manager_is_fenced(self, manager: TerminalCatalogEntry) -> None:
        fenced = message_child_tool(
            self.config,
            StructuralMessageRequest(
                task_document_ref=self.leaf,
                role="worker",
                ask="A must not claim after retirement.",
                response="This message must be refused.",
            ),
            self._runtime(manager.id, "manager"),
        )
        self.assertFalse(fenced["ok"])

    def _dispatch_manager(
        self,
        orchestrator: TerminalCatalogEntry,
        *,
        brief: str,
    ) -> TerminalCatalogEntry:
        result = self._dispatch(
            self.master,
            "manager",
            brief,
            caller_id=orchestrator.id,
            caller_role="orchestrator",
        )
        self.assertTrue(result["ok"])
        return self._current(self.master, "manager")

    def test_concurrent_repeated_dispatch_converges_on_one_briefed_ambient_seat(self) -> None:
        contenders = threading.Barrier(2)

        @contextmanager
        def contended_lock(
            coordination_root: Path,
            document: TaskDocumentRef,
            role: str,
        ) -> Iterator[None]:
            contenders.wait(timeout=3)
            with exclusive_structural_dispatch_lock(coordination_root, document, role):
                yield

        def dispatch() -> dict[str, object]:
            return self._dispatch(
                self.sprint,
                "architect",
                "Design the sprint.",
            )

        with (
            mock.patch(
                "agents_remember.application.structural.dispatch_transaction."
                "exclusive_structural_dispatch_lock",
                side_effect=contended_lock,
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            first = executor.submit(dispatch)
            second = executor.submit(dispatch)
            first_result = first.result(timeout=10)
            second_result = second.result(timeout=10)

        hidden = {"session", "sessionId", "agentId", "ownerSession"}
        for result in (first_result, second_result):
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "dispatch-queued")
            self.assertTrue(hidden.isdisjoint(result))
        architects = self.catalog.list()
        self.assertEqual(len(architects), 1)
        self.assertEqual(len(self.host.ensured), 1)
        briefs = tuple(OperatorInboxStore(observer_root(self.config)).current().values())
        self.assertEqual(len(briefs), 1)
        self.assertEqual(briefs[0].messageKind, "dispatch-brief")
        self.assertEqual(architects[0].dispatch_brief_entry_id, briefs[0].id)

    def test_different_canonical_seats_do_not_share_a_global_dispatch_lock(self) -> None:
        inside_spawn = threading.Barrier(2)
        real_spawn = spawn_agent_session_tool

        def synchronized_spawn(*args: object, **kwargs: object) -> dict[str, object]:
            inside_spawn.wait(timeout=3)
            return real_spawn(*args, **kwargs)  # type: ignore[arg-type]

        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                side_effect=synchronized_spawn,
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            architect = executor.submit(self._dispatch, self.sprint, "architect", "Design it.")
            strategist = executor.submit(self._dispatch, self.sprint, "strategist", "Evaluate it.")
            results = [architect.result(timeout=10), strategist.result(timeout=10)]

        self.assertTrue(all(result["ok"] for result in results))
        self.assertEqual(
            {
                row.binding_role
                for row in self.catalog.list()
                if row.binding_task_document_ref == self.sprint and row.status == "running"
            },
            {"architect", "strategist"},
        )
        lock_dir = self.config.coordination_root / "runtime" / "structural-seat-locks"
        locks = tuple(lock_dir.glob("*.lock"))
        self.assertTrue(1 <= len(locks) <= 2)
        self.assertEqual({lock.stat().st_size for lock in locks}, {0})

    def test_dispatch_refuses_contradictory_brief_evidence_without_leaking_the_occupant(
        self,
    ) -> None:
        self._dispatch(self.sprint, "architect", "Design the sprint.")
        occupant = self._current(self.sprint, "architect")
        self.catalog.upsert(replace(occupant, dispatch_brief_entry_id="contradictory-receipt"))

        repeated = self._dispatch(self.sprint, "architect", "Design the sprint.")

        self.assertFalse(repeated["ok"])
        self.assertEqual(repeated["status"], "dispatch-reconciliation-refused")
        self.assertNotIn(occupant.id, str(repeated))
        self.assertEqual(len(self.host.ensured), 1)

    def test_repeated_dispatch_repairs_a_missing_catalog_receipt_from_the_durable_brief(
        self,
    ) -> None:
        self._dispatch(self.sprint, "architect", "Design the sprint.")
        occupant = self._current(self.sprint, "architect")
        expected_receipt = occupant.dispatch_brief_entry_id
        self.catalog.upsert(replace(occupant, dispatch_brief_entry_id=None))

        repeated = self._dispatch(self.sprint, "architect", "Design the sprint.")

        self.assertTrue(repeated["ok"])
        self.assertEqual(
            self._current(self.sprint, "architect").dispatch_brief_entry_id,
            expected_receipt,
        )
        self.assertEqual(len(self.host.ensured), 1)

    def test_repeated_dispatch_trusts_the_catalog_receipt_after_inbox_compaction(self) -> None:
        self._dispatch(self.sprint, "architect", "Design the sprint.")

        with mock.patch(
            "agents_remember.application.structural.dispatch_transaction.pinned_dispatch_brief",
            return_value=None,
        ):
            repeated = self._dispatch(self.sprint, "architect", "Design the sprint.")

        self.assertTrue(repeated["ok"])
        self.assertEqual(repeated["status"], "dispatch-queued")
        self.assertEqual(len(self.host.ensured), 1)

    def test_dispatch_lock_failure_is_a_typed_refusal_without_a_spawn_fallback(self) -> None:
        with (
            mock.patch(
                "agents_remember.serving.structural_dispatch.fcntl.flock",
                side_effect=OSError("seat lock unavailable"),
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool"
            ) as spawn,
        ):
            refused = self._dispatch(self.sprint, "architect", "Design the sprint.")

        self.assertFalse(refused["ok"])
        self.assertEqual(refused["status"], "dispatch-serialization-refused")
        self.assertNotIn("session", str(refused).lower())
        spawn.assert_not_called()

    def test_crash_stranded_unbriefed_ambient_child_is_retired_and_replaced(self) -> None:
        stranded = spawn_agent_session_tool(
            self.config,
            seat=SpawnSeat(
                task_document_ref=self.sprint,
                level="portfolio",
                env={"AR_SPAWN_ROLE": "architect"},
            ),
            retired=RetiredSpawnInputs(),
            spawned_by=SpawnedBy(caller_kind="ambient"),
            overrides=self.overrides,
        )
        stranded_id = str(stranded["session"])

        recovered = self._dispatch(self.sprint, "architect", "Design the sprint.")

        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["status"], "dispatch-queued")
        self.assertNotIn(stranded_id, str(recovered))
        retired = self.catalog.get(stranded_id)
        assert retired is not None
        self.assertEqual(retired.status, "terminated")
        current = self._current(self.sprint, "architect")
        self.assertNotEqual(current.id, stranded_id)
        self.assertIsNotNone(current.dispatch_brief_entry_id)
        self.assertEqual(len(self.host.ensured), 2)
        self.assertEqual(self.host.terminated, [stranded_id])

    def test_ambient_and_plane_flow_keeps_a_queued_message_canonical_across_manager_replacement(
        self,
    ) -> None:
        self._dispatch(self.sprint, "architect", "Design the sprint.")
        architect = self._current(self.sprint, "architect")
        self.assertEqual(architect.spawned_by_kind, "ambient")

        self._dispatch(
            self.sprint,
            "orchestrator",
            "Coordinate the sprint.",
            caller_id=architect.id,
            caller_role="architect",
        )
        orchestrator = self._current(self.sprint, "orchestrator")
        self.assertEqual(orchestrator.spawned_by_kind, "plane")

        self._dispatch(
            self.master,
            "manager",
            "Manage the master.",
            caller_id=orchestrator.id,
            caller_role="orchestrator",
        )
        manager_a = self._current(self.master, "manager")
        worker = TerminalCatalogEntry(
            id="worker",
            label="worker",
            kind="harness",
            harness="claude",
            lifecycle_id=None,
            cwd=self.root,
            tmux_name="ar-worker",
            command=("claude",),
            created_at="2026-08-25T00:00:00+00:00",
            last_attached_at="2026-08-25T00:00:00+00:00",
            status="running",
            task_document_ref=self.leaf,
            seat_role="worker",
            spawned_by_session=manager_a.id,
        )
        self.catalog.upsert(worker)
        self.host.known.add(worker.tmux_name)

        manager_a = replace(
            manager_a,
            control_state="ready",
            control_endpoint=self.root / "manager-a.sock",
            control_protocol="ar-harness-control/v1",
        )
        self.catalog.upsert(manager_a)
        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            return_value=SubmissionReceipt(
                request_id="manager-a-message",
                acceptance="immediate",
                submitted_at="2026-08-25T00:30:00+00:00",
                accepted_at="2026-08-25T00:30:00+00:00",
            ),
        ):
            established_row = self._post_worker_message(
                worker,
                ask="Established with manager A.",
                response="The worker is active.",
            )
        self.assertIsNone(established_row.agentId)
        self.assertEqual(established_row.deliveredToSession, manager_a.id)

        self._retire_manager(orchestrator, reason="replace the manager")
        self.assertIsNone(
            current_seat_occupant(self.catalog.list(), document=self.master, role="manager")
        )
        self._assert_retired_manager_is_fenced(manager_a)

        queued = self._post_worker_message(
            worker,
            ask="Continue after manager replacement.",
            response="The work remains on the same leaf.",
        )
        self.assertIsNone(queued.agentId)
        self.assertIsNone(queued.lifecycleId)
        self.assertEqual(queued.taskDocumentRef, self.master)
        self.assertEqual(queued.recipientRole, "manager")
        self.assertIsNone(target_session_for_entry(self.catalog, queued))

        manager_b = self._dispatch_manager(orchestrator, brief="Resume the master.")
        manager_b = replace(
            manager_b,
            control_state="ready",
            control_endpoint=self.root / "manager.sock",
            control_protocol="ar-harness-control/v1",
        )
        self.catalog.upsert(manager_b)
        repeated_manager = self._dispatch_manager(
            orchestrator,
            brief="This retry must not create another manager.",
        )
        self.assertEqual(repeated_manager.id, manager_b.id)
        self.assertNotEqual(manager_a.id, manager_b.id)
        self.assertEqual(target_session_for_entry(self.catalog, queued), manager_b)
        self.assertNotIn(manager_a.id, queued.model_dump_json())
        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            side_effect=lambda _target, _text, submission: SubmissionReceipt(
                request_id=submission.request_id,
                acceptance="immediate",
                submitted_at="2026-08-25T02:00:00+00:00",
                accepted_at="2026-08-25T02:00:00+00:00",
            ),
        ):
            delivered = deliver_inbox_entry(
                InboxDeliveryLog(
                    store=OperatorInboxStore(observer_root(self.config)),
                    entry=queued,
                    at="2026-08-25T02:00:00+00:00",
                ),
                sessions=HostedSessionRuntime(catalog=self.catalog, host=self.host),  # type: ignore[arg-type]
                paster=object(),  # type: ignore[arg-type]
            )
        self.assertEqual(delivered.deliveredToSession, manager_b.id)
        self.assertEqual(delivered.adapterDeliveryState, "accepted")
        self.assertIsNone(delivered.agentId)

        self._retire_manager(orchestrator, reason="exercise a second replacement")
        third_row = self._post_worker_message(
            worker,
            ask="Continue after the second manager replacement.",
            response="The canonical chain must still hold.",
        )
        self.assertIsNone(third_row.agentId)
        self.assertIsNone(target_session_for_entry(self.catalog, third_row))
        manager_c = self._dispatch_manager(
            orchestrator,
            brief="Take over as the second replacement.",
        )
        self.assertNotIn(manager_c.id, {manager_a.id, manager_b.id})
        self.assertEqual(target_session_for_entry(self.catalog, third_row), manager_c)
        self.assertNotIn(manager_a.id, third_row.model_dump_json())
        self.assertNotIn(manager_b.id, third_row.model_dump_json())
        running_managers = [
            row
            for row in self.catalog.list()
            if row.status == "running"
            and row.binding_task_document_ref == self.master
            and row.binding_role == "manager"
        ]
        self.assertEqual([row.id for row in running_managers], [manager_c.id])

    def test_staged_replacement_becomes_the_only_current_occupant_after_retire(self) -> None:
        primary = spawn_agent_session_tool(
            self.config,
            seat=SpawnSeat(
                task_document_ref=self.sprint,
                level="portfolio",
                env={"AR_SPAWN_ROLE": "architect"},
            ),
            spawned_by=SpawnedBy(caller_kind="plane"),
            overrides=self.overrides,
        )
        staged = spawn_agent_session_tool(
            self.config,
            seat=SpawnSeat(
                replacement_for_task_document_ref=self.sprint,
                level="portfolio",
                env={"AR_SPAWN_ROLE": "architect"},
            ),
            spawned_by=SpawnedBy(caller_kind="plane"),
            overrides=self.overrides,
        )
        self.assertEqual(primary["status"], "spawned-unbriefed", primary)
        self.assertEqual(staged["status"], "spawned-unbriefed", staged)
        primary_entry = self.catalog.get(str(primary["session"]))
        staged_entry = self.catalog.get(str(staged["session"]))
        assert primary_entry is not None and staged_entry is not None
        self.assertEqual(
            self.catalog.active_for_task(self.sprint, seat_role="architect"), primary_entry
        )

        retire_entry(
            self.catalog,
            self.host,  # type: ignore[arg-type]
            primary_entry,
            SeatClosure(at="2026-08-25T01:00:00+00:00", reason="handover", edge="test"),
        )
        self.assertEqual(
            self.catalog.active_for_task(self.sprint, seat_role="architect"), staged_entry
        )

        duplicate = spawn_agent_session_tool(
            self.config,
            seat=SpawnSeat(
                task_document_ref=self.sprint,
                level="portfolio",
                env={"AR_SPAWN_ROLE": "architect"},
            ),
            spawned_by=SpawnedBy(caller_kind="plane"),
            overrides=self.overrides,
        )
        self.assertEqual(duplicate["status"], "seat-taken")
        self.assertEqual(len(self.host.ensured), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
