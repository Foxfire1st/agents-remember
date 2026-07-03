"""Dashboard daemon supervision: detached serve, status/stop, and boot-time ensure.

``agents-remember dashboard --daemon`` and the MCP server's ``dashboard.autoStart``
settings key funnel into one decision function, ``ensure()``: adopt a healthy
daemon, spawn a missing one, restart on version/host/port mismatch (developer
decision: a stale dashboard is worse than a restart blip). State lives under
``<coordinationRoot>/logs/dashboard/``:

- ``daemon.json`` — pid, host, port, version, configPath, logPath, startedAt —
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

Liveness is ``kill(pid, 0)`` plus a ``/proc/<pid>/cmdline`` marker probe: the
recorded pid must still be an agents-remember dashboard, or reboots and pid
reuse would resurrect foreign processes (a zombie's empty cmdline fails the
probe too). On platforms without procfs the kill-probe alone decides.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.mcp import SERVER_VERSION
from agents_remember.mcp.config import McpRuntimeConfig

STATE_FILE_NAME = "daemon.json"
LOG_FILE_NAME = "dashboard.log"
LOCK_FILE_NAME = "ensure.lock"

_READY_TIMEOUT_SECONDS = 20.0
_STOP_TIMEOUT_SECONDS = 10.0
_CMDLINE_MARKERS = (b"agents_remember", b"agents-remember")

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


@dataclass(frozen=True)
class EnsureResult:
    action: str  # adopted | started | restarted | failed | lock-held
    state: DaemonState | None
    detail: str


def daemon_dir(config: McpRuntimeConfig) -> Path:
    return config.coordination_root / "logs" / "dashboard"


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
        )
    except (KeyError, TypeError, ValueError):
        return None


def write_state(directory: Path, state: DaemonState) -> None:
    """Atomic write (tmp + rename): readers never observe a torn state file."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / STATE_FILE_NAME
    tmp = path.with_suffix(".json.tmp")
    payload = {
        "pid": state.pid,
        "host": state.host,
        "port": state.port,
        "version": state.version,
        "configPath": state.config_path,
        "logPath": state.log_path,
        "startedAt": state.started_at,
    }
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def clear_state(directory: Path) -> None:
    (directory / STATE_FILE_NAME).unlink(missing_ok=True)


def probe(directory: Path) -> tuple[DaemonState | None, bool]:
    """The recorded state plus whether that pid is still OUR dashboard."""
    state = read_state(directory)
    if state is None:
        return None, False
    return state, _state_alive(state)


def _state_alive(state: DaemonState) -> bool:
    if not _pid_alive(state.pid):
        return False
    if not Path("/proc").exists():  # no procfs (e.g. macOS): kill-probe decides
        return True
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


def _pid_is_dashboard(pid: int) -> bool:
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    if b"dashboard" not in cmdline:
        return False
    return any(marker in cmdline for marker in _CMDLINE_MARKERS)


def spawn(
    config: McpRuntimeConfig,
    *,
    host: str,
    port: int,
    version: str,
    interval: float = 1.0,
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
        host,
        "--port",
        str(port),
        "--interval",
        str(interval),
        "--no-access-log",
    ]
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
        host=host,
        port=port,
        version=version,
        config_path=str(config.config_path),
        log_path=str(log_path),
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    write_state(directory, state)
    return state


def stop(directory: Path, *, timeout: float = _STOP_TIMEOUT_SECONDS) -> str:
    """TERM, bounded wait, KILL fallback; idempotent when nothing runs.

    Returns ``not-running`` | ``stopped`` | ``killed``. The state file is
    cleared in every branch — after stop, the record must not resurrect.
    """
    state, alive = probe(directory)
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
    *,
    host: str,
    port: int,
    version: str = SERVER_VERSION,
    interval: float = 1.0,
) -> EnsureResult:
    """Adopt a healthy daemon, spawn a missing one, restart a mismatched one.

    ``interval`` reaches the child only when this call spawns (or restarts) it;
    an adopted daemon keeps the cadence it was started with.
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
            return _ensure_locked(
                config, directory, host=host, port=port, version=version, interval=interval
            )
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _ensure_locked(
    config: McpRuntimeConfig,
    directory: Path,
    *,
    host: str,
    port: int,
    version: str,
    interval: float,
) -> EnsureResult:
    current, alive = probe(directory)
    mismatch = ""
    if alive and current is not None:
        if current.version == version and current.port == port and current.host == host:
            return EnsureResult(
                action="adopted",
                state=current,
                detail=f"healthy daemon pid {current.pid} on {_url(current)} (v{current.version})",
            )
        mismatch = _describe_mismatch(current, host=host, port=port, version=version)
        stop(directory)
    state = spawn(config, host=host, port=port, version=version, interval=interval)
    if not _wait_ready(state):
        if _pid_alive(state.pid):
            detail = (
                f"spawned pid {state.pid} but {_url(state)} did not accept within "
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
            detail=f"{mismatch}; now pid {state.pid} on {_url(state)} (v{version})",
        )
    return EnsureResult(
        action="started",
        state=state,
        detail=f"pid {state.pid} serving {_url(state)} (v{version}); log: {state.log_path}",
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
        result = ensure(config, host="127.0.0.1", port=config.dashboard.port)
        print(f"dashboard autostart: {result.action}: {result.detail}", file=sys.stderr)
    except Exception as error:  # boot must survive any autostart failure
        print(f"dashboard autostart: failed: {error}", file=sys.stderr)


def _describe_mismatch(current: DaemonState, *, host: str, port: int, version: str) -> str:
    reasons: list[str] = []
    if current.version != version:
        reasons.append(f"version {current.version} -> {version}")
    if current.port != port:
        reasons.append(f"port {current.port} -> {port}")
    if current.host != host:
        reasons.append(f"host {current.host} -> {host}")
    return f"restarting pid {current.pid} ({', '.join(reasons)})"


def _wait_ready(state: DaemonState, *, timeout: float = _READY_TIMEOUT_SECONDS) -> bool:
    """True once the child both stays alive and accepts TCP on its bind."""
    connect_host = state.host if state.host not in ("0.0.0.0", "::") else "127.0.0.1"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(state.pid):
            return False
        try:
            with socket.create_connection((connect_host, state.port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
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
            os.replace(log_path, log_path.with_suffix(".log.1"))
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
