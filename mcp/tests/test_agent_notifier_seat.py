from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.controlplane.agent_notifier_signals import AgentNotifierSignalCooldownStore
from agents_remember.controlplane.expectation_rows import (
    Expectation,
    ExpectationRowStore,
    ExpectationSubject,
    write_expectation_row,
)
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    InboxSubject,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.orchestration_nudges import OrchestrationNudgeStore
from agents_remember.observer.store import EventStore
from agents_remember.serving import _agent_notifier_actions as agent_notifier_actions_module
from agents_remember.serving.agent_notifier import (
    AgentNotifierContext,
    AgentNotifierFinding,
    act_on_finding,
    evaluate_seat_liveness_findings,
    run_agent_notifier_sweep,
)
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog
from test_agent_notifier import NOW, _entry, _fake_paster, _FakeHost


class SeatLivenessPredicateTests(unittest.TestCase):
    def test_stale_turn_state_past_cutoff_fires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(
                _entry("s1").with_turn_state(
                    "stale", changed_at=(NOW - timedelta(minutes=5)).isoformat()
                )
            )
            findings = evaluate_seat_liveness_findings(catalog, now=NOW, stale_seconds=60.0)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].detail, "turn-state-stale")

    def test_recently_stale_does_not_fire_yet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(_entry("s1").with_turn_state("stale", changed_at=NOW.isoformat()))
            self.assertEqual(
                evaluate_seat_liveness_findings(catalog, now=NOW, stale_seconds=60.0), []
            )

    def test_degraded_row_with_no_turn_state_uses_liveness_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(replace(_entry("s1"), liveness_failures=1))
            findings = evaluate_seat_liveness_findings(catalog, now=NOW, stale_seconds=60.0)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].detail, "liveness-degraded")

    def test_unbound_reviewer_completion_suppresses_false_inactive_refire(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            leaf_key = "repo-a/260707_master/leaf-9"
            catalog.upsert(
                replace(
                    _entry("manager-current", leaf_key="repo-a/260707_master/manager-anchor"),
                    spawn_role="manager",
                )
            )
            catalog.upsert(
                replace(
                    _entry("worker-1", leaf_key=leaf_key).with_turn_state(
                        "stale", changed_at=(NOW - timedelta(minutes=10)).isoformat()
                    ),
                    spawn_role="worker",
                    spawned_by_session="manager-current",
                )
            )
            catalog.upsert(
                replace(
                    _entry("reviewer-1", status="landed"),
                    spawn_role="reviewer",
                    spawned_by_session="manager-current",
                    replacement_for_leaf=leaf_key,
                    landed_at=(NOW - timedelta(minutes=1)).isoformat(),
                )
            )

            self.assertEqual(
                evaluate_seat_liveness_findings(catalog, now=NOW, stale_seconds=60.0),
                [],
            )

    def test_declared_unbound_replacement_suppresses_false_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            leaf_key = "repo-a/260707_master/leaf-9"
            catalog.upsert(
                replace(
                    _entry("manager-current", leaf_key="repo-a/260707_master/manager-anchor"),
                    spawn_role="manager",
                )
            )
            catalog.upsert(
                replace(
                    _entry("worker-dead", leaf_key=leaf_key).with_turn_state(
                        "stale", changed_at=(NOW - timedelta(minutes=10)).isoformat()
                    ),
                    spawn_role="worker",
                    spawned_by_session="manager-current",
                )
            )
            catalog.upsert(
                replace(
                    replace(_entry("worker-replacement"), turn_state="working"),
                    spawn_role="worker",
                    spawned_by_session="manager-current",
                    replacement_for_leaf=leaf_key,
                )
            )

            self.assertEqual(
                evaluate_seat_liveness_findings(catalog, now=NOW, stale_seconds=60.0),
                [],
            )


class SweepIntegrationTests(unittest.TestCase):
    """Seeded drift across every predicate family -> the expected action set."""

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
            paster=_fake_paster(),
            inbox_store=self.inbox_store,
            expectation_store=self.expectation_store,
            nudge_store=self.nudge_store,
            signal_cooldown_store=self.signal_cooldown_store,
            event_store=self.event_store,
            heartbeat_store=self.heartbeat_store,
            coordination_root=self.coordination_root,
            stale_seat_seconds=60.0,
        )
        base.update(overrides)
        return AgentNotifierContext(**base)  # type: ignore[arg-type]

    def test_seeded_drift_produces_expected_actions_and_ticks_heartbeat(self) -> None:
        # A worker seat spawned by a manager seat -- the routing edge signal-emit/auto-nudge walk.
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        worker = replace(
            _entry("worker-1", leaf_key="repo-a/260707_master/leaf-9"),
            spawn_role="worker",
            spawned_by_session="manager-1",
        )
        self.catalog.upsert(worker)

        # R2e: a seat gone stale past the cutoff -- exercises the signal-emit action path.
        stale_seat = replace(
            _entry("stale-1").with_turn_state(
                "stale", changed_at=(NOW - timedelta(minutes=5)).isoformat()
            ),
            spawn_role="worker",
            spawned_by_session="manager-1",
            cwd=Path("/workspace"),
            replacement_for_leaf="repo-a/260707_master/leaf-10",
        )
        self.catalog.upsert(stale_seat)

        # R2d: an unacked inbox row, immediately redeliverable.
        inbox_entry = create_operator_inbox_entry(
            InboxMessage(ask="ask", response="resp"),
            entry_id="inbox-1",
            now=NOW.isoformat(),
            routing=InboxRouting(address=InboxAddress(lifecycle_id=None, agent_id="worker-1")),
            poster=InboxPoster(created_by="system", created_via="cli"),
        )
        self.inbox_store.append(inbox_entry)

        # R2b: an overdue ack-by row for the worker.
        write_expectation_row(
            self.expectation_store,
            Expectation(
                kind="ack-by",
                source_id="worker-1",
                subject=ExpectationSubject(
                    agent_id="worker-1", leaf_key="repo-a/260707_master/leaf-9"
                ),
            ),
            row_id="exp-1",
            now=NOW - timedelta(minutes=10),
            sla_seconds=60.0,
        )

        ctx = self._ctx()
        result = run_agent_notifier_sweep(ctx, now=NOW)

        finding_kinds = sorted(f.kind for f in result.findings)
        self.assertIn("inbox-redeliverable", finding_kinds)
        self.assertIn("expectation-overdue", finding_kinds)
        self.assertIn("seat-liveness", finding_kinds)

        action_kinds = {a.action for a in result.actions}
        self.assertIn("redeliver", action_kinds)
        self.assertIn("auto-nudge", action_kinds)
        self.assertIn("signal-emit", action_kinds)

        # The signal-emit action routed to the stale seat's manager. This legacy fixture has no
        # protocol endpoint, so delivery is loudly unsupported instead of raw-pasted.
        signal_actions = [a for a in result.actions if a.action == "signal-emit"]
        self.assertEqual(signal_actions[0].outcome, "unconfirmed")

        # The pre-existing row follows the same no-fallback contract.
        redeliver_actions = [a for a in result.actions if a.action == "redeliver"]
        self.assertEqual(redeliver_actions[0].outcome, "unconfirmed")

        # The overdue expectation row is marked missed -- the sweep is its reserved caller.
        current = self.expectation_store.current()["exp-1"]
        self.assertEqual(current.state, "missed")

        # R5: the heartbeat ticked exactly once for this sweep.
        heartbeat = self.heartbeat_store.read()
        assert heartbeat is not None
        self.assertEqual(heartbeat.sweepCount, 1)
        self.assertEqual(heartbeat.lastTickAt, NOW.isoformat())

        # R4e: every action is logged as an orchestration.agent-notifier.* (or reused nudge) event.
        events = self.event_store.read(None)
        kinds = {event.kind for event in events}
        self.assertTrue(
            kinds
            & {
                "orchestration.agent-notifier.redeliver",
                "orchestration.nudge",
                "orchestration.agent-notifier.signal",
            }
        )

    def test_finding_with_no_routable_owner_skips_its_action(self) -> None:
        # A seat with no spawn provenance -- derive_signal_owner has nothing to route to, so both
        # the nudge and signal-emit actions must skip rather than raise or fabricate an address.
        self.catalog.upsert(
            replace(
                _entry("orphan-1").with_turn_state(
                    "stale", changed_at=(NOW - timedelta(minutes=5)).isoformat()
                )
            )
        )
        write_expectation_row(
            self.expectation_store,
            Expectation(
                kind="ack-by",
                source_id="orphan-1",
                subject=ExpectationSubject(agent_id="orphan-1"),
            ),
            row_id="exp-orphan",
            now=NOW - timedelta(minutes=10),
            sla_seconds=60.0,
        )
        ctx = self._ctx()
        result = run_agent_notifier_sweep(ctx, now=NOW)
        outcomes = {a.action: a.outcome for a in result.actions}
        self.assertEqual(outcomes.get("auto-nudge"), "skipped")
        self.assertEqual(outcomes.get("signal-emit"), "skipped")

    def test_zero_drift_sweep_still_ticks_the_heartbeat(self) -> None:
        ctx = self._ctx()
        result = run_agent_notifier_sweep(ctx, now=NOW)
        self.assertEqual(result.findings, ())
        self.assertEqual(result.actions, ())
        heartbeat = self.heartbeat_store.read()
        assert heartbeat is not None
        self.assertEqual(heartbeat.sweepCount, 1)

    def test_second_sweep_bumps_sweep_count(self) -> None:
        ctx = self._ctx()
        run_agent_notifier_sweep(ctx, now=NOW)
        run_agent_notifier_sweep(ctx, now=NOW + timedelta(seconds=10))
        heartbeat = self.heartbeat_store.read()
        assert heartbeat is not None
        self.assertEqual(heartbeat.sweepCount, 2)

    def test_terminal_dead_seat_row_becomes_ladder_resolved_not_redelivered(self) -> None:
        entry = create_operator_inbox_entry(
            InboxMessage(ask="ask", response="resp"),
            entry_id="dead-row",
            now=NOW.isoformat(),
            routing=InboxRouting(address=InboxAddress(lifecycle_id=None, agent_id="missing-seat")),
            poster=InboxPoster(created_by="system", created_via="cli"),
        ).model_copy(update={"rung": 3})
        self.inbox_store.append(entry)

        result = run_agent_notifier_sweep(self._ctx(), now=NOW)

        actions = {action.action: action for action in result.actions}
        self.assertIn("ladder-resolve", actions)
        self.assertNotIn("redeliver", actions)
        resolved = self.inbox_store.current()["dead-row"]
        self.assertEqual(resolved.state, "ladder-resolved")
        event_kinds = {event.kind for event in self.event_store.read(None)}
        self.assertIn("orchestration.agent-notifier.ladder-resolved", event_kinds)

    def test_redeliver_budget_limits_attempts_and_heartbeat_reports_backlog(self) -> None:
        for index in range(3):
            self.inbox_store.append(
                create_operator_inbox_entry(
                    InboxMessage(ask="ask", response="resp"),
                    entry_id=f"row-{index}",
                    now=NOW.isoformat(),
                    routing=InboxRouting(
                        address=InboxAddress(lifecycle_id=None, agent_id=f"missing-seat-{index}")
                    ),
                    poster=InboxPoster(created_by="system", created_via="cli"),
                )
            )

        result = run_agent_notifier_sweep(self._ctx(redeliver_budget=1), now=NOW)

        redeliver_actions = [action for action in result.actions if action.action == "redeliver"]
        self.assertEqual(len(redeliver_actions), 1)
        self.assertEqual(result.pending_inbox_count, 3)
        self.assertEqual(result.redeliverable_inbox_count, 3)
        heartbeat = self.heartbeat_store.read()
        assert heartbeat is not None
        self.assertEqual(heartbeat.pendingInboxCount, 3)
        self.assertEqual(heartbeat.redeliverableInboxCount, 3)
        self.assertIsNotNone(heartbeat.lastSweepDurationSeconds)

    def test_redelivery_uses_one_row_sweep_budget_not_an_uncalibrated_timeout(self) -> None:
        self.catalog.upsert(_entry("seat-1"))
        self.inbox_store.append(
            create_operator_inbox_entry(
                InboxMessage(ask="ask", response="resp"),
                entry_id="row-1",
                now=NOW.isoformat(),
                routing=InboxRouting(address=InboxAddress(lifecycle_id=None, agent_id="seat-1")),
                poster=InboxPoster(created_by="system", created_via="cli"),
            )
        )

        with mock.patch.object(agent_notifier_actions_module, "deliver_inbox_entry") as delivered:
            delivered.return_value = self.inbox_store.current()["row-1"]
            run_agent_notifier_sweep(self._ctx(), now=NOW)

        self.assertNotIn("submit_timeout", delivered.call_args.kwargs)
        self.assertEqual(self._ctx().redeliver_budget, 1)

    def test_repeated_seat_liveness_sweeps_coalesce_into_one_signal_row(self) -> None:
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        self.catalog.upsert(
            replace(
                _entry("worker-1", leaf_key="repo-a/260707_master/leaf-3").with_turn_state(
                    "stale", changed_at=(NOW - timedelta(minutes=5)).isoformat()
                ),
                spawn_role="worker",
                spawned_by_session="manager-1",
            )
        )
        ctx = self._ctx(signal_cooldown_seconds=900.0)

        run_agent_notifier_sweep(ctx, now=NOW)
        run_agent_notifier_sweep(ctx, now=NOW + timedelta(seconds=10))

        signal_rows = [
            entry
            for entry in self.inbox_store.current().values()
            if entry.messageKind == "escalation"
        ]
        self.assertEqual(len(signal_rows), 1)
        self.assertEqual(signal_rows[0].agentId, "manager-1")
        first = signal_rows[0]

        run_agent_notifier_sweep(ctx, now=NOW + timedelta(seconds=901))
        signal_rows = [
            entry
            for entry in self.inbox_store.current().values()
            if entry.messageKind == "escalation"
        ]
        # Ruled invariant (developer, 2026-07-09): past the cooldown the re-fired condition
        # RENEWS its one existing row (same id, bumped ts) instead of appending a duplicate.
        self.assertEqual(len(signal_rows), 1)
        self.assertEqual(signal_rows[0].id, first.id)
        self.assertGreater(signal_rows[0].ts, first.ts)

    def test_same_leaf_different_seat_roles_do_not_coalesce(self) -> None:
        leaf_key = "repo-a/260707_master/leaf-3"
        self.catalog.upsert(replace(_entry("manager-1", leaf_key=leaf_key), seat_role="manager"))
        for session_id, role in (("worker-1", "worker"), ("reviewer-1", "reviewer")):
            self.catalog.upsert(
                replace(
                    _entry(session_id, leaf_key=leaf_key),
                    seat_role=role,
                    spawn_role=role,
                    spawned_by_session="manager-1",
                )
            )

        for session_id, role in (("worker-1", "worker"), ("reviewer-1", "reviewer")):
            act_on_finding(
                self._ctx(),
                AgentNotifierFinding(
                    kind="seat-liveness",
                    detail="turn-state-stale",
                    session_id=session_id,
                    leaf_key=leaf_key,
                    seat_role=role,
                ),
                now=NOW,
            )

        signal_rows = [
            entry
            for entry in self.inbox_store.current().values()
            if entry.messageKind == "escalation"
        ]
        self.assertEqual(len(signal_rows), 2)
        self.assertEqual({entry.seatRole for entry in signal_rows}, {"worker", "reviewer"})

    def test_legacy_ask_pending_row_is_renewed_by_new_format_refire(self) -> None:
        # F1 regression (260713-TES-L1): a pre-window pending seat-liveness row carries the
        # legacy ask prefix and createdBy. A new-format re-fire must RENEW that one row (same
        # id, bumped ts), never append a second pending row -- the ruled one-row-per-root-cause
        # invariant survives the rename window.
        leaf_key = "repo-a/260707_master/leaf-3"
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        self.catalog.upsert(
            replace(
                _entry("worker-1", leaf_key=leaf_key).with_turn_state(
                    "stale", changed_at=(NOW - timedelta(minutes=5)).isoformat()
                ),
                spawn_role="worker",
                spawned_by_session="manager-1",
            )
        )
        self.inbox_store.append(
            create_operator_inbox_entry(
                InboxMessage(
                    ask="Supervisor observed seat-liveness: turn-state-stale",
                    response="session worker-1 (leaf repo-a/260707_master/leaf-3)",
                    message_kind="escalation",
                    subject=InboxSubject(
                        leaf_key=leaf_key, seat_role="worker", agent_id="worker-1"
                    ),
                ),
                entry_id="legacy-row",
                now=(NOW - timedelta(minutes=1)).isoformat(),
                routing=InboxRouting(
                    address=InboxAddress(
                        lifecycle_id=None, agent_id="manager-1", recipient_role="manager"
                    )
                ),
                poster=InboxPoster(created_by="supervisor", created_via="cli"),
            )
        )

        run_agent_notifier_sweep(self._ctx(signal_cooldown_seconds=900.0), now=NOW)

        rows = [e for e in self.inbox_store.current().values() if e.messageKind == "escalation"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, "legacy-row")
        self.assertGreater(rows[0].ts, (NOW - timedelta(minutes=1)).isoformat())

    def test_new_format_ask_row_is_renewed_by_new_format_refire(self) -> None:
        # Same seam, current-format path: prefix normalization must not break new/new coalescing.
        leaf_key = "repo-a/260707_master/leaf-3"
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        self.catalog.upsert(
            replace(
                _entry("worker-1", leaf_key=leaf_key).with_turn_state(
                    "stale", changed_at=(NOW - timedelta(minutes=5)).isoformat()
                ),
                spawn_role="worker",
                spawned_by_session="manager-1",
            )
        )
        self.inbox_store.append(
            create_operator_inbox_entry(
                InboxMessage(
                    ask="Agent notifier observed seat-liveness: turn-state-stale",
                    response="session worker-1 (leaf repo-a/260707_master/leaf-3)",
                    message_kind="escalation",
                    subject=InboxSubject(
                        leaf_key=leaf_key, seat_role="worker", agent_id="worker-1"
                    ),
                ),
                entry_id="current-row",
                now=(NOW - timedelta(minutes=1)).isoformat(),
                routing=InboxRouting(
                    address=InboxAddress(
                        lifecycle_id=None, agent_id="manager-1", recipient_role="manager"
                    )
                ),
                poster=InboxPoster(created_by="agent-notifier", created_via="cli"),
            )
        )

        run_agent_notifier_sweep(self._ctx(signal_cooldown_seconds=900.0), now=NOW)

        rows = [e for e in self.inbox_store.current().values() if e.messageKind == "escalation"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, "current-row")
        self.assertGreater(rows[0].ts, (NOW - timedelta(minutes=1)).isoformat())

    def test_diagnostic_pane_signal_is_not_actionable(self) -> None:
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        self.catalog.upsert(
            replace(
                _entry("worker-1", leaf_key="repo-a/260707_master/leaf-3"),
                spawn_role="worker",
                spawned_by_session="manager-1",
            )
        )
        finding = AgentNotifierFinding(
            kind="pane-signal",
            detail="mid-turn",
            session_id="worker-1",
            leaf_key="repo-a/260707_master/leaf-3",
        )

        result = act_on_finding(self._ctx(), finding, now=NOW)

        self.assertEqual(result.action, "none")
        self.assertEqual(result.outcome, "skipped")
        self.assertEqual(self.inbox_store.current(), {})

    def test_pending_backlog_does_not_burst_redeliver_before_floor_after_restart(self) -> None:
        for index in range(3):
            entry = create_operator_inbox_entry(
                InboxMessage(ask="ask", response="resp"),
                entry_id=f"row-{index}",
                now=NOW.isoformat(),
                routing=InboxRouting(
                    address=InboxAddress(lifecycle_id=None, agent_id=f"worker-{index}")
                ),
                poster=InboxPoster(created_by="system", created_via="cli"),
            ).model_copy(
                update={
                    "deliveryState": "delivered",
                    "attemptCount": 1,
                    "lastAttemptAt": NOW.isoformat(),
                    "nextAttemptAt": (NOW + timedelta(seconds=900)).isoformat(),
                }
            )
            self.inbox_store.append(entry)

        restarted_ctx = self._ctx()
        result = run_agent_notifier_sweep(restarted_ctx, now=NOW + timedelta(seconds=60))

        self.assertEqual([a for a in result.actions if a.action == "redeliver"], [])
        self.assertEqual(result.redeliverable_inbox_count, 0)

    def test_one_second_sweeps_do_not_emit_per_second_signal_rows(self) -> None:
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        self.catalog.upsert(
            replace(
                _entry("worker-1", leaf_key="repo-a/260707_master/leaf-3").with_turn_state(
                    "stale", changed_at=(NOW - timedelta(minutes=5)).isoformat()
                ),
                spawn_role="worker",
                spawned_by_session="manager-1",
            )
        )
        ctx = self._ctx(signal_cooldown_seconds=900.0)

        for tick in range(180):
            run_agent_notifier_sweep(ctx, now=NOW + timedelta(seconds=tick))

        signal_rows = [
            entry
            for entry in self.inbox_store.current().values()
            if entry.messageKind == "escalation"
        ]
        self.assertEqual(len(signal_rows), 1)
        heartbeat = self.heartbeat_store.read()
        assert heartbeat is not None
        self.assertEqual(heartbeat.sweepCount, 180)
