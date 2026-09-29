"""The index cache: one SQLite file per tree key, untracked, rebuilt when absent (MIK-R23 rules 2, 4, 5).

* **Where.** One directory under the coordination runtime, by default
  ``<coordination-root>/runtime/knowledge-index/`` (:func:`default_cache_directory`). A cache
  directory inside any Git working tree is refused, so an index file can never be staged or
  committed in a repository -- the code repository, the memory repository or any other.
* **What.** ``<tree-key>.sqlite``, one per key. A file present for the key, of this index format and
  built for that key, is reused; any other file at that name is rebuilt. A build writes a temporary
  file in the same directory and renames it into place, so a reader never sees a half-built index
  and two concurrent builds of one key both end with a whole file.
* **Freshness.** A lookup against a working tree recomputes the tree's key first
  (:meth:`KnowledgeIndexCache.for_directory`); the index opened is the one for that key and is
  checked against it on open, so an answer built for different content is never served.
* **Eviction.** After a build, files unused for longer than ``max_age`` are removed, then the oldest
  until the directory fits ``max_bytes``. Reuse refreshes a file's time. The file just built or
  opened is never evicted.

Deleting the directory, or any file in it, loses nothing: every index is rebuilt from its tree.
"""

from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge_index.build import BuildReport, build_index
from agents_remember.memory.knowledge_index.query import IndexMismatchError, KnowledgeIndex
from agents_remember.memory.knowledge_index.tree import (
    MemoryTreeError,
    MemoryTreeSnapshot,
    directory_key,
    directory_snapshot,
    git_tree_snapshot,
)

# The cache location under the coordination root: ``runtime/knowledge-index`` (a chosen spelling,
# beside the runtime's other untracked state; the coordination root is not a Git working tree).
CACHE_DIRECTORY_PARTS: Final = ("runtime", "knowledge-index")
# Eviction defaults (chosen): a file unused for 14 days goes first, then the oldest files until the
# directory holds at most 1 GiB. A converted real memory tree indexes to about 2 MB.
DEFAULT_MAX_AGE_SECONDS: Final = 14 * 24 * 60 * 60
DEFAULT_MAX_BYTES: Final = 1024 * 1024 * 1024
_SUFFIX: Final = ".sqlite"


def default_cache_directory(coordination_root: Path) -> Path:
    """Return ``<coordination-root>/runtime/knowledge-index``."""

    return coordination_root.joinpath(*CACHE_DIRECTORY_PARTS)


@dataclass(frozen=True)
class CacheOutcome:
    """How one lookup was served: the key, whether the file was reused, and the build report."""

    key: str
    reused: bool
    report: BuildReport | None


class KnowledgeIndexCache:
    """The cache directory and its policy. Lookups return an opened :class:`KnowledgeIndex`."""

    def __init__(
        self,
        directory: Path,
        *,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        # The location is probed at its nearest existing ancestor *before* anything is created, so
        # a refused cache leaves no directory behind in a working tree.
        existing = directory.resolve()
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        inside = run_git(existing, ["rev-parse", "--is-inside-work-tree"])
        if inside.returncode == 0 and inside.stdout.strip() == "true":
            raise MemoryTreeError(
                f"the index cache {directory} is inside a Git working tree; an index is never "
                "placed where it could be staged or committed"
            )
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory.resolve()
        self.max_age_seconds = max_age_seconds
        self.max_bytes = max_bytes
        self.last_outcome: CacheOutcome | None = None

    def path_for(self, key: str) -> Path:
        return self.directory / f"{key}{_SUFFIX}"

    def for_directory(self, directory: Path) -> KnowledgeIndex:
        """Open the index of a working-tree directory's current captured state."""

        key = directory_key(directory)
        index = self._reuse(key)
        if index is not None:
            return index
        return self._build(directory_snapshot(directory))

    def for_git_tree(self, repository: Path, revision: str) -> KnowledgeIndex:
        """Open the index of the Git tree ``revision`` names, read through Git objects."""

        resolved = run_git(repository, ["rev-parse", "--verify", "--quiet", f"{revision}^{{tree}}"])
        if resolved.returncode != 0:
            raise MemoryTreeError(f"{revision!r} names no Git tree in {repository}")
        index = self._reuse(resolved.stdout.strip())
        if index is not None:
            return index
        return self._build(git_tree_snapshot(repository, revision))

    def evict(self, *, keep: Path | None = None) -> list[Path]:
        """Remove files unused past ``max_age``, then the oldest until the cache fits ``max_bytes``."""

        now = time.time()
        files = sorted(
            (path for path in self.directory.glob(f"*{_SUFFIX}") if path != keep),
            key=_last_used,
        )
        removed: list[Path] = []
        for path in list(files):
            if now - _last_used(path) > self.max_age_seconds:
                _unlink(path)
                removed.append(path)
                files.remove(path)
        total = sum(_size(path) for path in files) + (_size(keep) if keep is not None else 0)
        for path in files:
            if total <= self.max_bytes:
                break
            total -= _size(path)
            _unlink(path)
            removed.append(path)
        return removed

    # -- internals -------------------------------------------------------------------------------

    def _reuse(self, key: str) -> KnowledgeIndex | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        try:
            index = KnowledgeIndex(path, expected_key=key)
        except IndexMismatchError:
            return None
        with contextlib.suppress(OSError):
            os.utime(path)
        self.last_outcome = CacheOutcome(key=key, reused=True, report=None)
        return index

    def _build(self, snapshot: MemoryTreeSnapshot) -> KnowledgeIndex:
        destination = self.path_for(snapshot.key)
        temporary = self.directory / f".{snapshot.key}.{os.getpid()}.{time.monotonic_ns()}.tmp"
        try:
            report = build_index(snapshot, temporary)
            os.replace(temporary, destination)
        finally:
            _unlink(temporary)
        self.last_outcome = CacheOutcome(key=snapshot.key, reused=False, report=report)
        self.evict(keep=destination)
        return KnowledgeIndex(destination, expected_key=snapshot.key)


def _last_used(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _unlink(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
