"""Queue projection, withdrawal, and recovery contract tests (260718-CHATS-L3, R2/R3/R7).

Real composition up to the harness edge (bridge + IPC + real authority + the
L2E control-plane reads); the only double is the structural fake adapter.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from _control_plane import OPERATOR, FakeControlAdapter, drive_activity, make_harness
from agents_remember.errors import HarnessControlClientError
from agents_remember.serving.conversation.control import attachments, queue_projection, withdrawals
from agents_remember.serving.conversation.control.previews import (
    MAX_PREVIEW_CLUSTERS,
    _clusters,
    payload_digest,
    redacted_preview,
)
from agents_remember.serving.conversation.control.refs import (
    ControlRefError,
    OperationIdentity,
    mint_ref,
)
from agents_remember.serving.conversation.control.service import (
    ConversationControlService,
    OperationConflictError,
    OperationNotFoundError,
)
from agents_remember.serving.conversation.models import (
    ConversationSubmitRequest,
    TextSubmitBlock,
    WithdrawnQueueResponse,
)
from agents_remember.serving.harness_control_client import (
    set_control_model,
    submit_control_prompt,
)
from agents_remember.serving.harness_control_models import SubmissionSource

SESSION = "ar-queue-1"


class QueueProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = FakeControlAdapter(harness="codex")
        self.harness = make_harness(self, self.adapter, SESSION, harness="codex")
        await self.harness.start()
        self.service = self.harness.service
        self.epoch = self.harness.epoch

    async def asyncTearDown(self) -> None:
        await self.harness.stop()

    async def _typed_submit(self, request_id: str, text: str) -> None:
        await attachments.submit(
            self.service,
            OPERATOR,
            SESSION,
            body=ConversationSubmitRequest(
                expected_bridge_epoch=self.epoch,
                request_id=request_id,
                disposition="next",
                content=(TextSubmitBlock(type="text", text=text),),
                draft_revision=3,
            ),
        )

    async def _legacy_submit(
        self, request_id: str, text: str, source: SubmissionSource
    ) -> None:
        await asyncio.to_thread(
            submit_control_prompt,
            self.harness.control_entry,
            text,
            source=source,
            request_id=request_id,
            expected_bridge_epoch=self.epoch if source == "cockpit" else None,
        )

    async def _queue(self):
        return await queue_projection.operation_queue(
            self.service, OPERATOR, SESSION, expected_bridge_epoch=self.epoch
        )

    async def test_complete_multi_source_truth_with_cockpit_only_identity(self) -> None:
        await drive_activity(self.harness, "running")
        await self._typed_submit("q-cockpit", "the exact cockpit draft body")
        await self._legacy_submit("q-terminal", "terminal body never crosses", "terminal")
        await self._legacy_submit("q-durable", "durable body never crosses", "durable")
        projection = await self._queue()
        self.assertEqual(projection.bridge_epoch, self.epoch)
        self.assertEqual(len(projection.items), 3)
        by_source = {row.source: row for row in projection.items}
        self.assertEqual(set(by_source), {"cockpit", "terminal", "durable"})
        self.assertTrue(all(row.kind == "prompt" for row in projection.items))
        self.assertTrue(all(row.phase == "queued" for row in projection.items))
        sequences = [row.sequence for row in projection.items]
        self.assertEqual(sequences, sorted(sequences))
        cockpit = by_source["cockpit"]
        self.assertTrue(cockpit.withdrawable)
        assert cockpit.cockpit is not None
        self.assertEqual(cockpit.cockpit.redacted_preview, "the exact cockpit draft body")
        self.assertFalse(cockpit.cockpit.preview_truncated)
        self.assertEqual(cockpit.cockpit.content_digest, payload_digest("the exact cockpit draft body"))
        self.assertTrue(cockpit.cockpit.withdrawal_ref.startswith("ar-wdr1."))
        for source in ("terminal", "durable"):
            self.assertFalse(by_source[source].withdrawable)
            self.assertIsNone(by_source[source].cockpit)
        # Privacy: no other-source body anywhere in the serialized projection.
        serialized = json.dumps(projection.model_dump(mode="json", by_alias=True))
        self.assertNotIn("terminal body never crosses", serialized)
        self.assertNotIn("durable body never crosses", serialized)
        self.assertNotIn("the exact cockpit draft body", serialized.replace("the exact cockpit draft body", "", 1))

    async def test_legacy_cockpit_row_reports_empty_held_content_honestly(self) -> None:
        await drive_activity(self.harness, "running")
        await self._legacy_submit("q-legacy", "legacy route body", "cockpit")
        projection = await self._queue()
        (row,) = projection.items
        self.assertEqual(row.source, "cockpit")
        self.assertTrue(row.withdrawable)
        assert row.cockpit is not None
        self.assertEqual(row.cockpit.redacted_preview, "")
        self.assertEqual(row.cockpit.content_digest, payload_digest(""))

    async def test_row_and_projection_revisions_are_semantic_and_monotonic(self) -> None:
        await drive_activity(self.harness, "running")
        await self._typed_submit("q-rev", "revision body")
        first = await self._queue()
        second = await self._queue()
        self.assertEqual(first.revision, second.revision)
        self.assertEqual(first.items[0].revision, second.items[0].revision)
        await self._typed_submit("q-rev-2", "another body")
        third = await self._queue()
        self.assertGreater(third.revision, first.revision)
        # Dispatch the head row and hold it mid-dispatch: the queued ->
        # dispatching transition must bump its row revision deterministically.
        self.adapter.submit_gate = asyncio.Event()
        self.adapter.set_activity("idle")
        head_before = third.items[0]
        deadline = asyncio.get_running_loop().time() + 5.0
        while True:
            fourth = await self._queue()
            if fourth.items[0].phase == "dispatching":
                break
            if asyncio.get_running_loop().time() > deadline:
                self.fail("head row never dispatched")
            await asyncio.sleep(0.05)
        self.assertGreater(fourth.items[0].revision, head_before.revision)
        self.assertGreater(fourth.revision, third.revision)
        self.adapter.submit_gate.set()
        self.adapter.submit_gate = None

    async def test_setter_operations_are_not_queue_rows(self) -> None:
        await drive_activity(self.harness, "running")
        await self._typed_submit("q-prompt", "prompt body")
        # A queued setter blocks its caller until resolution; run it as a task
        # while the lane is busy, then drain so the task completes.
        setter = asyncio.create_task(
            asyncio.to_thread(set_control_model, self.harness.control_entry, "any-model")
        )
        deadline = asyncio.get_running_loop().time() + 5.0
        while True:
            timeline = await self.service.read_full_timeline(
                self.harness.control_entry, expected_bridge_epoch=self.epoch
            )
            if any(item.kind == "set-model" for item in timeline):
                break
            if asyncio.get_running_loop().time() > deadline:
                self.fail("the setter never enumerated in the retained ledger")
            await asyncio.sleep(0.05)
        projection = await self._queue()
        self.assertEqual(len(projection.items), 1)
        self.assertEqual(projection.items[0].kind, "prompt")
        # Drain the lane: the queued head completes, the setter dispatches, and
        # its blocked caller resolves without a cancellation-wait on the thread.
        self.adapter.auto_release = True
        await drive_activity(self.harness, "idle")
        await asyncio.wait_for(asyncio.shield(setter), timeout=10.0)

    async def test_preview_transform_strips_collapses_redacts_and_truncates(self) -> None:
        text = "  hello\x00\x07 world \n\t  PASSWORD=hunter2 secret  " + "x" * 400
        preview, truncated = redacted_preview(text)
        self.assertNotIn("\x00", preview)
        self.assertNotIn("\x07", preview)
        self.assertNotIn("  ", preview)
        self.assertIn("PASSWORD=***", preview)
        self.assertTrue(truncated)
        emoji = "a\u200db" * 120  # ZWJ chains must never be split at the cut edge
        emoji_preview, emoji_truncated = redacted_preview(emoji)
        self.assertTrue(emoji_truncated)
        self.assertFalse(emoji_preview.endswith("\u200d"))
        self.assertLessEqual(len(_clusters(emoji_preview)), MAX_PREVIEW_CLUSTERS)
        short, short_truncated = redacted_preview("short")
        self.assertEqual((short, short_truncated), ("short", False))


class WithdrawalRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = FakeControlAdapter(harness="codex")
        self.harness = make_harness(self, self.adapter, SESSION, harness="codex")
        await self.harness.start()
        self.service = self.harness.service
        self.epoch = self.harness.epoch

    async def asyncTearDown(self) -> None:
        await self.harness.stop()

    async def _typed_submit(self, request_id: str, text: str, *, busy: bool = True) -> None:
        if busy:
            await drive_activity(self.harness, "running")
        await attachments.submit(
            self.service,
            OPERATOR,
            SESSION,
            body=ConversationSubmitRequest(
                expected_bridge_epoch=self.epoch,
                request_id=request_id,
                disposition="next",
                content=(TextSubmitBlock(type="text", text=text),),
                draft_revision=7,
            ),
        )

    async def _queue_row(self, request_index: int = 0):
        projection = await queue_projection.operation_queue(
            self.service, OPERATOR, SESSION, expected_bridge_epoch=self.epoch
        )
        return projection.items[request_index]

    async def _withdraw(self, row, withdraw_request_id: str):
        assert row.cockpit is not None
        return await withdrawals.withdraw(
            self.service,
            OPERATOR,
            SESSION,
            expected_bridge_epoch=self.epoch,
            operation_ref=row.operation_ref,
            withdrawal_ref=row.cockpit.withdrawal_ref,
            withdraw_request_id=withdraw_request_id,
        )

    async def test_successful_withdrawal_returns_exact_recovery_and_hides_row(self) -> None:
        await self._typed_submit("w-head", "head keeps the lane busy")
        await self._typed_submit("w-body", "the exact withdrawn draft")
        row = await self._queue_row(1)
        response = await self._withdraw(row, "wd-1")
        assert isinstance(response, WithdrawnQueueResponse)
        self.assertEqual(response.outcome, "withdrawn")
        self.assertEqual(response.revision, 1)
        self.assertEqual(response.recovery.text, "the exact withdrawn draft")
        self.assertEqual(response.recovery.content_digest, payload_digest("the exact withdrawn draft"))
        self.assertEqual(response.recovery.submitted_draft_revision, 7)
        self.assertTrue(response.recovery.recovery_ref.startswith("ar-wrr1."))
        self.assertEqual(withdrawals.withdraw_http_status(response), 200)
        projection = await queue_projection.operation_queue(
            self.service, OPERATOR, SESSION, expected_bridge_epoch=self.epoch
        )
        self.assertEqual(len(projection.items), 1)

    async def test_replay_returns_same_outcome_revision_and_recovery(self) -> None:
        await self._typed_submit("w-head", "head")
        await self._typed_submit("w-body", "replay body")
        row = await self._queue_row(1)
        first = await self._withdraw(row, "wd-2")
        replay = await self._withdraw(row, "wd-2")
        assert isinstance(first, WithdrawnQueueResponse) and isinstance(replay, WithdrawnQueueResponse)
        self.assertEqual(replay.revision, first.revision)
        self.assertEqual(replay.outcome, "withdrawn")
        self.assertEqual(replay.recovery.text, "replay body")

    async def test_conflicting_withdraw_request_id_is_typed(self) -> None:
        await self._typed_submit("w-head", "head")
        await self._typed_submit("w-body-a", "body a")
        await self._typed_submit("w-body-b", "body b")
        await self._withdraw(await self._queue_row(1), "wd-3")
        with self.assertRaises(OperationConflictError):
            await self._withdraw(await self._queue_row(1), "wd-3")

    async def test_already_dispatching_and_not_found_outcomes(self) -> None:
        # Queue the head while busy and capture its refs, then dispatch it
        # into the gate: the withdraw races the queued -> dispatching edge and
        # must lose honestly with already-dispatching.
        await self._typed_submit("w-head", "head is dispatching")
        queued_head = await self._queue_row(0)
        self.assertEqual(queued_head.phase, "queued")
        self.adapter.submit_gate = asyncio.Event()
        await drive_activity(self.harness, "idle")
        deadline = asyncio.get_running_loop().time() + 5.0
        head = await self._queue_row(0)
        while head.phase != "dispatching":
            if asyncio.get_running_loop().time() > deadline:
                self.fail("head row never dispatched into the gate")
            await asyncio.sleep(0.05)
            head = await self._queue_row(0)
        response = await self._withdraw(queued_head, "wd-4")
        self.assertEqual(response.outcome, "already-dispatching")
        self.assertEqual(withdrawals.withdraw_http_status(response), 409)
        self.adapter.submit_gate.set()
        self.adapter.submit_gate = None
        await drive_activity(self.harness, "running")
        # A valid ref pair for a row that is not a cockpit row answers not-found.
        await self._typed_submit("w-second", "second")
        terminal_row = None
        await asyncio.to_thread(
            submit_control_prompt,
            self.harness.control_entry,
            "durable body",
            source="durable",
            request_id="w-durable",
        )
        projection = await queue_projection.operation_queue(
            self.service, OPERATOR, SESSION, expected_bridge_epoch=self.epoch
        )
        terminal_row = next(item for item in projection.items if item.source == "durable")
        forged_withdrawal_ref = mint_ref(
            self.service.secret,
            "withdrawal-ref",
            OPERATOR,
            ar_session_id=SESSION,
            bridge_epoch=self.epoch,
            identity=OperationIdentity(
                kind="prompt", operation_id="w-durable", sequence=terminal_row.sequence
            ),
        )
        response = await withdrawals.withdraw(
            self.service,
            OPERATOR,
            SESSION,
            expected_bridge_epoch=self.epoch,
            operation_ref=terminal_row.operation_ref,
            withdrawal_ref=forged_withdrawal_ref,
            withdraw_request_id="wd-5",
        )
        self.assertEqual(response.outcome, "not-found")
        self.assertEqual(withdrawals.withdraw_http_status(response), 404)

    async def test_pending_discovery_fetch_ack_and_disposed_replay(self) -> None:
        await self._typed_submit("w-head", "head")
        await self._typed_submit("w-body", "discoverable draft")
        row = await self._queue_row(1)
        await self._withdraw(row, "wd-6")
        pending = await withdrawals.pending_recoveries(
            self.service, OPERATOR, SESSION, expected_bridge_epoch=self.epoch
        )
        self.assertEqual(len(pending.items), 1)
        item = pending.items[0]
        self.assertEqual(item.withdraw_request_id, "wd-6")
        self.assertEqual(item.state, "recovery-unacknowledged")
        serialized = json.dumps(pending.model_dump(mode="json", by_alias=True))
        self.assertNotIn("discoverable draft", serialized)
        recovery = await withdrawals.fetch_recovery(
            self.service,
            OPERATOR,
            SESSION,
            expected_bridge_epoch=self.epoch,
            recovery_ref=item.recovery_ref,
        )
        self.assertEqual(recovery.text, "discoverable draft")
        projection = await withdrawals.acknowledge_recovery(
            self.service,
            OPERATOR,
            SESSION,
            expected_bridge_epoch=self.epoch,
            recovery_ref=item.recovery_ref,
            disposition="replace-current-draft",
        )
        self.assertEqual(projection.recovery_state, "acknowledged")
        self.assertGreater(projection.revision, 1)
        with self.assertRaises(OperationNotFoundError):
            await withdrawals.fetch_recovery(
                self.service,
                OPERATOR,
                SESSION,
                expected_bridge_epoch=self.epoch,
                recovery_ref=item.recovery_ref,
            )
        replay = await self._withdraw(row, "wd-6")
        assert isinstance(replay, WithdrawnQueueResponse)
        self.assertEqual(replay.recovery.text, "")
        status = await withdrawals.withdraw_status(
            self.service,
            OPERATOR,
            SESSION,
            expected_bridge_epoch=self.epoch,
            operation_ref=row.operation_ref,
            withdraw_request_id="wd-6",
            reconcile=False,
        )
        self.assertEqual(status.phase, "settled")
        self.assertEqual(status.outcome, "withdrawn")
        self.assertEqual(status.recovery_state, "acknowledged")

    async def test_legacy_row_recovers_from_substrate_payload(self) -> None:
        await self._typed_submit("w-head", "head")
        await asyncio.to_thread(
            submit_control_prompt,
            self.harness.control_entry,
            "legacy exact body",
            source="cockpit",
            request_id="w-legacy",
            expected_bridge_epoch=self.epoch,
        )
        projection = await queue_projection.operation_queue(
            self.service, OPERATOR, SESSION, expected_bridge_epoch=self.epoch
        )
        row = projection.items[1]
        self.assertEqual(row.source, "cockpit")
        assert row.cockpit is not None
        self.assertEqual(row.cockpit.redacted_preview, "")
        response = await self._withdraw(row, "wd-7")
        assert isinstance(response, WithdrawnQueueResponse)
        self.assertEqual(response.recovery.text, "legacy exact body")
        self.assertEqual(response.recovery.content_digest, payload_digest("legacy exact body"))

    async def test_lost_withdraw_response_recovers_through_journal(self) -> None:
        await self._typed_submit("w-head", "head")
        await self._typed_submit("w-body", "lost response draft")
        row = await self._queue_row(1)
        assert row.cockpit is not None
        with mock.patch.object(
            withdrawals,
            "withdraw_control_submission",
            side_effect=HarnessControlClientError("socket died mid-read", may_have_sent=True),
        ):
            response = await withdrawals.withdraw(
                self.service,
                OPERATOR,
                SESSION,
                expected_bridge_epoch=self.epoch,
                operation_ref=row.operation_ref,
                withdrawal_ref=row.cockpit.withdrawal_ref,
                withdraw_request_id="wd-8",
            )
        self.assertEqual(response.outcome, "delivery-unknown")
        self.assertEqual(withdrawals.withdraw_http_status(response), 202)
        projection = await withdrawals.withdraw_status(
            self.service,
            OPERATOR,
            SESSION,
            expected_bridge_epoch=self.epoch,
            operation_ref=row.operation_ref,
            withdraw_request_id="wd-8",
            reconcile=True,
        )
        self.assertEqual(projection.phase, "settled")
        self.assertEqual(projection.outcome, "withdrawn")
        pending = await withdrawals.pending_recoveries(
            self.service, OPERATOR, SESSION, expected_bridge_epoch=self.epoch
        )
        self.assertEqual(len(pending.items), 1)
        recovery = await withdrawals.fetch_recovery(
            self.service,
            OPERATOR,
            SESSION,
            expected_bridge_epoch=self.epoch,
            recovery_ref=pending.items[0].recovery_ref,
        )
        self.assertEqual(recovery.text, "lost response draft")

    async def test_recovery_lease_expiry_disposes_content(self) -> None:
        clock = {"now": "2026-07-20T09:00:00+00:00"}
        timed_service = ConversationControlService(self.harness.runtime, clock=lambda: clock["now"])
        await drive_activity(self.harness, "running")
        await attachments.submit(
            timed_service,
            OPERATOR,
            SESSION,
            body=ConversationSubmitRequest(
                expected_bridge_epoch=self.epoch,
                request_id="w-exp",
                disposition="next",
                content=(TextSubmitBlock(type="text", text="expiring draft"),),
                draft_revision=1,
            ),
        )
        projection = await queue_projection.operation_queue(
            timed_service, OPERATOR, SESSION, expected_bridge_epoch=self.epoch
        )
        row = projection.items[0]
        assert row.cockpit is not None
        await withdrawals.withdraw(
            timed_service,
            OPERATOR,
            SESSION,
            expected_bridge_epoch=self.epoch,
            operation_ref=row.operation_ref,
            withdrawal_ref=row.cockpit.withdrawal_ref,
            withdraw_request_id="wd-9",
        )
        clock["now"] = "2026-07-20T09:16:01+00:00"
        pending = await withdrawals.pending_recoveries(
            timed_service, OPERATOR, SESSION, expected_bridge_epoch=self.epoch
        )
        self.assertEqual(pending.items, ())
        status = await withdrawals.withdraw_status(
            timed_service,
            OPERATOR,
            SESSION,
            expected_bridge_epoch=self.epoch,
            operation_ref=row.operation_ref,
            withdraw_request_id="wd-9",
            reconcile=False,
        )
        self.assertEqual(status.recovery_state, "expired")

    async def test_reference_forgery_battery_fails_typed(self) -> None:
        await self._typed_submit("w-head", "head")
        await self._typed_submit("w-body", "battery body")
        row = await self._queue_row(1)
        assert row.cockpit is not None
        identity = OperationIdentity(kind="prompt", operation_id="w-body", sequence=row.sequence)
        foreign_session_ref = mint_ref(
            self.service.secret,
            "withdrawal-ref",
            OPERATOR,
            ar_session_id="ar-other",
            bridge_epoch=self.epoch,
            identity=identity,
        )
        with self.assertRaises(ControlRefError) as raised:
            await withdrawals.withdraw(
                self.service,
                OPERATOR,
                SESSION,
                expected_bridge_epoch=self.epoch,
                operation_ref=row.operation_ref,
                withdrawal_ref=foreign_session_ref,
                withdraw_request_id="wd-10",
            )
        self.assertEqual(raised.exception.http_status, 403)
        foreign_epoch_ref = mint_ref(
            self.service.secret,
            "withdrawal-ref",
            OPERATOR,
            ar_session_id=SESSION,
            bridge_epoch="other-epoch",
            identity=identity,
        )
        with self.assertRaises(ControlRefError) as raised:
            await withdrawals.withdraw(
                self.service,
                OPERATOR,
                SESSION,
                expected_bridge_epoch=self.epoch,
                operation_ref=row.operation_ref,
                withdrawal_ref=foreign_epoch_ref,
                withdraw_request_id="wd-10",
            )
        self.assertEqual(raised.exception.http_status, 409)
        tampered = row.cockpit.withdrawal_ref[:-4] + (
            "aaaa" if not row.cockpit.withdrawal_ref.endswith("aaaa") else "bbbb"
        )
        with self.assertRaises(ControlRefError) as raised:
            await withdrawals.withdraw(
                self.service,
                OPERATOR,
                SESSION,
                expected_bridge_epoch=self.epoch,
                operation_ref=row.operation_ref,
                withdrawal_ref=tampered,
                withdraw_request_id="wd-10",
            )
        self.assertEqual(raised.exception.http_status, 400)


if __name__ == "__main__":
    unittest.main()
