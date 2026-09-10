"""Owned leaf-subordinate→manager state-signal relay forcing tests.

Incident-#1 shape (a worker finishes without posting an inbox row and the manager
still receives the done signal), origin attribution, busy-manager boundary hold with
exactly one landing, dedupe across re-projection, owner rebinding after seat
replacement, idle flap re-arm, and the non-reaction residue fact.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
from unittest import mock

from agents_remember.controlplane import signal_routing as signal_routing_module
from agents_remember.controlplane.agent_notifier_signals import AgentNotifierSignalCooldownStore
from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    OperatorInboxEntry,
    create_operator_inbox_entry,
    state_signal_landed,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.signal_routing import RoutedOwner
from agents_remember.models.conversations.control_wire import SubmissionReceipt
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.store import EventStore
from agents_remember.serving import _agent_notifier_actions as notifier_actions
from agents_remember.serving._agent_notifier_actions import act_on_finding
from agents_remember.serving.agent_notifier import AgentNotifierContext, run_agent_notifier_sweep
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.state_signals import (
    NonReactionRuntime,
    evaluate_non_reaction_findings,
    evaluate_state_signal_findings,
)
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import PasteResult, TerminalPaster
from agents_remember.serving.terminal_tmux import TmuxProbeResult
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology

NOW = datetime(2026, 7, 13, 15, 41, 0, tzinfo=UTC)
MASTER = TaskDocumentRef(repository="repo-a", path="260707_master/task.json")
LEAF = TaskDocumentRef(repository="repo-a", path="260707_master/leaf-9.json")


def _entry(
    session_id: str,
    *,
    task_document_ref: TaskDocumentRef | None = None,
    **overrides: object,
) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=f"Chat {session_id}",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at="2026-07-13T00:00:00+00:00",
        last_attached_at="2026-07-13T00:00:00+00:00",
        status="running",
        task_document_ref=task_document_ref,
        **overrides,  # type: ignore[arg-type]
    )


def _manager(session_id: str = "manager-1", **overrides: object) -> TerminalCatalogEntry:
    return _entry(
        session_id,
        task_document_ref=MASTER,
        spawn_role="manager",
        seat_role="manager",
        **overrides,
    )


def _done_worker(session_id: str = "worker-1", **overrides: object) -> TerminalCatalogEntry:
    return replace(
        _entry(
            session_id,
            task_document_ref=LEAF,
            spawn_role="worker",
            seat_role="worker",
            spawned_by_session="manager-1",
            turn_state="turn-ended",
            turn_state_changed_at=NOW.isoformat(),
            terminal_outcome="completed",
            terminal_outcome_at=NOW.isoformat(),
            terminal_evidence_id="turn-9",
        ),
        **overrides,
    )


class _FakeHost:
    def has_session(self, _tmux_name: str) -> bool:
        return True

    def probe_session(self, _tmux_name: str) -> TmuxProbeResult:
        return TmuxProbeResult(exists=True, evidence="alive")

    def get(self, _sid: str) -> None:
        return None


def _accepted_paster() -> TerminalPaster:
    class _AcceptedPaster:
        def paste(
            self,
            _tmux_name: str,
            _text: str,
            *,
            submit: bool = False,
            **_kwargs: object,
        ) -> PasteResult:
            return PasteResult(delivered=True, submitted=submit)

    return cast(TerminalPaster, _AcceptedPaster())


def _task_doc(**values: object) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": values.pop("id"),
            "slug": values.pop("slug"),
            "title": values.pop("title"),
            "kind": values.pop("kind"),
            "repo": "repo-a",
            "createdAt": "2026-07-13T00:00",
            **values,
        }
    )


def _write_task_topology(coordination_root: Path) -> None:
    root = coordination_root / "tasks" / "repo-a"
    write_task_doc(
        root / "sprint",
        _task_doc(
            id="SPRINT",
            slug="sprint",
            title="Sprint",
            kind="master",
            orchestrates=["260707_master", "other-master"],
        ),
    )
    for directory, task_id, leaves in (
        ("260707_master", "MASTER", ("leaf-9", "leaf-rebound")),
        ("other-master", "OTHER", ("leaf",)),
    ):
        write_task_doc(
            root / directory,
            _task_doc(
                id=task_id,
                slug=directory,
                title=directory,
                kind="master",
                subTasks=[
                    {
                        "number": leaf,
                        "name": leaf,
                        "file": f"{leaf}.md",
                        "status": "inProgress",
                    }
                    for leaf in leaves
                ],
            ),
        )
        for leaf in leaves:
            write_task_doc(
                root / directory,
                _task_doc(
                    id=leaf,
                    slug=leaf,
                    title=leaf,
                    kind="subTask",
                    master="task.md",
                ),
            )


class StateSignalRelayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.coordination_root = root / "ar-coordination"
        _write_task_topology(self.coordination_root)
        self.topology = TaskDocumentTopology(self.coordination_root)
        observer_root = self.coordination_root / "logs" / "observer"
        self.catalog = TerminalCatalog(root / "catalog.json")
        self.inbox_store = OperatorInboxStore(observer_root)
        self.expectation_store = ExpectationRowStore(observer_root)
        self.signal_cooldown_store = AgentNotifierSignalCooldownStore(observer_root)
        self.event_store = EventStore(observer_root)
        self.heartbeat_store = AgentNotifierHeartbeatStore(observer_root)

    def _ctx(self, **overrides: object) -> AgentNotifierContext:
        base: dict[str, object] = dict(
            catalog=self.catalog,
            host=cast(TerminalHost, _FakeHost()),
            paster=_accepted_paster(),
            inbox_store=self.inbox_store,
            expectation_store=self.expectation_store,
            signal_cooldown_store=self.signal_cooldown_store,
            event_store=self.event_store,
            heartbeat_store=self.heartbeat_store,
            coordination_root=self.coordination_root,
            stale_seat_seconds=60.0,
            redeliver_rate_limit_seconds=900.0,
        )
        base.update(overrides)
        return AgentNotifierContext(**base)  # type: ignore[arg-type]

    def _state_signals(self) -> list[OperatorInboxEntry]:
        return [
            entry
            for entry in self.inbox_store.current().values()
            if entry.messageKind == "state-signal"
        ]

    def _non_reaction_runtime(self) -> NonReactionRuntime:
        return NonReactionRuntime(self.catalog, self.topology, self.inbox_store)

    def _accepted_receipt(self, request_id: str) -> SubmissionReceipt:
        return SubmissionReceipt(
            request_id=request_id,
            acceptance="immediate",
            submitted_at=NOW.isoformat(),
            accepted_at=NOW.isoformat(),
        )

    def _landed_row(self, *, entry_id: str, target_id: str) -> OperatorInboxEntry:
        return create_operator_inbox_entry(
            InboxMessage(ask="nudge", response="resp"),
            entry_id=entry_id,
            now=(NOW - timedelta(minutes=10)).isoformat(),
            routing=InboxRouting(address=InboxAddress(agent_id=target_id)),
            poster=InboxPoster(created_by="system", created_via="cli"),
        ).model_copy(
            update={
                "state": "landed",
                "deliveryState": "delivered",
                "adapterDeliveryState": "accepted",
                "deliveredToSession": target_id,
                "adapterAcceptedAt": (NOW - timedelta(minutes=10)).isoformat(),
            }
        )

    def test_incident_1_finished_worker_without_inbox_row_still_signals_manager(self) -> None:
        self.catalog.upsert(_manager())
        self.catalog.upsert(_done_worker())
        ctx = self._ctx()

        result = run_agent_notifier_sweep(ctx, now=NOW)
        signals = self._state_signals()
        self.assertEqual(len(signals), 1, result.actions)
        signal = signals[0]
        self.assertEqual(signal.agentId, "manager-1")
        self.assertEqual(signal.taskDocumentRef, MASTER)
        self.assertEqual(signal.subjectTaskDocumentRef, LEAF)
        self.assertEqual(signal.subjectAgentId, "worker-1")
        self.assertIn(LEAF.key, signal.response)
        self.assertIn("as worker", signal.response)
        self.assertIn("turn-9", signal.response)
        self.assertIn("completed", signal.response)
        worker = self.catalog.get("worker-1")
        assert worker is not None
        self.assertEqual(worker.state_signal_emitted_for, "turn-9")

        # Re-projection with the same terminal evidence must not mint a second row.
        run_agent_notifier_sweep(ctx, now=NOW + timedelta(seconds=10))
        self.assertEqual(len(self._state_signals()), 1)

    def test_non_reaction_action_revalidates_current_topology_and_landed_episode(self) -> None:
        self.catalog.upsert(_manager())
        mutations = {
            "terminated": lambda entry, row: (replace(entry, status="terminated"), row),
            "exited": lambda entry, row: (replace(entry, status="exited"), row),
            "working": lambda entry, row: (replace(entry, turn_state="working"), row),
            "unbound": lambda entry, row: (replace(entry, task_document_ref=None), row),
            "retargeted-row": lambda entry, row: (
                entry,
                row.model_copy(update={"deliveredToSession": "other-seat"}),
            ),
            "consumed-row": lambda entry, row: (
                entry,
                row.model_copy(update={"state": "consumed"}),
            ),
            "timezone-naive-row": lambda entry, row: (
                entry,
                row.model_copy(
                    update={
                        "adapterAcceptedAt": (NOW - timedelta(minutes=10))
                        .replace(tzinfo=None)
                        .isoformat()
                    }
                ),
            ),
        }

        for name, mutate in mutations.items():
            with self.subTest(name=name):
                worker_id = f"reviewer-{name}"
                row_id = f"landed-{name}"
                worker = replace(
                    _done_worker(worker_id),
                    spawn_role="reviewer",
                    seat_role="reviewer",
                    terminal_outcome=None,
                    terminal_evidence_id=None,
                    turn_state_changed_at=(NOW - timedelta(minutes=10)).isoformat(),
                )
                row = self._landed_row(entry_id=row_id, target_id=worker_id)
                self.catalog.upsert(worker)
                self.inbox_store.append(row)
                finding = next(
                    item
                    for item in evaluate_non_reaction_findings(
                        self.catalog, self.topology, self.inbox_store, now=NOW
                    )
                    if item.session_id == worker_id
                )
                mutated_entry, mutated_row = mutate(worker, row)
                self.catalog.upsert(mutated_entry)
                if mutated_row != row:
                    self.inbox_store.append(mutated_row)

                result = act_on_finding(self._ctx(), finding, now=NOW)

                self.assertEqual(result.outcome, "skipped")
                self.assertEqual(self._state_signals(), [])
                current = self.catalog.get(worker_id)
                assert current is not None
                self.assertIsNone(current.non_reaction_emitted_for)

    def test_busy_manager_holds_at_boundary_then_lands_exactly_once(self) -> None:
        manager = replace(
            _manager(),
            turn_state="working",
            turn_state_changed_at=(NOW - timedelta(minutes=1)).isoformat(),
            control_endpoint=Path("/tmp/manager.sock"),
            control_state="ready",
        )
        self.catalog.upsert(manager)
        self.catalog.upsert(_done_worker())
        ctx = self._ctx()

        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            side_effect=lambda _target, _text, submission: self._accepted_receipt(
                submission.request_id
            ),
        ) as submit:
            # Tick 1: the signal is durable immediately; the boundary gate holds it.
            run_agent_notifier_sweep(ctx, now=NOW)
            signals = self._state_signals()
            self.assertEqual(len(signals), 1)
            held = signals[0]
            self.assertEqual(held.deliveryState, "queued")
            self.assertEqual(held.adapterDeliveryState, "queued")
            self.assertIsNotNone(held.nextAttemptAt)
            self.assertEqual(submit.call_count, 0)

            # Tick 2: still working and not yet due -- one row, no landing.
            run_agent_notifier_sweep(ctx, now=NOW + timedelta(minutes=2))
            self.assertEqual(len(self._state_signals()), 1)
            self.assertEqual(submit.call_count, 0)

            # Tick 3: past the escalation SLA (300 s) with the manager still working --
            # the held row must NOT be escalated or pushed mid-turn.
            run_agent_notifier_sweep(ctx, now=NOW + timedelta(seconds=301))
            self.assertEqual(submit.call_count, 0)
            self.assertEqual(self._state_signals()[0].rung, 0)
            self.assertEqual(self._state_signals()[0].deliveryState, "queued")

            # Tick 4: past the redelivery floor (900 s) with the manager still working --
            # the held row must NOT be redelivered mid-turn either.
            run_agent_notifier_sweep(ctx, now=NOW + timedelta(seconds=901))
            self.assertEqual(submit.call_count, 0)
            self.assertEqual(self._state_signals()[0].rung, 0)
            self.assertEqual(self._state_signals()[0].deliveryState, "queued")

            # Tick 5: the manager reaches a turn boundary -> boundary drain lands it.
            self.catalog.upsert(
                replace(
                    manager,
                    turn_state="turn-ended",
                    turn_state_changed_at=(NOW + timedelta(minutes=16)).isoformat(),
                )
            )
            run_agent_notifier_sweep(ctx, now=NOW + timedelta(minutes=17))
            landed = self._state_signals()[0]
            self.assertEqual(landed.deliveryState, "delivered")
            self.assertEqual(landed.adapterDeliveryState, "accepted")
            self.assertTrue(state_signal_landed(landed))
            self.assertIsNone(landed.nextAttemptAt)
            self.assertEqual(submit.call_count, 1)

            # Tick 6: landed is terminal on this path -- no retry, no second landing.
            run_agent_notifier_sweep(ctx, now=NOW + timedelta(minutes=19))
            self.assertEqual(submit.call_count, 1)
            self.assertEqual(len(self._state_signals()), 1)

    def test_owner_rebinding_after_manager_replacement(self) -> None:
        self.catalog.upsert(replace(_manager(), status="exited"))
        self.catalog.upsert(
            replace(
                _manager("manager-2"),
                spawned_by_session="orchestrator-1",
                # A working manager isolates the worker-done rebinding regression: a
                # turn-ended manager + idle worker would additionally fire the
                # compound-idle fact, which this test does not exercise.
                turn_state="working",
                control_endpoint=None,
            )
        )
        self.catalog.upsert(_done_worker())
        run_agent_notifier_sweep(self._ctx(), now=NOW)
        signals = self._state_signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].agentId, "manager-2")

    def test_master_exit_reviewer_signal_reaches_current_manager(self) -> None:
        self.catalog.upsert(replace(_manager("manager-old"), status="exited"))
        self.catalog.upsert(replace(_manager("manager-current"), turn_state="working"))
        reviewer = _entry(
            "reviewer-master-exit",
            task_document_ref=MASTER,
            spawn_role="reviewer",
            seat_role="reviewer",
            structural_parent_task_document_ref=MASTER,
            structural_parent_role="manager",
            turn_state="turn-ended",
            turn_state_changed_at=NOW.isoformat(),
            terminal_outcome="completed",
            terminal_outcome_at=NOW.isoformat(),
            terminal_evidence_id="review-turn-1",
        )
        self.catalog.upsert(reviewer)

        run_agent_notifier_sweep(self._ctx(), now=NOW)

        signals = self._state_signals()
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.agentId, "manager-current")
        self.assertEqual(signal.taskDocumentRef, MASTER)
        self.assertEqual(signal.recipientRole, "manager")
        self.assertEqual(signal.subjectTaskDocumentRef, MASTER)
        self.assertEqual(signal.subjectAgentId, "reviewer-master-exit")
        self.assertEqual(signal.seatRole, "reviewer")

    def test_topology_refusal_fences_one_subject_and_keeps_unrelated_finding(self) -> None:
        self.catalog.upsert(_manager())
        self.catalog.upsert(_done_worker("worker-malformed"))
        self.catalog.upsert(
            _done_worker(
                "worker-valid",
                task_document_ref=TaskDocumentRef(
                    repository="repo-a", path="260707_master/leaf-rebound.json"
                ),
            )
        )

        class _RefusalTopology:
            def __init__(self, status: str) -> None:
                self.status = status

            def parent(self, ref: TaskDocumentRef) -> TaskDocumentRef | None:
                if ref == LEAF:
                    raise TaskDocumentRefError(self.status, "test topology refusal")
                return self._topology.parent(ref)

            def altitude(self, ref: TaskDocumentRef) -> str:
                return self._topology.altitude(ref)

            _topology = self.topology

        for status in ("task-document-parent-missing", "task-document-parent-ambiguous"):
            with self.subTest(status=status):
                findings = evaluate_state_signal_findings(self.catalog, _RefusalTopology(status))
                self.assertEqual([finding.session_id for finding in findings], ["worker-valid"])

    def test_owner_disappearance_after_revalidation_keeps_source_eligible(self) -> None:
        manager = _manager("manager-race", turn_state="working")
        self.catalog.upsert(manager)
        self.catalog.upsert(_done_worker("worker-race"))
        finding = evaluate_state_signal_findings(self.catalog, self.topology)[0]

        real_derive_signal_owner = signal_routing_module.derive_signal_owner

        def remove_current_manager(
            catalog: TerminalCatalog,
            hierarchy: TaskDocumentTopology,
            *,
            sender_agent_id: str | None,
            message_kind: Literal["state-signal"],
            task_document_ref: TaskDocumentRef | None = None,
        ) -> RoutedOwner:
            self.catalog.upsert(replace(manager, status="exited"))
            return real_derive_signal_owner(
                catalog,
                hierarchy,
                sender_agent_id=sender_agent_id,
                message_kind=message_kind,
                task_document_ref=task_document_ref,
            )

        with mock.patch.object(
            notifier_actions,
            "derive_signal_owner",
            side_effect=remove_current_manager,
        ):
            result = act_on_finding(self._ctx(), finding, now=NOW)

        self.assertEqual(result.outcome, "skipped")
        self.assertIn("no routable owner", result.detail or "")
        self.assertEqual(self._state_signals(), [])
        current = self.catalog.get("worker-race")
        assert current is not None
        self.assertIsNone(current.state_signal_emitted_for)

    def test_action_topology_refusal_fences_subject_without_marker(self) -> None:
        self.catalog.upsert(_manager("manager-refused", turn_state="working"))
        self.catalog.upsert(_done_worker("worker-refused"))
        finding = evaluate_state_signal_findings(self.catalog, self.topology)[0]

        with mock.patch.object(
            notifier_actions,
            "derive_signal_owner",
            side_effect=TaskDocumentRefError("task-document-parent-ambiguous", "test action"),
        ):
            result = act_on_finding(self._ctx(), finding, now=NOW)

        self.assertEqual(result.outcome, "skipped")
        self.assertIn("test action", result.detail or "")
        self.assertEqual(self._state_signals(), [])
        current = self.catalog.get("worker-refused")
        assert current is not None
        self.assertIsNone(current.state_signal_emitted_for)

    def test_no_done_signal_for_killed_or_hung_seats(self) -> None:
        self.catalog.upsert(_manager())
        self.catalog.upsert(
            replace(
                _done_worker("worker-killed"),
                status="exited",
                spawned_by_session="manager-1",
                task_document_ref=LEAF,
            )
        )
        self.catalog.upsert(
            replace(
                _done_worker("worker-hung"),
                turn_state="stale",
                turn_state_changed_at=NOW.isoformat(),
            )
        )
        run_agent_notifier_sweep(self._ctx(), now=NOW)
        self.assertEqual(self._state_signals(), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
