from __future__ import annotations

import unittest
from unittest import mock

import agents_remember.serving.conversation.active.projector as projector_module
from agents_remember.models.conversations.evidence import (
    EvidenceFrame,
)
from agents_remember.models.conversations.stream_events import (
    ConversationEventEnvelope,
    GapMutation,
)
from agents_remember.serving.conversation.active.projector import (
    ZipperEvidenceEvicted,
    mutation_stream,
)
from test_conversation_active_service import (
    NOW,
    _codex_turn,
    _identity,
    _projector,
    _ScriptedBridge,
)


class OverflowGapTests(unittest.IsolatedAsyncioTestCase):
    """An overflowed consumer gets exactly one gap then close."""

    async def test_overflow_delivers_one_gap_then_close_and_no_sequence_hole(self) -> None:
        bridge = _ScriptedBridge()
        _codex_turn(bridge, "turn-1")
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=50)
        with mock.patch.object(mutation_stream, "SUBSCRIBER_QUEUE_LIMIT", 4):
            queue = projector.subscribe()
            for index in range(6):
                bridge.push_evidence(
                    "codex-notification",
                    {
                        "threadId": "vendor-1",
                        "turnId": "turn-1",
                        "completedAtMs": index + 10,
                        "item": {
                            "id": f"extra-{index}",
                            "type": "agentMessage",
                            "text": f"extra {index}",
                        },
                    },
                )
            await projector.poll_once()
            received: list[object] = []
            while not queue.empty():
                received.append(queue.get_nowait())
            envelopes = [item for item in received if isinstance(item, ConversationEventEnvelope)]
            gaps = [item for item in envelopes if item.mutation.op == "gap"]
            self.assertEqual(len(gaps), 1)
            gap = gaps[0]
            assert isinstance(gap.mutation, GapMutation)
            self.assertEqual(gap.mutation.reason, "retention-overflow")
            self.assertTrue(gap.mutation.requires_repage)
            self.assertTrue(gap.mutation.close_after_event)
            self.assertIs(received[-1], projector_module.CLOSE_SENTINEL)
            self.assertLess(received.index(gap), len(received) - 1)
            sequences = [envelope.sequence for envelope in projector.retained_after(0)]
            self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
            self.assertIn(gap.sequence, sequences)
            previous = gap.previous_cursor
            assert previous is not None
            self.assertNotEqual(str(previous), str(gap.cursor))


class ZipperEvictionGapTests(unittest.IsolatedAsyncioTestCase):
    """Evidence eviction gaps the echo zipper with ordering-fault."""

    async def test_eviction_after_hydration_raises_for_echo_harness(self) -> None:
        bridge = _ScriptedBridge(harness="claude")
        bridge.transcript_entries.append(
            {
                "sequence": 1,
                "role": "user",
                "text": "p1",
                "createdAt": NOW,
                "requestId": "req-1",
                "vendorCorrelationId": "uuid-user-1",
            }
        )
        bridge.push_evidence(
            "state",
            {
                "type": "assistant",
                "uuid": "uuid-agent-1",
                "session_id": "vendor-1",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "a1"}]},
            },
        )
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual([item.item_id for item in result.items], ["uuid-user-1", "uuid-agent-1"])
        bridge.evicted_before = 1
        bridge.push_evidence(
            "state",
            {
                "type": "assistant",
                "uuid": "uuid-agent-2",
                "session_id": "vendor-1",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "a2"}]},
            },
        )
        with self.assertRaises(ZipperEvidenceEvicted):
            await projector.poll_once()

    async def test_eviction_does_not_gap_non_echo_harness(self) -> None:
        bridge = _ScriptedBridge()
        _codex_turn(bridge, "turn-1")
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=50)
        bridge.evicted_before = 1
        bridge.push_evidence(
            "codex-notification",
            {
                "threadId": "vendor-1",
                "turnId": "turn-2",
                "completedAtMs": 20,
                "item": {"id": "extra-1", "type": "agentMessage", "text": "x"},
            },
        )
        await projector.poll_once()
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertIn("extra-1", [item.item_id for item in result.items])
        self.assertIsNone(result.total_items)

    async def test_fresh_projector_rehydrates_from_remaining_window(self) -> None:
        bridge = _ScriptedBridge(harness="claude")
        bridge.evicted_before = 2
        bridge.push_evidence(
            "state",
            {
                "type": "assistant",
                "uuid": "uuid-agent-1",
                "session_id": "vendor-1",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "a1"}]},
            },
        )
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual([item.item_id for item in result.items], ["uuid-agent-1"])
        self.assertIsNone(result.total_items)

    def _scripted_claude_turns(self, bridge: _ScriptedBridge, turns: range) -> None:
        for n in turns:
            bridge.transcript_entries.append(
                {
                    "sequence": n,
                    "role": "user",
                    "text": f"prompt {n}",
                    "createdAt": NOW,
                    "requestId": f"req-{n}",
                    "vendorCorrelationId": f"uuid-user-{n}",
                }
            )
            bridge.push_evidence(
                "state",
                {
                    "type": "assistant",
                    "uuid": f"uuid-agent-{n}",
                    "session_id": "vendor-1",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": f"answer {n}"}],
                    },
                },
            )
            bridge.push_evidence(
                "completed",
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "uuid": f"uuid-result-{n}",
                },
            )

    async def test_rehydration_realigns_echoes_after_evidence_eviction(self) -> None:
        # After a dormancy release + re-hydration the
        # bridge's bounded evidence deque (2,000 frames) has prefix-evicted the oldest turn
        # bodies while the transcript deque (1,000 entries) still retains every submission
        # echo, including turns that were queued as follow-ups. The echo zipper paired
        # strictly positionally from sequence 0, so the first retained echoes zipped against
        # LATER turns' bodies — the developer's tail rebuilt with the last four answers
        # pinned four turns early and the four newest user messages orphaned at the bottom.
        # Both channels keep suffix order, so hydration must realign by turn count: the
        # leading echoes whose bodies were evicted project as bare user items, then each
        # assistant body follows its OWN user message and no tail user message orphans.
        bridge = _ScriptedBridge(harness="claude")
        self._scripted_claude_turns(bridge, range(1, 9))
        # The retained evidence window holds only the last four turns' bodies.
        bridge.evicted_before = 8
        bridge.evidence_frames = bridge.evidence_frames[8:]
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            [item.item_id for item in result.items],
            [
                "uuid-user-1",
                "uuid-user-2",
                "uuid-user-3",
                "uuid-user-4",
                "uuid-user-5",
                "uuid-agent-5",
                "uuid-result-5:result",
                "uuid-user-6",
                "uuid-agent-6",
                "uuid-result-6:result",
                "uuid-user-7",
                "uuid-agent-7",
                "uuid-result-7:result",
                "uuid-user-8",
                "uuid-agent-8",
                "uuid-result-8:result",
            ],
        )
        self.assertIsNone(result.total_items)

    async def test_rehydration_realigns_with_inflight_tail_turn(self) -> None:
        # Same evicted window, but the newest turn is still running at hydration: its body
        # has no result frame yet. The open body belongs to the LAST retained echo, so the
        # realignment must leave it paired instead of shifting the zip one turn further.
        bridge = _ScriptedBridge(harness="claude")
        self._scripted_claude_turns(bridge, range(1, 9))
        # Retained: turns 5-7 complete plus turn 8's assistant frame (no result yet).
        bridge.evicted_before = 8
        bridge.evidence_frames = bridge.evidence_frames[8:15]
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            [item.item_id for item in result.items],
            [
                "uuid-user-1",
                "uuid-user-2",
                "uuid-user-3",
                "uuid-user-4",
                "uuid-user-5",
                "uuid-agent-5",
                "uuid-result-5:result",
                "uuid-user-6",
                "uuid-agent-6",
                "uuid-result-6:result",
                "uuid-user-7",
                "uuid-agent-7",
                "uuid-result-7:result",
                "uuid-user-8",
                "uuid-agent-8",
            ],
        )

    async def test_rehydration_realigns_echoless_leading_bodies(self) -> None:
        # The mirror asymmetry: the transcript window evicted the oldest echoes while the
        # evidence window still carries their turn bodies. Those leading bodies belong to
        # turns whose user messages are gone; they project echo-less, in order, and the
        # retained echoes zip against the LAST retained bodies — never the first ones.
        bridge = _ScriptedBridge(harness="claude")
        self._scripted_claude_turns(bridge, range(1, 9))
        bridge.evicted_before = 4
        bridge.evidence_frames = bridge.evidence_frames[4:]
        # Only the last two turns' echoes survived in the transcript window.
        bridge.transcript_entries = bridge.transcript_entries[6:]
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            [item.item_id for item in result.items],
            [
                "uuid-agent-3",
                "uuid-result-3:result",
                "uuid-agent-4",
                "uuid-result-4:result",
                "uuid-agent-5",
                "uuid-result-5:result",
                "uuid-agent-6",
                "uuid-result-6:result",
                "uuid-user-7",
                "uuid-agent-7",
                "uuid-result-7:result",
                "uuid-user-8",
                "uuid-agent-8",
                "uuid-result-8:result",
            ],
        )

    async def test_rehydration_ignores_settled_turn_close_frames(self) -> None:
        # The live 2.1.218 wire closes every slash-command turn with ONE post-result
        # command_lifecycle/completed frame. When that close frame trails the retained
        # window it belongs to the SETTLED turn, not to a phantom in-flight one: counting
        # it as an open body would flush one leading body echo-less and shift the whole
        # realigned zip one turn early.
        bridge = _ScriptedBridge(harness="claude")
        self._scripted_claude_turns(bridge, range(1, 9))
        bridge.push_evidence(
            "state",
            {"type": "command_lifecycle", "command_uuid": "cmd-8", "state": "completed"},
        )
        # Retained: turns 5-8 complete plus turn 8's post-result lifecycle close frame.
        bridge.evicted_before = 8
        bridge.evidence_frames = bridge.evidence_frames[8:]
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            [item.item_id for item in result.items],
            [
                "uuid-user-1",
                "uuid-user-2",
                "uuid-user-3",
                "uuid-user-4",
                "uuid-user-5",
                "uuid-agent-5",
                "uuid-result-5:result",
                "uuid-user-6",
                "uuid-agent-6",
                "uuid-result-6:result",
                "uuid-user-7",
                "uuid-agent-7",
                "uuid-result-7:result",
                "uuid-user-8",
                "uuid-agent-8",
                "uuid-result-8:result",
            ],
        )

    async def test_rehydration_ignores_non_turn_trailing_frames(self) -> None:
        # `_opens_echo_turn_body` must be an ALLOWLIST of frames the live wire
        # proves are in-turn, not a denylist of the two shapes this round happened to see.
        # Anything else trailing the last retained result inflates the body count by one and
        # shifts the ENTIRE realigned tail one turn early — every answer above the user
        # message that produced it, the newest user message orphaned answerless: the exact
        # ghost the realignment exists to eliminate.
        #
        # The repo-provable trigger is the interrupt race: `_handle_result`
        # (claude_stream_state.py) pops the pending control entry when the turn settles, so an
        # interrupt control_response that lost the race to natural completion arrives
        # unmatched and is diverted to evidence AFTER that turn's result. Rate-limit telemetry
        # and any frame type the mapper has never seen trail results the same way.
        trailing_shapes: list[tuple[str, dict]] = [
            # Unmatched interrupt answer (shape from the 2.1.217 interrupt fixture).
            (
                "control_response",
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": "ar-claude-interrupt-1",
                        "response": {"still_queued": []},
                    },
                },
            ),
            # Telemetry that rides outside turns entirely.
            (
                "rate_limit_event",
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour"},
                    "session_id": "vendor-1",
                },
            ),
            # A shape no mapper version has seen: unknown is not proof of an open body.
            ("undocumented", {"type": "ar-test-undocumented-frame", "session_id": "vendor-1"}),
        ]
        for label, raw in trailing_shapes:
            with self.subTest(trailing=label):
                bridge = _ScriptedBridge(harness="claude")
                self._scripted_claude_turns(bridge, range(1, 9))
                bridge.push_evidence("state", raw)
                # Retained: turns 5-8 complete plus the one non-turn trailing frame.
                bridge.evicted_before = 8
                bridge.evidence_frames = bridge.evidence_frames[8:]
                projector = _projector(bridge, harness="claude")
                result = await projector.page(before_ordinal=None, limit=50)
                self.assertEqual(
                    [item.item_id for item in result.items],
                    [
                        "uuid-user-1",
                        "uuid-user-2",
                        "uuid-user-3",
                        "uuid-user-4",
                        "uuid-user-5",
                        "uuid-agent-5",
                        "uuid-result-5:result",
                        "uuid-user-6",
                        "uuid-agent-6",
                        "uuid-result-6:result",
                        "uuid-user-7",
                        "uuid-agent-7",
                        "uuid-result-7:result",
                        "uuid-user-8",
                        "uuid-agent-8",
                        "uuid-result-8:result",
                    ],
                )

    async def test_rehydration_pairs_lifecycle_only_inflight_turn(self) -> None:
        # The in-flight mirror: the newest turn has only lifecycle frames yet (a queued
        # follow-up just starting, no assistant content and no result). Its echo is the
        # LAST retained one and must stay the open turn — not orphan as a bare item — so
        # the answer frames arriving after hydration land under their own user message.
        bridge = _ScriptedBridge(harness="claude")
        self._scripted_claude_turns(bridge, range(1, 9))
        bridge.push_evidence(
            "state",
            {"type": "command_lifecycle", "command_uuid": "cmd-8", "state": "queued"},
        )
        bridge.push_evidence(
            "state",
            {"type": "command_lifecycle", "command_uuid": "cmd-8", "state": "started"},
        )
        # Retained: turns 5-7 complete; turn 8 has only its two lifecycle start frames.
        bridge.evicted_before = 8
        bridge.evidence_frames = bridge.evidence_frames[8:14] + bridge.evidence_frames[16:18]
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            [item.item_id for item in result.items],
            [
                "uuid-user-1",
                "uuid-user-2",
                "uuid-user-3",
                "uuid-user-4",
                "uuid-user-5",
                "uuid-agent-5",
                "uuid-result-5:result",
                "uuid-user-6",
                "uuid-agent-6",
                "uuid-result-6:result",
                "uuid-user-7",
                "uuid-agent-7",
                "uuid-result-7:result",
                "uuid-user-8",
            ],
        )
        # Turn 8's content crosses after hydration: it must land under its own echo.
        bridge.evidence_frames.append(
            EvidenceFrame(
                sequence=19,
                kind="state",
                created_at=NOW,
                raw={
                    "type": "assistant",
                    "uuid": "uuid-agent-8",
                    "session_id": "vendor-1",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "answer 8"}],
                    },
                },
            )
        )
        bridge.evidence_frames.append(
            EvidenceFrame(
                sequence=20,
                kind="completed",
                created_at=NOW,
                raw={
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "uuid": "uuid-result-8",
                },
            )
        )
        await projector.poll_once()
        continued = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            [item.item_id for item in continued.items][-3:],
            ["uuid-user-8", "uuid-agent-8", "uuid-result-8:result"],
        )

    async def test_rehydration_realigns_across_transcript_pages(self) -> None:
        # A genuinely long conversation retains more echoes than one 500-entry transcript
        # page: the realignment must count every retained echo (hydration drains the
        # window), or it undercounts the orphans and re-introduces the constant-offset
        # mis-zip it exists to fix — here 600 echoes against the last three turn bodies.
        bridge = _ScriptedBridge(harness="claude")
        self._scripted_claude_turns(bridge, range(1, 601))
        bridge.evicted_before = 1194
        bridge.evidence_frames = bridge.evidence_frames[1194:]
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=700)
        ids = [item.item_id for item in result.items]
        self.assertEqual(len(ids), 606)
        self.assertEqual(ids[:4], ["uuid-user-1", "uuid-user-2", "uuid-user-3", "uuid-user-4"])
        self.assertEqual(
            ids[-9:],
            [
                "uuid-user-598",
                "uuid-agent-598",
                "uuid-result-598:result",
                "uuid-user-599",
                "uuid-agent-599",
                "uuid-result-599:result",
                "uuid-user-600",
                "uuid-agent-600",
                "uuid-result-600:result",
            ],
        )
        # The 597 evicted-body echoes project bare, in order, before the paired tail.
        self.assertEqual(ids[596], "uuid-user-597")
        self.assertEqual(ids[597], "uuid-user-598")


class DormantReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_release_dormant_state_frees_heavy_projection_and_retires_shell(self) -> None:
        # A dormant projector must release its full ProjectionStore, the
        # live-turn/request id-sets, and retained envelopes instead of lingering as a registered
        # tombstone until 32-LRU eviction. After release the shell is retired (matches() is False),
        # so the next access re-creates a fresh projector.
        bridge = _ScriptedBridge(harness="codex")
        _codex_turn(bridge, "turn-1")
        _codex_turn(bridge, "turn-2")
        projector = _projector(bridge)
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertGreater(len(result.items), 0)
        self.assertGreater(len(projector._native.live_turn_ids), 0)

        projector._release_dormant_state()

        self.assertTrue(projector._closed)
        self.assertEqual(
            projector._stream.store.page(before_ordinal=None, limit=50, total_known=True).items,
            (),
        )
        self.assertEqual(projector._native.live_turn_ids, {})
        self.assertEqual(projector._native.live_request_ids, {})
        self.assertEqual(projector._stream.retention, [])
        # The retired shell no longer matches its identity, so a re-access re-creates it.
        self.assertFalse(projector.matches(_identity("codex")))
