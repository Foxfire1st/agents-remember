"""Normalize the observed Hermes MCP dispatcher into the graded action list."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

from .models import NativeCapture, NativeEvent, tool_server
from .opaque_dispatch import opaque_action


def _child(event: NativeEvent) -> dict[str, Any]:
    raw = event.payload.get("arguments")
    if not isinstance(raw, str):
        raise ValueError("arguments is not native serialized JSON")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"calls"}:
        raise ValueError("arguments must contain exactly calls")
    calls = value["calls"]
    if not isinstance(calls, list) or len(calls) != 1:
        raise ValueError("observed calls shape contains exactly one child")
    child = calls[0]
    if not isinstance(child, dict) or set(child) != {"name", "arguments"}:
        raise ValueError("child must contain exactly name and arguments")
    if not isinstance(child["name"], str) or not child["name"].strip():
        raise ValueError("child name is not nonblank text")
    if child["name"] == "tool_call" or not isinstance(child["arguments"], dict):
        raise ValueError("child is another dispatcher or its arguments are not an object")
    return child


def _problem(event: NativeEvent, index: int, reason: str) -> str:
    digest = hashlib.sha256(
        json.dumps(event.payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return (
        f"{event.source}: unsupported Hermes dispatch kind={event.kind!r}; "
        f"keys={sorted(event.payload)}; JSONPath=$.events[{index}].payload.arguments.calls; "
        f"sha256={digest}; {reason}"
    )


def _paired_result(capture: NativeCapture, event: NativeEvent, child: dict[str, Any]) -> None:
    call_id = event.payload.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("native parent call_id is absent")
    matches = [
        item
        for item in capture.events
        if item.kind == "tool_result" and item.payload.get("call_id") == call_id
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one native paired result, found {len(matches)}")
    result = matches[0]
    if result.payload.get("name") != child["name"]:
        raise ValueError("native paired result names a different child")
    if not isinstance(result.payload.get("output"), str) or result.sequence <= event.sequence:
        raise ValueError("native paired result has no text output or precedes its call")


def _refusal_message(event: NativeEvent) -> str:
    """Reproduce only the two observed pre-dispatch input validation errors."""
    raw = event.payload.get("arguments")
    if not isinstance(raw, str):
        raise ValueError("refused arguments are not native serialized JSON")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"calls"}:
        raise ValueError("refused arguments have an unobserved outer shape")
    calls = value["calls"]
    if isinstance(calls, str):
        try:
            json.loads(calls)
        except json.JSONDecodeError as error:
            return f"tool_call 'calls' is not valid JSON: {error}"
        raise ValueError("refused calls string is valid JSON")
    if (
        isinstance(calls, list)
        and len(calls) == 1
        and isinstance(calls[0], dict)
        and set(calls[0]) == {"arguments"}
        and isinstance(calls[0]["arguments"], dict)
    ):
        return "tool_call calls[0] requires a 'name'"
    raise ValueError("refused calls have an unobserved validation shape")


def _refusal_result(capture: NativeCapture, event: NativeEvent) -> NativeEvent:
    call_id = event.payload.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        raise ValueError("refused native parent call_id is absent")
    calls = [
        item
        for item in capture.events
        if item.kind == "tool_call" and item.payload.get("call_id") == call_id
    ]
    matches = [
        item
        for item in capture.events
        if item.kind == "tool_result" and item.payload.get("call_id") == call_id
    ]
    if len(calls) != 1 or len(matches) != 1:
        raise ValueError("refusal needs exactly one native call and paired result")
    result = matches[0]
    if (
        event.session_id != capture.session_id
        or (result.session_id, result.source, result.source_sha256)
        != (event.session_id, event.source, event.source_sha256)
        or result.sequence <= event.sequence
        or not any(
            source.path == event.source and source.sha256 == event.source_sha256
            for source in capture.source_files
        )
    ):
        raise ValueError("refusal native session/source/hash/order is not bound to its call")
    if (
        set(result.payload) != {"call_id", "name", "output"}
        or result.payload["name"] != "tool_call"
        or not isinstance(result.payload["output"], str)
        or json.loads(result.payload["output"]) != {"error": _refusal_message(event)}
    ):
        raise ValueError("native result is not the matching observed dispatcher refusal")
    return result


def _refused_dispatch(capture: NativeCapture, event: NativeEvent, index: int) -> NativeEvent:
    payload = dict(event.payload)
    previous = payload.pop("native_dispatch", None)
    if set(payload) != {"call_id", "name", "arguments", "server"} or payload["server"] is not None:
        raise ValueError("refused native dispatcher payload has an unobserved shape")
    result = _refusal_result(capture, event)
    native = {
        "payload": payload,
        "path": f"$.events[{index}].payload.arguments",
        "outcome": "refused",
        "effect": "none",
        "refusal": result.payload,
    }
    if previous is not None and previous != native:
        raise ValueError("existing native refusal provenance differs from the paired evidence")
    return replace(event, payload={**payload, "native_dispatch": native})


def normalize_hermes_dispatches(capture: NativeCapture) -> NativeCapture:
    """Expose proved children/refusals; retain other wrappers as opaque actions."""
    if capture.harness != "hermes":
        raise ValueError("Hermes dispatch normalization requires a Hermes capture")
    events = list(capture.events)
    unknowns = list(capture.unknowns)
    for index, event in enumerate(capture.events):
        if event.kind != "tool_call" or event.payload.get("name") != "tool_call":
            continue
        try:
            child = _child(event)
            _paired_result(capture, event, child)
        except (TypeError, ValueError) as error:
            try:
                events[index] = _refused_dispatch(capture, event, index)
            except (TypeError, ValueError) as refusal_error:
                events[index], material_unknowns = opaque_action(
                    capture, event, _problem(event, index, f"{error}; {refusal_error}")
                )
                unknowns.extend(material_unknowns)
            continue
        events[index] = replace(
            event,
            payload={
                "call_id": event.payload["call_id"],
                "name": child["name"],
                "arguments": child["arguments"],
                "server": tool_server(child["name"]),
                "native_dispatch": {
                    "payload": event.payload,
                    "path": f"$.events[{index}].payload.arguments.calls[0]",
                },
            },
        )
    return replace(capture, events=tuple(events), unknowns=tuple(dict.fromkeys(unknowns)))
