"""A worktree's kept capture index: a review read's capture writes only what moved (D569).

A capture builds its tree in a private index (:func:`~.git.worktree_candidate_tree`). Built afresh
from a copy of the worktree's own index, every file Git cannot prove unchanged against that index
is hashed and written again, and ``write-tree`` writes again every tree above it. Git writes an
object it already holds by rewriting that object's file times -- it freshens the loose file, or
the whole pack that holds it -- so each capture moved the storage witnesses of objects nothing
changed, and a review's closure proofs over those objects failed and were made again.

The **kept index** is the previous capture's own private index, kept per worktree under the
coordination root's ``runtime`` directory (never in the worktree or its repository). ``git add -A``
over it hashes only the paths whose file stats moved since that capture, and ``write-tree`` writes
nothing while its cached trees hold. Its tree is the one a fresh capture names: the fresh capture
is ``HEAD``'s entries plus every eligible change; the kept index holds ``HEAD``'s entries plus the
paths an earlier capture added, so after ``add -A`` the only paths it can hold beyond the fresh
capture's are earlier additions that are ignored now, and those are removed before the tree is
written. A kept index made under another ``HEAD`` is not used: the capture is taken afresh into
it. Any failure on the kept index takes the capture afresh, and any failure there takes the full
capture, exactly as before.

**One capture of a worktree at a time.** The kept index is locked for the whole capture, across
threads and processes. Under the lock, while the worktree's status witness still proves the last
capture -- the same real index, every captured path at its recorded stat, the same eligible
untracked names and ``HEAD`` (:func:`~.capture_baseline.baseline_still_captures`) -- the last
tree is the answer and no Git command writes. Only size evicts a held witness.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final

from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.worktrees.modules import capture_index
from agents_remember.worktrees.modules.capture_baseline import (
    WorktreeCaptureBaseline,
    admitted_baseline,
    baseline_still_captures,
    bracket_real_index,
)
from agents_remember.worktrees.modules.git import worktree_candidate_tree

__all__ = ["KEPT_INDEX_DIRECTORY", "kept_candidate_tree"]

KEPT_INDEX_DIRECTORY: Final = ("runtime", "capture-indexes")

#: How many worktrees' last captures this process holds; an evicted one is captured again.
HELD_CAPACITY: Final = 64

_HELD: OrderedDict[str, WorktreeCaptureBaseline] = OrderedDict()
_HELD_GUARD = threading.Lock()


def kept_candidate_tree(
    coordination_root: Path,
    repo: Path,
    *,
    head: str,
    captured: Callable[[Path], None] | None = None,
) -> str:
    """``repo``'s candidate tree, as :func:`~.git.worktree_candidate_tree` names it.

    ``head`` is the commit the caller read before the capture; the caller re-reads it after and
    refuses a capture that spanned a move, as it always did. A held witness names this ``head``,
    and Git's status re-reads it before the witness answers again. ``captured`` is handed the
    private index the tree was written from, as there: the kept index, which is the census of the
    capture that named this tree.
    """

    key = repo.as_posix()
    directory = coordination_root.joinpath(
        *KEPT_INDEX_DIRECTORY, hashlib.sha256(key.encode()).hexdigest()[:32]
    )
    directory.mkdir(parents=True, exist_ok=True)
    index = directory / "index"
    with exclusive_file_lock(directory / "capture", "kept capture index"):
        held = _held(key)
        if held is not None and index.is_file() and baseline_still_captures(repo, held):
            if captured is not None:
                captured(index)
            return held.tree
        _forget(key)
        before = bracket_real_index(repo)
        tree = _kept_tree(repo, directory, head)
        if tree is None:
            return _full_capture(repo, captured)
        entries = capture_index._index_entries(index)
        if captured is not None:
            captured(index)
        baseline = admitted_baseline(repo, tree=tree, entries=entries, before=before, head=head)
        if baseline is not None:
            _hold(key, baseline)
        return tree


def _kept_tree(repo: Path, directory: Path, head: str) -> str | None:
    """The tree from the kept index, else taken afresh into it; ``None`` when neither answers."""

    index = directory / "index"
    made_under = directory / "kept.json"
    if index.is_file() and _kept_head(made_under) == head:
        try:
            return _tree_from_kept_index(repo, index)
        except capture_index._IndexCopyUnusable:
            pass
    made_under.unlink(missing_ok=True)
    try:
        tree = capture_index._tree_from_index_copy(repo, index, ())
    except capture_index._IndexCopyUnusable:
        index.unlink(missing_ok=True)
        return None
    made_under.write_text(json.dumps({"head": head}), encoding="utf-8")
    return tree


def _tree_from_kept_index(repo: Path, index: Path) -> str:
    """``add -A`` over the kept index, without an earlier addition that is ignored now."""

    _real, top = capture_index._real_index(repo)
    capture_index._clear_trust_flags(top, index)
    capture_index._index_step(top, index, ["add", "-A"])
    ignored = _paths(
        capture_index._index_step(top, index, ["ls-files", "-z", "-c", "-i", "--exclude-standard"])
    )
    if ignored:
        added = set(
            _paths(
                capture_index._index_step(
                    top,
                    index,
                    ["diff-index", "--cached", "--name-only", "--diff-filter=A", "-z", "HEAD"],
                )
            )
        )
        gone = [path for path in ignored if path in added]
        if gone:
            capture_index._index_step(
                top,
                index,
                ["update-index", "-z", "--force-remove", "--stdin"],
                paths="".join(f"{path}\0" for path in gone),
            )
    return capture_index._index_step(top, index, ["write-tree"]).strip()


def _full_capture(repo: Path, captured: Callable[[Path], None] | None) -> str:
    """The capture as it is taken without a kept index; nothing is kept."""

    with TemporaryDirectory(prefix=".kept-capture-fallback-") as scratch:
        return worktree_candidate_tree(repo, Path(scratch) / "index", captured=captured)


def _paths(listing: str) -> list[str]:
    return [path for path in listing.split("\0") if path]


def _kept_head(path: Path) -> str | None:
    try:
        made = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    head = made.get("head") if isinstance(made, dict) else None
    return head if isinstance(head, str) else None


def _held(key: str) -> WorktreeCaptureBaseline | None:
    with _HELD_GUARD:
        held = _HELD.get(key)
        if held is not None:
            _HELD.move_to_end(key)
        return held


def _hold(key: str, baseline: WorktreeCaptureBaseline) -> None:
    with _HELD_GUARD:
        _HELD[key] = baseline
        _HELD.move_to_end(key)
        while len(_HELD) > HELD_CAPACITY:
            _HELD.popitem(last=False)


def _forget(key: str) -> None:
    with _HELD_GUARD:
        _HELD.pop(key, None)
