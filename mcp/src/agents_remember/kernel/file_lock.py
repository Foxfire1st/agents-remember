"""Policy-free, reentrant POSIX file exclusion shared by local resource owners.

Callers authorize the resource before entering this primitive. Each outer acquisition
holds the per-resource thread mutex, verifies filesystem exclusion, then holds flock.
The sibling lock path, thread state and capability cache have one owner here.
"""

from __future__ import annotations

import fcntl
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agents_remember.errors import LockCapabilityError


class LockAcquisitionTimeout(LockCapabilityError):
    """The caller's finite exclusion budget expired; the resource was not entered."""


def _acquire_flock(descriptor: int, deadline: float | None) -> None:
    while True:
        try:
            flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if deadline is not None else 0)
            fcntl.flock(descriptor, flags)
            return
        except BlockingIOError:
            remaining = deadline - time.monotonic() if deadline is not None else 0
            if remaining <= 0:
                raise LockAcquisitionTimeout(
                    "the resource lock is busy; retry after its owner finishes"
                ) from None
            time.sleep(min(0.05, remaining))


class _LockDepth(threading.local):
    """Per-thread nesting depth for reentrant :func:`exclusive_file_lock` calls.

    A nested acquisition on the same thread bypasses both locks and remains covered by the outer
    frame. Other threads retain independent depth and must acquire the mutex and ``flock``.
    """

    def __init__(self) -> None:
        self.depth: dict[Path, int] = {}


_lock_depth = _LockDepth()
_verified_lock_paths: set[Path] = set()
_thread_mutexes: dict[Path, threading.RLock] = {}
_thread_mutex_registry = threading.Lock()


def lock_path_for(log_path: Path) -> Path:
    """Return the sibling ``<whole-resource-name>.lock`` path guarding one resource."""
    return log_path.with_name(f"{log_path.name}.lock")


def thread_mutex_for(log_path: Path) -> threading.RLock:
    """Return the one reentrant in-process mutex paired with this log's ``flock``.

    The mutex preserves thread exclusion independently of file-handle reuse. ``RLock`` is required
    because :func:`exclusive_file_lock` permits same-thread nesting through :class:`_LockDepth`; a
    non-reentrant mutex would deadlock before that nesting rule could return. Lock acquisition is
    always mutex first, then ``flock``.
    """
    path = lock_path_for(log_path)
    mutex = _thread_mutexes.get(path)
    if mutex is not None:
        return mutex
    # Concurrent first access must resolve to the same per-log mutex.
    with _thread_mutex_registry:
        return _thread_mutexes.setdefault(path, threading.RLock())


def _verify_lock_capability(path: Path, resource: str, deadline: float | None) -> None:
    """Refuse a filesystem whose ``flock`` does not actually exclude.

    Taken twice from two file descriptions in this one process: POSIX says the second must be
    denied, so a success proves the lock is decorative -- NFS/SMB emulate ``flock`` with
    per-process byte-range locks, and WSL's DrvFs ignores it. Probing the capability is
    stronger than parsing a mount table and works the same on Linux and macOS.
    """
    if path in _verified_lock_paths:
        return
    with path.open("a+b") as first:
        _acquire_flock(first.fileno(), deadline)
        try:
            with path.open("a+b") as second:
                try:
                    fcntl.flock(second.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    _verified_lock_paths.add(path)
                    return
                fcntl.flock(second.fileno(), fcntl.LOCK_UN)
        finally:
            fcntl.flock(first.fileno(), fcntl.LOCK_UN)
    raise LockCapabilityError(
        f"the {resource} lock ({path}) is on a filesystem where flock does not exclude "
        "(NFS, SMB or WSL DrvFs). Shared writers cannot serialize safely through this lock. "
        "Put the lock on a local POSIX filesystem."
    )


@contextmanager
def exclusive_file_lock(
    resource_path: Path, resource: str, *, deadline: float | None = None
) -> Iterator[None]:
    """Hold one mutex and flock across the caller's complete transaction.

    Same-thread nesting shares the outer hold, including exception release. Other
    threads and processes remain excluded. Acquire the mutex before flock; never
    acquire another resource's lock while holding this one.
    """
    path = lock_path_for(resource_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    depth = _lock_depth.depth.get(path, 0)
    if depth:
        _lock_depth.depth[path] = depth + 1
        try:
            yield
        finally:
            _lock_depth.depth[path] = depth
        return
    mutex = thread_mutex_for(resource_path)
    acquired = (
        mutex.acquire()
        if deadline is None
        else mutex.acquire(timeout=max(0, deadline - time.monotonic()))
    )
    if not acquired:
        raise LockAcquisitionTimeout(
            f"the {resource} lock ({path}) is busy; retry after its owner finishes"
        )
    try:
        _verify_lock_capability(path, resource, deadline)
        with path.open("a+b") as handle:
            _acquire_flock(handle.fileno(), deadline)
            _lock_depth.depth[path] = 1
            try:
                yield
            finally:
                _lock_depth.depth.pop(path, None)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        mutex.release()


def lock_held(resource_path: Path) -> bool:
    """Whether this thread holds the resource lock, including nested acquisitions."""
    return bool(_lock_depth.depth.get(lock_path_for(resource_path), 0))
