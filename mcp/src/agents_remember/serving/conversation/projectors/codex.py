"""Codex active projector: app-server thread items and notifications -> items.

Schema authority: the codex app-server v2 generated protocol (item/started,
item/completed ``{item, threadId, turnId, ...AtMs}``; item delta methods;
``thread/tokenUsage/updated``; turn lifecycle notifications) and the landed
installed-runtime fixture rows. Every item type with exact field evidence maps
to a typed item; every other shape becomes ``unknown-vendor`` evidence with its
native id/turn parent preserved. Codex's documented historical tool loss stays
visible through capabilities, never hidden by a completeness claim.
"""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.serving.conversation.models import (
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


def map_evidence_frame(frame: EvidenceFrame, *, evidence_ref: str) -> list[MapperOutput]:
    """Map one live notification; deltas, items, and turn outcomes stay exact.

    The evidence payload is the notification ``params`` object; the method
    itself does not cross. Discrimination uses the adapter event kind plus the
    schema-disjoint required keys of each params shape; the bare-delta family
    resolves its target block through the mapped item's kind at the engine.
    Anything else becomes unknown-vendor evidence, never a guessed meaning.
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
            return [MappedBlockDelta(item_id=item_id, block_id=f"summary-{params['summaryIndex']}", delta=delta)]
        if isinstance(params.get("contentIndex"), int):
            return [MappedBlockDelta(item_id=item_id, block_id=f"content-{params['contentIndex']}", delta=delta)]
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
            vendor_type="codex:notification",
            safe_summary="unrecognized codex notification params",
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
            blocks.append(
                TextBlock(block_id=f"text-{position}", text=str(part.get("text") or ""))
            )
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
        blocks.append(
            ThinkingBlock(block_id=f"summary-{index}", markdown=str(summary))
        )
    for index, content in enumerate(required_list(item.get("content"), "reasoning.content")):
        blocks.append(
            ThinkingBlock(block_id=f"content-{index}", markdown=str(content))
        )
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
