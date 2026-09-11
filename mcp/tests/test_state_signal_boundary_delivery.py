"""Boundary-aware durable state-signal delivery forcing tests.

One delivery state machine: the state-signal row is persisted before the source's emitted
marker is stamped, a mid-turn manager holds that same durable row without any active-turn
submission, and the row reaches the current manager at its next admissible turn boundary --
across an occupant replacement, a fresh notifier context, and a failed protocol submission.
"""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
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
from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import SubmissionReceipt
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.store import EventStore
from agents_remember.serving import _agent_notifier_actions as notifier_actions
from agents_remember.serving._agent_notifier_actions import act_on_finding
from agents_remember.serving.agent_notifier import AgentNotifierContext, run_agent_notifier_sweep
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.harness_control_models import ReconciliationResult
from agents_remember.serving.state_signals import evaluate_state_signal_findings
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import PasteResult, TerminalPaster
from agents_remember.serving.terminal_tmux import TmuxProbeResult
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology

NOW = datetime(2026, 7, 13, 15, 41, 0, tzinfo=UTC)
MASTER = TaskDocumentRef(repository="repo-a", path="260707_master/task.json")
LEAF = TaskDocumentRef(repository="repo-a", path="260707_master/leaf-9.json")

_SUBMIT = "agents_remember.serving.inbox_delivery.submit_control_prompt"
_RECONCILE = "agents_remember.serving.inbox_delivery.reconcile_control_prompt"


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
    """A manager seat that can receive a protocol push at whatever turn state is supplied."""

    return _entry(
        session_id,
        task_document_ref=MASTER,
        spawn_role="manager",
        seat_role="manager",
        control_state="ready",
        control_endpoint=Path(f"/tmp/{session_id}.sock"),
        **overrides,
    )


def _working_manager(session_id: str = "manager-1", **overrides: object) -> TerminalCatalogEntry:
    return replace(
        _manager(session_id),
        turn_state="working",
        turn_state_changed_at=(NOW - timedelta(minutes=1)).isoformat(),
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
            orchestrates=["260707_master"],
        ),
    )
    write_task_doc(
        root / "260707_master",
        _task_doc(
            id="MASTER",
            slug="260707_master",
            title="260707_master",
            kind="master",
            subTasks=[
                {"number": "leaf-9", "name": "leaf-9", "file": "leaf-9.md", "status": "inProgress"}
            ],
        ),
    )
    write_task_doc(
        root / "260707_master",
        _task_doc(
            id="leaf-9",
            slug="leaf-9",
            title="leaf-9",
            kind="subTask",
            master="task.md",
        ),
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


class StateSignalBoundaryDeliveryTests(unittest.TestCase):
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

    def test_persist_failure_keeps_marker_unset_and_retry_publishes_one_row(self) -> None:
        """The row is durable before the marker is stamped; a failed persistence retries."""
        self.world.catalog.upsert(_working_manager())
        self.world.catalog.upsert(_done_worker())
        finding = evaluate_state_signal_findings(
            self.world.catalog, TaskDocumentTopology(self.world.coordination_root)
        )[0]

        with self._submit_patch() as submit:
            # Persistence fails first: no row, no delivery, and no source marker.
            with (
                mock.patch.object(
                    self.world.store, "append", side_effect=OSError("inbox log unavailable")
                ),
                self.assertRaises(OSError),
            ):
                act_on_finding(self.world.ctx, finding, now=NOW)
            self.assertEqual(submit.call_count, 0)
            self.assertEqual(self.world.signals(), [])
            worker = self.world.catalog.get("worker-1")
            assert worker is not None
            self.assertIsNone(worker.state_signal_emitted_for)

            # The next pass publishes the row; the marker is stamped only with the row durable.
            observed: list[tuple[str, str]] = []
            real_record = notifier_actions.record_state_signal_emitted

            def observe_marker(catalog: object, session_id: str, evidence_id: str) -> None:
                rows = self.world.signals()
                observed.append((rows[0].id if rows else "", rows[0].state if rows else ""))
                real_record(catalog, session_id, evidence_id)  # type: ignore[arg-type]

            with mock.patch.object(
                notifier_actions, "record_state_signal_emitted", side_effect=observe_marker
            ):
                run_agent_notifier_sweep(self.world.ctx, now=NOW + timedelta(seconds=5))

            signals = self.world.signals()
            self.assertEqual(len(signals), 1)
            self.assertEqual(observed, [(signals[0].id, "pending")])
            self.assertEqual(signals[0].deliveryState, "queued")
            self.assertEqual(submit.call_count, 0)
            worker = self.world.catalog.get("worker-1")
            assert worker is not None
            self.assertEqual(worker.state_signal_emitted_for, "turn-9")

            # The retried row is a real signal: the manager's next boundary lands it once.
            self.world.catalog.upsert(
                replace(
                    _working_manager(),
                    turn_state="turn-ended",
                    turn_state_changed_at=(NOW + timedelta(minutes=2)).isoformat(),
                )
            )
            run_agent_notifier_sweep(self.world.ctx, now=NOW + timedelta(minutes=3))

            landed = self.world.signals()
            self.assertEqual(len(landed), 1)
            self.assertEqual(landed[0].id, signals[0].id)
            self.assertEqual(landed[0].deliveredToSession, "manager-1")
            self.assertTrue(state_signal_landed(landed[0]))
            self.assertEqual(submit.call_count, 1)

    def test_held_signal_follows_replacement_manager_on_the_same_row(self) -> None:
        """A row held under manager A is delivered once, unchanged, to replacement B."""
        self.world.catalog.upsert(_working_manager())
        self.world.catalog.upsert(_done_worker())

        with self._submit_patch() as submit:
            run_agent_notifier_sweep(self.world.ctx, now=NOW)
            held = self.world.signals()[0]
            self.assertEqual(held.deliveryState, "queued")
            self.assertEqual(submit.call_count, 0)

            # A is replaced by B while the row is still held.
            self.world.catalog.upsert(
                replace(_manager(), status="terminated", terminated_at=NOW.isoformat())
            )
            self.world.catalog.upsert(
                replace(
                    _manager("manager-2"),
                    turn_state="turn-ended",
                    turn_state_changed_at=(NOW + timedelta(minutes=2)).isoformat(),
                )
            )
            run_agent_notifier_sweep(self.world.ctx, now=NOW + timedelta(minutes=3))
            rebound = self.world.signals()[0]
            self.assertEqual(rebound.id, held.id)
            self.assertEqual(rebound.agentId, "manager-2")
            self.assertEqual(submit.call_count, 0)

            run_agent_notifier_sweep(self.world.ctx, now=NOW + timedelta(minutes=4))
            landed = self.world.signals()[0]
            self.assertEqual(len(self.world.signals()), 1)
            self.assertEqual(landed.id, held.id)
            self.assertEqual(landed.deliveredToSession, "manager-2")
            self.assertTrue(state_signal_landed(landed))
            self.assertEqual(submit.call_count, 1)

            # The landing is terminal: no duplicate row and no second submission.
            run_agent_notifier_sweep(self.world.ctx, now=NOW + timedelta(minutes=6))
            self.assertEqual(len(self.world.signals()), 1)
            self.assertEqual(submit.call_count, 1)

    def test_held_signal_survives_a_fresh_notifier_context(self) -> None:
        """A held row persisted by one context is delivered by a later, fresh context."""
        self.world.catalog.upsert(_working_manager())
        self.world.catalog.upsert(_done_worker())

        with self._submit_patch() as submit:
            run_agent_notifier_sweep(self.world.ctx, now=NOW)
            held = self.world.signals()[0]
            self.assertEqual(held.deliveryState, "queued")
            self.assertEqual(submit.call_count, 0)

            restarted = self.world.restarted()
            self.assertEqual([row.id for row in restarted.signals()], [held.id])
            restarted.catalog.upsert(
                replace(
                    _working_manager(),
                    turn_state="turn-ended",
                    turn_state_changed_at=(NOW + timedelta(minutes=2)).isoformat(),
                )
            )
            run_agent_notifier_sweep(restarted.ctx, now=NOW + timedelta(minutes=3))

            landed = restarted.signals()[0]
            self.assertEqual(len(restarted.signals()), 1)
            self.assertEqual(landed.id, held.id)
            self.assertEqual(landed.deliveredToSession, "manager-1")
            self.assertTrue(state_signal_landed(landed))
            self.assertEqual(submit.call_count, 1)

    def test_held_signal_lands_at_each_classified_turn_boundary(self) -> None:
        """Each classified boundary state drains the same held row through the protocol path."""
        for boundary_state in ("turn-ended", "awaiting-input"):
            with self.subTest(boundary_state=boundary_state):
                world = _world()
                self.addCleanup(world.tmp.cleanup)
                world.catalog.upsert(_working_manager())
                world.catalog.upsert(_done_worker())

                with self._submit_patch() as submit:
                    run_agent_notifier_sweep(world.ctx, now=NOW)
                    held = world.signals()[0]
                    self.assertEqual(held.deliveryState, "queued")
                    self.assertEqual(submit.call_count, 0)

                    world.catalog.upsert(
                        replace(
                            _working_manager(),
                            turn_state=boundary_state,  # type: ignore[arg-type]
                            turn_state_changed_at=(NOW + timedelta(minutes=2)).isoformat(),
                        )
                    )
                    run_agent_notifier_sweep(world.ctx, now=NOW + timedelta(minutes=3))

                    landed = world.signals()[0]
                    self.assertEqual(len(world.signals()), 1)
                    self.assertEqual(landed.id, held.id)
                    self.assertTrue(state_signal_landed(landed))
                    self.assertEqual(submit.call_count, 1)

    def test_ready_idle_manager_receives_the_signal_without_a_hold(self) -> None:
        """A manager already at the ready-idle boundary is admissible for the first post."""
        self.world.catalog.upsert(replace(_manager(), turn_state=None, control_state="ready"))
        self.world.catalog.upsert(_done_worker())

        with self._submit_patch() as submit:
            run_agent_notifier_sweep(self.world.ctx, now=NOW)

        signals = self.world.signals()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].deliveredToSession, "manager-1")
        self.assertTrue(state_signal_landed(signals[0]))
        self.assertEqual(submit.call_count, 1)

    def test_failed_submission_keeps_the_same_row_pending_until_the_next_boundary(self) -> None:
        """A failed protocol submission leaves one retryable row, later landed unchanged."""
        self.world.catalog.upsert(replace(_manager(), turn_state="turn-ended"))
        self.world.catalog.upsert(_done_worker())

        with (
            mock.patch(
                _SUBMIT, side_effect=HarnessControlError("adapter transport down")
            ) as submit,
            mock.patch(
                _RECONCILE,
                side_effect=lambda _target, request_id: ReconciliationResult(
                    request_id=request_id,
                    state="unresolved",
                    reconciled_at=NOW.isoformat(),
                ),
            ),
        ):
            run_agent_notifier_sweep(self.world.ctx, now=NOW)

        self.assertEqual(submit.call_count, 1)
        pending_rows = self.world.signals()
        self.assertEqual(len(pending_rows), 1)
        pending = pending_rows[0]
        self.assertEqual(pending.state, "pending")
        self.assertEqual(pending.deliveryState, "unconfirmed")
        self.assertEqual(pending.adapterDeliveryState, "unknown")
        self.assertEqual(pending.attemptCount, 1)
        self.assertIsNotNone(pending.nextAttemptAt)

        # The manager's next boundary drains the SAME row: it is reconciled, never resubmitted.
        self.world.catalog.upsert(
            replace(
                _manager(),
                turn_state="turn-ended",
                turn_state_changed_at=(NOW + timedelta(minutes=2)).isoformat(),
            )
        )
        with mock.patch(
            _RECONCILE,
            side_effect=lambda _target, request_id: ReconciliationResult(
                request_id=request_id,
                state="accepted",
                reconciled_at=(NOW + timedelta(minutes=3)).isoformat(),
                detail="adapter reconciliation accepted the request",
            ),
        ):
            run_agent_notifier_sweep(self.world.ctx, now=NOW + timedelta(minutes=3))

        landed_rows = self.world.signals()
        self.assertEqual(len(landed_rows), 1)
        landed = landed_rows[0]
        self.assertEqual(landed.id, pending.id)
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(landed.attemptCount, 2)
        self.assertTrue(state_signal_landed(landed))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
