"""The shared projection projector: one tick loop fanned out to every client.

A single background task ticks :func:`project_and_write` on an interval (writing the
atomic ``latest-state.json`` as a side benefit), diffs each new projection against the
last, and broadcasts the per-entity deltas to all subscribed SSE connections. N clients
therefore cost one re-projection per tick -- what makes the single multiplexed
EventSource (note 09) scale. Reads go only through ``McpRuntimeConfig`` (North-Star #5).

Two seams keep this generic across live and sim (slice 4b): ``now`` is the clock the
tick projects at (a replay clock under sim, wall-clock UTC live), and ``before_tick`` is
an optional hook run with that same moment just before each projection -- sim uses it to
feed the next due fixture events into the sim store so the projection evolves over replay
time. Both default to the live no-op behaviour, so the live path is unchanged.

The change gate (260703-L15): the diff compares *stable forms* (volatile age fields
stripped -- ``serving/delta.py``), so ``_seq`` only advances when projection CONTENT
changes. That makes ``revision`` a truthful content fingerprint: ``/api/state`` serves it
as the ETag, and an ``If-None-Match`` poll of an unchanged projection costs a header
exchange, not a ~780 KB dump+parse. The previous tick's stable form is cached here so
each tick pays for one stable dump, and ``(seq, projection)`` publish atomically as one
tuple so a threadpool reader can never pair a new revision with an old snapshot.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from agents_remember.observer.projection_store import project_and_write
from agents_remember.serving.delta import (
    DeltaEvent,
    StableProjectionState,
    diff_projection,
    stable_projection_state,
)

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig
    from agents_remember.observer.projection import WorkspaceProjection
    from agents_remember.observer.projection_store import ProviderStateRefresh

logger = logging.getLogger(__name__)

_Item = tuple[int, DeltaEvent]


def _utcnow() -> datetime:
    """The live clock: wall-clock UTC, re-read every tick."""
    return datetime.now(UTC)


class Projector:
    """Owns the latest projection, a monotonic sequence, and the subscriber fan-out."""

    def __init__(
        self,
        config: McpRuntimeConfig,
        *,
        interval: float = 1.0,
        now: Callable[[], datetime] | None = None,
        before_tick: Callable[[datetime], object] | None = None,
        provider_refresher: ProviderStateRefresh | None = None,
    ) -> None:
        self._config = config
        self._interval = interval
        self._now: Callable[[], datetime] = now or _utcnow
        self._before_tick = before_tick
        self._provider_refresher = provider_refresher
        # (seq, projection) publish as ONE tuple: /api/state runs in a threadpool, so pairing
        # them via two attributes could tear (a bumped seq read against the previous snapshot
        # would hand a poller a stale body under a fresh ETag).
        self._published: tuple[int, WorkspaceProjection | None] = (0, None)
        self._latest_stable: StableProjectionState | None = None
        # Boot nonce: seq restarts at 0 on every process start, so the ETag must not -- a client
        # holding "0" from the previous process would 304 against different content otherwise.
        self._boot_id = uuid4().hex[:12]
        self._subscribers: set[asyncio.Queue[_Item]] = set()

    async def prime(self) -> None:
        """Compute the first projection so the first client gets an immediate snapshot.

        Resilient: a failure here is logged and left for the tick loop to retry, so the
        server still starts and serves ``503`` from ``/api/state`` until a tick succeeds.
        """
        try:
            first = await asyncio.to_thread(self._tick_sync, self._now())
        except Exception:
            logger.exception("initial projection failed; the tick loop will retry")
            return
        self._latest_stable = stable_projection_state(first)
        self._published = (0, first)

    async def run(self) -> None:
        """Tick forever: re-project, diff, broadcast. One bad tick never kills the loop."""
        while True:
            await asyncio.sleep(self._interval)
            try:
                current = await asyncio.to_thread(self._tick_sync, self._now())
            except Exception:
                logger.exception("projection tick failed; retrying next interval")
                continue
            current_stable = stable_projection_state(current)
            seq, previous = self._published
            for delta in diff_projection(
                previous,
                current,
                previous_state=self._latest_stable,
                current_state=current_stable,
            ):
                seq += 1
                self._broadcast((seq, delta))
            self._latest_stable = current_stable
            self._published = (seq, current)

    def _tick_sync(self, moment: datetime) -> WorkspaceProjection:
        """Run the optional pre-tick hook, then project at ``moment`` (off the loop thread)."""
        if self._before_tick is not None:
            self._before_tick(moment)
        return project_and_write(
            self._config,
            now=moment,
            provider_refresher=self._provider_refresher,
        )

    def current(self) -> tuple[int, WorkspaceProjection | None]:
        """The latest (sequence, projection) for a new connection's snapshot."""
        return self._published

    def revision(self, seq: int) -> str:
        """The opaque content fingerprint for ``seq``: boot nonce + content sequence.

        ``seq`` only advances on stable-form changes (the volatile-age-free diff), so this
        is a truthful ``/api/state`` ETag value: same revision => same projection content
        (up to volatile ages and ``generatedAt``, which change every tick by design).
        """
        return f"{self._boot_id}-{seq}"

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
