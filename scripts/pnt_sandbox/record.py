"""Which processes belong to a sandbox, and the proof that a recorded one is still the same.

Two records exist. The dashboard is recorded by ``start`` in the sandbox's own process record.
The Paseo runtime is recorded by Paseo itself in its home (``paseo.pid``), which is how the stop
command of PNT-R01 addresses it; the home lies inside the sandbox directory, so its daemon is the
sandbox's own whichever provision run started it. Either record is trusted only after the
recorded process id is shown, from ``/proc``, to belong to the same process: the dashboard by
start time and command line, the supervisor by command line and by either its recorded start
time or its own environment naming this home.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .layout import SandboxLayout
from .procfs import (
    ProcessIdentity,
    environment_value,
    is_running,
    lineage,
    port_holders,
    read_identity,
)

RECORD_SCHEMA = "pnt-sandbox-processes/v1"
PASEO_PROCESS_RECORD = "paseo.pid"
SUPERVISOR_COMMAND = "Paseo Supervisor"
PASEO_HOME_VARIABLE = "PASEO_HOME"


def read_record(layout: SandboxLayout) -> dict[str, Any]:
    try:
        record = json.loads(layout.process_record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(record, dict) or record.get("schema") != RECORD_SCHEMA:
        return {}
    return record


def write_record(layout: SandboxLayout, record: dict[str, Any]) -> None:
    layout.run_dir.mkdir(parents=True, exist_ok=True)
    staged = layout.process_record.with_suffix(".json.new")
    staged.write_text(
        json.dumps({"schema": RECORD_SCHEMA, **record}, indent=2) + "\n", encoding="utf-8"
    )
    staged.replace(layout.process_record)


def recorded_dashboard(layout: SandboxLayout) -> ProcessIdentity | None:
    """The dashboard ``start`` recorded, when that very process is still running."""
    identity = ProcessIdentity.from_record(read_record(layout).get("dashboard"))
    return identity if is_running(identity) else None


def paseo_record_pid(home: Path) -> int | None:
    """The process id Paseo's own record names, verified or not; ``None`` without a record."""
    try:
        pid = json.loads((home / PASEO_PROCESS_RECORD).read_text(encoding="utf-8")).get("pid")
    except (OSError, ValueError, AttributeError):
        return None
    return pid if isinstance(pid, int) else None


def verified_supervisor(
    home: Path, recorded: ProcessIdentity | None = None
) -> ProcessIdentity | None:
    """The supervisor of the daemon of ``home``, when Paseo's record still names that process.

    The named process must carry the supervisor's command line, and one of two things must
    prove it is this home's: it is exactly the process ``start`` recorded (same start time), or
    its own environment names this home, which Paseo sets when it starts a supervisor. A process
    that inherited the id proves neither and is left alone.
    """
    pid = paseo_record_pid(home)
    identity = read_identity(pid) if pid is not None else None
    if identity is None or identity.argv[:1] != (SUPERVISOR_COMMAND,):
        return None
    if identity == recorded:
        return identity
    named_home = environment_value(identity.pid, PASEO_HOME_VARIABLE)
    if named_home is not None and Path(named_home).resolve() == home.resolve():
        return identity
    return None


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
