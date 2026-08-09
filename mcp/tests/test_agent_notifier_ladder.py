from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast
from unittest import mock

from _scaling import assert_bounded_count
from agents_remember.controlplane import operator_inbox_transitions as inbox_transitions
from agents_remember.controlplane.agent_notifier_signals import AgentNotifierSignalCooldownStore
from agents_remember.controlplane.escalation_ladder import MAX_RUNG
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
    OperatorInboxEntry,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.orchestration_nudges import OrchestrationNudgeStore
from agents_remember.observer.store import EventStore
from agents_remember.serving import agent_notifier as agent_notifier_module
from agents_remember.serving._agent_notifier_evaluation import PERSISTENT_FAILURE_ATTEMPTS
from agents_remember.serving.agent_notifier import (
    AgentNotifierContext,
    AgentNotifierFinding,
    EscalationSchedule,
    _inactivity_signal_chain_progressed,
    evaluate_dead_upstream_findings,
    evaluate_escalation_findings,
    run_agent_notifier_sweep,
)
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.harness_control_models import SubmissionReceipt
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.inbox_delivery import InboxDeliveryLog, deliver_inbox_entry
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog
from test_agent_notifier import NOW, _entry, _fake_paster, _FakeHost


class EscalationPredicateTests(unittest.TestCase):
    def test_delivery_failure_waits_for_retry_exhaustion_before_escalating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OperatorInboxStore(Path(tmp))
            for entry_id, attempt_count in (
                ("retrying", agent_notifier_module.PERSISTENT_FAILURE_ATTEMPTS - 1),
                ("exhausted", agent_notifier_module.PERSISTENT_FAILURE_ATTEMPTS),
            ):
                store.append(
                    create_operator_inbox_entry(
                        InboxMessage(ask="ask", response="resp", message_kind="escalation"),
                        entry_id=entry_id,
                        now=(NOW - timedelta(minutes=10)).isoformat(),
                        routing=InboxRouting(
                            address=InboxAddress(lifecycle_id=None, agent_id="worker-1")
                        ),
                        poster=InboxPoster(created_by="system", created_via="cli"),
                    ).model_copy(
                        update={
                            "deliveryState": "no-hosted-session",
                            "attemptCount": attempt_count,
                        }
                    )
                )

            findings = evaluate_escalation_findings(
                store,
                now=NOW,
                schedule=EscalationSchedule(sla_seconds={"escalation": 60.0}, rung_seconds={}),
            )

            self.assertEqual([finding.source_id for finding in findings], ["exhausted"])

    def test_dispatch_failure_never_enters_generic_escalation_ladder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OperatorInboxStore(Path(tmp))
            store.append(
                create_operator_inbox_entry(
                    InboxMessage(ask="brief", response="work", message_kind="dispatch-brief"),
                    entry_id="dispatch-1",
                    now=(NOW - timedelta(minutes=10)).isoformat(),
                    routing=InboxRouting(
                        address=InboxAddress(lifecycle_id=None, agent_id="worker-1")
                    ),
                    poster=InboxPoster(created_by="manager-1", created_via="cli"),
                ).model_copy(
                    update={
                        "deliveryState": "unconfirmed",
                        "attemptCount": agent_notifier_module.PERSISTENT_FAILURE_ATTEMPTS + 10,
                    }
                )
            )

            findings = evaluate_escalation_findings(
                store,
                now=NOW,
                schedule=EscalationSchedule(sla_seconds={"dispatch-brief": 60.0}, rung_seconds={}),
            )

            self.assertEqual(findings, [])

    def test_pending_row_past_sla_fires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OperatorInboxStore(Path(tmp))
            entry = create_operator_inbox_entry(
                InboxMessage(ask="ask", response="resp", message_kind="escalation"),
                entry_id="e1",
                now=(NOW - timedelta(minutes=10)).isoformat(),
                routing=InboxRouting(address=InboxAddress(lifecycle_id=None, agent_id="worker-1")),
                poster=InboxPoster(created_by="system", created_via="cli"),
            )
            store.append(entry)
            findings = evaluate_escalation_findings(
                store,
                now=NOW,
                schedule=EscalationSchedule(sla_seconds={"escalation": 60.0}, rung_seconds={}),
            )
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].kind, "escalation-due")
            self.assertEqual(findings[0].source_id, "e1")

    def test_not_yet_due_row_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OperatorInboxStore(Path(tmp))
            entry = create_operator_inbox_entry(
                InboxMessage(ask="ask", response="resp", message_kind="escalation"),
                entry_id="e1",
                now=NOW.isoformat(),
                routing=InboxRouting(address=InboxAddress(lifecycle_id=None, agent_id="worker-1")),
                poster=InboxPoster(created_by="system", created_via="cli"),
            )
            store.append(entry)
            findings = evaluate_escalation_findings(
                store,
                now=NOW,
                schedule=EscalationSchedule(sla_seconds={"escalation": 3600.0}, rung_seconds={}),
            )
            self.assertEqual(findings, [])

    def test_landed_rows_are_never_escalation_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OperatorInboxStore(Path(tmp))
            store.append(
                create_operator_inbox_entry(
                    InboxMessage(ask="ask", response="resp", message_kind="escalation"),
                    entry_id="landed-1",
                    now=(NOW - timedelta(minutes=10)).isoformat(),
                    routing=InboxRouting(
                        address=InboxAddress(lifecycle_id=None, agent_id="worker-1")
                    ),
                    poster=InboxPoster(created_by="system", created_via="cli"),
                ).model_copy(update={"state": "landed"})
            )
            findings = evaluate_escalation_findings(
                store,
                now=NOW,
                schedule=EscalationSchedule(sla_seconds={"escalation": 1.0}, rung_seconds={}),
            )
            self.assertEqual(findings, [])

    def test_boundary_held_state_signal_is_not_escalation_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = OperatorInboxStore(root / "observer")
            catalog = TerminalCatalog(root / "catalog.json")
            catalog.upsert(
                replace(
                    _entry("manager-1"),
                    spawn_role="manager",
                    turn_state="working",
                    turn_state_changed_at=NOW.isoformat(),
                )
            )
            store.append(
                create_operator_inbox_entry(
                    InboxMessage(ask="state-signal", response="resp", message_kind="state-signal"),
                    entry_id="signal-1",
                    now=(NOW - timedelta(minutes=10)).isoformat(),
                    routing=InboxRouting(
                        address=InboxAddress(
                            lifecycle_id=None, agent_id="manager-1", recipient_role="manager"
                        )
                    ),
                    poster=InboxPoster(created_by="agent-notifier", created_via="cli"),
                )
            )
            findings = evaluate_escalation_findings(
                store,
                now=NOW,
                catalog=catalog,
                schedule=EscalationSchedule(sla_seconds={"state-signal": 1.0}, rung_seconds={}),
            )
            self.assertEqual(findings, [])

    def test_inactivity_signal_with_chain_progress_is_not_escalation_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = OperatorInboxStore(root / "observer")
            catalog = TerminalCatalog(root / "catalog.json")
            leaf_key = "repo-a/260707_master/leaf-9"
            catalog.upsert(
                replace(
                    _entry("manager-1", leaf_key="repo-a/260707_master/manager-anchor"),
                    spawn_role="manager",
                    turn_state="working",
                    turn_state_changed_at=(NOW - timedelta(minutes=5)).isoformat(),
                )
            )
            catalog.upsert(
                replace(
                    _entry("worker-1", leaf_key=leaf_key),
                    spawn_role="worker",
                    spawned_by_session="manager-1",
                )
            )
            store.append(
                create_operator_inbox_entry(
                    InboxMessage(
                        ask="Agent notifier observed seat-liveness: stale",
                        response="resp",
                        message_kind="escalation",
                    ),
                    entry_id="inactivity-1",
                    now=(NOW - timedelta(minutes=10)).isoformat(),
                    routing=InboxRouting(
                        address=InboxAddress(
                            lifecycle_id=None, agent_id="manager-1", recipient_role="manager"
                        )
                    ),
                    poster=InboxPoster(created_by="agent-notifier", created_via="cli"),
                ).model_copy(
                    update={
                        "leafKey": leaf_key,
                        "subjectAgentId": "worker-1",
                        "createdAt": (NOW - timedelta(minutes=10)).isoformat(),
                    }
                )
            )
            worker = catalog.get("worker-1")
            assert worker is not None
            catalog.upsert(
                replace(
                    worker,
                    turn_state="working",
                    turn_state_changed_at=(NOW - timedelta(minutes=5)).isoformat(),
                )
            )
            findings = evaluate_escalation_findings(
                store,
                now=NOW,
                catalog=catalog,
                schedule=EscalationSchedule(sla_seconds={"escalation": 1.0}, rung_seconds={}),
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
                    InboxMessage(
                        ask="Agent notifier observed seat-liveness: turn-state-stale",
                        response="worker-1 inactive",
                        message_kind="escalation",
                        subject=InboxSubject(leaf_key=leaf_key, agent_id="worker-1"),
                    ),
                    entry_id="e1",
                    now=(NOW - timedelta(minutes=10)).isoformat(),
                    routing=InboxRouting(
                        address=InboxAddress(
                            lifecycle_id=None, agent_id="manager-current", recipient_role="manager"
                        )
                    ),
                    poster=InboxPoster(created_by="agent-notifier", created_via="cli"),
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
                    catalog=catalog,
                    schedule=EscalationSchedule(
                        sla_seconds={"escalation": 60.0}, rung_seconds={1: 60.0}
                    ),
                ),
                [],
            )

    def test_inactivity_chain_progress_suppresses_legacy_and_current_ask_formats(self) -> None:
        # F1 regression (260713-TES-L1): chain-progress suppression must match BOTH the legacy
        # createdBy/ask-prefix row and the current-format row during the rename window.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
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

            def inactivity_row(*, entry_id: str, ask: str, created_by: str) -> OperatorInboxEntry:
                return create_operator_inbox_entry(
                    InboxMessage(
                        ask=ask,
                        response="worker-1 inactive",
                        message_kind="escalation",
                        subject=InboxSubject(leaf_key=leaf_key, agent_id="worker-1"),
                    ),
                    entry_id=entry_id,
                    now=(NOW - timedelta(minutes=10)).isoformat(),
                    routing=InboxRouting(
                        address=InboxAddress(
                            lifecycle_id=None,
                            agent_id="manager-current",
                            recipient_role="manager",
                        )
                    ),
                    poster=InboxPoster(created_by=created_by, created_via="cli"),
                )

            legacy = inactivity_row(
                entry_id="e-legacy",
                ask="Supervisor observed seat-liveness: turn-state-stale",
                created_by="supervisor",
            )
            current = inactivity_row(
                entry_id="e-current",
                ask="Agent notifier observed seat-liveness: turn-state-stale",
                created_by="agent-notifier",
            )
            self.assertTrue(_inactivity_signal_chain_progressed(catalog, legacy))
            self.assertTrue(_inactivity_signal_chain_progressed(catalog, current))


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
            escalation_sla_seconds={"escalation": 60.0},
            escalation_rung_seconds={1: 60.0, 2: 60.0},
            respawn_after_rung=2,
        )
        base.update(overrides)
        return AgentNotifierContext(**base)  # type: ignore[arg-type]

    def _events(self) -> set[str]:
        return {event.kind for event in self.event_store.read(None)}

    def test_delivered_dispatch_never_rebinds(self) -> None:
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        self.catalog.upsert(
            replace(
                _entry("worker-1"),
                status="terminated",
                terminated_at=(NOW - timedelta(minutes=10)).isoformat(),
                spawn_role="worker",
                spawned_by_session="manager-1",
            )
        )
        entry = create_operator_inbox_entry(
            InboxMessage(ask="brief", response="work", message_kind="dispatch-brief"),
            entry_id="dispatch-1",
            now=(NOW - timedelta(minutes=10)).isoformat(),
            routing=InboxRouting(
                address=InboxAddress(
                    lifecycle_id=None, agent_id="worker-1", recipient_role="worker"
                )
            ),
            poster=InboxPoster(created_by="manager-1", created_via="cli"),
        ).model_copy(
            update={
                "deliveryState": "delivered",
                "deliveryDetail": "harness-log-confirmed",
            }
        )
        self.inbox_store.append(entry)

        result = run_agent_notifier_sweep(self._ctx(), now=NOW)

        self.assertEqual(
            [f for f in result.findings if f.kind in ("rebind-due", "rebind-expired")],
            [],
        )
        current = self.inbox_store.current()[entry.id]
        # Exact-pinned: even a dead addressee never rebinds or expires via the rebind path; a
        # fresh brief comes from the owner.
        self.assertEqual(current.state, "pending")
        self.assertEqual(current.agentId, "worker-1")
        self.assertEqual([a for a in result.actions if a.action in ("rebind", "expire")], [])

    def test_silent_live_seat_reaches_unresolved_after_attempt_ceiling(self) -> None:
        self.catalog.upsert(replace(_entry("orchestrator-1"), spawn_role="orchestrator"))
        self.catalog.upsert(
            replace(_entry("manager-1"), spawn_role="manager", spawned_by_session="orchestrator-1")
        )
        self.catalog.upsert(
            replace(_entry("worker-1"), spawn_role="worker", spawned_by_session="manager-1")
        )
        entry = create_operator_inbox_entry(
            InboxMessage(ask="ask", response="resp", message_kind="escalation"),
            entry_id="e1",
            now=(NOW - timedelta(minutes=5)).isoformat(),
            routing=InboxRouting(
                address=InboxAddress(
                    lifecycle_id=None, agent_id="worker-1", recipient_role="worker"
                )
            ),
            poster=InboxPoster(created_by="system", created_via="cli"),
        )
        self.inbox_store.append(entry)

        ctx = self._ctx()
        now = NOW
        for _ in range(PERSISTENT_FAILURE_ATTEMPTS):
            run_agent_notifier_sweep(ctx, now=now)
            current = self.inbox_store.current()["e1"]
            if current.state != "pending":
                break
            assert current.nextAttemptAt is not None
            now = datetime.fromisoformat(current.nextAttemptAt)

        # N3: five attempts resolve the live-but-silent row terminal ``unresolved`` with its
        # delivery evidence intact -- no ladder rung, no escalation, no sibling rows.
        final = self.inbox_store.current()["e1"]
        self.assertEqual(final.attemptCount, PERSISTENT_FAILURE_ATTEMPTS)
        self.assertEqual(final.state, "unresolved")
        self.assertEqual(final.terminalReason, "attempt-limit")
        self.assertNotIn("orchestration.escalation.rung", self._events())
        self.assertIn("orchestration.agent-notifier.unresolved", self._events())
        self.assertEqual(len(self.inbox_store.current()), 1)
        self.assertEqual(final.ask, "ask")
        self.assertEqual(final.recipientRole, "worker")
        self.assertEqual(final.agentId, "worker-1")

    def test_duplicate_rebind_findings_cannot_rebind_twice_in_one_sweep(self) -> None:
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        self.catalog.upsert(
            replace(
                _entry("manager-1"),
                status="terminated",
                terminated_at=(NOW - timedelta(minutes=10)).isoformat(),
                spawn_role="manager",
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
        self.inbox_store.append(
            create_operator_inbox_entry(
                InboxMessage(ask="ask", response="resp", message_kind="escalation"),
                entry_id="e1",
                now=(NOW - timedelta(minutes=5)).isoformat(),
                routing=InboxRouting(
                    address=InboxAddress(
                        lifecycle_id=None, agent_id="manager-1", recipient_role="manager"
                    )
                ),
                poster=InboxPoster(
                    created_by="worker-1",
                    created_via="cli",
                    sender_agent_id="worker-1",
                    sender_role="worker",
                ),
            ).model_copy(update={"leafKey": "repo-a/260707_master/leaf-1"})
        )
        duplicate = AgentNotifierFinding(
            kind="rebind-due",
            detail="replacement-owner",
            session_id="manager-1",
            source_id="e1",
        )
        with mock.patch.object(
            agent_notifier_module, "evaluate_predicates", return_value=[duplicate, duplicate]
        ):
            result = run_agent_notifier_sweep(self._ctx(), now=NOW)

        rebound = self.inbox_store.current()["e1"]
        self.assertEqual(rebound.agentId, "manager-2")
        self.assertEqual(result.actions[1].outcome, "skipped")

    def test_dead_manager_row_rebinds_to_replacement_within_grace(self) -> None:
        self.catalog.upsert(replace(_entry("orchestrator-1"), spawn_role="orchestrator"))
        self.catalog.upsert(
            replace(
                _entry("manager-1"),
                status="terminated",
                terminated_at=(NOW - timedelta(minutes=10)).isoformat(),
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
        entry = create_operator_inbox_entry(
            InboxMessage(ask="ask", response="resp", message_kind="escalation"),
            entry_id="e1",
            now=(NOW - timedelta(minutes=5)).isoformat(),
            routing=InboxRouting(
                address=InboxAddress(
                    lifecycle_id=None, agent_id="manager-1", recipient_role="manager"
                )
            ),
            poster=InboxPoster(
                created_by="worker-1",
                created_via="cli",
                sender_agent_id="worker-1",
                sender_role="worker",
            ),
        ).model_copy(update={"leafKey": "repo-a/260707_master/leaf-1"})
        self.inbox_store.append(entry)

        run_agent_notifier_sweep(self._ctx(), now=NOW)
        rebound = self.inbox_store.current()["e1"]
        self.assertEqual(rebound.agentId, "manager-2")
        self.assertEqual(rebound.ownerAgentId, "manager-2")
        self.assertEqual(rebound.attemptCount, 0)
        rebind_events = [
            event
            for event in self.event_store.read(None)
            if event.kind == "orchestration.agent-notifier.rebind"
        ]
        self.assertEqual(len(rebind_events), 1)
        self.assertEqual(rebind_events[0].data["toAgentId"], "manager-2")

    def test_dead_manager_without_replacement_expires_to_architect_mailbox(self) -> None:
        self.catalog.upsert(replace(_entry("orchestrator-1"), spawn_role="orchestrator"))
        self.catalog.upsert(
            replace(
                _entry("manager-1"),
                status="terminated",
                terminated_at=(NOW - timedelta(minutes=10)).isoformat(),
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
        entry = create_operator_inbox_entry(
            InboxMessage(ask="ask", response="resp", message_kind="escalation"),
            entry_id="e1",
            now=(NOW - timedelta(minutes=10)).isoformat(),
            routing=InboxRouting(
                address=InboxAddress(
                    lifecycle_id=None, agent_id="manager-1", recipient_role="manager"
                )
            ),
            poster=InboxPoster(
                created_by="worker-1",
                created_via="cli",
                sender_agent_id="worker-1",
                sender_role="worker",
            ),
        ).model_copy(update={"leafKey": "repo-a/260707_master/leaf-1"})
        self.inbox_store.append(entry)

        result = run_agent_notifier_sweep(self._ctx(), now=NOW)

        self.assertNotIn("redeliver", {action.action for action in result.actions})
        expired = self.inbox_store.current()["e1"]
        self.assertEqual(expired.state, "expired")
        self.assertEqual(expired.terminalReason, "rebind-grace-expired")
        # N3: the dead owner chain surfaces to the scoped architect mailbox -- a mailbox, not
        # a ladder rung.
        self.assertEqual(expired.recipientRole, "architect")
        self.assertIsNone(expired.agentId)
        self.assertIn("orchestration.agent-notifier.rebind-expired", self._events())
        # Workers stay their own seats -- never re-parented, never absorbing the dead role.
        worker1 = self.catalog.get("worker-1")
        assert worker1 is not None
        self.assertEqual(worker1.status, "running")

    def test_landed_row_produces_no_retry_nudge_or_escalation_ever(self) -> None:
        """N16 regression: the delivered-but-unconsumed class is dissolved -- a landed row
        must produce no further retry, nudge, or escalation of any kind."""
        self.catalog.upsert(replace(_entry("manager-1"), spawn_role="manager"))
        entry = create_operator_inbox_entry(
            InboxMessage(ask="signal", response="worker done", message_kind="state-signal"),
            entry_id="e1",
            now=(NOW - timedelta(minutes=5)).isoformat(),
            routing=InboxRouting(
                address=InboxAddress(
                    lifecycle_id=None, agent_id="manager-1", recipient_role="manager"
                )
            ),
            poster=InboxPoster(
                created_by="agent-notifier", created_via="cli", sender_role="system"
            ),
        )
        self.inbox_store.append(entry)
        inbox_transitions.mark_landed(
            self.inbox_store,
            "e1",
            now=NOW.isoformat(),
            reason="adapter-accepted-at-turn-boundary",
        )

        for offset in (0, 10, 30):
            result = run_agent_notifier_sweep(self._ctx(), now=NOW + timedelta(seconds=offset))
            self.assertFalse(any(f.source_id == "e1" for f in result.findings))
            self.assertFalse(any(a.finding.source_id == "e1" for a in result.actions))
        self.assertEqual(self.inbox_store.current()["e1"].state, "landed")
        self.assertEqual(
            self.inbox_store.list_redeliverable(now=NOW + timedelta(hours=1)),
            [],
        )

    def test_relay_restart_reconciles_by_request_id_without_duplicate_submission(self) -> None:
        """R7: a relay kill/restart mid-redelivery replays the SAME correlated request -- the
        row fires exactly once more at the boundary and lands, never duplicating the push."""
        self.catalog.upsert(replace(_entry("architect-1"), spawn_role="architect"))
        self.catalog.upsert(
            replace(
                _entry("worker-1"),
                turn_state="working",
                turn_state_changed_at=NOW.isoformat(),
                control_state="ready",
                control_endpoint=Path("/tmp/worker.sock"),
                control_protocol="ar-harness-control/v1",
            )
        )
        entry = create_operator_inbox_entry(
            InboxMessage(ask="dispatch", response="work", message_kind="message"),
            entry_id="e1",
            now=(NOW - timedelta(minutes=30)).isoformat(),
            routing=InboxRouting(
                address=InboxAddress(
                    lifecycle_id=None, agent_id="worker-1", recipient_role="worker"
                )
            ),
            poster=InboxPoster(created_by="manager-1", created_via="cli"),
        )
        self.inbox_store.append(entry)

        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            side_effect=lambda _target, _text, submission: SubmissionReceipt(
                request_id=submission.request_id,
                acceptance="immediate",
                submitted_at=NOW.isoformat(),
                accepted_at=NOW.isoformat(),
            ),
        ) as submit:
            first = deliver_inbox_entry(
                InboxDeliveryLog(store=self.inbox_store, entry=entry, at=NOW.isoformat()),
                sessions=HostedSessionRuntime(
                    catalog=self.catalog, host=cast(TerminalHost, _FakeHost())
                ),
                paster=_fake_paster(),
            )
        # Accepted mid-turn: delivered evidence, but NOT landed (N1 gate).
        self.assertEqual(first.adapterDeliveryState, "accepted")
        self.assertEqual(first.state, "pending")
        self.assertEqual(submit.call_count, 1)

        # The relay restarts; the worker reaches a turn boundary; the SAME request id is
        # reconciled (never resubmitted) and the correlated acceptance now lands the row.
        worker = self.catalog.get("worker-1")
        assert worker is not None
        self.catalog.upsert(
            replace(
                worker,
                turn_state="turn-ended",
                turn_state_changed_at=(NOW + timedelta(minutes=1)).isoformat(),
            )
        )
        redelivered = deliver_inbox_entry(
            InboxDeliveryLog(
                store=self.inbox_store,
                entry=self.inbox_store.current()["e1"],
                at=(NOW + timedelta(minutes=1)).isoformat(),
            ),
            sessions=HostedSessionRuntime(
                catalog=self.catalog, host=cast(TerminalHost, _FakeHost())
            ),
            paster=_fake_paster(),
        )
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(redelivered.state, "landed")
        self.assertEqual(redelivered.attemptCount, 2)

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
                    InboxMessage(
                        ask=f"turn report {index}", response="resp", message_kind="turn-report"
                    ),
                    entry_id=f"root-{index}",
                    now=NOW.isoformat(),
                    routing=InboxRouting(
                        address=InboxAddress(
                            lifecycle_id=None,
                            agent_id=f"dead-seat-{index}",
                            recipient_role="manager",
                        )
                    ),
                    poster=InboxPoster(created_by="system", created_via="cli"),
                )
            )

        ctx = self._ctx()
        moment = NOW
        for _ in range(50):  # 50 sweeps x 6 min = 5 hours of absent developer
            moment += timedelta(minutes=6)
            run_agent_notifier_sweep(ctx, now=moment)
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
        result = run_agent_notifier_sweep(ctx, now=NOW)
        dead_upstream_findings = [f for f in result.findings if f.kind == "dead-upstream"]
        self.assertEqual(len(dead_upstream_findings), 1)
        self.assertEqual(dead_upstream_findings[0].session_id, "worker-1")
        events = [
            event
            for event in self.event_store.read(None)
            if event.kind == "orchestration.agent-notifier.dead-upstream"
        ]
        legacy_events = [
            event
            for event in self.event_store.read(None)
            if event.kind == "orchestration.supervisor.dead-upstream"
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(len(legacy_events), 1)
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
                        f"worker-{index}", leaf_key=f"repo/260707_master/leaf-{index}"
                    ).with_turn_state("stale", changed_at=(NOW - timedelta(minutes=5)).isoformat()),
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
                result = run_agent_notifier_sweep(self._ctx(), now=NOW)
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
                    Expectation(
                        kind="verdict-by",
                        source_id=f"seat-{index}",
                        subject=ExpectationSubject(
                            agent_id="worker-1", leaf_key="repo/260707_master/leaf-1"
                        ),
                    ),
                    row_id=f"exp-{index}",
                    now=NOW - timedelta(minutes=10),
                    sla_seconds=60.0,
                )
            counter = self._wrap_reads(self.expectation_store)
            result = run_agent_notifier_sweep(self._ctx(), now=NOW)
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

    def test_dead_seat_expiry_emission_is_exactly_one_per_row_per_sweep(self) -> None:
        """Z17 replacement: a backlog of dead-seat rows emits exactly one rebind-expired
        finding+action per row per sweep (linear, never quadratic), and level-triggered
        re-fires stop because the rows resolve terminal."""
        for pending_count in (20, 60):
            with self.subTest(pending=pending_count):
                self.setUp()
                for index in range(pending_count):
                    self.inbox_store.append(
                        create_operator_inbox_entry(
                            InboxMessage(ask="ask", response="resp", message_kind="escalation"),
                            entry_id=f"esc-{index}",
                            now=(NOW - timedelta(minutes=10)).isoformat(),
                            routing=InboxRouting(
                                address=InboxAddress(lifecycle_id=None, agent_id=f"worker-{index}")
                            ),
                            poster=InboxPoster(created_by="system", created_via="cli"),
                        )
                    )
                result = run_agent_notifier_sweep(self._ctx(), now=NOW)
                expiry_findings = [f for f in result.findings if f.kind == "rebind-expired"]
                assert_bounded_count(
                    len(expiry_findings),
                    pending_count,
                    label=f"expiry findings at N={pending_count}",
                )
                self.assertEqual(len(expiry_findings), pending_count)
                self.assertEqual(
                    len([a for a in result.actions if a.action == "expire"]),
                    pending_count,
                )
                # Terminal now: a second sweep re-fires nothing.
                again = run_agent_notifier_sweep(self._ctx(), now=NOW + timedelta(seconds=1))
                self.assertEqual(
                    [f for f in again.findings if f.kind == "rebind-expired"],
                    [],
                )
                heartbeat = self.heartbeat_store.read()
                assert heartbeat is not None
                self.assertEqual(heartbeat.sweepCount, 2)
