"""Native-page projection keeps transport identity when payload parsing cannot."""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from agents_remember.serving.conversation.models import ConversationItem, UnknownVendorBlock
from agents_remember.serving.harness_control_models import NativeEvidenceFrame
from test_conversation_active_service import NOW, _projector, _ScriptedBridge


def _native_frame(
    native_id: str,
    *,
    harness_type: str,
    raw: dict[str, object],
    parent: str | None,
) -> NativeEvidenceFrame:
    return NativeEvidenceFrame(
        native_id=native_id,
        native_parent_id=parent,
        native_type=harness_type,
        created_at=NOW,
        raw=raw,
    )


def _unknown_block(item: ConversationItem) -> UnknownVendorBlock:
    (block,) = item.blocks
    assert isinstance(block, UnknownVendorBlock)
    return block


async def _project_items(bridge: _ScriptedBridge, *, harness: str) -> tuple[ConversationItem, ...]:
    projector = _projector(bridge, harness=harness)
    try:
        with mock.patch(
            "agents_remember.serving.conversation.active.projector.native_ingestion.asyncio.to_thread",
            new=mock.AsyncMock(side_effect=lambda func, *args, **kwargs: func(*args, **kwargs)),
        ):
            return (await projector.page(before_ordinal=None, limit=50)).items
    finally:
        await projector.close()
        await asyncio.sleep(0)


class NativeFrameIdentityFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_codex_truncation_envelope_preserves_native_item_and_parent_ids(self) -> None:
        bridge = _ScriptedBridge(harness="codex")
        bridge.native_frames.append(
            _native_frame(
                "mcp-call-17",
                harness_type="mcpToolCall",
                parent="turn-9",
                raw={
                    "arEvidenceTruncated": True,
                    "originalBytes": 318_975,
                    "preview": '{"id":"mcp-call-17","type":"mcpToolCall"…[truncated]',
                },
            )
        )

        (item,) = await _project_items(bridge, harness="codex")
        self.assertEqual(item.item_id, "mcp-call-17")
        self.assertEqual(item.turn_id, "turn-9")
        block = _unknown_block(item)
        self.assertEqual(block.vendor_type, "codex:evidence-truncated")
        self.assertEqual(
            block.safe_summary,
            "oversized mcpToolCall native frame clipped to the evidence budget (318975 bytes)",
        )

    async def test_schema_failure_preserves_codex_native_item_identity(self) -> None:
        bridge = _ScriptedBridge(harness="codex")
        bridge.native_frames.append(
            _native_frame(
                "agent-message-4",
                harness_type="agentMessage",
                parent="turn-3",
                raw={"text": "the substrate retained identity outside this malformed body"},
            )
        )

        (item,) = await _project_items(bridge, harness="codex")
        self.assertEqual((item.item_id, item.turn_id), ("agent-message-4", "turn-3"))
        self.assertEqual(_unknown_block(item).vendor_type, "codex:malformed")

    async def test_pi_eager_native_continuation_uses_the_same_identity_fallback(self) -> None:
        bridge = _ScriptedBridge(harness="pi")
        bridge.native_frames.append(
            _native_frame(
                "pi-entry-8",
                harness_type="message",
                parent="pi-entry-7",
                raw={
                    "arEvidenceTruncated": True,
                    "originalBytes": 99_001,
                    "preview": '{"id":"pi-entry-8","type":"message"…[truncated]',
                },
            )
        )

        (item,) = await _project_items(bridge, harness="pi")
        self.assertEqual((item.item_id, item.turn_id), ("pi-entry-8", "pi-entry-7"))
        self.assertEqual(_unknown_block(item).vendor_type, "pi:evidence-truncated")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
