"""Payload builder for the direct landing operation."""

from __future__ import annotations

from typing import Any

from agents_remember.application.lifecycle.direct_landing import (
    DirectLandingRequest,
    direct_landing_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

from .base import _tool_payload


def direct_landing_payload(
    config: McpRuntimeConfig,
    request: DirectLandingRequest,
) -> dict[str, Any]:
    return _tool_payload("direct_landing", direct_landing_tool(config, request))
