"""The always-on background account (MIK-R81 rule 1): per-owner processor time and every wake."""

from __future__ import annotations

import asyncio
import io
import pathlib
import threading
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

import agents_remember.serving.background_accounting as accounting_module
from agents_remember.cli import dashboard as dashboard_module
from agents_remember.serving.background_accounting import (
    OWNER_METRICS,
    OWNER_PROJECTION,
    BackgroundLedger,
    describe_snapshot,
    measured,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, async_wait_until


class LedgerRecordTests(unittest.TestCase):
    def test_recorded_unit_lands_in_totals_and_window(self) -> None:
        ledger = BackgroundLedger()
        ledger.record(OWNER_PROJECTION, 0.5, 1.0)
        snapshot = ledger.snapshot()
        owners = snapshot["owners"]
        assert isinstance(owners, dict)
        projection = owners[OWNER_PROJECTION]
        assert isinstance(projection, dict)
        self.assertEqual(projection["units"], 1)
        self.assertEqual(projection["cpuSecondsTotal"], 0.5)
        self.assertEqual(projection["wallSecondsTotal"], 1.0)
        self.assertEqual(projection["cpuSecondsLast60s"], 0.5)

    def test_unknown_owner_is_kept_not_refused(self) -> None:
        ledger = BackgroundLedger()
        ledger.record("a-future-loop", 0.25, 0.5)
        owners = ledger.snapshot()["owners"]
        assert isinstance(owners, dict)
        self.assertEqual(owners["a-future-loop"]["units"], 1)

    def test_negative_samples_clamp_to_zero(self) -> None:
        ledger = BackgroundLedger()
        ledger.record(OWNER_METRICS, -1.0, -2.0)
        owners = ledger.snapshot()["owners"]
        assert isinstance(owners, dict)
        self.assertEqual(owners[OWNER_METRICS]["cpuSecondsTotal"], 0.0)
        self.assertEqual(owners[OWNER_METRICS]["wallSecondsTotal"], 0.0)


class WakeLogTests(unittest.TestCase):
    def test_every_wake_is_recorded_with_reason_domains_and_paths(self) -> None:
        ledger = BackgroundLedger()
        ledger.note_wake("change", ("tasks",), 2, ("tasks/a/task.json", "tasks/b/task.json"))
        ledger.note_wake("heartbeat", (), 0)
        snapshot = ledger.snapshot()
        wakes = snapshot["wakes"]
        assert isinstance(wakes, dict)
        self.assertEqual(wakes["total"], 2)
        self.assertEqual(wakes["byReason"], {"change": 1, "heartbeat": 1})
        self.assertEqual(wakes["last60s"], 2)
        recent = wakes["recent"]
        assert isinstance(recent, list)
        change = recent[0]
        assert isinstance(change, dict)
        self.assertEqual(change["reason"], "change")
        self.assertEqual(change["domains"], ["tasks"])
        self.assertEqual(change["pathCount"], 2)
        self.assertEqual(change["samplePaths"], ["tasks/a/task.json", "tasks/b/task.json"])

    def test_wake_log_is_bounded_and_counts_its_drops(self) -> None:
        ledger = BackgroundLedger()
        for _ in range(300):
            ledger.note_wake("heartbeat", (), 0)
        snapshot = ledger.snapshot()
        wakes = snapshot["wakes"]
        assert isinstance(wakes, dict)
        self.assertEqual(wakes["total"], 300)
        recent = wakes["recent"]
        assert isinstance(recent, list)
        self.assertEqual(len(recent), 20)
        dropped = wakes["droppedWakes"]
        assert isinstance(dropped, int)
        self.assertGreater(dropped, 0)


class MeasuredTests(unittest.TestCase):
    def test_measured_records_thread_cpu_and_wall(self) -> None:
        ledger = BackgroundLedger()
        with measured(OWNER_PROJECTION, ledger):
            total = 0
            for number in range(10000):
                total += number
        self.assertGreater(total, 0)
        owners = ledger.snapshot()["owners"]
        assert isinstance(owners, dict)
        projection = owners[OWNER_PROJECTION]
        assert isinstance(projection, dict)
        self.assertEqual(projection["units"], 1)
        self.assertGreaterEqual(projection["cpuSecondsTotal"], 0.0)
        self.assertGreater(projection["wallSecondsTotal"], 0.0)

    def test_measured_without_ledger_runs_the_body(self) -> None:
        seen = []
        with measured(OWNER_PROJECTION, None):
            seen.append(True)
        self.assertEqual(seen, [True])

    def test_measured_records_even_when_the_body_raises(self) -> None:
        ledger = BackgroundLedger()
        with self.assertRaises(RuntimeError), measured(OWNER_METRICS, ledger):
            raise RuntimeError("boom")
        owners = ledger.snapshot()["owners"]
        assert isinstance(owners, dict)
        self.assertEqual(owners[OWNER_METRICS]["units"], 1)


class StatusLineTests(unittest.TestCase):
    def test_status_line_names_share_and_wake_count(self) -> None:
        ledger = BackgroundLedger()
        ledger.record(OWNER_PROJECTION, 0.6, 1.0)
        ledger.note_wake("heartbeat", (), 0)
        line = ledger.status_line()
        self.assertIn("background work:", line)
        self.assertIn("of one processor over the last 60 s", line)
        self.assertIn("1 projection wakes", line)

    def test_describe_snapshot_mirrors_the_route_payload(self) -> None:
        ledger = BackgroundLedger()
        ledger.record(OWNER_PROJECTION, 0.6, 1.0)
        line = describe_snapshot(ledger.snapshot())  # type: ignore[arg-type]
        self.assertIn("background work:", line)
        self.assertIn("0 projection wakes", line)

    def test_describe_snapshot_never_raises(self) -> None:
        self.assertIn("unavailable", describe_snapshot({}))
        line = describe_snapshot({"owners": {}, "wakes": {}})  # type: ignore[dict-item]
        self.assertIn("background work:", line)

    def test_describe_snapshot_prints_cumulative_and_trailing_owner_totals(self) -> None:
        """MIK-R81 rule 1 (F03): the CLI shows lifetime vs window totals per owner."""

        text = describe_snapshot(
            {
                "windowSeconds": 60.0,
                "owners": {
                    "projection": {
                        "units": 10,
                        "cpuSecondsTotal": 5.0,
                        "wallSecondsTotal": 50.0,
                        "cpuSecondsLast60s": 0.5,
                        "wallSecondsLast60s": 4.5,
                        "unitsLast60s": 1,
                    },
                    "landing-refresh": {
                        "units": 4,
                        "cpuSecondsTotal": 2.0,
                        "wallSecondsTotal": 20.0,
                        "cpuSecondsLast60s": 0.0,
                        "wallSecondsLast60s": 0.0,
                        "unitsLast60s": 0,
                    },
                },
                "wakes": {
                    "total": 9,
                    "byReason": {"change": 6, "heartbeat": 3},
                    "last60s": 2,
                    "last60sByReason": {"heartbeat": 2},
                    "recent": [
                        {
                            "reason": "change",
                            "domains": ["tasks"],
                            "pathCount": 2,
                            "samplePaths": ["tasks/a/task.json", "tasks/b/task.json"],
                        },
                    ],
                },
            }
        )
        first, *rest = text.splitlines()
        self.assertIn("background work:", first)
        self.assertIn("2 projection wakes", first)
        joined = "\n".join(rest)
        self.assertIn("projection: 10 units, 5.000 CPU-s total / 0.500 CPU-s last 60 s", joined)
        self.assertIn("50.000 wall-s total / 4.500 wall-s last 60 s", joined)
        self.assertIn("landing-refresh: 4 units, 2.000 CPU-s total / 0.000 CPU-s last 60 s", joined)
        self.assertIn("wakes: 9 total (change 6, heartbeat 3), last 60 s: 2", joined)
        self.assertIn("wake change [tasks]: 2 paths (tasks/a/task.json,tasks/b/task.json)", joined)

    def test_describe_snapshot_names_unknown_values_readably(self) -> None:
        text = describe_snapshot(
            {
                "owners": {"projection": {"units": 1}},
                "wakes": {},
            }
        )
        self.assertIn("unknown", text)

    def test_print_background_status_survives_a_route_failure(self) -> None:
        """A failing diagnostics route prints unavailable and changes no exit code."""

        buffer = io.StringIO()
        with (
            mock.patch.object(
                dashboard_module.urllib.request,
                "urlopen",
                side_effect=ConnectionError("daemon is away"),
            ),
            redirect_stdout(buffer),
        ):
            dashboard_module._print_background_status("127.0.0.1", 9)
        self.assertIn("background work: unavailable", buffer.getvalue())


class ExactWindowTests(unittest.TestCase):
    """MIK-R81 rule 1 (F04): totals stay exact past raw-detail caps, under injected clocks."""

    def _clock(self, wall: float, mono: float):  # type: ignore[no-untyped-def]
        cell = {"wall": wall, "mono": mono, "cpu": 0.0}
        fake_time = SimpleNamespace(time=lambda: cell["wall"], monotonic=lambda: cell["mono"])
        return cell, fake_time

    def test_unit_totals_stay_complete_above_the_sample_cap(self) -> None:
        cell, fake_time = self._clock(1_700_000_000.0, 10_000.0)
        with (
            mock.patch.object(accounting_module, "time", fake_time),
            mock.patch.object(accounting_module, "_process_cpu_seconds", lambda: cell["cpu"]),
        ):
            ledger = BackgroundLedger()
            for _ in range(601):
                ledger.record(OWNER_PROJECTION, 0.001, 0.002)
            snapshot = ledger.snapshot()
            owners = snapshot["owners"]
            assert isinstance(owners, dict)
            projection = owners[OWNER_PROJECTION]
            assert isinstance(projection, dict)
            self.assertEqual(projection["units"], 601)
            self.assertAlmostEqual(projection["cpuSecondsTotal"], 0.601)
            self.assertAlmostEqual(projection["cpuSecondsLast60s"], 0.601)
            self.assertAlmostEqual(projection["wallSecondsLast60s"], 1.202)
            self.assertEqual(projection["unitsLast60s"], 601)
            self.assertGreater(projection["droppedSamples"], 0)
            # Expiry: 61 monotonic seconds later the window is empty, lifetime intact.
            cell["mono"] += 61.0
            cell["wall"] += 61.0
            expired = ledger.snapshot()["owners"]
            assert isinstance(expired, dict)
            reprojection = expired[OWNER_PROJECTION]
            assert isinstance(reprojection, dict)
            self.assertEqual(reprojection["units"], 601)
            self.assertAlmostEqual(reprojection["cpuSecondsTotal"], 0.601)
            self.assertEqual(reprojection["cpuSecondsLast60s"], 0.0)
            self.assertEqual(reprojection["unitsLast60s"], 0)

    def test_unit_totals_below_the_cap_drop_nothing(self) -> None:
        cell, fake_time = self._clock(1_700_000_000.0, 20_000.0)
        with (
            mock.patch.object(accounting_module, "time", fake_time),
            mock.patch.object(accounting_module, "_process_cpu_seconds", lambda: cell["cpu"]),
        ):
            ledger = BackgroundLedger()
            for _ in range(10):
                ledger.record(OWNER_METRICS, 0.01, 0.02)
            owners = ledger.snapshot()["owners"]
            assert isinstance(owners, dict)
            metrics = owners[OWNER_METRICS]
            assert isinstance(metrics, dict)
            self.assertEqual(metrics["units"], 10)
            self.assertAlmostEqual(metrics["cpuSecondsLast60s"], 0.10)
            self.assertEqual(metrics["unitsLast60s"], 10)
            self.assertEqual(metrics["droppedSamples"], 0)

    def test_wake_counts_stay_complete_above_the_detail_cap(self) -> None:
        cell, fake_time = self._clock(1_700_000_000.0, 30_000.0)
        with (
            mock.patch.object(accounting_module, "time", fake_time),
            mock.patch.object(accounting_module, "_process_cpu_seconds", lambda: cell["cpu"]),
        ):
            ledger = BackgroundLedger()
            for _ in range(300):
                ledger.note_wake("heartbeat", (), 0)
            wakes = ledger.snapshot()["wakes"]
            assert isinstance(wakes, dict)
            self.assertEqual(wakes["total"], 300)
            self.assertEqual(wakes["byReason"], {"heartbeat": 300})
            self.assertEqual(wakes["last60s"], 300)
            self.assertEqual(wakes["last60sByReason"], {"heartbeat": 300})
            recent = wakes["recent"]
            assert isinstance(recent, list)
            self.assertEqual(len(recent), 20)
            cell["mono"] += 61.0
            cell["wall"] += 61.0
            expired = ledger.snapshot()["wakes"]
            assert isinstance(expired, dict)
            self.assertEqual(expired["total"], 300)
            self.assertEqual(expired["last60s"], 0)
            self.assertEqual(expired["last60sByReason"], {})

    def test_wall_clock_jump_does_not_corrupt_window_expiry(self) -> None:
        cell, fake_time = self._clock(1_700_000_000.0, 40_000.0)
        with (
            mock.patch.object(accounting_module, "time", fake_time),
            mock.patch.object(accounting_module, "_process_cpu_seconds", lambda: cell["cpu"]),
        ):
            ledger = BackgroundLedger()
            ledger.record(OWNER_PROJECTION, 0.5, 0.5)
            # The wall clock leaps a day while monotonic stands still: buckets keyed by
            # monotonic seconds keep the window intact.
            cell["wall"] += 86_400.0
            owners = ledger.snapshot()["owners"]
            assert isinstance(owners, dict)
            projection = owners[OWNER_PROJECTION]
            assert isinstance(projection, dict)
            self.assertEqual(projection["cpuSecondsLast60s"], 0.5)
            self.assertEqual(projection["unitsLast60s"], 1)
            # Monotonic expiry still applies afterwards.
            cell["mono"] += 61.0
            reexpired = ledger.snapshot()["owners"]
            assert isinstance(reexpired, dict)
            aged = reexpired[OWNER_PROJECTION]
            assert isinstance(aged, dict)
            self.assertEqual(aged["cpuSecondsLast60s"], 0.0)

    def test_process_share_starts_at_the_preserved_window_boundary(self) -> None:
        cell, fake_time = self._clock(1_700_000_000.0, 50_000.0)
        with (
            mock.patch.object(accounting_module, "time", fake_time),
            mock.patch.object(
                accounting_module,
                "_process_cpu_seconds",
                lambda: cell["mono"] * 0.5,
            ),
        ):
            ledger = BackgroundLedger()
            for _ in range(3):
                cell["mono"] += 1.0
                cell["wall"] += 1.0
                ledger.record(OWNER_PROJECTION, 0.01, 0.01)
            share = ledger.snapshot()["processCpuShareLast60s"]
            assert isinstance(share, (int, float))
            self.assertAlmostEqual(float(share), 0.5)

    def test_process_window_discloses_sparse_boundary_and_cap_loss(self) -> None:
        cell, fake_time = self._clock(1_700_000_000.0, 1000.0)
        with (
            mock.patch.object(accounting_module, "time", fake_time),
            mock.patch.object(accounting_module, "_process_cpu_seconds", lambda: cell["cpu"]),
        ):
            ledger = BackgroundLedger()
            cell.update(mono=1090.0, cpu=30.0)
            sparse = ledger.snapshot()
            self.assertEqual(sparse["processCpuWindow"], {"spanSeconds": 90.0, "complete": False})
            sparse_share = sparse["processCpuShareLast60s"]
            assert isinstance(sparse_share, (int, float))
            self.assertAlmostEqual(sparse_share, 30.0 / 90.0)
            self.assertIn("incomplete window", describe_snapshot(sparse).splitlines()[-1])

            # Variable CPU rate distinguishes an exact cutoff from an older sample.
            cell.update(mono=1100.0, cpu=40.0)
            ledger.snapshot()
            cell.update(mono=1120.0, cpu=41.0)
            ledger.snapshot()
            cell.update(mono=1160.0, cpu=45.0)
            exact = ledger.snapshot()
            self.assertEqual(exact["processCpuWindow"], {"spanSeconds": 60.0, "complete": True})
            exact_share = exact["processCpuShareLast60s"]
            assert isinstance(exact_share, (int, float))
            self.assertAlmostEqual(exact_share, 5.0 / 60.0)
            self.assertNotIn("incomplete window", describe_snapshot(exact).splitlines()[-1])

            # A burst drops the real boundary. Keep the actual shorter span visible.
            for index in range(accounting_module.PROCESS_SAMPLES + 1):
                cell.update(mono=1160.0 + index / 100.0, cpu=45.0 + index / 10.0)
                ledger.record(OWNER_METRICS, 0.1, 0.01)
            lost = ledger.snapshot()
            window = lost["processCpuWindow"]
            assert isinstance(window, dict)
            self.assertFalse(window["complete"])
            self.assertLess(window["spanSeconds"], 60.0)
            self.assertIn("incomplete window", describe_snapshot(lost).splitlines()[-1])

    def test_partial_second_units_and_wakes_are_exact_or_honestly_incomplete(self) -> None:
        for count in (10, accounting_module.UNIT_SAMPLES + 11):
            with self.subTest(count=count):
                cell, fake_time = self._clock(1_700_000_000.0, 1000.25)
                with (
                    mock.patch.object(accounting_module, "time", fake_time),
                    mock.patch.object(
                        accounting_module, "_process_cpu_seconds", lambda cell=cell: cell["cpu"]
                    ),
                ):
                    ledger = BackgroundLedger()
                    for _ in range(count):
                        ledger.record(OWNER_METRICS, 1.0, 2.0)
                        ledger.note_wake("change", ("tasks",), 1, ("a/task.json",))
                    cell.update(mono=1060.1, wall=-10000.0)
                    snapshot = ledger.snapshot()
                    owners = snapshot["owners"]
                    assert isinstance(owners, dict)
                    owner = owners[OWNER_METRICS]
                    wakes = snapshot["wakes"]
                    assert isinstance(owner, dict) and isinstance(wakes, dict)
                    retained_units = min(count, accounting_module.UNIT_SAMPLES)
                    retained_wakes = min(count, accounting_module.WAKE_SAMPLES)
                    self.assertEqual(owner["cpuSecondsLast60s"], float(retained_units))
                    self.assertEqual(owner["wallSecondsLast60s"], float(retained_units * 2))
                    self.assertEqual(owner["unitsLast60s"], retained_units)
                    self.assertEqual(
                        owner["windowComplete"], count <= accounting_module.UNIT_SAMPLES
                    )
                    self.assertEqual(wakes["last60s"], retained_wakes)
                    self.assertEqual(wakes["last60sByReason"], {"change": retained_wakes})
                    self.assertEqual(
                        wakes["windowComplete"], count <= accounting_module.WAKE_SAMPLES
                    )
                    cell.update(mono=1060.3, wall=99999.0)
                    expired = ledger.snapshot()
                    expired_owners = expired["owners"]
                    expired_wakes = expired["wakes"]
                    assert isinstance(expired_owners, dict) and isinstance(expired_wakes, dict)
                    expired_owner = expired_owners[OWNER_METRICS]
                    assert isinstance(expired_owner, dict)
                    self.assertEqual(expired_owner["unitsLast60s"], 0)
                    self.assertEqual(expired_wakes["last60s"], 0)
                    self.assertTrue(expired_owner["windowComplete"])
                    self.assertTrue(expired_wakes["windowComplete"])

    def test_oldest_second_is_exact_not_whole(self) -> None:
        """F04: events before the cutoff leave even when their whole second has not."""

        cell, fake_time = self._clock(2_700_000_000.0, 60_000.2)
        with (
            mock.patch.object(accounting_module, "time", fake_time),
            mock.patch.object(accounting_module, "_process_cpu_seconds", lambda: cell["cpu"]),
        ):
            ledger = BackgroundLedger()
            # A first unit at the opening of its second, a second unit a minute later,
            # and the read half a second after that: the cutoff falls inside the first
            # unit's second, so only the later unit counts — a whole-second bucket
            # would still certify the expired first unit.
            ledger.record(OWNER_PROJECTION, 1.0, 1.0)
            cell["mono"] += 58.8
            cell["wall"] += 58.8
            ledger.record(OWNER_PROJECTION, 2.0, 2.0)
            cell["mono"] += 1.7
            cell["wall"] += 1.7
            projection = ledger.snapshot()["owners"]
            assert isinstance(projection, dict)
            projection = projection[OWNER_PROJECTION]
            assert isinstance(projection, dict)
            self.assertEqual(projection["units"], 2)
            self.assertAlmostEqual(projection["cpuSecondsTotal"], 3.0)
            self.assertAlmostEqual(projection["cpuSecondsLast60s"], 2.0)
            self.assertEqual(projection["unitsLast60s"], 1)
            self.assertTrue(projection["windowComplete"])

    def test_cap_drop_in_oldest_second_marks_the_window_incomplete(self) -> None:
        """F04: a dropped in-window sample in the oldest second is an honest incomplete window."""

        cell, fake_time = self._clock(3_700_000_000.0, 70_000.8)
        with (
            mock.patch.object(accounting_module, "time", fake_time),
            mock.patch.object(accounting_module, "_process_cpu_seconds", lambda: cell["cpu"]),
        ):
            ledger = BackgroundLedger()
            for _ in range(300):
                ledger.record(OWNER_PROJECTION, 0.001, 0.001)
            cell["mono"] += 10.0
            cell["wall"] += 10.0
            for _ in range(300):
                ledger.record(OWNER_PROJECTION, 0.001, 0.001)
            # A minute later the first burst sits in the oldest partially-covered second
            # and is still in the window; one more unit evicts one of its samples, so the
            # window covers the retained 299 plus the rest, and says it is incomplete.
            cell["mono"] += 49.9
            cell["wall"] += 49.9
            ledger.record(OWNER_PROJECTION, 1.0, 1.0)
            projection = ledger.snapshot()["owners"]
            assert isinstance(projection, dict)
            projection = projection[OWNER_PROJECTION]
            assert isinstance(projection, dict)
            self.assertEqual(projection["units"], 601)
            self.assertEqual(projection["droppedSamples"], 1)
            self.assertFalse(projection["windowComplete"])
            self.assertAlmostEqual(projection["cpuSecondsLast60s"], 0.299 + 0.3 + 1.0)
            self.assertEqual(projection["unitsLast60s"], 600)

    def test_evicted_expired_samples_keep_the_window_complete(self) -> None:
        """F04: evicting already-expired samples loses nothing and claims nothing."""

        cell, fake_time = self._clock(4_700_000_000.0, 80_000.0)
        with (
            mock.patch.object(accounting_module, "time", fake_time),
            mock.patch.object(accounting_module, "_process_cpu_seconds", lambda: cell["cpu"]),
        ):
            ledger = BackgroundLedger()
            for _ in range(600):
                ledger.record(OWNER_PROJECTION, 0.001, 0.001)
            cell["mono"] += 60.5
            cell["wall"] += 60.5
            ledger.record(OWNER_PROJECTION, 1.0, 1.0)
            projection = ledger.snapshot()["owners"]
            assert isinstance(projection, dict)
            projection = projection[OWNER_PROJECTION]
            assert isinstance(projection, dict)
            self.assertEqual(projection["units"], 601)
            self.assertEqual(projection["droppedSamples"], 1)
            self.assertTrue(projection["windowComplete"])
            self.assertAlmostEqual(projection["cpuSecondsLast60s"], 1.0)
            self.assertEqual(projection["unitsLast60s"], 1)


class OffloadAttributionTests(unittest.IsolatedAsyncioTestCase):
    """MIK-R81 rule 1 (F01): the worker thread's CPU is the unit, never the wait."""

    async def test_offload_attributes_the_worker_thread_not_the_waiter(self) -> None:
        ledger = BackgroundLedger()
        loop_ident = threading.get_ident()
        clocks: dict[int, float] = {}
        worker_idents: list[int] = []

        def _work() -> int:
            ident = threading.get_ident()
            worker_idents.append(ident)
            clocks[ident] = clocks.get(ident, 0.0) + 2.0
            return 17

        with mock.patch.object(
            accounting_module.time,
            "thread_time",
            side_effect=lambda: clocks.get(threading.get_ident(), 0.0),
        ):
            result = await accounting_module.offload(OWNER_PROJECTION, ledger, _work)
            clocks[loop_ident] = 1000.0  # Unrelated event-loop CPU cannot enter the worker account.
            await asyncio.sleep(0)
        owners = ledger.snapshot()["owners"]
        assert isinstance(owners, dict)
        projection = owners[OWNER_PROJECTION]
        assert isinstance(projection, dict)
        self.assertEqual(result, 17)
        self.assertEqual(projection["units"], 1)
        self.assertEqual(projection["cpuSecondsTotal"], 2.0)
        self.assertNotEqual(worker_idents[0], loop_ident)

    async def test_offload_records_failures_with_the_same_attribution(self) -> None:
        ledger = BackgroundLedger()
        clocks: dict[int, float] = {}

        def _boom() -> None:
            ident = threading.get_ident()
            clocks[ident] = clocks.get(ident, 0.0) + 7.0
            raise RuntimeError("worker blew up")

        with (
            mock.patch.object(
                accounting_module.time,
                "thread_time",
                side_effect=lambda: clocks.get(threading.get_ident(), 0.0),
            ),
            self.assertRaises(RuntimeError),
        ):
            await accounting_module.offload(OWNER_METRICS, ledger, _boom)
        owners = ledger.snapshot()["owners"]
        assert isinstance(owners, dict)
        metrics = owners[OWNER_METRICS]
        assert isinstance(metrics, dict)
        self.assertEqual(metrics["units"], 1)
        self.assertEqual(metrics["cpuSecondsTotal"], 7.0)

    async def test_offload_without_ledger_runs_unmeasured(self) -> None:
        seen: list[bool] = []

        def _work() -> str:
            seen.append(True)
            return "done"

        result = await accounting_module.offload(OWNER_METRICS, None, _work)
        self.assertEqual(result, "done")
        self.assertEqual(seen, [True])

    async def test_offload_cancellation_drains_the_worker_and_records_its_delta(self) -> None:
        ledger = BackgroundLedger()
        entered, release = threading.Event(), threading.Event()
        finished: list[bool] = []
        clocks: dict[int, float] = {}

        def _blocked() -> str:
            entered.set()
            assert release.wait(timeout=HANG_GUARD_SECONDS)
            clocks[threading.get_ident()] = 11.0
            finished.append(True)
            return "drained"

        with mock.patch.object(
            accounting_module.time,
            "thread_time",
            side_effect=lambda: clocks.get(threading.get_ident(), 0.0),
        ):
            task = asyncio.ensure_future(
                accounting_module.offload(OWNER_PROJECTION, ledger, _blocked)
            )
            try:
                await asyncio.to_thread(entered.wait, HANG_GUARD_SECONDS)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done(), "repeated cancellation orphaned the worker")
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(finished, [True])
        owners = ledger.snapshot()["owners"]
        assert isinstance(owners, dict)
        projection = owners[OWNER_PROJECTION]
        assert isinstance(projection, dict)
        self.assertEqual(projection["units"], 1)
        self.assertEqual(projection["cpuSecondsTotal"], 11.0)


class BackgroundThreadIsolationTests(unittest.IsolatedAsyncioTestCase):
    """MIK-R179 rule 2: background work never takes the thread a read runs on."""

    async def test_saturated_background_work_does_not_hold_the_default_executor(self) -> None:
        """Four blocked units fill the background pool while a read still runs on its own."""

        ledger = BackgroundLedger()
        entered = threading.Semaphore(0)
        release = threading.Event()
        names: list[str] = []

        def _blocked() -> None:
            names.append(threading.current_thread().name)
            entered.release()
            release.wait(HANG_GUARD_SECONDS)

        units = [
            asyncio.ensure_future(accounting_module.offload(OWNER_PROJECTION, ledger, _blocked))
            for _ in range(accounting_module.BACKGROUND_WORKERS)
        ]
        read = None
        try:
            for _ in units:
                self.assertTrue(
                    await asyncio.to_thread(entered.acquire, True, HANG_GUARD_SECONDS),
                    "background unit did not enter the saturated pool",
                )
            # Prove completion while every background thread is still parked. The deadline
            # only guards a hang; the ordering condition, rather than elapsed time, is the proof.
            read = asyncio.create_task(asyncio.to_thread(lambda: "read"))
            await async_wait_until(read.done, "read completed while the background pool was full")
            self.assertEqual(read.result(), "read")
            self.assertTrue(all(not unit.done() for unit in units))
        finally:
            release.set()
            await asyncio.gather(*units)
            if read is not None:
                await read

        self.assertTrue(names and all(name.startswith("ar-background") for name in names), names)
        owners = ledger.snapshot()["owners"]
        assert isinstance(owners, dict)
        projection = owners[OWNER_PROJECTION]
        assert isinstance(projection, dict)
        self.assertEqual(projection["units"], accounting_module.BACKGROUND_WORKERS)


class OwnerInventoryTests(unittest.TestCase):
    """MIK-R81 rule 1 (F02): every named owner has an accountable production path."""

    def test_every_owner_is_wired_to_a_record_site(self) -> None:

        serving = pathlib.Path(accounting_module.__file__).resolve().parent
        sources = [
            path.read_text(encoding="utf-8")
            for path in serving.rglob("*.py")
            if path.name != "background_accounting.py"
        ]
        constants = {
            value: name
            for name in dir(accounting_module)
            if name.startswith("OWNER_")
            for value in [getattr(accounting_module, name)]
            if isinstance(value, str)
        }
        missing = [
            name
            for name in accounting_module.BACKGROUND_OWNERS
            if not any(constants[name] in source for source in sources)
        ]
        self.assertEqual(missing, [])


class AccountingCostTests(unittest.TestCase):
    def test_recording_ten_thousand_units_costs_less_than_half_a_thread_cpu_second(self) -> None:
        ledger = BackgroundLedger()
        started = time.thread_time()
        for _ in range(10000):
            ledger.record(OWNER_PROJECTION, 0.001, 0.002)
        elapsed = time.thread_time() - started
        # The accounting is always on and costs less than 1 percent of a
        # processor (MIK-R81 rule 1): measure its CPU, excluding scheduler contention.
        self.assertLess(elapsed, 0.5)
        owners = ledger.snapshot()["owners"]
        assert isinstance(owners, dict)
        projection = owners[OWNER_PROJECTION]
        assert isinstance(projection, dict)
        self.assertEqual(projection["units"], 10000)
