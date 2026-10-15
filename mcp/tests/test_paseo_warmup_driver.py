"""After a host start the tree's agents load in the background, before a click (MIK-R145).

Fail-first: every test below fails without ``cli/paseo_warmup.py``. The bridge is
never touched: resume and listing calls are injected fakes, so no test starts, stops
or restarts a host and none sends it a request.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agents_remember.cli.paseo_status import HostStartIdentity
from agents_remember.cli.paseo_warmup_driver import WarmupDriver
from agents_remember.cli.paseo_warmup_run import (
    WARMUP_CONCURRENCY,
    WarmupRun,
)
from agents_remember.cli.paseo_warmup_state import (
    WarmupClaim,
    WarmupTracker,
    claim_warmup_record,
)
from agents_remember_test_support.testing.paseo_warmup import (
    _BlockedWorkerResumes,
    _mem_plenty,
    _release_then_hang,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, async_wait_until


class DriverTests(unittest.TestCase):
    """F26: the watch loop retires old passes, ends them on host-away, and drains."""

    def _claim(self, number: int) -> WarmupClaim:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        claim = claim_warmup_record(
            Path(temporary.name), "srv-1", HostStartIdentity(number, str(number * 100), "test-boot")
        )
        assert claim is not None
        self.addCleanup(claim.close)
        return claim

    def _drive_fn(
        self,
        tracker: Any,
        started: list[str],
        releases: dict[str, asyncio.Event],
    ) -> Any:
        def drive(run: Any, path: Any, end_note: dict[str, str | None]) -> asyncio.Task[None]:
            async def _go() -> None:
                try:
                    await run.run(
                        resume_fn=lambda agent_id: _release_then_hang(agent_id, started, releases),
                        mem_fn=_mem_plenty,
                    )
                finally:
                    reason = end_note.get("end_reason")
                    if reason is None:
                        tracker.note_run_finished(run, path)
                    else:
                        tracker.note_run_ended(run, path, reason)

            return asyncio.create_task(_go())

        return drive

    def test_a_blocked_resume_cancels_and_drains(self) -> None:

        cancelled: list[str] = []
        release = asyncio.Event()

        async def resume(agent_id: str) -> None:
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.append(agent_id)
                raise

        run = WarmupRun(server_id="srv-1", agent_ids=["a1", "a2", "a3"])

        async def main() -> None:
            task = asyncio.create_task(run.run(resume_fn=resume, mem_fn=_mem_plenty))
            await async_wait_until(lambda: len(run.in_flight) == 2, "two admitted loads")
            self.assertEqual(sorted(run.in_flight), ["a1", "a2"])
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=HANG_GUARD_SECONDS)

        asyncio.run(main())
        self.assertTrue(run.done)
        self.assertEqual(run.in_flight, [])
        self.assertEqual(sorted(cancelled), ["a1", "a2"])
        self.assertEqual(run.loaded, 0)

    def test_a_new_start_retires_the_driving_pass(self) -> None:

        async def main() -> None:
            tracker = WarmupTracker()
            driver = WarmupDriver(tracker)
            started: list[str] = []
            releases: dict[str, asyncio.Event] = {}
            first = WarmupRun(server_id="srv-1", agent_ids=["a1"])
            await driver.noticed(
                (first, self._claim(1)), self._drive_fn(tracker, started, releases)
            )
            await async_wait_until(lambda: started == ["a1"], "first resume entered")
            self.assertEqual(started, ["a1"])
            second = WarmupRun(server_id="srv-1", agent_ids=["b1"])
            await driver.noticed(
                (second, self._claim(2)), self._drive_fn(tracker, started, releases)
            )
            await async_wait_until(lambda: "b1" in started, "successor resume entered")
            # The old pass was retired with its reason; the new one drives.
            self.assertTrue(first.done)
            assert tracker.last_record is not None
            self.assertEqual(tracker.last_record.get("state"), "ended")
            self.assertIn("newer start", tracker.last_record.get("reason", ""))
            self.assertIs(tracker.run, second)
            await driver.aclose()

        asyncio.run(main())

    def test_an_unreachable_host_ends_the_driving_pass_with_its_record(self) -> None:

        async def main() -> None:
            tracker = WarmupTracker()
            driver = WarmupDriver(tracker)
            started: list[str] = []
            releases: dict[str, asyncio.Event] = {}
            run = WarmupRun(server_id="srv-1", agent_ids=["a1"])
            await driver.noticed((run, self._claim(1)), self._drive_fn(tracker, started, releases))
            await async_wait_until(lambda: started == ["a1"], "resume before host loss")
            tracker.note_host_away()
            await driver.noticed(None, self._drive_fn(tracker, started, releases))
            self.assertTrue(run.done)
            assert tracker.last_record is not None
            self.assertEqual(tracker.last_record.get("state"), "ended")
            self.assertIn("away", tracker.last_record.get("reason", ""))
            await driver.aclose()

        asyncio.run(main())

    def test_the_concurrency_cap_stays_two(self) -> None:

        self.assertEqual(WARMUP_CONCURRENCY, 2)

    def test_blocked_worker_resumes_drain_on_handoff_and_cap_holds(self) -> None:
        """F26: production-style worker-blocked resumes drain; the peak stays two.

        The production resume blocks a worker thread (``offload`` of the bridge
        call), not a coroutine await: cancelling the pass must wait those workers
        out, never abandon them, and the successor must not stack fresh blocked
        resumes on top of the old ones.
        """
        rig = _BlockedWorkerResumes()

        async def main() -> None:
            first = WarmupRun(server_id="srv-1", agent_ids=["a1", "a2", "a3"])
            task = asyncio.create_task(first.run(resume_fn=rig.resume, mem_fn=_mem_plenty))
            await async_wait_until(lambda: rig.active == 2, "two blocked native workers")
            # The handoff cancels and drains: while the workers stay blocked the
            # old pass is still draining, never abandoned.
            task.cancel()
            await asyncio.sleep(0)  # Deliver cancellation before checking the blocked drain.
            self.assertEqual(rig.active, 2)
            # The cancelled pass is still draining its workers, not done: the
            # handoff waits them out instead of abandoning them mid-resume.
            self.assertFalse(task.done())
            self.assertEqual(sorted(first.in_flight), ["a1", "a2"])
            rig.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=HANG_GUARD_SECONDS)
            self.assertEqual(rig.active, 0)
            self.assertEqual(first.in_flight, [])
            self.assertEqual(first.loaded, 0)
            # The successor loads on freed workers: the peak never exceeds two.
            rig.release.clear()
            second = WarmupRun(server_id="srv-1", agent_ids=["b1", "b2", "b3"])
            rig.release.set()
            await asyncio.wait_for(
                second.run(resume_fn=rig.resume, mem_fn=_mem_plenty), timeout=HANG_GUARD_SECONDS
            )
            self.assertEqual(second.loaded, 3)

        asyncio.run(main())
        self.assertLessEqual(rig.peak, 2)
        self.assertEqual(rig.active, 0)

    def test_driver_handoff_waits_out_blocked_workers_before_successor(self) -> None:
        """F26: the driver retires into the drain; the successor starts after it."""
        rig = _BlockedWorkerResumes()

        def drive(
            tracker: Any, run: Any, path: Any, end_note: dict[str, str | None]
        ) -> asyncio.Task[None]:
            async def _go() -> None:
                try:
                    await run.run(resume_fn=rig.resume, mem_fn=_mem_plenty)
                finally:
                    reason = end_note.get("end_reason")
                    if reason is None:
                        tracker.note_run_finished(run, path)
                    else:
                        tracker.note_run_ended(run, path, reason)

            return asyncio.create_task(_go())

        async def main() -> None:
            tracker = WarmupTracker()
            driver = WarmupDriver(tracker)
            first = WarmupRun(server_id="srv-1", agent_ids=["a1", "a2"])
            await driver.noticed(
                (first, self._claim(1)),
                lambda run, path, note: drive(tracker, run, path, note),
            )
            await async_wait_until(lambda: rig.active == 2, "first pass occupies both workers")
            second = WarmupRun(server_id="srv-1", agent_ids=["b1", "b2"])
            handoff = asyncio.create_task(
                driver.noticed(
                    (second, self._claim(2)),
                    lambda run, path, note: drive(tracker, run, path, note),
                )
            )
            await asyncio.sleep(0)  # Run the handoff into retirement before inspecting its drain.
            # The handoff is still draining the old workers: nothing new started.
            self.assertFalse(handoff.done())
            self.assertEqual(rig.active, 2)
            rig.release.set()
            await asyncio.wait_for(handoff, timeout=HANG_GUARD_SECONDS)
            self.assertTrue(first.done)
            record = tracker.last_record
            assert record is not None
            self.assertEqual(record.get("state"), "ended")
            self.assertIn("newer start", record.get("reason", ""))
            await driver.aclose()

        asyncio.run(main())
        self.assertLessEqual(rig.peak, 2)
        self.assertEqual(rig.active, 0)
