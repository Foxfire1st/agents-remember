"""Payload adapter for the contract-owned curator-coherence authority."""

from __future__ import annotations

from typing import Any

from agents_remember.application.curator_coherence import curator_coherence_tool
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.lifecycles.curator_coherence import CuratorCoherenceRequest

from .base import _tool_payload


def curator_coherence_payload(
    config: McpRuntimeConfig,
    request: CuratorCoherenceRequest,
) -> dict[str, Any]:
    return _tool_payload("curator_coherence", curator_coherence_tool(config, request))
