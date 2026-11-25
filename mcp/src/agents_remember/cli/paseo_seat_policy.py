"""Paseo's per-tool approvals for the tool server a role seat starts with."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, TypedDict

from agents_remember.models.tools.role_tool_scope import role_tool_roster


class McpToolGrant(TypedDict):
    kind: Literal["mcp"]
    server: str
    tool: str


class ToolPolicy(TypedDict):
    preapproved: list[McpToolGrant]


def preapproval_policy(
    provider: str,
    server: str,
    explainer_tools: Sequence[str] = (),
    *,
    role: str | None = None,
) -> ToolPolicy | None:
    """Grant the seat's ordered tool roster on Codex and the identity and explainer tools on Claude.

    ``role`` names the AR role of a fresh ordinary seat; its grant is exactly the ordered allowlist
    the same binding makes the task server advertise. ``None`` keeps the full public roster, which
    is what an explainer seat grants beside its own four tools.
    """

    if provider == "codex":
        # Paseo uses the grant as Codex's enabled-tools list and keeps it across resumes.
        tools = role_tool_roster(role)
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
