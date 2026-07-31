"""The dashboard FastAPI app: one-shot state, the multiplexed SSE stream, raw events, actions.

Endpoints:

* ``GET  /api/state``           -- the current projection once (curl-friendly, no streaming).
* ``GET  /api/stream``          -- one EventSource: an ``event: snapshot`` with the full
  projection on connect, then per-entity ``state`` deltas (``lifecycle`` / ``enclosure`` /
  ``provider`` / ``metrics`` / ``analytics`` and their ``*.removed`` markers) fanned out from
  the shared :class:`Projector`.
* ``GET  /api/events``          -- the raw ``ar-observer-event/v1`` log, tailed with exact
  byte-offset ``Last-Event-ID`` resume. A *separate* stream from ``/api/stream``:
  it resumes by byte offset, the state channel re-snapshots, so mixing them on one stream
  would be incoherent. The cockpit opens both (still well under the ~6 connections/origin cap).
* ``POST /api/actions/{action}``-- the gate return-channel. Lifecycle transitions
  (``resume`` / ``integrate`` / ``cleanup``) are validated against the reducer's
  ``ActionAvailability`` and acknowledged without mutation; gate-decision verbs
  (``approve`` / ``reject`` / ``request-revision`` / ``cancel``) are recorded as
  developer-attributed gate decisions, which server-side closeout enforcement
  makes binding. Routing maps :class:`ActionOutcome` onto the response; see ``serving/actions.py``.
* ``POST /api/operator-inbox``  -- the external-chat gate response return channel. The
  dashboard writes a developer response to the append-only operator inbox when no hosted chat
  session can be injected into; external agents poll/consume through the MCP ``operator_inbox_*``
  tools.

Local-first posture: bind ``127.0.0.1`` only (the CLI default) with no auth in v1. This is a
cockpit for the developer's own machine; exposing it (an SSH tunnel, a reverse proxy) hands an
unauthenticated reader the whole projection -- and the POST action surface -- so any multi-user
or remote story is a deliberate later design with its own auth gate.

The ``now`` / ``before_tick`` parameters are the sim seams: live serving leaves them
at their defaults; ``cli.dashboard`` passes a replay clock + fixture feeder under ``--sim``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any
from uuid import uuid4

from fastapi import (
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field
from starlette.middleware.gzip import GZipMiddleware

from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import (
    AgentRole,
    InboxAddress,
    InboxMessage,
    InboxMessageKind,
    InboxPoster,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.orchestration_nudges import OrchestrationNudgeStore
from agents_remember.controlplane.records import GateVerdict
from agents_remember.controlplane.supervisor_signals import SupervisorSignalCooldownStore
from agents_remember.errors import HarnessControlError
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.mcp.tools.dispatch_brief import HostedDelivery
from agents_remember.mcp.tools.gates import gate_decide_for_lifecycle, gate_decide_payload
from agents_remember.mcp.tools.operator_inbox import operator_inbox_post_payload
from agents_remember.observer import observer_root
from agents_remember.observer.event_retention import (
    WORKSPACE_EVENT_COMPACT_INTERVAL_SECONDS,
    compact_workspace_river,
)
from agents_remember.observer.events import now_iso
from agents_remember.observer.landing_state import LandingStateRefresher
from agents_remember.observer.projection_store import ProviderStateRefresher
from agents_remember.observer.snapshots import read_task_document_body
from agents_remember.observer.store import EventStore
from agents_remember.providers.degradation import evaluate_provider_degradation
from agents_remember.providers.metrics import (
    DEFAULT_SAMPLE_INTERVAL_SECONDS,
    ProviderMetricsStore,
    sample_provider_containers,
)
from agents_remember.serving.actions import (
    ActionEvaluationContext,
    ActionOutcome,
    ActionRequest,
    DismissalIntent,
    GateDecisionIntent,
    evaluate_action,
)
from agents_remember.serving.build_info import ServingBuild, resolve_serving_build
from agents_remember.serving.change_watcher import ProjectionInputWatcher
from agents_remember.serving.changeset import register_changeset_routes
from agents_remember.serving.conversation.authorization import (
    LocalOperatorAuthorizationResolver,
)
from agents_remember.serving.conversation.runtime import (
    ConversationRuntime,
    ConversationScope,
)
from agents_remember.serving.events import stream_raw_events
from agents_remember.serving.files import register_files_routes
from agents_remember.serving.harness_capability_catalog import HarnessCapabilityCatalog
from agents_remember.serving.harness_control_api import (
    register_harness_control_routes,
    resolve_terminal_open_selection,
)
from agents_remember.serving.harness_control_client import (
    ControlSubmission,
    stop_control_session,
    submit_control_prompt,
)
from agents_remember.serving.harnesses import detect_harnesses
from agents_remember.serving.heap_diag import (
    heap_diag_loop,
    malloc_trim_enabled,
    malloc_trim_interval_seconds,
    start_heap_tracing,
    trim_malloc,
)
from agents_remember.serving.hosted_interactions import HostedInteractionSynchronizer
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.leaf_ref_validation import resolve_catalog_leaf_key
from agents_remember.serving.notes import register_notes_routes
from agents_remember.serving.projector import (
    DEFAULT_PROJECTION_CADENCE,
    LIVE_PROJECTION_CLOCK,
    ProjectionCadence,
    ProjectionRefreshers,
    ProjectionReplay,
    Projector,
)
from agents_remember.serving.retire import SeatClosure, retire_entry
from agents_remember.serving.retire_policy import (
    RetirePolicyError,
    SeatRef,
    check_retire_authority,
)
from agents_remember.serving.seat_events import (
    log_rename_event,
    log_retire_event,
    log_turn_state_change_event,
)
from agents_remember.serving.static import mount_static
from agents_remember.serving.supervisor import SupervisorContext, run_supervisor_sweep
from agents_remember.serving.supervisor_heartbeat import (
    SupervisorHeartbeatStore,
    heartbeat_age_seconds,
)
from agents_remember.serving.terminal import (
    TerminalHost,
    TerminalSession,
    TerminalSessionSpec,
)
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    terminal_catalog_path,
)
from agents_remember.serving.terminal_leaf_assignment import assign_terminal_session_to_leaf
from agents_remember.serving.terminal_liveness import (
    LivenessProbe,
    TerminalCatalogLivenessConfig,
    TerminalCatalogLivenessSweeper,
    observe_terminal_liveness,
    utc_now,
)
from agents_remember.serving.terminal_opener import (
    ControlRunnerRequest,
    SpawnProvenance,
    TerminalLaunchRequest,
    open_terminal_session,
)
from agents_remember.serving.terminal_paste import TerminalPaster
from agents_remember.worktrees.leaf_refs import LeafRefResolutionError

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig

logger = logging.getLogger(__name__)


def _encode(data: BaseModel | dict[str, Any]) -> Any:
    """JSON-ready payload: projection nodes dumped by alias (camelCase), markers as-is."""
    if isinstance(data, BaseModel):
        return data.model_dump(by_alias=True, exclude_none=True)
    return data


class _ProjectionBodyCache:
    """One-entry memo of the dumped projection body for the published projection instance.

    ``/api/state`` and the SSE boot snapshot serve the SAME ~1.3 MB
    projection dump, previously re-walked by ``model_dump`` per request/subscriber (measured
    13.7-16.5 ms per ``/api/state`` call). The projector swaps in a NEW projection instance
    every tick (``Projector._published``), so keying the memo by instance identity refreshes
    it every tick: serve freshness is exactly the former per-request dump semantics (ages as
    of the last tick), while every request/subscriber landing within one tick shares a single
    dump. Keying by the coarser ETag revision instead would freeze the volatile age fields
    for ETag-less full GETs and SSE reconnects until the next content change. The volatile
    tail is NOT cached: ``servingBuild``/``supervisorHeartbeat`` are injected per serve, so
    callers must shallow-copy the returned dict and never mutate the memo itself. Holding the
    instance in the entry makes the identity compare ABA-safe, and tuple assignment is
    GIL-atomic, so no lock (the ``/api/state`` threadpool and the event loop can race
    harmlessly: a concurrent miss just recomputes the same content).
    """

    def __init__(self) -> None:
        self._entry: tuple[BaseModel, dict[str, Any]] | None = None

    def body(self, projection: BaseModel) -> dict[str, Any]:
        """``projection``'s wire dump (cached per instance; read-only for callers)."""
        entry = self._entry
        if entry is None or entry[0] is not projection:
            entry = (projection, projection.model_dump(by_alias=True, exclude_none=True))
            self._entry = entry
        return entry[1]


_projection_body_cache = _ProjectionBodyCache()


def _if_none_match_matches(header: str | None, revision: str) -> bool:
    """RFC 7232 weak comparison of an ``If-None-Match`` header against our revision.

    The revision is the projector's opaque content fingerprint (boot nonce + content
    sequence); the served ETag is its weak form ``W/"<revision>"``. Weak comparison strips
    ``W/`` prefixes, so any listed entity-tag whose opaque value equals the revision
    matches; ``*`` matches any current representation.
    """
    if header is None:
        return False
    for candidate in header.split(","):
        tag = candidate.strip()
        if tag == "*":
            return True
        if tag.startswith(("W/", "w/")):
            tag = tag[2:]
        if tag.strip('"') == revision:
            return True
    return False


async def stream_events(
    projector: Projector,
    *,
    build: ServingBuild | None = None,
    supervisor_heartbeat: dict[str, Any] | None = None,
) -> AsyncGenerator[ServerSentEvent]:
    """The SSE event sequence for one atomic projector subscription.

    Module-level (not a route closure) so it is unit-testable without an HTTP client.
    ``build`` (the boot-time serving stamp) rides the snapshot as
    ``servingBuild`` so the cockpit can render which process/commit is answering.
    ``supervisor_heartbeat`` rides as ``supervisorHeartbeat`` -- the tick age
    at connect time, so a stale supervisor is visible in the dashboard header at a glance.
    """
    async with contextlib.aclosing(projector.subscribe()) as subscription:
        async for seq, delta in subscription:
            if delta.event == "snapshot" and isinstance(delta.data, BaseModel):
                # The boot snapshot is the same projection dump /api/state serves -- reuse the
                # per-instance memo (copy before the volatile injections below) instead of
                # re-dumping ~1.3 MB per subscriber.
                payload = dict(_projection_body_cache.body(delta.data))
            else:
                payload = _encode(delta.data)
            if delta.event == "snapshot":
                if build is not None:
                    payload["servingBuild"] = build.payload()
                if supervisor_heartbeat is not None:
                    payload["supervisorHeartbeat"] = supervisor_heartbeat
            yield ServerSentEvent(data=payload, event=delta.event, id=str(seq), retry=2000)


_TERMINAL_EXIT_FRAME = json.dumps({"type": "exit"})
"""The text frame sent to the browser once the PTY child exits (then the socket closes)."""

_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp"})
"""Image extensions Claude Code's vision accepts. BMP/TIFF/SVG are rejected -- BMP is the
documented WSL clipboard paste-failure root, and the others are not vision formats."""

_MAX_IMAGE_BYTES = 5 * 1024 * 1024
"""Per-image upload cap. The Claude API allows ~10 MB but the claude-code CLI has historically enforced a
5 MB local cap, so clamp to the smaller bound; an oversized image is rejected, never silently truncated.
(The handler caps the bytes it buffers; a full pre-parse bound on the multipart spool would need ASGI
body-limit middleware -- tracked as a follow-up, acceptable on the single-user localhost cockpit.)"""


def _looks_like_image(body: bytes, ext: str) -> bool:
    """Cheap magic-byte sniff so a non-image saved with an image extension is rejected (defence in depth
    on top of the extension check). Conservative -- only the well-known signatures, mismatch -> reject."""
    if ext == "png":
        return body.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in {"jpg", "jpeg"}:
        return body.startswith(b"\xff\xd8\xff")
    if ext == "gif":
        return body.startswith((b"GIF87a", b"GIF89a"))
    if ext == "webp":
        return len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP"
    return False


def _apply_terminal_input(host: TerminalHost, session: str, text: str) -> None:
    """Apply one client text frame to the session: a ``stdin`` write or a ``resize``.

    Malformed frames and unknown types are ignored; the fixed-argv host accepts
    only these two control shapes, never an arbitrary command, so the wire carries no
    spawn surface.
    """
    try:
        message = json.loads(text)
    except (TypeError, ValueError):
        return
    if not isinstance(message, dict):
        return
    if message.get("type") == "stdin":
        data = message.get("data")
        if isinstance(data, str):
            with contextlib.suppress(KeyError, OSError):
                host.write(session, data.encode())
    elif message.get("type") == "resize":
        cols, rows = message.get("cols"), message.get("rows")
        if isinstance(cols, int) and isinstance(rows, int):
            with contextlib.suppress(KeyError, OSError):
                host.resize(session, cols=cols, rows=rows)


def _apply_terminal_session_input(host: TerminalHost, session: TerminalSession, text: str) -> None:
    """Apply one client text frame to a concrete PTY client."""
    try:
        message = json.loads(text)
    except (TypeError, ValueError):
        return
    if not isinstance(message, dict):
        return
    if message.get("type") == "stdin":
        data = message.get("data")
        if isinstance(data, str):
            with contextlib.suppress(OSError):
                host.write_session(session, data.encode())
    elif message.get("type") == "resize":
        cols, rows = message.get("cols"), message.get("rows")
        if isinstance(cols, int) and isinstance(rows, int):
            with contextlib.suppress(OSError):
                host.resize_session(session, cols=cols, rows=rows)


async def _terminal_to_socket(websocket: WebSocket, outbound: asyncio.Queue[bytes | None]) -> None:
    """Forward queued PTY output frames to the browser until the EOF sentinel (``None``)."""
    while True:
        chunk = await outbound.get()
        if chunk is None:
            break
        await websocket.send_bytes(chunk)
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await websocket.send_text(_TERMINAL_EXIT_FRAME)


async def _socket_to_terminal(
    websocket: WebSocket, host: TerminalHost, session: TerminalSession
) -> None:
    """Forward browser frames (``stdin`` / ``resize``) to the PTY until the socket closes."""
    with contextlib.suppress(WebSocketDisconnect):
        async for text in websocket.iter_text():
            _apply_terminal_session_input(host, session, text)


async def _bridge_terminal(
    websocket: WebSocket, host: TerminalHost, session: TerminalSession
) -> None:
    """Pump PTY <-> WebSocket until the child exits or the client disconnects.

    The PTY master fd is watched via ``loop.add_reader`` (no polling); readable output is
    queued and sent as **binary** frames (raw VT bytes for xterm.js), and an EOF sentinel
    closes the outbound pump. Client disconnect ends the inbound pump; either ending cancels
    the other. The session is left open -- tmux keeps it alive for a reconnect (persistence).
    """
    loop = asyncio.get_running_loop()
    outbound: asyncio.Queue[bytes | None] = asyncio.Queue()
    fd = session.master_fd
    reader_active = True

    def _remove_reader() -> None:
        nonlocal reader_active
        if not reader_active:
            return
        reader_active = False
        with contextlib.suppress(RuntimeError):
            loop.remove_reader(fd)

    def _on_readable() -> None:
        data = host.read_session(session)
        if data:
            outbound.put_nowait(data)
        elif not session.is_alive:
            _remove_reader()
            outbound.put_nowait(None)

    loop.add_reader(fd, _on_readable)
    out_task = asyncio.create_task(_terminal_to_socket(websocket, outbound))
    in_task = asyncio.create_task(_socket_to_terminal(websocket, host, session))
    try:
        await asyncio.wait({out_task, in_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        _remove_reader()
        out_task.cancel()
        in_task.cancel()
        await asyncio.gather(out_task, in_task, return_exceptions=True)


DEFAULT_SHELL = "/bin/bash"
"""Fallback when the dashboard process has no ``$SHELL`` (the generic-terminal command)."""


class TerminalOpenRequest(BaseModel):
    """Body of ``POST /api/terminal/{session}``: which kind of session to spawn.

    ``kind="terminal"`` spawns a shell; ``kind="harness"`` spawns the supported TUI
    harness named by ``harness`` (its id, e.g. ``"claude"``). The server resolves the
    command from the kind/harness id -- only ids are on the wire, never an argv -- so there is no
    command-injection surface.
    """

    kind: str = "terminal"
    harness: str | None = None
    model: str | None = None
    effort: str | None = None
    label: str | None = None
    lifecycle_id: str | None = Field(default=None, alias="lifecycleId")
    # The durable leaf-identity key the chat claims at open (qualified leaf id ``repo/master/leaf-id``).
    # Opaque to the backend; persisted on the catalog entry, uniqueness-checked before the spawn.
    leaf_key: str | None = Field(default=None, alias="leafKey")


class TerminalAttachLeafRequest(BaseModel):
    """Body of ``POST /api/terminal/{session}/attach-leaf``: claim a leaf for an existing session."""

    leaf_key: str = Field(alias="leafKey")
    role: str | None = None


class TerminalRetireRequest(BaseModel):
    """Body of ``POST /api/terminal/{session}/retire``: the retire authority check.

    ``actor_session`` is the RETIRING seat's own catalog session id (self-declared, mirroring
    ``spawn_agent_session``'s ``spawned_by_session`` provenance -- there is no ambient "who am I"
    session-id resolution in this codebase). ``reason`` is a free-form human-readable justification,
    always recorded in the retirement provenance.
    """

    actor_session: str = Field(alias="actorSession")
    reason: str = "manual retire"


class TerminalLandedCleanupRequest(BaseModel):
    """Body of ``POST /api/terminal/landed-cleanup``: close selected archive rows."""

    session_ids: list[str] = Field(default_factory=list, alias="sessionIds")


class TerminalRenameRequest(BaseModel):
    """Body of ``POST /api/terminal/{session}/rename``: the new display label."""

    label: str


class TerminalPasteRequest(BaseModel):
    """Body of ``POST /api/terminal/{session}/paste``: deliver a context packet to a hosted session.

    The server-side mirror of the frontend seam: submitted text receives a unique delivery id and is
    confirmed from the target harness session log; a draft stays an unsubmitted draft.
    """

    text: str
    submit: bool = False


class OperatorInboxPostRequest(BaseModel):
    """Body of ``POST /api/operator-inbox`` for non-hosted chat replies."""

    lifecycle_id: str | None = Field(default=None, alias="lifecycleId")
    agent_id: str | None = Field(default=None, alias="agentId")
    sender_agent_id: str | None = Field(default=None, alias="senderAgentId")
    sender_role: AgentRole | None = Field(default=None, alias="senderRole")
    recipient_role: AgentRole | None = Field(default=None, alias="recipientRole")
    gate_id: str | None = Field(default=None, alias="gateId")
    message_kind: InboxMessageKind = Field(default="message", alias="messageKind")
    artifact_path: str | None = Field(default=None, alias="artifactPath")
    deliver_to_hosted: bool = Field(default=True, alias="deliverToHosted")
    ask: str
    response: str


def _catalog_payload(entry: TerminalCatalogEntry) -> dict[str, Any]:
    return entry.to_json()


def _resolve_request_leaf_key(config: McpRuntimeConfig, leaf_key: str | None) -> str | None:
    if leaf_key is None:
        return None
    return resolve_catalog_leaf_key(config, leaf_key)


def _leaf_ref_response(error: LeafRefResolutionError, leaf_key: str) -> JSONResponse:
    return JSONResponse(
        content={"status": error.status, "leafKey": leaf_key, "detail": str(error)},
        status_code=400,
    )


def _attach_terminal_session(
    sessions: HostedSessionRuntime,
    *,
    session_id: str,
    attached_at: str,
    checked_at: datetime,
    liveness_config: TerminalCatalogLivenessConfig,
) -> TerminalSession | None:
    catalog = sessions.catalog
    host = sessions.host
    entry = catalog.get(session_id)
    if entry is None or entry.status not in ("running", "landed"):
        return None
    observation = observe_terminal_liveness(
        catalog,
        host,
        entry,
        checked_at=checked_at,
        probe=LivenessProbe(hysteresis=liveness_config),
    )
    if not observation.alive or observation.entry.status not in ("running", "landed"):
        return None
    entry = observation.entry
    session = host.attach(
        session_id,
        TerminalSessionSpec(
            cwd=entry.cwd,
            command=entry.command,
            lifecycle_id=entry.lifecycle_id,
            name=entry.tmux_name,
            suspend_unsafe=entry.kind == "harness",
        ),
    )
    catalog.mark_attached(session_id, attached_at)
    return session


@dataclass(frozen=True)
class _ResolvedLiveInputs:
    provider_state: bool
    landing_state: bool
    change_watch: bool


@dataclass(frozen=True)
class LiveProjectionInputs:
    """Which live world-inputs this app drives; ``None`` means "infer from the replay seam".

    One value because it is one question -- is this app attached to a moving world, or replaying a
    recorded one? Sim replay must disable all three together (the feeder writes only *inside* a
    tick, so a change-gated loop would never wake), and answering it three times independently is
    how a replay app ends up half-live.
    """

    provider_state: bool | None = None
    landing_state: bool | None = None
    change_watch: bool | None = None

    def resolved(self, replay: ProjectionReplay) -> _ResolvedLiveInputs:
        """Settle each unset toggle against the replay seam: a feeder means sim, so off."""

        live = replay.before_tick is None
        return _ResolvedLiveInputs(
            provider_state=live if self.provider_state is None else self.provider_state,
            landing_state=live if self.landing_state is None else self.landing_state,
            change_watch=live if self.change_watch is None else self.change_watch,
        )


@dataclass(frozen=True)
class ServingCollaborators:
    """Long-lived collaborators supplied instead of letting :func:`create_app` construct them.

    The same four objects :class:`_ServingRuntime` is built around. They are offered as one value
    because substituting them is one decision -- a test that fakes the terminal host almost always
    needs the catalog and paster that agree with it, and a host paired with someone else's catalog
    observes sessions that do not exist. ``None`` on any field constructs the real one.
    """

    terminal_host: TerminalHost | None = None
    terminal_catalog: TerminalCatalog | None = None
    terminal_paster: TerminalPaster | None = None
    harness_capability_catalog: HarnessCapabilityCatalog | None = None


INFERRED_LIVE_INPUTS = LiveProjectionInputs()
OWNED_SERVING_COLLABORATORS = ServingCollaborators()
"""No injected collaborators: the app constructs and owns every one of them."""


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
    staleness (default ``DEFAULT_HEARTBEAT_SECONDS``). ``collaborators`` default to freshly
    constructed ones (the Mode B2 terminal backend and friends); tests inject fakes to drive the
    WebSocket bridge without a real PTY.
    """
    enabled = live_inputs.resolved(replay)
    projector = Projector(
        config,
        cadence=cadence,
        replay=replay,
        refreshers=ProjectionRefreshers(
            provider=ProviderStateRefresher() if enabled.provider_state else None,
            landing=LandingStateRefresher(config) if enabled.landing_state else None,
            change_watcher=ProjectionInputWatcher(config) if enabled.change_watch else None,
        ),
    )
    interval = cadence.interval
    host = collaborators.terminal_host or TerminalHost()
    catalog = collaborators.terminal_catalog or TerminalCatalog(
        terminal_catalog_path(config.coordination_root)
    )
    paster = collaborators.terminal_paster or TerminalPaster()
    liveness_clock = replay.now or utc_now
    liveness_config = TerminalCatalogLivenessConfig()
    interaction_synchronizer = HostedInteractionSynchronizer(observer_root(config))
    liveness_sweeper = TerminalCatalogLivenessSweeper(
        catalog,
        host,
        now=replay.now,
        probe=LivenessProbe(
            hysteresis=liveness_config,
            on_control_snapshot=interaction_synchronizer.observe,
        ),
        on_turn_state_change=lambda observation: log_turn_state_change_event(
            config, observation.entry
        ),
    )
    # Resolved ONCE at boot: the stamp that makes a stale serving process visible.
    build = resolve_serving_build()

    # The serving daemon samples labeled provider
    # containers on its own cadence (decoupled from the 1s projection tick) into
    # the central metrics store — the feed for provider_status, the statistics
    # board, and the degradation protocol. Read-only + dockerless-safe.
    metrics_store = ProviderMetricsStore(config.coordination_root)
    runtime = _ServingRuntime(
        config=config,
        projector=projector,
        host=host,
        catalog=catalog,
        paster=paster,
        liveness_clock=liveness_clock,
        liveness_config=liveness_config,
        liveness_sweeper=liveness_sweeper,
        build=build,
        # The deterministic supervisor sweep runs on its own decoupled cadence
        # (default ~10s, settings-controlled), zero tokens, pure code. "The model is never the
        # polling layer": every predicate reads TerminalCatalog/OperatorInboxStore/
        # ExpectationRowStore/the nudge log DIRECTLY, never the projection.
        heartbeat_store=SupervisorHeartbeatStore(observer_root(config)),
        interval=interval,
    )
    app = FastAPI(
        title="Agents Remember dashboard",
        lifespan=_serving_lifespan(runtime, metrics_store),
    )
    # Gzip the multi-hundred-KB JSON bodies (/api/state ~1.3 MB, the files
    # catalog) for the clients that negotiate it. compresslevel=6: on a ~1.3 MB JSON body it
    # matches level 9's ratio for ~16% less CPU per serve. Starlette's responder excludes
    # text/event-stream by content type, so the SSE channels (/api/stream, /api/events, the
    # conversation event streams) keep streaming uncompressed and unbuffered (covered by test).
    app.add_middleware(GZipMiddleware, compresslevel=6)
    _register_projection_routes(app, runtime)
    _register_action_routes(app, runtime)
    _register_terminal_session_routes(app, runtime)
    _register_terminal_control_routes(app, runtime)
    register_files_routes(app, config)
    register_changeset_routes(app, config)
    register_notes_routes(app, config)
    register_harness_control_routes(
        app,
        ConversationRuntime(
            scope=ConversationScope(
                workspace_root=config.workspace_root,
                coordination_root=config.coordination_root,
            ),
            catalog=catalog,
            host=host,
            harness_registry=lambda: load_agentic_settings(config.coordination_root).harnesses,
            liveness_clock=liveness_clock,
            liveness_config=liveness_config,
            capability_catalog=(
                collaborators.harness_capability_catalog
                or HarnessCapabilityCatalog(config.workspace_root)
            ),
            authorization=LocalOperatorAuthorizationResolver.for_workspace(config.workspace_root),
        ),
    )
    mount_static(app)
    return app


@dataclass(frozen=True)
class _ServingRuntime:
    """The long-lived collaborators one serving app is built around.

    Both the route handlers and the background loops read from exactly this set, which is what
    lets each of them be an ordinary module-level function instead of a closure over
    :func:`create_app`'s locals -- and what lets one move to its own package without rewriting.
    """

    config: McpRuntimeConfig
    projector: Projector
    host: TerminalHost
    catalog: TerminalCatalog
    paster: TerminalPaster
    liveness_clock: Callable[[], datetime]
    liveness_config: TerminalCatalogLivenessConfig
    liveness_sweeper: TerminalCatalogLivenessSweeper
    build: ServingBuild
    heartbeat_store: SupervisorHeartbeatStore
    interval: float

    @property
    def observer_root(self) -> Path:
        """The observer store root every durable control-plane store is opened under."""

        return observer_root(self.config)


# --- background loops ---------------------------------------------------------------------------


async def _metrics_loop(config: McpRuntimeConfig, metrics_store: ProviderMetricsStore) -> None:
    while True:
        try:
            snapshot = await asyncio.to_thread(
                sample_provider_containers, cwd=config.coordination_root
            )
            await asyncio.to_thread(metrics_store.record, snapshot)
            await asyncio.to_thread(evaluate_provider_degradation, config)
            # Reclaim the append-only metrics log (O(1) stat unless past its byte budget).
            await asyncio.to_thread(metrics_store.compact)
        except Exception:
            logger.exception("provider metrics sample failed; retrying next interval")
        await asyncio.sleep(DEFAULT_SAMPLE_INTERVAL_SECONDS)


def _supervisor_context(runtime: _ServingRuntime) -> SupervisorContext:
    """One sweep's view of every store its predicates read directly."""

    settings = load_agentic_settings(runtime.config.coordination_root)
    root = runtime.observer_root
    return SupervisorContext(
        catalog=runtime.catalog,
        host=runtime.host,
        paster=runtime.paster,
        inbox_store=OperatorInboxStore(root),
        expectation_store=ExpectationRowStore(root),
        nudge_store=OrchestrationNudgeStore(root),
        signal_cooldown_store=SupervisorSignalCooldownStore(root),
        event_store=EventStore(root),
        heartbeat_store=runtime.heartbeat_store,
        coordination_root=runtime.config.coordination_root,
        stale_seat_seconds=max(settings.supervisor.interval_seconds * 4, 60.0),
        redeliver_rate_limit_seconds=settings.supervisor.redeliver_rate_limit_seconds,
        signal_cooldown_seconds=settings.supervisor.signal_cooldown_seconds,
        escalation_sla_seconds=settings.escalation.sla_seconds,
        escalation_rung_seconds=settings.escalation.rung_seconds,
        respawn_after_rung=settings.escalation.respawn_after_rung,
        redeliver_budget=settings.supervisor.redeliver_budget,
        escalation_budget=settings.supervisor.escalation_budget,
    )


async def _supervisor_loop(runtime: _ServingRuntime) -> None:
    while True:
        settings = load_agentic_settings(runtime.config.coordination_root)
        if not settings.supervisor.enabled:
            await asyncio.sleep(settings.supervisor.interval_seconds)
            continue
        try:
            ctx = _supervisor_context(runtime)
            await asyncio.to_thread(run_supervisor_sweep, ctx, now=runtime.liveness_clock())
        except Exception:
            logger.exception("supervisor sweep failed; retrying next interval")
        await asyncio.sleep(settings.supervisor.interval_seconds)


async def _malloc_trim_loop() -> None:
    # Opt-in glibc arena reclaim. The steady RSS growth is allocator
    # fragmentation from per-tick projection churn (gc object count is flat while RSS climbs);
    # malloc_trim(0) returns the freed arena pages to the OS and holds RSS flat. Off unless
    # AR_MALLOC_TRIM is set, glibc-only, and run off the event loop since it walks the arenas.
    interval = malloc_trim_interval_seconds()
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(trim_malloc)
        except Exception:
            logger.exception("malloc_trim failed; retrying next interval")


async def _workspace_river_compaction_loop(runtime: _ServingRuntime) -> None:
    while True:
        await asyncio.sleep(WORKSPACE_EVENT_COMPACT_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(
                compact_workspace_river, runtime.observer_root, now=runtime.liveness_clock()
            )
        except Exception:
            logger.exception("workspace event-river compaction failed; retrying next interval")


def _serving_lifespan(
    runtime: _ServingRuntime, metrics_store: ProviderMetricsStore
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """The app lifespan: prime the projection, run the background loops, stop them cleanly."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Compact once before accepting clients, then keep compacting on a slow live
        # cadence. Workspace cursors are virtual (base offset + physical offset), and append/compact/read
        # share a cross-process lock, so this is cursor-safe while MCP and serving processes both write.
        await asyncio.to_thread(
            compact_workspace_river, runtime.observer_root, now=runtime.liveness_clock()
        )
        await runtime.projector.prime()
        projection_task = asyncio.create_task(runtime.projector.run())
        metrics_task = asyncio.create_task(_metrics_loop(runtime.config, metrics_store))
        supervisor_task = asyncio.create_task(_supervisor_loop(runtime))
        river_compaction_task = asyncio.create_task(_workspace_river_compaction_loop(runtime))
        optional: list[asyncio.Task[None]] = []
        # The heap-growth diagnostic only exists when AR_HEAP_DIAG is set (tracemalloc started here
        # so the very first snapshot has a full trace history); otherwise there is no extra task at all.
        if start_heap_tracing():
            optional.append(asyncio.create_task(heap_diag_loop()))
        # Opt-in RSS bound (glibc arena reclaim), independent of the diagnostic above.
        if malloc_trim_enabled():
            optional.append(asyncio.create_task(_malloc_trim_loop()))
        background = [
            river_compaction_task,
            supervisor_task,
            metrics_task,
            projection_task,
            *optional,
        ]
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            for task in background:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            runtime.host.shutdown()

    return lifespan


# --- projection routes --------------------------------------------------------------------------


def _supervisor_heartbeat_payload(runtime: _ServingRuntime) -> dict[str, Any]:
    # The tick age at RESPONSE time (not the ETag-gated content revision --
    # a heartbeat is deliberately volatile, the same "ages excluded" posture delta.py already
    # applies to other live ages, so it never busts the projection's change-gate revision).
    settings = load_agentic_settings(runtime.config.coordination_root)
    moment = runtime.liveness_clock()
    heartbeat = runtime.heartbeat_store.read()
    age = heartbeat_age_seconds(heartbeat, now=moment)
    stale_cutoff = settings.supervisor.stale_cutoff_seconds
    return {
        "lastTickAt": heartbeat.lastTickAt if heartbeat is not None else None,
        "ageSeconds": age,
        "staleCutoffSeconds": stale_cutoff,
        "stale": age is None or age >= stale_cutoff,
        "pendingInboxCount": heartbeat.pendingInboxCount if heartbeat is not None else 0,
        "redeliverableInboxCount": (
            heartbeat.redeliverableInboxCount if heartbeat is not None else 0
        ),
        "lastSweepDurationSeconds": (
            heartbeat.lastSweepDurationSeconds if heartbeat is not None else None
        ),
    }


def _state_response(runtime: _ServingRuntime, if_none_match: str | None) -> Response:
    # The change gate: the ETag is the projector's content revision, which only
    # advances when the stable projection form changes (volatile ages excluded -- delta.py).
    # An If-None-Match poll of an unchanged projection therefore costs a header exchange
    # instead of a ~780 KB dump+parse; when content DID change, the full fresh dump serves
    # exactly as before. `Cache-Control: no-cache` keeps any cache honest (always revalidate).
    seq, snapshot = runtime.projector.current()
    if snapshot is None:
        raise HTTPException(status_code=503, detail="projection not ready")
    revision = runtime.projector.revision(seq)
    headers = {"ETag": f'W/"{revision}"', "Cache-Control": "no-cache"}
    if _if_none_match_matches(if_none_match, revision):
        return Response(status_code=304, headers=headers)
    # The dump rides the per-instance memo; only the volatile tail
    # (build stamp + at-response-time heartbeat) is computed per request, on a copy.
    body = dict(_projection_body_cache.body(snapshot))
    body["servingBuild"] = runtime.build.payload()
    body["supervisorHeartbeat"] = _supervisor_heartbeat_payload(runtime)
    return JSONResponse(content=body, headers=headers)


def _task_document_response(runtime: _ServingRuntime, path: str) -> JSONResponse:
    _, snapshot = runtime.projector.current()
    if snapshot is None:
        raise HTTPException(status_code=503, detail="projection not ready")
    doc = read_task_document_body(
        runtime.config.coordination_root,
        doc_path=path,
        enclosures=snapshot.enclosures,
        now=runtime.liveness_clock(),
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="task document not found")
    return JSONResponse(content=doc.model_dump(by_alias=True, exclude_none=True))


def _register_projection_routes(app: FastAPI, runtime: _ServingRuntime) -> None:
    """The read side of the cockpit: the projection once, tailed, and the raw event river."""

    @app.get("/api/state")
    def api_state(
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> Response:
        return _state_response(runtime, if_none_match)

    @app.get("/api/task-document")
    def api_task_document(path: Annotated[str, Query()]) -> JSONResponse:
        return _task_document_response(runtime, path)

    @app.get("/api/stream", response_class=EventSourceResponse)
    async def api_stream() -> AsyncIterator[ServerSentEvent]:
        async for event in stream_events(
            runtime.projector,
            build=runtime.build,
            supervisor_heartbeat=_supervisor_heartbeat_payload(runtime),
        ):
            yield event

    @app.get("/api/events", response_class=EventSourceResponse)
    async def api_events(
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> AsyncIterator[ServerSentEvent]:
        async for event in stream_raw_events(
            runtime.config, last_event_id=last_event_id, interval=runtime.interval
        ):
            yield event


# --- action + operator-inbox routes -------------------------------------------------------------


def _recorded_gate_decision(
    config: McpRuntimeConfig, decision: GateDecisionIntent
) -> dict[str, Any]:
    """Durably record one gate decision as developer-attributed, and answer with the gate.

    Developer attribution is the un-forgeable half of the contract -- the agent's own path is
    model-attributed -- and it is what server-side closeout enforcement consumes.

    Every intent that arrives here is ADDRESSED. Whether a request names a gate to decide is a
    question about the request, so it is settled once, in the layer that validates requests:
    ``actions._gate_decision_outcome`` refuses a decision naming neither a lifecycle target nor a
    gate id with 400 ``missing-target`` and never builds an intent for that shape. A decision
    without a lifecycle id is therefore the gate-id-only cancel that guard let through.
    """

    verdict = GateVerdict(
        decision=decision.decision,
        by="developer",
        via="dashboard",
        note=decision.note,
    )
    if decision.lifecycle_id is not None:
        return gate_decide_for_lifecycle(
            config,
            lifecycle_id=decision.lifecycle_id,
            verdict=verdict,
            expected_gate_id=decision.gate_id,
        )
    assert decision.gate_id is not None
    return gate_decide_payload(config, gate_id=decision.gate_id, lifecycle_id=None, verdict=verdict)


def _gate_decision_response(
    config: McpRuntimeConfig,
    outcome: ActionOutcome,
    decision: GateDecisionIntent,
    *,
    target: str | None,
) -> Response:
    """Record the operator's gate decision and answer with the gate it wrote."""

    try:
        gate = _recorded_gate_decision(config, decision)
    except KeyError as exc:
        return JSONResponse(
            content={
                "status": "stale-gate" if decision.gate_id else "no-open-gate",
                "detail": str(exc),
                "target": target,
            },
            status_code=409,
        )
    return JSONResponse(content={**outcome.body, "gate": gate}, status_code=outcome.status_code)


def _dismissal_response(
    config: McpRuntimeConfig, outcome: ActionOutcome, intent: DismissalIntent
) -> Response:
    """Apply one attention dismissal.

    Dismissals are current acknowledgements, not history. A gate-open item is consumed by
    cancelling the gate itself, so it needs no acknowledgement row once the source is gone.

    Every intent that arrives here is SCOPED, because whether a request carries a scope is settled
    once, in the layer that validates requests: ``actions._dismiss_action_outcome`` refuses an
    acknowledgement that names neither a lifecycle, nor a gate to cancel, nor the repo-level
    ``actionable-drift`` signal with 400 ``missing-lifecycle``. So a dismissal that is not the gate
    cancel below is one of the two the acknowledgement row can be written for.
    """

    gate: dict[str, Any] | None = None
    if intent.kind == "gate-open" and intent.gate_id is not None:
        with contextlib.suppress(KeyError):
            gate = gate_decide_payload(
                config,
                gate_id=intent.gate_id,
                lifecycle_id=intent.lifecycle_id,
                verdict=GateVerdict(
                    decision="cancel",
                    by="developer",
                    via="dashboard",
                    note=intent.note or "Dismissed from attention queue.",
                ),
            )
    else:
        AttentionDismissalStore(observer_root(config)).dismiss(
            AttentionDismissalRecord(
                itemId=intent.item_id,
                dismissedAt=intent.dismissed_at,
                kind=intent.kind,
                lifecycleId=intent.lifecycle_id,
                gateId=intent.gate_id,
            )
        )
    body = outcome.body if gate is None else {**outcome.body, "gate": gate}
    return JSONResponse(content=body, status_code=outcome.status_code)


def _action_response(runtime: _ServingRuntime, action: str, request: ActionRequest) -> Response:
    """Route one action: acknowledge it, apply its dismissal, or record its gate decision."""

    _, snapshot = runtime.projector.current()
    if snapshot is None:
        raise HTTPException(status_code=503, detail="projection not ready")
    outcome = evaluate_action(
        snapshot,
        action,
        request.target,
        ActionEvaluationContext(
            actor=request.actor,
            now=now_iso(),
            gate_id=request.gateId,
            note=request.note,
            item_id=request.itemId,
            kind=request.kind,
        ),
    )
    if outcome.dismissal is not None:
        return _dismissal_response(runtime.config, outcome, outcome.dismissal)
    if outcome.gate_decision is not None:
        return _gate_decision_response(
            runtime.config, outcome, outcome.gate_decision, target=request.target
        )
    return JSONResponse(content=outcome.body, status_code=outcome.status_code)


def _operator_inbox_response(
    runtime: _ServingRuntime, request: OperatorInboxPostRequest
) -> Response:
    # External-chat path: a dashboard response with no hosted session is written to the
    # pull-based operator inbox. External agents read it through the MCP operator_inbox_poll /
    # operator_inbox_consume tools; this endpoint only owns the developer/dashboard write side.
    try:
        payload = operator_inbox_post_payload(
            runtime.config,
            address=InboxAddress(
                lifecycle_id=request.lifecycle_id,
                agent_id=request.agent_id,
                recipient_role=request.recipient_role,
            ),
            message=InboxMessage(
                ask=request.ask,
                response=request.response,
                message_kind=request.message_kind,
                gate_id=request.gate_id,
                artifact_path=request.artifact_path,
            ),
            poster=InboxPoster(
                created_by="developer",
                created_via="dashboard",
                sender_agent_id=request.sender_agent_id,
                sender_role=request.sender_role,
            ),
            delivery=HostedDelivery(
                enabled=request.deliver_to_hosted,
                catalog=runtime.catalog,
                host=runtime.host,
                paster=runtime.paster,
            ),
        )
    except ValueError as exc:
        return JSONResponse(content={"status": "bad-address", "detail": str(exc)}, status_code=400)
    return JSONResponse(content=payload, status_code=200)


def _inbox_dismiss_response(runtime: _ServingRuntime, entry_id: str) -> Response:
    removed = OperatorInboxStore(runtime.observer_root).delete(entry_id)
    if not removed:
        return JSONResponse(content={"status": "not-found", "entryId": entry_id}, status_code=404)
    return JSONResponse(content={"status": "dismissed", "entryId": entry_id}, status_code=200)


def _register_action_routes(app: FastAPI, runtime: _ServingRuntime) -> None:
    """The write side the developer drives: the gate return channel and the operator inbox."""

    @app.post("/api/actions/{action}")
    def api_action(action: str, request: ActionRequest) -> Response:
        return _action_response(runtime, action, request)

    @app.post("/api/operator-inbox")
    def api_operator_inbox(request: OperatorInboxPostRequest) -> Response:
        return _operator_inbox_response(runtime, request)

    @app.post("/api/operator-inbox/{entry_id}/dismiss")
    def api_operator_inbox_dismiss(entry_id: str) -> Response:
        return _inbox_dismiss_response(runtime, entry_id)


# --- terminal session routes --------------------------------------------------------------------


async def _serve_terminal_websocket(
    runtime: _ServingRuntime, websocket: WebSocket, session: str
) -> None:
    # Mode B2: bridge a live terminal-host session to xterm.js. Attach-only
    # -- the session is opened out-of-band (the lifecycle-correlated launch comes
    # later); an unknown id is refused with a private close code. Same localhost posture
    # as the rest of the app (the host spawns a fixed argv, never a wire-supplied command).
    await websocket.accept()
    session_obj = _attach_terminal_session(
        HostedSessionRuntime(catalog=runtime.catalog, host=runtime.host),
        session_id=session,
        attached_at=now_iso(),
        checked_at=runtime.liveness_clock(),
        liveness_config=runtime.liveness_config,
    )
    if session_obj is None:
        await websocket.close(code=4404)
        return
    try:
        await _bridge_terminal(websocket, runtime.host, session_obj)
    finally:
        if not session_obj.is_alive:
            runtime.catalog.mark_exited(session)
        else:
            runtime.host.close_session(session_obj)
        with contextlib.suppress(RuntimeError):
            await websocket.close()


def _detected_harnesses_payload(runtime: _ServingRuntime) -> dict[str, Any]:
    # The supported TUI harnesses + whether each is installed here. The dashboard
    # renders a launch button per *detected* harness; the argv stays server-side (open via POST).
    # The EFFECTIVE registry (builtin merged with orchestration.harnesses in the
    # GLOBAL agentic settings, per-use) -- settings-defined harnesses get buttons too. Repo-local
    # overrides are leaf-scoped dispatch material (the MCP spawn tool), not workspace buttons.
    registry = load_agentic_settings(runtime.config.coordination_root).harnesses
    return {
        "harnesses": [
            {"id": h.id, "name": h.name, "detected": h.detected}
            for h in detect_harnesses(registry=registry)
        ]
    }


def _register_terminal_session_routes(app: FastAPI, runtime: _ServingRuntime) -> None:
    """Attaching to a live pane, and what there is to attach to."""

    @app.websocket("/api/terminal/{session}")
    async def api_terminal(websocket: WebSocket, session: str) -> None:
        await _serve_terminal_websocket(runtime, websocket, session)

    @app.get("/api/terminal/sessions")
    def api_terminal_sessions() -> dict[str, Any]:
        return {
            "sessions": [_catalog_payload(entry) for entry in runtime.liveness_sweeper.refresh()]
        }

    @app.get("/api/harnesses")
    def api_harnesses() -> dict[str, Any]:
        return _detected_harnesses_payload(runtime)


# --- terminal control routes --------------------------------------------------------------------


def _landed_cleanup_response(runtime: _ServingRuntime, session_ids: Sequence[str]) -> Response:
    closed: list[str] = []
    skipped: list[dict[str, str]] = []
    closed_entries: list[TerminalCatalogEntry] = []
    for session in session_ids:
        entry = runtime.catalog.get(session)
        if entry is None:
            skipped.append({"session": session, "reason": "unknown-session"})
            continue
        if entry.status != "landed":
            skipped.append({"session": session, "reason": f"status:{entry.status}"})
            continue
        updated = retire_entry(
            runtime.catalog,
            runtime.host,
            entry,
            SeatClosure(
                at=now_iso(),
                by_session=None,
                reason="landed group cleanup",
                edge="landed-group-cleanup",
            ),
        )
        if updated is None:
            skipped.append({"session": session, "reason": "unknown-session"})
            continue
        closed.append(session)
        closed_entries.append(updated)
    for entry in closed_entries:
        log_retire_event(runtime.config, entry)
    return JSONResponse(
        content={
            "status": "cleaned",
            "closed": len(closed),
            "skipped": len(skipped),
            "closedSessions": closed,
            "skippedSessions": skipped,
        },
        status_code=200,
    )


def _terminal_entry_payload(entry: TerminalCatalogEntry) -> dict[str, Any]:
    """The catalog facts every open/conflict answer repeats about one durable row."""

    return {
        "session": entry.id,
        "kind": entry.kind,
        "harness": entry.harness,
        "tmuxName": entry.tmux_name,
        "controlState": entry.control_state,
        "controlEndpoint": (
            str(entry.control_endpoint) if entry.control_endpoint is not None else None
        ),
        "controlProtocol": entry.control_protocol,
        "resolvedModel": entry.resolved_model,
        "resolvedEffort": entry.resolved_effort,
    }


def _open_terminal_response(
    runtime: _ServingRuntime, session: str, request: TerminalOpenRequest
) -> Response:
    # Mode B2 opener: the dashboard *spawns + owns* a
    # session, then the WebSocket above attaches to it. The leaf-claim + ensure + upsert
    # composition lives in the shared `open_terminal_session` so this route and the agent-facing
    # `spawn_agent_session` MCP tool spawn through ONE opener (no parallel spawn path).
    config = runtime.config
    try:
        leaf_key = _resolve_request_leaf_key(config, request.leaf_key)
    except LeafRefResolutionError as exc:
        return _leaf_ref_response(exc, request.leaf_key or "")
    try:
        resolved_launch = resolve_terminal_open_selection(
            kind=request.kind,
            harness=request.harness,
            model=request.model,
            effort=request.effort,
            workspace=config.workspace_root,
        )
    except HarnessControlError as exc:
        return JSONResponse(
            content={"status": "launch-selection-invalid", "detail": str(exc)},
            status_code=400,
        )
    result = open_terminal_session(
        runtime=HostedSessionRuntime(catalog=runtime.catalog, host=runtime.host),
        session_id=session,
        launch=TerminalLaunchRequest(
            kind=request.kind,
            workspace_root=config.workspace_root,
            shell=os.environ.get("SHELL") or DEFAULT_SHELL,
            harness=request.harness,
            # Resolve harness ids against the effective GLOBAL registry (builtin merged
            # with orchestration.harnesses) so dashboard launches and MCP dispatches agree on argv.
            # Loaded only for harness-kind opens: a malformed settings file must
            # fail the launches that USE it, never a plain scratch terminal.
            harnesses=(
                load_agentic_settings(config.coordination_root).harnesses
                if request.kind == "harness" or request.harness
                else None
            ),
            control=ControlRunnerRequest(
                resolved_launch=resolved_launch,
                endpoint_root=config.coordination_root / "runtime" / "harness-control",
            ),
        ),
        provenance=SpawnProvenance(
            label=request.label,
            lifecycle_id=request.lifecycle_id,
            leaf_key=leaf_key,
        ),
    )
    if result.status == "bad-kind":
        return JSONResponse(
            content={"status": "bad-kind", "detail": result.detail}, status_code=400
        )
    if result.status == "leaf-taken":
        # Server-authoritative pair uniqueness; the client guard is only advisory.
        return JSONResponse(
            content={
                "status": "leaf-taken",
                "leafKey": leaf_key,
                "session": result.owner_session_id,
            },
            status_code=409,
        )
    entry = result.entry
    assert entry is not None  # opened/conflict => the actual durable row
    if result.status == "launch-conflict":
        return JSONResponse(
            content={
                **_terminal_entry_payload(entry),
                "status": "launch-selection-conflict",
                "detail": result.detail,
            },
            status_code=409,
        )
    return JSONResponse(
        content={
            **_terminal_entry_payload(entry),
            "label": entry.label,
            "lifecycleId": entry.lifecycle_id,
            "leafKey": entry.leaf_key,
            "seatRole": entry.binding_role,
            "cwd": str(entry.cwd),
            "status": "running",
        },
        status_code=200,
    )


def _attach_leaf_response(
    runtime: _ServingRuntime, session: str, request: TerminalAttachLeafRequest
) -> Response:
    # Claim or move one existing session's leaf-role binding (enclosure-free, no respawn).
    try:
        leaf_key = _resolve_request_leaf_key(runtime.config, request.leaf_key)
    except LeafRefResolutionError as exc:
        return _leaf_ref_response(exc, request.leaf_key)
    assert leaf_key is not None
    result = assign_terminal_session_to_leaf(
        runtime.catalog,
        runtime.host,
        session_id=session,
        leaf_key=leaf_key,
        role=request.role,
    )
    if result.status == "unknown-session":
        return JSONResponse(content={"status": "unknown-session"}, status_code=404)
    if result.status == "leaf-taken":
        return JSONResponse(
            content={
                "session": result.owner_session_id,
                "status": "leaf-taken",
                "leafKey": leaf_key,
            },
            status_code=409,
        )
    if result.status == "role-required":
        return JSONResponse(
            content={
                "session": session,
                "status": "role-required",
                "leafKey": leaf_key,
                "detail": "role is required for a hand-opened harness session",
            },
            status_code=400,
        )
    return JSONResponse(
        content={
            "session": session,
            "status": "attached",
            "leafKey": leaf_key,
            "role": result.role,
            "seatRole": result.seat_role,
            "previousSeatRole": result.previous_seat_role,
        },
        status_code=200,
    )


def _live_paste_target(runtime: _ServingRuntime, session: str) -> TerminalCatalogEntry | None:
    """The running catalog row for one seat, re-proven live, or ``None`` if it is neither."""

    entry = runtime.catalog.get(session)
    if entry is None or entry.status != "running":
        return None
    observation = observe_terminal_liveness(
        runtime.catalog,
        runtime.host,
        entry,
        checked_at=runtime.liveness_clock(),
        probe=LivenessProbe(hysteresis=runtime.liveness_config),
    )
    if not observation.alive or observation.entry.status != "running":
        return None
    return observation.entry


def _harness_submit_response(
    entry: TerminalCatalogEntry,
    session: str,
    request: TerminalPasteRequest,
    *,
    delivery_id: str,
) -> Response:
    """Deliver operator input to a protocol harness as one correlated whole message.

    A harness never takes raw pane input, so a legacy session with no adapter and an unsubmitted
    draft are both refused here rather than degraded to a keystroke path.
    """

    if entry.control_endpoint is None:
        return JSONResponse(
            content={
                "session": session,
                "status": "unsupported",
                "detail": "legacy harness session has no protocol adapter",
            },
            status_code=409,
        )
    if not request.submit:
        return JSONResponse(
            content={
                "session": session,
                "status": "draft-not-submitted",
                "detail": "harness drafts remain on the attached terminal surface",
            },
            status_code=409,
        )
    try:
        receipt = submit_control_prompt(
            entry, request.text, ControlSubmission(source="terminal", request_id=delivery_id)
        )
    except HarnessControlError as exc:
        return JSONResponse(
            content={
                "session": session,
                "entryId": delivery_id,
                "status": "unconfirmed",
                "delivered": False,
                "submitted": True,
                "detail": str(exc),
            },
            status_code=200,
        )
    delivered = receipt.acceptance in {"immediate", "queued"}
    return JSONResponse(
        content={
            "session": session,
            "entryId": delivery_id,
            "status": "delivered" if delivered else "unconfirmed",
            "delivered": delivered,
            "submitted": True,
            "acceptance": receipt.acceptance,
            "vendorCorrelationId": receipt.vendor_correlation_id,
            "acceptedAt": receipt.accepted_at,
            "detail": receipt.detail,
        },
        status_code=200,
    )


def _pane_paste_response(
    runtime: _ServingRuntime,
    entry: TerminalCatalogEntry,
    session: str,
    request: TerminalPasteRequest,
    *,
    delivery_id: str,
) -> Response:
    """Paste into an ordinary terminal's pane; transport is the only thing this path can prove."""

    outcome = runtime.paster.paste(
        entry.tmux_name,
        request.text,
        submit=request.submit,
        accepted=None,
    )
    delivered = outcome.delivered
    content: dict[str, object] = {
        "session": session,
        "entryId": delivery_id,
        "status": "delivered" if delivered else "unconfirmed",
        "delivered": delivered,
        "submitted": request.submit and delivered,
    }
    if not delivered:
        content["capture"] = outcome.capture
    return JSONResponse(content=content, status_code=200)


def _paste_response(
    runtime: _ServingRuntime, session: str, request: TerminalPasteRequest
) -> Response:
    # Explicit operator terminal input. Inter-agent messaging uses the durable inbox route;
    # a protocol harness receives one correlated whole message, never raw pane input.
    entry = _live_paste_target(runtime, session)
    if entry is None:
        return JSONResponse(content={"status": "unknown-session"}, status_code=404)
    delivery_id = uuid4().hex
    if entry.kind == "harness":
        return _harness_submit_response(entry, session, request, delivery_id=delivery_id)
    return _pane_paste_response(runtime, entry, session, request, delivery_id=delivery_id)


def _terminate_response(runtime: _ServingRuntime, session: str) -> Response:
    entry = runtime.catalog.get(session)
    live = runtime.host.get(session)
    if entry is None and live is None:
        return JSONResponse(content={"status": "unknown-session"}, status_code=404)
    control_stop_detail = None
    if entry is not None and entry.control_endpoint is not None:
        try:
            stop_control_session(entry)
        except HarnessControlError as exc:
            control_stop_detail = str(exc)
    runtime.host.terminate(session, tmux_name=entry.tmux_name if entry is not None else None)
    terminated_at = now_iso()
    updated = runtime.catalog.mark_terminated(session, terminated_at)
    return JSONResponse(
        content={
            "session": session,
            "status": "terminated",
            "terminatedAt": terminated_at,
            **({"tmuxName": updated.tmux_name} if updated is not None else {}),
            **({"controlStopDetail": control_stop_detail} if control_stop_detail else {}),
        },
        status_code=200,
    )


def _retire_response(
    runtime: _ServingRuntime, session: str, request: TerminalRetireRequest
) -> Response:
    # The server-authoritative retire surface. Never a zombie row --
    # a retire is the SAME terminal mark ``/terminate`` writes, plus retirement provenance
    # (who, why, when, which edge); transcripts are never touched. Authority is enforced here,
    # not trusted from the caller: owner-never-self-retires, a manager retires only its own
    # master's worker/reviewer seats, the orchestrator retires anything.
    target_entry = runtime.catalog.get(session)
    if target_entry is None:
        return JSONResponse(content={"status": "unknown-session"}, status_code=404)
    actor_entry = runtime.catalog.get(request.actor_session)
    if actor_entry is None:
        return JSONResponse(
            content={"status": "unknown-actor", "actorSession": request.actor_session},
            status_code=404,
        )
    if target_entry.status == "terminated":
        return JSONResponse(
            content={
                "session": session,
                "status": "already-retired",
                "retiredAt": target_entry.retired_at,
            },
            status_code=200,
        )
    try:
        check_retire_authority(_seat_ref(actor_entry), _seat_ref(target_entry))
    except RetirePolicyError as exc:
        return JSONResponse(
            content={"session": session, "status": "retire-refused", "detail": str(exc)},
            status_code=403,
        )
    updated = retire_entry(
        runtime.catalog,
        runtime.host,
        target_entry,
        SeatClosure(
            at=now_iso(), by_session=request.actor_session, reason=request.reason, edge="manual"
        ),
    )
    assert updated is not None  # the entry existed above; no concurrent delete path removes rows
    log_retire_event(runtime.config, updated)
    return JSONResponse(
        content={
            "session": session,
            "status": "retired",
            "retiredAt": updated.retired_at,
            "retiredBySession": updated.retired_by_session,
            "retiredReason": updated.retired_reason,
            "retiredEdge": updated.retired_edge,
        },
        status_code=200,
    )


def _seat_ref(entry: TerminalCatalogEntry) -> SeatRef:
    """The three facts retire authority is decided from: who, on which leaf, in which role."""

    return SeatRef(
        session_id=entry.id,
        leaf_key=entry.binding_leaf_key,
        seat_role=entry.binding_role,
    )


def _rename_response(runtime: _ServingRuntime, session: str, label: str) -> Response:
    # Post-spawn identity rename. Identity text ONLY -- spawn_role
    # (the immutable seat role) is never touched by a rename.
    entry = runtime.catalog.get(session)
    if entry is None or entry.status == "terminated":
        return JSONResponse(content={"status": "unknown-session"}, status_code=404)
    updated = runtime.catalog.set_label(session, label)
    assert updated is not None
    log_rename_event(runtime.config, updated)
    return JSONResponse(
        content={
            "session": session,
            "status": "renamed",
            "label": updated.label,
            "spawnedLabel": updated.spawned_label,
        },
        status_code=200,
    )


async def _terminal_image_response(
    runtime: _ServingRuntime, session: str, request: Request, file: UploadFile
) -> Response:
    # The terminal channel is text-only, so a pasted screenshot is carried by
    # saving it under the session's own cwd and injecting the on-disk path (Claude Code auto-attaches
    # an image path before the model runs). Same localhost posture as the rest of serving/; writes
    # ONLY under the session cwd, with a uuid basename (no traversal) and validated type (extension +
    # magic bytes) + size. Returns the absolute path the composer injects over {type:stdin}.
    # SECURITY NOTE: like the rest of serving/ this is unauthenticated and 127.0.0.1-bound, but unlike
    # the JSON POSTs it is multipart (a CORS "simple request", preflight-free). The write target is
    # keyed by an unguessable client UUID, so cross-origin/CSRF writes can't target a real session;
    # an Origin/Host allowlist for all write routes is folded into the documented remote-auth story.
    session_obj = runtime.host.get(session)
    entry = runtime.catalog.get(session)
    cwd = session_obj.cwd if session_obj is not None else (entry.cwd if entry else None)
    if cwd is None:
        return JSONResponse(content={"status": "unknown-session"}, status_code=404)
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > _MAX_IMAGE_BYTES + 4096:
        return JSONResponse(content={"status": "too-large"}, status_code=413)  # fast reject
    ext = Path(file.filename or "").suffix.lstrip(".").lower()
    if ext not in _IMAGE_EXTS:
        return JSONResponse(content={"status": "bad-type"}, status_code=400)
    body = await file.read(_MAX_IMAGE_BYTES + 1)  # +1 so an at-cap read still detects oversize
    if len(body) > _MAX_IMAGE_BYTES:
        return JSONResponse(content={"status": "too-large"}, status_code=413)
    if not body or not _looks_like_image(body, ext):
        return JSONResponse(content={"status": "bad-type"}, status_code=400)  # empty / not an image
    dest = cwd / ".dashboard-pastes" / f"{uuid4().hex}.{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)  # flush before the path is injected -- the harness validates existence
    return JSONResponse(content={"path": str(dest.resolve())}, status_code=200)


def _register_terminal_control_routes(app: FastAPI, runtime: _ServingRuntime) -> None:
    """Everything that changes a seat: open it, bind it, feed it, rename it, retire it."""

    @app.post("/api/terminal/landed-cleanup")
    def api_terminal_landed_cleanup(request: TerminalLandedCleanupRequest) -> Response:
        return _landed_cleanup_response(runtime, request.session_ids)

    @app.post("/api/terminal/{session}")
    def api_terminal_open(session: str, request: TerminalOpenRequest) -> Response:
        return _open_terminal_response(runtime, session, request)

    @app.post("/api/terminal/{session}/attach-leaf")
    def api_terminal_attach_leaf(session: str, request: TerminalAttachLeafRequest) -> Response:
        return _attach_leaf_response(runtime, session, request)

    @app.post("/api/terminal/{session}/paste")
    def api_terminal_paste(session: str, request: TerminalPasteRequest) -> Response:
        return _paste_response(runtime, session, request)

    @app.post("/api/terminal/{session}/terminate")
    def api_terminal_terminate(session: str) -> Response:
        return _terminate_response(runtime, session)

    @app.post("/api/terminal/{session}/retire")
    def api_terminal_retire(session: str, request: TerminalRetireRequest) -> Response:
        return _retire_response(runtime, session, request)

    @app.post("/api/terminal/{session}/rename")
    def api_terminal_rename(session: str, request: TerminalRenameRequest) -> Response:
        return _rename_response(runtime, session, request.label)

    @app.post("/api/terminal/{session}/image")
    async def api_terminal_image(
        session: str, request: Request, file: Annotated[UploadFile, File()]
    ) -> Response:
        return await _terminal_image_response(runtime, session, request, file)
