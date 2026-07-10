"""End-to-end liveness simulations (260707-HFX2-L5, R3/S4).

The P-15 "predicate fixture zoo" becomes focused, END-TO-END simulations of the whole L1-L4
liveness stack (expectation rows -> supervisor sweep -> paste injector -> escalation ladder):
every named incident class must end acked-or-escalated within SLA with zero human/model
intervention. L1-L4's own test suites already prove each PIECE in isolation (predicate unit
tests, ladder-walk unit tests, injector outcome-mapping unit tests) -- this file's job is driving
``run_supervisor_sweep`` across MULTIPLE simulated ticks per named incident and asserting the
whole chain converges, the way ``LadderWalkIntegrationTests`` in ``test_supervisor.py`` already
does for its own two fixtures (this file deliberately reuses that exact setup/``_ctx`` shape
rather than re-inventing one).

Composer contents and paste-chip rendering are deliberately absent from these simulations: harness
session logs, not pane vocabulary, determine delivery acceptance.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import cast

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.expectation_rows import (
    ExpectationRowStore,
    write_expectation_row,
)
from agents_remember.controlplane.interaction_retention import INBOX_MAX_CURRENT_ROWS
from agents_remember.controlplane.operator_inbox_records import create_operator_inbox_entry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.orchestration_nudges import OrchestrationNudgeStore
from agents_remember.controlplane.supervisor_signals import SupervisorSignalCooldownStore
from agents_remember.observer.store import EventStore
from agents_remember.serving.supervisor import (
    PERSISTENT_FAILURE_ATTEMPTS,
    SupervisorContext,
    evaluate_seat_liveness_findings,
    run_supervisor_sweep,
)
from agents_remember.serving.supervisor_heartbeat import (
    SupervisorHeartbeatStore,
    supervisor_staleness_banner,
)
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
    """Every catalog session is reachable; a scenario that needs the opposite (#16) overrides."""

    def __init__(self, *, reachable: bool = True) -> None:
        self.reachable = reachable

    def has_session(self, _tmux_name: str) -> bool:
        return self.reachable

    def terminate(self, _sid: str, *, tmux_name: str | None = None) -> None:
        pass


def _landing_paster() -> TerminalPaster:
    """An already-log-confirmed healthy delivery fixture."""

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


class _StubPaster:
    """Returns one fixed :class:`PasteResult` regardless of what is pasted -- pure outcome control
    for a scenario that needs the SAME pane state on every attempt (a stuck modal, a busy pane)."""

    def __init__(self, result: PasteResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str, bool]] = []

    def paste(
        self, tmux_name: str, text: str, *, submit: bool = False, **_kwargs: object
    ) -> PasteResult:
        self.calls.append((tmux_name, text, submit))
        return self.result


class _LivenessSimulationCase(unittest.TestCase):
    """Shared multi-tick harness -- mirrors ``LadderWalkIntegrationTests`` in test_supervisor.py."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.coordination_root = root / "ar-coordination"
        self.observer_root = self.coordination_root / "logs" / "observer"
        self.catalog = TerminalCatalog(root / "catalog.json")
        self.inbox_store = OperatorInboxStore(self.observer_root)
        self.expectation_store = ExpectationRowStore(self.observer_root)
        self.nudge_store = OrchestrationNudgeStore(self.observer_root)
        self.signal_cooldown_store = SupervisorSignalCooldownStore(self.observer_root)
        self.event_store = EventStore(self.observer_root)
        self.heartbeat_store = SupervisorHeartbeatStore(self.observer_root)

    def _ctx(self, *, host: TerminalHost | None = None, paster: TerminalPaster | None = None, **overrides: object) -> SupervisorContext:
        base: dict[str, object] = dict(
            catalog=self.catalog,
            host=host if host is not None else cast(TerminalHost, _FakeHost()),
            paster=paster if paster is not None else _landing_paster(),
            inbox_store=self.inbox_store,
            expectation_store=self.expectation_store,
            nudge_store=self.nudge_store,
            signal_cooldown_store=self.signal_cooldown_store,
            event_store=self.event_store,
            heartbeat_store=self.heartbeat_store,
            coordination_root=self.coordination_root,
            stale_seat_seconds=60.0,
            escalation_sla_seconds={"nudge": 60.0, "escalation": 60.0},
            escalation_rung_seconds={1: 60.0, 2: 60.0},
            respawn_after_rung=2,
        )
        base.update(overrides)
        return SupervisorContext(**base)  # type: ignore[arg-type]

    def _events(self) -> list[str]:
        return [event.kind for event in self.event_store.read(None)]

    def _run_until(self, ctx: SupervisorContext, predicate, *, start: datetime, max_ticks: int = 12) -> datetime:
        """Advance in escalation-rung-sized steps (2 min) until ``predicate()`` is True, or fail."""
        now = start
        for _ in range(max_ticks):
            run_supervisor_sweep(ctx, now=now)
            if predicate():
                return now
            now += timedelta(minutes=2)
        self.fail(f"predicate did not converge within {max_ticks} ticks (last now={now.isoformat()})")


class NeverBriefedSeatTests(_LivenessSimulationCase):
    """Scenario 1 (P-5/P-14): a spawned seat whose ``briefed-by`` expectation row is never met."""

    def test_never_briefed_worker_nudges_then_escalates_to_the_ladder(self) -> None:
        self.catalog.upsert(replace(_entry("orchestrator-1"), spawn_role="orchestrator"))
        self.catalog.upsert(
            replace(_entry("manager-1"), spawn_role="manager", spawned_by_session="orchestrator-1")
        )
        self.catalog.upsert(
            replace(
                _entry("worker-1", leaf_key="repo-a/260707_master/leaf-9"),
                spawn_role="worker",
                spawned_by_session="manager-1",
            )
        )
        # The spawn-time expectation row (HFX2-L1) that a real dispatch writes atomically -- never
        # met because the worker's very first paste (the brief) never landed.
        write_expectation_row(
            self.expectation_store,
            row_id="briefed-worker-1",
            now=NOW - timedelta(minutes=10),
            kind="briefed-by",
            sla_seconds=60.0,
            source_id="worker-1",
            subject_agent_id="worker-1",
            leaf_key="repo-a/260707_master/leaf-9",
        )
        ctx = self._ctx()

        # Tick 1: the sweep sees the overdue briefed-by row, auto-nudges the owner (worker-1's
        # manager, via spawn provenance), and marks the expectation row missed.
        run_supervisor_sweep(ctx, now=NOW)
        self.assertEqual(self.expectation_store.current()["briefed-worker-1"].state, "missed")
        self.assertIn("orchestration.nudge", self._events())
        nudges = [e for e in self.inbox_store.current().values() if e.messageKind == "nudge"]
        self.assertEqual(len(nudges), 1)
        self.assertEqual(nudges[0].agentId, "manager-1")

        # The nudge itself is now a pending, owner-addressed row -- if the manager never acks it,
        # the SAME escalation ladder every other unacked row rides climbs it to the developer.
        nudge_id = nudges[0].id
        final_rung_at = self._run_until(
            ctx,
            lambda: self.inbox_store.current()[nudge_id].rung >= 3,
            start=NOW + timedelta(minutes=2),
        )
        self.assertLessEqual((final_rung_at - NOW).total_seconds(), 12 * 60 * 60)
        self.assertEqual(self.inbox_store.current()[nudge_id].rung, 3)


class NoHostedSessionTests(_LivenessSimulationCase):
    """Scenario 3 (#16): a durable row addressed to a seat with no running hosted session."""

    def test_no_hosted_session_row_redelivers_on_backoff_then_escalates(self) -> None:
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        # worker-1 has a catalog row but no live tmux session behind it (host says unreachable).
        self.catalog.upsert(
            replace(_entry("worker-1"), spawn_role="worker", spawned_by_session="manager-1")
        )
        entry = create_operator_inbox_entry(
            entry_id="e1",
            now=NOW.isoformat(),
            lifecycle_id=None,
            agent_id="worker-1",
            ask="dispatch",
            response="you are the worker",
            created_by="manager",
            created_via="cli",
            message_kind="message",
        )
        self.inbox_store.append(entry)
        ctx = self._ctx(
            host=cast(TerminalHost, _FakeHost(reachable=False)),
            escalation_sla_seconds={"message": 1_000_000_000.0, "nudge": 60.0, "escalation": 60.0},
        )

        now = NOW
        for _ in range(PERSISTENT_FAILURE_ATTEMPTS):
            run_supervisor_sweep(ctx, now=now)
            current = self.inbox_store.current()["e1"]
            self.assertEqual(current.deliveryState, "no-hosted-session")
            if current.escalatedAt is not None:
                break
            assert current.nextAttemptAt is not None
            now = datetime.fromisoformat(current.nextAttemptAt)

        final = self.inbox_store.current()["e1"]
        self.assertEqual(final.attemptCount, PERSISTENT_FAILURE_ATTEMPTS)
        self.assertIsNotNone(final.escalatedAt)
        self.assertIn("orchestration.supervisor.escalate", self._events())


class DeadSeatStormTests(_LivenessSimulationCase):
    """HFX2-L8: fleet-scale rung-terminal no-hosted-session rows terminate in bounded time.

    Updated for the ruled invariant (developer, 2026-07-09): a storm past the hard health cap is
    trimmed to INBOX_MAX_CURRENT_ROWS by the sweep's own compaction BEFORE the sweep reads its
    snapshot -- system health outranks the rows -- and the capped survivors then ladder-resolve
    and compact away exactly as before."""

    def test_dead_seat_storm_terminates_and_compacts_without_stale_heartbeat(self) -> None:
        row_count = 2000
        for index in range(row_count):
            entry = create_operator_inbox_entry(
                entry_id=f"storm-{index}",
                now=NOW.isoformat(),
                lifecycle_id=None,
                agent_id=f"dead-seat-{index}",
                ask="dispatch",
                response="you are the worker",
                created_by="manager",
                created_via="cli",
                message_kind="message",
            ).model_copy(
                update={
                    "deliveryState": "no-hosted-session",
                    "attemptCount": PERSISTENT_FAILURE_ATTEMPTS,
                    "lastAttemptAt": (NOW - timedelta(hours=12)).isoformat(),
                    "nextAttemptAt": (NOW - timedelta(hours=1)).isoformat(),
                    "rung": 3,
                    "escalatedAt": (NOW - timedelta(hours=1)).isoformat(),
                }
            )
            self.inbox_store.append(entry)

        ctx = self._ctx(redeliver_budget=25)
        started = perf_counter()
        result = run_supervisor_sweep(ctx, now=NOW)
        elapsed = perf_counter() - started

        self.assertLess(elapsed, 20.0)
        # The health cap trims the storm before the sweep reads its snapshot: the sweep carries
        # at most INBOX_MAX_CURRENT_ROWS, never the full 2000-row storm.
        capped = INBOX_MAX_CURRENT_ROWS
        self.assertEqual(result.pending_inbox_count, capped)
        self.assertEqual(result.redeliverable_inbox_count, capped)
        self.assertEqual(result.findings[0].kind, "inbox-ladder-terminal")
        self.assertEqual(
            len([action for action in result.actions if action.action == "ladder-resolve"]),
            capped,
        )
        self.assertEqual([action for action in result.actions if action.action == "redeliver"], [])

        current = self.inbox_store.current()
        self.assertLessEqual(len(current), capped)
        self.assertTrue(all(entry.state == "ladder-resolved" for entry in current.values()))
        self.assertEqual(
            self.inbox_store.list_redeliverable(now=NOW + timedelta(seconds=1)),
            [],
        )

        heartbeat = self.heartbeat_store.read()
        assert heartbeat is not None
        self.assertEqual(heartbeat.sweepCount, 1)
        self.assertEqual(heartbeat.pendingInboxCount, capped)
        self.assertEqual(heartbeat.redeliverableInboxCount, capped)
        self.assertIsNotNone(heartbeat.lastSweepDurationSeconds)
        self.assertIsNone(
            supervisor_staleness_banner(
                self.observer_root,
                now=NOW + timedelta(seconds=30),
                stale_cutoff_seconds=60.0,
            )
        )

        removed = self.inbox_store.compact(now=NOW + timedelta(seconds=1))
        self.assertGreaterEqual(removed, capped)
        self.assertEqual(self.inbox_store.read(), [])


class ManagerMidTurnSignalLandsTests(_LivenessSimulationCase):
    """Scenario 4: pane busyness never substitutes for a harness-log record."""

    def test_busy_pane_without_log_acceptance_is_unconfirmed(self) -> None:
        self.catalog.upsert(replace(_entry("orchestrator-1"), spawn_role="orchestrator"))
        self.catalog.upsert(
            replace(_entry("manager-1"), spawn_role="manager", spawned_by_session="orchestrator-1")
        )
        entry = create_operator_inbox_entry(
            entry_id="e1",
            now=NOW.isoformat(),
            lifecycle_id=None,
            agent_id="manager-1",
            ask="escalation",
            response="worker-1 is silent",
            created_by="system",
            created_via="cli",
            message_kind="escalation",
        )
        self.inbox_store.append(entry)
        # A busy-pane marker is failure evidence only; without log acceptance the signal remains
        # unconfirmed instead of receiving transport credit.
        busy_paster = cast(
            TerminalPaster,
            _StubPaster(PasteResult(delivered=True, submitted=False, capture="esc to interrupt")),
        )
        ctx = self._ctx(paster=busy_paster)
        run_supervisor_sweep(ctx, now=NOW)
        current = self.inbox_store.current()["e1"]
        self.assertEqual(current.deliveryState, "unconfirmed")
        self.assertIn("harness-log-confirmed", current.deliveryDetail or "")


class DeadManagerLiveWorkersTests(_LivenessSimulationCase):
    """Scenario 5 (P-6 + ladder walk): builds on
    ``test_dead_manager_with_live_workers_respawns_and_surfaces_orphans``
    (``test_supervisor.py::LadderWalkIntegrationTests``) rather than duplicating its single-sweep
    proof -- this continues the SAME simulation for a second tick to prove the orphaned workers
    themselves get surfaced to the current manager as dead-upstream on the very next sweep, closing
    the loop end-to-end instead of stopping at "the manager got respawned"."""

    def test_dead_manager_respawn_then_orphans_signal_the_current_manager_next_tick(self) -> None:
        self.catalog.upsert(replace(_entry("orchestrator-1"), spawn_role="orchestrator"))
        self.catalog.upsert(
            replace(
                _entry("manager-1"),
                spawn_role="manager",
                spawned_by_session="orchestrator-1",
                leaf_key="repo-a/260707_master/old-manager-anchor",
                turn_state="stale",
                turn_state_changed_at=(NOW - timedelta(minutes=10)).isoformat(),
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
                _entry("worker-2", leaf_key="repo-a/260707_master/leaf-2"),
                spawn_role="worker",
                spawned_by_session="manager-1",
            )
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

        # Tick 1: the silent manager is retired-as-suspect and respawned; its live workers are
        # surfaced as orphans in the respawn event (the single-sweep proof this builds on).
        run_supervisor_sweep(ctx, now=NOW)
        retired = self.catalog.get("manager-1")
        assert retired is not None
        self.assertEqual(retired.status, "terminated")
        respawn_events = [e for e in self.event_store.read(None) if e.kind == "orchestration.supervisor.respawn"]
        self.assertEqual(len(respawn_events), 1)
        self.assertEqual(sorted(respawn_events[0].data["orphanedWorkers"]), ["worker-1", "worker-2"])

        # The respawn directive is the existing mechanism that creates/wakes a successor. Model
        # that successor appearing before the next reconciliation tick; leaf signals must now
        # address it rather than the workers' retired provenance or the orchestrator above it.
        self.catalog.upsert(
            replace(
                _entry("manager-2", leaf_key="repo-a/260707_master/current-manager-anchor"),
                spawn_role="manager",
            )
        )

        # Tick 2 (same ctx, later): the SAME catalog now shows both workers' owner as dead, so the
        # dead-upstream predicate fires for each and signals the current manager -- the
        # orphaned workers are never silently stranded, and they never absorb the manager's role.
        result = run_supervisor_sweep(ctx, now=NOW + timedelta(minutes=2))
        dead_upstream = [f for f in result.findings if f.kind == "dead-upstream"]
        session_ids = sorted(f.session_id for f in dead_upstream if f.session_id is not None)
        self.assertEqual(session_ids, ["worker-1", "worker-2"])
        manager_events = [
            e for e in self.event_store.read(None) if e.kind == "orchestration.supervisor.dead-upstream"
        ]
        self.assertEqual(len(manager_events), 2)
        self.assertTrue(all(e.data["managerAgentId"] == "manager-2" for e in manager_events))

        # The orphaned workers are still running, untouched -- doctrine: never auto re-parented,
        # never absorbing the dead manager's role.
        worker1 = self.catalog.get("worker-1")
        worker2 = self.catalog.get("worker-2")
        assert worker1 is not None and worker2 is not None
        self.assertEqual(worker1.status, "running")
        self.assertEqual(worker2.status, "running")


class KilledSupervisorDaemonTests(_LivenessSimulationCase):
    """Scenario 6: the supervisor's own self-heartbeat goes stale -> the fail-loud banner fires."""

    def test_heartbeat_stops_ticking_and_the_staleness_banner_fires(self) -> None:
        ctx = self._ctx()
        # The daemon was alive and sweeping...
        run_supervisor_sweep(ctx, now=NOW)
        run_supervisor_sweep(ctx, now=NOW + timedelta(seconds=30))
        heartbeat = self.heartbeat_store.read()
        assert heartbeat is not None
        self.assertEqual(heartbeat.sweepCount, 2)

        # ...then it is killed: no further sweep ever ticks the heartbeat again. A caller reading
        # the banner long after the last tick sees the fail-loud signal, proven at the exact
        # observer_root/store this ctx's own heartbeat_store writes to.
        stale_at = NOW + timedelta(minutes=10)
        self.assertIsNone(
            supervisor_staleness_banner(self.observer_root, now=NOW + timedelta(seconds=60), stale_cutoff_seconds=120.0)
        )
        banner = supervisor_staleness_banner(self.observer_root, now=stale_at, stale_cutoff_seconds=120.0)
        self.assertIsNotNone(banner)
        assert banner is not None
        self.assertIn("supervisor stale", banner)

    def test_a_heartbeat_that_never_ticked_is_deliberately_silent(self) -> None:
        # No sweep has ever run in this repo -- "no row yet" must not read as "daemon down".
        banner = supervisor_staleness_banner(self.observer_root, now=NOW, stale_cutoff_seconds=120.0)
        self.assertIsNone(banner)


class CodexQuotaModalTests(_LivenessSimulationCase):
    """Scenario 7 (#20): the codex quota/rate-limit modal is BLOCKED, never silently dropped, and
    the sweep itself (not just the injector unit test) drives it to NEEDS-ATTENTION escalation."""

    def test_quota_modal_blocks_every_redelivery_then_escalates_needs_attention(self) -> None:
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        self.catalog.upsert(
            replace(_entry("worker-1", kind="harness"), spawn_role="worker", spawned_by_session="manager-1")
        )
        entry = create_operator_inbox_entry(
            entry_id="e1",
            now=NOW.isoformat(),
            lifecycle_id=None,
            agent_id="worker-1",
            ask="dispatch",
            response="you are the worker",
            created_by="manager",
            created_via="cli",
            message_kind="message",
        )
        self.inbox_store.append(entry)
        # The pane permanently shows the codex quota modal -- every redelivery attempt lands into
        # it and is classified ``blocked``/``codex-quota-limit``, never ``failed`` or silently
        # dropped (the injector's own outcome-mapping unit tests prove the CLASSIFICATION in
        # isolation; this proves the SWEEP consumes it correctly across ticks).
        quota_paster = cast(
            TerminalPaster,
            _StubPaster(
                PasteResult(
                    delivered=True,
                    submitted=False,
                    capture="Approaching rate limits — switch model?",
                )
            ),
        )
        ctx = self._ctx(
            paster=quota_paster,
            escalation_sla_seconds={"message": 1_000_000_000.0, "nudge": 60.0, "escalation": 60.0},
        )

        now = NOW
        for _ in range(PERSISTENT_FAILURE_ATTEMPTS):
            run_supervisor_sweep(ctx, now=now)
            current = self.inbox_store.current()["e1"]
            self.assertEqual(current.deliveryState, "unconfirmed")
            assert current.deliveryDetail is not None
            self.assertIn("NEEDS-ATTENTION", current.deliveryDetail)
            self.assertIn("codex-quota-limit", current.deliveryDetail)
            if current.escalatedAt is not None:
                break
            assert current.nextAttemptAt is not None
            now = datetime.fromisoformat(current.nextAttemptAt)

        final = self.inbox_store.current()["e1"]
        self.assertEqual(final.attemptCount, PERSISTENT_FAILURE_ATTEMPTS)
        self.assertIsNotNone(final.escalatedAt)


class FalseDeadSeatHysteresisTests(_LivenessSimulationCase):
    """Scenario 8 (#17): a seat that flickers ``stale`` briefly must NOT trigger respawn -- the
    HFX-L5 hysteresis (proven separately at the ``TerminalCatalogLivenessSweeper`` probe layer in
    ``test_terminal_liveness.py``) must ALSO hold when consumed through the supervisor's own R2e
    seat-liveness predicate, across multiple sweep ticks."""

    def test_brief_stale_flicker_never_fires_and_never_respawns(self) -> None:
        self.catalog.upsert(replace(_entry("orchestrator-1"), spawn_role="orchestrator"))
        self.catalog.upsert(
            replace(
                _entry(
                    "worker-1",
                    turn_state="stale",
                    turn_state_changed_at=NOW.isoformat(),
                ),
                spawn_role="worker",
                spawned_by_session="orchestrator-1",
            )
        )
        ctx = self._ctx(stale_seat_seconds=60.0)

        # Tick right after the flicker starts: well under the 60s hysteresis window -- silent.
        result = run_supervisor_sweep(ctx, now=NOW + timedelta(seconds=10))
        self.assertEqual([f for f in result.findings if f.kind == "seat-liveness"], [])

        # The seat recovers (turn_state clears) before the window elapses -- still silent, and it
        # never crosses into a respawn candidate.
        flickering = self.catalog.get("worker-1")
        assert flickering is not None
        recovered = replace(flickering, turn_state=None, turn_state_changed_at=None)
        self.catalog.upsert(recovered)
        result = run_supervisor_sweep(ctx, now=NOW + timedelta(seconds=45))
        self.assertEqual([f for f in result.findings if f.kind == "seat-liveness"], [])
        self.assertEqual(
            [a for a in result.actions if a.action == "signal-emit" and a.finding.kind == "seat-liveness"], []
        )

        # No respawn/escalation event of any kind was ever logged for this seat across the flicker.
        respawn_events = [e for e in self._events() if e == "orchestration.supervisor.respawn"]
        self.assertEqual(respawn_events, [])
        still_running = self.catalog.get("worker-1")
        assert still_running is not None
        self.assertEqual(still_running.status, "running")

    def test_seat_actually_stale_past_the_window_still_fires(self) -> None:
        """Control case: hysteresis must not swallow a REAL failure -- past the window it fires."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "catalog.json")
            catalog.upsert(
                _entry(
                    "worker-1",
                    turn_state="stale",
                    turn_state_changed_at=(NOW - timedelta(minutes=5)).isoformat(),
                )
            )
            findings = evaluate_seat_liveness_findings(catalog, now=NOW, stale_seconds=60.0)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].detail, "turn-state-stale")


if __name__ == "__main__":
    unittest.main()
