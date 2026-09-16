"""Recover the exact code and memory outputs without a ledger transaction."""

from __future__ import annotations

from dataclasses import dataclass, field

from agents_remember.kernel.memory_cache import refresh_memory_cache
from agents_remember.models.closeout.input import EffectiveCloseoutInput
from agents_remember.models.lifecycles.operation import LifecycleOperationRecoveryCommits
from agents_remember.worktrees.integration.mutation_evidence import (
    begin_git_mutation,
    prove_git_commit,
)
from agents_remember.worktrees.modules.args import WorktreeArgs, report_operation_progress
from agents_remember.worktrees.modules.git import (
    branch_commit,
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
    """The real memory output and informational cache/metadata refresh results."""

    memory_commit: str = ""
    refreshed_onboarding: list[dict[str, str]] = field(default_factory=list)
    refreshed_entities: list[dict[str, object]] = field(default_factory=list)
    refreshed_route_overviews: list[dict[str, str]] = field(default_factory=list)
    route_index_refresh: dict[str, object] = field(default_factory=dict)
    ledger_repair: dict[str, object] = field(default_factory=dict)


def prove_closeout_recovery_commits(
    contract: WorktreeContract, commits: LifecycleOperationRecoveryCommits
) -> MemoryCloseoutOutcome:
    """Reprove the accepted output refs without consulting a cached table."""

    if contract.kind == "series":
        code_head = branch_commit(contract.code_repo_path, contract.code_work_branch)
    else:
        require_clean(contract.code_worktree, "recovering closeout code worktree")
        code_head = head_commit(contract.code_worktree)
    if code_head != commits.codeCommit:
        raise RuntimeError("closeout recovery code commit does not match the exact task ref")
    if contract.memory_mode != "external":
        if commits.memoryContentCommit:
            raise RuntimeError("non-external closeout recorded an external memory commit")
        return MemoryCloseoutOutcome()
    return _prove_memory_output(contract, commits.memoryContentCommit)


def _prove_memory_output(contract: WorktreeContract, expected: str) -> MemoryCloseoutOutcome:
    if contract.memory_repo_path is None or not expected:
        raise RuntimeError("external-memory recovery requires an accepted memory output")
    if contract.kind == "series":
        actual = branch_commit(contract.memory_repo_path, contract.memory_work_branch)
    else:
        if contract.memory_worktree is None:
            raise RuntimeError("external-memory recovery requires the memory worktree")
        require_clean(
            contract.memory_worktree,
            "recovering closeout memory worktree",
            exclude_paths=("memory.md",),
        )
        actual = head_commit(contract.memory_worktree)
    if actual != expected:
        raise RuntimeError("closeout recovery memory commit does not match the exact task ref")
    if not is_ancestor(contract.memory_repo_path, contract.memory_base_commit, actual):
        raise RuntimeError("closeout recovery memory output does not contain its accepted source")
    cache = (
        refresh_memory_cache(contract.memory_worktree, actual, path=contract.ledger_path)
        if contract.memory_worktree is not None
        else {}
    )
    return MemoryCloseoutOutcome(memory_commit=actual, ledger_repair=cache)


def accepted_code_commit(
    contract,
    args: WorktreeArgs,
    effective_input: EffectiveCloseoutInput,
) -> str:
    """Commit or prove the accepted code tree, then journal its exact commit.

    Closeout is a Git transaction.  The candidate/ref and mutation journal carry
    the safety checks; code-quality execution is an explicit developer action
    outside this transaction.
    """
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
        require_git(contract.code_worktree, ["add", "-A"])
        code_commit = commit_verified_staged(
            contract.code_worktree, effective_input.message_for("code")
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
            },
        )
    return code_commit


def resume_external_commits(
    contract: WorktreeContract,
    args: WorktreeArgs,
    *,
    code_commit: str,
    memory_commit: str,
) -> MemoryCloseoutOutcome:
    """Memory was already committed; reprove it and refresh only its disposable view."""

    outcome = _prove_memory_output(contract, memory_commit)
    report_operation_progress(
        args,
        "memory-commit",
        current_command="verified-existing memory output",
        recovery_commits={"codeCommit": code_commit, "memoryContentCommit": memory_commit},
    )
    return outcome
