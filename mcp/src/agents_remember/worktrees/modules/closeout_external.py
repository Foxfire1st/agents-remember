"""Publish external memory content and refresh its disposable ledger view."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory

from agents_remember.kernel.memory_cache import prepare_memory_cache, refresh_memory_cache
from agents_remember.models.closeout.input import EffectiveCloseoutInput
from agents_remember.models.memory_content_excludes import (
    MEMORY_CONTENT_EXCLUDES,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    begin_git_mutation,
    prove_git_commit,
)
from agents_remember.worktrees.knowledge_gate import (
    HistoryClosing,
    close_owner_history,
    leaf_memory_converted,
    parent_memory_tip,
)
from agents_remember.worktrees.knowledge_validation import PairedCode, memory_commit_refusal
from agents_remember.worktrees.modules.args import WorktreeArgs, report_operation_progress
from agents_remember.worktrees.modules.context import contract_context
from agents_remember.worktrees.modules.git import (
    commit_verified_staged,
    head_commit,
    stage_worktree_content,
    worktree_candidate_tree,
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
    closing = None
    if leaf_memory_converted(contract):
        # MIK-R07 rule 7 / MIK-R09 rule 3: the memory commit that publishes the leaf closes its
        # history file, creating it with no rows when the leaf wrote none.
        closing = close_owner_history(contract.memory_worktree, _owner(contract))
    memory_commit, created = _commit_memory_content(
        contract,
        args,
        effective_input,
        code_commit=change.commit,
        closing=closing,
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


def _owner(contract) -> str:
    if not contract.leaf_id:
        raise RuntimeError(
            "the mandatory invariant gate (MIK-R09) refuses this closeout: a converted leaf names "
            "its leaf id, the owner of its history file"
        )
    return contract.leaf_id


def _refuse_invalid_memory_commit(contract, repository: Path, code_commit: str) -> None:
    """Validate the exact tree this closeout is about to commit (MIK-R22 rule 8, MIK-R09 rule 3).

    The tree is captured with the commit's own exclusions and validated against the parent line's
    memory tip, with the closeout's code commit as the paired code tree. A refusal names every
    violation and nothing is committed.
    """

    with TemporaryDirectory(
        prefix=".closeout-memory-gate-", dir=contract.worktree_group
    ) as scratch:
        tree = worktree_candidate_tree(
            repository, Path(scratch) / "index", exclude_paths=MEMORY_CONTENT_EXCLUDES
        )
    try:
        tip = parent_memory_tip(contract)
    except (RuntimeError, subprocess.SubprocessError) as error:  # failed or timed out: named
        raise RuntimeError(
            "the knowledge validator (MIK-R22) cannot read the parent memory line "
            f"({type(error).__name__}: {error}); nothing is committed"
        ) from error
    refusal = memory_commit_refusal(
        memory_repository=repository,
        candidate_tree=tree,
        bases=() if tip is None else (tip,),
        paired_code=PairedCode(repository=contract.code_worktree, commit=code_commit),
        leaf_publication=True,
    )
    if refusal is not None:
        raise RuntimeError(refusal)


def _commit_memory_content(
    contract,
    args: WorktreeArgs,
    effective_input: EffectiveCloseoutInput,
    *,
    code_commit: str,
    closing: HistoryClosing | None = None,
) -> tuple[str, bool]:
    """Commit only actual memory changes, binding code attribution inside the commit.

    ``closing`` (converted memory) is the closeout's own closing of the leaf's history file: the
    exact tree is validated before anything is committed, and a refusal -- or any failure before
    the commit begins -- restores the file, so a refused closeout leaves it as the leaf wrote it
    (L09 review R1, finding 2).
    """

    repository = contract.memory_worktree
    assert repository is not None
    try:
        if not worktree_dirty(repository, exclude_paths=MEMORY_CONTENT_EXCLUDES):
            return head_commit(repository), False
        if closing is not None:
            _refuse_invalid_memory_commit(contract, repository, code_commit)
        prepare_memory_cache(repository)
    except BaseException:
        if closing is not None:
            closing.restore()
        raise
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
