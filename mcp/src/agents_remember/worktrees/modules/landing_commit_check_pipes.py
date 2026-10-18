"""Best-effort observation of descendants holding a private hook output pipe."""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path


def _pipe_write_end_holders(pipe_inode: int) -> list[int]:
    """Live PIDs holding the pipe's write end open, other than this process.

    The hook output pipe is private to one run: only the hook subtree inherits
    its write end. A descendant that escaped the process group (new session)
    survives the group kill still holding it; the inode names exactly those
    processes. Best effort: unreadable entries are skipped, never fatal.
    """
    holders: list[int] = []
    me = os.getpid()
    proc = Path("/proc")
    if not proc.is_dir():
        return holders
    with suppress(OSError):
        entries = list(proc.glob("[0-9]*"))
    for entry in entries:
        try:
            pid = int(entry.name)
        except ValueError:
            continue
        if pid == me:
            continue
        try:
            fds = list((entry / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            with suppress(OSError):
                if os.readlink(fd) == f"pipe:[{pipe_inode}]":
                    holders.append(pid)
                    break
    return sorted(set(holders))
