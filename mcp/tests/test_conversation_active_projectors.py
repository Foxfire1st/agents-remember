"""Per-harness projector frame mappings: stable identity, blocks, tools, provenance."""

from __future__ import annotations

import unittest

from agents_remember.serving.conversation.models import (
    DiffBlock,
    MarkdownBlock,
    TextBlock,
    ThinkingBlock,
    ToolInputBlock,
    ToolOutputBlock,
)
from agents_remember.serving.conversation.projectors import claude, codex, pi
from agents_remember.serving.conversation.projectors.common import (
    MappedBlockDelta,
    MappedItem,
    MappedTurnOutcome,
    MappedUnknownVendor,
)
from agents_remember.serving.harness_control_models import (
    EvidenceFrame,
    NativeEvidenceFrame,
)

NOW = "2026-07-19T08:00:00+00:00"
REF = "ar-ev:epoch:1"


def _native(native_id: str, native_type: str, raw: dict, parent: str | None = None) -> NativeEvidenceFrame:
    return NativeEvidenceFrame(
        native_id=native_id,
        native_parent_id=parent,
        native_type=native_type,
        created_at=NOW,
        raw=raw,
    )


def _evidence(sequence: int, kind: str, raw: dict) -> EvidenceFrame:
    return EvidenceFrame(sequence=sequence, kind=kind, created_at=NOW, raw=raw)


def _items(outputs: list) -> list:
    return [output.item for output in outputs if isinstance(output, MappedItem)]


class CodexMapperTests(unittest.TestCase):
    def test_native_user_message_unknown_input_with_client_correlation(self) -> None:
        outputs = codex.map_native_frame(
            _native(
                "item-1",
                "userMessage",
                {
                    "id": "item-1",
                    "type": "userMessage",
                    "clientId": "req-1",
                    "content": [{"type": "text", "text": "hello"}],
                },
                parent="turn-1",
            ),
            evidence_ref=REF,
        )
        (item,) = _items(outputs)
        self.assertEqual(item.item_id, "item-1")
        self.assertEqual(item.turn_id, "turn-1")
        self.assertEqual(item.lane, "unknown-input")
        self.assertEqual(item.source, "native-history")
        self.assertEqual(item.provenance.strength, "native-only")
        self.assertIsNone(item.provenance.producer)
        assert item.correlation is not None
        self.assertEqual(item.correlation.request_id, "req-1")
        self.assertIsInstance(item.blocks[0], TextBlock)

    def test_native_agent_message_is_markdown_assistant(self) -> None:
        outputs = codex.map_native_frame(
            _native("item-2", "agentMessage", {"id": "item-2", "type": "agentMessage", "text": "**hi**"}),
            evidence_ref=REF,
        )
        (item,) = _items(outputs)
        self.assertEqual(item.role, "assistant")
        self.assertEqual(item.kind, "message")
        self.assertEqual(item.source, "native-history")
        self.assertIsInstance(item.blocks[0], MarkdownBlock)
        self.assertEqual(item.blocks[0].markdown, "**hi**")

    def test_native_reasoning_maps_summary_and_content_blocks(self) -> None:
        outputs = codex.map_native_frame(
            _native(
                "item-3",
                "reasoning",
                {"id": "item-3", "type": "reasoning", "summary": ["s0", "s1"], "content": ["c0"]},
            ),
            evidence_ref=REF,
        )
        (item,) = _items(outputs)
        self.assertEqual(item.kind, "thinking")
        block_ids = [block.block_id for block in item.blocks]
        self.assertEqual(block_ids, ["summary-0", "summary-1", "content-0"])
        self.assertTrue(all(isinstance(block, ThinkingBlock) for block in item.blocks))

    def test_native_command_execution_is_tool_call_with_io(self) -> None:
        outputs = codex.map_native_frame(
            _native(
                "item-4",
                "commandExecution",
                {
                    "id": "item-4",
                    "type": "commandExecution",
                    "command": "ls -la",
                    "cwd": "/workspace",
                    "source": "agent",
                    "status": "completed",
                    "aggregatedOutput": "total 0",
                    "exitCode": 0,
                    "durationMs": 5,
                },
            ),
            evidence_ref=REF,
        )
        (item,) = _items(outputs)
        self.assertEqual(item.kind, "tool-call")
        self.assertEqual(item.role, "tool")
        self.assertEqual(item.phase, "completed")
        self.assertIsInstance(item.blocks[0], ToolInputBlock)
        self.assertEqual(item.blocks[0].summary, "ls -la")
        self.assertIsInstance(item.blocks[1], ToolOutputBlock)
        self.assertEqual(item.blocks[1].text, "total 0")

    def test_native_file_change_maps_diff_blocks(self) -> None:
        outputs = codex.map_native_frame(
            _native(
                "item-5",
                "fileChange",
                {
                    "id": "item-5",
                    "type": "fileChange",
                    "status": "completed",
                    "changes": [{"path": "a.py", "kind": {"type": "update", "move_path": None}, "diff": "@@-1+1@@"}],
                },
            ),
            evidence_ref=REF,
        )
        (item,) = _items(outputs)
        self.assertIsInstance(item.blocks[0], DiffBlock)
        self.assertEqual(item.blocks[0].path, "a.py")
        self.assertEqual(item.blocks[0].unified, "@@-1+1@@")

    def test_native_mcp_tool_call(self) -> None:
        outputs = codex.map_native_frame(
            _native(
                "item-6",
                "mcpToolCall",
                {
                    "id": "item-6",
                    "type": "mcpToolCall",
                    "server": "srv",
                    "tool": "search",
                    "status": "inProgress",
                    "arguments": {"q": "x"},
                },
            ),
            evidence_ref=REF,
        )
        (item,) = _items(outputs)
        self.assertEqual(item.phase, "streaming")
        self.assertEqual(item.blocks[0].summary, "srv/search")

    def test_unknown_native_item_type_is_unknown_vendor_with_native_id(self) -> None:
        outputs = codex.map_native_frame(
            _native("item-7", "collabAgentToolCall", {"id": "item-7", "type": "collabAgentToolCall"}),
            evidence_ref=REF,
        )
        (unknown,) = [o for o in outputs if isinstance(o, MappedUnknownVendor)]
        self.assertEqual(unknown.item_id, "item-7")
        self.assertIn("collabAgentToolCall", unknown.vendor_type)

    def test_live_item_started_and_completed(self) -> None:
        started = codex.map_evidence_frame(
            _evidence(
                1,
                "codex-notification",
                {
                    "threadId": "t",
                    "turnId": "turn-1",
                    "startedAtMs": 1,
                    "item": {"id": "item-8", "type": "agentMessage", "text": "partial"},
                },
            ),
            evidence_ref=REF,
        )
        (item,) = _items(started)
        self.assertEqual(item.phase, "streaming")
        self.assertEqual(item.source, "harness-live")
        completed = codex.map_evidence_frame(
            _evidence(
                2,
                "transcript",
                {
                    "threadId": "t",
                    "turnId": "turn-1",
                    "completedAtMs": 2,
                    "item": {"id": "item-8", "type": "agentMessage", "text": "done"},
                },
            ),
            evidence_ref=REF,
        )
        (item2,) = _items(completed)
        self.assertEqual(item2.phase, "completed")

    def test_live_indexed_and_bare_deltas(self) -> None:
        summary = codex.map_evidence_frame(
            _evidence(
                1,
                "codex-notification",
                {"threadId": "t", "turnId": "turn-1", "itemId": "item-3", "delta": "x", "summaryIndex": 1},
            ),
            evidence_ref=REF,
        )
        (delta,) = [o for o in summary if isinstance(o, MappedBlockDelta)]
        self.assertEqual(delta.block_id, "summary-1")
        bare = codex.map_evidence_frame(
            _evidence(
                2,
                "codex-notification",
                {"threadId": "t", "turnId": "turn-1", "itemId": "item-2", "delta": "y"},
            ),
            evidence_ref=REF,
        )
        (delta2,) = [o for o in bare if isinstance(o, MappedBlockDelta)]
        self.assertEqual(delta2.block_id, "")
        self.assertEqual(delta2.delta, "y")

    def test_turn_completed_maps_result_and_outcome(self) -> None:
        outputs = codex.map_evidence_frame(
            _evidence(
                3,
                "completed",
                {"threadId": "t", "turn": {"id": "turn-1", "status": "interrupted", "items": []}},
            ),
            evidence_ref=REF,
        )
        items = _items(outputs)
        outcomes = [o for o in outputs if isinstance(o, MappedTurnOutcome)]
        self.assertEqual(items[0].item_id, "turn-result:turn-1")
        self.assertEqual(items[0].kind, "turn-result")
        self.assertEqual(items[0].phase, "interrupted")
        self.assertEqual(outcomes[0].outcome, "interrupted")

    def test_usage_and_rate_frames_mint_no_items(self) -> None:
        for params in (
            {"threadId": "t", "turnId": "turn-1", "tokenUsage": {"total": {}}},
            {"rateLimits": {"limitId": "x"}},
        ):
            self.assertEqual(
                codex.map_evidence_frame(
                    _evidence(1, "codex-notification", params), evidence_ref=REF
                ),
                [],
            )

    def test_unknown_notification_is_preserved_unknown_vendor(self) -> None:
        outputs = codex.map_evidence_frame(
            _evidence(9, "codex-notification", {"something": "new"}),
            evidence_ref=REF,
        )
        (unknown,) = [o for o in outputs if isinstance(o, MappedUnknownVendor)]
        self.assertIn("codex", unknown.vendor_type)


class ClaudeMapperTests(unittest.TestCase):
    def test_assistant_frame_splits_text_thinking_and_tools(self) -> None:
        outputs = claude.map_evidence_frame(
            _evidence(
                1,
                "state",
                {
                    "type": "assistant",
                    "uuid": "uuid-1",
                    "session_id": "sess-1",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "answer"},
                            {"type": "thinking", "thinking": "hmm"},
                            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}},
                        ],
                    },
                },
            ),
            evidence_ref=REF,
        )
        items = _items(outputs)
        assistant = next(item for item in items if item.item_id == "uuid-1")
        self.assertEqual(
            [type(block) for block in assistant.blocks],
            [MarkdownBlock, ThinkingBlock],
        )
        tool = next(item for item in items if item.item_id == "toolu_1")
        self.assertEqual(tool.kind, "tool-call")
        self.assertEqual(tool.parent_item_id, "uuid-1")
        self.assertEqual(tool.blocks[0].summary, "Bash")
        self.assertEqual(tool.correlation.tool_call_id, "toolu_1")

    def test_tool_result_upserts_same_tool_item(self) -> None:
        outputs = claude.map_evidence_frame(
            _evidence(
                2,
                "state",
                {
                    "type": "user",
                    "uuid": "uuid-2",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": "toolu_1", "content": [{"type": "text", "text": "out"}], "is_error": False}
                        ],
                    },
                },
            ),
            evidence_ref=REF,
        )
        (item,) = _items(outputs)
        self.assertEqual(item.item_id, "toolu_1")
        self.assertEqual(item.phase, "completed")
        self.assertIsInstance(item.blocks[0], ToolOutputBlock)

    def test_result_frame_maps_terminal_outcomes(self) -> None:
        for subtype, is_error, reason, expected in (
            ("success", False, None, "completed"),
            ("error", True, "interrupted", "interrupted"),
            ("error", True, None, "failed"),
        ):
            frame = {
                "type": "result",
                "subtype": subtype,
                "is_error": is_error,
                "uuid": "uuid-r",
                "stop_reason": "end_turn",
            }
            if reason is not None:
                frame["terminal_reason"] = reason
            outputs = claude.map_evidence_frame(
                _evidence(3, "completed", frame), evidence_ref=REF
            )
            outcomes = [o for o in outputs if isinstance(o, MappedTurnOutcome)]
            self.assertEqual(outcomes[0].outcome, expected)
            items = _items(outputs)
            self.assertEqual(items[0].kind, "turn-result")

    def test_transcript_echo_is_exact_submission_user_item(self) -> None:
        outputs = claude.map_transcript_echo(
            {
                "sequence": 1,
                "role": "user",
                "text": "ship it",
                "createdAt": NOW,
                "requestId": "req-9",
                "vendorCorrelationId": "uuid-u",
            },
            evidence_ref=REF,
        )
        (item,) = _items(outputs)
        self.assertEqual(item.item_id, "uuid-u")
        self.assertEqual(item.role, "user")
        self.assertEqual(item.lane, "unknown-input")
        self.assertEqual(item.blocks[0].text, "ship it")
        self.assertEqual(item.correlation.request_id, "req-9")

    def test_system_frames_mint_no_items(self) -> None:
        self.assertEqual(
            claude.map_evidence_frame(
                _evidence(1, "state", {"type": "system", "subtype": "api_retry"}),
                evidence_ref=REF,
            ),
            [],
        )

    def test_unknown_frame_type_preserved(self) -> None:
        outputs = claude.map_evidence_frame(
            _evidence(1, "state", {"type": "brand_new"}),
            evidence_ref=REF,
        )
        (unknown,) = [o for o in outputs if isinstance(o, MappedUnknownVendor)]
        self.assertIn("brand_new", unknown.vendor_type)


class PiMapperTests(unittest.TestCase):
    def test_entry_user_message_is_unknown_input(self) -> None:
        outputs = pi.map_native_frame(
            _native(
                "entry-1",
                "message",
                {
                    "id": "entry-1",
                    "type": "message",
                    "message": {"role": "user", "content": "hello", "timestamp": 1},
                },
            ),
            evidence_ref=REF,
        )
        (item,) = _items(outputs)
        self.assertEqual(item.item_id, "entry-1")
        self.assertEqual(item.lane, "unknown-input")
        self.assertIsNone(item.provenance.producer)
        self.assertEqual(item.source, "native-history")

    def test_entry_assistant_splits_blocks_and_tools(self) -> None:
        outputs = pi.map_native_frame(
            _native(
                "entry-2",
                "message",
                {
                    "id": "entry-2",
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "ok"},
                            {"type": "thinking", "thinking": "plan"},
                            {"type": "toolCall", "id": "tc-1", "name": "bash", "arguments": {"command": "ls"}},
                        ],
                        "stopReason": "toolUse",
                        "timestamp": 2,
                    },
                },
            ),
            evidence_ref=REF,
        )
        items = _items(outputs)
        assistant = next(item for item in items if item.item_id == "entry-2")
        self.assertEqual(
            [type(block) for block in assistant.blocks], [MarkdownBlock, ThinkingBlock]
        )
        tool = next(item for item in items if item.item_id == "tc-1")
        self.assertEqual(tool.correlation.tool_call_id, "tc-1")
        outcomes = [o for o in outputs if isinstance(o, MappedTurnOutcome)]
        self.assertEqual(outcomes[0].outcome, "completed")

    def test_entry_tool_result_converges_by_tool_call_id(self) -> None:
        outputs = pi.map_native_frame(
            _native(
                "entry-3",
                "message",
                {
                    "id": "entry-3",
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "toolCallId": "tc-1",
                        "toolName": "bash",
                        "content": [{"type": "text", "text": "done"}],
                        "isError": False,
                        "timestamp": 3,
                    },
                },
            ),
            evidence_ref=REF,
        )
        (item,) = _items(outputs)
        self.assertEqual(item.item_id, "tc-1")
        self.assertEqual(item.phase, "completed")

    def test_aborted_assistant_mints_turn_result(self) -> None:
        outputs = pi.map_native_frame(
            _native(
                "entry-4",
                "message",
                {
                    "id": "entry-4",
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "partial"}],
                        "stopReason": "aborted",
                        "timestamp": 4,
                    },
                },
            ),
            evidence_ref=REF,
        )
        items = _items(outputs)
        result = next(item for item in items if item.item_id == "turn-result:entry-4")
        self.assertEqual(result.phase, "interrupted")

    def test_compaction_and_model_entries_are_notices(self) -> None:
        compaction = pi.map_native_frame(
            _native(
                "entry-5",
                "compaction",
                {
                    "id": "entry-5",
                    "type": "compaction",
                    "summary": "condensed",
                    "firstKeptEntryId": "entry-2",
                    "tokensBefore": 100,
                },
            ),
            evidence_ref=REF,
        )
        (notice,) = _items(compaction)
        self.assertEqual(notice.kind, "notice")
        self.assertEqual(notice.blocks[0].text, "condensed")
        model = pi.map_native_frame(
            _native(
                "entry-6",
                "model_change",
                {"id": "entry-6", "type": "model_change", "provider": "openai", "modelId": "gpt"},
            ),
            evidence_ref=REF,
        )
        (notice2,) = _items(model)
        self.assertIn("openai/gpt", notice2.blocks[0].text)

    def test_unknown_entry_type_preserved_with_native_id(self) -> None:
        outputs = pi.map_native_frame(
            _native("entry-7", "branch_summary", {"id": "entry-7", "type": "branch_summary", "fromId": "e", "summary": "s"}),
            evidence_ref=REF,
        )
        (unknown,) = [o for o in outputs if isinstance(o, MappedUnknownVendor)]
        self.assertEqual(unknown.item_id, "entry-7")
        self.assertFalse(unknown.live)

    def test_live_tool_execution_upserts_by_tool_call_id(self) -> None:
        start = pi.map_evidence_frame(
            _evidence(1, "pi:tool_execution_start", {"type": "tool_execution_start", "toolCallId": "tc-9", "toolName": "bash", "args": {"command": "ls"}}),
            evidence_ref=REF,
        )
        (item,) = _items(start)
        self.assertEqual(item.item_id, "tc-9")
        self.assertEqual(item.phase, "streaming")
        self.assertIsInstance(item.blocks[0], ToolInputBlock)
        end = pi.map_evidence_frame(
            _evidence(2, "pi:tool_execution_end", {"type": "tool_execution_end", "toolCallId": "tc-9", "toolName": "bash", "result": {"content": []}, "isError": True}),
            evidence_ref=REF,
        )
        (item2,) = _items(end)
        self.assertEqual(item2.phase, "failed")

    def test_message_events_mint_no_live_items(self) -> None:
        for event_type in ("message_end", "message_update", "agent_end"):
            self.assertEqual(
                pi.map_evidence_frame(
                    _evidence(1, f"pi:{event_type}", {"type": event_type}),
                    evidence_ref=REF,
                ),
                [],
            )


if __name__ == "__main__":
    unittest.main()
