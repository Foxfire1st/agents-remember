"""Tests for the opt-in heap-growth diagnostic."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import threading
import time
import tracemalloc
import unittest
from unittest import mock

from agents_remember.serving import heap_diag


class HeapDiagFlagTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        self.assertFalse(heap_diag.heap_diag_enabled({}))

    def test_truthy_values_enable(self) -> None:
        for value in ("1", "true", "TRUE", "yes", "on", " On "):
            self.assertTrue(heap_diag.heap_diag_enabled({"AR_HEAP_DIAG": value}), value)

    def test_falsy_values_stay_disabled(self) -> None:
        for value in ("0", "false", "no", "off", ""):
            self.assertFalse(heap_diag.heap_diag_enabled({"AR_HEAP_DIAG": value}), value)

    def test_interval_default_and_override(self) -> None:
        self.assertEqual(
            heap_diag.heap_diag_interval_seconds({}), heap_diag.DEFAULT_INTERVAL_SECONDS
        )
        self.assertEqual(
            heap_diag.heap_diag_interval_seconds({"AR_HEAP_DIAG_INTERVAL": "5"}), 5.0
        )

    def test_interval_ignores_garbage_and_nonpositive(self) -> None:
        for bad in ("abc", "0", "-3"):
            self.assertEqual(
                heap_diag.heap_diag_interval_seconds({"AR_HEAP_DIAG_INTERVAL": bad}),
                heap_diag.DEFAULT_INTERVAL_SECONDS,
                bad,
            )

    def test_start_tracing_noop_when_disabled(self) -> None:
        self.assertFalse(tracemalloc.is_tracing())
        self.assertFalse(heap_diag.start_heap_tracing({}))
        self.assertFalse(tracemalloc.is_tracing())


class MallocTrimTests(unittest.TestCase):
    def test_trim_disabled_by_default(self) -> None:
        self.assertFalse(heap_diag.malloc_trim_enabled({}))

    def test_trim_enabled_by_flag(self) -> None:
        self.assertTrue(heap_diag.malloc_trim_enabled({"AR_MALLOC_TRIM": "1"}))

    def test_trim_interval_default_and_override(self) -> None:
        self.assertEqual(
            heap_diag.malloc_trim_interval_seconds({}),
            heap_diag.DEFAULT_TRIM_INTERVAL_SECONDS,
        )
        self.assertEqual(
            heap_diag.malloc_trim_interval_seconds({"AR_MALLOC_TRIM_INTERVAL": "30"}), 30.0
        )
        self.assertEqual(
            heap_diag.malloc_trim_interval_seconds({"AR_MALLOC_TRIM_INTERVAL": "junk"}),
            heap_diag.DEFAULT_TRIM_INTERVAL_SECONDS,
        )

    def test_trim_malloc_returns_int_or_none(self) -> None:
        # On glibc this returns 0/1; on non-glibc it must degrade to None, never raise.
        result = heap_diag.trim_malloc()
        self.assertTrue(result is None or isinstance(result, int))
        # Called twice to exercise the cached symbol path.
        self.assertEqual(type(heap_diag.trim_malloc()), type(result))


class HeapDiagReportTests(unittest.TestCase):
    def setUp(self) -> None:
        if not tracemalloc.is_tracing():
            tracemalloc.start(10)
        self.addCleanup(tracemalloc.stop)

    def test_report_baseline_and_diff_render(self) -> None:
        first = heap_diag.take_snapshot()
        baseline = heap_diag.format_heap_report(first, None)
        self.assertIn("heap-diag:", baseline)
        self.assertIn("rss=", baseline)
        self.assertIn("traced_now=", baseline)
        # Allocate a retained blob so the diff has a positive grower to render.
        retained = [bytearray(200_000) for _ in range(5)]
        second = heap_diag.take_snapshot()
        report = heap_diag.format_heap_report(second, first)
        self.assertIn("heap-diag:", report)
        self.assertIn("top growth since last snapshot", report)
        self.assertEqual(len(retained), 5)

    def test_rss_bytes_is_positive_on_procfs(self) -> None:
        rss = heap_diag.process_rss_bytes()
        if rss is not None:
            self.assertGreater(rss, 0)


class HeapDiagLoopTests(unittest.IsolatedAsyncioTestCase):
    """The loop keeps the event loop responsive by running the heavy work off it.

    Measured live: report formatting (the pure-Python ``Snapshot.compare_to`` heap walk, ~99% of
    the cost) burned ~35% of daemon CPU ON the event loop and caused a daemon-wide latency storm;
    moving it to a worker thread cut max loop stall from 13,691ms to 78ms because that walk releases
    the GIL and interleaves. These tests pin two things: that the snapshot AND the formatting run in
    worker threads (placement), and -- the property no earlier test covered -- that the loop
    actually STAYS responsive (bounded max stall) while a full report is formatted off it.
    """

    async def test_snapshot_and_report_run_off_the_loop_with_chained_baselines(self) -> None:
        loop_thread = threading.get_ident()
        snapshots = [object(), object()]
        taken = itertools.cycle(snapshots)
        two_reports = threading.Event()
        calls: list[tuple[str, int, object, object]] = []

        def fake_take_snapshot() -> object:
            snapshot = next(taken)
            calls.append(("snapshot", threading.get_ident(), snapshot, None))
            return snapshot

        def fake_report(snapshot: object, previous: object) -> str:
            calls.append(("report", threading.get_ident(), snapshot, previous))
            if len([call for call in calls if call[0] == "report"]) == 2:
                two_reports.set()
            return "heap-diag: fake report"

        with (
            mock.patch.object(heap_diag, "heap_diag_interval_seconds", return_value=0.01),
            mock.patch.object(heap_diag, "take_snapshot", fake_take_snapshot),
            mock.patch.object(heap_diag, "format_heap_report", fake_report),
            self.assertLogs(heap_diag.logger.name, level="WARNING") as logged,
        ):
            task = asyncio.create_task(heap_diag.heap_diag_loop())
            try:
                deadline = time.monotonic() + 5.0
                while not two_reports.is_set() and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
                self.assertTrue(two_reports.is_set(), "the loop never produced two reports")
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        # The report still rides the same log line as before the move.
        self.assertTrue(any("heap-diag: fake report" in line for line in logged.output))
        reports = [call for call in calls if call[0] == "report"][:2]
        # Baseline chaining is unchanged: no previous on the first tick, then diff against the
        # prior snapshot -- only WHERE the work runs moved.
        self.assertEqual(
            [(call[2], call[3]) for call in reports],
            [(snapshots[0], None), (snapshots[1], snapshots[0])],
        )
        for name, thread, _, _ in calls:
            self.assertNotEqual(thread, loop_thread, f"{name} ran on the event loop")

    async def test_a_blocked_report_never_stalls_the_event_loop(self) -> None:
        report_started = threading.Event()
        release_report = threading.Event()
        # Safety net: a regression (formatting back on the loop) must FAIL via this timer, never
        # hang the suite waiting on itself.
        timer = threading.Timer(5.0, release_report.set)

        def blocked_report(snapshot: object, previous: object) -> str:
            report_started.set()
            release_report.wait(timeout=10.0)
            return "heap-diag: fake report"

        with (
            mock.patch.object(heap_diag, "heap_diag_interval_seconds", return_value=0.01),
            mock.patch.object(heap_diag, "take_snapshot", return_value=object()),
            mock.patch.object(heap_diag, "format_heap_report", blocked_report),
        ):
            timer.start()
            task = asyncio.create_task(heap_diag.heap_diag_loop())
            try:
                deadline = time.monotonic() + 5.0
                while not report_started.is_set() and time.monotonic() < deadline:
                    await asyncio.sleep(0.005)
                self.assertTrue(report_started.is_set(), "the report never ran")
                started = time.monotonic()
                await asyncio.sleep(0.2)
                elapsed = time.monotonic() - started
                # The report stays blocked far longer than this; an on-loop format would stretch
                # the sleep to the 5s safety timer instead of ~0.2s.
                self.assertLess(elapsed, 2.0)
            finally:
                release_report.set()
                timer.cancel()
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def test_off_loop_formatting_keeps_the_event_loop_responsive(self) -> None:
        """The loop stays responsive (bounded max stall) while a heavy report formats off it.

        The two tests above assert only WHERE the work runs; the third asserts the property
        that actually matters -- the event loop keeps ticking. A heartbeat coroutine samples the
        loop every 5ms and records the largest gap between wakeups (the max loop stall) while a
        full CPU-bound report is produced. Off-loop (the shipped ``to_thread`` code) the walk
        releases the GIL and interleaves, so the stall stays small; on-loop (the earlier version) the
        same work would pin the loop for the whole formatting duration.

        The workload is a wall-clock-bounded busy loop rather than a real ``format_heap_report``
        because the real pure-Python heap walk only costs seconds against a multi-GB live heap --
        far too small on a unit-test heap to pin the regression. A busy loop is a faithful stand-in:
        it is exactly the same shape of work (CPU-bound Python that yields the GIL at the switch
        interval), so its off-loop interleaving exercises the identical mechanism.
        """
        work_seconds = 0.6
        max_tolerated_stall = 0.3  # off-loop stall is ~tens of ms; on-loop it would be ~0.6s
        report_done = threading.Event()

        def cpu_busy_report(snapshot: object, previous: object) -> str:
            end = time.monotonic() + work_seconds
            acc = 0
            while time.monotonic() < end:
                acc += 1  # busy-spin; the interpreter hands off the GIL every switch interval
            report_done.set()
            return f"heap-diag: fake report {acc}"

        max_gap = 0.0
        stop_heartbeat = False

        async def heartbeat() -> None:
            nonlocal max_gap
            last = time.monotonic()
            while not stop_heartbeat:
                await asyncio.sleep(0.005)
                now = time.monotonic()
                max_gap = max(max_gap, now - last)
                last = now

        with (
            mock.patch.object(heap_diag, "heap_diag_interval_seconds", return_value=0.01),
            mock.patch.object(heap_diag, "take_snapshot", return_value=object()),
            mock.patch.object(heap_diag, "format_heap_report", cpu_busy_report),
        ):
            beat = asyncio.create_task(heartbeat())
            task = asyncio.create_task(heap_diag.heap_diag_loop())
            try:
                deadline = time.monotonic() + work_seconds + 4.0
                while not report_done.is_set() and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
                self.assertTrue(report_done.is_set(), "the heavy report never ran")
                # Give the heartbeat a moment to record the gap that spans the heavy work.
                await asyncio.sleep(0.05)
            finally:
                stop_heartbeat = True
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                await beat

        self.assertLess(
            max_gap,
            max_tolerated_stall,
            f"event loop stalled {max_gap * 1000:.0f}ms while formatting a report; off-loop "
            "formatting must keep the loop responsive",
        )


if __name__ == "__main__":
    unittest.main()
