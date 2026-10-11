"""Payload builders for the tools through which role agents start and message role agents."""

from __future__ import annotations

from typing import Any

from agents_remember.cli.paseo_role_tools import send_role_message, start_leaf_set, start_role
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_agents import (
    RoleMessageCall,
    RoleStartCall,
    RoleStartSetCall,
)
from agents_remember.observer.tool_events import call_target_role

from .base import _tool_payload


def role_start_set_payload(
    config: McpRuntimeConfig,
    call: RoleStartSetCall,
) -> dict[str, Any]:
    # A set starts three seats; no single target role rides the river for it.
    return _tool_payload("role_start_set", start_leaf_set(config, call))


def role_start_payload(config: McpRuntimeConfig, call: RoleStartCall) -> dict[str, Any]:
    # The river names the started role: the choke point sees the answer, not the
    # call, so the role rides a context variable around it (never the answer).
    token = call_target_role.set(call.role)
    try:
        return _tool_payload("role_start", start_role(config, call))
    finally:
        call_target_role.reset(token)


def role_message_payload(config: McpRuntimeConfig, call: RoleMessageCall) -> dict[str, Any]:
    # Same as above for the addressed role. ``call.role`` is absent when the
    # recipient is addressed by agent id; then ``send_role_message`` carries
    # the role from the resolved recipient's durable receipt instead.
    token = call_target_role.set(call.role)
    try:
        return _tool_payload("role_message", send_role_message(config, call))
    finally:
        call_target_role.reset(token)


__all__ = ["role_message_payload", "role_start_payload", "role_start_set_payload"]
