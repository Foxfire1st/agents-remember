"""The deadline-correct virtual timeline and its two synchronization primitives.

The handoff oracle is measured in virtual time, so the timeline itself is an instrument and its
semantics matter. ``_VirtualClock`` (the shared serving fixture's clock) releases a sleep by adding
its delay to the current time, which is only coherent while one loop owns the timeline: here two
loops park on different delays (the observer's ``P`` and the notifier's ``N``), so adding ``N`` at a
moment the clock has already reached that sleep's park time plus ``N`` would date the notifier's
wake-up late and corrupt both the sweeper's rate limits and every measured latency.

``_DeadlineClock`` records each sleep's deadline when it parks and moves ``seconds`` to that deadline,
never backwards: a sleep parked now still expires exactly one delay later, and an overdue sleep fires
wherever it is released. ``_Gate`` parks one chosen pass until the case releases it, and ``_Sequence``
gives the events of different threads one real-time total order, which is what makes "the read
returned before the commit" decidable while virtual time stands still.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from test_serving_observation_loop import _VirtualClock


class _DeadlineClock(_VirtualClock):
    """The shared virtual clock, advanced to a released sleep's own deadline.

    ``_VirtualClock.release`` adds a released sleep's delay to the current time, which is only
    coherent while a single loop owns the timeline. Two loops park on different delays here (the
    observer's ``P`` and the notifier's ``N``), so adding ``N`` at a moment the clock has already
    reached that sleep's park time plus ``N`` would date the notifier's wake-up late and corrupt both
    the sweeper's rate limits and every latency measured against this clock. This clock records each
    sleep's deadline when it parks and moves ``seconds`` to that deadline, never backwards, so a
    sleep parked now still expires exactly one delay later and an overdue sleep fires where it is
    released.
    """

    def __init__(self, timeline: list[str]) -> None:
        super().__init__(timeline)
        self._deadlines: dict[int, float] = {}

    async def sleep(self, delay: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.requested.append(delay)
        self.timeline.append(f"sleep({delay})")
        self._parked.append((delay, future))
        self._deadlines[id(future)] = self.seconds + delay
        try:
            await future
        except asyncio.CancelledError:
            self._parked = [item for item in self._parked if item[1] is not future]
            raise

    def has_pending(self, delay: float) -> bool:
        """Whether a sleep of exactly ``delay`` is parked: its loop is between passes."""

        return any(parked == delay for parked, _ in self._parked)

    def release_delay(self, delay: float) -> None:
        """Complete the parked sleep of ``delay`` and advance virtual time to its own deadline."""

        for index, (parked, future) in enumerate(self._parked):
            if parked == delay:
                self._parked.pop(index)
                self.seconds = max(self.seconds, self._deadlines.pop(id(future), self.seconds))
                if not future.done():
                    future.set_result(None)
                return
        raise AssertionError(f"no parked sleep of {delay}s")


class _Gate:
    """Parks the next matching call, once a case arms it, until the case releases it.

    ``arm`` clears the release flag, so arming is also the reset: a case arms the gate immediately
    before the step whose pass it wants to park, and the hook that fires next is that pass. The
    bounded wait turns a gate that is never reached into a failure instead of a hang.
    """

    def __init__(self) -> None:
        self.armed = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def arm(self) -> None:
        self.armed = True
        self.entered.clear()
        self.release.clear()

    def __call__(self) -> None:
        if not self.armed:
            return
        self.armed = False
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise AssertionError("a parked pass was never released")


class _Sequence:
    """One real-time total order across threads for the events a case orders against each other.

    Virtual time stands still while a pass works, so "the read returned before the commit" is not
    expressible in it. Every recorded event takes its number under one lock instead, which makes that
    claim a single integer comparison.

    ``lock`` is public and reentrant so a recorder whose callers are different threads can hold the
    number and the record that carries it in one critical section: two concurrent callers must not be
    able to take the same number, and the record must not be appended between another caller's number
    and its own record.
    """

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.value = 0

    def next(self) -> int:
        with self.lock:
            self.value += 1
            return self.value
