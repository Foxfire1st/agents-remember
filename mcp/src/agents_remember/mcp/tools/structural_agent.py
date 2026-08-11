"""MCP response adapters for structural agent operations."""

from __future__ import annotations

from typing import Any

from agents_remember.application.structural.agent_tools import (
    StructuralAgentRuntime,
    dispatch_agent_tool,
    message_child_tool,
    message_parent_tool,
    rename_child_tool,
    rename_self_tool,
    retire_child_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.structural.agent import (
    DispatchAgentRequest,
    RenameChildRequest,
    RetireChildRequest,
    StructuralMessageRequest,
)

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


def message_parent_payload(
    config: McpRuntimeConfig,
    request: StructuralMessageRequest,
    **overrides: Any,
) -> dict[str, Any]:
    return _tool_payload(
        "message_parent",
        message_parent_tool(
            config,
            request,
            _runtime(overrides),
        ),
    )


def message_child_payload(
    config: McpRuntimeConfig,
    request: StructuralMessageRequest,
    **overrides: Any,
) -> dict[str, Any]:
    return _tool_payload(
        "message_child",
        message_child_tool(
            config,
            request,
            _runtime(overrides),
        ),
    )


def retire_child_payload(
    config: McpRuntimeConfig,
    request: RetireChildRequest,
    **overrides: Any,
) -> dict[str, Any]:
    return _tool_payload(
        "retire_child",
        retire_child_tool(
            config,
            request,
            _runtime(overrides),
        ),
    )


def rename_child_payload(
    config: McpRuntimeConfig,
    request: RenameChildRequest,
    **overrides: Any,
) -> dict[str, Any]:
    return _tool_payload(
        "rename_child",
        rename_child_tool(
            config,
            request,
            _runtime(overrides),
        ),
    )


def rename_self_payload(
    config: McpRuntimeConfig,
    *,
    label: str,
    **overrides: Any,
) -> dict[str, Any]:
    return _tool_payload(
        "rename_self",
        rename_self_tool(config, label=label, **overrides),
    )
