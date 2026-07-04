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


SpawnAgentSessionStatus = Literal[
    "spawned",
    "leaf-taken",
    "harness-unknown",
    "harness-not-detected",
    "bad-kind",
]


class SpawnAgentSessionResponse(ToolResponse):
    """``spawn_agent_session``: spawn a role-configured, leaf-attached, context-primed hosted session.

    Composes the existing session primitives (opener + leaf claim + echo-confirmed paste + optional
    submit). ``ok`` is true only for ``spawned``; ``leaf-taken`` surfaces the server-arbitrated
    refusal (the tool never overrides it), and the harness/kind statuses report a validation refusal
    before anything is spawned.
    """

    operation: Literal["spawn_agent_session"] = "spawn_agent_session"
    status: SpawnAgentSessionStatus
    session: str
    harness: str | None = None
    kind: Literal["harness", "terminal"] | None = None
    leafKey: str | None = None
    label: str | None = None
    cwd: str | None = None
    tmuxName: str | None = None
    # Spawned-by provenance recorded on the catalog row (the dashboard orchestration-tree seam).
    spawnedBySession: str | None = None
    spawnedByLifecycle: str | None = None
    # Set on ``leaf-taken``: the running same-role session that already owns the leaf.
    ownerSession: str | None = None
    # Context-packet delivery outcome (echo-confirmed paste; submit only when requested).
    contextDelivered: bool | None = None
    submitted: bool | None = None
    detail: str | None = None
