"""MCP response adapters for structural agent operations."""

from __future__ import annotations

from typing import Any

from agents_remember.application.structural.agent_tools import (
    StructuralAgentRuntime,
    dispatch_agent_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.structural.agent import DispatchAgentRequest, RetireChildRequest

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


def retire_child_payload(
    config: McpRuntimeConfig,
    request: RetireChildRequest,
    **overrides: Any,
) -> dict[str, Any]:
    # MIK-R100 rule 8: the starter retires the receipt-tracked agent in its seat.
    # (The earlier structural plane path went away with the L112 tool list; an
    # empty id is refused by name inside the native path.)
    from agents_remember.cli.role_retire import retire_native_seat  # noqa: PLC0415

    payload = retire_native_seat(config, request, environment=overrides.get("environ"))
    return _tool_payload("retire_child", payload)
