"""One command at a time on one sandbox.

``build``, ``start``, ``stop`` and ``reset`` hold an exclusive lock for the whole command. A second
command does not wait: it refuses and names the holder. Two starts at once would each provision,
each start a dashboard, and each undo the other's work on failure. The lock file lies beside the
sandbox directory, so it also covers a build of a missing directory and a reset that deletes it.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from collections.abc import Iterator
from typing import IO

from .layout import SandboxLayout, SandboxRefusal


def _holder(handle: IO[str]) -> str:
    try:
        handle.seek(0)
        holder = json.loads(handle.read())
        return f"'{holder['command']}' (pid {holder['pid']}, since {holder['since']})"
    except (OSError, ValueError, KeyError, TypeError):
        return "an unnamed command"


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


@contextlib.contextmanager
def sandbox_lock(layout: SandboxLayout, command: str) -> Iterator[None]:
    handle = _acquire(layout)
    try:
        handle.seek(0)
        handle.truncate()
        since = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        json.dump({"command": command, "pid": os.getpid(), "since": since}, handle)
        handle.flush()
        yield
    finally:
        with contextlib.suppress(OSError):
            layout.lock_file.unlink()
        handle.close()
