"""Exact Git mutations and proofs for resumable code and memory-content sync.

The memory side excludes its root memory.md cache from candidate, conflict, and merge-tree
identity. Code-side files keep ordinary Git semantics, including a file named memory.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_cache import prepare_memory_cache, refresh_memory_cache
from agents_remember.models.knowledge.merge import AuthoredReconciliation
from agents_remember.worktrees.knowledge_conflict import (
    RefusedKnowledgeStage,
    settle_knowledge_conflict,
    settle_knowledge_conflicts,
)
from agents_remember.worktrees.modules.git import (
    branch_commit,
    current_branch,
    head_commit,
    is_ancestor,
    repository_identity,
)
from agents_remember.worktrees.sync_transaction_state import SyncSideRecord


class SyncGitProofError(RuntimeError):
    """Live Git state cannot be attributed exactly to the journaled sync."""


@dataclass(frozen=True)
class SideMergeOutcome:
    """What one side's merge attempt reached, with the adapter's reason when it stopped.

    ``state`` is ``completed`` or ``resolution-required``; ``conflicts`` names the paths still
    unmerged; ``message`` is Git's own text for a conflict outside the knowledge adapter. ``refused``
    carries the adapter's explanation for a conflicted knowledge dataset -- the row it refused and
    the action it advertised -- so the caller can journal it and publish it rather than only knowing
    that one path is unresolved.
    """

    state: str
    conflicts: tuple[str, ...] = ()
    message: str = ""
    refused: RefusedKnowledgeStage | None = None


def read_ref(repository: Path, ref: str) -> str | None:
    valid = run_git(repository, ["check-ref-format", ref])
    if valid.returncode != 0:
        raise SyncGitProofError(valid.stderr.strip() or f"invalid sync authority ref {ref!r}")
    result = run_git(
        repository,
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"],
    )
    if result.returncode == 0:
        return result.stdout.strip()
    if result.returncode == 1:
        return None
    raise SyncGitProofError(result.stderr.strip() or f"could not inspect sync authority ref {ref}")


def create_pinned_ref(repository: Path, ref: str, commit: str) -> None:
    current = read_ref(repository, ref)
    if current is not None:
        if current != commit:
            raise SyncGitProofError(f"sync authority ref {ref} pins another commit")
        return
    zeros = "0" * len(commit)
    result = run_git(repository, ["update-ref", ref, commit, zeros])
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or f"could not create {ref}")


def delete_pinned_ref(repository: Path, ref: str, expected: str) -> None:
    current = read_ref(repository, ref)
    if current is None:
        return
    if current != expected:
        raise SyncGitProofError(f"sync authority ref {ref} changed before cleanup")
    result = run_git(repository, ["update-ref", "-d", ref, expected])
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or f"could not delete {ref}")


def ensure_temporary_worktree(side: SyncSideRecord) -> None:
    if not side.temporary:
        require_side_checkout(side)
        return
    repository = Path(side.repository)
    worktree = Path(side.worktree)
    if worktree.exists():
        require_side_checkout(side)
        return
    worktree.parent.mkdir(parents=True, exist_ok=True)
    result = run_git(repository, ["worktree", "add", str(worktree), side.workBranch])
    if result.returncode != 0:
        raise SyncGitProofError(
            result.stderr.strip() or f"could not create temporary {side.side} sync worktree"
        )
    require_side_checkout(side)


def require_side_checkout(side: SyncSideRecord) -> None:
    repository = Path(side.repository)
    worktree = Path(side.worktree)
    if repository_identity(worktree) != repository_identity(repository):
        raise SyncGitProofError(f"{side.side} sync worktree changed repository identity")
    if current_branch(worktree) != side.workBranch:
        raise SyncGitProofError(f"{side.side} sync worktree changed its journaled branch")


def remove_temporary_worktree(side: SyncSideRecord) -> None:
    if not side.temporary:
        return
    repository = Path(side.repository)
    worktree = Path(side.worktree)
    if not worktree.exists():
        return
    require_side_checkout(side)
    if git_status(side):
        raise SyncGitProofError(f"temporary {side.side} sync worktree is not clean")
    discard_memory_cache_changes(side)
    result = run_git(repository, ["worktree", "remove", str(worktree)])
    if result.returncode != 0:
        raise SyncGitProofError(
            result.stderr.strip() or f"could not remove temporary {side.side} sync worktree"
        )


def git_status(side: SyncSideRecord) -> str:
    result = run_git(Path(side.worktree), ["status", "--porcelain", *_content_pathspec(side)])
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or "could not read sync worktree status")
    return result.stdout.strip()


def worktree_dirty_paths(side: SyncSideRecord) -> tuple[str, ...]:
    """Every dirty path the worktree holds, untracked included.

    NUL separation is the only porcelain form that never quotes a path, so the reported
    paths are exactly the paths git reported.
    """

    result = run_git(
        Path(side.worktree), ["status", "--porcelain", "-z", "-uall", *_content_pathspec(side)]
    )
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or "could not read sync worktree status")
    entries = result.stdout.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status = entry[:2]
        paths.append(entry[3:])
        if "R" in status or "C" in status:
            index += 1
    return tuple(paths)


def park_worktree_wip(side: SyncSideRecord, *, message: str) -> str:
    """Stash the exact worktree candidate with its untracked files; return its identity."""

    worktree = Path(side.worktree)
    require_side_checkout(side)
    discard_memory_cache_changes(side)
    result = run_git(
        worktree,
        ["stash", "push", "--include-untracked", "--message", message, *_content_pathspec(side)],
    )
    if result.returncode != 0:
        raise SyncGitProofError(
            result.stderr.strip() or result.stdout.strip() or "git stash push failed"
        )
    if git_status(side):
        raise SyncGitProofError("git stash push left the sync worktree dirty")
    parked = run_git(worktree, ["rev-parse", "--verify", "refs/stash^{commit}"])
    if parked.returncode != 0:
        raise SyncGitProofError("git stash push created no stash entry")
    return parked.stdout.strip()


def apply_parked_wip(side: SyncSideRecord) -> tuple[str, tuple[str, ...]]:
    """Reapply the parked content and retain every genuine content conflict."""

    worktree = Path(side.worktree)
    require_side_checkout(side)
    clean_before = not git_status(side)
    discard_memory_cache_changes(side)
    result = run_git(worktree, ["stash", "apply", "--quiet", side.wipStash])
    raw_conflicts = unmerged_paths(worktree)
    cache_conflict = side.side == "memory" and raw_conflicts == ("memory.md",)
    if side.side == "memory" and "memory.md" in raw_conflicts:
        _remove_memory_cache_from_index(side)
    conflicts = content_conflicts(side)
    if result.returncode == 0 and not conflicts:
        _refresh_memory_cache(side)
        return "applied", ()
    if conflicts:
        return "conflict", conflicts
    if (
        result.returncode == 1
        and cache_conflict
        and clean_before
        and prove_parked_wip_restored(side, head_commit(worktree))
    ):
        discard_memory_cache_changes(side)
        _refresh_memory_cache(side)
        return "applied", ()
    raise SyncGitProofError(
        result.stderr.strip() or result.stdout.strip() or "the parked worktree WIP did not reapply"
    )


def prove_parked_wip_restored(side: SyncSideRecord, carried_head: str) -> bool:
    """Prove the parked candidate is back in the worktree, not silently lost.

    A parked path is restored when the worktree reports it dirty again, or when the
    carried result already holds exactly the parked content (a clean reapply that the
    moved source made identical). Anything else fails the proof.
    """

    dirty = set(worktree_dirty_paths(side))
    for path in side.wipPaths:
        if side.side == "memory" and path == "memory.md":
            continue
        if path in dirty:
            continue
        parked = _revision_blob(side, (f"{side.wipStash}^3:{path}", f"{side.wipStash}:{path}"))
        if parked is not None and parked == _revision_blob(side, (f"{carried_head}:{path}",)):
            continue
        return False
    return True


def discard_conflicted_wip_reapply(side: SyncSideRecord) -> None:
    """Drop a conflicted parked-candidate reapply; the candidate itself stays in its stash."""

    worktree = Path(side.worktree)
    require_side_checkout(side)
    if merge_head(worktree) is not None:
        raise SyncGitProofError(f"{side.side} sync has an active merge outside cancel authority")
    reset = run_git(worktree, ["reset", "--hard"])
    if reset.returncode != 0:
        raise SyncGitProofError(
            reset.stderr.strip() or f"could not clear the {side.side} candidate reapply"
        )


def drop_parked_wip(side: SyncSideRecord) -> bool:
    """Drop exactly the recorded stash entry, and never another one."""

    repository = Path(side.repository)
    listed = run_git(repository, ["stash", "list", "--format=%gd %H"])
    if listed.returncode != 0:
        raise SyncGitProofError(listed.stderr.strip() or "could not list the stash stack")
    for line in listed.stdout.splitlines():
        cells = line.split()
        if len(cells) == 2 and cells[1] == side.wipStash:
            dropped = run_git(repository, ["stash", "drop", cells[0]])
            if dropped.returncode != 0:
                raise SyncGitProofError(
                    dropped.stderr.strip() or "could not drop the parked worktree WIP"
                )
            return True
    return False


def _revision_blob(side: SyncSideRecord, specs: tuple[str, ...]) -> str | None:
    repository = Path(side.repository)
    for spec in specs:
        resolved = run_git(repository, ["rev-parse", "--verify", "--quiet", spec])
        if resolved.returncode == 0:
            return resolved.stdout.strip()
    return None


def merge_head(worktree: Path) -> str | None:
    result = run_git(worktree, ["rev-parse", "--verify", "MERGE_HEAD"])
    return result.stdout.strip() if result.returncode == 0 else None


def unmerged_paths(worktree: Path) -> tuple[str, ...]:
    result = run_git(worktree, ["diff", "--name-only", "--diff-filter=U"])
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or "could not inspect merge conflicts")
    return tuple(path for path in result.stdout.splitlines() if path)


def side_merge_completed(side: SyncSideRecord) -> bool:
    worktree = Path(side.worktree)
    if merge_head(worktree) is not None:
        return False
    current = head_commit(worktree)
    return exact_created_head(side, current)


def _content_pathspec(side: SyncSideRecord) -> list[str]:
    return ["--", ".", ":(top,exclude)memory.md"] if side.side == "memory" else []


def content_conflicts(side: SyncSideRecord) -> tuple[str, ...]:
    """Read unresolved content without assigning authority to a memory-side cache."""

    return tuple(
        path
        for path in unmerged_paths(Path(side.worktree))
        if side.side != "memory" or path != "memory.md"
    )


def discard_memory_cache_changes(side: SyncSideRecord) -> None:
    """Restore only the derived path to Git's pre-operation state; discard no content."""

    if side.side != "memory":
        return
    worktree = Path(side.worktree)
    tracked = _require_sync_git(worktree, ["ls-tree", "--name-only", "HEAD", "--", "memory.md"])
    _require_sync_git(worktree, ["clean", "-fdx", "--", "memory.md"])
    if tracked:
        _require_sync_git(
            worktree, ["restore", "--source=HEAD", "--staged", "--worktree", "--", "memory.md"]
        )
    else:
        _remove_memory_cache_from_index(side)


def _remove_memory_cache_from_index(side: SyncSideRecord) -> None:
    _require_sync_git(
        Path(side.worktree), ["rm", "--cached", "--force", "--ignore-unmatch", "--", "memory.md"]
    )


def _require_sync_git(worktree: Path, args: list[str]) -> str:
    result = run_git(worktree, args)
    if result.returncode != 0:
        raise SyncGitProofError(
            result.stderr.strip() or result.stdout.strip() or "sync Git operation failed"
        )
    return result.stdout.strip()


def _refresh_memory_cache(side: SyncSideRecord) -> None:
    if side.side != "memory":
        return
    worktree = Path(side.worktree)
    if not _require_sync_git(worktree, ["ls-tree", "--name-only", "HEAD", "--", "memory.md"]):
        refresh_memory_cache(worktree)


def _require_active_merge(side: SyncSideRecord) -> None:
    worktree = Path(side.worktree)
    if head_commit(worktree) != side.preSyncHead or merge_head(worktree) != side.sourceCommit:
        raise SyncGitProofError(f"{side.side} active merge is not the pinned sync merge")


def _continue_memory_merge(side: SyncSideRecord) -> SideMergeOutcome:
    """Settle the memory merge: knowledge datasets route, everything else stays the agent's.

    A knowledge database is binary to Git, so an ordinary merge can only declare the whole file
    conflicted and no amount of staging resolves it. Those paths are routed through the merge adapter
    by :mod:`agents_remember.worktrees.knowledge_conflict` -- which republishes the union into the
    worktree and stages it -- so the transaction finishes what Git cannot. Whatever the adapter will
    not decide (a schema disagreement above all) comes back as a content conflict and is still the
    agent's to resolve, so this narrows the agent's work rather than hiding any of it. A merged dataset
    is structurally valid and nothing more; no compatibility verdict is taken here.
    """

    _require_active_merge(side)
    _remove_memory_cache_from_index(side)
    conflicts = content_conflicts(side)
    if conflicts:
        # ``ours`` is the work branch tip the merge started from and ``theirs`` is the source commit
        # being merged in, which is exactly the left/right pair the adapter's request names.
        settlement = settle_knowledge_conflicts(
            Path(side.worktree), conflicts, side.preSyncHead, side.sourceCommit
        )
        if settlement.remaining:
            return SideMergeOutcome(
                state="resolution-required",
                conflicts=settlement.remaining,
                refused=settlement.guidance,
            )
    validate_staged_resolution(side)
    return SideMergeOutcome(
        state="completed", conflicts=(), message=_finish_staged_memory_merge(side)
    )


def reconcile_side_merge(
    side: SyncSideRecord, path: str, reconciliations: Sequence[AuthoredReconciliation]
) -> RefusedKnowledgeStage | None:
    """Author the caller's decision for one retained knowledge conflict, and stage the result.

    This is the supported operation's Git half, and it is deliberately the *same* route the automatic
    pass takes: the three index stages are materialised again, the adapter is asked for the merge with
    the caller's decision for that one conflict, and the settled dataset is republished into the
    worktree and staged. Nothing about the conflict is interpreted here -- which row, which decision
    and whether the decision is expressible at all are the adapter's answers, and the caller has
    already checked the decision against the diagnosis it journaled.

    Returns ``None`` when the path settled and is staged, and the adapter's fresh explanation when it
    did not: a decision that settles the first conflict may reveal a second one, and that second one
    is reported exactly as the first was. The caller finishes the merge afterwards through the
    ordinary continuation, so a reconciled sync is a normal sync with one authored input.
    """

    worktree = Path(side.worktree)
    _require_active_merge(side)
    return settle_knowledge_conflict(
        worktree,
        path,
        side.preSyncHead,
        side.sourceCommit,
        reconciliations=tuple(reconciliations),
    )


def _existing_side_merge(side: SyncSideRecord) -> SideMergeOutcome | None:
    """Resume only the admitted merge or its exact completed output."""
    worktree = Path(side.worktree)
    if merge_head(worktree) is not None:
        _require_active_merge(side)
        if side.side == "memory" and side.plan == "merge":
            return _continue_memory_merge(side)
        return SideMergeOutcome(state="resolution-required", conflicts=unmerged_paths(worktree))
    if side_merge_completed(side):
        return SideMergeOutcome(state="completed", message=head_commit(worktree))
    return None


def start_side_merge(side: SyncSideRecord) -> SideMergeOutcome:
    """Attempt the pinned merge; only genuine content conflicts require resolution."""

    worktree = Path(side.worktree)
    require_side_checkout(side)
    existing = _existing_side_merge(side)
    if existing is not None:
        return existing
    if head_commit(worktree) != side.preSyncHead:
        raise SyncGitProofError(f"{side.side} work branch moved after sync admission")
    if git_status(side):
        raise SyncGitProofError(f"{side.side} sync requires a clean worktree before merging")
    discard_memory_cache_changes(side)
    memory_merge = side.side == "memory" and side.plan == "merge"
    merge_args = ["merge", "--no-edit", side.sourceCommit]
    if memory_merge:
        merge_args.insert(1, "--no-commit")
    result = run_git(worktree, merge_args)
    if result.returncode == 0:
        if memory_merge:
            return _continue_memory_merge(side)
        result_head = head_commit(worktree)
        if not exact_created_head(side, result_head):
            raise SyncGitProofError(f"{side.side} merge did not create the exact admitted head")
        _refresh_memory_cache(side)
        return SideMergeOutcome(state="completed", message=result_head)
    conflicts = unmerged_paths(worktree)
    if result.returncode == 1 and merge_head(worktree) == side.sourceCommit and conflicts:
        if memory_merge:
            return _continue_memory_merge(side)
        return SideMergeOutcome(
            state="resolution-required",
            conflicts=conflicts,
            message=(result.stderr or result.stdout).strip(),
        )
    raise SyncGitProofError(
        (result.stderr or result.stdout).strip() or f"{side.side} source merge failed"
    )


def _finish_staged_memory_merge(side: SyncSideRecord) -> str:
    """Publish one ordinary memory merge with exact parents and no cache in its tree."""

    worktree = Path(side.worktree)
    _require_active_merge(side)
    prepare_memory_cache(worktree)
    _require_sync_git(worktree, ["add", "--", ".gitignore"])
    _require_sync_git(worktree, ["commit", "--no-edit"])
    result_head = head_commit(worktree)
    if not exact_created_head(side, result_head):
        raise SyncGitProofError("memory merge commit does not have the pinned parents")
    _refresh_memory_cache(side)
    return result_head


def continue_side_merge(side: SyncSideRecord) -> str:
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
        return _finish_staged_memory_merge(side)
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


def rollback_side(side: SyncSideRecord) -> None:
    """Restore one side only when live history proves the exact sync-owned delta."""

    ensure_temporary_worktree(side)
    worktree = Path(side.worktree)
    current = head_commit(worktree)
    current_merge = merge_head(worktree)
    if current_merge is not None:
        if current != side.preSyncHead or current_merge != side.sourceCommit:
            raise SyncGitProofError(f"{side.side} active merge is outside sync authority")
        aborted = run_git(worktree, ["merge", "--abort"])
        if aborted.returncode != 0 or head_commit(worktree) != side.preSyncHead:
            raise SyncGitProofError(f"{side.side} exact merge abort did not restore its head")
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


def exact_created_head(side: SyncSideRecord, current: str) -> bool:
    """Prove the only two heads the admitted merge can create."""
    if current == side.sourceCommit and is_ancestor(
        Path(side.repository), side.preSyncHead, side.sourceCommit
    ):
        return True
    parents = run_git(Path(side.repository), ["rev-list", "--parents", "-n", "1", current])
    cells = parents.stdout.split()
    return parents.returncode == 0 and cells[1:] == [side.preSyncHead, side.sourceCommit]


def side_branch_head(side: SyncSideRecord) -> str:
    return branch_commit(Path(side.repository), side.workBranch)
