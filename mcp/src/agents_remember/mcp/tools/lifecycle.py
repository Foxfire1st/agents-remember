"""MCP response adapters for lifecycle application operations."""

from __future__ import annotations

from typing import Any

from agents_remember.application.lifecycle.lifecycle_tools import (
    lifecycle_block_tool,
    lifecycle_end_tool,
    lifecycle_phase_tool,
    lifecycle_resume_tool,
    lifecycle_start_tool,
    lifecycle_turn_end_notification_tool,
    switch_lifecycle_tool,
)

from .base import _tool_payload


def lifecycle_start_payload() -> dict[str, Any]:
    return _tool_payload("lifecycle_start", lifecycle_start_tool())


def lifecycle_block_payload(
    *,
    kind: str | None = None,
    prompt: str | None = None,
    options: list[str] | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "lifecycle_block",
        lifecycle_block_tool(kind=kind, prompt=prompt, options=options),
    )


def lifecycle_resume_payload() -> dict[str, Any]:
    return _tool_payload("lifecycle_resume", lifecycle_resume_tool())


def lifecycle_turn_end_notification_payload(summary: str) -> dict[str, Any]:
    return _tool_payload(
        "lifecycle_turn_end_notification",
        lifecycle_turn_end_notification_tool(summary),
    )


def lifecycle_end_payload(outcome: str) -> dict[str, Any]:
    return _tool_payload("lifecycle_end", lifecycle_end_tool(outcome))


def lifecycle_phase_payload(phase: str) -> dict[str, Any]:
    return _tool_payload("lifecycle_phase", lifecycle_phase_tool(phase))


def switch_lifecycle_payload(on_unsaved: str | None = None) -> dict[str, Any]:
    return _tool_payload("switch_lifecycle", switch_lifecycle_tool(on_unsaved))
