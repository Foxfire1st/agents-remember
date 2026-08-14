"""Persist and resume closeout outputs at each irreversible Git boundary."""

from __future__ import annotations

from agents_remember.kernel.memory_ledger import (
    find_mapping,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
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
