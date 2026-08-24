"""Payload builder for contract-owned closeout-door source controls."""

from __future__ import annotations

from typing import Any

from agents_remember.application.closeout_door import CloseoutDoorRequest, closeout_door_tool
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

from .base import _tool_payload


def closeout_door_payload(
    config: McpRuntimeConfig,
    request: CloseoutDoorRequest,
) -> dict[str, Any]:
    return _tool_payload("closeout_door", closeout_door_tool(config, request))
