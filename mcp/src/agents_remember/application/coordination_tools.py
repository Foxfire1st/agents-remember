"""Application entry points for coordination-context MCP tools."""

from __future__ import annotations

from typing import Any

from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.kernel.authority import require_repo, require_within_coordination
from agents_remember.kernel.coordination_context.models import CoordinationRequest
from agents_remember.kernel.coordination_context_resolver import (
    CoordinationHints,
    EnclosureSelector,
    context_to_dict,
    resolve_coordination_context,
)
from agents_remember.kernel.memory_mode import Topology, require_supported_topology
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader


def resolve_context_tool(
    config: McpRuntimeConfig,
    task: TaskRef,
    *,
    worktree_name: str | None = None,
    topology: str | None = None,
) -> dict[str, Any]:
    repo = require_repo(config, task.repo_id)
    context = resolve_coordination_context(
        code_repository_name=repo.repo_id,
        workspace_root=config.workspace_root,
        code_repository_root=repo.path,
        request=CoordinationRequest(
            hints=CoordinationHints(
                topology=_topology(topology), coordination_root=config.coordination_root
            ),
            selector=EnclosureSelector(
                contract_path=require_within_coordination(
                    config, task.contract_path, "contract_path"
                )
                if task.contract_path
                else repo.contract_path,
                task_name=task.task_name,
                parent_task=task.parent_task,
                leaf_id=task.leaf_id,
                worktree_name=worktree_name,
            ),
            contract_reader=WorktreeContractReader(),
        ),
    )
    return {
        "ok": True,
        "operation": "resolve_context",
        "repoId": repo.repo_id,
        "context": context_to_dict(context),
    }


def _topology(value: str | None) -> Topology | None:
    """Narrow a caller-supplied topology onto the supported set, or refuse.

    Every narrowing goes through the shared vocabulary helper rather than an inline comparison,
    so the public tool reports the same typed status and the same supported set as every other
    surface -- and, equally, so an unknown token stays a plain invalid-argument refusal instead
    of being reported as a removal that never happened.
    """
    if value is None:
        return None
    return require_supported_topology(value)
