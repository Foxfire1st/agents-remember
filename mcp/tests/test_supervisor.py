"""Tests for the deterministic supervisor sweep (260707-HFX2-L2).

Predicate unit tests over store/pane fixtures (R6), plus one sweep integration test that seeds
drift across every predicate family and asserts the expected action set -- no model in the loop
anywhere: every fixture is a plain store write or a fake pane capturer.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from _scaling import assert_bounded_count
from agents_remember.controlplane.escalation_ladder import MAX_RUNG
from agents_remember.controlplane.expectation_rows import (
    ExpectationRowStore,
    write_expectation_row,
)
from agents_remember.controlplane.operator_inbox_records import create_operator_inbox_entry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.orchestration_nudges import OrchestrationNudgeStore
from agents_remember.controlplane.supervisor_signals import SupervisorSignalCooldownStore
from agents_remember.observer.store import EventStore
from agents_remember.serving import supervisor as supervisor_module
from agents_remember.serving.supervisor import (
    SupervisorContext,
    SupervisorFinding,
    act_on_finding,
    evaluate_dead_upstream_findings,
    evaluate_escalation_findings,
    evaluate_expectation_findings,
    evaluate_inbox_findings,
    evaluate_ladder_terminal_findings,
    evaluate_pane_findings,
    evaluate_seat_liveness_findings,
    evaluate_turn_report_findings,
    run_supervisor_sweep,
    turn_report_path_for_leaf_key,
)
from agents_remember.serving.supervisor_heartbeat import SupervisorHeartbeatStore
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import (
    SeatTurnState,
    TerminalCatalog,
    TerminalCatalogEntry,
    TerminalSessionKind,
    TerminalSessionStatus,
)
from agents_remember.serving.terminal_paste import PasteResult, TerminalPaster

NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)


def _entry(
    session_id: str,
    *,
    kind: TerminalSessionKind = "harness",
    status: TerminalSessionStatus = "running",
    leaf_key: str | None = None,
    turn_state: SeatTurnState | None = None,
    turn_state_changed_at: str | None = None,
    liveness_failures: int = 0,
) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=f"Chat {session_id}",
        kind=kind,
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at="2026-07-08T00:00:00+00:00",
        last_attached_at="2026-07-08T00:00:00+00:00",
        status=status,
        leaf_key=leaf_key,
        turn_state=turn_state,
        turn_state_changed_at=turn_state_changed_at,
        liveness_failures=liveness_failures,
    )


class _FakeHost:
    """The minimal ``deliver_inbox_entry`` seam: every catalog session is reachable."""

    def has_session(self, _tmux_name: str) -> bool:
        return True

    def terminate(self, _sid: str, *, tmux_name: str | None = None) -> None:
        pass


def _fake_paster() -> TerminalPaster:
    """An already-log-confirmed delivery seam for supervisor orchestration tests."""

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


class PanePredicateTests(unittest.TestCase):
    def test_mid_turn_pane_fires_a_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(_entry("s1"))
            findings = evaluate_pane_findings(catalog, pane_capturer=lambda _n: "esc to interrupt")
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].kind, "pane-signal")
            self.assertEqual(findings[0].detail, "mid-turn")
            self.assertEqual(findings[0].session_id, "s1")

    def test_normal_pane_fires_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(_entry("s1"))
            findings = evaluate_pane_findings(catalog, pane_capturer=lambda _n: "all clear")
            self.assertEqual(findings, [])

    def test_terminal_kind_rows_are_never_pane_classified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(_entry("s1", kind="terminal"))
            findings = evaluate_pane_findings(catalog, pane_capturer=lambda _n: "esc to interrupt")
            self.assertEqual(findings, [])


class ExpectationPredicateTests(unittest.TestCase):
    def test_overdue_briefed_by_row_fires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ExpectationRowStore(Path(tmp))
            write_expectation_row(
                store,
                row_id="r1",
                now=NOW - timedelta(minutes=10),
                kind="briefed-by",
                sla_seconds=60.0,
                source_id="s1",
                subject_agent_id="s1",
            )
            findings = evaluate_expectation_findings(store, now=NOW)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].source_id, "r1")

    def test_not_yet_due_row_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ExpectationRowStore(Path(tmp))
            write_expectation_row(
                store,
                row_id="r1",
                now=NOW,
                kind="briefed-by",
                sla_seconds=3600.0,
                source_id="s1",
            )
            self.assertEqual(evaluate_expectation_findings(store, now=NOW), [])


class TurnReportStalenessTests(unittest.TestCase):
    def test_missing_report_fires_when_row_is_overdue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coordination_root = Path(tmp) / "ar-coordination"
            store = ExpectationRowStore(coordination_root / "logs" / "observer")
            write_expectation_row(
                store,
                row_id="r1",
                now=NOW - timedelta(hours=2),
                kind="turn-report-by",
                sla_seconds=60.0,
                source_id="s1",
                subject_agent_id="s1",
                leaf_key="repo-a/260707_master/leaf-9",
            )
            findings = evaluate_turn_report_findings(
                store, coordination_root=coordination_root, now=NOW
            )
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].kind, "turn-report-stale")

    def test_present_report_does_not_fire(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coordination_root = Path(tmp) / "ar-coordination"
            store = ExpectationRowStore(coordination_root / "logs" / "observer")
            write_expectation_row(
                store,
                row_id="r1",
                now=NOW - timedelta(hours=2),
                kind="turn-report-by",
                sla_seconds=60.0,
                source_id="s1",
                leaf_key="repo-a/260707_master/leaf-9",
            )
            path = turn_report_path_for_leaf_key(coordination_root, "repo-a/260707_master/leaf-9")
            assert path is not None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# turn report\n", encoding="utf-8")
            findings = evaluate_turn_report_findings(
                store, coordination_root=coordination_root, now=NOW
            )
            self.assertEqual(findings, [])

    def test_malformed_leaf_key_is_skipped_not_guessed(self) -> None:
        self.assertIsNone(turn_report_path_for_leaf_key(Path("/coord"), "not-a-qualified-key"))


class InboxPredicateTests(unittest.TestCase):
    def test_pending_row_with_no_next_attempt_is_immediately_redeliverable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OperatorInboxStore(Path(tmp))
            entry = create_operator_inbox_entry(
                entry_id="e1",
                now=NOW.isoformat(),
                lifecycle_id=None,
                agent_id="s1",
                ask="ask",
                response="resp",
                created_by="system",
                created_via="cli",
            )
            store.append(entry)
            findings = evaluate_inbox_findings(store, now=NOW)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].source_id, "e1")

    def test_terminal_ladder_row_for_dead_seat_fires_ladder_terminal_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = OperatorInboxStore(root / "observer")
            catalog = TerminalCatalog(root / "catalog.json")
            entry = create_operator_inbox_entry(
                entry_id="e1",
                now=NOW.isoformat(),
                lifecycle_id=None,
                agent_id="dead-seat",
                ask="ask",
                response="resp",
                created_by="system",
                created_via="cli",
            ).model_copy(update={"rung": 3})
            store.append(entry)
            findings = evaluate_ladder_terminal_findings(store, catalog)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].kind, "inbox-ladder-terminal")
            self.assertEqual(findings[0].source_id, "e1")


class SeatLivenessPredicateTests(unittest.TestCase):
    def test_stale_turn_state_past_cutoff_fires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(
                _entry(
                    "s1",
                    turn_state="stale",
                    turn_state_changed_at=(NOW - timedelta(minutes=5)).isoformat(),
                )
            )
            findings = evaluate_seat_liveness_findings(catalog, now=NOW, stale_seconds=60.0)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].detail, "turn-state-stale")

    def test_recently_stale_does_not_fire_yet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(_entry("s1", turn_state="stale", turn_state_changed_at=NOW.isoformat()))
            self.assertEqual(
                evaluate_seat_liveness_findings(catalog, now=NOW, stale_seconds=60.0), []
            )

    def test_degraded_row_with_no_turn_state_uses_liveness_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(_entry("s1", liveness_failures=1))
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
                    _entry(
                        "worker-1",
                        leaf_key=leaf_key,
                        turn_state="stale",
                        turn_state_changed_at=(NOW - timedelta(minutes=10)).isoformat(),
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
                    _entry(
                        "worker-dead",
                        leaf_key=leaf_key,
                        turn_state="stale",
                        turn_state_changed_at=(NOW - timedelta(minutes=10)).isoformat(),
                    ),
                    spawn_role="worker",
                    spawned_by_session="manager-current",
                )
            )
            catalog.upsert(
                replace(
                    _entry("worker-replacement", turn_state="working"),
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
        self.signal_cooldown_store = SupervisorSignalCooldownStore(observer_root)
        self.event_store = EventStore(observer_root)
        self.heartbeat_store = SupervisorHeartbeatStore(observer_root)

    def _ctx(self, **overrides: object) -> SupervisorContext:
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
        return SupervisorContext(**base)  # type: ignore[arg-type]

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
            _entry(
                "stale-1",
                turn_state="stale",
                turn_state_changed_at=(NOW - timedelta(minutes=5)).isoformat(),
            ),
            spawn_role="worker",
            spawned_by_session="manager-1",
            cwd=Path("/workspace"),
            replacement_for_leaf="repo-a/260707_master/leaf-10",
        )
        self.catalog.upsert(stale_seat)

        # R2d: an unacked inbox row, immediately redeliverable.
        inbox_entry = create_operator_inbox_entry(
            entry_id="inbox-1",
            now=NOW.isoformat(),
            lifecycle_id=None,
            agent_id="worker-1",
            ask="ask",
            response="resp",
            created_by="system",
            created_via="cli",
        )
        self.inbox_store.append(inbox_entry)

        # R2b: an overdue briefed-by row for the worker.
        write_expectation_row(
            self.expectation_store,
            row_id="exp-1",
            now=NOW - timedelta(minutes=10),
            kind="briefed-by",
            sla_seconds=60.0,
            source_id="worker-1",
            subject_agent_id="worker-1",
            leaf_key="repo-a/260707_master/leaf-9",
        )

        ctx = self._ctx()
        result = run_supervisor_sweep(ctx, now=NOW)

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

        # R4e: every action is logged as an orchestration.supervisor.* (or reused nudge) event.
        events = self.event_store.read(None)
        kinds = {event.kind for event in events}
        self.assertTrue(
            kinds
            & {
                "orchestration.supervisor.redeliver",
                "orchestration.nudge",
                "orchestration.supervisor.signal",
            }
        )

    def test_finding_with_no_routable_owner_skips_its_action(self) -> None:
        # A seat with no spawn provenance -- derive_signal_owner has nothing to route to, so both
        # the nudge and signal-emit actions must skip rather than raise or fabricate an address.
        self.catalog.upsert(
            replace(
                _entry(
                    "orphan-1",
                    turn_state="stale",
                    turn_state_changed_at=(NOW - timedelta(minutes=5)).isoformat(),
                )
            )
        )
        write_expectation_row(
            self.expectation_store,
            row_id="exp-orphan",
            now=NOW - timedelta(minutes=10),
            kind="briefed-by",
            sla_seconds=60.0,
            source_id="orphan-1",
            subject_agent_id="orphan-1",
        )
        ctx = self._ctx()
        result = run_supervisor_sweep(ctx, now=NOW)
        outcomes = {a.action: a.outcome for a in result.actions}
        self.assertEqual(outcomes.get("auto-nudge"), "skipped")
        self.assertEqual(outcomes.get("signal-emit"), "skipped")

    def test_zero_drift_sweep_still_ticks_the_heartbeat(self) -> None:
        ctx = self._ctx()
        result = run_supervisor_sweep(ctx, now=NOW)
        self.assertEqual(result.findings, ())
        self.assertEqual(result.actions, ())
        heartbeat = self.heartbeat_store.read()
        assert heartbeat is not None
        self.assertEqual(heartbeat.sweepCount, 1)

    def test_second_sweep_bumps_sweep_count(self) -> None:
        ctx = self._ctx()
        run_supervisor_sweep(ctx, now=NOW)
        run_supervisor_sweep(ctx, now=NOW + timedelta(seconds=10))
        heartbeat = self.heartbeat_store.read()
        assert heartbeat is not None
        self.assertEqual(heartbeat.sweepCount, 2)

    def test_terminal_dead_seat_row_becomes_ladder_resolved_not_redelivered(self) -> None:
        entry = create_operator_inbox_entry(
            entry_id="dead-row",
            now=NOW.isoformat(),
            lifecycle_id=None,
            agent_id="missing-seat",
            ask="ask",
            response="resp",
            created_by="system",
            created_via="cli",
        ).model_copy(update={"rung": 3})
        self.inbox_store.append(entry)

        result = run_supervisor_sweep(self._ctx(), now=NOW)

        actions = {action.action: action for action in result.actions}
        self.assertIn("ladder-resolve", actions)
        self.assertNotIn("redeliver", actions)
        resolved = self.inbox_store.current()["dead-row"]
        self.assertEqual(resolved.state, "ladder-resolved")
        event_kinds = {event.kind for event in self.event_store.read(None)}
        self.assertIn("orchestration.supervisor.ladder-resolved", event_kinds)

    def test_redeliver_budget_limits_attempts_and_heartbeat_reports_backlog(self) -> None:
        for index in range(3):
            self.inbox_store.append(
                create_operator_inbox_entry(
                    entry_id=f"row-{index}",
                    now=NOW.isoformat(),
                    lifecycle_id=None,
                    agent_id=f"missing-seat-{index}",
                    ask="ask",
                    response="resp",
                    created_by="system",
                    created_via="cli",
                )
            )

        result = run_supervisor_sweep(self._ctx(redeliver_budget=1), now=NOW)

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
                entry_id="row-1",
                now=NOW.isoformat(),
                lifecycle_id=None,
                agent_id="seat-1",
                ask="ask",
                response="resp",
                created_by="system",
                created_via="cli",
            )
        )

        with mock.patch.object(supervisor_module, "deliver_inbox_entry") as delivered:
            delivered.return_value = self.inbox_store.current()["row-1"]
            run_supervisor_sweep(self._ctx(), now=NOW)

        self.assertNotIn("submit_timeout", delivered.call_args.kwargs)
        self.assertEqual(self._ctx().redeliver_budget, 1)

    def test_repeated_seat_liveness_sweeps_coalesce_into_one_signal_row(self) -> None:
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        self.catalog.upsert(
            replace(
                _entry(
                    "worker-1",
                    leaf_key="repo-a/260707_master/leaf-3",
                    turn_state="stale",
                    turn_state_changed_at=(NOW - timedelta(minutes=5)).isoformat(),
                ),
                spawn_role="worker",
                spawned_by_session="manager-1",
            )
        )
        ctx = self._ctx(signal_cooldown_seconds=900.0)

        run_supervisor_sweep(ctx, now=NOW)
        run_supervisor_sweep(ctx, now=NOW + timedelta(seconds=10))

        signal_rows = [
            entry
            for entry in self.inbox_store.current().values()
            if entry.messageKind == "escalation"
        ]
        self.assertEqual(len(signal_rows), 1)
        self.assertEqual(signal_rows[0].agentId, "manager-1")
        first = signal_rows[0]

        run_supervisor_sweep(ctx, now=NOW + timedelta(seconds=901))
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
                SupervisorFinding(
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

    def test_diagnostic_pane_signal_is_not_actionable(self) -> None:
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        self.catalog.upsert(
            replace(
                _entry("worker-1", leaf_key="repo-a/260707_master/leaf-3"),
                spawn_role="worker",
                spawned_by_session="manager-1",
            )
        )
        finding = SupervisorFinding(
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
                entry_id=f"row-{index}",
                now=NOW.isoformat(),
                lifecycle_id=None,
                agent_id=f"worker-{index}",
                ask="ask",
                response="resp",
                created_by="system",
                created_via="cli",
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
        result = run_supervisor_sweep(restarted_ctx, now=NOW + timedelta(seconds=60))

        self.assertEqual([a for a in result.actions if a.action == "redeliver"], [])
        self.assertEqual(result.redeliverable_inbox_count, 0)

    def test_one_second_sweeps_do_not_emit_per_second_signal_rows(self) -> None:
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        self.catalog.upsert(
            replace(
                _entry(
                    "worker-1",
                    leaf_key="repo-a/260707_master/leaf-3",
                    turn_state="stale",
                    turn_state_changed_at=(NOW - timedelta(minutes=5)).isoformat(),
                ),
                spawn_role="worker",
                spawned_by_session="manager-1",
            )
        )
        ctx = self._ctx(signal_cooldown_seconds=900.0)

        for tick in range(180):
            run_supervisor_sweep(ctx, now=NOW + timedelta(seconds=tick))

        signal_rows = [
            entry
            for entry in self.inbox_store.current().values()
            if entry.messageKind == "escalation"
        ]
        self.assertEqual(len(signal_rows), 1)
        heartbeat = self.heartbeat_store.read()
        assert heartbeat is not None
        self.assertEqual(heartbeat.sweepCount, 180)


class EscalationPredicateTests(unittest.TestCase):
    def test_delivery_failure_waits_for_retry_exhaustion_before_escalating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OperatorInboxStore(Path(tmp))
            for entry_id, attempt_count in (
                ("retrying", supervisor_module.PERSISTENT_FAILURE_ATTEMPTS - 1),
                ("exhausted", supervisor_module.PERSISTENT_FAILURE_ATTEMPTS),
            ):
                store.append(
                    create_operator_inbox_entry(
                        entry_id=entry_id,
                        now=(NOW - timedelta(minutes=10)).isoformat(),
                        lifecycle_id=None,
                        agent_id="worker-1",
                        ask="ask",
                        response="resp",
                        created_by="system",
                        created_via="cli",
                        message_kind="escalation",
                    ).model_copy(
                        update={
                            "deliveryState": "no-hosted-session",
                            "attemptCount": attempt_count,
                        }
                    )
                )

            findings = evaluate_escalation_findings(
                store, now=NOW, sla_seconds={"escalation": 60.0}, rung_seconds={}
            )

            self.assertEqual([finding.source_id for finding in findings], ["exhausted"])

    def test_dispatch_failure_never_enters_generic_escalation_ladder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OperatorInboxStore(Path(tmp))
            store.append(
                create_operator_inbox_entry(
                    entry_id="dispatch-1",
                    now=(NOW - timedelta(minutes=10)).isoformat(),
                    lifecycle_id=None,
                    agent_id="worker-1",
                    ask="brief",
                    response="work",
                    created_by="manager-1",
                    created_via="cli",
                    message_kind="dispatch-brief",
                ).model_copy(
                    update={
                        "deliveryState": "unconfirmed",
                        "attemptCount": supervisor_module.PERSISTENT_FAILURE_ATTEMPTS + 10,
                    }
                )
            )

            findings = evaluate_escalation_findings(
                store, now=NOW, sla_seconds={"dispatch-brief": 60.0}, rung_seconds={}
            )

            self.assertEqual(findings, [])

    def test_pending_row_past_sla_fires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OperatorInboxStore(Path(tmp))
            entry = create_operator_inbox_entry(
                entry_id="e1",
                now=(NOW - timedelta(minutes=10)).isoformat(),
                lifecycle_id=None,
                agent_id="worker-1",
                ask="ask",
                response="resp",
                created_by="system",
                created_via="cli",
                message_kind="escalation",
            )
            store.append(entry)
            findings = evaluate_escalation_findings(
                store, now=NOW, sla_seconds={"escalation": 60.0}, rung_seconds={}
            )
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].kind, "escalation-due")
            self.assertEqual(findings[0].source_id, "e1")

    def test_not_yet_due_row_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OperatorInboxStore(Path(tmp))
            entry = create_operator_inbox_entry(
                entry_id="e1",
                now=NOW.isoformat(),
                lifecycle_id=None,
                agent_id="worker-1",
                ask="ask",
                response="resp",
                created_by="system",
                created_via="cli",
                message_kind="escalation",
            )
            store.append(entry)
            findings = evaluate_escalation_findings(
                store, now=NOW, sla_seconds={"escalation": 3600.0}, rung_seconds={}
            )
            self.assertEqual(findings, [])

    def test_leaf_chain_progress_suppresses_inactivity_signal_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = OperatorInboxStore(root / "observer")
            catalog = TerminalCatalog(root / "catalog.json")
            leaf_key = "repo-a/260707_master/leaf-9"
            catalog.upsert(
                replace(
                    _entry("manager-current", leaf_key="repo-a/260707_master/manager-anchor"),
                    spawn_role="manager",
                )
            )
            catalog.upsert(
                replace(
                    _entry("worker-1", leaf_key=leaf_key),
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
            store.append(
                create_operator_inbox_entry(
                    entry_id="e1",
                    now=(NOW - timedelta(minutes=10)).isoformat(),
                    lifecycle_id=None,
                    agent_id="manager-current",
                    ask="Supervisor observed seat-liveness: turn-state-stale",
                    response="worker-1 inactive",
                    created_by="supervisor",
                    created_via="cli",
                    message_kind="escalation",
                    recipient_role="manager",
                    leaf_key=leaf_key,
                    subject_agent_id="worker-1",
                ).model_copy(
                    update={
                        "rung": 1,
                        "escalatedAt": (NOW - timedelta(minutes=10)).isoformat(),
                    }
                )
            )

            self.assertEqual(
                evaluate_escalation_findings(
                    store,
                    now=NOW,
                    sla_seconds={"escalation": 60.0},
                    rung_seconds={1: 60.0},
                    catalog=catalog,
                ),
                [],
            )


class DeadUpstreamPredicateTests(unittest.TestCase):
    def test_worker_with_dead_manager_fires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(replace(_entry("manager-1"), status="terminated", spawn_role="manager"))
            catalog.upsert(
                replace(_entry("worker-1"), spawn_role="worker", spawned_by_session="manager-1")
            )
            findings = evaluate_dead_upstream_findings(catalog)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].kind, "dead-upstream")
            self.assertEqual(findings[0].session_id, "worker-1")

    def test_live_owner_does_not_fire(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
            catalog.upsert(
                replace(_entry("worker-1"), spawn_role="worker", spawned_by_session="manager-1")
            )
            self.assertEqual(evaluate_dead_upstream_findings(catalog), [])

    def test_no_provenance_at_all_does_not_fire(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(replace(_entry("worker-1"), spawn_role="worker"))
            self.assertEqual(evaluate_dead_upstream_findings(catalog), [])


class LadderWalkIntegrationTests(unittest.TestCase):
    """R6 fixtures: silent seat, dead intermediate, dead manager with live workers."""

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
        self.signal_cooldown_store = SupervisorSignalCooldownStore(observer_root)
        self.event_store = EventStore(observer_root)
        self.heartbeat_store = SupervisorHeartbeatStore(observer_root)

    def _ctx(self, **overrides: object) -> SupervisorContext:
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
            escalation_sla_seconds={"escalation": 60.0},
            escalation_rung_seconds={1: 60.0, 2: 60.0},
            respawn_after_rung=2,
        )
        base.update(overrides)
        return SupervisorContext(**base)  # type: ignore[arg-type]

    def _events(self) -> set[str]:
        return {event.kind for event in self.event_store.read(None)}

    def test_delivered_dispatch_never_readdresses_at_rung_two(self) -> None:
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        self.catalog.upsert(
            replace(_entry("worker-1"), spawn_role="worker", spawned_by_session="manager-1")
        )
        entry = create_operator_inbox_entry(
            entry_id="dispatch-1",
            now=(NOW - timedelta(minutes=10)).isoformat(),
            lifecycle_id=None,
            agent_id="worker-1",
            ask="brief",
            response="work",
            created_by="manager-1",
            created_via="cli",
            message_kind="dispatch-brief",
            recipient_role="worker",
        ).model_copy(
            update={
                "deliveryState": "delivered",
                "deliveryDetail": "harness-log-confirmed",
                "rung": 1,
                "escalatedAt": (NOW - timedelta(minutes=5)).isoformat(),
            }
        )
        self.inbox_store.append(entry)

        self.assertEqual(
            evaluate_escalation_findings(
                self.inbox_store,
                now=NOW,
                sla_seconds={"dispatch-brief": 60.0},
                rung_seconds={1: 60.0},
                catalog=self.catalog,
            ),
            [],
        )
        finding = SupervisorFinding(
            kind="escalation-due",
            detail="dispatch-brief",
            session_id="worker-1",
            source_id=entry.id,
        )
        with mock.patch.object(supervisor_module, "deliver_inbox_entry") as deliver:
            result = act_on_finding(self._ctx(), finding, now=NOW)

        deliver.assert_not_called()
        current = self.inbox_store.current()[entry.id]
        self.assertEqual(result.detail, "dispatch brief stays on its exact session")
        self.assertEqual(current.rung, 1)
        self.assertEqual(current.agentId, "worker-1")

    def test_silent_seat_climbs_rung_one_then_two_then_three(self) -> None:
        self.catalog.upsert(replace(_entry("orchestrator-1"), spawn_role="orchestrator"))
        self.catalog.upsert(
            replace(_entry("manager-1"), spawn_role="manager", spawned_by_session="orchestrator-1")
        )
        self.catalog.upsert(
            replace(_entry("worker-1"), spawn_role="worker", spawned_by_session="manager-1")
        )
        entry = create_operator_inbox_entry(
            entry_id="e1",
            now=(NOW - timedelta(minutes=5)).isoformat(),
            lifecycle_id=None,
            agent_id="worker-1",
            ask="ask",
            response="resp",
            created_by="system",
            created_via="cli",
            message_kind="escalation",
            recipient_role="worker",
        )
        self.inbox_store.append(entry)

        ctx = self._ctx()
        run_supervisor_sweep(ctx, now=NOW)
        rung1 = self.inbox_store.current()["e1"]
        self.assertEqual(rung1.rung, 1)
        self.assertIn("orchestration.escalation.rung", self._events())

        run_supervisor_sweep(ctx, now=NOW + timedelta(minutes=2))
        still_rung1 = self.inbox_store.current()["e1"]
        self.assertEqual(still_rung1.rung, 1)

        run_supervisor_sweep(ctx, now=NOW + timedelta(minutes=5))
        rung2 = self.inbox_store.current()["e1"]
        self.assertEqual(rung2.rung, 2)

        run_supervisor_sweep(ctx, now=NOW + timedelta(minutes=7))
        still_rung2 = self.inbox_store.current()["e1"]
        self.assertEqual(still_rung2.rung, 2)

        run_supervisor_sweep(ctx, now=NOW + timedelta(minutes=10))
        rung3 = self.inbox_store.current()["e1"]
        self.assertEqual(rung3.rung, 3)

        # Rung 3 is terminal -- a further sweep never advances it past the developer.
        run_supervisor_sweep(ctx, now=NOW + timedelta(minutes=20))
        still_rung3 = self.inbox_store.current()["e1"]
        self.assertEqual(still_rung3.rung, 3)

        # Ruled invariant (developer, 2026-07-09): the whole climb mutated the ONE root row --
        # re-addressed up the chain, original ask preserved, and NO sibling rows minted (the
        # sibling-per-transition shape is the branching process behind the 2026-07-09 storm).
        # Terminal custody is the ARCHITECT (role-addressed here: no architect seat is attached,
        # so the row waits, level-triggered, for the next architect session).
        self.assertEqual(len(self.inbox_store.current()), 1)
        self.assertEqual(still_rung3.ask, "ask")
        self.assertEqual(still_rung3.recipientRole, "architect")
        self.assertIsNone(still_rung3.agentId)

    def test_duplicate_due_findings_cannot_advance_two_rungs_in_one_sweep(self) -> None:
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        self.catalog.upsert(
            replace(_entry("worker-1"), spawn_role="worker", spawned_by_session="manager-1")
        )
        self.inbox_store.append(
            create_operator_inbox_entry(
                entry_id="e1",
                now=(NOW - timedelta(minutes=5)).isoformat(),
                lifecycle_id=None,
                agent_id="worker-1",
                ask="ask",
                response="resp",
                created_by="system",
                created_via="cli",
                message_kind="escalation",
                recipient_role="worker",
            )
        )
        duplicate = SupervisorFinding(
            kind="escalation-due",
            detail="escalation",
            session_id="worker-1",
            source_id="e1",
        )
        with mock.patch.object(
            supervisor_module, "evaluate_predicates", return_value=[duplicate, duplicate]
        ):
            result = run_supervisor_sweep(self._ctx(), now=NOW)

        self.assertEqual(self.inbox_store.current()["e1"].rung, 1)
        self.assertEqual(result.actions[1].detail, "entry already transitioned this sweep")

    def test_dead_intermediate_manager_is_skipped_at_rung_two(self) -> None:
        self.catalog.upsert(replace(_entry("orchestrator-1"), spawn_role="orchestrator"))
        self.catalog.upsert(
            replace(
                _entry("manager-1"),
                status="terminated",
                spawn_role="manager",
                spawned_by_session="orchestrator-1",
            )
        )
        self.catalog.upsert(
            replace(_entry("worker-1"), spawn_role="worker", spawned_by_session="manager-1")
        )
        entry = create_operator_inbox_entry(
            entry_id="e1",
            now=(NOW - timedelta(minutes=5)).isoformat(),
            lifecycle_id=None,
            agent_id="worker-1",
            ask="ask",
            response="resp",
            created_by="system",
            created_via="cli",
            message_kind="escalation",
            recipient_role="worker",
        ).model_copy(update={"rung": 1, "escalatedAt": (NOW - timedelta(minutes=5)).isoformat()})
        self.inbox_store.append(entry)

        ctx = self._ctx()
        run_supervisor_sweep(ctx, now=NOW)
        advanced = self.inbox_store.current()["e1"]
        self.assertEqual(advanced.rung, 2)
        # The dead manager is skipped -- the row lands on the orchestrator instead.
        events = [
            event
            for event in self.event_store.read(None)
            if event.kind == "orchestration.escalation.rung"
        ]
        self.assertEqual(events[-1].data["ownerAgentId"], "orchestrator-1")

    def test_dead_manager_with_live_workers_respawns_and_surfaces_orphans(self) -> None:
        self.catalog.upsert(replace(_entry("orchestrator-1"), spawn_role="orchestrator"))
        self.catalog.upsert(
            replace(
                _entry("manager-1"),
                spawn_role="manager",
                spawned_by_session="orchestrator-1",
                turn_state="stale",
                turn_state_changed_at=(NOW - timedelta(minutes=10)).isoformat(),
            )
        )
        self.catalog.upsert(
            replace(_entry("worker-1"), spawn_role="worker", spawned_by_session="manager-1")
        )
        self.catalog.upsert(
            replace(_entry("worker-2"), spawn_role="worker", spawned_by_session="manager-1")
        )
        entry = create_operator_inbox_entry(
            entry_id="e1",
            now=(NOW - timedelta(minutes=5)).isoformat(),
            lifecycle_id=None,
            agent_id="manager-1",
            ask="ask",
            response="resp",
            created_by="system",
            created_via="cli",
            message_kind="escalation",
            recipient_role="manager",
        ).model_copy(update={"rung": 1, "escalatedAt": (NOW - timedelta(minutes=5)).isoformat()})
        self.inbox_store.append(entry)

        ctx = self._ctx()
        run_supervisor_sweep(ctx, now=NOW)

        # The suspect manager's husk is retired (catalog status flips to terminated)...
        retired = self.catalog.get("manager-1")
        assert retired is not None
        self.assertEqual(retired.status, "terminated")

        # ...and its live workers are surfaced as orphans in the respawn event, never re-parented
        # automatically and never absorbing the dead manager's role themselves.
        respawn_events = [
            event
            for event in self.event_store.read(None)
            if event.kind == "orchestration.supervisor.respawn"
        ]
        self.assertEqual(len(respawn_events), 1)
        self.assertEqual(
            sorted(respawn_events[0].data["orphanedWorkers"]), ["worker-1", "worker-2"]
        )
        self.assertEqual(respawn_events[0].data["ownerRole"], "orchestrator")
        self.assertEqual(respawn_events[0].data["ownerAgentId"], "orchestrator-1")

        # The workers themselves are untouched -- still running, still their own seats.
        worker1 = self.catalog.get("worker-1")
        worker2 = self.catalog.get("worker-2")
        assert worker1 is not None and worker2 is not None
        self.assertEqual(worker1.status, "running")
        self.assertEqual(worker2.status, "running")

    def test_rung_three_re_addresses_the_row_to_the_live_architect_seat(self) -> None:
        """Ruled terminal (developer, 2026-07-09): with an architect session attached, terminal
        custody lands on that seat (agent-addressed, deliverable, mechanically ackable) -- the
        one live seat whose job is to hold the item and brief the human."""
        self.catalog.upsert(replace(_entry("architect-1"), spawn_role="architect"))
        self.catalog.upsert(replace(_entry("orchestrator-1"), spawn_role="orchestrator"))
        entry = create_operator_inbox_entry(
            entry_id="e1",
            now=(NOW - timedelta(minutes=30)).isoformat(),
            lifecycle_id=None,
            agent_id="orchestrator-1",
            ask="master completed",
            response="resp",
            created_by="system",
            created_via="cli",
            message_kind="escalation",
            recipient_role="orchestrator",
        ).model_copy(update={"rung": 2, "escalatedAt": (NOW - timedelta(minutes=5)).isoformat()})
        self.inbox_store.append(entry)

        run_supervisor_sweep(self._ctx(), now=NOW)

        advanced = self.inbox_store.current()["e1"]
        self.assertEqual(advanced.rung, 3)
        self.assertEqual(advanced.recipientRole, "architect")
        self.assertEqual(advanced.agentId, "architect-1")
        self.assertEqual(len(self.inbox_store.current()), 1)

    def test_unacked_backlog_reaches_a_fixed_point_with_absent_developer(self) -> None:
        """The 2026-07-09 meltdown regression (quiescence probe the HFX2-L12 audit lacked):
        with NO acks, NO live seats, and hours of sweeps, the inbox must reach a fixed point --
        exactly the seeded root-cause rows, rungs capped at MAX_RUNG, on-disk log bounded near
        folded size. The pre-fix ladder diverged here: every rung transition minted a new
        ladder-eligible pending row, so an absent developer grew 67k lines / 227 MB overnight."""
        seeded = 9
        for index in range(seeded):
            self.inbox_store.append(
                create_operator_inbox_entry(
                    entry_id=f"root-{index}",
                    now=NOW.isoformat(),
                    lifecycle_id=None,
                    agent_id=f"dead-seat-{index}",
                    ask=f"turn report {index}",
                    response="resp",
                    created_by="system",
                    created_via="cli",
                    message_kind="turn-report",
                    recipient_role="manager",
                )
            )

        ctx = self._ctx()
        moment = NOW
        for _ in range(50):  # 50 sweeps x 6 min = 5 hours of absent developer
            moment += timedelta(minutes=6)
            run_supervisor_sweep(ctx, now=moment)
            current = self.inbox_store.current()
            self.assertLessEqual(len(current), seeded)
            self.assertTrue(all(entry.rung <= MAX_RUNG for entry in current.values()))

        final = self.inbox_store.current()
        self.assertEqual(len(final), seeded)
        self.assertEqual(sorted(final), [f"root-{index}" for index in range(seeded)])
        # Per-sweep compaction keeps the on-disk log within one sweep's appends of folded size.
        lines = [
            line
            for line in self.inbox_store.log_path().read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        # One row reaches a nine-snapshot fixed point (initial row, delivery/rung/escalation marks);
        # the divergent pre-fix shape produced THOUSANDS of lines here.
        self.assertLessEqual(len(lines), seeded * 9)

    def test_dead_upstream_signals_the_current_manager(self) -> None:
        self.catalog.upsert(replace(_entry("orchestrator-1"), spawn_role="orchestrator"))
        self.catalog.upsert(
            replace(
                _entry("manager-1"),
                status="terminated",
                spawn_role="manager",
                spawned_by_session="orchestrator-1",
                leaf_key="repo-a/260707_master/old-manager-anchor",
            )
        )
        self.catalog.upsert(
            replace(
                _entry("worker-1", leaf_key="repo-a/260707_master/leaf-1"),
                spawn_role="worker",
                spawned_by_session="manager-1",
            )
        )
        self.catalog.upsert(
            replace(
                _entry("manager-2", leaf_key="repo-a/260707_master/current-manager-anchor"),
                spawn_role="manager",
            )
        )
        ctx = self._ctx()
        result = run_supervisor_sweep(ctx, now=NOW)
        dead_upstream_findings = [f for f in result.findings if f.kind == "dead-upstream"]
        self.assertEqual(len(dead_upstream_findings), 1)
        self.assertEqual(dead_upstream_findings[0].session_id, "worker-1")
        events = [
            event
            for event in self.event_store.read(None)
            if event.kind == "orchestration.supervisor.dead-upstream"
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data["managerAgentId"], "manager-2")


class Cs6SweepScalingTests(unittest.TestCase):
    """260707-HFX2-L12 CS-6 sweep regressions: the store reads + escalation emission a single
    sweep does must NOT scale with the finding count (the L7 accidental-quadratic floor)."""

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
        self.signal_cooldown_store = SupervisorSignalCooldownStore(observer_root)
        self.event_store = EventStore(observer_root)
        self.heartbeat_store = SupervisorHeartbeatStore(observer_root)

    def _ctx(self, **overrides: object) -> SupervisorContext:
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
        return SupervisorContext(**base)  # type: ignore[arg-type]

    def _wrap_reads(self, store: object) -> dict[str, int]:
        counter = {"count": 0}
        original = store.read  # type: ignore[attr-defined]

        def counting_read(*args, **kwargs):  # type: ignore[no-untyped-def]
            counter["count"] += 1
            return original(*args, **kwargs)

        store.read = counting_read  # type: ignore[attr-defined]
        return counter

    def _seed_stale_workers(self, count: int) -> None:
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        for index in range(count):
            self.catalog.upsert(
                replace(
                    _entry(
                        f"worker-{index}",
                        leaf_key=f"repo/260707_master/leaf-{index}",
                        turn_state="stale",
                        turn_state_changed_at=(NOW - timedelta(minutes=5)).isoformat(),
                    ),
                    spawn_role="worker",
                    spawned_by_session="manager-1",
                )
            )

    def test_signal_cooldown_store_read_at_most_once_per_sweep_regardless_of_findings(self) -> None:
        """Z1: the seeded finding. F seat-liveness findings -> F cooldown checks, but the signal
        log is read ONCE per sweep (in compact) via the threaded snapshot -- not once per finding
        (the O(F x L) freeze the L9 reviewer flagged)."""
        for worker_count in (2, 40):
            with self.subTest(workers=worker_count):
                self.setUp()
                self._seed_stale_workers(worker_count)
                counter = self._wrap_reads(self.signal_cooldown_store)
                result = run_supervisor_sweep(self._ctx(), now=NOW)
                signal_emits = [a for a in result.actions if a.action == "signal-emit"]
                self.assertEqual(len(signal_emits), worker_count)  # every finding hit in_cooldown
                assert_bounded_count(
                    counter["count"], 1, label=f"signal reads/sweep at F={worker_count}"
                )
                heartbeat = self.heartbeat_store.read()
                assert heartbeat is not None
                self.assertEqual(heartbeat.sweepCount, 1)

    def test_expectation_store_reads_do_not_scale_with_overdue_finding_count(self) -> None:
        """Z4b: K overdue expectations -> K mark_missed calls, but each uses the sweep's one-read
        snapshot, so total expectation-store reads stay flat instead of growing by K."""
        reads_by_k: dict[int, int] = {}
        for overdue_count in (2, 40):
            self.setUp()
            self.catalog.upsert(replace(_entry("manager-1"), spawn_role="orchestrator"))
            self.catalog.upsert(
                replace(
                    _entry("worker-1", leaf_key="repo/260707_master/leaf-1"),
                    spawn_role="worker",
                    spawned_by_session="manager-1",
                )
            )
            for index in range(overdue_count):
                write_expectation_row(
                    self.expectation_store,
                    row_id=f"exp-{index}",
                    now=NOW - timedelta(minutes=10),
                    kind="briefed-by",
                    sla_seconds=60.0,
                    source_id=f"seat-{index}",
                    subject_agent_id="worker-1",
                    leaf_key="repo/260707_master/leaf-1",
                )
            counter = self._wrap_reads(self.expectation_store)
            result = run_supervisor_sweep(self._ctx(), now=NOW)
            overdue_findings = [f for f in result.findings if f.kind == "expectation-overdue"]
            self.assertEqual(len(overdue_findings), overdue_count)
            reads_by_k[overdue_count] = counter["count"]
        # Reads are flat in K (the fix); the pre-fix code did one full fold per overdue finding.
        self.assertEqual(
            reads_by_k[40],
            reads_by_k[2],
            f"expectation reads scaled with finding count: {reads_by_k}",
        )
        assert_bounded_count(reads_by_k[40], 6, label="expectation reads/sweep")

    def test_escalation_budget_caps_rung_emission_per_sweep(self) -> None:
        """Z17: a river-storm-sized backlog of rung-due rows emits at most escalation_budget
        escalation findings per sweep; the rest stay rung_due and re-fire next sweep."""
        for pending_count in (20, 60):
            with self.subTest(pending=pending_count):
                self.setUp()
                for index in range(pending_count):
                    self.inbox_store.append(
                        create_operator_inbox_entry(
                            entry_id=f"esc-{index}",
                            now=(NOW - timedelta(minutes=10)).isoformat(),
                            lifecycle_id=None,
                            agent_id=f"worker-{index}",
                            ask="ask",
                            response="resp",
                            created_by="system",
                            created_via="cli",
                            message_kind="escalation",
                        )
                    )
                result = run_supervisor_sweep(
                    self._ctx(
                        escalation_budget=5,
                        escalation_sla_seconds={"escalation": 60.0},
                    ),
                    now=NOW,
                )
                escalation_findings = [f for f in result.findings if f.kind == "escalation-due"]
                assert_bounded_count(
                    len(escalation_findings), 5, label=f"escalation findings at N={pending_count}"
                )
                self.assertEqual(len(escalation_findings), 5)  # budget-full, not fewer
                heartbeat = self.heartbeat_store.read()
                assert heartbeat is not None
                self.assertEqual(heartbeat.sweepCount, 1)


if __name__ == "__main__":
    unittest.main()
