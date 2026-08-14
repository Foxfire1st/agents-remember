from __future__ import annotations

import unittest
from dataclasses import replace
from typing import Any

from _agent_wire_fixtures import (
    CollabAgents,
    agent_message_item,
    collab_agent_tool_call_item,
    item_completed_params,
    item_started_params,
    sub_agent_activity_item,
    turn_completed_params,
    turn_started_params,
)
from agents_remember.errors import NativeHistoryUnavailable
from agents_remember.models.conversations.content import (
    MarkdownBlock,
    TextBlock,
)
from agents_remember.models.conversations.control_wire import (
    PendingInteraction,
)
from agents_remember.models.conversations.evidence import (
    NativeEvidenceFrame,
)
from test_conversation_active_service import _codex_turn
from test_conversation_projector_codex_agents import (
    AGENT,
    NOW,
    PARENT,
    _agent_turn_frames,
    _collab_item,
    _MultiplexedBridge,
    _projector,
)


def _roster_of(page: Any) -> dict[str, str]:
    """Roster rows as ``{agent_id: status}`` from one projected page."""
    return {
        item.agent.agent_id: item.agent.status
        for item in page.items
        if item.item_id.startswith("codex-agent-") and item.agent is not None
    }


def _spawn_frames(bridge: _MultiplexedBridge, children: list[str]) -> None:
    """Historical ``subAgentActivity`` starts for every child, in order."""
    bridge.native_frames.extend(
        NativeEvidenceFrame(
            native_id=f"spawn-{index}",
            native_parent_id="parent-turn",
            native_type="subAgentActivity",
            created_at=NOW,
            raw=sub_agent_activity_item(
                f"spawn-{index}",
                kind="started",
                agent_thread_id=child,
                agent_path=f"/root/{child}",
            ),
        )
        for index, child in enumerate(children)
    )


def _registry_snapshot(
    bridge: _MultiplexedBridge, children: list[str], completed: set[str]
) -> None:
    """The persistent adapter snapshot: completed status for one wave, running otherwise."""
    bridge.snapshot = replace(
        bridge.snapshot,
        raw={
            "agentRegistry": {
                child: {
                    "status": "completed" if child in completed else "running",
                    "agentPath": f"/root/{child}",
                }
                for child in children
            }
        },
    )


def _complete_wave(bridge: _MultiplexedBridge, wave_two: list[str]) -> None:
    """Push one ``turn/completed`` notification per wave-two child."""
    for index, child in enumerate(wave_two):
        bridge.push_frame(
            "codex-notification",
            turn_completed_params(child, f"wave-two-turn-{index}"),
            thread_id=child,
            native_method="turn/completed",
        )


class CodexAgentEngineTests1(unittest.IsolatedAsyncioTestCase):
    async def test_two_wave_roster_rehydrates_exactly_without_root_or_status_regression(
        self,
    ) -> None:
        """Six historical starts reconcile against the persistent live registry."""

        wave_one = [f"wave-one-{index}" for index in range(3)]
        wave_two = [f"wave-two-{index}" for index in range(3)]
        children = [*wave_one, *wave_two]
        bridge = _MultiplexedBridge()
        _spawn_frames(bridge, children)
        _registry_snapshot(bridge, children, completed=set(wave_one))
        projector = _projector(bridge)
        initial = await projector.page(before_ordinal=None, limit=100)
        initial_roster = _roster_of(initial)
        self.assertEqual(
            initial_roster,
            {
                **{child: "completed" for child in wave_one},
                **{child: "running" for child in wave_two},
            },
        )
        self.assertNotIn("root", initial_roster)

        _complete_wave(bridge, wave_two)
        _registry_snapshot(bridge, children, completed=set(children))
        await projector.poll_once()
        settled = await projector.page(before_ordinal=None, limit=100)
        settled_roster = _roster_of(settled)
        self.assertEqual(settled_roster, {child: "completed" for child in children})

        # A fresh backend projector has only persisted starts plus the persistent
        # adapter snapshot. It must reconstruct the same six terminal seats.
        reloaded = _projector(bridge)
        restarted_page = await reloaded.page(before_ordinal=None, limit=100)
        restarted_roster = _roster_of(restarted_page)
        self.assertEqual(restarted_roster, {child: "completed" for child in children})
        self.assertNotIn("root", restarted_roster)
        await projector.close()
        await reloaded.close()

    async def test_incident_stream_multiplexes_into_one_projection(self) -> None:
        """The 2026-07-24 incident shape: collab spawn, then interleaved agent + parent traffic."""

        bridge = _MultiplexedBridge()
        bridge.snapshot = replace(
            bridge.snapshot,
            raw={"agentRegistry": {AGENT: {"status": "running", "agentPath": "/root/agent_one"}}},
        )
        # Parent timeline: the collab spawn, then the subAgentActivity binding.
        bridge.push_frame(
            "transcript",
            item_completed_params(
                PARENT,
                "turn-1",
                _collab_item(
                    "collab-1",
                    "spawnAgent",
                    receivers=[AGENT],
                    states={AGENT: {"status": "running"}},
                ),
            ),
            thread_id=PARENT,
        )
        bridge.push_frame(
            "transcript",
            item_completed_params(
                PARENT,
                "turn-1",
                sub_agent_activity_item(
                    "collab-2",
                    kind="started",
                    agent_thread_id=AGENT,
                    agent_path="/root/agent_one",
                ),
            ),
            thread_id=PARENT,
        )
        # Agent-thread turn traffic interleaved with parent traffic.
        _agent_turn_frames(bridge, AGENT, "agent-turn-1", "agent-item-1")
        bridge.push_frame(
            "codex-notification",
            item_started_params(PARENT, "turn-1", agent_message_item("turn-1-agent", "spawned")),
            thread_id=PARENT,
        )
        bridge.push_frame(
            "completed",
            turn_completed_params(PARENT, "turn-1"),
            thread_id=PARENT,
        )

        projector = _projector(bridge)
        result = await projector.page(before_ordinal=None, limit=50)
        by_id = {item.item_id: item for item in result.items}

        # One cursor domain: a single contiguous ordinal space over both threads.
        self.assertEqual(
            [item.global_ordinal for item in result.items],
            list(range(1, len(result.items) + 1)),
        )

        # The roster is one upserted item, settled by the agent's turn completion,
        # with the agentPath the subAgentActivity/registry evidence bound.
        roster_rows = [item for item in result.items if item.item_id == f"codex-agent-{AGENT}"]
        self.assertEqual(len(roster_rows), 1)
        roster = roster_rows[0]
        assert roster.agent is not None
        self.assertEqual(roster.agent.agent_id, AGENT)
        self.assertEqual(roster.agent.status, "completed")
        self.assertEqual(roster.agent.agent_path, "/root/agent_one")

        # Agent-thread items carry the bound ref; the delta streamed onto the aggregate.
        agent_item = by_id["agent-item-1"]
        assert agent_item.agent is not None
        self.assertEqual(agent_item.agent.agent_id, AGENT)
        self.assertEqual(agent_item.agent.agent_path, "/root/agent_one")
        markdown = next(b for b in agent_item.blocks if isinstance(b, MarkdownBlock))
        self.assertEqual(markdown.markdown, "partial more")
        turn_result = by_id["turn-result:agent-turn-1"]
        assert turn_result.agent is not None
        self.assertEqual(turn_result.agent.agent_id, AGENT)

        # Parent items are byte-identical to pre-multiplex: no agent dimension anywhere.
        for item_id in ("collab-1", "turn-1-agent", "turn-result:turn-1"):
            self.assertIsNone(by_id[item_id].agent, item_id)

    async def test_roster_upserts_retain_the_final_message_block(self) -> None:
        """A blocks=() roster upsert never wipes the final message."""

        bridge = _MultiplexedBridge()
        # Terminal collab binds the final message into the roster...
        bridge.push_frame(
            "transcript",
            item_completed_params(
                PARENT,
                "turn-1",
                _collab_item(
                    "collab-7",
                    "wait",
                    receivers=[AGENT],
                    states={AGENT: {"status": "completed", "message": "the fix is in"}},
                ),
            ),
            thread_id=PARENT,
        )
        # ...then later block-less lifecycle roster upserts land (turn/started and
        # turn/completed know nothing about the final message).
        bridge.push_frame(
            "codex-notification",
            turn_started_params(AGENT, "agent-turn-2"),
            thread_id=AGENT,
            native_method="turn/started",
        )
        bridge.push_frame(
            "codex-notification",
            turn_completed_params(AGENT, "agent-turn-2"),
            thread_id=AGENT,
            native_method="turn/completed",
        )
        projector = _projector(bridge)
        result = await projector.page(before_ordinal=None, limit=50)
        roster = next(item for item in result.items if item.item_id == f"codex-agent-{AGENT}")
        final = next(b for b in roster.blocks if isinstance(b, TextBlock))
        self.assertEqual(final.block_id, "final-message")
        self.assertEqual(final.text, "the fix is in")

    async def test_multiplexed_pending_interactions_project_labeled_and_resolve(self) -> None:
        bridge = _MultiplexedBridge()
        parent_pending = PendingInteraction(
            interaction_id="parent-approval",
            kind="approval",
            prompt="run ls?",
            created_at=NOW,
            raw={"threadId": PARENT},
        )
        agent_pending = PendingInteraction(
            interaction_id="agent-approval",
            kind="approval",
            prompt="git status?",
            created_at=NOW,
            raw={"threadId": AGENT, "agentLabel": "/root/agent_one"},
        )
        bridge.snapshot = replace(
            bridge.snapshot,
            pending_interaction=parent_pending,
            pending_interactions=(parent_pending, agent_pending),
            raw={"agentRegistry": {AGENT: {"status": "running", "agentPath": "/root/agent_one"}}},
        )
        projector = _projector(bridge)
        result = await projector.page(before_ordinal=None, limit=50)
        by_id = {item.item_id: item for item in result.items}

        parent_item = by_id["parent-approval"]
        self.assertEqual(parent_item.lane, "interaction")
        self.assertEqual(parent_item.phase, "waiting")
        self.assertIsNone(parent_item.agent)

        agent_item = by_id["agent-approval"]
        self.assertEqual(agent_item.lane, "interaction")
        self.assertEqual(agent_item.phase, "waiting")
        assert agent_item.agent is not None
        self.assertEqual(agent_item.agent.agent_id, AGENT)
        self.assertEqual(agent_item.agent.nickname, "/root/agent_one")
        self.assertEqual(agent_item.agent.agent_path, "/root/agent_one")

        # The agent request clears while the parent's stays open: resolve per id.
        bridge.snapshot = replace(bridge.snapshot, pending_interactions=(parent_pending,))
        await projector.poll_once()
        result = await projector.page(before_ordinal=None, limit=50)
        by_id = {item.item_id: item for item in result.items}
        self.assertEqual(by_id["agent-approval"].phase, "unknown")
        self.assertEqual(by_id["parent-approval"].phase, "waiting")

        # Full clear settles the singular parent path exactly as today.
        bridge.snapshot = replace(
            bridge.snapshot, pending_interaction=None, pending_interactions=()
        )
        await projector.poll_once()
        result = await projector.page(before_ordinal=None, limit=50)
        by_id = {item.item_id: item for item in result.items}
        self.assertEqual(by_id["parent-approval"].phase, "unknown")

    async def test_concurrent_parent_pendings_all_project_and_resolve_per_id(self) -> None:
        """Concurrent parent pendings (the 2026-07-26 kill scenario's healthy form)."""
        bridge = _MultiplexedBridge()
        parent_first = PendingInteraction(
            interaction_id="parent-approval-1",
            kind="approval",
            prompt="run ls?",
            created_at=NOW,
            raw={"threadId": PARENT},
        )
        parent_second = PendingInteraction(
            interaction_id="parent-approval-2",
            kind="approval",
            prompt="run pwd?",
            created_at=NOW,
            raw={"threadId": PARENT},
        )
        agent_pending = PendingInteraction(
            interaction_id="agent-approval",
            kind="approval",
            prompt="git status?",
            created_at=NOW,
            raw={"threadId": AGENT, "agentLabel": "/root/agent_one"},
        )
        # The singular slot carries the parent's OLDEST pending (back-compat).
        bridge.snapshot = replace(
            bridge.snapshot,
            pending_interaction=parent_first,
            pending_interactions=(parent_first, parent_second, agent_pending),
            raw={"agentRegistry": {AGENT: {"status": "running", "agentPath": "/root/agent_one"}}},
        )
        projector = _projector(bridge)
        result = await projector.page(before_ordinal=None, limit=50)
        by_id = {item.item_id: item for item in result.items}

        for interaction_id in ("parent-approval-1", "parent-approval-2", "agent-approval"):
            self.assertIn(interaction_id, by_id)
            self.assertEqual(by_id[interaction_id].lane, "interaction")
            self.assertEqual(by_id[interaction_id].phase, "waiting")
        # Parent-thread entries project plainly; the agent entry carries its identity.
        self.assertIsNone(by_id["parent-approval-1"].agent)
        self.assertIsNone(by_id["parent-approval-2"].agent)
        assert by_id["agent-approval"].agent is not None
        self.assertEqual(by_id["agent-approval"].agent.agent_id, AGENT)

        # The second parent request settles while the others stay open.
        bridge.snapshot = replace(
            bridge.snapshot,
            pending_interactions=(parent_first, agent_pending),
        )
        await projector.poll_once()
        result = await projector.page(before_ordinal=None, limit=50)
        by_id = {item.item_id: item for item in result.items}
        self.assertEqual(by_id["parent-approval-2"].phase, "unknown")
        self.assertEqual(by_id["parent-approval-1"].phase, "waiting")
        self.assertEqual(by_id["agent-approval"].phase, "waiting")

    async def test_parent_singular_rotation_resolves_evicted_and_keeps_rotated_live(self) -> None:
        """A→B singular rotation: the evicted id settles; the rotated id stays live."""
        bridge = _MultiplexedBridge()
        parent_first = PendingInteraction(
            interaction_id="parent-approval-1",
            kind="approval",
            prompt="run ls?",
            created_at=NOW,
            raw={"threadId": PARENT},
        )
        parent_second = PendingInteraction(
            interaction_id="parent-approval-2",
            kind="approval",
            prompt="run pwd?",
            created_at=NOW,
            raw={"threadId": PARENT},
        )
        bridge.snapshot = replace(
            bridge.snapshot,
            pending_interaction=parent_first,
            pending_interactions=(parent_first, parent_second),
        )
        projector = _projector(bridge)
        result = await projector.page(before_ordinal=None, limit=50)
        by_id = {item.item_id: item for item in result.items}
        self.assertEqual(by_id["parent-approval-1"].phase, "waiting")
        self.assertEqual(by_id["parent-approval-2"].phase, "waiting")

        # The oldest is answered: the adapter rotates the singular slot to the
        # next-oldest, which leaves the multiplexed tuple for the same id.
        bridge.snapshot = replace(
            bridge.snapshot,
            pending_interaction=parent_second,
            pending_interactions=(parent_second,),
        )
        await projector.poll_once()
        result = await projector.page(before_ordinal=None, limit=50)
        by_id = {item.item_id: item for item in result.items}
        self.assertEqual(by_id["parent-approval-1"].phase, "unknown")
        self.assertEqual(by_id["parent-approval-2"].phase, "waiting")

        # The rotated request settles: both end resolved, none left waiting.
        bridge.snapshot = replace(
            bridge.snapshot, pending_interaction=None, pending_interactions=()
        )
        await projector.poll_once()
        result = await projector.page(before_ordinal=None, limit=50)
        by_id = {item.item_id: item for item in result.items}
        self.assertEqual(by_id["parent-approval-1"].phase, "unknown")
        self.assertEqual(by_id["parent-approval-2"].phase, "unknown")

    async def test_selected_agent_backfills_content_when_live_delivery_is_partial(self) -> None:
        """The 2026-07-26 live-verification gap: roster/lifecycle crossed, content did not.

        The vendor's auto-attach is best-effort — a fast agent outruns it and its
        content events never reach the connection. Parent paging stays metadata-first;
        selecting the agent walks only that native history.
        """
        bridge = _MultiplexedBridge()
        _codex_turn(bridge, "turn-1")
        # Only spawn + turn lifecycle for the agent crosses live; its content does not.
        bridge.push_frame(
            "codex-notification",
            item_completed_params(
                PARENT,
                "turn-1",
                collab_agent_tool_call_item(
                    "collab-1",
                    "spawnAgent",
                    agents=CollabAgents(PARENT, receiver_thread_ids=[AGENT]),
                ),
            ),
            thread_id=PARENT,
        )
        bridge.push_frame(
            "codex-notification",
            turn_started_params(AGENT, "agent-turn-1"),
            thread_id=AGENT,
            native_method="turn/started",
        )
        bridge.push_frame(
            "codex-notification",
            turn_completed_params(AGENT, "agent-turn-1"),
            thread_id=AGENT,
            native_method="turn/completed",
        )
        # Native authority holds the agent's actual work (thread/read proven live).
        bridge.agent_native_frames[AGENT] = [
            NativeEvidenceFrame(
                native_id="call-1",
                native_parent_id="agent-turn-1",
                native_type="subAgentActivity",
                created_at=NOW,
                raw={
                    "id": "call-1",
                    "type": "subAgentActivity",
                    "kind": "interacted",
                    "agentThreadId": "other-agent",
                    "agentPath": "/root",
                },
            ),
            NativeEvidenceFrame(
                native_id="msg-1",
                native_parent_id="agent-turn-1",
                native_type="agentMessage",
                created_at=NOW,
                raw={"id": "msg-1", "type": "agentMessage", "text": "agent work result"},
            ),
        ]
        projector = _projector(bridge)
        result = await projector.page(before_ordinal=None, limit=50)
        by_id = {item.item_id: item for item in result.items}

        # Parent paging establishes the roster but does not hydrate every discovered child.
        scoped = f"{AGENT}:msg-1"
        self.assertNotIn(scoped, by_id)
        self.assertEqual(bridge.agent_native_reads, [])

        hydration = await projector.refresh_agent_native(AGENT)
        self.assertEqual(hydration.status, "hydrated")
        result = await projector.page(before_ordinal=None, limit=50)
        by_id = {item.item_id: item for item in result.items}

        # The selected agent's content is backfilled and attributed even though it never crossed live;
        # native ids are thread-scoped so the forked copies cannot collide with the parent's.
        self.assertIn(scoped, by_id)
        scoped_agent = by_id[scoped].agent
        assert scoped_agent is not None
        self.assertEqual(scoped_agent.agent_id, AGENT)
        self.assertNotIn("msg-1", by_id)
        # Roster + turn-result still present from the live channel; the walk minted
        # no roster of its own (no duplicate for the referenced other agent).
        self.assertIn(f"codex-agent-{AGENT}", by_id)
        self.assertIn("turn-result:agent-turn-1", by_id)
        self.assertNotIn("codex-agent-other-agent", by_id)
        self.assertNotIn(f"{AGENT}:codex-agent-other-agent", by_id)

        # The selected walk happens once per projector; ordinary pages never re-read it.
        self.assertIn(AGENT, projector._child_history.walked)
        await projector.page(before_ordinal=None, limit=50)
        by_id = {
            item.item_id: item
            for item in (await projector.page(before_ordinal=None, limit=50)).items
        }
        self.assertIn(f"{AGENT}:msg-1", by_id)
        self.assertEqual(bridge.agent_native_reads, [AGENT])

    async def test_second_wave_child_failure_is_local_and_siblings_hydrate_on_selection(
        self,
    ) -> None:
        bridge = _MultiplexedBridge()
        _codex_turn(bridge, "turn-parent")
        children = ("agent-wave1", "agent-wave2-bad", "agent-wave2-good")
        for index, child in enumerate(children, start=1):
            _agent_turn_frames(bridge, child, f"agent-turn-{index}", f"agent-live-{index}")
        bridge.agent_native_frames["agent-wave1"] = [
            NativeEvidenceFrame(
                native_id="history-wave1",
                native_parent_id="older-wave1",
                native_type="agentMessage",
                created_at=NOW,
                raw={"id": "history-wave1", "type": "agentMessage", "text": "wave one history"},
            )
        ]
        bridge.agent_native_errors["agent-wave2-bad"] = NativeHistoryUnavailable(
            "child history page exceeded its bounded acquisition contract",
            code="bounded-rpc-failed",
        )
        bridge.agent_native_frames["agent-wave2-good"] = [
            NativeEvidenceFrame(
                native_id="history-wave2-good",
                native_parent_id="older-wave2-good",
                native_type="agentMessage",
                created_at=NOW,
                raw={
                    "id": "history-wave2-good",
                    "type": "agentMessage",
                    "text": "wave two sibling history",
                },
            )
        ]
        projector = _projector(bridge)

        initial = await projector.page(before_ordinal=None, limit=100)
        initial_ids = {item.item_id for item in initial.items}
        self.assertIn("turn-parent-agent", initial_ids)
        self.assertEqual(bridge.agent_native_reads, [])

        bad = await projector.refresh_agent_native("agent-wave2-bad")
        self.assertEqual(bad.status, "unavailable")
        self.assertEqual(bad.code, "bounded-rpc-failed")
        after_bad = await projector.page(before_ordinal=None, limit=100)
        after_bad_ids = {item.item_id for item in after_bad.items}
        self.assertIn("agent-history:agent-wave2-bad", after_bad_ids)
        self.assertIn("turn-parent-agent", after_bad_ids)

        wave1 = await projector.refresh_agent_native("agent-wave1")
        good = await projector.refresh_agent_native("agent-wave2-good")
        self.assertEqual(wave1.status, "hydrated")
        self.assertEqual(good.status, "hydrated")
        final = await projector.page(before_ordinal=None, limit=100)
        final_ids = {item.item_id for item in final.items}
        self.assertIn("agent-wave1:history-wave1", final_ids)
        self.assertIn("agent-wave2-good:history-wave2-good", final_ids)
        self.assertIn("agent-history:agent-wave2-bad", final_ids)
        self.assertIn("turn-parent-agent", final_ids)
        self.assertEqual(
            bridge.agent_native_reads,
            ["agent-wave2-bad", "agent-wave1", "agent-wave2-good"],
        )
