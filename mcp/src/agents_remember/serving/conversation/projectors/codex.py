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

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

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
        ItemPlacement(
            turn_id=frame.native_parent_id,
            created_at=frame.created_at,
            origin="codex thread/read native page",
            live=False,
            evidence_ref=evidence_ref,
        ),
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
            ItemPlacement(
                turn_id=optional_text(params.get("turnId")),
                created_at=frame.created_at,
                origin="codex app-server item/completed",
                live=True,
                evidence_ref=evidence_ref,
            ),
            phase_override="completed",
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
    return _map_codex_notification(
        frame, evidence_ref=evidence_ref, parent_thread_id=parent_thread_id
    )


def _map_codex_notification(
    frame: EvidenceFrame,
    *,
    evidence_ref: str,
    parent_thread_id: str | None,
) -> list[MapperOutput]:
    """Route one codex notification: by native method first, then by params shape."""

    method = frame.native_method
    if method in _SILENT_NOTIFICATION_METHODS:
        # Session lifecycle/status/telemetry; no timeline item. Recognized by the native method,
        # dropped explicitly the same way ``state``-kind frames and usage frames are dropped.
        return []
    agent_thread = _AGENT_THREAD_NOTIFICATIONS.get(method or "")
    if agent_thread is not None:
        return agent_thread(
            frame.raw, parent_thread_id=parent_thread_id, created_at=frame.created_at
        )
    return _map_notification_params(frame, method=method, evidence_ref=evidence_ref)


def _map_notification_params(
    frame: EvidenceFrame,
    *,
    method: str | None,
    evidence_ref: str,
) -> list[MapperOutput]:
    """Discriminate one notification by the schema-disjoint required keys of its params.

    A params object carrying an ``item`` body is a full item; one carrying only an ``itemId``
    mutates or annotates an existing row; usage/rate evidence belongs to telemetry. Anything else
    is preserved as unknown-vendor evidence rather than given a guessed meaning.
    """

    params = frame.raw
    if isinstance(params.get("item"), dict):
        return _map_notification_item(frame, evidence_ref=evidence_ref)
    if isinstance(params.get("itemId"), str):
        return _map_item_scoped_notification(frame)
    if "tokenUsage" in params or "rateLimits" in params:
        # Usage/rate evidence feeds L3 telemetry projections; it never becomes
        # a token-theater timeline row.
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


def _map_notification_item(frame: EvidenceFrame, *, evidence_ref: str) -> list[MapperOutput]:
    """Map an item body delivered live; ``startedAtMs`` is what tells started from completed."""

    params = frame.raw
    started = "startedAtMs" in params
    return _map_thread_item(
        required_object(params.get("item"), "codex item params.item"),
        ItemPlacement(
            turn_id=optional_text(params.get("turnId")),
            created_at=frame.created_at,
            origin="codex app-server item/started"
            if started
            else "codex app-server item/completed",
            live=True,
            evidence_ref=evidence_ref,
        ),
        phase_override="streaming" if started else "completed",
    )


def _map_item_scoped_notification(frame: EvidenceFrame) -> list[MapperOutput]:
    """Map a notification that names an existing item but carries no item body.

    Deltas append to a block and patch updates replace a file-change row. Everything else scoped
    to an item -- MCP progress notes, terminal interaction -- resolves on completion and mints
    nothing here.
    """

    params = frame.raw
    if "delta" in params:
        return _map_block_delta(params)
    if isinstance(params.get("changes"), list):
        return [
            MappedItem(
                item=_file_change_item(
                    required_list(params.get("changes"), "patchUpdated params.changes"),
                    _LiveItemContext(
                        item_id=required_text(params.get("itemId"), "patchUpdated params.itemId"),
                        turn_id=optional_text(params.get("turnId")),
                        created_at=frame.created_at,
                        origin="codex app-server item/fileChange/patchUpdated",
                        live=True,
                    ),
                    status="inProgress",
                )
            )
        ]
    return []


def _map_block_delta(params: Mapping[str, object]) -> list[MapperOutput]:
    """Route one text delta to the block it extends.

    A summary or content index names the target block outright. The bare-delta family --
    agentMessage, plan, and commandExecution output -- shares one shape with no index, so the
    engine resolves the block from the mapped item's kind instead.
    """

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
    return [MappedBlockDelta(item_id=item_id, block_id="", delta=delta)]


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


def _map_thread_item(
    item: Mapping[str, object],
    placement: ItemPlacement,
    *,
    phase_override: ItemPhase | None = None,
) -> list[MapperOutput]:
    """Map one app-server thread item; an unrecognized type is preserved, never guessed.

    The routers are tried in order and each returns ``None`` for a type it does not own, so a type
    nobody claims -- or a collab item whose identity fields do not prove an agent -- falls through
    to preserved unknown-vendor evidence.
    """

    context = _LiveItemContext(
        item_id=required_text(item.get("id"), "codex thread item id"),
        turn_id=placement.turn_id,
        created_at=placement.created_at,
        origin=placement.origin,
        live=placement.live,
        phase=phase_override or "completed",
        evidence_ref=placement.evidence_ref,
    )
    item_type = required_text(item.get("type"), "codex thread item type")
    for route in (_map_prose_item, _map_tool_item, _map_collab_item):
        mapped = route(item, item_type, context)
        if mapped is not None:
            return mapped
    return [
        MappedUnknownVendor(
            item_id=context.item_id,
            vendor_type=f"codex:{item_type}",
            safe_summary=f"codex item of type {item_type}",
            live=placement.live,
            turn_id=placement.turn_id,
            created_at=placement.created_at,
        )
    ]


def _map_prose_item(
    item: Mapping[str, object], item_type: str, context: _LiveItemContext
) -> list[MapperOutput] | None:
    """The item types that are somebody's words: the prompt, the reply, the plan, the reasoning.

    ``None`` means this router does not own the type, not that the item is unmappable.
    """

    if item_type == "userMessage":
        return [MappedItem(item=_user_message_item(item, context))]
    if item_type == "agentMessage":
        return [MappedItem(item=_markdown_item(item, context, kind="message"))]
    if item_type == "plan":
        return [MappedItem(item=_markdown_item(item, context, kind="plan"))]
    if item_type == "reasoning":
        return [MappedItem(item=_reasoning_item(item, context))]
    return None


def _map_tool_item(
    item: Mapping[str, object], item_type: str, context: _LiveItemContext
) -> list[MapperOutput] | None:
    """The item types that are the agent acting on the world: shell, edits, MCP calls.

    ``None`` means this router does not own the type, not that the item is unmappable.
    """

    if item_type == "commandExecution":
        return [MappedItem(item=_command_execution_item(item, context))]
    if item_type == "fileChange":
        return [
            MappedItem(
                item=_file_change_item(
                    required_list(item.get("changes"), "fileChange item.changes"),
                    context,
                    status=optional_text(item.get("status")) or "completed",
                )
            )
        ]
    if item_type == "mcpToolCall":
        return [MappedItem(item=_mcp_tool_call_item(item, context))]
    return None


def _map_collab_item(
    item: Mapping[str, object], item_type: str, context: _LiveItemContext
) -> list[MapperOutput] | None:
    """The parent-thread items that model the collaboration itself, minting roster rows.

    Unlike the other routers these mappers may decline a well-typed item whose identity fields do
    not prove an agent; ``None`` from them means the same thing as an unowned type -- preserve the
    item as unknown-vendor evidence rather than invent a roster entry.
    """

    if item_type == "collabAgentToolCall":
        return _map_collab_tool_call(
            item,
            _LiveItemContext(
                item_id=context.item_id,
                turn_id=context.turn_id,
                created_at=context.created_at,
                origin=context.origin,
                live=context.live,
            ),
        )
    if item_type == "subAgentActivity":
        return _map_sub_agent_activity(
            item,
            created_at=context.created_at,
            origin=context.origin,
            live=context.live,
        )
    return None


def _markdown_item(
    item: Mapping[str, object], context: _LiveItemContext, *, kind: Literal["message", "plan"]
) -> ConversationItem:
    """One assistant item whose whole body is a single markdown block (agentMessage, plan)."""

    return ConversationItem(
        item_id=context.item_id,
        revision=1,
        global_ordinal=1,
        turn_id=context.turn_id,
        lane="harness",
        source="harness-live" if context.live else "native-history",
        provenance=harness_provenance(context.origin, observed_at=context.created_at),
        role="assistant",
        kind=kind,
        phase=context.phase,
        blocks=(MarkdownBlock(block_id="markdown", markdown=str(item.get("text") or "")),),
        created_at=context.created_at,
    )


def _user_message_item(item: Mapping[str, object], context: _LiveItemContext) -> ConversationItem:
    item_id = context.item_id
    turn_id = context.turn_id
    created_at = context.created_at
    origin = context.origin
    phase = context.phase
    evidence_ref = context.evidence_ref

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


def _reasoning_item(item: Mapping[str, object], context: _LiveItemContext) -> ConversationItem:
    item_id = context.item_id
    turn_id = context.turn_id
    created_at = context.created_at
    origin = context.origin
    live = context.live
    phase = context.phase

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
    item: Mapping[str, object], context: _LiveItemContext
) -> ConversationItem:
    item_id = context.item_id
    turn_id = context.turn_id
    created_at = context.created_at
    origin = context.origin
    live = context.live

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
    changes: list[object],
    context: _LiveItemContext,
    *,
    status: str,
) -> ConversationItem:
    item_id = context.item_id
    turn_id = context.turn_id
    created_at = context.created_at
    origin = context.origin
    live = context.live

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


def _mcp_tool_call_item(item: Mapping[str, object], context: _LiveItemContext) -> ConversationItem:
    item_id = context.item_id
    turn_id = context.turn_id
    created_at = context.created_at
    origin = context.origin
    live = context.live

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
            ItemPlacement(
                created_at=created_at,
                origin="codex app-server thread/status/changed (sub-agent)",
                live=True,
            ),
            status=roster_status,
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
            ItemPlacement(
                created_at=created_at, origin="codex app-server turn/started (sub-agent)", live=True
            ),
            status="running",
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
