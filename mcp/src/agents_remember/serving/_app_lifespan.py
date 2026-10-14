from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from functools import partial
from typing import TYPE_CHECKING

from fastapi import FastAPI

from agents_remember.cli.role_launch_archive_recovery import (
    RECOVERY_TICK_SECONDS,
    LeafArchiveRecovery,
)
from agents_remember.observer.event_retention import (
    WORKSPACE_EVENT_COMPACT_INTERVAL_SECONDS,
    compact_workspace_river,
)
from agents_remember.providers.degradation import evaluate_provider_degradation
from agents_remember.providers.metrics import (
    DEFAULT_SAMPLE_INTERVAL_SECONDS,
    ProviderMetricsStore,
    sample_provider_containers,
)
from agents_remember.providers.watcher_service import run_configured_watchers
from agents_remember.serving._app_common import _ServingRuntime, logger
from agents_remember.serving.heap_diag import (
    heap_diag_loop,
    malloc_trim_enabled,
    malloc_trim_interval_seconds,
    start_heap_tracing,
    trim_malloc,
)

if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import (
        McpRuntimeConfig,
    )


async def _to_thread_drained_on_cancel[**Args, Result](
    function: Callable[Args, Result],
    /,
    *args: Args.args,
    **kwargs: Args.kwargs,
) -> Result:
    """Do not let a cancelled lifespan return while its worker thread still writes."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await worker
        raise


async def _leaf_archive_recovery_loop(runtime: _ServingRuntime) -> None:
    """The serving lifetime's owner of the leaf agents' archive recovery.

    The pass limits itself (one bounded scan per ``RECOVERY_INTERVAL_SECONDS``); this loop owns
    only the attempt cadence. The sleep is taken AFTER each attempt returns, so a slow pass
    delays the next attempt instead of queueing ticks, and two attempts never overlap.
    """

    archives = LeafArchiveRecovery(runtime.config)
    try:
        while True:
            try:
                await _to_thread_drained_on_cancel(archives.tick)
            except Exception:
                logger.exception("leaf agent archive observation failed; retrying next interval")
            await asyncio.sleep(RECOVERY_TICK_SECONDS)
    finally:
        archives.close()


async def _metrics_loop(config: McpRuntimeConfig, metrics_store: ProviderMetricsStore) -> None:
    while True:
        try:
            snapshot = await _to_thread_drained_on_cancel(
                sample_provider_containers, cwd=config.coordination_root
            )
            await _to_thread_drained_on_cancel(metrics_store.record, snapshot)
            await _to_thread_drained_on_cancel(
                evaluate_provider_degradation,
                config,
                stop_provider_stacks=partial(
                    run_configured_watchers,
                    action="stop",
                    dry_run=False,
                ),
            )
            # Reclaim the append-only metrics log (O(1) stat unless past its byte budget).
            await _to_thread_drained_on_cancel(metrics_store.compact)
        except Exception:
            logger.exception("provider metrics sample failed; retrying next interval")
        await asyncio.sleep(DEFAULT_SAMPLE_INTERVAL_SECONDS)


async def _held_delivery_loop(runtime: _ServingRuntime) -> None:
    """Deliver durably held role messages on the dashboard's own tick (MIK-R100 rule 4a).

    The dashboard runs without a model, and a start of the product starts this loop with
    it. While it does not run, messages stay held and are delivered when it runs again.
    A failed sweep never kills the loop; the store keeps everything that still waits.
    """

    from agents_remember.cli.role_held_messages import (  # noqa: PLC0415
        HELD_DELIVERY_SWEEP_SECONDS,
        sweep_held_messages,
    )

    while True:
        try:
            await _to_thread_drained_on_cancel(sweep_held_messages, runtime.config)
        except Exception:
            logger.exception("held-message delivery sweep failed; retrying next interval")
        await asyncio.sleep(HELD_DELIVERY_SWEEP_SECONDS)


async def _malloc_trim_loop() -> None:
    # Opt-in glibc arena reclaim. The steady RSS growth is allocator
    # fragmentation from per-tick projection churn (gc object count is flat while RSS climbs);
    # malloc_trim(0) returns the freed arena pages to the OS and holds RSS flat. Off unless
    # AR_MALLOC_TRIM is set, glibc-only, and run off the event loop since it walks the arenas.
    interval = malloc_trim_interval_seconds()
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(trim_malloc)
        except Exception:
            logger.exception("malloc_trim failed; retrying next interval")


async def _workspace_river_compaction_loop(runtime: _ServingRuntime) -> None:
    while True:
        await asyncio.sleep(WORKSPACE_EVENT_COMPACT_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(
                compact_workspace_river, runtime.observer_root, now=runtime.clock()
            )
        except Exception:
            logger.exception("workspace event-river compaction failed; retrying next interval")


def _serving_lifespan(
    runtime: _ServingRuntime, metrics_store: ProviderMetricsStore
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """The app lifespan: prime the projection, run the background loops, stop them cleanly."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Compact once before accepting clients, then keep compacting on a slow live
        # cadence. Workspace cursors are virtual (base offset + physical offset), and append/compact/read
        # share a cross-process lock, so this is cursor-safe while MCP and serving processes both write.
        await asyncio.to_thread(compact_workspace_river, runtime.observer_root, now=runtime.clock())
        await runtime.projector.prime()
        projection_task = asyncio.create_task(runtime.projector.run())
        metrics_task = asyncio.create_task(_metrics_loop(runtime.config, metrics_store))
        archive_recovery_task = asyncio.create_task(_leaf_archive_recovery_loop(runtime))
        held_delivery_task = asyncio.create_task(_held_delivery_loop(runtime))
        river_compaction_task = asyncio.create_task(_workspace_river_compaction_loop(runtime))
        optional: list[asyncio.Task[None]] = []
        # The heap-growth diagnostic only exists when AR_HEAP_DIAG is set (tracemalloc started here
        # so the very first snapshot has a full trace history); otherwise there is no extra task at all.
        if start_heap_tracing():
            optional.append(asyncio.create_task(heap_diag_loop()))
        # Opt-in RSS bound (glibc arena reclaim), independent of the diagnostic above.
        if malloc_trim_enabled():
            optional.append(asyncio.create_task(_malloc_trim_loop()))
        background = [
            archive_recovery_task,
            river_compaction_task,
            held_delivery_task,
            metrics_task,
            projection_task,
            *optional,
        ]
        try:
            yield
        finally:
            if runtime.review_trees_shutdown is not None:
                await asyncio.to_thread(runtime.review_trees_shutdown)
            for task in background:
                task.cancel()
            for task in background:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    return lifespan
