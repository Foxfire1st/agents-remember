"""GT1/GT4 evidence selection: decisions need a rubric, never a success-text heuristic."""

from __future__ import annotations

import json
from typing import Any

from ..adapters.models import NativeCapture, NativeEvent
from .models import Grade, native_reference


def arguments(event: NativeEvent) -> dict[str, Any] | None:
    """Decode the declared native argument field, with no recursive blob search."""
    value = event.payload.get("arguments", event.payload.get("input"))
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def grade_continuation(capture: NativeCapture) -> Grade:
    messages = tuple(
        native_reference(event)
        for event in capture.events
        if event.kind in {"message", "tool_call", "tool_result"}
    )
    return Grade.produced(
        __file__,
        "GT1",
        "unknown" if capture.unknowns or not messages else "needs_rubric",
        messages,
        (
            "Read the actual authority and chronology: did a request or stop seek a new go for already admitted work, or hold a real decision/permission?",
        ),
    )


def grade_encountered_fault(capture: NativeCapture, artifacts: tuple[str, ...]) -> Grade:
    reads = tuple(native_reference(event) for event in capture.events if event.kind == "tool_call")
    return Grade.produced(
        __file__,
        "GT4",
        "unknown" if capture.unknowns or not artifacts else "needs_rubric",
        reads + artifacts,
        (
            "Under the authority in effect at this episode, did the Curator repair the encountered faulty memory, or accurately route a real code/meaning/other-leaf boundary? Inspect final files, not its success statement.",
        ),
    )
