"""Decisions the conversation control and projector suites only ever reach on the happy path.

Each test names one branch the production code takes and asserts the value it produces: the
unstamped Claude result classification the interrupt ledger falls back to when the adapter never
attributed an outcome, the interrupt-status tuple guard, a pi interrupt read while the operation's
delivery is still uncertified, the four typed refusals of the attachment multipart parser, the
active-identity proof gate, and the codex/claude/pi mapper shapes (a patch update, an item-scoped
note, a content-indexed delta, a plan item, an unowned item type, a description-less roster
notification, an errored pi turn) that no existing frame in the suite carries.
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from _control_plane import OPERATOR, FakeControlAdapter, make_harness
from agents_remember.serving.conversation.active.factories import (
    UnsupportedSessionError,
    build_identity,
)
from agents_remember.serving.conversation.control import api, attachments, operations
from agents_remember.serving.conversation.control.service import (
    ControlRequest,
    OperationConflictError,
    OperationRejectedError,
)
from agents_remember.serving.conversation.models import (
    ConversationSubmitRequest,
    TextSubmitBlock,
)
from agents_remember.serving.conversation.projectors import claude, codex, pi
from agents_remember.serving.conversation.projectors.common import (
    MappedBlockDelta,
    MappedItem,
    MappedTurnOutcome,
    MappedUnknownVendor,
)
from agents_remember.serving.harness_control_models import (
    AR_TERMINAL_OUTCOME_KEY,
    AdapterSnapshot,
    ControlIdentity,
    EvidenceFrame,
    NativeEvidenceFrame,
)
from agents_remember.serving.terminal_catalog import TerminalCatalogEntry
from fastapi import UploadFile
from starlette.datastructures import Headers

NOW = "2026-07-31T09:00:00+00:00"
REF = "ar-ev:epoch-1:1"
PARENT = "thread-parent"


def _evidence(kind: str, raw: dict, *, sequence: int = 1, method: str | None = None):
    return EvidenceFrame(
        sequence=sequence, kind=kind, created_at=NOW, raw=raw, native_method=method
    )


def _items(outputs: list) -> list:
    return [output.item for output in outputs if isinstance(output, MappedItem)]


# --------------------------------------------------------------------------------------
# control/operations.py :: _claude_result_settlement (the unstamped fallback)
# --------------------------------------------------------------------------------------


class ClaudeResultSettlementFallbackTests(unittest.TestCase):
    """A Claude result frame the adapter never classified is read from its native shape.

    The stamp is the accepted-interrupt correlation, so a frame without one may only be read
    from what the native fields themselves prove -- and an error-shaped result with no proven
    cancellation keeps its failed meaning rather than being generously called an interrupt.
    """

    @staticmethod
    def _settle(raw: dict) -> tuple[str, str]:
        return operations._claude_result_settlement(_evidence("completed", raw))

    def test_an_unstamped_clean_success_is_a_natural_completion(self) -> None:
        settlement, detail = self._settle(
            {"type": "result", "subtype": "success", "is_error": False}
        )
        self.assertEqual(settlement, "already-settled")
        self.assertEqual(detail, "the exact Claude turn completed natively before interruption")

    def test_an_unstamped_success_subtype_flagged_as_an_error_is_not_a_completion(self) -> None:
        # Both halves are required: a success subtype carrying is_error True is a failure the
        # vendor mislabelled, and reading it as a completion would settle a turn that never ended.
        settlement, _detail = self._settle(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "terminal_reason": "user_cancelled",
            }
        )
        self.assertEqual(settlement, "interrupted")

    def test_an_unstamped_cancelled_terminal_reason_settles_interrupted(self) -> None:
        settlement, detail = self._settle(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "terminal_reason": "cancelled",
            }
        )
        self.assertEqual(settlement, "interrupted")
        self.assertEqual(detail, "native terminal_reason settled the exact Claude turn")

    def test_an_unstamped_error_shape_keeps_its_failed_meaning(self) -> None:
        # ``aborted_streaming`` is the native shape the adapter stamps as cancelled. Without the
        # stamp there is no interrupt correlation, so it must NOT be read as an interruption.
        settlement, detail = self._settle(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "terminal_reason": "aborted_streaming",
            }
        )
        self.assertEqual(settlement, "failed")
        self.assertEqual(detail, "native result failed the exact Claude turn")

    def test_the_adapter_stamp_outranks_the_native_shape(self) -> None:
        settlement, _detail = self._settle(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                AR_TERMINAL_OUTCOME_KEY: "cancelled",
            }
        )
        self.assertEqual(settlement, "interrupted")


# --------------------------------------------------------------------------------------
# control/operations.py :: interrupt_status tuple guard + pi settlement abstention
# --------------------------------------------------------------------------------------


def _request(service, session: str, epoch: str) -> ControlRequest:
    return ControlRequest(
        service=service,
        authorization=OPERATOR,
        ar_session_id=session,
        expected_bridge_epoch=epoch,
    )


async def _submit(service, session: str, request_id: str, epoch: str) -> attachments.SubmitAnswer:
    return await attachments.submit(
        service,
        OPERATOR,
        session,
        body=ConversationSubmitRequest(
            expected_bridge_epoch=epoch,
            request_id=request_id,
            disposition="next",
            content=(TextSubmitBlock(type="text", text="work"),),
            draft_revision=1,
        ),
    )


class InterruptStatusTupleGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = FakeControlAdapter(harness="codex")
        self.harness = make_harness(self, self.adapter, "ar-ops-guard", harness="codex")
        await self.harness.start()
        self.service = self.harness.service
        self.epoch = self.harness.epoch

    async def asyncTearDown(self) -> None:
        await self.harness.stop()

    async def test_reading_a_retained_request_id_under_another_turn_is_a_conflict(self) -> None:
        # The request id is only idempotent for the tuple that minted it; a status read that
        # names a different turn is a second operation wearing the first one's id.
        await _submit(self.service, "ar-ops-guard", "req-guard-1", self.epoch)
        self.adapter.set_activity("running")
        await operations.interrupt(
            _request(self.service, "ar-ops-guard", self.epoch),
            turn_id="turn-req-guard-1",
            request_id="int-guard-1",
        )
        with self.assertRaises(OperationConflictError):
            await operations.interrupt_status(
                _request(self.service, "ar-ops-guard", self.epoch),
                turn_id="turn-someone-else",
                request_id="int-guard-1",
                reconcile=False,
            )
        # The retained operation is untouched: its own tuple still reads.
        same = await operations.interrupt_status(
            _request(self.service, "ar-ops-guard", self.epoch),
            turn_id="turn-req-guard-1",
            request_id="int-guard-1",
            reconcile=False,
        )
        self.assertEqual(same.request_id, "int-guard-1")


class PiUncertifiedOperationSettlementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = FakeControlAdapter(harness="pi", vendor_id="pi-session-edge")
        self.harness = make_harness(self, self.adapter, "ar-ops-pi-edge", harness="pi")
        await self.harness.start()
        self.service = self.harness.service
        self.epoch = self.harness.epoch

    async def asyncTearDown(self) -> None:
        await self.harness.stop()

    async def test_an_interrupt_on_an_uncertified_operation_stays_pending(self) -> None:
        # The adapter could not prove the prompt was accepted, so the authority holds the row in
        # its ambiguity state. An operation whose delivery is not certified cannot have settled,
        # so the ledger must abstain rather than mint a terminal outcome from the stopReason
        # evidence that a *later* turn might carry.
        self.adapter.next_acceptance = "unknown"
        receipt = await _submit(self.service, "ar-ops-pi-edge", "req-pi-edge", self.epoch)
        self.assertEqual(receipt.acceptance, "unknown")
        self.adapter.set_activity("running")
        operation = await operations.interrupt(
            _request(self.service, "ar-ops-pi-edge", self.epoch),
            turn_id="req-pi-edge",
            request_id="int-pi-edge",
        )
        self.assertEqual(operation.acknowledgement, "accepted")
        self.assertEqual(operation.settlement, "pending")
        status = await operations.interrupt_status(
            _request(self.service, "ar-ops-pi-edge", self.epoch),
            turn_id="req-pi-edge",
            request_id="int-pi-edge",
            reconcile=False,
        )
        self.assertEqual(status.settlement, "pending")
        self.assertIsNone(status.settled_at)


# --------------------------------------------------------------------------------------
# control/api.py :: the attachment multipart parser's typed refusals
# --------------------------------------------------------------------------------------


def _upload(name: str = "dot.png", *, content_type: str = "image/png") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(b"\x89PNG"),
        filename=name,
        headers=Headers({"content-type": content_type}),
        size=4,
    )


class AttachmentFormParsingTests(unittest.IsolatedAsyncioTestCase):
    """The multipart body is refused before any byte is spooled, and says which part was wrong."""

    async def test_metadata_that_is_not_json_is_refused(self) -> None:
        with self.assertRaises(OperationRejectedError) as caught:
            api._parse_metadata_array("{not json")
        self.assertEqual(str(caught.exception), "attachment metadata must be a JSON array")

    async def test_metadata_that_is_not_an_array_of_objects_is_refused(self) -> None:
        for payload in ('{"kind": "image"}', '["image"]', "[[]]"):
            with self.subTest(payload=payload), self.assertRaises(OperationRejectedError) as caught:
                api._parse_metadata_array(payload)
            self.assertEqual(
                str(caught.exception), "attachment metadata must be a JSON array of objects"
            )

    async def test_absent_metadata_is_an_empty_array_rather_than_a_refusal(self) -> None:
        self.assertEqual(api._parse_metadata_array(None), [])
        self.assertEqual(api._parse_metadata_array("[]"), [])

    async def test_metadata_that_does_not_describe_every_file_is_refused(self) -> None:
        # Metadata is matched positionally against the uploads, so a partial array would silently
        # attach one file's declared kind and alt text to a different file.
        with self.assertRaises(OperationRejectedError) as caught:
            await api._parse_uploads([_upload()], '[{"kind": "image"}, {"kind": "file"}]')
        self.assertEqual(
            str(caught.exception), "attachment metadata must describe every uploaded file"
        )

    async def test_metadata_for_no_uploads_at_all_is_refused(self) -> None:
        # The other side of the same count check: metadata describing a file that was never
        # sent is refused rather than dropped, so a lost multipart part cannot pass silently.
        with self.assertRaises(OperationRejectedError) as caught:
            await api._parse_uploads([], '[{"kind": "image"}]')
        self.assertEqual(
            str(caught.exception), "attachment metadata must describe every uploaded file"
        )

    async def test_an_asset_without_a_declared_kind_is_refused(self) -> None:
        for meta in ({}, {"kind": "video"}, {"kind": None}):
            with self.subTest(meta=meta), self.assertRaises(OperationRejectedError) as caught:
                await api._upload_for(_upload(), dict(meta), position=0)
            self.assertEqual(
                str(caught.exception), "each staged asset requires its attachment kind"
            )

    async def test_an_empty_alt_string_is_refused_rather_than_stored(self) -> None:
        with self.assertRaises(OperationRejectedError) as caught:
            await api._upload_for(_upload(), {"kind": "image", "alt": ""}, position=0)
        self.assertEqual(str(caught.exception), "asset alt text must be non-empty text")

    async def test_a_fully_declared_upload_parses_into_a_staged_upload(self) -> None:
        staged = await api._parse_uploads(
            [_upload("photo.png")], '[{"kind": "image", "alt": "a dot"}]'
        )
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0].kind, "image")
        self.assertEqual(staged[0].name, "photo.png")
        self.assertEqual(staged[0].alt, "a dot")
        # The MIME type is the multipart part's own header, never the caller's metadata.
        self.assertEqual(staged[0].mime_type, "image/png")
        self.assertEqual(staged[0].data, b"\x89PNG")

    async def test_an_unnamed_upload_falls_back_to_a_positional_name(self) -> None:
        staged = await api._upload_for(
            UploadFile(file=io.BytesIO(b"x"), filename=None, headers=Headers({}), size=1),
            {"kind": "file"},
            position=2,
        )
        self.assertEqual(staged.name, "asset-3")
        self.assertEqual(staged.mime_type, "")


# --------------------------------------------------------------------------------------
# active/factories.py :: the native-identity proof gate
# --------------------------------------------------------------------------------------


def _entry(harness: str) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id="ar-identity-1",
        label="identity",
        kind="harness",
        harness=harness,
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name="tmux-ar-identity-1",
        command=("fake",),
        created_at=NOW,
        last_attached_at=NOW,
        status="running",
        control_endpoint=Path("/tmp/ar-identity-1.sock"),
    )


def _snapshot(vendor_session_id: str | None) -> AdapterSnapshot:
    return AdapterSnapshot(
        identity=ControlIdentity(
            ar_session_id="ar-identity-1", tmux_name="tmux-ar-identity-1", created_at=NOW
        ),
        control="ready",
        activity="idle",
        acceptance="immediate",
        vendor_session_id=vendor_session_id,
    )


class ActiveIdentityProofTests(unittest.TestCase):
    def test_a_session_with_no_native_conversation_id_is_refused(self) -> None:
        # A running seat whose harness has not yet reported a native session id has nothing to
        # project: the identity is what every evidence ref and cursor is bound to, so it is
        # refused rather than minted from the AR session id.
        with self.assertRaises(UnsupportedSessionError) as caught:
            build_identity(
                _entry("codex"), secret=b"s" * 32, bridge_epoch="epoch-1", snapshot=_snapshot(None)
            )
        self.assertEqual(
            str(caught.exception), "session has no proven native conversation identity yet"
        )

    def test_an_empty_native_conversation_id_is_refused_the_same_way(self) -> None:
        # "The same way" is the claim: an empty string is absence, not a usable id, so it must
        # not fall through to a different error (or, worse, mint an identity bound to "").
        with self.assertRaises(UnsupportedSessionError) as caught:
            build_identity(
                _entry("codex"), secret=b"s" * 32, bridge_epoch="epoch-1", snapshot=_snapshot("")
            )
        self.assertEqual(
            str(caught.exception), "session has no proven native conversation identity yet"
        )

    def test_a_proven_native_id_yields_the_bound_identity(self) -> None:
        identity, mapper = build_identity(
            _entry("codex"),
            secret=b"s" * 32,
            bridge_epoch="epoch-1",
            snapshot=_snapshot("thread-9"),
        )
        self.assertEqual(identity.vendor_conversation_id, "thread-9")
        self.assertEqual(identity.harness_id, "codex")
        self.assertEqual(identity.bridge_epoch, "epoch-1")
        self.assertTrue(identity.identity_digest.startswith("sha256:"))
        self.assertEqual(mapper.harness_id, "codex")


# --------------------------------------------------------------------------------------
# projectors/codex.py :: item-scoped notifications, indexed deltas, unrouted item types
# --------------------------------------------------------------------------------------


def _codex_notification(raw: dict, *, method: str) -> list:
    return codex.map_evidence_frame(
        _evidence("codex-notification", raw, method=method),
        evidence_ref=REF,
        parent_thread_id=PARENT,
    )


def _codex_item(item: dict) -> list:
    return codex.map_evidence_frame(
        _evidence("transcript", {"turnId": "turn-1", "item": item}),
        evidence_ref=REF,
        parent_thread_id=PARENT,
    )


class CodexItemScopedNotificationTests(unittest.TestCase):
    def test_a_patch_update_replaces_the_file_change_row_while_it_is_still_running(self) -> None:
        outputs = _codex_notification(
            {
                "itemId": "item-patch-1",
                "turnId": "turn-1",
                "changes": [{"path": "/repo/a.py", "diff": "@@ -1 +1 @@"}],
            },
            method="item/fileChange/patchUpdated",
        )
        (item,) = _items(outputs)
        self.assertEqual(item.item_id, "item-patch-1")
        self.assertEqual(item.turn_id, "turn-1")
        self.assertEqual(item.kind, "tool-call")
        # ``inProgress`` is the whole point of the patch update: the edit is not finished.
        self.assertEqual(item.phase, "streaming")
        self.assertEqual(item.source, "harness-live")
        (block,) = item.blocks
        self.assertEqual(block.path, "/repo/a.py")
        self.assertEqual(block.unified, "@@ -1 +1 @@")

    def test_an_item_scoped_note_with_no_body_mints_nothing(self) -> None:
        # MCP progress notes and terminal interaction name an item they do not carry; they
        # resolve on completion, so minting a row here would create an item from no content.
        self.assertEqual(
            _codex_notification(
                {"itemId": "item-mcp-1", "progress": 0.5},
                method="item/mcpToolCall/progress",
            ),
            [],
        )

    def test_a_content_indexed_delta_names_its_own_block(self) -> None:
        (delta,) = _codex_notification(
            {"itemId": "item-msg-1", "delta": "world", "contentIndex": 2},
            method="item/agentMessage/delta",
        )
        assert isinstance(delta, MappedBlockDelta)
        self.assertEqual(delta.item_id, "item-msg-1")
        self.assertEqual(delta.block_id, "content-2")
        self.assertEqual(delta.delta, "world")

    def test_a_summary_indexed_delta_names_the_summary_block_instead(self) -> None:
        (delta,) = _codex_notification(
            {"itemId": "item-reason-1", "delta": "thinking", "summaryIndex": 0},
            method="item/reasoning/delta",
        )
        assert isinstance(delta, MappedBlockDelta)
        self.assertEqual(delta.block_id, "summary-0")

    def test_a_bare_delta_leaves_the_target_block_for_the_engine(self) -> None:
        (delta,) = _codex_notification(
            {"itemId": "item-msg-2", "delta": "hi"},
            method="item/agentMessage/delta",
        )
        assert isinstance(delta, MappedBlockDelta)
        self.assertEqual(delta.block_id, "")


class CodexThreadItemRoutingTests(unittest.TestCase):
    def test_a_plan_item_is_assistant_markdown_kept_apart_from_a_message(self) -> None:
        outputs = _codex_item({"id": "item-plan-1", "type": "plan", "text": "1. read\n2. write"})
        (item,) = _items(outputs)
        self.assertEqual(item.item_id, "item-plan-1")
        self.assertEqual(item.kind, "plan")
        self.assertEqual(item.role, "assistant")
        (block,) = item.blocks
        self.assertEqual(block.markdown, "1. read\n2. write")

    def test_an_item_type_no_router_owns_is_preserved_rather_than_guessed(self) -> None:
        (output,) = _codex_item({"id": "item-web-1", "type": "webSearch", "query": "ruff"})
        assert isinstance(output, MappedUnknownVendor)
        self.assertEqual(output.item_id, "item-web-1")
        self.assertEqual(output.vendor_type, "codex:webSearch")
        self.assertEqual(output.safe_summary, "codex item of type webSearch")

    def test_a_native_page_item_of_an_unowned_type_is_preserved_as_history(self) -> None:
        (output,) = codex.map_native_frame(
            NativeEvidenceFrame(
                native_id="item-web-2",
                native_parent_id="turn-9",
                native_type="webSearch",
                created_at=NOW,
                raw={"id": "item-web-2", "type": "webSearch"},
            ),
            evidence_ref=REF,
        )
        assert isinstance(output, MappedUnknownVendor)
        self.assertFalse(output.live)
        self.assertEqual(output.turn_id, "turn-9")


# --------------------------------------------------------------------------------------
# projectors/claude.py :: the sparse task_notification roster row
# --------------------------------------------------------------------------------------


class ClaudeSparseTaskNotificationTests(unittest.TestCase):
    def test_a_notification_with_only_telemetry_emits_just_the_usage_block(self) -> None:
        # A task_notification carries neither description nor summary, and its usage half is
        # independent of its last-tool half. The roster row must therefore emit exactly the
        # evidence the frame carried -- no empty description block, no null usage key.
        outputs = claude.map_evidence_frame(
            _evidence(
                "transcript",
                {
                    "type": "system",
                    "subtype": "task_notification",
                    "session_id": "claude-sparse-1",
                    "task_id": "task-sparse-1",
                    "status": "completed",
                    "last_tool_name": "Bash",
                },
            ),
            evidence_ref=REF,
        )
        (item,) = _items(outputs)
        self.assertEqual(item.item_id, "claude-agent-task-sparse-1")
        self.assertEqual(item.phase, "completed")
        assert item.agent is not None
        self.assertEqual(item.agent.agent_id, "task-sparse-1")
        self.assertEqual(item.agent.status, "completed")
        # Nothing named the join key or the sub-agent role, so neither is invented.
        self.assertIsNone(item.agent.join_key)
        self.assertIsNone(item.agent.role)
        (block,) = item.blocks
        self.assertEqual(block.block_id, "usage")
        self.assertEqual(block.data, {"lastToolName": "Bash"})

    def test_a_notification_with_neither_telemetry_half_emits_no_blocks(self) -> None:
        outputs = claude.map_evidence_frame(
            _evidence(
                "transcript",
                {
                    "type": "system",
                    "subtype": "task_notification",
                    "session_id": "claude-sparse-2",
                    "task_id": "task-sparse-2",
                    "status": "stopped",
                },
            ),
            evidence_ref=REF,
        )
        (item,) = _items(outputs)
        self.assertEqual(item.blocks, ())
        assert item.agent is not None
        self.assertEqual(item.agent.status, "interrupted")


# --------------------------------------------------------------------------------------
# projectors/pi.py :: assistant stop reasons outside the completed/aborted pair
# --------------------------------------------------------------------------------------


def _pi_assistant(message: dict) -> list:
    return pi.map_native_frame(
        NativeEvidenceFrame(
            native_id="entry-pi-1",
            native_parent_id=None,
            native_type="message",
            created_at=NOW,
            raw={"id": "entry-pi-1", "type": "message", "message": message},
        ),
        evidence_ref=REF,
    )


class PiAssistantStopReasonTests(unittest.TestCase):
    def test_an_errored_turn_mints_a_failed_turn_result_carrying_the_error_message(self) -> None:
        outputs = _pi_assistant(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "partial"}],
                "stopReason": "error",
                "errorMessage": "provider refused the request",
                "timestamp": 4,
            }
        )
        message = next(item for item in _items(outputs) if item.item_id == "entry-pi-1")
        self.assertEqual(message.phase, "failed")
        result = next(item for item in _items(outputs) if item.item_id == "turn-result:entry-pi-1")
        self.assertEqual(result.kind, "turn-result")
        self.assertEqual(result.phase, "failed")
        (outcome,) = [output for output in outputs if isinstance(output, MappedTurnOutcome)]
        self.assertEqual(outcome.outcome, "failed")
        # The vendor's own words are what the stop reason carries, not the bare "error" word.
        self.assertEqual(outcome.stop_reason, "provider refused the request")

    def test_an_errored_turn_without_a_message_falls_back_to_the_stop_reason(self) -> None:
        outputs = _pi_assistant(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "partial"}],
                "stopReason": "error",
                "timestamp": 5,
            }
        )
        (outcome,) = [output for output in outputs if isinstance(output, MappedTurnOutcome)]
        self.assertEqual(outcome.stop_reason, "error")

    def test_a_message_with_no_stop_reason_settles_nothing(self) -> None:
        # A streamed message that has not reported why it stopped is not evidence that the turn
        # ended; mapping it must produce the content and no terminal outcome at all.
        outputs = _pi_assistant(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "still going"}],
                "timestamp": 6,
            }
        )
        (item,) = _items(outputs)
        self.assertEqual(item.item_id, "entry-pi-1")
        self.assertEqual(item.phase, "completed")
        self.assertEqual([o for o in outputs if isinstance(o, MappedTurnOutcome)], [])

    def test_an_undocumented_stop_reason_settles_nothing_either(self) -> None:
        outputs = _pi_assistant(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "hm"}],
                "stopReason": "refusal",
                "timestamp": 7,
            }
        )
        self.assertEqual([o for o in outputs if isinstance(o, MappedTurnOutcome)], [])
        self.assertEqual(len(_items(outputs)), 1)


if __name__ == "__main__":
    unittest.main()
