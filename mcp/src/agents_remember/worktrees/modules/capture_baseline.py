"""Request-scoped capture baselines and worktree snapshot proof."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.worktrees.modules.capture_index import _IndexCopyUnusable, _real_index


@dataclass(frozen=True)
class WorktreeCaptureBaseline:
    """A tree and the same index/file snapshot observed on both sides of its capture.

    A captured HEAD is checked before reuse when the capture depends on it. Other identity
    fields (including the code endpoint and base commit) remain independently checked by
    the identity consumer. No snapshot taken after capture alone authorizes reuse.
    """

    tree: str
    index_stat: tuple[int, int, int]
    entries: tuple[tuple[str, ...], ...]
    head: str | None = None


_CAPTURE_BASELINES: ContextVar[dict[str, WorktreeCaptureBaseline] | None] = ContextVar(
    "worktree_capture_baselines", default=None
)


@contextmanager
def memo_capture_baselines() -> Iterator[None]:
    """Within the block, one worktree's capture baseline is remembered for its recheck.

    The recheck a review runs before publication (ICR-R01) re-derives the candidate to prove the
    worktree did not move while the review was composed. When the baseline remembered here proves
    nothing moved -- the same index file, the same file stats -- the re-derivation
    is skipped and the remembered tree stands; any doubt re-derives, exactly as without the
    scope (MIK-R81 rule 7, F17). Scopes nest like the object-lookup scope. The scope lives and
    dies with the read; nothing outside it ever reads a baseline.
    """

    if _CAPTURE_BASELINES.get() is not None:
        yield
        return
    token = _CAPTURE_BASELINES.set({})
    try:
        yield
    finally:
        _CAPTURE_BASELINES.reset(token)


def begin_capture_baseline(repo: Path) -> WorktreeCaptureBaseline | None:
    """Observe inputs before capture; invalidate any earlier proof for this worktree."""

    scope = _CAPTURE_BASELINES.get()
    if scope is None:
        return None
    scope.pop(repo.as_posix(), None)
    return _snapshot_baseline(repo, tree="")


def note_capture_baseline(
    repo: Path, *, tree: str, before: WorktreeCaptureBaseline | None, head: str | None = None
) -> None:
    """Remember a tree only when its entire capture was bracketed by equal snapshots."""

    scope = _CAPTURE_BASELINES.get()
    if scope is None or before is None:
        return
    after = _snapshot_baseline(repo, tree="")
    if after == before:
        scope[repo.as_posix()] = replace(before, tree=tree, head=head)


def capture_baseline_unchanged(repo: Path, *, tree: str) -> bool:
    """Whether the worktree provably still captures to ``tree`` (the fast recheck).

    True only when a baseline was remembered for this worktree in this scope, it names this
    tree, the index file's stamp is untouched, and a fresh snapshot matches entry for entry.
    A staged change rewrites the index file; a file change moves a stat; a recorded HEAD
    is read fresh even when the index is untouched. Each change requires re-derivation. Anything unreadable is
    a doubt, never an agreement: ``False``.
    """

    scope = _CAPTURE_BASELINES.get()
    if scope is None:
        return False
    baseline = scope.get(repo.as_posix())
    if baseline is None or baseline.tree != tree:
        return False
    if baseline.head is not None:
        observed = run_git(repo, ["rev-parse", "HEAD"])
        if observed.returncode != 0 or observed.stdout.strip() != baseline.head:
            return False
    fresh = _snapshot_baseline(repo, tree=tree)
    return fresh is not None and replace(fresh, head=baseline.head) == baseline


def _snapshot_baseline(repo: Path, *, tree: str) -> WorktreeCaptureBaseline | None:
    """The baseline for one worktree, or ``None`` when anything behind it cannot be read."""

    try:
        real_index, top = _real_index(repo)
    except (OSError, RuntimeError, _IndexCopyUnusable):
        return None
    try:
        stamp = real_index.stat()
        index_stat = (stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns)
    except OSError:
        return None
    entries = _snapshot_entries(top)
    if entries is None:
        return None
    return WorktreeCaptureBaseline(tree=tree, index_stat=index_stat, entries=entries)


def _snapshot_entries(
    top: Path, *, max_entries: int | None = None
) -> tuple[tuple[str, ...], ...] | None:
    """Every non-``.git`` entry's kind and stat, sorted; ``None`` when the walk fails.

    ``max_entries`` bounds the walk: past it the walk stops with ``None``, so a caller
    that only needs a change signal recomputes instead of walking a huge tree.
    """

    entries: list[tuple[str, ...]] = []
    stack = [top]
    while stack:
        directory = stack.pop()
        try:
            children = list(os.scandir(directory))
        except OSError:
            return None
        for child in children:
            if directory == top and child.name == ".git":
                # The worktree's own Git directory is not captured; nested `.git` files
                # (submodules) are walked like any other entry.
                continue
            try:
                entries.append(_snapshot_entry(top, child, stack))
            except _SnapshotFailed:
                return None
            if max_entries is not None and len(entries) > max_entries:
                return None
    entries.sort()
    return tuple(entries)


class _SnapshotFailed(Exception):
    """A child that cannot be stated: the whole snapshot is unusable."""


def _snapshot_entry(top: Path, child: os.DirEntry[str], stack: list[Path]) -> tuple[str, ...]:
    """One child's snapshot entry; raises :class:`_SnapshotFailed` when unreadable."""
    try:
        info = child.stat(follow_symlinks=False)
    except OSError as error:
        raise _SnapshotFailed from error
    relative = str(Path(child.path).relative_to(top))
    if child.is_symlink():
        try:
            target = os.readlink(child.path)
        except OSError as error:
            raise _SnapshotFailed from error
        return ("link", relative, target)
    if child.is_dir(follow_symlinks=False):
        stack.append(Path(child.path))
        return ("dir", relative, str(info.st_mtime_ns))
    if child.is_file(follow_symlinks=False):
        return (
            "file",
            relative,
            str(info.st_mode & 0o777),
            str(info.st_size),
            str(info.st_mtime_ns),
            str(info.st_ctime_ns),
        )
    return ("other", relative, str(info.st_mode))
