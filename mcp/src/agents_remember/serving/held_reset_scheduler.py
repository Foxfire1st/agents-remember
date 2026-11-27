"""Own the deadline wake and the fixed set of threads delivering past a provider reset."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING

from agents_remember.controlplane.held_reset_index import HeldResetIndex
from agents_remember.serving.background_accounting import OWNER_HELD_DELIVERY, measured, offload_on

if TYPE_CHECKING:
    from agents_remember.controlplane.held_reset_index import ResetEntry
    from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
    from agents_remember.serving.background_accounting import BackgroundLedger

logger = logging.getLogger(__name__)

# Peak concurrency is independent of fleet size. The host witness binds this choice.
RESET_WORKERS = 16
# Independent MCP writers cannot signal this process directly. Recheck the indexed
# head on the same interval as turn-end observations, even if the head is far away.
_HEAD_RECHECK_SECONDS = 3.0
_RETRY_DELAY = timedelta(seconds=15)


@dataclass(frozen=True, slots=True)
class ResetAttempt:
    attempted: bool
    delivered: bool = False
    gone: bool = False


@dataclass(frozen=True, slots=True)
class ResetDeliveryPorts:
    adopt: Callable[[HeldResetIndex], None]
    attempt: Callable[[HeldResetIndex, ResetEntry, datetime], ResetAttempt]


@dataclass(frozen=True, slots=True)
class ResetPassResult:
    selected: int
    attempted: int
    delivered: int


def deliver_due_pass(
    index: HeldResetIndex,
    pool: Executor,
    pool_size: int,
    attempt: Callable[[ResetEntry, datetime], ResetAttempt],
    now: datetime,
) -> ResetPassResult:
    """Dispatch the snapshot once and keep submission bounded by the worker count."""
    entries = index.due_at(now)

    def run(entry: ResetEntry) -> ResetAttempt:
        return attempt(entry, datetime.now(UTC) + _RETRY_DELAY)

    attempted = delivered = 0
    for result in pool.map(run, entries, buffersize=pool_size):
        attempted += result.attempted
        delivered += result.delivered
    return ResetPassResult(len(entries), attempted, delivered)


def _deliver(
    ports: ResetDeliveryPorts,
    ledger: BackgroundLedger | None,
    index: HeldResetIndex,
    entry: ResetEntry,
    retry_at: datetime,
) -> ResetAttempt:
    with measured(OWNER_HELD_DELIVERY, ledger):
        return ports.attempt(index, entry, retry_at)


async def _run_due_passes(
    index: HeldResetIndex,
    pool: Executor,
    coordinator: Executor,
    ports: ResetDeliveryPorts,
    ledger: BackgroundLedger | None,
) -> None:
    attempt = partial(_deliver, ports, ledger, index)
    while True:
        delay = _HEAD_RECHECK_SECONDS
        try:
            head = await offload_on(coordinator, OWNER_HELD_DELIVERY, ledger, index.next_attempt)
            if head is not None:
                now = datetime.now(UTC)
                if head <= now:
                    await offload_on(
                        coordinator,
                        OWNER_HELD_DELIVERY,
                        ledger,
                        deliver_due_pass,
                        index,
                        pool,
                        RESET_WORKERS,
                        attempt,
                        now,
                    )
                    continue
                delay = min(delay, (head - now).total_seconds())
        except Exception:
            logger.exception("Could not run the held-message reset pass")
        await asyncio.sleep(delay)


async def run_held_reset_scheduler(
    config: McpRuntimeConfig, ports: ResetDeliveryPorts, ledger: BackgroundLedger | None
) -> None:
    """Load existing waits and run the due path independently of ordinary sweep ticks."""
    index = HeldResetIndex(config.coordination_root)
    with (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="ar-reset-coordinator") as coordinator,
        ThreadPoolExecutor(max_workers=RESET_WORKERS, thread_name_prefix="ar-held-reset") as pool,
    ):
        try:
            await offload_on(coordinator, OWNER_HELD_DELIVERY, ledger, ports.adopt, index)
        except Exception:
            logger.exception("Could not finish enrolling saved held-message resets")
        await _run_due_passes(index, pool, coordinator, ports, ledger)
