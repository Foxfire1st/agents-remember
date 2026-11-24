"""The index cache: one SQLite file per loaded build and tree key, rebuilt when absent.

* **Where.** One directory under the coordination runtime, by default
  ``<coordination-root>/runtime/knowledge-index/`` (:func:`default_cache_directory`). A cache
  directory inside any Git working tree is refused, so an index file can never be staged or
  committed in a repository -- the code repository, the memory repository or any other.
* **What.** ``<loaded-source-digest>--<tree-key>.sqlite``, one per build and key.
  A file present for the key, of this index format and
  built for that key, is reused; any other file at that name is rebuilt. A build writes a temporary
  file in the same directory and renames it into place, so a reader never sees a half-built index.
  On a miss, callers in one process share the result of one build per destination. Cold lookups
  take one lock per cache directory and recheck for reuse after entering it. If exclusion is
  unavailable or the lock wait reaches 60 seconds, the build still runs once within its process.
  A process without a loaded source digest never reuses a file; its private namespace is unique
  and its files remain available to readers that reopen the dataset, under the same eviction.
* **Freshness.** A lookup against a working tree recomputes the tree's key first
  (:meth:`KnowledgeIndexCache.for_directory`); the index opened is the one for that key and is
  checked against it on open, so an answer built for different content is never served.
* **Eviction.** After a build, files unused for longer than ``max_age`` are removed, then the oldest
  until the directory fits ``max_bytes``. Reuse refreshes a zero-byte ``.sqlite.used`` marker,
  preserving the index bytes and timestamps for readers. Existing markerless indexes retain
  their recorded mtime until first reuse creates the marker. The file just built or opened is
  never evicted; the byte budget still counts the SQLite files.
  Eviction also prunes temporary files whose builders are known to be gone. If a builder's
  process cannot be checked or another live process has taken its PID, its file stays until
  that process is known to have ended.

SQLite indexes can be rebuilt from their source trees. Keep ``.build.lock`` and the cache directory
in place while any process can use the cache.
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final
from uuid import uuid4

from agents_remember.errors import LockCapabilityError
from agents_remember.kernel.file_lock import exclusive_file_lock
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
from agents_remember.serving.build_info import process_serving_build

# The cache location under the coordination root: ``runtime/knowledge-index`` (a chosen spelling,
# beside the runtime's other untracked state; the coordination root is not a Git working tree).
CACHE_DIRECTORY_PARTS: Final = ("runtime", "knowledge-index")
# Eviction defaults (chosen): a file unused for 14 days goes first, then the oldest files until the
# directory holds at most 1 GiB of SQLite indexes.
DEFAULT_MAX_AGE_SECONDS: Final = 14 * 24 * 60 * 60
DEFAULT_MAX_BYTES: Final = 1024 * 1024 * 1024
_SUFFIX: Final = ".sqlite"
_USAGE_SUFFIX: Final = ".used"
_BUILD_LOCK_RESOURCE: Final = ".build"
_BUILD_LOCK_WAIT_SECONDS: Final = 60
_TEMP_NAME = re.compile(r"\.(?:[0-9a-f]{40}|[0-9a-f]{64})\.([1-9][0-9]*)\.[0-9]+\.tmp")
_builds: dict[Path, Future[None]] = {}
_builds_guard = threading.Lock()
_active_temps: set[Path] = set()
_active_temps_guard = threading.Lock()


def default_cache_directory(coordination_root: Path) -> Path:
    """Return ``<coordination-root>/runtime/knowledge-index``."""

    return coordination_root.joinpath(*CACHE_DIRECTORY_PARTS)


def open_cached_index(directory: Path, key: str) -> KnowledgeIndex | None:
    """Open only this loaded build's existing index, without writing cache bookkeeping."""
    digest = process_serving_build().source_digest
    if digest is None:
        return None
    return _open_cached_index(_cache_path(directory, key, digest), key)


def _cache_path(directory: Path, key: str, namespace: str) -> Path:
    return directory / f"{namespace.removeprefix('sha256:')}--{key}{_SUFFIX}"


def _open_cached_index(path: Path, key: str) -> KnowledgeIndex | None:
    if not path.is_file():
        return None
    try:
        return KnowledgeIndex(path, expected_key=key)
    except IndexMismatchError:
        return None


def _claim_build(destination: Path) -> tuple[Future[None], bool]:
    with _builds_guard:
        completion = _builds.get(destination)
        if completion is not None:
            return completion, False
        pending: Future[None] = Future()
        _builds[destination] = pending
        return pending, True


def _complete_build(
    destination: Path, completion: Future[None], error: BaseException | None
) -> None:
    with _builds_guard:
        del _builds[destination]
        if error is None:
            completion.set_result(None)
        else:
            completion.set_exception(error)


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
        self._build_digest = process_serving_build().source_digest
        self._namespace = (
            self._build_digest if self._build_digest is not None else f"private-{uuid4().hex}"
        )

    def path_for(self, key: str) -> Path:
        return _cache_path(self.directory, key, self._namespace)

    def for_directory(self, directory: Path) -> KnowledgeIndex:
        """Open the index of a working-tree directory's current captured state."""

        return self._lookup(directory_key(directory), partial(directory_snapshot, directory))

    def for_git_tree(self, repository: Path, revision: str) -> KnowledgeIndex:
        """Open the index of the Git tree ``revision`` names, read through Git objects."""

        resolved = run_git(
            repository,
            ["--no-replace-objects", "rev-parse", "--verify", "--quiet", f"{revision}^{{tree}}"],
        )
        if resolved.returncode != 0:
            raise MemoryTreeError(f"{revision!r} names no Git tree in {repository}")
        key = resolved.stdout.strip()
        return self._lookup(key, partial(git_tree_snapshot, repository, key))

    def evict(self, *, keep: Path | None = None) -> list[Path]:
        """Remove unused and excess indexes, and prune abandoned build files."""

        self._prune_usage()
        self._prune_temporary()
        now = time.time()
        files = sorted(
            (path for path in self.directory.glob(f"*{_SUFFIX}") if path != keep),
            key=_last_used,
        )
        removed: list[Path] = []
        for path in list(files):
            if now - _last_used(path) > self.max_age_seconds:
                _remove_index(path)
                removed.append(path)
                files.remove(path)
        total = sum(_size(path) for path in files) + (_size(keep) if keep is not None else 0)
        for path in files:
            if total <= self.max_bytes:
                break
            total -= _size(path)
            _remove_index(path)
            removed.append(path)
        return removed

    # -- internals -------------------------------------------------------------------------------

    def _lookup(self, key: str, capture: Callable[[], MemoryTreeSnapshot]) -> KnowledgeIndex:
        if self._build_digest is None:
            return self._build(capture())
        while True:
            index = self._reuse(key)
            if index is not None:
                return index
            destination = self.path_for(key)
            completion, is_builder = _claim_build(destination)
            if not is_builder:
                completion.result()
                continue
            try:
                result = self._build_cold(key, capture)
            except BaseException as error:
                _complete_build(destination, completion, error)
                raise
            _complete_build(destination, completion, None)
            if isinstance(result, KnowledgeIndex):
                return result
            # A working tree can change between the first key and the byte capture.
            # Its snapshot belongs to a different flight, and other callers keep their own key.
            key = result.key
            capture = partial(_captured_snapshot, result)

    def _build_cold(
        self, key: str, capture: Callable[[], MemoryTreeSnapshot]
    ) -> KnowledgeIndex | MemoryTreeSnapshot:
        with contextlib.ExitStack() as lock:
            # The timeout is a capability-error subclass. Both leave the local flight in charge.
            with contextlib.suppress(LockCapabilityError):
                lock.enter_context(
                    exclusive_file_lock(
                        self.directory / _BUILD_LOCK_RESOURCE,
                        "knowledge index build",
                        deadline=time.monotonic() + _BUILD_LOCK_WAIT_SECONDS,
                    )
                )
            index = self._reuse(key)
            if index is not None:
                return index
            snapshot = capture()
            if snapshot.key != key:
                return snapshot
            return self._build(snapshot)

    def _reuse(self, key: str) -> KnowledgeIndex | None:
        if self._build_digest is None:
            return None
        path = self.path_for(key)
        index = _open_cached_index(path, key)
        if index is None:
            return None
        _touch_usage(path)
        self.last_outcome = CacheOutcome(key=key, reused=True, report=None)
        return index

    def _build(self, snapshot: MemoryTreeSnapshot) -> KnowledgeIndex:
        destination = self.path_for(snapshot.key)
        temporary = self.directory / f".{snapshot.key}.{os.getpid()}.{time.monotonic_ns()}.tmp"
        with _active_temps_guard:
            _active_temps.add(temporary)
        try:
            report = build_index(snapshot, temporary)
            os.replace(temporary, destination)
        finally:
            try:
                _unlink(temporary)
            finally:
                with _active_temps_guard:
                    _active_temps.remove(temporary)
        self.last_outcome = CacheOutcome(key=snapshot.key, reused=False, report=report)
        _touch_usage(destination)
        self.evict(keep=destination)
        return KnowledgeIndex(destination, expected_key=snapshot.key)

    def _prune_usage(self) -> None:
        """A usage hint has no life after its index; deletion is safe for links too."""
        for marker in self.directory.glob(f"*{_SUFFIX}{_USAGE_SUFFIX}"):
            index = marker.with_name(marker.name.removesuffix(_USAGE_SUFFIX))
            if not index.is_file():
                with contextlib.suppress(OSError):
                    _unlink(marker)

    def _prune_temporary(self) -> None:
        for temporary in self.directory.glob(".*.tmp"):
            owner = _TEMP_NAME.fullmatch(temporary.name)
            if owner is not None and _builder_is_gone(temporary, int(owner[1])):
                with contextlib.suppress(OSError):
                    _unlink(temporary)


def _captured_snapshot(snapshot: MemoryTreeSnapshot) -> MemoryTreeSnapshot:
    return snapshot


def _builder_is_gone(temporary: Path, pid: int) -> bool:
    with _active_temps_guard:
        if temporary in _active_temps:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (OSError, OverflowError):
        pass
    return False


def _usage_path(path: Path) -> Path:
    return path.with_name(f"{path.name}{_USAGE_SUFFIX}")


def _touch_usage(path: Path) -> None:
    """Update only our private regular marker; an unavailable hint never fails a read."""
    with contextlib.suppress(OSError):
        descriptor = os.open(
            _usage_path(path),
            os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            0o600,
        )
        try:
            current = os.fstat(descriptor)
            if stat.S_ISREG(current.st_mode) and current.st_nlink == 1:
                os.utime(descriptor, None)
        finally:
            os.close(descriptor)


def _remove_index(path: Path) -> None:
    _unlink(path)
    with contextlib.suppress(OSError):
        _unlink(_usage_path(path))


def _last_used(path: Path) -> float:
    try:
        marker = _usage_path(path).lstat()
        return marker.st_mtime if stat.S_ISREG(marker.st_mode) and marker.st_nlink == 1 else 0.0
    except FileNotFoundError:
        # Runtime indexes created before usage markers stored recency in their own mtime.
        # Read that value only until their first successful reuse creates a marker.
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0
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
