"""Controllers for coordination-context MCP tools."""

from __future__ import annotations

from typing import Any, Literal

from agents_remember.controllers._guards import require_repo, require_within_coordination
from agents_remember.kernel.coordination_context_resolver import (
    context_to_dict,
    resolve_coordination_context,
)
from agents_remember.mcp.config import McpRuntimeConfig

Topology = Literal["internal", "external"]


def resolve_context_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    task_name: str | None = None,
    contract_path: str | None = None,
    worktree_name: str | None = None,
    topology: str | None = None,
) -> dict[str, Any]:
    repo = require_repo(config, repo_id)
    context = resolve_coordination_context(
        code_repository_name=repo.repo_id,
        workspace_root=config.workspace_root,
        requested_topology=_topology(topology),
        coordination_root=config.coordination_root,
        code_repository_root=repo.path,
        task_name=task_name,
        worktree_name=worktree_name,
        contract_path=require_within_coordination(config, contract_path, "contract_path")
        if contract_path
        else repo.contract_path,
    )
    return {
        "ok": True,
        "operation": "resolve_context",
        "repoId": repo.repo_id,
        "context": context_to_dict(context),
    }


def _topology(value: str | None) -> Topology | None:
    if value is None:
        return None
    if value == "internal":
        return "internal"
    if value == "external":
        return "external"
    raise ValueError("topology must be 'internal' or 'external'")
