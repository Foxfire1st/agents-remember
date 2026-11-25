"""Paseo's per-tool approvals for the tool server a role seat starts with."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, TypedDict

from agents_remember.models.tools.public_roster import PUBLIC_TOOLS


class McpToolGrant(TypedDict):
    kind: Literal["mcp"]
    server: str
    tool: str


class ToolPolicy(TypedDict):
    preapproved: list[McpToolGrant]


def preapproval_policy(
    provider: str, server: str, explainer_tools: Sequence[str] = ()
) -> ToolPolicy | None:
    """Grant the full starting roster on Codex and the identity and explainer tools on Claude."""

    if provider == "codex":
        # Paseo uses the grant as Codex's enabled-tools list and keeps it across resumes.
        tools = PUBLIC_TOOLS
    elif provider == "claude":
        tools = ("server_info",)
    else:
        return None
    return {
        "preapproved": [
            {"kind": "mcp", "server": server, "tool": name} for name in (*tools, *explainer_tools)
        ]
    }


def preapproval_detail(provider: str) -> str:
    """Describe an applied task-server grant in the launch receipt."""

    if provider == "codex":
        return (
            "Codex keeps the starting build's task-server grant when the seat resumes or reloads, "
            "and tools added by later builds require replacing the seat."
        )
    return "Claude's task-server approval covers server_info for this session."
