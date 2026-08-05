"""MCP response adapter for exact hosted-session readiness."""

from __future__ import annotations

from typing import Any

from agents_remember.application.hosted_readiness import hosted_session_readiness_tool

from ..config import McpRuntimeConfig
from .base import _tool_payload


def hosted_session_readiness_payload(
    config: McpRuntimeConfig,
    *,
    session_id: str,
    wait_seconds: float = 0.0,
    catalog: Any = None,
    host: Any = None,
) -> dict[str, Any]:
    return _tool_payload(
        "hosted_session_readiness",
        hosted_session_readiness_tool(
            config,
            session_id=session_id,
            wait_seconds=wait_seconds,
            catalog=catalog,
            host=host,
        ),
    )
