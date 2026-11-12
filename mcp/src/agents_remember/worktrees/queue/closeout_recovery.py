"""Recover the exact code and memory outputs without a ledger transaction."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_cache import refresh_memory_cache
from agents_remember.models.closeout.input import EffectiveCloseoutInput
from agents_remember.models.lifecycles.mutation_evidence import GitMutationSnapshot
from agents_remember.models.lifecycles.operation import LifecycleOperationRecoveryCommits
from agents_remember.worktrees.integration.integration_branch_repository import (
    canonical_local_branch,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    begin_git_mutation,
    prove_git_commit,
)
from agents_remember.worktrees.modules.args import WorktreeArgs, report_operation_progress
from agents_remember.worktrees.modules.git import (
    CommitPublicationRefusal,
    branch_commit,
    head_commit,
    is_ancestor,
    publish_tree_commit,
    require_clean,
    require_git,
    worktree_dirty,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class AdmittedTip:
    """What a closeout's publication is bound to: the contract's work branch and its tip.

    ``head`` is the commit the branch named when the tree the closeout commits was read (the
    judged tree of the memory side, the accepted candidate tree of the code side), so the
    published commit's only parent is the commit that tree was judged on. ``None`` when the route
    recorded no head with its tree; the branch is bound all the same.
    """

    side: str
    branch: str
    head: str | None


def observe_admitted_tip(repository: Path, work_branch: str, *, side: str) -> AdmittedTip:
    """Read the tip a publication is bound to, before the tree it commits is read.

    Read in this order, a commit that moves the branch at any later moment refuses the publication
    instead of silently becoming the parent of a tree that was judged without it. A checkout that
    is not on the contract's work branch (another branch, a detached ``HEAD``) refuses here.
    """

    admitted = AdmittedTip(side, canonical_local_branch(repository, work_branch), None)
    require_admitted_tip(repository, admitted)
    return AdmittedTip(side, admitted.branch, head_commit(repository))


def require_admitted_tip(
    repository: Path, admitted: AdmittedTip, *, snapshot: GitMutationSnapshot | None = None
) -> None:
    """Refuse a publication that would not move the admitted branch from the admitted tip.

    ``snapshot`` is the Git state the publication is bound to (its mutation intent); without one
    the repository is read now, which is the check made before an intent is recorded. The
    snapshot alone is taken after the gate ran, so a ``HEAD`` switched to another
    branch, or a branch moved by someone else's commit, in between would otherwise be published
    to, on, or over.
    """

    if snapshot is None:
        head_ref = run_git(repository, ["symbolic-ref", "--quiet", "HEAD"]).stdout.strip()
        head = head_commit(repository)
    else:
        head_ref, head = snapshot.headRef, snapshot.head
    expected = f"refs/heads/{admitted.branch}"
    if not head_ref or canonical_local_branch(repository, head_ref) != admitted.branch:
        raise CommitPublicationRefusal(
            f"the {admitted.side} worktree's HEAD names "
            f"{head_ref or 'no branch (it is detached)'}, not the contract's work branch "
            f"{expected} this closeout admitted, so nothing was published; return to {expected} "
            "and rerun the closeout"
        )
    if admitted.head is not None and head != admitted.head:
        raise CommitPublicationRefusal(
            f"{expected} moved from {admitted.head} to {head} after this closeout read the "
            f"{admitted.side} tree it judged, so nothing was published; rerun the closeout and it "
            "judges the tree on the new tip"
        )


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
        assert args.candidate_tree is not None
        # The publication is bound to what the closeout admitted: the contract's work branch, and
        # the head the candidate tree was read on (the closeout's admission has just checked the
        # branch; the snapshot the publication uses is checked for both).
        admitted = AdmittedTip(
            "code",
            canonical_local_branch(contract.code_worktree, contract.code_work_branch),
            args.candidate_head,
        )
        intent = begin_git_mutation(
            args,
            leg="code",
            repository=contract.code_worktree,
            expected_output_tree=args.candidate_tree,
        )
        assert intent.before is not None
        require_admitted_tip(contract.code_worktree, admitted, snapshot=intent.before)
        # The commit is the accepted candidate tree itself, whatever was written to the worktree
        # since it was read; such a file stays an uncommitted change. Staging, publication and the
        # index restore after a refusal are one step of the shared primitive.
        code_commit = publish_tree_commit(
            contract.code_worktree,
            effective_input.message_for("code"),
            tree=args.candidate_tree,
            before=intent.before,
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
