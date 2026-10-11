"""MCP response adapters for lifecycle application operations."""

from __future__ import annotations

from typing import Any

from agents_remember.application.lifecycle.lifecycle_tools import lifecycle_block_tool

from .base import _tool_payload


def lifecycle_block_payload(
    *,
    kind: str | None = None,
    prompt: str | None = None,
    options: list[str] | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "lifecycle_block",
        lifecycle_block_tool(kind=kind, prompt=prompt, options=options),
    )
