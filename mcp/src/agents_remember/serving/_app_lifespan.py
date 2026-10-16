from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from functools import partial
from typing import TYPE_CHECKING

from fastapi import FastAPI

from agents_remember.cli.paseo_warmup import (
    WARMUP_POLL_SECONDS,
    check_host_once,
    resume_agent,
)
from agents_remember.cli.paseo_warmup_driver import WarmupDriver
from agents_remember.cli.paseo_warmup_run import WarmupRun, read_available_memory
from agents_remember.cli.paseo_warmup_state import (
    TRACKER as _warmup_tracker,
)
from agents_remember.cli.paseo_warmup_state import WarmupClaim, read_warmup_priority
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
from agents_remember.serving.background_accounting import (
    OWNER_HELD_DELIVERY,
    OWNER_METRICS,
    OWNER_RIVER_COMPACTION,
    OWNER_STARTUP,
    OWNER_TERMINAL_OBSERVATION,
    OWNER_WARMUP,
    BackgroundLedger,
    measured,
    offload,
)
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


async def _to_thread_owned[**Args, Result](
    owner: str,
    ledger: BackgroundLedger | None,
    function: Callable[Args, Result],
    /,
    *args: Args.args,
    **kwargs: Args.kwargs,
) -> Result:
    """The drained handoff with worker-thread owner attribution (F01).

    The worker thread's own CPU is attributed to ``owner``; the event loop's wait is
    never measured, only the thread that runs the unit. Failures record through the
    ``measured`` finally, and cancellation still drains the worker before returning.
    """

    return await offload(owner, ledger, function, *args, **kwargs)


async def _leaf_archive_recovery_loop(runtime: _ServingRuntime) -> None:
    """The serving lifetime's owner of the leaf agents' archive recovery.

    The pass limits itself (one bounded scan per ``RECOVERY_INTERVAL_SECONDS``); this loop owns
    only the attempt cadence. The sleep is taken AFTER each attempt returns, so a slow pass
    delays the next attempt instead of queueing ticks, and two attempts never overlap.
    """

    archives = LeafArchiveRecovery(runtime.config)
    try:
        while True:
            # Attribute each archive recovery attempt to its worker thread,
            # never to the event loop while it waits.
            try:
                await _to_thread_owned(
                    OWNER_TERMINAL_OBSERVATION, runtime.background_ledger, archives.tick
                )
            except Exception:
                logger.exception("leaf agent archive observation failed; retrying next interval")
            await asyncio.sleep(RECOVERY_TICK_SECONDS)
    finally:
        archives.close()


async def _metrics_loop(
    config: McpRuntimeConfig,
    metrics_store: ProviderMetricsStore,
    ledger: BackgroundLedger | None = None,
) -> None:
    while True:
        # One accounted unit per sample stage (MIK-R81 rule 1, F01): each worker
        # thread's own CPU is its unit; the loop's waits are never measured.
        try:
            snapshot = await _to_thread_owned(
                OWNER_METRICS,
                ledger,
                sample_provider_containers,
                cwd=config.coordination_root,
            )
            await _to_thread_owned(OWNER_METRICS, ledger, metrics_store.record, snapshot)
            await _to_thread_owned(
                OWNER_METRICS,
                ledger,
                evaluate_provider_degradation,
                config,
                stop_provider_stacks=partial(
                    run_configured_watchers,
                    action="stop",
                    dry_run=False,
                ),
            )
            # Reclaim the append-only metrics log (O(1) stat unless past its byte budget).
            await _to_thread_owned(OWNER_METRICS, ledger, metrics_store.compact)
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
        # One accounted unit per delivery sweep (MIK-R81 rule 1, F01): the worker
        # thread's own CPU is the unit; the loop's wait is never measured.
        try:
            await _to_thread_owned(
                OWNER_HELD_DELIVERY,
                runtime.background_ledger,
                sweep_held_messages,
                runtime.config,
            )
        except Exception:
            logger.exception("held-message delivery sweep failed; retrying next interval")
        await asyncio.sleep(HELD_DELIVERY_SWEEP_SECONDS)


async def _malloc_trim_loop(ledger: BackgroundLedger | None = None) -> None:
    # Opt-in glibc arena reclaim. The steady RSS growth is allocator
    # fragmentation from per-tick projection churn (gc object count is flat while RSS climbs);
    # malloc_trim(0) returns the freed arena pages to the OS and holds RSS flat. Off unless
    # AR_MALLOC_TRIM is set, glibc-only, and run off the event loop since it walks the arenas.
    # The trim runs as an accounted startup-maintenance unit (MIK-R81 rule 1, F01/F02).
    interval = malloc_trim_interval_seconds()
    while True:
        await asyncio.sleep(interval)
        try:
            await _to_thread_owned(OWNER_STARTUP, ledger, trim_malloc)
        except Exception:
            logger.exception("malloc_trim failed; retrying next interval")


async def _workspace_river_compaction_loop(runtime: _ServingRuntime) -> None:
    while True:
        await asyncio.sleep(WORKSPACE_EVENT_COMPACT_INTERVAL_SECONDS)
        # One accounted unit per compaction (MIK-R81 rule 1, F01): the worker
        # thread's own CPU is the unit; the loop's wait is never measured.
        try:
            await offload(
                OWNER_RIVER_COMPACTION,
                runtime.background_ledger,
                compact_workspace_river,
                runtime.observer_root,
                now=runtime.clock(),
            )
        except Exception:
            logger.exception("workspace event-river compaction failed; retrying next interval")


async def _drive_warmup_run(
    runtime: _ServingRuntime,
    run: WarmupRun,
    claim: WarmupClaim,
    end_note: dict[str, str | None],
) -> None:
    """Drive one host start's warmup pass to its record, whatever it costs.

    The pass never fails the loop: a resume that raises is the run's note, and
    anything else is logged. Cancellation (lifespan exit, a newer start, the host
    going away) propagates so the driver still drains; the end note tells whether
    the pass finished or was retired, and the record says which.
    """
    try:
        home = claim.path.parent

        async def _measured_resume(agent_id: str) -> None:
            """One warmup resume, attributed to the warmup owner on its worker (F01/F02)."""
            await offload(
                OWNER_WARMUP,
                runtime.background_ledger,
                resume_agent,
                runtime.config,
                agent_id,
                run.role_receipts[agent_id],
            )

        def progress() -> None:
            with measured(OWNER_WARMUP, runtime.background_ledger):
                _warmup_tracker.write_progress()

        def priority() -> list[str]:
            with measured(OWNER_WARMUP, runtime.background_ledger):
                return read_warmup_priority(home)

        def memory() -> int | None:
            with measured(OWNER_WARMUP, runtime.background_ledger):
                return read_available_memory()

        await run.run(
            resume_fn=_measured_resume,
            mem_fn=memory,
            progress_fn=progress,
            priority_fn=priority,
        )
    except asyncio.CancelledError:
        end_note["end_reason"] = (
            end_note.get("end_reason") or "the loading pass was cancelled before it finished"
        )
        raise
    except Exception as error:
        end_note["end_reason"] = f"the loading pass failed: {error}"
        logger.exception("host warmup pass failed")
    finally:
        reason = end_note.get("end_reason")
        with measured(OWNER_WARMUP, runtime.background_ledger):
            if reason is None:
                _warmup_tracker.note_run_finished(run, claim)
            else:
                _warmup_tracker.note_run_ended(run, claim, reason)


async def _warmup_watch_loop(runtime: _ServingRuntime) -> None:
    """Notice a start of the chat host and warm the tree's agents (MIK-R145).

    The watch is server-side, not page-driven: one light live listing per
    interval notices a ``serverId`` never seen in this process, with no page
    open or with many. The listing is one accounted background unit (MIK-R81
    rule 1); the pass it starts is accounted per resume the same way. A pass of
    a previous start that is still going is retired when the new one starts, and
    every driving pass ends with its record when the host goes away (F26).
    """

    def drive(
        run: WarmupRun, claim: WarmupClaim, end_note: dict[str, str | None]
    ) -> asyncio.Task[None]:
        return asyncio.create_task(_drive_warmup_run(runtime, run, claim, end_note))

    driver = WarmupDriver(_warmup_tracker)
    try:
        while True:
            # The listing is one accounted background unit (MIK-R81 rule 1, F01):
            # the worker thread's own CPU is the unit, never the loop's wait.
            try:
                claimed = await offload(
                    OWNER_WARMUP, runtime.background_ledger, check_host_once, runtime.config
                )
            except Exception:
                logger.exception("host warmup watch failed; retrying next interval")
                claimed = None
            if await driver.noticed(claimed, drive):
                continue
            await asyncio.sleep(WARMUP_POLL_SECONDS)
    finally:
        await driver.aclose()


def _serving_lifespan(
    runtime: _ServingRuntime, metrics_store: ProviderMetricsStore
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """The app lifespan: prime the projection, run the background loops, stop them cleanly."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Compact once before accepting clients, then keep compacting on a slow live
        # cadence. Workspace cursors are virtual (base offset + physical offset), and append/compact/read
        # share a cross-process lock, so this is cursor-safe while MCP and serving processes both write.
        await offload(
            OWNER_STARTUP,
            runtime.background_ledger,
            compact_workspace_river,
            runtime.observer_root,
            now=runtime.clock(),
        )
        await runtime.projector.prime()
        projection_task = asyncio.create_task(runtime.projector.run())
        metrics_task = asyncio.create_task(
            _metrics_loop(runtime.config, metrics_store, runtime.background_ledger)
        )
        archive_recovery_task = asyncio.create_task(_leaf_archive_recovery_loop(runtime))
        held_delivery_task = asyncio.create_task(_held_delivery_loop(runtime))
        river_compaction_task = asyncio.create_task(_workspace_river_compaction_loop(runtime))
        warmup_watch_task = asyncio.create_task(_warmup_watch_loop(runtime))
        optional: list[asyncio.Task[None]] = []
        # The heap-growth diagnostic only exists when AR_HEAP_DIAG is set (tracemalloc started here
        # so the very first snapshot has a full trace history); otherwise there is no extra task at all.
        with measured(OWNER_STARTUP, runtime.background_ledger):
            heap_enabled = start_heap_tracing()
        if heap_enabled:
            optional.append(asyncio.create_task(heap_diag_loop(runtime.background_ledger)))
        # Opt-in RSS bound (glibc arena reclaim), independent of the diagnostic above.
        if malloc_trim_enabled():
            optional.append(asyncio.create_task(_malloc_trim_loop(runtime.background_ledger)))
        background = [
            archive_recovery_task,
            river_compaction_task,
            held_delivery_task,
            warmup_watch_task,
            metrics_task,
            projection_task,
            *optional,
        ]
        try:
            yield
        finally:
            if runtime.review_trees_shutdown is not None:
                # Shutdown work is startup-owned teardown: attributed and drained, so a
                # cancelled shutdown still waits for the worker before returning (F01).
                await _to_thread_owned(
                    OWNER_STARTUP, runtime.background_ledger, runtime.review_trees_shutdown
                )
            for task in background:
                task.cancel()
            for task in background:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    return lifespan
