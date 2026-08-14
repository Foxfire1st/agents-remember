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
from typing import Literal

from agents_remember.models.conversations.content import (
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
from agents_remember.models.conversations.evidence import (
    EvidenceFrame,
    NativeEvidenceFrame,
)
from agents_remember.serving.conversation.projectors._codex_collab import (
    _AGENT_THREAD_NOTIFICATIONS,
    _COLLAB_AGENT_STATUS,
    _ROSTER_ITEM_PHASE,
    _THREAD_STATUS_ROSTER,
    AgentThreadNotificationMapper,
    ItemPlacement,
    _agent_notification_thread_id,
    _collab_call_agent,
    _collab_call_input_block,
    _collab_call_shape,
    _collab_final_message,
    _collab_receiver_ids,
    _collab_roster_ids,
    _collab_roster_upserts,
    _collab_status,
    _CollabCall,
    _LiveItemContext,
    _map_agent_thread_status,
    _map_agent_turn_completed,
    _map_agent_turn_started,
    _map_collab_tool_call,
    _map_sub_agent_activity,
    _map_thread_started,
    _map_turn_completed,
    _roster_item,
    _tool_phase,
)
from agents_remember.serving.conversation.projectors.common import (
    ItemPhase,
    MappedBlockDelta,
    MappedItem,
    MappedUnknownVendor,
    MapperOutput,
    UnmappableShape,
    harness_provenance,
    optional_text,
    required_list,
    required_object,
    required_text,
    unknown_input_provenance,
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
        # 260731-EFA-L7 R16: turn/diff/updated is a known Codex turn/diff control
        # notification observed live in the L6 curator stress run (ar-ev:91d4e5776af6:756
        # and siblings). It carries no transcript content and no diff consumer exists yet,
        # so it is consumed as timeline-silent exactly like the other control methods --
        # never minted as unknown-vendor conversation evidence. Genuinely unknown methods
        # still fall through to the honest unknown-vendor mapper.
        "turn/diff/updated",
    }
)


# -- Sub-agent roster mapping -------------------------------


# Collab ``agentsStates``/``subAgentActivity.kind`` vocabulary -> roster status. The probe-locked
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/projectors/codex.py:126).
def map_evidence_frame(  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/projectors/codex.py:452).
def _user_message_item(
    item: Mapping[str, object], context: _LiveItemContext
) -> ConversationItem:  # pragma: no cover
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


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/projectors/codex.py:627).
def _mcp_tool_call_item(
    item: Mapping[str, object], context: _LiveItemContext
) -> ConversationItem:  # pragma: no cover
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


__all__ = [
    "HARNESS",
    "_COLLAB_AGENT_STATUS",
    "_ROSTER_ITEM_PHASE",
    "_THREAD_STATUS_ROSTER",
    "AgentThreadNotificationMapper",
    "_CollabCall",
    "_agent_notification_thread_id",
    "_collab_call_agent",
    "_collab_call_input_block",
    "_collab_call_shape",
    "_collab_final_message",
    "_collab_receiver_ids",
    "_collab_roster_ids",
    "_collab_roster_upserts",
    "_collab_status",
    "_map_agent_thread_status",
    "_map_agent_turn_completed",
    "_map_agent_turn_started",
    "_map_thread_started",
    "_roster_item",
    "map_evidence_frame",
    "map_native_frame",
]
