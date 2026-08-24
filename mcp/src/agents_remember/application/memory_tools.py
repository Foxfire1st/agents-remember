"""Application entry points for memory-facing MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_remember.application.memory_scope import (
    MemoryScope,
)
from agents_remember.application.memory_scope import (
    resolve_memory_scope as _memory_scope,
)
from agents_remember.errors import AuthorityError
from agents_remember.kernel.authority import require_repo, require_within_coordination
from agents_remember.kernel.memory_init import initialize_memory
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.kernel.route_index import build_route_indexes
from agents_remember.memory import baseline, carryover
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    run_drift_summary,
)
from agents_remember.memory_quality.style.citations import (
    fixer,
    migration,
    range_resolution,
    source_index,
)
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.worktrees.integration.integration_branch_authority import (
    require_ordinary_worktree,
)
from agents_remember.worktrees.worktree_contract import load_contract


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
            f"memory-tree mutation refuses to write into the OFFICIAL memory repo "
            f"({official.as_posix()}); it writes only into a leaf's memory worktree, "
            f"resolved from that leaf's enclosure contract. Start or attach a worktree and "
            f"pass its contract path"
        )


def _leaf_memory_writer_scope(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str,
    operation: str,
) -> MemoryScope:
    path = require_within_coordination(config, contract_path, "contract_path")
    contract = load_contract(path)
    scope = _memory_scope(config, repo_id=repo_id, contract_path=contract_path)
    _refuse_official_memory(require_repo(config, repo_id), scope)
    require_ordinary_worktree(contract, operation=operation)
    return scope


def citation_check_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str,
    operation_scope: CitationOperationScope = DEFAULT_CITATION_OPERATION_SCOPE,
) -> dict[str, Any]:
    """Report one leaf's citations, optionally scoped to a single document.

    Separate from the memory-quality controller because ``document`` would be a LIE there:
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
    scope = _leaf_memory_writer_scope(
        config,
        repo_id=repo_id,
        contract_path=contract_path,
        operation="citation_fix",
    )
    trees = Trees(
        code_root=scope.code_root,
        memory_root=scope.onboarding_root.parent,
        cache_authority=scope.cache_authority,
    )
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
    scope = _leaf_memory_writer_scope(
        config,
        repo_id=repo_id,
        contract_path=contract_path,
        operation="citation_migrate",
    )
    trees = Trees(
        code_root=scope.code_root,
        memory_root=scope.onboarding_root.parent,
        cache_authority=scope.cache_authority,
    )
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
    if not dry_run and contract_path is None:
        raise AuthorityError(
            "route_index_refresh apply requires a leaf contract_path; the configured "
            "official memory checkout is read-only outside a journaled landing"
        )
    scope = (
        _leaf_memory_writer_scope(
            config,
            repo_id=repo_id,
            contract_path=contract_path,
            operation="route_index_refresh",
        )
        if not dry_run and contract_path is not None
        else _memory_scope(config, repo_id=repo_id, contract_path=contract_path)
    )
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
    range the carryover is allowed to trust -- the landed code tip, the source branch tip
    its memory describes, and the base the branch was cut from. ``contract_path`` names the
    open recovery leaf that owns the target memory worktree. ``replace_existing`` decides
    whether landed entries may overwrite different recovery-leaf entries.
    """

    repo_id: str
    contract_path: str
    source_memory: str
    official_code_ref: str
    source_code_ref: str
    old_base: str
    replace_existing: bool = False


@dataclass(frozen=True)
class CarryoverCommitMessages:
    """The recovery-leaf onboarding commit and its code-to-memory ledger commit."""

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
    request = _carryover_request(config, selection)
    carryover._require_carryover_authority(request, config)
    payload = carryover.build_plan_for_request(request)
    return {"ok": True, "operation": "memory_carryover_plan", **payload}


def memory_carryover_apply_tool(
    config: McpRuntimeConfig,
    selection: CarryoverSelection,
    *,
    intent_note: str,
    include_review_required: list[str] | None = None,
    messages: CarryoverCommitMessages = DEFAULT_CARRYOVER_MESSAGES,
) -> dict[str, Any]:
    payload = carryover._apply_carryover_for_request(
        _carryover_request(config, selection),
        authority=config,
        options=carryover.CarryoverApplyOptions(
            intent_note=intent_note,
            include_review_required=include_review_required,
            memory_commit_message=messages.memory,
            ledger_commit_message=messages.ledger,
        ),
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
    contract_path = require_within_coordination(config, selection.contract_path, "contract_path")
    contract = load_contract(contract_path)
    if (
        contract.kind != "leaf"
        or contract.repo_name != repo.repo_id
        or contract.memory_mode != "external"
        or contract.memory_worktree is None
    ):
        raise AuthorityError(
            "memory carryover requires an external-memory leaf contract owned by repo_id"
        )
    if contract.closeout_status != "not-started" or contract.integration_status != "not-started":
        raise AuthorityError(
            "memory carryover target leaf must be open before closeout and integration"
        )
    require_ordinary_worktree(contract, operation="memory_carryover_apply")
    return carryover.CarryoverRequest(
        config_path=config.config_path,
        target_contract_path=contract.contract_path,
        code_repository_root=repo.path,
        official_code_ref=selection.official_code_ref,
        source_code_ref=selection.source_code_ref,
        old_base=selection.old_base,
        target_memory=contract.memory_worktree,
        source_memory=source_memory_path,
        code_repository_name=repo.repo_id,
        replace_existing=selection.replace_existing,
    )
