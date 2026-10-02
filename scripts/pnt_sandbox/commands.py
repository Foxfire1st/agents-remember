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
    PortState,
    clear_stale_paseo_record,
    is_sandbox_dashboard,
    port_state,
    read_record,
    recorded_dashboard,
    unrecorded_dashboards,
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


def _refuse_foreign_ports(layout: SandboxLayout, observed: Observed) -> None:
    for state in (observed.paseo_port, observed.dashboard_port):
        if state.foreign:
            holders = ", ".join(
                "unknown (not this user's process)" if pid is None else str(pid)
                for pid in state.foreign
            )
            unrecorded = [
                pid
                for pid in state.foreign
                if pid is not None and is_sandbox_dashboard(pid, layout)
            ]
            hint = (
                f" (process {unrecorded[0]} runs this sandbox's dashboard, but the process record "
                "does not name it: end it yourself)"
                if unrecorded
                else ""
            )
            raise SandboxRefusal(
                f"port {state.port} is held by process {holders}, which this sandbox did not "
                f"start{hint}; nothing was started and no other port is used"
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
    with sandbox_lock(layout, "start"):
        return _start(layout, checkout, ops, out, eve_project)


def _start(
    layout: SandboxLayout, checkout: Path, ops: Operations, out: Out, eve_project: Path
) -> int:
    observed = observe(layout, ops)
    _refuse_foreign_ports(layout, observed)
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
    _refuse_foreign_ports(layout, observed)
    return _bring_up(layout, checkout, ops, out, observed)


def _provision(
    layout: SandboxLayout, checkout: Path, ops: Operations, out: Out, started: _Started
) -> ProcessIdentity:
    """Bring the Paseo runtime up through PNT-R01; the proven supervisor of the sandbox home."""
    if ops.paseo_supervisor() is not None:
        # It ran before this start and has been checked; provision may reload it, and when it
        # restarts it, the report says so.
        stale = None
    else:
        stale = clear_stale_paseo_record(layout.paseo_home)
    if stale is not None:
        out(
            f"deleted the stale Paseo process record {layout.paseo_home / PASEO_PROCESS_RECORD}: "
            f"it named process {stale}, which is not this home's Paseo supervisor; that process "
            "was not signalled"
        )
    try:
        provision = ops.paseo(checkout, "provision")
    except StepFailed:
        # No report: the command did not say what it did, so what is there now decides.
        started.paseo = ops.paseo_supervisor() is not None
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
        stale = clear_stale_paseo_record(layout.paseo_home)
        if stale is None:
            out("paseo runtime: not running")
        else:
            out(
                "paseo runtime: not running (deleted the stale record "
                f"{layout.paseo_home / PASEO_PROCESS_RECORD}: it named process {stale}, which is "
                "not this home's Paseo supervisor; nothing was signalled)"
            )
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
    # The record is the only thing start leaves behind; a dashboard it does not name (a start
    # that was killed between starting and recording it) is reported, not guessed at.
    unrecorded = unrecorded_dashboards(layout, None)
    for pid in unrecorded:
        out(
            f"dashboard: NOT STOPPED: process {pid} runs this sandbox's dashboard and holds a "
            "reserved port, but the process record does not name it; it was not signalled. End "
            f"it yourself (kill {pid})"
        )
    if dashboard_remains or paseo_remains or unrecorded:
        return 1
    layout.process_record.unlink(missing_ok=True)
    return 0


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
            shutil.rmtree(layout.root)
        except OSError as error:
            out(
                f"{layout.root} could not be deleted completely: {error}; what remains is at "
                f"{error.filename or layout.root}"
            )
            return 1
    out(f"deleted {layout.root}")
    return 0
