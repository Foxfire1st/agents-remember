"""Preserve uninterpreted native dispatches without inventing child actions."""

from __future__ import annotations

import copy
from dataclasses import asdict, replace
from typing import Any

from ..artifacts import json_digest
from .models import NativeCapture, NativeEvent, raw_export_digest


def _opaque_metadata(event: NativeEvent) -> dict[str, Any] | None:
    value = event.payload.get("native_dispatch")
    if (
        event.payload.get("opaque") is True
        and isinstance(value, dict)
        and value.get("classification") == "opaque"
        and value.get("rawPayloadBasis") == "original-capture-event.payload"
        and isinstance(value.get("rawPayloadSha256"), str)
    ):
        return value
    return None


def _payload_digest(event: NativeEvent) -> str:
    metadata = _opaque_metadata(event)
    return metadata["rawPayloadSha256"] if metadata is not None else json_digest(event.payload)


def _key(event: NativeEvent) -> tuple[str, str, str, object]:
    return event.session_id, event.source, event.source_sha256, event.payload.get("call_id")


def _binding(capture: NativeCapture, event: NativeEvent) -> tuple[int, list[str]]:
    problems = []
    if not capture.session_id or event.session_id != capture.session_id:
        problems.append("native capture/event session identity is absent or differs")
    if not isinstance(event.payload.get("call_id"), str) or not event.payload["call_id"]:
        problems.append("native call identity is absent")
    if not any(
        (source.path, source.sha256) == (event.source, event.source_sha256)
        for source in capture.source_files
    ):
        problems.append("native call source/hash is not bound by the capture")
    matches = [
        (index, item)
        for index, item in enumerate(capture.events)
        if item.kind == "tool_call" and _key(item) == _key(event)
    ]
    if len(matches) != 1:
        problems.append(f"native call identity is not unique in capture (found {len(matches)})")
        return event.sequence, problems
    index, original = matches[0]
    if original.sequence != event.sequence or _payload_digest(original) != _payload_digest(event):
        problems.append("native call payload/sequence differs from its capture event")
    return index, problems


def _outputs(capture: NativeCapture, event: NativeEvent, index: int) -> list[dict[str, Any]]:
    return [
        asdict(item)
        for position, item in enumerate(capture.events)
        if position > index
        and item.kind == "tool_result"
        and _key(item) == _key(event)
        and item.sequence > event.sequence
    ]


def opaque_action(
    capture: NativeCapture, event: NativeEvent, reason: str
) -> tuple[NativeEvent, tuple[str, ...]]:
    """Keep a complete opaque call and all of its source-bound native outputs."""
    index, problems = _binding(capture, event)
    outputs = [] if problems else _outputs(capture, event, index)
    if not problems and not outputs:
        problems.append("no later native result with matching session/call/source/hash")
    unknowns = tuple(
        f"capture-absent: opaque dispatch call_id={event.payload.get('call_id')}: {problem}; "
        f"source={event.source}; sourceSha256={event.source_sha256}"
        for problem in problems
    )
    if _opaque_metadata(event) is not None:
        return event, unknowns
    metadata = {
        "classification": "opaque",
        "reason": reason,
        "kind": event.kind,
        "keys": sorted(event.payload),
        "path": f"$.events[{index}].payload",
        "pathBasis": "original pre-merge native capture events",
        "sourcePath": event.source,
        "sourceSha256": event.source_sha256,
        "originalRawExportSha256": raw_export_digest(
            capture.harness, capture.session_id, capture.raw_records
        ),
        "rawPayloadSha256": json_digest(event.payload),
        "rawPayloadBasis": "original-capture-event.payload",
        "pairedOutputs": outputs,
        "pairedOutputsSequenceBasis": "original pre-merge native capture events",
    }
    return replace(
        event,
        payload={**copy.deepcopy(event.payload), "opaque": True, "native_dispatch": metadata},
    ), unknowns
