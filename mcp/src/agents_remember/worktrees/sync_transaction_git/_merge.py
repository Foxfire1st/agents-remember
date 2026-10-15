"""Exact Git mutations and proofs for resumable code and memory-content sync.

The memory side excludes its root memory.md cache from candidate, conflict, and merge-tree
identity. Code-side files keep ordinary Git semantics, including a file named memory.md.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Literal

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_cache import prepare_memory_cache
from agents_remember.worktrees.knowledge_crossing import (
    CrossingSyncError,
    apply_crossing,
    close_crossing_history,
    crossing_plan,
    merge_base,
    merge_structure,
    write_crossing_report,
)
from agents_remember.worktrees.knowledge_validation import (
    LayoutProbeError,
    PairedCode,
    has_layout_marker,
    memory_commit_refusal,
)
from agents_remember.worktrees.modules.git import head_commit
from agents_remember.worktrees.services import CrossingPlanView
from agents_remember.worktrees.sync_transaction_git._base import (
    SideMergeOutcome,
    SyncFastForwardValidationError,
    SyncGitProofError,
    SyncKnowledgeValidationError,
)
from agents_remember.worktrees.sync_transaction_git._read import (
    _content_pathspec,
    content_conflicts,
    exact_created_head,
    git_status,
    merge_head,
    sample_paths,
    side_merge_completed,
    stashed_parked_paths,
    unmerged_paths,
)
from agents_remember.worktrees.sync_transaction_git._stash import (
    _refresh_memory_cache,
    _remove_memory_cache_from_index,
    _require_sync_git,
    discard_memory_cache_changes,
    ensure_temporary_worktree,
    require_side_checkout,
)
from agents_remember.worktrees.sync_transaction_state import SyncSideRecord


def commit_check_staged_files(side: SyncSideRecord) -> tuple[str, ...] | None:
    """Staged files of a merge stopped at the repository's commit check, else ``None``.

    The content merged and no path is unmerged, but the merge is still the pinned sync
    merge: the commit -- and the pre-commit check it runs -- did not finish (MIK-R61
    rule 12). ``None`` everywhere else, including a merge with genuine content conflicts.
    """

    worktree = Path(side.worktree)
    if merge_head(worktree) != side.sourceCommit or head_commit(worktree) != side.preSyncHead:
        return None
    if content_conflicts(side):
        return None
    # Anything else that stops the commit before the hook is not the commit check: unstaged
    # changes, or whitespace errors git's own --check refuses. Those keep their own answers.
    unstaged = run_git(worktree, ["diff", "--quiet", *_content_pathspec(side)])
    if unstaged.returncode != 0:
        return None
    checked = run_git(worktree, ["diff", "--cached", "--check", *_content_pathspec(side)])
    if checked.returncode != 0:
        return None
    staged = run_git(worktree, ["diff", "--cached", "--name-only", "-z", "--", "."])
    if staged.returncode != 0:
        return None
    names = [entry for entry in staged.stdout.split("\0") if entry]
    if side.side == "memory":
        names = [name for name in names if name != "memory.md"]
    return tuple(names) if names else None


def commit_check_report(side: SyncSideRecord) -> dict[str, object] | None:
    """Describe a merge stopped at the repository's commit check (MIK-R61 rule 12).

    Names every staged file and whether the task changed it: a staged file the sync's
    parked candidate holds is the task's, any other is from the carried line. ``None``
    where the side is not stopped at the check.
    """

    staged = commit_check_staged_files(side)
    if staged is None:
        return None
    try:
        parked = set(stashed_parked_paths(side))
    except SyncGitProofError:
        parked = set(sample_paths(side))
    return {
        "side": side.side,
        "staged": list(staged),
        "taskChanged": {path: (path in parked) for path in staged},
    }


def _require_active_merge(side: SyncSideRecord) -> None:
    worktree = Path(side.worktree)
    if head_commit(worktree) != side.preSyncHead or merge_head(worktree) != side.sourceCommit:
        raise SyncGitProofError(f"{side.side} active merge is not the pinned sync merge")


def validate_memory_fast_forward(side: SyncSideRecord, paired_code: PairedCode | None) -> None:
    """Judge a fast-forward's incoming tree before the sync moves any ref (MIK-R61 rule 4).

    A fast-forward adopts its incoming tree as it is, so an incoming converted tree is run
    through the validator first, with the call the merge route uses after a merge, the
    leaf's own pre-sync head as the base, and the settled code side of the same sync.
    Plans that adopt nothing never reach here. A refusal moves no ref: the violation is
    on the source line, and the way on is repairing that line and syncing again.
    """

    repository = Path(side.repository)
    try:
        converted = has_layout_marker(repository, side.sourceCommit)
    except LayoutProbeError as error:
        raise SyncFastForwardValidationError(
            "the knowledge validator (MIK-R22) cannot read the incoming memory tree "
            f"{side.sourceCommit} of {side.sourceBranch}: {error}; the fast-forward is "
            "refused and no ref moved"
        ) from error
    if not converted:
        return
    incoming = run_git(repository, ["rev-parse", "--verify", f"{side.sourceCommit}^{{tree}}"])
    if incoming.returncode != 0:
        raise SyncFastForwardValidationError(
            "the knowledge validator (MIK-R22) cannot read the incoming memory tree of "
            f"{side.sourceBranch} at {side.sourceCommit} "
            f"({incoming.stderr.strip() or incoming.stdout.strip() or 'unreadable tree'}); "
            "the fast-forward is refused and no ref moved"
        )
    refusal = memory_commit_refusal(
        memory_repository=repository,
        candidate_tree=incoming.stdout.strip(),
        bases=(side.preSyncHead, side.sourceCommit),
        paired_code=paired_code,
    )
    if refusal is not None:
        raise SyncFastForwardValidationError(
            f"the incoming memory tree of {side.sourceBranch} at {side.sourceCommit} fails "
            f"the knowledge validator (MIK-R22): {refusal} The violation is on the source "
            "line: repair that line and sync again; the leaf's memory ref has not moved."
        )


def _continue_memory_merge(
    side: SyncSideRecord, paired_code: PairedCode | None
) -> SideMergeOutcome:
    """Settle the memory merge: commit it when nothing is unmerged, else hand it to the agent.

    Knowledge is text. A merge with a converted side has already had its ``knowledge/`` and
    ``onboarding/`` paths merged structurally (:func:`_apply_knowledge_merge`), so whatever is still
    unmerged here is an item both sides authored differently, or a file outside those roots, and is
    the agent's to resolve and ``continue``. The staged tree is validated (MIK-R22) before it is
    committed.
    """

    _require_active_merge(side)
    _remove_memory_cache_from_index(side)
    conflicts = content_conflicts(side)
    if conflicts:
        return SideMergeOutcome(state="resolution-required", conflicts=conflicts)
    validate_staged_resolution(side)
    return SideMergeOutcome(
        state="completed", conflicts=(), message=_finish_staged_memory_merge(side, paired_code)
    )


def _existing_side_merge(
    side: SyncSideRecord, paired_code: PairedCode | None
) -> SideMergeOutcome | None:
    """Resume only the admitted merge or its exact completed output."""
    worktree = Path(side.worktree)
    if merge_head(worktree) is not None:
        _require_active_merge(side)
        if side.side == "memory" and side.plan == "merge":
            return _continue_memory_merge(side, paired_code)
        return SideMergeOutcome(state="resolution-required", conflicts=unmerged_paths(worktree))
    if side_merge_completed(side):
        return SideMergeOutcome(state="completed", message=head_commit(worktree))
    return None


def start_side_merge(
    side: SyncSideRecord,
    *,
    paired_code: PairedCode | None = None,
    crossing_owner: tuple[Literal["leaf", "master"], str] | None = None,
) -> SideMergeOutcome:
    """Attempt the pinned merge; only genuine content conflicts require resolution.

    ``paired_code`` is the code commit the memory merge is paired with (the code side's result):
    the knowledge validator checks the merged memory tree against it (MIK-R22 rule 8).
    ``crossing_owner`` names who performs the sync (the leaf, or the master line's task): a memory
    merge with a converted side has its knowledge paths merged structurally (MIK-R24 rule 8 step 3,
    in every managed sync by MIK-R26 rule 2), and a conflicted record's row is that owner's.
    """

    worktree = Path(side.worktree)
    require_side_checkout(side)
    existing = _existing_side_merge(side, paired_code)
    if existing is not None:
        return existing
    if head_commit(worktree) != side.preSyncHead:
        raise SyncGitProofError(f"{side.side} work branch moved after sync admission")
    if git_status(side):
        raise SyncGitProofError(f"{side.side} sync requires a clean worktree before merging")
    discard_memory_cache_changes(side)
    memory_merge = side.side == "memory" and side.plan == "merge"
    knowledge = _knowledge_merge(side, paired_code, crossing_owner, memory_merge=memory_merge)
    # A memory merge stops before committing so the structural merge and the validator see it staged.
    no_commit = ["--no-commit"] if memory_merge else []
    result = run_git(worktree, ["merge", *no_commit, "--no-edit", side.sourceCommit])
    if knowledge is not None:
        return _apply_knowledge_merge(side, knowledge, result, paired_code)
    if result.returncode == 0:
        if memory_merge:
            return _continue_memory_merge(side, paired_code)
        result_head = head_commit(worktree)
        if not exact_created_head(side, result_head):
            raise SyncGitProofError(f"{side.side} merge did not create the exact admitted head")
        _refresh_memory_cache(side)
        return SideMergeOutcome(state="completed", message=result_head)
    conflicts = unmerged_paths(worktree)
    if result.returncode == 1 and merge_head(worktree) == side.sourceCommit and conflicts:
        if memory_merge:
            return _continue_memory_merge(side, paired_code)
        return SideMergeOutcome(
            state="resolution-required",
            conflicts=conflicts,
            message=(result.stderr or result.stdout).strip(),
        )
    raise SyncGitProofError(
        (result.stderr or result.stdout).strip() or f"{side.side} source merge failed"
    )


def _needs_crossing_report(plan: CrossingPlanView) -> bool:
    """Whether the structural plan leaves something for the curator to read.

    An item for the curator, a converted side, a renumbered reference, a kept
    evidence line, or a skipped card (MIK-R66 rules 7-9): every nonzero alignment
    diagnostic persists a report, even with no conflict.
    """

    alignment = plan.report.get("alignment") or {}
    return bool(
        plan.conflict_versions
        or plan.report.get("converted")
        or alignment.get("renumberedReferences")
        or alignment.get("evidenceLinesKept")
        or alignment.get("skippedCards")
    )


def _apply_knowledge_merge(
    side: SyncSideRecord,
    plan: CrossingPlanView,
    result: subprocess.CompletedProcess[str],
    paired_code: PairedCode | None,
) -> SideMergeOutcome:
    """Replace the started merge's knowledge and onboarding paths with the structural plan.

    The report is written when the merge leaves something to read: an item for the curator, or a
    side it converted (a crossing). A clean merge of converted lines writes none, unless the
    reference alignment left diagnostics (MIK-R66 rules 7-9): renumbered references, kept
    evidence lines, or skipped cards are reported too.
    """

    worktree = Path(side.worktree)
    if result.returncode not in {0, 1} or merge_head(worktree) != side.sourceCommit:
        raise SyncGitProofError(
            (result.stderr or result.stdout).strip() or "memory knowledge merge failed"
        )
    report = ""
    try:
        apply_crossing(worktree, plan)
        if _needs_crossing_report(plan):
            report = write_crossing_report(
                worktree, side.preSyncHead, side.sourceCommit, plan
            ).as_posix()
    except CrossingSyncError as error:
        raise SyncGitProofError(str(error)) from error
    outcome = _continue_memory_merge(side, paired_code)
    return replace(outcome, crossing_report=report)


def _knowledge_merge(
    side: SyncSideRecord,
    paired_code: PairedCode | None,
    owner: tuple[Literal["leaf", "master"], str] | None,
    *,
    memory_merge: bool,
) -> CrossingPlanView | None:
    """The structural plan of a memory merge with a converted side, or ``None`` for a plain merge.

    MIK-R24 rule 8 step 3 in every managed sync (MIK-R26 rule 2): three converted trees merge as
    they are, and a crossing converts its unconverted trees first. Only three unconverted trees are
    left to Git. It runs before Git touches the worktree, so a failing step leaves the line
    unchanged.
    """

    if not memory_merge:
        return None
    worktree = Path(side.worktree)
    try:
        base = merge_base(worktree, side.preSyncHead, side.sourceCommit)
        if merge_structure(worktree, base, side.preSyncHead, side.sourceCommit) == "plain":
            return None
        if owner is None:
            raise CrossingSyncError(
                "crossing sync step 'markers' failed: the sync names no leaf or master owner"
            )
        return crossing_plan(
            worktree,
            (base, side.preSyncHead, side.sourceCommit),
            paired_code=paired_code,
            owner=owner,
        )
    except CrossingSyncError as error:
        raise SyncGitProofError(str(error)) from error


def _finish_staged_memory_merge(side: SyncSideRecord, paired_code: PairedCode | None) -> str:
    """Publish one ordinary memory merge with exact parents and no cache in its tree.

    The staged tree is validated first against both parents (MIK-R22 rule 8); a refused merge is
    left staged for the agent to repair and continue.
    """

    worktree = Path(side.worktree)
    _require_active_merge(side)
    prepare_memory_cache(worktree)
    _require_sync_git(worktree, ["add", "--", ".gitignore"])
    try:
        close_crossing_history(worktree, (side.preSyncHead, side.sourceCommit))
    except CrossingSyncError as error:
        raise SyncGitProofError(str(error)) from error
    refusal = memory_commit_refusal(
        memory_repository=worktree,
        candidate_tree=_require_sync_git(worktree, ["write-tree"]),
        bases=(side.preSyncHead, side.sourceCommit),
        paired_code=paired_code,
    )
    if refusal is not None:
        raise SyncKnowledgeValidationError(refusal)
    _require_sync_git(worktree, ["commit", "--no-edit"])
    result_head = head_commit(worktree)
    if not exact_created_head(side, result_head):
        raise SyncGitProofError("memory merge commit does not have the pinned parents")
    _refresh_memory_cache(side)
    return result_head


def continue_side_merge(side: SyncSideRecord, *, paired_code: PairedCode | None = None) -> str:
    """Validate a staged agent resolution and commit the exact retained merge."""

    worktree = Path(side.worktree)
    require_side_checkout(side)
    if merge_head(worktree) is None:
        if not exact_created_head(side, head_commit(worktree)):
            raise SyncGitProofError(
                f"{side.side} merge state disappeared without an operation-owned commit"
            )
        _refresh_memory_cache(side)
        return head_commit(worktree)
    _require_active_merge(side)
    validate_staged_resolution(side)
    if side.side == "memory":
        _remove_memory_cache_from_index(side)
        return _finish_staged_memory_merge(side, paired_code)
    _require_sync_git(worktree, ["commit", "--no-edit"])
    result_head = head_commit(worktree)
    if not exact_created_head(side, result_head):
        raise SyncGitProofError(f"{side.side} merge commit does not have the pinned parents")
    return result_head


def validate_staged_resolution(side: SyncSideRecord) -> None:
    """Read-only proof of the exact retained content merge, excluding the memory cache."""

    worktree = Path(side.worktree)
    require_side_checkout(side)
    _require_active_merge(side)
    conflicts = content_conflicts(side)
    if conflicts:
        raise SyncGitProofError(
            f"{side.side} resolution still has unmerged paths: {', '.join(conflicts[:30])}"
        )
    unstaged = run_git(worktree, ["diff", "--quiet", *_content_pathspec(side)])
    if unstaged.returncode != 0:
        raise SyncGitProofError(f"{side.side} resolution has unstaged changes")
    checked = run_git(worktree, ["diff", "--cached", "--check", *_content_pathspec(side)])
    if checked.returncode != 0:
        raise SyncGitProofError(
            checked.stdout.strip() or checked.stderr.strip() or "staged resolution is invalid"
        )


def _abort_failure(
    side: SyncSideRecord, aborted: subprocess.CompletedProcess[str]
) -> SyncGitProofError:
    """The refusal of a merge abort that did not restore the head, naming Git's own reason."""

    detail = (aborted.stderr or aborted.stdout).strip()
    suffix = f": {detail}" if detail else ""
    return SyncGitProofError(f"{side.side} exact merge abort did not restore its head{suffix}")


def rollback_side(side: SyncSideRecord) -> None:
    """Restore one side only when live history proves the exact sync-owned delta."""

    ensure_temporary_worktree(side)
    worktree = Path(side.worktree)
    current = head_commit(worktree)
    current_merge = merge_head(worktree)
    if current_merge is not None:
        if current != side.preSyncHead or current_merge != side.sourceCommit:
            raise SyncGitProofError(f"{side.side} active merge is outside sync authority")
        # The memory merge removed a tracked cache from the index (_continue_memory_merge); put the
        # derived path back to HEAD first, or `git merge --abort` meets it as an untracked file and
        # refuses (L37: cancel after a memory conflict on a line that still tracks memory.md).
        discard_memory_cache_changes(side)
        aborted = run_git(worktree, ["merge", "--abort"])
        if aborted.returncode != 0 or head_commit(worktree) != side.preSyncHead:
            raise _abort_failure(side, aborted)
    elif current != side.preSyncHead:
        if not exact_created_head(side, current):
            raise SyncGitProofError(
                f"{side.side} has later or unrelated commits; automatic rollback is unsafe"
            )
        if git_status(side):
            raise SyncGitProofError(f"{side.side} has post-sync work; automatic rollback is unsafe")
        discard_memory_cache_changes(side)
        reset = run_git(worktree, ["reset", "--hard", side.preSyncHead])
        if reset.returncode != 0:
            raise SyncGitProofError(reset.stderr.strip() or f"could not restore {side.side} head")
    if head_commit(worktree) != side.preSyncHead:
        raise SyncGitProofError(f"{side.side} rollback did not reach its pinned pre-sync head")
    if git_status(side):
        raise SyncGitProofError(
            f"{side.side} branch is restored but post-sync work remains for manual repair"
        )
