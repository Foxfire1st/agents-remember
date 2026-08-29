"""External-memory commit phase for normalized worktree closeout."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from agents_remember.kernel.memory_ledger import (
    find_mapping,
    ledger_to_text,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.models.closeout.input import EffectiveCloseoutInput
from agents_remember.worktrees.integration.closeout.curator_coherence import (
    CuratorCoherenceNoImpact,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    begin_exact_file_git_mutation,
    begin_git_mutation,
    prove_git_commit,
)
from agents_remember.worktrees.modules.args import WorktreeArgs, report_operation_progress
from agents_remember.worktrees.modules.context import contract_context
from agents_remember.worktrees.modules.git import (
    commit_if_dirty,
    head_commit,
    is_ancestor,
    require_git,
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
from agents_remember.worktrees.modules.quality.closeout_memory import (
    combine_memory_quality,
    run_memory_quality_phase,
)
from agents_remember.worktrees.queue.closeout_recovery import (
    MemoryCloseoutOutcome,
    resume_external_commits,
)
from agents_remember.worktrees.series_closeout import exact_series_memory_closeout
from agents_remember.worktrees.services import worktree_services


@dataclass(frozen=True)
class ExternalCloseoutEvidence:
    """Reversible memory and no-impact evidence accepted before code commit."""

    memory_quality_before_refresh: dict[str, Any]
    coherence_no_impact: CuratorCoherenceNoImpact


def external_closeout_commits(
    contract,
    args: WorktreeArgs,
    effective_input: EffectiveCloseoutInput,
    change: VerifiedChange,
    evidence: ExternalCloseoutEvidence,
) -> MemoryCloseoutOutcome:
    if contract.ledger_path is None:
        raise RuntimeError("external-memory closeout requires a ledger path")
    code_commit = change.commit
    if contract.kind == "series":
        return exact_series_memory_closeout(contract, code_commit)
    if contract.memory_worktree is None:
        raise RuntimeError("external-memory leaf closeout requires a memory worktree")
    resuming = args.approval_claimed or args.recovery_commits is not None
    recovered = _resumed_external_outcome(contract, args, effective_input, code_commit)
    if recovered is not None:
        return recovered
    refresh = _refresh_external_memory(
        contract,
        args,
        change,
        evidence,
    )
    ledger = load_ledger(contract.ledger_path)
    existing_mapping = find_mapping(ledger, code_commit)
    memory_commit, memory_created = _commit_memory_content(
        contract,
        args,
        effective_input,
        existing_mapping=existing_mapping,
        resuming=resuming,
    )
    if not memory_created:
        _report_memory_commit(args, code_commit, memory_commit)
    ledger_commit, ledger_created = _commit_ledger_mapping(
        contract,
        args,
        effective_input,
        _LedgerCommitFacts(ledger, existing_mapping, code_commit, memory_commit),
    )
    if not ledger_created:
        _report_ledger_commit(args, code_commit, memory_commit, ledger_commit)
    return MemoryCloseoutOutcome(
        memory_commit=memory_commit,
        ledger_commit=ledger_commit,
        refreshed_onboarding=refresh.onboarding,
        refreshed_entities=refresh.entities,
        refreshed_route_overviews=refresh.route_overviews,
        route_index_refresh=refresh.route_index,
        memory_quality=refresh.quality,
    )


@dataclass(frozen=True)
class _ExternalMemoryRefresh:
    onboarding: list[dict[str, str]]
    entities: list[dict[str, object]]
    route_overviews: list[dict[str, str]]
    route_index: dict[str, object]
    quality: dict[str, Any]


@dataclass(frozen=True)
class _LedgerCommitFacts:
    ledger: Any
    existing_mapping: Any
    code_commit: str
    memory_commit: str


def _refresh_external_memory(
    contract,
    args: WorktreeArgs,
    change: VerifiedChange,
    evidence: ExternalCloseoutEvidence,
) -> _ExternalMemoryRefresh:
    context = replace(contract_context(contract), code_repository_root=contract.code_worktree)
    report_operation_progress(
        args, "memory-refresh", current_command="refresh onboarding and route metadata"
    )
    refreshed_onboarding = refresh_onboarding_metadata(
        contract,
        change,
        accepted_no_impact=evidence.coherence_no_impact.content_sources,
    )
    refreshed_route_overviews = refresh_route_overview_metadata_for_context(
        context,
        change,
        memory_tree=contract.memory_worktree,
        memory_verified_commit=contract_memory_verified_commit(contract),
        accepted_no_impact=evidence.coherence_no_impact.source_routes,
    )
    refreshed_entities = refresh_entity_fingerprints_for_context(context, change.changed_paths)
    route_index_refresh = refresh_route_indexes_for_context(context)
    _, after_checks = worktree_services().memory_quality.check_groups()
    memory_quality_after_refresh = run_memory_quality_phase(context, after_checks)
    memory_quality = combine_memory_quality(
        evidence.memory_quality_before_refresh, memory_quality_after_refresh
    )
    return _ExternalMemoryRefresh(
        refreshed_onboarding,
        refreshed_entities,
        refreshed_route_overviews,
        route_index_refresh,
        memory_quality,
    )


def _commit_memory_content(
    contract,
    args: WorktreeArgs,
    effective_input: EffectiveCloseoutInput,
    *,
    existing_mapping,
    resuming: bool,
) -> tuple[str, bool]:
    assert contract.memory_worktree is not None
    report_operation_progress(
        args, "memory-commit", current_command="commit verified external memory"
    )
    if worktree_dirty(contract.memory_worktree):
        memory_intent = begin_git_mutation(
            args,
            leg="memory",
            repository=contract.memory_worktree,
            expected_output_tree=None,
            use_current_candidate=True,
        )
        committed = commit_if_dirty(
            contract.memory_worktree,
            effective_input.message_for("memory"),
        )
        prove_git_commit(
            args,
            memory_intent,
            repository=contract.memory_worktree,
            commit=committed,
        )
        return committed, True
    if existing_mapping is not None:
        committed = existing_mapping.memory_commit
        if not is_ancestor(
            contract.memory_worktree, committed, head_commit(contract.memory_worktree)
        ):
            raise RuntimeError(
                "closeout recovery ledger mapping names memory content that is not reachable "
                "from the current memory worktree"
            )
        return committed, False
    memory_head = head_commit(contract.memory_worktree)
    return (
        memory_head if resuming else contract.memory_content_commit or memory_head,
        False,
    )


def _report_memory_commit(args: WorktreeArgs, code_commit: str, memory_commit: str) -> None:
    report_operation_progress(
        args,
        "memory-commit",
        current_command="external memory commit recorded for recovery",
        recovery_commits={
            "codeCommit": code_commit,
            "memoryContentCommit": memory_commit,
            "ledgerCommit": "",
        },
    )


def _commit_ledger_mapping(
    contract,
    args: WorktreeArgs,
    effective_input: EffectiveCloseoutInput,
    facts: _LedgerCommitFacts,
) -> tuple[str, bool]:
    assert contract.memory_worktree is not None and contract.ledger_path is not None
    if (
        facts.existing_mapping is not None
        and facts.existing_mapping.memory_commit == facts.memory_commit
    ):
        return head_commit(contract.memory_worktree), False
    intended_ledger = prepend_mapping(
        facts.ledger,
        facts.code_commit,
        facts.memory_commit,
    )
    ledger_intent = begin_exact_file_git_mutation(
        args,
        leg="ledger",
        repository=contract.memory_worktree,
        path=contract.ledger_path,
        intended_text=ledger_to_text(intended_ledger),
    )
    write_ledger(contract.ledger_path, intended_ledger)
    require_git(contract.memory_worktree, ["add", "memory.md"])
    committed = commit_if_dirty(
        contract.memory_worktree,
        effective_input.message_for("ledger"),
    )
    prove_git_commit(
        args,
        ledger_intent,
        repository=contract.memory_worktree,
        commit=committed,
    )
    return committed, True


def _report_ledger_commit(
    args: WorktreeArgs,
    code_commit: str,
    memory_commit: str,
    ledger_commit: str,
) -> None:
    report_operation_progress(
        args,
        "ledger-commit",
        current_command="external ledger commit recorded for recovery",
        recovery_commits={
            "codeCommit": code_commit,
            "memoryContentCommit": memory_commit,
            "ledgerCommit": ledger_commit,
        },
    )


def _resumed_external_outcome(
    contract,
    args: WorktreeArgs,
    effective_input: EffectiveCloseoutInput,
    code_commit: str,
) -> MemoryCloseoutOutcome | None:
    recovery_memory_commit = (
        args.recovery_commits.memoryContentCommit if args.recovery_commits is not None else ""
    )
    if not recovery_memory_commit:
        return None
    memory_commit, ledger_commit = resume_external_commits(
        contract,
        args,
        effective_input,
        code_commit=code_commit,
        memory_commit=recovery_memory_commit,
    )
    return MemoryCloseoutOutcome(memory_commit=memory_commit, ledger_commit=ledger_commit)
