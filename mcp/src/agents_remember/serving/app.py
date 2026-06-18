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
import os
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from agents_remember.mcp.tools.gates import gate_decide_for_lifecycle
from agents_remember.observer.events import now_iso
from agents_remember.serving.actions import ActionRequest, evaluate_action
from agents_remember.serving.events import stream_raw_events
from agents_remember.serving.projector import Projector
from agents_remember.serving.static import mount_static
from agents_remember.serving.terminal import TerminalHost, TerminalSession

if TYPE_CHECKING:
    from datetime import datetime

    from agents_remember.mcp.config import McpRuntimeConfig


def _encode(data: BaseModel | dict[str, Any]) -> Any:
    """JSON-ready payload: projection nodes dumped by alias (camelCase), markers as-is."""
    if isinstance(data, BaseModel):
        return data.model_dump(by_alias=True, exclude_none=True)
    return data


async def stream_events(projector: Projector) -> AsyncGenerator[ServerSentEvent]:
    """The SSE event sequence for one connection: snapshot, then per-entity deltas.

    Module-level (not a route closure) so it is unit-testable without an HTTP client.
    """
    seq, snapshot = projector.current()
    if snapshot is not None:
        yield ServerSentEvent(
            data=_encode(snapshot), event="snapshot", id=str(seq), retry=2000
        )
    async for seq, delta in projector.subscribe():
        yield ServerSentEvent(
            data=_encode(delta.data), event=delta.event, id=str(seq), retry=2000
        )


_TERMINAL_EXIT_FRAME = json.dumps({"type": "exit"})
"""The text frame sent to the browser once the PTY child exits (then the socket closes)."""


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


async def _terminal_to_socket(
    websocket: WebSocket, outbound: asyncio.Queue[bytes | None]
) -> None:
    """Forward queued PTY output frames to the browser until the EOF sentinel (``None``)."""
    while True:
        chunk = await outbound.get()
        if chunk is None:
            break
        await websocket.send_bytes(chunk)
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await websocket.send_text(_TERMINAL_EXIT_FRAME)


async def _socket_to_terminal(
    websocket: WebSocket, host: TerminalHost, session: str
) -> None:
    """Forward browser frames (``stdin`` / ``resize``) to the PTY until the socket closes."""
    with contextlib.suppress(WebSocketDisconnect):
        async for text in websocket.iter_text():
            _apply_terminal_input(host, session, text)


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

    def _on_readable() -> None:
        data = host.read_nonblocking(session.sid)
        if data:
            outbound.put_nowait(data)
        elif not session.is_alive:
            loop.remove_reader(fd)
            outbound.put_nowait(None)

    loop.add_reader(fd, _on_readable)
    out_task = asyncio.create_task(_terminal_to_socket(websocket, outbound))
    in_task = asyncio.create_task(_socket_to_terminal(websocket, host, session.sid))
    try:
        await asyncio.wait({out_task, in_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        loop.remove_reader(fd)
        out_task.cancel()
        in_task.cancel()
        await asyncio.gather(out_task, in_task, return_exceptions=True)


DEFAULT_SHELL = "/bin/bash"
"""Fallback when the dashboard process has no ``$SHELL`` (the generic-terminal command)."""


class TerminalOpenRequest(BaseModel):
    """Body of ``POST /api/terminal/{session}``: which kind of session to spawn.

    Slice 6e-2a supports ``kind="terminal"`` (a shell). The harness registry (``kind="harness"``)
    lands in 6e-2b. The server resolves the command from the kind -- only a kind id is on the
    wire, never an argv -- so there is no command-injection surface.
    """

    kind: str = "terminal"
    lifecycle_id: str | None = None


def resolve_terminal_launch(
    kind: str, *, workspace_root: Path, shell: str
) -> tuple[Path, list[str]]:
    """Resolve a launch ``kind`` to ``(cwd, argv)`` -- the server owns the command. Pure.

    ``terminal`` spawns ``shell`` at the workspace root (the dashboard-owned scratch terminal,
    slice 6e-2a). Unknown kinds raise ``ValueError``; the harness kinds land in 6e-2b.
    """
    if kind == "terminal":
        return workspace_root, [shell]
    raise ValueError(f"unknown terminal kind: {kind!r}")


def create_app(
    config: McpRuntimeConfig,
    *,
    interval: float = 1.0,
    now: Callable[[], datetime] | None = None,
    before_tick: Callable[[datetime], object] | None = None,
    terminal_host: TerminalHost | None = None,
) -> FastAPI:
    """Build the dashboard app bound to one shared projector for ``config``.

    ``now`` / ``before_tick`` default to live behaviour; sim wires a replay clock + feeder.
    ``terminal_host`` defaults to a fresh :class:`TerminalHost` (the Mode B2 terminal backend);
    tests inject a fake to drive the WebSocket bridge without a real PTY.
    """
    projector = Projector(config, interval=interval, now=now, before_tick=before_tick)
    host = terminal_host if terminal_host is not None else TerminalHost()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await projector.prime()
        task = asyncio.create_task(projector.run())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            host.shutdown()

    app = FastAPI(title="Agents Remember dashboard", lifespan=lifespan)

    @app.get("/api/state")
    def api_state() -> dict[str, Any]:
        _, snapshot = projector.current()
        if snapshot is None:
            raise HTTPException(status_code=503, detail="projection not ready")
        return snapshot.model_dump(by_alias=True, exclude_none=True)

    @app.get("/api/stream", response_class=EventSourceResponse)
    async def api_stream() -> AsyncIterator[ServerSentEvent]:
        async for event in stream_events(projector):
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
            snapshot, action, request.target, actor=request.actor, now=now_iso()
        )
        if outcome.gate_decision is not None:
            # The one durable side effect: record the operator's gate decision as
            # developer-attributed -- un-forgeable vs. the agent's model-attributed
            # path, and what server-side closeout enforcement (slice 6b) consumes.
            try:
                gate = gate_decide_for_lifecycle(
                    config,
                    lifecycle_id=outcome.gate_decision.lifecycle_id,
                    decision=outcome.gate_decision.decision,
                    decided_by="developer",
                    decided_via="dashboard",
                )
            except KeyError as exc:
                return JSONResponse(
                    content={
                        "status": "no-open-gate",
                        "detail": str(exc),
                        "target": request.target,
                    },
                    status_code=409,
                )
            return JSONResponse(
                content={**outcome.body, "gate": gate}, status_code=outcome.status_code
            )
        return JSONResponse(content=outcome.body, status_code=outcome.status_code)

    @app.websocket("/api/terminal/{session}")
    async def api_terminal(websocket: WebSocket, session: str) -> None:
        # Mode B2 (slice 6d-2): bridge a live terminal-host session to xterm.js. Attach-only
        # -- the session is opened out-of-band (the lifecycle-correlated launch is a later
        # slice); an unknown id is refused with a private close code. Same localhost posture
        # as the rest of the app (the host spawns a fixed argv, never a wire-supplied command).
        await websocket.accept()
        session_obj = host.get(session)
        if session_obj is None:
            await websocket.close(code=4404)
            return
        try:
            await _bridge_terminal(websocket, host, session_obj)
        finally:
            with contextlib.suppress(RuntimeError):
                await websocket.close()

    @app.post("/api/terminal/{session}")
    def api_terminal_open(session: str, request: TerminalOpenRequest) -> Response:
        # Mode B2 opener (slice 6e-2a): the dashboard *spawns + owns* a session, then the
        # WebSocket above attaches to it. The command is server-resolved from the kind (never
        # wire-supplied) and spawned as the dashboard's OS user/env at the workspace root.
        shell = os.environ.get("SHELL") or DEFAULT_SHELL
        try:
            cwd, command = resolve_terminal_launch(
                request.kind, workspace_root=config.workspace_root, shell=shell
            )
        except ValueError as exc:
            return JSONResponse(
                content={"status": "bad-kind", "detail": str(exc)}, status_code=400
            )
        opened = host.open(
            session, cwd=cwd, command=command, lifecycle_id=request.lifecycle_id
        )
        return JSONResponse(
            content={"session": opened.sid, "kind": request.kind, "cwd": str(opened.cwd)},
            status_code=200,
        )

    mount_static(app)
    return app
