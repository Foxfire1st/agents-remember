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

import threading
import unittest

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
    TextBlock,
    ToolInputBlock,
)
from agents_remember.serving.conversation.projectors import (
    codex,
    projector_for,
)
from agents_remember.serving.conversation.projectors.common import (
    MappedItem,
    MappedTurnOutcome,
    MappedUnknownVendor,
)
from agents_remember.serving.harness_control_models import (
    EvidenceFrame,
    NativeEvidenceFrame,
    NativeEvidencePage,
)
from test_conversation_active_service import (
    SECRET,
    _authorization,
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

    def test_turn_diff_updated_is_silent_while_unknown_methods_stay_addressable(self) -> None:
        """260731-EFA-L7 R16: the known Codex turn/diff control notification is not transcript.

        The L6 live curator stress run minted unknown-vendor rows for every
        ``codex:notification:turn/diff/updated`` (ar-ev:91d4e5776af6:756, :759, :767,
        :789, :796, :805). The method is now recognized exactly and consumed as
        timeline-silent -- repeated notifications mint ZERO conversation items.
        Genuinely unknown vendor methods are NOT suppressed: they keep producing the
        honest unknown-vendor evidence rows.
        """
        for sequence in range(3):
            self.assertEqual(
                codex.map_evidence_frame(
                    _evidence(
                        sequence,
                        "codex-notification",
                        {
                            "threadId": PARENT,
                            "turnId": "turn-1",
                            "diff": {"itemId": "item-1", "blockId": "b"},
                        },
                        native_method="turn/diff/updated",
                    ),
                    evidence_ref=REF,
                ),
                [],
            )

        outputs = codex.map_evidence_frame(
            _evidence(
                99,
                "codex-notification",
                {"opaque": True},
                native_method="vendor/mystery",
            ),
            evidence_ref=REF,
        )
        unknown = [output for output in outputs if isinstance(output, MappedUnknownVendor)]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0].vendor_type, "codex:notification:vendor/mystery")
        self.assertEqual(unknown[0].safe_summary, "unrecognized codex notification vendor/mystery")


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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_projector_codex_agents.py:406).
    def read_native_page(  # pragma: no cover
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
