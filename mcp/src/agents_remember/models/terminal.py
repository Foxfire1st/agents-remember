"""Response models for dashboard terminal-session MCP tools."""

from __future__ import annotations

from typing import Literal

from agents_remember.models.base import ToolResponse

LeafAssignmentStatus = Literal["attached", "leaf-taken", "unknown-session"]


class AttachTerminalSessionToLeafResponse(ToolResponse):
    """``attach_terminal_session_to_leaf``: move one hosted session to a leaf."""

    operation: Literal["attach_terminal_session_to_leaf"] = "attach_terminal_session_to_leaf"
    status: LeafAssignmentStatus
    session: str
    leafKey: str
    previousLeafKey: str | None = None
    ownerSession: str | None = None
    role: Literal["chat", "terminal"] | None = None
