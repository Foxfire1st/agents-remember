"""MCP response adapter for orchestration application operations."""

from __future__ import annotations

from typing import Any

from agents_remember.application.orchestration_tools import (
    NudgeSubject,
    NudgeTarget,
    nudge_manager,
    orchestration_nudge_manager_tool,
)
from agents_remember.models.application_requests import OrchestrationNudgeRequest

from ..config import McpRuntimeConfig
from .base import _tool_payload


def orchestration_nudge_manager_payload(
    config: McpRuntimeConfig,
    *,
    reason: Any,
    target: NudgeTarget,
    subject: NudgeSubject,
    rate_limit_seconds: int = 900,
) -> dict[str, Any]:
    return _tool_payload(
        "orchestration_nudge_manager",
        orchestration_nudge_manager_tool(
            config,
            reason=reason,
            target=target,
            subject=subject,
            rate_limit_seconds=rate_limit_seconds,
        ),
    )


def registered_orchestration_nudge_payload(
    config: McpRuntimeConfig,
    request: OrchestrationNudgeRequest,
) -> dict[str, Any]:
    """Complete a registered nudge after application-owned request composition."""
    return _tool_payload(
        "orchestration_nudge_manager",
        nudge_manager(config, request),
    )
