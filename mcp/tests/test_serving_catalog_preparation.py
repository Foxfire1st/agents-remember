"""Cold launcher discovery runs beside serving startup and drains with that lifetime."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from agents_remember.serving import _app_lifespan as serving
from agents_remember.serving._app_common import _ServingRuntime
from agents_remember.serving.stream_shutdown import StreamShutdown
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from fastapi import FastAPI
from test_serving_preview_background import _preview_env, _recorder


class CatalogPreparationLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        self.entered = asyncio.Event()
        self.finished = threading.Event()
        self.loop_thread = threading.get_ident()
        self.catalog_thread: int | None = None
        self.calls = 0
        self.loop = asyncio.get_running_loop()

    def _catalog(self) -> object:
        self.calls += 1
        self.catalog_thread = threading.get_ident()
        self.loop.call_soon_threadsafe(self.entered.set)
        try:
            if not self.release.wait(HANG_GUARD_SECONDS):
                raise AssertionError("catalog fixture gate was not released")
            return object()
        finally:
            self.finished.set()

    def _runtime(self, *, prime: Any = None, prepare: Any = None) -> _ServingRuntime:
        return cast(
            _ServingRuntime,
            SimpleNamespace(
                config=SimpleNamespace(coordination_root=self.root),
                observer_root=self.root,
                projector=SimpleNamespace(
                    close_readiness=mock.Mock(),
                    prime=prime or mock.AsyncMock(),
                    run=_recorder("projection"),
                ),
                clock=lambda: datetime(2026, 10, 25, tzinfo=UTC),
                background_ledger=None,
                review_trees_shutdown=None,
                master_preparation=None,
                explainer_application=None,
                launcher_catalog_prepare=prepare or self._catalog,
                stream_shutdown=StreamShutdown(),
            ),
        )

    @contextlib.contextmanager
    def _quiet_other_owners(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(_preview_env("1"))
            stack.enter_context(mock.patch.object(serving, "compact_workspace_river"))
            stack.enter_context(
                mock.patch.object(serving, "start_heap_tracing", return_value=False)
            )
            stack.enter_context(
                mock.patch.object(serving, "malloc_trim_enabled", return_value=False)
            )
            yield

    async def test_preview_becomes_ready_while_catalog_is_gated_then_drains_on_close(
        self,
    ) -> None:
        primed = asyncio.Event()
        runtime = self._runtime(prime=mock.AsyncMock(side_effect=primed.set))
        with self._quiet_other_owners():
            context = serving._serving_lifespan(runtime, mock.Mock())(FastAPI())
            await asyncio.wait_for(context.__aenter__(), HANG_GUARD_SECONDS)
            closing = None
            try:
                await asyncio.wait_for(self.entered.wait(), HANG_GUARD_SECONDS)
                await asyncio.wait_for(primed.wait(), HANG_GUARD_SECONDS)
                cast(Any, runtime.projector.prime).assert_awaited_once()
                self.assertNotEqual(self.catalog_thread, self.loop_thread)
                self.assertFalse(self.finished.is_set())
                # A callback can run on the loop while the synchronous discovery is still blocked.
                responsive = asyncio.Event()
                asyncio.get_running_loop().call_soon(responsive.set)
                await asyncio.wait_for(responsive.wait(), HANG_GUARD_SECONDS)
                closing = asyncio.create_task(context.__aexit__(None, None, None))
                await asyncio.sleep(0)
                self.assertFalse(closing.done(), "shutdown returned while catalog worker was alive")
                self.release.set()
                await asyncio.wait_for(closing, HANG_GUARD_SECONDS)
            finally:
                self.release.set()
                if closing is None:
                    await context.__aexit__(None, None, None)
                elif not closing.done():
                    await asyncio.wait_for(closing, HANG_GUARD_SECONDS)
        self.assertTrue(self.finished.is_set())
        self.assertEqual(self.calls, 1)

    async def test_readiness_does_not_wait_for_projection_or_master_preparation(self) -> None:
        projection_entered = asyncio.Event()
        projection_release = asyncio.Event()
        master_entered = asyncio.Event()
        master_release = asyncio.Event()
        projection_running = asyncio.Event()
        first_state = asyncio.Event()

        async def prime() -> None:
            projection_entered.set()
            await projection_release.wait()

        async def run() -> None:
            self.assertTrue(projection_release.is_set(), "run overlapped prime")
            projection_running.set()
            first_state.set()
            await asyncio.Future()

        async def prepare_inputs() -> None:
            master_entered.set()
            await master_release.wait()

        runtime = self._runtime(prime=prime)
        cast(Any, runtime.projector).run = run
        cast(Any, runtime.projector).wait_ready = first_state.wait
        cast(Any, runtime).master_preparation = SimpleNamespace(
            prepare_inputs=prepare_inputs, aclose=mock.AsyncMock()
        )
        with self._quiet_other_owners():
            context = serving._serving_lifespan(runtime, mock.Mock())(FastAPI())
            await asyncio.wait_for(context.__aenter__(), HANG_GUARD_SECONDS)
            try:
                await asyncio.wait_for(projection_entered.wait(), HANG_GUARD_SECONDS)
                self.assertFalse(master_entered.is_set(), "startup counters jumped the first state")
                await asyncio.wait_for(self.entered.wait(), HANG_GUARD_SECONDS)
                self.assertFalse(projection_running.is_set())
                self.assertFalse(self.finished.is_set())
                projection_release.set()
                await asyncio.wait_for(projection_running.wait(), HANG_GUARD_SECONDS)
                await asyncio.wait_for(master_entered.wait(), HANG_GUARD_SECONDS)
            finally:
                self.release.set()
                await asyncio.wait_for(context.__aexit__(None, None, None), HANG_GUARD_SECONDS)
        cast(Any, runtime.projector.close_readiness).assert_called_once()
        cast(Any, cast(Any, runtime).master_preparation.aclose).assert_awaited_once()
        self.assertTrue(self.finished.is_set())

    async def test_catalog_failure_is_logged_once_without_failing_serving_or_retrying(
        self,
    ) -> None:
        async_seen = asyncio.Event()

        def fail_catalog() -> None:
            self.calls += 1
            self.loop.call_soon_threadsafe(async_seen.set)
            raise OSError("catalog unavailable")

        with self._quiet_other_owners(), mock.patch.object(serving, "logger") as logger:
            async with serving._serving_lifespan(self._runtime(prepare=fail_catalog), mock.Mock())(
                FastAPI()
            ):
                await asyncio.wait_for(async_seen.wait(), HANG_GUARD_SECONDS)
                # Let the failure reach its coroutine handler without elapsed-time polling.
                for _ in range(3):
                    await asyncio.sleep(0)
            self.assertEqual(logger.exception.call_count, 1)
        self.assertEqual(self.calls, 1)
