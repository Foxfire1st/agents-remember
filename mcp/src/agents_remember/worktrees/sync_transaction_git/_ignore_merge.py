"""Settle the generated briefs stanza in converted memory ignore conflicts."""

from __future__ import annotations

import re
import stat
import tempfile
from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.git_command import merge_file_bytes, read_git_blobs_bytes
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.kernel.memory_ignore import BRIEFS_IGNORE_TEXT, with_briefs_ignore
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.worktrees.sync_transaction_git._base import (
    _REGULAR_BLOB_MODES,
    SyncGitProofError,
)
from agents_remember.worktrees.sync_transaction_git._read import revision_content
from agents_remember.worktrees.sync_transaction_git._stash import _require_sync_git

_PATH = ".gitignore"
# The writer inserts one separator line ahead of its exact stanza.
_GENERATED = re.compile(rb"(?m)^(?:\n)?" + re.escape(BRIEFS_IGNORE_TEXT.encode("utf-8")))

type _IndexEntry = tuple[str, str]


def settle_generated_briefs_ignore(
    worktree: Path, current_parent: str, incoming_parent: str
) -> None:
    """Merge authored text before restoring its one generated briefs stanza."""
    if not (worktree / LAYOUT_MARKER_PATH).is_file():
        return
    stages = _ignore_stages(worktree)
    if not _pinned_text_stages(worktree, stages, current_parent, incoming_parent):
        return
    path = worktree / _PATH
    original = revision_content(worktree, "AUTO_MERGE", _PATH)
    if not _has_conflict_bytes(path, original):
        return
    try:
        merged = _authored_merge(worktree, stages)
    except GitPreparationError as error:
        raise SyncGitProofError(f"could not merge the authored ignore text: {error}") from error
    if merged is None or stages != _ignore_stages(worktree):
        return
    # Recheck after running the three-way merge. An unresolved index can coexist with owner edits.
    if _has_conflict_bytes(path, original):
        assert original is not None
        _stage_ignore(worktree, path, original, merged)


def _ignore_stages(worktree: Path) -> dict[str, _IndexEntry]:
    listed = _require_sync_git(worktree, ["ls-files", "--unmerged", "--", _PATH])
    stages = {}
    for row in listed.splitlines():
        metadata, _ = row.split("\t", 1)
        mode, oid, stage = metadata.split()
        stages[stage] = (mode, oid)
    return stages


def _pinned_text_stages(
    worktree: Path, stages: dict[str, _IndexEntry], current_parent: str, incoming_parent: str
) -> bool:
    if stages.keys() not in ({"2", "3"}, {"1", "2", "3"}):
        return False
    mode, _ = stages["2"]
    if mode not in _REGULAR_BLOB_MODES or any(entry[0] != mode for entry in stages.values()):
        return False
    for stage, parent in (("2", current_parent), ("3", incoming_parent)):
        entry = _require_sync_git(worktree, ["ls-tree", parent, "--", _PATH])
        mode, oid = stages[stage]
        if entry != f"{mode} blob {oid}\t{_PATH}":
            return False
    return True


def _has_conflict_bytes(path: Path, original: bytes | None) -> bool:
    if original is None or path.is_symlink():
        return False
    try:
        return path.read_bytes() == original
    except FileNotFoundError:
        return False


def _authored_merge(worktree: Path, stages: dict[str, _IndexEntry]) -> bytes | None:
    blobs = read_git_blobs_bytes(worktree, [entry[1] for entry in stages.values()])
    original = {stage: blobs[oid] for stage, (_, oid) in stages.items()}
    authored = {stage: _GENERATED.sub(b"", data) for stage, data in original.items()}
    if authored == original:
        return None
    # The writer terminates the last authored line when appending the stanza.
    for stage, data in authored.items():
        if data and not data.endswith(b"\n"):
            authored[stage] = data + b"\n"
    with tempfile.TemporaryDirectory(prefix="ar-merge-ignore-") as folder:
        ours, base, theirs = (Path(folder) / name for name in ("ours", "base", "theirs"))
        ours.write_bytes(authored["2"])
        base.write_bytes(authored.get("1", b""))
        theirs.write_bytes(authored["3"])
        merged, conflicts = merge_file_bytes(
            worktree, ours, base, theirs, ("ours", "base", "theirs")
        )
    if conflicts:
        return None
    text = merged.decode("utf-8", errors="surrogateescape")
    return with_briefs_ignore(text).encode("utf-8", errors="surrogateescape")


def _stage_ignore(worktree: Path, path: Path, original: bytes, merged: bytes) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    try:
        atomic_write_bytes(path, merged)
        path.chmod(mode)
        _require_sync_git(worktree, ["add", "--", _PATH])
    except (OSError, SyncGitProofError):
        if _has_conflict_bytes(path, merged):
            atomic_write_bytes(path, original)
            path.chmod(mode)
        raise
