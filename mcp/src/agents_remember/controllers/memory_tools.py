"""Controllers for memory-facing MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_remember.controllers._guards import require_repo, require_within_coordination
from agents_remember.kernel.coordination_context_resolver import (
    CoordinationHints,
    EnclosureSelector,
    resolve_coordination_context,
)
from agents_remember.kernel.memory_init import initialize_memory
from agents_remember.kernel.route_index import build_route_indexes
from agents_remember.mcp.config import McpRuntimeConfig, RepositoryScope
from agents_remember.memory import baseline, carryover
from agents_remember.memory_quality.check import DriftCheckContext, run_memory_quality_check
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    run_drift_summary,
)


def drift_check_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    detail_limit: int = 50,
) -> dict[str, Any]:
    repo = require_repo(config, repo_id)
    context = resolve_coordination_context(
        code_repository_name=repo.repo_id,
        workspace_root=config.workspace_root,
        code_repository_root=repo.path,
        hints=CoordinationHints(
            coordination_root=config.coordination_root,
            onboarding_root=(repo.memory_root / "onboarding") if repo.memory_root else None,
        ),
        selector=EnclosureSelector(contract_path=repo.contract_path),
    )
    packet = run_drift_summary(
        code_repository_root=repo.path,
        context=context,
        detail_limit=detail_limit,
    )
    return {"ok": packet.get("status") == "checked", "operation": "drift_check", **packet}


def memory_quality_check_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    checks: list[str] | None = None,
    detail_limit: int = 50,
) -> dict[str, Any]:
    repo = require_repo(config, repo_id)
    if repo.memory_root is None:
        raise ValueError(f"repo_id {repo_id!r} does not have a memory root")
    onboarding_root = repo.memory_root / "onboarding"
    context = resolve_coordination_context(
        code_repository_name=repo.repo_id,
        workspace_root=config.workspace_root,
        code_repository_root=repo.path,
        hints=CoordinationHints(
            coordination_root=config.coordination_root, onboarding_root=onboarding_root
        ),
        selector=EnclosureSelector(contract_path=repo.contract_path),
    )
    payload = run_memory_quality_check(
        onboarding_root,
        checks=checks,
        drift_context=DriftCheckContext(
            code_repository_root=repo.path,
            context=context,
            detail_limit=detail_limit,
        ),
    )
    return {
        "operation": "memory_quality_check",
        "repoId": repo.repo_id,
        "onboardingRoot": onboarding_root.as_posix(),
        **payload,
    }


def route_index_refresh_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    repo = require_repo(config, repo_id)
    if repo.memory_root is None:
        raise ValueError(f"repo_id {repo_id!r} does not have a memory root")
    onboarding_root = repo.memory_root / "onboarding"
    context = resolve_coordination_context(
        code_repository_name=repo.repo_id,
        workspace_root=config.workspace_root,
        code_repository_root=repo.path,
        hints=CoordinationHints(
            coordination_root=config.coordination_root, onboarding_root=onboarding_root
        ),
        selector=EnclosureSelector(contract_path=repo.contract_path),
    )
    result = build_route_indexes(
        code_root=repo.path,
        onboarding_root=onboarding_root,
        repository=context.code_repository_name,
        storage=context.storage,
        dry_run=dry_run,
    )
    return {
        "ok": True,
        "operation": "route_index_refresh",
        "repoId": repo.repo_id,
        "dryRun": dry_run,
        **result.to_dict(),
    }


def memory_init_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    dry_run: bool = False,
    initialize_git: bool = True,
) -> dict[str, Any]:
    return initialize_memory(
        config,
        repo_id=repo_id,
        dry_run=dry_run,
        initialize_git=initialize_git,
    )


@dataclass(frozen=True)
class MemoryBranches:
    """The external-memory repo's branch pair: the line adoption starts from and the line it
    works on. Either side omitted falls back to the memory repo's configured default."""

    source_branch: str | None = None
    work_branch: str | None = None


DEFAULT_MEMORY_BRANCHES = MemoryBranches()
"""The memory repo's own configured branches -- neither side overridden by the caller."""


@dataclass(frozen=True)
class CarryoverSelection:
    """Which branch memory is carried onto which landed code.

    ``source_memory`` is the branch's memory repo; the three refs bound the landed
    range the carryover is allowed to trust -- the official code tip memory already
    describes, the source branch tip its memory describes, and the base the branch was
    cut from. ``replace_existing`` decides whether landed entries may overwrite
    official ones already present.
    """

    repo_id: str
    source_memory: str
    official_code_ref: str
    source_code_ref: str
    old_base: str
    replace_existing: bool = False


@dataclass(frozen=True)
class CarryoverCommitMessages:
    """The two commits an applied carryover writes: one in the official memory repo for the
    carried onboarding, one in the ledger that records the carryover itself."""

    memory: str = "Carry over landed branch memory"
    ledger: str = "Record branch memory carryover"


DEFAULT_CARRYOVER_MESSAGES = CarryoverCommitMessages()
"""The standard carryover commit subjects, used when the caller supplies none."""


def memory_baseline_status_tool(config: McpRuntimeConfig, *, repo_id: str) -> dict[str, Any]:
    repo = require_repo(config, repo_id)
    payload = baseline.baseline_status(_baseline_request(config, repo))
    return {
        "ok": payload.get("state") != "blocked-drift",
        "operation": "memory_baseline_status",
        **payload,
    }


def memory_baseline_adopt_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    accept_drift: bool = False,
    branches: MemoryBranches = DEFAULT_MEMORY_BRANCHES,
    dry_run: bool = False,
) -> dict[str, Any]:
    repo = require_repo(config, repo_id)
    returncode, payload = baseline.baseline_adopt(
        _baseline_request(config, repo),
        accept_drift=accept_drift,
        source_branch=branches.source_branch,
        work_branch=branches.work_branch,
        dry_run=dry_run,
    )
    return {"ok": returncode == 0, "operation": "memory_baseline_adopt", **payload}


def memory_carryover_plan_tool(
    config: McpRuntimeConfig,
    selection: CarryoverSelection,
) -> dict[str, Any]:
    payload = carryover.build_plan_for_request(_carryover_request(config, selection))
    return {"ok": True, "operation": "memory_carryover_plan", **payload}


def memory_carryover_apply_tool(
    config: McpRuntimeConfig,
    selection: CarryoverSelection,
    *,
    intent_note: str,
    include_review_required: list[str] | None = None,
    messages: CarryoverCommitMessages = DEFAULT_CARRYOVER_MESSAGES,
) -> dict[str, Any]:
    payload = carryover.apply_carryover_for_request(
        _carryover_request(config, selection),
        intent_note=intent_note,
        include_review_required=include_review_required,
        memory_commit_message=messages.memory,
        ledger_commit_message=messages.ledger,
    )
    return {"ok": True, "operation": "memory_carryover_apply", **payload}


def _baseline_request(config: McpRuntimeConfig, repo: RepositoryScope) -> baseline.BaselineRequest:
    return baseline.BaselineRequest(
        code_repository_name=repo.repo_id,
        workspace_root=config.workspace_root,
        code_repository_root=repo.path,
        coordination_root=config.coordination_root,
        topology="external",
    )


def _carryover_request(
    config: McpRuntimeConfig,
    selection: CarryoverSelection,
) -> carryover.CarryoverRequest:
    repo = require_repo(config, selection.repo_id)
    if repo.memory_root is None:
        raise ValueError(f"repo_id {selection.repo_id!r} does not have a memory root")
    source_memory_path = require_within_coordination(
        config, selection.source_memory, "source_memory"
    )
    return carryover.CarryoverRequest(
        code_repository_root=repo.path,
        official_code_ref=selection.official_code_ref,
        source_code_ref=selection.source_code_ref,
        old_base=selection.old_base,
        official_memory=repo.memory_root,
        source_memory=source_memory_path,
        code_repository_name=repo.repo_id,
        replace_existing=selection.replace_existing,
    )
