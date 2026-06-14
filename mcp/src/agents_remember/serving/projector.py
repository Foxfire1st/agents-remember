"""The shared projection projector: one tick loop fanned out to every client.

A single background task ticks :func:`project_and_write` on an interval (writing the
atomic ``latest-state.json`` as a side benefit), diffs each new projection against the
last, and broadcasts the per-entity deltas to all subscribed SSE connections. N clients
therefore cost one re-projection per tick -- what makes the single multiplexed
EventSource (note 09) scale. Reads go only through ``McpRuntimeConfig`` (North-Star #5).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from agents_remember.observer.projection_store import project_and_write
from agents_remember.serving.delta import DeltaEvent, diff_projection

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig
    from agents_remember.observer.projection import WorkspaceProjection

logger = logging.getLogger(__name__)

_Item = tuple[int, DeltaEvent]


class Projector:
    """Owns the latest projection, a monotonic sequence, and the subscriber fan-out."""

    def __init__(self, config: McpRuntimeConfig, *, interval: float = 1.0) -> None:
        self._config = config
        self._interval = interval
        self._latest: WorkspaceProjection | None = None
        self._seq = 0
        self._subscribers: set[asyncio.Queue[_Item]] = set()

    async def prime(self) -> None:
        """Compute the first projection so the first client gets an immediate snapshot.

        Resilient: a failure here is logged and left for the tick loop to retry, so the
        server still starts and serves ``503`` from ``/api/state`` until a tick succeeds.
        """
        try:
            self._latest = await asyncio.to_thread(project_and_write, self._config)
        except Exception:
            logger.exception("initial projection failed; the tick loop will retry")

    async def run(self) -> None:
        """Tick forever: re-project, diff, broadcast. One bad tick never kills the loop."""
        while True:
            await asyncio.sleep(self._interval)
            try:
                current = await asyncio.to_thread(project_and_write, self._config)
            except Exception:
                logger.exception("projection tick failed; retrying next interval")
                continue
            for delta in diff_projection(self._latest, current):
                self._seq += 1
                self._broadcast((self._seq, delta))
            self._latest = current

    def current(self) -> tuple[int, WorkspaceProjection | None]:
        """The latest (sequence, projection) for a new connection's snapshot."""
        return self._seq, self._latest

    def _broadcast(self, item: _Item) -> None:
        for queue in self._subscribers:
            queue.put_nowait(item)

    async def subscribe(self) -> AsyncGenerator[_Item]:
        """Yield ``(sequence, delta)`` items until the consumer stops iterating."""
        queue: asyncio.Queue[_Item] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)
