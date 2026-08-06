from __future__ import annotations

import unittest
from dataclasses import replace
from itertools import pairwise

import agents_remember.serving.conversation.active.projector as projector_module
from agents_remember.serving.conversation.active.cursor import mint_event_cursor
from agents_remember.serving.conversation.active.projector import ActiveSessionProjector
from agents_remember.serving.conversation.active.store import ProjectionStore
from agents_remember.serving.conversation.models import (
    ConversationEventEnvelope,
    GapMutation,
    StatusMutation,
    ToolOutputBlock,
)
from agents_remember.serving.conversation.projectors import claude, codex, pi
from agents_remember.serving.conversation.projectors.codex import map_native_frame
from agents_remember.serving.conversation.projectors.common import MappedBlockDelta, MappedItem
from agents_remember.serving.harness_control_models import EvidenceFrame, NativeEvidenceFrame
from test_conversation_active_service import (
    NOW,
    SECRET,
    _authorization,
    _identity,
    _projector,
    _ScriptedBridge,
)


class ToolConvergenceTests(unittest.TestCase):
    """Partial-block tool items converge by scoped block-union."""

    def _blocks(self, item) -> dict:
        return {block.block_id: block for block in item.blocks}

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_active_service_queues.py:37).
    def test_claude_tool_use_then_tool_result_keeps_input_and_output(
        self,
    ) -> None:  # pragma: no cover
        store = ProjectionStore()
        use = claude.map_evidence_frame(
            EvidenceFrame(
                sequence=1,
                kind="state",
                created_at=NOW,
                raw={
                    "type": "assistant",
                    "uuid": "uuid-1",
                    "session_id": "vendor-1",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "Bash",
                                "input": {"command": "ls -la"},
                            }
                        ],
                    },
                },
            ),
            evidence_ref="ref-1",
        )
        for output in use:
            if isinstance(output, MappedItem):
                store.apply_item(output)
        result = claude.map_evidence_frame(
            EvidenceFrame(
                sequence=2,
                kind="state",
                created_at=NOW,
                raw={
                    "type": "user",
                    "uuid": "uuid-2",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "content": [{"type": "text", "text": "total 0"}],
                                "is_error": False,
                            }
                        ],
                    },
                },
            ),
            evidence_ref="ref-2",
        )
        for output in result:
            if isinstance(output, MappedItem):
                store.apply_item(output)
        (item,) = store.items()
        blocks = self._blocks(item)
        self.assertEqual(set(blocks), {"input", "output"})
        self.assertEqual(blocks["input"].summary, "Bash")
        self.assertEqual(blocks["input"].data, {"command": "ls -la"})
        self.assertEqual(blocks["output"].text, "total 0")
        self.assertEqual(item.phase, "completed")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_active_service_queues.py:100).
    def test_reordered_task_started_tagging_never_regresses_a_terminal_phase(
        self,
    ) -> None:  # pragma: no cover
        """Reordered evidence — the Agent
        tool_result settles the call BEFORE task_started binds the agent identity —
        keeps the terminal phase while the agent ref still lands."""

        session = "vendor-1"
        tool_id = "toolu_agent_1"
        frames = [
            {
                "type": "assistant",
                "uuid": "uuid-spawn",
                "session_id": session,
                "parent_tool_use_id": None,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_id,
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
            },
            {
                "type": "user",
                "uuid": "uuid-spawn-result",
                "session_id": session,
                "parent_tool_use_id": None,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": [{"type": "text", "text": "The word is: probe"}],
                        }
                    ],
                },
            },
            # The reordered binder: task_started arrives AFTER the tool_result.
            {
                "type": "system",
                "subtype": "task_started",
                "task_id": "task-1",
                "tool_use_id": tool_id,
                "description": "probe",
                "subagent_type": "Explore",
                "task_type": "local_agent",
                "prompt": "read probe.txt",
                "uuid": "uuid-started",
                "session_id": session,
            },
        ]
        store = ProjectionStore()
        for sequence, raw in enumerate(frames, start=1):
            for output in claude.map_evidence_frame(
                EvidenceFrame(sequence=sequence, kind="state", created_at=NOW, raw=raw),
                evidence_ref=f"ref-{sequence}",
            ):
                if isinstance(output, MappedItem):
                    store.apply_item(output)
        item = next(item for item in store.items() if item.item_id == tool_id)
        self.assertEqual(item.phase, "completed")
        assert item.agent is not None
        self.assertEqual(item.agent.agent_id, "task-1")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_active_service_queues.py:173).
    def test_pi_live_start_update_end_keeps_input_through_every_step(
        self,
    ) -> None:  # pragma: no cover
        store = ProjectionStore()
        start = pi.map_evidence_frame(
            EvidenceFrame(
                sequence=1,
                kind="pi:tool_execution_start",
                created_at=NOW,
                raw={
                    "type": "tool_execution_start",
                    "toolCallId": "tc-1",
                    "toolName": "bash",
                    "args": {"command": "ls"},
                },
            ),
            evidence_ref="ref-1",
        )
        update = pi.map_evidence_frame(
            EvidenceFrame(
                sequence=2,
                kind="pi:tool_execution_update",
                created_at=NOW,
                raw={
                    "type": "tool_execution_update",
                    "toolCallId": "tc-1",
                    "toolName": "bash",
                    "args": {"command": "ls"},
                    "partialResult": {"content": [{"type": "text", "text": "part"}]},
                },
            ),
            evidence_ref="ref-2",
        )
        end = pi.map_evidence_frame(
            EvidenceFrame(
                sequence=3,
                kind="pi:tool_execution_end",
                created_at=NOW,
                raw={
                    "type": "tool_execution_end",
                    "toolCallId": "tc-1",
                    "toolName": "bash",
                    "result": {"content": [{"type": "text", "text": "done"}]},
                    "isError": False,
                },
            ),
            evidence_ref="ref-3",
        )
        for outputs in (start, update, end):
            for output in outputs:
                if isinstance(output, MappedItem):
                    store.apply_item(output)
        (item,) = store.items()
        blocks = self._blocks(item)
        self.assertEqual(set(blocks), {"input", "output"})
        self.assertEqual(blocks["input"].summary, "bash")
        self.assertEqual(blocks["input"].data, {"command": "ls"})
        self.assertEqual(item.phase, "completed")
        self.assertEqual(item.revision, 3)

    def test_pi_entry_call_then_tool_result_keeps_input_and_output(self) -> None:
        store = ProjectionStore()
        call = pi.map_native_frame(
            NativeEvidenceFrame(
                native_id="entry-1",
                native_parent_id=None,
                native_type="message",
                created_at=NOW,
                raw={
                    "id": "entry-1",
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "toolCall",
                                "id": "tc-9",
                                "name": "bash",
                                "arguments": {"command": "ls"},
                            }
                        ],
                        "stopReason": "toolUse",
                        "timestamp": 1,
                    },
                },
            ),
            evidence_ref="ref-1",
        )
        result = pi.map_native_frame(
            NativeEvidenceFrame(
                native_id="entry-2",
                native_parent_id="entry-1",
                native_type="message",
                created_at=NOW,
                raw={
                    "id": "entry-2",
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "toolCallId": "tc-9",
                        "toolName": "bash",
                        "content": [{"type": "text", "text": "done"}],
                        "isError": False,
                        "timestamp": 2,
                    },
                },
            ),
            evidence_ref="ref-2",
        )
        for outputs in (call, result):
            for output in outputs:
                if isinstance(output, MappedItem):
                    store.apply_item(output)
        tool = next(item for item in store.items() if item.item_id == "tc-9")
        blocks = self._blocks(tool)
        self.assertEqual(set(blocks), {"input", "output"})
        self.assertEqual(blocks["input"].summary, "bash")
        self.assertEqual(blocks["output"].text, "done")
        self.assertEqual(tool.phase, "completed")

    def test_codex_full_item_remap_is_identical_under_union(self) -> None:
        outputs = map_native_frame(
            NativeEvidenceFrame(
                native_id="item-7",
                native_parent_id="turn-1",
                native_type="commandExecution",
                created_at=NOW,
                raw={
                    "id": "item-7",
                    "type": "commandExecution",
                    "command": "ls",
                    "cwd": "/workspace",
                    "status": "completed",
                    "aggregatedOutput": "total 0",
                    "exitCode": 0,
                },
            ),
            evidence_ref="ref",
        )
        store = ProjectionStore()
        mapped = outputs[0]
        assert isinstance(mapped, MappedItem)
        store.apply_item(mapped)
        self.assertEqual(store.apply_item(mapped), [])
        (item,) = store.items()
        self.assertEqual(item.revision, 1)
        self.assertEqual({block.block_id for block in item.blocks}, {"input", "output"})


class DeltaStreamingTests(unittest.TestCase):
    """Streamed command output is live and never duplicated.

    Two store invariants: a delta addressed to a block the existing item does not carry yet
    MINTS the block (emitted as upsert-item so a subscriber converges without a re-page), and
    an arriving item whose block carries aggregated content DROPS that block's buffered delta
    backlog instead of replaying it on top.
    """

    def _started_command(self, sequence: int = 1) -> list:
        return codex.map_evidence_frame(
            EvidenceFrame(
                sequence=sequence,
                kind="codex-notification",
                created_at=NOW,
                raw={
                    "item": {
                        "id": "item-cmd",
                        "type": "commandExecution",
                        "command": "make test",
                        "status": "inProgress",
                    },
                    "turnId": "turn-1",
                    "startedAtMs": 1,
                },
            ),
            evidence_ref=f"ref-{sequence}",
        )

    def _completed_command(self, sequence: int, aggregated: str) -> list:
        return codex.map_evidence_frame(
            EvidenceFrame(
                sequence=sequence,
                kind="codex-notification",
                created_at=NOW,
                raw={
                    "item": {
                        "id": "item-cmd",
                        "type": "commandExecution",
                        "command": "make test",
                        "status": "completed",
                        "aggregatedOutput": aggregated,
                        "exitCode": 0,
                    },
                    "turnId": "turn-1",
                },
            ),
            evidence_ref=f"ref-{sequence}",
        )

    def _delta(self, sequence: int, text: str) -> list:
        return codex.map_evidence_frame(
            EvidenceFrame(
                sequence=sequence,
                kind="codex-notification",
                created_at=NOW,
                raw={"itemId": "item-cmd", "delta": text},
            ),
            evidence_ref=f"ref-{sequence}",
        )

    @staticmethod
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_active_service_queues.py:382).
    def _apply(store: ProjectionStore, outputs: list) -> list:  # pragma: no cover
        mutations = []
        for output in outputs:
            if isinstance(output, MappedItem):
                mutations.extend(store.apply_item(output))
            elif isinstance(output, MappedBlockDelta):
                mutations.extend(store.apply_delta(output))
        return mutations

    @staticmethod
    def _output_text(store: ProjectionStore) -> str | None:
        (item,) = store.items()
        block = next((b for b in item.blocks if b.block_id == "output"), None)
        return block.text if isinstance(block, ToolOutputBlock) else None

    def test_output_delta_mints_missing_block_and_streams_live(self) -> None:
        store = ProjectionStore()
        self._apply(store, self._started_command())
        first = self._apply(store, self._delta(2, "line 1\n"))
        # The mint crosses as a full upsert (a bare delta naming an unknown block would force
        # the browser reducer to re-page).
        self.assertEqual([mutation.kind for mutation in first], ["upsert-item"])
        second = self._apply(store, self._delta(3, "line 2\n"))
        self.assertEqual([mutation.kind for mutation in second], ["append-block-delta"])
        self.assertEqual(self._output_text(store), "line 1\nline 2\n")

    def test_completed_aggregate_drops_buffered_backlog(self) -> None:
        # Deltas arrive before the item ever materializes (arrival race) and then the completed
        # item carries the FULL aggregated output: the backlog must not replay on top.
        store = ProjectionStore()
        self._apply(store, self._delta(1, "line 1\n"))
        self._apply(store, self._delta(2, "line 2\n"))
        self._apply(store, self._completed_command(3, "line 1\nline 2\nline 3\n"))
        self.assertEqual(self._output_text(store), "line 1\nline 2\nline 3\n")

    def test_streamed_then_completed_aggregate_never_duplicates(self) -> None:
        # The live path: started item, streamed deltas, then the aggregate-carrying completion.
        # The completion's output block is authoritative and replaces the accumulated stream.
        store = ProjectionStore()
        self._apply(store, self._started_command())
        self._apply(store, self._delta(2, "line 1\n"))
        self._apply(store, self._delta(3, "line 2\n"))
        self._apply(store, self._completed_command(4, "line 1\nline 2\n"))
        self.assertEqual(self._output_text(store), "line 1\nline 2\n")
        (item,) = store.items()
        self.assertEqual(item.phase, "completed")

    def test_buffered_deltas_apply_when_started_item_carries_no_output(self) -> None:
        # Backlog before the STARTED item (which carries no output block): nothing is dropped —
        # the flush mints the block and the stream stays whole.
        store = ProjectionStore()
        self._apply(store, self._delta(1, "early\n"))
        self._apply(store, self._started_command(2))
        self.assertEqual(self._output_text(store), "early\n")


class GenesisCursorTests(unittest.IsolatedAsyncioTestCase):
    """Genesis (sequence-1) envelopes chain to the stream origin.

    A fresh page names event cursor 0; the first live frame must carry that
    cursor as its previousCursor instead of dropping the key (exclude_none),
    or the browser chain guard re-pages on every fresh chat.
    """

    def _origin(self, projector: ActiveSessionProjector) -> str:
        return str(
            mint_event_cursor(
                SECRET,
                _authorization(),
                _identity(),
                generation=projector.generation,
                sequence=0,
            )
        )

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_active_service_queues.py:457).
    async def test_genesis_envelope_previous_cursor_is_event_cursor_zero(
        self,
    ) -> None:  # pragma: no cover
        bridge = _ScriptedBridge()
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=50)
        bridge.push_evidence(
            "codex-notification",
            {
                "threadId": "vendor-1",
                "turnId": "turn-1",
                "completedAtMs": 10,
                "item": {"id": "turn-1-agent", "type": "agentMessage", "text": "hi"},
            },
        )
        await projector.poll_once()
        retained = projector.retained_after(0)
        genesis = retained[0]
        self.assertEqual(genesis.sequence, 1)
        assert genesis.previous_cursor is not None
        self.assertEqual(str(genesis.previous_cursor), self._origin(projector))
        # exclude_none wire serialization used to drop the key entirely; the
        # previous cursor now rides every frame.
        wire = genesis.model_dump(mode="json", by_alias=True, exclude_none=True)
        self.assertEqual(wire["previousCursor"], self._origin(projector))
        # The rest of the chain links frame to frame with no hole.
        for earlier, later in pairwise(retained):
            assert later.previous_cursor is not None
            self.assertEqual(str(later.previous_cursor), str(earlier.cursor))

    async def test_genesis_gap_envelope_previous_cursor_is_event_cursor_zero(self) -> None:
        bridge = _ScriptedBridge()
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=50)
        queue = projector.subscribe()
        await projector._gap("generation-changed")
        gap = queue.get_nowait()
        assert isinstance(gap, ConversationEventEnvelope)
        self.assertEqual(gap.sequence, 1)
        assert isinstance(gap.mutation, GapMutation)
        assert gap.previous_cursor is not None
        self.assertEqual(str(gap.previous_cursor), self._origin(projector))
        self.assertEqual(str(gap.mutation.requested_after), self._origin(projector))
        self.assertIs(queue.get_nowait(), projector_module.CLOSE_SENTINEL)


class HydrationBaselineStatusTests(unittest.IsolatedAsyncioTestCase):
    """The page already delivers the hydration baseline.

    The first poll after a quiet boot must not re-emit the page's status
    revision: the re-emitted envelope carries recomputed derived freshness
    (observed_at/age_ms) at the SAME revision, which the browser reducer's
    same-revision/different-payload guard treats as a protocol fault — the
    measured deterministic re-page on every quiet fresh chat. The cursor chain
    itself was already whole (the live frame's previousCursor equals the page
    cursor); the redundant emission is what had to go.
    """

    async def test_quiet_first_poll_after_page_emits_no_status_frame(self) -> None:
        # The fresh-chat boot repro: page + subscribe, then a poll whose snapshot
        # is unchanged. Nothing may reach the subscriber — the page already
        # carried this exact status revision atomically with the resume cursor.
        bridge = _ScriptedBridge()
        projector = _projector(bridge)
        page = await projector.page(before_ordinal=None, limit=50)
        queue = projector.subscribe()
        await projector.poll_once()
        self.assertTrue(queue.empty())
        self.assertEqual(projector.retained_after(0), ())
        # The baseline is marked delivered: a later semantic change still emits.
        self.assertEqual(projector._coordinator.status_revision_emitted, page.status.revision)

    async def test_first_poll_emits_status_only_when_semantic_state_changes(self) -> None:
        # Boot-timing variant: the status moves between page and first poll. The
        # frame must carry a strictly newer revision (never a same-revision
        # re-emission) and chain straight onto the page cursor.
        bridge = _ScriptedBridge()
        projector = _projector(bridge)
        page = await projector.page(before_ordinal=None, limit=50)
        queue = projector.subscribe()
        bridge.snapshot = replace(
            bridge.snapshot, activity="running", raw={"activeTurnId": "turn-1"}
        )
        await projector.poll_once()
        frames: list[ConversationEventEnvelope] = []
        while not queue.empty():
            item = queue.get_nowait()
            assert isinstance(item, ConversationEventEnvelope)
            frames.append(item)
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual(frame.mutation.op, "status")
        assert isinstance(frame.mutation, StatusMutation)
        self.assertGreater(frame.mutation.status.revision, page.status.revision)
        assert frame.previous_cursor is not None
        self.assertEqual(str(frame.previous_cursor), str(page.event_cursor))
