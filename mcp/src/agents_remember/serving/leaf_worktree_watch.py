"""One process-wide inotify watch over the leaves' worktrees (D569), Linux only.

The review learns that a leaf's worktree changed from the kernel, not by polling it, and every
leaf the process watches shares **one** inotify instance: the per-user instance limit is small
(``max_user_instances``, 128 by default, and other programs hold some) while the watch limit is
large, and each directory costs one watch.

**Which leaves.** A key is watched while it has at least one *reason* -- a seat, an environment,
an open review stream. :meth:`LeafWorktreeWatch.sync` replaces the reasons every non-stream source
gives in one call; :meth:`LeafWorktreeWatch.cover` and :meth:`LeafWorktreeWatch.uncover` change
one; :meth:`LeafWorktreeWatch.subscribe` adds the reason ``open stream`` for as long as the stream
is open. A key's roots (its code and memory worktrees) come from ``roots_of(key)``. Every add and
drop is logged with the key, its reasons, its directory count and the instance's total watches.

**Which directories.** Only the ones Git would show: the parents of every path
``git ls-files -z -co --exclude-standard`` lists in a root, and the root itself, each watched without
recursion, and none that :func:`admitted` refuses (``.git``, ``__pycache__`` ...). A gitignored
tree such as ``node_modules`` costs no watch and raises no event. Two keys may share a directory;
it is watched once and its events reach both.

**Which events.** A file closed after writing, moved in or out, created, deleted or given new
metadata in a watched directory, and a watched directory deleted or moved itself, when its path is
:func:`admitted` (scratch names -- ``.lock``, ``.tmp``, ``~``, ``.swp``, ``.index.json``, ``.#`` --
are not). Each read of the queue calls ``on_change`` once for every key an admitted event reached,
and ``notify(None)`` once for every stream subscribed under whose ``listen_root`` one fell. A
directory created or moved in re-lists its keys' directories and watches the new ones first.

**What is never lost silently.** An overflowed kernel queue (``IN_Q_OVERFLOW``) is logged, calls
``on_overflow`` and notifies every subscriber, once per overflow. A key whose directories cannot all
be watched -- the watch budget is spent (``ENOSPC``), Git cannot list a root, any add fails --
keeps none of the watches it was adding, is logged as uncovered with the reason and reported by
:meth:`LeafWorktreeWatch.uncovered`, and is tried again at the next :meth:`LeafWorktreeWatch.sync`;
a subscribe that cannot cover its key raises :class:`OSError` naming the reason. A reader that
cannot read the instance any more calls every subscriber with the error.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import logging
import os
import select
import struct
import subprocess
import sys
import threading
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git_bytes,
)

__all__ = [
    "IGNORED",
    "STREAM_REASON",
    "WATCH_MASK",
    "LeafWorktreeWatch",
    "WatchBudget",
    "admitted",
    "eligible_directories",
    "process_worktree_watch",
    "read_budget",
    "reset_process_worktree_watch",
    "walked_directories",
]

logger = logging.getLogger(__name__)

IN_ATTRIB: Final = 0x00000004
IN_CLOSE_WRITE: Final = 0x00000008
IN_MOVED_FROM: Final = 0x00000040
IN_MOVED_TO: Final = 0x00000080
IN_CREATE: Final = 0x00000100
IN_DELETE: Final = 0x00000200
IN_DELETE_SELF: Final = 0x00000400
IN_MOVE_SELF: Final = 0x00000800
IN_Q_OVERFLOW: Final = 0x00004000
IN_IGNORED: Final = 0x00008000
IN_ONLYDIR: Final = 0x01000000
IN_ISDIR: Final = 0x40000000

WATCH_MASK: Final = (
    IN_CLOSE_WRITE
    | IN_MOVED_TO
    | IN_MOVED_FROM
    | IN_CREATE
    | IN_DELETE
    | IN_DELETE_SELF
    | IN_MOVE_SELF
    | IN_ATTRIB
)
STREAM_REASON: Final = "open stream"
IGNORED: Final = frozenset({".git", "provider-runtime", "__pycache__", ".cache"})
_SCRATCH_SUFFIXES: Final = (".index.json", ".tmp", ".lock", "~", ".swp")
_SCRATCH_PREFIXES: Final = (".#",)
_EVENT: Final = struct.Struct("iIII")  # struct inotify_event: wd, mask, cookie, name length
_READ_BYTES: Final = 64 * 1024
_PROC: Final = Path("/proc/sys/fs/inotify")


def admitted(path: Path, root: Path) -> bool:
    """Whether ``path`` is under ``root`` and no part of it is ignored or a scratch name."""

    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    return not any(
        part in IGNORED or part.endswith(_SCRATCH_SUFFIXES) or part.startswith(_SCRATCH_PREFIXES)
        for part in parts
    )


@dataclass(frozen=True)
class WatchBudget:
    """The kernel's per-user inotify limits, ``None`` where they cannot be read."""

    max_user_watches: int | None
    max_user_instances: int | None


def read_budget(proc: Path = _PROC) -> WatchBudget:
    def number(name: str) -> int | None:
        try:
            return int((proc / name).read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None

    return WatchBudget(number("max_user_watches"), number("max_user_instances"))


def eligible_directories(root: Path) -> tuple[Path, ...]:
    """The root and every admitted parent of a path ``git ls-files -co --exclude-standard`` lists.

    Raises :class:`OSError` when Git cannot list the root. A directory that no longer exists (a
    tracked file deleted from the worktree) is left out.
    """

    try:
        listed = run_git_bytes(
            root,
            ["ls-files", "-z", "-co", "--exclude-standard"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except subprocess.SubprocessError as error:
        raise OSError(f"git cannot list {root}: {error}") from error
    if listed.returncode != 0:
        detail = listed.stderr.decode("utf-8", "replace").strip()
        if "not a git repository" in detail and root.is_dir():
            return walked_directories(root)
        raise OSError(f"git cannot list {root}: {detail or f'exit {listed.returncode}'}")
    relative: set[str] = {""}
    for entry in listed.stdout.split(b"\0"):
        parts = os.fsdecode(entry).split("/")[:-1] if entry else []
        for depth in range(1, len(parts) + 1):
            relative.add("/".join(parts[:depth]))
    directories = [root / one if one else root for one in sorted(relative)]
    return tuple(one for one in directories if admitted(one, root) and one.is_dir())


def walked_directories(root: Path) -> tuple[Path, ...]:
    """Every admitted, non-symlink directory under a root that is not a Git worktree.

    A memory root is a worktree; a plain directory (a test's, an unconverted one) is watched as the
    connection-owned watch watched it before this module: every directory :func:`admitted` keeps.
    """

    directories = []
    for directory, children, _files in os.walk(root, followlinks=False):
        parent = Path(directory)
        children[:] = [
            name
            for name in children
            if admitted(parent / name, root) and not (parent / name).is_symlink()
        ]
        directories.append(parent)
    return tuple(directories)


class _Inotify:
    """The three libc calls, with errno kept."""

    def __init__(self) -> None:
        if not sys.platform.startswith("linux"):
            raise OSError(errno.ENOSYS, "the leaf worktree watch needs Linux inotify")
        libc = ctypes.CDLL(None, use_errno=True)
        self._init = libc.inotify_init1
        self._init.argtypes = [ctypes.c_int]
        self._init.restype = ctypes.c_int
        self._add = libc.inotify_add_watch
        self._add.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self._add.restype = ctypes.c_int
        self._rm = libc.inotify_rm_watch
        self._rm.argtypes = [ctypes.c_int, ctypes.c_int]
        self._rm.restype = ctypes.c_int

    def init(self) -> int:
        fd = self._init(os.O_NONBLOCK | os.O_CLOEXEC)  # IN_NONBLOCK | IN_CLOEXEC
        if fd < 0:
            number = ctypes.get_errno()
            raise OSError(number, f"inotify_init1: {os.strerror(number)}")
        return fd

    def add(self, fd: int, path: Path, mask: int) -> int:
        wd = self._add(fd, os.fsencode(path), mask)
        if wd < 0:
            number = ctypes.get_errno()
            raise OSError(number, f"inotify_add_watch {path}: {os.strerror(number)}")
        return wd

    def rm(self, fd: int, wd: int) -> None:
        self._rm(fd, wd)  # a watch the kernel already dropped answers EINVAL: nothing to undo


@dataclass(eq=False)
class _Subscriber:
    key: Hashable
    listen_root: Path
    notify: Callable[[Exception | None], None]


@dataclass
class _Maps:
    """Every watch, every key's reasons and subscribers (guarded by the watch's one lock)."""

    wd_of: dict[Path, int] = field(default_factory=dict)
    dirs_of: dict[int, set[Path]] = field(default_factory=dict)
    keys_of: dict[int, set[Hashable]] = field(default_factory=dict)
    wds_of: dict[Hashable, set[int]] = field(default_factory=dict)
    reasons: dict[Hashable, set[str]] = field(default_factory=dict)
    subscribers: dict[Hashable, list[_Subscriber]] = field(default_factory=dict)
    uncovered: dict[Hashable, str] = field(default_factory=dict)


class LeafWorktreeWatch[K: Hashable]:
    """The process's one inotify instance over every covered leaf's eligible worktree directories."""

    def __init__(
        self,
        roots_of: Callable[[K], tuple[Path, ...]] = lambda _key: (),
        on_change: Callable[[K], None] = lambda _key: None,
        on_overflow: Callable[[], None] = lambda: None,
    ) -> None:
        self._roots_of = roots_of
        self._on_change = on_change
        self._on_overflow = on_overflow
        self._lock = threading.RLock()
        self._maps = _Maps()
        self._calls: _Inotify | None = None
        self._fd: int | None = None
        self._wake: tuple[int, int] | None = None
        self._thread: threading.Thread | None = None
        self.overflows = 0

    def bind(
        self,
        roots_of: Callable[[K], tuple[Path, ...]],
        on_change: Callable[[K], None],
        on_overflow: Callable[[], None],
    ) -> None:
        """The composition's leaf roots and change handlers (the process watch is made first)."""

        with self._lock:
            self._roots_of, self._on_change, self._on_overflow = roots_of, on_change, on_overflow

    def _key_roots(self, key: Hashable) -> tuple[Path, ...]:
        """``roots_of(key)`` and the root every stream subscribed for ``key`` listens under."""

        try:
            given = tuple(Path(one) for one in self._roots_of(key))  # type: ignore[arg-type]
        except (OSError, ValueError, LookupError) as error:
            logger.info("leaf worktree watch: no worktree roots for %r: %s", key, error)
            given = ()
        with self._lock:
            listened = tuple(one.listen_root for one in self._maps.subscribers.get(key, ()))
        return tuple(dict.fromkeys((*given, *listened)))

    # -- the instance and its reader ---------------------------------------------------------------

    def start(self) -> None:
        """Open the instance (once) and start its one reader thread (once)."""

        with self._lock:
            if self._thread is not None:
                return
            fd = self._instance()
            self._wake = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
            self._thread = threading.Thread(
                target=self._read, args=(fd, self._wake[0]), name="leaf-worktree-watch", daemon=True
            )
            self._thread.start()
        self._log_plan("started")

    def shutdown(self) -> None:
        """Stop the reader, close the instance and forget every watch, reason and subscriber."""

        with self._lock:
            thread, wake, fd = self._thread, self._wake, self._fd
            self._thread = self._wake = self._fd = None
            self._maps = _Maps()
        if wake is not None:
            with contextlib.suppress(OSError):
                os.write(wake[1], b"x")
        if thread is not None:
            thread.join()
        for one in (*(wake or ()), *(() if fd is None else (fd,))):
            with contextlib.suppress(OSError):
                os.close(one)

    def _instance(self) -> int:
        with self._lock:
            if self._fd is None:
                self._calls = self._calls or _Inotify()
                self._fd = self._calls.init()
            return self._fd

    def total_watches(self) -> int:
        with self._lock:
            return len(self._maps.keys_of)

    # -- reasons -----------------------------------------------------------------------------------

    def covered(self, key: K) -> bool:
        with self._lock:
            return bool(self._maps.wds_of.get(key)) and key not in self._maps.uncovered

    def uncovered(self) -> frozenset[K]:
        with self._lock:
            return frozenset(self._maps.uncovered)  # type: ignore[arg-type]

    def uncovered_reason(self, key: K) -> str | None:
        """Why ``key`` is uncovered, when it is."""

        with self._lock:
            return self._maps.uncovered.get(key)

    def covered_keys(self) -> tuple[K, ...]:
        """Every key with watches now (an overflow recaptures each)."""

        with self._lock:
            return tuple(key for key, held in self._maps.wds_of.items() if held)  # type: ignore[misc]

    def reasons(self, key: K) -> frozenset[str]:
        with self._lock:
            return frozenset(self._maps.reasons.get(key, ()))

    def cover(self, key: K, reason: str) -> None:
        """Add one reason for ``key``; a key that gains its first reason is registered."""

        with self._lock:
            self._maps.reasons.setdefault(key, set()).add(reason)
        self._settle([key], retry={key})

    def uncover(self, key: K, reason: str) -> None:
        """Remove one reason for ``key``; a key left with none has its watches dropped."""

        with self._lock:
            self._maps.reasons.get(key, set()).discard(reason)
        self._settle([key])

    def sync(self, reasons: Mapping[K, frozenset[str]]) -> None:
        """Replace every non-stream reason of every key with ``reasons`` in one call.

        A stream's reason is left as it is (only :meth:`subscribe` changes it), and every key that
        still has a reason and is uncovered is tried again. The plan is logged when this sync
        changed it -- a key, a reason, a watch or an uncovered leaf -- and not on every tick.
        """

        before = self._plan()
        with self._lock:
            touched = set(self._maps.reasons) | set(reasons)
            for key in touched:
                streamed = STREAM_REASON in self._maps.reasons.get(key, set())
                given = {one for one in reasons.get(key) or () if one != STREAM_REASON}  # type: ignore[arg-type]
                self._maps.reasons[key] = given | ({STREAM_REASON} if streamed else set())
            retry = set(self._maps.uncovered)
            wanted = any(self._maps.reasons.values())
        if wanted:
            self.start()  # a covered leaf's events are read from its first sync on
        self._settle([*touched, *retry], retry=retry)
        if self._plan() != before:
            self._log_plan("synced")

    def _plan(self) -> tuple[object, ...]:
        with self._lock:
            reasons = {key: frozenset(held) for key, held in self._maps.reasons.items() if held}
            return reasons, len(self._maps.keys_of), dict(self._maps.uncovered)

    def subscribe(
        self, key: K, listen_root: Path, notify: Callable[[Exception | None], None]
    ) -> Callable[[], None]:
        """Watch ``key`` for an open stream; its watches are armed before this returns.

        ``notify(None)`` is called from the reader for every batch with an admitted event under
        ``listen_root`` and for every overflow; ``notify(error)`` when the reader fails. Raises
        :class:`OSError` naming the reason when the key cannot be covered. The returned callable
        unsubscribes (once) and frees the watches no other key or reason keeps.
        """

        subscriber = _Subscriber(key, Path(listen_root), notify)
        with self._lock:
            self._maps.subscribers.setdefault(key, []).append(subscriber)
            self._maps.reasons.setdefault(key, set()).add(STREAM_REASON)
        self.start()
        self._settle([key], retry={key})
        with self._lock:
            failure = self._maps.uncovered.get(key)
            if failure is not None or not self._maps.wds_of.get(key):
                self._remove_subscriber(subscriber)
        if failure is not None or not self.covered(key):
            self._settle([key])
            raise OSError(failure or f"the leaf {key!r} has no watchable directory")

        done = threading.Event()

        def unsubscribe() -> None:
            if done.is_set():
                return
            done.set()
            with self._lock:
                self._remove_subscriber(subscriber)
            self._settle([key])

        return unsubscribe

    def _remove_subscriber(self, subscriber: _Subscriber) -> None:
        """Lock held: the stream leaves, and with the last one its key's stream reason."""

        listed = self._maps.subscribers.get(subscriber.key, [])
        if subscriber in listed:
            listed.remove(subscriber)
        if not listed:
            self._maps.subscribers.pop(subscriber.key, None)
            self._maps.reasons.get(subscriber.key, set()).discard(STREAM_REASON)

    def _settle(self, keys: Iterable[Hashable], *, retry: set[Hashable] | None = None) -> None:
        """Register every key that has a reason and no watch; drop every key without a reason."""

        for key in dict.fromkeys(keys):
            with self._lock:
                wanted = bool(self._maps.reasons.get(key))
                if not wanted:
                    self._maps.reasons.pop(key, None)
                    self._maps.uncovered.pop(key, None)
                    if key in self._maps.wds_of:
                        self._drop(key)
                    continue
                if key in self._maps.wds_of and key not in self._maps.uncovered:
                    continue
                if key in self._maps.uncovered and (retry is None or key not in retry):
                    continue
            self._register(key)  # type: ignore[arg-type]

    # -- watches -----------------------------------------------------------------------------------

    def _register(self, key: K) -> None:
        roots = self._key_roots(key)
        try:
            directories = sorted({one for root in roots for one in eligible_directories(root)})
            fd = self._instance()
        except OSError as error:
            self._mark_uncovered(key, str(error))
            return
        if not directories:
            self._mark_uncovered(key, f"no root of {key!r} is a directory: {roots}")
            return
        with self._lock:
            if not self._maps.reasons.get(key):
                return  # its last reason left while the roots were listed
            if not self._watch(key, fd, directories):
                return
            self._maps.uncovered.pop(key, None)
            reasons, count = sorted(self._maps.reasons[key]), len(self._maps.wds_of.get(key, ()))
            total = len(self._maps.keys_of)
        logger.info(
            "leaf worktree watch: added %r (reasons %s): %d directories, %d watches in total",
            key,
            ", ".join(reasons),
            count,
            total,
        )

    def _watch(self, key: Hashable, fd: int, directories: Iterable[Path]) -> bool:
        """Lock held: add ``directories`` for ``key``; on any failure undo what this call added."""

        assert self._calls is not None
        added: list[int] = []
        joined: list[int] = []
        held = self._maps.wds_of.setdefault(key, set())
        for directory in directories:
            wd = self._maps.wd_of.get(directory)
            if wd is None:
                try:
                    wd = self._calls.add(fd, directory, WATCH_MASK | IN_ONLYDIR)
                except OSError as error:
                    for one in joined:
                        self._leave(one, key)
                    for one in added:
                        self._leave(one, key)
                    held.difference_update(added, joined)
                    if not held:
                        self._maps.wds_of.pop(key, None)
                    self._mark_uncovered(key, str(error))
                    return False
                self._maps.wd_of[directory] = wd
                self._maps.dirs_of.setdefault(wd, set()).add(directory)
            if wd in held:
                continue
            (joined if wd in self._maps.keys_of else added).append(wd)
            self._maps.keys_of.setdefault(wd, set()).add(key)
            held.add(wd)
        return True

    def _mark_uncovered(self, key: Hashable, reason: str) -> None:
        with self._lock:
            self._maps.uncovered[key] = reason
            reasons = sorted(self._maps.reasons.get(key, ()))
        logger.warning(
            "leaf worktree watch: %r (reasons %s) is uncovered: %s", key, ", ".join(reasons), reason
        )

    def _drop(self, key: Hashable) -> None:
        """Lock held: forget ``key``'s watches; the ones no other key shares are removed."""

        count = len(self._maps.wds_of.get(key, ()))
        for wd in self._maps.wds_of.pop(key, set()):
            self._leave(wd, key)
        logger.info(
            "leaf worktree watch: dropped %r (no reason left): %d directories, %d watches in total",
            key,
            count,
            len(self._maps.keys_of),
        )

    def _leave(self, wd: int, key: Hashable) -> None:
        keys = self._maps.keys_of.get(wd)
        if keys is not None:
            keys.discard(key)
            if keys:
                return
        self._maps.keys_of.pop(wd, None)
        for directory in self._maps.dirs_of.pop(wd, set()):
            self._maps.wd_of.pop(directory, None)
        if self._fd is not None and self._calls is not None:
            self._calls.rm(self._fd, wd)

    def _forget_wd(self, wd: int) -> None:
        """The kernel dropped ``wd`` (its directory went away): forget it everywhere."""

        with self._lock:
            for key in self._maps.keys_of.pop(wd, set()):
                self._maps.wds_of.get(key, set()).discard(wd)
            for directory in self._maps.dirs_of.pop(wd, set()):
                self._maps.wd_of.pop(directory, None)

    def _log_plan(self, when: str) -> None:
        budget = read_budget()
        with self._lock:
            keys = len([one for one, held in self._maps.wds_of.items() if held])
            dirs, uncovered = len(self._maps.keys_of), len(self._maps.uncovered)
        logger.info(
            "leaf worktree watch %s: one inotify instance, %d keys over %d directories (%d "
            "uncovered); budget %s watches and %s instances per user",
            when,
            keys,
            dirs,
            uncovered,
            budget.max_user_watches,
            budget.max_user_instances,
        )

    # -- events ------------------------------------------------------------------------------------

    def _read(self, fd: int, wake: int) -> None:
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        poller.register(wake, select.POLLIN)
        while True:
            ready = {one for one, _ in poller.poll()}
            if wake in ready:
                return
            buffer = bytearray()
            while True:
                try:
                    chunk = os.read(fd, _READ_BYTES)
                except BlockingIOError:
                    break
                except OSError as error:
                    self._fail(error)
                    return
                if not chunk:
                    break
                buffer.extend(chunk)
            if buffer:
                self.handle(bytes(buffer))

    def _fail(self, error: Exception) -> None:
        logger.error("leaf worktree watch: the inotify instance cannot be read: %s", error)
        with self._lock:
            subscribers = [one for listed in self._maps.subscribers.values() for one in listed]
        for subscriber in subscribers:
            self._call(subscriber.notify, error)

    def handle(self, buffer: bytes) -> None:
        """Act on one read of the queue: overflows, dropped watches, new directories, changes."""

        changed: dict[Hashable, None] = {}
        notified: dict[_Subscriber, None] = {}
        for wd, mask, name in _events(buffer):
            if wd == -1 or mask & IN_Q_OVERFLOW:
                self._overflowed(notified)
            elif mask & IN_IGNORED:
                self._forget_wd(wd)
            else:
                self._event(wd, mask, name, changed, notified)
        for key in changed:
            self._call(self._on_change, key)
        for subscriber in notified:
            self._call(subscriber.notify, None)

    def _event(
        self,
        wd: int,
        mask: int,
        name: str,
        changed: dict[Hashable, None],
        notified: dict[_Subscriber, None],
    ) -> None:
        """One event on a watched directory: whom it reaches, and the directories it adds."""

        with self._lock:
            keys = tuple(self._maps.keys_of.get(wd, ()))
            places = tuple(self._maps.dirs_of.get(wd, ()))
            subscribers = [one for key in keys for one in self._maps.subscribers.get(key, ())]
        paths = [place / name if name else place for place in places]
        if name and not any(
            admitted(path, place) for path, place in zip(paths, places, strict=True)
        ):
            return  # a scratch or ignored name
        if mask & IN_ISDIR and mask & (IN_CREATE | IN_MOVED_TO):
            for key in keys:
                self._extend(key)
        if mask & (IN_DELETE_SELF | IN_MOVE_SELF):
            self._forget_wd(wd)
        changed.update(dict.fromkeys(keys))
        for subscriber in subscribers:
            if any(admitted(path, subscriber.listen_root) for path in paths):
                notified[subscriber] = None

    def _overflowed(self, notified: dict[_Subscriber, None]) -> None:
        with self._lock:
            self.overflows += 1
            count = self.overflows
            subscribers = [one for listed in self._maps.subscribers.values() for one in listed]
        logger.warning(
            "leaf worktree watch: the kernel queue overflowed (%d so far); events were lost", count
        )
        self._call(self._on_overflow)
        notified.update(dict.fromkeys(subscribers))  # each is told once, with the batch

    def _extend(self, key: Hashable) -> None:
        """Watch the directories of ``key`` that appeared since it was registered."""

        roots = self._key_roots(key)
        try:
            directories = sorted({one for root in roots for one in eligible_directories(root)})
        except OSError as error:
            self._mark_uncovered(key, str(error))
            return
        with self._lock:
            if self._fd is not None and key in self._maps.wds_of:
                self._watch(key, self._fd, directories)

    def _call(self, callback: Callable[..., None], *arguments: object) -> None:
        try:
            callback(*arguments)
        except Exception:  # a failing owner never stops the reader
            logger.exception("leaf worktree watch: a callback failed")


def _events(buffer: bytes) -> Iterable[tuple[int, int, str]]:
    """Each ``struct inotify_event`` of ``buffer``: its wd, mask and name (``""`` for none)."""

    at = 0
    while at + _EVENT.size <= len(buffer):
        wd, mask, _cookie, length = _EVENT.unpack_from(buffer, at)
        start = at + _EVENT.size
        raw = buffer[start : start + length].split(b"\0", 1)[0]
        yield wd, mask, os.fsdecode(raw)
        at = start + length


_PROCESS_WATCH: dict[str, LeafWorktreeWatch[tuple[str, str, str]]] = {}
_PROCESS_GUARD = threading.Lock()


def process_worktree_watch() -> LeafWorktreeWatch[tuple[str, str, str]]:
    """The process's one watch, keyed by ``(repository, master, leaf)``; made on first use.

    Review streams subscribe to it and the composition binds its leaf roots and change handlers to
    it (:meth:`LeafWorktreeWatch.bind`), so one process holds one inotify instance however many
    leaves and streams it watches.
    """

    with _PROCESS_GUARD:
        return _PROCESS_WATCH.setdefault("process", LeafWorktreeWatch())


def reset_process_worktree_watch() -> None:
    """Shut the process watch down and forget it (tests, between modules)."""

    with _PROCESS_GUARD:
        watches = list(_PROCESS_WATCH.values())
        _PROCESS_WATCH.clear()
    for watch in watches:
        watch.shutdown()
