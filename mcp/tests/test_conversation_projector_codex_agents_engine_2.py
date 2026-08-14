from __future__ import annotations

import asyncio
import threading
import unittest
from dataclasses import replace
from unittest import mock

from _agent_wire_fixtures import agent_message_item, item_completed_params
from agents_remember.errors import NativeHistoryUnavailable
from agents_remember.models.conversations.evidence import (
    NativeEvidenceFrame,
)
from test_conversation_active_service import _codex_turn
from test_conversation_projector_codex_agents import (
    AGENT,
    NOW,
    PARENT,
    _agent_turn_frames,
    _MultiplexedBridge,
    _projector,
)


class CodexAgentEngineTests2(unittest.IsolatedAsyncioTestCase):
    async def test_same_child_concurrent_hydration_is_singleflight(self) -> None:
        bridge = _MultiplexedBridge()
        _codex_turn(bridge, "turn-parent")
        _agent_turn_frames(bridge, AGENT, "agent-turn-1", "agent-live-1")
        bridge.agent_native_frames[AGENT] = [
            NativeEvidenceFrame(
                native_id="history-one",
                native_parent_id="agent-turn-0",
                native_type="agentMessage",
                created_at=NOW,
                raw={
                    "id": "history-one",
                    "type": "agentMessage",
                    "text": "one shared acquisition",
                },
            )
        ]
        started = bridge.agent_native_started[AGENT] = threading.Event()
        release = bridge.agent_native_release[AGENT] = threading.Event()
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=50)

        first = asyncio.create_task(projector.refresh_agent_native(AGENT))
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        second = asyncio.create_task(projector.refresh_agent_native(AGENT))
        await asyncio.sleep(0)
        release.set()
        outcomes = await asyncio.gather(first, second)

        self.assertEqual([outcome.status for outcome in outcomes], ["hydrated", "hydrated"])
        self.assertEqual(bridge.agent_native_reads, [AGENT])
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertIn(f"{AGENT}:history-one", {item.item_id for item in result.items})

    async def test_slow_child_does_not_block_parent_poll_or_sibling_hydration(self) -> None:
        slow = "agent-slow"
        sibling = "agent-sibling"
        bridge = _MultiplexedBridge()
        _codex_turn(bridge, "turn-parent")
        _agent_turn_frames(bridge, slow, "agent-turn-slow", "slow-live")
        _agent_turn_frames(bridge, sibling, "agent-turn-sibling", "sibling-live")
        bridge.agent_native_frames[slow] = [
            NativeEvidenceFrame(
                native_id="slow-history",
                native_parent_id="slow-old",
                native_type="agentMessage",
                created_at=NOW,
                raw={
                    "id": "slow-history",
                    "type": "agentMessage",
                    "text": "slow child result",
                },
            )
        ]
        bridge.agent_native_frames[sibling] = [
            NativeEvidenceFrame(
                native_id="sibling-history",
                native_parent_id="sibling-old",
                native_type="agentMessage",
                created_at=NOW,
                raw={
                    "id": "sibling-history",
                    "type": "agentMessage",
                    "text": "sibling result",
                },
            )
        ]
        started = bridge.agent_native_started[slow] = threading.Event()
        release = bridge.agent_native_release[slow] = threading.Event()
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=100)

        slow_task = asyncio.create_task(projector.refresh_agent_native(slow))
        self.assertTrue(await asyncio.to_thread(started.wait, 1))
        bridge.push_frame(
            "codex-notification",
            item_completed_params(
                PARENT,
                "turn-parent",
                agent_message_item("parent-during-slow-child", "parent stayed live"),
            ),
            thread_id=PARENT,
        )

        sibling_result, _ = await asyncio.wait_for(
            asyncio.gather(projector.refresh_agent_native(sibling), projector.poll_once()),
            timeout=1,
        )
        self.assertEqual(sibling_result.status, "hydrated")
        during_slow = await projector.page(before_ordinal=None, limit=100)
        during_slow_ids = {item.item_id for item in during_slow.items}
        self.assertIn("parent-during-slow-child", during_slow_ids)
        self.assertIn(f"{sibling}:sibling-history", during_slow_ids)
        self.assertNotIn(f"{slow}:slow-history", during_slow_ids)

        release.set()
        self.assertEqual((await slow_task).status, "hydrated")
        self.assertCountEqual(bridge.agent_native_reads, [slow, sibling])

    async def test_selected_child_singleflight_registry_has_a_visible_hard_cap(self) -> None:
        slow = "agent-slow"
        sibling = "agent-capacity-refused"
        bridge = _MultiplexedBridge()
        _codex_turn(bridge, "turn-parent")
        _agent_turn_frames(bridge, slow, "agent-turn-slow", "slow-live")
        _agent_turn_frames(bridge, sibling, "agent-turn-sibling", "sibling-live")
        started = bridge.agent_native_started[slow] = threading.Event()
        release = bridge.agent_native_release[slow] = threading.Event()
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=100)

        with mock.patch(
            "agents_remember.serving.conversation.active.projector.child_history.MAX_AGENT_NATIVE_INFLIGHT",
            1,
        ):
            slow_task = asyncio.create_task(projector.refresh_agent_native(slow))
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            refused = await projector.refresh_agent_native(sibling)
            self.assertEqual(refused.status, "unavailable")
            self.assertEqual(refused.code, "child-history-capacity")
            self.assertNotIn(sibling, bridge.agent_native_reads)
            release.set()
            self.assertEqual((await slow_task).status, "hydrated")

    async def test_unavailable_child_retry_recovers_same_item_at_revision_two(self) -> None:
        bridge = _MultiplexedBridge()
        _codex_turn(bridge, "turn-parent")
        _agent_turn_frames(bridge, AGENT, "agent-turn-1", "agent-live-1")
        bridge.agent_native_errors[AGENT] = NativeHistoryUnavailable(
            "temporary child read failure",
            code="temporary-child-read",
        )
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=50)

        failed = await projector.refresh_agent_native(AGENT)
        self.assertEqual(failed.status, "unavailable")
        failed_page = await projector.page(before_ordinal=None, limit=50)
        failed_item = next(
            item for item in failed_page.items if item.item_id == f"agent-history:{AGENT}"
        )
        self.assertEqual(failed_item.revision, 1)
        self.assertEqual(failed_item.phase, "failed")

        del bridge.agent_native_errors[AGENT]
        bridge.agent_native_frames[AGENT] = [
            NativeEvidenceFrame(
                native_id="recovered-history",
                native_parent_id="agent-turn-0",
                native_type="agentMessage",
                created_at=NOW,
                raw={
                    "id": "recovered-history",
                    "type": "agentMessage",
                    "text": "retry worked",
                },
            )
        ]
        recovered = await projector.refresh_agent_native(AGENT)
        self.assertEqual(recovered.status, "hydrated")
        recovered_page = await projector.page(before_ordinal=None, limit=50)
        recovered_item = next(
            item for item in recovered_page.items if item.item_id == f"agent-history:{AGENT}"
        )
        self.assertEqual(recovered_item.revision, 2)
        self.assertEqual(recovered_item.phase, "completed")
        self.assertIn(f"{AGENT}:recovered-history", {item.item_id for item in recovered_page.items})
        self.assertEqual(bridge.agent_native_reads, [AGENT, AGENT])

    async def test_per_thread_twin_suppression_and_lazy_agent_native_walk(self) -> None:
        bridge = _MultiplexedBridge()
        _codex_turn(bridge, "turn-1")
        _agent_turn_frames(bridge, AGENT, "agent-turn-1", "agent-msg-1")
        projector = _projector(bridge)
        live = await projector.page(before_ordinal=None, limit=50)
        live_ids = [item.item_id for item in live.items]
        self.assertIn("agent-msg-1", live_ids)

        # The agent thread persists its settled turn through thread/read under
        # disjoint positional ids, plus one genuinely older turn never seen live.
        bridge.agent_native_frames[AGENT] = [
            NativeEvidenceFrame(
                native_id="item-0",
                native_parent_id="agent-turn-0",
                native_type="agentMessage",
                created_at=NOW,
                raw={"id": "item-0", "type": "agentMessage", "text": "earlier"},
            ),
            NativeEvidenceFrame(
                native_id="item-1",
                native_parent_id="agent-turn-1",
                native_type="agentMessage",
                created_at=NOW,
                raw={"id": "item-1", "type": "agentMessage", "text": "twin"},
            ),
        ]
        await projector.refresh_agent_native(AGENT)
        result = await projector.page(before_ordinal=None, limit=50)
        ids = [item.item_id for item in result.items]
        self.assertNotIn(f"{AGENT}:item-1", ids, "the live-settled agent turn must not twin")
        self.assertIn(f"{AGENT}:item-0", ids, "genuine agent history must backfill")
        by_id = {item.item_id: item for item in result.items}
        item0_agent = by_id[f"{AGENT}:item-0"].agent
        assert item0_agent is not None
        self.assertEqual(item0_agent.agent_id, AGENT)

        # Bucket isolation: a PARENT native frame whose turn id collides with the
        # agent thread's live turn id is genuine parent history and survives.
        bridge.native_frames.append(
            NativeEvidenceFrame(
                native_id="parent-item-9",
                native_parent_id="agent-turn-1",
                native_type="agentMessage",
                created_at=NOW,
                raw={"id": "parent-item-9", "type": "agentMessage", "text": "parent history"},
            )
        )
        bridge.push_frame(
            "codex-notification",
            {"threadId": PARENT, "turnId": "turn-1", "tokenUsage": {"totalTokens": 1}},
            thread_id=PARENT,
        )
        await projector.poll_once()
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertIn(
            "parent-item-9",
            [item.item_id for item in result.items],
            "the agent bucket must never suppress the parent re-walk",
        )

    async def test_lazy_agent_walk_refuses_unlived_or_parent_threads(self) -> None:
        bridge = _MultiplexedBridge()
        _codex_turn(bridge, "turn-1")
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=50)
        # No live evidence for this agent thread: the walk stays closed.
        bridge.agent_native_frames["agent-t-9"] = [
            NativeEvidenceFrame(
                native_id="item-1",
                native_parent_id="agent-turn-9",
                native_type="agentMessage",
                created_at=NOW,
                raw={"id": "item-1", "type": "agentMessage", "text": "eager"},
            )
        ]
        await projector.refresh_agent_native("agent-t-9")
        await projector.refresh_agent_native(PARENT)
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertNotIn("item-1", [item.item_id for item in result.items])

    async def test_malformed_agent_frame_unknown_vendor_carries_the_agent_ref(self) -> None:
        """Preserved evidence from a malformed AGENT-thread
        frame is tagged with the agent ref and filters into the agent view — never the
        parent's."""

        bridge = _MultiplexedBridge()
        bridge.snapshot = replace(
            bridge.snapshot,
            raw={"agentRegistry": {AGENT: {"status": "running", "agentPath": "/root/agent_one"}}},
        )
        # A malformed agent-thread frame (a delta whose payload is not text) and the
        # same malformed shape on the parent thread for contrast.
        bridge.push_frame(
            "codex-notification",
            {"threadId": AGENT, "itemId": "agent-item-9", "delta": 42},
            thread_id=AGENT,
            native_method="item/agentMessage/delta",
        )
        bridge.push_frame(
            "codex-notification",
            {"threadId": PARENT, "itemId": "parent-item-9", "delta": 42},
            thread_id=PARENT,
            native_method="item/agentMessage/delta",
        )
        projector = _projector(bridge)
        result = await projector.page(before_ordinal=None, limit=50)
        unknown = [item for item in result.items if item.kind == "unknown-vendor"]
        self.assertEqual(len(unknown), 2)
        agent_item, parent_item = unknown
        assert agent_item.agent is not None
        self.assertEqual(agent_item.agent.agent_id, AGENT)
        self.assertEqual(agent_item.agent.agent_path, "/root/agent_one")
        # The agent view keeps the malformed frame's evidence; the parent's identical
        # failure stays untagged exactly as before.
        agent_view = [item for item in result.items if item.agent is not None]
        self.assertEqual(agent_view, [agent_item])
        self.assertIsNone(parent_item.agent)
