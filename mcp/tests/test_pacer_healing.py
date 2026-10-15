"""MIK-R81 rules 5/6 (F05/F06): healing wakes and invalidation acknowledgement.

A due full refresh preempts partial-change traffic and never overshoots its 300-second
bound; only a published full refresh advances the bound. Consumed invalidations stay
outstanding until their tick succeeds: failures, arrivals during work and cancelled ticks
all keep their domains for the next eligible cycle.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from agents_remember.serving import change_watcher as watcher_module
from agents_remember.serving.change_watcher import (
    ALL_PROJECTION_DOMAINS,
    FULL_REFRESH_SECONDS,
    ChangePacer,
    ProjectionDomain,
)


class PacerHealingTests(unittest.IsolatedAsyncioTestCase):
    async def test_continuous_partial_traffic_still_heals_on_time(self) -> None:
        """Healthy TASKS changes at every wake: the full still fires at 300s (F05)."""

        clock = [0.0]
        with mock.patch.object(watcher_module, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            pacer = ChangePacer(interval=1)
            pacer.set_watcher_healthy(True)
            reasons: list[str] = []
            for _cycle in range(4):
                pacer.notify_change(frozenset({ProjectionDomain.TASKS}))
                clock[0] += 1
                wake = await pacer.wait()
                reasons.append(wake.reason)
                pacer.acknowledge(wake)
            self.assertTrue(all(reason == "change" for reason in reasons))
            clock[0] = FULL_REFRESH_SECONDS + 1
            pacer.notify_change(frozenset({ProjectionDomain.TASKS}))
            wake = await pacer.wait()
            self.assertEqual(wake.reason, "full")
            self.assertEqual(wake.domains, ALL_PROJECTION_DOMAINS)
            pacer.acknowledge(wake)
            pacer.confirm_full_published()
            clock[0] += 16
            wake = await pacer.wait()
            self.assertEqual(wake.reason, "heartbeat")

    async def test_misaligned_heartbeat_never_overshoots_the_heal_bound(self) -> None:
        """A heartbeat longer than the remaining heal budget wakes as full on time (F05)."""

        clock = [0.0]
        with mock.patch.object(watcher_module, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            pacer = ChangePacer(interval=1, heartbeat=70)
            pacer.set_watcher_healthy(True)
            clock[0] = 290
            wake = await pacer.wait()
            self.assertEqual(wake.reason, "heartbeat")
            # The next heartbeat would land at 360, past the 300s heal bound: the pacer
            # wakes full at exactly 300 instead. Pure deadline reads: the mocked clock
            # must never drive a real sleep.
            deadline, reason = pacer._next_deadline()
            self.assertEqual((deadline, reason), (300.0, "full"))

    async def test_failed_full_wake_does_not_advance_the_heal_bound(self) -> None:
        """A full wake without a published tick heals again immediately (F05/F06)."""

        clock = [0.0]
        with mock.patch.object(watcher_module, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            pacer = ChangePacer(interval=1)
            pacer.set_watcher_healthy(True)
            clock[0] = FULL_REFRESH_SECONDS + 1
            wake = await pacer.wait()
            self.assertEqual(wake.reason, "full")
            # The tick fails: no acknowledgement, no publication.
            clock[0] += 2
            retry = await pacer.wait()
            self.assertEqual(retry.reason, "full")
            self.assertEqual(retry.domains, ALL_PROJECTION_DOMAINS)

    async def test_failed_change_tick_keeps_its_domains(self) -> None:
        """A change wake whose tick fails stays outstanding for the next cycle (F06)."""

        clock = [0.0]
        with mock.patch.object(watcher_module, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            pacer = ChangePacer(interval=1)
            pacer.set_watcher_healthy(True)
            pacer.notify_change(frozenset({ProjectionDomain.TOOL_REPORTS}))
            clock[0] = 1
            wake = await pacer.wait()
            self.assertEqual(wake.domains, frozenset({ProjectionDomain.TOOL_REPORTS}))
            # Tick fails: no acknowledgement.
            clock[0] = 3
            retry = await pacer.wait()
            self.assertEqual(retry.reason, "change")
            self.assertEqual(retry.domains, frozenset({ProjectionDomain.TOOL_REPORTS}))
            pacer.acknowledge(retry)
            clock[0] = 4
            pacer.notify_change(frozenset({ProjectionDomain.WORKSPACE}))
            clock[0] = 5
            third = await pacer.wait()
            self.assertEqual(third.domains, frozenset({ProjectionDomain.WORKSPACE}))

    async def test_arrival_during_work_keeps_the_merged_set(self) -> None:
        """An arrival between wake and acknowledgement re-wakes everything merged (F06)."""

        clock = [0.0]
        with mock.patch.object(watcher_module, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            pacer = ChangePacer(interval=1)
            pacer.set_watcher_healthy(True)
            pacer.notify_change(frozenset({ProjectionDomain.TASKS}))
            clock[0] = 1
            wake = await pacer.wait()
            pacer.notify_change(frozenset({ProjectionDomain.WORKSPACE}))
            pacer.acknowledge(wake)
            self.assertEqual(wake.generation + 1, pacer._generation)
            clock[0] = 3
            rewake = await pacer.wait()
            self.assertEqual(
                rewake.domains,
                frozenset({ProjectionDomain.TASKS, ProjectionDomain.WORKSPACE}),
            )

    async def test_cancelled_tick_keeps_queued_domains(self) -> None:
        """A wake whose tick is cancelled is never acknowledged: it stays queued (F06)."""

        clock = [0.0]
        with mock.patch.object(watcher_module, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            pacer = ChangePacer(interval=1)
            pacer.set_watcher_healthy(True)
            pacer.notify_change(frozenset({ProjectionDomain.LIFECYCLES}))
            clock[0] = 1

            async def cancelled_wait() -> None:
                wake = await pacer.wait()
                raise asyncio.CancelledError(wake)

            with self.assertRaises(asyncio.CancelledError):
                await cancelled_wait()
            clock[0] = 3
            retry = await pacer.wait()
            self.assertEqual(retry.domains, frozenset({ProjectionDomain.LIFECYCLES}))

    async def test_two_corpus_sizes_keep_one_change_independent(self) -> None:
        """The ack contract holds at two pending-path volumes (F05/F06 sizing)."""

        for path_count in (1, 500):
            clock = [0.0]

            def _now(clock: list[float] = clock) -> float:
                return clock[0]

            with mock.patch.object(watcher_module, "time", SimpleNamespace(monotonic=_now)):
                pacer = ChangePacer(interval=1)
                pacer.set_watcher_healthy(True)
                pacer.notify_change(
                    frozenset({ProjectionDomain.TASKS}),
                    frozenset({f"tasks/{index}/task.json" for index in range(path_count)}),
                )
                clock[0] = 1
                wake = await pacer.wait()
                self.assertEqual(wake.domains, frozenset({ProjectionDomain.TASKS}))
                pacer.acknowledge(wake)
                clock[0] = 2
                pacer.notify_change(frozenset({ProjectionDomain.WORKSPACE}))
                clock[0] = 3
                rewake = await pacer.wait()
                self.assertEqual(rewake.domains, frozenset({ProjectionDomain.WORKSPACE}))
