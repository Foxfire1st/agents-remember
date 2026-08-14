"""Pi active projector: durable session entries and RPC events -> items.

Schema authority: the locked Pi RPC documentation (``rpc.md``) and the pinned
``SessionEntry``/message shapes. Durable entries are the identity anchor:
messages, compaction, model/thinking changes map to typed items; every other
entry type becomes unknown-vendor evidence with its native id/parent preserved.
Live ``tool_execution_*`` events upsert tool-call items by the native
``toolCallId``; ``message_end`` triggers the engine's native continuation
(messages mint from entries, never from id-less live frames); ``message_update``
deltas stay in the substrate buffer until the completed entry lands, so no
provisional identity is ever minted.

Pi has no request-id correlation for user messages; they honestly carry
``unknown-input`` provenance instead of defaulting to the operator or the bus.
"""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.models.conversations.content import (
    ConversationCorrelation,
    ConversationItem,
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
from agents_remember.serving.conversation.projectors.common import (
    ItemPhase,
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

HARNESS = "pi"

_COMPLETED_STOP_REASONS = {"stop", "toolUse", "length"}


def map_native_frame(frame: NativeEvidenceFrame, *, evidence_ref: str) -> list[MapperOutput]:
    """Map one durable session entry; unknown entry types stay visible."""

    entry = frame.raw
    entry_type = frame.native_type
    created_at = frame.created_at
    if entry_type == "message":
        message = required_object(entry.get("message"), "pi message entry.message")
        return _map_message(
            message,
            item_id=frame.native_id,
            created_at=created_at,
            origin="pi get_entries native page",
            evidence_ref=evidence_ref,
        )
    if entry_type == "compaction":
        summary = required_text(entry.get("summary"), "pi compaction entry.summary")
        return [
            MappedItem(
                item=_notice_item(
                    item_id=frame.native_id,
                    text=summary,
                    created_at=created_at,
                    origin="pi get_entries native page",
                )
            )
        ]
    if entry_type in {"thinking_level_change", "model_change"}:
        if entry_type == "thinking_level_change":
            text = f"thinking level: {required_text(entry.get('thinkingLevel'), 'thinkingLevel')}"
        else:
            provider = optional_text(entry.get("provider")) or "unknown-provider"
            model = optional_text(entry.get("modelId")) or "unknown-model"
            text = f"model: {provider}/{model}"
        return [
            MappedItem(
                item=_notice_item(
                    item_id=frame.native_id,
                    text=text,
                    created_at=created_at,
                    origin="pi get_entries native page",
                )
            )
        ]
    return [
        MappedUnknownVendor(
            item_id=frame.native_id,
            vendor_type=f"pi-entry:{entry_type}",
            safe_summary=f"pi session entry of type {entry_type}",
            live=False,
            turn_id=None,
            created_at=created_at,
        )
    ]


def map_evidence_frame(
    frame: EvidenceFrame,
    *,
    evidence_ref: str,  # noqa: ARG001 - protocol keyword seam; refs are minted engine-side for this harness
    parent_thread_id: str | None = None,  # noqa: ARG001 - multiplexed-harness demux context; pi carries no sub-agent threads
) -> list[MapperOutput]:
    """Map one live RPC event; message records themselves mint from entries."""

    raw = frame.raw
    event_type = optional_text(raw.get("type"))
    if event_type == "tool_execution_start":
        return [
            MappedItem(
                item=_live_tool_item(
                    raw,
                    created_at=frame.created_at,
                    phase="streaming",
                    include_input=True,
                )
            )
        ]
    if event_type == "tool_execution_update":
        return [
            MappedItem(
                item=_live_tool_item(
                    raw,
                    created_at=frame.created_at,
                    phase="streaming",
                    include_input=False,
                )
            )
        ]
    if event_type == "tool_execution_end":
        is_error = raw.get("isError") is True
        return [
            MappedItem(
                item=_live_tool_item(
                    raw,
                    created_at=frame.created_at,
                    phase="failed" if is_error else "completed",
                    include_input=False,
                )
            )
        ]
    if event_type in {"message_end", "message_update", "agent_end"}:
        # Completed messages mint from durable entries (native identity);
        # in-flight deltas stay in the substrate buffer until then.
        return []
    return [
        MappedUnknownVendor(
            item_id=f"pi-event-{frame.sequence}",
            vendor_type=f"pi:{event_type or 'unknown'}",
            safe_summary=f"pi rpc event of type {event_type or 'unknown'}",
            created_at=frame.created_at,
        )
    ]


def _map_message(
    message: Mapping[str, object],
    *,
    item_id: str,
    created_at: str | None,
    origin: str,
    evidence_ref: str,
) -> list[MapperOutput]:
    role = required_text(message.get("role"), "pi message.role")
    if role == "user":
        return [
            _map_user_message(
                message,
                item_id=item_id,
                created_at=created_at,
                origin=origin,
                evidence_ref=evidence_ref,
            )
        ]
    if role == "assistant":
        return _map_assistant_message(
            message,
            item_id=item_id,
            created_at=created_at,
            origin=origin,
            evidence_ref=evidence_ref,
        )
    if role == "toolResult":
        return [_map_tool_result_message(message, created_at=created_at, origin=origin)]
    return [
        MappedUnknownVendor(
            item_id=item_id,
            vendor_type=f"pi-message:{role}",
            safe_summary=f"pi message of role {role}",
            turn_id=None,
            created_at=created_at,
        )
    ]


def _map_user_message(
    message: Mapping[str, object],
    *,
    item_id: str,
    created_at: str | None,
    origin: str,
    evidence_ref: str,
) -> MappedItem:
    content = message.get("content")
    blocks: list = []
    if isinstance(content, str):
        blocks.append(TextBlock(block_id="text-0", text=content))
    elif isinstance(content, list):
        for position, raw_part in enumerate(content):
            part = required_object(raw_part, "pi user content part")
            part_type = required_text(part.get("type"), "pi user content part type")
            if part_type == "text":
                blocks.append(
                    TextBlock(block_id=f"text-{position}", text=str(part.get("text") or ""))
                )
            else:
                blocks.append(
                    UnknownVendorBlock(
                        block_id=f"unknown-{position}",
                        vendor_type=f"pi-user-part:{part_type}",
                        safe_summary=f"pi user message content part of type {part_type}",
                        evidence_ref=f"{evidence_ref}:b{position}",
                    )
                )
    else:
        raise UnmappableShape("pi user message content must be text or a part list")
    return MappedItem(
        item=ConversationItem(
            item_id=item_id,
            revision=1,
            global_ordinal=1,
            lane="unknown-input",
            source="native-history",
            provenance=unknown_input_provenance(origin, observed_at=created_at),
            role="user",
            kind="message",
            phase="completed",
            blocks=tuple(blocks),
            created_at=created_at,
            evidence_ref=evidence_ref,
        )
    )


def _map_assistant_message(
    message: Mapping[str, object],
    *,
    item_id: str,
    created_at: str | None,
    origin: str,
    evidence_ref: str,
) -> list[MapperOutput]:
    content = required_list(message.get("content"), "pi assistant message.content")
    outputs: list[MapperOutput] = []
    blocks: list = []
    for position, raw_part in enumerate(content):
        part = required_object(raw_part, "pi assistant content part")
        part_type = required_text(part.get("type"), "pi assistant content part type")
        if part_type == "text":
            blocks.append(
                MarkdownBlock(block_id=f"text-{position}", markdown=str(part.get("text") or ""))
            )
        elif part_type == "thinking":
            blocks.append(
                ThinkingBlock(
                    block_id=f"thinking-{position}", markdown=str(part.get("thinking") or "")
                )
            )
        elif part_type == "toolCall":
            outputs.append(
                _entry_tool_call_item(
                    part,
                    parent_item_id=item_id,
                    created_at=created_at,
                    origin=origin,
                )
            )
        else:
            blocks.append(
                UnknownVendorBlock(
                    block_id=f"unknown-{position}",
                    vendor_type=f"pi-assistant-part:{part_type}",
                    safe_summary=f"pi assistant content part of type {part_type}",
                    evidence_ref=f"{evidence_ref}:b{position}",
                )
            )
    stop_reason = optional_text(message.get("stopReason"))
    error_message = optional_text(message.get("errorMessage"))
    if blocks:
        outputs.insert(
            0,
            MappedItem(
                item=ConversationItem(
                    item_id=item_id,
                    revision=1,
                    global_ordinal=1,
                    lane="harness",
                    source="native-history",
                    provenance=harness_provenance(origin, observed_at=created_at),
                    role="assistant",
                    kind="message",
                    phase="failed" if stop_reason == "error" else "completed",
                    blocks=tuple(blocks),
                    created_at=created_at,
                    evidence_ref=evidence_ref,
                )
            ),
        )
    if stop_reason in _COMPLETED_STOP_REASONS:
        outputs.append(MappedTurnOutcome(outcome="completed", stop_reason=stop_reason))
    elif stop_reason == "aborted":
        outputs.extend(_terminal_outputs(item_id, "interrupted", stop_reason, created_at, origin))
    elif stop_reason == "error":
        outputs.extend(
            _terminal_outputs(item_id, "failed", error_message or stop_reason, created_at, origin)
        )
    return outputs


def _terminal_outputs(
    item_id: str,
    outcome: TerminalOutcomeValue,
    stop_reason: str | None,
    created_at: str | None,
    origin: str,
) -> list[MapperOutput]:
    outputs: list[MapperOutput] = []
    outputs.append(
        MappedItem(
            item=ConversationItem(
                item_id=f"turn-result:{item_id}",
                revision=1,
                global_ordinal=1,
                lane="harness",
                source="native-history",
                provenance=harness_provenance(origin, observed_at=created_at),
                role="system",
                kind="turn-result",
                phase=outcome,
                blocks=(),
                created_at=created_at,
            )
        )
    )
    outputs.append(MappedTurnOutcome(outcome=outcome, stop_reason=stop_reason))
    return outputs


def _map_tool_result_message(
    message: Mapping[str, object],
    *,
    created_at: str | None,
    origin: str,
) -> MappedItem:
    tool_call_id = required_text(message.get("toolCallId"), "pi toolResult.toolCallId")
    content = message.get("content")
    text_parts: list[str] = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(str(part.get("text") or ""))
    is_error = message.get("isError") is True
    return MappedItem(
        item=ConversationItem(
            item_id=tool_call_id,
            revision=1,
            global_ordinal=1,
            lane="harness",
            source="native-history",
            provenance=harness_provenance(origin, observed_at=created_at),
            role="tool",
            kind="tool-call",
            phase="failed" if is_error else "completed",
            blocks=(
                ToolOutputBlock(
                    block_id="output",
                    text="\n".join(text_parts) if text_parts else None,
                    data={"isError": is_error, "toolName": message.get("toolName")},
                ),
            ),
            correlation=ConversationCorrelation(tool_call_id=tool_call_id),
            created_at=created_at,
        )
    )


def _entry_tool_call_item(
    part: Mapping[str, object],
    *,
    parent_item_id: str,
    created_at: str | None,
    origin: str,
) -> MappedItem:
    tool_id = required_text(part.get("id"), "pi toolCall part id")
    name = required_text(part.get("name"), "pi toolCall part name")
    return MappedItem(
        item=ConversationItem(
            item_id=tool_id,
            revision=1,
            global_ordinal=1,
            parent_item_id=parent_item_id,
            lane="harness",
            source="native-history",
            provenance=harness_provenance(origin, observed_at=created_at),
            role="tool",
            kind="tool-call",
            phase="streaming",
            blocks=(ToolInputBlock(block_id="input", summary=name, data=part.get("arguments")),),
            correlation=ConversationCorrelation(tool_call_id=tool_id),
            created_at=created_at,
        )
    )


def _live_tool_item(
    raw: Mapping[str, object],
    *,
    created_at: str | None,
    phase: ItemPhase,
    include_input: bool,
) -> ConversationItem:
    tool_call_id = required_text(raw.get("toolCallId"), "pi tool execution toolCallId")
    tool_name = optional_text(raw.get("toolName")) or "tool"
    blocks: list = []
    if include_input:
        blocks.append(ToolInputBlock(block_id="input", summary=tool_name, data=raw.get("args")))
    result = raw.get("result") if "result" in raw else raw.get("partialResult")
    if result is not None:
        blocks.append(
            ToolOutputBlock(
                block_id="output", data={"result": result, "isError": raw.get("isError") is True}
            )
        )
    return ConversationItem(
        item_id=tool_call_id,
        revision=1,
        global_ordinal=1,
        lane="harness",
        source="harness-live",
        provenance=harness_provenance("pi rpc tool execution event", observed_at=created_at),
        role="tool",
        kind="tool-call",
        phase=phase,
        blocks=tuple(blocks),
        correlation=ConversationCorrelation(tool_call_id=tool_call_id),
        created_at=created_at,
    )


def _notice_item(
    *,
    item_id: str,
    text: str,
    created_at: str | None,
    origin: str,
) -> ConversationItem:
    return ConversationItem(
        item_id=item_id,
        revision=1,
        global_ordinal=1,
        lane="system",
        source="harness-live" if origin.endswith("event") else "native-history",
        provenance=harness_provenance(origin, observed_at=created_at),
        role="system",
        kind="notice",
        phase="completed",
        blocks=(TextBlock(block_id="text", text=text),),
        created_at=created_at,
    )


__all__ = ["HARNESS", "map_evidence_frame", "map_native_frame"]
