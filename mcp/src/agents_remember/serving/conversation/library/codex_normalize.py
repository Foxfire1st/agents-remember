"""Codex thread-item → normalized ConversationItem parser (260718-CHATS-L2).

One responsibility: convert native Codex app-server thread items into the landed normalized
grammar with exact provenance rules. Unknown vendor kinds become explicit ``unknown-vendor``
evidence items; nothing is flattened into guessed semantics and no raw frame is retained.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal, TypeAlias

from agents_remember.models.conversations.content import (
    ConversationContentBlock,
    ConversationCorrelation,
    ConversationItem,
    DiffBlock,
    MarkdownBlock,
    TextBlock,
    ThinkingBlock,
    ToolInputBlock,
    ToolOutputBlock,
    UnknownVendorBlock,
)
from agents_remember.models.conversations.identity import (
    ProvenanceEvidence,
)
from agents_remember.serving.codex_app_server_state import (
    required_list,
    required_object,
    required_text,
    user_message_text,
)
from agents_remember.serving.conversation.library.normalize_common import (
    capped_text as _capped,
)
from agents_remember.serving.conversation.library.normalize_common import (
    text_content_parts,
)


def conversation_items_from_thread(thread: Mapping[str, object]) -> list[ConversationItem]:
    """Flatten a thread's turns into the canonical chronological item order (1-based ordinals)."""

    items: list[ConversationItem] = []
    turns = required_list(thread, "turns", context="thread/read thread")
    for raw_turn in turns:
        turn = required_object(raw_turn, context="thread/read turn")
        turn_id = required_text(turn, "id", context="thread/read turn")
        turn_status = turn.get("status")
        for raw_item in required_list(turn, "items", context="thread/read turn"):
            item = required_object(raw_item, context="thread/read item")
            items.append(
                _conversation_item(
                    item,
                    global_ordinal=len(items) + 1,
                    turn_id=turn_id,
                    turn_status=turn_status if isinstance(turn_status, str) else None,
                )
            )
    return items


ItemBuilder = Callable[[Mapping[str, object], str, dict[str, Any], str | None], ConversationItem]


def _conversation_item(
    item: Mapping[str, object],
    *,
    global_ordinal: int,
    turn_id: str,
    turn_status: str | None,
) -> ConversationItem:
    item_type = required_text(item, "type", context="Codex history item")
    item_id = required_text(item, "id", context="Codex history item")
    base: dict[str, Any] = {
        "item_id": item_id,
        "revision": 1,
        "global_ordinal": global_ordinal,
        "turn_id": turn_id,
        "evidence_ref": f"codex-item:{item_id}",
    }
    builder = _ITEM_BUILDERS.get(item_type, _unknown_item)
    return builder(item, item_id, base, turn_status)


def _user_message_item(
    item: Mapping[str, object],
    item_id: str,
    base: dict[str, Any],
    turn_status: str | None,
) -> ConversationItem:
    del turn_status
    client_id = item.get("clientId")
    return ConversationItem(
        **base,
        lane="unknown-input",
        source="native-history",
        role="user",
        kind="message",
        phase="completed",
        provenance=_unknown_input_provenance(),
        blocks=(
            TextBlock(
                block_id=f"{item_id}:b0",
                text=_capped(user_message_text(item)),
            ),
        ),
        correlation=(
            ConversationCorrelation(vendor_correlation_id=client_id)
            if isinstance(client_id, str)
            else None
        ),
    )


def _agent_message_item(
    item: Mapping[str, object],
    item_id: str,
    base: dict[str, Any],
    turn_status: str | None,
) -> ConversationItem:
    del turn_status
    return ConversationItem(
        **base,
        provenance=_harness_provenance(),
        lane="harness",
        source="native-history",
        role="assistant",
        kind="message",
        phase="completed",
        blocks=(
            MarkdownBlock(
                block_id=f"{item_id}:b0",
                markdown=_capped(required_text(item, "text", context="agentMessage")),
            ),
        ),
    )


def _reasoning_item(
    item: Mapping[str, object],
    item_id: str,
    base: dict[str, Any],
    turn_status: str | None,
) -> ConversationItem:
    del turn_status
    return ConversationItem(
        **base,
        provenance=_harness_provenance(),
        lane="harness",
        source="native-history",
        role="assistant",
        kind="thinking",
        phase="completed",
        blocks=(ThinkingBlock(block_id=f"{item_id}:b0", markdown=_capped(_reasoning_text(item))),),
    )


def _command_execution_item(
    item: Mapping[str, object],
    item_id: str,
    base: dict[str, Any],
    turn_status: str | None,
) -> ConversationItem:
    return ConversationItem(
        **base,
        provenance=_harness_provenance(),
        lane="harness",
        source="native-history",
        role="tool",
        kind="tool-call",
        phase=_tool_phase(item, turn_status),
        blocks=_command_blocks(item, item_id),
    )


def _file_change_item(
    item: Mapping[str, object],
    item_id: str,
    base: dict[str, Any],
    turn_status: str | None,
) -> ConversationItem:
    return ConversationItem(
        **base,
        provenance=_harness_provenance(),
        lane="harness",
        source="native-history",
        role="tool",
        kind="tool-call",
        phase=_tool_phase(item, turn_status),
        blocks=_file_change_blocks(item, item_id),
    )


def _mcp_tool_call_item(
    item: Mapping[str, object],
    item_id: str,
    base: dict[str, Any],
    turn_status: str | None,
) -> ConversationItem:
    return ConversationItem(
        **base,
        provenance=_harness_provenance(),
        lane="harness",
        source="native-history",
        role="tool",
        kind="tool-call",
        phase=_tool_phase(item, turn_status),
        blocks=_mcp_blocks(item, item_id),
    )


def _context_compaction_item(
    item: Mapping[str, object],
    item_id: str,
    base: dict[str, Any],
    turn_status: str | None,
) -> ConversationItem:
    del item, turn_status
    return ConversationItem(
        **base,
        lane="system",
        source="native-history",
        role="system",
        kind="notice",
        phase="completed",
        provenance=_system_provenance(),
        blocks=(
            TextBlock(
                block_id=f"{item_id}:b0",
                text="Codex compacted the conversation context at this point.",
            ),
        ),
    )


def _unknown_item(
    item: Mapping[str, object],
    item_id: str,
    base: dict[str, Any],
    turn_status: str | None,
) -> ConversationItem:
    del turn_status
    item_type = required_text(item, "type", context="Codex history item")
    return ConversationItem(
        **base,
        provenance=_harness_provenance(),
        lane="harness",
        source="native-history",
        role="tool",
        kind="unknown-vendor",
        phase="unknown",
        blocks=(
            UnknownVendorBlock(
                block_id=f"{item_id}:b0",
                vendor_type=item_type,
                safe_summary=f"unsupported Codex history item type {item_type!r}",
                evidence_ref=f"codex-item:{item_id}",
            ),
        ),
    )


_ITEM_BUILDERS: Mapping[str, ItemBuilder] = {
    "userMessage": _user_message_item,
    "agentMessage": _agent_message_item,
    "reasoning": _reasoning_item,
    "commandExecution": _command_execution_item,
    "fileChange": _file_change_item,
    "mcpToolCall": _mcp_tool_call_item,
    "contextCompaction": _context_compaction_item,
}


def _harness_provenance() -> ProvenanceEvidence:
    return ProvenanceEvidence(
        strength="native-only",
        producer="harness",
        origin="codex-native-history",
    )


def _system_provenance() -> ProvenanceEvidence:
    return ProvenanceEvidence(
        strength="native-only",
        producer="system",
        origin="codex-native-history",
    )


def _unknown_input_provenance() -> ProvenanceEvidence:
    return ProvenanceEvidence(strength="native-only", origin="codex-native-history")


def _reasoning_text(item: Mapping[str, object]) -> str:
    for key in ("summary", "content"):
        parts = _string_parts(item.get(key))
        if parts:
            return "\n\n".join(parts)
    return "(reasoning summary unavailable)"


def _string_parts(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [part for part in value if isinstance(part, str) and part]


ToolPhase: TypeAlias = Literal["completed", "failed", "interrupted"]
"""Normalized terminal phases a Codex tool item can report."""

_STATUS_PHASES: dict[str, ToolPhase] = {
    "completed": "completed",
    "failed": "failed",
    "error": "failed",
    "interrupted": "interrupted",
}


_TURN_PHASES: Mapping[str, ToolPhase] = {"failed": "failed", "interrupted": "interrupted"}


def _tool_phase(item: Mapping[str, object], turn_status: str | None) -> ToolPhase:
    status = item.get("status")
    if isinstance(status, str) and status in _STATUS_PHASES:
        return _STATUS_PHASES[status]
    if item.get("error") is not None:
        return "failed"
    return _TURN_PHASES.get(turn_status or "", "completed")


def _command_blocks(
    item: Mapping[str, object], item_id: str
) -> tuple[ConversationContentBlock, ...]:
    blocks: list[ConversationContentBlock] = []
    command = item.get("command")
    summary = command if isinstance(command, str) and command else "(command unavailable)"
    blocks.append(ToolInputBlock(block_id=f"{item_id}:b0", summary=_capped(summary, 512)))
    output = item.get("output")
    if isinstance(output, str) and output:
        blocks.append(ToolOutputBlock(block_id=f"{item_id}:b1", text=_capped(output)))
    return tuple(blocks)


def _file_change_blocks(
    item: Mapping[str, object], item_id: str
) -> tuple[ConversationContentBlock, ...]:
    blocks: list[ConversationContentBlock] = []
    changes = item.get("changes")
    index = 0
    if isinstance(changes, list):
        for raw in changes:
            if not isinstance(raw, Mapping):
                continue
            path = raw.get("path")
            diff = raw.get("diff")
            blocks.append(
                DiffBlock(
                    block_id=f"{item_id}:b{index}",
                    path=path if isinstance(path, str) else None,
                    unified=_capped(diff) if isinstance(diff, str) else None,
                )
            )
            index += 1
    if not blocks:
        blocks.append(
            UnknownVendorBlock(
                block_id=f"{item_id}:b0",
                vendor_type="fileChange",
                safe_summary="file change detail unavailable",
                evidence_ref=f"codex-item:{item_id}",
            )
        )
    return tuple(blocks)


def _mcp_blocks(item: Mapping[str, object], item_id: str) -> tuple[ConversationContentBlock, ...]:
    blocks: list[ConversationContentBlock] = []
    server = item.get("server")
    tool = item.get("tool")
    label = ".".join(
        part
        for part in (
            server if isinstance(server, str) else "",
            tool if isinstance(tool, str) else "",
        )
        if part
    )
    blocks.append(
        ToolInputBlock(
            block_id=f"{item_id}:b0",
            summary=_capped(label or "mcp tool call", 512),
        )
    )
    result = item.get("result")
    text = _result_text(result)
    if text:
        blocks.append(ToolOutputBlock(block_id=f"{item_id}:b1", text=_capped(text)))
    return tuple(blocks)


def _result_text(result: object) -> str:
    if not isinstance(result, Mapping):
        return ""
    return "\n".join(text_content_parts(result.get("content")))


__all__ = ["conversation_items_from_thread"]
