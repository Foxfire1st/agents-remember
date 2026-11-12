"""Which processes belong to a sandbox, and the proof that a recorded one is still the same.

Two records exist. The dashboard is recorded by ``start`` in the sandbox's own process record.
The Paseo runtime is recorded by Paseo itself in its home (``paseo.pid``), which is how the stop
command of PNT-R01 addresses it; the home lies inside the sandbox directory, so its daemon is the
sandbox's own whichever provision run started it. Neither record is trusted on its word. The
recorded process id must be shown, from ``/proc``, to belong to the same process: the dashboard
by start time, command line, working directory and boot; the supervisor by its command line and
by its own environment naming this home. A Paseo record that fails this is stale and is deleted
before any runtime command reads it. What the records do not name is looked for as well: a
dashboard or a supervisor of this sandbox that runs unrecorded is reported, never signalled.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .layout import SandboxLayout
from .procfs import (
    PROC,
    ProcessIdentity,
    boot_id,
    environment,
    is_running,
    lineage,
    port_holders,
    read_identity,
    working_directory,
)

RECORD_SCHEMA = "pnt-sandbox-processes/v1"
PASEO_PROCESS_RECORD = "paseo.pid"
SUPERVISOR_COMMAND = "Paseo Supervisor"
PASEO_HOME_VARIABLE = "PASEO_HOME"
DASHBOARD_COMMAND = ("agents_remember.cli", "dashboard", "--config")


def read_record(layout: SandboxLayout) -> dict[str, Any]:
    """The process record of this boot; a record of an earlier boot names nothing.

    A record without a boot id was written by the earlier tooling and is read as it is: what it
    names is still proven process by process before it is trusted.
    """
    try:
        record = json.loads(layout.process_record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(record, dict) or record.get("schema") != RECORD_SCHEMA:
        return {}
    if record.get("bootId", boot_id()) != boot_id():
        return {}
    return record


def write_record(layout: SandboxLayout, record: dict[str, Any]) -> None:
    layout.run_dir.mkdir(parents=True, exist_ok=True)
    staged = layout.process_record.with_suffix(".json.new")
    document = {**record, "schema": RECORD_SCHEMA, "bootId": boot_id()}
    staged.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    staged.replace(layout.process_record)


def _process_ids() -> list[int]:
    return sorted(int(entry.name) for entry in PROC.iterdir() if entry.name.isdigit())


def is_sandbox_dashboard(pid: int, layout: SandboxLayout) -> bool:
    """Whether a running process is a dashboard of this sandbox, judged by what it is.

    Its command line runs the build's dashboard on this sandbox's settings file and its working
    directory is the sandbox directory.
    """
    identity = read_identity(pid)
    if identity is None:
        return False
    argv, size = identity.argv, len(DASHBOARD_COMMAND)
    settings = layout.settings_file.resolve()
    runs_dashboard = any(
        argv[index : index + size] == DASHBOARD_COMMAND
        and Path(argv[index + size]).resolve() == settings
        for index in range(len(argv) - size)
    )
    cwd = working_directory(pid) if runs_dashboard else None
    return cwd is not None and cwd.resolve() == layout.root.resolve()


def recorded_dashboard(layout: SandboxLayout) -> ProcessIdentity | None:
    """The dashboard ``start`` recorded, when that very process is still running."""
    identity = ProcessIdentity.from_record(read_record(layout).get("dashboard"))
    if identity is None or not is_running(identity):
        return None
    return identity if is_sandbox_dashboard(identity.pid, layout) else None


def unrecorded_dashboards(layout: SandboxLayout, known: ProcessIdentity | None) -> list[int]:
    """Running dashboards of this sandbox that the process record does not name.

    Every process is looked at, not only the holders of the reserved ports: a dashboard that a
    killed start left behind may not have bound its port yet.
    """
    return [
        pid
        for pid in _process_ids()
        if (known is None or pid != known.pid) and is_sandbox_dashboard(pid, layout)
    ]


def paseo_record_pid(home: Path) -> int | None:
    """The process id Paseo's own record names, verified or not; ``None`` when it names none."""
    try:
        pid = json.loads((home / PASEO_PROCESS_RECORD).read_text(encoding="utf-8")).get("pid")
    except (OSError, ValueError, AttributeError):
        return None
    return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 1 else None


def _is_home_supervisor(pid: int, home: Path) -> bool | None:
    """Whether a process is this home's Paseo supervisor; ``None`` when it cannot be told.

    It must carry the supervisor's command line and name this home in its own environment,
    which Paseo sets when it starts a supervisor. A supervisor whose environment this user may
    not read is neither proven nor disproven.
    """
    identity = read_identity(pid)
    if identity is None or identity.argv[:1] != (SUPERVISOR_COMMAND,):
        return False
    carried = environment(pid)
    if carried is None:
        return None if read_identity(pid) == identity else False
    named_home = carried.get(PASEO_HOME_VARIABLE)
    return named_home is not None and Path(named_home).resolve() == home.resolve()


@dataclass(frozen=True)
class PaseoRecord:
    """What Paseo's own process record of the sandbox home says, judged against ``/proc``.

    ``absent``: no record. ``unusable``: a record that names no process. ``own``: it names this
    home's supervisor. ``stale``: it names a process that is gone or is not this home's
    supervisor. ``unreadable``: it names a live supervisor whose environment cannot be read.
    """

    kind: str
    pid: int | None = None


def inspect_paseo_record(home: Path) -> PaseoRecord:
    if not (home / PASEO_PROCESS_RECORD).is_file():
        return PaseoRecord("absent")
    pid = paseo_record_pid(home)
    if pid is None:
        return PaseoRecord("unusable")
    proven = _is_home_supervisor(pid, home)
    if proven is None:
        return PaseoRecord("unreadable", pid)
    return PaseoRecord("own" if proven else "stale", pid)


def verified_supervisor(home: Path) -> ProcessIdentity | None:
    """The supervisor of the daemon of ``home``, when Paseo's record still names that process."""
    record = inspect_paseo_record(home)
    return read_identity(record.pid) if record.kind == "own" and record.pid is not None else None


def clear_stale_paseo_record(home: Path) -> PaseoRecord:
    """Delete Paseo's record when it is stale or names no process; say what it was.

    The PNT-R01 commands address whatever process the record names, so they are never run while
    a stale record exists; the record lies inside the sandbox and names nothing of the sandbox's,
    so deleting it loses nothing. The process it named is not signalled. A record that names this
    home's supervisor, or a supervisor that cannot be inspected, is left alone.
    """
    record = inspect_paseo_record(home)
    if record.kind in {"stale", "unusable"}:
        (home / PASEO_PROCESS_RECORD).unlink(missing_ok=True)
    return record


def unrecorded_supervisors(home: Path, known: ProcessIdentity | None) -> list[int]:
    """Running Paseo supervisors of this home that the home's own record does not name."""
    return [
        pid
        for pid in _process_ids()
        if (known is None or pid != known.pid) and _is_home_supervisor(pid, home)
    ]


@dataclass(frozen=True)
class PortState:
    """Who holds one reserved port, seen from the sandbox."""

    port: int
    holders: tuple[int | None, ...]
    foreign: tuple[int | None, ...]

    @property
    def held_by_sandbox(self) -> bool:
        return bool(self.holders) and not self.foreign


def port_state(port: int, owner: ProcessIdentity | None) -> PortState:
    """Split the holders of ``port`` into the owner's own processes and everything else.

    A holder belongs to the owner when the owner is the holder or one of its ancestors.
    """
    holders = tuple(port_holders(port))
    foreign = tuple(
        holder
        for holder in holders
        if holder is None or owner is None or owner.pid not in lineage(holder)
    )
    return PortState(port, holders, foreign)
