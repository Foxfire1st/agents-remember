"""Exact Git mutations and proofs for resumable code and memory-content sync.

The memory side excludes its root memory.md cache from candidate, conflict, and merge-tree
identity. Code-side files keep ordinary Git semantics, including a file named memory.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.worktrees.modules.git import head_commit
from agents_remember.worktrees.sync_transaction_git._base import (
    CancelTreeAssessment,
    SyncGitProofError,
)
from agents_remember.worktrees.sync_transaction_git._proof import (
    _ReapplyMerge,
    _renamed_match,
    expected_reapplied_content,
    parked_path_content,
    path_fingerprint,
)
from agents_remember.worktrees.sync_transaction_git._read import (
    _content_pathspec,
    _is_untracked,
    _parked_deleted,
    _parked_raw_content,
    _spec_content,
    merge_head,
    renamed_parked_paths,
    stash_entry_live,
    stashed_parked_paths,
    worktree_dirty_paths,
    worktree_entry_content,
)
from agents_remember.worktrees.sync_transaction_git._stash import (
    discard_memory_cache_changes,
    require_side_checkout,
)
from agents_remember.worktrees.sync_transaction_state import SyncSideRecord


def assess_stash_tree(side: SyncSideRecord, carried_head: str | None) -> CancelTreeAssessment:
    """Decide whether a cancel may discard one side's tree before rolling back.

    ``carried_head`` is the head the reapply ran onto, or ``None`` where the head
    never moved and the reference is the parked content itself. Dirty paths the
    live recorded stash does not hold, parked paths whose worktree bytes differ
    from the reference, parked paths whose index bytes differ from the head (or
    from identical parked bytes), and uncomputable paths whose bytes differ from
    the parked content are later work: the cancel saves them first (MIK-R61 rule
    12), never refuses them.
    A rename's new name is never later work: the worktree holding the reapply's
    merged bytes there is the transaction's own, and anything else staged or held
    there is unproven — saved and named, never refused (MIK-R61 rule 13).
    """

    if side.wipStash and not stash_entry_live(side):
        raise SyncGitProofError(
            f"{side.side} recorded stash {side.wipStash} is missing; "
            "cancel cannot prove tree ownership"
        )
    dirty = set(worktree_dirty_paths(side))
    parked = set(stashed_parked_paths(side)) if side.wipStash else set()
    renames = renamed_parked_paths(side, carried_head) if carried_head is not None else {}
    reverse = {new: old for old, new in renames.items()}
    later: list[str] = []
    unproven: list[str] = []
    owned_untracked: list[str] = []
    merge = _ReapplyMerge(side, carried_head) if carried_head is not None else None
    view = _StashTreeView(
        parked=frozenset(parked),
        reverse=reverse,
        carried_head=carried_head,
        merge=merge,
    )
    for path in sorted(dirty):
        outcome = _classify_stash_dirty_path(side, path, view)
        if outcome == "later":
            later.append(path)
        elif outcome == "unproven":
            unproven.append(path)
        elif outcome == "owned-untracked":
            owned_untracked.append(path)
        # "owned" needs no list: it stays out of later work, and needs_discard reads the rest.
    for path in _index_later_paths(side, carried_head, parked):
        if path not in later:
            later.append(path)
    for path in _index_renamed_unproven(side, carried_head, renames, merge):
        if path not in unproven:
            unproven.append(path)
    later_sorted = sorted(later)
    return CancelTreeAssessment(
        later_work=tuple(later_sorted),
        owned_untracked=tuple(owned_untracked),
        unproven_paths=tuple(sorted(unproven)),
        needs_discard=bool(dirty - set(later_sorted)),
        merge_fault=merge.fault if merge is not None else "",
    )


def _assess_unretained(
    side: SyncSideRecord, carried_head: str | None, dirty: set[str]
) -> CancelTreeAssessment:
    """Assess a retained state whose crash window left no reference map.

    With no active merge and a live stash the stash proof decides: a tree that
    holds exactly the parked bytes is the transaction's own and cancels, anything
    else is later work. Everywhere else — an active merge, a missing stash —
    nothing shows the bytes to be the transaction's own, so every dirty path is
    unproven, never later work.
    """

    if merge_head(Path(side.worktree)) is None and side.wipStash and stash_entry_live(side):
        return assess_stash_tree(side, carried_head)
    return CancelTreeAssessment(unproven_paths=tuple(sorted(dirty)))


def assess_retained_tree(side: SyncSideRecord, carried_head: str | None) -> CancelTreeAssessment:
    """Decide whether a cancel may discard a retained merge or reapply state.

    Every dirty path must fingerprint-match the operation-created state captured
    at retain time: merge output, conflict markers and stages included. A
    fingerprint mismatch falls back to the stash's own ownership proof only
    where the retained side carries no active merge: the live stash enumerates
    the owned paths, and a mismatched path whose worktree and index bytes the
    stash still owns is a crash-stale fingerprint, not later work. Paths no
    Paths no reference shows to be the transaction's own — an unverified crash
    window, a rename's new name holding later work (MIK-R61 rule 13), or a recorded
    stash that is gone — are unproven, never later work:
    discarding them could destroy the only copy. The one exception is a retained
    reapply with no reference map, no active merge and a live stash: there the
    stash proof itself decides, exactly as for an unretained tree. Anything else
    is later work and joins the save-first set before the cancel clears it.
    """

    dirty = set(worktree_dirty_paths(side))
    retained = side.retainedTreeDigests
    if not retained:
        return _assess_unretained(side, carried_head, dirty)
    if side.wipStash and not stash_entry_live(side):
        return CancelTreeAssessment(unproven_paths=tuple(sorted(dirty)))
    matched = {
        path
        for path in dirty
        if retained.get(path) is not None and path_fingerprint(side, path) == retained[path]
    }
    mismatched = set(sorted(dirty)) - matched
    fallback_owned: set[str] = set()
    fallback_unproven: set[str] = set()
    merge = _ReapplyMerge(side, carried_head) if carried_head is not None else None
    renames = renamed_parked_paths(side, carried_head) if carried_head is not None else {}
    reverse = {new: old for old, new in renames.items()}
    # A rename's new name is never later work (MIK-R61 rule 13): a mismatched one holds
    # later work only the save can take, with or without an active merge.
    rename_mismatched = {path for path in mismatched if path in reverse}
    if mismatched - rename_mismatched and merge_head(Path(side.worktree)) is None:
        parked = set(stashed_parked_paths(side))
        index_later = set(_index_later_paths(side, carried_head, parked))
        for path in sorted(mismatched - rename_mismatched):
            if (
                path in parked
                and path not in index_later
                and _path_owned_by_stash(side, path, carried_head, merge)
            ):
                fallback_owned.add(path)
    if rename_mismatched and merge_head(Path(side.worktree)) is None:
        for path in sorted(rename_mismatched):
            # A crash-stale fingerprint on a rename's new name: the stash's ownership
            # proof follows the rename (MIK-R61 rule 13); a mismatch there is later
            # work only the save can take, never a refusal.
            if merge is not None and _renamed_match(side, path, merge):
                fallback_owned.add(path)
            else:
                fallback_unproven.add(path)
    else:
        fallback_unproven |= rename_mismatched
    owned = matched | fallback_owned
    owned_untracked = [
        path
        for path in sorted(owned)
        if worktree_entry_content(side, path).kind == "bytes" and _is_untracked(side, path)
    ]
    later_sorted = sorted(dirty - owned - fallback_unproven)
    return CancelTreeAssessment(
        later_work=tuple(later_sorted),
        owned_untracked=tuple(owned_untracked),
        unproven_paths=tuple(sorted(fallback_unproven)),
        needs_discard=bool(owned),
        merge_fault=merge.fault if merge is not None else "",
    )


@dataclass(frozen=True)
class _StashTreeView:
    """What one stash-tree assessment judges every dirty path against."""

    parked: frozenset[str]
    reverse: dict[str, str]
    carried_head: str | None
    merge: _ReapplyMerge | None


def _classify_stash_dirty_path(side: SyncSideRecord, path: str, view: _StashTreeView) -> str:
    """One dirty path's cancel class: "owned", "owned-untracked", "unproven" or "later".

    A rename's new name is never later work: the worktree holding the reapply's merged
    bytes there is the transaction's own, and anything else held there is unproven —
    saved and named, never refused (MIK-R61 rule 13). Staged bytes on a new name are
    judged with the index proof, not here.
    """

    if path not in view.parked:
        if (
            path in view.reverse
            and view.merge is not None
            and _renamed_match(side, path, view.merge)
        ):
            actual = worktree_entry_content(side, path)
            if actual.kind == "bytes" and _is_untracked(side, path):
                return "owned-untracked"
            return "owned"
        return "unproven" if path in view.reverse else "later"
    if _path_owned_by_stash(side, path, view.carried_head, view.merge):
        actual = worktree_entry_content(side, path)
        if actual.kind == "bytes" and _is_untracked(side, path):
            return "owned-untracked"
        return "owned"
    return "later"


def _path_owned_by_stash(
    side: SyncSideRecord, path: str, carried_head: str | None, merge: _ReapplyMerge | None
) -> bool:
    """Whether one dirty parked path's worktree bytes are exactly the reapply's.

    The cancel judges with the proof's own construction: ``merge`` is git's
    merge of the candidate onto the carried head, shared by one assessment.
    """

    if carried_head is None:
        expected = parked_path_content(side, path)
    else:
        expected = expected_reapplied_content(side, path, carried_head, merge)
    if expected.kind != "uncomputable":
        actual = worktree_entry_content(side, path)
        return (actual.kind, actual.content) == (expected.kind, expected.content)
    raw = _parked_raw_content(side, path)
    if raw is None:
        return False
    actual = worktree_entry_content(side, path)
    return (actual.kind, actual.content) == ("bytes", raw)


def _index_later_paths(
    side: SyncSideRecord, carried_head: str | None, parked: set[str]
) -> list[str]:
    """Parked paths whose staged index bytes the operation did not create.

    A clean reapply leaves the index at the head; any staged deviation — later
    content, a staged deletion, foreign unmerged stages — is bytes no admitted
    stash or commit holds. Staged bytes identical to the parked content are owned:
    discarding them loses nothing the reapply does not return.
    """

    head = carried_head if carried_head is not None else side.preSyncHead
    later: list[str] = []
    for path in sorted(parked):
        if side.side == "memory" and path == "memory.md":
            continue
        state, indexed = _index_state(side, path)
        if state == "unmerged":
            later.append(path)
            continue
        head_blob = _spec_content(Path(side.repository), (f"{head}:{path}",))
        if state == "absent":
            if head_blob is None or _parked_deleted(side, path):
                continue
            later.append(path)
            continue
        if head_blob is not None and indexed == head_blob:
            continue
        raw = _parked_raw_content(side, path)
        if raw is not None and indexed == raw:
            continue
        later.append(path)
    return later


def _index_renamed_unproven(
    side: SyncSideRecord,
    carried_head: str | None,
    renames: dict[str, str],
    merge: _ReapplyMerge | None,
) -> list[str]:
    """Rename targets whose staged bytes the reapply does not own (MIK-R61 rule 13).

    The staged proof of ownership covers exactly two states: the index at the carried
    head (a clean reapply leaves the index there) and the reapply's merged bytes at the
    new name. Anything else staged on a rename's new name — later work, also when only
    staged — is saved and named by the cancel, never refused as later work.
    """

    if carried_head is None or merge is None:
        return []
    repository = Path(side.repository)
    unproven: list[str] = []
    for _old, new in sorted(renames.items()):
        if side.side == "memory" and new == "memory.md":
            continue
        state, indexed = _index_state(side, new)
        if state == "unmerged":
            unproven.append(new)
            continue
        head_blob = _spec_content(repository, (f"{carried_head}:{new}",))
        if state == "absent":
            if head_blob is None:
                continue
            unproven.append(new)
            continue
        if head_blob is not None and indexed == head_blob:
            continue
        merged = merge.content(new)
        if merged.kind == "bytes" and indexed == merged.content:
            continue
        unproven.append(new)
    return unproven


def _index_state(side: SyncSideRecord, path: str) -> tuple[str, bytes]:
    """One path's staged state: ("blob", bytes), ("absent", b"") or ("unmerged", b"").

    The staged bytes are the blob's own: a text pipe would turn every carriage
    return into a line feed, and a staged picture would never equal its stash.
    """

    worktree = Path(side.worktree)
    listed = run_git(worktree, ["ls-files", "-s", "--", path])
    if listed.returncode != 0:
        raise SyncGitProofError(f"could not read the {side.side} index state of {path}")
    if not listed.stdout.strip():
        return ("absent", b"")
    stages = {line.split()[2] for line in listed.stdout.splitlines()}
    if stages != {"0"}:
        return ("unmerged", b"")
    try:
        staged = _spec_content(worktree, (f":0:{path}",))
    except GitPreparationError:
        staged = None
    if staged is None:
        raise SyncGitProofError(f"could not read the {side.side} staged bytes of {path}")
    return ("blob", staged)


class CancelSaveError(RuntimeError):
    """The cancel could not save the unattributable state: names the unwritten path."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"could not write {path}: {reason}")
        self.path = path
        self.reason = reason


@dataclass(frozen=True)
class SavedUnprovenState:
    """What a cancel saved before discarding bytes it could not attribute (MIK-R61 rule 12).

    ``files`` are ``(path, sha256)`` pairs for every file written: the staged patch, the
    unstaged patch, the path list, and each mirrored untracked file. ``untracked`` names
    the mirrored untracked paths the discard step cleans.
    """

    side: str
    directory: str
    files: tuple[tuple[str, str], ...] = ()
    untracked: tuple[str, ...] = ()


def save_unproven_state(
    scratch_root: Path,
    generation: int,
    sides: list[SyncSideRecord],
    assessments: dict[str, CancelTreeAssessment],
) -> dict[str, SavedUnprovenState]:
    """Save what the worktrees hold beyond the sync's own record (MIK-R61 rule 12).

    Where a merge is still in progress, the cancel first saves what the worktree holds
    beyond the sync's own record: a binary patch of the staged state and one of the
    unstaged state against the head, and the list of the paths, as files in the leaf's
    scratch folder. Untracked files no patch carries are mirrored byte for byte. Nothing
    is discarded that was not saved first: a write failure raises before anything else
    is touched.
    """

    saved: dict[str, SavedUnprovenState] = {}
    for side in sides:
        assessment = assessments.get(side.side)
        if assessment is None or not assessment.unproven_paths:
            continue
        saved[side.side] = _save_unproven_side(scratch_root, generation, side, assessment)
    return saved


def read_saved_unproven_state(
    scratch_root: Path, generation: int, sides: list[SyncSideRecord]
) -> dict[str, SavedUnprovenState]:
    """Reopen completed save manifests after a death between discard and the journal write.

    These are evidence files owned and reclaimed with the enclosure's scratch,
    not a second transaction authority. Reopen only this generation's two side
    folders and verify the named bytes before reporting their digests.
    """
    saved: dict[str, SavedUnprovenState] = {}
    for side in sides:
        directory = scratch_root / f"sync-cancel-generation-{generation}" / side.side
        manifests = [directory / "saved-state.json", *directory.glob("saved-*/saved-state.json")]
        files: list[tuple[str, str]] = []
        for manifest in manifests:
            if not manifest.exists():
                continue
            try:
                data = json.loads(manifest.read_bytes())
                if data["schema"] != "ar-sync-cancel-save/v1" or data["side"] != side.side:
                    raise ValueError("save manifest identity differs")
                for name, digest in data["files"]:
                    path = Path(name)
                    if not path.resolve().is_relative_to(directory.resolve()):
                        raise ValueError("saved path leaves its generation")
                    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                        raise ValueError("saved bytes differ from their manifest")
                    files.append((name, digest))
                files.append(
                    (manifest.as_posix(), hashlib.sha256(manifest.read_bytes()).hexdigest())
                )
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise CancelSaveError(manifest.as_posix(), str(error)) from error
        if files:
            saved[side.side] = SavedUnprovenState(side.side, directory.as_posix(), tuple(files))
    return saved


def _save_unproven_side(
    scratch_root: Path,
    generation: int,
    side: SyncSideRecord,
    assessment: CancelTreeAssessment,
) -> SavedUnprovenState:
    worktree = Path(side.worktree)
    directory = scratch_root / f"sync-cancel-generation-{generation}" / side.side
    if (directory / "saved-state.json").exists():
        # A completed save may already be the only copy of discarded owner work.
        # New owner work after a crash earns a new snapshot, never an overwrite.
        directory = directory / f"saved-{uuid.uuid4().hex}"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise CancelSaveError(directory.as_posix(), type(error).__name__) from error
    paths = tuple(sorted(assessment.unproven_paths))
    files = [
        _save_diff(
            directory / "staged.patch",
            run_git(worktree, ["diff", "--cached", "--binary", "HEAD", *_content_pathspec(side)]),
        ),
        _save_diff(
            directory / "unstaged.patch",
            run_git(worktree, ["diff", "--binary", *_content_pathspec(side)]),
        ),
    ]
    untracked: list[str] = []
    for path in paths:
        if _is_untracked(side, path):
            untracked.append(path)
            files.append(_mirror_untracked(worktree, directory / "untracked", path))
    files.append(_save_paths(directory / "paths.txt", paths))
    manifest = directory / "saved-state.json"
    identity = {"schema": "ar-sync-cancel-save/v1", "side": side.side, "files": files}
    files.append((manifest.as_posix(), _save_bytes(manifest, json.dumps(identity).encode())))
    return SavedUnprovenState(
        side=side.side,
        directory=directory.as_posix(),
        files=tuple(files),
        untracked=tuple(untracked),
    )


def _save_diff(path: Path, result: subprocess.CompletedProcess[str]) -> tuple[str, str]:
    """Write one binary patch file; return its ``(path, sha256)`` identity."""

    if result.returncode != 0:
        raise CancelSaveError(
            path.as_posix(),
            result.stderr.strip() or result.stdout.strip() or "git diff failed",
        )
    return (path.as_posix(), _save_bytes(path, result.stdout.encode("utf-8", "surrogateescape")))


def _save_paths(path: Path, paths: tuple[str, ...]) -> tuple[str, str]:
    """Write the unproven path list; return its ``(path, sha256)`` identity."""

    return (path.as_posix(), _save_bytes(path, "".join(f"{entry}\n" for entry in paths).encode()))


def _mirror_untracked(worktree: Path, root: Path, path: str) -> tuple[str, str]:
    """Mirror one untracked file byte for byte; return its ``(path, sha256)`` identity."""

    source = worktree / path
    target = root / path
    try:
        if source.is_symlink():
            payload = os.fsencode(os.readlink(source))
        elif source.is_file():
            payload = source.read_bytes()
        else:
            raise CancelSaveError(
                target.as_posix(), f"unproven path {path!r} is not a file or symlink"
            )
    except OSError as error:
        raise CancelSaveError(target.as_posix(), type(error).__name__) from error
    return (target.as_posix(), _save_bytes(target, payload))


def _save_bytes(path: Path, payload: bytes) -> str:
    """Write one save file atomically; return its SHA-256 hex digest."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, payload)
    except OSError as error:
        raise CancelSaveError(path.as_posix(), type(error).__name__) from error
    return hashlib.sha256(payload).hexdigest()


def discard_saved_unproven(side: SyncSideRecord, untracked: tuple[str, ...]) -> None:
    """Clear saved unattributable state so the rollback can prove its head (MIK-R61 rule 12).

    Only what the save wrote is discarded: the merge is ended, the head is reset to the
    pinned pre-sync head, and exactly the mirrored untracked paths are cleaned. The
    rollback that follows then proves the restored head from the repository.
    """

    worktree = Path(side.worktree)
    require_side_checkout(side)
    active = merge_head(worktree)
    if active is not None:
        if head_commit(worktree) != side.preSyncHead or active != side.sourceCommit:
            raise SyncGitProofError(
                f"{side.side} active merge is outside sync authority; the saved state is "
                "kept and nothing was discarded"
            )
        # The memory merge removed a tracked cache from the index; put the derived path
        # back first, or the abort meets it as an untracked file and refuses — the same
        # order the rollback uses. The save already holds every byte this clears.
        discard_memory_cache_changes(side)
        aborted = run_git(worktree, ["merge", "--abort"])
        if aborted.returncode != 0 or head_commit(worktree) != side.preSyncHead:
            detail = (aborted.stderr or aborted.stdout).strip()
            suffix = f": {detail}" if detail else ""
            raise SyncGitProofError(
                f"{side.side} exact merge abort did not restore its head{suffix}"
            )
    # The saved patches hold the staged and unstaged state, so the head is restored
    # exactly; the rollback that follows then proves it from the repository.
    reset = run_git(worktree, ["reset", "--hard", side.preSyncHead])
    if reset.returncode != 0 or head_commit(worktree) != side.preSyncHead:
        raise SyncGitProofError(reset.stderr.strip() or f"could not restore {side.side} head")
    if untracked:
        cleaned = run_git(worktree, ["clean", "-f", "--", *untracked])
        if cleaned.returncode != 0:
            raise SyncGitProofError(
                cleaned.stderr.strip() or f"could not clear the {side.side} saved paths"
            )
