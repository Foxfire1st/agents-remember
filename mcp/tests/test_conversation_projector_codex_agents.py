"""Codex projector sub-agent mappings: roster, demux, multiplexed interactions.

Fixtures are synthesized minimal shapes built by ``_agent_wire_fixtures``, proven field-for-field against the vendored codex app-server protocol
(``app-server-protocol/src/protocol/v2/{item,turn,thread,notification}.rs`` and the live
spawn suite ``app-server/tests/suite/v2/turn_start.rs`` ~3544-3868):
``collabAgentToolCall``/``subAgentActivity`` items, agent-thread
``thread/status/changed`` + ``turn/started``/``turn/completed`` notifications,
multiplexed ``pending_interactions``. The parent thread id is ``vendor-1`` —
the projection identity's ``vendor_conversation_id`` — and every agent frame
carries its verbatim ``threadId`` demux key. Intentionally malformed shapes (the
unknown-vendor degrade cases) stay inline where they are asserted.

``refresh_agent_native`` coverage below pins a LATENT SEAM: no production caller invokes it — agent native history is reachable
through the library open/read path (``thread/read`` on the agent thread) — so the seam
stays tested here rather than deleted.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from _agent_wire_fixtures import (
    agent_message_delta_params,
    agent_message_item,
    collab_agent_tool_call_item,
    item_completed_params,
    item_started_params,
    sub_agent_activity_item,
    thread_status_changed_params,
    turn_completed_params,
    turn_started_params,
)
from agents_remember.serving.conversation.active.projector import ActiveSessionProjector
from agents_remember.serving.conversation.models import (
    ConversationItem,
    MarkdownBlock,
    TextBlock,
    ToolInputBlock,
)
from agents_remember.serving.conversation.projectors import codex, projector_for
from agents_remember.serving.conversation.projectors.common import (
    MappedItem,
    MappedTurnOutcome,
    MappedUnknownVendor,
)
from agents_remember.serving.harness_control_models import (
    EvidenceFrame,
    NativeEvidenceFrame,
    NativeEvidencePage,
    PendingInteraction,
)
from test_conversation_active_service import (
    SECRET,
    _authorization,
    _codex_turn,
    _ControlledEntry,
    _identity,
    _ScriptedBridge,
)

NOW = "2026-07-26T08:30:00+00:00"
REF = "ar-ev:epoch:1"

PARENT = "vendor-1"
AGENT = "agent-t-1"


def _evidence(
    sequence: int,
    kind: str,
    raw: dict,
    *,
    native_method: str | None = None,
    thread_id: str | None = None,
) -> EvidenceFrame:
    return EvidenceFrame(
        sequence=sequence,
        kind=kind,
        created_at=NOW,
        raw=raw,
        native_method=native_method,
        thread_id=thread_id,
    )


def _items(outputs: list) -> list[ConversationItem]:
    return [output.item for output in outputs if isinstance(output, MappedItem)]


def _map(raw: dict, sequence: int = 1, **kwargs) -> list:
    return codex.map_evidence_frame(
        _evidence(sequence, "codex-notification", raw, **kwargs),
        evidence_ref=REF,
        parent_thread_id=PARENT,
    )


def _collab_item(
    item_id: str,
    tool: str,
    *,
    status: str = "completed",
    receivers: list[str] | None = None,
    states: dict | None = None,
) -> dict:
    # The vendored wire item always carries senderThreadId.
    return collab_agent_tool_call_item(
        item_id,
        tool,
        sender_thread_id=PARENT,
        status=status,
        receiver_thread_ids=receivers,
        agents_states=states,
    )


def _collab_frame(item: dict, sequence: int = 1, turn_id: str = "turn-1") -> list:
    return codex.map_evidence_frame(
        _evidence(
            sequence,
            "transcript",
            item_completed_params(PARENT, turn_id, item),
            thread_id=PARENT,
        ),
        evidence_ref=REF,
        parent_thread_id=PARENT,
    )


class CodexCollabMapperTests(unittest.TestCase):
    def test_spawn_call_is_a_parent_tool_call_and_mints_roster(self) -> None:
        outputs = _collab_frame(
            _collab_item(
                "collab-1",
                "spawnAgent",
                receivers=[AGENT],
                states={AGENT: {"status": "running"}},
            )
        )
        items = _items(outputs)
        call = next(item for item in items if item.item_id == "collab-1")
        self.assertEqual(call.kind, "tool-call")
        self.assertEqual(call.role, "tool")
        self.assertIsNone(call.agent, "a spawn call is the parent's own act")
        input_block = next(b for b in call.blocks if isinstance(b, ToolInputBlock))
        self.assertEqual(input_block.summary, "spawnAgent")

        roster = next(item for item in items if item.item_id == f"codex-agent-{AGENT}")
        self.assertEqual(roster.kind, "notice")
        self.assertEqual(roster.role, "system")
        self.assertEqual(roster.phase, "streaming")
        assert roster.agent is not None
        self.assertEqual(roster.agent.agent_id, AGENT)
        self.assertEqual(roster.agent.status, "running")
        self.assertIsNone(roster.agent.agent_path)

    def test_spawn_without_state_entry_registers_the_receiver(self) -> None:
        outputs = _collab_frame(_collab_item("collab-1", "spawnAgent", receivers=[AGENT]))
        (roster,) = [item for item in _items(outputs) if item.item_id == f"codex-agent-{AGENT}"]
        assert roster.agent is not None
        self.assertEqual(roster.agent.status, "registered")
        self.assertEqual(roster.phase, "pending")

    def test_agent_bound_collab_call_carries_ref_only_for_single_receiver(self) -> None:
        outputs = _collab_frame(
            _collab_item(
                "collab-2",
                "sendInput",
                receivers=[AGENT],
                states={AGENT: {"status": "running"}},
            )
        )
        call = next(item for item in _items(outputs) if item.item_id == "collab-2")
        assert call.agent is not None
        self.assertEqual(call.agent.agent_id, AGENT)
        self.assertEqual(call.agent.status, "running")

        # Multiple receivers: the call addresses several agents, so it stays a parent item.
        outputs = _collab_frame(
            _collab_item("collab-3", "wait", receivers=[AGENT, "agent-t-2"])
        )
        call = next(item for item in _items(outputs) if item.item_id == "collab-3")
        self.assertIsNone(call.agent)

    def test_terminal_collab_roster_carries_the_final_message(self) -> None:
        outputs = _collab_frame(
            _collab_item(
                "collab-4",
                "wait",
                receivers=[AGENT],
                states={AGENT: {"status": "completed", "message": "the fix is in"}},
            )
        )
        roster = next(item for item in _items(outputs) if item.item_id == f"codex-agent-{AGENT}")
        assert roster.agent is not None
        self.assertEqual(roster.agent.status, "completed")
        self.assertEqual(roster.phase, "completed")
        final = next(b for b in roster.blocks if isinstance(b, TextBlock))
        self.assertEqual(final.block_id, "final-message")
        self.assertEqual(final.text, "the fix is in")

    def test_sub_agent_activity_binds_agent_path_into_the_same_roster(self) -> None:
        started = _collab_frame(
            sub_agent_activity_item(
                "collab-5", kind="started", agent_thread_id=AGENT, agent_path="/root/agent_one"
            )
        )
        (roster,) = _items(started)
        self.assertEqual(roster.item_id, f"codex-agent-{AGENT}")
        assert roster.agent is not None
        self.assertEqual(roster.agent.agent_path, "/root/agent_one")
        self.assertEqual(roster.agent.status, "running")

        interrupted = _collab_frame(
            sub_agent_activity_item(
                "collab-6",
                kind="interrupted",
                agent_thread_id=AGENT,
                agent_path="/root/agent_one",
            )
        )
        (roster,) = _items(interrupted)
        assert roster.agent is not None
        self.assertEqual(roster.agent.status, "interrupted")
        self.assertEqual(roster.phase, "interrupted")

    def test_unknown_collab_shapes_degrade_to_preserved_unknown_vendor(self) -> None:
        cases = (
            {"id": "c-1", "type": "collabAgentToolCall"},
            {"id": "c-2", "type": "collabAgentToolCall", "tool": "wait", "receiverThreadIds": "nope"},
            {"id": "c-3", "type": "collabAgentToolCall", "tool": "wait", "agentsStates": ["nope"]},
            {"id": "c-4", "type": "subAgentActivity", "kind": "started"},
            {"id": "c-5", "type": "subAgentActivity", "agentThreadId": AGENT},
        )
        for index, item in enumerate(cases):
            with self.subTest(case=index):
                (unknown,) = [
                    output
                    for output in _collab_frame(item, sequence=index + 1)
                    if isinstance(output, MappedUnknownVendor)
                ]
                self.assertEqual(unknown.item_id, item["id"])
                self.assertEqual(unknown.vendor_type, f"codex:{item['type']}")

    def test_thread_started_mints_registration_only_for_a_proven_non_parent(self) -> None:
        outputs = _map(
            {"thread": {"id": "agent-t-9"}}, native_method="thread/started"
        )
        (roster,) = _items(outputs)
        self.assertEqual(roster.item_id, "codex-agent-agent-t-9")
        assert roster.agent is not None
        self.assertEqual(roster.agent.status, "registered")

        # Seat boot/resume is not a timeline event.
        self.assertEqual(
            _map({"thread": {"id": PARENT}}, native_method="thread/started"), []
        )
        # Without the parent context the shape alone cannot distinguish: stay silent.
        self.assertEqual(
            codex.map_evidence_frame(
                _evidence(1, "codex-notification", {"thread": {"id": "agent-t-9"}}, native_method="thread/started"),
                evidence_ref=REF,
            ),
            [],
        )

    def test_agent_thread_lifecycle_drives_roster_status(self) -> None:
        (roster,) = _items(
            _map(
                thread_status_changed_params(AGENT),
                native_method="thread/status/changed",
                thread_id=AGENT,
            )
        )
        assert roster.agent is not None
        self.assertEqual(roster.agent.status, "running")

        # ``idle`` cannot distinguish completed from interrupted: it mints nothing.
        self.assertEqual(
            _map(
                thread_status_changed_params(AGENT, active=False),
                native_method="thread/status/changed",
                thread_id=AGENT,
            ),
            [],
        )

        (roster,) = _items(
            _map(
                turn_started_params(AGENT, "agent-turn-1"),
                native_method="turn/started",
                thread_id=AGENT,
            )
        )
        assert roster.agent is not None
        self.assertEqual(roster.agent.status, "running")

        outputs = _map(
            turn_completed_params(AGENT, "agent-turn-1"),
            native_method="turn/completed",
            thread_id=AGENT,
        )
        # An agent turn settlement must never feed the parent-scoped status service.
        self.assertEqual(
            [output for output in outputs if isinstance(output, MappedTurnOutcome)], []
        )
        items = _items(outputs)
        roster = next(item for item in items if item.item_id == f"codex-agent-{AGENT}")
        assert roster.agent is not None
        self.assertEqual(roster.agent.status, "completed")
        turn_result = next(item for item in items if item.item_id == "turn-result:agent-turn-1")
        self.assertEqual(turn_result.kind, "turn-result")
        self.assertEqual(turn_result.phase, "completed")
        assert turn_result.agent is not None
        self.assertEqual(turn_result.agent.agent_id, AGENT)

        # A parent occurrence arriving as a notification (never emitted today) stays silent.
        self.assertEqual(
            _map(
                turn_completed_params(PARENT, "turn-1"),
                native_method="turn/completed",
                thread_id=PARENT,
            ),
            [],
        )


class _MultiplexedBridge(_ScriptedBridge):
    """Scripted bridge with per-thread native pages and demux-keyed evidence frames."""

    def __init__(self) -> None:
        super().__init__()
        self.agent_native_frames: dict[str, list[NativeEvidenceFrame]] = {}

    def push_frame(
        self,
        kind: str,
        raw: dict,
        *,
        thread_id: str | None = None,
        native_method: str | None = None,
    ) -> None:
        self.evidence_frames.append(
            EvidenceFrame(
                sequence=len(self.evidence_frames) + 1,
                kind=kind,
                created_at=NOW,
                raw=raw,
                native_method=native_method,
                thread_id=thread_id,
            )
        )

    def read_native_page(
        self,
        entry,
        *,
        cursor=None,
        limit=200,
        byte_budget=None,
        expected_bridge_epoch=None,
        thread_id=None,
    ):
        if thread_id is None:
            return super().read_native_page(
                entry,
                cursor=cursor,
                limit=limit,
                byte_budget=byte_budget,
                expected_bridge_epoch=expected_bridge_epoch,
            )
        frames = self.agent_native_frames.get(thread_id, [])
        start = 0
        if cursor is not None:
            for index, frame in enumerate(frames):
                if frame.native_id == cursor:
                    start = index + 1
                    break
        selected = frames[start : start + limit]
        truncated = start + limit < len(frames)
        return NativeEvidencePage(
            frames=tuple(selected),
            next_cursor=selected[-1].native_id if truncated and selected else None,
            truncated=truncated,
            bridge_epoch=self.epoch,
        )


def _projector(bridge: _MultiplexedBridge) -> ActiveSessionProjector:
    mapper = projector_for("codex")
    assert mapper is not None
    return ActiveSessionProjector(
        identity=_identity("codex"),
        authorization=_authorization(),
        entry=_ControlledEntry(),
        mapper=mapper,
        secret=SECRET,
        clock=lambda: NOW,
        evidence_reader=bridge.read_evidence,
        native_page_reader=bridge.read_native_page,
        transcript_reader=bridge.read_transcript,
        provenance_reader=bridge.read_provenance,
        snapshot_reader=bridge.read_snapshot,
    )


def _agent_turn_frames(bridge: _MultiplexedBridge, thread_id: str, turn_id: str, item_id: str) -> None:
    bridge.push_frame(
        "codex-notification",
        turn_started_params(thread_id, turn_id),
        thread_id=thread_id,
        native_method="turn/started",
    )
    bridge.push_frame(
        "codex-notification",
        item_started_params(thread_id, turn_id, agent_message_item(item_id, "partial")),
        thread_id=thread_id,
    )
    bridge.push_frame(
        "codex-notification",
        agent_message_delta_params(thread_id, turn_id, item_id, " more"),
        thread_id=thread_id,
    )
    bridge.push_frame(
        "transcript",
        item_completed_params(thread_id, turn_id, agent_message_item(item_id, "partial more")),
        thread_id=thread_id,
    )
    bridge.push_frame(
        "codex-notification",
        turn_completed_params(thread_id, turn_id),
        thread_id=thread_id,
        native_method="turn/completed",
    )


class CodexAgentEngineTests(unittest.IsolatedAsyncioTestCase):
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
        bridge.snapshot = replace(
            bridge.snapshot, pending_interactions=(parent_pending,)
        )
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
        self.assertNotIn("item-1", ids, "the live-settled agent turn must not twin")
        self.assertIn("item-0", ids, "genuine agent history must backfill")
        by_id = {item.item_id: item for item in result.items}
        assert by_id["item-0"].agent is not None
        self.assertEqual(by_id["item-0"].agent.agent_id, AGENT)

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


if __name__ == "__main__":
    unittest.main()
