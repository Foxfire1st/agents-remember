"""Exact Git mutations and proofs for resumable code and memory-content sync.

The memory side excludes its root memory.md cache from candidate, conflict, and merge-tree
identity. Code-side files keep ordinary Git semantics, including a file named memory.md.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from agents_remember.kernel.git_command import read_git_blob_bytes, run_git
from agents_remember.worktrees.modules.git import branch_commit, head_commit, is_ancestor
from agents_remember.worktrees.sync_transaction_git._base import (
    _REGULAR_BLOB_MODES,
    ExpectedReappliedContent,
    SyncGitProofError,
)
from agents_remember.worktrees.sync_transaction_state import SyncSideRecord


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


def git_status(side: SyncSideRecord) -> str:
    result = run_git(Path(side.worktree), ["status", "--porcelain", *_content_pathspec(side)])
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or "could not read sync worktree status")
    return result.stdout.strip()


def _content_spec_paths(side_name: str) -> tuple[str, ...]:
    if side_name == "memory":
        return (".", ":!memory.md")
    return (".",)


def _dirty_names(worktree: Path, side_name: str) -> tuple[str, ...]:
    """Every dirty path one worktree holds, untracked included, NUL-parsed."""

    result = run_git(
        worktree, ["status", "--porcelain", "-z", "-uall", *_content_spec_paths(side_name)]
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


def _untracked_names(worktree: Path, side_name: str) -> frozenset[str]:
    """Every untracked path one worktree holds, minus the derived memory cache."""

    result = run_git(worktree, ["status", "--porcelain", "-z", "-uall"])
    if result.returncode != 0:
        raise SyncGitProofError(result.stderr.strip() or "could not read sync worktree status")
    entries = result.stdout.split("\0")
    names: set[str] = set()
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status = entry[:2]
        path = entry[3:]
        if "R" in status or "C" in status:
            index += 1
            continue
        if status == "??" and not (side_name == "memory" and path == "memory.md"):
            names.add(path)
    return frozenset(names)


def _fingerprint_at(worktree: Path, path: str) -> str:
    """One path's state at one worktree: worktree bytes plus full index entries."""

    actual = _entry_content_at(worktree, path)
    if actual.kind == "bytes":
        worktree_part = f"bytes:{hashlib.sha256(actual.content).hexdigest()}"
    else:
        worktree_part = actual.kind
    listed = run_git(worktree, ["ls-files", "-s", "-z", "--", path])
    if listed.returncode != 0:
        raise SyncGitProofError(f"could not read the index state of {path}")
    index_part = hashlib.sha256(listed.stdout.encode("utf-8", "surrogateescape")).hexdigest()
    return f"{worktree_part}:{index_part}"


def worktree_dirty_paths(side: SyncSideRecord) -> tuple[str, ...]:
    """Every dirty path the worktree holds, untracked included.

    NUL separation is the only porcelain form that never quotes a path, so the reported
    paths are exactly the paths git reported.
    """

    return _dirty_names(Path(side.worktree), side.side)


def _entry_content_at(worktree: Path, path: str) -> ExpectedReappliedContent:
    target = worktree / path
    if target.is_symlink():
        return ExpectedReappliedContent("bytes", os.fsencode(os.readlink(target)))
    if target.is_file():
        return ExpectedReappliedContent("bytes", target.read_bytes())
    if not os.path.lexists(target):
        return ExpectedReappliedContent("absent")
    return ExpectedReappliedContent("unreadable")


def worktree_entry_content(side: SyncSideRecord, path: str) -> ExpectedReappliedContent:
    """The live worktree's exact bytes for one path, without following checkouts."""

    return _entry_content_at(Path(side.worktree), path)


def stashed_parked_paths(side: SyncSideRecord) -> tuple[str, ...]:
    """Every path the recorded stash holds: tracked changes plus untracked files."""

    if not side.wipStash:
        return ()
    repository = Path(side.repository)
    tracked = run_git(
        repository, ["diff", "--name-only", "-z", f"{side.wipStash}^1", side.wipStash]
    )
    if tracked.returncode != 0:
        raise SyncGitProofError("could not read the parked worktree WIP paths")
    paths = [entry for entry in tracked.stdout.split("\0") if entry]
    third = run_git(repository, ["rev-parse", "--verify", "--quiet", f"{side.wipStash}^3"])
    if third.returncode == 0:
        listed = run_git(repository, ["ls-tree", "-r", "--name-only", "-z", f"{side.wipStash}^3"])
        if listed.returncode != 0:
            raise SyncGitProofError("could not read the parked worktree untracked paths")
        paths.extend(entry for entry in listed.stdout.split("\0") if entry)
    ordered: list[str] = []
    for path in paths:
        if path not in ordered:
            ordered.append(path)
    return tuple(ordered)


def stash_entry_live(side: SyncSideRecord) -> bool:
    """Whether the recorded stash entry is still on the live stash stack.

    Assessment trusts only a live entry: a dropped stash can leave readable ghost
    objects behind, and retiring authority on those would discard the tree's only copy.
    """

    if not side.wipStash:
        return False
    listed = run_git(Path(side.repository), ["stash", "list", "--format=%H"])
    return listed.returncode == 0 and side.wipStash in listed.stdout.split()


def sample_paths(side: SyncSideRecord) -> tuple[str, ...]:
    """The journaled path sample, minus the derived memory cache.

    It names failure paths only when the full stash inventory is unreadable; it
    never passes a proof.
    """

    return tuple(
        path for path in side.wipPaths if not (side.side == "memory" and path == "memory.md")
    )


def renamed_parked_paths(side: SyncSideRecord, carried_head: str) -> dict[str, str]:
    """Parked paths the carried line renamed, old name to new (MIK-R61 rule 13).

    Read from the carried line itself with git's rename detection, restricted to paths
    the recorded stash holds: a parked edit the line renamed is reapplied by git under
    the new name, and every proof of the restored work follows it there.
    """

    if carried_head == side.preSyncHead:
        return {}
    repository = Path(side.repository)
    result = run_git(
        repository,
        ["diff", "--find-renames", "--name-status", "-z", side.preSyncHead, carried_head],
    )
    if result.returncode != 0:
        raise SyncGitProofError(
            result.stderr.strip() or "could not read the carried line's renames"
        )
    parked = set(stashed_parked_paths(side))
    renames: dict[str, str] = {}
    cells = result.stdout.split("\0")
    index = 0
    while index < len(cells):
        status = cells[index]
        index += 1
        if not status:
            continue
        if status[0] in {"R", "C"} and index + 1 < len(cells):
            old, new = cells[index], cells[index + 1]
            index += 2
            if old in parked:
                renames[old] = new
        else:
            index += 1
    return renames


def _parked_raw_content(side: SyncSideRecord, path: str) -> bytes | None:
    """The stashed bytes for one path without any type gate, if the stash holds them."""

    _, content = _parked_path_content(side, path)
    return content


def _parked_deleted(side: SyncSideRecord, path: str) -> bool:
    """Whether the parked candidate deletes a path the pre-sync head holds."""

    repository = Path(side.repository)
    _, parked = _parked_path_content(side, path)
    if parked is not None:
        return False
    return _spec_content(repository, (f"{side.preSyncHead}:{path}",)) is not None


def _parked_path_content(side: SyncSideRecord, path: str) -> tuple[str | None, bytes | None]:
    """The stashed bytes for one path with the rev that held them, if any."""

    repository = Path(side.repository)
    for spec in (f"{side.wipStash}^3:{path}", f"{side.wipStash}:{path}"):
        content = _spec_content(repository, (spec,))
        if content is not None:
            rev, _, _ = spec.rpartition(":")
            return rev, content
    return None, None


def _spec_content(repository: Path, specs: tuple[str, ...]) -> bytes | None:
    """The exact blob bytes of the first resolvable spec, or ``None`` when absent."""

    for spec in specs:
        resolved = run_git(repository, ["rev-parse", "--verify", "--quiet", spec])
        if resolved.returncode == 0:
            return read_git_blob_bytes(repository, resolved.stdout.strip())
    return None


def _rev_mode(repository: Path, rev: str, path: str) -> str | None:
    """The tree mode one rev holds for a path, or ``None`` when it holds none."""

    listed = run_git(repository, ["ls-tree", rev, "--", path])
    if listed.returncode != 0:
        return None
    lines = listed.stdout.splitlines()
    if not lines:
        return None
    return lines[0].split()[0]


def _regular_modes(repository: Path, path: str, holders: list[tuple[str, bool]]) -> bool:
    """Whether every present revision holds a blob ``merge-file`` can reapply."""

    for rev, present in holders:
        if present and _rev_mode(repository, rev, path) not in _REGULAR_BLOB_MODES:
            return False
    return True


def _is_untracked(side: SyncSideRecord, path: str) -> bool:
    """Whether a dirty path is untracked rather than modified."""

    listed = run_git(Path(side.worktree), ["ls-files", "--", path])
    return listed.returncode == 0 and not listed.stdout.strip()


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
    return ["--", *_content_spec_paths(side.side)] if side.side == "memory" else []


def content_conflicts(side: SyncSideRecord) -> tuple[str, ...]:
    """Read unresolved content without assigning authority to a memory-side cache."""

    return tuple(
        path
        for path in unmerged_paths(Path(side.worktree))
        if side.side != "memory" or path != "memory.md"
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
