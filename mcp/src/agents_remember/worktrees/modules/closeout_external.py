"""Publish external memory content and refresh its disposable ledger view."""

from __future__ import annotations

from dataclasses import dataclass, replace

from agents_remember.kernel.memory_cache import prepare_memory_cache, refresh_memory_cache
from agents_remember.models.closeout.input import EffectiveCloseoutInput
from agents_remember.models.memory_content_excludes import (
    MEMORY_CONTENT_EXCLUDES,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    begin_git_mutation,
    prove_git_commit,
)
from agents_remember.worktrees.modules.args import WorktreeArgs, report_operation_progress
from agents_remember.worktrees.modules.context import contract_context
from agents_remember.worktrees.modules.git import (
    commit_verified_staged,
    head_commit,
    stage_worktree_content,
    worktree_dirty,
)
from agents_remember.worktrees.modules.models import VerifiedChange
from agents_remember.worktrees.modules.onboarding import (
    contract_memory_verified_commit,
    refresh_entity_fingerprints_for_context,
    refresh_onboarding_metadata,
    refresh_route_indexes_for_context,
    refresh_route_overview_metadata_for_context,
)
from agents_remember.worktrees.queue.closeout_recovery import (
    MemoryCloseoutOutcome,
    resume_external_commits,
)
from agents_remember.worktrees.series_closeout import series_memory_closeout


def external_closeout_commits(
    contract,
    args: WorktreeArgs,
    effective_input: EffectiveCloseoutInput,
    change: VerifiedChange,
) -> MemoryCloseoutOutcome:
    if contract.kind == "series":
        return series_memory_closeout(contract, change.commit)
    if contract.memory_worktree is None:
        raise RuntimeError("external-memory leaf closeout requires a memory worktree")
    recovered = args.recovery_commits
    if recovered is not None and recovered.memoryContentCommit:
        return resume_external_commits(
            contract,
            args,
            code_commit=change.commit,
            memory_commit=recovered.memoryContentCommit,
        )
    refresh = _refresh_external_memory(contract, args, change)
    memory_commit, created = _commit_memory_content(
        contract,
        args,
        effective_input,
        code_commit=change.commit,
    )
    if not created:
        _report_memory_commit(args, change.commit, memory_commit)
    cache = refresh_memory_cache(
        contract.memory_worktree,
        memory_commit,
        path=contract.ledger_path,
        repo_name=contract.repo_name,
    )
    return MemoryCloseoutOutcome(
        memory_commit=memory_commit,
        refreshed_onboarding=refresh.onboarding,
        refreshed_entities=refresh.entities,
        refreshed_route_overviews=refresh.route_overviews,
        route_index_refresh=refresh.route_index,
        ledger_repair=cache,
    )


def _commit_memory_content(
    contract,
    args: WorktreeArgs,
    effective_input: EffectiveCloseoutInput,
    *,
    code_commit: str,
) -> tuple[str, bool]:
    """Commit only actual memory changes, binding code attribution inside the commit."""

    repository = contract.memory_worktree
    assert repository is not None
    if not worktree_dirty(repository, exclude_paths=MEMORY_CONTENT_EXCLUDES):
        return head_commit(repository), False
    prepare_memory_cache(repository)
    report_operation_progress(
        args, "memory-commit", current_command="commit verified memory content"
    )
    intent = begin_git_mutation(
        args,
        leg="memory",
        repository=repository,
        expected_output_tree=None,
        use_current_candidate=True,
    )
    stage_worktree_content(repository, exclude_paths=MEMORY_CONTENT_EXCLUDES)
    committed = commit_verified_staged(
        repository,
        effective_input.memory_content_message(code_commit),
        exclude_paths=MEMORY_CONTENT_EXCLUDES,
    )
    prove_git_commit(args, intent, repository=repository, commit=committed)
    return committed, True


@dataclass(frozen=True)
class _ExternalMemoryRefresh:
    onboarding: list[dict[str, str]]
    entities: list[dict[str, object]]
    route_overviews: list[dict[str, str]]
    route_index: dict[str, object]


def _refresh_external_memory(
    contract,
    args: WorktreeArgs,
    change: VerifiedChange,
) -> _ExternalMemoryRefresh:
    context = replace(contract_context(contract), code_repository_root=contract.code_worktree)
    report_operation_progress(
        args, "memory-refresh", current_command="refresh onboarding and route metadata"
    )
    refreshed_onboarding = refresh_onboarding_metadata(
        contract,
        change,
    )
    refreshed_route_overviews = refresh_route_overview_metadata_for_context(
        context,
        change,
        memory_tree=contract.memory_worktree,
        memory_verified_commit=contract_memory_verified_commit(contract),
    )
    refreshed_entities = refresh_entity_fingerprints_for_context(context, change.changed_paths)
    route_index_refresh = refresh_route_indexes_for_context(context)
    return _ExternalMemoryRefresh(
        refreshed_onboarding,
        refreshed_entities,
        refreshed_route_overviews,
        route_index_refresh,
    )


def _report_memory_commit(args: WorktreeArgs, code_commit: str, memory_commit: str) -> None:
    report_operation_progress(
        args,
        "memory-commit",
        current_command="external memory commit recorded for recovery",
        recovery_commits={
            "codeCommit": code_commit,
            "memoryContentCommit": memory_commit,
        },
    )
