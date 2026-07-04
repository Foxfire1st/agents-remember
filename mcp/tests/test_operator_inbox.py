"""Tests for the external-chat operator inbox backend."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
