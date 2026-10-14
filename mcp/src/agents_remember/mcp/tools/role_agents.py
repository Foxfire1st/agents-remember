"""Payload builders for the tools through which role agents start, message and retire role agents."""

from __future__ import annotations

from typing import Any

from agents_remember.cli.paseo_role_tools import send_role_message, start_leaf_set, start_role
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_agents import (
    RoleMessageCall,
    RoleStartCall,
    RoleStartSetCall,
)
from agents_remember.models.structural.agent import RetireChildRequest
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
        result = send_role_message(config, call)
        # A held answer carries its own facts -- why, the permission, the
        # waiting counts and the issued held id (MIK-R100 rules 4a/4b): give
        # the projector that mapping explicitly so the shaped row keeps
        # them. Withdrawal and the issued id ride inside it, unchanged; the
        # guard, the log line and the whole detail still apply downstream.
        # Completion stays inside the scoped role so the observer event
        # keeps the recipient role; reset follows, even on exception.
        held = isinstance(result, dict) and result.get("status") == "held"
        return _tool_payload("role_message", result, answer=result if held else None)
    finally:
        call_target_role.reset(token)


def retire_child_payload(
    config: McpRuntimeConfig,
    request: RetireChildRequest,
    **overrides: Any,
) -> dict[str, Any]:
    # MIK-R100 rule 8: the starter retires the receipt-tracked agent in its seat. An
    # empty id is refused by name inside the retirement.
    from agents_remember.cli.role_retire import retire_native_seat  # noqa: PLC0415

    payload = retire_native_seat(config, request, environment=overrides.get("environ"))
    return _tool_payload("retire_child", payload)


__all__ = [
    "retire_child_payload",
    "role_message_payload",
    "role_start_payload",
    "role_start_set_payload",
]
