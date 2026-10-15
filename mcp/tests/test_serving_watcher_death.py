"""MIK-R81 rule 5 (F10): refresher death beside an established native watch.

The added coverage faults the root refresher only after the native watch is
established (not initial discovery) and executes the death matrix: a dying
refresher reconciles every domain and rethrows after the drain, an abnormal
native-watch child rides the standard outage path, the next generation picks up
a new root, and a notification queued during the outage still arrives.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving import change_watcher as watcher_module
from agents_remember.serving.change_watcher import (
    ChangePacer,
    ProjectionInputWatcher,
)
from agents_remember.serving.projections.projection_inputs import (
    ALL_PROJECTION_DOMAINS,
    ProjectionDomain,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, async_wait_until


def _config(tmp: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=tmp / "settings.json",
        coordination_root=tmp,
        workspace_root=tmp,
        transcript_root=tmp / "logs" / "mcp",
    )


class _DeathMatrix:
    """Shared state for the abnormal-child death-matrix doubles (F10)."""

    def __init__(self, added: Path) -> None:
        self.added = added
        self.real_roots = watcher_module.projection_input_roots
        self.derivations = 0
        self.awatch_entries: list[list[Path]] = []
        self.failed_once = False

    def growing_roots(self, cfg: McpRuntimeConfig) -> list[Path]:
        self.derivations += 1
        roots = self.real_roots(cfg)
        if self.derivations > 2 and self.added.exists():
            return [*roots, self.added]
        return roots

    async def flaky_awatch(self, *roots: Path, **kwargs: object):
        self.awatch_entries.append(list(roots))
        if not self.failed_once:
            self.failed_once = True
            yield set()
            raise RuntimeError("native watch child died abnormally")
        stop = kwargs.get("stop_event")
        while True:
            if isinstance(stop, asyncio.Event) and stop.is_set():
                return
            yield set()
            await asyncio.sleep(0.01)


class _WakeTrace:
    def __init__(self) -> None:
        self.health: list[bool] = []
        self.notifications: list[tuple[frozenset[ProjectionDomain], frozenset[str]]] = []

    def set_watcher_healthy(self, healthy: bool) -> None:
        self.health.append(healthy)

    def notify_change(
        self,
        domains: frozenset[ProjectionDomain] = ALL_PROJECTION_DOMAINS,
        paths: frozenset[str] = frozenset(),
    ) -> None:
        self.notifications.append((domains, paths))


class _CommonFailureMatrix:
    """One native-generation failure driver, with real roots, pacer and default retry."""

    def __init__(self, root: Path, fault: str, case: unittest.TestCase) -> None:
        (root / "tasks").mkdir()
        self.config = _config(root)
        self.watcher = ProjectionInputWatcher(self.config)
        self.pacer = ChangePacer(interval=1)
        self.fault, self.case = fault, case
        self.recovered = asyncio.Event()
        self.retry_stamps: list[float | None] = []
        self.registered_roots: list[tuple[Path, ...]] = []
        self.real_roots = watcher_module.projection_input_roots
        self.real_sleep = asyncio.sleep
        self.attempts = {"discovery": 0, "native": 0, "refresh": 0}
        self.clock = watcher_module.time.monotonic()
        self.runner: asyncio.Task[None] | None = None
        self.new_root = root / "logs" / "providers" / "status"

    def roots(self, cfg: McpRuntimeConfig) -> list[Path]:
        self.attempts["discovery"] += 1
        if self.fault == "discovery" and self.attempts["discovery"] <= 2:
            raise OSError("root discovery failure")
        return self.real_roots(cfg)

    async def refresh(self, _current: list[Path], stop: asyncio.Event) -> None:
        self.attempts["refresh"] += 1
        if self.fault in ("refresh", "cancel", "return") and self.attempts["refresh"] <= 2:
            if self.fault == "cancel":
                raise asyncio.CancelledError
            if self.fault == "return":
                return
            raise OSError("root refresh failure")
        await stop.wait()

    async def native(self, *paths: Path, stop_event: asyncio.Event, **kwargs: object):
        self.attempts["native"] += 1
        self.registered_roots.append(paths)
        self.case.assertTrue(kwargs["yield_on_timeout"])
        self.case.assertFalse(kwargs["force_polling"])
        await self.real_sleep(0)
        if self.fault in ("registration", "iteration") and self.attempts["native"] <= 2:
            raise OSError(f"native {self.fault} failure")
        if stop_event.is_set():
            return
        yield set()
        if len(self.retry_stamps) == 2:
            assert watcher_module.watchfiles is not None
            yield {(watcher_module.watchfiles.Change.modified, str(self.new_root / "current.json"))}
            self.recovered.set()
        await stop_event.wait()

    async def retry_sleep(self, seconds: float) -> None:
        if asyncio.current_task() is not self.runner:
            await self.real_sleep(seconds)
            return
        self.case.assertEqual(seconds, 30.0)
        self.case.assertFalse(self.pacer.watcher_healthy)
        self.case.assertIsNotNone(self.pacer.watcher_down_since)
        self.retry_stamps.append(self.pacer.watcher_down_since)
        self.clock += seconds
        self.pacer.notify_change(frozenset({watcher_module.ProjectionDomain.TASKS}))
        wake = await self.pacer.wait()
        self.case.assertEqual(wake.reason, "interval")
        self.case.assertEqual(wake.domains, ALL_PROJECTION_DOMAINS)
        self.case.assertIn(watcher_module.ProjectionDomain.TASKS, self.pacer._pending_domains)
        if len(self.retry_stamps) == 2:
            self.new_root.mkdir(parents=True)
            (self.new_root / "current.json").write_text("{}")
        await self.real_sleep(0)

    async def run(self) -> None:
        with (
            mock.patch.object(watcher_module, "projection_input_roots", self.roots),
            mock.patch.object(self.watcher, "_stop_when_roots_change", self.refresh),
            mock.patch.object(watcher_module.watchfiles, "awatch", self.native),
            mock.patch.object(watcher_module.asyncio, "sleep", self.retry_sleep),
            mock.patch.object(
                watcher_module, "time", SimpleNamespace(monotonic=lambda: self.clock)
            ),
            mock.patch.object(watcher_module, "_wall_now", lambda: 1000.0 + self.clock),
        ):
            self.runner = asyncio.create_task(self.watcher.run(self.pacer))
            try:
                await asyncio.wait_for(self.recovered.wait(), HANG_GUARD_SECONDS)
                self.case.assertEqual(len(self.retry_stamps), 2)
                self.case.assertEqual(self.retry_stamps[0], self.retry_stamps[1])
                self.case.assertTrue(self.pacer.watcher_healthy)
                self.case.assertIsNone(self.pacer.watcher_down_since)
                self.case.assertIn(self.new_root, self.registered_roots[-1])
                self.case.assertIn(
                    str(self.new_root / "current.json"), self.pacer._pending_distinct_set
                )
                self.case.assertIn(
                    watcher_module.ProjectionDomain.TASKS, self.pacer._pending_domains
                )
            finally:
                self.runner.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.runner


class RefresherDeathBesideLiveWatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_common_failure_matrix_keeps_down_since_and_default_thirty_second_retry(
        self,
    ) -> None:
        for fault in ("discovery", "registration", "iteration", "refresh", "cancel", "return"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                await _CommonFailureMatrix(Path(tmp), fault, self).run()

    async def test_refresher_death_beside_live_watch_reconciles_and_raises(self) -> None:
        """The refresher dying beside an established watch reconciles, then raises.

        The death is recorded, the native watch stops, the refresher drains, then the
        failure rethrows after reconciling every domain -- never a silent stay-live
        on possibly-obsolete roots.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tasks").mkdir()
            config = _config(root)
            pacer = _WakeTrace()
            watcher = ProjectionInputWatcher(config, refresh_seconds=0.01)
            derived = 0
            real_roots = watcher_module.projection_input_roots

            def dying_roots(cfg: McpRuntimeConfig) -> list[Path]:
                nonlocal derived
                derived += 1
                if derived > 1:
                    raise OSError("refresher blew up beside a live watch")
                return real_roots(cfg)

            async def idle_awatch(*roots: Path, stop_event: asyncio.Event, **kwargs: object):
                while not stop_event.is_set():
                    await asyncio.sleep(0.01)
                    yield set()
                yield set()

            with (
                mock.patch.object(watcher_module, "projection_input_roots", dying_roots),
                mock.patch.object(watcher_module.watchfiles, "awatch", idle_awatch),
            ):
                async with asyncio.timeout(HANG_GUARD_SECONDS):
                    with self.assertRaisesRegex(OSError, "live watch"):
                        await watcher._watch_once([root / "tasks"], pacer, reconcile=False)
            self.assertGreaterEqual(derived, 2)
            self.assertIn(True, pacer.health)
            notified = pacer.notifications
            self.assertTrue(
                any(domains == ALL_PROJECTION_DOMAINS for domains, _paths in notified),
                f"death reconciles every domain, saw {notified}",
            )

    async def test_abnormal_watch_child_death_recovers_with_new_root(self) -> None:
        """Death matrix: abnormal child, new-root recovery, drained notification.

        An abnormal native-watch death beside a live refresher rides the standard
        outage path; the next generation picks up a new root, marked healthy again,
        and a notification queued during the outage still arrives (drained, not lost).
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / "tasks"
            tasks.mkdir()
            added = root / "added"
            config = _config(root)
            matrix = _DeathMatrix(added)
            awatch_entries = matrix.awatch_entries

            pacer = ChangePacer(interval=1, heartbeat=15)
            watcher = ProjectionInputWatcher(config, refresh_seconds=0.05)
            with (
                mock.patch.object(watcher_module, "projection_input_roots", matrix.growing_roots),
                mock.patch.object(watcher_module.watchfiles, "awatch", matrix.flaky_awatch),
            ):
                task = asyncio.create_task(watcher.run(pacer))
                try:
                    async with asyncio.timeout(HANG_GUARD_SECONDS):
                        await async_wait_until(
                            lambda: pacer.watcher_down_since is not None,
                            "watcher outage after abnormal child death",
                        )
                        self.assertFalse(pacer.watcher_healthy)
                        # The outage reconciles every domain on the retry path.
                        wake = await pacer.wait()
                        pacer.acknowledge(wake)
                        self.assertEqual(wake.domains, ALL_PROJECTION_DOMAINS)
                        # A notification queued while the watch is down is not lost.
                        pacer.notify_change(frozenset({ProjectionDomain.TASKS}))
                        # A new root appears while the watch is down.
                        added.mkdir()
                        (added / "task-doc.json").write_text("{}", encoding="utf-8")
                        await async_wait_until(
                            lambda: pacer.watcher_down_since is None,
                            "watcher recovery with the new root",
                        )
                        self.assertTrue(pacer.watcher_healthy)
                        # The refresher's next derivation sees the moved root set and
                        # restarts the watch; the new generation covers the new root.
                        await async_wait_until(
                            lambda: len(awatch_entries) > 2,
                            "watch restarts on the moved root set",
                        )
                        self.assertTrue(pacer.watcher_healthy)
                        self.assertTrue(
                            any(added in entry for entry in awatch_entries),
                            f"recovered generation watches the new root: {awatch_entries}",
                        )
                        # The drained notification arrives after recovery.
                        while True:
                            wake = await pacer.wait()
                            pacer.acknowledge(wake)
                            if ProjectionDomain.TASKS in wake.domains:
                                break
                finally:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
