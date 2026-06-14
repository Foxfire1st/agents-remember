"""The dashboard FastAPI app: one-shot state + the multiplexed SSE stream (slice 04).

Endpoints:

* ``GET /api/state``  -- the current projection once (curl-friendly, no streaming).
* ``GET /api/stream`` -- one EventSource: an ``event: snapshot`` with the full projection
  on connect, then per-entity ``state`` deltas (``lifecycle`` / ``enclosure`` /
  ``provider`` / ``metrics`` / ``analytics`` and their ``*.removed`` markers) fanned out
  from the shared :class:`Projector`. The raw ``event`` channel and the POST action
  skeleton land in slice 4b.

Local-first posture: bind ``127.0.0.1`` only (the CLI default) with no auth in v1. This is
a cockpit for the developer's own machine; exposing it (an SSH tunnel, a reverse proxy)
hands an unauthenticated reader the whole projection and, later, the action surface, so any
multi-user or remote story is a deliberate later design with its own auth gate.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from agents_remember.serving.projector import Projector
from agents_remember.serving.static import mount_static

if TYPE_CHECKING:
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


def create_app(config: McpRuntimeConfig, *, interval: float = 1.0) -> FastAPI:
    """Build the dashboard app bound to one shared projector for ``config``."""
    projector = Projector(config, interval=interval)

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

    mount_static(app)
    return app
