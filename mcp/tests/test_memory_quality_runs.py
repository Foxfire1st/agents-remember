"""Bounded background run-registry tests (L15-R7, gate-repair coverage)."""

from __future__ import annotations

import time
import unittest
from unittest import mock

from agents_remember.application import memory_quality_runs as runs
from agents_remember.application import memory_tools


class MemoryQualityRunRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(runs._registry.clear)

    def _unique_key(self, label: str) -> str:
        return f"{label}:{id(self)}"

    def _poll_until_settled(self, run_id: str) -> dict:
        deadline = time.monotonic() + 5
        envelope = None
        while time.monotonic() < deadline:
            envelope = runs.poll_quality_run(run_id)
            if envelope is not None and envelope["status"] != "running":
                return envelope
            time.sleep(0.01)
        raise AssertionError(f"run {run_id} did not settle: {envelope}")

    def test_start_poll_completed_and_unknown(self) -> None:
        def _work() -> dict:
            time.sleep(0.05)
            return {"operation": "memory_quality_check", "ok": True}

        run_id, status = runs.start_quality_run(self._unique_key("poll"), _work)
        self.assertEqual(status, "started")
        envelope = self._poll_until_settled(run_id)
        self.assertEqual(envelope["status"], "completed")
        self.assertTrue(envelope["ok"])
        self.assertIsNone(runs.poll_quality_run("missing-run"))

    def test_poll_until_settled_raises_when_the_run_never_settles(self) -> None:
        runs._registry["never"] = runs._QualityRun(run_id="never", key="k", status="running")
        with self.assertRaisesRegex(AssertionError, "did not settle"):
            self._poll_until_settled("never")

    def test_failed_run_reports_the_error(self) -> None:
        def _boom() -> dict:
            raise RuntimeError("probe failure")

        run_id, _status = runs.start_quality_run(self._unique_key("fail"), _boom)
        envelope = self._poll_until_settled(run_id)
        self.assertEqual(envelope["status"], "failed")
        self.assertIn("probe failure", envelope["error"])

    def test_single_flight_while_a_run_is_active(self) -> None:
        def _slow() -> dict:
            time.sleep(0.2)
            return {"ok": True}

        run_id, _status = runs.start_quality_run(self._unique_key("single"), _slow)
        again_id, again_status = runs.start_quality_run(self._unique_key("single"), _slow)
        self.assertEqual(again_id, run_id)
        self.assertEqual(again_status, "running")

    def test_registry_stays_bounded(self) -> None:
        def _quick() -> dict:
            return {"ok": True}

        with mock.patch.object(runs, "MAX_QUALITY_RUNS", 2):
            runs.start_quality_run(self._unique_key("bounded-1"), _quick)
            time.sleep(0.05)
            runs.start_quality_run(self._unique_key("bounded-2"), _quick)
            time.sleep(0.05)
            third_id, _status = runs.start_quality_run(self._unique_key("bounded-3"), _quick)
            time.sleep(0.05)
            self.assertLessEqual(len(runs._registry), 2)
        envelope = runs.poll_quality_run(third_id)
        assert envelope is not None
        self.assertEqual(envelope["status"], "completed")

    def test_ttl_eviction_drops_stale_completed_runs(self) -> None:
        stale = runs._QualityRun(
            run_id="stale",
            key="k-stale",
            status="completed",
            completed_at=time.monotonic() - runs.QUALITY_RUN_TTL_SECONDS - 10,
        )
        fresh = runs._QualityRun(
            run_id="fresh", key="k-fresh", status="completed", completed_at=time.monotonic()
        )
        runs._registry.update({"stale": stale, "fresh": fresh})
        runs._evict_locked()
        self.assertNotIn("stale", runs._registry)
        self.assertIn("fresh", runs._registry)

    def test_eviction_with_no_completed_runs_is_a_noop(self) -> None:
        with mock.patch.object(runs, "MAX_QUALITY_RUNS", 1):
            running = runs._QualityRun(run_id="r1", key="k", status="running")
            runs._registry["r1"] = running
            runs._evict_locked()  # must not raise; nothing completed to evict
            self.assertIn("r1", runs._registry)


class MemoryQualityApplicationWrapperTests(unittest.TestCase):
    """The start/poll application wrappers (memory_tools L15-R7)."""

    def setUp(self) -> None:
        self.addCleanup(runs._registry.clear)

    def test_start_and_poll_wrappers_drive_a_background_run(self) -> None:
        captured: dict = {}

        def fake_run(config, **kwargs):
            captured["kwargs"] = kwargs
            time.sleep(0.2)  # slow enough for the first poll to see "running"
            return {"operation": "memory_quality_check", "ok": True, "checks": {}}

        with mock.patch.object(
            memory_tools, "_run_quality_check", side_effect=fake_run
        ) as run_check:
            started = memory_tools.start_memory_quality_check_run(
                mock.Mock(), repo_id="r", checks=["style.update_history.history_order"]
            )
            self.assertTrue(started["ok"])
            self.assertEqual(started["status"], "started")
            run_id = started["runId"]
            deadline = time.monotonic() + 5
            envelope = None
            while (envelope is None or envelope["status"] == "running") and (
                time.monotonic() < deadline
            ):
                envelope = memory_tools.poll_memory_quality_check_run("r", run_id)
                if envelope["status"] == "running":
                    time.sleep(0.01)
            assert envelope is not None
            self.assertEqual(envelope["status"], "completed")
            self.assertTrue(envelope["ok"])
            run_check.assert_called_once()
            self.assertEqual(
                captured["kwargs"],
                {
                    "repo_id": "r",
                    "checks": ["style.update_history.history_order"],
                    "detail_limit": 50,
                    "contract_path": None,
                },
            )

    def test_poll_reports_an_unknown_run_as_run_not_found(self) -> None:
        envelope = memory_tools.poll_memory_quality_check_run("r", "no-such-run")
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["status"], "run-not-found")
        self.assertEqual(envelope["runId"], "no-such-run")

    def test_poll_wraps_running_and_failed_envelopes_with_ok(self) -> None:
        # Deterministic registry entries (no threads): the running/failed wrapper
        # branch of poll_memory_quality_check_run must carry ok=True.
        runs._registry["r1"] = runs._QualityRun(run_id="r1", key="k", status="running")
        running = memory_tools.poll_memory_quality_check_run("r", "r1")
        self.assertEqual(running["status"], "running")
        self.assertTrue(running["ok"])
        runs._registry["r2"] = runs._QualityRun(run_id="r2", key="k", status="failed", error="boom")
        failed = memory_tools.poll_memory_quality_check_run("r", "r2")
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["ok"])
        self.assertIn("boom", failed["error"])

    def test_start_key_scopes_contract_path_and_checks(self) -> None:
        # _quality_run_key branches: contract_path set vs official, checks set vs empty.
        with mock.patch.object(
            memory_tools, "_run_quality_check", return_value={"ok": True, "checks": {}}
        ):
            first = memory_tools.start_memory_quality_check_run(
                mock.Mock(),
                repo_id="r",
                checks=["a"],
                contract_path="/leaf/contract.yaml",
            )
            second = memory_tools.start_memory_quality_check_run(mock.Mock(), repo_id="r")
            self.assertNotEqual(first["runId"], second["runId"])
