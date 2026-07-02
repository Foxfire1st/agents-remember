"""Payload builders for dashboard terminal-session catalog tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.serving.terminal_leaf_assignment import assign_terminal_session_to_leaf

from .base import _tool_payload

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig


def attach_terminal_session_to_leaf_payload(
    config: McpRuntimeConfig,
    *,
    session_id: str,
    leaf_key: str,
) -> dict[str, Any]:
    """Move an existing hosted terminal/chat session to a durable leaf key."""

    catalog = TerminalCatalog(terminal_catalog_path(config.coordination_root))
    result = assign_terminal_session_to_leaf(
        catalog,
        session_id=session_id,
        leaf_key=leaf_key,
    )
    return _tool_payload(
        "attach_terminal_session_to_leaf",
        {
            "ok": result.status == "attached",
            "operation": "attach_terminal_session_to_leaf",
            "status": result.status,
            "session": result.session_id,
            "leafKey": result.leaf_key,
            "previousLeafKey": result.previous_leaf_key,
            "ownerSession": result.owner_session_id,
            "role": result.role,
        },
    )
