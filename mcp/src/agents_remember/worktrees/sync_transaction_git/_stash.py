"""Exact Git mutations and proofs for resumable code and memory-content sync.

The memory side excludes its root memory.md cache from candidate, conflict, and merge-tree
identity. Code-side files keep ordinary Git semantics, including a file named memory.md.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_cache import refresh_memory_cache
from agents_remember.worktrees.modules.git import (
    current_branch,
    head_commit,
    repository_identity,
)
from agents_remember.worktrees.sync_transaction_git._base import (
    MAX_NAMED_SYNC_PATHS,
    SyncGitProofError,
)
from agents_remember.worktrees.sync_transaction_git._proof import prove_parked_wip_restored
from agents_remember.worktrees.sync_transaction_git._read import (
    _content_pathspec,
    content_conflicts,
    git_status,
    unmerged_paths,
    worktree_dirty_paths,
)
from agents_remember.worktrees.sync_transaction_state import SyncSideRecord


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


def clear_stale_git_locks(worktrees: Sequence[Path]) -> tuple[str, ...]:
    """Remove index locks no live process holds open (MIK-R61 rule 12).

    A sync killed inside a git operation leaves its worktree's ``index.lock``
    behind; git's lock carries no owner PID, so staleness is proved by absence:
    the lock is removed only when no live process holds that exact file open.
    A lock a live process holds (a genuinely running git) is left alone, and
    the platform without ``/proc`` cannot prove staleness, so it removes
    nothing. Answers the removed lock paths for the journal/log. Never raises:
    an unclearable lock stays one and the next git operation names it.
    """
    cleared: list[str] = []
    for worktree in worktrees:
        try:
            if not worktree.is_dir():
                # A series side's temporary worktree does not exist until the
                # sync creates it; there is no lock to clear there yet.
                continue
        except OSError:
            continue
        try:
            gitdir = run_git(worktree, ["rev-parse", "--absolute-git-dir"])
        except OSError:
            continue
        if gitdir.returncode != 0:
            continue
        lock = Path(gitdir.stdout.strip()) / "index.lock"
        try:
            locked = lock.exists()
        except OSError:
            continue
        if not locked:
            continue
        if _lock_held_open(lock):
            continue
        with suppress(OSError):
            lock.unlink()
            cleared.append(lock.as_posix())
    return tuple(cleared)


def _lock_held_open(lock: Path) -> bool:
    """Whether any live process holds ``lock`` open (a genuinely running git)."""
    try:
        wanted = (lock.stat().st_dev, lock.stat().st_ino)
    except OSError:
        return False
    proc = Path("/proc")
    if not proc.is_dir():
        return True
    try:
        entries = list(proc.glob("[0-9]*"))
    except OSError:
        return True
    for entry in entries:
        try:
            int(entry.name)
        except ValueError:
            continue
        try:
            fds = list((entry / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            with suppress(OSError):
                st = os.stat(fd)
                if (st.st_dev, st.st_ino) == wanted:
                    return True
    return False


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


def park_worktree_wip(side: SyncSideRecord, *, message: str) -> str:
    """Stash the exact worktree candidate with its untracked files; return its identity.

    Parking is all or nothing (MIK-R61 rule 7): when the worktree is not clean after the
    stash was taken -- a stashed ignore rule can turn ignored files untracked -- the sync
    applies and drops its own stash before refusing, so the worktree's status and the
    stash list equal those before the call. Where giving the stash back fails, the stash
    is kept, the refusal names it, and nothing else is touched.
    """

    worktree = Path(side.worktree)
    require_side_checkout(side)
    discard_memory_cache_changes(side)
    # The pre-push baseline is captured BEFORE the push: after the push the
    # index no longer holds the stashed staged changes, so a post-push
    # baseline could never prove the giveback.
    before_index = run_git(worktree, ["write-tree"]).stdout.strip()
    before_status = worktree_dirty_paths(side)
    result = run_git(
        worktree,
        ["stash", "push", "--include-untracked", "--message", message, *_content_pathspec(side)],
    )
    if result.returncode != 0:
        raise SyncGitProofError(
            result.stderr.strip() or result.stdout.strip() or "git stash push failed"
        )
    parked = run_git(worktree, ["rev-parse", "--verify", "refs/stash^{commit}"])
    if parked.returncode != 0:
        raise SyncGitProofError("git stash push created no stash entry")
    stash = parked.stdout.strip()
    dirty = worktree_dirty_paths(side)
    if not dirty:
        return stash
    try:
        _pop_own_stash(side, stash, before_index=before_index, before_status=before_status)
    except SyncGitProofError as error:
        raise SyncGitProofError(
            f"git stash push left the {side.side} sync worktree dirty, and giving the "
            f"just-taken stash back failed: {error} The stash is kept as {stash}; nothing "
            "else was touched."
        ) from error
    shown = ", ".join(dirty[:MAX_NAMED_SYNC_PATHS])
    if len(dirty) > MAX_NAMED_SYNC_PATHS:
        shown += f" and {len(dirty) - MAX_NAMED_SYNC_PATHS} more"
    raise SyncGitProofError(
        f"git stash push left the {side.side} sync worktree dirty "
        f"({shown}); the likely cause is a stashed ignore rule. The just-taken stash was "
        "applied and dropped, so the worktree and the stash list are as before the call."
    )


def _pop_own_stash(
    side: SyncSideRecord, stash: str, *, before_index: str, before_status: tuple[str, ...]
) -> None:
    """Apply and drop the just-taken stash entry identified by its commit.

    The apply restores the index partition exactly (``--index``): a staged
    ignore rule comes back staged, not unstaged. The drop happens only after
    the index tree and the worktree status prove equal to the pre-push state;
    otherwise the stash is kept and named, and nothing is dropped.
    """

    worktree = Path(side.worktree)
    listed = run_git(worktree, ["stash", "list", "--format=%gd %H"])
    if listed.returncode != 0:
        raise SyncGitProofError(listed.stderr.strip() or "could not list the stash stack")
    reference: str | None = None
    for line in listed.stdout.splitlines():
        cells = line.split()
        if len(cells) == 2 and cells[1] == stash:
            reference = cells[0]
            break
    if reference is None:
        raise SyncGitProofError(f"the just-taken stash {stash} is not on the stash stack")
    applied = run_git(worktree, ["stash", "apply", "--index", "--quiet", reference])
    if applied.returncode != 0:
        raise SyncGitProofError(
            applied.stderr.strip() or applied.stdout.strip() or "git stash apply --index failed"
        )
    restored_index = run_git(worktree, ["write-tree"]).stdout.strip()
    if restored_index != before_index:
        raise SyncGitProofError(
            f"the just-taken stash {stash} applied but the index tree differs from the "
            "pre-push state; the stash is kept and nothing was dropped"
        )
    if worktree_dirty_paths(side) != before_status:
        raise SyncGitProofError(
            f"the just-taken stash {stash} applied but the worktree status differs from "
            "the pre-push state; the stash is kept and nothing was dropped"
        )
    dropped = run_git(worktree, ["stash", "drop", "--quiet", reference])
    if dropped.returncode != 0:
        raise SyncGitProofError(
            dropped.stderr.strip() or dropped.stdout.strip() or "git stash drop failed"
        )


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


def discard_owned_reapply(side: SyncSideRecord, owned_untracked: tuple[str, ...]) -> None:
    """Drop the transaction-owned reapply; the candidate itself stays in its stash.

    Only call after the save-first step named every later and unproven path: the reset
    discards tracked changes the save holds, and the scoped clean removes exactly the owned
    untracked paths ``reset --hard`` keeps.
    """

    worktree = Path(side.worktree)
    require_side_checkout(side)
    reset = run_git(worktree, ["reset", "--hard"])
    if reset.returncode != 0:
        raise SyncGitProofError(
            reset.stderr.strip() or f"could not clear the {side.side} candidate reapply"
        )
    if owned_untracked:
        cleaned = run_git(worktree, ["clean", "-f", "--", *owned_untracked])
        if cleaned.returncode != 0:
            raise SyncGitProofError(
                cleaned.stderr.strip() or f"could not clear the {side.side} owned untracked paths"
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
