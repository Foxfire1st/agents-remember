"""The start, stop, reset and safety-check commands of the sandbox.

``start`` refuses before it starts anything: a checkout that is not a PNT build, a reserved port
held by a process the sandbox did not start, or a root that does not resolve inside the sandbox
directory. It never chooses another port. ``stop`` signals only processes whose recorded identity
it has just re-read from ``/proc``.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import builder, safety
from .layout import SandboxLayout, SandboxRefusal, read_marker
from .operations import TOOLING_CHECKOUT, Operations, StepFailed, require_checkout
from .procfs import (
    ProcessIdentity,
    is_running,
    read_identity,
    session_members,
    terminate,
    wait_until,
)
from .record import (
    PASEO_PROCESS_RECORD,
    SUPERVISOR_COMMAND,
    PortState,
    paseo_record_pid,
    port_state,
    read_record,
    recorded_dashboard,
    write_record,
)

DASHBOARD_GRACE_SECONDS = 15.0
DASHBOARD_KILL_SECONDS = 5.0

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


def observe(layout: SandboxLayout, ops: Operations) -> Observed:
    dashboard = recorded_dashboard(layout)
    paseo = ops.paseo_supervisor()
    return Observed(
        dashboard,
        paseo,
        port_state(layout.dashboard_port, dashboard),
        port_state(layout.paseo_port, paseo),
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
    observed = observe(layout, ops)
    _refuse_foreign_ports(observed)
    if observed.running:
        out(f"already running: {layout.dashboard_url}")
        return 0
    # The build comes first: it marks the directory as a sandbox before any child process, which
    # all write their bytecode cache there, can create it.
    if not builder.is_built(layout):
        builder.build(layout, checkout, ops, out, eve_project)
    for line in [*ops.prepare_python(checkout), *ops.prepare_bundle(checkout)]:
        out(line)
    if not check(layout, checkout, ops, out):
        raise SandboxRefusal("the safety check failed; nothing was started")
    observed = observe(layout, ops)
    _refuse_foreign_ports(observed)
    if ops.removed_variables:
        out(f"removed from the environment of what is started: {', '.join(ops.removed_variables)}")
    return _bring_up(layout, checkout, ops, out, observed)


def _bring_up(
    layout: SandboxLayout, checkout: Path, ops: Operations, out: Out, observed: Observed
) -> int:
    record = {**read_record(layout), "checkout": checkout.as_posix(), "url": layout.dashboard_url}
    # A daemon that ran before this start is left running when the start fails.
    started_paseo = observed.paseo is None
    started_dashboard: ProcessIdentity | None = None
    write_record(layout, record)
    try:
        provision = ops.paseo(checkout, "provision")
        if provision.get("ok") is not True:
            raise StepFailed(
                "paseo provision", _error_text(provision), layout.paseo_home / "daemon.log"
            )
        supervisor = ops.paseo_supervisor()
        if supervisor is None:
            raise StepFailed(
                "paseo provision",
                "provision reported success but the Paseo home names no running daemon",
                layout.paseo_home / "daemon.log",
            )
        daemon = provision.get("daemon")
        action = daemon.get("action") if isinstance(daemon, dict) else "running"
        out(f"paseo runtime: {action} (pid {supervisor.pid}) on {layout.paseo_listen}")
        record["paseo"] = supervisor.as_record()
        write_record(layout, record)
        dashboard = observed.dashboard
        if dashboard is None:
            dashboard = started_dashboard = ops.spawn_dashboard(checkout)
            record["dashboard"] = {**dashboard.as_record(), "log": layout.dashboard_log.as_posix()}
            write_record(layout, record)
        _await_dashboard(layout, ops, dashboard)
        state = "started" if started_dashboard is not None else "running"
        out(f"dashboard: {state} (pid {dashboard.pid}), log {layout.dashboard_log}")
    except StepFailed as failure:
        out(f"start failed at step '{failure.step}': {failure}")
        if failure.log is not None:
            out(f"log file: {failure.log}")
        _undo(layout, ops, out, dashboard=started_dashboard, paseo=started_paseo)
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
    layout: SandboxLayout,
    ops: Operations,
    out: Out,
    *,
    dashboard: ProcessIdentity | None,
    paseo: bool,
) -> None:
    """Stop what this failed start itself started, and nothing that ran before it."""
    record = read_record(layout)
    if dashboard is not None:
        out(f"dashboard: {_stop_process_tree(dashboard)} (pid {dashboard.pid})")
        record.pop("dashboard", None)
    if paseo and ops.paseo_supervisor() is not None:
        stopped = ops.paseo(_stop_checkout(layout, ops), "stop", 120)
        out(f"paseo runtime: {stopped.get('action')} (pid {stopped.get('pid')})")
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
        "no PNT build checkout with a Python environment is known; stop the runtime with "
        f"'agents-remember paseo stop --config {layout.settings_file}' from a PNT build",
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
    result = _stop_process_tree(recorded)
    out(f"dashboard: {result} (pid {recorded.pid})")
    return result == "alive"


def _stop_paseo(layout: SandboxLayout, ops: Operations, out: Out) -> bool:
    """Stop the Paseo runtime of the sandbox's home; whether one is still running afterwards."""
    supervisor = ops.paseo_supervisor()
    if supervisor is not None:
        try:
            stopped = ops.paseo(_stop_checkout(layout, ops), "stop", 120)
        except StepFailed as failure:
            stopped = {"ok": False, "error": {"message": str(failure)}}
        if stopped.get("ok") is True:
            out(f"paseo runtime: {stopped.get('action')} (pid {stopped.get('pid')})")
        else:
            out(f"paseo runtime: STILL RUNNING (pid {supervisor.pid}): {_error_text(stopped)}")
        return ops.paseo_supervisor() is not None
    named = paseo_record_pid(layout.paseo_home)
    if named is None:
        out("paseo runtime: not running")
        return False
    live = read_identity(named)
    if live is None or live.argv[:1] != (SUPERVISOR_COMMAND,):
        out(
            f"paseo runtime: not running ({PASEO_PROCESS_RECORD} is stale: process {named} is "
            "not a Paseo supervisor; nothing was signalled)"
        )
        return False
    out(
        f"paseo runtime: NOT STOPPED: {PASEO_PROCESS_RECORD} names a Paseo supervisor (pid "
        f"{named}) that could not be proven to be the one this home started; nothing was "
        f"signalled. Stop it with 'agents-remember paseo stop --config {layout.settings_file}'."
    )
    return True


def stop(layout: SandboxLayout, ops: Operations, out: Out) -> int:
    """Stop the sandbox's dashboard and Paseo runtime and report each; 0 when none remains."""
    dashboard_remains = _stop_dashboard(layout, out)
    paseo_remains = _stop_paseo(layout, ops, out)
    tmux = ops.stop_tmux_server()
    if tmux is not None:
        out(f"terminal multiplexer server of the sandbox: {tmux}")
    if dashboard_remains or paseo_remains:
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
    if stop(layout, ops, out) != 0:
        out(f"a sandbox process is still running; {layout.root} was not deleted")
        return 1
    shutil.rmtree(layout.root)
    out(f"deleted {layout.root}")
    return 0
