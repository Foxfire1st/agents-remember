"""The Codex adapter's bounded event fan-out and its load-shedding policy."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from agents_remember.serving.harness_control_models import (
    AdapterEvent,
)

ADAPTER_EVENT_QUEUE_LIMIT = 1024
LOAD_SHED_DELTA_METHODS = frozenset(
    {
        "item/agentMessage/delta",
        "item/plan/delta",
        "item/reasoning/summaryTextDelta",
        "item/reasoning/summaryPartAdded",
        "item/reasoning/textDelta",
        "item/commandExecution/outputDelta",
        "item/fileChange/patchUpdated",
    }
)


class CodexEventQueue:
    """Bounded queue with honest load-shedding: never raises, never silently drops.

    A multiplexed seat's delta floods can outpace the consumer (the 2026-07-26
    seat death: the queue-full raise killed the bridge, and content vanished
    before it). On a full queue the oldest HIGH-VOLUME delta event sheds first
    (structural events -- turns, completions, interactions, failures, the close
    sentinel -- shed only when nothing else remains), every shed is counted, and
    one load-shed notice crosses when the consumer catches up, so the projection
    surfaces the loss instead of hiding it.

    ``notice`` mints that notice for a given shed count, and answers ``None`` while the
    adapter has no snapshot to sequence it against; the count is then still owed and is
    carried to the next opportunity rather than lost.
    """

    def __init__(
        self,
        *,
        notice: Callable[[int], AdapterEvent | None],
        limit: int = ADAPTER_EVENT_QUEUE_LIMIT,
    ) -> None:
        self._notice = notice
        self._queue: asyncio.Queue[AdapterEvent | None] = asyncio.Queue(maxsize=limit)
        self._dropped = 0

    @property
    def dropped(self) -> int:
        """Shed events not yet accounted for in a load-shed notice."""

        return self._dropped

    def full(self) -> bool:
        return self._queue.full()

    def offer(self, event: AdapterEvent | None) -> None:
        """Queue one event, or the ``None`` close sentinel, shedding first if there is no room."""

        if event is None:
            # The close sentinel terminates subscribers: the shed accounting must
            # cross BEFORE it, never behind it -- make room for notice + sentinel,
            # mint, then offer. Every eviction lands in the notice's count.
            while self._queue.qsize() > self._queue.maxsize - 2:
                self._evict_for_space()
            self._flush_notice()
        while self._queue.full():
            self._evict_for_space()
        self._queue.put_nowait(event)
        self._flush_notice()

    async def stream(self) -> AsyncIterator[AdapterEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event
            # Consumer-side catch-up: after a flood the producer may go silent,
            # so the shed accounting cannot wait for the next put — mint the notice
            # as soon as the drained queue has room. Same monotonic sequence path,
            # and dropped==0 makes it a no-op, so the notice itself never recurses.
            self._flush_notice()

    def drain(self) -> list[AdapterEvent | None]:
        """Take everything queued right now, in order, leaving the queue empty."""

        held: list[AdapterEvent | None] = []
        while not self._queue.empty():
            held.append(self._queue.get_nowait())
        return held

    def _evict_for_space(self) -> None:
        held = self.drain()
        index = next(
            (
                position
                for position, candidate in enumerate(held)
                if candidate is not None
                and candidate.raw.get("codexMethod") in LOAD_SHED_DELTA_METHODS
            ),
            0,  # nothing sheddable: the oldest event overall
        )
        evicted = held.pop(index)
        for candidate in held:
            self._queue.put_nowait(candidate)
        if evicted is not None:
            self._dropped += 1

    def _flush_notice(self) -> None:
        if self._dropped == 0 or self._queue.full():
            return
        event = self._notice(self._dropped)
        if event is None:
            return
        self._dropped = 0
        self._queue.put_nowait(event)
