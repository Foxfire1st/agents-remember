"""CLI adapter: run the local mission-control dashboard server.

Mirrors the MCP server's ``--config`` contract (``load_config`` over the same trusted MCP
settings JSON), so the dashboard resolves the identical coordination context. ``--sim``
replays a recorded observer fixture through the byte-identical serving path instead of live
state; the sim's throwaway root is held alive for the server's lifetime.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import urllib.request
from functools import partial
from pathlib import Path
from typing import Any, NamedTuple

import uvicorn

import agents_remember
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.cli.dashboard_review_summary import review_intent_summary_port
from agents_remember.cli.developer_messages import register_developer_chat_routes
from agents_remember.cli.discovery import ConfigDiscoveryError, discover_config
from agents_remember.cli.paseo_bridge import bridge_call
from agents_remember.cli.role_launch_archive import LeafAgentArchive
from agents_remember.cli.role_launch_routes import register_role_launch_routes
from agents_remember.controlplane.durable_store import declare_process_role
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    McpRuntimeConfig,
    load_config,
)
from agents_remember.kernel.reviewer_worklist_process import ReviewerWorklistProcesses
from agents_remember.serving import daemon as serving_daemon
from agents_remember.serving._app_common import ServingCollaborators
from agents_remember.serving.app import create_app
from agents_remember.serving.change_watcher import DEFAULT_HEARTBEAT_SECONDS
from agents_remember.serving.paseo.paseo_start import (
    STATUS_TIMEOUT_SECONDS,
    ensure_host,
    observe_host,
)
from agents_remember.serving.projector import ProjectionCadence, ProjectionReplay
from agents_remember.serving.sim import SimError, SimSetup, build_sim, parse_sim_speed
from agents_remember.worktrees.services import bind_worktree_services

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
# the lifespan shutdown (which cancels the projector/landing tasks) within a few seconds.
# Fast API/JSON requests finish well inside this window; only the intentionally-endless SSE streams
# are force-closed, which is exactly the desired behaviour on shutdown. uvicorn types this as whole
# seconds (``int | None``), so keep it an int.
DASHBOARD_GRACEFUL_SHUTDOWN_SECONDS = 3

# A review card read names up to 500 invariant identities in its query (MIK-R89 v2 rule 2, read 5):
# 500 URL-encoded UUIDs are ~19.5 KB of request line. uvicorn serves HTTP/1.1 through h11 here
# (httptools is not installed), and h11 refuses a request head that is still incomplete beyond its
# default 16 KiB (``h11._connection.DEFAULT_MAX_INCOMPLETE_EVENT_SIZE``) -- uvicorn answers that
# "Invalid HTTP request received." with a 400 -- whenever the head arrives over more than one read.
# 64 KiB holds the largest card read with room, and still bounds an unterminated head.
DASHBOARD_H11_MAX_INCOMPLETE_EVENT_SIZE = 64 * 1024


def _review_memory_root(
    config: McpRuntimeConfig, repository_id: str, master: str, leaf_id: str
) -> Path | None:
    """Admit only this authority's exact live leaf root; never capture Git or open an index."""
    from agents_remember.application.review_candidate_resolution import (  # noqa: PLC0415
        recorded_leaf_contract,
    )

    for value in (repository_id, master, leaf_id):
        if not value or "/" in value or "\\" in value or value.startswith("."):
            return None
    contract = recorded_leaf_contract(config, repository_id, master, leaf_id)
    if contract is None or contract.kind != "leaf" or contract.memory_mode != "external":
        return None
    task_root = config.coordination_root / "tasks" / repository_id / master
    if (
        contract.coordination_root.resolve() != config.coordination_root.resolve()
        or contract.task_root.resolve() != task_root.resolve()
        or not contract.contract_path.resolve().is_relative_to(task_root.resolve() / "enclosures")
    ):
        return None
    root = contract.memory_worktree
    if (
        root is None
        or root.is_symlink()
        or root != root.resolve()
        or not root.is_dir()
        or not contract.code_worktree.is_dir()
        or root == contract.code_worktree.resolve()
        or not root.is_relative_to(contract.worktree_group.resolve())
        or root == contract.worktree_group.resolve()
        or "provider-runtime" in root.parts
    ):
        return None
    return root


def serving_collaborators(config: McpRuntimeConfig) -> ServingCollaborators:
    """The collaborators one dashboard app is composed with, bound to this configuration.

    The review and knowledge-reader adapters are ``application``-rank callables and ``serving``
    may not import them, so this composition root — the one place that already knows which
    configuration the app serves — binds them here.
    """

    from agents_remember.application.knowledge_reader import (  # noqa: PLC0415 - composition
        read_knowledge_reader,
    )
    from agents_remember.application.knowledge_review import (  # noqa: PLC0415 - composition
        list_comparison_review_entries,
        read_complete_knowledge_review,
    )
    from agents_remember.application.review_source_content import (  # noqa: PLC0415 - composition
        read_review_source_content,
    )
    from agents_remember.application.review_tree_knowledge import (  # noqa: PLC0415 - composition
        read_review_trees,
    )
    from agents_remember.cli.dashboard_review_reads import (  # noqa: PLC0415 - composition
        RecordedReviewAnswers,
    )
    from agents_remember.serving.review_answer_reuse import (  # noqa: PLC0415 - composition
        RecordedAnswerReuse,
    )

    worklist_processes = ReviewerWorklistProcesses()
    # A recorded comparison is fixed; its four answers are kept by the exact inputs they are a
    # function of and served unchanged while those hold (MIK-R179 rule 3).
    recorded_answers = RecordedReviewAnswers(config, RecordedAnswerReuse())

    def review_port(request):
        """Render one review through the application adapter, with its complete record collection.

        Every collection the candidate's owners can answer for is read from the owner's own operation
        for the candidate this request resolves to -- the detection runs and their signals, the
        verification observations, the review matrix's authored effects and evidence claims, and the
        curator authority's published assessments -- and each arrives with the availability fact its
        owner's answer earned. A collection an owner answers for and holds none is a measured zero; a
        collection whose expected content could not be read is ``unavailable`` with that owner's own
        provenance; and a dependency-currentness measurement is reported ``not_measured`` rather than
        omitted. The bundle therefore never presents an empty tuple where three different facts are
        possible, and one unreadable authority does not withdraw the collections that were readable.

        The candidate is resolved once for both halves (MIK-R40 rule 2): the records and the
        composition read the same resolution, so one request captures each worktree once and
        rechecks it once before the answer is published.

        A recorded read is a function of the record and its trees, and of nothing this process's
        working views do; its answer is kept and served again unchanged while those inputs hold.
        """

        return recorded_answers.intent(
            request, lambda: read_complete_knowledge_review(config, request)
        )

    def review_entries_port(repository_id, master, leaf_id, number=None):
        """List the subjects the same resolution can be reviewed on, through the same adapter.

        The task view calls this before any subject exists, so it takes the task context alone. It
        resolves through the identical operation the review route uses, which is what keeps the
        entry a caller is offered and the review it then opens on one candidate. ``number`` names
        one recorded comparison of the leaf instead (MIK-R89 v2 rule 10).
        """

        return recorded_answers.entries(
            repository_id,
            master,
            leaf_id,
            number,
            lambda: list_comparison_review_entries(config, repository_id, master, leaf_id, number),
        )

    def review_source_content_port(request):
        """Open one listed entry's content at the two code trees the listing published.

        The request carries the generation the browser is looking at rather than the server choosing
        one, and the application owner re-resolves the leaf to *measure* whether that generation is
        still the candidate's. It then reads those two objects and nothing else -- no working tree,
        no ``HEAD`` -- so an entry opened after the branch moved still shows the listed generation's
        bytes and says that the leaf has moved past them.
        """

        return read_review_source_content(config, request)

    def review_trees_port(query):
        """The tree view of the same resolution (MIK-R25): four trees, knowledge diff, worklist.

        A recorded comparison's tree view is a function of its record and the objects it names, so
        it is kept and served again while those hold; a live view depends on a working tree and is
        computed on every read.
        """

        return recorded_answers.trees(
            query, lambda: read_review_trees(config, query, processes=worklist_processes)
        )

    def knowledge_reader_port(query):
        """The path-based knowledge reader (MIK-R29): one read-only view of one memory tree."""

        return read_knowledge_reader(config, query)

    def api_routes(app: Any) -> None:
        """The launcher's routes, then the tray's: seat list, developer send, sent picks."""

        register_role_launch_routes(app, config)
        # A reference picked on a review comparison is read from the leaf's memory, resolved as
        # the review reads resolve it (MIK-R188 rule 2; L89 P-T2).
        register_developer_chat_routes(
            app, config, leaf_memory_root=partial(_review_memory_root, config)
        )

    return ServingCollaborators(
        host_activity_reconcile=lambda runtime: bridge_call(runtime, "host-activity-snapshot", {}),
        review_memory_root=partial(_review_memory_root, config),
        knowledge_review=review_port,
        knowledge_review_entries=review_entries_port,
        review_source_content=review_source_content_port,
        review_intent_summary=review_intent_summary_port(config),
        review_trees=review_trees_port,
        review_trees_shutdown=worklist_processes.shutdown,
        knowledge_reader=knowledge_reader_port,
        extra_api_routes=api_routes,
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
    bind_worktree_services(
        build_default_worktree_services(leaf_agent_archive=LeafAgentArchive(config))
    )
    heartbeat_env = os.environ.get(_DEV_HEARTBEAT_ENV)
    app = create_app(
        config,
        cadence=ProjectionCadence(
            interval=float(os.environ.get(_DEV_INTERVAL_ENV, "1.0")),
            heartbeat=float(heartbeat_env) if heartbeat_env else None,
        ),
        collaborators=serving_collaborators(config),
    )
    return app


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
        "restarts one whose version, host, or port differs. Also starts the installed host; never installs or upgrades it. Exit 0 dashboard and host up/unconfigured, 1 dashboard down, 3 configured host down. Dashboard stop/restart leaves host sessions running.",
    )
    control.add_argument(
        "--status",
        action="store_true",
        help=f"Report dashboard and host state; host queries have a {STATUS_TIMEOUT_SECONDS:g}s limit. Exit 0 both up (or host unconfigured), 1 dashboard down, 3 configured host down.",
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
    parser.add_argument("--host-start-owner", type=int, default=None, help=argparse.SUPPRESS)


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
    bind_worktree_services(
        build_default_worktree_services(leaf_agent_archive=LeafAgentArchive(config))
    )
    port = args.port if args.port is not None else config.dashboard.port
    if args.daemon or args.status or args.stop:
        return _run_daemon_command(args, config, port)
    if args.reload:
        if not args.sim and args.host_start_owner is None:
            _start_host_background(config)
        return _run_reload_server(args, config_path, port)
    # ``built`` keeps the sim (and its temp coordination root) referenced until this call
    # returns, i.e. for the whole server lifetime, so the sim root is not reclaimed early.
    built = _build_app(args, config)
    if built is None:
        return 1
    if not args.sim and args.host_start_owner is None:
        _start_host_background(config)
    try:
        uvicorn.run(
            built.app,
            host=args.host,
            port=port,
            access_log=not args.no_access_log,
            timeout_graceful_shutdown=DASHBOARD_GRACEFUL_SHUTDOWN_SECONDS,
            h11_max_incomplete_event_size=DASHBOARD_H11_MAX_INCOMPLETE_EVENT_SIZE,
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
        h11_max_incomplete_event_size=DASHBOARD_H11_MAX_INCOMPLETE_EVENT_SIZE,
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
        app = create_app(
            config,
            cadence=ProjectionCadence(interval=args.interval, heartbeat=args.heartbeat),
            collaborators=serving_collaborators(config),
        )
        return _DashboardApp(app, None)
    try:
        sim = build_sim(config, Path(args.sim), speed=parse_sim_speed(args.sim_speed))
    except SimError as error:
        print(f"error: {error}")
        return None
    app = create_app(
        sim.config,
        cadence=ProjectionCadence(interval=args.interval),
        replay=ProjectionReplay(now=sim.clock.now, before_tick=sim.feeder.feed),
        collaborators=serving_collaborators(config),
    )
    return _DashboardApp(app, sim)


def _print_background_status(host: str, port: int) -> None:
    """Print the dashboard's own background-work totals (MIK-R81 rule 1).

    Best-effort: the diagnostics route is observability, so its failure prints
    one unavailable line and never changes the status exit code.
    """
    from agents_remember.serving.background_accounting import (  # noqa: PLC0415
        describe_snapshot,
    )

    url = f"http://{host}:{port}/api/diagnostics/background"
    try:
        with urllib.request.urlopen(url, timeout=STATUS_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
        print(describe_snapshot(payload))
    except Exception as error:
        print(f"background work: unavailable ({error})")


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
            _print_background_status(state.host, state.port)
        else:
            print("dashboard daemon not running")
        host = observe_host(config)
        print(host.line)
        return 1 if not alive else (0 if host.ready else 3)
    if args.stop:
        print(f"dashboard daemon: {serving_daemon.stop(directory)}")
        return 0
    result = serving_daemon.ensure(
        config,
        serving_daemon.DaemonEndpoint(host=args.host, port=port),
        cadence=ProjectionCadence(interval=args.interval, heartbeat=args.heartbeat),
    )
    print(f"dashboard daemon {result.action}: {result.detail}")
    host = ensure_host(config)
    print(host.line)
    if result.action == "adopted":
        serving_daemon.record_host_outcome(config, host.line)
    if result.action not in ("adopted", "started", "restarted"):
        return 1
    return 0 if host.ready else 3


def _start_host_background(config: McpRuntimeConfig) -> None:
    """Serving starts immediately; host readiness has its own bounded worker."""

    def start():
        host = ensure_host(config)
        print(host.line, flush=True)
        state = serving_daemon.read_state(serving_daemon.daemon_dir(config))
        if state is None or state.pid != os.getpid():
            serving_daemon.record_host_outcome(config, host.line)

    threading.Thread(target=start, name="ar-host-start", daemon=True).start()
