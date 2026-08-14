"""Judgment-demolition forcing tests (260713-TES-L5).

The relay must carry no suspect-respawn path, no timed escalation-ladder policy,
no inferred nudges, and no ack-by/turn-report-by expectation interpretation; a
landed row must never escalate; and the live orchestrator→manager→worker loop
must be provable as a fact-only emission vocabulary (done/interrupted/idle/
compound-idle/non-reaction are the complete relay vocabulary).

Written FIRST (red against the pre-demolition code), then pinned green after the
demolition lands: every class here is a demolition proof, not a behavior test.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from agents_remember.controlplane import expectation_rows
from agents_remember.controlplane import operator_inbox_transitions as transitions
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
    InboxMessageKind,
    InboxPoster,
    InboxRouting,
    OperatorInboxEntry,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.orchestration_nudges import OrchestrationNudgeStore
from agents_remember.kernel import _agentic_settings_core as settings_core
from agents_remember.kernel import agentic_settings
from agents_remember.kernel.agentic_settings import AgenticSettingsError, _parse_expectations
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.store import EventStore
from agents_remember.serving import _agent_notifier_actions as notifier_actions
from agents_remember.serving.agent_notifier import AgentNotifierContext, run_agent_notifier_sweep
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.agent_notifier_models import ActionKind, FindingKind
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import PasteResult, TerminalPaster
from agents_remember.tasks import TaskDocument, write_task_doc

NOW = datetime(2026, 7, 13, 15, 41, 0, tzinfo=UTC)
SPRINT_REF = TaskDocumentRef(repository="repo-a", path="sprint/task.json")
MASTER_REF = TaskDocumentRef(repository="repo-a", path="260707_master/task.json")
LEAF_REF = TaskDocumentRef(repository="repo-a", path="260707_master/leaf-9.json")

ACTIONS_SOURCE = Path(notifier_actions.__file__)


def _entry(
    session_id: str,
    *,
    task_document_ref: TaskDocumentRef | None = None,
    seat_role: str | None = None,
    **overrides: Any,
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
        seat_role=seat_role,
        **overrides,  # type: ignore[arg-type]
    )


def _orchestrator(session_id: str = "orchestrator-1", **overrides: Any) -> TerminalCatalogEntry:
    return _entry(
        session_id,
        task_document_ref=SPRINT_REF,
        seat_role="orchestrator",
        spawn_role="orchestrator",
        turn_state="turn-ended",
        turn_state_changed_at=NOW.isoformat(),
        **overrides,
    )


def _manager(session_id: str = "manager-1", **overrides: Any) -> TerminalCatalogEntry:
    return _entry(
        session_id,
        task_document_ref=MASTER_REF,
        seat_role="manager",
        spawn_role="manager",
        spawned_by_session="orchestrator-1",
        turn_state="turn-ended",
        turn_state_changed_at=NOW.isoformat(),
        **overrides,
    )


def _done_worker(session_id: str = "worker-1", **overrides: Any) -> TerminalCatalogEntry:
    return _entry(
        session_id,
        task_document_ref=LEAF_REF,
        seat_role="worker",
        spawn_role="worker",
        spawned_by_session="manager-1",
        turn_state="turn-ended",
        turn_state_changed_at=NOW.isoformat(),
        terminal_outcome="completed",
        terminal_outcome_at=NOW.isoformat(),
        terminal_evidence_id="turn-9",
        **overrides,
    )


class _FakeHost:
    def has_session(self, _tmux_name: str) -> bool:
        return True

    def terminate(self, _sid: str, *, tmux_name: str | None = None) -> None:
        pass


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


class _DemolitionCase(unittest.TestCase):
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
        task_root = self.coordination_root / "tasks" / "repo-a"
        write_task_doc(
            task_root / "sprint",
            TaskDocument.model_validate(
                {
                    "id": "SPRINT",
                    "slug": "sprint",
                    "title": "Sprint",
                    "kind": "master",
                    "repo": "repo-a",
                    "createdAt": "2026-07-07T00:00",
                    "orchestrates": ["260707_master"],
                }
            ),
        )
        write_task_doc(
            task_root / "260707_master",
            TaskDocument.model_validate(
                {
                    "id": "MASTER",
                    "slug": "260707_master",
                    "title": "Master",
                    "kind": "master",
                    "repo": "repo-a",
                    "createdAt": "2026-07-07T00:00",
                    "subTasks": [
                        {
                            "number": "leaf-9",
                            "name": "Leaf 9",
                            "file": "leaf-9.md",
                            "status": "inProgress",
                        }
                    ],
                }
            ),
        )
        write_task_doc(
            task_root / "260707_master",
            TaskDocument.model_validate(
                {
                    "id": "LEAF-9",
                    "slug": "leaf-9",
                    "title": "Leaf 9",
                    "kind": "subTask",
                    "repo": "repo-a",
                    "createdAt": "2026-07-07T00:00",
                    "master": "task.md",
                }
            ),
        )

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
        if "nudge_store" in AgentNotifierContext.__dataclass_fields__:  # type: ignore[attr-defined]
            base["nudge_store"] = self.nudge_store
        base.update(overrides)
        return AgentNotifierContext(**base)  # type: ignore[arg-type]

    def _events(self) -> list[str]:
        return [event.kind for event in self.event_store.read(None)]

    def _append_owner_row(
        self, entry_id: str, *, target: str, kind: InboxMessageKind = "message"
    ) -> None:
        self.inbox_store.append(
            create_operator_inbox_entry(
                InboxMessage(ask="ask", response="resp", message_kind=kind),
                entry_id=entry_id,
                now=(NOW - timedelta(minutes=10)).isoformat(),
                routing=InboxRouting(address=InboxAddress(lifecycle_id=None, agent_id=target)),
                poster=InboxPoster(created_by="system", created_via="cli"),
            )
        )


class SuspectRespawnDemolitionTests(_DemolitionCase):
    """(a) The relay never retires or respawns a seat."""

    def test_no_respawn_function_on_actions_module(self) -> None:
        self.assertFalse(hasattr(notifier_actions, "_respawn_suspect"))

    def test_no_seat_is_suspect_importable(self) -> None:
        self.assertIsNone(
            importlib.util.find_spec("agents_remember.controlplane.escalation_ladder")
        )

    def test_no_escalation_ladder_module(self) -> None:
        self.assertIsNone(
            importlib.util.find_spec("agents_remember.controlplane.escalation_ladder")
        )

    def test_no_suspect_retire_reason_in_actions_source(self) -> None:
        source = ACTIONS_SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("escalation-ladder-suspect", source)

    def test_no_respawn_after_rung_settings_surface(self) -> None:
        self.assertFalse(hasattr(settings_core, "DEFAULT_RESPAWN_AFTER_RUNG"))
        self.assertFalse(hasattr(agentic_settings, "DEFAULT_RESPAWN_AFTER_RUNG"))

    def test_sweep_never_retires_a_stale_seat(self) -> None:
        self.catalog.upsert(_manager())
        self.catalog.upsert(
            replace(
                _done_worker(),
                turn_state="stale",
                turn_state_changed_at=(NOW - timedelta(minutes=5)).isoformat(),
            )
        )
        result = run_agent_notifier_sweep(self._ctx(), now=NOW)
        self.assertFalse(any(a.action == "respawn" for a in result.actions))
        self.assertFalse(
            any(
                a.action == "signal-manager" and "respawn" in (a.detail or "")
                for a in result.actions
            )
        )
        worker = self.catalog.get("worker-1")
        assert worker is not None
        self.assertEqual(worker.status, "running")


class LadderPolicyDemolitionTests(_DemolitionCase):
    """(b) No timed rung climbing (renudge/skip-level/architect-attention as policy)."""

    def test_no_rung_due_or_next_step_importable(self) -> None:
        self.assertIsNone(
            importlib.util.find_spec("agents_remember.controlplane.escalation_ladder")
        )

    def test_no_escalation_due_finding_kind(self) -> None:
        self.assertNotIn("escalation-due", FindingKind.__args__)

    def test_no_escalate_rung_action_kind(self) -> None:
        self.assertNotIn("escalate-rung", ActionKind.__args__)

    def test_no_ladder_actions_registered(self) -> None:
        self.assertNotIn("escalation-due", notifier_actions._FINDING_ACTIONS)
        self.assertNotIn("inbox-ladder-terminal", notifier_actions._FINDING_ACTIONS)
        self.assertNotIn("expectation-overdue", notifier_actions._FINDING_ACTIONS)

    def test_no_ladder_transitions(self) -> None:
        self.assertFalse(hasattr(transitions, "mark_escalated"))
        self.assertFalse(hasattr(transitions, "advance_rung"))
        self.assertFalse(hasattr(transitions, "mark_ladder_resolved"))
        self.assertFalse(hasattr(transitions, "RungAdvance"))

    def test_no_escalation_settings_family(self) -> None:
        self.assertNotIn("escalation", settings_core.KNOWN_ORCHESTRATION_FIELDS)
        self.assertFalse(hasattr(settings_core, "KNOWN_ESCALATION_FIELDS"))
        self.assertFalse(hasattr(agentic_settings, "EscalationSettings"))

    def test_sweep_never_emits_escalation_rung_event(self) -> None:
        for index in range(3):
            self._append_owner_row(f"e{index}", target=f"dead-seat-{index}")
        result = run_agent_notifier_sweep(self._ctx(), now=NOW)
        self.assertNotIn("orchestration.escalation.rung", self._events())
        self.assertNotIn("orchestration.agent-notifier.escalate", self._events())
        self.assertFalse(any(a.action == "escalate-rung" for a in result.actions))


class InferredNudgeDemolitionTests(_DemolitionCase):
    """(c) No `_auto_nudge` inferred reasons, no `_mark_expectation_missed`."""

    def test_no_auto_nudge_function(self) -> None:
        self.assertFalse(hasattr(notifier_actions, "_auto_nudge"))

    def test_no_mark_expectation_missed_function(self) -> None:
        self.assertFalse(hasattr(notifier_actions, "_mark_expectation_missed"))

    def test_no_auto_nudge_action_kind(self) -> None:
        self.assertNotIn("auto-nudge", ActionKind.__args__)

    def test_overdue_expectation_rows_produce_no_findings(self) -> None:
        for kind in ("verdict-by", "ack-by", "turn-report-by"):
            write_expectation_row(
                self.expectation_store,
                Expectation(
                    kind=kind,  # type: ignore[arg-type]
                    source_id=f"source-{kind}",
                    subject=ExpectationSubject(agent_id="worker-1"),
                ),
                row_id=f"exp-{kind}",
                now=NOW - timedelta(minutes=10),
                sla_seconds=60.0,
            )
        self.catalog.upsert(_manager())
        self.catalog.upsert(_done_worker())
        result = run_agent_notifier_sweep(self._ctx(), now=NOW)
        self.assertFalse(any(f.kind == "expectation-overdue" for f in result.findings))
        self.assertFalse(any(a.action == "auto-nudge" for a in result.actions))
        self.assertNotIn("orchestration.nudge", self._events())


class AckByRetirementTests(_DemolitionCase):
    """(d) ack-by expectations retired: no consume to expect, no settings surface."""

    def test_ack_by_not_in_settings_kinds(self) -> None:
        self.assertNotIn("ack-by", settings_core.KNOWN_EXPECTATION_KINDS)

    def test_ack_by_not_in_default_sla(self) -> None:
        self.assertNotIn("ack-by", settings_core.DEFAULT_EXPECTATION_SLA_SECONDS)

    def test_ack_by_settings_override_is_refused(self) -> None:
        with self.assertRaises(AgenticSettingsError):
            _parse_expectations({"defaults": {"ack-by": 60.0}}, source="<test>")

    def test_no_source_writes_ack_by_rows(self) -> None:
        source = Path(expectation_rows.__file__).read_text(encoding="utf-8")
        self.assertNotIn('kind="ack-by"', source)

    def test_legacy_ack_by_row_is_parseable_and_silent(self) -> None:
        write_expectation_row(
            self.expectation_store,
            Expectation(
                kind="ack-by",  # type: ignore[arg-type]
                source_id="legacy-1",
                subject=ExpectationSubject(agent_id="worker-1"),
            ),
            row_id="legacy-ack",
            now=NOW - timedelta(hours=2),
            sla_seconds=300.0,
        )
        self.catalog.upsert(_manager())
        self.catalog.upsert(_done_worker())
        result = run_agent_notifier_sweep(self._ctx(), now=NOW)
        self.assertFalse(any(f.source_id == "legacy-ack" for f in result.findings))
        self.assertFalse(any(a.finding.source_id == "legacy-ack" for a in result.actions))


class TurnReportByRetirementTests(_DemolitionCase):
    """(e) turn-report-by retired: verify no consumer writes or evaluates it."""

    def test_no_source_writes_turn_report_by_rows(self) -> None:
        src_root = Path(str(sys.modules["agents_remember"].__file__)).resolve().parent
        offenders: list[str] = []
        for path in src_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if 'kind="turn-report-by"' in text or 'kind="turn-report-by",' in text:
                offenders.append(str(path))
        self.assertEqual(offenders, [])

    def test_legacy_turn_report_by_row_is_parseable_and_silent(self) -> None:
        write_expectation_row(
            self.expectation_store,
            Expectation(
                kind="turn-report-by",  # type: ignore[arg-type]
                source_id="legacy-tr",
                subject=ExpectationSubject(
                    agent_id="worker-1",
                    task_document_ref=LEAF_REF,
                    seat_role="worker",
                ),
            ),
            row_id="legacy-tr",
            now=NOW - timedelta(hours=2),
            sla_seconds=60.0,
        )
        self.catalog.upsert(_manager())
        self.catalog.upsert(_done_worker())
        result = run_agent_notifier_sweep(self._ctx(), now=NOW)
        self.assertFalse(any(f.source_id == "legacy-tr" for f in result.findings))
        self.assertFalse(any(a.finding.source_id == "legacy-tr" for a in result.actions))


class LandedRowNeverEscalatesTests(_DemolitionCase):
    """(f) A landed row is terminal: no retry, nudge, rebind, expiry, or escalation ever."""

    def test_landed_row_stays_terminal_across_sweeps(self) -> None:
        self.catalog.upsert(_manager())
        self._append_owner_row("e1", target="manager-1", kind="state-signal")
        transitions.mark_landed(
            self.inbox_store,
            "e1",
            now=NOW.isoformat(),
            reason="adapter-accepted-at-turn-boundary",
        )
        ctx = self._ctx()
        for offset in (0, 10, 30, 60):
            result = run_agent_notifier_sweep(ctx, now=NOW + timedelta(seconds=offset))
            self.assertFalse(any(f.source_id == "e1" for f in result.findings))
            self.assertFalse(any(a.finding.source_id == "e1" for a in result.actions))
        current = self.inbox_store.current()["e1"]
        self.assertEqual(current.state, "landed")
        self.assertEqual(self.inbox_store.list_redeliverable(now=NOW + timedelta(hours=1)), [])
        self.assertNotIn("orchestration.escalation.rung", self._events())
        self.assertNotIn("orchestration.agent-notifier.escalate", self._events())


class LiveChainShapeTests(_DemolitionCase):
    """(g) The live orchestrator→manager→worker loop, as a runnable E2E shape.

    Worker turn-ended with completed terminal evidence → exactly one durable
    state-signal to the manager; the same sweep relays the compound-idle fact to
    the orchestrator when the whole set is idle; re-sweeps re-emit nothing; and
    the whole run's emission vocabulary is facts only -- no nudge, no escalation
    rung, no respawn, no expectation interpretation.
    """

    def _seed_live_chain(self) -> None:
        self.catalog.upsert(_orchestrator())
        # The manager crossed its last boundary BEFORE the expectation row was created, so
        # no chain progress can mask the overdue verdict-by row in the pre-demolition code.
        self.catalog.upsert(
            replace(
                _manager(),
                turn_state_changed_at=(NOW - timedelta(minutes=20)).isoformat(),
            )
        )
        self.catalog.upsert(_done_worker())
        write_expectation_row(
            self.expectation_store,
            Expectation(
                kind="verdict-by",
                source_id="worker-1",
                subject=ExpectationSubject(
                    agent_id="worker-1",
                    task_document_ref=LEAF_REF,
                    seat_role="worker",
                ),
            ),
            row_id="exp-1",
            now=NOW - timedelta(minutes=10),
            sla_seconds=60.0,
        )

    def _state_signals(self) -> list[OperatorInboxEntry]:
        return [
            entry
            for entry in self.inbox_store.current().values()
            if entry.messageKind == "state-signal"
        ]

    def test_worker_done_and_compound_idle_relay_with_fact_only_vocabulary(self) -> None:
        self._seed_live_chain()
        ctx = self._ctx()
        first = run_agent_notifier_sweep(ctx, now=NOW)
        signals = self._state_signals()
        # Worker done → manager: exactly one durable row addressed to the manager.
        worker_signal = [s for s in signals if s.subjectAgentId == "worker-1"]
        self.assertEqual(len(worker_signal), 1, first.actions)
        self.assertEqual(worker_signal[0].agentId, "manager-1")
        self.assertIn("completed", worker_signal[0].ask)
        self.assertIn("turn-9", worker_signal[0].response)
        # Compound idle → orchestrator: exactly one durable row addressed to the orchestrator.
        compound = [s for s in signals if "compound-idle" in s.ask]
        self.assertEqual(len(compound), 1, first.actions)
        self.assertEqual(compound[0].agentId, "orchestrator-1")
        self.assertIn("repo-a/260707_master/leaf-9.json as worker", compound[0].response)
        self.assertNotIn("worker-1", compound[0].response)
        # No inferred nudge from the overdue verdict-by row, and the row stays pending
        # (relocated to owner agents: surfaced, never judged by the relay).
        self.assertFalse(any(a.action == "auto-nudge" for a in first.actions))
        self.assertEqual(self.expectation_store.current()["exp-1"].state, "pending")
        # Re-sweeps re-emit nothing (dedupe by catalog markers) and land no new rows.
        second = run_agent_notifier_sweep(ctx, now=NOW + timedelta(seconds=10))
        self.assertEqual(len(self._state_signals()), 2)
        self.assertFalse(any(a.action == "state-signal" for a in second.actions))
        self.assertFalse(any(a.action == "compound-idle" for a in second.actions))
        # Event audit: fact relays only -- the complete relay vocabulary is
        # done/interrupted/idle/compound-idle/non-reaction (+ their delivery facts).
        forbidden = {
            "orchestration.nudge",
            "orchestration.escalation.rung",
            "orchestration.agent-notifier.escalate",
            "orchestration.agent-notifier.respawn",
            "orchestration.agent-notifier.ladder-resolved",
            "orchestration.agent-notifier.signal",  # seat-liveness is not seeded; stay quiet
        }
        emitted = set(self._events())
        self.assertEqual(emitted & forbidden, set(), emitted)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
