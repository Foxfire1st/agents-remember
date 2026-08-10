"""Tests for the external-chat operator inbox backend."""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from pydantic import Field, ValidationError

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application import operator_inbox_tools as inbox_application
from agents_remember.controlplane import operator_inbox_transitions as inbox_transitions
from agents_remember.controlplane.interaction_retention import INBOX_MAX_CURRENT_ROWS
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    OperatorInboxCompatibleRecord,
    OperatorInboxEntry,
    consume_operator_inbox_entry,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.operator_inbox_transitions import (
    AdapterReceipt,
    DeliveryAttempt,
    RedeliveryFloor,
)
from agents_remember.controlplane.signal_routing import RoutedOwner
from agents_remember.mcp.tools import operator_inbox as inbox_tools
from agents_remember.serving import operator_inbox_posts
from agents_remember.serving.dispatch_brief import HostedDelivery
from agents_remember.serving.harness_control_models import SubmissionReceipt
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.inbox_delivery import (
    InboxDeliveryLog,
    deliver_inbox_entry,
)
from agents_remember.serving.terminal import TerminalHost, TerminalHostSeams
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import PasteResult

T1 = "2026-06-23T10:00:00+00:00"
T2 = "2026-06-23T10:05:00+00:00"


class OperatorInboxRecordTests(unittest.TestCase):
    def test_create_and_consume_are_snapshots(self) -> None:
        entry = create_operator_inbox_entry(
            InboxMessage(
                ask="Continue?",
                response="Yes, proceed.",
                message_kind="turn-report",
                gate_id="gate-1",
                artifact_path="notes/reports/L3-worker-report.md",
            ),
            entry_id="01H",
            now=T1,
            routing=InboxRouting(
                address=InboxAddress(lifecycle_id="L1", agent_id="agent-a", recipient_role="worker")
            ),
            poster=InboxPoster(
                created_by="developer",
                created_via="dashboard",
                sender_agent_id="manager-1",
                sender_role="manager",
            ),
        )
        self.assertEqual(entry.state, "pending")
        self.assertEqual(entry.senderAgentId, "manager-1")
        self.assertEqual(entry.senderRole, "manager")
        self.assertEqual(entry.recipientRole, "worker")
        self.assertEqual(entry.messageKind, "turn-report")
        self.assertEqual(entry.artifactPath, "notes/reports/L3-worker-report.md")
        consumed = consume_operator_inbox_entry(
            entry,
            now=T2,
            consumed_by="model",
            consumed_via="cli",
        )
        self.assertEqual(consumed.id, entry.id)
        # N16: consume is an attribution marker -- the row state stays pending.
        self.assertEqual(consumed.state, "pending")
        self.assertEqual(consumed.consumedAt, T2)
        self.assertEqual(consumed.createdBy, "developer")
        self.assertEqual(entry.state, "pending")

    def test_requires_mailbox_address(self) -> None:
        with self.assertRaises(ValueError):
            create_operator_inbox_entry(
                InboxMessage(ask="Continue?", response="Yes."),
                entry_id="01H",
                now=T1,
                routing=InboxRouting(address=InboxAddress(lifecycle_id=None, agent_id=None)),
                poster=InboxPoster(created_by="developer", created_via="dashboard"),
            )

    def test_wire_roundtrip_uses_schema_alias(self) -> None:
        entry = create_operator_inbox_entry(
            InboxMessage(ask="Continue?", response="Yes."),
            entry_id="01H",
            now=T1,
            routing=InboxRouting(address=InboxAddress(lifecycle_id="L1", agent_id=None)),
            poster=InboxPoster(created_by="developer", created_via="dashboard"),
        )
        line = entry.model_dump_json(by_alias=True, exclude_none=True)
        self.assertIn('"schema":"ar-operator-inbox-entry/v1"', line)
        self.assertEqual(OperatorInboxEntry.model_validate_json(line), entry)

    def test_legacy_reader_preserves_named_adapter_evidence_only(self) -> None:
        class LegacyOperatorInboxEntry(OperatorInboxCompatibleRecord):
            schema_version: str = Field(alias="schema")
            id: str
            ts: str
            state: str
            ask: str
            response: str
            createdAt: str
            createdBy: str
            createdVia: str

        payload: dict[str, object] = {
            "schema": "ar-operator-inbox-entry/v1",
            "id": "01H",
            "ts": T1,
            "state": "pending",
            "ask": "Continue?",
            "response": "Yes.",
            "createdAt": T1,
            "createdBy": "developer",
            "createdVia": "dashboard",
            "adapterDeliveryState": "queued",
            "adapterDeliveryDetail": "accepted by exact adapter request",
        }
        legacy = LegacyOperatorInboxEntry.model_validate(payload)
        self.assertEqual(
            legacy.model_extra,
            {
                "adapterDeliveryState": "queued",
                "adapterDeliveryDetail": "accepted by exact adapter request",
            },
        )
        roundtrip = legacy.model_dump(by_alias=True, exclude_none=True)
        self.assertEqual(roundtrip["adapterDeliveryState"], "queued")
        self.assertEqual(roundtrip["adapterDeliveryDetail"], "accepted by exact adapter request")

        with self.assertRaisesRegex(ValidationError, "unsupported fields: futureEvidence"):
            LegacyOperatorInboxEntry.model_validate({**payload, "futureEvidence": True})


class OperatorInboxStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = OperatorInboxStore(Path(tmp.name))

    def _entry(
        self,
        entry_id: str,
        *,
        lifecycle_id: str | None = "L1",
        agent_id: str | None = "agent-a",
    ) -> OperatorInboxEntry:
        return create_operator_inbox_entry(
            InboxMessage(ask=f"Ask {entry_id}", response=f"Response {entry_id}"),
            entry_id=entry_id,
            now=T1,
            routing=InboxRouting(
                address=InboxAddress(lifecycle_id=lifecycle_id, agent_id=agent_id)
            ),
            poster=InboxPoster(created_by="developer", created_via="dashboard"),
        )

    def test_pending_filter_matches_lifecycle_or_agent(self) -> None:
        self.store.append(self._entry("A", lifecycle_id="L1", agent_id="agent-a"))
        self.store.append(self._entry("B", lifecycle_id="L2", agent_id="agent-a"))
        self.store.append(self._entry("C", lifecycle_id="L1", agent_id="agent-b"))
        self.store.append(
            create_operator_inbox_entry(
                InboxMessage(ask="Nudge", response="Check worker.", message_kind="nudge"),
                entry_id="D",
                now=T1,
                routing=InboxRouting(
                    address=InboxAddress(lifecycle_id=None, agent_id=None, recipient_role="manager")
                ),
                poster=InboxPoster(created_by="system", created_via="cli"),
            )
        )

        self.assertEqual(
            [entry.id for entry in self.store.list_pending(lifecycle_id="L1", agent_id=None)],
            ["A", "C"],
        )
        self.assertEqual(
            [entry.id for entry in self.store.list_pending(lifecycle_id=None, agent_id="agent-a")],
            ["A", "B"],
        )
        self.assertEqual(
            [entry.id for entry in self.store.list_pending(lifecycle_id="L1", agent_id="agent-a")],
            ["A"],
        )
        self.assertEqual(
            [
                entry.id
                for entry in self.store.list_pending(
                    lifecycle_id=None,
                    agent_id=None,
                    recipient_role="manager",
                )
            ],
            ["D"],
        )

    def test_consume_is_attribution_only_and_idempotent(self) -> None:
        self.store.append(self._entry("A"))
        consumed, consumed_now = self.store.consume(
            "A",
            now=T2,
            consumed_by="model",
            consumed_via="cli",
        )
        self.assertTrue(consumed_now)
        self.assertEqual(consumed.state, "pending")
        self.assertEqual(consumed.consumedAt, T2)
        # Nothing mechanical hangs off the marker: the row stays pending and retryable.
        self.assertEqual(
            [entry.id for entry in self.store.list_pending(lifecycle_id="L1", agent_id="agent-a")],
            ["A"],
        )
        self.assertEqual(len(self.store.read()), 2)

        consumed_again, consumed_now_again = self.store.consume(
            "A",
            now="2026-06-23T10:10:00+00:00",
            consumed_by="model",
            consumed_via="cli",
        )
        self.assertFalse(consumed_now_again)
        self.assertEqual(consumed_again.consumedAt, T2)
        self.assertEqual(len(self.store.read()), 2)

    def test_delete_removes_entry_snapshots(self) -> None:
        self.store.append(self._entry("A"))
        self.store.consume("A", now=T2, consumed_by="model", consumed_via="cli")
        self.assertTrue(self.store.delete("A"))
        self.assertEqual(self.store.read(), [])

    def test_consume_missing_entry_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.store.consume("nope", now=T2, consumed_by="model", consumed_via="cli")

    def test_record_delivery_appends_status_snapshot(self) -> None:
        self.store.append(self._entry("A"))
        delivered = inbox_transitions.record_delivery(
            self.store,
            "A",
            DeliveryAttempt(
                delivery_state="delivered",
                delivered_to_session="agent-a",
                detail="harness-log-confirmed",
            ),
            now=T2,
        )
        self.assertEqual(delivered.deliveryState, "delivered")
        self.assertEqual(delivered.deliveredAt, T2)
        self.assertEqual(delivered.deliveredToSession, "agent-a")
        self.assertEqual(len(self.store.read()), 2)

    def test_poll_requires_mailbox_address(self) -> None:
        with self.assertRaises(ValueError):
            self.store.list_pending(lifecycle_id=None, agent_id=None)

    def test_record_delivery_bumps_attempt_and_schedules_next_attempt(self) -> None:
        # R1/R3: every attempt -- including a confirmed 'delivered' paste -- bumps attemptCount
        # and schedules a durable nextAttemptAt, because consume=ack is the only terminal outcome.
        self.store.append(self._entry("A"))
        delivered = inbox_transitions.record_delivery(
            self.store,
            "A",
            DeliveryAttempt(delivery_state="delivered", delivered_to_session="agent-a"),
            now=T2,
        )
        self.assertEqual(delivered.attemptCount, 1)
        self.assertEqual(delivered.lastAttemptAt, T2)
        self.assertIsNotNone(delivered.nextAttemptAt)
        assert delivered.nextAttemptAt is not None
        self.assertGreaterEqual(
            (
                datetime.fromisoformat(delivered.nextAttemptAt) - datetime.fromisoformat(T2)
            ).total_seconds(),
            900.0,
        )
        # A second delivery attempt (e.g. a redelivery pass) bumps again and re-schedules further out.
        second = inbox_transitions.record_delivery(
            self.store,
            "A",
            DeliveryAttempt(delivery_state="unconfirmed"),
            now="2026-06-23T10:10:00+00:00",
        )
        self.assertEqual(second.attemptCount, 2)
        assert second.nextAttemptAt is not None
        self.assertGreater(second.nextAttemptAt, delivered.nextAttemptAt)

    def test_record_delivery_clears_schedule_only_via_landing(self) -> None:
        self.store.append(self._entry("A"))
        inbox_transitions.record_delivery(
            self.store,
            "A",
            DeliveryAttempt(
                delivery_state="delivered",
                adapter=AdapterReceipt(delivery_state="accepted"),
                landed=True,
            ),
            now=T2,
        )
        landed = self.store.current()["A"]
        self.assertEqual(landed.state, "landed")
        self.assertIsNone(landed.nextAttemptAt)

    def test_list_redeliverable_returns_pending_rows_past_backoff(self) -> None:
        self.store.append(self._entry("A"))
        inbox_transitions.record_delivery(
            self.store,
            "A",
            DeliveryAttempt(delivery_state="no-hosted-session"),
            now="2026-06-23T09:00:00+00:00",
        )
        now = datetime.fromisoformat("2026-06-24T09:00:00+00:00")
        redeliverable = self.store.list_redeliverable(now=now)
        self.assertEqual([entry.id for entry in redeliverable], ["A"])

    def test_list_redeliverable_keeps_attribution_marked_rows(self) -> None:
        self.store.append(self._entry("A"))
        inbox_transitions.record_delivery(
            self.store, "A", DeliveryAttempt(delivery_state="delivered"), now=T1
        )
        self.store.consume("A", now=T2, consumed_by="model", consumed_via="cli")
        now = datetime.fromisoformat("2026-06-24T09:00:00+00:00")
        # N16: consume no longer stops redelivery -- only landing/terminal resolution does.
        self.assertEqual([entry.id for entry in self.store.list_redeliverable(now=now)], ["A"])

    def test_legacy_ladder_resolved_row_is_terminal_without_ack(self) -> None:
        """The pre-formal-vocabulary terminal state stays parse-compatible and terminal."""
        self.store.append(self._entry("A"))
        self.store.append(
            self._entry("A").model_copy(
                update={
                    "state": "ladder-resolved",
                    "terminalAt": T2,
                    "ladderResolvedAt": T2,
                    "ladderResolvedReason": "legacy ladder-resolved row",
                    "nextAttemptAt": None,
                }
            )
        )
        current = self.store.current()["A"]
        self.assertEqual(current.state, "ladder-resolved")
        self.assertEqual(current.ladderResolvedAt, T2)
        self.assertIsNone(current.nextAttemptAt)
        consumed, consumed_now = self.store.consume(
            "A", now="2026-06-23T10:10:00+00:00", consumed_by="model", consumed_via="cli"
        )
        self.assertFalse(consumed_now)
        self.assertEqual(consumed.state, "ladder-resolved")

    def test_legacy_ladder_resolved_row_stays_terminal_across_folds(self) -> None:
        self.store.append(
            self._entry("A").model_copy(
                update={
                    "state": "ladder-resolved",
                    "terminalAt": T2,
                    "ladderResolvedAt": T2,
                    "nextAttemptAt": None,
                }
            )
        )
        # A physically later stale pending snapshot cannot resurrect the terminal state.
        self.store.append(
            self._entry("A").model_copy(
                update={"ts": "2026-06-23T10:30:00+00:00", "state": "pending"}
            )
        )
        folded = self.store.current()["A"]
        self.assertEqual(folded.state, "ladder-resolved")
        self.assertEqual(folded.ladderResolvedAt, T2)

    def test_compaction_keeps_an_ancient_pending_row_for_the_sweep_to_expire(self) -> None:
        # §9/N13: the pending TTL is a resolution boundary owned by the sweep -- an older
        # pending row is stamped ``expired`` (visible, counted) before retention drops it, so
        # compaction alone never silently purges a pending row. The hard cap still bounds the
        # store even when the sweep is down.
        ancient = create_operator_inbox_entry(
            InboxMessage(ask="Ancient ask", response="Ancient response"),
            entry_id="OLD",
            now="2020-01-01T00:00:00+00:00",
            routing=InboxRouting(address=InboxAddress(lifecycle_id="L1", agent_id="agent-a")),
            poster=InboxPoster(created_by="developer", created_via="dashboard"),
        )
        self.store.append(ancient)
        removed = self.store.compact(now=datetime.now(UTC))
        self.assertEqual(removed, 0)
        self.assertEqual([entry.id for entry in self.store.read()], ["OLD"])

    def test_compaction_keeps_a_fresh_pending_row(self) -> None:
        fresh = create_operator_inbox_entry(
            InboxMessage(ask="Fresh ask", response="Fresh response"),
            entry_id="FRESH",
            now=datetime.now(UTC).isoformat(),
            routing=InboxRouting(address=InboxAddress(lifecycle_id="L1", agent_id="agent-a")),
            poster=InboxPoster(created_by="developer", created_via="dashboard"),
        )
        self.store.append(fresh)
        removed = self.store.compact(now=datetime.now(UTC))
        self.assertEqual(removed, 0)
        self.assertEqual([entry.id for entry in self.store.read()], ["FRESH"])

    def test_compaction_enforces_the_hard_health_cap_keeping_newest(self) -> None:
        # The health-cap backstop: even fresh pending rows are evicted oldest-first past
        # INBOX_MAX_CURRENT_ROWS -- a store that size means a producer is misbehaving, and the
        # host matters more than any row.
        now = datetime.now(UTC)
        total = INBOX_MAX_CURRENT_ROWS + 25
        for index in range(total):
            self.store.append(
                create_operator_inbox_entry(
                    InboxMessage(ask=f"ask {index}", response="resp"),
                    entry_id=f"row-{index:04d}",
                    now=(now - timedelta(seconds=total - index)).isoformat(),
                    routing=InboxRouting(
                        address=InboxAddress(lifecycle_id="L1", agent_id="agent-a")
                    ),
                    poster=InboxPoster(created_by="developer", created_via="dashboard"),
                )
            )
        removed = self.store.compact(now=now)
        self.assertEqual(removed, 25)
        kept_ids = {entry.id for entry in self.store.read()}
        self.assertEqual(len(kept_ids), INBOX_MAX_CURRENT_ROWS)
        self.assertNotIn("row-0000", kept_ids)
        self.assertIn(f"row-{total - 1:04d}", kept_ids)

    def test_compaction_prunes_ladder_resolved_rows(self) -> None:
        self.store.append(
            self._entry("A").model_copy(
                update={
                    "state": "ladder-resolved",
                    "terminalAt": "2020-01-01T00:00:00+00:00",
                    "nextAttemptAt": None,
                }
            )
        )
        removed = self.store.compact(now=datetime.now(UTC))
        self.assertEqual(removed, 1)
        self.assertEqual(self.store.read(), [])

    def test_compaction_still_prunes_a_stale_consumed_row(self) -> None:
        # Legacy pre-N16 rows may still carry the ``consumed`` state; they keep the same
        # marker-retention window and age out.
        self.store.append(
            self._entry("A").model_copy(
                update={
                    "state": "consumed",
                    "consumedAt": "2020-01-01T00:00:00+00:00",
                }
            )
        )
        removed = self.store.compact(now=datetime.now(UTC))
        self.assertGreater(removed, 0)
        self.assertEqual(self.store.read(), [])


class OperatorInboxToolTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = OperatorInboxStore(Path(tmp.name))
        patcher = mock.patch.object(inbox_application, "_store", return_value=self.store)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_post_poll_consume_flow(self) -> None:
        posted = inbox_tools.operator_inbox_post_payload(
            None,  # type: ignore[arg-type]  # the store is patched; config is unused here
            address=InboxAddress(lifecycle_id="L1", agent_id="agent-a", recipient_role="worker"),
            message=InboxMessage(
                ask="Continue?", response="Yes, proceed.", message_kind="message", gate_id="gate-1"
            ),
            poster=InboxPoster(
                created_by="developer",
                created_via="dashboard",
                sender_agent_id="manager-1",
                sender_role="manager",
            ),
            delivery=HostedDelivery(enabled=False),
        )
        self.assertEqual(posted["state"], "pending")
        self.assertEqual(posted["senderAgentId"], "manager-1")
        self.assertEqual(posted["recipientRole"], "worker")
        self.assertEqual(posted["deliveryState"], "queued")

        polled = inbox_tools.operator_inbox_poll_payload(
            None,  # type: ignore[arg-type]
            lifecycle_id=None,
            agent_id="agent-a",
            recipient_role=None,
        )
        self.assertEqual(polled["entryCount"], 1)
        self.assertEqual(polled["entries"][0]["ask"], "Continue?")

        consumed = inbox_tools.operator_inbox_consume_payload(
            None,  # type: ignore[arg-type]
            entry_id=posted["entryId"],
            consumed_by="model",
            consumed_via="cli",
        )
        self.assertTrue(consumed["consumedNow"])
        self.assertEqual(consumed["state"], "pending")
        self.assertIsNotNone(consumed["consumedAt"])
        self.assertEqual([entry.state for entry in self.store.read()], ["pending", "pending"])

    def test_poll_without_address_raises(self) -> None:
        with self.assertRaises(ValueError):
            inbox_tools.operator_inbox_poll_payload(
                None,  # type: ignore[arg-type]
                lifecycle_id=None,
                agent_id=None,
                recipient_role=None,
            )

    def test_decision_item_relay_round_trip_between_orchestrator_and_architect(self) -> None:
        # HFX-L6 doctrine (architect.md/orchestrator.md/SKILL.md) mandates this exact call shape.
        # Master-exit Finding 1: the schema rejected it with a ValidationError before this leaf.
        posted_item = inbox_tools.operator_inbox_post_payload(
            None,  # type: ignore[arg-type]
            address=InboxAddress(lifecycle_id="L1", agent_id=None, recipient_role="architect"),
            message=InboxMessage(
                ask="Ratify the escalation-ladder change?",
                response="See notes/reports/decision-context.md",
                message_kind="decision-item",
            ),
            poster=InboxPoster(
                created_by="orchestrator", created_via="cli", sender_role="orchestrator"
            ),
            delivery=HostedDelivery(enabled=False),
        )
        self.assertEqual(posted_item["messageKind"], "decision-item")
        self.assertEqual(posted_item["recipientRole"], "architect")

        polled = inbox_tools.operator_inbox_poll_payload(
            None,  # type: ignore[arg-type]
            lifecycle_id="L1",
            agent_id=None,
            recipient_role="architect",
        )
        self.assertEqual(polled["entryCount"], 1)
        self.assertEqual(polled["entries"][0]["messageKind"], "decision-item")

        posted_ruling = inbox_tools.operator_inbox_post_payload(
            None,  # type: ignore[arg-type]
            address=InboxAddress(lifecycle_id="L1", agent_id=None, recipient_role="orchestrator"),
            message=InboxMessage(
                ask="Ruling on the escalation-ladder change",
                response="Ratified as proposed.",
                message_kind="decision-ruling",
            ),
            poster=InboxPoster(created_by="architect", created_via="cli", sender_role="architect"),
            delivery=HostedDelivery(enabled=False),
        )
        self.assertEqual(posted_ruling["messageKind"], "decision-ruling")
        self.assertEqual(posted_ruling["recipientRole"], "orchestrator")

    def test_decision_item_pins_the_resolved_architect_or_refuses_without_one(self) -> None:
        catalog = TerminalCatalog(self.store.root / "terminal-sessions.json")
        exact = operator_inbox_posts._post_address(
            catalog,
            RoutedOwner(role="architect", agent_id="architect-a", lifecycle_id="L-architect"),
            message_kind="decision-item",
            address=InboxAddress(recipient_role="architect"),
        )
        self.assertEqual(
            exact,
            InboxAddress(
                lifecycle_id="L-architect", agent_id="architect-a", recipient_role="architect"
            ),
        )
        catalog.upsert(
            TerminalCatalogEntry(
                id="manager-a",
                label="Manager",
                kind="harness",
                harness="claude",
                lifecycle_id="L-manager",
                cwd=self.store.root,
                tmux_name="ar-manager-a",
                command=("claude",),
                created_at=T1,
                last_attached_at=T1,
                status="running",
                leaf_key="repo-a/sprint-a/leaf-1",
                spawn_role="manager",
                seat_role="manager",
                spawn_repo="repo-a",
                spawn_sprint="sprint-a",
            )
        )
        refused = operator_inbox_posts.post_operator_inbox_entry(
            operator_inbox_posts.OperatorInboxPostContext(
                config=None,
                store=self.store,
                delivery=HostedDelivery(enabled=False, catalog=catalog),
            ),
            address=InboxAddress(recipient_role="architect"),
            message=InboxMessage(ask="Decide", response="Context", message_kind="decision-item"),
            poster=InboxPoster(
                created_by="manager",
                created_via="cli",
                sender_agent_id="manager-a",
                sender_role="manager",
            ),
        )
        self.assertEqual(refused["status"], "sprint-owner-required")
        self.assertEqual(self.store.read(), [])

    def test_plain_message_addressed_to_architect_and_curator_succeeds(self) -> None:
        for role in ("architect", "curator"):
            posted = inbox_tools.operator_inbox_post_payload(
                None,  # type: ignore[arg-type]
                address=InboxAddress(lifecycle_id="L1", agent_id=None, recipient_role=role),
                message=InboxMessage(
                    ask="FYI", response="Nothing to action.", message_kind="message"
                ),
                poster=InboxPoster(
                    created_by="developer", created_via="dashboard", sender_role="developer"
                ),
                delivery=HostedDelivery(enabled=False),
            )
            self.assertEqual(posted["recipientRole"], role)
            self.assertEqual(posted["messageKind"], "message")

    def test_reviewer_completion_targets_and_wakes_current_manager(self) -> None:
        """Live halt regression: a completed reviewer with stale manager provenance must address
        and paste the existing turn-report signal into the current manager session."""
        catalog = TerminalCatalog(self.store.root / "terminal-sessions.json")
        leaf_key = "repo-a/260707_master/leaf-9"
        catalog.upsert(
            TerminalCatalogEntry(
                id="manager-old",
                label="Old manager",
                kind="harness",
                harness="codex",
                lifecycle_id="L-old",
                cwd=self.store.root,
                tmux_name="ar-manager-old",
                command=("codex",),
                created_at=T1,
                last_attached_at=T1,
                status="terminated",
                leaf_key="repo-a/260707_master/old-manager-anchor",
                spawn_role="manager",
                control_state="ready",
                control_endpoint=self.store.root / "manager.sock",
                control_protocol="ar-harness-control/v1",
            )
        )
        catalog.upsert(
            TerminalCatalogEntry(
                id="manager-current",
                label="Current manager",
                kind="harness",
                harness="codex",
                lifecycle_id="L-current",
                cwd=self.store.root,
                tmux_name="ar-manager-current",
                command=("codex",),
                created_at=T1,
                last_attached_at=T2,
                status="running",
                leaf_key="repo-a/260707_master/current-manager-anchor",
                spawn_role="manager",
                control_state="ready",
                control_endpoint=self.store.root / "manager-current.sock",
                control_protocol="ar-harness-control/v1",
            )
        )
        catalog.upsert(
            TerminalCatalogEntry(
                id="reviewer-1",
                label="Reviewer",
                kind="harness",
                harness="codex",
                lifecycle_id="L-reviewer",
                cwd=self.store.root,
                tmux_name="ar-reviewer-1",
                command=("codex",),
                created_at=T1,
                last_attached_at=T2,
                status="running",
                leaf_key=leaf_key,
                spawned_by_session="manager-old",
                spawn_role="reviewer",
            )
        )
        pasted_to: list[str] = []

        class _Paster:
            def paste(
                self,
                tmux_name: str,
                _text: str,
                *,
                submit: bool = False,
                **_kwargs: object,
            ) -> PasteResult:
                pasted_to.append(tmux_name)
                return PasteResult(delivered=True, submitted=submit)

        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            autospec=True,
        ) as submit_prompt:
            submit_prompt.side_effect = lambda _target, _text, submission: SubmissionReceipt(
                request_id=submission.request_id,
                acceptance="immediate",
                submitted_at=T1,
                accepted_at=T1,
            )
            posted = inbox_tools.operator_inbox_post_payload(
                None,  # type: ignore[arg-type]
                address=InboxAddress(
                    lifecycle_id="L-old", agent_id="manager-old", recipient_role="manager"
                ),
                message=InboxMessage(
                    ask="Reviewer report complete",
                    response="See notes/reports/reviewer-report.md",
                    message_kind="turn-report",
                    artifact_path="notes/reports/reviewer-report.md",
                ),
                poster=InboxPoster(
                    created_by="reviewer-1",
                    created_via="cli",
                    sender_agent_id="reviewer-1",
                    sender_role="reviewer",
                ),
                delivery=HostedDelivery(
                    catalog=catalog,
                    host=TerminalHost(TerminalHostSeams(tmux_probe=lambda _name: True)),
                    paster=_Paster(),  # type: ignore[arg-type]
                ),
            )

        self.assertEqual(posted["recipientRole"], "manager")
        self.assertEqual(posted["agentId"], "manager-current")
        self.assertEqual(posted["ownerAgentId"], "manager-current")
        self.assertEqual(posted["deliveryState"], "delivered")
        self.assertEqual(posted["deliveredToSession"], "manager-current")
        self.assertEqual(pasted_to, [])
        submit_prompt.assert_called_once()


class OperatorInboxDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.store = OperatorInboxStore(root)
        self.catalog = TerminalCatalog(root / "terminal-sessions.json")
        self.catalog.upsert(
            TerminalCatalogEntry(
                id="agent-a",
                label="Worker",
                kind="harness",
                harness="claude",
                lifecycle_id="L1",
                cwd=root,
                tmux_name="ar-agent-a",
                command=("claude",),
                created_at=T1,
                last_attached_at=T1,
                status="running",
                control_state="ready",
                control_endpoint=root / "agent-a.sock",
                control_protocol="ar-harness-control/v1",
            )
        )
        self.host = TerminalHost(TerminalHostSeams(tmux_probe=lambda _name: True))

    def test_deliver_inbox_entry_pushes_to_hosted_session(self) -> None:
        entry = create_operator_inbox_entry(
            InboxMessage(ask="Please continue.", response="Review the report."),
            entry_id="A",
            now=T1,
            routing=InboxRouting(
                address=InboxAddress(lifecycle_id="L1", agent_id="agent-a", recipient_role="worker")
            ),
            poster=InboxPoster(created_by="manager-1", created_via="cli", sender_role="manager"),
        )
        self.store.append(entry)
        calls: list[tuple[str, str, bool]] = []

        class _Paster:
            def paste(
                self,
                tmux_name: str,
                text: str,
                *,
                submit: bool = False,
                **_kwargs: object,
            ) -> PasteResult:
                calls.append((tmux_name, text, submit))
                return PasteResult(delivered=True, submitted=True)

        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            side_effect=lambda _target, _text, submission: SubmissionReceipt(
                request_id=submission.request_id,
                acceptance="queued",
                submitted_at=T1,
                accepted_at=T1,
            ),
        ) as submit_prompt:
            delivered = deliver_inbox_entry(
                InboxDeliveryLog(store=self.store, entry=entry),
                sessions=HostedSessionRuntime(catalog=self.catalog, host=self.host),
                paster=_Paster(),  # type: ignore[arg-type]
            )
        self.assertEqual(delivered.deliveryState, "delivered")
        self.assertEqual(delivered.deliveredToSession, "agent-a")
        self.assertEqual(calls, [])
        self.assertEqual(delivered.adapterDeliveryState, "queued")
        self.assertEqual(delivered.state, "pending")
        self.assertIn("[Agents Remember inbox:message]", submit_prompt.call_args.args[1])

    def test_consume_during_in_flight_delivery_cannot_steal_the_landing(self) -> None:
        # N16: consume is attribution-only, so an in-flight acceptance at a turn boundary still
        # lands the row -- the marker can never resurrect a pending state over a landing.
        entry = create_operator_inbox_entry(
            InboxMessage(ask="Please continue.", response="Review the report."),
            entry_id="A",
            now=T1,
            routing=InboxRouting(
                address=InboxAddress(lifecycle_id="L1", agent_id="agent-a", recipient_role="worker")
            ),
            poster=InboxPoster(created_by="manager-1", created_via="cli", sender_role="manager"),
        )
        self.store.append(entry)
        stale_current = self.store.current()
        delivery_started = threading.Event()
        finish_delivery = threading.Event()

        def in_flight(_target: object, _text: str, submission: object) -> SubmissionReceipt:
            delivery_started.set()
            if not finish_delivery.wait(timeout=2):
                raise AssertionError("test did not release the in-flight delivery")
            return SubmissionReceipt(
                request_id=str(submission.request_id),  # type: ignore[attr-defined]
                acceptance="immediate",
                submitted_at=T1,
                accepted_at=T1,
            )

        with (
            mock.patch(
                "agents_remember.serving.inbox_delivery.submit_control_prompt",
                side_effect=in_flight,
            ),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            delivery = executor.submit(
                deliver_inbox_entry,
                InboxDeliveryLog(
                    store=self.store,
                    entry=entry,
                    at=T2,
                    floor=RedeliveryFloor(current=stale_current),
                ),
                sessions=HostedSessionRuntime(catalog=self.catalog, host=self.host),
                paster=mock.Mock(),  # type: ignore[arg-type]
            )
            self.assertTrue(delivery_started.wait(timeout=2))
            with mock.patch.object(inbox_application, "_store", return_value=self.store):
                consumed = inbox_tools.operator_inbox_consume_payload(
                    None,  # type: ignore[arg-type]
                    entry_id=entry.id,
                    consumed_by="model",
                    consumed_via="cli",
                )
            self.assertTrue(consumed["consumedNow"])
            finish_delivery.set()
            delivered = delivery.result(timeout=2)

        self.assertEqual(delivered.deliveryState, "delivered")
        self.assertEqual(delivered.state, "landed")
        self.assertEqual(
            [record.state for record in self.store.read()],
            ["pending", "pending", "landed"],
        )
        self.assertEqual(self.store.current()[entry.id].state, "landed")
        self.assertEqual(
            self.store.list_pending(lifecycle_id="L1", agent_id="agent-a"),
            [],
        )
        self.assertEqual(
            self.store.list_redeliverable(now=datetime.fromisoformat("2026-06-24T10:05:00+00:00")),
            [],
        )

    def test_deliver_inbox_entry_records_unknown_adapter_acceptance(self) -> None:
        entry = create_operator_inbox_entry(
            InboxMessage(ask="Please continue.", response="Review the report."),
            entry_id="A",
            now=T1,
            routing=InboxRouting(
                address=InboxAddress(lifecycle_id="L1", agent_id="agent-a", recipient_role="worker")
            ),
            poster=InboxPoster(created_by="manager-1", created_via="cli", sender_role="manager"),
        )
        self.store.append(entry)
        attempts: list[tuple[str, str]] = []

        class _UnconfirmedPaster:
            def paste(
                self,
                tmux_name: str,
                text: str,
                *,
                _submit: bool = False,
                **_kwargs: object,
            ) -> PasteResult:
                # A booting harness accepts the keystrokes but never records them. The paster
                # attaches its final failure capture.
                attempts.append((tmux_name, text))
                return PasteResult(delivered=False, submitted=False, capture="claude> (booting)")

        with mock.patch(
            "agents_remember.serving.inbox_delivery.submit_control_prompt",
            side_effect=lambda _target, _text, submission: SubmissionReceipt(
                request_id=submission.request_id,
                acceptance="unknown",
                submitted_at=T1,
                detail="transport closed after write",
            ),
        ):
            recorded = deliver_inbox_entry(
                InboxDeliveryLog(store=self.store, entry=entry),
                sessions=HostedSessionRuntime(catalog=self.catalog, host=self.host),
                paster=_UnconfirmedPaster(),  # type: ignore[arg-type]
            )
        self.assertEqual(attempts, [])
        self.assertEqual(recorded.deliveryState, "unconfirmed")
        self.assertNotEqual(recorded.deliveryState, "delivered")
        self.assertEqual(recorded.deliveredToSession, "agent-a")
        self.assertEqual(recorded.adapterDeliveryState, "unknown")
        self.assertIn("transport closed after write", recorded.deliveryDetail or "")

    def test_unverified_delivery_with_empty_capture_still_records_a_loud_detail(self) -> None:
        entry = create_operator_inbox_entry(
            InboxMessage(ask="Please continue.", response="Review the report."),
            entry_id="B",
            now=T1,
            routing=InboxRouting(
                address=InboxAddress(lifecycle_id="L1", agent_id="agent-a", recipient_role="worker")
            ),
            poster=InboxPoster(created_by="manager-1", created_via="cli", sender_role="manager"),
        )
        self.store.append(entry)

        class _GonePaster:
            def paste(
                self,
                _tmux_name: str,
                _text: str,
                *,
                _submit: bool = False,
                **_kwargs: object,
            ) -> PasteResult:
                # capture-pane against a vanished session yields an empty capture.
                return PasteResult(delivered=False, submitted=False, capture="")

        legacy = self.catalog.get("agent-a")
        assert legacy is not None
        self.catalog.upsert(replace(legacy, control_endpoint=None, control_state="unsupported"))
        recorded = deliver_inbox_entry(
            InboxDeliveryLog(store=self.store, entry=entry),
            sessions=HostedSessionRuntime(catalog=self.catalog, host=self.host),
            paster=_GonePaster(),  # type: ignore[arg-type]
        )
        self.assertEqual(recorded.deliveryState, "unconfirmed")
        self.assertEqual(recorded.adapterDeliveryState, "unsupported")
        self.assertIn("no protocol delivery adapter", recorded.deliveryDetail or "")
