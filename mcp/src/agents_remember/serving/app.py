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
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from agents_remember.mcp.tools.gates import gate_decide_for_lifecycle
from agents_remember.observer.events import now_iso
from agents_remember.serving.actions import ActionRequest, evaluate_action
from agents_remember.serving.events import stream_raw_events
from agents_remember.serving.projector import Projector
from agents_remember.serving.static import mount_static

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


def create_app(
    config: McpRuntimeConfig,
    *,
    interval: float = 1.0,
    now: Callable[[], datetime] | None = None,
    before_tick: Callable[[datetime], object] | None = None,
) -> FastAPI:
    """Build the dashboard app bound to one shared projector for ``config``.

    ``now`` / ``before_tick`` default to live behaviour; sim wires a replay clock + feeder.
    """
    projector = Projector(config, interval=interval, now=now, before_tick=before_tick)

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

    mount_static(app)
    return app
