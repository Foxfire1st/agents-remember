"""Minimal Responses API server-sent-event projections for the real Codex fixture."""

from __future__ import annotations

import json
import uuid


def function_sse(
    namespace: str | None,
    name: str,
    arguments: dict[str, object],
    call_id: str,
) -> str:
    response_id = f"resp-{uuid.uuid4().hex}"
    item: dict[str, object] = {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments, separators=(",", ":")),
    }
    if namespace is not None:
        item["namespace"] = namespace
    return _event_stream(
        [
            {"type": "response.created", "response": {"id": response_id}},
            {"type": "response.output_item.done", "item": item},
            _completed(response_id),
        ]
    )


def tool_search_sse(query: str, call_id: str) -> str:
    response_id = f"resp-{uuid.uuid4().hex}"
    return _event_stream(
        [
            {"type": "response.created", "response": {"id": response_id}},
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "tool_search_call",
                    "call_id": call_id,
                    "execution": "client",
                    "arguments": {"query": query, "limit": 8},
                },
            },
            _completed(response_id),
        ]
    )


def assistant_sse(text: str) -> str:
    response_id = f"resp-{uuid.uuid4().hex}"
    return _event_stream(
        [
            {"type": "response.created", "response": {"id": response_id}},
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "id": f"msg-{uuid.uuid4().hex}",
                    "content": [{"type": "output_text", "text": text}],
                },
            },
            _completed(response_id),
        ]
    )


def _event_stream(events: list[dict[str, object]]) -> str:
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
        for event in events
    )


def _completed(response_id: str) -> dict[str, object]:
    return {
        "type": "response.completed",
        "response": {
            "id": response_id,
            "usage": {
                "input_tokens": 0,
                "input_tokens_details": None,
                "output_tokens": 0,
                "output_tokens_details": None,
                "total_tokens": 0,
            },
        },
    }
