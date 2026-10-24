"""L81 F01/F02: concrete projection, event-tail and warmup unit graphs."""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
import threading
import unittest
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

from agents_remember.serving import _app_lifespan as serving
from agents_remember.serving import background_accounting as accounting
from agents_remember.serving import events
from agents_remember.serving import projector as projection
from agents_remember.serving._app_common import _ServingRuntime
from agents_remember.serving.change_watcher import ProjectionDomain, ProjectionWake
from agents_remember.serving.projections.landing_state import LandingStateRefresher
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from fastapi import FastAPI
from test_paseo_warmup_operations import _World
from test_serving import _config


class _UnitClock:
    def __init__(self, mode: str = "success", target: str = "") -> None:
        self.mode, self.target = mode, target
        self.clocks: dict[int, float] = {}
        self.trace: list[tuple[str, int, float]] = []
        self.loop = threading.get_ident()
        self.started, self.release = threading.Event(), threading.Event()
        self.ledger = accounting.BackgroundLedger()

    def cpu(self) -> float:
        return self.clocks.get(threading.get_ident(), 0.0)

    def wrap[**P, R](self, name: str, delta: float, function: Callable[P, R]) -> Callable[P, R]:
        def call(*args: P.args, **kwargs: P.kwargs) -> R:
            ident = threading.get_ident()
            self.trace.append((name, ident, delta))
            self.clocks[ident] = self.clocks.get(ident, 0) + delta
            if name == self.target:
                if self.mode == "failure":
                    raise OSError("injected unit boundary failure")
                if self.mode == "cancel":
                    self.started.set()
                    if not self.release.wait(timeout=HANG_GUARD_SECONDS):
                        raise AssertionError("fixture did not drain its native worker")
            return function(*args, **kwargs)

        return call

    async def cancel_twice(self, task: asyncio.Task[object], test: unittest.TestCase) -> None:
        async with asyncio.timeout(HANG_GUARD_SECONDS):
            while not self.started.is_set():
                await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        test.assertFalse(task.done())
        self.release.set()
        with test.assertRaises(asyncio.CancelledError):
            await task

    def verify(self, test: unittest.TestCase, owner: str, *, units: int | None = None) -> None:
        owners = self.ledger.snapshot()["owners"]
        assert isinstance(owners, dict)
        for name, row in owners.items():
            assert isinstance(row, dict)
            test.assertEqual(
                row["cpuSecondsTotal"],
                sum(delta for _, _, delta in self.trace) if name == owner else 0,
            )
            if name == owner and units is not None:
                test.assertEqual(row["units"], units)
        print(
            "UNIT-TRACE "
            + json.dumps(
                {
                    "owner": owner,
                    "mode": self.mode,
                    "target": self.target,
                    "loopThread": self.loop,
                    "trace": self.trace,
                    "owners": owners,
                },
                sort_keys=True,
            )
        )


class MaterialOwnerUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_wake_keeps_its_exact_reason_domain_and_paths_on_failed_ticks(self) -> None:
        for reason in ("change", "heartbeat", "interval", "full"):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                clock = _UnitClock("failure", "project")
                worker = projection.Projector(_config(Path(tmp)), ledger=clock.ledger)
                wake = ProjectionWake(
                    reason,
                    frozenset({ProjectionDomain.TASKS}),
                    paths=("tasks/one/task.json", "tasks/one/leaf.json"),
                    path_count=2,
                    paths_complete=True,
                )
                refresh = worker._next_refresh(wake)
                with (
                    mock.patch.object(accounting.time, "thread_time", clock.cpu),
                    mock.patch.object(
                        projection,
                        "project_and_write",
                        clock.wrap("project", 7, projection.project_and_write),
                    ),
                    self.assertRaisesRegex(OSError, "injected unit"),
                ):
                    await worker._tick(datetime(2026, 10, 13, tzinfo=UTC), refresh)
                wakes = clock.ledger.snapshot()["wakes"]
                assert isinstance(wakes, dict)
                self.assertEqual(wakes["total"], 1)
                self.assertEqual(wakes["byReason"], {reason: 1})
                recent = wakes["recent"]
                assert isinstance(recent, list) and isinstance(recent[0], dict)
                self.assertEqual(
                    {
                        key: recent[0][key]
                        for key in ("reason", "domains", "pathCount", "samplePaths")
                    },
                    {
                        "reason": reason,
                        "domains": ["tasks"],
                        "pathCount": 2,
                        "samplePaths": ["tasks/one/task.json", "tasks/one/leaf.json"],
                    },
                )
                clock.verify(self, accounting.OWNER_PROJECTION, units=1)

    async def test_real_projection_tick_accounts_success_failure_and_repeated_cancel(self) -> None:
        for mode in ("success", "failure", "cancel"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                clock = _UnitClock(mode, "project")
                worker = projection.Projector(_config(Path(tmp)), ledger=clock.ledger)
                with (
                    mock.patch.object(accounting.time, "thread_time", clock.cpu),
                    mock.patch.object(
                        projection,
                        "project_and_write",
                        clock.wrap("project", 7, projection.project_and_write),
                    ),
                ):
                    task = asyncio.create_task(
                        worker._tick(datetime(2026, 10, 13, tzinfo=UTC), None)
                    )
                    try:
                        if mode == "cancel":
                            await clock.cancel_twice(task, self)
                        elif mode == "failure":
                            with self.assertRaisesRegex(OSError, "injected unit"):
                                await task
                        else:
                            await task
                    finally:
                        clock.release.set()
                        if not task.done():
                            task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await task
                self.assertEqual([name for name, _, _ in clock.trace], ["project"])
                self.assertNotEqual(clock.trace[0][1], clock.loop)
                clock.verify(self, accounting.OWNER_PROJECTION, units=1)

    async def test_tail_worker_reads_and_both_serialization_callbacks_have_exact_units(
        self,
    ) -> None:
        for size in (1, 205):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as tmp:
                clock = _UnitClock()
                pending = [
                    events.RawEvent(source="test", data="{}", payload={"n": n}, cursor=str(n))
                    for n in range(size)
                ]

                def read(
                    *_args: object, captured=pending, **_kwargs: object
                ) -> tuple[list[events.RawEvent], dict[str, int]]:
                    rows = captured[:]
                    captured.clear()
                    return rows, {}

                feed = events.stream_raw_events(_config(Path(tmp)), ledger=clock.ledger)
                with (
                    mock.patch.object(accounting.time, "thread_time", clock.cpu),
                    mock.patch.object(
                        events,
                        "_initial_offsets_and_last_ts",
                        clock.wrap("scan", 2, lambda _: ({}, None)),
                    ),
                    mock.patch.object(events, "read_new_events", clock.wrap("read", 3, read)),
                    mock.patch.object(
                        events, "ServerSentEvent", clock.wrap("encode", 4, events.ServerSentEvent)
                    ),
                ):
                    try:
                        for _ in range(size + 1):
                            await anext(feed)
                    finally:
                        await feed.aclose()
                self.assertEqual(
                    [name for name, _, _ in clock.trace],
                    ["scan", "read", *("encode" for _ in range(size)), "read", "encode"],
                )
                self.assertTrue(
                    all(
                        (ident == clock.loop) == (name == "encode")
                        for name, ident, _ in clock.trace
                    )
                )
                clock.verify(self, accounting.OWNER_EVENT_TAIL, units=size + 4)

    async def test_tail_failures_and_repeated_cancellation_drain_the_actual_readers(self) -> None:
        for target in ("scan", "read"):
            for mode in ("failure", "cancel"):
                with self.subTest(target=target, mode=mode), tempfile.TemporaryDirectory() as tmp:
                    clock = _UnitClock(mode, target)
                    feed = events.stream_raw_events(_config(Path(tmp)), ledger=clock.ledger)
                    with (
                        mock.patch.object(accounting.time, "thread_time", clock.cpu),
                        mock.patch.object(
                            events,
                            "_initial_offsets_and_last_ts",
                            clock.wrap("scan", 2, lambda _: ({}, None)),
                        ),
                        mock.patch.object(
                            events,
                            "read_new_events",
                            clock.wrap("read", 3, lambda *_a, **_k: ([], {})),
                        ),
                    ):
                        task = asyncio.create_task(anext(feed))
                        try:
                            if mode == "cancel":
                                await clock.cancel_twice(task, self)
                            else:
                                with self.assertRaisesRegex(OSError, "injected unit"):
                                    await task
                        finally:
                            clock.release.set()
                            if not task.done():
                                task.cancel()
                                with contextlib.suppress(asyncio.CancelledError):
                                    await task
                            await feed.aclose()
                    self.assertEqual(
                        [name for name, _, _ in clock.trace],
                        ["scan", "read"] if target == "read" else ["scan"],
                    )
                    self.assertTrue(all(ident != clock.loop for _, ident, _ in clock.trace))
                    clock.verify(
                        self, accounting.OWNER_EVENT_TAIL, units=2 if target == "read" else 1
                    )

    async def test_warmup_uses_one_ledger_for_every_actual_callback_and_drained_resume(
        self,
    ) -> None:
        for mode in ("success", "failure", "cancel"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                world = _World(Path(tmp))
                clock = _UnitClock(mode, "resume")
                with (
                    mock.patch.object(world.runtime, "background_ledger", clock.ledger),
                    world.patches(),
                    contextlib.ExitStack() as stack,
                ):
                    claimed = serving.check_host_once(world.config, tracker=world.tracker)
                    assert claimed is not None
                    run, claim = claimed
                    stack.enter_context(
                        mock.patch.object(accounting.time, "thread_time", clock.cpu)
                    )
                    stack.enter_context(
                        mock.patch.object(
                            serving, "resume_agent", clock.wrap("resume", 7, lambda *_: None)
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            serving,
                            "read_available_memory",
                            clock.wrap("memory", 2, lambda world=world: world.memory),
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            serving,
                            "read_warmup_priority",
                            clock.wrap("priority", 3, serving.read_warmup_priority),
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            world.tracker,
                            "write_progress",
                            clock.wrap("progress", 5, world.tracker.write_progress),
                        )
                    )
                    for name in ("note_run_finished", "note_run_ended"):
                        stack.enter_context(
                            mock.patch.object(
                                world.tracker,
                                name,
                                clock.wrap("final", 11, getattr(world.tracker, name)),
                            )
                        )
                    task = asyncio.create_task(
                        serving._drive_warmup_run(world.runtime, run, claim, {})
                    )
                    try:
                        if mode == "cancel":
                            await clock.cancel_twice(task, self)
                        else:
                            await task
                    finally:
                        clock.release.set()
                        if not task.done():
                            task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await task
                        claim.close()
                self.assertTrue(
                    all(
                        (ident == clock.loop) == (name != "resume")
                        for name, ident, _ in clock.trace
                    )
                )
                self.assertEqual(clock.trace[-1][0], "final")
                if mode != "cancel":
                    self.assertEqual(sum(name == "resume" for name, _, _ in clock.trace), 3)
                    self.assertEqual(run.loaded if mode == "success" else len(run.failed), 3)
                clock.verify(self, accounting.OWNER_WARMUP, units=len(clock.trace))

    async def test_landing_discovery_probe_and_publication_keep_exact_attribution(self) -> None:
        for mode in ("success", "failure", "cancel"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                contract = default_contract(
                    ContractTask("task", "repo", root, "light-task", "external"),
                    leaf=LeafIdentity("leaf"),
                    code=RepoBranchPlan(root / "code", "main", "work", "a" * 40),
                    memory=RepoBranchPlan(root / "memory", "main", "work", "a" * 40),
                )
                write_contract(contract.contract_path, contract)
                clock = _UnitClock(mode, "probe")
                refresher = LandingStateRefresher(
                    _config(root), observe=clock.wrap("probe", 3, lambda _: []), ledger=clock.ledger
                )
                with (
                    mock.patch.object(accounting.time, "thread_time", clock.cpu),
                    mock.patch.object(
                        refresher,
                        "_landing_contracts",
                        clock.wrap("discovery", 2, lambda contract=contract: [contract]),
                    ),
                    mock.patch.object(
                        refresher, "_publish", clock.wrap("publication", 5, refresher._publish)
                    ),
                    mock.patch.object(
                        refresher, "_maybe_freeze", clock.wrap("freeze", 7, refresher._maybe_freeze)
                    ),
                ):
                    task = asyncio.create_task(
                        refresher.refresh_once(now=datetime(2026, 10, 13, tzinfo=UTC))
                    )
                    try:
                        if mode == "cancel":
                            await clock.cancel_twice(task, self)
                        else:
                            await task
                    finally:
                        clock.release.set()
                        if not task.done():
                            task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await task
                self.assertEqual(
                    [name for name, _, _ in clock.trace],
                    ["discovery", "probe"]
                    if mode in {"failure", "cancel"}
                    else ["discovery", "probe", "publication", "freeze"],
                )
                self.assertTrue(
                    all(
                        (ident == clock.loop) == (name in {"publication", "freeze"})
                        for name, ident, _ in clock.trace
                    )
                )
                clock.verify(self, accounting.OWNER_LANDING, units=2 if mode == "cancel" else 3)

    async def test_startup_workers_sync_setup_and_teardown_use_the_same_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clock = _UnitClock()

            async def parked(*_args: object) -> None:
                await asyncio.Future()

            runtime = cast(
                _ServingRuntime,
                SimpleNamespace(
                    config=_config(root),
                    observer_root=root,
                    background_ledger=clock.ledger,
                    clock=lambda: datetime(2026, 10, 13, tzinfo=UTC),
                    projector=SimpleNamespace(prime=mock.AsyncMock(), run=parked),
                    review_trees_shutdown=clock.wrap("shutdown", 5, lambda: None),
                    # The merged runtime carries the explainer's application port (MIK-R186);
                    # this stand-in runs no explainer, so the lifespan admits no move watcher.
                    explainer_application=None,
                ),
            )
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(accounting.time, "thread_time", clock.cpu))
                stack.enter_context(
                    mock.patch.object(
                        serving,
                        "compact_workspace_river",
                        clock.wrap("river", 3, lambda *_a, **_k: None),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        serving, "start_heap_tracing", clock.wrap("heap-setup", 4, lambda: False)
                    )
                )
                stack.enter_context(
                    mock.patch.object(serving, "malloc_trim_enabled", return_value=False)
                )
                for name in (
                    "_metrics_loop",
                    "_leaf_archive_recovery_loop",
                    "_held_delivery_loop",
                    "_workspace_river_compaction_loop",
                    "_warmup_watch_loop",
                ):
                    stack.enter_context(mock.patch.object(serving, name, parked))
                async with serving._serving_lifespan(runtime, mock.Mock())(FastAPI()):
                    self.assertEqual([name for name, _, _ in clock.trace], ["river", "heap-setup"])
                    clock.clocks[clock.loop] = clock.clocks.get(clock.loop, 0) + 1000
            self.assertEqual(
                [name for name, _, _ in clock.trace],
                ["river", "heap-setup", "shutdown"],
            )
            self.assertTrue(
                all(
                    (ident == clock.loop) == (name == "heap-setup")
                    for name, ident, _ in clock.trace
                )
            )
            clock.verify(self, accounting.OWNER_STARTUP, units=3)

    async def test_startup_failure_and_repeated_cancellation_record_the_drained_worker(
        self,
    ) -> None:
        for mode in ("failure", "cancel"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                clock = _UnitClock(mode, "river")
                runtime = cast(
                    _ServingRuntime,
                    SimpleNamespace(
                        config=_config(Path(tmp)),
                        observer_root=Path(tmp),
                        background_ledger=clock.ledger,
                        clock=lambda: datetime(2026, 10, 13, tzinfo=UTC),
                        explainer_application=None,
                    ),
                )

                async def start(runtime=runtime) -> None:
                    async with serving._serving_lifespan(runtime, mock.Mock())(FastAPI()):
                        raise AssertionError(
                            "a failed or cancelled startup compaction must not admit serving"
                        )

                with (
                    mock.patch.object(accounting.time, "thread_time", clock.cpu),
                    mock.patch.object(
                        serving,
                        "compact_workspace_river",
                        clock.wrap("river", 2, lambda *_a, **_k: None),
                    ),
                ):
                    task = asyncio.create_task(start())
                    try:
                        if mode == "cancel":
                            await clock.cancel_twice(task, self)
                        else:
                            with self.assertRaisesRegex(OSError, "injected unit"):
                                await task
                    finally:
                        clock.release.set()
                        if not task.done():
                            task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await task
                self.assertEqual([name for name, _, _ in clock.trace], ["river"])
                self.assertNotEqual(clock.trace[0][1], clock.loop)
                clock.verify(self, accounting.OWNER_STARTUP, units=1)
