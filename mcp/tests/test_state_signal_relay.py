"""Worker→manager state-signal relay forcing tests.

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
from agents_remember.controlplane.orchestration_nudges import OrchestrationNudgeStore
from agents_remember.controlplane.signal_routing import RoutedOwner
from agents_remember.observer.store import EventStore
from agents_remember.serving.agent_notifier import AgentNotifierContext, run_agent_notifier_sweep
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.harness_control_models import SubmissionReceipt
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
from agents_remember.serving.state_signals import evaluate_non_reaction_findings
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import PasteResult, TerminalPaster
from agents_remember.serving.terminal_tmux import TmuxProbeResult

NOW = datetime(2026, 7, 13, 15, 41, 0, tzinfo=UTC)
LEAF = "repo-a/260707_master/leaf-9"
MANAGER_ANCHOR = "repo-a/260707_master/manager-anchor"


def _entry(
    session_id: str, *, leaf_key: str | None = None, **overrides: object
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
        leaf_key=leaf_key,
        **overrides,  # type: ignore[arg-type]
    )


def _manager(session_id: str = "manager-1", **overrides: object) -> TerminalCatalogEntry:
    return _entry(session_id, leaf_key=MANAGER_ANCHOR, spawn_role="manager", **overrides)


def _done_worker(session_id: str = "worker-1", **overrides: object) -> TerminalCatalogEntry:
    return replace(
        _entry(
            session_id,
            leaf_key=LEAF,
            spawn_role="worker",
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


class StateSignalRelayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.coordination_root = root / "ar-coordination"
        observer_root = self.coordination_root / "logs" / "observer"
        self.catalog = TerminalCatalog(root / "catalog.json")
        self.inbox_store = OperatorInboxStore(observer_root)
        self.expectation_store = ExpectationRowStore(observer_root)
        self.nudge_store = OrchestrationNudgeStore(observer_root)
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
            nudge_store=self.nudge_store,
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

    def _accepted_receipt(self, request_id: str) -> SubmissionReceipt:
        return SubmissionReceipt(
            request_id=request_id,
            acceptance="immediate",
            submitted_at=NOW.isoformat(),
            accepted_at=NOW.isoformat(),
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
        self.assertEqual(signal.leafKey, LEAF)
        self.assertEqual(signal.subjectAgentId, "worker-1")
        self.assertIn("worker-1", signal.response)
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
            len(evaluate_non_reaction_findings(self.catalog, self.inbox_store, now=NOW)), 1
        )
        record_non_reaction_emitted(self.catalog, "worker-1", "landed-1")
        self.assertEqual(
            evaluate_non_reaction_findings(self.catalog, self.inbox_store, now=NOW), []
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
                leaf_key=LEAF,
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

    def test_non_reaction_ignores_non_worker_young_and_malformed_rows(self) -> None:
        self.catalog.upsert(_manager())
        # A manager at turn-ended with an old landed row is not a worker residue fact.
        self.catalog.upsert(
            replace(
                _manager("manager-stuck"),
                turn_state="turn-ended",
                turn_state_changed_at=(NOW - timedelta(minutes=10)).isoformat(),
            )
        )
        landed = create_operator_inbox_entry(
            InboxMessage(ask="state-signal", response="resp", message_kind="message"),
            entry_id="manager-landed",
            now=(NOW - timedelta(minutes=10)).isoformat(),
            routing=InboxRouting(address=InboxAddress(agent_id="manager-stuck")),
            poster=InboxPoster(created_by="agent-notifier", created_via="cli"),
        ).model_copy(
            update={
                "deliveryState": "delivered",
                "adapterDeliveryState": "accepted",
                "deliveredToSession": "manager-stuck",
                "adapterAcceptedAt": (NOW - timedelta(minutes=10)).isoformat(),
            }
        )
        self.inbox_store.append(landed)
        # Young and malformed accepted evidence on a worker does not fire either.
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
                leaf_key=LEAF,
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
