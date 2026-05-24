"""Controller for the versioned Agents Remember context packet."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_remember.kernel.coordination_context_resolver import (
    context_to_dict,
    resolve_coordination_context,
)
from agents_remember.kernel.git_facts import git_facts_to_packet, read_git_facts
from agents_remember.mcp.config import McpRuntimeConfig, RepositoryScope
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    not_checked,
    run_drift_summary,
)
from agents_remember.providers.status import provider_status_packet
from agents_remember.worktrees.status import worktree_status_packet

CONTEXT_PACKET_VERSION = 1


class ContextPacketError(ValueError):
    """Raised when the context packet request violates MCP authority settings."""


@dataclass(frozen=True)
class ContextPacketRequest:
    repo_id: str
    include_providers: bool = True
    include_drift: bool = False
    provider_detail_limit: int = 20
    drift_detail_limit: int = 10


def build_context_packet(
    config: McpRuntimeConfig,
    request: ContextPacketRequest,
) -> dict[str, Any]:
    repo_scope = _repo_scope(config, request.repo_id)
    context = resolve_coordination_context(
        code_repository_name=repo_scope.repo_id,
        workspace_root=config.workspace_root,
        coordination_root=config.coordination_root,
        code_repository_root=repo_scope.path,
        onboarding_root=(repo_scope.memory_root / "onboarding") if repo_scope.memory_root else None,
        contract_path=repo_scope.contract_path,
    )
    context_dict = context_to_dict(context)
    git_facts = read_git_facts(repo_scope.repo_id, repo_scope.path)

    return {
        "ok": git_facts.state in {"available", "detached"},
        "operation": "context_packet",
        "contextPacketVersion": CONTEXT_PACKET_VERSION,
        "repoId": repo_scope.repo_id,
        "limits": {
            "providerItems": request.provider_detail_limit,
            "driftItems": request.drift_detail_limit,
        },
        "repo": git_facts_to_packet(git_facts),
        "coordination": {
            "root": context_dict["coordination_root"],
            "taskRoot": context_dict["task_root"],
            "settingsPath": context_dict["settings_path"],
            "pathSettingsPath": context_dict["path_settings_path"],
            "systemRoot": context_dict["system_root"],
            "toolsPath": context_dict["tools_path"],
        },
        "memory": {
            "mode": context_dict["memory_mode"],
            "root": context_dict["memory_root"],
            "onboardingRoot": context_dict["onboarding_root"],
            "ledgerPath": context_dict["ledger_path"],
            "settingsPath": context_dict["settings_path"],
        },
        "storage": context_dict["storage"],
        "pathRules": context_dict["pathRules"],
        "crossRepo": context_dict["crossRepo"],
        "worktree": worktree_status_packet(context.contract_path),
        "providers": provider_status_packet(
            config,
            include_providers=request.include_providers,
            detail_limit=request.provider_detail_limit,
        ),
        "drift": _drift_packet(request, context, repo_scope),
    }


def _repo_scope(config: McpRuntimeConfig, repo_id: str) -> RepositoryScope:
    if repo_id not in config.repositories:
        allowed = ", ".join(config.allowed_repo_ids) or "<none>"
        raise ContextPacketError(
            f"repoId {repo_id!r} is not allowed by MCP settings; allowed: {allowed}"
        )
    return config.repositories[repo_id]


def _drift_packet(
    request: ContextPacketRequest,
    context: Any,
    repo_scope: RepositoryScope,
) -> dict[str, Any]:
    if not request.include_drift:
        return not_checked()
    return run_drift_summary(
        code_repository_root=repo_scope.path,
        context=context,
        detail_limit=request.drift_detail_limit,
    )
