"""L81 F25/F26/F28: operation-level host birth, lease, drain and shared truth."""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

from agents_remember.cli import paseo_warmup as intake
from agents_remember.cli import paseo_warmup_state as state
from agents_remember.cli.paseo_status import HostStartIdentity, LiveAgents
from agents_remember.cli.paseo_warmup_driver import WarmupDriver
from agents_remember.cli.paseo_warmup_run import WARMUP_MEMORY_FLOOR_BYTES
from agents_remember.cli.paseo_warmup_state import (
    WarmupTracker,
    claim_warmup_record,
    read_warmup_record,
)
from agents_remember.kernel.file_lock import try_file_lease
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving import _app_lifespan as serving
from agents_remember.serving._app_common import _ServingRuntime
from agents_remember.serving.background_accounting import OWNER_WARMUP, BackgroundLedger, offload
from agents_remember_test_support.testing.paseo_warmup import _BlockedWorkerResumes
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, async_wait_until


def _identity(pid: int) -> HostStartIdentity:
    raw = Path(f"/proc/{pid}/stat").read_text()
    return HostStartIdentity(
        pid,
        raw.rsplit(")", 1)[1].split()[19],
        Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    )


def _record(path: Path):
    record = read_warmup_record(path)
    assert record is not None
    return record


class _World:
    def __init__(self, root: Path) -> None:
        self.home = root / "home"
        self.home.mkdir()
        (root / "tasks").mkdir()
        self.config = McpRuntimeConfig(
            config_path=root / "settings.json",
            coordination_root=root,
            workspace_root=root,
            transcript_root=root / "transcripts",
        )
        self.tracker = WarmupTracker()
        self.driver = WarmupDriver(self.tracker)
        self.workers = _BlockedWorkerResumes()
        self.identity: HostStartIdentity | None = _identity(os.getpid())
        self.memory = WARMUP_MEMORY_FLOOR_BYTES * 2
        self.runtime = cast(
            _ServingRuntime,
            SimpleNamespace(
                launcher_catalog_prepare=None,
                config=self.config,
                background_ledger=BackgroundLedger(),
            ),
        )

    def patches(self) -> contextlib.ExitStack:
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(serving, "_warmup_tracker", self.tracker))
        stack.enter_context(
            mock.patch.object(
                serving, "resume_agent", lambda _cfg, agent, _receipt: self.workers.block(agent)
            )
        )
        stack.enter_context(
            mock.patch.object(serving, "read_available_memory", lambda: self.memory)
        )
        stack.enter_context(
            mock.patch.object(
                intake,
                "read_live_agents",
                lambda _: LiveAgents(
                    server_id="same-daemon",
                    agent_ids=frozenset({"a1", "a2", "a3"}),
                    start_identity=self.identity,
                    start_detail="isolated unknown identity",
                ),
            )
        )
        stack.enter_context(
            mock.patch.object(
                intake, "require_bridge_runtime", return_value=SimpleNamespace(home=self.home)
            )
        )

        def candidates(_config, _live, *, receipts):
            receipts.update({agent: {} for agent in ("a1", "a2", "a3")})
            return ["a1", "a2", "a3"]

        stack.enter_context(mock.patch.object(intake, "tree_agent_ids", side_effect=candidates))
        return stack

    async def step(self):
        claimed = await offload(
            OWNER_WARMUP,
            self.runtime.background_ledger,
            intake.check_host_once,
            self.config,
            tracker=self.tracker,
        )
        if claimed is not None:
            claimed[0]._memory_recheck_seconds = 0.01
        retired = await self.driver.noticed(
            claimed,
            lambda run, claim, note: asyncio.create_task(
                serving._drive_warmup_run(self.runtime, run, claim, note)
            ),
        )
        return claimed, retired


class WarmupOperationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_watch_intake_releases_admitted_claim_after_worker_drains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp))
            admitted, release = threading.Event(), threading.Event()
            claimed = []

            def intake_then_block(config):
                result = intake.check_host_once(config, tracker=world.tracker)
                assert result is not None
                claimed.append(result)
                admitted.set()
                if not release.wait(HANG_GUARD_SECONDS):
                    raise RuntimeError("test did not release the intake worker")
                return result

            with world.patches(), mock.patch.object(serving, "check_host_once", intake_then_block):
                watch = asyncio.create_task(serving._warmup_watch_loop(world.runtime))
                try:
                    await async_wait_until(admitted.is_set, "actual watch intake admission")
                    run, claim = claimed[0]
                    self.assertFalse(claim.lease.closed)
                    self.assertEqual(_record(claim.path)["state"], "running")
                    watch.cancel()
                    await asyncio.sleep(0)  # Deliver the first cancellation into the owned drain.
                    watch.cancel()
                    await asyncio.sleep(
                        0
                    )  # Deliver repeated cancellation while the worker is held.
                    self.assertFalse(watch.done())
                    self.assertFalse(claim.lease.closed)
                    self.assertEqual(world.workers.active, 0)
                finally:
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await watch
                self.assertTrue(claim.lease.closed)
                self.assertIsNone(world.tracker.claim)
                self.assertIsNone(world.tracker.run)
                self.assertIsNone(world.tracker.last_start_identity)
                self.assertEqual(_record(claim.path)["state"], "ended")
                self.assertIn("before the loading pass started", _record(claim.path)["reason"])
                self.assertEqual(run.loaded, 0)
                self.assertEqual(world.workers.peak, 0)
                world.workers.release.set()
                recovered, _ = await world.step()
                assert recovered is not None
                self.assertNotEqual(recovered[1].generation, claim.generation)
                await world.driver.aclose()
                self.assertTrue(recovered[1].lease.closed)

    async def test_restart_and_shutdown_drain_real_loads_before_successor_owns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp))
            with world.patches():
                first, _ = await world.step()
                assert first is not None
                await async_wait_until(lambda: world.workers.active == 2, "two native workers")
                # A separate sandbox host process has a different OS birth; serverId stays fixed.
                with subprocess.Popen(
                    [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
                ) as host:
                    world.identity = _identity(host.pid)
                    handoff = asyncio.create_task(world.step())
                    await async_wait_until(
                        lambda: world.tracker.retire_reason is None, "retirement decision"
                    )
                    await async_wait_until(
                        lambda: not world.driver._driving,
                        "handoff retirement entered before successor admission",
                    )
                    self.assertFalse(handoff.done())
                    self.assertEqual(world.workers.active, 2)
                    self.assertIsNone(
                        claim_warmup_record(world.home, "same-daemon", world.identity)
                    )
                    world.workers.release.set()
                    _, retired = await handoff
                    self.assertTrue(retired)
                    self.assertTrue(first[1].lease.closed)
                    assert world.tracker.last_record is not None
                    self.assertEqual(world.tracker.last_record["state"], "ended")
                    before = _record(first[1].path)
                    second, _ = await world.step()
                    assert second is not None
                    self.assertNotEqual(first[1].generation, second[1].generation)
                    first[1].publish({"state": "finished", "foreign": "old finalizer"})
                    self.assertNotIn("foreign", _record(second[1].path))
                    self.assertEqual(before["startIdentity"], first[1].start_identity.record())
                    await world.driver.aclose()
                    self.assertTrue(second[1].lease.closed)
                    assert world.tracker.last_record is not None
                    self.assertEqual(world.tracker.last_record["state"], "ended")
                    self.assertIsNone(world.tracker.last_start_identity)
                    assert host.stdin is not None
                    host.stdin.close()
                self.assertLessEqual(world.workers.peak, 2)
                self.assertEqual(world.workers.active, 0)

    async def test_memory_pause_progress_failure_priority_and_finished_truth_are_shared(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp))
            world.memory = 0
            with world.patches():
                claimed, _ = await world.step()
                assert claimed is not None
                try:
                    await async_wait_until(
                        lambda: world.tracker.status()["stoppedForMemory"], "actual pause"
                    )
                    observer = WarmupTracker(home=world.home)
                    self.assertIsNone(intake.check_host_once(world.config, tracker=observer))
                    assert observer.intake_detail is not None
                    self.assertIn("starts no load", observer.intake_detail)
                    local, foreign = world.tracker.status(), observer.status()
                    for field in (
                        "stoppedForMemory",
                        "line",
                        "pending",
                        "inFlight",
                        "agents",
                        "finished",
                    ):
                        self.assertEqual(local[field], foreign[field])
                    self.assertEqual(world.workers.active, 0)
                    self.assertIn("below6 GiB", foreign["agents"]["a3"]["reason"])
                    observer.promote(["a3"])
                    world.memory = WARMUP_MEMORY_FLOOR_BYTES * 2
                    await async_wait_until(
                        lambda: world.workers.active == 2, "loading after memory recovers"
                    )
                    await async_wait_until(
                        lambda: "a3" in observer.status()["inFlight"], "shared clicked agent"
                    )
                    self.assertEqual(world.workers.started[0], "a3")
                    self.assertEqual(observer.status()["inFlight"][0], "a3")
                    world.workers.release.set()
                    await async_wait_until(lambda: world.tracker.run is None, "completed pass")
                    self.assertFalse(observer.status()["running"])
                    self.assertIsNone(observer.status()["line"])
                    self.assertEqual(observer.status()["finished"], 3)
                    self.assertEqual(observer.status()["agents"]["a3"]["state"], "loaded")
                    self.assertTrue(
                        all(
                            row["state"] == "loaded" for row in observer.status()["agents"].values()
                        )
                    )
                    # Transient outage and unreadable identity do not create a second start.
                    world.tracker.note_host_away()
                    self.assertIsNone(intake.check_host_once(world.config, tracker=world.tracker))
                    world.identity = None
                    self.assertIsNone(intake.check_host_once(world.config, tracker=world.tracker))
                    assert world.tracker.host_detail is not None
                    self.assertIn("unknown identity", world.tracker.host_detail)
                finally:
                    world.workers.release.set()
                    await world.driver.aclose()

    async def test_prestart_driver_cancellation_releases_only_after_its_task_drains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp))
            with world.patches():
                claimed, _ = await world.step()
                assert claimed is not None
                self.assertEqual(world.workers.active, 0)
                await world.driver.aclose()
                self.assertTrue(claimed[1].lease.closed)
                self.assertEqual(world.workers.active, 0)
                self.assertEqual(_record(claimed[1].path)["state"], "ended")
                self.assertIsNone(world.tracker.last_start_identity)
                retry = intake.check_host_once(world.config, tracker=world.tracker)
                assert retry is not None
                retry[1].close()

    async def test_claim_and_progress_failure_release_and_retry_the_same_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp))
            world.memory = 0
            with world.patches():
                with mock.patch.object(
                    state, "write_warmup_record", side_effect=OSError("isolated disk failure")
                ):
                    self.assertIsNone(intake.check_host_once(world.config, tracker=world.tracker))
                self.assertIsNone(world.tracker.last_start_identity)
                claimed, _ = await world.step()
                assert claimed is not None
                world.tracker.retire_reason = "isolated early holder end"
                await world.driver.noticed(None, lambda *_: self.fail("must not start"))
                self.assertTrue(claimed[1].lease.closed)
                retry = intake.check_host_once(world.config, tracker=world.tracker)
                assert retry is not None
                self.assertNotEqual(claimed[1].generation, retry[1].generation)
                retry[1].close()


class LeaseLifetimeTests(unittest.TestCase):
    def test_holder_death_releases_without_removing_the_empty_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resource = Path(tmp) / "ar-warmup"
            script = (
                "from pathlib import Path; import sys; "
                "from agents_remember.kernel.file_lock import try_file_lease; "
                "lease=try_file_lease(Path(sys.argv[1]),'sandbox host loading'); "
                "assert lease is not None; print('held',flush=True); sys.stdin.read()"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", script, str(resource)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            assert child.stdout is not None and child.stdin is not None
            try:
                self.assertEqual(child.stdout.readline().strip(), "held")
                self.assertIsNone(try_file_lease(resource, "sandbox host loading"))
                child.kill()
                child.wait(timeout=HANG_GUARD_SECONDS)
                self.assertEqual(resource.with_name("ar-warmup.lock").read_bytes(), b"")
                lease = try_file_lease(resource, "sandbox host loading")
                assert lease is not None
                lease.close()
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=HANG_GUARD_SECONDS)
                child.stdin.close()
                child.stdout.close()
