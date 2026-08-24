"""CLI adapter: run the local mission-control dashboard server.

Mirrors the MCP server's ``--config`` contract (``load_config`` over the same trusted MCP
settings JSON), so the dashboard resolves the identical coordination context. ``--sim``
replays a recorded observer fixture through the byte-identical serving path instead of live
state; the sim's throwaway root is held alive for the server's lifetime.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, NamedTuple

import uvicorn

import agents_remember
from agents_remember.application.task_docs.task_execution_registration import (
    register_operator_inbox_execution_evidence,
    register_terminal_catalog_execution_evidence,
)
from agents_remember.cli.discovery import ConfigDiscoveryError, discover_config
from agents_remember.controlplane.durable_store import declare_process_role
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    load_config,
)
from agents_remember.serving import daemon as serving_daemon
from agents_remember.serving._app_common import ServingCollaborators
from agents_remember.serving.app import create_app
from agents_remember.serving.change_watcher import DEFAULT_HEARTBEAT_SECONDS
from agents_remember.serving.projector import ProjectionCadence, ProjectionReplay
from agents_remember.serving.sim import SimError, SimSetup, build_sim, parse_sim_speed

# Dev hot-reload (``--reload``): uvicorn's reloader re-imports the app per worker restart, so it
# needs an import-string *factory*, not a pre-built app object. Handing it an object does NOT
# silently disable reload -- uvicorn refuses to start, loudly. uvicorn 0.49.0, ``uvicorn/main.py``
# lines 604-607: ``if (config.reload or config.workers > 1) and not isinstance(app, str):`` logs the
# ``uvicorn.error`` warning "You must pass the application as an import string to enable 'reload' or
# 'workers'." and calls ``sys.exit(1)`` (measured: exit code 1, before the port is bound).
# The factory reads the resolved config from the environment the parent ``run`` sets.
_DEV_CONFIG_ENV = "AR_DASHBOARD_DEV_CONFIG"
_DEV_INTERVAL_ENV = "AR_DASHBOARD_DEV_INTERVAL"
_DEV_HEARTBEAT_ENV = "AR_DASHBOARD_DEV_HEARTBEAT"

# The dashboard always has a browser tab holding an EventSource open on
# ``/api/stream`` (and ``/api/events``). Those handlers are ``while True`` streaming responses that
# never return on their own, so uvicorn's *unbounded* default graceful shutdown waits for them
# forever on SIGTERM: the listening socket closes (port released) but the process hangs as a zombie
# with its landing sweep still spawning git/gh -- exactly the three survivors observed live, killable
# only by SIGKILL. A bounded graceful window makes SIGTERM force-cancel the lingering streams and run
# the lifespan shutdown (which cancels the projector/landing/agent-notifier tasks) within a few seconds.
# Fast API/JSON requests finish well inside this window; only the intentionally-endless SSE streams
# are force-closed, which is exactly the desired behaviour on shutdown. uvicorn types this as whole
# seconds (``int | None``), so keep it an int.
DASHBOARD_GRACEFUL_SHUTDOWN_SECONDS = 3
EXECUTION_REGISTRATION_COLLABORATORS = ServingCollaborators(
    register_terminal_execution_evidence=register_terminal_catalog_execution_evidence,
    register_inbox_execution_evidence=register_operator_inbox_execution_evidence,
)


def _dev_app():
    """Zero-arg app factory for ``uvicorn --reload`` (live state only; never sim).

    This is the reload worker's PROCESS ENTRY POINT, which is why it declares the durable-store
    role even though :func:`run` declares it too and ``create_app`` deliberately does not.
    uvicorn 0.49.0 starts the reload worker with ``multiprocessing.get_context("spawn")``
    (``uvicorn/_subprocess.py``, ``supervisors/basereload.py``), and a spawn child re-imports
    every module: it does NOT inherit the parent's ``declare_process_role("dashboard")`` and it
    never runs :func:`run`. Measured before this line existed, the reload worker saw an empty
    declaration and so counted as compaction owner of every control-plane log -- running the gate
    reclaim a foreground dashboard deliberately skips. Not a durability defect (the per-log lock
    is unconditional and covered the rewrite), but it made the three serving modes disagree about
    what this process is. Foreground and ``--daemon`` already declared correctly (the daemon
    re-enters ``run`` via ``-m agents_remember.cli dashboard``); this makes ``--reload`` agree.

    Declaring in THIS factory and not in ``create_app`` is the distinction that matters: this one
    is reload-only -- it reads its config from ``AR_DASHBOARD_DEV_CONFIG`` and cannot serve a sim
    -- whereas ``create_app`` is called in-process by the test suite, where a declaration would
    stamp "dashboard" onto every later test in the same interpreter.
    """
    declare_process_role("dashboard")
    config = load_config(os.environ[_DEV_CONFIG_ENV])
    heartbeat_env = os.environ.get(_DEV_HEARTBEAT_ENV)
    return create_app(
        config,
        cadence=ProjectionCadence(
            interval=float(os.environ.get(_DEV_INTERVAL_ENV, "1.0")),
            heartbeat=float(heartbeat_env) if heartbeat_env else None,
        ),
        collaborators=EXECUTION_REGISTRATION_COLLABORATORS,
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=None,
        help="Path to trusted MCP settings JSON. Omit to discover it from the working "
        "directory: the nearest .claude/mcp/agents-remember-settings.json, or the "
        "--config recorded in an .mcp.json agents-remember entry.",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (localhost-only by default; do not expose)."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: the dashboard.port settings key, else 8765).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Fast-path projection cadence floor, seconds: change-driven re-projections are "
        "never spaced closer than this, a continuously-busy world still projects once per "
        "interval, and it stays the fixed tick cadence under --sim or when the change "
        "watcher is unavailable. Also the /api/events raw-tail poll cadence.",
    )
    parser.add_argument(
        "--heartbeat",
        type=float,
        default=None,
        help="Idle re-projection heartbeat, seconds (default "
        f"{DEFAULT_HEARTBEAT_SECONDS:g}). With no detected input change the projection "
        "still refreshes at this cadence -- the staleness bound for /api/state and for "
        "time-derived fields (ageSeconds/staleSeconds and stale/overdue flips).",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Dev hot-reload: re-import the serving app on source change (uvicorn reloader). "
        "Live state only; not compatible with --sim.",
    )
    parser.add_argument(
        "--sim",
        default=None,
        help="Replay a recorded observer fixture dir (with logs/observer/...) instead of live.",
    )
    parser.add_argument(
        "--sim-speed",
        default="1",
        help="Sim replay speed multiplier (e.g. 1, 10) or 'paused' (default 1).",
    )
    control = parser.add_mutually_exclusive_group()
    control.add_argument(
        "--daemon",
        action="store_true",
        help="Detach: ensure a background dashboard daemon (state and log under "
        "<coordinationRoot>/logs/dashboard/) and return. Adopts a healthy matching daemon; "
        "restarts one whose version, host, or port differs.",
    )
    control.add_argument(
        "--status",
        action="store_true",
        help="Report the dashboard daemon's state and exit (exit 0 running, 1 not).",
    )
    control.add_argument(
        "--stop",
        action="store_true",
        help="Stop the dashboard daemon (TERM, bounded wait, KILL fallback) and exit.",
    )
    parser.add_argument(
        "--no-access-log",
        action="store_true",
        help="Serve without per-request access logs (the daemon child uses this to keep "
        "its log file bounded).",
    )


def run(args: argparse.Namespace) -> int:
    # Which of the two concurrent durable-store writers THIS PROCESS is
    # (controlplane/durable_store.py). Declared at the process entry point rather than inside
    # create_app, because the role is a fact about the process: create_app is a factory the
    # test suite calls in-process, and declaring there would stamp "dashboard" onto every
    # later test in the same interpreter.
    declare_process_role("dashboard")
    resolved = _resolve_settings(args)
    if resolved is None:
        return 1
    config_path, config = resolved
    port = args.port if args.port is not None else config.dashboard.port
    if args.daemon or args.status or args.stop:
        return _run_daemon_command(args, config, port)
    if args.reload:
        return _run_reload_server(args, config_path, port)
    # ``built`` keeps the sim (and its temp coordination root) referenced until this call
    # returns, i.e. for the whole server lifetime, so the sim root is not reclaimed early.
    built = _build_app(args, config)
    if built is None:
        return 1
    try:
        uvicorn.run(
            built.app,
            host=args.host,
            port=port,
            access_log=not args.no_access_log,
            timeout_graceful_shutdown=DASHBOARD_GRACEFUL_SHUTDOWN_SECONDS,
        )
    finally:
        # The server has stopped, so the throwaway root has no reader left. Closing it here
        # is what ends its life: dropping the reference instead leaves the directory to
        # ``TemporaryDirectory``'s finaliser, which is a ResourceWarning, not a cleanup.
        if built.sim is not None:
            built.sim.temp_dir.cleanup()
    return 0


def _resolve_settings(
    args: argparse.Namespace,
) -> tuple[str, serving_daemon.McpRuntimeConfig] | None:
    """Discover and load the trusted settings; report the failure and return None on error."""
    try:
        config_path = args.config or discover_config()
        return str(config_path), load_config(config_path)
    except (ConfigDiscoveryError, ConfigError) as error:
        print(f"error: {error}")
        return None


def _run_reload_server(args: argparse.Namespace, config_path: str, port: int) -> int:
    """Serve under uvicorn's reloader, which re-imports the app on every source change."""
    if args.sim:
        print("error: --reload is not supported with --sim")
        return 1
    # Pass an import-string factory (not the built app object) so uvicorn's reloader can
    # re-import on change; watch only the package source so node_modules/.git don't churn it.
    os.environ[_DEV_CONFIG_ENV] = str(Path(config_path).resolve())
    os.environ[_DEV_INTERVAL_ENV] = str(args.interval)
    if args.heartbeat is not None:
        os.environ[_DEV_HEARTBEAT_ENV] = str(args.heartbeat)
    uvicorn.run(
        "agents_remember.cli.dashboard:_dev_app",
        factory=True,
        reload=True,
        reload_dirs=[str(Path(agents_remember.__file__).parent)],
        host=args.host,
        port=port,
        access_log=not args.no_access_log,
        timeout_graceful_shutdown=DASHBOARD_GRACEFUL_SHUTDOWN_SECONDS,
    )
    return 0


class _DashboardApp(NamedTuple):
    """The serving app together with the sim whose throwaway root it reads (None when live).

    The sim is carried alongside the app rather than dropped at build time because it owns
    that root: releasing it would reclaim the directory under the running server.
    """

    app: Any
    sim: SimSetup | None


def _build_app(
    args: argparse.Namespace, config: serving_daemon.McpRuntimeConfig
) -> _DashboardApp | None:
    """The app to serve — live state, or a replayed fixture; None when the fixture is unusable."""
    if not args.sim:
        return _DashboardApp(
            create_app(
                config,
                cadence=ProjectionCadence(interval=args.interval, heartbeat=args.heartbeat),
                collaborators=EXECUTION_REGISTRATION_COLLABORATORS,
            ),
            None,
        )
    try:
        sim = build_sim(config, Path(args.sim), speed=parse_sim_speed(args.sim_speed))
    except SimError as error:
        print(f"error: {error}")
        return None
    app = create_app(
        sim.config,
        cadence=ProjectionCadence(interval=args.interval),
        replay=ProjectionReplay(now=sim.clock.now, before_tick=sim.feeder.feed),
        collaborators=EXECUTION_REGISTRATION_COLLABORATORS,
    )
    return _DashboardApp(app, sim)


def _run_daemon_command(
    args: argparse.Namespace, config: serving_daemon.McpRuntimeConfig, port: int
) -> int:
    """Dispatch --status/--stop/--daemon against the recorded daemon state."""
    if args.sim or args.reload:
        print("error: --daemon/--status/--stop are not supported with --sim or --reload")
        return 1
    directory = serving_daemon.daemon_dir(config)
    if args.status:
        state, alive = serving_daemon.probe(directory)
        if alive and state is not None:
            print(
                f"dashboard daemon running: pid {state.pid}, http://{state.host}:{state.port}/ "
                f"(v{state.version}); log: {state.log_path}"
            )
            return 0
        print("dashboard daemon not running")
        return 1
    if args.stop:
        print(f"dashboard daemon: {serving_daemon.stop(directory)}")
        return 0
    result = serving_daemon.ensure(
        config,
        serving_daemon.DaemonEndpoint(host=args.host, port=port),
        cadence=ProjectionCadence(interval=args.interval, heartbeat=args.heartbeat),
    )
    print(f"dashboard daemon {result.action}: {result.detail}")
    return 0 if result.action in ("adopted", "started", "restarted") else 1
