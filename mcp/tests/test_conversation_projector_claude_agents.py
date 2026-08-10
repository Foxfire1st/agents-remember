"""Claude projector sub-agent mappings: sidechain correlation, roster lifecycle.

Fixtures are synthesized minimal shapes matching the frames probe-locked on the
installed claude 2.1.220 (2026-07-26 live stream-json probes; captures retained at
``/tmp/ar-l7-probe/stream.jsonl`` and ``/tmp/ar-l7-probe/stream2.jsonl``, transcripts
under ``~/.claude/projects/-tmp-ar-l7-probe/``): sidechain assistant/user frames keyed
by ``parent_tool_use_id`` with ``subagent_type``/``task_description``, and system
``task_started`` / ``task_progress`` / ``task_notification`` /
``background_tasks_changed`` frames. Verified field-for-field:
the probe ``task_progress``/``task_notification`` ``usage`` object is exactly
``{total_tokens, tool_uses, duration_ms}``; ``background_tasks_changed`` task entries
are exactly ``{task_id, task_type, description}``; the Agent spawn ``input`` keys are
exactly ``{description, prompt, run_in_background, subagent_type}``; sidechain
``tool_result`` content crosses as a plain string while the parent-carried Agent
result keeps list content. Each test drives a distinct vendor session id so the
session-keyed binding registry never leaks across cases.
"""

from __future__ import annotations

import unittest

from agents_remember.models.conversations.content import (
    ConversationItem,
    TextBlock,
    ToolOutputBlock,
)
from agents_remember.models.conversations.evidence import (
    EvidenceFrame,
)
from agents_remember.serving.claude_stream_protocol import (
    build_claude_stream_argv,
    forward_subagent_text_supported,
)
from agents_remember.serving.conversation.projectors import claude
from agents_remember.serving.conversation.projectors.common import (
    MappedItem,
    MappedUnknownVendor,
)

NOW = "2026-07-26T08:00:00+00:00"
REF = "ar-ev:epoch:1"

AGENT_TOOL_ID = "toolu_agent001"
INNER_TOOL_ID = "toolu_inner001"
TASK_ID = "a1b2c3d4e5f6"


def _evidence(sequence: int, raw: dict) -> EvidenceFrame:
    return EvidenceFrame(sequence=sequence, kind="state", created_at=NOW, raw=raw)


def _items(outputs: list) -> list[ConversationItem]:
    return [output.item for output in outputs if isinstance(output, MappedItem)]


def _agent_tool_use_frame(session: str) -> dict:
    return {
        "type": "assistant",
        "uuid": "uuid-spawn",
        "session_id": session,
        "parent_tool_use_id": None,
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": AGENT_TOOL_ID,
                    "name": "Agent",
                    "input": {
                        "description": "probe",
                        "subagent_type": "Explore",
                        "run_in_background": False,
                        "prompt": "read probe.txt",
                    },
                }
            ],
        },
    }


def _task_started(session: str) -> dict:
    return {
        "type": "system",
        "subtype": "task_started",
        "task_id": TASK_ID,
        "tool_use_id": AGENT_TOOL_ID,
        "description": "probe",
        "subagent_type": "Explore",
        "task_type": "local_agent",
        "prompt": "read probe.txt",
        "uuid": "uuid-started",
        "session_id": session,
    }


def _sidechain_user_text(session: str) -> dict:
    return {
        "type": "user",
        "uuid": "uuid-side-user",
        "session_id": session,
        "parent_tool_use_id": AGENT_TOOL_ID,
        "subagent_type": "Explore",
        "task_description": "probe",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "read probe.txt"}],
        },
    }


def _sidechain_assistant_text(session: str) -> dict:
    return {
        "type": "assistant",
        "uuid": "uuid-side-assistant",
        "session_id": session,
        "parent_tool_use_id": AGENT_TOOL_ID,
        "subagent_type": "Explore",
        "task_description": "probe",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "I'll read that file."}],
        },
    }


def _sidechain_tool_cycle(session: str) -> tuple[dict, dict]:
    tool_use = {
        "type": "assistant",
        "uuid": "uuid-side-tool",
        "session_id": session,
        "parent_tool_use_id": AGENT_TOOL_ID,
        "subagent_type": "Explore",
        "task_description": "probe",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": INNER_TOOL_ID,
                    "name": "Read",
                    "input": {"file_path": "/probe.txt"},
                }
            ],
        },
    }
    tool_result = {
        "type": "user",
        "uuid": "uuid-side-result",
        "session_id": session,
        "parent_tool_use_id": AGENT_TOOL_ID,
        "subagent_type": "Explore",
        "task_description": "probe",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": INNER_TOOL_ID,
                    "content": "1\tprobe\n",
                }
            ],
        },
    }
    return tool_use, tool_result


def _task_progress(session: str) -> dict:
    return {
        "type": "system",
        "subtype": "task_progress",
        "task_id": TASK_ID,
        "tool_use_id": AGENT_TOOL_ID,
        "description": "Reading probe.txt",
        "subagent_type": "Explore",
        "usage": {"total_tokens": 11519, "tool_uses": 1, "duration_ms": 2102},
        "last_tool_name": "Read",
        "uuid": "uuid-progress",
        "session_id": session,
    }


def _task_notification(session: str) -> dict:
    return {
        "type": "system",
        "subtype": "task_notification",
        "task_id": TASK_ID,
        "tool_use_id": AGENT_TOOL_ID,
        "status": "completed",
        "output_file": "/tmp/claude-1000/-probe/sess/tasks/a1b2c3d4e5f6.output",
        "summary": "The word is: probe",
        "usage": {"total_tokens": 11754, "tool_uses": 1, "duration_ms": 3660},
        "uuid": "uuid-notification",
        "session_id": session,
    }


def _agent_tool_result_frame(session: str) -> dict:
    return {
        "type": "user",
        "uuid": "uuid-spawn-result",
        "session_id": session,
        "parent_tool_use_id": None,
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": AGENT_TOOL_ID,
                    "content": [{"type": "text", "text": "The word is: probe"}],
                }
            ],
        },
    }


def _map(raw: dict, sequence: int = 1) -> list:
    return claude.map_evidence_frame(_evidence(sequence, raw), evidence_ref=REF)


class ClaudeAgentLifecycleTests(unittest.TestCase):
    def test_full_lifecycle_binds_identity_and_upserts_roster(self) -> None:
        """One sub-agent, start to finish, in the order the harness emits its frames.

        The stages share the projector's per-session state, so they run in sequence against
        one session and each asserts what its own frame added: the ninth frame's settlement
        is only meaningful because the second frame bound the identity it settles.
        """
        session = "sess-l7-lifecycle"
        self._assert_spawn_call_is_untagged(session)
        self._assert_task_started_binds_the_roster_and_tags_the_call(session)
        self._assert_sidechain_records_are_bound(session)
        self._assert_task_progress_upserts_usage(session)
        self._assert_task_notification_completes_the_roster_row(session)
        self._assert_tool_result_settles_the_bound_call(session)

    def _assert_spawn_call_is_untagged(self, session: str) -> None:
        """The spawning Agent tool_use stays a parent-timeline tool-call, untagged
        until task_started binds the roster identity."""
        (spawn_item,) = _items(_map(_agent_tool_use_frame(session), 1))
        self.assertEqual(spawn_item.item_id, AGENT_TOOL_ID)
        self.assertEqual(spawn_item.kind, "tool-call")
        self.assertIsNone(spawn_item.agent)

    def _assert_task_started_binds_the_roster_and_tags_the_call(self, session: str) -> None:
        """task_started: roster item (running) + tagging upsert of the tool-call."""
        started_items = _items(_map(_task_started(session), 2))
        roster = next(item for item in started_items if item.item_id == f"claude-agent-{TASK_ID}")
        self.assertEqual(roster.kind, "notice")
        self.assertEqual(roster.role, "system")
        self.assertEqual(roster.phase, "streaming")
        assert roster.agent is not None
        self.assertEqual(roster.agent.agent_id, TASK_ID)
        self.assertEqual(roster.agent.join_key, AGENT_TOOL_ID)
        self.assertEqual(roster.agent.role, "Explore")
        self.assertEqual(roster.agent.status, "running")
        description = next(
            b for b in roster.blocks if isinstance(b, TextBlock) and b.block_id == "description"
        )
        self.assertEqual(description.text, "probe")
        tagged = next(item for item in started_items if item.item_id == AGENT_TOOL_ID)
        self.assertEqual(tagged.kind, "tool-call")
        self.assertEqual(tagged.phase, "streaming")
        self.assertEqual(tagged.blocks, ())
        assert tagged.agent is not None
        self.assertEqual(tagged.agent.agent_id, TASK_ID)
        self.assertEqual(tagged.agent.status, "running")

    def _assert_sidechain_records_are_bound(self, session: str) -> None:
        """Everything the sub-agent itself emits carries the bound identity.

        Its own input record, its assistant text (which crosses via
        ``--forward-subagent-text``), and both halves of an inner tool cycle.
        """
        (side_user,) = _items(_map(_sidechain_user_text(session), 3))
        self.assertEqual(side_user.role, "user")
        self.assertEqual(side_user.kind, "message")
        self.assertEqual(side_user.lane, "harness")
        assert side_user.agent is not None
        self.assertEqual(side_user.agent.agent_id, TASK_ID)
        self.assertEqual(side_user.agent.join_key, AGENT_TOOL_ID)
        self.assertEqual(side_user.agent.role, "Explore")
        self.assertEqual(side_user.agent.status, "running")
        self.assertIsInstance(side_user.blocks[0], TextBlock)

        (side_assistant,) = _items(_map(_sidechain_assistant_text(session), 4))
        self.assertEqual(side_assistant.role, "assistant")
        assert side_assistant.agent is not None
        self.assertEqual(side_assistant.agent.agent_id, TASK_ID)

        tool_use_frame, tool_result_frame = _sidechain_tool_cycle(session)
        (inner_call,) = _items(_map(tool_use_frame, 5))
        self.assertEqual(inner_call.item_id, INNER_TOOL_ID)
        assert inner_call.agent is not None
        self.assertEqual(inner_call.agent.agent_id, TASK_ID)
        (inner_result,) = _items(_map(tool_result_frame, 6))
        self.assertEqual(inner_result.item_id, INNER_TOOL_ID)
        self.assertEqual(inner_result.phase, "completed")
        assert inner_result.agent is not None
        self.assertEqual(inner_result.agent.agent_id, TASK_ID)

    def _assert_task_progress_upserts_usage(self, session: str) -> None:
        """task_progress: roster upsert carrying usage + last tool."""
        (progress_roster,) = _items(_map(_task_progress(session), 7))
        self.assertEqual(progress_roster.item_id, f"claude-agent-{TASK_ID}")
        self.assertEqual(progress_roster.phase, "streaming")
        assert progress_roster.agent is not None
        self.assertEqual(progress_roster.agent.status, "running")
        usage_block = next(b for b in progress_roster.blocks if b.block_id == "usage")
        assert isinstance(usage_block, ToolOutputBlock)
        self.assertEqual(
            usage_block.data,
            {
                "usage": {"total_tokens": 11519, "tool_uses": 1, "duration_ms": 2102},
                "lastToolName": "Read",
            },
        )

    def _assert_task_notification_completes_the_roster_row(self, session: str) -> None:
        """task_notification: terminal roster upsert with the final summary."""
        (final_roster,) = _items(_map(_task_notification(session), 8))
        self.assertEqual(final_roster.phase, "completed")
        assert final_roster.agent is not None
        self.assertEqual(final_roster.agent.status, "completed")
        # The notification carries no description/subagent_type; the bound record
        # keeps the roster row whole across the replacement upsert.
        self.assertEqual(final_roster.agent.role, "Explore")
        summary_block = next(
            b for b in final_roster.blocks if isinstance(b, TextBlock) and b.block_id == "summary"
        )
        self.assertEqual(summary_block.text, "The word is: probe")
        description = next(
            b
            for b in final_roster.blocks
            if isinstance(b, TextBlock) and b.block_id == "description"
        )
        self.assertEqual(description.text, "probe")

    def _assert_tool_result_settles_the_bound_call(self, session: str) -> None:
        """The Agent tool_result carrier settles the tool-call with the bound identity."""
        (settled,) = _items(_map(_agent_tool_result_frame(session), 9))
        self.assertEqual(settled.item_id, AGENT_TOOL_ID)
        self.assertEqual(settled.phase, "completed")
        assert settled.agent is not None
        self.assertEqual(settled.agent.agent_id, TASK_ID)
        self.assertEqual(settled.agent.status, "completed")

    def test_parent_timeline_items_carry_no_agent(self) -> None:
        session = "sess-l7-parent"
        _map(_task_started(session), 1)
        parent_assistant = {
            "type": "assistant",
            "uuid": "uuid-parent",
            "session_id": session,
            "parent_tool_use_id": None,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "on it"}]},
        }
        (item,) = _items(_map(parent_assistant, 2))
        self.assertIsNone(item.agent)

    def test_sidechain_before_task_started_falls_back_to_join_key(self) -> None:
        session = "sess-l7-unbound"
        # No task_started yet: identity stays honest — the join key IS the id.
        (item,) = _items(_map(_sidechain_assistant_text(session), 1))
        assert item.agent is not None
        self.assertEqual(item.agent.agent_id, AGENT_TOOL_ID)
        self.assertEqual(item.agent.join_key, AGENT_TOOL_ID)
        self.assertEqual(item.agent.role, "Explore")
        self.assertEqual(item.agent.status, "unknown")
        # Once task_started binds, later sidechain items carry the real agent id.
        _map(_task_started(session), 2)
        (bound,) = _items(_map(_sidechain_assistant_text(session), 3))
        assert bound.agent is not None
        self.assertEqual(bound.agent.agent_id, TASK_ID)
        self.assertEqual(bound.agent.status, "running")

    def test_malformed_task_frames_degrade_to_preserved_unknown_vendor(self) -> None:
        session = "sess-l7-malformed"
        cases = (
            {"type": "system", "subtype": "task_started", "session_id": session},
            {
                "type": "system",
                "subtype": "task_started",
                "task_id": TASK_ID,
                "session_id": session,
            },
            {
                "type": "system",
                "subtype": "task_notification",
                "task_id": TASK_ID,
                "session_id": session,
            },
            {
                "type": "system",
                "subtype": "task_progress",
                "task_id": TASK_ID,
                "usage": "not-an-object",
                "session_id": session,
            },
            {
                "type": "system",
                "subtype": "background_tasks_changed",
                "tasks": "not-a-list",
                "session_id": session,
            },
            {
                "type": "system",
                "subtype": "background_tasks_changed",
                "tasks": [{"task_type": "local_agent"}],
                "session_id": session,
            },
        )
        for index, frame in enumerate(cases):
            with self.subTest(subtype=frame["subtype"], case=index):
                outputs = _map(frame, index + 1)
                (unknown,) = [o for o in outputs if isinstance(o, MappedUnknownVendor)]
                self.assertEqual(unknown.vendor_type, f"claude-system:{frame['subtype']}")

    def test_unmapped_system_subtypes_keep_dropping_silently(self) -> None:
        for subtype in ("api_retry", "status", "init", "task_updated", "hook_started"):
            with self.subTest(subtype=subtype):
                self.assertEqual(
                    _map(
                        {"type": "system", "subtype": subtype, "session_id": "sess-l7-silent"},
                        1,
                    ),
                    [],
                )

    def test_background_tasks_changed_registers_unknown_task_once(self) -> None:
        session = "sess-l7-background"
        frame = {
            "type": "system",
            "subtype": "background_tasks_changed",
            "tasks": [{"task_id": TASK_ID, "task_type": "local_agent", "description": "bg-probe"}],
            "uuid": "uuid-bg",
            "session_id": session,
        }
        (roster,) = _items(_map(frame, 1))
        self.assertEqual(roster.item_id, f"claude-agent-{TASK_ID}")
        self.assertEqual(roster.kind, "notice")
        self.assertEqual(roster.phase, "streaming")
        assert roster.agent is not None
        self.assertEqual(roster.agent.agent_id, TASK_ID)
        self.assertIsNone(roster.agent.join_key)
        self.assertEqual(roster.agent.status, "running")
        # Already registered: the richer task_* authority owns the row; the frame
        # mints nothing twice.
        self.assertEqual(_map(frame, 2), [])
        # A late task_started still binds the join key onto the same roster row.
        started_items = _items(_map(_task_started(session), 3))
        roster = next(item for item in started_items if item.item_id == f"claude-agent-{TASK_ID}")
        assert roster.agent is not None
        self.assertEqual(roster.agent.join_key, AGENT_TOOL_ID)

    def test_empty_background_task_set_reconciles_nothing(self) -> None:
        self.assertEqual(
            _map(
                {
                    "type": "system",
                    "subtype": "background_tasks_changed",
                    "tasks": [],
                    "session_id": "sess-l7-bg-empty",
                },
                1,
            ),
            [],
        )


class ClaudeLaunchFlagTests(unittest.TestCase):
    def test_stream_argv_forwards_subagent_text(self) -> None:
        # The flag is fail-closed — emitted only when the
        # caller proved the floor, never by default.
        argv = build_claude_stream_argv(("claude", "--model", "sonnet"))
        self.assertEqual(argv[:3], ("claude", "--model", "sonnet"))
        self.assertNotIn("--forward-subagent-text", argv)
        proven = build_claude_stream_argv(
            ("claude", "--model", "sonnet"), forward_subagent_text=True
        )
        self.assertIn("--forward-subagent-text", proven)

    def test_stream_argv_does_not_duplicate_an_existing_flag(self) -> None:
        argv = build_claude_stream_argv(("claude", "--forward-subagent-text"))
        self.assertEqual(argv.count("--forward-subagent-text"), 1)

    def test_forward_subagent_text_floor_verdicts(self) -> None:
        self.assertTrue(forward_subagent_text_supported("2.1.220"))
        self.assertTrue(forward_subagent_text_supported("2.2.0"))
        self.assertFalse(forward_subagent_text_supported("2.1.219"))
        self.assertFalse(forward_subagent_text_supported("2.1.210"))
        self.assertFalse(forward_subagent_text_supported("dev-build"))
        self.assertFalse(forward_subagent_text_supported(None))


if __name__ == "__main__":
    unittest.main()
