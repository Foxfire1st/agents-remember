"""Interrupt ledger contract tests (260718-CHATS-L3, R1/R7).

Every test drives the REAL composition up to the harness edge: a real bridge +
IPC server on a real socket, the real submission authority, and the landed L2E
client reads; the only double is the structural fake adapter (interrupt- and
asset-capable). Lost-response classes patch the client boundary inside the
ledger (documented); every other path crosses the real socket.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from _control_plane import OPERATOR, FakeControlAdapter, make_harness
from agents_remember.errors import (
    HarnessBridgeEpochMismatchError,
    HarnessControlClientError,
)
from agents_remember.serving.conversation.control import attachments, operations
from agents_remember.serving.conversation.control.service import (
    CapabilityRefusedError,
    ConversationControlService,
    OperationConflictError,
    OperationNotFoundError,
)
from agents_remember.serving.conversation.models import (
    ConversationSubmitRequest,
    TextSubmitBlock,
)


class CodexInterruptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = FakeControlAdapter(harness="codex")
        self.harness = make_harness(self, self.adapter, "ar-ops-1", harness="codex")
        await self.harness.start()
        self.service = self.harness.service
        self.epoch = self.harness.epoch

    async def asyncTearDown(self) -> None:
        await self.harness.stop()

    async def _submit(self, request_id: str, text: str = "work") -> None:
        await operations_submit(self.service, "ar-ops-1", request_id, text, self.epoch)
        self.adapter.set_activity("running")

    async def _interrupt(self, turn: str = "turn-req-1", request_id: str = "req-1"):
        return await operations.interrupt(
            self.service,
            OPERATOR,
            "ar-ops-1",
            expected_bridge_epoch=self.epoch,
            turn_id=turn,
            request_id=request_id,
        )

    async def _status(self, turn: str = "turn-req-1", request_id: str = "req-1", *, reconcile=False):
        return await operations.interrupt_status(
            self.service,
            OPERATOR,
            "ar-ops-1",
            expected_bridge_epoch=self.epoch,
            turn_id=turn,
            request_id=request_id,
            reconcile=reconcile,
        )

    async def test_accepted_acknowledgement_is_not_settlement_and_writes_once(self) -> None:
        await self._submit("req-1")
        operation = await self._interrupt()
        self.assertEqual(operation.acknowledgement, "accepted")
        self.assertEqual(operation.settlement, "pending")
        self.assertIsNone(operation.settled_at)
        self.assertEqual(operation.native_correlation_id, "turn-req-1")
        self.assertEqual(operation.revision, 2)
        self.assertEqual(operations.interrupt_http_status(operation), 202)
        self.assertEqual(len(self.adapter.interrupt_calls), 1)

    async def test_identical_replay_returns_same_revision_with_no_second_write(self) -> None:
        await self._submit("req-1")
        first = await self._interrupt()
        replay = await self._interrupt()
        self.assertEqual(replay.revision, first.revision)
        self.assertEqual(replay.acknowledgement, "accepted")
        self.assertEqual(replay.settlement, "pending")
        self.assertEqual(len(self.adapter.interrupt_calls), 1)

    async def test_request_id_reuse_with_different_turn_conflicts(self) -> None:
        await self._submit("req-1")
        await self._interrupt()
        with self.assertRaises(OperationConflictError):
            await self._interrupt(turn="turn-other")

    async def test_no_active_turn_refuses_with_rejected_failed(self) -> None:
        operation = await self._interrupt()
        self.assertEqual(operation.acknowledgement, "rejected")
        self.assertEqual(operation.settlement, "failed")
        self.assertIsNotNone(operation.settled_at)
        self.assertEqual(operations.interrupt_http_status(operation), 422)
        self.assertEqual(len(self.adapter.interrupt_calls), 0)

    async def test_settlement_correlates_interrupted_terminal_event(self) -> None:
        await self._submit("req-1")
        await self._interrupt()
        self.adapter.settle_turn("interrupted")
        status = await self._status()
        self.assertEqual(status.acknowledgement, "accepted")
        self.assertEqual(status.settlement, "interrupted")
        self.assertIsNotNone(status.settled_at)
        self.assertGreater(status.revision, 2)
        self.assertEqual(operations.interrupt_http_status(status), 200)

    async def test_settlement_already_settled_on_natural_completion(self) -> None:
        await self._submit("req-1")
        await self._interrupt()
        self.adapter.settle_turn("completed")
        status = await self._status()
        self.assertEqual(status.settlement, "already-settled")
        self.assertEqual(operations.interrupt_http_status(status), 200)

    async def test_settlement_failed_maps_503(self) -> None:
        await self._submit("req-1")
        await self._interrupt()
        self.adapter.settle_turn("failed")
        status = await self._status()
        self.assertEqual(status.settlement, "failed")
        self.assertEqual(operations.interrupt_http_status(status), 503)

    async def test_lost_response_is_unknown_and_reconcile_recovers_first_ack(self) -> None:
        await self._submit("req-1")
        with mock.patch.object(
            operations,
            "interrupt_control",
            side_effect=HarnessControlClientError("socket died mid-write", may_have_sent=True),
        ):
            operation = await self._interrupt()
        self.assertEqual(operation.acknowledgement, "unknown")
        self.assertEqual(operation.settlement, "pending")
        self.assertEqual(operations.interrupt_http_status(operation), 202)
        self.assertEqual(len(self.adapter.interrupt_calls), 0)
        reconciled = await self._status(reconcile=True)
        self.assertEqual(reconciled.acknowledgement, "accepted")
        self.assertGreater(reconciled.revision, operation.revision)
        self.assertEqual(len(self.adapter.interrupt_calls), 1)

    async def test_control_unavailable_before_write_raises_503_class(self) -> None:
        await self._submit("req-1")
        with mock.patch.object(
            operations,
            "interrupt_control",
            side_effect=HarnessControlClientError("endpoint down", may_have_sent=False),
        ), self.assertRaises(operations.ControlUnavailableError):
            await self._interrupt()
        with self.assertRaises(OperationNotFoundError):
            await self._status()

    async def test_concurrent_same_tuple_serializes_to_one_write(self) -> None:
        await self._submit("req-1")
        first, second = await asyncio.gather(self._interrupt(), self._interrupt())
        self.assertEqual(first.request_id, second.request_id)
        self.assertEqual(first.revision, second.revision)
        self.assertEqual(len(self.adapter.interrupt_calls), 1)

    async def test_epoch_mismatch_fails_typed(self) -> None:
        with self.assertRaises(HarnessBridgeEpochMismatchError):
            await operations.interrupt(
                self.service,
                OPERATOR,
                "ar-ops-1",
                expected_bridge_epoch="wrong-epoch",
                turn_id="turn-req-1",
                request_id="req-1",
            )


class PiInterruptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = FakeControlAdapter(harness="pi", vendor_id="pi-session-1")
        self.harness = make_harness(self, self.adapter, "ar-ops-pi", harness="pi")
        await self.harness.start()
        self.service = self.harness.service
        self.epoch = self.harness.epoch

    async def asyncTearDown(self) -> None:
        await self.harness.stop()

    async def _submit(self, request_id: str) -> None:
        await operations_submit(self.service, "ar-ops-pi", request_id, "work", self.epoch)
        self.adapter.set_activity("running")

    async def test_pi_interrupt_uses_operation_identity_and_settles_aborted(self) -> None:
        await self._submit("req-pi-1")
        operation = await operations.interrupt(
            self.service,
            OPERATOR,
            "ar-ops-pi",
            expected_bridge_epoch=self.epoch,
            turn_id="req-pi-1",
            request_id="int-pi-1",
        )
        self.assertEqual(operation.acknowledgement, "accepted")
        self.assertEqual(operation.settlement, "pending")
        self.assertEqual(self.adapter.interrupt_calls[0]["expected_operation_id"], "req-pi-1")
        self.assertIsNone(self.adapter.interrupt_calls[0]["turn_id"])
        self.adapter.pi_settle("aborted")
        status = await operations.interrupt_status(
            self.service,
            OPERATOR,
            "ar-ops-pi",
            expected_bridge_epoch=self.epoch,
            turn_id="req-pi-1",
            request_id="int-pi-1",
            reconcile=False,
        )
        self.assertEqual(status.settlement, "interrupted")

    async def test_pi_stale_expected_identity_refuses_before_write(self) -> None:
        await self._submit("req-pi-1")
        operation = await operations.interrupt(
            self.service,
            OPERATOR,
            "ar-ops-pi",
            expected_bridge_epoch=self.epoch,
            turn_id="req-pi-stale",
            request_id="int-pi-2",
        )
        self.assertEqual(operation.acknowledgement, "rejected")
        self.assertEqual(operation.settlement, "failed")
        self.assertEqual(len(self.adapter.interrupt_calls), 0)

    async def test_pi_natural_completion_settles_already_settled(self) -> None:
        await self._submit("req-pi-1")
        await operations.interrupt(
            self.service,
            OPERATOR,
            "ar-ops-pi",
            expected_bridge_epoch=self.epoch,
            turn_id="req-pi-1",
            request_id="int-pi-3",
        )
        self.adapter.pi_settle("stop")
        status = await operations.interrupt_status(
            self.service,
            OPERATOR,
            "ar-ops-pi",
            expected_bridge_epoch=self.epoch,
            turn_id="req-pi-1",
            request_id="int-pi-3",
            reconcile=False,
        )
        self.assertEqual(status.settlement, "already-settled")

    async def test_pi_content_ful_natural_completion_settles_already_settled(self) -> None:
        # The ordinary pi turn: the abort ack lands, then generation finishes with final
        # text, so the message_end crosses as kind "transcript" (not "pi:message_end") with
        # its stopReason under the evidence key. Settlement must still resolve — the regression
        # is that _pi_stop_reason once matched on event kind and never saw this frame.
        await self._submit("req-pi-1")
        await operations.interrupt(
            self.service,
            OPERATOR,
            "ar-ops-pi",
            expected_bridge_epoch=self.epoch,
            turn_id="req-pi-1",
            request_id="int-pi-4",
        )
        self.adapter.pi_settle_with_content("stop")
        status = await operations.interrupt_status(
            self.service,
            OPERATOR,
            "ar-ops-pi",
            expected_bridge_epoch=self.epoch,
            turn_id="req-pi-1",
            request_id="int-pi-4",
            reconcile=False,
        )
        self.assertEqual(status.settlement, "already-settled")

    async def test_pi_content_ful_abort_settles_interrupted(self) -> None:
        # A content-ful message_end can still carry stopReason "aborted" (text was already
        # streamed before the abort took effect); it likewise crosses as kind "transcript".
        await self._submit("req-pi-1")
        operation = await operations.interrupt(
            self.service,
            OPERATOR,
            "ar-ops-pi",
            expected_bridge_epoch=self.epoch,
            turn_id="req-pi-1",
            request_id="int-pi-5",
        )
        self.assertEqual(operation.acknowledgement, "accepted")
        self.assertEqual(operation.settlement, "pending")
        self.adapter.pi_settle_with_content("aborted")
        status = await operations.interrupt_status(
            self.service,
            OPERATOR,
            "ar-ops-pi",
            expected_bridge_epoch=self.epoch,
            turn_id="req-pi-1",
            request_id="int-pi-5",
            reconcile=False,
        )
        self.assertEqual(status.settlement, "interrupted")

    async def test_pi_oversized_contentful_message_end_settles_not_pending(self) -> None:
        # Finding 2 facet (a): a content-ful message_end whose frame serializes over the
        # bridge's 32 KiB evidence budget is stored as the truncation envelope. Pre-L3E that
        # envelope had no "type"/stopReason, so _pi_stop_reason skipped it and settlement
        # stalled `pending` forever (the runaway-generation interrupt case). L3E preserves
        # type + message.stopReason in the envelope, so it now settles honestly. The real
        # bridge clip path does the clipping (40 KB text > 32 KiB budget).
        await self._submit("req-pi-1")
        await operations.interrupt(
            self.service,
            OPERATOR,
            "ar-ops-pi",
            expected_bridge_epoch=self.epoch,
            turn_id="req-pi-1",
            request_id="int-pi-big",
        )
        self.adapter.pi_emit_message_end(text="x" * 40_000, stop_reason="stop")
        self.adapter.pi_release()
        status = await operations.interrupt_status(
            self.service,
            OPERATOR,
            "ar-ops-pi",
            expected_bridge_epoch=self.epoch,
            turn_id="req-pi-1",
            request_id="int-pi-big",
            reconcile=False,
        )
        self.assertNotEqual(status.settlement, "pending")
        self.assertEqual(status.settlement, "already-settled")

    async def test_pi_clipped_final_abort_after_tool_use_settles_interrupted(self) -> None:
        # Finding 2 facet (b): a small mid-turn message_end (stopReason "toolUse") precedes an
        # OVERSIZED (> 32 KiB) final message_end with stopReason "aborted". Pre-L3E the final
        # frame clipped to a type-less envelope, so the latest VISIBLE stopReason was "toolUse"
        # and settlement affirmatively mis-read `already-settled` while the abort took effect.
        # L3E preserves the aborted stopReason in the envelope, so latest-wins settles
        # `interrupted`, never `already-settled`.
        await self._submit("req-pi-1")
        operation = await operations.interrupt(
            self.service,
            OPERATOR,
            "ar-ops-pi",
            expected_bridge_epoch=self.epoch,
            turn_id="req-pi-1",
            request_id="int-pi-mix",
        )
        self.assertEqual(operation.acknowledgement, "accepted")
        self.adapter.pi_emit_message_end(text="let me check that file", stop_reason="toolUse")
        self.adapter.pi_emit_message_end(text="y" * 40_000, stop_reason="aborted")
        self.adapter.pi_release()
        status = await operations.interrupt_status(
            self.service,
            OPERATOR,
            "ar-ops-pi",
            expected_bridge_epoch=self.epoch,
            turn_id="req-pi-1",
            request_id="int-pi-mix",
            reconcile=False,
        )
        self.assertNotEqual(status.settlement, "already-settled")
        self.assertEqual(status.settlement, "interrupted")


class ClaudeInterruptGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = FakeControlAdapter(harness="claude", vendor_id="claude-1")
        self.harness = make_harness(self, self.adapter, "ar-ops-cl", harness="claude")
        await self.harness.start()
        self.service = self.harness.service

    async def asyncTearDown(self) -> None:
        await self.harness.stop()

    async def test_unverified_capability_refuses_before_any_native_call(self) -> None:
        with self.assertRaises(CapabilityRefusedError) as raised:
            await operations.interrupt(
                self.service,
                OPERATOR,
                "ar-ops-cl",
                expected_bridge_epoch=self.harness.epoch,
                turn_id="turn-1",
                request_id="req-cl-1",
            )
        self.assertIn("2.1.211", str(raised.exception))
        self.assertEqual(len(self.adapter.interrupt_calls), 0)


async def operations_submit(
    service: ConversationControlService, session: str, request_id: str, text: str, epoch: str
) -> None:
    await attachments.submit(
        service,
        OPERATOR,
        session,
        body=ConversationSubmitRequest(
            expected_bridge_epoch=epoch,
            request_id=request_id,
            disposition="next",
            content=(TextSubmitBlock(type="text", text=text),),
            draft_revision=1,
        ),
    )


if __name__ == "__main__":
    unittest.main()
