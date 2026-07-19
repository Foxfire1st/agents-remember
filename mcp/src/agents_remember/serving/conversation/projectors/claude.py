"""Claude active projector: stream-json frames -> items.

Schema authority: the locked stream-json fixtures (2.1.207/2.1.210), the
adapter's parsed frame surface, and the Anthropic content-block grammar
(text/thinking/tool_use/tool_result). Assistant messages keep their text and
thinking blocks; ``tool_use`` blocks become stable-ID tool-call items keyed by
the native block id; ``tool_result`` blocks upsert the same item. Result frames
mint turn-result items and canonical terminal evidence.

Claude has no native history page (stream/replay-only by design); the active
projection hydrates from the live evidence window only, and user submissions
arrive through the adapter's *exact submission echo* — the replay-correlated
record the adapter builds from the authority's own submission (original text,
exact request id, replay message uuid) — never from a flattened projection of
native assistant/tool semantics. Provenance is resolved independently through
the submission-provenance batch.
"""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.serving.conversation.models import (
    ConversationCorrelation,
    ConversationItem,
    MarkdownBlock,
    TextBlock,
    ThinkingBlock,
    ToolInputBlock,
    ToolOutputBlock,
    UnknownVendorBlock,
)
from agents_remember.serving.conversation.projectors.common import (
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
from agents_remember.serving.harness_control_models import EvidenceFrame

HARNESS = "claude"

_CANCEL_REASONS = {"cancelled", "interrupted", "user_cancelled"}


def map_evidence_frame(frame: EvidenceFrame, *, evidence_ref: str) -> list[MapperOutput]:
    """Map one full stream-json frame; unrecognized types stay preserved."""

    raw = frame.raw
    frame_type = optional_text(raw.get("type"))
    if frame_type == "assistant":
        return _map_assistant(raw, created_at=frame.created_at, evidence_ref=evidence_ref)
    if frame_type == "result":
        return _map_result(raw, created_at=frame.created_at)
    if frame_type == "user":
        return _map_tool_carrier(raw, created_at=frame.created_at, evidence_ref=evidence_ref)
    if frame_type == "system":
        # api_retry/status feed the canonical status service via the snapshot.
        return []
    return [
        MappedUnknownVendor(
            item_id=f"claude-event-{frame.sequence}",
            vendor_type=f"claude:{frame_type or 'unknown'}",
            safe_summary=f"claude frame of type {frame_type or 'unknown'}",
            created_at=frame.created_at,
        )
    ]


def map_transcript_echo(
    entry: Mapping[str, object],
    *,
    evidence_ref: str,
) -> list[MapperOutput]:
    """Map the adapter's exact submission echo to a user message item.

    Only ``role="user"`` entries are consumed here; the echo is the authority's
    own submission record (original text, exact request id, replay uuid), so
    the item is complete without native-frame projection. Provenance is
    resolved by the engine through the submission-provenance batch.
    """

    if entry.get("role") != "user":
        raise UnmappableShape("claude transcript echo is only consumed for user submissions")
    item_id = required_text(
        entry.get("vendorCorrelationId"), "claude replay correlation uuid"
    )
    request_id = optional_text(entry.get("requestId"))
    created_at = optional_text(entry.get("createdAt"))
    return [
        MappedItem(
            item=ConversationItem(
                item_id=item_id,
                revision=1,
                global_ordinal=1,
                lane="unknown-input",
                source="native-history",
                provenance=unknown_input_provenance(
                    "agents-remember submission echo (claude replay correlation)",
                    observed_at=created_at,
                ),
                role="user",
                kind="message",
                phase="completed",
                blocks=(
                    TextBlock(block_id="text", text=str(entry.get("text") or "")),
                ),
                correlation=(
                    ConversationCorrelation(request_id=request_id) if request_id else None
                ),
                created_at=created_at,
                evidence_ref=evidence_ref,
            )
        )
    ]


def _map_assistant(
    raw: Mapping[str, object],
    *,
    created_at: str | None,
    evidence_ref: str,
) -> list[MapperOutput]:
    item_id = required_text(raw.get("uuid"), "claude assistant frame uuid")
    message = required_object(raw.get("message"), "claude assistant frame.message")
    content = required_list(message.get("content"), "claude assistant message.content")
    outputs: list[MapperOutput] = []
    blocks: list = []
    for position, raw_block in enumerate(content):
        block = required_object(raw_block, "claude assistant content block")
        block_type = required_text(block.get("type"), "claude content block type")
        if block_type == "text":
            blocks.append(
                MarkdownBlock(
                    block_id=f"text-{position}",
                    markdown=str(block.get("text") or ""),
                )
            )
        elif block_type == "thinking":
            blocks.append(
                ThinkingBlock(
                    block_id=f"thinking-{position}",
                    markdown=str(block.get("thinking") or ""),
                )
            )
        elif block_type == "tool_use":
            outputs.extend(
                _map_tool_use(
                    block,
                    parent_item_id=item_id,
                    created_at=created_at,
                )
            )
        else:
            blocks.append(
                UnknownVendorBlock(
                    block_id=f"unknown-{position}",
                    vendor_type=f"claude-block:{block_type}",
                    safe_summary=f"claude assistant content block of type {block_type}",
                    evidence_ref=f"{evidence_ref}:b{position}",
                )
            )
    if blocks:
        outputs.insert(
            0,
            MappedItem(
                item=ConversationItem(
                    item_id=item_id,
                    revision=1,
                    global_ordinal=1,
                    lane="harness",
                    source="harness-live",
                    provenance=harness_provenance(
                        "claude stream-json assistant frame", observed_at=created_at
                    ),
                    role="assistant",
                    kind="message",
                    phase="completed",
                    blocks=tuple(blocks),
                    created_at=created_at,
                    evidence_ref=evidence_ref,
                )
            ),
        )
    return outputs


def _map_tool_use(
    block: Mapping[str, object],
    *,
    parent_item_id: str,
    created_at: str | None,
) -> list[MapperOutput]:
    tool_id = required_text(block.get("id"), "claude tool_use id")
    name = required_text(block.get("name"), "claude tool_use name")
    return [
        MappedItem(
            item=ConversationItem(
                item_id=tool_id,
                revision=1,
                global_ordinal=1,
                parent_item_id=parent_item_id,
                lane="harness",
                source="harness-live",
                provenance=harness_provenance(
                    "claude stream-json tool_use block", observed_at=created_at
                ),
                role="tool",
                kind="tool-call",
                phase="streaming",
                blocks=(
                    ToolInputBlock(
                        block_id="input",
                        summary=name,
                        data=block.get("input"),
                    ),
                ),
                correlation=ConversationCorrelation(tool_call_id=tool_id),
                created_at=created_at,
            )
        )
    ]


def _map_tool_carrier(
    raw: Mapping[str, object],
    *,
    created_at: str | None,
    evidence_ref: str,
) -> list[MapperOutput]:
    """Map a non-replay user frame (the tool_result carrier)."""

    if raw.get("isReplay") is True:
        raise UnmappableShape("replayed user frames are consumed as submission echoes")
    message = required_object(raw.get("message"), "claude user frame.message")
    content = message.get("content")
    if not isinstance(content, list):
        raise UnmappableShape("claude user frame content must be a list of blocks")
    outputs: list[MapperOutput] = []
    saw_tool_result = False
    for position, raw_block in enumerate(content):
        block = required_object(raw_block, "claude user content block")
        block_type = required_text(block.get("type"), "claude content block type")
        if block_type == "tool_result":
            saw_tool_result = True
            outputs.append(_map_tool_result(block, created_at=created_at))
        else:
            outputs.append(
                MappedUnknownVendor(
                    item_id=f"claude-user-{raw.get('uuid') or evidence_ref.rsplit(':', 1)[-1]}-{position}",
                    vendor_type=f"claude-user-block:{block_type}",
                    safe_summary=f"claude user-frame content block of type {block_type}",
                    created_at=created_at,
                )
            )
    if not saw_tool_result and not outputs:
        raise UnmappableShape("claude user frame carried no mappable blocks")
    return outputs


def _map_tool_result(
    block: Mapping[str, object],
    *,
    created_at: str | None,
) -> MappedItem:
    tool_use_id = required_text(block.get("tool_use_id"), "claude tool_result tool_use_id")
    content = block.get("content")
    text_parts: list[str] = []
    if isinstance(content, str):
        text_parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(str(part.get("text") or ""))
    is_error = block.get("is_error") is True
    return MappedItem(
        item=ConversationItem(
            item_id=tool_use_id,
            revision=1,
            global_ordinal=1,
            lane="harness",
            source="harness-live",
            provenance=harness_provenance(
                "claude stream-json tool_result block", observed_at=created_at
            ),
            role="tool",
            kind="tool-call",
            phase="failed" if is_error else "completed",
            blocks=(
                ToolOutputBlock(
                    block_id="output",
                    text="\n".join(text_parts) if text_parts else None,
                    data={"isError": is_error},
                ),
            ),
            correlation=ConversationCorrelation(tool_call_id=tool_use_id),
            created_at=created_at,
        )
    )


def _map_result(
    raw: Mapping[str, object],
    *,
    created_at: str | None,
) -> list[MapperOutput]:
    uuid = required_text(raw.get("uuid"), "claude result frame uuid")
    outcome: TerminalOutcomeValue
    if raw.get("subtype") == "success" and raw.get("is_error") is False:
        outcome = "completed"
    elif raw.get("terminal_reason") in _CANCEL_REASONS:
        outcome = "interrupted"
    else:
        outcome = "failed"
    detail: str | None = None
    result_text = raw.get("result")
    errors = raw.get("errors")
    if isinstance(result_text, str):
        detail = result_text
    elif isinstance(errors, list):
        detail = "\n".join(item for item in errors if isinstance(item, str))
    stop_reason = optional_text(raw.get("stop_reason")) or optional_text(
        raw.get("terminal_reason")
    )
    outputs: list[MapperOutput] = [
        MappedItem(
            item=ConversationItem(
                item_id=f"{uuid}:result",
                revision=1,
                global_ordinal=1,
                lane="harness",
                source="harness-live",
                provenance=harness_provenance(
                    "claude stream-json result frame", observed_at=created_at
                ),
                role="system",
                kind="turn-result",
                phase=outcome,
                blocks=(),
                created_at=created_at,
            )
        ),
        MappedTurnOutcome(outcome=outcome, turn_id=None, stop_reason=stop_reason or detail),
    ]
    return outputs


__all__ = ["HARNESS", "map_evidence_frame", "map_transcript_echo"]
