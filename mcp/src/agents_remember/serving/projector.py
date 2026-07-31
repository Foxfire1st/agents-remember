"""The shared projection projector: one tick loop fanned out to every client.

A single background task ticks :func:`project_and_write` (writing the atomic
``latest-state.json`` as a side benefit), diffs each new projection against the last, and
broadcasts the per-entity deltas to all subscribed SSE connections. N clients therefore
cost one re-projection per tick -- what makes the single multiplexed EventSource (note 09)
scale. Reads go only through ``McpRuntimeConfig`` (North-Star #5).

Adaptive waking (260712-PTS-L3): with a ``change_watcher`` the pacemaker is no longer an
unconditional ``sleep(interval)`` -- the loop wakes on debounced input changes (floored to
one projection per ``interval``; ``--interval`` keeps meaning the fast-path cadence floor)
or on a slow ``heartbeat`` when nothing changed, so a quiet daemon idles near zero CPU.
Freshness bounds: change -> SSE delta within debounce + projection time (plus the interval
floor when busy); ``/api/state`` staleness and time-derived field resolution are bounded
by the heartbeat. The live path retains fixed-slot reader-domain snapshots and invalidates
only the domains named by the watcher. Without a watcher (sim replay, existing tests) the
loop keeps the exact fixed-interval full-refresh behaviour, and a failed watcher degrades
back to it loudly (fail-open).

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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from agents_remember.observer.projection_inputs import (
    ProjectionInputState,
    ProjectionRefresh,
)
from agents_remember.observer.projection_store import ProjectionTickState, project_and_write
from agents_remember.serving.cadence import DEFAULT_PROJECTION_CADENCE, ProjectionCadence
from agents_remember.serving.change_watcher import DEFAULT_HEARTBEAT_SECONDS, ChangePacer
from agents_remember.serving.delta import (
    DeltaEvent,
    StableProjectionState,
    diff_projection,
    stable_projection_state,
)

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig
    from agents_remember.observer.landing_state import LandingStateRefresh
    from agents_remember.observer.projection import WorkspaceProjection
    from agents_remember.observer.projection_store import ProviderStateRefresh
    from agents_remember.serving.change_watcher import ChangeWatch

logger = logging.getLogger(__name__)

_Item = tuple[int, DeltaEvent]


def _utcnow() -> datetime:
    """The live clock: wall-clock UTC, re-read every tick."""
    return datetime.now(UTC)


async def _shutdown_task(task: asyncio.Task[None] | None, label: str) -> None:
    """Cancel-and-await one background task at projector shutdown.

    Both background tasks log their own cycle failures. This guard is still required for
    an already-dead task: its stored exception must not replace the Projector cancellation
    and skip the serving lifespan's terminal-host cleanup.
    """
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("%s task failed before projector shutdown", label)


@dataclass(frozen=True)
class ProjectionReplay:
    """The sim/replay seam: the clock the projector reads and the feeder that runs each tick.

    They are one substitution. Sim wires a replay clock together with the feeder that writes the
    world that clock is about; a replay clock without its feeder ticks over a world that never
    moves. Both ``None`` is live serving.
    """

    now: Callable[[], datetime] | None = None
    before_tick: Callable[[datetime], object] | None = None


@dataclass(frozen=True)
class ProjectionRefreshers:
    """The side-inputs a LIVE tick drives, and the watcher that lets it wake early.

    All three are enabled together for live serving and disabled together for sim replay (the
    feeder only writes *inside* a tick, so a change-gated loop would never wake). They are one
    choice -- "is this projector attached to a moving world?" -- not three independent hooks.
    """

    provider: ProviderStateRefresh | None = None
    landing: LandingStateRefresh | None = None
    change_watcher: ChangeWatch | None = None


LIVE_PROJECTION_CLOCK = ProjectionReplay()
"""No replay clock and no feeder: the projector reads real time and drives a moving world."""
NO_PROJECTION_REFRESHERS = ProjectionRefreshers()


class Projector:
    """Owns the latest projection, a monotonic sequence, and the subscriber fan-out."""

    def __init__(
        self,
        config: McpRuntimeConfig,
        *,
        cadence: ProjectionCadence = DEFAULT_PROJECTION_CADENCE,
        replay: ProjectionReplay = LIVE_PROJECTION_CLOCK,
        refreshers: ProjectionRefreshers = NO_PROJECTION_REFRESHERS,
    ) -> None:
        interval = cadence.interval
        heartbeat = cadence.heartbeat
        change_watcher = refreshers.change_watcher
        self._config = config
        self._interval = interval
        self._now: Callable[[], datetime] = replay.now or _utcnow
        self._before_tick = replay.before_tick
        self._provider_refresher = refreshers.provider
        self._landing_refresher = refreshers.landing
        # Adaptive waking (260712-PTS-L3): with a watcher, the run loop paces via the
        # ChangePacer (change-driven + heartbeat, floored to one tick per ``interval``).
        # Without one -- sim replay and the injected-now() tests -- it keeps the exact
        # legacy ``sleep(interval)`` pacemaker.
        self._change_watcher = change_watcher
        self._pacer: ChangePacer | None = (
            ChangePacer(
                interval=interval,
                heartbeat=heartbeat if heartbeat is not None else DEFAULT_HEARTBEAT_SECONDS,
            )
            if change_watcher is not None
            else None
        )
        self._input_state = ProjectionInputState() if change_watcher is not None else None
        # Instrumentation (R7 tests + ops): successful projections since run() started,
        # and why the last tick woke ("change" | "heartbeat" | "interval").
        self.projection_count = 0
        self.last_wake_reason: str | None = None
        self.last_invalidated_domains: frozenset[str] = frozenset()
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
            first = await asyncio.to_thread(
                self._tick_sync,
                self._now(),
                ProjectionRefresh.full() if self._input_state is not None else None,
            )
        except Exception:
            logger.exception("initial projection failed; the tick loop will retry")
            return
        self._latest_stable = stable_projection_state(first)
        self._published = (0, first)

    async def run(self) -> None:
        """Tick on wake: re-project, diff, broadcast. One bad tick never kills the loop."""
        landing_task = (
            asyncio.create_task(self._landing_refresher.run())
            if self._landing_refresher is not None
            else None
        )
        watch_task = (
            asyncio.create_task(self._change_watcher.run(self._pacer))
            if self._change_watcher is not None and self._pacer is not None
            else None
        )
        if watch_task is not None:
            # A watcher that dies (or returns) must not leave the pacer believing changes
            # are still detected -- that would silently stretch every wake to the heartbeat.
            # Fail-open (R7): drop to fixed-interval ticking and say so loudly.
            watch_task.add_done_callback(self._on_watch_task_done)
        try:
            while True:
                if self._pacer is None:
                    await asyncio.sleep(self._interval)
                    refresh = None
                else:
                    wake = await self._pacer.wait()
                    self.last_wake_reason = wake.reason
                    self.last_invalidated_domains = frozenset(
                        domain.value for domain in wake.domains
                    )
                    if wake.reason == "change":
                        refresh = ProjectionRefresh.change(wake.domains)
                    elif wake.reason == "heartbeat":
                        refresh = ProjectionRefresh.heartbeat()
                    else:
                        refresh = ProjectionRefresh.full()
                try:
                    current = await asyncio.to_thread(self._tick_sync, self._now(), refresh)
                except Exception:
                    logger.exception("projection tick failed; retrying next interval")
                    continue
                self.projection_count += 1
                self._publish_projection(current)
        finally:
            await _shutdown_task(watch_task, "change watcher")
            await _shutdown_task(landing_task, "landing refresher")

    def _on_watch_task_done(self, task: asyncio.Task[None]) -> None:
        """R7 fail-open: a finished watcher task degrades pacing to the fixed interval."""
        if self._pacer is None or task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.error(
                "change watcher task died; falling back to fixed-interval ticking every %.1fs",
                self._interval,
                exc_info=exception,
            )
        self._pacer.set_watcher_healthy(False)

    def _tick_sync(
        self, moment: datetime, refresh: ProjectionRefresh | None = None
    ) -> WorkspaceProjection:
        """Run the optional pre-tick hook, then project at ``moment`` (off the loop thread)."""
        if self._before_tick is not None:
            self._before_tick(moment)
        return project_and_write(
            self._config,
            now=moment,
            refresh=refresh,
            tick=ProjectionTickState(
                input_state=self._input_state,
                provider_refresher=self._provider_refresher,
                landing_state=self._landing_refresher,
            ),
        )

    def _publish_projection(self, current: WorkspaceProjection) -> None:
        """Atomically publish one successful tick and notify current subscribers.

        A first tick after a failed :meth:`prime` has no previous projection to diff. Existing
        subscribers still need that recovered authority, so it is broadcast once as a full
        ``snapshot`` event. The published tuple is committed before queue notification; this
        method contains no await, so subscription capture cannot interleave with the transition.
        """
        current_stable = stable_projection_state(current)
        seq, previous = self._published
        events = (
            [DeltaEvent("snapshot", current)]
            if previous is None
            else diff_projection(
                previous,
                current,
                previous_state=self._latest_stable,
                current_state=current_stable,
            )
        )
        items: list[_Item] = []
        for event in events:
            seq += 1
            items.append((seq, event))
        self._latest_stable = current_stable
        self._published = (seq, current)
        for item in items:
            self._broadcast(item)

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
        """Yield the current snapshot, then deltas, from one atomic subscription boundary.

        Queue registration and ``_published`` capture happen in the same event-loop turn with
        no await between them. A projection transition therefore lands either in the captured
        snapshot or in this queue, never in the former handoff gap.
        """
        queue: asyncio.Queue[_Item] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            seq, snapshot = self._published
            if snapshot is not None:
                yield seq, DeltaEvent("snapshot", snapshot)
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)
