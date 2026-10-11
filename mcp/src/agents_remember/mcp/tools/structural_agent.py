"""MCP response adapters for structural agent operations."""

from __future__ import annotations

from typing import Any

from agents_remember.application.structural.agent_tools import (
    StructuralAgentRuntime,
    dispatch_agent_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.structural.agent import DispatchAgentRequest

from .base import _tool_payload


def _runtime(overrides: dict[str, Any]) -> StructuralAgentRuntime:
    return StructuralAgentRuntime(**overrides)


def dispatch_agent_payload(
    config: McpRuntimeConfig,
    request: DispatchAgentRequest,
    **overrides: Any,
) -> dict[str, Any]:
    return _tool_payload(
        "dispatch_agent",
        dispatch_agent_tool(
            config,
            request,
            _runtime(overrides),
        ),
    )
