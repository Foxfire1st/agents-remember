"""Payload builder for the durable pre-closeout scheduler."""

from __future__ import annotations

from typing import Any

from agents_remember.application.closeout_queue import CloseoutQueueRequest, closeout_queue_tool
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

from .base import _tool_payload


def closeout_queue_payload(
    config: McpRuntimeConfig, request: CloseoutQueueRequest
) -> dict[str, Any]:
    return _tool_payload("closeout_queue", closeout_queue_tool(config, request))
