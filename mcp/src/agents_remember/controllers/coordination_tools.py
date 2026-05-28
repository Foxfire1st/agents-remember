"""Controllers for coordination-context MCP tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from agents_remember.kernel.coordination_context_resolver import (
    context_to_dict,
    resolve_coordination_context,
)
from agents_remember.mcp.config import McpRuntimeConfig, RepositoryScope, path_is_relative_to

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
    repo = _repo(config, repo_id)
    context = resolve_coordination_context(
        code_repository_name=repo.repo_id,
        workspace_root=config.workspace_root,
        requested_topology=_topology(topology),
        coordination_root=config.coordination_root,
        code_repository_root=repo.path,
        task_name=task_name,
        worktree_name=worktree_name,
        contract_path=_coord_path(config, contract_path, "contract_path")
        if contract_path
        else repo.contract_path,
    )
    return {
        "ok": True,
        "operation": "resolve_context",
        "repoId": repo.repo_id,
        "context": context_to_dict(context),
    }


def _repo(config: McpRuntimeConfig, repo_id: str) -> RepositoryScope:
    try:
        return config.repositories[repo_id]
    except KeyError as error:
        allowed = ", ".join(config.allowed_repo_ids) or "<none>"
        raise ValueError(
            f"repo_id {repo_id!r} is not allowed by MCP settings; allowed: {allowed}"
        ) from error


def _coord_path(config: McpRuntimeConfig, value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = config.coordination_root / path
    path = path.resolve()
    if not path_is_relative_to(path, config.coordination_root):
        raise ValueError(f"{label} must stay inside coordination_root")
    return path


def _topology(value: str | None) -> Topology | None:
    if value is None:
        return None
    if value == "internal":
        return "internal"
    if value == "external":
        return "external"
    raise ValueError("topology must be 'internal' or 'external'")
