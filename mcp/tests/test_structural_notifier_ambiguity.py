"""An ambiguous canonical seat fences its own row without poisoning the notifier sweep."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import cast

from agents_remember.controlplane.agent_notifier_signals import AgentNotifierSignalCooldownStore
from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    InboxSubject,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.observer.store import EventStore
from agents_remember.serving import _agent_notifier_evaluation as notifier_evaluation
from agents_remember.serving import state_signals
from agents_remember.serving.agent_notifier import AgentNotifierContext, run_agent_notifier_sweep
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.tasks.document_refs import TaskDocumentTopology
from test_agent_notifier import NOW, _entry, _fake_paster, _FakeHost
from test_agent_notifier_ladder import MASTER_REF, SPRINT_REF, _leaf_ref, _write_topology


class StructuralNotifierAmbiguityTests(unittest.TestCase):
    def test_read_only_evaluators_fence_only_the_ambiguous_seat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordination_root = root / "ar-coordination"
            _write_topology(coordination_root)
            catalog = TerminalCatalog(root / "catalog.json")
            for session_id in ("manager-a", "manager-b"):
                catalog.upsert(
                    replace(
                        _entry(session_id, task_document_ref=MASTER_REF),
                        seat_role="manager",
                    )
                )

            rebind_row = create_operator_inbox_entry(
                InboxMessage(
                    ask="rebind",
                    response="do not guess",
                    subject=InboxSubject(
                        task_document_ref=_leaf_ref(1),
                        seat_role="worker",
                        agent_id="worker-dead",
                    ),
                ),
                entry_id="ambiguous-rebind",
                now=NOW.isoformat(),
                routing=InboxRouting(
                    address=InboxAddress(
                        task_document_ref=MASTER_REF,
                        agent_id="manager-dead",
                        recipient_role="manager",
                    )
                ),
                poster=InboxPoster(created_by="worker-dead", created_via="cli"),
            )
            self.assertEqual(
                notifier_evaluation.evaluate_rebind_findings(
                    catalog,
                    TaskDocumentTopology(coordination_root),
                    current={rebind_row.id: rebind_row},
                    now=NOW,
                ),
                [],
            )

            signal_row = create_operator_inbox_entry(
                InboxMessage(
                    ask="state",
                    response="hold at the boundary",
                    message_kind="state-signal",
                ),
                entry_id="ambiguous-signal",
                now=NOW.isoformat(),
                routing=InboxRouting(
                    address=InboxAddress(
                        task_document_ref=MASTER_REF,
                        recipient_role="manager",
                    )
                ),
                poster=InboxPoster(created_by="system", created_via="cli"),
            )
            self.assertTrue(state_signals.state_signal_held_on_boundary(catalog, signal_row))
            attempted = signal_row.model_copy(update={"lastAttemptAt": NOW.isoformat()})
            self.assertEqual(
                state_signals.evaluate_boundary_drain_findings(
                    catalog,
                    {attempted.id: attempted},
                ),
                [],
            )
            self.assertEqual(state_signals._current_manager_rows(catalog.list()), [])

    def test_ambiguous_row_is_skipped_while_unrelated_retry_and_heartbeat_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordination_root = root / "ar-coordination"
            _write_topology(coordination_root)
            observer_root = coordination_root / "logs" / "observer"
            catalog = TerminalCatalog(root / "catalog.json")
            catalog.upsert(
                replace(
                    _entry("orchestrator", task_document_ref=SPRINT_REF),
                    seat_role="orchestrator",
                )
            )
            for session_id in ("manager-a", "manager-b"):
                catalog.upsert(
                    replace(
                        _entry(session_id, task_document_ref=MASTER_REF),
                        seat_role="manager",
                    )
                )
            catalog.upsert(
                replace(
                    _entry("worker", task_document_ref=_leaf_ref(1)),
                    seat_role="worker",
                )
            )
            catalog.upsert(
                replace(
                    _entry("invalid-stale", task_document_ref=SPRINT_REF),
                    seat_role="system-specialist",
                    turn_state="stale",
                    turn_state_changed_at=(NOW - timedelta(minutes=5)).isoformat(),
                )
            )
            inbox_store = OperatorInboxStore(observer_root)
            inbox_store.append(
                create_operator_inbox_entry(
                    InboxMessage(ask="ambiguous", response="hold this row"),
                    entry_id="ambiguous-row",
                    now=NOW.isoformat(),
                    routing=InboxRouting(
                        address=InboxAddress(
                            task_document_ref=MASTER_REF,
                            recipient_role="manager",
                        )
                    ),
                    poster=InboxPoster(created_by="system", created_via="cli"),
                )
            )
            inbox_store.append(
                create_operator_inbox_entry(
                    InboxMessage(
                        ask="Agent notifier observed seat-liveness: invalid root",
                        response="fence only this row",
                        subject=InboxSubject(
                            task_document_ref=SPRINT_REF,
                            seat_role="system-specialist",
                            agent_id="invalid-stale",
                        ),
                    ),
                    entry_id="invalid-chain-row",
                    now=NOW.isoformat(),
                    routing=InboxRouting(address=InboxAddress(agent_id="orchestrator")),
                    poster=InboxPoster(created_by="agent-notifier", created_via="cli"),
                )
            )
            inbox_store.append(
                create_operator_inbox_entry(
                    InboxMessage(ask="unrelated", response="continue this row"),
                    entry_id="unrelated-row",
                    now=NOW.isoformat(),
                    routing=InboxRouting(address=InboxAddress(agent_id="orchestrator")),
                    poster=InboxPoster(created_by="system", created_via="cli"),
                )
            )
            heartbeat = AgentNotifierHeartbeatStore(observer_root)
            context = AgentNotifierContext(
                catalog=catalog,
                host=cast(TerminalHost, _FakeHost()),
                paster=_fake_paster(),
                inbox_store=inbox_store,
                expectation_store=ExpectationRowStore(observer_root),
                signal_cooldown_store=AgentNotifierSignalCooldownStore(observer_root),
                event_store=EventStore(observer_root),
                heartbeat_store=heartbeat,
                coordination_root=coordination_root,
                stale_seat_seconds=60.0,
                redeliver_budget=2,
            )

            result = run_agent_notifier_sweep(context, now=NOW)

            by_source = {action.finding.source_id: action for action in result.actions}
            self.assertEqual(by_source["ambiguous-row"].outcome, "skipped")
            self.assertEqual(by_source["unrelated-row"].action, "redeliver")
            self.assertNotIn("invalid-chain-row", by_source)
            self.assertEqual(inbox_store.current()["ambiguous-row"].attemptCount, 0)
            state = heartbeat.read()
            assert state is not None
            self.assertEqual(state.sweepCount, 1)

    def test_ambiguous_expiry_mailbox_skips_only_that_row_and_heartbeat_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordination_root = root / "ar-coordination"
            _write_topology(coordination_root)
            observer_root = coordination_root / "logs" / "observer"
            catalog = TerminalCatalog(root / "catalog.json")
            for session_id in ("architect-a", "architect-b"):
                catalog.upsert(
                    replace(
                        _entry(session_id, task_document_ref=SPRINT_REF),
                        seat_role="architect",
                    )
                )
            inbox_store = OperatorInboxStore(observer_root)
            inbox_store.append(
                create_operator_inbox_entry(
                    InboxMessage(
                        ask="expired route",
                        response="do not guess an architect",
                        message_kind="escalation",
                        subject=InboxSubject(
                            task_document_ref=_leaf_ref(1),
                            seat_role="worker",
                            agent_id="worker-dead",
                        ),
                    ),
                    entry_id="ambiguous-expiry",
                    now=(NOW - timedelta(minutes=10)).isoformat(),
                    routing=InboxRouting(
                        address=InboxAddress(
                            agent_id="manager-dead",
                            recipient_role="manager",
                        )
                    ),
                    poster=InboxPoster(created_by="worker-dead", created_via="cli"),
                )
            )
            inbox_store.append(
                create_operator_inbox_entry(
                    InboxMessage(ask="old row", response="expire independently"),
                    entry_id="unrelated-expiry",
                    now=(NOW - timedelta(days=3)).isoformat(),
                    routing=InboxRouting(address=InboxAddress(agent_id="unrelated-dead")),
                    poster=InboxPoster(created_by="system", created_via="cli"),
                )
            )
            heartbeat = AgentNotifierHeartbeatStore(observer_root)
            context = AgentNotifierContext(
                catalog=catalog,
                host=cast(TerminalHost, _FakeHost()),
                paster=_fake_paster(),
                inbox_store=inbox_store,
                expectation_store=ExpectationRowStore(observer_root),
                signal_cooldown_store=AgentNotifierSignalCooldownStore(observer_root),
                event_store=EventStore(observer_root),
                heartbeat_store=heartbeat,
                coordination_root=coordination_root,
                stale_seat_seconds=60.0,
                redeliver_budget=2,
            )

            result = run_agent_notifier_sweep(context, now=NOW)

            expiry_actions = [
                action
                for action in result.actions
                if action.finding.source_id == "ambiguous-expiry"
                and action.finding.kind == "rebind-expired"
            ]
            self.assertEqual(len(expiry_actions), 1)
            self.assertEqual(expiry_actions[0].outcome, "skipped")
            self.assertIn("multiple running occupants", expiry_actions[0].detail or "")
            self.assertEqual(inbox_store.current()["ambiguous-expiry"].state, "pending")
            self.assertEqual(inbox_store.current()["unrelated-expiry"].state, "expired")
            state = heartbeat.read()
            assert state is not None
            self.assertEqual(state.sweepCount, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
