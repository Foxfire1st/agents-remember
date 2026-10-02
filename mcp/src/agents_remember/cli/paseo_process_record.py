"""Prove that the home's process record names this home's Paseo supervisor.

``<home>/paseo.pid`` records the supervisor's process id. Paseo's own ``daemon stop`` sends
SIGTERM to whatever live process that id names (``stopDaemonInstance`` in Paseo 0.11.0-beta.2
checks only that the id is alive and the record is not older than this boot), and its ``status``
and ``start`` take any live id for a running daemon. After a crash the operating system hands the
id to another process, so the record alone identifies nothing.

Before any Paseo command that would act on the record, the commands here require three facts
about the recorded process: it is alive, its command line is a Paseo supervisor's, and its own
environment names this home. A record that fails this is stale: nothing is signalled.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agents_remember.cli.paseo_command import PaseoRuntimeFailure

PROCESS_RECORD = "paseo.pid"
# ``process.title`` of Paseo's supervisor entry point (0.10 and 0.11).
SUPERVISOR_TITLE = "Paseo Supervisor"


@dataclass(frozen=True)
class ProcessFacts:
    """What a live process says about itself: its command line and the home it was started for."""

    command_line: str
    paseo_home: str | None


class ProcessUnreadable(Exception):
    """A live process whose command line or environment this user cannot read."""


ProcessReader = Callable[[int], ProcessFacts | None]


@dataclass(frozen=True)
class RecordState:
    """``own``: this home's supervisor. ``stale``: a record that names anything else, or nothing."""

    kind: Literal["absent", "own", "stale"]
    pid: int | None = None
    reason: str | None = None

    def as_payload(self) -> dict[str, Any] | None:
        """The stale record for a report; ``None`` when there is none."""
        return {"pid": self.pid, "reason": self.reason} if self.kind == "stale" else None


def read_process(pid: int) -> ProcessFacts | None:
    """The facts of a live process from ``/proc``; ``None`` when no such process exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    base = Path("/proc") / str(pid)
    try:
        command_line = (base / "cmdline").read_bytes()
        environment = (base / "environ").read_bytes()
    except FileNotFoundError:
        if Path("/proc/self").exists():
            return None
        raise ProcessUnreadable("this system has no /proc to read the process from") from None
    except PermissionError as error:
        raise ProcessUnreadable(str(error)) from error
    variables = dict(entry.split(b"=", 1) for entry in environment.split(b"\0") if b"=" in entry)
    home = variables.get(b"PASEO_HOME")
    return ProcessFacts(
        command_line=command_line.replace(b"\0", b" ").decode("utf-8", "replace").strip(),
        paseo_home=None if home is None else os.fsdecode(home),
    )


def inspect_record(home: Path, step: str, reader: ProcessReader = read_process) -> RecordState:
    """Classify the home's process record; refuse when a live process cannot be inspected."""
    path = home / PROCESS_RECORD
    if not path.is_file():
        return RecordState("absent")
    pid = _recorded_pid(path)
    if pid is None:
        return RecordState("stale", None, "the record names no process")
    try:
        facts = reader(pid)
    except ProcessUnreadable as error:
        raise PaseoRuntimeFailure(
            "process_record_unverifiable",
            step,
            f"{path} names process {pid}, which is alive but cannot be inspected; nothing was "
            "signalled",
            str(error),
        ) from error
    if facts is None:
        return RecordState("stale", pid, "the recorded process no longer exists")
    if not facts.command_line.startswith(SUPERVISOR_TITLE):
        return RecordState("stale", pid, "the recorded process is not a Paseo supervisor")
    if facts.paseo_home is None or Path(facts.paseo_home).resolve() != home.resolve():
        return RecordState("stale", pid, "the recorded process is the supervisor of another home")
    return RecordState("own", pid)


def remove_stale_record(home: Path) -> None:
    """Delete a record proven stale; the file lies inside the home and names no daemon of it."""
    (home / PROCESS_RECORD).unlink(missing_ok=True)


def _recorded_pid(path: Path) -> int | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = record.get("pid") if isinstance(record, dict) else None
    return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 1 else None
