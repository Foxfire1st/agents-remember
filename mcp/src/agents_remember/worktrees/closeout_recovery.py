"""Persist and resume closeout outputs at each irreversible Git boundary."""

from __future__ import annotations

from dataclasses import dataclass, field

from agents_remember.kernel.memory_ledger import (
    find_mapping,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecoveryCommits
from agents_remember.worktrees.modules.args import WorktreeArgs, report_operation_progress
from agents_remember.worktrees.modules.git import (
    commit_if_dirty,
    commit_verified_staged,
    head_commit,
    is_ancestor,
    require_clean,
    require_git,
    worktree_dirty,
)


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
    contract, commits: LifecycleOperationRecoveryCommits
) -> MemoryCloseoutOutcome:
    """Prove the exact post-commit state without replaying any closeout mutation."""
    require_clean(contract.code_worktree, "recovering closeout code worktree")
    code_head = head_commit(contract.code_worktree)
    if code_head != commits.codeCommit:
        raise RuntimeError(
            "closeout contract-finalization recovery requires manual reconciliation: "
            f"recorded code commit {commits.codeCommit}, found task HEAD {code_head}"
        )
    if contract.memory_mode != "external":
        if commits.memoryContentCommit or commits.ledgerCommit:
            raise RuntimeError(
                "closeout contract-finalization recovery recorded external-memory commits "
                "for an internal-memory contract"
            )
        return MemoryCloseoutOutcome()
    if contract.memory_worktree is None or contract.ledger_path is None:
        raise RuntimeError("external-memory closeout recovery requires memory worktree and ledger")
    require_clean(contract.memory_worktree, "recovering closeout memory worktree")
    memory_head = head_commit(contract.memory_worktree)
    if memory_head != commits.ledgerCommit:
        raise RuntimeError(
            "closeout contract-finalization recovery requires manual reconciliation: "
            f"recorded ledger commit {commits.ledgerCommit}, found memory HEAD {memory_head}"
        )
    mapping = find_mapping(load_ledger(contract.ledger_path), commits.codeCommit)
    if mapping is None or mapping.memory_commit != commits.memoryContentCommit:
        found = "missing" if mapping is None else mapping.memory_commit
        raise RuntimeError(
            "closeout contract-finalization recovery requires manual reconciliation: "
            f"ledger mapping for {commits.codeCommit} is {found}, expected "
            f"{commits.memoryContentCommit}"
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


def accepted_code_commit(
    contract,
    args: WorktreeArgs,
    *,
    strict_code_quality_required: bool,
    resuming: bool,
) -> str:
    """Commit or prove the accepted code tree, then journal its exact commit."""
    commits = args.recovery_commits
    if commits is not None:
        require_clean(contract.code_worktree, "resuming closeout code commit")
        code_commit = head_commit(contract.code_worktree)
        if code_commit != commits.codeCommit:
            raise RuntimeError("closeout recovery code commit does not match task HEAD")
    elif contract.kind != "leaf":
        require_clean(contract.code_worktree, "recording series/master closeout code")
        code_commit = head_commit(contract.code_worktree)
    elif resuming and not worktree_dirty(contract.code_worktree):
        code_commit = head_commit(contract.code_worktree)
    else:
        code_commit = (
            commit_verified_staged(contract.code_worktree, args.code_commit_message)
            if strict_code_quality_required
            else commit_if_dirty(contract.code_worktree, args.code_commit_message)
        )
    committed_tree = require_git(contract.code_worktree, ["rev-parse", f"{code_commit}^{{tree}}"])
    if args.candidate_tree and committed_tree != args.candidate_tree:
        raise RuntimeError("closeout committed tree does not match the accepted candidate tree")
    report_operation_progress(
        args,
        "code-commit",
        current_command="code commit recorded for recovery",
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
    if mapping is None:
        if memory_head != memory_commit:
            raise RuntimeError(
                "closeout recovery cannot prove the recorded memory commit at memory HEAD"
            )
        write_ledger(contract.ledger_path, prepend_mapping(ledger, code_commit, memory_commit))
        require_git(contract.memory_worktree, ["add", "memory.md"])
        ledger_commit = commit_if_dirty(
            contract.memory_worktree,
            args.ledger_commit_message
            or f"[{contract.task_id}] Ledger sync: {code_commit} -> {memory_commit}",
        )
    else:
        if not is_ancestor(contract.memory_worktree, memory_commit, memory_head):
            raise RuntimeError("closeout recovery memory commit is not reachable from ledger HEAD")
        ledger_commit = memory_head
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
    return memory_commit, ledger_commit
