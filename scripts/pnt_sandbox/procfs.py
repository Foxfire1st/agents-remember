"""Process identity, listening-port holders and signalling, read from ``/proc``.

A process id alone identifies nothing: the system hands the number to something else once its
owner is gone. A process is therefore always addressed by an identity (id, start time in clock
ticks since boot, command line) and re-read from ``/proc`` before it is trusted or signalled.
"""

from __future__ import annotations

import contextlib
import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROC = Path("/proc")
_LISTEN = "0A"
_POLL_SECONDS = 0.1


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_ticks: int
    argv: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        return {"pid": self.pid, "startTicks": self.start_ticks, "argv": list(self.argv)}

    @classmethod
    def from_record(cls, raw: object) -> ProcessIdentity | None:
        if not isinstance(raw, dict):
            return None
        pid, ticks, argv = raw.get("pid"), raw.get("startTicks"), raw.get("argv")
        if not isinstance(pid, int) or not isinstance(ticks, int) or not isinstance(argv, list):
            return None
        return cls(pid, ticks, tuple(str(part) for part in argv))


@dataclass(frozen=True)
class _Stat:
    state: str
    parent: int
    session: int
    start_ticks: int


def _stat(pid: int) -> _Stat | None:
    try:
        text = (PROC / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # The command name sits in parentheses and may itself hold spaces or parentheses.
    fields = text.rsplit(")", 1)[-1].split()
    try:
        return _Stat(fields[0], int(fields[1]), int(fields[3]), int(fields[19]))
    except (IndexError, ValueError):
        return None


def read_identity(pid: int) -> ProcessIdentity | None:
    """The identity of a running process; ``None`` when it is gone or only a zombie remains."""
    stat = _stat(pid)
    if stat is None or stat.state in {"Z", "X"}:
        return None
    try:
        raw = (PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        return None
    argv = tuple(part.decode("utf-8", "replace") for part in raw.rstrip(b"\0").split(b"\0"))
    return ProcessIdentity(pid, stat.start_ticks, argv)


def environment_value(pid: int, name: str) -> str | None:
    """One variable of a process's environment; ``None`` when unset or not readable."""
    try:
        raw = (PROC / str(pid) / "environ").read_bytes()
    except OSError:
        return None
    prefix = f"{name}=".encode()
    for entry in raw.split(b"\0"):
        if entry.startswith(prefix):
            return entry[len(prefix) :].decode("utf-8", "replace")
    return None


def is_running(identity: ProcessIdentity | None) -> bool:
    """Whether the recorded process, and not a later owner of its id, is running."""
    return identity is not None and read_identity(identity.pid) == identity


def lineage(pid: int) -> list[int]:
    """``pid`` and its ancestors, nearest first."""
    chain: list[int] = []
    current = pid
    while current > 0 and current not in chain:
        chain.append(current)
        stat = _stat(current)
        if stat is None:
            break
        current = stat.parent
    return chain


def session_members(leader: int) -> list[ProcessIdentity]:
    """Every running process in the session ``leader`` leads, the leader excluded."""
    members: list[ProcessIdentity] = []
    for entry in PROC.iterdir():
        if not entry.name.isdigit() or int(entry.name) == leader:
            continue
        stat = _stat(int(entry.name))
        if stat is not None and stat.session == leader:
            identity = read_identity(int(entry.name))
            if identity is not None:
                members.append(identity)
    return members


def _listening_inodes(port: int) -> set[str]:
    inodes: set[str] = set()
    for table in ("tcp", "tcp6"):
        try:
            lines = (PROC / "net" / table).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != _LISTEN:
                continue
            if int(fields[1].rsplit(":", 1)[1], 16) == port:
                inodes.add(fields[9])
    return inodes


def _link_targets(links: list[Path]) -> set[str]:
    """Where each descriptor points; one that closed meanwhile is skipped, not the process."""
    targets: set[str] = set()
    for link in links:
        with contextlib.suppress(OSError):
            targets.add(os.readlink(link))
    return targets


def port_holders(port: int) -> list[int | None]:
    """The processes listening on ``port`` on any address; empty when nothing listens.

    A holder this user may not inspect (another user's process) is reported as ``None``: the
    port is held, the process id is not readable.
    """
    inodes = _listening_inodes(port)
    if not inodes:
        return []
    wanted = {f"socket:[{inode}]" for inode in inodes}
    holders: set[int] = set()
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            descriptors = list((entry / "fd").iterdir())
        except OSError:
            continue
        if wanted.intersection(_link_targets(descriptors)):
            holders.add(int(entry.name))
    found: list[int | None] = [*sorted(holders)]
    return found or [None]


def wait_until(condition: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if condition():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_SECONDS)


def terminate(identity: ProcessIdentity, *, grace_seconds: float, kill_seconds: float) -> str:
    """Stop exactly the recorded process: ``stopped``, ``killed``, ``not running`` or ``alive``.

    The identity is re-read immediately before each signal, so a process id that has passed to
    another process is never signalled.
    """
    if not is_running(identity):
        return "not running"
    with contextlib.suppress(ProcessLookupError):
        os.kill(identity.pid, signal.SIGTERM)
    if wait_until(lambda: not is_running(identity), grace_seconds):
        return "stopped"
    if is_running(identity):
        with contextlib.suppress(ProcessLookupError):
            os.kill(identity.pid, signal.SIGKILL)
    return "killed" if wait_until(lambda: not is_running(identity), kill_seconds) else "alive"
