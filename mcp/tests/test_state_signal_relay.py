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
from typing import cast
from unittest import mock

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
from agents_remember.controlplane.operator_inbox_transitions import RedeliveryFloor
from agents_remember.controlplane.signal_routing import RoutedOwner
from agents_remember.models.conversations.control_wire import SubmissionReceipt
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.store import EventStore
from agents_remember.serving._agent_notifier_actions import act_on_finding
from agents_remember.serving.agent_notifier import AgentNotifierContext, run_agent_notifier_sweep
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.agent_notifier_models import AgentNotifierFinding
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.inbox_delivery import (
    DeliveryAdmission,
    InboxDeliveryLog,
    deliver_inbox_entry,
)
from agents_remember.serving.owner_signals import (
    OwnerSignal,
    OwnerSignalOptions,
    _post_owner_signal,
)
from agents_remember.serving.seat_turn_truth import record_non_reaction_emitted
from agents_remember.serving.state_signals import (
    NonReactionRuntime,
    current_non_reaction_finding,
    current_state_signal_finding,
    evaluate_non_reaction_findings,
    evaluate_state_signal_findings,
)
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import PasteResult, TerminalPaster
from agents_remember.serving.terminal_tmux import TmuxProbeResult
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology

NOW = datetime(2026, 7, 13, 15, 41, 0, tzinfo=UTC)
SPRINT = TaskDocumentRef(repository="repo-a", path="sprint/task.json")
MASTER = TaskDocumentRef(repository="repo-a", path="260707_master/task.json")
LEAF = TaskDocumentRef(repository="repo-a", path="260707_master/leaf-9.json")
REBOUND_LEAF = TaskDocumentRef(repository="repo-a", path="260707_master/leaf-rebound.json")
OTHER_MASTER = TaskDocumentRef(repository="repo-a", path="other-master/task.json")
OTHER_LEAF = TaskDocumentRef(repository="repo-a", path="other-master/leaf.json")


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

    def test_dedupe_keys_per_seat_and_turn(self) -> None:
        self.catalog.upsert(_manager())
        self.catalog.upsert(_done_worker())
        ctx = self._ctx()
        run_agent_notifier_sweep(ctx, now=NOW)
        self.assertEqual(len(self._state_signals()), 1)

        self.catalog.upsert(replace(_done_worker(), terminal_evidence_id="turn-10"))
        run_agent_notifier_sweep(ctx, now=NOW + timedelta(seconds=10))
        signals = self._state_signals()
        self.assertEqual(len(signals), 2)
        self.assertTrue(any("turn-9" in signal.response for signal in signals))
        self.assertTrue(any("turn-10" in signal.response for signal in signals))

    def test_current_finding_helpers_fail_closed_for_missing_and_malformed_truth(self) -> None:
        self.assertIsNone(
            current_state_signal_finding(
                self.catalog,
                self.topology,
                session_id="missing",
                source_id="missing-turn",
            )
        )
        no_address = AgentNotifierFinding(kind="non-reaction-due", detail="missing")
        self.assertIsNone(
            current_non_reaction_finding(self._non_reaction_runtime(), no_address, now=NOW)
        )
        missing_seat = AgentNotifierFinding(
            kind="non-reaction-due",
            detail="missing-row",
            session_id="missing",
            source_id="missing-row",
        )
        self.assertIsNone(
            current_non_reaction_finding(self._non_reaction_runtime(), missing_seat, now=NOW)
        )

        self.catalog.upsert(_manager())
        malformed = replace(
            _done_worker("reviewer-malformed"),
            spawn_role="reviewer",
            seat_role="reviewer",
            terminal_outcome=None,
            terminal_evidence_id=None,
        )
        self.catalog.upsert(malformed)
        self.inbox_store.append(
            self._landed_row(
                entry_id="malformed-landed", target_id="reviewer-malformed"
            ).model_copy(update={"adapterAcceptedAt": "not-a-timestamp"})
        )
        malformed_episode = AgentNotifierFinding(
            kind="non-reaction-due",
            detail="malformed-landed",
            session_id="reviewer-malformed",
            source_id="malformed-landed",
        )
        self.assertIsNone(
            current_non_reaction_finding(self._non_reaction_runtime(), malformed_episode, now=NOW)
        )

    def test_state_signal_action_revalidates_every_current_terminal_predicate(self) -> None:
        self.catalog.upsert(_manager())
        mutations = {
            "terminated": lambda entry: replace(entry, status="terminated"),
            "exited": lambda entry: replace(entry, status="exited"),
            "working": lambda entry: replace(entry, turn_state="working"),
            "cross-master": lambda entry: replace(entry, task_document_ref=OTHER_LEAF),
            "unbound": lambda entry: replace(entry, task_document_ref=None),
            "evidence-replaced": lambda entry: replace(entry, terminal_evidence_id="turn-new"),
        }

        for name, mutate in mutations.items():
            with self.subTest(name=name):
                worker_id = f"worker-{name}"
                worker = _done_worker(worker_id)
                self.catalog.upsert(worker)
                finding = next(
                    item
                    for item in evaluate_state_signal_findings(self.catalog, self.topology)
                    if item.session_id == worker_id
                )
                self.catalog.upsert(mutate(worker))

                result = act_on_finding(self._ctx(), finding, now=NOW)

                self.assertEqual(result.outcome, "skipped")
                self.assertEqual(self._state_signals(), [])
                current = self.catalog.get(worker_id)
                assert current is not None
                self.assertIsNone(current.state_signal_emitted_for)

    def test_actions_use_fresh_same_master_reparent_and_leaf_metadata(self) -> None:
        self.catalog.upsert(_manager())

        completed = _done_worker("worker-rebound")
        self.catalog.upsert(completed)
        completed_finding = next(
            item
            for item in evaluate_state_signal_findings(self.catalog, self.topology)
            if item.session_id == completed.id
        )
        self.catalog.upsert(replace(_manager(), status="terminated"))
        self.catalog.upsert(_manager("manager-2"))
        self.catalog.upsert(replace(completed, task_document_ref=REBOUND_LEAF))
        self.assertNotEqual(completed_finding.task_document_ref, REBOUND_LEAF)

        result = act_on_finding(self._ctx(), completed_finding, now=NOW)

        self.assertEqual(result.outcome, "unconfirmed")
        completed_signal = next(
            row for row in self._state_signals() if row.subjectAgentId == completed.id
        )
        self.assertEqual(completed_signal.agentId, "manager-2")
        self.assertEqual(completed_signal.taskDocumentRef, MASTER)
        self.assertEqual(completed_signal.subjectTaskDocumentRef, REBOUND_LEAF)
        self.assertEqual(completed_signal.seatRole, "worker")

        reviewer = replace(
            _done_worker("reviewer-rebound"),
            spawn_role="reviewer",
            seat_role="reviewer",
            terminal_outcome=None,
            terminal_evidence_id=None,
            turn_state_changed_at=(NOW - timedelta(minutes=10)).isoformat(),
        )
        self.catalog.upsert(reviewer)
        self.inbox_store.append(
            self._landed_row(entry_id="reviewer-rebound-landed", target_id=reviewer.id)
        )
        reviewer_finding = next(
            item
            for item in evaluate_non_reaction_findings(
                self.catalog,
                self.topology,
                self.inbox_store,
                now=NOW,
            )
            if item.session_id == reviewer.id
        )
        self.catalog.upsert(replace(reviewer, task_document_ref=REBOUND_LEAF))
        self.assertNotEqual(reviewer_finding.task_document_ref, REBOUND_LEAF)

        result = act_on_finding(self._ctx(), reviewer_finding, now=NOW)

        self.assertEqual(result.outcome, "unconfirmed")
        reviewer_signal = next(
            row for row in self._state_signals() if row.subjectAgentId == reviewer.id
        )
        self.assertEqual(reviewer_signal.agentId, "manager-2")
        self.assertEqual(reviewer_signal.taskDocumentRef, MASTER)
        self.assertEqual(reviewer_signal.subjectTaskDocumentRef, REBOUND_LEAF)
        self.assertEqual(reviewer_signal.seatRole, "reviewer")

    def test_owned_reviewer_and_curator_terminal_outcomes_remain_manager_visible(self) -> None:
        self.catalog.upsert(_manager())
        reviewer = replace(
            _done_worker("reviewer-1"),
            spawn_role="reviewer",
            seat_role="reviewer",
            terminal_evidence_id="review-turn",
        )
        curator = replace(
            _done_worker("curator-1"),
            spawn_role="curator",
            seat_role="curator",
            terminal_outcome="interrupted",
            terminal_evidence_id="curator-turn",
        )
        self.catalog.upsert(reviewer)
        self.catalog.upsert(curator)

        run_agent_notifier_sweep(self._ctx(), now=NOW)
        signals = self._state_signals()
        self.assertEqual(len(signals), 2)
        self.assertEqual({signal.agentId for signal in signals}, {"manager-1"})
        self.assertEqual({signal.subjectAgentId for signal in signals}, {"reviewer-1", "curator-1"})

    def test_reviewer_non_reaction_remains_manager_visible(self) -> None:
        self.catalog.upsert(_manager())
        reviewer = replace(
            _done_worker("reviewer-1"),
            spawn_role="reviewer",
            seat_role="reviewer",
            terminal_outcome=None,
            terminal_evidence_id=None,
            turn_state_changed_at=(NOW - timedelta(minutes=10)).isoformat(),
        )
        self.catalog.upsert(reviewer)
        self.inbox_store.append(
            create_operator_inbox_entry(
                InboxMessage(ask="nudge", response="resp"),
                entry_id="reviewer-landed-1",
                now=(NOW - timedelta(minutes=10)).isoformat(),
                routing=InboxRouting(address=InboxAddress(agent_id="reviewer-1")),
                poster=InboxPoster(created_by="system", created_via="cli"),
            ).model_copy(
                update={
                    "state": "landed",
                    "deliveryState": "delivered",
                    "adapterDeliveryState": "accepted",
                    "deliveredToSession": "reviewer-1",
                    "adapterAcceptedAt": (NOW - timedelta(minutes=10)).isoformat(),
                }
            )
        )

        run_agent_notifier_sweep(self._ctx(), now=NOW)
        signals = self._state_signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].agentId, "manager-1")
        self.assertEqual(signals[0].subjectAgentId, "reviewer-1")
        self.assertIn("non-reaction", signals[0].ask)

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

    def test_non_reaction_routes_from_the_current_cross_master_binding(self) -> None:
        self.catalog.upsert(_manager())
        self.catalog.upsert(replace(_manager("manager-foreign"), task_document_ref=OTHER_MASTER))
        reviewer = replace(
            _done_worker("reviewer-cross-master"),
            spawn_role="reviewer",
            seat_role="reviewer",
            terminal_outcome=None,
            terminal_evidence_id=None,
            turn_state_changed_at=(NOW - timedelta(minutes=10)).isoformat(),
        )
        row = self._landed_row(entry_id="landed-cross-master", target_id=reviewer.id)
        self.catalog.upsert(reviewer)
        self.inbox_store.append(row)
        finding = next(
            item
            for item in evaluate_non_reaction_findings(
                self.catalog,
                self.topology,
                self.inbox_store,
                now=NOW,
            )
            if item.session_id == reviewer.id
        )
        self.catalog.upsert(replace(reviewer, task_document_ref=OTHER_LEAF))

        result = act_on_finding(self._ctx(), finding, now=NOW)

        self.assertEqual(result.outcome, "unconfirmed")
        signal = self._state_signals()[0]
        self.assertEqual(signal.agentId, "manager-foreign")
        self.assertEqual(signal.taskDocumentRef, OTHER_MASTER)
        self.assertEqual(signal.subjectTaskDocumentRef, OTHER_LEAF)

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

    def test_interrupted_signal_carries_developer_origin(self) -> None:
        self.catalog.upsert(_manager())
        self.catalog.upsert(
            replace(
                _done_worker(),
                terminal_outcome="interrupted",
                interrupted_by="developer",
            )
        )
        run_agent_notifier_sweep(self._ctx(), now=NOW)
        signals = self._state_signals()
        self.assertEqual(len(signals), 1)
        self.assertIn("interrupted", signals[0].ask)
        self.assertIn("interrupted_by=developer", signals[0].response)
        self.assertNotIn("completed", signals[0].ask)

    def test_interrupted_signal_with_unknown_origin(self) -> None:
        self.catalog.upsert(_manager())
        self.catalog.upsert(
            replace(
                _done_worker(),
                terminal_outcome="interrupted",
                interrupted_by="unknown",
            )
        )
        run_agent_notifier_sweep(self._ctx(), now=NOW)
        signals = self._state_signals()
        self.assertEqual(len(signals), 1)
        self.assertIn("interrupted_by=unknown", signals[0].response)

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

    def test_idle_flap_rearms_for_a_new_turn(self) -> None:
        manager = replace(
            _manager(),
            turn_state="turn-ended",
            turn_state_changed_at=NOW.isoformat(),
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
            run_agent_notifier_sweep(ctx, now=NOW)
            self.assertEqual(submit.call_count, 1)

            # Manager goes active, then idle again, and the worker completes a new turn.
            self.catalog.upsert(
                replace(
                    manager,
                    turn_state="working",
                    turn_state_changed_at=(NOW + timedelta(minutes=1)).isoformat(),
                )
            )
            run_agent_notifier_sweep(ctx, now=NOW + timedelta(minutes=2))
            self.catalog.upsert(
                replace(
                    manager,
                    turn_state="turn-ended",
                    turn_state_changed_at=(NOW + timedelta(minutes=3)).isoformat(),
                )
            )
            self.catalog.upsert(replace(_done_worker(), terminal_evidence_id="turn-10"))
            run_agent_notifier_sweep(ctx, now=NOW + timedelta(minutes=4))
            self.assertEqual(submit.call_count, 2)
            self.assertEqual(len(self._state_signals()), 2)

    def test_non_reaction_residue_relays_distinct_fact(self) -> None:
        self.catalog.upsert(_manager())
        self.catalog.upsert(
            replace(
                _done_worker(),
                terminal_outcome=None,
                terminal_evidence_id=None,
                turn_state="turn-ended",
                turn_state_changed_at=(NOW - timedelta(minutes=10)).isoformat(),
            )
        )
        landed = create_operator_inbox_entry(
            InboxMessage(ask="nudge", response="resp"),
            entry_id="landed-1",
            now=(NOW - timedelta(minutes=10)).isoformat(),
            routing=InboxRouting(address=InboxAddress(agent_id="worker-1")),
            poster=InboxPoster(created_by="system", created_via="cli"),
        ).model_copy(
            update={
                "state": "landed",
                "deliveryState": "delivered",
                "adapterDeliveryState": "accepted",
                "deliveredToSession": "worker-1",
                "adapterAcceptedAt": (NOW - timedelta(minutes=10)).isoformat(),
            }
        )
        self.inbox_store.append(landed)
        ctx = self._ctx()

        run_agent_notifier_sweep(ctx, now=NOW)
        signals = self._state_signals()
        self.assertEqual(len(signals), 1)
        self.assertIn("non-reaction", signals[0].ask)
        self.assertIn("landed-1", signals[0].response)
        self.assertNotIn("unconsumed", signals[0].response)
        worker = self.catalog.get("worker-1")
        assert worker is not None
        self.assertEqual(worker.non_reaction_emitted_for, "landed-1")

        # Same episode: no second relay.
        run_agent_notifier_sweep(ctx, now=NOW + timedelta(minutes=1))
        self.assertEqual(len(self._state_signals()), 1)

    def test_non_reaction_dedupe_marker_suppresses_repeat(self) -> None:
        self.catalog.upsert(_manager())
        self.catalog.upsert(
            replace(
                _done_worker(),
                terminal_outcome=None,
                terminal_evidence_id=None,
                turn_state="turn-ended",
                turn_state_changed_at=(NOW - timedelta(minutes=10)).isoformat(),
            )
        )
        landed = create_operator_inbox_entry(
            InboxMessage(ask="nudge", response="resp"),
            entry_id="landed-1",
            now=(NOW - timedelta(minutes=10)).isoformat(),
            routing=InboxRouting(address=InboxAddress(agent_id="worker-1")),
            poster=InboxPoster(created_by="system", created_via="cli"),
        ).model_copy(
            update={
                "state": "landed",
                "deliveryState": "delivered",
                "adapterDeliveryState": "accepted",
                "deliveredToSession": "worker-1",
                "adapterAcceptedAt": (NOW - timedelta(minutes=10)).isoformat(),
            }
        )
        self.inbox_store.append(landed)
        self.assertEqual(
            len(
                evaluate_non_reaction_findings(
                    self.catalog, self.topology, self.inbox_store, now=NOW
                )
            ),
            1,
        )
        record_non_reaction_emitted(self.catalog, "worker-1", "landed-1")
        self.assertEqual(
            evaluate_non_reaction_findings(self.catalog, self.topology, self.inbox_store, now=NOW),
            [],
        )
        run_agent_notifier_sweep(self._ctx(), now=NOW)
        self.assertEqual(self._state_signals(), [])

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

    def test_no_signal_for_failed_or_unknown_terminal_outcomes(self) -> None:
        self.catalog.upsert(_manager())
        self.catalog.upsert(replace(_done_worker("worker-failed"), terminal_outcome="failed"))
        self.catalog.upsert(replace(_done_worker("worker-unknown"), terminal_outcome="unknown"))
        self.catalog.upsert(
            replace(
                _done_worker("worker-no-evidence-id"),
                terminal_outcome="completed",
                terminal_evidence_id=None,
            )
        )
        run_agent_notifier_sweep(self._ctx(), now=NOW)
        self.assertEqual(self._state_signals(), [])

    def test_repeat_fire_renews_the_same_row(self) -> None:
        self.catalog.upsert(_manager())
        self.catalog.upsert(_done_worker())
        ctx = self._ctx()
        run_agent_notifier_sweep(ctx, now=NOW)
        first = self._state_signals()
        self.assertEqual(len(first), 1)
        row_id = first[0].id
        # Simulate a lost marker between persist and marker write: re-projection re-fires
        # and must renew the SAME durable row, never mint a sibling.
        worker = self.catalog.get("worker-1")
        assert worker is not None
        self.catalog.upsert(replace(worker, state_signal_emitted_for=None))
        run_agent_notifier_sweep(ctx, now=NOW + timedelta(seconds=10))
        signals = self._state_signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].id, row_id)
        worker = self.catalog.get("worker-1")
        assert worker is not None
        self.assertEqual(worker.state_signal_emitted_for, "turn-9")

    def test_non_reaction_ignores_young_and_malformed_rows(self) -> None:
        self.catalog.upsert(_manager())
        # Young and malformed accepted evidence on a worker does not fire.
        self.catalog.upsert(
            replace(
                _done_worker("worker-young"),
                terminal_outcome=None,
                terminal_evidence_id=None,
                turn_state="turn-ended",
                turn_state_changed_at=NOW.isoformat(),
            )
        )
        self.catalog.upsert(
            replace(
                _done_worker("worker-bad-ts"),
                terminal_outcome=None,
                terminal_evidence_id=None,
                turn_state="turn-ended",
                turn_state_changed_at=(NOW - timedelta(minutes=10)).isoformat(),
            )
        )
        for seat_id, accepted_at in (
            ("worker-young", (NOW - timedelta(minutes=1)).isoformat()),
            ("worker-bad-ts", "not-a-timestamp"),
        ):
            self.inbox_store.append(
                create_operator_inbox_entry(
                    InboxMessage(ask="nudge", response="resp"),
                    entry_id=f"landed-{seat_id}",
                    now=(NOW - timedelta(minutes=10)).isoformat(),
                    routing=InboxRouting(address=InboxAddress(agent_id=seat_id)),
                    poster=InboxPoster(created_by="system", created_via="cli"),
                ).model_copy(
                    update={
                        "deliveryState": "delivered",
                        "adapterDeliveryState": "accepted",
                        "deliveredToSession": seat_id,
                        "adapterAcceptedAt": accepted_at,
                    }
                )
            )
        run_agent_notifier_sweep(self._ctx(), now=NOW)
        self.assertEqual(self._state_signals(), [])

    def test_boundary_drain_skips_rows_without_a_fresh_boundary(self) -> None:
        manager = replace(
            _manager(),
            turn_state="turn-ended",
            turn_state_changed_at=(NOW + timedelta(minutes=1)).isoformat(),
            control_endpoint=Path("/tmp/manager.sock"),
            control_state="ready",
        )
        self.catalog.upsert(manager)
        self.catalog.upsert(replace(manager, id="manager-no-boundary", turn_state_changed_at=None))
        self.catalog.upsert(
            replace(
                manager,
                id="manager-bad-boundary",
                turn_state_changed_at="not-a-timestamp",
            )
        )
        self.catalog.upsert(
            replace(
                manager,
                id="manager-old-boundary",
                turn_state_changed_at=(NOW - timedelta(minutes=3)).isoformat(),
            )
        )
        for seat_id, row_id in (
            ("manager-1", "row-fresh"),
            ("manager-1", "row-none-last"),
            ("manager-1", "row-consumed"),
            ("manager-no-boundary", "row-no-boundary"),
            ("manager-bad-boundary", "row-bad-boundary"),
            ("manager-old-boundary", "row-old-boundary"),
        ):
            self.inbox_store.append(
                create_operator_inbox_entry(
                    InboxMessage(ask="nudge", response="resp", message_kind="message"),
                    entry_id=row_id,
                    now=NOW.isoformat(),
                    routing=InboxRouting(address=InboxAddress(agent_id=seat_id)),
                    poster=InboxPoster(created_by="system", created_via="cli"),
                ).model_copy(
                    update={
                        "lastAttemptAt": (
                            None
                            if row_id == "row-none-last"
                            else (NOW - timedelta(minutes=2)).isoformat()
                        ),
                        "nextAttemptAt": (NOW + timedelta(hours=1)).isoformat(),
                        "state": "consumed" if row_id == "row-consumed" else "pending",
                        "deliveryState": "queued",
                        "adapterDeliveryState": "queued",
                    }
                )
            )
        self.inbox_store.append(
            create_operator_inbox_entry(
                InboxMessage(
                    ask="Agent notifier observed state-signal: completed (turn-9)",
                    response="resp",
                    message_kind="state-signal",
                ),
                entry_id="row-landed",
                now=NOW.isoformat(),
                routing=InboxRouting(address=InboxAddress(agent_id="manager-1")),
                poster=InboxPoster(created_by="agent-notifier", created_via="cli"),
            ).model_copy(
                update={
                    "lastAttemptAt": (NOW - timedelta(minutes=2)).isoformat(),
                    "deliveryState": "delivered",
                    "adapterDeliveryState": "accepted",
                    "adapterAcceptedAt": (NOW - timedelta(minutes=2)).isoformat(),
                }
            )
        )
        # row-fresh targets the fresh boundary: the only pushable one, drained by the sweep.
        ctx = self._ctx()
        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            side_effect=lambda _target, _text, submission: self._accepted_receipt(
                submission.request_id
            ),
        ) as submit:
            run_agent_notifier_sweep(ctx, now=NOW + timedelta(minutes=2))
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(self.inbox_store.current()["row-fresh"].adapterDeliveryState, "accepted")
        for skipped in (
            "row-none-last",
            "row-consumed",
            "row-no-boundary",
            "row-bad-boundary",
            "row-old-boundary",
        ):
            self.assertNotEqual(
                self.inbox_store.current()[skipped].adapterDeliveryState, "accepted"
            )

    def test_boundary_drain_pushes_other_pending_rows_for_the_seat(self) -> None:
        manager = replace(
            _manager(),
            turn_state="working",
            turn_state_changed_at=(NOW - timedelta(minutes=1)).isoformat(),
            control_endpoint=Path("/tmp/manager.sock"),
            control_state="ready",
        )
        self.catalog.upsert(manager)
        ordinary = create_operator_inbox_entry(
            InboxMessage(ask="please proceed", response="resp", message_kind="message"),
            entry_id="ordinary-1",
            now=NOW.isoformat(),
            routing=InboxRouting(address=InboxAddress(agent_id="manager-1")),
            poster=InboxPoster(created_by="system", created_via="cli"),
        )
        self.inbox_store.append(ordinary)
        # The ordinary row was attempted while the manager was mid-turn and held.
        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            return_value=SubmissionReceipt(
                request_id="ordinary-1",
                acceptance="queued",
                submitted_at=NOW.isoformat(),
                accepted_at=NOW.isoformat(),
            ),
        ):
            deliver_inbox_entry(
                InboxDeliveryLog(
                    store=self.inbox_store,
                    entry=ordinary,
                    at=NOW.isoformat(),
                    floor=RedeliveryFloor(current=self.inbox_store.current()),
                ),
                sessions=HostedSessionRuntime(catalog=self.catalog, host=_FakeHost()),  # type: ignore[arg-type]
                paster=_accepted_paster(),
                admission=DeliveryAdmission(boundary=True),
            )
        self.catalog.upsert(
            replace(
                manager,
                turn_state="turn-ended",
                turn_state_changed_at=(NOW + timedelta(minutes=1)).isoformat(),
            )
        )
        ctx = self._ctx()
        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            side_effect=lambda _target, _text, submission: self._accepted_receipt(
                submission.request_id
            ),
        ) as submit:
            run_agent_notifier_sweep(ctx, now=NOW + timedelta(minutes=2))
        self.assertEqual(submit.call_count, 1)
        row = self.inbox_store.current()["ordinary-1"]
        self.assertEqual(row.deliveryState, "delivered")
        self.assertEqual(row.adapterDeliveryState, "accepted")

    def test_post_owner_signal_without_sweep_reads_the_store_fold(self) -> None:
        self.catalog.upsert(_manager())
        ctx = self._ctx()
        delivery_state = _post_owner_signal(
            ctx,
            RoutedOwner(role="manager", agent_id="manager-1"),
            OwnerSignal(
                message_kind="state-signal",
                ask="Agent notifier observed state-signal: completed (turn-9)",
                response="worker done",
                task_document_ref=LEAF,
                seat_role="worker",
                subject_agent_id="worker-1",
            ),
            OwnerSignalOptions(now=NOW),
        )
        self.assertEqual(delivery_state, "unconfirmed")
        signals = self._state_signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].agentId, "manager-1")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
