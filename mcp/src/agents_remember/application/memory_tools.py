"""Application entry points for memory-facing MCP tools."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from agents_remember.errors import AuthorityError
from agents_remember.kernel.authority import require_repo, require_within_coordination
from agents_remember.kernel.coordination_context.models import CoordinationRequest
from agents_remember.kernel.coordination_context_resolver import (
    CoordinationContext,
    CoordinationHints,
    EnclosureSelector,
    resolve_coordination_context,
)
from agents_remember.kernel.memory_init import initialize_memory
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.kernel.route_index import build_route_indexes
from agents_remember.memory import baseline, carryover
from agents_remember.memory_quality.check import DriftCheckContext, run_memory_quality_check
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    run_drift_summary,
)
from agents_remember.memory_quality.style.citations import (
    fixer,
    migration,
    range_resolution,
    source_index,
    source_index_cache,
)
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.worktrees.git_worktree_manager import contract_context
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader
from agents_remember.worktrees.worktree_contract import load_contract


@dataclass(frozen=True)
class MemoryScope:
    """The one memory tree a memory tool reads or writes, and the code tree it measures against.

    ``repo_id`` alone resolves the configured OFFICIAL memory repo. A ``contract_path``
    resolves the leaf enclosure that contract names instead, so a session working inside a
    leaf can point these tools at its own change-set rather than at a repository it does not
    own.
    """

    repo_id: str
    code_root: Path
    onboarding_root: Path
    context: CoordinationContext
    cache_authority: source_index_cache.ManagedCacheAuthority | None = None


@dataclass(frozen=True)
class CitationOperationScope:
    """One owned memory document and the prevalidated source generation it uses.

    An expected snapshot is an explicit frozen-wave lease, not a freshness detector. The
    operator builds and validates once, freezes source, and passes the returned id to every
    independent document operation. A source edit requires a new freeze/build; Git HEAD is
    intentionally not consulted because it cannot represent dirty or untracked content.
    """

    document: str | None = None
    expected_snapshot: str | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Enforce the indivisible document-plus-generation frozen-wave contract."""
        source_index.validate_operation_scope(
            self.document,
            self.expected_snapshot,
            leased_index=False,
        )


DEFAULT_CITATION_OPERATION_SCOPE = CitationOperationScope()


def _memory_scope(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str | None,
) -> MemoryScope:
    """Resolve which memory tree the caller asked for. ``repo_id`` is always the authority."""
    repo = require_repo(config, repo_id)
    if contract_path is not None:
        return _leaf_memory_scope(config, repo, contract_path)
    if repo.memory_root is None:
        raise ValueError(f"repo_id {repo_id!r} does not have a memory root")
    onboarding_root = repo.memory_root / "onboarding"
    return MemoryScope(
        repo_id=repo.repo_id,
        code_root=repo.path,
        onboarding_root=onboarding_root,
        context=resolve_coordination_context(
            code_repository_name=repo.repo_id,
            workspace_root=config.workspace_root,
            code_repository_root=repo.path,
            request=CoordinationRequest(
                hints=CoordinationHints(
                    coordination_root=config.coordination_root, onboarding_root=onboarding_root
                ),
                selector=EnclosureSelector(contract_path=repo.contract_path),
                contract_reader=WorktreeContractReader(),
            ),
        ),
    )


def _leaf_memory_scope(
    config: McpRuntimeConfig,
    repo: RepositoryScope,
    contract_path: str,
) -> MemoryScope:
    """The enclosure's own memory tree, resolved the way the worktree verbs resolve it.

    Same three steps ``worktree_status``/``worktree_sync``/``worktree_closeout_preview`` take:
    confine the path to the coordinator root, load the contract, and read the context through
    :func:`contract_context` -- which parses the LEAF memory worktree's own coordination
    settings, so the storage rules are the leaf's too. The code root is its code worktree, the
    same substitution ``closeout`` makes before it runs these checks internally.

    Every refusal below exists instead of a fallback to the official repo.
    ``route_index_refresh`` WRITES: a silent fallback dirties a repository the caller does not
    own, and ``worktree_start`` refuses to open the next task while the source memory repo is
    dirty, so one wrong write blocks the next worktree until somebody reverts it by hand.
    """
    path = require_within_coordination(config, contract_path, "contract_path")
    contract = load_contract(path)
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
        # Also what pins the resolved context to the leaf: `build_coordination_context`
        # keeps the memory worktree's onboarding root only when it exists, and falls back
        # to the official one when it does not. Refusing here means that fallback is
        # unreachable rather than merely unlikely.
        raise ValueError(
            f"contract {path.as_posix()} names memory worktree "
            f"{contract.memory_worktree.as_posix()}, which has no onboarding tree at "
            f"{onboarding_root.as_posix()}; the worktree was removed or never opened"
        )
    return MemoryScope(
        repo_id=repo.repo_id,
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
    )


def drift_check_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    detail_limit: int = 50,
    contract_path: str | None = None,
) -> dict[str, Any]:
    scope = _memory_scope(config, repo_id=repo_id, contract_path=contract_path)
    packet = run_drift_summary(
        code_repository_root=scope.code_root,
        context=scope.context,
        detail_limit=detail_limit,
    )
    return {
        "ok": packet.get("status") == "checked",
        "operation": "drift_check",
        "onboardingRoot": scope.onboarding_root.as_posix(),
        **packet,
    }


def memory_quality_check_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    checks: list[str] | None = None,
    detail_limit: int = 50,
    contract_path: str | None = None,
) -> dict[str, Any]:
    scope = _memory_scope(config, repo_id=repo_id, contract_path=contract_path)
    payload = run_memory_quality_check(
        scope.onboarding_root,
        checks=checks,
        drift_context=DriftCheckContext(
            code_repository_root=scope.code_root,
            context=scope.context,
            detail_limit=detail_limit,
        ),
    )
    return {
        "operation": "memory_quality_check",
        "repoId": scope.repo_id,
        "onboardingRoot": scope.onboarding_root.as_posix(),
        **payload,
    }


def _refuse_official_memory(repo: RepositoryScope, scope: MemoryScope) -> None:
    """The write guard: a citation rewrite happens in a LEAF or it does not happen.

    ``contract_path`` is mandatory rather than optional here, which is the whole guard --
    there is no argument list that names the official memory repo. This is the second half,
    for a contract that names the official repo AS its memory worktree.

    The defect this exists to avoid inheriting is a measured one: ``route_index_refresh``
    resolved the official repo from ``repo_id`` alone and generated indexes into a
    repository the session did not own, which left the leaf stale and made the official
    repo dirty -- and ``worktree_start`` refuses to open the next task while it is. A
    citation fix rewrites ranges across thousands of rows, with no closeout reviewing the
    result. It refuses; it never falls back.
    """
    official = repo.memory_root.resolve() if repo.memory_root is not None else None
    target = scope.onboarding_root.resolve()
    if official is not None and (official == target or official in target.parents):
        raise AuthorityError(
            f"citation_fix refuses to write into the OFFICIAL memory repo "
            f"({official.as_posix()}); it writes only into a leaf's memory worktree, "
            f"resolved from that leaf's enclosure contract. Start or attach a worktree and "
            f"pass its contract path"
        )


def citation_check_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str,
    operation_scope: CitationOperationScope = DEFAULT_CITATION_OPERATION_SCOPE,
) -> dict[str, Any]:
    """Report one leaf's citations, optionally scoped to a single document.

    Separate from ``memory_quality_check_tool`` because ``document`` would be a LIE there:
    the other style checks do not honour it, so a scoped call would report the citations of
    one document beside three tree-wide results under one ``ok``. Scope belongs where every
    check under it is scoped.

    Read-only, so unlike the fix and migrate tools this does not refuse the official memory
    repo -- reading it is legitimate; only writing it is not.
    """
    operation_scope.validate()
    scope = _memory_scope(config, repo_id=repo_id, contract_path=contract_path)
    trees = Trees(
        code_root=scope.code_root,
        memory_root=scope.onboarding_root.parent,
        cache_authority=scope.cache_authority,
    )
    return {
        "repoId": scope.repo_id,
        **range_resolution.check_onboarding_root(
            scope.onboarding_root,
            trees,
            only=operation_scope.document,
            expected_snapshot=operation_scope.expected_snapshot,
        ),
    }


def citation_source_index_build_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str,
) -> dict[str, Any]:
    """Build or validate the reusable source snapshot selected by a leaf contract."""
    scope = _memory_scope(config, repo_id=repo_id, contract_path=contract_path)
    return {
        "repoId": scope.repo_id,
        **source_index.build_repository_index(
            Trees(
                code_root=scope.code_root,
                memory_root=scope.onboarding_root.parent,
                cache_authority=scope.cache_authority,
            )
        ),
    }


def citation_fix_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str,
    dry_run: bool = False,
    operation_scope: CitationOperationScope = DEFAULT_CITATION_OPERATION_SCOPE,
) -> dict[str, Any]:
    """Regenerate ranges in one leaf, optionally against an explicit frozen generation."""
    operation_scope.validate()
    scope = _memory_scope(config, repo_id=repo_id, contract_path=contract_path)
    trees = Trees(
        code_root=scope.code_root,
        memory_root=scope.onboarding_root.parent,
        cache_authority=scope.cache_authority,
    )
    _refuse_official_memory(require_repo(config, repo_id), scope)
    return {
        "repoId": scope.repo_id,
        **fixer.fix_onboarding_root(
            scope.onboarding_root,
            trees,
            dry_run=dry_run,
            only=operation_scope.document,
            expected_snapshot=operation_scope.expected_snapshot,
        ),
    }


def citation_migrate_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str,
    dry_run: bool = False,
    operation_scope: CitationOperationScope = DEFAULT_CITATION_OPERATION_SCOPE,
) -> dict[str, Any]:
    """Convert a memory tree to the anchored citation format, inside one leaf's worktree.

    Same guard as ``citation_fix_tool`` and for a stronger reason: this rewrites the SHAPE of
    every evidence table in the tree, which is not a diff any closeout could review if it
    landed in a repository the session does not own.
    """
    operation_scope.validate()
    scope = _memory_scope(config, repo_id=repo_id, contract_path=contract_path)
    trees = Trees(
        code_root=scope.code_root,
        memory_root=scope.onboarding_root.parent,
        cache_authority=scope.cache_authority,
    )
    _refuse_official_memory(require_repo(config, repo_id), scope)
    return {
        "repoId": scope.repo_id,
        **migration.migrate_onboarding_root(
            scope.onboarding_root,
            trees,
            dry_run=dry_run,
            only=operation_scope.document,
            expected_snapshot=operation_scope.expected_snapshot,
        ),
    }


def route_index_refresh_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    dry_run: bool = False,
    contract_path: str | None = None,
) -> dict[str, Any]:
    scope = _memory_scope(config, repo_id=repo_id, contract_path=contract_path)
    result = build_route_indexes(
        code_root=scope.code_root,
        onboarding_root=scope.onboarding_root,
        repository=scope.context.code_repository_name,
        storage=scope.context.storage,
        dry_run=dry_run,
    )
    return {
        "ok": True,
        "operation": "route_index_refresh",
        "repoId": scope.repo_id,
        "onboardingRoot": scope.onboarding_root.as_posix(),
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
