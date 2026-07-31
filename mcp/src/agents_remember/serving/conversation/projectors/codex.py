"""Codex active projector: app-server thread items and notifications -> items.

Schema authority: the codex app-server v2 generated protocol (item/started,
item/completed ``{item, threadId, turnId, ...AtMs}``; item delta methods;
``thread/tokenUsage/updated``; turn lifecycle notifications) and the landed
installed-runtime fixture rows. Every item type with exact field evidence maps
to a typed item; every other shape becomes ``unknown-vendor`` evidence with its
native id/turn parent preserved. Codex's documented historical tool loss stays
visible through capabilities, never hidden by a completeness claim.

Sub-agents: the app-server auto-attaches sub-agent thread
listeners to the seat's connection, so one evidence stream carries many threads
demuxed by ``threadId`` (wave 1). Parent-thread ``collabAgentToolCall`` /
``subAgentActivity`` items model the collaboration itself: each maps to a roster
item per agent (``codex-agent-<threadId>``) upserted through the collab
lifecycle, plus the collab tool call as a parent-timeline tool-call item.
Agent-thread lifecycle notifications (``thread/status/changed``, ``turn/started``,
``turn/completed``) cross as ``codex-notification`` evidence ONLY for non-parent
threads (the parent occurrences ride the ``state``/``completed`` kinds), so the
mapper keys roster status off them without ever inventing identity. An agent
turn's completion mints a turn-result item but NEVER a ``MappedTurnOutcome`` —
the canonical status service stays parent-scoped.
"""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.serving.conversation.models import (
    ConversationAgentRef,
    ConversationAgentStatus,
    ConversationContentBlock,
    ConversationCorrelation,
    ConversationItem,
    DiffBlock,
    FileReferenceBlock,
    MarkdownBlock,
    TextBlock,
    ThinkingBlock,
    ToolInputBlock,
    ToolOutputBlock,
    UnknownVendorBlock,
)
from agents_remember.serving.conversation.projectors.common import (
    ItemPhase,
    MappedBlockDelta,
    MappedItem,
    MappedTurnOutcome,
    MappedUnknownVendor,
    MapperOutput,
    TerminalOutcomeValue,
    UnmappableShape,
    harness_provenance,
    optional_text,
    required_list,
    required_object,
    required_text,
    unknown_input_provenance,
)
from agents_remember.serving.harness_control_models import (
    EvidenceFrame,
    NativeEvidenceFrame,
)

HARNESS = "codex"

# Codex 0.144.5 app-server session lifecycle / status / telemetry notifications that mint no
# conversation item. They cross the same ``codex-notification`` evidence kind as the item and delta
# methods, but the fresh-open startup burst (one ``mcpServer/startupStatus/updated`` per configured
# MCP server, ``thread/started``, ``remoteControl/status/changed``, the ``warning``/``configWarning``
# advisory family) carries no timeline content — exactly like the already-dropped usage/rate frames.
# They are recognized by the native method the adapter now preserves and dropped explicitly, never
# re-guessed from params shape and never flooded as one unknown-vendor row per server. The
# ``configWarning`` advisory in particular fires at open on setups with a config note (observed live
# in the AR_RUN_CHATS_E2E composed drive as ``codex:notification:configWarning`` on evidence seq 1) —
# a sibling of ``warning``, telemetry-like, no timeline item. See
# notes/260721-halftime-codex-open-diagnosis.md.
# ``thread/settings/updated`` rides the ``state`` kind for the parent thread (dropped by kind);
# the agent-thread occurrence crosses as a ``codex-notification`` and is equally
# timeline-less, so it is recognized here rather than flooded as unknown-vendor per settings flip.
_SILENT_NOTIFICATION_METHODS = frozenset(
    {
        "mcpServer/startupStatus/updated",
        "remoteControl/status/changed",
        "warning",
        "configWarning",
        "account/rateLimits/updated",
        "thread/tokenUsage/updated",
        "thread/settings/updated",
    }
)


# -- Sub-agent roster mapping -------------------------------

# Collab ``agentsStates``/``subAgentActivity.kind`` vocabulary -> roster status. The probe-locked
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


def map_native_frame(frame: NativeEvidenceFrame, *, evidence_ref: str) -> list[MapperOutput]:
    """Map one ``thread/read`` item frame; unknown types stay visible."""

    item = required_object(frame.raw, "codex thread item")
    return _map_thread_item(
        item,
        turn_id=frame.native_parent_id,
        created_at=frame.created_at,
        origin="codex thread/read native page",
        live=False,
        evidence_ref=evidence_ref,
    )


def map_evidence_frame(
    frame: EvidenceFrame,
    *,
    evidence_ref: str,
    parent_thread_id: str | None = None,
) -> list[MapperOutput]:
    """Map one live notification; deltas, items, and turn outcomes stay exact.

    The evidence payload is the notification ``params`` object; the method
    itself does not cross. Discrimination uses the adapter event kind plus the
    schema-disjoint required keys of each params shape; the bare-delta family
    resolves its target block through the mapped item's kind at the engine.
    Anything else becomes unknown-vendor evidence, never a guessed meaning.

    ``parent_thread_id`` is the multiplexed demux context: the
    session/parent thread id the engine knows from the projection identity. It is
    only consulted where parent-vs-agent cannot be told from the frame kind alone
    (``thread/started``); without it those frames keep the pre-multiplex silent behavior
    rather than guess.
    """

    params = frame.raw
    if frame.kind == "completed":
        turn = required_object(params.get("turn"), "turn/completed params.turn")
        return _map_turn_completed(turn, created_at=frame.created_at)
    if frame.kind == "transcript":
        item = required_object(params.get("item"), "item/completed params.item")
        return _map_thread_item(
            item,
            turn_id=optional_text(params.get("turnId")),
            created_at=frame.created_at,
            origin="codex app-server item/completed",
            live=True,
            phase_override="completed",
            evidence_ref=evidence_ref,
        )
    if frame.kind == "state":
        # thread/status/changed and thread/settings/updated feed the canonical
        # status service through the snapshot; they mint no timeline items.
        return []
    if frame.kind != "codex-notification":
        return [
            MappedUnknownVendor(
                item_id=f"codex-event-{frame.sequence}",
                vendor_type=f"codex-event:{frame.kind}",
                safe_summary=f"codex adapter event of kind {frame.kind}",
                created_at=frame.created_at,
            )
        ]
    method = frame.native_method
    if method in _SILENT_NOTIFICATION_METHODS:
        # Session lifecycle/status/telemetry; no timeline item. Recognized by the native method,
        # dropped explicitly the same way ``state``-kind frames and usage frames are dropped.
        return []
    if method == "thread/started":
        return _map_thread_started(
            params, parent_thread_id=parent_thread_id, created_at=frame.created_at
        )
    if method == "thread/status/changed":
        # Only a NON-parent thread's status crosses as a codex-notification (the parent's rides
        # the ``state`` kind); it is roster evidence, never a parent-timeline item.
        return _map_agent_thread_status(
            params, parent_thread_id=parent_thread_id, created_at=frame.created_at
        )
    if method == "turn/started":
        return _map_agent_turn_started(
            params, parent_thread_id=parent_thread_id, created_at=frame.created_at
        )
    if method == "turn/completed":
        return _map_agent_turn_completed(
            params, parent_thread_id=parent_thread_id, created_at=frame.created_at
        )
    if isinstance(params.get("item"), dict):
        started = "startedAtMs" in params
        item = required_object(params.get("item"), "codex item params.item")
        return _map_thread_item(
            item,
            turn_id=optional_text(params.get("turnId")),
            created_at=frame.created_at,
            origin="codex app-server item/started"
            if started
            else "codex app-server item/completed",
            live=True,
            phase_override="streaming" if started else "completed",
            evidence_ref=evidence_ref,
        )
    if isinstance(params.get("itemId"), str) and "delta" in params:
        item_id = required_text(params.get("itemId"), "codex delta params.itemId")
        delta = params.get("delta")
        if not isinstance(delta, str):
            raise UnmappableShape("codex delta params.delta must be text")
        if isinstance(params.get("summaryIndex"), int):
            return [
                MappedBlockDelta(
                    item_id=item_id, block_id=f"summary-{params['summaryIndex']}", delta=delta
                )
            ]
        if isinstance(params.get("contentIndex"), int):
            return [
                MappedBlockDelta(
                    item_id=item_id, block_id=f"content-{params['contentIndex']}", delta=delta
                )
            ]
        # Bare delta: agentMessage, plan, and commandExecution output deltas
        # share this shape; the engine resolves the block from the item kind.
        return [MappedBlockDelta(item_id=item_id, block_id="", delta=delta)]
    if isinstance(params.get("itemId"), str) and isinstance(params.get("changes"), list):
        return [
            MappedItem(
                item=_file_change_item(
                    required_text(params.get("itemId"), "patchUpdated params.itemId"),
                    required_list(params.get("changes"), "patchUpdated params.changes"),
                    turn_id=optional_text(params.get("turnId")),
                    created_at=frame.created_at,
                    origin="codex app-server item/fileChange/patchUpdated",
                    status="inProgress",
                    live=True,
                )
            )
        ]
    if "tokenUsage" in params or "rateLimits" in params:
        # Usage/rate evidence feeds L3 telemetry projections; it never becomes
        # a token-theater timeline row.
        return []
    if isinstance(params.get("itemId"), str):
        # MCP progress notes and terminal interaction resolve on completion.
        return []
    return [
        MappedUnknownVendor(
            item_id=f"codex-event-{frame.sequence}",
            vendor_type=f"codex:notification:{method}" if method else "codex:notification",
            safe_summary=(
                f"unrecognized codex notification {method}"
                if method
                else "unrecognized codex notification params"
            ),
            turn_id=optional_text(params.get("turnId")),
            created_at=frame.created_at,
        )
    ]


def _map_thread_item(
    item: Mapping[str, object],
    *,
    turn_id: str | None,
    created_at: str | None,
    origin: str,
    live: bool,
    evidence_ref: str,
    phase_override: ItemPhase | None = None,
) -> list[MapperOutput]:
    item_id = required_text(item.get("id"), "codex thread item id")
    item_type = required_text(item.get("type"), "codex thread item type")
    phase = phase_override or "completed"
    if item_type == "userMessage":
        return [
            MappedItem(
                item=_user_message_item(
                    item,
                    item_id=item_id,
                    turn_id=turn_id,
                    created_at=created_at,
                    origin=origin,
                    phase=phase,
                    evidence_ref=evidence_ref,
                )
            )
        ]
    if item_type == "agentMessage":
        return [
            MappedItem(
                item=ConversationItem(
                    item_id=item_id,
                    revision=1,
                    global_ordinal=1,
                    turn_id=turn_id,
                    lane="harness",
                    source="harness-live" if live else "native-history",
                    provenance=harness_provenance(origin, observed_at=created_at),
                    role="assistant",
                    kind="message",
                    phase=phase,
                    blocks=(
                        MarkdownBlock(
                            block_id="markdown",
                            markdown=str(item.get("text") or ""),
                        ),
                    ),
                    created_at=created_at,
                )
            )
        ]
    if item_type == "plan":
        return [
            MappedItem(
                item=ConversationItem(
                    item_id=item_id,
                    revision=1,
                    global_ordinal=1,
                    turn_id=turn_id,
                    lane="harness",
                    source="harness-live" if live else "native-history",
                    provenance=harness_provenance(origin, observed_at=created_at),
                    role="assistant",
                    kind="plan",
                    phase=phase,
                    blocks=(
                        MarkdownBlock(block_id="markdown", markdown=str(item.get("text") or "")),
                    ),
                    created_at=created_at,
                )
            )
        ]
    if item_type == "reasoning":
        return [
            MappedItem(
                item=_reasoning_item(
                    item,
                    item_id=item_id,
                    turn_id=turn_id,
                    created_at=created_at,
                    origin=origin,
                    live=live,
                    phase=phase,
                )
            )
        ]
    if item_type == "commandExecution":
        return [
            MappedItem(
                item=_command_execution_item(
                    item,
                    item_id=item_id,
                    turn_id=turn_id,
                    created_at=created_at,
                    origin=origin,
                    live=live,
                )
            )
        ]
    if item_type == "fileChange":
        changes = required_list(item.get("changes"), "fileChange item.changes")
        status = optional_text(item.get("status")) or "completed"
        return [
            MappedItem(
                item=_file_change_item(
                    item_id,
                    changes,
                    turn_id=turn_id,
                    created_at=created_at,
                    origin=origin,
                    status=status,
                    live=live,
                )
            )
        ]
    if item_type == "mcpToolCall":
        return [
            MappedItem(
                item=_mcp_tool_call_item(
                    item,
                    item_id=item_id,
                    turn_id=turn_id,
                    created_at=created_at,
                    origin=origin,
                    live=live,
                )
            )
        ]
    if item_type == "collabAgentToolCall":
        collab = _map_collab_tool_call(
            item,
            item_id=item_id,
            turn_id=turn_id,
            created_at=created_at,
            origin=origin,
            live=live,
        )
        if collab is not None:
            return collab
    elif item_type == "subAgentActivity":
        activity = _map_sub_agent_activity(
            item,
            created_at=created_at,
            origin=origin,
            live=live,
        )
        if activity is not None:
            return activity
    return [
        MappedUnknownVendor(
            item_id=item_id,
            vendor_type=f"codex:{item_type}",
            safe_summary=f"codex item of type {item_type}",
            live=live,
            turn_id=turn_id,
            created_at=created_at,
        )
    ]


def _user_message_item(
    item: Mapping[str, object],
    *,
    item_id: str,
    turn_id: str | None,
    created_at: str | None,
    origin: str,
    phase: ItemPhase,
    evidence_ref: str,
) -> ConversationItem:
    blocks: list = []
    for position, raw_part in enumerate(required_list(item.get("content"), "userMessage.content")):
        part = required_object(raw_part, "userMessage content part")
        part_type = required_text(part.get("type"), "userMessage content part type")
        if part_type == "text":
            blocks.append(TextBlock(block_id=f"text-{position}", text=str(part.get("text") or "")))
        elif part_type in {"image", "localImage", "skill", "mention"}:
            reference = optional_text(part.get("url")) or optional_text(part.get("path"))
            name = (
                optional_text(part.get("name"))
                or (reference.rsplit("/", 1)[-1] if reference else None)
                or part_type
            )
            blocks.append(
                FileReferenceBlock(
                    block_id=f"ref-{position}",
                    type="resource-ref",
                    name=name,
                    uri=reference,
                )
            )
        else:
            blocks.append(
                UnknownVendorBlock(
                    block_id=f"unknown-{position}",
                    vendor_type=f"codex-user-part:{part_type}",
                    safe_summary=f"codex user content part of type {part_type}",
                    evidence_ref=f"{evidence_ref}:b{position}",
                )
            )
    client_id = optional_text(item.get("clientId"))
    return ConversationItem(
        item_id=item_id,
        revision=1,
        global_ordinal=1,
        turn_id=turn_id,
        lane="unknown-input",
        source="native-history",
        provenance=unknown_input_provenance(origin, observed_at=created_at),
        role="user",
        kind="message",
        phase=phase,
        blocks=tuple(blocks),
        correlation=ConversationCorrelation(request_id=client_id) if client_id else None,
        created_at=created_at,
        evidence_ref=evidence_ref,
    )


def _reasoning_item(
    item: Mapping[str, object],
    *,
    item_id: str,
    turn_id: str | None,
    created_at: str | None,
    origin: str,
    live: bool,
    phase: ItemPhase,
) -> ConversationItem:
    blocks: list = []
    for index, summary in enumerate(required_list(item.get("summary"), "reasoning.summary")):
        blocks.append(ThinkingBlock(block_id=f"summary-{index}", markdown=str(summary)))
    for index, content in enumerate(required_list(item.get("content"), "reasoning.content")):
        blocks.append(ThinkingBlock(block_id=f"content-{index}", markdown=str(content)))
    return ConversationItem(
        item_id=item_id,
        revision=1,
        global_ordinal=1,
        turn_id=turn_id,
        lane="harness",
        source="harness-live" if live else "native-history",
        provenance=harness_provenance(origin, observed_at=created_at),
        role="assistant",
        kind="thinking",
        phase=phase,
        blocks=tuple(blocks),
        created_at=created_at,
    )


def _command_execution_item(
    item: Mapping[str, object],
    *,
    item_id: str,
    turn_id: str | None,
    created_at: str | None,
    origin: str,
    live: bool,
) -> ConversationItem:
    command = required_text(item.get("command"), "commandExecution.command")
    status = optional_text(item.get("status")) or "completed"
    blocks: list = [
        ToolInputBlock(
            block_id="input",
            summary=command,
            data={
                key: item.get(key)
                for key in ("cwd", "source", "processId")
                if item.get(key) is not None
            },
        )
    ]
    output = item.get("aggregatedOutput")
    if isinstance(output, str) and output:
        blocks.append(
            ToolOutputBlock(
                block_id="output",
                text=output,
                data={
                    key: item.get(key)
                    for key in ("exitCode", "durationMs")
                    if item.get(key) is not None
                },
            )
        )
    return ConversationItem(
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
        blocks=tuple(blocks),
        created_at=created_at,
    )


def _file_change_item(
    item_id: str,
    changes: list[object],
    *,
    turn_id: str | None,
    created_at: str | None,
    origin: str,
    status: str,
    live: bool,
) -> ConversationItem:
    blocks: list = []
    for index, raw_change in enumerate(changes):
        change = required_object(raw_change, "fileChange change")
        blocks.append(
            DiffBlock(
                block_id=f"diff-{index}",
                path=optional_text(change.get("path")),
                unified=str(change.get("diff") or ""),
            )
        )
    return ConversationItem(
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
        blocks=tuple(blocks),
        created_at=created_at,
    )


def _mcp_tool_call_item(
    item: Mapping[str, object],
    *,
    item_id: str,
    turn_id: str | None,
    created_at: str | None,
    origin: str,
    live: bool,
) -> ConversationItem:
    server = required_text(item.get("server"), "mcpToolCall.server")
    tool = required_text(item.get("tool"), "mcpToolCall.tool")
    status = optional_text(item.get("status")) or "completed"
    blocks: list = [
        ToolInputBlock(
            block_id="input",
            summary=f"{server}/{tool}",
            data=item.get("arguments"),
        )
    ]
    if item.get("result") is not None or item.get("error") is not None:
        blocks.append(
            ToolOutputBlock(
                block_id="output",
                data={"result": item.get("result"), "error": item.get("error")},
            )
        )
    return ConversationItem(
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
        blocks=tuple(blocks),
        created_at=created_at,
    )


def _roster_item(
    thread_id: str,
    *,
    status: ConversationAgentStatus,
    agent_path: str | None = None,
    blocks: tuple[ConversationContentBlock, ...] = (),
    turn_id: str | None = None,
    created_at: str | None,
    origin: str,
    live: bool,
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
            turn_id=turn_id,
            lane="harness",
            source="harness-live" if live else "native-history",
            provenance=harness_provenance(origin, observed_at=created_at),
            role="system",
            kind="notice",
            phase=_ROSTER_ITEM_PHASE.get(status, "unknown"),
            blocks=blocks,
            agent=ConversationAgentRef(agent_id=thread_id, agent_path=agent_path, status=status),
            created_at=created_at,
        )
    )


def _collab_status(
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


def _collab_final_message(agents_states: Mapping[str, object] | None, thread_id: str) -> str | None:
    """The agent's final message when the collab state carries one (``message`` text)."""

    if agents_states is None:
        return None
    entry = agents_states.get(thread_id)
    if not isinstance(entry, Mapping):
        return None
    return optional_text(entry.get("message"))


def _map_collab_tool_call(
    item: Mapping[str, object],
    *,
    item_id: str,
    turn_id: str | None,
    created_at: str | None,
    origin: str,
    live: bool,
) -> list[MapperOutput] | None:
    """Map one ``collabAgentToolCall`` item; ``None`` = not the documented collab shape.

    Outputs the collab tool call itself as a parent-timeline tool-call item plus one
    roster upsert per involved agent (``receiverThreadIds`` union ``agentsStates`` keys).
    The tool call carries a ``ConversationAgentRef`` only when it belongs to exactly
    one agent (``sendInput``/``resumeAgent``/``wait``/``closeAgent`` with a single
    receiver); a ``spawnAgent`` call is the parent's own act and stays untagged.
    """

    tool = optional_text(item.get("tool"))
    receivers_raw = item.get("receiverThreadIds")
    states_raw = item.get("agentsStates")
    if tool is None:
        return None
    if receivers_raw is not None and not isinstance(receivers_raw, list):
        return None
    if states_raw is not None and not isinstance(states_raw, Mapping):
        return None
    receiver_ids = [
        receiver for receiver in (receivers_raw or []) if isinstance(receiver, str) and receiver
    ]
    agents_states = states_raw if isinstance(states_raw, Mapping) else None
    status = optional_text(item.get("status")) or "completed"
    blocks: list[ConversationContentBlock] = [
        ToolInputBlock(
            block_id="input",
            summary=tool,
            data={
                key: item.get(key)
                for key in ("prompt", "senderThreadId", "receiverThreadIds")
                if item.get(key) is not None
            },
        )
    ]
    call_agent: ConversationAgentRef | None = None
    if tool != "spawnAgent" and len(receiver_ids) == 1:
        call_agent = ConversationAgentRef(
            agent_id=receiver_ids[0],
            status=_collab_status(agents_states, receiver_ids[0]),
        )
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
                blocks=tuple(blocks),
                agent=call_agent,
                created_at=created_at,
            )
        )
    ]
    roster_ids = list(
        dict.fromkeys(
            [
                *receiver_ids,
                *(key for key in (agents_states or {}) if isinstance(key, str) and key),
            ]
        )
    )
    for agent_thread_id in roster_ids:
        agent_status = _collab_status(agents_states, agent_thread_id)
        if agent_status == "unknown" and tool == "spawnAgent":
            # The spawn itself is the registration evidence for a receiver whose
            # state has not streamed yet.
            agent_status = "registered"
        final_message = _collab_final_message(agents_states, agent_thread_id)
        outputs.append(
            _roster_item(
                agent_thread_id,
                status=agent_status,
                blocks=(
                    (TextBlock(block_id="final-message", text=final_message),)
                    if final_message
                    else ()
                ),
                created_at=created_at,
                origin=f"{origin} collabAgentToolCall roster",
                live=live,
            )
        )
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
            status=status,
            agent_path=agent_path,
            created_at=created_at,
            origin=f"{origin} subAgentActivity roster",
            live=live,
        )
    ]


def _map_thread_started(
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
            status="registered",
            created_at=created_at,
            origin="codex app-server thread/started (sub-agent registration)",
            live=True,
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


def _map_agent_thread_status(
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
            status=roster_status,
            created_at=created_at,
            origin="codex app-server thread/status/changed (sub-agent)",
            live=True,
        )
    ]


def _map_agent_turn_started(
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
            status="running",
            created_at=created_at,
            origin="codex app-server turn/started (sub-agent)",
            live=True,
        )
    ]


def _map_agent_turn_completed(
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
            status=roster_status,
            created_at=created_at,
            origin="codex app-server turn/completed (sub-agent)",
            live=True,
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


def _map_turn_completed(
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


def _tool_phase(status: str) -> ItemPhase:
    if status == "inProgress":
        return "streaming"
    if status == "completed":
        return "completed"
    if status == "failed":
        return "failed"
    if status == "declined":
        return "interrupted"
    return "unknown"


__all__ = ["HARNESS", "map_evidence_frame", "map_native_frame"]
