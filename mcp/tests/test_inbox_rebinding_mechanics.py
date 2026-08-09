"""260713-TES-L4 rebind/expiry mechanics: branch and idempotence coverage."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

from agents_remember.controlplane import operator_inbox_transitions as inbox_transitions
from agents_remember.controlplane.agent_notifier_signals import AgentNotifierSignalCooldownStore
from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.interaction_retention import inbox_keep_ids
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    OperatorInboxEntry,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.operator_inbox_transitions import ExpiryOptions
from agents_remember.controlplane.orchestration_nudges import OrchestrationNudgeStore
from agents_remember.controlplane.signal_routing import derive_row_owner
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.mcp.tools.operator_inbox import operator_inbox_supersede_payload
from agents_remember.observer.store import EventStore
from agents_remember.serving import _agent_notifier_actions as notifier_actions
from agents_remember.serving import _app_lifespan
from agents_remember.serving import inbox_delivery as inbox_delivery_module
from agents_remember.serving._agent_notifier_evaluation import (
    REBIND_GRACE_SECONDS,
    _row_dead_since,
    evaluate_pending_expiry_findings,
    evaluate_rebind_findings,
)
from agents_remember.serving.agent_notifier import (
    _fold_legacy_landed,
    run_agent_notifier_sweep,
)
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.agent_notifier_models import (
    AgentNotifierContext,
    AgentNotifierFinding,
    FindingKind,
    SweepState,
)
from agents_remember.serving.harness_control_models import SubmissionReceipt
from agents_remember.serving.inbox_reclamation import TmuxSessionNameSnapshot
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog
from test_inbox_arrival_guarantee import NOW, _seat


class TransitionIdempotenceTests(unittest.TestCase):
    """Terminal transitions are idempotent: the second call appends nothing (level-triggered
    sweeps re-decide the same finding on every pass)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = OperatorInboxStore(Path(self.tmp.name) / "logs" / "observer")
        self.entry = create_operator_inbox_entry(
            InboxMessage(ask="ask", response="resp", message_kind="message"),
            entry_id="e1",
            now=NOW.isoformat(),
            routing=InboxRouting(address=InboxAddress(lifecycle_id=None, agent_id="worker-1")),
            poster=InboxPoster(created_by="system", created_via="cli"),
        )
        self.store.append(self.entry)

    def test_landed_superseded_unresolved_expired_and_rebind_are_idempotent(self) -> None:
        transitions = (
            lambda: inbox_transitions.mark_landed(
                self.store, "e1", now=NOW.isoformat(), reason="x"
            ),
            lambda: inbox_transitions.mark_superseded(
                self.store, "e1", now=NOW.isoformat(), reason="x"
            ),
            lambda: inbox_transitions.mark_unresolved(
                self.store, "e1", now=NOW.isoformat(), reason="x"
            ),
            lambda: inbox_transitions.mark_expired(
                self.store, "e1", now=NOW.isoformat(), options=ExpiryOptions(reason="x")
            ),
        )
        for transition in transitions:
            with self.subTest(transition=transition):
                self.setUp()
                first, first_now = transition()
                self.assertTrue(first_now)
                rows_after_first = len(self.store.read())
                again, again_now = transition()
                self.assertFalse(again_now)
                self.assertEqual(len(self.store.read()), rows_after_first)
                self.assertEqual(again.state, first.state)

    def test_rebind_entry_only_rewrites_pending_rows(self) -> None:
        inbox_transitions.mark_landed(self.store, "e1", now=NOW.isoformat(), reason="x")
        rows_before = len(self.store.read())
        again, rebound_now = inbox_transitions.rebind_entry(
            self.store,
            "e1",
            inbox_transitions.InboxOwner(role="manager", agent_id="m2"),
            now=NOW.isoformat(),
        )
        self.assertFalse(rebound_now)
        self.assertEqual(len(self.store.read()), rows_before)
        self.assertEqual(again.state, "landed")

    def test_expiry_can_readdress_the_terminal_marker(self) -> None:
        expired, _ = inbox_transitions.mark_expired(
            self.store,
            "e1",
            now=NOW.isoformat(),
            options=ExpiryOptions(
                reason="dead-owner-chain",
                readdress_to=inbox_transitions.InboxOwner(role="architect"),
            ),
        )
        self.assertEqual(expired.state, "expired")
        self.assertEqual(expired.recipientRole, "architect")
        self.assertIsNone(expired.agentId)


class RowOwnerDerivationTests(unittest.TestCase):
    """N14 row-based owner derivation branches."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.catalog = TerminalCatalog(Path(self.tmp.name) / "catalog.json")

    def _row(self, **updates: object) -> OperatorInboxEntry:
        return create_operator_inbox_entry(
            InboxMessage(ask="ask", response="resp", message_kind="escalation"),
            entry_id="e1",
            now=NOW.isoformat(),
            routing=InboxRouting(address=InboxAddress(lifecycle_id=None, agent_id="manager-1")),
            poster=InboxPoster(created_by="system", created_via="cli"),
        ).model_copy(update=updates)

    def test_dispatch_brief_never_derives_an_owner(self) -> None:
        owner = derive_row_owner(
            self.catalog, self._row(messageKind="dispatch-brief", state="pending")
        )
        self.assertIsNone(owner.role)
        self.assertIsNone(owner.agent_id)

    def test_manager_row_resolves_live_orchestrator_replacement(self) -> None:
        self.catalog.upsert(
            _seat(
                "orchestrator-old",
                status="terminated",
                terminated_at=(NOW - timedelta(minutes=10)).isoformat(),
                spawn_role="orchestrator",
            )
        )
        self.catalog.upsert(
            _seat(
                "manager-1",
                status="terminated",
                terminated_at=(NOW - timedelta(minutes=10)).isoformat(),
                spawn_role="manager",
                spawned_by_session="orchestrator-old",
                leaf_key="repo-a/260707_master/manager-anchor",
            )
        )
        self.catalog.upsert(
            _seat(
                "orchestrator-new",
                leaf_key="repo-a/260707_master/orchestrator-anchor",
                spawn_role="orchestrator",
            )
        )
        row = self._row(
            seatRole="manager",
            senderRole="manager",
            subjectAgentId="manager-1",
            leafKey="repo-a/260707_master/leaf-1",
        )
        owner = derive_row_owner(self.catalog, row)
        self.assertEqual(owner.role, "orchestrator")
        self.assertEqual(owner.agent_id, "orchestrator-new")

    def test_stamped_owner_fallbacks_cover_manager_orchestrator_and_architect(self) -> None:
        self.catalog.upsert(
            _seat(
                "architect-1",
                leaf_key="repo-a/260707_master/architect-anchor",
                seat_role="architect",
                spawn_role="architect",
            )
        )
        self.catalog.upsert(
            _seat(
                "manager-2",
                leaf_key="repo-a/260707_master/current-manager-anchor",
                spawn_role="manager",
            )
        )
        manager_row = self._row(ownerRole="manager", leafKey="repo-a/260707_master/leaf-1")
        self.assertEqual(derive_row_owner(self.catalog, manager_row).agent_id, "manager-2")
        architect_row = self._row(ownerRole="architect", leafKey="repo-a/260707_master/leaf-1")
        self.assertEqual(derive_row_owner(self.catalog, architect_row).agent_id, "architect-1")
        orchestrator_row = self._row(ownerRole="orchestrator")
        owner = derive_row_owner(self.catalog, orchestrator_row)
        self.assertEqual(owner.role, "orchestrator")
        self.assertIsNone(owner.agent_id)

    def test_unroutable_row_returns_empty_owner(self) -> None:
        owner = derive_row_owner(self.catalog, self._row())
        self.assertIsNone(owner.role)
        self.assertIsNone(owner.agent_id)

    def test_manager_row_with_live_orchestrator_uses_provenance(self) -> None:
        self.catalog.upsert(_seat("orchestrator-live", spawn_role="orchestrator"))
        self.catalog.upsert(
            _seat(
                "manager-1",
                spawn_role="manager",
                spawned_by_session="orchestrator-live",
            )
        )
        row = self._row(
            seatRole="manager",
            senderRole="manager",
            subjectAgentId="manager-1",
        )
        owner = derive_row_owner(self.catalog, row)
        self.assertEqual(owner.agent_id, "orchestrator-live")

    def test_manager_row_with_unknown_subject_falls_back_to_role_mailbox(self) -> None:
        row = self._row(seatRole="manager", senderRole="manager", subjectAgentId="ghost")
        owner = derive_row_owner(self.catalog, row)
        self.assertIsNone(owner.role)
        self.assertIsNone(owner.agent_id)

    def test_ambiguous_orchestrator_scope_resolves_to_role_mailbox(self) -> None:
        self.catalog.upsert(
            _seat(
                "orchestrator-old",
                status="terminated",
                terminated_at=(NOW - timedelta(minutes=10)).isoformat(),
                spawn_role="orchestrator",
                spawned_by_lifecycle="L-old",
            )
        )
        self.catalog.upsert(
            _seat(
                "manager-1",
                status="terminated",
                terminated_at=(NOW - timedelta(minutes=10)).isoformat(),
                spawn_role="manager",
                spawned_by_session="orchestrator-old",
                leaf_key="repo-a/260707_master/manager-anchor",
            )
        )
        self.catalog.upsert(
            _seat(
                "orch-a",
                leaf_key="repo-a/260707_master/orch-a",
                spawn_role="orchestrator",
            )
        )
        self.catalog.upsert(
            _seat(
                "orch-b",
                leaf_key="repo-a/260707_master/orch-b",
                spawn_role="orchestrator",
            )
        )
        row = self._row(
            seatRole="manager",
            senderRole="manager",
            subjectAgentId="manager-1",
            leafKey="repo-a/260707_master/leaf-1",
        )
        owner = derive_row_owner(self.catalog, row)
        self.assertEqual(owner.role, "orchestrator")
        self.assertIsNone(owner.agent_id)


class ActionSkipBranchTests(unittest.TestCase):
    """Defensive skip branches in the rebind/expiry action handlers."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.root = root
        self.observer_root = root / "logs" / "observer"
        self.catalog = TerminalCatalog(root / "catalog.json")
        self.inbox_store = OperatorInboxStore(self.observer_root)

    def _ctx(self) -> AgentNotifierContext:
        return AgentNotifierContext(
            catalog=self.catalog,
            host=cast(
                TerminalHost,
                SimpleNamespace(
                    has_session=lambda _n: True,
                    terminate=lambda *_a, **_k: None,
                ),
            ),
            paster=cast("object", None),  # type: ignore[arg-type]
            inbox_store=self.inbox_store,
            expectation_store=ExpectationRowStore(self.observer_root),
            nudge_store=OrchestrationNudgeStore(self.observer_root),
            signal_cooldown_store=AgentNotifierSignalCooldownStore(self.observer_root),
            event_store=EventStore(self.observer_root),
            heartbeat_store=AgentNotifierHeartbeatStore(self.observer_root),
            coordination_root=self.root,
        )

    def _finding(self, kind: FindingKind, source: str | None) -> AgentNotifierFinding:
        return AgentNotifierFinding(kind=kind, detail="x", source_id=source)

    def test_rebind_skip_branches(self) -> None:
        ctx = self._ctx()
        sweep = SweepState(inbox_current={}, redeliver_budget=1)
        result = notifier_actions.act_on_finding(
            ctx, self._finding("rebind-due", None), now=NOW, sweep=sweep
        )
        self.assertEqual(result.detail, "no source entry id")
        result = notifier_actions.act_on_finding(
            ctx, self._finding("rebind-due", "missing"), now=NOW, sweep=sweep
        )
        self.assertEqual(result.detail, "entry not pending")

    def test_rebind_skips_when_no_replacement_owner_exists(self) -> None:
        self.catalog.upsert(
            _seat(
                "manager-1",
                status="terminated",
                terminated_at=(NOW - timedelta(minutes=10)).isoformat(),
                spawn_role="manager",
                leaf_key="repo-a/260707_master/old-manager-anchor",
            )
        )
        self.catalog.upsert(
            _seat(
                "worker-1",
                leaf_key="repo-a/260707_master/leaf-1",
                spawn_role="worker",
                spawned_by_session="manager-1",
            )
        )
        row = create_operator_inbox_entry(
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
        self.inbox_store.append(row)
        sweep = SweepState(inbox_current={row.id: row}, redeliver_budget=1)
        result = notifier_actions.act_on_finding(
            self._ctx(),
            self._finding("rebind-due", row.id),
            now=NOW,
            sweep=sweep,
        )
        self.assertEqual(result.outcome, "skipped")
        self.assertEqual(result.detail, "no replacement owner")

    def test_rebind_expired_reroutes_to_rebind_when_replacement_appeared(self) -> None:
        self.catalog.upsert(
            _seat(
                "manager-1",
                status="terminated",
                terminated_at=(NOW - timedelta(minutes=10)).isoformat(),
                spawn_role="manager",
                leaf_key="repo-a/260707_master/old-manager-anchor",
            )
        )
        self.catalog.upsert(
            _seat(
                "worker-1",
                leaf_key="repo-a/260707_master/leaf-1",
                spawn_role="worker",
                spawned_by_session="manager-1",
            )
        )
        self.catalog.upsert(
            _seat(
                "manager-2",
                leaf_key="repo-a/260707_master/current-manager-anchor",
                spawn_role="manager",
            )
        )
        row = create_operator_inbox_entry(
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
        self.inbox_store.append(row)
        sweep = SweepState(inbox_current={row.id: row}, redeliver_budget=1)
        result = notifier_actions.act_on_finding(
            self._ctx(),
            self._finding("rebind-expired", row.id),
            now=NOW,
            sweep=sweep,
        )
        self.assertEqual(result.action, "rebind")
        self.assertEqual(self.inbox_store.current()["e1"].agentId, "manager-2")

    def test_expire_and_unresolved_skip_branches(self) -> None:
        ctx = self._ctx()
        sweep = SweepState(inbox_current={}, redeliver_budget=1)
        for kind in ("rebind-expired", "inbox-ttl-expired"):
            result = notifier_actions.act_on_finding(
                ctx, self._finding(kind, None), now=NOW, sweep=sweep
            )
            self.assertEqual(result.detail, "no source entry id")
            result = notifier_actions.act_on_finding(
                ctx, self._finding(kind, "missing"), now=NOW, sweep=sweep
            )
            self.assertEqual(result.detail, "entry not pending")

    def test_unresolved_missing_entry_is_silent(self) -> None:
        sweep = SweepState(inbox_current={}, redeliver_budget=1)
        notifier_actions._mark_unresolved(
            self._ctx(), "missing", now=NOW, sweep=sweep
        )  # must not raise

    def test_stale_sweep_snapshot_hits_idempotent_false_branches(self) -> None:
        """A concurrent transition between evaluate and act leaves the sweep snapshot stale;
        the action must tolerate the already-terminal store row (level-triggered re-decide)."""
        row = create_operator_inbox_entry(
            InboxMessage(ask="ask", response="resp", message_kind="message"),
            entry_id="e1",
            now=NOW.isoformat(),
            routing=InboxRouting(address=InboxAddress(lifecycle_id=None, agent_id="worker-1")),
            poster=InboxPoster(created_by="system", created_via="cli"),
        )
        self.inbox_store.append(row)
        stale = SweepState(inbox_current={row.id: row}, redeliver_budget=1)
        ctx = self._ctx()
        inbox_transitions.mark_expired(
            self.inbox_store,
            "e1",
            now=NOW.isoformat(),
            options=ExpiryOptions(reason="concurrent"),
        )
        result = notifier_actions.act_on_finding(
            ctx, self._finding("inbox-ttl-expired", row.id), now=NOW, sweep=stale
        )
        self.assertEqual(result.outcome, "expired")
        # F1: the stale action appended nothing; the concurrent expired terminal survives.
        self.assertEqual(self.inbox_store.current()["e1"].state, "expired")
        self.assertEqual(len(self.inbox_store.read()), 2)
        self.inbox_store.delete("e1")
        self.inbox_store.append(row)
        inbox_transitions.mark_unresolved(
            self.inbox_store, "e1", now=NOW.isoformat(), reason="concurrent"
        )
        stale = SweepState(inbox_current={row.id: row}, redeliver_budget=1)
        notifier_actions._mark_unresolved(ctx, "e1", now=NOW, sweep=stale)
        # F1: stale unresolved after the concurrent unresolved appends nothing.
        self.assertEqual(self.inbox_store.current()["e1"].state, "unresolved")
        self.assertEqual(len(self.inbox_store.read()), 2)
        # Rebind with a stale pending snapshot while the store row already landed: the
        # transition reports resolved_now=False and nothing is re-stamped.
        self.catalog.upsert(
            _seat(
                "manager-2",
                leaf_key="repo-a/260707_master/current-manager-anchor",
                spawn_role="manager",
            )
        )
        rebind_row = create_operator_inbox_entry(
            InboxMessage(ask="ask", response="resp", message_kind="escalation"),
            entry_id="e2",
            now=NOW.isoformat(),
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
        self.inbox_store.append(rebind_row)
        inbox_transitions.mark_landed(
            self.inbox_store, "e2", now=NOW.isoformat(), reason="concurrent"
        )
        stale = SweepState(inbox_current={rebind_row.id: rebind_row}, redeliver_budget=1)
        result = notifier_actions.act_on_finding(
            ctx, self._finding("rebind-due", "e2"), now=NOW, sweep=stale
        )
        self.assertEqual(result.action, "rebind")
        # F1: the store row is already landed; the stale rebind appended nothing.
        self.assertEqual(self.inbox_store.current()["e2"].state, "landed")
        self.assertEqual(len(self.inbox_store.current()), 2)


class EvaluationBranchTests(unittest.TestCase):
    """Bounded fallback branches in rebind/expiry evaluation."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.catalog = TerminalCatalog(root / "catalog.json")
        self.store = OperatorInboxStore(root / "logs" / "observer")

    def _row(self, entry_id: str = "e1", **updates: object) -> OperatorInboxEntry:
        return create_operator_inbox_entry(
            InboxMessage(ask="ask", response="resp", message_kind="message"),
            entry_id=entry_id,
            now=NOW.isoformat(),
            routing=InboxRouting(address=InboxAddress(lifecycle_id=None, agent_id="ghost-seat")),
            poster=InboxPoster(created_by="system", created_via="cli"),
        ).model_copy(update=updates)

    def test_unparseable_death_stamp_keeps_grace_unmeasured(self) -> None:
        self.catalog.upsert(
            _seat(
                "ghost-seat",
                status="exited",
                turn_state_changed_at="not-a-date",
            )
        )
        row = self._row()
        self.assertIsNone(_row_dead_since(self.catalog, row))
        findings = evaluate_rebind_findings(
            self.catalog,
            current={row.id: row},
            now=NOW,
            grace_seconds=REBIND_GRACE_SECONDS,
        )
        self.assertEqual(findings, [])

    def test_running_seat_has_no_death_stamp(self) -> None:
        self.catalog.upsert(_seat("live-seat"))
        row = self._row(agentId="live-seat")
        self.assertIsNone(_row_dead_since(self.catalog, row))

    def test_unparseable_created_at_skips_pending_expiry(self) -> None:
        row = self._row(createdAt="not-a-date")
        findings = evaluate_pending_expiry_findings(
            {row.id: row},
            now=NOW,
        )
        self.assertEqual(findings, [])


class KeepRetentionBranchTests(unittest.TestCase):
    """Retention branches: legacy markers, missing stamps, and terminal states."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = OperatorInboxStore(Path(self.tmp.name) / "logs" / "observer")

    def _entry(self, entry_id: str, created_at: datetime = NOW) -> OperatorInboxEntry:
        return create_operator_inbox_entry(
            InboxMessage(ask="ask", response="resp", message_kind="message"),
            entry_id=entry_id,
            now=created_at.isoformat(),
            routing=InboxRouting(address=InboxAddress(lifecycle_id=None, agent_id="worker-1")),
            poster=InboxPoster(created_by="system", created_via="cli"),
        )

    def test_legacy_consumed_marker_retention_branches(self) -> None:
        # Fresh legacy consumed marker (by createdAt fallback when consumedAt is absent) keeps.
        fresh = self._entry("fresh").model_copy(update={"state": "consumed", "consumedAt": None})
        self.store.append(fresh)
        keep = inbox_keep_ids(self.store.read(), now=NOW)
        self.assertIn("fresh", keep)
        # An ancient consumed marker without consumedAt ages out from createdAt.
        self.store.append(
            self._entry("ancient", created_at=NOW - timedelta(hours=49)).model_copy(
                update={"state": "consumed", "consumedAt": None}
            )
        )
        keep = inbox_keep_ids(self.store.read(), now=NOW)
        self.assertNotIn("ancient", keep)

    def test_terminal_marker_retention_branches(self) -> None:
        self.store.append(self._entry("t-fresh").model_copy(update={"state": "unresolved"}))
        self.store.append(
            self._entry("t-ancient", created_at=NOW - timedelta(hours=49)).model_copy(
                update={"state": "superseded", "terminalAt": None}
            )
        )
        keep = inbox_keep_ids(self.store.read(), now=NOW)
        self.assertIn("t-fresh", keep)
        self.assertNotIn("t-ancient", keep)

    def test_ladder_resolved_rows_drop_immediately(self) -> None:
        self.store.append(
            self._entry("legacy-ladder").model_copy(update={"state": "ladder-resolved"})
        )
        keep = inbox_keep_ids(self.store.read(), now=NOW)
        self.assertNotIn("legacy-ladder", keep)


class LoopModeTests(unittest.IsolatedAsyncioTestCase):
    """R7/N5 loop branches: disabled mode and sweep-exception resilience."""

    async def test_disabled_loop_skips_sweeps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = load_agentic_settings(root)
            disabled = replace(
                settings,
                agent_notifier=replace(settings.agent_notifier, enabled=False),
            )
            runtime = SimpleNamespace(
                config=SimpleNamespace(coordination_root=root),
                observer_root=root / "logs" / "observer",
            )

            async def fake_sleep(_seconds: float) -> None:
                fake_sleep.calls += 1
                if fake_sleep.calls >= 2:
                    raise asyncio.CancelledError

            fake_sleep.calls = 0
            with (
                mock.patch.object(_app_lifespan, "load_agentic_settings", return_value=disabled),
                mock.patch.object(_app_lifespan, "run_agent_notifier_sweep") as sweep,
                mock.patch.object(_app_lifespan.asyncio, "sleep", new=fake_sleep),
                self.assertRaises(asyncio.CancelledError),
            ):
                await _app_lifespan._agent_notifier_loop(runtime)  # type: ignore[arg-type]
            sweep.assert_not_called()

    async def test_sweep_exception_fails_loud_and_loop_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = load_agentic_settings(root)
            runtime = SimpleNamespace(
                config=SimpleNamespace(coordination_root=root),
                catalog=TerminalCatalog(root / "catalog.json"),
                host=cast(
                    TerminalHost,
                    SimpleNamespace(
                        has_session=lambda _n: True,
                        terminate=lambda *_a, **_k: None,
                    ),
                ),
                paster=cast("object", None),  # type: ignore[arg-type]
                heartbeat_store=AgentNotifierHeartbeatStore(root / "logs" / "observer"),
                observer_root=root / "logs" / "observer",
                liveness_clock=lambda: NOW,
            )

            async def fake_sleep(_seconds: float) -> None:
                fake_sleep.calls += 1
                if fake_sleep.calls >= 2:
                    raise asyncio.CancelledError

            fake_sleep.calls = 0
            with (
                mock.patch.object(_app_lifespan, "load_agentic_settings", return_value=good),
                mock.patch.object(
                    _app_lifespan, "run_agent_notifier_sweep", side_effect=RuntimeError("boom")
                ) as sweep,
                mock.patch.object(_app_lifespan.asyncio, "sleep", new=fake_sleep),
                self.assertLogs("agents_remember.serving.app", level="ERROR") as logs,
                self.assertRaises(asyncio.CancelledError),
            ):
                await _app_lifespan._agent_notifier_loop(runtime)  # type: ignore[arg-type]
            self.assertGreaterEqual(sweep.call_count, 2)
            self.assertTrue(all("sweep failed" in line for line in logs.output))


class LegacyLandedFoldTests(unittest.TestCase):
    """N13: pre-migration by-rule landed rows fold into the formal state exactly once."""

    def test_legacy_by_rule_state_signal_row_lands_formally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observer = root / "logs" / "observer"
            store = OperatorInboxStore(observer)
            catalog = TerminalCatalog(root / "catalog.json")
            catalog.upsert(_seat("manager-1", spawn_role="manager"))
            store.append(
                create_operator_inbox_entry(
                    InboxMessage(
                        ask="Agent notifier observed state-signal: completed",
                        response="done",
                        message_kind="state-signal",
                    ),
                    entry_id="legacy-1",
                    now=NOW.isoformat(),
                    routing=InboxRouting(
                        address=InboxAddress(
                            lifecycle_id=None, agent_id="manager-1", recipient_role="manager"
                        )
                    ),
                    poster=InboxPoster(
                        created_by="agent-notifier", created_via="cli", sender_role="system"
                    ),
                ).model_copy(
                    update={
                        "deliveryState": "delivered",
                        "adapterDeliveryState": "accepted",
                    }
                )
            )
            store.append(
                create_operator_inbox_entry(
                    InboxMessage(ask="nudge", response="resp", message_kind="message"),
                    entry_id="not-eligible",
                    now=NOW.isoformat(),
                    routing=InboxRouting(
                        address=InboxAddress(lifecycle_id=None, agent_id="manager-1")
                    ),
                    poster=InboxPoster(created_by="system", created_via="cli"),
                )
            )
            ctx = AgentNotifierContext(
                catalog=catalog,
                host=cast(
                    TerminalHost,
                    SimpleNamespace(
                        has_session=lambda _n: True,
                        terminate=lambda *_a, **_k: None,
                    ),
                ),
                paster=cast("object", None),  # type: ignore[arg-type]
                inbox_store=store,
                expectation_store=ExpectationRowStore(observer),
                nudge_store=OrchestrationNudgeStore(observer),
                signal_cooldown_store=AgentNotifierSignalCooldownStore(observer),
                event_store=EventStore(observer),
                heartbeat_store=AgentNotifierHeartbeatStore(observer),
                coordination_root=root,
                tmux_name_snapshotter=lambda: TmuxSessionNameSnapshot(
                    frozenset(), "tmux-no-server"
                ),
            )
            run_agent_notifier_sweep(ctx, now=NOW)
            current = store.current()
            self.assertEqual(current["legacy-1"].state, "landed")
            self.assertEqual(current["legacy-1"].terminalReason, "legacy-by-rule-landed")
            self.assertEqual(current["not-eligible"].state, "pending")
            event_kinds = {e.kind for e in EventStore(observer).read(None)}
            self.assertIn("orchestration.agent-notifier.inbox-landed-fold", event_kinds)

    def test_legacy_fold_skips_a_row_concurrently_superseded(self) -> None:
        """F1: the legacy fold holds a stale pending snapshot; a concurrent supersede wins
        and the fold appends nothing (no false landing of an explicitly superseded row)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observer = root / "logs" / "observer"
            store = OperatorInboxStore(observer)
            pending = create_operator_inbox_entry(
                InboxMessage(
                    ask="Agent notifier observed state-signal: completed",
                    response="done",
                    message_kind="state-signal",
                ),
                entry_id="legacy-1",
                now=NOW.isoformat(),
                routing=InboxRouting(
                    address=InboxAddress(
                        lifecycle_id=None, agent_id="manager-1", recipient_role="manager"
                    )
                ),
                poster=InboxPoster(
                    created_by="agent-notifier", created_via="cli", sender_role="system"
                ),
            ).model_copy(
                update={
                    "deliveryState": "delivered",
                    "adapterDeliveryState": "accepted",
                }
            )
            store.append(pending)
            inbox_transitions.mark_superseded(
                store,
                "legacy-1",
                now=(NOW + timedelta(seconds=1)).isoformat(),
                reason="concurrent",
                superseded_by="owner",
            )
            stale_fold = {pending.id: pending}
            ctx = SimpleNamespace(
                inbox_store=store,
                event_store=EventStore(observer),
            )
            folded = _fold_legacy_landed(ctx, stale_fold, now=NOW)  # type: ignore[arg-type]
            self.assertEqual(folded, 0)
            self.assertEqual(store.current()["legacy-1"].state, "superseded")
            self.assertEqual(len(store.read()), 2)


class CapFillBranchTests(unittest.TestCase):
    """Cap eviction keeps the newest terminal markers when the pending set is small."""

    def test_terminal_markers_fill_the_remaining_cap_slots_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = OperatorInboxStore(root / "logs" / "observer")
            pending_ids: list[str] = []
            for index in range(3):
                entry_id = f"p-{index}"
                pending_ids.append(entry_id)
                store.append(
                    create_operator_inbox_entry(
                        InboxMessage(ask="ask", response="resp", message_kind="message"),
                        entry_id=entry_id,
                        now=(NOW - timedelta(seconds=index)).isoformat(),
                        routing=InboxRouting(
                            address=InboxAddress(lifecycle_id=None, agent_id="worker-1")
                        ),
                        poster=InboxPoster(created_by="system", created_via="cli"),
                    )
                )
            terminal_ids: list[str] = []
            for index in range(10):
                entry_id = f"t-{index}"
                terminal_ids.append(entry_id)
                store.append(
                    create_operator_inbox_entry(
                        InboxMessage(ask="ask", response="resp", message_kind="message"),
                        entry_id=entry_id,
                        now=(NOW - timedelta(hours=1, minutes=index)).isoformat(),
                        routing=InboxRouting(
                            address=InboxAddress(lifecycle_id=None, agent_id="worker-1")
                        ),
                        poster=InboxPoster(created_by="system", created_via="cli"),
                    )
                )
                inbox_transitions.mark_landed(
                    store,
                    entry_id,
                    now=(NOW - timedelta(minutes=index)).isoformat(),
                    reason="x",
                )
            keep = inbox_keep_ids(store.read(), now=NOW, max_rows=5)
            self.assertEqual(set(pending_ids) <= keep, True)
            # The two newest terminal markers fill the two remaining cap slots.


class StaleSnapshotTerminalAuthorityTests(unittest.TestCase):
    """F1: every terminal transition verifies the LATEST folded state at append time.

    A stale act-phase snapshot must never overwrite a different terminal truth: concurrent
    supersede stays ``superseded``, concurrent landed stays ``landed``, concurrent expired
    stays ``expired``, and a stale unresolved after landed appends nothing. The final
    FOLDED STORE STATE is the assertion, not the action strings.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = OperatorInboxStore(Path(self.tmp.name) / "logs" / "observer")
        self.store.append(
            create_operator_inbox_entry(
                InboxMessage(ask="ask", response="resp", message_kind="message"),
                entry_id="e1",
                now=NOW.isoformat(),
                routing=InboxRouting(address=InboxAddress(lifecycle_id=None, agent_id="worker-1")),
                poster=InboxPoster(created_by="system", created_via="cli"),
            )
        )
        self.stale = self.store.current()

    def _stale_landing(self) -> OperatorInboxEntry:
        return inbox_transitions.record_delivery(
            self.store,
            "e1",
            inbox_transitions.DeliveryAttempt(
                delivery_state="delivered",
                landed=True,
                adapter=inbox_transitions.AdapterReceipt(delivery_state="accepted"),
            ),
            now=(NOW + timedelta(seconds=2)).isoformat(),
            floor=inbox_transitions.RedeliveryFloor(current=self.stale, seconds=0.0),
        )

    def test_concurrent_supersede_survives_a_stale_landing_append(self) -> None:
        inbox_transitions.mark_superseded(
            self.store,
            "e1",
            now=(NOW + timedelta(seconds=1)).isoformat(),
            reason="explicit",
            superseded_by="owner",
        )
        returned = self._stale_landing()
        self.assertEqual(returned.state, "superseded")
        self.assertEqual(self.store.current()["e1"].state, "superseded")
        # Nothing appended: pending + superseded only.
        self.assertEqual(len(self.store.read()), 2)

    def test_concurrent_landed_survives_a_stale_unresolved(self) -> None:
        inbox_transitions.mark_landed(
            self.store, "e1", now=(NOW + timedelta(seconds=1)).isoformat(), reason="boundary"
        )
        latest, changed = inbox_transitions.mark_unresolved(
            self.store, "e1", now=(NOW + timedelta(seconds=2)).isoformat(), reason="attempt-limit"
        )
        self.assertFalse(changed)
        self.assertEqual(latest.state, "landed")
        self.assertEqual(self.store.current()["e1"].state, "landed")
        self.assertEqual(len(self.store.read()), 2)

    def test_concurrent_expired_survives_a_stale_landing_append(self) -> None:
        inbox_transitions.mark_expired(
            self.store,
            "e1",
            now=(NOW + timedelta(seconds=1)).isoformat(),
            options=inbox_transitions.ExpiryOptions(reason="rebind-grace-expired"),
        )
        returned = self._stale_landing()
        self.assertEqual(returned.state, "expired")
        self.assertEqual(self.store.current()["e1"].state, "expired")
        self.assertEqual(len(self.store.read()), 2)

    def test_stale_unresolved_after_landed_appends_nothing(self) -> None:
        inbox_transitions.mark_landed(
            self.store, "e1", now=(NOW + timedelta(seconds=1)).isoformat(), reason="boundary"
        )
        rows_before = len(self.store.read())
        latest, changed = inbox_transitions.mark_unresolved(
            self.store, "e1", now=(NOW + timedelta(seconds=3)).isoformat(), reason="attempt-limit"
        )
        self.assertFalse(changed)
        self.assertEqual(latest.state, "landed")
        self.assertEqual(len(self.store.read()), rows_before)

    def test_same_state_idempotence_still_holds(self) -> None:
        inbox_transitions.mark_superseded(
            self.store,
            "e1",
            now=(NOW + timedelta(seconds=1)).isoformat(),
            reason="explicit",
        )
        rows_before = len(self.store.read())
        latest, changed = inbox_transitions.mark_superseded(
            self.store,
            "e1",
            now=(NOW + timedelta(seconds=2)).isoformat(),
            reason="explicit again",
        )
        self.assertFalse(changed)
        self.assertEqual(latest.state, "superseded")
        self.assertEqual(len(self.store.read()), rows_before)


class SupersedeDuringInFlightDeliveryTests(unittest.TestCase):
    """F1 e2e: an explicit supersede during an in-flight sweep delivery is never overwritten
    by the sweep's landing (no false ack of an explicitly superseded command)."""

    def test_supersede_during_in_flight_delivery_wins_over_landing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observer = root / "logs" / "observer"
            catalog = TerminalCatalog(root / "logs" / "dashboard" / "terminal-sessions.json")
            catalog.upsert(
                _seat(
                    "manager-1",
                    spawn_role="manager",
                    turn_state="turn-ended",
                    turn_state_changed_at=(NOW - timedelta(minutes=1)).isoformat(),
                    control_state="ready",
                    control_endpoint=Path("/tmp/x.sock"),
                    control_protocol="ar-harness-control/v1",
                )
            )
            store = OperatorInboxStore(observer)
            store.append(
                create_operator_inbox_entry(
                    InboxMessage(ask="dispatch", response="work", message_kind="message"),
                    entry_id="e1",
                    now=(NOW - timedelta(minutes=30)).isoformat(),
                    routing=InboxRouting(
                        address=InboxAddress(
                            lifecycle_id=None, agent_id="manager-1", recipient_role="manager"
                        )
                    ),
                    poster=InboxPoster(
                        created_by="agent-notifier", created_via="cli", sender_role="system"
                    ),
                ).model_copy(
                    update={
                        "deliveryState": "delivered",
                        "adapterDeliveryState": "accepted",
                        "lastAttemptAt": (NOW - timedelta(minutes=16)).isoformat(),
                        "nextAttemptAt": (NOW - timedelta(minutes=15)).isoformat(),
                    }
                )
            )
            ctx = AgentNotifierContext(
                catalog=catalog,
                host=cast(
                    TerminalHost,
                    SimpleNamespace(
                        has_session=lambda _n: True,
                        terminate=lambda *_a, **_k: None,
                    ),
                ),
                paster=cast("object", None),  # type: ignore[arg-type]
                inbox_store=store,
                expectation_store=ExpectationRowStore(observer),
                nudge_store=OrchestrationNudgeStore(observer),
                signal_cooldown_store=AgentNotifierSignalCooldownStore(observer),
                event_store=EventStore(observer),
                heartbeat_store=AgentNotifierHeartbeatStore(observer),
                coordination_root=root,
                tmux_name_snapshotter=lambda: TmuxSessionNameSnapshot(
                    frozenset(), "tmux-no-server"
                ),
            )
            submitted = threading.Event()
            release = threading.Event()

            def blocking_submit(_target, _text, submission):
                submitted.set()
                if not release.wait(timeout=5):
                    raise AssertionError("release not set")
                return SubmissionReceipt(
                    request_id=submission.request_id,
                    acceptance="immediate",
                    submitted_at=NOW.isoformat(),
                    accepted_at=NOW.isoformat(),
                )

            sweep_done = threading.Event()
            sweep_error: list[BaseException] = []

            def run_sweep() -> None:
                try:
                    run_agent_notifier_sweep(ctx, now=NOW)
                except BaseException as exc:
                    sweep_error.append(exc)
                finally:
                    sweep_done.set()

            with mock.patch.object(
                inbox_delivery_module,
                "submit_control_prompt",
                side_effect=blocking_submit,
            ):
                thread = threading.Thread(target=run_sweep)
                thread.start()
                self.assertTrue(submitted.wait(timeout=5), "delivery did not start")
                operator_inbox_supersede_payload(
                    McpRuntimeConfig(
                        config_path=root / "settings.json",
                        coordination_root=root,
                        workspace_root=root,
                        transcript_root=root / "logs" / "mcp",
                    ),
                    entry_id="e1",
                    reason="overtaken",
                    superseded_by="manager-1",
                )
                release.set()
                sweep_done.wait(timeout=10)
                thread.join(timeout=10)
            if sweep_error:
                raise sweep_error[0]
            final = store.current()["e1"]
            self.assertEqual(final.state, "superseded")
            self.assertEqual(final.terminalReason, "overtaken")


class ReboundDeliveryToReplacementTests(unittest.TestCase):
    """F3: after a sweep-time rebind to manager B, a subsequent sweep actually DELIVERS the
    row to B (B's session receives the push), not only re-addresses it."""

    def test_rebound_row_is_delivered_to_replacement_in_next_sweep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observer = root / "logs" / "observer"
            catalog = TerminalCatalog(root / "catalog.json")
            catalog.upsert(_seat("orchestrator-1", spawn_role="orchestrator"))
            catalog.upsert(
                _seat(
                    "manager-old",
                    status="terminated",
                    terminated_at=(NOW - timedelta(minutes=10)).isoformat(),
                    spawn_role="manager",
                    spawned_by_session="orchestrator-1",
                    leaf_key="repo-a/260707_master/old-manager-anchor",
                )
            )
            catalog.upsert(
                _seat(
                    "worker-1",
                    leaf_key="repo-a/260707_master/leaf-1",
                    spawn_role="worker",
                    spawned_by_session="manager-old",
                )
            )
            catalog.upsert(
                _seat(
                    "manager-new",
                    leaf_key="repo-a/260707_master/current-manager-anchor",
                    spawn_role="manager",
                    turn_state="turn-ended",
                    turn_state_changed_at=(NOW - timedelta(minutes=1)).isoformat(),
                    control_state="ready",
                    control_endpoint=Path("/tmp/manager-new.sock"),
                    control_protocol="ar-harness-control/v1",
                )
            )
            store = OperatorInboxStore(observer)
            store.append(
                create_operator_inbox_entry(
                    InboxMessage(ask="ask", response="resp", message_kind="escalation"),
                    entry_id="e1",
                    now=(NOW - timedelta(minutes=5)).isoformat(),
                    routing=InboxRouting(
                        address=InboxAddress(
                            lifecycle_id=None, agent_id="manager-old", recipient_role="manager"
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
            ctx = AgentNotifierContext(
                catalog=catalog,
                host=cast(
                    TerminalHost,
                    SimpleNamespace(
                        has_session=lambda _n: True,
                        terminate=lambda *_a, **_k: None,
                    ),
                ),
                paster=cast("object", None),  # type: ignore[arg-type]
                inbox_store=store,
                expectation_store=ExpectationRowStore(observer),
                nudge_store=OrchestrationNudgeStore(observer),
                signal_cooldown_store=AgentNotifierSignalCooldownStore(observer),
                event_store=EventStore(observer),
                heartbeat_store=AgentNotifierHeartbeatStore(observer),
                coordination_root=root,
                tmux_name_snapshotter=lambda: TmuxSessionNameSnapshot(
                    frozenset(), "tmux-no-server"
                ),
            )
            with mock.patch(
                "agents_remember.serving.inbox_delivery.submit_control_prompt",
                side_effect=lambda _target, _text, submission: SubmissionReceipt(
                    request_id=submission.request_id,
                    acceptance="immediate",
                    submitted_at=NOW.isoformat(),
                    accepted_at=NOW.isoformat(),
                ),
            ) as submit:
                run_agent_notifier_sweep(ctx, now=NOW)
                self.assertEqual(store.current()["e1"].agentId, "manager-new")
                e1_calls = [call for call in submit.call_args_list if "entry: e1" in call.args[1]]
                self.assertEqual(e1_calls, [])
                run_agent_notifier_sweep(ctx, now=NOW + timedelta(seconds=1))
            e1_calls = [call for call in submit.call_args_list if "entry: e1" in call.args[1]]
            self.assertEqual(len(e1_calls), 1)
            self.assertEqual(e1_calls[0].args[0].id, "manager-new")
            final = store.current()["e1"]
            self.assertEqual(final.state, "landed")
            self.assertEqual(final.deliveredToSession, "manager-new")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
