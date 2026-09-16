"""Idempotent crash and restart recovery for the state-signal relay.

One durable sequence -- row persisted, marker stamped, delivery attempted -- held across every
cut point: a marker write that fails after the row is durable, a process stop after the marker
but before the adapter submission, and a same-seat structural rebind in between. Retries and
restarts converge on one row per seat/evidence identity, a pending row whose source marker is
not yet stamped is fenced from the shared redelivery action however its finding was produced,
and two distinct seats never renew each other's row.
"""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.controlplane.agent_notifier_signals import AgentNotifierSignalCooldownStore
from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import (
    OperatorInboxEntry,
    state_signal_landed,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.signal_routing import RoutedOwner
from agents_remember.models.conversations.control_wire import SubmissionReceipt
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.store import EventStore
from agents_remember.serving import owner_signals
from agents_remember.serving._agent_notifier_actions import act_on_finding
from agents_remember.serving._agent_notifier_evaluation import evaluate_inbox_findings
from agents_remember.serving.agent_notifier import AgentNotifierContext, run_agent_notifier_sweep
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.owner_signals import (
    OwnerSignal,
    OwnerSignalOptions,
    _post_owner_signal,
)
from agents_remember.serving.state_signals import (
    evaluate_boundary_drain_findings,
    evaluate_state_signal_findings,
)
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import PasteResult, TerminalPaster
from agents_remember.serving.terminal_tmux import TmuxProbeResult
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology

NOW = datetime(2026, 7, 13, 15, 41, 0, tzinfo=UTC)
MASTER_A = TaskDocumentRef(repository="repo-a", path="260707_master/task.json")
LEAF_A = TaskDocumentRef(repository="repo-a", path="260707_master/leaf-9.json")
MASTER_B = TaskDocumentRef(repository="repo-a", path="other-master/task.json")
LEAF_B = TaskDocumentRef(repository="repo-a", path="other-master/leaf-9.json")


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


def _manager(
    session_id: str = "manager-1",
    *,
    task_document_ref: TaskDocumentRef = MASTER_A,
    **overrides: object,
) -> TerminalCatalogEntry:
    """A manager seat that can receive a protocol push at whatever turn state is supplied."""

    return _entry(
        session_id,
        task_document_ref=task_document_ref,
        spawn_role="manager",
        seat_role="manager",
        control_state="ready",
        control_endpoint=Path(f"/tmp/{session_id}.sock"),
        **overrides,
    )


def _boundary_manager(
    session_id: str = "manager-1",
    *,
    task_document_ref: TaskDocumentRef = MASTER_A,
) -> TerminalCatalogEntry:
    """A manager already at a classified turn boundary: immediately admissible for a push."""

    return replace(
        _manager(session_id, task_document_ref=task_document_ref),
        turn_state="turn-ended",
        turn_state_changed_at=(NOW - timedelta(minutes=1)).isoformat(),
    )


def _working_manager(session_id: str = "manager-1") -> TerminalCatalogEntry:
    return replace(
        _manager(session_id),
        turn_state="working",
        turn_state_changed_at=(NOW - timedelta(minutes=1)).isoformat(),
    )


def _done_worker(
    session_id: str = "worker-1",
    *,
    task_document_ref: TaskDocumentRef = LEAF_A,
    **overrides: object,
) -> TerminalCatalogEntry:
    return replace(
        _entry(
            session_id,
            task_document_ref=task_document_ref,
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


def _accepted_receipt(request_id: str) -> SubmissionReceipt:
    return SubmissionReceipt(
        request_id=request_id,
        acceptance="immediate",
        submitted_at=NOW.isoformat(),
        accepted_at=NOW.isoformat(),
    )


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
        ("other-master", "OTHER", ("leaf-9",)),
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


def _context(
    *,
    catalog: TerminalCatalog,
    store: OperatorInboxStore,
    coordination_root: Path,
    observer_root: Path,
) -> AgentNotifierContext:
    return AgentNotifierContext(
        catalog=catalog,
        host=cast(TerminalHost, _FakeHost()),
        paster=_accepted_paster(),
        inbox_store=store,
        expectation_store=ExpectationRowStore(observer_root),
        signal_cooldown_store=AgentNotifierSignalCooldownStore(observer_root),
        event_store=EventStore(observer_root),
        heartbeat_store=AgentNotifierHeartbeatStore(observer_root),
        coordination_root=coordination_root,
        stale_seat_seconds=60.0,
        redeliver_rate_limit_seconds=900.0,
    )


@dataclass(frozen=True)
class _World:
    """One isolated durable world: catalog file, inbox log, and the notifier context over them."""

    tmp: tempfile.TemporaryDirectory[str]
    root: Path
    coordination_root: Path
    observer_root: Path
    catalog: TerminalCatalog
    store: OperatorInboxStore
    ctx: AgentNotifierContext

    def all_rows(self) -> list[OperatorInboxEntry]:
        return list(self.store.current().values())

    def signals(self) -> list[OperatorInboxEntry]:
        return [
            entry for entry in self.store.current().values() if entry.messageKind == "state-signal"
        ]

    def restarted(self) -> _World:
        """The same durable files read through brand-new objects: a fresh notifier process."""

        catalog = TerminalCatalog(self.root / "catalog.json")
        store = OperatorInboxStore(self.observer_root)
        return _World(
            tmp=self.tmp,
            root=self.root,
            coordination_root=self.coordination_root,
            observer_root=self.observer_root,
            catalog=catalog,
            store=store,
            ctx=_context(
                catalog=catalog,
                store=store,
                coordination_root=self.coordination_root,
                observer_root=self.observer_root,
            ),
        )


def _world() -> _World:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    coordination_root = root / "ar-coordination"
    _write_task_topology(coordination_root)
    observer_root = coordination_root / "logs" / "observer"
    catalog = TerminalCatalog(root / "catalog.json")
    store = OperatorInboxStore(observer_root)
    return _World(
        tmp=tmp,
        root=root,
        coordination_root=coordination_root,
        observer_root=observer_root,
        catalog=catalog,
        store=store,
        ctx=_context(
            catalog=catalog,
            store=store,
            coordination_root=coordination_root,
            observer_root=observer_root,
        ),
    )


def _failed_marker_write(world: _World) -> AbstractContextManager[mock.Mock]:
    """One durable-store fault: the marker write raises while the row write is unaffected."""

    return mock.patch.object(
        world.catalog, "upsert", side_effect=OSError("catalog store unavailable")
    )


class StateSignalRestartRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = _world()
        self.addCleanup(self.world.tmp.cleanup)

    @contextmanager
    def _submit_patch(self) -> Iterator[mock.Mock]:
        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            side_effect=lambda _target, _text, submission: _accepted_receipt(submission.request_id),
        ) as submit:
            yield submit

    def test_marker_failure_keeps_one_unmarked_row_and_restart_retries_it(self) -> None:
        """A crash after row persistence converges on the same row once the marker retries."""
        self.world.catalog.upsert(_boundary_manager())
        self.world.catalog.upsert(_done_worker())

        with self._submit_patch() as submit:
            # The marker write fails after the row is already durable: no submission is made.
            with _failed_marker_write(self.world), self.assertRaises(OSError):
                run_agent_notifier_sweep(self.world.ctx, now=NOW)
            self.assertEqual(submit.call_count, 0)
            held = self.world.signals()
            self.assertEqual(len(held), 1)
            self.assertEqual(held[0].state, "pending")
            self.assertEqual(held[0].attemptCount, 0)
            self.assertIsNone(held[0].nextAttemptAt)
            self.assertIsNone(held[0].deliveredToSession)
            worker = self.world.catalog.get("worker-1")
            assert worker is not None
            self.assertIsNone(worker.state_signal_emitted_for)

            # A restarted process reads that durable row, renews it, and only then delivers.
            restarted = self.world.restarted()
            run_agent_notifier_sweep(restarted.ctx, now=NOW + timedelta(seconds=30))
            rows = restarted.signals()
            self.assertEqual([row.id for row in rows], [held[0].id])
            self.assertTrue(state_signal_landed(rows[0]))
            self.assertEqual(rows[0].deliveredToSession, "manager-1")
            worker = restarted.catalog.get("worker-1")
            assert worker is not None
            self.assertEqual(worker.state_signal_emitted_for, "turn-9")
            self.assertEqual(submit.call_count, 1)

            # The stamped marker suppresses a second logical signal for the same evidence.
            run_agent_notifier_sweep(restarted.ctx, now=NOW + timedelta(minutes=5))
            self.assertEqual([row.id for row in restarted.signals()], [held[0].id])
            self.assertEqual(submit.call_count, 1)

    def test_immediate_boundary_marker_failure_is_fenced_until_the_marker_retries(self) -> None:
        """Sweep two's redelivery and boundary drain cannot deliver the unmarked row first."""
        self.world.catalog.upsert(_boundary_manager())
        self.world.catalog.upsert(_done_worker())

        with self._submit_patch() as submit:
            # Sweep one: the manager is immediately admissible, and the marker write fails
            # after the row is durable, so nothing reaches the adapter.
            with _failed_marker_write(self.world), self.assertRaises(OSError):
                run_agent_notifier_sweep(self.world.ctx, now=NOW)
            held = self.world.signals()
            self.assertEqual(len(held), 1)
            self.assertEqual(submit.call_count, 0)

            # Sweep two: the generic redelivery finder is enabled over this generation and
            # yields the exact unmarked row; the boundary drain that delegates to the same
            # shared action yields it too. Neither may reach the adapter or touch the row.
            later = NOW + timedelta(seconds=30)
            generation = self.world.store.current()
            generic = [
                finding
                for finding in evaluate_inbox_findings(
                    self.world.store,
                    now=later,
                    rate_limit_seconds=self.world.ctx.redeliver_rate_limit_seconds,
                    current=generation,
                )
                if finding.source_id == held[0].id
            ]
            self.assertEqual([finding.kind for finding in generic], ["inbox-redeliverable"])
            drained = [
                finding
                for finding in evaluate_boundary_drain_findings(self.world.catalog, generation)
                if finding.source_id == held[0].id
            ]
            self.assertEqual([finding.kind for finding in drained], ["boundary-drain"])
            for finding in (*generic, *drained):
                with self.subTest(kind=finding.kind):
                    result = act_on_finding(self.world.ctx, finding, now=later)
                    self.assertEqual(result.outcome, "skipped")
                    self.assertEqual(result.detail, "state-signal source marker not stamped")
            fenced = self.world.signals()[0]
            self.assertEqual(fenced.id, held[0].id)
            self.assertEqual(fenced.state, "pending")
            self.assertEqual(fenced.attemptCount, 0)
            self.assertIsNone(fenced.nextAttemptAt)
            self.assertIsNone(fenced.adapterDeliveryState)
            self.assertEqual(submit.call_count, 0)

            # State-signal recovery in that same second sweep renews the row, stamps the
            # marker, and only then delivers it.
            run_agent_notifier_sweep(self.world.ctx, now=later)
            rows = self.world.signals()
            self.assertEqual([row.id for row in rows], [held[0].id])
            self.assertEqual(rows[0].deliveryState, "delivered")
            self.assertTrue(state_signal_landed(rows[0]))
            worker = self.world.catalog.get("worker-1")
            assert worker is not None
            self.assertEqual(worker.state_signal_emitted_for, "turn-9")
            self.assertEqual(submit.call_count, 1)

    def test_stop_after_marker_before_delivery_resumes_on_the_pending_row_path(self) -> None:
        """A stop after the marker callback leaves one marked pending row for later delivery."""
        self.world.catalog.upsert(_boundary_manager())
        self.world.catalog.upsert(_done_worker())
        deliver = owner_signals.deliver_inbox_entry

        def _stop_before_adapter(log: object, **kwargs: object) -> object:
            assert isinstance(log, owner_signals.InboxDeliveryLog)
            if log.entry.messageKind == "state-signal":
                raise RuntimeError("process stopped before adapter submission")
            return deliver(log, **kwargs)  # type: ignore[arg-type]

        with (
            mock.patch.object(
                owner_signals, "deliver_inbox_entry", side_effect=_stop_before_adapter
            ),
            self.assertRaises(RuntimeError),
        ):
            run_agent_notifier_sweep(self.world.ctx, now=NOW)

        held = self.world.signals()
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0].state, "pending")
        self.assertEqual(held[0].attemptCount, 0)
        worker = self.world.catalog.get("worker-1")
        assert worker is not None
        self.assertEqual(worker.state_signal_emitted_for, "turn-9")

        with self._submit_patch() as submit:
            restarted = self.world.restarted()
            run_agent_notifier_sweep(restarted.ctx, now=NOW + timedelta(seconds=30))
            rows = restarted.signals()
            self.assertEqual([row.id for row in rows], [held[0].id])
            self.assertTrue(state_signal_landed(rows[0]))
            self.assertEqual(rows[0].deliveredToSession, "manager-1")
            self.assertEqual(submit.call_count, 1)

    def test_rebind_before_marker_retry_renews_and_readdresses_the_same_row(self) -> None:
        """A same-seat structural rebind is a renewal input, never a new signal root."""
        self.world.catalog.upsert(_manager())
        self.world.catalog.upsert(_boundary_manager("manager-2", task_document_ref=MASTER_B))
        self.world.catalog.upsert(_done_worker())

        with _failed_marker_write(self.world), self.assertRaises(OSError):
            run_agent_notifier_sweep(self.world.ctx, now=NOW)
        held = self.world.signals()
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0].subjectTaskDocumentRef, LEAF_A)
        self.assertEqual(held[0].seatRole, "worker")
        self.assertEqual(held[0].agentId, "manager-1")

        # The same seat is rebound to another master's leaf as another role before the retry.
        self.world.catalog.upsert(
            _done_worker(
                "worker-1",
                task_document_ref=LEAF_B,
                spawn_role="curator",
                seat_role="curator",
            )
        )

        with self._submit_patch() as submit:
            restarted = self.world.restarted()
            run_agent_notifier_sweep(restarted.ctx, now=NOW + timedelta(seconds=30))
            rows = restarted.signals()
            self.assertEqual([row.id for row in rows], [held[0].id])
            renewed = rows[0]
            self.assertEqual(renewed.subjectAgentId, "worker-1")
            self.assertEqual(renewed.subjectTaskDocumentRef, LEAF_B)
            self.assertEqual(renewed.seatRole, "curator")
            self.assertEqual(renewed.agentId, "manager-2")
            self.assertEqual(renewed.ownerAgentId, "manager-2")
            self.assertTrue(state_signal_landed(renewed))
            self.assertEqual(submit.call_count, 1)
            worker = restarted.catalog.get("worker-1")
            assert worker is not None
            self.assertEqual(worker.state_signal_emitted_for, "turn-9")

    def test_two_seats_reporting_the_same_evidence_keep_distinct_rows(self) -> None:
        """Two replacement seats on one leaf never renew each other's row."""
        self.world.catalog.upsert(_boundary_manager())
        self.world.catalog.upsert(_done_worker("worker-1"))
        self.world.catalog.upsert(_done_worker("worker-2"))
        findings = evaluate_state_signal_findings(
            self.world.catalog, TaskDocumentTopology(self.world.coordination_root)
        )
        self.assertEqual(
            sorted(finding.session_id or "" for finding in findings), ["worker-1", "worker-2"]
        )

        # Both seats persist their own row while the marker write keeps failing.
        with _failed_marker_write(self.world):
            for finding in findings:
                with self.assertRaises(OSError):
                    act_on_finding(self.world.ctx, finding, now=NOW)
        rows = self.world.signals()
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(row.subjectAgentId or "" for row in rows), ["worker-1", "worker-2"])
        row_ids = {row.subjectAgentId: row.id for row in rows}

        # A retry renews each seat's own row, and neither seat's marker covers the other.
        with self._submit_patch() as submit:
            run_agent_notifier_sweep(self.world.ctx, now=NOW + timedelta(seconds=30))
            retried = self.world.signals()
            self.assertEqual({row.subjectAgentId: row.id for row in retried}, row_ids)
            self.assertTrue(all(state_signal_landed(row) for row in retried))
            self.assertEqual(submit.call_count, 2)
        for seat_id in ("worker-1", "worker-2"):
            seat = self.world.catalog.get(seat_id)
            assert seat is not None
            self.assertEqual(seat.state_signal_emitted_for, "turn-9")

    def test_new_evidence_identity_rearms_and_the_older_row_still_delivers(self) -> None:
        """A later turn re-arms the seat while the older pending row keeps its own path."""
        self.world.catalog.upsert(_boundary_manager())
        self.world.catalog.upsert(_done_worker())

        with _failed_marker_write(self.world), self.assertRaises(OSError):
            run_agent_notifier_sweep(self.world.ctx, now=NOW)
        older = self.world.signals()[0]
        self.assertEqual(older.state, "pending")

        # The same seat reports a later turn while the older row is still pending and unmarked.
        self.world.catalog.upsert(
            replace(
                _done_worker(),
                terminal_evidence_id="turn-10",
                terminal_outcome_at=(NOW + timedelta(minutes=1)).isoformat(),
            )
        )

        with self._submit_patch() as submit:
            drain = [
                finding
                for finding in evaluate_boundary_drain_findings(
                    self.world.catalog, self.world.store.current()
                )
                if finding.source_id == older.id
            ]
            self.assertEqual(len(drain), 1)
            result = act_on_finding(self.world.ctx, drain[0], now=NOW + timedelta(minutes=2))
            self.assertEqual(result.outcome, "delivered")
            self.assertEqual(submit.call_count, 1)

            run_agent_notifier_sweep(self.world.ctx, now=NOW + timedelta(minutes=3))
            rows = self.world.signals()
            self.assertEqual(len(rows), 2)
            successor = next(row for row in rows if row.id != older.id)
            self.assertEqual(successor.subjectAgentId, "worker-1")
            self.assertIn("turn-10", successor.ask)
            self.assertTrue(state_signal_landed(successor))
            delivered_older = next(row for row in rows if row.id == older.id)
            self.assertIn("turn-9", delivered_older.ask)
            self.assertTrue(state_signal_landed(delivered_older))
            self.assertEqual(submit.call_count, 2)
            worker = self.world.catalog.get("worker-1")
            assert worker is not None
            self.assertEqual(worker.state_signal_emitted_for, "turn-10")

    def test_other_kinds_keep_their_structural_coalescing_key(self) -> None:
        """Non-state-signal posting still coalesces structurally and ignores the occupant."""
        self.world.catalog.upsert(_working_manager())
        owner = RoutedOwner(
            role="manager",
            task_document_ref=MASTER_A,
            agent_id="manager-1",
            lifecycle_id=None,
        )
        ask = "Agent notifier observed seat-liveness: leaf-9 as worker"

        def post(subject_agent_id: str, seat_role: str) -> None:
            _post_owner_signal(
                self.world.ctx,
                owner,
                OwnerSignal(
                    message_kind="escalation",
                    ask=ask,
                    response=f"{seat_role} at {subject_agent_id}",
                    task_document_ref=MASTER_A,
                    seat_role=seat_role,
                    subject_agent_id=subject_agent_id,
                ),
                OwnerSignalOptions(now=NOW),
            )

        with self._submit_patch():
            post("worker-1", "manager")
            first = self.world.all_rows()
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0].attemptCount, 1)

            # A different occupant under the same structural key renews that same row.
            post("worker-7", "manager")
            renewed = self.world.all_rows()
            self.assertEqual(len(renewed), 1)
            self.assertEqual(renewed[0].id, first[0].id)
            self.assertEqual(renewed[0].subjectAgentId, "worker-7")
            self.assertEqual(renewed[0].attemptCount, 2)

            # A different structural key is a different row, occupant identity notwithstanding.
            post("worker-7", "reviewer")
            self.assertEqual(len(self.world.all_rows()), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
