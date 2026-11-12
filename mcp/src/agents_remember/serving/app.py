"""The dashboard FastAPI app: one-shot state, the multiplexed SSE stream, raw events, actions.

Endpoints:

* ``GET  /api/state``           -- the current projection once (curl-friendly, no streaming).
* ``GET  /api/stream``          -- one EventSource: an ``event: snapshot`` with the full
  projection on connect, then per-entity ``state`` deltas (``task`` / ``enclosure`` /
  ``provider`` / ``metrics`` / ``analytics`` and their ``*.removed`` markers) fanned out from
  the shared :class:`Projector`.
* ``GET  /api/events``          -- the raw ``ar-observer-event/v1`` log, tailed with exact
  byte-offset ``Last-Event-ID`` resume. A *separate* stream from ``/api/stream``:
  it resumes by byte offset, the state channel re-snapshots, so mixing them on one stream
  would be incoherent. The cockpit opens both (still well under the ~6 connections/origin cap).
* ``POST /api/actions/{action}``-- enclosure transitions (``integrate`` / ``cleanup``) are
  validated against the reducer's ``ActionAvailability`` and acknowledged without
  mutation; ``dismiss`` records an attention acknowledgement. Routing maps
  :class:`ActionOutcome` onto the response; see ``serving/actions.py``.

Local-first posture: bind ``127.0.0.1`` only (the CLI default) with no auth in v1. This is a
cockpit for the developer's own machine; exposing it (an SSH tunnel, a reverse proxy) hands an
unauthenticated reader the whole projection -- and the POST action surface -- so any multi-user
or remote story is a deliberate later design with its own auth gate.

The ``now`` / ``before_tick`` parameters are the sim seams: live serving leaves them
at their defaults; ``cli.dashboard`` passes a replay clock + fixture feeder under ``--sim``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from agents_remember.providers.metrics import ProviderMetricsStore
from agents_remember.serving._app_common import (
    INFERRED_LIVE_INPUTS,
    LiveProjectionInputs,
    ServingCollaborators,
    _ServingRuntime,
)
from agents_remember.serving._app_lifespan import _serving_lifespan
from agents_remember.serving._app_routes import (
    _register_action_routes,
    _register_projection_routes,
)
from agents_remember.serving.background_accounting import BackgroundLedger
from agents_remember.serving.build_info import process_serving_build
from agents_remember.serving.canvas import canvas_hosts, register_canvas_routes
from agents_remember.serving.change_watcher import ProjectionInputWatcher
from agents_remember.serving.changeset import register_changeset_routes
from agents_remember.serving.explainer import ExplainerWiring, explainer_composition
from agents_remember.serving.explainer_routes import register_explainer_routes
from agents_remember.serving.files import register_files_routes
from agents_remember.serving.knowledge_reader import register_knowledge_reader_route
from agents_remember.serving.notes import register_notes_routes
from agents_remember.serving.projections.landing_state import LandingStateRefresher
from agents_remember.serving.projector import (
    DEFAULT_PROJECTION_CADENCE,
    LIVE_PROJECTION_CLOCK,
    ProjectionCadence,
    ProjectionRefreshers,
    ProjectionReplay,
    Projector,
)
from agents_remember.serving.requirements import register_requirements_routes
from agents_remember.serving.review import register_review_routes
from agents_remember.serving.review_open_observation import install_review_open
from agents_remember.serving.review_summary import register_review_summary_route
from agents_remember.serving.review_trees import (
    register_review_detail_order_route,
    register_review_invariant_changes_route,
    register_review_trees_route,
)
from agents_remember.serving.static import mount_static
from agents_remember.serving.write_guard import RefuseCrossOriginWrites, RefuseForeignHosts

if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import (
        McpRuntimeConfig,
    )

OWNED_SERVING_COLLABORATORS = ServingCollaborators()
"""No injected collaborators: every port's route refuses by name."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _build_serving_runtime(
    config: McpRuntimeConfig,
    cadence: ProjectionCadence,
    replay: ProjectionReplay,
    live_inputs: LiveProjectionInputs,
    collaborators: ServingCollaborators,
) -> tuple[_ServingRuntime, ProviderMetricsStore]:
    """Construct the long-lived collaborators one serving app owns, before any route exists.

    Separated from ``create_app`` because the two halves answer different questions: this is
    what the process will be running, and ``create_app`` is what it will serve. The metrics
    store rides back beside the runtime rather than on it -- the lifespan owns its sampling
    loop, and nothing that handles a request reads it.
    """
    enabled = live_inputs.resolved(replay)
    # The always-on background account (MIK-R81 rule 1): one ledger per app, shared
    # by the projector and every background loop through the runtime below.
    ledger = BackgroundLedger()
    landing = LandingStateRefresher(config, ledger=ledger) if enabled.landing_state else None
    projector = Projector(
        config,
        cadence=cadence,
        replay=replay,
        refreshers=ProjectionRefreshers(
            landing=landing,
            change_watcher=ProjectionInputWatcher(config, ledger=ledger)
            if enabled.change_watch
            else None,
            host_activity_reconcile=collaborators.host_activity_reconcile,
        ),
        ledger=ledger,
    )
    if landing is not None:
        landing.bind_interest(projector.has_subscribers)
    runtime = _ServingRuntime(
        config=config,
        projector=projector,
        clock=replay.now or _utc_now,
        # Resolved ONCE at boot: the stamp that makes a stale serving process visible.
        build=process_serving_build(),
        interval=cadence.interval,
        review_memory_root=collaborators.review_memory_root,
        review_trees_shutdown=collaborators.review_trees_shutdown,
        launcher_catalog_prepare=collaborators.launcher_catalog_prepare,
        background_ledger=ledger,
        usage_read=collaborators.usage_read,
        explainer_application=collaborators.explainer_application,
    )
    # The serving daemon samples labeled provider
    # containers on its own cadence (decoupled from the 1s projection tick) into
    # the central metrics store — the feed for provider_status, the statistics
    # board, and the degradation protocol. Read-only + dockerless-safe.
    return runtime, ProviderMetricsStore(config.coordination_root)


#: The old authoring POSTs, which the explainer replaced. Their reads and retained-revision
#: viewing channels (/api/canvas/sessions...) are not in this list.
REPLACED_AUTHORING_PATHS = (
    "/api/canvas/frames/{repository_id}/{master}/{leaf_id}/{frame_id}/generate",
    "/api/canvas/frames/{repository_id}/{master}/{leaf_id}/{frame_id}/regenerate",
    "/api/canvas/frames/{repository_id}/{master}/{leaf_id}/{frame_id}/publish",
    "/api/canvas/frames/{repository_id}/{master}/{leaf_id}/{frame_id}/cancel",
    "/api/canvas/frames/{repository_id}/{master}/{leaf_id}/{frame_id}/replace",
    "/api/canvas/attempts/{repository_id}/{master}/{leaf_id}/{frame_id}/{attempt_id}/{operation}",
)


def refuse_replaced_canvas_authoring(app: FastAPI) -> None:
    """Refuse the retired authoring POSTs by name, and serve nothing on them.

    MIK-R106 v2 rule 1 splits the page from the old Generate: one page and one conversation per leaf,
    built by the explainer's environment. A retired route that still ran would be a second authoring
    path, so each POST answers a typed refusal and starts no process, opens no session and writes
    nothing. Register this before the canvas routes: the first match is the refusal.
    """

    async def _replaced(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "state": "refused",
                "reason": "canvas-authoring-replaced",
                "detail": (
                    "the review page's page and conversation are built by the leaf's explainer "
                    "(POST /api/explainer/<repository>/<master>/<leaf>/build); this authoring route "
                    "was retired and starts nothing"
                ),
            },
            status_code=409,
        )

    for path in REPLACED_AUTHORING_PATHS:
        app.add_api_route(path, _replaced, methods=["POST"], name=f"replaced:{path}")


def create_app(
    config: McpRuntimeConfig,
    *,
    cadence: ProjectionCadence = DEFAULT_PROJECTION_CADENCE,
    replay: ProjectionReplay = LIVE_PROJECTION_CLOCK,
    live_inputs: LiveProjectionInputs = INFERRED_LIVE_INPUTS,
    collaborators: ServingCollaborators = OWNED_SERVING_COLLABORATORS,
) -> FastAPI:
    """Build the dashboard app bound to one shared projector for ``config``.

    ``replay`` defaults to live behaviour; sim wires a replay clock + feeder. Live serving enables
    the landing-state refresher and the change-driven projection watcher by default; sim disables
    both unless ``live_inputs`` says otherwise (replay must stay time-driven -- the sim feeder only
    writes *inside* a tick, so a change-gated loop would never wake). ``cadence.interval`` is the
    fast-path projection cadence floor; ``cadence.heartbeat`` bounds quiet-world ``/api/state``
    staleness (default ``DEFAULT_HEARTBEAT_SECONDS``). ``collaborators`` are the ports of the
    composition root.
    """
    runtime, metrics_store = _build_serving_runtime(
        config, cadence, replay, live_inputs, collaborators
    )
    app = FastAPI(
        title="Agents Remember dashboard",
    )
    # Gzip the multi-hundred-KB JSON bodies (/api/state ~1.3 MB, the files
    # catalog) for the clients that negotiate it. compresslevel=6: on a ~1.3 MB JSON body it
    # matches level 9's ratio for ~16% less CPU per serve. Starlette's responder excludes
    # text/event-stream by content type, so the SSE channels (/api/stream, /api/events)
    # keep streaming uncompressed and unbuffered (covered by test).
    app.add_middleware(GZipMiddleware, compresslevel=6)
    _register_projection_routes(app, runtime)
    _register_action_routes(app, runtime)
    register_files_routes(app, config)
    changeset_cache = register_changeset_routes(app, config)
    preparation = changeset_cache.master_preparation
    if preparation is not None:
        preparation.bind_ledger(runtime.background_ledger)
        runtime.projector.bind_series_changed(preparation.offer_master)
    runtime = replace(runtime, master_preparation=preparation)
    app.router.lifespan_context = _serving_lifespan(runtime, metrics_store)
    register_notes_routes(app, config)
    register_requirements_routes(app, config)
    register_review_routes(
        app,
        config,
        collaborators.knowledge_review,
        collaborators.knowledge_review_entries,
        collaborators.review_source_content,
    )
    register_review_summary_route(app, collaborators.review_intent_summary, changeset_cache)
    register_review_trees_route(app, collaborators.review_trees)
    register_review_invariant_changes_route(app, collaborators.review_invariant_changes)
    register_review_detail_order_route(app, collaborators.review_detail_order)
    # One observation per open of the review page, shared by that open's reads (D569).
    install_review_open(app)
    register_knowledge_reader_route(app, collaborators.knowledge_reader)
    if collaborators.extra_api_routes is not None:
        collaborators.extra_api_routes(app)
    # The generated page's host (MIK-R104) and the presenter's start/session surface (MIK-R105).
    # The review-trees port is the landed comparison owner, so a presenter start prepares its
    # evidence through it rather than through a second comparison.
    # The old Generate and presenter-session authoring POSTs are retired: the page and its
    # conversation are the explainer's (MIK-R106 v2 rule 1), so those paths refuse by name and start
    # nothing. They are registered before the canvas routes so the refusal is what matches; the
    # retained frame and session *reads* are untouched and keep serving.
    refuse_replaced_canvas_authoring(app)
    register_canvas_routes(app, canvas_hosts(config, review_trees=collaborators.review_trees))
    # The explainer's environment, seat and lifetime (MIK-R186): the page's build, Update, Retire
    # and state. Without a host the routes refuse by name; a build never invents a seat.
    register_explainer_routes(
        app,
        explainer_composition(
            config,
            collaborators.review_trees,
            wiring=(
                None
                if collaborators.explainer_host is None
                or collaborators.explainer_application is None
                or collaborators.explainer_preparation is None
                else ExplainerWiring(
                    host=collaborators.explainer_host,
                    application=collaborators.explainer_application,
                    comparison_prepare=collaborators.explainer_preparation,
                    contract_for=collaborators.explainer_contract_for,
                    stale_answers=collaborators.explainer_stale_answers,
                )
            ),
        ),
    )
    # Every state-changing request from a foreign or opaque origin is refused before any route
    # (MIK-R104 rule 4): local-first is a deployment posture, not a write authorization. The frame
    # navigating itself is an accepted channel (v2, D331), so no navigation guard is installed: a
    # navigated frame keeps its sandbox flags and its writes still fail this guard as null-origin.
    app.add_middleware(RefuseCrossOriginWrites)
    # Rule 15 (D332): every route, reads included, answers only this dashboard's own host and port,
    # so a page reached through a rebinding name never reaches a route.
    app.add_middleware(RefuseForeignHosts)
    mount_static(app)
    return app


__all__ = [
    "INFERRED_LIVE_INPUTS",
    "OWNED_SERVING_COLLABORATORS",
    "LiveProjectionInputs",
    "ServingCollaborators",
    "create_app",
]
