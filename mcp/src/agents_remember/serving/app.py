"""The dashboard FastAPI app: one-shot state, the multiplexed SSE stream, raw events, actions.

Endpoints:

* ``GET  /api/state``           -- the current projection once (curl-friendly, no streaming).
* ``GET  /api/stream``          -- one EventSource: an ``event: snapshot`` with the full
  projection on connect, then per-entity ``state`` deltas (``lifecycle`` / ``enclosure`` /
  ``provider`` / ``metrics`` / ``analytics`` and their ``*.removed`` markers) fanned out from
  the shared :class:`Projector`.
* ``GET  /api/events``          -- the raw ``ar-observer-event/v1`` log, tailed with exact
  byte-offset ``Last-Event-ID`` resume (slice 4b). A *separate* stream from ``/api/stream``:
  it resumes by byte offset, the state channel re-snapshots, so mixing them on one stream
  would be incoherent. The cockpit opens both (still well under the ~6 connections/origin cap).
* ``POST /api/actions/{action}``-- the gate return-channel. Lifecycle transitions
  (``resume`` / ``integrate`` / ``cleanup``) are validated against the reducer's
  ``ActionAvailability`` and acknowledged without mutation (slice 4b); gate-decision verbs
  (``approve`` / ``reject`` / ``request-revision`` / ``cancel``) are recorded as
  developer-attributed gate decisions (slice 6b), which server-side closeout enforcement
  makes binding. Routing maps :class:`ActionOutcome` onto the response; see ``serving/actions.py``.
* ``POST /api/operator-inbox``  -- the external-chat gate response return channel. The
  dashboard writes a developer response to the append-only operator inbox when no hosted chat
  session can be injected into; external agents poll/consume through the MCP ``operator_inbox_*``
  tools.

Local-first posture: bind ``127.0.0.1`` only (the CLI default) with no auth in v1. This is a
cockpit for the developer's own machine; exposing it (an SSH tunnel, a reverse proxy) hands an
unauthenticated reader the whole projection -- and the POST action surface -- so any multi-user
or remote story is a deliberate later design with its own auth gate.

The ``now`` / ``before_tick`` parameters are the sim seams (slice 4b): live serving leaves them
at their defaults; ``cli.dashboard`` passes a replay clock + fixture feeder under ``--sim``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
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

from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import AgentRole, InboxMessageKind
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.orchestration_nudges import OrchestrationNudgeStore
from agents_remember.controlplane.supervisor_signals import SupervisorSignalCooldownStore
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.mcp.tools.gates import gate_decide_for_lifecycle, gate_decide_payload
from agents_remember.mcp.tools.operator_inbox import operator_inbox_post_payload
from agents_remember.observer import observer_root
from agents_remember.observer.event_retention import (
    WORKSPACE_EVENT_COMPACT_INTERVAL_SECONDS,
    compact_workspace_river,
)
from agents_remember.observer.events import now_iso
from agents_remember.observer.projection_store import ProviderStateRefresher
from agents_remember.observer.snapshots import read_task_document_body
from agents_remember.observer.store import EventStore
from agents_remember.providers.degradation import evaluate_provider_degradation
from agents_remember.providers.metrics import (
    DEFAULT_SAMPLE_INTERVAL_SECONDS,
    ProviderMetricsStore,
    sample_provider_containers,
)
from agents_remember.serving.actions import ActionRequest, evaluate_action
from agents_remember.serving.build_info import ServingBuild, resolve_serving_build
from agents_remember.serving.changeset import register_changeset_routes
from agents_remember.serving.events import stream_raw_events
from agents_remember.serving.files import register_files_routes
from agents_remember.serving.harness_logs import HarnessSessionLog
from agents_remember.serving.harnesses import detect_harnesses
from agents_remember.serving.injector import DeliveryRow, deliver
from agents_remember.serving.leaf_ref_validation import resolve_catalog_leaf_key
from agents_remember.serving.notes import register_notes_routes
from agents_remember.serving.projector import Projector
from agents_remember.serving.retire import retire_entry
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
from agents_remember.serving.terminal import TerminalHost, TerminalSession
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    terminal_catalog_path,
)
from agents_remember.serving.terminal_leaf_assignment import assign_terminal_session_to_leaf
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    TerminalCatalogLivenessSweeper,
    observe_terminal_liveness,
    utc_now,
)
from agents_remember.serving.terminal_opener import open_terminal_session
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
    """The SSE event sequence for one connection: snapshot, then per-entity deltas.

    Module-level (not a route closure) so it is unit-testable without an HTTP client.
    ``build`` (the boot-time serving stamp, 260703-L15) rides the snapshot as
    ``servingBuild`` so the cockpit can render which process/commit is answering.
    ``supervisor_heartbeat`` (260707-HFX2-L2 R5) rides as ``supervisorHeartbeat`` -- the tick age
    at connect time, so a stale supervisor is visible in the dashboard header at a glance.
    """
    seq, snapshot = projector.current()
    if snapshot is not None:
        payload = _encode(snapshot)
        if build is not None:
            payload["servingBuild"] = build.payload()
        if supervisor_heartbeat is not None:
            payload["supervisorHeartbeat"] = supervisor_heartbeat
        yield ServerSentEvent(data=payload, event="snapshot", id=str(seq), retry=2000)
    async for seq, delta in projector.subscribe():
        yield ServerSentEvent(data=_encode(delta.data), event=delta.event, id=str(seq), retry=2000)


_TERMINAL_EXIT_FRAME = json.dumps({"type": "exit"})
"""The text frame sent to the browser once the PTY child exits (then the socket closes)."""

_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp"})
"""Image extensions Claude Code's vision accepts (slice 6f). BMP/TIFF/SVG are rejected -- BMP is the
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

    Malformed frames and unknown types are ignored; the fixed-argv host (slice 6d) accepts
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

    ``kind="terminal"`` spawns a shell (slice 6e-2a); ``kind="harness"`` spawns the supported TUI
    harness named by ``harness`` (its id, e.g. ``"claude"`` -- slice 6e-2b). The server resolves the
    command from the kind/harness id -- only ids are on the wire, never an argv -- so there is no
    command-injection surface.
    """

    kind: str = "terminal"
    harness: str | None = None
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
    """Body of ``POST /api/terminal/{session}/retire`` (260707-HFX-L8): the retire authority check.

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
    """Body of ``POST /api/terminal/{session}/rename`` (260707-HFX-L8, issue #4): the new display label."""

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
    *,
    catalog: TerminalCatalog,
    host: TerminalHost,
    session_id: str,
    attached_at: str,
    checked_at: datetime,
    liveness_config: TerminalCatalogLivenessConfig,
) -> TerminalSession | None:
    entry = catalog.get(session_id)
    if entry is None or entry.status not in ("running", "landed"):
        return None
    observation = observe_terminal_liveness(
        catalog,
        host,
        entry,
        checked_at=checked_at,
        config=liveness_config,
    )
    if not observation.alive or observation.entry.status not in ("running", "landed"):
        return None
    entry = observation.entry
    session = host.attach(
        session_id,
        cwd=entry.cwd,
        command=entry.command,
        lifecycle_id=entry.lifecycle_id,
        name=entry.tmux_name,
        suspend_unsafe=entry.kind == "harness",
    )
    catalog.mark_attached(session_id, attached_at)
    return session


def create_app(
    config: McpRuntimeConfig,
    *,
    interval: float = 1.0,
    now: Callable[[], datetime] | None = None,
    before_tick: Callable[[datetime], object] | None = None,
    refresh_provider_state: bool | None = None,
    terminal_host: TerminalHost | None = None,
    terminal_catalog: TerminalCatalog | None = None,
    terminal_paster: TerminalPaster | None = None,
) -> FastAPI:
    """Build the dashboard app bound to one shared projector for ``config``.

    ``now`` / ``before_tick`` default to live behaviour; sim wires a replay clock + feeder.
    ``terminal_host`` defaults to a fresh :class:`TerminalHost` (the Mode B2 terminal backend);
    tests inject a fake to drive the WebSocket bridge without a real PTY.
    """
    if refresh_provider_state is None:
        refresh_provider_state = before_tick is None
    projector = Projector(
        config,
        interval=interval,
        now=now,
        before_tick=before_tick,
        provider_refresher=ProviderStateRefresher() if refresh_provider_state else None,
    )
    host = terminal_host if terminal_host is not None else TerminalHost()
    catalog = terminal_catalog or TerminalCatalog(terminal_catalog_path(config.coordination_root))
    paster = terminal_paster if terminal_paster is not None else TerminalPaster()
    liveness_clock = now or utc_now
    liveness_config = TerminalCatalogLivenessConfig()
    liveness_sweeper = TerminalCatalogLivenessSweeper(
        catalog,
        host,
        now=now,
        config=liveness_config,
        on_turn_state_change=lambda observation: log_turn_state_change_event(config, observation.entry),
    )
    # Resolved ONCE at boot (260703-L15): the stamp that makes a stale serving process visible.
    build = resolve_serving_build()

    # Containment R4 (260707-HFX-L1): the serving daemon samples labeled provider
    # containers on its own cadence (decoupled from the 1s projection tick) into
    # the central metrics store — the feed for provider_status, the statistics
    # board, and the degradation protocol (260707-HFX-L7). Read-only + dockerless-safe.
    metrics_store = ProviderMetricsStore(config.coordination_root)

    async def metrics_loop() -> None:
        while True:
            try:
                snapshot = await asyncio.to_thread(
                    sample_provider_containers, cwd=config.coordination_root
                )
                await asyncio.to_thread(metrics_store.record, snapshot)
                await asyncio.to_thread(evaluate_provider_degradation, config)
                # F5: reclaim the append-only metrics log (O(1) stat unless past its byte budget).
                await asyncio.to_thread(metrics_store.compact)
            except Exception:
                logger.exception("provider metrics sample failed; retrying next interval")
            await asyncio.sleep(DEFAULT_SAMPLE_INTERVAL_SECONDS)

    # 260707-HFX2-L2 R1: the deterministic supervisor sweep -- its own decoupled cadence
    # (default ~10s, settings-controlled), zero tokens, pure code. "The model is never the
    # polling layer": every predicate reads TerminalCatalog/OperatorInboxStore/
    # ExpectationRowStore/the nudge log DIRECTLY (R3), never the projection.
    supervisor_heartbeat_store = SupervisorHeartbeatStore(observer_root(config))

    def _supervisor_context() -> SupervisorContext:
        settings = load_agentic_settings(config.coordination_root)
        root = observer_root(config)
        return SupervisorContext(
            catalog=catalog,
            host=host,
            paster=paster,
            inbox_store=OperatorInboxStore(root),
            expectation_store=ExpectationRowStore(root),
            nudge_store=OrchestrationNudgeStore(root),
            signal_cooldown_store=SupervisorSignalCooldownStore(root),
            event_store=EventStore(root),
            heartbeat_store=supervisor_heartbeat_store,
            coordination_root=config.coordination_root,
            stale_seat_seconds=max(settings.supervisor.interval_seconds * 4, 60.0),
            redeliver_rate_limit_seconds=settings.supervisor.redeliver_rate_limit_seconds,
            signal_cooldown_seconds=settings.supervisor.signal_cooldown_seconds,
            escalation_sla_seconds=settings.escalation.sla_seconds,
            escalation_rung_seconds=settings.escalation.rung_seconds,
            respawn_after_rung=settings.escalation.respawn_after_rung,
            redeliver_budget=settings.supervisor.redeliver_budget,
            escalation_budget=settings.supervisor.escalation_budget,
        )

    async def supervisor_loop() -> None:
        while True:
            settings = load_agentic_settings(config.coordination_root)
            if not settings.supervisor.enabled:
                await asyncio.sleep(settings.supervisor.interval_seconds)
                continue
            try:
                ctx = _supervisor_context()
                await asyncio.to_thread(run_supervisor_sweep, ctx, now=(now or utc_now)())
            except Exception:
                logger.exception("supervisor sweep failed; retrying next interval")
            await asyncio.sleep(settings.supervisor.interval_seconds)

    async def workspace_river_compaction_loop() -> None:
        while True:
            await asyncio.sleep(WORKSPACE_EVENT_COMPACT_INTERVAL_SECONDS)
            try:
                await asyncio.to_thread(
                    compact_workspace_river, observer_root(config), now=(now or utc_now)()
                )
            except Exception:
                logger.exception("workspace event-river compaction failed; retrying next interval")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # F3/CS-6 D3: compact once before accepting clients, then keep compacting on a slow live
        # cadence. Workspace cursors are virtual (base offset + physical offset), and append/compact/read
        # share a cross-process lock, so this is cursor-safe while MCP and serving processes both write.
        await asyncio.to_thread(
            compact_workspace_river, observer_root(config), now=(now or utc_now)()
        )
        await projector.prime()
        task = asyncio.create_task(projector.run())
        metrics_task = asyncio.create_task(metrics_loop())
        supervisor_task = asyncio.create_task(supervisor_loop())
        river_compaction_task = asyncio.create_task(workspace_river_compaction_loop())
        try:
            yield
        finally:
            river_compaction_task.cancel()
            supervisor_task.cancel()
            metrics_task.cancel()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await river_compaction_task
            with contextlib.suppress(asyncio.CancelledError):
                await supervisor_task
            with contextlib.suppress(asyncio.CancelledError):
                await metrics_task
            with contextlib.suppress(asyncio.CancelledError):
                await task
            host.shutdown()

    app = FastAPI(title="Agents Remember dashboard", lifespan=lifespan)

    def _supervisor_heartbeat_payload() -> dict[str, Any]:
        # 260707-HFX2-L2 R5: the tick age at RESPONSE time (not the ETag-gated content revision --
        # a heartbeat is deliberately volatile, the same "ages excluded" posture delta.py already
        # applies to other live ages, so it never busts the projection's change-gate revision).
        settings = load_agentic_settings(config.coordination_root)
        moment = liveness_clock()
        heartbeat = supervisor_heartbeat_store.read()
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

    @app.get("/api/state")
    def api_state(
        if_none_match: Annotated[str | None, Header()] = None,
    ) -> Response:
        # The change gate (260703-L15): the ETag is the projector's content revision, which only
        # advances when the stable projection form changes (volatile ages excluded -- delta.py).
        # An If-None-Match poll of an unchanged projection therefore costs a header exchange
        # instead of a ~780 KB dump+parse; when content DID change, the full fresh dump serves
        # exactly as before. `Cache-Control: no-cache` keeps any cache honest (always revalidate).
        seq, snapshot = projector.current()
        if snapshot is None:
            raise HTTPException(status_code=503, detail="projection not ready")
        revision = projector.revision(seq)
        etag = f'W/"{revision}"'
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if _if_none_match_matches(if_none_match, revision):
            return Response(status_code=304, headers=headers)
        body = snapshot.model_dump(by_alias=True, exclude_none=True)
        body["servingBuild"] = build.payload()
        body["supervisorHeartbeat"] = _supervisor_heartbeat_payload()
        return JSONResponse(content=body, headers=headers)

    @app.get("/api/task-document")
    def api_task_document(path: Annotated[str, Query()]) -> JSONResponse:
        _, snapshot = projector.current()
        if snapshot is None:
            raise HTTPException(status_code=503, detail="projection not ready")
        doc = read_task_document_body(
            config.coordination_root,
            doc_path=path,
            enclosures=snapshot.enclosures,
            now=(now or utc_now)(),
        )
        if doc is None:
            raise HTTPException(status_code=404, detail="task document not found")
        return JSONResponse(content=doc.model_dump(by_alias=True, exclude_none=True))

    @app.get("/api/stream", response_class=EventSourceResponse)
    async def api_stream() -> AsyncIterator[ServerSentEvent]:
        async for event in stream_events(
            projector, build=build, supervisor_heartbeat=_supervisor_heartbeat_payload()
        ):
            yield event

    @app.get("/api/events", response_class=EventSourceResponse)
    async def api_events(
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> AsyncIterator[ServerSentEvent]:
        async for event in stream_raw_events(
            config, last_event_id=last_event_id, interval=interval
        ):
            yield event

    @app.post("/api/actions/{action}")
    def api_action(action: str, request: ActionRequest) -> Response:
        _, snapshot = projector.current()
        if snapshot is None:
            raise HTTPException(status_code=503, detail="projection not ready")
        outcome = evaluate_action(
            snapshot,
            action,
            request.target,
            actor=request.actor,
            now=now_iso(),
            gate_id=request.gateId,
            note=request.note,
            item_id=request.itemId,
            kind=request.kind,
        )
        if outcome.dismissal is not None:
            # Leaf-28 S5.2: attention dismissals are current acknowledgements, not
            # history. A gate-open item is consumed by deleting/cancelling the gate
            # itself, so it does not need an acknowledgement row after the source is gone.
            intent = outcome.dismissal
            gate: dict[str, Any] | None = None
            if intent.kind == "gate-open" and intent.gate_id is not None:
                with contextlib.suppress(KeyError):
                    gate = gate_decide_payload(
                        config,
                        gate_id=intent.gate_id,
                        lifecycle_id=intent.lifecycle_id,
                        decision="cancel",
                        decided_by="developer",
                        decided_via="dashboard",
                        note=intent.note or "Dismissed from attention queue.",
                    )
            elif intent.lifecycle_id is not None or intent.kind == "actionable-drift":
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
        if outcome.gate_decision is not None:
            # The one durable side effect: record the operator's gate decision as
            # developer-attributed -- un-forgeable vs. the agent's model-attributed
            # path, and what server-side closeout enforcement (slice 6b) consumes.
            try:
                if outcome.gate_decision.lifecycle_id is None:
                    if outcome.gate_decision.gate_id is None:
                        return JSONResponse(
                            content={
                                "status": "missing-gate-id",
                                "detail": "gate-id-only decisions require gateId",
                            },
                            status_code=400,
                        )
                    gate = gate_decide_payload(
                        config,
                        gate_id=outcome.gate_decision.gate_id,
                        lifecycle_id=None,
                        decision=outcome.gate_decision.decision,
                        decided_by="developer",
                        decided_via="dashboard",
                        note=outcome.gate_decision.note,
                    )
                else:
                    gate = gate_decide_for_lifecycle(
                        config,
                        lifecycle_id=outcome.gate_decision.lifecycle_id,
                        decision=outcome.gate_decision.decision,
                        decided_by="developer",
                        decided_via="dashboard",
                        expected_gate_id=outcome.gate_decision.gate_id,
                        note=outcome.gate_decision.note,
                    )
            except KeyError as exc:
                status = "stale-gate" if outcome.gate_decision.gate_id else "no-open-gate"
                return JSONResponse(
                    content={
                        "status": status,
                        "detail": str(exc),
                        "target": request.target,
                    },
                    status_code=409,
                )
            return JSONResponse(
                content={**outcome.body, "gate": gate}, status_code=outcome.status_code
            )
        return JSONResponse(content=outcome.body, status_code=outcome.status_code)

    @app.post("/api/operator-inbox")
    def api_operator_inbox(request: OperatorInboxPostRequest) -> Response:
        # Task 10 external-chat path: a dashboard response with no hosted session is written to the
        # pull-based operator inbox. External agents read it through the MCP operator_inbox_poll /
        # operator_inbox_consume tools; this endpoint only owns the developer/dashboard write side.
        try:
            payload = operator_inbox_post_payload(
                config,
                lifecycle_id=request.lifecycle_id,
                agent_id=request.agent_id,
                gate_id=request.gate_id,
                sender_agent_id=request.sender_agent_id,
                sender_role=request.sender_role,
                recipient_role=request.recipient_role,
                message_kind=request.message_kind,
                artifact_path=request.artifact_path,
                deliver_to_hosted=request.deliver_to_hosted,
                terminal_catalog=catalog,
                terminal_host=host,
                terminal_paster=paster,
                ask=request.ask,
                response=request.response,
                created_by="developer",
                created_via="dashboard",
            )
        except ValueError as exc:
            return JSONResponse(
                content={"status": "bad-address", "detail": str(exc)}, status_code=400
            )
        return JSONResponse(content=payload, status_code=200)

    @app.post("/api/operator-inbox/{entry_id}/dismiss")
    def api_operator_inbox_dismiss(entry_id: str) -> Response:
        removed = OperatorInboxStore(observer_root(config)).delete(entry_id)
        if not removed:
            return JSONResponse(
                content={"status": "not-found", "entryId": entry_id}, status_code=404
            )
        return JSONResponse(content={"status": "dismissed", "entryId": entry_id}, status_code=200)

    @app.websocket("/api/terminal/{session}")
    async def api_terminal(websocket: WebSocket, session: str) -> None:
        # Mode B2 (slice 6d-2): bridge a live terminal-host session to xterm.js. Attach-only
        # -- the session is opened out-of-band (the lifecycle-correlated launch is a later
        # slice); an unknown id is refused with a private close code. Same localhost posture
        # as the rest of the app (the host spawns a fixed argv, never a wire-supplied command).
        await websocket.accept()
        session_obj = _attach_terminal_session(
            catalog=catalog,
            host=host,
            session_id=session,
            attached_at=now_iso(),
            checked_at=liveness_clock(),
            liveness_config=liveness_config,
        )
        if session_obj is None:
            await websocket.close(code=4404)
            return
        try:
            await _bridge_terminal(websocket, host, session_obj)
        finally:
            if not session_obj.is_alive:
                catalog.mark_exited(session)
            else:
                host.close_session(session_obj)
            with contextlib.suppress(RuntimeError):
                await websocket.close()

    @app.get("/api/terminal/sessions")
    def api_terminal_sessions() -> dict[str, Any]:
        return {"sessions": [_catalog_payload(entry) for entry in liveness_sweeper.refresh()]}

    @app.get("/api/harnesses")
    def api_harnesses() -> dict[str, Any]:
        # The supported TUI harnesses + whether each is installed here (slice 6e-2b). The dashboard
        # renders a launch button per *detected* harness; the argv stays server-side (open via POST).
        # 260703-L16: the EFFECTIVE registry (builtin merged with orchestration.harnesses in the
        # GLOBAL agentic settings, per-use) -- settings-defined harnesses get buttons too. Repo-local
        # overrides are leaf-scoped dispatch material (the MCP spawn tool), not workspace buttons.
        registry = load_agentic_settings(config.coordination_root).harnesses
        return {
            "harnesses": [
                {"id": h.id, "name": h.name, "detected": h.detected}
                for h in detect_harnesses(registry=registry)
            ]
        }

    @app.post("/api/terminal/landed-cleanup")
    def api_terminal_landed_cleanup(request: TerminalLandedCleanupRequest) -> Response:
        closed: list[str] = []
        skipped: list[dict[str, str]] = []
        closed_entries: list[TerminalCatalogEntry] = []
        for session in request.session_ids:
            entry = catalog.get(session)
            if entry is None:
                skipped.append({"session": session, "reason": "unknown-session"})
                continue
            if entry.status != "landed":
                skipped.append({"session": session, "reason": f"status:{entry.status}"})
                continue
            updated = retire_entry(
                catalog,
                host,
                entry,
                at=now_iso(),
                by_session=None,
                reason="landed group cleanup",
                edge="landed-group-cleanup",
            )
            if updated is None:
                skipped.append({"session": session, "reason": "unknown-session"})
                continue
            closed.append(session)
            closed_entries.append(updated)
        for entry in closed_entries:
            log_retire_event(config, entry)
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

    @app.post("/api/terminal/{session}")
    def api_terminal_open(session: str, request: TerminalOpenRequest) -> Response:
        # Mode B2 opener (slice 6e-2a; harness kinds 6e-2b): the dashboard *spawns + owns* a
        # session, then the WebSocket above attaches to it. L2 moves the leaf-claim + ensure + upsert
        # composition into the shared `open_terminal_session` so this route and the agent-facing
        # `spawn_agent_session` MCP tool spawn through ONE opener (no parallel spawn path).
        try:
            leaf_key = _resolve_request_leaf_key(config, request.leaf_key)
        except LeafRefResolutionError as exc:
            return _leaf_ref_response(exc, request.leaf_key or "")
        shell = os.environ.get("SHELL") or DEFAULT_SHELL
        result = open_terminal_session(
            catalog=catalog,
            host=host,
            session_id=session,
            kind=request.kind,
            workspace_root=config.workspace_root,
            shell=shell,
            harness=request.harness,
            label=request.label,
            lifecycle_id=request.lifecycle_id,
            leaf_key=leaf_key,
            # 260703-L16: resolve harness ids against the effective GLOBAL registry (builtin merged
            # with orchestration.harnesses) so dashboard launches and MCP dispatches agree on argv.
            # Loaded only for harness-kind opens (review L16R-1): a malformed settings file must
            # fail the launches that USE it, never a plain scratch terminal.
            harnesses=(
                load_agentic_settings(config.coordination_root).harnesses
                if request.kind == "harness" or request.harness
                else None
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
        assert entry is not None  # opened => an upserted row
        return JSONResponse(
            content={
                "session": entry.id,
                "label": entry.label,
                "kind": request.kind,
                "harness": request.harness,
                "lifecycleId": request.lifecycle_id,
                "leafKey": entry.leaf_key,
                "seatRole": entry.binding_role,
                "cwd": str(entry.cwd),
                "tmuxName": entry.tmux_name,
                "status": "running",
            },
            status_code=200,
        )

    @app.post("/api/terminal/{session}/attach-leaf")
    def api_terminal_attach_leaf(session: str, request: TerminalAttachLeafRequest) -> Response:
        # Claim or move one existing session's leaf-role binding (enclosure-free, no respawn).
        try:
            leaf_key = _resolve_request_leaf_key(config, request.leaf_key)
        except LeafRefResolutionError as exc:
            return _leaf_ref_response(exc, request.leaf_key)
        assert leaf_key is not None
        result = assign_terminal_session_to_leaf(
            catalog,
            host,
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

    @app.post("/api/terminal/{session}/paste")
    def api_terminal_paste(session: str, request: TerminalPasteRequest) -> Response:
        # L2 paste seam: deliver a context packet to a hosted session server-side (the mirror of the
        # frontend delivery seam), so a packet can be pushed to a durable tmux session that has no
        # attached browser client. Submitted input is accepted only from the bound harness log.
        entry = catalog.get(session)
        if entry is None or entry.status != "running":
            return JSONResponse(content={"status": "unknown-session"}, status_code=404)
        observation = observe_terminal_liveness(
            catalog,
            host,
            entry,
            checked_at=liveness_clock(),
            config=liveness_config,
        )
        if not observation.alive or observation.entry.status != "running":
            return JSONResponse(content={"status": "unknown-session"}, status_code=404)
        entry = observation.entry
        delivery_id = uuid4().hex
        session_log = HarnessSessionLog(
            harness=entry.harness or "",
            cwd=entry.cwd,
            started_at=datetime.fromisoformat(entry.created_at),
            bound_path=entry.session_log_path,
        )
        outcome = deliver(
            DeliveryRow(kind="message", entry_id=delivery_id, text=request.text, submit=request.submit),
            tmux_name=entry.tmux_name,
            paster=paster,
            harness=entry.harness,
            session_log=session_log,
        )
        if outcome.session_log_path is not None and outcome.bound_entry_id is not None:
            catalog.bind_session_log(
                entry.id,
                entry_id=outcome.bound_entry_id,
                path=outcome.session_log_path,
            )
        delivered = outcome.outcome in ("acked", "landed-unacked")
        content: dict[str, object] = {
            "session": session,
            "entryId": delivery_id,
            "status": "delivered" if delivered else "unconfirmed",
            "delivered": delivered,
            "submitted": outcome.submitted,
        }
        if not delivered or (request.submit and not outcome.submitted):
            content["capture"] = outcome.capture
        return JSONResponse(content=content, status_code=200)

    @app.post("/api/terminal/{session}/terminate")
    def api_terminal_terminate(session: str) -> Response:
        entry = catalog.get(session)
        live = host.get(session)
        if entry is None and live is None:
            return JSONResponse(content={"status": "unknown-session"}, status_code=404)
        host.terminate(session, tmux_name=entry.tmux_name if entry is not None else None)
        terminated_at = now_iso()
        updated = catalog.mark_terminated(session, terminated_at)
        return JSONResponse(
            content={
                "session": session,
                "status": "terminated",
                "terminatedAt": terminated_at,
                **({"tmuxName": updated.tmux_name} if updated is not None else {}),
            },
            status_code=200,
        )

    @app.post("/api/terminal/{session}/retire")
    def api_terminal_retire(session: str, request: TerminalRetireRequest) -> Response:
        # 260707-HFX-L8 (issue #12): the server-authoritative retire surface. Never a zombie row --
        # a retire is the SAME terminal mark ``/terminate`` writes, plus retirement provenance
        # (who, why, when, which edge); transcripts are never touched. Authority is enforced here,
        # not trusted from the caller: owner-never-self-retires, a manager retires only its own
        # master's worker/reviewer seats, the orchestrator retires anything.
        target_entry = catalog.get(session)
        if target_entry is None:
            return JSONResponse(content={"status": "unknown-session"}, status_code=404)
        actor_entry = catalog.get(request.actor_session)
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
            check_retire_authority(
                SeatRef(
                    session_id=actor_entry.id,
                    leaf_key=actor_entry.binding_leaf_key,
                    seat_role=actor_entry.binding_role,
                ),
                SeatRef(
                    session_id=target_entry.id,
                    leaf_key=target_entry.binding_leaf_key,
                    seat_role=target_entry.binding_role,
                ),
            )
        except RetirePolicyError as exc:
            return JSONResponse(
                content={"session": session, "status": "retire-refused", "detail": str(exc)},
                status_code=403,
            )
        updated = retire_entry(
            catalog,
            host,
            target_entry,
            at=now_iso(),
            by_session=request.actor_session,
            reason=request.reason,
            edge="manual",
        )
        assert updated is not None  # the entry existed above; no concurrent delete path removes rows
        log_retire_event(config, updated)
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

    @app.post("/api/terminal/{session}/rename")
    def api_terminal_rename(session: str, request: TerminalRenameRequest) -> Response:
        # 260707-HFX-L8 (issue #4): post-spawn identity rename. Identity text ONLY -- spawn_role
        # (the L6-immutable seat role) is never touched by a rename.
        entry = catalog.get(session)
        if entry is None or entry.status == "terminated":
            return JSONResponse(content={"status": "unknown-session"}, status_code=404)
        updated = catalog.set_label(session, request.label)
        assert updated is not None
        log_rename_event(config, updated)
        return JSONResponse(
            content={
                "session": session,
                "status": "renamed",
                "label": updated.label,
                "spawnedLabel": updated.spawned_label,
            },
            status_code=200,
        )

    @app.post("/api/terminal/{session}/image")
    async def api_terminal_image(
        session: str, request: Request, file: Annotated[UploadFile, File()]
    ) -> Response:
        # Slice 6f images: the terminal channel is text-only, so a pasted screenshot is carried by
        # saving it under the session's own cwd and injecting the on-disk path (Claude Code auto-attaches
        # an image path before the model runs). Same localhost posture as the rest of serving/; writes
        # ONLY under the session cwd, with a uuid basename (no traversal) and validated type (extension +
        # magic bytes) + size. Returns the absolute path the composer injects over {type:stdin}.
        # SECURITY NOTE: like the rest of serving/ this is unauthenticated and 127.0.0.1-bound, but unlike
        # the JSON POSTs it is multipart (a CORS "simple request", preflight-free). The write target is
        # keyed by an unguessable client UUID, so cross-origin/CSRF writes can't target a real session;
        # an Origin/Host allowlist for all write routes is folded into the documented remote-auth story.
        session_obj = host.get(session)
        entry = catalog.get(session)
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
            return JSONResponse(
                content={"status": "bad-type"}, status_code=400
            )  # empty / not an image
        dest = cwd / ".dashboard-pastes" / f"{uuid4().hex}.{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(
            body
        )  # flush before the path is injected -- the harness validates existence
        return JSONResponse(content={"path": str(dest.resolve())}, status_code=200)

    register_files_routes(app, config)
    register_changeset_routes(app, config)
    register_notes_routes(app, config)
    mount_static(app)
    return app
