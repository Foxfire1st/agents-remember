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

``refresh_agent_native`` is the selected-child production seam. Ordinary parent paging
hydrates roster/live evidence only; agent focus explicitly requests one child's native backfill.
"""

from __future__ import annotations

import asyncio
import threading
import unittest
from dataclasses import replace
from unittest import mock

from _agent_wire_fixtures import (
    CollabAgents,
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
from agents_remember.errors import NativeHistoryUnavailable
from agents_remember.serving.conversation.active.projector import ActiveSessionProjector
from agents_remember.serving.conversation.active.projector.facade import ProjectedSession
from agents_remember.serving.conversation.active.projector.wiring import BridgeReaders
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
        agents=CollabAgents(PARENT, receiver_thread_ids=receivers, states=states),
        status=status,
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
        outputs = _collab_frame(_collab_item("collab-3", "wait", receivers=[AGENT, "agent-t-2"]))
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
            {
                "id": "c-2",
                "type": "collabAgentToolCall",
                "tool": "wait",
                "receiverThreadIds": "nope",
            },
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
        outputs = _map({"thread": {"id": "agent-t-9"}}, native_method="thread/started")
        (roster,) = _items(outputs)
        self.assertEqual(roster.item_id, "codex-agent-agent-t-9")
        assert roster.agent is not None
        self.assertEqual(roster.agent.status, "registered")

        # Seat boot/resume is not a timeline event.
        self.assertEqual(_map({"thread": {"id": PARENT}}, native_method="thread/started"), [])
        # Without the parent context the shape alone cannot distinguish: stay silent.
        self.assertEqual(
            codex.map_evidence_frame(
                _evidence(
                    1,
                    "codex-notification",
                    {"thread": {"id": "agent-t-9"}},
                    native_method="thread/started",
                ),
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
        self.agent_native_errors: dict[str, NativeHistoryUnavailable] = {}
        self.agent_native_reads: list[str] = []
        self.agent_native_started: dict[str, threading.Event] = {}
        self.agent_native_release: dict[str, threading.Event] = {}

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
        expected_bridge_epoch=None,
        thread_id=None,
    ):
        if thread_id is None:
            return super().read_native_page(
                entry,
                cursor=cursor,
                limit=limit,
                expected_bridge_epoch=expected_bridge_epoch,
            )
        self.agent_native_reads.append(thread_id)
        started = self.agent_native_started.get(thread_id)
        if started is not None:
            started.set()
        release = self.agent_native_release.get(thread_id)
        if release is not None and not release.wait(timeout=2):
            raise AssertionError(f"timed out waiting to release {thread_id}")
        error = self.agent_native_errors.get(thread_id)
        if error is not None:
            raise error
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
        ProjectedSession(
            identity=_identity("codex"),
            authorization=_authorization(),
            entry=_ControlledEntry(),
            mapper=mapper,
            secret=SECRET,
        ),
        clock=lambda: NOW,
        readers=BridgeReaders(
            evidence=bridge.read_evidence,
            native_page=bridge.read_native_page,
            transcript=bridge.read_transcript,
            provenance=bridge.read_provenance,
            snapshot=bridge.read_snapshot,
        ),
    )


def _agent_turn_frames(
    bridge: _MultiplexedBridge, thread_id: str, turn_id: str, item_id: str
) -> None:
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
    async def test_two_wave_roster_rehydrates_exactly_without_root_or_status_regression(
        self,
    ) -> None:
        """Six historical starts reconcile against the persistent live registry."""

        wave_one = [f"wave-one-{index}" for index in range(3)]
        wave_two = [f"wave-two-{index}" for index in range(3)]
        children = [*wave_one, *wave_two]
        bridge = _MultiplexedBridge()
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
        bridge.snapshot = replace(
            bridge.snapshot,
            raw={
                "agentRegistry": {
                    child: {
                        "status": "completed" if child in wave_one else "running",
                        "agentPath": f"/root/{child}",
                    }
                    for child in children
                }
            },
        )
        projector = _projector(bridge)
        initial = await projector.page(before_ordinal=None, limit=100)
        initial_roster = {
            item.agent.agent_id: item.agent.status
            for item in initial.items
            if item.item_id.startswith("codex-agent-") and item.agent is not None
        }
        self.assertEqual(
            initial_roster,
            {
                **{child: "completed" for child in wave_one},
                **{child: "running" for child in wave_two},
            },
        )
        self.assertNotIn("root", initial_roster)

        for index, child in enumerate(wave_two):
            bridge.push_frame(
                "codex-notification",
                turn_completed_params(child, f"wave-two-turn-{index}"),
                thread_id=child,
                native_method="turn/completed",
            )
        bridge.snapshot = replace(
            bridge.snapshot,
            raw={
                "agentRegistry": {
                    child: {"status": "completed", "agentPath": f"/root/{child}"}
                    for child in children
                }
            },
        )
        await projector.poll_once()
        settled = await projector.page(before_ordinal=None, limit=100)
        settled_roster = {
            item.agent.agent_id: item.agent.status
            for item in settled.items
            if item.item_id.startswith("codex-agent-") and item.agent is not None
        }
        self.assertEqual(settled_roster, {child: "completed" for child in children})

        # A fresh backend projector has only persisted starts plus the persistent
        # adapter snapshot. It must reconstruct the same six terminal seats.
        reloaded = _projector(bridge)
        restarted_page = await reloaded.page(before_ordinal=None, limit=100)
        restarted_roster = {
            item.agent.agent_id: item.agent.status
            for item in restarted_page.items
            if item.item_id.startswith("codex-agent-") and item.agent is not None
        }
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


if __name__ == "__main__":
    unittest.main()
