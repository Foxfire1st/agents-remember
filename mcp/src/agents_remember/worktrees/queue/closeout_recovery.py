"""Persist and resume closeout outputs at each irreversible Git boundary."""

from __future__ import annotations

from dataclasses import dataclass, field

from agents_remember.kernel.memory_ledger import (
    find_mapping,
    load_ledger,
    parse_ledger_text,
    prepend_mapping,
    write_ledger,
)
from agents_remember.models.closeout_input import EffectiveCloseoutInput
from agents_remember.models.lifecycles.operation import LifecycleOperationRecoveryCommits
from agents_remember.worktrees.integration.mutation_evidence import (
    begin_git_mutation,
    bind_expected_output_tree,
    prove_git_commit,
)
from agents_remember.worktrees.modules.args import WorktreeArgs, report_operation_progress
from agents_remember.worktrees.modules.git import (
    branch_commit,
    commit_if_dirty,
    commit_verified_staged,
    head_commit,
    is_ancestor,
    require_clean,
    require_git,
    worktree_dirty,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class MemoryCloseoutOutcome:
    """What external-memory closeout committed and refreshed."""

    memory_commit: str = ""
    ledger_commit: str = ""
    refreshed_onboarding: list[dict[str, str]] = field(default_factory=list)
    refreshed_entities: list[dict[str, object]] = field(default_factory=list)
    refreshed_route_overviews: list[dict[str, str]] = field(default_factory=list)
    route_index_refresh: dict[str, object] = field(default_factory=dict)
    memory_quality: dict[str, object] = field(default_factory=dict)


def prove_closeout_recovery_commits(
    contract: WorktreeContract, commits: LifecycleOperationRecoveryCommits
) -> MemoryCloseoutOutcome:
    """Prove the exact post-commit state without replaying any closeout mutation."""

    _prove_recovered_code_commit(contract, commits.codeCommit)
    if contract.memory_mode != "external":
        if commits.memoryContentCommit or commits.ledgerCommit:
            raise RuntimeError(
                "closeout contract-finalization recovery recorded external-memory commits "
                "for an internal-memory contract"
            )
        return MemoryCloseoutOutcome()
    if contract.kind == "series":
        return _prove_recovered_series_memory(contract, commits)
    return _prove_recovered_leaf_memory(contract, commits)


def _prove_recovered_code_commit(contract: WorktreeContract, expected: str) -> None:
    if contract.kind == "series":
        code_head = branch_commit(contract.code_repo_path, contract.code_work_branch)
    else:
        require_clean(contract.code_worktree, "recovering closeout code worktree")
        code_head = head_commit(contract.code_worktree)
    if code_head != expected:
        raise RuntimeError(
            "closeout contract-finalization recovery requires manual reconciliation: "
            f"recorded code commit {expected}, found task HEAD {code_head}"
        )


def _prove_recovered_series_memory(
    contract: WorktreeContract, commits: LifecycleOperationRecoveryCommits
) -> MemoryCloseoutOutcome:
    if contract.memory_repo_path is None:
        raise RuntimeError("external-memory series recovery requires a memory repository")
    memory_head = branch_commit(contract.memory_repo_path, contract.memory_work_branch)
    if memory_head != commits.ledgerCommit:
        raise RuntimeError(
            "closeout contract-finalization recovery requires manual reconciliation: "
            f"recorded ledger commit {commits.ledgerCommit}, found series memory ref "
            f"{memory_head}"
        )
    ledger_blob = require_git(
        contract.memory_repo_path,
        ["show", f"{memory_head}:memory.md"],
    )
    _require_recovered_mapping(
        parse_ledger_text(ledger_blob),
        code_commit=commits.codeCommit,
        memory_commit=commits.memoryContentCommit,
    )
    if not is_ancestor(
        contract.memory_repo_path,
        commits.memoryContentCommit,
        commits.ledgerCommit,
    ):
        raise RuntimeError(
            "closeout contract-finalization recovery requires manual reconciliation: "
            "recorded series memory content is not reachable from the recorded ledger commit"
        )
    return MemoryCloseoutOutcome(
        memory_commit=commits.memoryContentCommit,
        ledger_commit=commits.ledgerCommit,
    )


def _prove_recovered_leaf_memory(
    contract: WorktreeContract, commits: LifecycleOperationRecoveryCommits
) -> MemoryCloseoutOutcome:
    if contract.memory_worktree is None or contract.ledger_path is None:
        raise RuntimeError("external-memory closeout recovery requires memory worktree and ledger")
    require_clean(contract.memory_worktree, "recovering closeout memory worktree")
    memory_head = head_commit(contract.memory_worktree)
    if memory_head != commits.ledgerCommit:
        raise RuntimeError(
            "closeout contract-finalization recovery requires manual reconciliation: "
            f"recorded ledger commit {commits.ledgerCommit}, found memory HEAD {memory_head}"
        )
    _require_recovered_mapping(
        load_ledger(contract.ledger_path),
        code_commit=commits.codeCommit,
        memory_commit=commits.memoryContentCommit,
    )
    if not is_ancestor(
        contract.memory_worktree,
        commits.memoryContentCommit,
        commits.ledgerCommit,
    ):
        raise RuntimeError(
            "closeout contract-finalization recovery requires manual reconciliation: "
            "recorded memory content is not reachable from the recorded ledger commit"
        )
    return MemoryCloseoutOutcome(
        memory_commit=commits.memoryContentCommit,
        ledger_commit=commits.ledgerCommit,
    )


def _require_recovered_mapping(ledger, *, code_commit: str, memory_commit: str) -> None:
    mapping = find_mapping(ledger, code_commit)
    if mapping is None or mapping.memory_commit != memory_commit:
        found = "missing" if mapping is None else mapping.memory_commit
        raise RuntimeError(
            "closeout contract-finalization recovery requires manual reconciliation: "
            f"ledger mapping for {code_commit} is {found}, expected {memory_commit}"
        )


def accepted_code_commit(
    contract,
    args: WorktreeArgs,
    effective_input: EffectiveCloseoutInput,
    *,
    strict_code_quality_required: bool,
) -> str:
    """Commit or prove the accepted code tree, then journal its exact commit."""
    commits = args.recovery_commits
    created_commit = False
    if contract.kind == "series":
        code_commit = branch_commit(contract.code_repo_path, contract.code_work_branch)
        if commits is not None and code_commit != commits.codeCommit:
            raise RuntimeError("closeout recovery code commit does not match exact series ref")
    elif commits is not None:
        require_clean(contract.code_worktree, "resuming closeout code commit")
        code_commit = head_commit(contract.code_worktree)
        if code_commit != commits.codeCommit:
            raise RuntimeError("closeout recovery code commit does not match task HEAD")
    elif not worktree_dirty(contract.code_worktree):
        code_commit = head_commit(contract.code_worktree)
    else:
        created_commit = True
        intent = begin_git_mutation(
            args,
            leg="code",
            repository=contract.code_worktree,
            expected_output_tree=None,
            use_current_candidate=True,
        )
        code_commit = (
            commit_verified_staged(contract.code_worktree, effective_input.message_for("code"))
            if strict_code_quality_required
            else commit_if_dirty(contract.code_worktree, effective_input.message_for("code"))
        )
        prove_git_commit(
            args,
            intent,
            repository=contract.code_worktree,
            commit=code_commit,
        )
    repository = contract.code_repo_path if contract.kind == "series" else contract.code_worktree
    committed_tree = require_git(repository, ["rev-parse", f"{code_commit}^{{tree}}"])
    if args.candidate_tree and committed_tree != args.candidate_tree:
        raise RuntimeError("closeout committed tree does not match the accepted candidate tree")
    if not created_commit:
        report_operation_progress(
            args,
            "code-commit",
            current_command="verified-existing code commit recorded for recovery",
            recovery_commits={
                "codeCommit": code_commit,
                "memoryContentCommit": "",
                "ledgerCommit": "",
            },
        )
    return code_commit


def resume_external_commits(
    contract,
    args: WorktreeArgs,
    effective_input: EffectiveCloseoutInput,
    *,
    code_commit: str,
    memory_commit: str,
) -> tuple[str, str]:
    """Finish the exact ledger edge after a journaled memory-content commit."""
    assert contract.memory_worktree is not None and contract.ledger_path is not None
    require_clean(contract.memory_worktree, "resuming external-memory closeout")
    memory_head = head_commit(contract.memory_worktree)
    ledger = load_ledger(contract.ledger_path)
    mapping = find_mapping(ledger, code_commit)
    if mapping is not None and mapping.memory_commit != memory_commit:
        raise RuntimeError("closeout recovery found a conflicting code-to-memory ledger row")
    created_commit = mapping is None
    if created_commit:
        if memory_head != memory_commit:
            raise RuntimeError(
                "closeout recovery cannot prove the recorded memory commit at memory HEAD"
            )
        intent = begin_git_mutation(
            args,
            leg="ledger",
            repository=contract.memory_worktree,
            expected_output_tree=None,
        )
        write_ledger(contract.ledger_path, prepend_mapping(ledger, code_commit, memory_commit))
        require_git(contract.memory_worktree, ["add", "memory.md"])
        intent = bind_expected_output_tree(
            args,
            intent,
            repository=contract.memory_worktree,
        )
        ledger_commit = commit_if_dirty(
            contract.memory_worktree,
            effective_input.message_for("ledger"),
        )
        prove_git_commit(
            args,
            intent,
            repository=contract.memory_worktree,
            commit=ledger_commit,
        )
    else:
        if not is_ancestor(contract.memory_worktree, memory_commit, memory_head):
            raise RuntimeError("closeout recovery memory commit is not reachable from ledger HEAD")
        ledger_commit = memory_head
    if not created_commit:
        report_operation_progress(
            args,
            "ledger-commit",
            current_command="verified-existing ledger commit recorded for recovery",
            recovery_commits={
                "codeCommit": code_commit,
                "memoryContentCommit": memory_commit,
                "ledgerCommit": ledger_commit,
            },
        )
    return memory_commit, ledger_commit
