"""260712-PTS-L3: change-driven projection pacing.

Covers the watcher module's three layers plus the projector integration:

* the derived watch-root list (R1) and the input-event filter (self-trigger safety);
* the :class:`ChangePacer` scheduling core (debounce, max-delay bound, interval floor,
  heartbeat, degraded mode) -- deterministic via the pure ``_next_deadline``;
* the projector run loop with an injected watcher (R7): a quiet world projects only at
  heartbeat cadence, a write burst coalesces to a bounded projection count, a single
  write projects within the debounce bound, and watcher failure degrades loudly to
  fixed-interval ticking (fail-open);
* one real-``watchfiles`` end-to-end pass (inotify -> debounce -> projection).
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.serving import change_watcher as change_watcher_module
from agents_remember.serving.change_watcher import (
    ChangePacer,
    ProjectionInputWatcher,
    WakeTarget,
    is_projection_input_event,
    projection_domains_for_paths,
    projection_input_roots,
)
from agents_remember.serving.projections.projection_inputs import ProjectionDomain
from agents_remember.serving.projector import ProjectionCadence, ProjectionRefreshers, Projector


def _config(tmp: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=tmp / "settings.json",
        coordination_root=tmp,
        workspace_root=tmp,
        transcript_root=tmp / "logs" / "mcp",
    )


class ProjectionInputRootsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_empty_tree_yields_no_roots(self) -> None:
        self.assertEqual(projection_input_roots(_config(self.tmp)), [])

    def test_derives_the_projection_input_surfaces_and_nothing_else(self) -> None:
        observer = self.tmp / "logs" / "observer"
        expected = [
            self.tmp / "tasks",
            observer / "lifecycles",
            observer / "workspace",
            observer / "drift",
            self.tmp / "logs" / "providers" / "status",
            self.tmp / "logs" / "providers" / "setup",
            self.tmp / "temp" / "worktree-start",
            self.tmp / "temp" / "tool-reports",
        ]
        for path in expected:
            path.mkdir(parents=True)
        # A worktree group: NOTHING under worktrees/ is a watch root. Its provider-runtime
        # holds live container data (Postgres/grepai) that is unreadable to the daemon user
        # and churns on every container write -- watching it crashed the watcher and would
        # have re-projected on WAL writes. provider-state.json changes are heartbeat-covered.
        runtime = self.tmp / "worktrees" / "repo" / "group-ar" / "provider-runtime"
        runtime.mkdir(parents=True)
        checkout = self.tmp / "worktrees" / "repo" / "group-ar" / "src"
        checkout.mkdir(parents=True)
        # Non-input observer dirs stay unwatched (metrics churn, quarantines).
        (observer / "providers").mkdir(parents=True)
        (observer / "quarantine-1").mkdir(parents=True)

        roots = projection_input_roots(_config(self.tmp))
        self.assertEqual(roots, expected)
        # Regression guard: no watch root may ever fall under worktrees/ (container data).
        self.assertFalse(any((self.tmp / "worktrees") in root.parents for root in roots))
        self.assertNotIn(runtime, roots)
        self.assertNotIn(checkout, roots)
        self.assertNotIn(observer / "providers", roots)

    def test_missing_surfaces_are_skipped_until_they_exist(self) -> None:
        (self.tmp / "tasks").mkdir()
        self.assertEqual(projection_input_roots(_config(self.tmp)), [self.tmp / "tasks"])


class InputEventFilterTests(unittest.TestCase):
    def test_projection_inputs_pass(self) -> None:
        for path in (
            "/c/logs/observer/lifecycles/L1/events.jsonl",
            "/c/logs/observer/lifecycles/L1/heartbeat.json",
            "/c/logs/observer/lifecycles/L1/gates.jsonl",
            "/c/logs/observer/workspace/gates.jsonl",
            "/c/logs/observer/workspace/operator-inbox.jsonl",
            "/c/logs/observer/workspace/attention-dismissals.jsonl",
            "/c/logs/observer/workspace/expectation-rows.jsonl",
            "/c/logs/observer/drift/repo.json",
            "/c/tasks/agents-remember/260712_x/task.json",
        ):
            self.assertTrue(is_projection_input_event(path), path)

    def test_self_writes_and_non_input_churn_are_dropped(self) -> None:
        for path in (
            # The projection's own per-tick outputs: letting these through would make
            # every tick re-wake the next one (an infinite debounce-paced loop).
            "/c/logs/observer/latest-state.json",
            "/c/logs/observer/latest-metrics.json",
            "/c/logs/observer/latest-state.json.01HZX.tmp",
            "/c/tasks/agents-remember/task.json.01HZX.tmp",
            # workspace/ non-inputs: raw river + cursor/locks, agent-notifier heartbeat.
            "/c/logs/observer/workspace/events.jsonl",
            "/c/logs/observer/workspace/events.cursor.json",
            "/c/logs/observer/workspace/events.lock",
            # Created "a+b" by every inbox access (incl. each tick's read_agent_pickups):
            # its boot-time creation must not cost a spurious change-tick (review F1).
            # Named by durable_store.lock_path_for, i.e. the whole log name plus ".lock".
            "/c/logs/observer/workspace/operator-inbox.jsonl.lock",
            # The same rule outside workspace/: gates.jsonl (and its lock) exist once per
            # lifecycle, which a workspace-scoped name list could not have excluded.
            "/c/logs/observer/lifecycles/L1/gates.jsonl.lock",
            "/c/logs/observer/workspace/supervisor-heartbeat.json",
            "/c/logs/observer/workspace/.events.cursor.json.123.tmp",
        ):
            self.assertFalse(is_projection_input_event(path), path)

    def test_lifecycle_events_are_not_confused_with_the_workspace_river(self) -> None:
        # Same basename as the river, different (input) directory.
        self.assertTrue(is_projection_input_event("/c/logs/observer/lifecycles/L1/events.jsonl"))


class ProjectionDomainMappingTests(unittest.TestCase):
    def test_paths_map_to_their_reader_domains_and_coalesce(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            paths = {
                str(tmp / "tasks" / "repo" / "leaf.json"),
                str(tmp / "logs" / "observer" / "lifecycles" / "L1" / "events.jsonl"),
                str(tmp / "logs" / "observer" / "workspace" / "gates.jsonl"),
            }
            self.assertEqual(
                projection_domains_for_paths(_config(tmp), paths),
                frozenset(
                    {
                        ProjectionDomain.TASKS,
                        ProjectionDomain.LIFECYCLES,
                        ProjectionDomain.WORKSPACE,
                    }
                ),
            )

    def test_unknown_accepted_path_fails_open_to_every_domain(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            self.assertEqual(
                projection_domains_for_paths(
                    _config(tmp), {str(tmp / "unexpected" / "input.json")}
                ),
                frozenset(ProjectionDomain),
            )


class ChangePacerDeadlineTests(unittest.TestCase):
    """The pure scheduling core, driven with synthetic monotonic instants."""

    def _pacer(self, *, interval: float = 1.0, heartbeat: float = 15.0) -> ChangePacer:
        pacer = ChangePacer(interval=interval, heartbeat=heartbeat, debounce=0.1)
        pacer._last_wake = 100.0
        pacer._watcher_healthy = True
        return pacer

    def test_idle_world_waits_for_the_heartbeat(self) -> None:
        pacer = self._pacer()
        deadline, reason = pacer._next_deadline()
        self.assertEqual((deadline, reason), (115.0, "heartbeat"))

    def test_lone_change_projects_after_the_debounce(self) -> None:
        pacer = self._pacer()
        pacer._first_pending = pacer._last_pending = 105.0
        deadline, reason = pacer._next_deadline()
        self.assertEqual((deadline, reason), (105.1, "change"))

    def test_change_is_floored_to_one_projection_per_interval(self) -> None:
        pacer = self._pacer()
        # Change lands 50ms after the last wake: the floor (interval=1.0) wins.
        pacer._first_pending = pacer._last_pending = 100.05
        deadline, reason = pacer._next_deadline()
        self.assertEqual((deadline, reason), (101.0, "change"))

    def test_sustained_burst_is_bounded_by_max_delay(self) -> None:
        pacer = self._pacer()
        # First change at 105.0, still being extended at 105.95: the max-delay bound
        # (interval-sized, R2) beats the ever-sliding settle window.
        pacer._first_pending = 105.0
        pacer._last_pending = 105.95
        deadline, reason = pacer._next_deadline()
        self.assertEqual((deadline, reason), (106.0, "change"))

    def test_degraded_watcher_ticks_at_the_fixed_interval(self) -> None:
        pacer = self._pacer()
        pacer._watcher_healthy = False
        pacer._first_pending = pacer._last_pending = 100.2
        deadline, reason = pacer._next_deadline()
        self.assertEqual((deadline, reason), (101.0, "interval"))

    def test_heartbeat_never_undercuts_the_interval(self) -> None:
        pacer = ChangePacer(interval=10.0, heartbeat=1.0)
        pacer._last_wake = 100.0
        pacer._watcher_healthy = True
        deadline, _ = pacer._next_deadline()
        self.assertEqual(deadline, 110.0)


class _FakeWatcher:
    """A ChangeWatch fake: reports healthy, then emits on demand."""

    def __init__(self) -> None:
        self.pacer: WakeTarget | None = None
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()

    async def run(self, pacer: WakeTarget) -> None:
        self.pacer = pacer
        pacer.set_watcher_healthy(True)
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.stopped.set()

    def emit(self, domain: ProjectionDomain = ProjectionDomain.TASKS) -> None:
        assert self.pacer is not None
        self.pacer.notify_change(frozenset({domain}))


class _CrashingWatcher:
    async def run(self, pacer: WakeTarget) -> None:
        pacer.set_watcher_healthy(True)
        raise RuntimeError("watch backend exploded")


class AdaptiveProjectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    async def _run_projector(self, projector: Projector) -> asyncio.Task[None]:
        task = asyncio.create_task(projector.run())

        async def _cancel() -> None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        self.addAsyncCleanup(_cancel)
        return task

    async def _wait_for(self, predicate, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail(f"condition not reached within {timeout:.1f}s")

    async def test_quiet_world_projects_only_at_heartbeat_cadence(self) -> None:
        watcher = _FakeWatcher()
        projector = Projector(
            _config(self.tmp),
            cadence=ProjectionCadence(interval=0.02, heartbeat=0.2),
            refreshers=ProjectionRefreshers(change_watcher=watcher),
        )
        await self._run_projector(projector)
        await asyncio.wait_for(watcher.started.wait(), timeout=1)
        start_count = projector.projection_count
        await asyncio.sleep(0.65)
        beats = projector.projection_count - start_count
        # ~3 heartbeats in 0.65s at 0.2s cadence; the former behaviour (interval pacing)
        # would have produced ~32. Loose bounds absorb slow-CI tick durations.
        self.assertGreaterEqual(beats, 1)
        self.assertLessEqual(beats, 5)
        self.assertEqual(projector.last_wake_reason, "heartbeat")

    async def test_single_change_projects_within_the_debounce_bound(self) -> None:
        watcher = _FakeWatcher()
        projector = Projector(
            _config(self.tmp),
            cadence=ProjectionCadence(interval=0.02, heartbeat=30.0),
            refreshers=ProjectionRefreshers(change_watcher=watcher),
        )
        await self._run_projector(projector)
        await asyncio.wait_for(watcher.started.wait(), timeout=1)
        # Let boot-time ticks (degraded-start + floor) drain, then measure a lone change.
        await asyncio.sleep(0.2)
        baseline = projector.projection_count
        emitted_at = time.monotonic()
        watcher.emit()
        await self._wait_for(lambda: projector.projection_count > baseline, timeout=2.0)
        latency = time.monotonic() - emitted_at
        self.assertEqual(projector.last_wake_reason, "change")
        self.assertEqual(projector.last_invalidated_domains, frozenset({"tasks"}))
        # debounce (clamped to interval=0.02) + one projection + generous CI slack.
        self.assertLess(latency, 1.0)

    async def test_burst_coalesces_to_a_bounded_projection_count(self) -> None:
        watcher = _FakeWatcher()
        projector = Projector(
            _config(self.tmp),
            cadence=ProjectionCadence(interval=0.15, heartbeat=30.0),
            refreshers=ProjectionRefreshers(change_watcher=watcher),
        )
        await self._run_projector(projector)
        await asyncio.wait_for(watcher.started.wait(), timeout=1)
        await asyncio.sleep(0.3)  # drain boot ticks; pass the first interval floor
        baseline = projector.projection_count
        for _ in range(25):
            watcher.emit()
            await asyncio.sleep(0.002)
        await asyncio.sleep(0.7)
        projected = projector.projection_count - baseline
        # 25 writes -> one debounced projection (a second is tolerated if the burst
        # straddles a floor boundary); unconditional 1-per-write would give 25.
        self.assertGreaterEqual(projected, 1)
        self.assertLessEqual(projected, 2)
        self.assertEqual(projector.last_wake_reason, "change")

    async def test_missing_watchfiles_degrades_loudly_to_fixed_interval(self) -> None:
        config = _config(self.tmp)
        projector = Projector(
            config,
            cadence=ProjectionCadence(interval=0.05, heartbeat=30.0),
            refreshers=ProjectionRefreshers(change_watcher=ProjectionInputWatcher(config)),
        )
        with (
            mock.patch.object(change_watcher_module, "watchfiles", None),
            self.assertLogs("agents_remember.serving.change_watcher", level="ERROR") as logs,
        ):
            await self._run_projector(projector)
            await self._wait_for(lambda: projector.projection_count >= 3, timeout=3.0)
        # Ticks at the fixed 0.05s interval despite the 30s heartbeat: fail-open (R7).
        self.assertEqual(projector.last_wake_reason, "interval")
        self.assertIn("watchfiles is not installed", "\n".join(logs.output))

    async def test_crashed_watcher_task_degrades_loudly_to_fixed_interval(self) -> None:
        projector = Projector(
            _config(self.tmp),
            cadence=ProjectionCadence(interval=0.05, heartbeat=30.0),
            refreshers=ProjectionRefreshers(change_watcher=_CrashingWatcher()),
        )
        with self.assertLogs("agents_remember.serving.projector", level="ERROR") as logs:
            await self._run_projector(projector)
            await self._wait_for(lambda: projector.projection_count >= 3, timeout=3.0)
        self.assertEqual(projector.last_wake_reason, "interval")
        self.assertIn("change watcher task died", "\n".join(logs.output))

    async def test_root_derivation_failure_retries_instead_of_dying(self) -> None:
        # Review F3: a transient stat/glob failure while deriving the watch roots must
        # follow the same loud degrade-and-retry path as a watch failure -- not escape
        # run() and permanently kill the watcher task (losing the periodic self-heal).
        config = _config(self.tmp)
        (self.tmp / "tasks").mkdir()
        watcher = ProjectionInputWatcher(config, refresh_seconds=0.05)

        class RecordingPacer:
            def __init__(self) -> None:
                self.health: list[bool] = []
                self.changes = 0

            def notify_change(
                self,
                domains: frozenset[ProjectionDomain] = frozenset(ProjectionDomain),
            ) -> None:
                _ = domains
                self.changes += 1

            def set_watcher_healthy(self, healthy: bool) -> None:
                self.health.append(healthy)

        real_roots = change_watcher_module.projection_input_roots
        calls = 0

        def flaky_roots(cfg: McpRuntimeConfig) -> list[Path]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient derivation failure")
            return real_roots(cfg)

        pacer = RecordingPacer()
        with (
            mock.patch.object(change_watcher_module, "projection_input_roots", flaky_roots),
            self.assertLogs("agents_remember.serving.change_watcher", level="ERROR") as logs,
        ):
            task = asyncio.create_task(watcher.run(pacer))
            try:
                # Degrades loudly on the failed derivation, then recovers on the retry
                # cycle: a healthy=True report proves the watch re-established.
                await self._wait_for(lambda: True in pacer.health, timeout=3.0)
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self.assertIn(False, pacer.health)  # the loud degrade came first
        self.assertIn("projection input watcher FAILED", "\n".join(logs.output))

    async def test_run_owns_the_watcher_task_lifecycle(self) -> None:
        watcher = _FakeWatcher()
        projector = Projector(
            _config(self.tmp),
            cadence=ProjectionCadence(interval=100, heartbeat=100),
            refreshers=ProjectionRefreshers(change_watcher=watcher),
        )
        task = asyncio.create_task(projector.run())
        await asyncio.wait_for(watcher.started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(watcher.stopped.is_set())

    async def test_without_a_watcher_the_legacy_interval_pacing_is_kept(self) -> None:
        # The sim/replay and injected-now() path: no watcher, no pacer -- the loop must
        # keep ticking unconditionally every interval exactly as before this change.
        projector = Projector(_config(self.tmp), cadence=ProjectionCadence(interval=0.05))
        self.assertIsNone(projector._pacer)
        await self._run_projector(projector)
        await asyncio.sleep(0.4)
        self.assertGreaterEqual(projector.projection_count, 2)
        self.assertIsNone(projector.last_wake_reason)


class RealWatchfilesIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """One end-to-end pass over the real inotify backend (Linux CI + dev machines)."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    async def test_a_real_write_under_tasks_triggers_a_change_projection(self) -> None:
        if change_watcher_module.watchfiles is None:  # pragma: no cover
            self.skipTest("watchfiles not installed")
        config = _config(self.tmp)
        (self.tmp / "tasks").mkdir()
        projector = Projector(
            config,
            cadence=ProjectionCadence(interval=0.05, heartbeat=60.0),
            refreshers=ProjectionRefreshers(change_watcher=ProjectionInputWatcher(config)),
        )
        task = asyncio.create_task(projector.run())
        try:
            # Give awatch time to register its watches, then let boot ticks drain.
            await asyncio.sleep(0.8)
            baseline = projector.projection_count
            (self.tmp / "tasks" / "note.md").write_text("changed\n", encoding="utf-8")
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if projector.projection_count > baseline:
                    break
                await asyncio.sleep(0.02)
            self.assertGreater(
                projector.projection_count,
                baseline,
                "a write under tasks/ did not wake the projector within 5s",
            )
            self.assertEqual(projector.last_wake_reason, "change")
            self.assertEqual(projector.last_invalidated_domains, frozenset({"tasks"}))
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
