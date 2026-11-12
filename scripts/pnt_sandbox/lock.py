"""One command at a time on one sandbox.

``build``, ``start``, ``stop`` and ``reset`` hold an exclusive lock for the whole command. A second
command does not wait: it refuses and names the holder. Two starts at once would each provision,
each start a dashboard, and each undo the other's work on failure. The lock file lies beside the
sandbox directory, so it also covers a build of a missing directory and a reset that deletes it.

The provision run of a start is given the lock as well: it inherits the locked file, so a start
that is killed while it provisions leaves the sandbox locked until that run has ended, and the
holder record names it.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from collections.abc import Iterator
from typing import IO, Any

from .layout import SandboxLayout, SandboxRefusal
from .procfs import PROC

# How often and how far apart the holder's record is read before the holder counts as unnamed.
_RECORD_READS = 20
_RECORD_READ_PAUSE_SECONDS = 0.05


def _holder_record(handle: IO[str]) -> Any:
    """The record the holder wrote into the file.

    The holder writes it right after it has taken the lock, and rewrites it when a child joins
    or leaves, so a record that is empty or half written is read again for a moment.
    """
    holder = None
    for _ in range(_RECORD_READS):
        with contextlib.suppress(OSError, ValueError):
            handle.seek(0)
            holder = json.loads(handle.read())
        if isinstance(holder, dict):
            break
        time.sleep(_RECORD_READ_PAUSE_SECONDS)
    return holder


def _holder(handle: IO[str]) -> str:
    """Who holds the lock, in words for the refusal."""
    try:
        holder = _holder_record(handle)
        named = f"'{holder['command']}' (pid {holder['pid']}, since {holder['since']})"
        child = holder.get("child")
    except (KeyError, TypeError, AttributeError):
        return "an unnamed command"
    if not isinstance(child, dict):
        return named
    running = f"its '{child.get('name')}' run (pid {child.get('pid')})"
    if (PROC / str(holder["pid"])).exists():
        return f"{named}, with {running}"
    return f"{running} is still at work; {named} itself has ended"


def _acquire(layout: SandboxLayout) -> IO[str]:
    """Open and lock the lock file; refuse when another command holds it."""
    path = layout.lock_file
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        handle = open(path, "a+", encoding="utf-8")  # noqa: SIM115 - held for the whole command
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            holder = _holder(handle)
            handle.close()
            raise SandboxRefusal(
                f"another command is running on the sandbox {layout.root}: {holder}; "
                "nothing was done"
            ) from None
        # The holder before us deleted the file on release: lock the file that is there now.
        with contextlib.suppress(OSError):
            if os.fstat(handle.fileno()).st_ino == path.stat().st_ino:
                return handle
        handle.close()


class HeldLock:
    """The lock of the running command, and the record that says who holds it."""

    def __init__(self, handle: IO[str], command: str) -> None:
        self._handle = handle
        self._record: dict[str, Any] = {
            "command": command,
            "pid": os.getpid(),
            "since": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        self._write()

    def _write(self) -> None:
        self._handle.seek(0)
        self._handle.truncate()
        json.dump(self._record, self._handle)
        self._handle.flush()

    def fileno(self) -> int:
        """The locked file, for a child process that must keep the sandbox locked while it runs."""
        return self._handle.fileno()

    def note_child(self, name: str, pid: int | None) -> None:
        """Name the child that shares the lock, or with ``pid`` ``None`` say that it has ended."""
        if pid is None:
            self._record.pop("child", None)
        else:
            self._record["child"] = {"name": name, "pid": pid}
        self._write()


@contextlib.contextmanager
def sandbox_lock(layout: SandboxLayout, command: str) -> Iterator[HeldLock]:
    handle = _acquire(layout)
    try:
        yield HeldLock(handle, command)
    finally:
        with contextlib.suppress(OSError):
            layout.lock_file.unlink()
        handle.close()
