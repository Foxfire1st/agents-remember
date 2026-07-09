"""Tests for the external-chat operator inbox backend."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.operator_inbox_records import (
    OperatorInboxEntry,
    consume_operator_inbox_entry,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.mcp.tools import operator_inbox as inbox_tools
from agents_remember.serving.inbox_delivery import deliver_inbox_entry
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import PasteResult

T1 = "2026-06-23T10:00:00+00:00"
T2 = "2026-06-23T10:05:00+00:00"


class OperatorInboxRecordTests(unittest.TestCase):
    def test_create_and_consume_are_snapshots(self) -> None:
        entry = create_operator_inbox_entry(
            entry_id="01H",
            now=T1,
            lifecycle_id="L1",
            agent_id="agent-a",
            gate_id="gate-1",
            ask="Continue?",
            response="Yes, proceed.",
            created_by="developer",
            created_via="dashboard",
            sender_agent_id="manager-1",
            sender_role="manager",
            recipient_role="worker",
            message_kind="turn-report",
            artifact_path="notes/reports/L3-worker-report.md",
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
        self.assertEqual(consumed.state, "consumed")
        self.assertEqual(consumed.consumedAt, T2)
        self.assertEqual(consumed.createdBy, "developer")
        self.assertEqual(entry.state, "pending")

    def test_requires_mailbox_address(self) -> None:
        with self.assertRaises(ValueError):
            create_operator_inbox_entry(
                entry_id="01H",
                now=T1,
                lifecycle_id=None,
                agent_id=None,
                ask="Continue?",
                response="Yes.",
                created_by="developer",
                created_via="dashboard",
            )

    def test_wire_roundtrip_uses_schema_alias(self) -> None:
        entry = create_operator_inbox_entry(
            entry_id="01H",
            now=T1,
            lifecycle_id="L1",
            agent_id=None,
            ask="Continue?",
            response="Yes.",
            created_by="developer",
            created_via="dashboard",
        )
        line = entry.model_dump_json(by_alias=True, exclude_none=True)
        self.assertIn('"schema":"ar-operator-inbox-entry/v1"', line)
        self.assertEqual(OperatorInboxEntry.model_validate_json(line), entry)


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
            entry_id=entry_id,
            now=T1,
            lifecycle_id=lifecycle_id,
            agent_id=agent_id,
            ask=f"Ask {entry_id}",
            response=f"Response {entry_id}",
            created_by="developer",
            created_via="dashboard",
        )

    def test_pending_filter_matches_lifecycle_or_agent(self) -> None:
        self.store.append(self._entry("A", lifecycle_id="L1", agent_id="agent-a"))
        self.store.append(self._entry("B", lifecycle_id="L2", agent_id="agent-a"))
        self.store.append(self._entry("C", lifecycle_id="L1", agent_id="agent-b"))
        self.store.append(
            create_operator_inbox_entry(
                entry_id="D",
                now=T1,
                lifecycle_id=None,
                agent_id=None,
                recipient_role="manager",
                ask="Nudge",
                response="Check worker.",
                created_by="system",
                created_via="cli",
                message_kind="nudge",
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

    def test_consume_marks_entry_and_is_idempotent(self) -> None:
        self.store.append(self._entry("A"))
        consumed, consumed_now = self.store.consume(
            "A",
            now=T2,
            consumed_by="model",
            consumed_via="cli",
        )
        self.assertTrue(consumed_now)
        self.assertEqual(consumed.state, "consumed")
        self.assertEqual(self.store.list_pending(lifecycle_id="L1", agent_id="agent-a"), [])
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
        consumed, _ = self.store.consume(
            "A",
            now=T2,
            consumed_by="model",
            consumed_via="cli",
        )
        self.assertEqual(consumed.state, "consumed")
        self.assertTrue(self.store.delete("A"))
        self.assertEqual(self.store.read(), [])

    def test_consume_missing_entry_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.store.consume("nope", now=T2, consumed_by="model", consumed_via="cli")

    def test_record_delivery_appends_status_snapshot(self) -> None:
        self.store.append(self._entry("A"))
        delivered = self.store.record_delivery(
            "A",
            now=T2,
            delivery_state="delivered",
            delivered_to_session="agent-a",
            delivery_detail="echo-confirmed",
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
        delivered = self.store.record_delivery(
            "A", now=T2, delivery_state="delivered", delivered_to_session="agent-a"
        )
        self.assertEqual(delivered.attemptCount, 1)
        self.assertEqual(delivered.lastAttemptAt, T2)
        self.assertIsNotNone(delivered.nextAttemptAt)
        assert delivered.nextAttemptAt is not None
        self.assertGreaterEqual(
            (
                datetime.fromisoformat(delivered.nextAttemptAt)
                - datetime.fromisoformat(T2)
            ).total_seconds(),
            900.0,
        )
        # A second delivery attempt (e.g. a redelivery pass) bumps again and re-schedules further out.
        second = self.store.record_delivery(
            "A", now="2026-06-23T10:10:00+00:00", delivery_state="unconfirmed"
        )
        self.assertEqual(second.attemptCount, 2)
        assert second.nextAttemptAt is not None
        self.assertGreater(second.nextAttemptAt, delivered.nextAttemptAt)

    def test_record_delivery_clears_schedule_only_via_consume(self) -> None:
        self.store.append(self._entry("A"))
        self.store.record_delivery("A", now=T2, delivery_state="delivered")
        consumed, _ = self.store.consume("A", now=T2, consumed_by="model", consumed_via="cli")
        self.assertEqual(consumed.state, "consumed")

    def test_list_redeliverable_returns_pending_rows_past_backoff(self) -> None:
        self.store.append(self._entry("A"))
        self.store.record_delivery(
            "A", now="2026-06-23T09:00:00+00:00", delivery_state="no-hosted-session"
        )
        now = datetime.fromisoformat("2026-06-24T09:00:00+00:00")
        redeliverable = self.store.list_redeliverable(now=now)
        self.assertEqual([entry.id for entry in redeliverable], ["A"])

    def test_list_redeliverable_excludes_consumed_rows(self) -> None:
        self.store.append(self._entry("A"))
        self.store.record_delivery("A", now=T1, delivery_state="delivered")
        self.store.consume("A", now=T2, consumed_by="model", consumed_via="cli")
        now = datetime.fromisoformat("2026-06-24T09:00:00+00:00")
        self.assertEqual(self.store.list_redeliverable(now=now), [])

    def test_mark_escalated_stamps_the_reserved_field(self) -> None:
        self.store.append(self._entry("A"))
        escalated = self.store.mark_escalated("A", now=T2)
        self.assertEqual(escalated.escalatedAt, T2)

    def test_advance_rung_stamps_rung_and_reanchors_escalated_at(self) -> None:
        self.store.append(self._entry("A"))
        advanced = self.store.advance_rung("A", rung=1, now=T2)
        self.assertEqual(advanced.rung, 1)
        self.assertEqual(advanced.escalatedAt, T2)
        T3 = "2026-06-23T10:20:00+00:00"
        advanced_again = self.store.advance_rung("A", rung=2, now=T3)
        self.assertEqual(advanced_again.rung, 2)
        self.assertEqual(advanced_again.escalatedAt, T3)

    def test_advance_rung_unknown_entry_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.store.advance_rung("missing", rung=1, now=T2)

    def test_ladder_resolved_is_terminal_without_ack(self) -> None:
        self.store.append(self._entry("A"))
        resolved, resolved_now = self.store.mark_ladder_resolved(
            "A",
            now=T2,
            reason="terminal ladder rung reached for non-live target seat",
        )
        self.assertTrue(resolved_now)
        self.assertEqual(resolved.state, "ladder-resolved")
        self.assertEqual(resolved.ladderResolvedAt, T2)
        self.assertIsNone(resolved.nextAttemptAt)
        consumed, consumed_now = self.store.consume(
            "A", now="2026-06-23T10:10:00+00:00", consumed_by="model", consumed_via="cli"
        )
        self.assertFalse(consumed_now)
        self.assertEqual(consumed.state, "ladder-resolved")

    def test_compaction_never_removes_a_pending_unacked_row_regardless_of_age(self) -> None:
        # R1: an unacked row outlives any cleanup until acked or ladder-resolved. Exercised
        # against the exact post-time compaction path (operator_inbox_post_payload calls
        # store.compact() right after append) via a row far past the retention TTL.
        ancient = create_operator_inbox_entry(
            entry_id="OLD",
            now="2020-01-01T00:00:00+00:00",
            lifecycle_id="L1",
            agent_id="agent-a",
            ask="Ancient ask",
            response="Ancient response",
            created_by="developer",
            created_via="dashboard",
        )
        self.store.append(ancient)
        removed = self.store.compact(now=datetime.now(UTC))
        self.assertEqual(removed, 0)
        self.assertEqual([entry.id for entry in self.store.read()], ["OLD"])

    def test_compaction_prunes_ladder_resolved_rows(self) -> None:
        self.store.append(self._entry("A"))
        self.store.mark_ladder_resolved(
            "A",
            now=T2,
            reason="terminal ladder rung reached for non-live target seat",
        )
        removed = self.store.compact(now=datetime.now(UTC))
        self.assertEqual(removed, 2)
        self.assertEqual(self.store.read(), [])

    def test_compaction_still_prunes_a_stale_consumed_row(self) -> None:
        self.store.append(self._entry("A"))
        self.store.consume("A", now="2020-01-01T00:00:00+00:00", consumed_by="model", consumed_via="cli")
        removed = self.store.compact(now=datetime.now(UTC))
        self.assertGreater(removed, 0)
        self.assertEqual(self.store.read(), [])


class OperatorInboxToolTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = OperatorInboxStore(Path(tmp.name))
        patcher = mock.patch.object(inbox_tools, "_store", return_value=self.store)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_post_poll_consume_flow(self) -> None:
        posted = inbox_tools.operator_inbox_post_payload(
            None,  # type: ignore[arg-type]  # _store is patched; config is unused
            lifecycle_id="L1",
            agent_id="agent-a",
            gate_id="gate-1",
            ask="Continue?",
            response="Yes, proceed.",
            created_by="developer",
            created_via="dashboard",
            sender_agent_id="manager-1",
            sender_role="manager",
            recipient_role="worker",
            message_kind="message",
            deliver_to_hosted=False,
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
        self.assertEqual(consumed["state"], "consumed")
        self.assertEqual(self.store.read(), [])

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
            lifecycle_id="L1",
            agent_id=None,
            ask="Ratify the escalation-ladder change?",
            response="See notes/reports/decision-context.md",
            created_by="orchestrator",
            created_via="cli",
            sender_role="orchestrator",
            recipient_role="architect",
            message_kind="decision-item",
            deliver_to_hosted=False,
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
            lifecycle_id="L1",
            agent_id=None,
            ask="Ruling on the escalation-ladder change",
            response="Ratified as proposed.",
            created_by="architect",
            created_via="cli",
            sender_role="architect",
            recipient_role="orchestrator",
            message_kind="decision-ruling",
            deliver_to_hosted=False,
        )
        self.assertEqual(posted_ruling["messageKind"], "decision-ruling")
        self.assertEqual(posted_ruling["recipientRole"], "orchestrator")

    def test_plain_message_addressed_to_architect_and_curator_succeeds(self) -> None:
        for role in ("architect", "curator"):
            posted = inbox_tools.operator_inbox_post_payload(
                None,  # type: ignore[arg-type]
                lifecycle_id="L1",
                agent_id=None,
                ask="FYI",
                response="Nothing to action.",
                created_by="developer",
                created_via="dashboard",
                sender_role="developer",
                recipient_role=role,
                message_kind="message",
                deliver_to_hosted=False,
            )
            self.assertEqual(posted["recipientRole"], role)
            self.assertEqual(posted["messageKind"], "message")


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
            )
        )
        self.host = TerminalHost(tmux_probe=lambda _name: True)

    def test_deliver_inbox_entry_pushes_to_hosted_session(self) -> None:
        entry = create_operator_inbox_entry(
            entry_id="A",
            now=T1,
            lifecycle_id="L1",
            agent_id="agent-a",
            sender_role="manager",
            recipient_role="worker",
            ask="Please continue.",
            response="Review the report.",
            created_by="manager-1",
            created_via="cli",
        )
        self.store.append(entry)
        calls: list[tuple[str, str, bool]] = []

        class _Paster:
            def paste(self, tmux_name: str, text: str, *, submit: bool = False) -> PasteResult:
                calls.append((tmux_name, text, submit))
                return PasteResult(delivered=True, submitted=True)

        delivered = deliver_inbox_entry(
            store=self.store,
            catalog=self.catalog,
            host=self.host,
            paster=_Paster(),  # type: ignore[arg-type]
            entry=entry,
        )
        self.assertEqual(delivered.deliveryState, "delivered")
        self.assertEqual(delivered.deliveredToSession, "agent-a")
        self.assertEqual(calls[0][0], "ar-agent-a")
        self.assertTrue(calls[0][2])
        self.assertIn("[Agents Remember inbox:message]", calls[0][1])

    def test_deliver_inbox_entry_records_unconfirmed_when_paste_is_not_echoed(self) -> None:
        # FINDING 3 (260703-L18, pins friction F-A's echo-confirm seam): a paste the target session
        # did NOT echo back must record deliveryState 'unconfirmed', never 'delivered'. This is the
        # exact boot-discard failure echo-confirmation was built to catch (a booting harness silently
        # drops stdin). If someone collapses inbox_delivery's branch to always-'delivered' this test
        # FAILS -- the reachable session with an un-echoed paste is the only thing separating the two.
        entry = create_operator_inbox_entry(
            entry_id="A",
            now=T1,
            lifecycle_id="L1",
            agent_id="agent-a",
            sender_role="manager",
            recipient_role="worker",
            ask="Please continue.",
            response="Review the report.",
            created_by="manager-1",
            created_via="cli",
        )
        self.store.append(entry)
        attempts: list[tuple[str, str]] = []

        class _UnechoedPaster:
            def paste(self, tmux_name: str, text: str, *, submit: bool = False) -> PasteResult:
                # A booting harness accepts the keystrokes but never echoes them back. The paster
                # attaches its final pane capture (260707-HFX-L3 loud-failure contract).
                attempts.append((tmux_name, text))
                return PasteResult(delivered=False, submitted=submit, capture="claude> (booting)")

        recorded = deliver_inbox_entry(
            store=self.store,
            catalog=self.catalog,
            host=self.host,
            paster=_UnechoedPaster(),  # type: ignore[arg-type]
            entry=entry,
        )
        # The paste WAS attempted into the reachable session -- delivery just was not verified.
        self.assertEqual(attempts[0][0], "ar-agent-a")
        self.assertEqual(recorded.deliveryState, "unconfirmed")
        self.assertNotEqual(recorded.deliveryState, "delivered")
        self.assertEqual(recorded.deliveredToSession, "agent-a")
        # 260707-HFX-L3: the durable row carries the pane capture as forensic evidence, never a
        # bare "not echoed" -- the re-briefing operator reads what the pane actually showed.
        assert recorded.deliveryDetail is not None
        self.assertIn("paste was not capture-verified", recorded.deliveryDetail)
        self.assertIn("claude> (booting)", recorded.deliveryDetail)

    def test_unverified_delivery_with_empty_capture_still_records_a_loud_detail(self) -> None:
        entry = create_operator_inbox_entry(
            entry_id="B",
            now=T1,
            lifecycle_id="L1",
            agent_id="agent-a",
            sender_role="manager",
            recipient_role="worker",
            ask="Please continue.",
            response="Review the report.",
            created_by="manager-1",
            created_via="cli",
        )
        self.store.append(entry)

        class _GonePaster:
            def paste(self, _tmux_name: str, _text: str, *, submit: bool = False) -> PasteResult:
                # capture-pane against a vanished session yields an empty capture.
                return PasteResult(delivered=False, submitted=submit, capture="")

        recorded = deliver_inbox_entry(
            store=self.store,
            catalog=self.catalog,
            host=self.host,
            paster=_GonePaster(),  # type: ignore[arg-type]
            entry=entry,
        )
        self.assertEqual(recorded.deliveryState, "unconfirmed")
        self.assertEqual(
            recorded.deliveryDetail, "paste was not capture-verified (empty pane capture)"
        )
