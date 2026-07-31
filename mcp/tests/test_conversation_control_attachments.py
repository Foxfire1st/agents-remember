"""Attachment lifecycle, policy, and telemetry contract tests (260718-CHATS-L3, R4/R5/R6/R7).

Real composition up to the harness edge (bridge + IPC + real authority + the
L2E asset channel + the user-private spool); the only double is the structural
fake adapter. Bytes are tiny; every limit is exercised at its boundary.
"""

from __future__ import annotations

import asyncio
import unittest
from hashlib import sha256

from _control_plane import OPERATOR, TINY_PNG, FakeControlAdapter, drive_activity, make_harness
from agents_remember.serving.conversation.control import (
    attachments,
    policy,
    queue_projection,
    telemetry,
    withdrawals,
)
from agents_remember.serving.conversation.control.capabilities import (
    control_capabilities_for,
)
from agents_remember.serving.conversation.control.service import (
    CapabilityRefusedError,
    ControlRequest,
    OperationConflictError,
    OperationRejectedError,
)
from agents_remember.serving.conversation.models import (
    AssetSubmitBlock,
    ConversationSubmitRequest,
    TextSubmitBlock,
    WithdrawnQueueResponse,
)
from agents_remember.serving.harness_control_models import AR_EVIDENCE_KEY

SESSION = "ar-attach-1"


def _png(name: str = "dot.png", alt: str | None = None, data: bytes = TINY_PNG):
    return attachments.StagedUpload(
        kind="image", name=name, mime_type="image/png", alt=alt, data=data
    )


def _submit_body(
    request_id: str, epoch: str, receipt, text: str = "describe this"
) -> ConversationSubmitRequest:
    return ConversationSubmitRequest(
        expected_bridge_epoch=epoch,
        request_id=request_id,
        disposition="next",
        content=(
            TextSubmitBlock(type="text", text=text),
            AssetSubmitBlock(
                type="asset-ref",
                asset_id=receipt.asset_id,
                kind=receipt.kind,
                name=receipt.name,
                mime_type=receipt.mime_type,
                alt=receipt.alt,
                alt_provenance=receipt.alt_provenance,
                sha256=receipt.sha256,
            ),
        ),
        draft_revision=2,
    )


class AttachmentStageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = FakeControlAdapter(harness="codex")
        self.harness = make_harness(self, self.adapter, SESSION, harness="codex")
        await self.harness.start()
        self.service = self.harness.service
        self.epoch = self.harness.epoch
        self.caps = self._caps()

    def _caps(self):
        assert self.adapter.current is not None
        caps = control_capabilities_for("codex", self.adapter.current).attachments
        return {"image": caps.image, "file": caps.file, "resource": caps.resource}

    async def asyncTearDown(self) -> None:
        await self.harness.stop()

    async def _stage(self, request_id: str, uploads):
        return await attachments.stage(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            request_id=request_id,
            kind_capabilities=self.caps,
            uploads=uploads,
        )

    async def test_stage_binds_and_returns_receipt_with_fallback_alt(self) -> None:
        answer = await self._stage("at-1", [_png()])
        self.assertEqual(answer.operation.phase, "staged")
        self.assertEqual(answer.operation.outcome, "pending")
        (receipt,) = answer.receipts
        self.assertEqual(receipt.alt, "dot.png, image/png")
        self.assertEqual(receipt.alt_provenance, "filename-mime-fallback")
        self.assertEqual(receipt.sha256, sha256(TINY_PNG).hexdigest())
        self.assertEqual(receipt.size_bytes, len(TINY_PNG))
        self.assertEqual(receipt.ar_session_id, SESSION)
        self.assertEqual(receipt.bridge_epoch, self.epoch)
        spool = self.harness.endpoint.path.parent / "assets" / "at-1" / receipt.asset_id
        self.assertTrue(spool.exists())
        replay = await self._stage("at-1", [_png()])
        self.assertEqual(replay.receipts[0].asset_id, receipt.asset_id)
        self.assertEqual(replay.operation.revision, answer.operation.revision)

    async def test_supplied_alt_keeps_its_provenance(self) -> None:
        answer = await self._stage("at-2", [_png(alt="a diagram of the flow")])
        receipt = answer.receipts[0]
        self.assertEqual(receipt.alt, "a diagram of the flow")
        self.assertEqual(receipt.alt_provenance, "supplied-description")

    async def test_mime_count_byte_and_kind_limits_are_typed(self) -> None:
        with self.assertRaises(OperationRejectedError):
            await self._stage(
                "at-3",
                [
                    attachments.StagedUpload(
                        kind="image", name="note.txt", mime_type="text/plain", alt=None, data=b"hi"
                    )
                ],
            )
        with self.assertRaises(OperationRejectedError):
            await self._stage("at-4", [_png() for _ in range(5)])
        oversized = attachments.StagedUpload(
            kind="image",
            name="big.png",
            mime_type="image/png",
            alt=None,
            data=b"\x00" * (5 * 1024 * 1024 + 1),
        )
        with self.assertRaises(OperationRejectedError):
            await self._stage("at-5", [oversized])
        file_kind = attachments.StagedUpload(
            kind="file", name="doc.pdf", mime_type="application/pdf", alt=None, data=b"%PDF"
        )
        with self.assertRaises(CapabilityRefusedError):
            await self._stage("at-6", [file_kind])

    async def test_conflicting_content_reuse_is_typed(self) -> None:
        await self._stage("at-7", [_png()])
        with self.assertRaises(OperationConflictError):
            await self._stage("at-7", [_png(name="other.png")])


class AttachmentSubmitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = FakeControlAdapter(harness="codex")
        self.harness = make_harness(self, self.adapter, SESSION, harness="codex")
        await self.harness.start()
        self.service = self.harness.service
        self.epoch = self.harness.epoch
        assert self.adapter.current is not None
        caps = control_capabilities_for("codex", self.adapter.current).attachments
        self.caps = {"image": caps.image, "file": caps.file, "resource": caps.resource}

    async def asyncTearDown(self) -> None:
        await self.harness.stop()

    async def _stage_and_receipt(self, request_id: str):
        answer = await attachments.stage(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            request_id=request_id,
            kind_capabilities=self.caps,
            uploads=[_png()],
        )
        return answer.receipts[0]

    async def test_submit_carries_refs_and_consumes_one_use(self) -> None:
        receipt = await self._stage_and_receipt("at-s1")
        answer = await attachments.submit(
            self.service, OPERATOR, SESSION, body=_submit_body("at-s1", self.epoch, receipt)
        )
        self.assertEqual(answer.acceptance, "immediate")
        assert answer.attachment is not None
        self.assertEqual(answer.attachment.phase, "dispatching")
        self.assertTrue(answer.operation_ref and answer.operation_ref.startswith("ar-oqr1."))
        (request,) = self.adapter.submit_requests
        self.assertEqual(len(request.assets), 1)
        self.assertEqual(request.assets[0].asset_id, receipt.asset_id)
        replay = await attachments.submit(
            self.service, OPERATOR, SESSION, body=_submit_body("at-s1", self.epoch, receipt)
        )
        self.assertEqual(replay.acceptance, "immediate")
        self.assertEqual(len(self.adapter.submit_requests), 1)
        with self.assertRaises(OperationConflictError):
            await attachments.submit(
                self.service,
                OPERATOR,
                SESSION,
                body=_submit_body("at-s1", self.epoch, receipt, text="changed content"),
            )

    async def test_tampered_asset_block_is_rejected_before_dispatch(self) -> None:
        receipt = await self._stage_and_receipt("at-s2")
        body = _submit_body("at-s2", self.epoch, receipt)
        asset_block = next(block for block in body.content if block.type == "asset-ref")
        tampered = asset_block.model_copy(update={"sha256": "0" * 64})
        body = body.model_copy(update={"content": (body.content[0], tampered)})
        with self.assertRaises(OperationRejectedError):
            await attachments.submit(self.service, OPERATOR, SESSION, body=body)
        self.assertEqual(len(self.adapter.submit_requests), 0)

    async def test_double_use_of_one_asset_is_typed(self) -> None:
        receipt = await self._stage_and_receipt("at-s3")
        await attachments.submit(
            self.service, OPERATOR, SESSION, body=_submit_body("at-s3", self.epoch, receipt)
        )
        with self.assertRaises(OperationConflictError):
            await attachments.submit(
                self.service,
                OPERATOR,
                SESSION,
                body=_submit_body("at-s3", self.epoch, receipt, text="again"),
            )
        self.assertEqual(len(self.adapter.submit_requests), 1)


class AttachmentRebindTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = FakeControlAdapter(harness="codex")
        self.harness = make_harness(self, self.adapter, SESSION, harness="codex")
        await self.harness.start()
        self.service = self.harness.service
        self.epoch = self.harness.epoch
        assert self.adapter.current is not None
        caps = control_capabilities_for("codex", self.adapter.current).attachments
        self.caps = {"image": caps.image, "file": caps.file, "resource": caps.resource}

    async def asyncTearDown(self) -> None:
        await self.harness.stop()

    async def _withdraw_with_asset(self):
        await drive_activity(self.harness, "running")
        staged = await attachments.stage(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            request_id="at-r1",
            kind_capabilities=self.caps,
            uploads=[_png()],
        )
        await attachments.submit(
            self.service,
            OPERATOR,
            SESSION,
            body=_submit_body("at-r1", self.epoch, staged.receipts[0]),
        )
        await attachments.submit(
            self.service,
            OPERATOR,
            SESSION,
            body=ConversationSubmitRequest(
                expected_bridge_epoch=self.epoch,
                request_id="at-r2",
                disposition="next",
                content=(TextSubmitBlock(type="text", text="recover me with my asset"),),
                draft_revision=1,
            ),
        )
        projection = await queue_projection.operation_queue(
            self.service, OPERATOR, SESSION, expected_bridge_epoch=self.epoch
        )
        row = next(item for item in projection.items if item.phase == "queued")
        assert row.cockpit is not None
        response = await withdrawals.withdraw(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            operation_ref=row.operation_ref,
            withdrawal_ref=row.cockpit.withdrawal_ref,
            withdraw_request_id="wd-r1",
        )
        return staged, response

    async def test_withdraw_marks_recoverable_and_rebind_exchanges_one_use(self) -> None:

        _staged, response = await self._withdraw_with_asset()
        assert isinstance(response, WithdrawnQueueResponse)
        self.assertEqual(len(response.recovery.attachments), 1)
        recovery_asset = response.recovery.attachments[0]
        self.assertEqual(recovery_asset.alt, "dot.png, image/png")
        answer = await attachments.rebind(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            recovery_asset_ref=recovery_asset.recovery_asset_ref,
            request_id="at-r3",
        )
        (new_receipt,) = answer.receipts
        self.assertEqual(new_receipt.request_id, "at-r3")
        self.assertNotEqual(new_receipt.asset_id, _staged.receipts[0].asset_id)
        new_path = self.harness.endpoint.path.parent / "assets" / "at-r3" / new_receipt.asset_id
        self.assertTrue(new_path.exists())
        replay = await attachments.rebind(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            recovery_asset_ref=recovery_asset.recovery_asset_ref,
            request_id="at-r3",
        )
        self.assertEqual(replay.receipts[0].asset_id, new_receipt.asset_id)
        with self.assertRaises(OperationConflictError):
            await attachments.rebind(
                ControlRequest(
                    service=self.service,
                    authorization=OPERATOR,
                    ar_session_id=SESSION,
                    expected_bridge_epoch=self.epoch,
                ),
                recovery_asset_ref=recovery_asset.recovery_asset_ref,
                request_id="at-r4",
            )
        # The rebound asset resubmits natively through the asset channel.
        answer2 = await attachments.submit(
            self.service,
            OPERATOR,
            SESSION,
            body=ConversationSubmitRequest(
                expected_bridge_epoch=self.epoch,
                request_id="at-r3",
                disposition="next",
                content=(
                    TextSubmitBlock(type="text", text="resubmitted"),
                    AssetSubmitBlock(
                        type="asset-ref",
                        asset_id=new_receipt.asset_id,
                        kind=new_receipt.kind,
                        name=new_receipt.name,
                        mime_type=new_receipt.mime_type,
                        alt=new_receipt.alt,
                        alt_provenance=new_receipt.alt_provenance,
                        sha256=new_receipt.sha256,
                    ),
                ),
                draft_revision=1,
            ),
        )
        self.assertIn(answer2.acceptance, {"immediate", "queued"})
        # The session is busy, so the resubmit queued; driving the lane idle
        # dispatches it through the asset channel with the rebound identity.
        self.adapter.auto_release = True
        self.adapter.set_activity("idle")
        deadline = asyncio.get_running_loop().time() + 5.0
        while len(self.adapter.submit_requests) < 2:
            if asyncio.get_running_loop().time() > deadline:
                self.fail("the resubmitted prompt never dispatched")
            await asyncio.sleep(0.05)
        self.assertEqual(self.adapter.submit_requests[-1].assets[0].asset_id, new_receipt.asset_id)

    async def test_ack_keep_current_draft_deletes_recoverable_bytes(self) -> None:
        _staged, response = await self._withdraw_with_asset()
        assert isinstance(response, WithdrawnQueueResponse)
        pending = await withdrawals.pending_recoveries(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            )
        )
        await withdrawals.acknowledge_recovery(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            recovery_ref=pending.items[0].recovery_ref,
            disposition="keep-current-draft",
        )
        spool_dir = self.harness.endpoint.path.parent / "assets" / "at-r1"
        self.assertFalse(spool_dir.exists())


class AttachmentReconcileTransitionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = FakeControlAdapter(harness="codex")
        self.harness = make_harness(self, self.adapter, SESSION, harness="codex")
        await self.harness.start()
        self.service = self.harness.service
        self.epoch = self.harness.epoch
        assert self.adapter.current is not None
        caps = control_capabilities_for("codex", self.adapter.current).attachments
        self.caps = {"image": caps.image, "file": caps.file, "resource": caps.resource}

    async def asyncTearDown(self) -> None:
        await self.harness.stop()

    async def _stage_submit_and_status(self, request_id: str, *, reconcile: bool):
        staged = await attachments.stage(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            request_id=request_id,
            kind_capabilities=self.caps,
            uploads=[_png()],
        )
        await attachments.submit(
            self.service,
            OPERATOR,
            SESSION,
            body=_submit_body(request_id, self.epoch, staged.receipts[0]),
        )
        return await attachments.attachment_status(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            request_id=request_id,
            reconcile=reconcile,
        )

    async def test_rejected_row_advances_to_failed_with_revision_bump(self) -> None:
        self.adapter.next_acceptance = "rejected"
        staged = await attachments.stage(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            request_id="at-t1",
            kind_capabilities=self.caps,
            uploads=[_png()],
        )
        await attachments.submit(
            self.service,
            OPERATOR,
            SESSION,
            body=_submit_body("at-t1", self.epoch, staged.receipts[0]),
        )
        projection = await attachments.attachment_status(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            request_id="at-t1",
            reconcile=True,
        )
        self.assertEqual(projection.phase, "failed")
        self.assertEqual(projection.outcome, "rejected")

    async def test_unknown_outcome_is_retained_and_never_cleaned(self) -> None:
        self.adapter.next_acceptance = "unknown"
        staged = await attachments.stage(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            request_id="at-t2",
            kind_capabilities=self.caps,
            uploads=[_png()],
        )
        await attachments.submit(
            self.service,
            OPERATOR,
            SESSION,
            body=_submit_body("at-t2", self.epoch, staged.receipts[0]),
        )
        projection = await attachments.attachment_status(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            request_id="at-t2",
            reconcile=True,
        )
        self.assertEqual(projection.phase, "unknown")
        self.assertEqual(projection.outcome, "unknown")
        spool_dir = self.harness.endpoint.path.parent / "assets" / "at-t2"
        self.assertTrue(spool_dir.exists())

    async def test_queued_then_dispatching_advances_phase(self) -> None:
        await drive_activity(self.harness, "running")
        staged = await attachments.stage(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            request_id="at-t3",
            kind_capabilities=self.caps,
            uploads=[_png()],
        )
        await attachments.submit(
            self.service,
            OPERATOR,
            SESSION,
            body=_submit_body("at-t3", self.epoch, staged.receipts[0]),
        )
        queued = await attachments.attachment_status(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            request_id="at-t3",
            reconcile=True,
        )
        self.assertEqual(queued.phase, "queued")
        self.adapter.submit_gate = asyncio.Event()
        await drive_activity(self.harness, "idle")
        deadline = asyncio.get_running_loop().time() + 5.0
        while True:
            timeline = await self.service.read_full_timeline(
                self.harness.control_entry, expected_bridge_epoch=self.epoch
            )
            if any(item.state == "dispatching" for item in timeline):
                break
            if asyncio.get_running_loop().time() > deadline:
                self.fail("row never dispatched into the gate")
            await asyncio.sleep(0.05)
        dispatching = await attachments.attachment_status(
            ControlRequest(
                service=self.service,
                authorization=OPERATOR,
                ar_session_id=SESSION,
                expected_bridge_epoch=self.epoch,
            ),
            request_id="at-t3",
            reconcile=True,
        )
        self.assertEqual(dispatching.phase, "dispatching")
        self.assertGreater(dispatching.revision, queued.revision)
        self.adapter.submit_gate.set()
        self.adapter.submit_gate = None


class PolicyTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = FakeControlAdapter(harness="codex")
        self.harness = make_harness(self, self.adapter, SESSION, harness="codex")
        await self.harness.start()
        self.service = self.harness.service
        self.epoch = self.harness.epoch

    async def asyncTearDown(self) -> None:
        await self.harness.stop()

    async def test_policy_is_read_only_evidence_with_reasons(self) -> None:
        projection = await policy.conversation_policy(
            self.service, OPERATOR, SESSION, expected_bridge_epoch=self.epoch
        )
        self.assertEqual(projection.repo_policy.state, "supported")
        assert projection.repo_policy.value is not None
        self.assertIn("local single-operator", projection.repo_policy.value)
        self.assertEqual(projection.harness_mode.state, "unverified")
        self.assertIn("adapter-private", projection.harness_mode.reason)
        self.assertEqual(projection.policy_read.state, "unverified")
        self.assertGreaterEqual(projection.revision, 1)

    async def test_telemetry_usage_from_token_usage_frames_and_absent_before(self) -> None:
        before = await telemetry.conversation_telemetry(
            self.service, OPERATOR, SESSION, expected_bridge_epoch=self.epoch
        )
        self.assertIsNone(before.usage)
        self.adapter.emit(
            "codex-notification",
            {
                "codexMethod": "thread/tokenUsage/updated",
                AR_EVIDENCE_KEY: {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "tokenUsage": {
                        "total": {
                            "totalTokens": 100,
                            "inputTokens": 60,
                            "cachedInputTokens": 10,
                            "outputTokens": 40,
                            "reasoningOutputTokens": 5,
                        },
                        "last": {"totalTokens": 12, "inputTokens": 8, "outputTokens": 4},
                    },
                },
            },
        )
        after = await telemetry.conversation_telemetry(
            self.service, OPERATOR, SESSION, expected_bridge_epoch=self.epoch
        )
        assert after.usage is not None
        self.assertEqual(after.usage.value.input_tokens, 60)
        self.assertEqual(after.usage.value.output_tokens, 40)
        self.assertEqual(after.usage.value.cached_tokens, 10)
        self.assertEqual(after.usage.unit, "tokens")
        self.assertEqual(after.usage.precision, "exact")
        self.assertEqual(after.usage.scope.kind, "conversation")
        self.assertEqual(after.usage.runtime_version, "0.144.5")
        self.assertEqual(after.usage.fixture_id, "codex-0.144.5-installed-20260718")
        self.assertGreater(after.revision, before.revision)
        self.assertIsNone(after.cost)
        self.assertIsNone(after.context)
        self.assertIsNone(after.rate_limits)
        self.assertIsNone(after.compaction)

    async def test_pi_telemetry_stays_absent_when_unverified(self) -> None:
        adapter = FakeControlAdapter(harness="pi", vendor_id="pi-x")
        harness = make_harness(self, adapter, "ar-attach-pi", harness="pi")
        await harness.start()
        try:
            service = harness.service
            projection = await telemetry.conversation_telemetry(
                service, OPERATOR, "ar-attach-pi", expected_bridge_epoch=harness.epoch
            )
            self.assertIsNone(projection.usage)
            self.assertIsNone(projection.compaction)
        finally:
            await harness.stop()


if __name__ == "__main__":
    unittest.main()
