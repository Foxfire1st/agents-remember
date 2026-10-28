"""CLI adapter: run the local mission-control dashboard server.

Mirrors the MCP server's ``--config`` contract (``load_config`` over the same trusted MCP
settings JSON), so the dashboard resolves the identical coordination context. ``--sim``
replays a recorded observer fixture through the byte-identical serving path instead of live
state; the sim's throwaway root is held alive for the server's lifetime.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import threading
import urllib.request
from functools import partial
from pathlib import Path
from typing import Any, NamedTuple

import uvicorn
import uvicorn.config

import agents_remember
from agents_remember.application.review_comparison_preparation import (
    SharedComparisonPreparation,
    live_answer_witness,
)
from agents_remember.application.review_planned_comparison import (
    explainer_application_ports,
)
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.cli.dashboard_review_summary import review_intent_summary_port
from agents_remember.cli.developer_messages import register_developer_chat_routes
from agents_remember.cli.discovery import ConfigDiscoveryError, discover_config
from agents_remember.cli.paseo_bridge import bridge_call
from agents_remember.cli.paseo_catalog import launcher_catalog
from agents_remember.cli.paseo_explainer_host import PaseoExplainerHost
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


def dashboard_log_config() -> dict[str, Any]:
    """uvicorn's own logging, plus the package's INFO lines on the server's stderr (the journal).

    Without it the package has no handler: Python's last resort prints WARNING and above only, so
    every INFO line of the serving process -- the worktree watch's plan, budget and coverage adds
    and drops, the leaf-wide build's builds and cancellations, an open's join wait -- is dropped.
    It is uvicorn that applies this, when it builds its config (in the reload worker as well), so
    a caller that stubs ``uvicorn.run`` configures nothing in its own interpreter.
    """

    config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    config["formatters"]["package"] = {
        "()": "uvicorn.logging.DefaultFormatter",
        "fmt": "%(levelprefix)s %(name)s: %(message)s",
        "use_colors": False,
    }
    config["handlers"]["package"] = {
        "formatter": "package",
        "class": "logging.StreamHandler",
        "stream": "ext://sys.stderr",
    }
    config["loggers"]["agents_remember"] = {
        "handlers": ["package"],
        "level": "INFO",
        "propagate": False,
    }
    return config


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


def _leaf_build(
    config: McpRuntimeConfig,
) -> tuple[Any, Any, ReviewerWorklistProcesses]:
    """The explainer ports and the leaf-wide build off the open that their turn ends feed (D569).

    The build captures a watched leaf as the moved watcher captures it, compares the capture with
    the leaf's latest recorded comparison, and computes in children of lowered priority.
    """
    from agents_remember.application.review_candidate_resolution import (  # noqa: PLC0415
        recorded_leaf_contract,
    )
    from agents_remember.application.review_overview_precompute import (  # noqa: PLC0415 - composition
        BUILD_NICENESS,
        OverviewPrecompute,
    )
    from agents_remember.application.review_tree_comparison import (  # noqa: PLC0415
        live_review_trees,
    )
    from agents_remember.canvas.paths import PathRefused, task_root  # noqa: PLC0415 - composition
    from agents_remember.models.knowledge.review_trees import ReviewTrees  # noqa: PLC0415
    from agents_remember.serving.explainer_watcher import (  # noqa: PLC0415 - composition
        CapturedComparison,
        GitComparisonOwner,
    )

    def capture(selection: Any) -> Any:
        captured = GitComparisonOwner(config, application.watcher).capture(selection)
        return captured.record if isinstance(captured, CapturedComparison) else None

    def mint(selection: Any, draft: Any) -> Any:
        """The captured trees' comparison, recorded as the page's own open records it."""

        contract = recorded_leaf_contract(
            config, selection.repository_id, selection.master, selection.leaf_id
        )
        if contract is None:
            return None
        minted = live_review_trees(config.coordination_root, contract, draft.code_candidate.tree)
        return minted.record if isinstance(minted, ReviewTrees) else None

    def latest(selection: Any) -> Any:
        try:
            return application.watcher.latest_comparison(
                task_root(config, selection.repository_id, selection.master), selection.leaf_id
            )
        except (PathRefused, OSError, ValueError):
            return None

    processes = ReviewerWorklistProcesses(niceness=BUILD_NICENESS)
    build = OverviewPrecompute(config, processes, capture=capture, latest=latest, mint=mint)
    watch = _bound_worktree_watch(config, build)
    application = explainer_application_ports(build.offer, watch.sync)
    return application, build, processes


def _bound_worktree_watch(config: McpRuntimeConfig, build: Any) -> Any:
    """The process's one worktree watch, its leaf roots and its changes bound to the build.

    A leaf's roots are its code and memory worktrees. Every change reaches the build's debounce,
    and an overflowed queue -- events were lost -- recaptures every covered leaf.
    """
    from agents_remember.application.review_candidate_resolution import (  # noqa: PLC0415
        recorded_leaf_contract,
    )
    from agents_remember.serving.explainer_watcher import LeafSelection  # noqa: PLC0415
    from agents_remember.serving.leaf_worktree_watch import (  # noqa: PLC0415 - composition
        process_worktree_watch,
    )

    watch = process_worktree_watch()

    def roots_of(key: tuple[str, str, str]) -> tuple[Path, ...]:
        contract = recorded_leaf_contract(config, *key)
        if contract is None:
            return ()
        return tuple(
            root for root in (contract.code_worktree, contract.memory_worktree) if root is not None
        )

    def overflowed() -> None:
        for key in watch.covered_keys():
            build.changed(LeafSelection(*key))

    watch.bind(roots_of, lambda key: build.changed(LeafSelection(*key)), overflowed)
    return watch


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
    from agents_remember.application.review_answer_preparation import (  # noqa: PLC0415 - composition
        recorded_review_answers,
    )
    from agents_remember.application.review_source_content import (  # noqa: PLC0415 - composition
        read_review_source_content,
    )
    from agents_remember.cli.provider_limit_hold import (  # noqa: PLC0415 - composition
        read_usage_for_agents,
    )

    worklist_processes = ReviewerWorklistProcesses()
    explainer_application, precompute, build_processes = _leaf_build(config)
    recorded_answers = recorded_review_answers(config)
    comparisons = SharedComparisonPreparation(config, recorded_answers)
    recorded_answers.bind_live_witness(partial(live_answer_witness, comparisons))

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

    review_trees_port = _card_carrying_trees(recorded_answers, comparisons, worklist_processes)

    def review_trees_shutdown() -> None:
        """Take no further turn-end capture, then stop the worklist processes it computes in."""

        precompute.shutdown()
        build_processes.shutdown()
        worklist_processes.shutdown()

    summary_port = review_intent_summary_port(config, recorded_answers=recorded_answers)

    def review_invariant_changes_port(repository_id, master, leaf_id, shown, newer):
        """The invariants that differ between two comparisons, from the page's own overviews."""

        from agents_remember.application.review_invariant_changes import (  # noqa: PLC0415
            review_invariant_changes,
        )
        from agents_remember.serving.review_trees import ReviewTreesQuery  # noqa: PLC0415

        opened = {"repository_id": repository_id, "master": master, "leaf_id": leaf_id}
        return review_invariant_changes(
            lambda number: review_trees_port(
                ReviewTreesQuery(**opened, number=number, view="overview")
            ),
            (repository_id, master, leaf_id),
            shown,
            newer,
        )

    # A page open captures its leaf at once and its reads join the build (D570); the stream's
    # opens send no open header and capture nothing.
    from agents_remember.serving.explainer_watcher import LeafSelection  # noqa: PLC0415
    from agents_remember.serving.review_open_observation import (  # noqa: PLC0415 - composition
        OPEN_OBSERVATIONS,
    )

    OPEN_OBSERVATIONS.bind_opened(lambda leaf: precompute.opened(LeafSelection(*leaf)))

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

    collaborators = ServingCollaborators(
        launcher_catalog_prepare=partial(launcher_catalog, config),
        explainer_host=PaseoExplainerHost(config),
        explainer_application=explainer_application,
        explainer_preparation=comparisons.prepare,
        explainer_contract_for=partial(_master_contract, config),
        explainer_stale_answers=recorded_answers.stale,
        host_activity_reconcile=lambda runtime: bridge_call(runtime, "host-activity-snapshot", {}),
        usage_read=read_usage_for_agents,
        review_memory_root=partial(_review_memory_root, config),
        knowledge_review=review_port,
        knowledge_review_entries=review_entries_port,
        review_source_content=review_source_content_port,
        review_intent_summary=summary_port,
        review_invariant_changes=review_invariant_changes_port,
        review_detail_order=lambda repository_id, master, leaf_id, number, open_id, order: (
            precompute.order(LeafSelection(repository_id, master, leaf_id), number, open_id, order)
        ),
        review_trees=review_trees_port,
        review_trees_shutdown=review_trees_shutdown,
        knowledge_reader=knowledge_reader_port,
        extra_api_routes=api_routes,
    )
    ports = opening_ports(config, collaborators)
    precompute.bind_answers(partial(opening_answers, ports))
    precompute.bind_details(partial(detail_answers, ports))
    return collaborators


def _card_carrying_trees(recorded_answers: Any, comparisons: Any, processes: Any) -> Any:
    """The tree view of the same resolution (MIK-R25): four trees, knowledge diff, worklist.

    A recorded comparison's tree view is a function of its record and the objects it names, so it
    is kept and served again while those hold; a live view depends on a working tree and is computed
    on every read. An invariant's cards for a newer comparison are carried from the last ones made
    when every read they were made from holds (D574, D570).
    """

    from agents_remember.application.review_card_carry import CardCarry  # noqa: PLC0415
    from agents_remember.application.review_tree_knowledge import (  # noqa: PLC0415
        carried_cards_view,
    )

    card_carry = CardCarry()

    def carry_cards(query: Any, trees: Any) -> Any:
        leaf = (query.repository_id, query.master, query.leaf_id)
        return card_carry.carry(
            leaf,
            query.invariants,
            trees.record,
            lambda kept: carried_cards_view(query, trees, kept),
        )

    def review_trees_port(query: Any) -> Any:
        cards = bool(query.invariants) and query.entry is None and query.number is not None

        def render() -> Any:
            made = comparisons.render(
                query, processes=processes, carry=carry_cards if cards else None
            )
            if cards:
                leaf = (query.repository_id, query.master, query.leaf_id)
                card_carry.made(leaf, query.invariants, made)
            return made

        return recorded_answers.trees(query, render)

    return review_trees_port


#: The step of the opening order that prepares the leaf-wide view the later answers read.
LEAF_WIDE_VIEW = "leaf-wide view"

#: The answers the review page opens with for one comparison, in the order the background build
#: computes them (D569). Those that need no leaf-wide view come first, so an open arriving while
#: the build runs joins them at once; the summary follows the view, which may store the worklist
#: the summary reads. :func:`opening_answers` asks each exactly as the page's route asks it.
OPENING_ORDER = (
    "live intent",
    "live entries",
    "stale",
    LEAF_WIDE_VIEW,
    "summary",
    "overview",
    "files",
)

#: The page's opening answers, by the names an open's log gives them.
OPENING_ANSWERS = tuple(step for step in OPENING_ORDER if step != LEAF_WIDE_VIEW)


class OpeningPorts(NamedTuple):
    """The ports the page's opening routes call, as the dashboard composed them."""

    review: Any
    entries: Any
    summary: Any
    trees: Any
    stale: Any
    changes: Any = None


def opening_ports(config: McpRuntimeConfig, collaborators: ServingCollaborators) -> OpeningPorts:
    """The opening routes' own ports, and the explainer composition the app builds from them."""

    from agents_remember.serving.explainer import (  # noqa: PLC0415 - composition
        ExplainerWiring,
        explainer_composition,
    )

    host, application = collaborators.explainer_host, collaborators.explainer_application
    prepare = collaborators.explainer_preparation
    wiring = (
        None
        if host is None or application is None or prepare is None
        else ExplainerWiring(
            host=host,
            application=application,
            comparison_prepare=prepare,
            contract_for=collaborators.explainer_contract_for,
            stale_answers=collaborators.explainer_stale_answers,
        )
    )
    return OpeningPorts(
        collaborators.knowledge_review,
        collaborators.knowledge_review_entries,
        collaborators.review_intent_summary,
        collaborators.review_trees,
        explainer_composition(config, collaborators.review_trees, wiring=wiring),
        collaborators.review_invariant_changes,
    )


def opening_answers(ports: OpeningPorts, selection: Any, number: int, leaf_wide: Any) -> None:
    """Every answer the page opens with for comparison ``number``, as its reads ask (D569).

    The same ports and the same questions as the opening's routes, so the page's reads find these
    answers kept, or join the ones still being computed.
    """

    from agents_remember.canvas.explainer_comparison_inputs import LeafScope  # noqa: PLC0415
    from agents_remember.serving.explainer import stale_view  # noqa: PLC0415 - composition
    from agents_remember.serving.review import (  # noqa: PLC0415 - composition
        NO_PAGING,
        NO_SELECTOR,
        UnadmittedReviewQuery,
        paged_review_request,
    )
    from agents_remember.serving.review_trees import ReviewTreesQuery  # noqa: PLC0415

    repository_id, master, leaf_id = selection.as_key()
    opened = {"repository_id": repository_id, "master": master, "leaf_id": leaf_id}

    def intent() -> None:
        request = paged_review_request(repository_id, master, leaf_id, NO_SELECTOR, NO_PAGING)
        if not isinstance(request, UnadmittedReviewQuery):
            ports.review(request)

    asks = {
        "live intent": intent,
        "live entries": lambda: ports.entries(repository_id, master, leaf_id, None),
        "stale": lambda: stale_view(
            ports.stale, LeafScope(repository_id, master, leaf_id), selector=(number, "recorded")
        ),
        LEAF_WIDE_VIEW: leaf_wide,
        "summary": lambda: ports.summary.read(
            repository_id, master, leaf_id, number, lambda _sources: None
        ),
        "overview": lambda: ports.trees(ReviewTreesQuery(**opened, number=number, view="overview")),
        "files": lambda: ports.trees(ReviewTreesQuery(**opened, number=number, lane=True)),
    }
    for step in OPENING_ORDER:
        asks[step]()


def detail_answers(
    ports: OpeningPorts, selection: Any, number: int, previous: int | None = None
) -> tuple[tuple[str, Any, bool], ...]:
    """Each changed invariant of comparison ``number`` and the two reads a click on it makes (D574).

    The invariants with changed locations in the comparison's overview -- the open's kept answer --
    in the tree's Priority order as far as the overview carries it: changed locations, then their
    summed spans, both descending, then the key. Those the move from ``previous`` changed (D576's
    set, what the page marks) come first, each flagged; every other one follows. A click reads the
    invariant's cards for the comparison and its live subject review, as these do.
    """

    from agents_remember.serving.review import (  # noqa: PLC0415 - composition
        NO_PAGING,
        ReviewQuestionRef,
        UnadmittedReviewQuery,
        paged_review_request,
    )
    from agents_remember.serving.review_trees import ReviewTreesQuery  # noqa: PLC0415

    repository_id, master, leaf_id = selection.as_key()
    opened = {"repository_id": repository_id, "master": master, "leaf_id": leaf_id}
    answer = ports.trees(ReviewTreesQuery(**opened, number=number, view="overview"))
    overview = getattr(answer, "overview", None)
    if overview is None or overview.state != "ready":
        return ()
    moved = _moved_invariants(ports, (repository_id, master, leaf_id), previous, number)
    changed = sorted(
        (row for row in overview.subjects if row.changed),
        key=lambda row: (
            row.invariant_key not in moved,
            -row.changed,
            -row.changed_span,
            row.invariant_key,
        ),
    )

    def detail(key: str) -> Any:
        def ask() -> None:
            ports.trees(ReviewTreesQuery(**opened, number=number, invariants=(key,)))
            question = ReviewQuestionRef(selector_kind="invariant", selector_id=key)
            request = paged_review_request(repository_id, master, leaf_id, question, NO_PAGING)
            if not isinstance(request, UnadmittedReviewQuery):
                ports.review(request)

        return ask

    return tuple(
        (row.invariant_key, detail(row.invariant_key), row.invariant_key in moved)
        for row in changed
    )


def _moved_invariants(
    ports: OpeningPorts, leaf: tuple[str, str, str], previous: int | None, number: int
) -> frozenset[str]:
    """The invariants D576 names for the move from ``previous`` to ``number``; none when unknown."""

    if ports.changes is None or previous is None or previous == number:
        return frozenset()
    changes = ports.changes(*leaf, previous, number)
    if getattr(changes, "state", None) != "named":
        return frozenset()
    return frozenset(changes.invariants)


def _master_contract(config: McpRuntimeConfig, scope: Any) -> Path | None:
    """The master line's series contract: the tips a `planning` leaf's page is bound to.

    A planned comparison sits at the master line's two tip commits (MIK-R189 rule 4), and the master
    document names the contract that states those branches (`seriesContractPath`); a master that
    predates that field answers `<master folder>/series-contract.md` when the file is there. A leaf
    with no such contract refuses the planned read by name rather than borrowing another line.
    """

    folder = (
        config.coordination_root
        / "tasks"
        / str(getattr(scope, "repository_id", ""))
        / str(getattr(scope, "task", ""))
    )
    named = folder / "task.json"
    if named.is_file():
        try:
            document = json.loads(named.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            document = {}
        declared = document.get("seriesContractPath") if isinstance(document, dict) else None
        if isinstance(declared, str) and declared:
            candidate = Path(declared)
            if candidate.is_file():
                return candidate
    fallback = folder / "series-contract.md"
    return fallback if fallback.is_file() else None


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
            log_config=dashboard_log_config(),
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
        log_config=dashboard_log_config(),
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
