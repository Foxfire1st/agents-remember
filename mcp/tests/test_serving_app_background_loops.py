"""Behavioural coverage for the dashboard app's background loops and their lifespan wiring.

``serving/app.py`` runs four always-on background tasks (projection, provider metrics, agent-notifier
sweep, workspace-river compaction) plus two opt-in ones (the heap diagnostic, the glibc arena
trim). Their *steady state* is exercised by the rest of the suite simply by booting the app; what
was never exercised is the part that matters operationally:

* every loop's ``except Exception`` arm -- a background task that dies on one bad pass silently
  stops sampling / sweeping / compacting for the lifetime of the daemon, and nothing else in the
  process notices. Each test here proves the failing pass is logged AND that the loop performs the
  next pass anyway;
* the agent-notifier's disabled arm -- ``orchestration.agentNotifier.enabled`` is re-read on every pass,
  so turning the sweep on must take effect without restarting the daemon;
* the trim loop, which never runs unless ``AR_MALLOC_TRIM`` is set, and whose interval is resolved
  once at task start rather than per tick;
* the two ``if``s in the lifespan that decide whether the opt-in tasks exist at all, and the
  cancellation every background task shares on shutdown.

Fakes stop at the process/platform seam only: ``docker ps`` (``sample_provider_containers``), the
libc ``malloc_trim`` symbol, and -- where a *failure* has to be provoked -- the one collaborator
whose failure is under test. The stores, settings files, catalogs, event rivers and the FastAPI app
are all real.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
import threading
import tracemalloc
import unittest
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest import mock

from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.agentic_settings import agentic_settings_path
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.observer import observer_root
from agents_remember.observer.events import Event
from agents_remember.observer.store import EventStore
from agents_remember.providers.metrics import (
    PROVIDER_METRICS_SCHEMA,
    MetricsSnapshot,
    ProviderMetricsStore,
)
from agents_remember.serving import _app_lifespan as lifespan_module
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.app import (
    ServingCollaborators,
    _agent_notifier_loop,
    _malloc_trim_loop,
    _metrics_loop,
    _ServingRuntime,
    _workspace_river_compaction_loop,
    create_app,
)
from agents_remember.serving.build_info import resolve_serving_build
from agents_remember.serving.projector import ProjectionCadence, Projector
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
)
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    TerminalCatalogLivenessSweeper,
    utc_now,
)
from agents_remember.serving.terminal_paste import TerminalPaster

LOGGER_NAME = "agents_remember.serving.app"


def _config(tmp: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=tmp / "settings.json",
        coordination_root=tmp,
        workspace_root=tmp,
        transcript_root=tmp / "logs" / "mcp",
    )


class _CatalogOnlyHost:
    """A ``TerminalHost`` duck-type with no PTYs: the loops never touch a live session."""

    def __init__(self) -> None:
        self.shutdown_called = False

    def get(self, _sid: str) -> None:
        return None

    def has_session(self, _tmux_name: str) -> bool:
        return False

    def terminate(self, _sid: str, *, tmux_name: str | None = None) -> None:
        del tmux_name

    def shutdown(self) -> None:
        self.shutdown_called = True


def _runtime(tmp: Path, *, host: object | None = None) -> _ServingRuntime:
    """A real ``_ServingRuntime`` over real stores, with no PTY host and an unrun projector."""

    config = _config(tmp)
    catalog = TerminalCatalog(tmp / "terminal-sessions.json")
    terminal_host = cast(TerminalHost, host or _CatalogOnlyHost())
    liveness_config = TerminalCatalogLivenessConfig()
    return _ServingRuntime(
        config=config,
        projector=Projector(config, cadence=ProjectionCadence(interval=100)),
        host=terminal_host,
        catalog=catalog,
        paster=TerminalPaster(),
        liveness_clock=utc_now,
        liveness_config=liveness_config,
        liveness_sweeper=TerminalCatalogLivenessSweeper(catalog, terminal_host),
        build=resolve_serving_build(),
        heartbeat_store=AgentNotifierHeartbeatStore(observer_root(config)),
        interval=100.0,
    )


def _write_agent_notifier_settings(tmp: Path, *, enabled: bool, interval_seconds: float) -> None:
    path = agentic_settings_path(tmp)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "orchestration": {
                    "agentNotifier": {"enabled": enabled, "intervalSeconds": interval_seconds}
                }
            }
        ),
        encoding="utf-8",
    )


async def _until(predicate: Callable[[], bool], *, what: str, timeout: float = 10.0) -> None:
    """Wait for ``predicate`` while the loop under test runs, or fail naming what never happened."""

    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.005)


async def _run_until(
    coroutine: Any, predicate: Callable[[], bool], *, what: str, timeout: float = 10.0
) -> None:
    """Drive one background loop until ``predicate`` holds, then cancel it like the lifespan does."""

    task = asyncio.create_task(coroutine)
    try:
        await _until(predicate, what=what, timeout=timeout)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class MetricsLoopTests(unittest.IsolatedAsyncioTestCase):
    """A failed provider sample must cost one interval, not the rest of the daemon's life."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        self.store = ProviderMetricsStore(self.tmp)

    def _snapshot(self, sampled_at: str) -> MetricsSnapshot:
        return MetricsSnapshot(schema=PROVIDER_METRICS_SCHEMA, sampledAt=sampled_at, containers=[])

    async def test_a_failed_sample_is_logged_and_the_next_interval_still_records(self) -> None:
        # Fail, succeed, then fail for the rest of the run: exactly one row may ever be recorded,
        # so "the loop survived the first failure" and "a failed pass records nothing" are both
        # decided by the log's contents rather than by when the test happened to cancel the task.
        calls: list[str] = []

        def sample(*, cwd: Path, **_kwargs: object) -> MetricsSnapshot:
            del cwd
            calls.append("call")
            if len(calls) == 2:
                return self._snapshot("2026-07-31T00:00:02+00:00")
            raise RuntimeError(f"docker daemon went away ({len(calls)})")

        with (
            mock.patch.object(lifespan_module, "sample_provider_containers", sample),
            mock.patch.object(lifespan_module, "DEFAULT_SAMPLE_INTERVAL_SECONDS", 0),
            self.assertLogs(LOGGER_NAME, level="ERROR") as logs,
        ):
            # Wait on the LOGGED failures, not the attempts: a pass that has entered the sampler
            # has not yet reached the handler, so counting attempts would race the log.
            await _run_until(
                _metrics_loop(_config(self.tmp), self.store),
                lambda: len(logs.records) >= 2,
                what="the sampler to keep sampling across a failure on either side of a good pass",
            )

        self.assertGreaterEqual(len(calls), 3)
        rendered = "\n".join(logs.output)
        self.assertIn("provider metrics sample failed", rendered)
        self.assertIn("docker daemon went away (1)", rendered)
        self.assertIn("docker daemon went away (3)", rendered)
        stamps = [row["sampledAt"] for row in self.store.read_recent()]
        self.assertEqual(stamps, ["2026-07-31T00:00:02+00:00"])
        current = self.store.read_current()
        assert current is not None
        self.assertEqual(current["sampledAt"], "2026-07-31T00:00:02+00:00")

    async def test_cancellation_drains_an_inflight_metrics_write_before_returning(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        original_record = self.store.record

        def record(snapshot: MetricsSnapshot) -> None:
            entered.set()
            self.assertTrue(
                release.wait(timeout=5),
                "test did not release the in-flight metrics write",
            )
            original_record(snapshot)

        with (
            mock.patch.object(
                lifespan_module,
                "sample_provider_containers",
                return_value=self._snapshot("2026-08-14T00:00:00+00:00"),
            ),
            mock.patch.object(self.store, "record", side_effect=record),
        ):
            task = asyncio.create_task(_metrics_loop(_config(self.tmp), self.store))
            await _until(entered.is_set, what="the metrics writer to enter its worker thread")
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())

            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task

        current = self.store.read_current()
        assert current is not None
        self.assertEqual(current["sampledAt"], "2026-08-14T00:00:00+00:00")


class AgentNotifierLoopTests(unittest.IsolatedAsyncioTestCase):
    """The sweep's on/off switch is settings state re-read per pass, not boot state."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    async def test_disabled_agent_notifier_never_sweeps_and_enabling_it_needs_no_restart(
        self,
    ) -> None:
        _write_agent_notifier_settings(self.tmp, enabled=False, interval_seconds=0.01)
        runtime = _runtime(self.tmp)
        task = asyncio.create_task(_agent_notifier_loop(runtime))
        try:
            # Many disabled passes: the loop must keep looping and must tick nothing.
            await asyncio.sleep(0.2)
            self.assertIsNone(runtime.heartbeat_store.read())

            _write_agent_notifier_settings(self.tmp, enabled=True, interval_seconds=0.01)
            await _until(
                lambda: runtime.heartbeat_store.read() is not None,
                what="the first sweep after the agent-notifier was enabled in settings",
            )
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        heartbeat = runtime.heartbeat_store.read()
        assert heartbeat is not None
        self.assertGreaterEqual(heartbeat.sweepCount, 1)

    async def test_a_failed_sweep_is_logged_and_the_next_interval_sweeps_again(self) -> None:
        _write_agent_notifier_settings(self.tmp, enabled=True, interval_seconds=0.01)
        runtime = _runtime(self.tmp)
        sweeps: list[datetime] = []

        def sweep(_ctx: object, *, now: datetime) -> None:
            sweeps.append(now)
            if len(sweeps) == 1:
                raise RuntimeError("expectation store unreadable")

        with (
            mock.patch.object(lifespan_module, "run_agent_notifier_sweep", sweep),
            self.assertLogs(LOGGER_NAME, level="ERROR") as logs,
        ):
            await _run_until(
                _agent_notifier_loop(runtime),
                lambda: len(sweeps) >= 2,
                what="the agent-notifier to sweep again after a failed sweep",
            )

        self.assertIn("agent-notifier sweep failed", "\n".join(logs.output))
        self.assertIn("expectation store unreadable", "\n".join(logs.output))


class MallocTrimLoopTests(unittest.IsolatedAsyncioTestCase):
    """The opt-in arena reclaim: one interval resolution, one trim per tick, failures survivable."""

    async def test_interval_is_resolved_once_and_every_tick_trims(self) -> None:
        intervals: list[float] = []
        trims: list[int] = []

        def interval() -> float:
            intervals.append(0.0)
            return 0.0

        def trim() -> int | None:
            trims.append(len(trims))
            return 1

        with (
            mock.patch.object(lifespan_module, "malloc_trim_interval_seconds", interval),
            mock.patch.object(lifespan_module, "trim_malloc", trim),
        ):
            await _run_until(
                _malloc_trim_loop(),
                lambda: len(trims) >= 3,
                what="three arena-reclaim ticks",
            )

        # The cadence is read once at task start, not re-read per tick.
        self.assertEqual(len(intervals), 1)

    async def test_a_failed_trim_is_logged_and_the_loop_keeps_trimming(self) -> None:
        trims: list[int] = []

        def trim() -> int | None:
            trims.append(len(trims))
            if len(trims) == 1:
                raise OSError("malloc_trim unavailable")
            return 0

        with (
            mock.patch.object(lifespan_module, "malloc_trim_interval_seconds", lambda: 0.0),
            mock.patch.object(lifespan_module, "trim_malloc", trim),
            self.assertLogs(LOGGER_NAME, level="ERROR") as logs,
        ):
            await _run_until(
                _malloc_trim_loop(),
                lambda: len(trims) >= 2,
                what="a trim after the failed one",
            )

        self.assertIn("malloc_trim failed", "\n".join(logs.output))


class WorkspaceRiverCompactionLoopTests(unittest.IsolatedAsyncioTestCase):
    """The one event river nothing else reclaims: it must keep shrinking, and keep going on error."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def _river(self) -> Path:
        return observer_root(_config(self.tmp)) / "workspace" / "events.jsonl"

    def _append(self, ident: str, *, ts: datetime) -> None:
        EventStore(observer_root(_config(self.tmp))).append(
            Event(
                id=ident,
                ts=ts.isoformat(),
                kind="workspace.note",
                trust="observed",
                actor="system",
                data={"note": ident},
            )
        )

    async def test_expired_workspace_rows_are_physically_reclaimed_on_the_cadence(self) -> None:
        now = datetime.now(UTC)
        self._append("ancient", ts=now - timedelta(days=30))
        self._append("fresh", ts=now)
        self.assertEqual(len(self._river().read_text(encoding="utf-8").splitlines()), 2)

        runtime = _runtime(self.tmp)
        with mock.patch.object(lifespan_module, "WORKSPACE_EVENT_COMPACT_INTERVAL_SECONDS", 0):
            await _run_until(
                _workspace_river_compaction_loop(runtime),
                lambda: len(self._river().read_text(encoding="utf-8").splitlines()) == 1,
                what="the expired workspace row to be dropped from the river",
            )

        retained = self._river().read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line)["id"] for line in retained], ["fresh"])

    async def test_a_failed_compaction_is_logged_and_the_loop_compacts_again(self) -> None:
        runtime = _runtime(self.tmp)
        attempts: list[Path] = []

        def compact(root: Path, *, now: datetime) -> int:
            del now
            attempts.append(root)
            if len(attempts) == 1:
                raise OSError("river locked by another process")
            return 0

        with (
            mock.patch.object(lifespan_module, "WORKSPACE_EVENT_COMPACT_INTERVAL_SECONDS", 0),
            mock.patch.object(lifespan_module, "compact_workspace_river", compact),
            self.assertLogs(LOGGER_NAME, level="ERROR") as logs,
        ):
            await _run_until(
                _workspace_river_compaction_loop(runtime),
                lambda: len(attempts) >= 2,
                what="a compaction pass after the failed one",
            )

        self.assertIn("workspace event-river compaction failed", "\n".join(logs.output))
        # Every pass compacts THIS runtime's observer root, not a rediscovered one.
        self.assertEqual(set(attempts), {runtime.observer_root})


class _TaskProbe:
    """An awaitable stand-in for one optional lifespan task: records entry and cancellation."""

    def __init__(self) -> None:
        self.started = False
        self.cancelled = False

    async def __call__(self) -> None:
        self.started = True
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class OptionalLifespanTaskTests(unittest.TestCase):
    """The two opt-in diagnostics exist only when their env flag is set -- and die with the app."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        self.heap = _TaskProbe()
        self.trim = _TaskProbe()

    def _app(self) -> Any:
        return create_app(
            _config(self.tmp),
            cadence=ProjectionCadence(interval=100),
            collaborators=ServingCollaborators(
                terminal_host=cast(TerminalHost, _CatalogOnlyHost()),
                terminal_catalog=TerminalCatalog(self.tmp / "terminal-sessions.json"),
            ),
        )

    def _boot(self, environment: dict[str, str]) -> None:
        with (
            mock.patch.dict(os.environ, environment, clear=False),
            mock.patch.object(lifespan_module, "heap_diag_loop", self.heap),
            mock.patch.object(lifespan_module, "_malloc_trim_loop", self.trim),
            TestClient(self._app()) as client,
        ):
            self.assertEqual(client.get("/api/state").status_code, 200)

    def test_no_optional_task_runs_when_neither_flag_is_set(self) -> None:
        self._boot({"AR_HEAP_DIAG": "", "AR_MALLOC_TRIM": ""})
        self.assertFalse(self.heap.started)
        self.assertFalse(self.trim.started)

    def test_the_trim_flag_starts_only_the_trim_task_and_shutdown_cancels_it(self) -> None:
        self._boot({"AR_HEAP_DIAG": "", "AR_MALLOC_TRIM": "1"})
        self.assertTrue(self.trim.started)
        self.assertTrue(self.trim.cancelled)
        self.assertFalse(self.heap.started)

    def test_the_heap_flag_starts_tracing_plus_the_diagnostic_task(self) -> None:
        was_tracing = tracemalloc.is_tracing()
        if not was_tracing:
            self.addCleanup(tracemalloc.stop)
        self._boot({"AR_HEAP_DIAG": "1", "AR_MALLOC_TRIM": ""})
        self.assertTrue(self.heap.started)
        self.assertTrue(self.heap.cancelled)
        self.assertTrue(tracemalloc.is_tracing())
        self.assertFalse(self.trim.started)


if __name__ == "__main__":
    unittest.main()
