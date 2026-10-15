"""L81 F01/F02: exact thread CPU through the actual recurring owner callers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
import threading
import unittest
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from agents_remember.cli import role_held_messages
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving import _app_lifespan as serving
from agents_remember.serving import background_accounting as accounting
from agents_remember.serving import heap_diag
from agents_remember.serving._app_common import _ServingRuntime
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS


class _EndPass(BaseException):
    """The fixture stops at the next real cadence boundary, never inside a worker."""


@dataclass(frozen=True)
class _Case:
    name: str
    owner: str
    loop: Callable[[accounting.BackgroundLedger], Coroutine[Any, Any, None]]
    stages: tuple[tuple[object, str, object], ...]
    sleeps_before_work: int = 0
    sync_stages: frozenset[int] = frozenset()
    extra_patches: tuple[tuple[object, str, object], ...] = ()
    failure_continues: bool = False


def _cases(runtime: _ServingRuntime, config: McpRuntimeConfig) -> tuple[_Case, ...]:
    store = SimpleNamespace(record=lambda _: None, compact=lambda: None)
    archives = SimpleNamespace(tick=lambda: None, close=lambda: None)
    return (
        _Case(
            "metrics",
            accounting.OWNER_METRICS,
            lambda ledger: serving._metrics_loop(
                config, cast(serving.ProviderMetricsStore, store), ledger
            ),
            (
                (serving, "sample_provider_containers", object()),
                (store, "record", None),
                (serving, "evaluate_provider_degradation", None),
                (store, "compact", None),
            ),
        ),
        _Case(
            "held",
            accounting.OWNER_HELD_DELIVERY,
            lambda ledger: serving._held_delivery_loop(runtime),
            ((role_held_messages, "sweep_held_messages", None),),
        ),
        _Case(
            "river",
            accounting.OWNER_RIVER_COMPACTION,
            lambda ledger: serving._workspace_river_compaction_loop(runtime),
            ((serving, "compact_workspace_river", None),),
            1,
        ),
        _Case(
            "malloc",
            accounting.OWNER_STARTUP,
            serving._malloc_trim_loop,
            ((serving, "trim_malloc", None),),
            1,
        ),
        _Case(
            "warmup-watch",
            accounting.OWNER_WARMUP,
            lambda ledger: serving._warmup_watch_loop(runtime),
            ((serving, "check_host_once", None),),
        ),
        _Case(
            "heap",
            accounting.OWNER_METRICS,
            heap_diag.heap_diag_loop,
            (
                (heap_diag, "take_snapshot", object()),
                (heap_diag, "format_heap_report", "report"),
                (heap_diag.logger, "warning", None),
            ),
            1,
            frozenset({2}),
        ),
        _Case(
            "archive",
            accounting.OWNER_TERMINAL_OBSERVATION,
            lambda ledger: serving._leaf_archive_recovery_loop(runtime),
            ((archives, "tick", None),),
            extra_patches=((serving, "LeafArchiveRecovery", lambda _: archives),),
            failure_continues=True,
        ),
    )


class _Probe:
    def __init__(self, case: _Case, mode: str, stopped_stage: int | None) -> None:
        self.case, self.mode, self.stopped_stage = case, mode, stopped_stage
        self.ledger = accounting.BackgroundLedger()
        self.clocks: dict[int, float] = {}
        self.trace: list[tuple[int, int, float]] = []
        self.loop_ident = threading.get_ident()
        self.started, self.release = threading.Event(), threading.Event()
        self.sleeps = 0
        self.real_sleep = asyncio.sleep

    def clock(self) -> float:
        return self.clocks.get(threading.get_ident(), 0.0)

    async def cadence(self, _seconds: float) -> None:
        self.sleeps += 1
        self.clocks[self.loop_ident] = self.clocks.get(self.loop_ident, 0.0) + 1000
        if self.sleeps > self.case.sleeps_before_work:
            raise _EndPass

    def boundary(self, index: int, result: object) -> Callable[..., object]:
        def call(*_args: object, **_kwargs: object) -> object:
            ident, delta = threading.get_ident(), float(index + 2)
            self.trace.append((index, ident, delta))
            self.clocks[ident] = self.clocks.get(ident, 0.0) + delta
            if self.mode == "cancel" and index == self.stopped_stage:
                self.started.set()
                if not self.release.wait(timeout=HANG_GUARD_SECONDS):
                    raise AssertionError("the fixture failed to release its native worker")
            if self.mode == "failure" and index == self.stopped_stage:
                raise RuntimeError("injected actual leaf-stage failure")
            return result

        return call

    async def execute(self, test: unittest.TestCase) -> None:
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(accounting.time, "thread_time", self.clock))
            stack.enter_context(mock.patch.object(asyncio, "sleep", self.cadence))
            for owner, name, value in self.case.extra_patches:
                stack.enter_context(mock.patch.object(owner, name, value))
            for index, (owner, name, value) in enumerate(self.case.stages):
                stack.enter_context(mock.patch.object(owner, name, self.boundary(index, value)))
            task = asyncio.create_task(self.case.loop(self.ledger))
            try:
                if self.mode == "cancel":
                    async with asyncio.timeout(HANG_GUARD_SECONDS):
                        while not self.started.is_set():
                            await self.real_sleep(0.001)
                    task.cancel()
                    await self.real_sleep(0)
                    task.cancel()
                    await self.real_sleep(0)
                    test.assertFalse(task.done(), "the real worker was orphaned")
                    self.release.set()
                    with test.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    with test.assertRaises(_EndPass):
                        await task
            finally:
                self.release.set()
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, _EndPass):
                        await task

    def assert_proof(self, test: unittest.TestCase) -> dict[str, object]:
        end = (
            len(self.case.stages)
            if self.stopped_stage is None
            or (self.mode == "failure" and self.case.failure_continues)
            else self.stopped_stage + 1
        )
        test.assertEqual([index for index, _, _ in self.trace], list(range(end)))
        test.assertTrue(
            all(
                (ident == self.loop_ident) == (index in self.case.sync_stages)
                for index, ident, _ in self.trace
            )
        )
        owners = self.ledger.snapshot()["owners"]
        assert isinstance(owners, dict)
        for owner, row in owners.items():
            assert isinstance(row, dict)
            test.assertEqual(
                row["cpuSecondsTotal"],
                sum(delta for _, _, delta in self.trace) if owner == self.case.owner else 0,
            )
            test.assertEqual(row["units"], len(self.trace) if owner == self.case.owner else 0)
        return {
            "caller": self.case.name,
            "mode": self.mode,
            "stoppedStage": self.stopped_stage,
            "loopThread": self.loop_ident,
            "trace": self.trace,
            "owners": owners,
        }


class ProductionOwnerClockTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_recurring_caller_attributes_each_stage_and_drains_repeated_cancel(
        self,
    ) -> None:
        evidence = []
        with tempfile.TemporaryDirectory() as tmp:
            config = McpRuntimeConfig(
                config_path=Path(tmp) / "settings.json",
                coordination_root=Path(tmp),
                workspace_root=Path(tmp),
                transcript_root=Path(tmp) / "transcripts",
            )
            fields = SimpleNamespace(
                config=config,
                observer_root=Path(tmp),
                clock=lambda: datetime(2026, 10, 13, tzinfo=UTC),
            )
            for case in _cases(cast(_ServingRuntime, fields), config):
                modes = [("success", None)]
                modes += [
                    (mode, stage)
                    for mode in ("failure", "cancel")
                    for stage in range(len(case.stages))
                    if mode != "cancel" or stage not in case.sync_stages
                ]
                for mode, stopped_stage in modes:
                    with self.subTest(owner=case.name, mode=mode, stage=stopped_stage):
                        probe = _Probe(case, mode, stopped_stage)
                        fields.background_ledger = probe.ledger
                        await probe.execute(self)
                        evidence.append(probe.assert_proof(self))
        print("OWNER-TRACE " + json.dumps(evidence, sort_keys=True))
