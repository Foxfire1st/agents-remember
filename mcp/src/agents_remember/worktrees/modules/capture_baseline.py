"""Request-scoped capture baselines and worktree change proof."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.read_cancellation import raise_if_read_cancelled
from agents_remember.worktrees.modules.capture_index import (
    _index_copy_has_trust_flags,
    _index_entries,
    _IndexCopyUnusable,
    _real_index,
)

type _CapturedEntry = tuple[str, int, int, int, int]


@dataclass(frozen=True)
class WorktreeCaptureBaseline:
    """The capture's own entries and the real index that bracketed their capture.

    The private index names every captured path and its recorded file stat. The real index is
    read in process before capture, and its stamp must remain unchanged. Every real-index path
    must have been captured; otherwise an absent tracked path could later reappear unnoticed.
    Git's fresh status supplies the eligible untracked set and HEAD together at recheck. Staged,
    renamed, unmerged or trust-flagged inputs do not establish this proof and are re-derived.
    """

    tree: str
    index_stat: tuple[int, int, int]
    entries: tuple[_CapturedEntry, ...]
    untracked: frozenset[str]
    head: str | None = None
    root: str = ""


_CAPTURE_BASELINES: ContextVar[dict[str, WorktreeCaptureBaseline] | None] = ContextVar(
    "worktree_capture_baselines", default=None
)
_CAPTURE_OBSERVER: ContextVar[Callable[[Path], None] | None] = ContextVar(
    "worktree_capture_observer", default=None
)


@contextmanager
def memo_capture_baselines(*, on_capture: Callable[[Path], None] | None = None) -> Iterator[None]:
    """Within the block, one worktree's capture baseline is remembered for its recheck.

    The recheck a review runs before publication (ICR-R01) re-derives the candidate to prove the
    worktree did not move while the review was composed. When the baseline remembered here proves
    nothing moved -- the same index file, every captured path still carrying the stat the capture
    recorded, every eligible path still present -- the re-derivation is skipped and the
    remembered tree stands; any doubt re-derives, exactly as without the scope (MIK-R81 rule 7,
    F17). Scopes nest like the object-lookup scope. The scope lives and dies with the read;
    nothing outside it ever reads a baseline.
    """

    observer = _CAPTURE_OBSERVER.set(on_capture or _CAPTURE_OBSERVER.get())
    token = _CAPTURE_BASELINES.set({}) if _CAPTURE_BASELINES.get() is None else None
    try:
        yield
    finally:
        if token is not None:
            _CAPTURE_BASELINES.reset(token)
        _CAPTURE_OBSERVER.reset(observer)


def capture_entries(index: Path) -> tuple[_CapturedEntry, ...] | None:
    """The census of one captured private index, for :func:`note_capture_baseline`.

    This is the callback a capture is given (``worktree_candidate_tree(..., captured=...)``): it
    reads the index the tree was written from, in process, and names every path the capture read.
    ``None`` means that index is not one this build can read, and then nothing is remembered.
    """

    return _index_entries(index)


def begin_capture_baseline(
    repo: Path,
) -> tuple[tuple[int, int, int], frozenset[str]] | None:
    """Read the real index before capture; invalidate an earlier proof.

    Trust flags let status hide a tracked path's restoration, so they cannot prove reuse. A
    partial or unfamiliar index also supplies no proof. These inputs retain full re-derivation.
    """
    scope = _CAPTURE_BASELINES.get()
    if scope is None:
        return None
    scope.pop(repo.as_posix(), None)
    return bracket_real_index(repo)


def bracket_real_index(
    repo: Path,
) -> tuple[tuple[int, int, int], frozenset[str]] | None:
    """The real index's stamp and tracked paths before a capture, or ``None`` when no proof.

    The scope-free half of :func:`begin_capture_baseline`, for a capture owner that keeps its
    own baseline beyond one request (:mod:`~agents_remember.worktrees.modules.kept_capture`).
    """
    try:
        index, _top = _real_index(repo)
        before = index.stat()
        if _index_copy_has_trust_flags(index) is not False:
            return None
        entries = _index_entries(index)
        after = index.stat()
    except (OSError, RuntimeError, _IndexCopyUnusable):
        return None
    stamp = (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    if entries is None or stamp != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        return None
    return stamp, frozenset(entry[0] for entry in entries)


def note_capture_baseline(
    repo: Path,
    *,
    tree: str,
    entries: tuple[_CapturedEntry, ...] | None,
    before: tuple[tuple[int, int, int], frozenset[str]] | None = None,
    head: str | None = None,
) -> None:
    """Remember only a complete capture whose real index did not move.

    The eligible untracked set is derived from the capture itself, never an observation taken
    after capture. Missing real-index paths prevent admission: their restoration must not hide
    behind a clean status. Final status also refuses any staged difference from HEAD, because
    the capture seeds its private index from HEAD rather than from staged additions/removals.
    """
    scope = _CAPTURE_BASELINES.get()
    if scope is None:
        return
    baseline = admitted_baseline(repo, tree=tree, entries=entries, before=before, head=head)
    if baseline is None:
        return
    scope[repo.as_posix()] = baseline
    observer = _CAPTURE_OBSERVER.get()
    if observer is not None:
        observer(repo)


def admitted_baseline(
    repo: Path,
    *,
    tree: str,
    entries: tuple[_CapturedEntry, ...] | None,
    before: tuple[tuple[int, int, int], frozenset[str]] | None,
    head: str | None = None,
) -> WorktreeCaptureBaseline | None:
    """The baseline :func:`note_capture_baseline` admits, or ``None``; scope-free."""
    if entries is None or before is None:
        return None
    try:
        real_index, top = _real_index(repo)
        stamp = real_index.stat()
    except (OSError, RuntimeError, _IndexCopyUnusable):
        return None
    index_stamp, tracked = before
    paths = frozenset(entry[0] for entry in entries)
    if index_stamp != (stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns) or not tracked <= paths:
        return None
    return WorktreeCaptureBaseline(
        tree=tree,
        index_stat=index_stamp,
        entries=entries,
        untracked=paths - tracked,
        head=head,
        root=top.as_posix(),
    )


def baseline_still_captures(repo: Path, baseline: WorktreeCaptureBaseline) -> bool:
    """Whether ``baseline``'s worktree provably still captures to its tree (scope-free)."""

    return _still_captures(repo, baseline, head=baseline.head)


def capture_baseline_unchanged(repo: Path, *, tree: str, head: str | None = None) -> bool:
    """Whether the worktree provably still captures to ``tree`` (the fast recheck).

    True requires the same bracketed index, unchanged captured file stats, the same eligible
    untracked names from Git, and any recorded/required HEAD read fresh by that same status.
    Anything unreadable, hidden by trust flags, or staged differently from HEAD is re-derived.
    """

    scope = _CAPTURE_BASELINES.get()
    if scope is None:
        return False
    baseline = scope.get(repo.as_posix())
    if baseline is None or baseline.tree != tree:
        return False
    return _still_captures(repo, baseline, head=head)


def _still_captures(repo: Path, baseline: WorktreeCaptureBaseline, *, head: str | None) -> bool:
    """One native status supplies HEAD and eligible untracked names without ignored walks."""
    if not _index_stamp_unchanged(repo, baseline):
        return False
    result = run_git(
        repo,
        ["status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"],
    )
    if result.returncode:
        return False
    observed = _status_inputs(result.stdout)
    if observed is None:
        return False
    observed_head, untracked = observed
    return (
        all(expected is None or observed_head == expected for expected in (baseline.head, head))
        and untracked == baseline.untracked
        and _entries_unchanged(Path(baseline.root), baseline.entries)
        and _index_stamp_unchanged(repo, baseline)
    )


def _status_inputs(output: str) -> tuple[str, set[str]] | None:
    """Only unstaged changes and eligible untracked paths can certify the capture."""
    observed_head: str | None = None
    untracked: set[str] = set()
    for record in output.split("\0"):
        if record.startswith("# branch.oid "):
            observed_head = record.removeprefix("# branch.oid ")
        elif record.startswith("? "):
            untracked.add(record[2:])
        elif record.startswith("1 "):
            fields = record.split(" ", 8)
            if len(fields) != 9 or fields[1][0] != ".":
                return None
        elif record and not record.startswith("# "):
            # Renames and unmerged entries cannot certify this HEAD-seeded capture.
            return None
    if observed_head is None:
        return None
    return observed_head, untracked


def _index_stamp_unchanged(repo: Path, baseline: WorktreeCaptureBaseline) -> bool:
    """The worktree's own index file: the same bytes-written stamp, under the same top level."""

    try:
        real_index, top = _real_index(repo)
        stamp = real_index.stat()
    except (OSError, RuntimeError, _IndexCopyUnusable):
        return False
    return (
        top.as_posix() == baseline.root
        and (
            stamp.st_size,
            stamp.st_mtime_ns,
            stamp.st_ctime_ns,
        )
        == baseline.index_stat
    )


#: How many captured entries are stat'ed between two looks at the read's cancellation (D585).
_CHECK_EVERY = 256


def _entries_unchanged(top: Path, entries: tuple[_CapturedEntry, ...]) -> bool:
    """Whether every captured path still carries the stat the capture recorded for it.

    The census is the capture's own, so one pass over it proves what a walk of the whole worktree
    used to prove for a file: a path that changed has another size or another time, and a path that
    is gone cannot be read at all. An unreadable path is a doubt.
    """

    for index, (relative, mode, size, mtime_ns, ctime_ns) in enumerate(entries):
        if not index % _CHECK_EVERY:
            raise_if_read_cancelled()  # tens of thousands of entries: a read that left stops here
        try:
            info = os.lstat(top / relative)
        except OSError:
            return False
        if (info.st_mode & 0o170000) != (mode & 0o170000):
            return False
        if info.st_size != size:
            return False
        if info.st_mtime_ns != mtime_ns or info.st_ctime_ns != ctime_ns:
            return False
    return True


@dataclass(frozen=True)
class WorktreeCaptureWitness:
    """An immutable native capture proof retained across one preparation and its build.

    Constructed only from the capture owner's current request scope. It is never deserialized
    from disk: a fresh process must perform its own native capture before keeping this proof.
    """

    repository: Path
    baseline: WorktreeCaptureBaseline

    def current(self) -> bool:
        return _still_captures(self.repository, self.baseline, head=self.baseline.head)


def capture_witness(repository: Path, *, tree: str) -> WorktreeCaptureWitness | None:
    """Export the complete proof for this native capture, never guess one from a tree id."""
    scope = _CAPTURE_BASELINES.get()
    baseline = None if scope is None else scope.get(repository.as_posix())
    if baseline is None or baseline.tree != tree:
        return None
    return WorktreeCaptureWitness(repository, baseline)
