"""Payload builders for the two tools through which role agents start and message role agents."""

from __future__ import annotations

from typing import Any

from agents_remember.cli.paseo_role_tools import send_role_message, start_role
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_agents import RoleMessageCall, RoleStartCall

from .base import _tool_payload


def role_start_payload(config: McpRuntimeConfig, call: RoleStartCall) -> dict[str, Any]:
    return _tool_payload("role_start", start_role(config, call))


def role_message_payload(config: McpRuntimeConfig, call: RoleMessageCall) -> dict[str, Any]:
    return _tool_payload("role_message", send_role_message(config, call))


__all__ = ["role_message_payload", "role_start_payload"]
