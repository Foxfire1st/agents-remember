"""Which processes belong to a sandbox, and the proof that a recorded one is still the same.

Two records exist. The dashboard is recorded by ``start`` in the sandbox's own process record.
The Paseo runtime is recorded by Paseo itself in its home (``paseo.pid``), which is how the stop
command of PNT-R01 addresses it; the home lies inside the sandbox directory, so its daemon is the
sandbox's own whichever provision run started it. Neither record is trusted on its word. The
recorded process id must be shown, from ``/proc``, to belong to the same process: the dashboard
by start time, command line, working directory and boot; the supervisor by its command line and
by its own environment naming this home. A Paseo record that fails this is stale and is deleted
before any runtime command reads it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .layout import SandboxLayout
from .procfs import (
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
    """The process record of this boot; a record of an earlier boot names nothing."""
    try:
        record = json.loads(layout.process_record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(record, dict) or record.get("schema") != RECORD_SCHEMA:
        return {}
    if record.get("bootId") != boot_id():
        return {}
    return record


def write_record(layout: SandboxLayout, record: dict[str, Any]) -> None:
    layout.run_dir.mkdir(parents=True, exist_ok=True)
    staged = layout.process_record.with_suffix(".json.new")
    document = {**record, "schema": RECORD_SCHEMA, "bootId": boot_id()}
    staged.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    staged.replace(layout.process_record)


def is_sandbox_dashboard(pid: int, layout: SandboxLayout) -> bool:
    """Whether a running process is a dashboard of this sandbox, judged by what it is.

    Its command line runs the build's dashboard on this sandbox's settings file and its working
    directory is the sandbox directory.
    """
    identity = read_identity(pid)
    if identity is None:
        return False
    wanted = (*DASHBOARD_COMMAND, layout.settings_file.as_posix())
    argv = identity.argv
    runs_dashboard = any(argv[index : index + len(wanted)] == wanted for index in range(len(argv)))
    cwd = working_directory(pid)
    return runs_dashboard and cwd is not None and cwd.resolve() == layout.root.resolve()


def recorded_dashboard(layout: SandboxLayout) -> ProcessIdentity | None:
    """The dashboard ``start`` recorded, when that very process is still running."""
    identity = ProcessIdentity.from_record(read_record(layout).get("dashboard"))
    if identity is None or not is_running(identity):
        return None
    return identity if is_sandbox_dashboard(identity.pid, layout) else None


def unrecorded_dashboards(layout: SandboxLayout, known: ProcessIdentity | None) -> list[int]:
    """Dashboards of this sandbox that hold a reserved port and that the record does not name."""
    holders = {*port_holders(layout.dashboard_port), *port_holders(layout.paseo_port)}
    return sorted(
        pid
        for pid in holders
        if pid is not None
        and (known is None or pid != known.pid)
        and is_sandbox_dashboard(pid, layout)
    )


def paseo_record_pid(home: Path) -> int | None:
    """The process id Paseo's own record names, verified or not; ``None`` without a record."""
    try:
        pid = json.loads((home / PASEO_PROCESS_RECORD).read_text(encoding="utf-8")).get("pid")
    except (OSError, ValueError, AttributeError):
        return None
    return pid if isinstance(pid, int) else None


def verified_supervisor(home: Path) -> ProcessIdentity | None:
    """The supervisor of the daemon of ``home``, when Paseo's record still names that process.

    The named process must carry the supervisor's command line and name this home in its own
    environment, which Paseo sets when it starts a supervisor. A process that inherited the id
    shows neither.
    """
    pid = paseo_record_pid(home)
    identity = read_identity(pid) if pid is not None else None
    if identity is None or identity.argv[:1] != (SUPERVISOR_COMMAND,):
        return None
    named_home = (environment(identity.pid) or {}).get(PASEO_HOME_VARIABLE)
    if named_home is None or Path(named_home).resolve() != home.resolve():
        return None
    return identity


def clear_stale_paseo_record(home: Path) -> int | None:
    """Delete Paseo's record when it names a process that is not this home's supervisor.

    Returns the process id the stale record named. The PNT-R01 commands address whatever process
    the record names, so they are never run while a record of this kind exists; the record lies
    inside the sandbox and names nothing of the sandbox's, so deleting it loses nothing. The named
    process is not signalled.
    """
    pid = paseo_record_pid(home)
    if pid is None or verified_supervisor(home) is not None:
        return None
    (home / PASEO_PROCESS_RECORD).unlink(missing_ok=True)
    return pid


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
