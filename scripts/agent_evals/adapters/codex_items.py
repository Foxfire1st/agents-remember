"""Source-native Codex 0.164 TurnItem completion records, never printed output.

Wire enums/fields: openai/codex rust-v0.164.0,
codex-rs/protocol/src/items.rs and protocol.rs ItemCompletedEvent. Code-mode
children carry native exec-* item IDs inside the paired dispatcher interval.
An opaque cell without these native action records stays unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .models import SourceFile

if TYPE_CHECKING:
    from .capture import CaptureBuilder

NON_ACTION_ITEMS = frozenset({"UserMessage", "AgentMessage", "Reasoning", "Plan"})
ACTION_FIELDS = {
    "McpToolCall": ("id", "server", "tool", "arguments", "status"),
    "CommandExecution": ("id", "command", "cwd", "status"),
    "FileChange": ("id", "changes", "status", "stdout", "stderr"),
}


def supported_item(item: dict[str, Any]) -> bool:
    kind = item.get("type")
    fields = ACTION_FIELDS.get(kind) if isinstance(kind, str) else None
    if fields is None or any(field not in item for field in fields):
        return False
    if not isinstance(item.get("id"), str) or item.get("status") not in (
        "completed",
        "failed",
        "declined",
    ):
        return False
    if kind == "McpToolCall":
        return all(isinstance(item.get(key), str) and item[key] for key in ("server", "tool")) and (
            "result" in item or "error" in item
        )
    if kind == "CommandExecution":
        return isinstance(item["command"], list) and isinstance(item["cwd"], str)
    return isinstance(item["changes"], dict)


@dataclass
class _Dispatch:
    turn_id: object
    children: int = 0
    incomplete: bool = False


def covered_dispatches(records: list[dict[str, Any]], session_id: str) -> set[str]:
    active: dict[str, _Dispatch] = {}
    covered: set[str] = set()
    for row in records:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        identity = payload.get("call_id")
        if row.get("type") == "response_item" and isinstance(identity, str):
            if kind in ("function_call", "custom_tool_call") and payload.get("name") in (
                "exec",
                "functions.exec",
            ):
                metadata = payload.get("internal_chat_message_metadata_passthrough", {})
                active[identity] = _Dispatch(
                    metadata.get("turn_id") if isinstance(metadata, dict) else None
                )
            elif kind in ("function_call_output", "custom_tool_call_output"):
                state = active.pop(identity, None)
                if state is not None and state.children and not state.incomplete:
                    covered.add(identity)
        elif row.get("type") == "event_msg" and kind == "item_completed" and active:
            _observe_child(active, payload, session_id)
    return covered


def _observe_child(active: dict[str, _Dispatch], payload: dict[str, Any], session: str) -> None:
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") in NON_ACTION_ITEMS:
        return
    for state in active.values():
        valid = (
            len(active) == 1
            and payload.get("thread_id") == session
            and isinstance(payload.get("turn_id"), str)
            and (state.turn_id is None or state.turn_id == payload["turn_id"])
            and str(item.get("id", "")).startswith("exec-")
            and supported_item(item)
        )
        if valid:
            state.children += 1
        else:
            state.incomplete = True


def completed_item(builder: CaptureBuilder, payload: dict[str, Any], source: SourceFile) -> None:
    item = payload.get("item")
    if not isinstance(item, dict) or not isinstance(item.get("type"), str):
        builder.unknowns.append(
            f"{source.path}: malformed native Codex completed item; kind={payload.get('type')!r}; keys={sorted(payload)}; JSONPath=$.payload.item"
        )
        return
    if item["type"] in NON_ACTION_ITEMS:
        builder.event("native", payload, source)
        return
    if payload.get("thread_id") != builder.session_id or not supported_item(item):
        builder.unknowns.append(
            f"{source.path}: unsupported/mismatched native Codex item {item['type']!r}; keys={sorted(item)}; JSONPath=$.payload.item"
        )
        builder.event("native", payload, source)
        return
    kind = item["type"]
    name = item["tool"] if kind == "McpToolCall" else kind
    arguments = (
        item["arguments"]
        if kind == "McpToolCall"
        else {key: item[key] for key in ("command", "cwd", "parsed_cmd", "changes") if key in item}
    )
    original_stamp = builder.native_timestamp
    started, ended = payload.get("started_at_ms"), payload.get("completed_at_ms")
    builder.native_timestamp = started / 1000 if type(started) is int else original_stamp
    builder.event(
        "tool_call",
        {
            "name": name,
            "call_id": item["id"],
            "server": item.get("server"),
            "arguments": arguments,
            "native_item": item,
        },
        source,
    )
    builder.native_timestamp = ended / 1000 if type(ended) is int else original_stamp
    builder.event(
        "tool_result",
        {
            "call_id": item["id"],
            "output": item.get("result") if kind == "McpToolCall" else item,
            "native_item": item,
        },
        source,
    )
    builder.native_timestamp = original_stamp
