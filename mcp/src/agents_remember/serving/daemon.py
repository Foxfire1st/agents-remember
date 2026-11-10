"""Dashboard daemon supervision: detached serve, status/stop, and boot-time ensure.

``agents-remember dashboard --daemon`` and the MCP server's ``dashboard.autoStart``
settings key funnel into one decision function, ``ensure()``: adopt a healthy
daemon, spawn a missing one, restart on source digest/version/host/port mismatch (developer
decision: a stale dashboard is worse than a restart blip). State lives under
``<coordinationRoot>/logs/dashboard/``:

- ``daemon.json`` — pid, host, port, version, sourceDigest, configPath, logPath, startedAt —
  written atomically and immediately after spawn, so a dying supervisor never
  strands an unrecorded child;
- ``dashboard.log`` — the child's stdio, rotated to ``.1`` per spawn (the child
  serves with ``--no-access-log`` so per-request noise never grows it);
- ``ensure.lock`` — one non-blocking flock making concurrent MCP boots race-safe:
  losers report ``lock-held`` and skip, they never double-spawn.

The child is the plain foreground ``dashboard`` CLI addressed by module string
(``sys.executable -m agents_remember.cli``), never imported: this module stays
import-light (stdlib + settings types only) so the MCP server can call
``maybe_autostart_dashboard`` without pulling uvicorn/FastAPI into its boot, and
the autostart worker runs in a daemon thread whose only output goes to stderr —
over stdio transport, stdout IS the MCP protocol.

The build comes from the HTTP answer; the listening process comes from native socket
ownership. A saved PID never authorizes a signal by itself. The live process must also
still name the product dashboard in its command line: PID reuse and zombies cannot
resurrect a foreign process through an old state file.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_replace, atomic_write_text
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    McpRuntimeConfig,
)
from agents_remember.kernel.primitives.version import _resolve_server_version
from agents_remember.models.build_currentness import BuildCurrentnessPayload
from agents_remember.serving.build_currentness import inspect_build
from agents_remember.serving.build_info import process_serving_build
from agents_remember.serving.cadence import DEFAULT_PROJECTION_CADENCE, ProjectionCadence
from agents_remember.serving.daemon_identity import (
    DashboardIdentity,
    observe_dashboard,
    require_dashboard_root,
)
from agents_remember.serving.daemon_processes import pid_is_dashboard as _pid_is_dashboard
from agents_remember.serving.paseo.paseo_start import ensure_host

STATE_FILE_NAME = "daemon.json"
LOG_FILE_NAME = "dashboard.log"
LOCK_FILE_NAME = "ensure.lock"

_READY_TIMEOUT_SECONDS = 20.0
_STOP_TIMEOUT_SECONDS = 10.0

# Spawned Popen handles, kept so a long-lived supervisor (the MCP server) reaps
# a daemon child that exits instead of leaving a zombie holding its pid.
_spawned: list[subprocess.Popen[bytes]] = []


@dataclass(frozen=True)
class DaemonState:
    """The recorded identity of the running (or last-spawned) dashboard daemon."""

    pid: int
    host: str
    port: int
    version: str
    config_path: str
    log_path: str
    started_at: str
    source_digest: str | None = None
    build_currentness: BuildCurrentnessPayload | None = None


def _installed_digest() -> str | None:
    return inspect_build(process_serving_build(), role="dashboard").installedDigest


@dataclass(frozen=True)
class DaemonEndpoint:
    """Which daemon this is: where it serves and which build it runs.

    Adoption compares the endpoint, version and Python source digest (see :func:`_describe_mismatch`).
    A daemon matching only some of them is still the wrong daemon, so they are compared, recorded and
    passed as one value rather than three parallel arguments.
    """

    host: str
    port: int
    version: str = field(default_factory=_resolve_server_version)
    source_digest: str | None = field(default_factory=_installed_digest)


@dataclass(frozen=True)
class EnsureResult:
    action: str  # adopted | started | restarted | unknown | failed | lock-held
    state: DaemonState | None
    detail: str


def daemon_dir(config: McpRuntimeConfig) -> Path:
    return daemon_state_directory(config.coordination_root)


def daemon_state_directory(coordination_root: Path) -> Path:
    return coordination_root / "logs" / "dashboard"


def read_state(directory: Path) -> DaemonState | None:
    """The recorded daemon state, or None when absent or unreadable."""
    path = directory / STATE_FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return DaemonState(
            pid=int(data["pid"]),
            host=str(data["host"]),
            port=int(data["port"]),
            version=str(data["version"]),
            config_path=str(data["configPath"]),
            log_path=str(data["logPath"]),
            started_at=str(data["startedAt"]),
            source_digest=data.get("sourceDigest"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def write_state(directory: Path, state: DaemonState) -> None:
    """Atomic write (tmp + rename): readers never observe a torn state file."""
    path = directory / STATE_FILE_NAME
    payload = {
        "pid": state.pid,
        "host": state.host,
        "port": state.port,
        "version": state.version,
        "configPath": state.config_path,
        "logPath": state.log_path,
        "startedAt": state.started_at,
        "sourceDigest": state.source_digest,
    }
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n")


def clear_state(directory: Path) -> None:
    (directory / STATE_FILE_NAME).unlink(missing_ok=True)


def probe(
    directory: Path, endpoint: DaemonEndpoint | None = None
) -> tuple[DaemonState | None, bool]:
    """Observe the port's real dashboard and repair a mismatched saved identity."""
    saved = read_state(directory)
    current = _observed_state(directory, saved, endpoint)
    if current is None:
        return saved, False
    if saved is None or replace(current, build_currentness=None) != replace(
        saved, build_currentness=None
    ):
        write_state(directory, current)
    return current, _state_alive(current)


def _observed_state(
    directory: Path, saved: DaemonState | None, endpoint: DaemonEndpoint | None
) -> DaemonState | None:
    endpoints = []
    if saved is not None:
        endpoints.append((saved.host, saved.port))
    if endpoint is not None and (endpoint.host, endpoint.port) not in endpoints:
        endpoints.append((endpoint.host, endpoint.port))
    unknown = None
    for host, port in endpoints:
        try:
            observed = observe_dashboard(host, port)
            if observed is None:
                continue
            require_dashboard_root(observed, directory.parent.parent)
            return _observed_daemon(directory, saved, host, port, observed)
        except ConfigError as error:
            unknown = error
    if unknown is not None:
        raise unknown
    return None


def _observed_daemon(
    directory: Path, saved: DaemonState | None, host: str, port: int, observed: DashboardIdentity
) -> DaemonState:
    build = observed.build
    return DaemonState(
        pid=observed.pid,
        host=host,
        port=port,
        version=build.version if build is not None else "unknown",
        source_digest=build.sourceDigest if build is not None else None,
        config_path=observed.config_path or (saved.config_path if saved is not None else ""),
        log_path=str(directory / LOG_FILE_NAME),
        started_at=build.bootedAt if build is not None else "",
        build_currentness=observed.currentness,
    )


def plan_running_dashboard(config: McpRuntimeConfig) -> EnsureResult | None:
    """Read-only install preview: name the observed daemon and required build transition."""
    directory = daemon_dir(config)
    endpoint = DaemonEndpoint(host="127.0.0.1", port=config.dashboard.port)
    try:
        current = _observed_state(directory, read_state(directory), endpoint)
    except ConfigError as error:
        return EnsureResult("failed", read_state(directory), str(error))
    if current is None:
        return None
    target = DaemonEndpoint(host=current.host, port=current.port)
    if target.source_digest is None:
        return EnsureResult(
            "unknown",
            current,
            "The installed Python digest could not be computed; dashboard build currentness is unknown.",
        )
    if current.version == target.version and current.source_digest == target.source_digest:
        return EnsureResult(
            "would-adopt", current, f"current daemon pid {current.pid} on {_url(current)}"
        )
    return EnsureResult("would-restart", current, _describe_mismatch(current, target))


def ensure_running_dashboard(config: McpRuntimeConfig) -> EnsureResult | None:
    """Refresh a running dashboard after install; leave an absent one stopped."""
    directory = daemon_dir(config)
    endpoint = DaemonEndpoint(host="127.0.0.1", port=config.dashboard.port)
    try:
        current, alive = probe(directory, endpoint)
        if not alive or current is None:
            return None
        return ensure(config, DaemonEndpoint(host=current.host, port=current.port))
    except (OSError, ConfigError) as error:
        return EnsureResult(
            "failed",
            read_state(directory),
            f"dashboard restart failed: {error}; restart the dashboard",
        )


def _state_alive(state: DaemonState) -> bool:
    if not _pid_alive(state.pid):
        return False
    return _pid_is_dashboard(state.pid)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def spawn(
    config: McpRuntimeConfig,
    endpoint: DaemonEndpoint,
    *,
    cadence: ProjectionCadence = DEFAULT_PROJECTION_CADENCE,
) -> DaemonState:
    """Launch the detached foreground CLI and record it immediately.

    ``start_new_session`` detaches the child from the caller's terminal and
    process group, so it survives the tab that started it. The state file is
    written before any readiness check: a supervisor dying mid-ensure must not
    strand a running child that nothing remembers.
    """
    directory = daemon_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / LOG_FILE_NAME
    _rotate_log(log_path)
    command = [
        sys.executable,
        "-m",
        "agents_remember.cli",
        "dashboard",
        "--config",
        str(config.config_path),
        "--host",
        endpoint.host,
        "--port",
        str(endpoint.port),
        "--interval",
        str(cadence.interval),
    ]
    if cadence.heartbeat is not None:
        command += ["--heartbeat", str(cadence.heartbeat)]
    command += ["--no-access-log", "--host-start-owner", str(os.getpid())]
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            cwd=str(directory),
        )
    _spawned.append(process)
    state = DaemonState(
        pid=process.pid,
        host=endpoint.host,
        port=endpoint.port,
        version=endpoint.version,
        config_path=str(config.config_path),
        log_path=str(log_path),
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        source_digest=endpoint.source_digest,
    )
    write_state(directory, state)
    return state


def stop(directory: Path, *, timeout: float = _STOP_TIMEOUT_SECONDS) -> str:
    """TERM, bounded wait, KILL fallback; idempotent when nothing runs.

    Unknown ownership preserves the record and authorizes no signal. Proven
    stopped processes have their state cleared.
    """
    try:
        state, alive = probe(directory)
    except ConfigError as error:
        return f"identity-unknown: {error}"
    if state is None or not alive:
        clear_state(directory)
        return "not-running"
    try:
        os.kill(state.pid, signal.SIGTERM)
    except ProcessLookupError:
        clear_state(directory)
        return "not-running"
    if _wait_gone(state.pid, timeout=timeout):
        clear_state(directory)
        return "stopped"
    with contextlib.suppress(ProcessLookupError):
        os.kill(state.pid, signal.SIGKILL)
    _wait_gone(state.pid, timeout=2.0)
    clear_state(directory)
    return "killed"


def ensure(
    config: McpRuntimeConfig,
    endpoint: DaemonEndpoint,
    *,
    cadence: ProjectionCadence = DEFAULT_PROJECTION_CADENCE,
) -> EnsureResult:
    """Adopt a healthy daemon, spawn a missing one, restart a mismatched one.

    ``cadence`` reaches the child only when this call spawns (or restarts) it; an adopted daemon
    keeps the cadences it was started with.
    """
    directory = daemon_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    _reap_spawned()
    with (directory / LOCK_FILE_NAME).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return EnsureResult(
                action="lock-held",
                state=read_state(directory),
                detail="another supervisor is ensuring the dashboard daemon; skipped",
            )
        try:
            return _ensure_locked(config, directory, endpoint, cadence=cadence)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _ensure_locked(
    config: McpRuntimeConfig,
    directory: Path,
    endpoint: DaemonEndpoint,
    *,
    cadence: ProjectionCadence,
) -> EnsureResult:
    host, port, version = endpoint.host, endpoint.port, endpoint.version
    try:
        current, alive = probe(directory, endpoint)
    except ConfigError as error:
        return EnsureResult("failed", read_state(directory), str(error))
    mismatch = ""
    if alive and current is not None:
        if endpoint.source_digest is None:
            return EnsureResult(
                "unknown",
                current,
                "The installed Python digest could not be computed; dashboard build currentness is unknown.",
            )
        if (
            current.version == version
            and current.port == port
            and current.host == host
            and current.source_digest == endpoint.source_digest
        ):
            return EnsureResult(
                action="adopted",
                state=current,
                detail=f"healthy daemon pid {current.pid} on {_url(current)} (v{current.version})",
            )
        mismatch = _describe_mismatch(current, endpoint)
        stop_result = stop(directory)
        if _state_alive(current):
            write_state(directory, current)
            return EnsureResult(
                "failed",
                current,
                f"{mismatch}; restart failed ({stop_result}): stale daemon pid {current.pid} still serves {_url(current)}; restart the dashboard",
            )
    return _spawn_ready(config, endpoint, directory, cadence, mismatch)


def _spawn_ready(
    config: McpRuntimeConfig,
    endpoint: DaemonEndpoint,
    directory: Path,
    cadence: ProjectionCadence,
    mismatch: str,
) -> EnsureResult:
    state = spawn(config, endpoint, cadence=cadence)
    try:
        ready = _wait_ready(state)
    except ConfigError as error:
        return EnsureResult("failed", state, str(error))
    if not ready:
        if _pid_alive(state.pid):
            detail = (
                f"spawned pid {state.pid} but {_url(state)} did not prove its dashboard identity within "
                f"{_READY_TIMEOUT_SECONDS:.0f}s; it may still be starting — see {state.log_path}"
            )
        else:
            clear_state(directory)
            detail = f"daemon exited during startup; log tail:\n{_log_tail(Path(state.log_path))}"
        return EnsureResult(action="failed", state=state, detail=detail)
    if mismatch:
        return EnsureResult(
            action="restarted",
            state=state,
            detail=f"{mismatch}; now pid {state.pid} on {_url(state)} (v{endpoint.version})",
        )
    return EnsureResult(
        action="started",
        state=state,
        detail=f"pid {state.pid} serving {_url(state)} (v{endpoint.version}); log: {state.log_path}",
    )


def maybe_autostart_dashboard(config: McpRuntimeConfig) -> threading.Thread | None:
    """The MCP boot hook: total (never raises) and non-blocking (daemon thread).

    A no-op unless the trusted settings set ``dashboard.autoStart``. All
    reporting goes to stderr — the caller is a stdio MCP server whose stdout
    carries the protocol.
    """
    try:
        if not config.dashboard.auto_start:
            return None
        worker = threading.Thread(
            target=_autostart,
            args=(config,),
            name="ar-dashboard-autostart",
            daemon=True,
        )
        worker.start()
        return worker
    except Exception as error:  # boot must survive any autostart failure
        print(f"dashboard autostart: failed to launch: {error}", file=sys.stderr)
        return None


def _autostart(config: McpRuntimeConfig) -> None:
    try:
        result = ensure(config, DaemonEndpoint(host="127.0.0.1", port=config.dashboard.port))
        print(f"dashboard autostart: {result.action}: {result.detail}", file=sys.stderr)
        host = ensure_host(config)
        print(host.line, file=sys.stderr)
        if result.action in ("adopted", "unknown"):
            record_host_outcome(config, host.line)
    except Exception as error:  # boot must survive any autostart failure
        print(f"dashboard autostart: failed: {error}", file=sys.stderr)


def _describe_mismatch(current: DaemonState, endpoint: DaemonEndpoint) -> str:
    host, port, version = endpoint.host, endpoint.port, endpoint.version
    reasons: list[str] = []
    if current.version != version:
        reasons.append(f"version {current.version} -> {version}")
    if current.port != port:
        reasons.append(f"port {current.port} -> {port}")
    if current.host != host:
        reasons.append(f"host {current.host} -> {host}")
    if current.source_digest != endpoint.source_digest:
        reasons.append(f"source digest {current.source_digest} -> {endpoint.source_digest}")
    return f"restarting pid {current.pid} ({', '.join(reasons)})"


def _wait_ready(state: DaemonState, *, timeout: float = _READY_TIMEOUT_SECONDS) -> bool:
    """True once this child, rather than another listener, answers with its build."""
    deadline = time.monotonic() + timeout
    unknown = None
    while time.monotonic() < deadline:
        if not _pid_alive(state.pid):
            return False
        try:
            observed = observe_dashboard(state.host, state.port)
        except ConfigError as error:
            unknown = error
            observed = None
        if (
            observed is not None
            and observed.build is not None
            and observed.pid == state.pid
            and observed.build.sourceDigest == state.source_digest
            and observed.build.version == state.version
        ):
            return True
        time.sleep(0.25)
    if unknown is not None:
        raise unknown
    return False


def _wait_gone(pid: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _reap_spawned()
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    return not _pid_alive(pid)


def _reap_spawned() -> None:
    """Collect exited children spawned by this process so their pids free up."""
    for process in list(_spawned):
        if process.poll() is not None:
            _spawned.remove(process)


def _rotate_log(log_path: Path) -> None:
    try:
        if log_path.exists() and log_path.stat().st_size > 0:
            atomic_replace(log_path, log_path.with_suffix(".log.1"))
    except OSError:
        pass  # rotation is best-effort; a spawn must not fail over log shuffling


def _log_tail(log_path: Path, *, lines: int = 15) -> str:
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(log unreadable)"
    tail = content.splitlines()[-lines:]
    return "\n".join(tail) if tail else "(log empty)"


def _url(state: DaemonState) -> str:
    display_host = state.host if state.host not in ("0.0.0.0", "::") else "127.0.0.1"
    return f"http://{display_host}:{state.port}/"


def record_host_outcome(config: McpRuntimeConfig, line: str) -> None:
    """Bound the diagnostic log in place; an adopted dashboard keeps its open append handle."""
    path = daemon_dir(config) / LOG_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a+b") as log:
            if log.seek(0, os.SEEK_END) > 1024 * 1024:
                log.seek(-512 * 1024, os.SEEK_END)
                tail = log.read(512 * 1024)
                log.truncate(0)
                log.write(tail)
            log.write((line + "\n").encode("utf-8"))
    except OSError as error:
        print(f"host outcome log unavailable: {error}", file=sys.stderr)
