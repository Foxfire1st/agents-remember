"""The start, stop, reset and safety-check commands of the sandbox.

``start`` refuses before it starts anything: a checkout that is not a PNT build, another command
running on the sandbox, a reserved port held by a process the sandbox did not start, a running
Paseo runtime that does not carry the sandbox's environment, or a root that does not resolve
inside the sandbox directory. It never chooses another port. ``stop`` signals only processes it
has just proven, from ``/proc``, to be the sandbox's own.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import builder, safety
from .environment import foreign_variables
from .layout import SandboxLayout, SandboxRefusal, read_marker
from .lock import sandbox_lock
from .operations import TOOLING_CHECKOUT, Operations, StepFailed, require_checkout
from .procfs import ProcessIdentity, is_running, session_members, terminate, wait_until
from .record import (
    PASEO_PROCESS_RECORD,
    PaseoRecord,
    PortState,
    clear_stale_paseo_record,
    is_sandbox_dashboard,
    port_state,
    read_record,
    recorded_dashboard,
    unrecorded_dashboards,
    unrecorded_supervisors,
    write_record,
)

DASHBOARD_GRACE_SECONDS = 15.0
DASHBOARD_KILL_SECONDS = 5.0
# What provision reports when this run brought the daemon process up.
STARTED_ACTIONS = frozenset({"started", "restarted"})

Out = Callable[[str], None]


@dataclass(frozen=True)
class Observed:
    """What of the sandbox is running, and who holds its two ports."""

    dashboard: ProcessIdentity | None
    paseo: ProcessIdentity | None
    dashboard_port: PortState
    paseo_port: PortState

    @property
    def running(self) -> bool:
        return (
            self.dashboard is not None
            and self.paseo is not None
            and self.dashboard_port.held_by_sandbox
            and self.paseo_port.held_by_sandbox
        )


@dataclass
class _Started:
    """What one start itself brought up and wrote into the process record."""

    dashboard: ProcessIdentity | None = None
    paseo: bool = False
    recorded_paseo: ProcessIdentity | None = None


def observe(layout: SandboxLayout, ops: Operations) -> Observed:
    dashboard = recorded_dashboard(layout)
    paseo = ops.paseo_supervisor()
    return Observed(
        dashboard,
        paseo,
        port_state(layout.dashboard_port, dashboard),
        port_state(layout.paseo_port, paseo),
    )


def _refuse_unrecorded_processes(layout: SandboxLayout, observed: Observed) -> None:
    """Refuse while a dashboard or supervisor of this sandbox runs that no record names.

    A start that was killed between starting a process and recording it leaves one. Starting a
    second beside it would be wrong and signalling it on a guess is not this tool's to do.
    """
    dashboards = unrecorded_dashboards(layout, observed.dashboard)
    supervisors = unrecorded_supervisors(layout.paseo_home, observed.paseo)
    if dashboards:
        raise SandboxRefusal(
            f"process {dashboards[0]} runs this sandbox's dashboard, but the process record does "
            f"not name it; end it yourself (kill {dashboards[0]}). Nothing was started"
        )
    if supervisors:
        raise SandboxRefusal(
            f"process {supervisors[0]} is a Paseo supervisor of this sandbox's home, but the "
            f"home's process record does not name it; end it yourself (kill {supervisors[0]}). "
            "Nothing was started"
        )


def _refuse_foreign_ports(observed: Observed) -> None:
    for state in (observed.paseo_port, observed.dashboard_port):
        if state.foreign:
            holders = ", ".join(
                "unknown (not this user's process)" if pid is None else str(pid)
                for pid in state.foreign
            )
            raise SandboxRefusal(
                f"port {state.port} is held by process {holders}, which this sandbox did not "
                "start; nothing was started and no other port is used"
            )


def _refuse_foreign_environment(
    layout: SandboxLayout, ops: Operations, supervisor: ProcessIdentity | None
) -> None:
    """Refuse a running Paseo runtime that was not started with the sandbox's environment.

    The daemon hands its environment to every agent it starts. One that a direct provision run
    started carries the caller's variables, the calling harness session's among them.
    """
    if supervisor is None:
        return
    carried = ops.process_environment(supervisor.pid)
    if carried is None:
        raise SandboxRefusal(
            f"the environment of the running Paseo runtime (pid {supervisor.pid}) cannot be "
            "read; run 'stop', then 'start'. Nothing was started"
        )
    names = foreign_variables(carried, layout)
    if names:
        raise SandboxRefusal(
            f"the running Paseo runtime (pid {supervisor.pid}) was not started with the sandbox's "
            f"environment: {', '.join(names)}. Every agent it starts would inherit that. Run "
            "'stop', then 'start'. Nothing was started"
        )


def check(layout: SandboxLayout, checkout: Path, ops: Operations, out: Out) -> bool:
    """Run the safety check, print every root and every finding, and say whether it passed."""
    report = safety.check(layout, checkout, lambda mode: ops.resolve_roots(checkout, mode))
    for key, path in report.roots.items():
        out(f"  {key}: {path if path is not None else 'UNRESOLVED'}")
    for finding in report.findings:
        out(f"  FAIL {finding}")
    out(
        f"safety check passed: every root resolves inside {layout.root}"
        if report.ok
        else f"safety check FAILED: {', '.join(finding.key for finding in report.findings)}"
    )
    return report.ok


def start(
    layout: SandboxLayout, checkout_path: Path, ops: Operations, out: Out, eve_project: Path
) -> int:
    checkout = require_checkout(checkout_path)
    builder.require_sandbox_directory(layout)
    with sandbox_lock(layout, "start") as held:
        ops.held_lock = held
        try:
            return _start(layout, checkout, ops, out, eve_project)
        finally:
            ops.held_lock = None


def _start(
    layout: SandboxLayout, checkout: Path, ops: Operations, out: Out, eve_project: Path
) -> int:
    observed = observe(layout, ops)
    _refuse_unrecorded_processes(layout, observed)
    _refuse_foreign_ports(observed)
    _refuse_foreign_environment(layout, ops, observed.paseo)
    if observed.running:
        out(f"already running: {layout.dashboard_url}")
        return 0
    # The build comes first: it marks the directory as a sandbox before any child process, which
    # all write their bytecode cache there, can create it.
    if not builder.is_built(layout):
        builder.build_unlocked(layout, checkout, ops, out, eve_project)
    for line in [*ops.prepare_python(checkout), *ops.prepare_bundle(checkout)]:
        out(line)
    if not check(layout, checkout, ops, out):
        raise SandboxRefusal("the safety check failed; nothing was started")
    observed = observe(layout, ops)
    _refuse_foreign_ports(observed)
    return _bring_up(layout, checkout, ops, out, observed)


def _clear_paseo_record(layout: SandboxLayout, out: Out, deleted: str) -> PaseoRecord:
    """Delete a Paseo record that names nothing of this home, and say so in ``deleted`` words."""
    record = clear_stale_paseo_record(layout.paseo_home)
    path = layout.paseo_home / PASEO_PROCESS_RECORD
    if record.kind == "stale":
        out(
            f"{deleted} {path}: it named process {record.pid}, which is not this home's Paseo "
            "supervisor; that process was not signalled"
        )
    elif record.kind == "unusable":
        out(f"{deleted} {path}: it named no process")
    return record


def _provision(
    layout: SandboxLayout, checkout: Path, ops: Operations, out: Out, started: _Started
) -> ProcessIdentity:
    """Bring the Paseo runtime up through PNT-R01; the proven supervisor of the sandbox home."""
    before = ops.paseo_supervisor()
    if before is None:
        # A supervisor that ran before this start has been checked already. Without one, the
        # record is read once more: no runtime command may meet a record that names anything else.
        record = _clear_paseo_record(layout, out, "deleted the stale Paseo process record")
        if record.kind == "unreadable":
            raise SandboxRefusal(
                f"{layout.paseo_home / PASEO_PROCESS_RECORD} names process {record.pid}, a Paseo "
                "supervisor whose environment cannot be read, so it cannot be told whether it is "
                "this home's; nothing was signalled, deleted or started"
            )
    try:
        provision = ops.paseo(checkout, "provision")
    except StepFailed:
        # No report: the command did not say what it did. It counts as started by this run only
        # when a supervisor is there now that was not there before.
        after = ops.paseo_supervisor()
        started.paseo = after is not None and after != before
        raise
    daemon = provision.get("daemon")
    action = daemon.get("action") if isinstance(daemon, dict) else None
    started.paseo = action in STARTED_ACTIONS
    log = layout.paseo_home / "daemon.log"
    if provision.get("ok") is not True:
        raise StepFailed("paseo provision", _error_text(provision), log)
    supervisor = ops.paseo_supervisor()
    if supervisor is None:
        raise StepFailed(
            "paseo provision",
            "provision reported success but the Paseo home names no running daemon",
            log,
        )
    out(f"paseo runtime: {action} (pid {supervisor.pid}) on {layout.paseo_listen}")
    return supervisor


def _bring_up(
    layout: SandboxLayout, checkout: Path, ops: Operations, out: Out, observed: Observed
) -> int:
    record = {**read_record(layout), "checkout": checkout.as_posix(), "url": layout.dashboard_url}
    write_record(layout, record)
    if ops.removed_variables:
        out(
            "removed from the environment of what this start starts: "
            f"{', '.join(ops.removed_variables)}"
        )
    started = _Started()
    try:
        supervisor = _provision(layout, checkout, ops, out, started)
        record["paseo"] = supervisor.as_record()
        started.recorded_paseo = supervisor
        write_record(layout, record)
        dashboard = observed.dashboard
        if dashboard is None:
            dashboard = started.dashboard = ops.spawn_dashboard(checkout)
            record["dashboard"] = {**dashboard.as_record(), "log": layout.dashboard_log.as_posix()}
            write_record(layout, record)
        _await_dashboard(layout, ops, dashboard)
        state = "started" if started.dashboard is not None else "running"
        out(f"dashboard: {state} (pid {dashboard.pid}), log {layout.dashboard_log}")
    except StepFailed as failure:
        out(f"start failed at step '{failure.step}': {failure}")
        if failure.log is not None:
            out(f"log file: {failure.log}")
        _undo(layout, checkout, ops, out, started)
        return 1
    out(layout.dashboard_url)
    return 0


def _error_text(report: dict[str, Any]) -> str:
    error = report.get("error")
    if isinstance(error, dict):
        return " ".join(str(error[key]) for key in ("message", "detail") if error.get(key))
    return "the command reported a failure"


def _await_dashboard(layout: SandboxLayout, ops: Operations, dashboard: ProcessIdentity) -> None:
    answered = wait_until(
        lambda: ops.dashboard_answers() or not is_running(dashboard), ops.dashboard_wait_seconds
    )
    if not is_running(dashboard):
        raise StepFailed("dashboard", "the dashboard process exited", layout.dashboard_log)
    if not answered or not ops.dashboard_answers():
        raise StepFailed(
            "dashboard",
            f"the dashboard did not answer on {layout.dashboard_url} within "
            f"{ops.dashboard_wait_seconds:g} s",
            layout.dashboard_log,
        )
    if port_state(layout.dashboard_port, dashboard).foreign:
        raise StepFailed(
            "dashboard",
            f"port {layout.dashboard_port} is answered by another process",
            layout.dashboard_log,
        )


def _undo(
    layout: SandboxLayout, checkout: Path, ops: Operations, out: Out, started: _Started
) -> None:
    """Stop what this failed start itself started, and nothing that ran before it.

    The record loses only the entries this run wrote: an entry that names another process by
    now is someone else's and stays.
    """
    record = read_record(layout)
    if started.dashboard is not None:
        out(f"dashboard: {_stop_process_tree(started.dashboard)} (pid {started.dashboard.pid})")
        if ProcessIdentity.from_record(record.get("dashboard")) == started.dashboard:
            record.pop("dashboard", None)
    if started.paseo and ops.paseo_supervisor() is not None:
        stopped = ops.paseo(checkout, "stop", 120)
        out(f"paseo runtime: {stopped.get('action')} (pid {stopped.get('pid')})")
        recorded = ProcessIdentity.from_record(record.get("paseo"))
        if recorded is not None and recorded == started.recorded_paseo:
            record.pop("paseo", None)
    write_record(layout, record)


def _stop_process_tree(leader: ProcessIdentity) -> str:
    """Stop a process the sandbox started and whatever is left of its own session."""
    members = session_members(leader.pid) if is_running(leader) else []
    result = terminate(
        leader, grace_seconds=DASHBOARD_GRACE_SECONDS, kill_seconds=DASHBOARD_KILL_SECONDS
    )
    for member in members:
        terminate(member, grace_seconds=DASHBOARD_KILL_SECONDS, kill_seconds=DASHBOARD_KILL_SECONDS)
    return result


def _stop_checkout(layout: SandboxLayout, ops: Operations) -> Path:
    """The checkout whose Paseo stop command addresses the sandbox's runtime."""
    recorded = read_record(layout).get("checkout")
    for candidate in (Path(recorded) if isinstance(recorded, str) else None, TOOLING_CHECKOUT):
        if candidate is not None and ops.build_python(candidate).is_file():
            return candidate
    raise StepFailed(
        "paseo stop",
        "no PNT build checkout with a Python environment is known; run 'start <checkout>' "
        "once, or stop from a PNT build checkout",
    )


def _stop_dashboard(layout: SandboxLayout, out: Out) -> bool:
    """Stop the dashboard ``start`` recorded; whether it is still running afterwards."""
    recorded = ProcessIdentity.from_record(read_record(layout).get("dashboard"))
    if recorded is None:
        out("dashboard: not running")
        return False
    if not is_running(recorded):
        out(f"dashboard: not running (the recorded process {recorded.pid} is gone)")
        return False
    if not is_sandbox_dashboard(recorded.pid, layout):
        out(
            f"dashboard: not running (the recorded process {recorded.pid} is not a dashboard of "
            "this sandbox; nothing was signalled)"
        )
        return False
    result = _stop_process_tree(recorded)
    out(f"dashboard: {result} (pid {recorded.pid})")
    return result == "alive"


def _stop_paseo(layout: SandboxLayout, ops: Operations, out: Out) -> bool:
    """Stop the Paseo runtime of the sandbox's home; whether one is still running afterwards.

    The PNT-R01 stop is run only for a supervisor proven to be this home's. A record that names
    anything else is stale: it is deleted and the process it named is left alone.
    """
    supervisor = ops.paseo_supervisor()
    if supervisor is None:
        record = _clear_paseo_record(layout, out, "paseo runtime: deleted the stale record")
        if record.kind == "unreadable":
            out(
                f"paseo runtime: NOT STOPPED: {layout.paseo_home / PASEO_PROCESS_RECORD} names "
                f"process {record.pid}, a Paseo supervisor whose environment cannot be read, so "
                "it cannot be told whether it is this home's; nothing was signalled or deleted"
            )
            return True
        out("paseo runtime: not running")
        return False
    try:
        stopped = ops.paseo(_stop_checkout(layout, ops), "stop", 120)
    except StepFailed as failure:
        stopped = {"ok": False, "error": {"message": str(failure)}}
    if stopped.get("ok") is True:
        out(f"paseo runtime: {stopped.get('action')} (pid {stopped.get('pid')})")
    else:
        out(f"paseo runtime: STILL RUNNING (pid {supervisor.pid}): {_error_text(stopped)}")
    return ops.paseo_supervisor() is not None


def _report_unrecorded(layout: SandboxLayout, ops: Operations, out: Out) -> bool:
    """Name every process of this sandbox that is still running and that no record names.

    Looked for over all processes, by what they are: a dashboard by its command line and working
    directory, a supervisor by the home its environment names. A start that was killed between
    starting and recording leaves such a process; it is reported, not guessed at.
    """
    dashboards = unrecorded_dashboards(layout, recorded_dashboard(layout))
    supervisors = unrecorded_supervisors(layout.paseo_home, ops.paseo_supervisor())
    for pid in dashboards:
        out(
            f"dashboard: NOT STOPPED: process {pid} runs this sandbox's dashboard, but the "
            f"process record does not name it; it was not signalled. End it yourself (kill {pid})"
        )
    for pid in supervisors:
        out(
            f"paseo runtime: NOT STOPPED: process {pid} is a Paseo supervisor of this sandbox's "
            "home, but the home's process record does not name it; it was not signalled. End it "
            f"yourself (kill {pid})"
        )
    return bool(dashboards or supervisors)


def stop(layout: SandboxLayout, ops: Operations, out: Out) -> int:
    """Stop the sandbox's dashboard and Paseo runtime and report each; 0 when none remains."""
    if not layout.root.exists():
        out(f"no sandbox at {layout.root}")
        return 0
    with sandbox_lock(layout, "stop"):
        return _stop(layout, ops, out)


def _stop(layout: SandboxLayout, ops: Operations, out: Out) -> int:
    dashboard_remains = _stop_dashboard(layout, out)
    paseo_remains = _stop_paseo(layout, ops, out)
    tmux = ops.stop_tmux_server()
    if tmux is not None:
        out(f"terminal multiplexer server of the sandbox: {tmux}")
    unrecorded = _report_unrecorded(layout, ops, out)
    if dashboard_remains or paseo_remains or unrecorded:
        return 1
    layout.process_record.unlink(missing_ok=True)
    return 0


def _left(layout: SandboxLayout) -> list[Path]:
    return sorted(entry for entry in layout.root.iterdir() if entry != layout.marker)


def _delete(layout: SandboxLayout) -> tuple[list[Path], str]:
    """Delete the sandbox directory, its marker last; what is left of it, and why.

    Every entry is tried. While anything is left, the directory is still marked as this tool's
    and as being reset, so a deletion that failed half-way can be repeated and nothing is built
    or started in what remains.
    """
    builder.mark(layout, builder.RESETTING)
    reason = "something was written there during the deletion"
    for entry in _left(layout):
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError as error:
            reason = error.strerror or str(error)
    left = _left(layout)
    if left:
        return left, reason
    marker = layout.marker.read_bytes()
    layout.marker.unlink()
    try:
        layout.root.rmdir()
    except OSError as error:
        # Not empty after all, or not this user's to remove: what is there stays marked.
        layout.marker.write_bytes(marker)
        return _left(layout) or [layout.root], error.strerror or str(error)
    return [], ""


def reset(layout: SandboxLayout, ops: Operations, out: Out) -> int:
    """Stop the sandbox's processes and delete its directory."""
    if not layout.root.exists():
        out(f"no sandbox at {layout.root}")
        return 0
    if read_marker(layout) is None:
        raise SandboxRefusal(
            f"{layout.root} is not a sandbox this tool built; refusing to delete it"
        )
    with sandbox_lock(layout, "reset"):
        if _stop(layout, ops, out) != 0:
            out(f"a sandbox process is still running; {layout.root} was not deleted")
            return 1
        try:
            left, reason = _delete(layout)
        except OSError as error:
            left, reason = [layout.root], error.strerror or str(error)
        if left:
            out(
                f"{layout.root} could not be deleted completely ({reason}); left there: "
                f"{', '.join(entry.as_posix() for entry in left)}. It is still marked as this "
                "tool's sandbox: remove what blocks the deletion (a file or directory that cannot "
                "be written, or a program still writing there), then run 'reset' again"
            )
            return 1
    out(f"deleted {layout.root}")
    return 0
