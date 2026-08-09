from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from functools import partial
from typing import TYPE_CHECKING

from fastapi import FastAPI

from agents_remember.controlplane.agent_notifier_signals import AgentNotifierSignalCooldownStore
from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.orchestration_nudges import OrchestrationNudgeStore
from agents_remember.kernel.agentic_settings import (
    DEFAULT_AGENT_NOTIFIER_INTERVAL_SECONDS,
    AgenticSettings,
    load_agentic_settings,
)
from agents_remember.observer.event_retention import (
    WORKSPACE_EVENT_COMPACT_INTERVAL_SECONDS,
    compact_workspace_river,
)
from agents_remember.observer.store import EventStore
from agents_remember.providers.degradation import evaluate_provider_degradation
from agents_remember.providers.metrics import (
    DEFAULT_SAMPLE_INTERVAL_SECONDS,
    ProviderMetricsStore,
    sample_provider_containers,
)
from agents_remember.providers.watcher_service import run_configured_watchers
from agents_remember.serving._app_common import _ServingRuntime, logger
from agents_remember.serving.agent_notifier import AgentNotifierContext, run_agent_notifier_sweep
from agents_remember.serving.agent_notifier_heartbeat import (
    AgentNotifierHeartbeatPayload,
    heartbeat_age_seconds,
)
from agents_remember.serving.heap_diag import (
    heap_diag_loop,
    malloc_trim_enabled,
    malloc_trim_interval_seconds,
    start_heap_tracing,
    trim_malloc,
)
from agents_remember.serving.relay_death_watch import relay_death_watch_loop

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig


async def _metrics_loop(config: McpRuntimeConfig, metrics_store: ProviderMetricsStore) -> None:
    while True:
        try:
            snapshot = await asyncio.to_thread(
                sample_provider_containers, cwd=config.coordination_root
            )
            await asyncio.to_thread(metrics_store.record, snapshot)
            await asyncio.to_thread(
                evaluate_provider_degradation,
                config,
                stop_provider_stacks=partial(
                    run_configured_watchers,
                    action="stop",
                    dry_run=False,
                ),
            )
            # Reclaim the append-only metrics log (O(1) stat unless past its byte budget).
            await asyncio.to_thread(metrics_store.compact)
        except Exception:
            logger.exception("provider metrics sample failed; retrying next interval")
        await asyncio.sleep(DEFAULT_SAMPLE_INTERVAL_SECONDS)


def _agent_notifier_context(
    runtime: _ServingRuntime,
    settings: AgenticSettings | None = None,
) -> AgentNotifierContext:
    """One sweep's view of every store its predicates read directly.

    The sweep loop passes its resolved (possibly last-good) settings so the context is built
    from exactly the same snapshot the enabled-check and interval used.
    """

    settings = (
        settings
        if settings is not None
        else load_agentic_settings(runtime.config.coordination_root)
    )
    root = runtime.observer_root
    return AgentNotifierContext(
        catalog=runtime.catalog,
        host=runtime.host,
        paster=runtime.paster,
        inbox_store=OperatorInboxStore(root),
        expectation_store=ExpectationRowStore(root),
        nudge_store=OrchestrationNudgeStore(root),
        signal_cooldown_store=AgentNotifierSignalCooldownStore(root),
        event_store=EventStore(root),
        heartbeat_store=runtime.heartbeat_store,
        coordination_root=runtime.config.coordination_root,
        stale_seat_seconds=max(settings.agent_notifier.interval_seconds * 4, 60.0),
        redeliver_rate_limit_seconds=settings.agent_notifier.redeliver_rate_limit_seconds,
        signal_cooldown_seconds=settings.agent_notifier.signal_cooldown_seconds,
        escalation_sla_seconds=settings.escalation.sla_seconds,
        escalation_rung_seconds=settings.escalation.rung_seconds,
        respawn_after_rung=settings.escalation.respawn_after_rung,
        redeliver_budget=settings.agent_notifier.redeliver_budget,
        escalation_budget=settings.agent_notifier.escalation_budget,
    )


async def _agent_notifier_loop(runtime: _ServingRuntime) -> None:
    """The notifier sweep loop, with last-good settings resilience (R7/N5).

    ``load_agentic_settings`` runs INSIDE the loop's try: a failed read keeps the previous
    good configuration for that sweep, fails loud per tick, and never kills the loop. Only
    with no last-good snapshot at all does the loop skip the sweep and retry next interval.
    """
    last_good: AgenticSettings | None = None
    while True:
        try:
            settings = load_agentic_settings(runtime.config.coordination_root)
            last_good = settings
        except Exception:
            if last_good is None:
                logger.exception(
                    "agent-notifier settings load failed; no last-good settings yet; "
                    "retrying next interval"
                )
                await asyncio.sleep(DEFAULT_AGENT_NOTIFIER_INTERVAL_SECONDS)
                continue
            logger.exception(
                "agent-notifier settings load failed; using last-good settings for this sweep"
            )
            settings = last_good
        try:
            if not settings.agent_notifier.enabled:
                await asyncio.sleep(settings.agent_notifier.interval_seconds)
                continue
            ctx = _agent_notifier_context(runtime, settings=settings)
            await asyncio.to_thread(run_agent_notifier_sweep, ctx, now=runtime.liveness_clock())
        except Exception:
            logger.exception("agent-notifier sweep failed; retrying next interval")
        await asyncio.sleep(settings.agent_notifier.interval_seconds)


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
                compact_workspace_river, runtime.observer_root, now=runtime.liveness_clock()
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
        await asyncio.to_thread(
            compact_workspace_river, runtime.observer_root, now=runtime.liveness_clock()
        )
        await runtime.projector.prime()
        projection_task = asyncio.create_task(runtime.projector.run())
        metrics_task = asyncio.create_task(_metrics_loop(runtime.config, metrics_store))
        agent_notifier_task = asyncio.create_task(_agent_notifier_loop(runtime))
        relay_death_watch_task = asyncio.create_task(relay_death_watch_loop(runtime))
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
            river_compaction_task,
            agent_notifier_task,
            relay_death_watch_task,
            metrics_task,
            projection_task,
            *optional,
        ]
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            for task in background:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            runtime.host.shutdown()

    return lifespan


# --- projection routes --------------------------------------------------------------------------


def _agent_notifier_heartbeat_payload(runtime: _ServingRuntime) -> AgentNotifierHeartbeatPayload:
    # The tick age at RESPONSE time (not the ETag-gated content revision --
    # a heartbeat is deliberately volatile, the same "ages excluded" posture delta.py already
    # applies to other live ages, so it never busts the projection's change-gate revision).
    settings = load_agentic_settings(runtime.config.coordination_root)
    moment = runtime.liveness_clock()
    heartbeat = runtime.heartbeat_store.read()
    age = heartbeat_age_seconds(heartbeat, now=moment)
    stale_cutoff = settings.agent_notifier.stale_cutoff_seconds
    return AgentNotifierHeartbeatPayload(
        lastTickAt=heartbeat.lastTickAt if heartbeat is not None else None,
        ageSeconds=age,
        staleCutoffSeconds=stale_cutoff,
        stale=age is None or age >= stale_cutoff,
        pendingInboxCount=heartbeat.pendingInboxCount if heartbeat is not None else 0,
        redeliverableInboxCount=(heartbeat.redeliverableInboxCount if heartbeat is not None else 0),
        lastSweepDurationSeconds=(
            heartbeat.lastSweepDurationSeconds if heartbeat is not None else None
        ),
    )
