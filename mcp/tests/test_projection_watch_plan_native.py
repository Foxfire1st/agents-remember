"""Real awatch integration over the scratch D561 producer and watcher."""

import asyncio
import contextlib
import tempfile
import time
import unittest
from itertools import pairwise
from pathlib import Path

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving import change_watcher as m


class Trace:
    def __init__(self) -> None:
        self.health: list[bool] = []
        self.notifications: list[tuple[frozenset[m.ProjectionDomain], frozenset[str]]] = []

    def set_watcher_healthy(self, healthy: bool) -> None:
        self.health.append(healthy)

    def notify_change(
        self,
        domains: frozenset[m.ProjectionDomain] = m.ALL_PROJECTION_DOMAINS,
        paths: frozenset[str] = frozenset(),
    ) -> None:
        self.notifications.append((domains, paths))


async def until(
    check,
    # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
    timeout=8,
):
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.01)


class NativeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.task = self.root / "tasks/repo/master"
        self.task.mkdir(parents=True)
        (self.task / "task.json").write_text("{}")
        (self.task / "notes/reports").mkdir(parents=True)
        self.config = McpRuntimeConfig(
            config_path=self.root / "settings.json",
            coordination_root=self.root,
            workspace_root=self.root,
            transcript_root=self.root / "logs/mcp",
        )
        self.pacer = Trace()
        self.watcher = m.ProjectionInputWatcher(self.config, refresh_seconds=30)
        self.runner = asyncio.create_task(self.watcher.run(self.pacer))

    async def asyncTearDown(self):
        self.runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.runner
        self.tmp.cleanup()

    async def test_real_registration_loop_progress_and_report_quiet(self):
        ticks = []

        async def beat():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.05)

        heart = asyncio.create_task(beat())
        try:
            # load-independent: parks the task on the real condition before the assertion; nothing reads the clock
            await asyncio.sleep(0.15)
            self.assertFalse(self.pacer.health)  # no synthetic early registration signal
            await until(lambda: True in self.pacer.health)
        finally:
            heart.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heart
        self.assertGreater(len(ticks), 5)
        self.assertLess(max(b - a for a, b in pairwise(ticks)), 0.5)
        before = len(self.pacer.notifications)
        notes = self.task / "notes/reports"
        for i in range(10):
            (notes / f"progress-{i}.md").write_text("report only")
        (notes / "evidence").mkdir()
        (notes / "evidence/log.txt").write_text("inert")
        # load-independent: parks the task on the real condition before the assertion; nothing reads the clock
        await asyncio.sleep(0.4)
        self.assertEqual(len(self.pacer.notifications), before)
        self.assertEqual(self.pacer.health, [True])

    async def test_real_late_task_receipt_and_contract_are_reconciled(self):
        await until(lambda: True in self.pacer.health)
        child = self.root / "tasks/repo/new-task"
        holder = child / "notes/reports/paseo-native-executions"
        holder.mkdir(parents=True)
        doc = child / "task.json"
        doc.write_text("{}")
        receipt = holder / "receipt.json"
        receipt.write_text("{}")
        enclosure = child / "enclosures/leaf"
        enclosure.mkdir(parents=True)
        contract = enclosure / "series-contract.md"
        contract.write_text("contract")
        await until(lambda: len(self.pacer.health) >= 3 and self.pacer.health[-1] is True)
        self.assertIn(False, self.pacer.health)
        self.assertTrue(
            any(
                domains == m.ALL_PROJECTION_DOMAINS and not paths
                for domains, paths in self.pacer.notifications
            )
        )
        plan = await asyncio.to_thread(m.projection_watch_plan, self.config)
        files = set(await asyncio.to_thread(lambda: list(plan.task_inputs.input_files())))
        self.assertTrue({doc, receipt, contract} <= files)
        self.assertNotIn(self.root / "tasks", set(plan.roots) - set(plan.shallow))
        count = len(self.pacer.notifications)
        contract.write_text("changed contract")
        await until(lambda: len(self.pacer.notifications) > count)
        self.assertTrue(
            any(
                str(contract) in args[1]
                for args in self.pacer.notifications[count:]
                if len(args) > 1
            )
        )


def config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs/mcp",
    )


class TopologyTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_named_recreated_directory_still_replaces_the_watch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "tasks/repo/master"
            folder.mkdir(parents=True)
            document = folder / "task.json"
            document.write_text("{}")
            cfg = config(root)
            before = m.projection_watch_plan(cfg)
            document.unlink()
            folder.rmdir()
            folder.mkdir()
            document.write_text("{}")
            self.assertEqual(before, m.projection_watch_plan(cfg))
            pacer = Trace()
            health = pacer.health
            replan = asyncio.Event()
            stop = asyncio.Event()
            replan.set()
            replace_watch = asyncio.Event()
            replace_watch.set()
            owner = m.ProjectionInputWatcher(cfg, refresh_seconds=100)
            await asyncio.wait_for(
                owner._stop_when_roots_change(before, stop, replan, replace_watch, pacer),
                2,  # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            )
            self.assertTrue(stop.is_set())
            self.assertEqual(health, [False])

    async def test_periodic_equal_plan_keeps_health_until_real_topology_event(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tasks").mkdir()
            cfg = config(root)
            plan = m.projection_watch_plan(cfg)
            pacer = Trace()
            health = pacer.health
            replan = asyncio.Event()
            stop = asyncio.Event()
            replace_watch = asyncio.Event()
            owner = m.ProjectionInputWatcher(cfg, refresh_seconds=0.01)
            task = asyncio.create_task(
                owner._stop_when_roots_change(plan, stop, replan, replace_watch, pacer)
            )
            try:
                # load-independent: parks the task on the real condition before the assertion; nothing reads the clock
                await asyncio.sleep(0.05)
                self.assertFalse(task.done())
                self.assertEqual(health, [])
                replace_watch.set()
                replan.set()
                await asyncio.wait_for(
                    task,
                    # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                    2,
                )
                self.assertEqual(health, [False])
            finally:
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    async def test_ordinary_marker_rewrite_replans_without_replacing_equal_watch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "tasks/repo/master"
            folder.mkdir(parents=True)
            marker = folder / "task.json"
            marker.write_text("{}")
            cfg = config(root)
            plan = m.projection_watch_plan(cfg)
            marker.write_text('{"changed": true}')
            assert m.watchfiles is not None
            paths, _, _, topology, force = m._judge_planned_changes(
                {(m.watchfiles.Change.modified, str(marker))}, plan
            )
            self.assertTrue(topology)
            self.assertFalse(force)
            self.assertEqual(paths, {str(marker)})
            self.assertEqual(plan, m.projection_watch_plan(cfg))
            pacer = Trace()
            health = pacer.health
            replan = asyncio.Event()
            replace_watch = asyncio.Event()
            stop = asyncio.Event()
            replan.set()
            owner = m.ProjectionInputWatcher(cfg, refresh_seconds=100)
            task = asyncio.create_task(
                owner._stop_when_roots_change(plan, stop, replan, replace_watch, pacer)
            )
            try:
                # load-independent: parks the task on the real condition before the assertion; nothing reads the clock
                await asyncio.sleep(0.05)
                self.assertFalse(task.done())
                self.assertFalse(stop.is_set())
                self.assertEqual(health, [])
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

    async def test_marker_becoming_directory_requests_full_topology_reconciliation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "tasks/repo/master"
            folder.mkdir(parents=True)
            marker = folder / "task.json"
            marker.write_text("{}")
            plan = m.projection_watch_plan(config(root))
            marker.unlink()
            marker.mkdir()
            assert m.watchfiles is not None
            paths, _, _, topology, replace_watch = m._judge_planned_changes(
                {(m.watchfiles.Change.modified, str(marker))}, plan
            )
            self.assertTrue(topology)
            self.assertTrue(replace_watch)
            self.assertEqual(paths, set())


if __name__ == "__main__":
    unittest.main()
