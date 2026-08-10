"""Codex sub-agent collab and agent-thread mapping.

The app-server auto-attaches sub-agent thread listeners to the seat connection,
so one evidence stream carries many threads demuxed by ``threadId``. This module
owns the roster upserts, collab tool calls, agent-thread lifecycle notifications,
and the turn/completed outcome mapping; the frame router lives in
:mod:`agents_remember.serving.conversation.projectors.codex`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from agents_remember.models.conversations.content import (
    ConversationAgentRef,
    ConversationAgentStatus,
    ConversationContentBlock,
    ConversationItem,
    TextBlock,
    ToolInputBlock,
)
from agents_remember.serving.conversation.projectors.common import (
    ItemPhase,
    MappedItem,
    MappedTurnOutcome,
    MapperOutput,
    TerminalOutcomeValue,
    UnmappableShape,
    harness_provenance,
    optional_text,
    required_object,
    required_text,
)


@dataclass(frozen=True)
class _LiveItemContext:
    """What every codex item mapper needs about the frame, besides the item body itself.

    Carried as one value so each type's mapper takes the item and this, instead of the same seven
    positional facts threaded through every branch of the router.
    """

    item_id: str
    origin: str
    live: bool
    turn_id: str | None = None
    created_at: str | None = None
    phase: ItemPhase = "completed"
    evidence_ref: str = ""


@dataclass(frozen=True)
class ItemPlacement:
    """Where a mapped frame's items sit in the conversation, before any item body is read.

    One frame's worth of placement: the turn its items belong to, when it was observed, which
    origin produced it, whether it is still live, and the evidence ref that proves it. It is the
    same for every item in the frame, which is why it arrives once instead of per item.
    """

    origin: str
    live: bool
    turn_id: str | None = None
    created_at: str | None = None
    evidence_ref: str = ""


# wave-1 adapter evidence carries ``running`` (collab) and ``started``/``interacted``/``interrupted``
# (subAgentActivity); anything outside this table stays honest as ``unknown`` instead of a guess.
_COLLAB_AGENT_STATUS: dict[str, ConversationAgentStatus] = {
    "registered": "registered",
    "pending": "registered",
    "running": "running",
    "started": "running",
    "interacted": "running",
    "active": "running",
    "completed": "completed",
    "failed": "failed",
    "errored": "failed",
    "interrupted": "interrupted",
    "cancelled": "interrupted",
}

_ROSTER_ITEM_PHASE: dict[ConversationAgentStatus, ItemPhase] = {
    "registered": "pending",
    "running": "streaming",
    "completed": "completed",
    "failed": "failed",
    "interrupted": "interrupted",
}

# thread/status/changed ``status.type`` values that carry roster truth. ``idle`` says only "no
# active turn" — it cannot distinguish completed from interrupted, so it mints nothing rather
# than regressing a richer collab-derived roster status.
_THREAD_STATUS_ROSTER: dict[str, ConversationAgentStatus] = {
    "active": "running",
    "systemError": "failed",
}


def _roster_item(
    thread_id: str,
    placement: ItemPlacement,
    *,
    status: ConversationAgentStatus,
    agent_path: str | None = None,
    blocks: tuple[ConversationContentBlock, ...] = (),
) -> MappedItem:
    """One agent roster row (``codex-agent-<threadId>``), upserted across the lifecycle.

    The roster is never optimistic: every upsert derives from one concrete piece of
    collab / lifecycle evidence, and identity fields carry only what that evidence
    (or a prior bound upsert) proved.
    """

    return MappedItem(
        item=ConversationItem(
            item_id=f"codex-agent-{thread_id}",
            revision=1,
            global_ordinal=1,
            turn_id=placement.turn_id,
            lane="harness",
            source="harness-live" if placement.live else "native-history",
            provenance=harness_provenance(placement.origin, observed_at=placement.created_at),
            role="system",
            kind="notice",
            phase=_ROSTER_ITEM_PHASE.get(status, "unknown"),
            blocks=blocks,
            agent=ConversationAgentRef(agent_id=thread_id, agent_path=agent_path, status=status),
            created_at=placement.created_at,
        )
    )


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/projectors/_codex_collab.py:137).
def _collab_status(  # pragma: no cover
    agents_states: Mapping[str, object] | None, thread_id: str
) -> ConversationAgentStatus:
    """One agent's roster status from a collab item's ``agentsStates`` entry."""

    if agents_states is None:
        return "unknown"
    entry = agents_states.get(thread_id)
    if not isinstance(entry, Mapping):
        return "unknown"
    status = entry.get("status")
    if isinstance(status, str):
        return _COLLAB_AGENT_STATUS.get(status, "unknown")
    return "unknown"


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/projectors/_codex_collab.py:153).
def _collab_final_message(
    agents_states: Mapping[str, object] | None, thread_id: str
) -> str | None:  # pragma: no cover
    """The agent's final message when the collab state carries one (``message`` text)."""

    if agents_states is None:
        return None
    entry = agents_states.get(thread_id)
    if not isinstance(entry, Mapping):
        return None
    return optional_text(entry.get("message"))


@dataclass(frozen=True)
class _CollabCall:
    """One ``collabAgentToolCall`` item's identity fields, already proven well-typed.

    It exists because everything downstream of the shape check reads the same three facts:
    which collab tool ran, which agent threads it addressed, and what the item said about
    their states. Carrying them as one value keeps that check in exactly one place -- a
    ``_CollabCall`` in hand means the item *was* the documented collab shape.
    """

    tool: str
    receiver_ids: list[str]
    agents_states: Mapping[str, object] | None


def _collab_receiver_ids(receivers_raw: object) -> list[str] | None:
    """The addressed agent thread ids; ``None`` = present but not the documented list shape.

    An absent ``receiverThreadIds`` is a legitimate collab item (a broadcast ``wait``, say),
    so it normalizes to no receivers rather than to a rejection. Non-text and empty entries
    inside a well-typed list are dropped: they name no thread.
    """

    if receivers_raw is None:
        return []
    if not isinstance(receivers_raw, list):
        return None
    return [receiver for receiver in receivers_raw if isinstance(receiver, str) and receiver]


def _collab_call_shape(item: Mapping[str, object]) -> _CollabCall | None:
    """The one place a ``collabAgentToolCall`` item is judged against the documented shape.

    ``None`` means the item did not prove it: no tool name, or an identity field present
    with the wrong type. The caller preserves such an item as unknown-vendor evidence
    rather than inventing a roster entry from a shape codex never documented.
    """

    tool = optional_text(item.get("tool"))
    if tool is None:
        return None
    receiver_ids = _collab_receiver_ids(item.get("receiverThreadIds"))
    if receiver_ids is None:
        return None
    states_raw = item.get("agentsStates")
    if states_raw is not None and not isinstance(states_raw, Mapping):
        return None
    return _CollabCall(
        tool=tool,
        receiver_ids=receiver_ids,
        agents_states=states_raw if isinstance(states_raw, Mapping) else None,
    )


def _collab_call_input_block(item: Mapping[str, object], tool: str) -> ToolInputBlock:
    """The collab call's input block: the tool name plus the fields that say who it addressed.

    Only the three documented request fields cross, and only when the item actually carried
    them -- an absent field stays absent instead of becoming a null the reader must interpret.
    """

    return ToolInputBlock(
        block_id="input",
        summary=tool,
        data={
            key: item.get(key)
            for key in ("prompt", "senderThreadId", "receiverThreadIds")
            if item.get(key) is not None
        },
    )


def _collab_call_agent(call: _CollabCall) -> ConversationAgentRef | None:
    """The agent this collab call belongs to, when exactly one agent owns it.

    The tool call carries a ``ConversationAgentRef`` only when it belongs to exactly
    one agent (``sendInput``/``resumeAgent``/``wait``/``closeAgent`` with a single
    receiver); a ``spawnAgent`` call is the parent's own act and stays untagged.
    """

    if call.tool != "spawnAgent" and len(call.receiver_ids) == 1:
        return ConversationAgentRef(
            agent_id=call.receiver_ids[0],
            status=_collab_status(call.agents_states, call.receiver_ids[0]),
        )
    return None


def _collab_roster_ids(call: _CollabCall) -> list[str]:
    """Every agent thread this item is evidence about: receivers union ``agentsStates`` keys.

    Receiver order leads and duplicates collapse, so the roster upserts come out in the
    order the item itself named the agents rather than in mapping-iteration order.
    """

    return list(
        dict.fromkeys(
            [
                *call.receiver_ids,
                *(key for key in (call.agents_states or {}) if isinstance(key, str) and key),
            ]
        )
    )


def _collab_roster_upserts(
    call: _CollabCall,
    *,
    created_at: str | None,
    origin: str,
    live: bool,
) -> list[MapperOutput]:
    """One roster upsert per agent this collab item is evidence about.

    Each row carries the status the item proved for that agent, plus the agent's final
    message as a block when the collab state carried one.
    """

    outputs: list[MapperOutput] = []
    for agent_thread_id in _collab_roster_ids(call):
        agent_status = _collab_status(call.agents_states, agent_thread_id)
        if agent_status == "unknown" and call.tool == "spawnAgent":
            # The spawn itself is the registration evidence for a receiver whose
            # state has not streamed yet.
            agent_status = "registered"
        final_message = _collab_final_message(call.agents_states, agent_thread_id)
        outputs.append(
            _roster_item(
                agent_thread_id,
                ItemPlacement(
                    created_at=created_at, origin=f"{origin} collabAgentToolCall roster", live=live
                ),
                status=agent_status,
                blocks=(
                    (TextBlock(block_id="final-message", text=final_message),)
                    if final_message
                    else ()
                ),
            )
        )
    return outputs


def _map_collab_tool_call(
    item: Mapping[str, object],
    context: _LiveItemContext,
) -> list[MapperOutput] | None:
    """Map one ``collabAgentToolCall`` item; ``None`` = not the documented collab shape.

    Outputs the collab tool call itself as a parent-timeline tool-call item plus one
    roster upsert per involved agent (``receiverThreadIds`` union ``agentsStates`` keys),
    in that order. The shape check, the call's own fields and the roster rows each live in
    their own helper above, so this body is the three-step contract and nothing else.
    """

    item_id = context.item_id
    turn_id = context.turn_id
    created_at = context.created_at
    origin = context.origin
    live = context.live

    call = _collab_call_shape(item)
    if call is None:
        return None
    status = optional_text(item.get("status")) or "completed"
    outputs: list[MapperOutput] = [
        MappedItem(
            item=ConversationItem(
                item_id=item_id,
                revision=1,
                global_ordinal=1,
                turn_id=turn_id,
                lane="harness",
                source="harness-live" if live else "native-history",
                provenance=harness_provenance(origin, observed_at=created_at),
                role="tool",
                kind="tool-call",
                phase=_tool_phase(status),
                blocks=(_collab_call_input_block(item, call.tool),),
                agent=_collab_call_agent(call),
                created_at=created_at,
            )
        )
    ]
    outputs.extend(_collab_roster_upserts(call, created_at=created_at, origin=origin, live=live))
    return outputs


def _map_sub_agent_activity(
    item: Mapping[str, object],
    *,
    created_at: str | None,
    origin: str,
    live: bool,
) -> list[MapperOutput] | None:
    """Map one ``subAgentActivity`` item into the same roster row, binding ``agentPath``."""

    kind = optional_text(item.get("kind"))
    agent_thread_id = optional_text(item.get("agentThreadId"))
    if kind is None or agent_thread_id is None:
        return None
    agent_path = optional_text(item.get("agentPath"))
    status = _COLLAB_AGENT_STATUS.get(kind, "unknown")
    if not live and status in {"registered", "running"}:
        # A persisted spawn/start proves historical existence, not current liveness.
        # The adapter's live registry may overlay a current status during hydration.
        status = "unknown"
    return [
        _roster_item(
            agent_thread_id,
            ItemPlacement(
                created_at=created_at, origin=f"{origin} subAgentActivity roster", live=live
            ),
            status=status,
            agent_path=agent_path,
        )
    ]


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/projectors/_codex_collab.py:383).
def _map_thread_started(  # pragma: no cover
    params: Mapping[str, object],
    *,
    parent_thread_id: str | None,
    created_at: str | None,
) -> list[MapperOutput]:
    """``thread/started``: parent occurrences stay silent; a proven non-parent registers.

    The params shape (``{thread: {...}}``) is identical for the seat's own boot/resume
    and for a sub-agent thread, so parent-ness is NOT distinguishable from the frame
    alone — without ``parent_thread_id`` this keeps the pre-multiplex silent behavior rather
    than guessing (the honesty clause of R2.2).
    """

    thread = params.get("thread")
    if not isinstance(thread, Mapping):
        return []
    thread_id = optional_text(thread.get("id"))
    if thread_id is None or parent_thread_id is None or thread_id == parent_thread_id:
        return []
    return [
        _roster_item(
            thread_id,
            ItemPlacement(
                created_at=created_at,
                origin="codex app-server thread/started (sub-agent registration)",
                live=True,
            ),
            status="registered",
        )
    ]


def _agent_notification_thread_id(
    params: Mapping[str, object], parent_thread_id: str | None
) -> str | None:
    """The agent thread a lifecycle notification belongs to, or ``None`` to stay silent.

    The adapter emits these methods as ``codex-notification`` evidence ONLY for
    non-parent threads; the ``parent_thread_id`` comparison is defense-in-depth so a
    parent occurrence could never mint a roster row for the seat itself.
    """

    thread_id = optional_text(params.get("threadId"))
    if thread_id is None or thread_id == parent_thread_id:
        return None
    return thread_id


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/projectors/_codex_collab.py:432).
def _map_agent_thread_status(  # pragma: no cover
    params: Mapping[str, object],
    *,
    parent_thread_id: str | None,
    created_at: str | None,
) -> list[MapperOutput]:
    thread_id = _agent_notification_thread_id(params, parent_thread_id)
    if thread_id is None:
        return []
    status = params.get("status")
    status_type = optional_text(status.get("type")) if isinstance(status, Mapping) else None
    roster_status = _THREAD_STATUS_ROSTER.get(status_type or "")
    if roster_status is None:
        # ``idle`` (or an undocumented type) carries no honest roster transition.
        return []
    return [
        _roster_item(
            thread_id,
            ItemPlacement(
                created_at=created_at,
                origin="codex app-server thread/status/changed (sub-agent)",
                live=True,
            ),
            status=roster_status,
        )
    ]


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/projectors/_codex_collab.py:460).
def _map_agent_turn_started(  # pragma: no cover
    params: Mapping[str, object],
    *,
    parent_thread_id: str | None,
    created_at: str | None,
) -> list[MapperOutput]:
    thread_id = _agent_notification_thread_id(params, parent_thread_id)
    if thread_id is None:
        return []
    return [
        _roster_item(
            thread_id,
            ItemPlacement(
                created_at=created_at, origin="codex app-server turn/started (sub-agent)", live=True
            ),
            status="running",
        )
    ]


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/projectors/_codex_collab.py:480).
def _map_agent_turn_completed(  # pragma: no cover
    params: Mapping[str, object],
    *,
    parent_thread_id: str | None,
    created_at: str | None,
) -> list[MapperOutput]:
    """One agent turn's settlement: roster terminal status + an agent-bound turn-result.

    NEVER a ``MappedTurnOutcome``: the engine's pending-terminal slot and the canonical
    status service are parent-scoped, so an agent turn settling must not settle the
    parent conversation.
    """

    thread_id = _agent_notification_thread_id(params, parent_thread_id)
    if thread_id is None:
        return []
    turn = required_object(params.get("turn"), "turn/completed params.turn")
    turn_id = required_text(turn.get("id"), "turn/completed turn.id")
    status = required_text(turn.get("status"), "turn/completed turn.status")
    if status not in ("completed", "interrupted", "failed"):
        raise UnmappableShape(f"turn/completed turn.status {status!r} is undocumented")
    roster_status: ConversationAgentStatus = status
    return [
        _roster_item(
            thread_id,
            ItemPlacement(
                created_at=created_at,
                origin="codex app-server turn/completed (sub-agent)",
                live=True,
            ),
            status=roster_status,
        ),
        MappedItem(
            item=ConversationItem(
                item_id=f"turn-result:{turn_id}",
                revision=1,
                global_ordinal=1,
                turn_id=turn_id,
                lane="harness",
                source="harness-live",
                provenance=harness_provenance(
                    "codex app-server turn/completed (sub-agent)", observed_at=created_at
                ),
                role="system",
                kind="turn-result",
                phase=status,
                blocks=(),
                agent=ConversationAgentRef(agent_id=thread_id, status=roster_status),
                created_at=created_at,
            )
        ),
    ]


AgentThreadNotificationMapper = Callable[..., list[MapperOutput]]

# Agent-thread lifecycle, recognized by native method. Each of these crosses as a
# codex-notification ONLY for a non-parent thread -- the parent's own occurrences ride the
# ``state`` and ``completed`` kinds -- so every entry here is roster evidence about some agent,
# never a parent-timeline item.
_AGENT_THREAD_NOTIFICATIONS: dict[str, AgentThreadNotificationMapper] = {
    "thread/started": _map_thread_started,
    "thread/status/changed": _map_agent_thread_status,
    "turn/started": _map_agent_turn_started,
    "turn/completed": _map_agent_turn_completed,
}


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/projectors/_codex_collab.py:548).
def _map_turn_completed(  # pragma: no cover
    turn: Mapping[str, object],
    *,
    created_at: str | None,
) -> list[MapperOutput]:
    turn_id = required_text(turn.get("id"), "turn/completed turn.id")
    status = required_text(turn.get("status"), "turn/completed turn.status")
    outcome: TerminalOutcomeValue
    if status == "completed":
        outcome = "completed"
    elif status == "interrupted":
        outcome = "interrupted"
    elif status == "failed":
        outcome = "failed"
    else:
        raise UnmappableShape(f"turn/completed turn.status {status!r} is undocumented")
    detail = None
    error = turn.get("error")
    if isinstance(error, dict):
        detail = optional_text(error.get("message"))
    outputs: list[MapperOutput] = [
        MappedItem(
            item=ConversationItem(
                item_id=f"turn-result:{turn_id}",
                revision=1,
                global_ordinal=1,
                turn_id=turn_id,
                lane="harness",
                source="harness-live",
                provenance=harness_provenance(
                    "codex app-server turn/completed", observed_at=created_at
                ),
                role="system",
                kind="turn-result",
                phase=outcome if outcome != "unknown" else "unknown",
                blocks=(),
                created_at=created_at,
            )
        ),
        MappedTurnOutcome(outcome=outcome, turn_id=turn_id, stop_reason=detail),
    ]
    return outputs


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/projectors/_codex_collab.py:592).
def _tool_phase(status: str) -> ItemPhase:  # pragma: no cover
    if status == "inProgress":
        return "streaming"
    if status == "completed":
        return "completed"
    if status == "failed":
        return "failed"
    if status == "declined":
        return "interrupted"
    return "unknown"
