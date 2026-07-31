"""Per-harness projector frame mappings: stable identity, blocks, tools, provenance."""

from __future__ import annotations

import unittest

from agents_remember.serving.conversation.models import (
    ConversationItem,
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
    UnmappableShape,
)
from agents_remember.serving.harness_control_models import (
    EvidenceFrame,
    NativeEvidenceFrame,
)

NOW = "2026-07-19T08:00:00+00:00"
REF = "ar-ev:epoch:1"


def _native(
    native_id: str, native_type: str, raw: dict, parent: str | None = None
) -> NativeEvidenceFrame:
    return NativeEvidenceFrame(
        native_id=native_id,
        native_parent_id=parent,
        native_type=native_type,
        created_at=NOW,
        raw=raw,
    )


def _evidence(
    sequence: int, kind: str, raw: dict, *, native_method: str | None = None
) -> EvidenceFrame:
    return EvidenceFrame(
        sequence=sequence, kind=kind, created_at=NOW, raw=raw, native_method=native_method
    )


def _items(outputs: list) -> list:
    return [output.item for output in outputs if isinstance(output, MappedItem)]


def _claude_tool_item(name: str, tool_input: object, *, tool_id: str) -> ConversationItem:
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
                        {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": name,
                            "input": tool_input,
                        }
                    ],
                },
            },
        ),
        evidence_ref=REF,
    )
    return next(item for item in _items(outputs) if item.item_id == tool_id)


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
            _native(
                "item-2", "agentMessage", {"id": "item-2", "type": "agentMessage", "text": "**hi**"}
            ),
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
                    "changes": [
                        {
                            "path": "a.py",
                            "kind": {"type": "update", "move_path": None},
                            "diff": "@@-1+1@@",
                        }
                    ],
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
            _native(
                "item-7", "collabAgentToolCall", {"id": "item-7", "type": "collabAgentToolCall"}
            ),
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
                {
                    "threadId": "t",
                    "turnId": "turn-1",
                    "itemId": "item-3",
                    "delta": "x",
                    "summaryIndex": 1,
                },
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

    def test_fresh_open_startup_burst_mints_zero_unknown_vendor_rows(self) -> None:
        # The codex 0.144.5 fresh-dashboard-open startup burst — one
        # ``mcpServer/startupStatus/updated`` per configured MCP server plus ``thread/started``,
        # ``remoteControl/status/changed`` and an optional ``warning`` — are KNOWN session
        # lifecycle/status notifications, not truly-unknown frames. Opening a stock codex chat must
        # produce ZERO unknown-vendor rows.
        startup_burst = [
            (
                "mcpServer/startupStatus/updated",
                {
                    "threadId": "thread-1",
                    "name": "agents-remember",
                    "status": "running",
                    "error": None,
                    "failureReason": None,
                },
            ),
            (
                "mcpServer/startupStatus/updated",
                {
                    "threadId": "thread-1",
                    "name": "atlassian",
                    "status": "failed",
                    "error": "spawn ENOENT",
                    "failureReason": "startup",
                },
            ),
            ("thread/started", {"thread": {"id": "thread-1"}}),
            (
                "remoteControl/status/changed",
                {
                    "status": "disconnected",
                    "serverName": "codex-cloud",
                    "installationId": "inst-1",
                    "environmentId": "env-1",
                },
            ),
            ("warning", {"threadId": "thread-1", "message": "service tier downgraded"}),
            # The ``configWarning`` advisory (observed live at open on evidence
            # seq 1 in the AR_RUN_CHATS_E2E composed drive) is a sibling of ``warning`` — it must also
            # mint zero unknown-vendor rows, not leak through as one per fresh codex open.
            ("configWarning", {"message": "sandbox mode defaulted to read-only"}),
        ]
        for sequence, (method, params) in enumerate(startup_burst, start=1):
            outputs = codex.map_evidence_frame(
                _evidence(sequence, "codex-notification", params, native_method=method),
                evidence_ref=REF,
            )
            self.assertEqual(outputs, [], f"{method} must mint no item (got {outputs!r})")

    def test_item_notification_still_maps_when_method_is_carried(self) -> None:
        # The carried method never suppresses a real item notification.
        outputs = codex.map_evidence_frame(
            _evidence(
                7,
                "codex-notification",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "startedAtMs": 1,
                    "item": {"id": "item-1", "type": "agentMessage", "text": "hi"},
                },
                native_method="item/started",
            ),
            evidence_ref=REF,
        )
        (item,) = _items(outputs)
        self.assertEqual(item.item_id, "item-1")

    def test_truly_unknown_notification_names_the_method(self) -> None:
        # A genuinely novel method still falls to unknown-vendor, but WITH the method named.
        outputs = codex.map_evidence_frame(
            _evidence(
                9,
                "codex-notification",
                {"threadId": "thread-1", "somethingNovel": True},
                native_method="thread/futureFeature/appeared",
            ),
            evidence_ref=REF,
        )
        (unknown,) = [o for o in outputs if isinstance(o, MappedUnknownVendor)]
        self.assertIn("thread/futureFeature/appeared", unknown.vendor_type)
        self.assertIn("thread/futureFeature/appeared", unknown.safe_summary)


class ClaudeMapperTests(unittest.TestCase):
    def test_local_command_string_content_user_frame_is_preserved_not_fatal(self) -> None:
        # Claude Code records local slash-command turns (/effort etc.) as
        # user frames with bare STRING content. The transcript replay hit one mid-file and the
        # UnmappableShape crash-looped the projector (generation churn → dead cursors →
        # "structured surface unavailable" for the whole session). String content must map to
        # a preserved row, never a projection-fatal error.
        outputs = claude.map_evidence_frame(
            _evidence(
                1,
                "state",
                {
                    "type": "user",
                    "uuid": "uuid-cmd",
                    "session_id": "sess-1",
                    "message": {
                        "role": "user",
                        "content": "<command-name>/effort</command-name> max",
                    },
                },
            ),
            evidence_ref=REF,
        )
        self.assertEqual(len(outputs), 1)
        preserved = outputs[0]
        assert isinstance(preserved, MappedUnknownVendor)
        self.assertEqual(preserved.vendor_type, "claude-user-content:text")
        self.assertIn("/effort", preserved.safe_summary)

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
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "Bash",
                                "input": {"command": "ls"},
                            },
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

    def test_file_mutation_tool_use_carries_diff_blocks(self) -> None:
        cases = (
            (
                "Edit",
                {
                    "file_path": "/repo/a.ts",
                    "old_string": "const a = 1;",
                    "new_string": "const a = 2;",
                },
                (("diff-0", "/repo/a.ts", "const a = 1;", "const a = 2;"),),
            ),
            (
                "MultiEdit",
                {
                    "file_path": "/repo/multi.ts",
                    "edits": [
                        {"old_string": "one", "new_string": "ONE"},
                        "not-an-edit",
                        {"old_string": "missing-new"},
                        {"old_string": "two", "new_string": "TWO"},
                    ],
                },
                (
                    ("diff-0", "/repo/multi.ts", "one", "ONE"),
                    ("diff-3", "/repo/multi.ts", "two", "TWO"),
                ),
            ),
            (
                "Write",
                {"file_path": "/repo/b.md", "content": "# fresh\nbody"},
                (("diff-0", "/repo/b.md", None, "# fresh\nbody"),),
            ),
            (
                "NotebookEdit",
                {"notebook_path": "/repo/c.ipynb", "new_source": "print('new')"},
                (("diff-0", "/repo/c.ipynb", None, "print('new')"),),
            ),
        )
        for index, (name, tool_input, expected) in enumerate(cases):
            with self.subTest(name=name):
                item = _claude_tool_item(name, tool_input, tool_id=f"toolu_{index}")
                input_block = item.blocks[0]
                assert isinstance(input_block, ToolInputBlock)
                self.assertEqual(input_block.data, tool_input)
                diffs = [block for block in item.blocks if isinstance(block, DiffBlock)]
                self.assertEqual(
                    [(diff.block_id, diff.path, diff.old_text, diff.new_text) for diff in diffs],
                    list(expected),
                )

    def test_malformed_or_unknown_tool_input_keeps_only_the_raw_input(self) -> None:
        cases = (
            ("Edit", {"old_string": "old", "new_string": 2}),
            ("MultiEdit", {"edits": {"not": "a list"}}),
            ("Write", {"content": 2}),
            ("NotebookEdit", {"new_source": 2}),
            ("UnknownMutation", {"file_path": "/repo/a", "content": "new"}),
            ("Edit", "not-a-mapping"),
        )
        for index, (name, tool_input) in enumerate(cases):
            with self.subTest(name=name, tool_input=tool_input):
                item = _claude_tool_item(
                    name,
                    tool_input,
                    tool_id=f"toolu_invalid_{index}",
                )
                self.assertEqual(len(item.blocks), 1)
                input_block = item.blocks[0]
                assert isinstance(input_block, ToolInputBlock)
                self.assertEqual(input_block.data, tool_input)

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
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "content": [{"type": "text", "text": "out"}],
                                "is_error": False,
                            }
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
            outputs = claude.map_evidence_frame(_evidence(3, "completed", frame), evidence_ref=REF)
            outcomes = [o for o in outputs if isinstance(o, MappedTurnOutcome)]
            self.assertEqual(outcomes[0].outcome, expected)
            items = _items(outputs)
            self.assertEqual(items[0].kind, "turn-result")

    def test_result_frame_adapter_stamp_carries_the_interrupt_correlation(self) -> None:
        # Claude answers an accepted interrupt with a plain error_during_execution/is_error
        # result (probe-locked 2.1.217, terminal_reason aborted_streaming): only the adapter's
        # arTerminalOutcome stamp distinguishes interrupted from a real failure. The identical
        # native shape stamped "failed" (no accepted interrupt) must keep its failed meaning.
        for stamp, expected in (
            ("cancelled", "interrupted"),
            ("failed", "failed"),
            ("completed", "completed"),
        ):
            frame = {
                "type": "result",
                "subtype": "success" if stamp == "completed" else "error_during_execution",
                "is_error": stamp != "completed",
                "uuid": "uuid-r",
                "stop_reason": None,
                "terminal_reason": None if stamp == "completed" else "aborted_streaming",
                "arTerminalOutcome": stamp,
            }
            outputs = claude.map_evidence_frame(_evidence(3, "completed", frame), evidence_ref=REF)
            outcomes = [o for o in outputs if isinstance(o, MappedTurnOutcome)]
            self.assertEqual((stamp, outcomes[0].outcome), (stamp, expected))
            items = _items(outputs)
            self.assertEqual(items[0].phase, expected)

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

    def test_command_lifecycle_is_recognized_and_mints_no_unknown_vendor(self) -> None:
        # The installed Claude Code slash-command lifecycle (captured specimen)
        # is a first-class recognized contract, not a flooding unknown-vendor row. Each of the three
        # states maps to zero items.
        for state in ("queued", "started", "completed"):
            frame = _evidence(
                1,
                "state",
                {
                    "type": "command_lifecycle",
                    "command_uuid": "3fbfd39c-31bf-4ea2-8947-efae02df4b9d",
                    "state": state,
                    "uuid": "bed1195d-6e53-4461-b0c4-d47ebd2cc95a",
                    "session_id": "a3ad054c-0000-0000-0000-000000000000",
                },
            )
            self.assertEqual(claude.map_evidence_frame(frame, evidence_ref=REF), [])

    def test_command_lifecycle_unknown_state_surfaces_as_drift(self) -> None:
        # A genuine future shape drift is caught (strict recognition), not silently tolerated.
        with self.assertRaises(UnmappableShape):
            claude.map_evidence_frame(
                _evidence(
                    1,
                    "state",
                    {
                        "type": "command_lifecycle",
                        "command_uuid": "cmd-1",
                        "state": "paused",
                    },
                ),
                evidence_ref=REF,
            )

    def test_rate_limit_event_mints_no_items(self) -> None:
        self.assertEqual(
            claude.map_evidence_frame(
                _evidence(
                    1,
                    "state",
                    {
                        "type": "rate_limit_event",
                        "rate_limit_info": {
                            "status": "allowed",
                            "resetsAt": "2026-07-21T05:00:00Z",
                            "rateLimitType": "requests",
                            "overageStatus": "none",
                        },
                        "uuid": "u-1",
                        "session_id": "s-1",
                    },
                ),
                evidence_ref=REF,
            ),
            [],
        )


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
                            {
                                "type": "toolCall",
                                "id": "tc-1",
                                "name": "bash",
                                "arguments": {"command": "ls"},
                            },
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
            _native(
                "entry-7",
                "branch_summary",
                {"id": "entry-7", "type": "branch_summary", "fromId": "e", "summary": "s"},
            ),
            evidence_ref=REF,
        )
        (unknown,) = [o for o in outputs if isinstance(o, MappedUnknownVendor)]
        self.assertEqual(unknown.item_id, "entry-7")
        self.assertFalse(unknown.live)

    def test_live_tool_execution_upserts_by_tool_call_id(self) -> None:
        start = pi.map_evidence_frame(
            _evidence(
                1,
                "pi:tool_execution_start",
                {
                    "type": "tool_execution_start",
                    "toolCallId": "tc-9",
                    "toolName": "bash",
                    "args": {"command": "ls"},
                },
            ),
            evidence_ref=REF,
        )
        (item,) = _items(start)
        self.assertEqual(item.item_id, "tc-9")
        self.assertEqual(item.phase, "streaming")
        self.assertIsInstance(item.blocks[0], ToolInputBlock)
        end = pi.map_evidence_frame(
            _evidence(
                2,
                "pi:tool_execution_end",
                {
                    "type": "tool_execution_end",
                    "toolCallId": "tc-9",
                    "toolName": "bash",
                    "result": {"content": []},
                    "isError": True,
                },
            ),
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
