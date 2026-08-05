"""MCP response adapters for operator-inbox application operations."""

from __future__ import annotations

from typing import Any

from agents_remember.application.operator_inbox_tools import (
    operator_inbox_consume_tool,
    operator_inbox_poll_tool,
    operator_inbox_post_tool,
    post_operator_inbox,
)
from agents_remember.models.application_requests import OperatorInboxPostRequest

from ..config import McpRuntimeConfig
from .base import _tool_payload


def operator_inbox_post_payload(
    config: McpRuntimeConfig,
    *,
    address: Any,
    message: Any,
    poster: Any,
    delivery: Any = None,
) -> dict[str, Any]:
    return _tool_payload(
        "operator_inbox_post",
        operator_inbox_post_tool(
            config,
            address=address,
            message=message,
            poster=poster,
            delivery=delivery,
        ),
    )


def registered_operator_inbox_post_payload(
    config: McpRuntimeConfig,
    request: OperatorInboxPostRequest,
) -> dict[str, Any]:
    """Complete the MCP post after application-owned request composition."""
    return _tool_payload(
        "operator_inbox_post",
        post_operator_inbox(config, request),
    )


def operator_inbox_poll_payload(
    config: McpRuntimeConfig,
    *,
    lifecycle_id: str | None,
    agent_id: str | None,
    recipient_role: Any = None,
) -> dict[str, Any]:
    return _tool_payload(
        "operator_inbox_poll",
        operator_inbox_poll_tool(
            config,
            lifecycle_id=lifecycle_id,
            agent_id=agent_id,
            recipient_role=recipient_role,
        ),
    )


def operator_inbox_consume_payload(
    config: McpRuntimeConfig,
    *,
    entry_id: str,
    consumed_by: str,
    consumed_via: Any,
) -> dict[str, Any]:
    return _tool_payload(
        "operator_inbox_consume",
        operator_inbox_consume_tool(
            config,
            entry_id=entry_id,
            consumed_by=consumed_by,
            consumed_via=consumed_via,
        ),
    )
