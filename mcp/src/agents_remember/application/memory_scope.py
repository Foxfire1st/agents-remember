"""Resolve the one canonical memory/code scope used by memory-facing tools."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from agents_remember.errors import AuthorityError
from agents_remember.kernel.authority import require_repo, require_within_coordination
from agents_remember.kernel.coordination_context.models import CoordinationRequest
from agents_remember.kernel.coordination_context_resolver import (
    CoordinationContext,
    CoordinationHints,
    EnclosureSelector,
    resolve_coordination_context,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.memory_quality.curator_checklist import report_path_for
from agents_remember.memory_quality.style.citations import source_index_cache
from agents_remember.worktrees.git_worktree_manager import contract_context
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader
from agents_remember.worktrees.worktree_contract import load_contract


@dataclass(frozen=True)
class MemoryScopeIdentity:
    """Canonical authority and resolved trees that distinguish one quality scope."""

    authority: Literal["official", "leaf"]
    authority_path: str
    code_root: str
    onboarding_root: str
    unstamped_code_commit: str | None = None


@dataclass(frozen=True)
class MemoryScope:
    """One resolved memory tree, its measured code tree, and stable request identity."""

    repo_id: str
    identity: MemoryScopeIdentity
    code_root: Path
    onboarding_root: Path
    context: CoordinationContext
    cache_authority: source_index_cache.ManagedCacheAuthority | None = None
    unstamped_code_commit: str | None = None
    curator_report_path: Path | None = None


def resolve_memory_scope(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str | None,
) -> MemoryScope:
    """Resolve official or enclosure-local memory through configured authority."""

    repo = require_repo(config, repo_id)
    if contract_path is not None:
        return resolve_leaf_memory_scope(config, repo, contract_path)
    if repo.memory_root is None:
        raise ValueError(f"repo_id {repo_id!r} does not have a memory root")
    onboarding_root = repo.memory_root / "onboarding"
    return MemoryScope(
        repo_id=repo.repo_id,
        identity=MemoryScopeIdentity(
            authority="official",
            authority_path=(repo.contract_path or onboarding_root).resolve().as_posix(),
            code_root=repo.path.resolve().as_posix(),
            onboarding_root=onboarding_root.resolve().as_posix(),
        ),
        code_root=repo.path,
        onboarding_root=onboarding_root,
        context=resolve_coordination_context(
            code_repository_name=repo.repo_id,
            workspace_root=config.workspace_root,
            code_repository_root=repo.path,
            request=CoordinationRequest(
                hints=CoordinationHints(
                    coordination_root=config.coordination_root,
                    onboarding_root=onboarding_root,
                ),
                selector=EnclosureSelector(contract_path=repo.contract_path),
                contract_reader=WorktreeContractReader(),
            ),
        ),
    )


def resolve_leaf_memory_scope(
    config: McpRuntimeConfig,
    repo: RepositoryScope,
    contract_path: str,
) -> MemoryScope:
    """Resolve one leaf enclosure without falling back to official memory."""

    path = require_within_coordination(config, contract_path, "contract_path")
    contract = load_contract(path)
    if contract.kind != "leaf":
        raise AuthorityError(
            f"contract_path must name a leaf worktree contract, not {contract.kind!r}"
        )
    if contract.repo_name != repo.repo_id:
        raise AuthorityError(
            f"contract_path names repo {contract.repo_name!r} but repo_id is {repo.repo_id!r}; "
            f"pass the repo_id this contract was started for ({path.as_posix()})"
        )
    if contract.memory_worktree is None:
        raise ValueError(
            f"contract {path.as_posix()} carries no memory worktree (memory_mode is "
            f"{contract.memory_mode!r}), so it has no memory tree of its own to check; drop "
            "contract_path to check the official memory repo deliberately"
        )
    onboarding_root = contract.memory_worktree / "onboarding"
    if not onboarding_root.is_dir():
        raise ValueError(
            f"contract {path.as_posix()} names memory worktree "
            f"{contract.memory_worktree.as_posix()}, which has no onboarding tree at "
            f"{onboarding_root.as_posix()}; the worktree was removed or never opened"
        )
    return MemoryScope(
        repo_id=repo.repo_id,
        identity=MemoryScopeIdentity(
            authority="leaf",
            authority_path=path.as_posix(),
            code_root=contract.code_worktree.resolve().as_posix(),
            onboarding_root=onboarding_root.resolve().as_posix(),
            unstamped_code_commit=contract.code_base_commit,
        ),
        code_root=contract.code_worktree,
        onboarding_root=onboarding_root,
        context=replace(contract_context(contract), code_repository_root=contract.code_worktree),
        cache_authority=source_index_cache.managed_cache_authority(
            coordination_root=contract.coordination_root,
            contract_path=contract.contract_path,
            code_root=contract.code_worktree,
            memory_root=contract.memory_worktree,
            lifecycle_id=contract.lifecycle_id,
        ),
        unstamped_code_commit=contract.code_base_commit,
        curator_report_path=report_path_for(contract.worktree_group),
    )
