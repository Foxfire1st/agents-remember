from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.sse import ServerSentEvent
from pydantic import BaseModel, Field

from agents_remember.models.application_requests import AgentRole, InboxMessageKind
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
)
from agents_remember.observer import observer_root
from agents_remember.serving.agent_notifier_heartbeat import (
    AgentNotifierHeartbeatPayload,
    AgentNotifierHeartbeatStore,
)
from agents_remember.serving.build_info import ServingBuild
from agents_remember.serving.harness_capability_catalog import HarnessCapabilityCatalog
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.ports import TerminalCatalogPort
from agents_remember.serving.projector import ProjectionReplay, Projector
from agents_remember.serving.served_state import served_state_tail
from agents_remember.serving.terminal import TerminalHost, TerminalSessionSpec
from agents_remember.serving.terminal_liveness import (
    LivenessProbe,
    TerminalCatalogLivenessConfig,
    TerminalCatalogLivenessSweeper,
    observe_terminal_liveness,
)
from agents_remember.serving.terminal_paste import TerminalPaster
from agents_remember.serving.terminal_pty import TerminalSession

# The serving app's shared logger, named after the public module so existing
# ``assertLogs("agents_remember.serving.app", ...)`` tests keep matching.
logger = logging.getLogger("agents_remember.serving.app")

if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import (
        McpRuntimeConfig,
    )


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_app_common.py:50).
def _encode(data: BaseModel | dict[str, Any]) -> Any:  # pragma: no cover
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
    tail is NOT cached: ``servingBuild``/``agentNotifierHeartbeat`` are injected per serve, so
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
    agent_notifier_heartbeat: AgentNotifierHeartbeatPayload | None = None,
) -> AsyncGenerator[ServerSentEvent]:
    """The SSE event sequence for one atomic projector subscription.

    Module-level (not a route closure) so it is unit-testable without an HTTP client.
    ``build`` (the boot-time serving stamp) rides the snapshot as
    ``servingBuild`` so the cockpit can render which process/commit is answering.
    ``agent_notifier_heartbeat`` rides as ``agentNotifierHeartbeat`` (with the legacy
    ``supervisorHeartbeat`` alias during the rename window) -- the tick age
    at connect time, so a stale agent-notifier is visible in the dashboard header at a glance.

    The tail rides the ``snapshot`` ONLY: a ``delta`` is one projection node, not a state
    body, so there is nothing there for a whole-workspace stamp to be a field of. That
    asymmetry is why the snapshot is a :class:`ServedWorkspaceProjection` and a delta is
    just an encoded node.
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
                payload.update(served_state_tail(build=build, heartbeat=agent_notifier_heartbeat))
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


# 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_app_common.py:173).
def _apply_terminal_input(session: TerminalSession, text: str) -> None:  # pragma: no cover
    """Apply one client text frame to this connection's PTY client: a ``stdin`` write or a ``resize``.

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
            with contextlib.suppress(OSError):
                session.write(data.encode())
    elif message.get("type") == "resize":
        cols, rows = message.get("cols"), message.get("rows")
        if isinstance(cols, int) and isinstance(rows, int):
            with contextlib.suppress(OSError):
                session.resize(cols=cols, rows=rows)


async def _terminal_to_socket(websocket: WebSocket, outbound: asyncio.Queue[bytes | None]) -> None:
    """Forward queued PTY output frames to the browser until the EOF sentinel (``None``)."""
    while True:
        chunk = await outbound.get()
        if chunk is None:
            break
        await websocket.send_bytes(chunk)
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await websocket.send_text(_TERMINAL_EXIT_FRAME)


async def _socket_to_terminal(websocket: WebSocket, session: TerminalSession) -> None:
    """Forward browser frames (``stdin`` / ``resize``) to the PTY until the socket closes."""
    with contextlib.suppress(WebSocketDisconnect):
        async for text in websocket.iter_text():
            _apply_terminal_input(session, text)


async def _bridge_terminal(websocket: WebSocket, session: TerminalSession) -> None:
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

    # 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/_app_common.py:238).
    def _on_readable() -> None:  # pragma: no cover
        data = session.read_nonblocking()
        if data:
            outbound.put_nowait(data)
        elif not session.is_alive:
            _remove_reader()
            outbound.put_nowait(None)

    loop.add_reader(fd, _on_readable)
    out_task = asyncio.create_task(_terminal_to_socket(websocket, outbound))
    in_task = asyncio.create_task(_socket_to_terminal(websocket, session))
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
    task_document_ref: TaskDocumentRef | None = Field(default=None, alias="taskDocumentRef")
    role: str | None = None


class TerminalAttachTaskRequest(BaseModel):
    """Trusted dashboard administration: bind an existing session to a document+role seat."""

    task_document_ref: TaskDocumentRef = Field(alias="taskDocumentRef")
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
    terminal_catalog: TerminalCatalogPort | None = None
    terminal_paster: TerminalPaster | None = None
    harness_capability_catalog: HarnessCapabilityCatalog | None = None


INFERRED_LIVE_INPUTS = LiveProjectionInputs()


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
    catalog: TerminalCatalogPort
    paster: TerminalPaster
    liveness_clock: Callable[[], datetime]
    liveness_config: TerminalCatalogLivenessConfig
    liveness_sweeper: TerminalCatalogLivenessSweeper
    build: ServingBuild
    heartbeat_store: AgentNotifierHeartbeatStore
    interval: float

    @property
    def observer_root(self) -> Path:
        """The observer store root every durable control-plane store is opened under."""

        return observer_root(self.config)
