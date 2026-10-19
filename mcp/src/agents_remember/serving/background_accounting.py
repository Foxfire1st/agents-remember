"""Per-owner background-work accounting for the installed dashboard (MIK-R81 rule 1).

Each background owner records the processor time of its units of work, and the
projector records every wake with its reason. The totals since the start and over
the last 60 s are served by one read-only route (``GET
/api/diagnostics/background``) and printed by ``agents-remember dashboard
--status``. The accounting is always on and costs less than 1 percent of a
processor: recording one unit is a timestamp read plus a bounded-deque append,
and every structure is capped with drop-oldest reclamation (D2/D3), so the
ledger itself cannot grow with uptime or input size.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field

#: Threads the background owners run on. Their own pool, never the loop's default executor:
#: the default one is what Starlette runs a sync route on, so before this the projection and a
#: review read shared a queue and a long projection unit could hold the thread a read needed
#: (MIK-R179 rule 2). Four covers the loops that block -- projection, change watcher, landing
#: refresh, held delivery, event tail and metrics -- and keeps a runaway unit's cost bounded.
BACKGROUND_WORKERS = 4

#: Seconds of history the "last 60 s" columns cover (MIK-R81 rule 1).
WINDOW_SECONDS = 60.0

#: How many per-unit samples one owner keeps. At one projection tick per second
#: this is ten minutes; older samples leave from the left and a counter says so.
UNIT_SAMPLES = 600

#: How many projector wakes are kept with their reason. At the fastest cadence
#: (one wake per second) this is four minutes of wakes; older ones leave from
#: the left and a counter says so.
WAKE_SAMPLES = 240

#: How many process-CPU samples are kept for the trailing-window share. One
#: sample rides each recorded unit, so this is a time bound only through the
#: busiest owner's unit rate, and old samples leave from the left.
PROCESS_SAMPLES = 600

#: Background owners, in the order the status line prints them. These are the
#: loops the serving lifespan starts, named where they are started
#: (``serving/_app_lifespan.py``, ``serving/projector.py``,
#: ``serving/projections/landing_state.py``), plus the client-driven raw-event
#: tail and serving startup, which are dashboard work with the same account.
OWNER_PROJECTION = "projection"
OWNER_LANDING = "landing-refresh"
OWNER_TERMINAL_OBSERVATION = "terminal-observation"
OWNER_METRICS = "metrics"
OWNER_HELD_DELIVERY = "held-delivery"
OWNER_RIVER_COMPACTION = "river-compaction"
OWNER_WARMUP = "warmup"
OWNER_EVENT_TAIL = "event-tail"
OWNER_STARTUP = "startup"

BACKGROUND_OWNERS: tuple[str, ...] = (
    OWNER_PROJECTION,
    OWNER_LANDING,
    OWNER_TERMINAL_OBSERVATION,
    OWNER_METRICS,
    OWNER_HELD_DELIVERY,
    OWNER_RIVER_COMPACTION,
    OWNER_WARMUP,
    OWNER_EVENT_TAIL,
    OWNER_STARTUP,
)


@dataclass
class _UnitSample:
    at: float
    at_mono: float
    cpu_seconds: float
    wall_seconds: float


@dataclass
class _WakeSample:
    at: float
    at_mono: float
    reason: str
    domains: tuple[str, ...]
    path_count: int
    sample_paths: tuple[str, ...] = ()


@dataclass
class OwnerLedger:
    """The bounded account of one owner: totals since start plus an exact window.

    Lifetime totals are exact unbounded counters. The trailing window is exact too (F04):
    per-second aggregate buckets sum every unit in fully-covered seconds however fast units
    arrive, while the oldest partially-covered second is summed from exact per-sample
    monotonic timestamps, never from the whole second. The raw per-unit sample deque stays
    a capped detail view; when the cap drops a sample from the oldest partial second the
    window says so (``complete`` is False) instead of certifying an approximate sum.
    Buckets are keyed by whole monotonic seconds, so wall-clock jumps cannot corrupt
    expiry; fully-expired seconds are pruned, so at most a bounded few buckets exist.
    """

    units: int = 0
    cpu_seconds_total: float = 0.0
    wall_seconds_total: float = 0.0
    dropped_samples: int = 0
    _samples: deque[_UnitSample] = field(default_factory=lambda: deque(maxlen=UNIT_SAMPLES))
    _buckets: dict[int, list[float]] = field(default_factory=dict)
    # Cap-evicted monotonic times by whole second: the newest eviction per second. Only an
    # eviction from the oldest partially-covered second at or after the cutoff breaks that
    # second's exactness; anything older expired correctly. Keys older than the window are
    # pruned, so the map holds a bounded few seconds however fast units arrive.
    _evicted: dict[int, float] = field(default_factory=dict)

    def record(self, cpu_seconds: float, wall_seconds: float, *, at: float, at_mono: float) -> None:
        self.units += 1
        self.cpu_seconds_total += cpu_seconds
        self.wall_seconds_total += wall_seconds
        if len(self._samples) == self._samples.maxlen:
            evicted = self._samples[0]
            self.dropped_samples += 1
            second = int(evicted.at_mono)
            if evicted.at_mono > self._evicted.get(second, -1.0):
                self._evicted[second] = evicted.at_mono
        self._samples.append(_UnitSample(at, at_mono, cpu_seconds, wall_seconds))
        bucket = self._buckets.get(int(at_mono))
        if bucket is None:
            bucket = [0.0, 0.0, 0]
            self._buckets[int(at_mono)] = bucket
        bucket[0] += cpu_seconds
        bucket[1] += wall_seconds
        bucket[2] += 1
        self._prune(at_mono)

    def _prune(self, now_mono: float) -> None:
        cutoff = now_mono - WINDOW_SECONDS
        for key in [key for key in self._buckets if key + 1 <= cutoff]:
            del self._buckets[key]
        while self._samples and self._samples[0].at_mono < cutoff:
            self._samples.popleft()
        if len(self._evicted) > 128:
            cutoff_second = int(cutoff)
            for second in [second for second in self._evicted if second < cutoff_second]:
                del self._evicted[second]

    def window(self, *, now_mono: float) -> tuple[float, float, int, bool]:
        """Exact (cpu, wall, units, complete) over the trailing 60 s window (F04).

        Fully-covered seconds come from the complete per-second buckets; the oldest
        partially-covered second comes from exact per-sample timestamps at or after the
        cutoff. ``complete`` is False when the sample cap dropped a sample from that
        oldest second, in which case the sums cover the retained samples only — an
        honest incomplete window, never an approximate whole-second one.
        """

        cutoff = now_mono - WINDOW_SECONDS
        cutoff_second = int(cutoff)  # monotonic clocks are non-negative; truncation is floor
        cpu = wall = 0.0
        units = 0
        for key, bucket in self._buckets.items():
            if key > cutoff_second:
                cpu += bucket[0]
                wall += bucket[1]
                units += int(bucket[2])
        for sample in self._samples:
            if sample.at_mono >= cutoff and int(sample.at_mono) == cutoff_second:
                cpu += sample.cpu_seconds
                wall += sample.wall_seconds
                units += 1
        complete = self._evicted.get(cutoff_second, -1.0) < cutoff
        return cpu, wall, units, complete


class BackgroundLedger:
    """The always-on account of the dashboard's own background work.

    One instance belongs to one serving app (built in ``_build_serving_runtime``
    and carried on the runtime). Units are recorded from worker threads (the
    projection tick) and from the event loop thread (every other loop), so one
    lock guards the counters; it is never held across I/O.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._owners: dict[str, OwnerLedger] = {name: OwnerLedger() for name in BACKGROUND_OWNERS}
        self._wakes: deque[_WakeSample] = deque(maxlen=WAKE_SAMPLES)
        self._wake_reasons: dict[str, int] = {}
        self._dropped_wakes = 0
        # Exact trailing-window wake counts by reason (F04): per-second buckets keep every
        # wake in fully-covered seconds, and the oldest partially-covered second is counted
        # from exact per-wake timestamps; cap evictions from that oldest second mark the
        # window incomplete instead of certifying an approximate count.
        self._wake_buckets: dict[int, dict[str, int]] = {}
        self._wake_evicted: dict[int, float] = {}
        self._process_samples: deque[tuple[float, float]] = deque(maxlen=PROCESS_SAMPLES)
        self._note_process_sample(time.monotonic())

    def record(self, owner: str, cpu_seconds: float, wall_seconds: float) -> None:
        """Record one finished unit of work for ``owner`` (unknown owners are kept too)."""
        now_wall = time.time()
        now_mono = time.monotonic()
        with self._lock:
            ledger = self._owners.get(owner)
            if ledger is None:
                ledger = OwnerLedger()
                self._owners[owner] = ledger
            ledger.record(
                max(0.0, cpu_seconds), max(0.0, wall_seconds), at=now_wall, at_mono=now_mono
            )
            self._note_process_sample(now_mono)

    def note_wake(
        self,
        reason: str,
        domains: tuple[str, ...],
        path_count: int,
        sample_paths: tuple[str, ...] = (),
    ) -> None:
        """Record one projector wake with its reason (MIK-R81 rule 1: every wake)."""
        with self._lock:
            now_wall = time.time()
            now_mono = time.monotonic()
            if len(self._wakes) == self._wakes.maxlen:
                self._dropped_wakes += 1
                evicted = self._wakes[0]
                second = int(evicted.at_mono)
                if evicted.at_mono > self._wake_evicted.get(second, -1.0):
                    self._wake_evicted[second] = evicted.at_mono
            self._wakes.append(
                _WakeSample(now_wall, now_mono, reason, domains, max(0, path_count), sample_paths)
            )
            self._wake_reasons[reason] = self._wake_reasons.get(reason, 0) + 1
            bucket = self._wake_buckets.get(int(now_mono))
            if bucket is None:
                bucket = {}
                self._wake_buckets[int(now_mono)] = bucket
            bucket[reason] = bucket.get(reason, 0) + 1
            self._prune_wakes_locked(now_mono)
            self._note_process_sample(now_mono)

    def snapshot(self) -> dict[str, object]:
        """The JSON-ready body of ``GET /api/diagnostics/background``."""
        with self._lock:
            now_mono = time.monotonic()
            self._note_process_sample(now_mono)
            owners = {}
            for name, ledger in self._owners.items():
                cpu_window, wall_window, units_window, complete = ledger.window(now_mono=now_mono)
                owners[name] = {
                    "units": ledger.units,
                    "cpuSecondsTotal": ledger.cpu_seconds_total,
                    "wallSecondsTotal": ledger.wall_seconds_total,
                    "cpuSecondsLast60s": cpu_window,
                    "wallSecondsLast60s": wall_window,
                    "unitsLast60s": units_window,
                    "windowComplete": complete,
                    "droppedSamples": ledger.dropped_samples,
                }
            window_wakes, wakes_complete = self._window_wakes_locked(now_mono)
            last60s = sum(window_wakes.values())
            wakes = {
                "total": sum(self._wake_reasons.values()),
                "byReason": dict(self._wake_reasons),
                "last60s": last60s,
                "last60sByReason": window_wakes,
                "windowComplete": wakes_complete,
                "recent": [
                    {
                        "at": wake.at,
                        "reason": wake.reason,
                        "domains": list(wake.domains),
                        "pathCount": wake.path_count,
                        "samplePaths": list(wake.sample_paths),
                    }
                    for wake in list(self._wakes)[-20:]
                ],
                "droppedWakes": self._dropped_wakes,
            }
            share, share_span, share_complete = self._process_share_locked(now_mono)
            body: dict[str, object] = {
                "startedAt": self._started_at,
                "windowSeconds": WINDOW_SECONDS,
                "processCpuShareLast60s": share,
                "processCpuWindow": {
                    "spanSeconds": share_span,
                    "complete": share_complete,
                },
                "owners": owners,
                "wakes": wakes,
            }
            return body

    def _prune_wakes_locked(self, now_mono: float) -> None:
        """Drop fully-expired wake buckets, detail and eviction marks (F04)."""

        cutoff = now_mono - WINDOW_SECONDS
        for key in [key for key in self._wake_buckets if key + 1 <= cutoff]:
            del self._wake_buckets[key]
        while self._wakes and self._wakes[0].at_mono < cutoff:
            self._wakes.popleft()
        if len(self._wake_evicted) > 128:
            cutoff_second = int(cutoff)
            for second in [second for second in self._wake_evicted if second < cutoff_second]:
                del self._wake_evicted[second]

    def _window_wakes_locked(self, now_mono: float) -> tuple[dict[str, int], bool]:
        """Exact per-reason wake counts over the trailing window, plus completeness (F04).

        Fully-covered seconds come from the complete per-second buckets; the oldest
        partially-covered second is counted from exact per-wake timestamps at or after
        the cutoff. ``complete`` is False when the detail cap dropped a wake from that
        oldest second.
        """

        cutoff = now_mono - WINDOW_SECONDS
        cutoff_second = int(cutoff) if cutoff >= 0 else int(cutoff) - 1
        counts: dict[str, int] = {}
        for key, bucket in self._wake_buckets.items():
            if key > cutoff_second:
                for reason, count in bucket.items():
                    counts[reason] = counts.get(reason, 0) + count
        for wake in self._wakes:
            if wake.at_mono >= cutoff and int(wake.at_mono) == cutoff_second:
                counts[wake.reason] = counts.get(wake.reason, 0) + 1
        complete = self._wake_evicted.get(cutoff_second, -1.0) < cutoff
        return counts, complete

    def status_line(self) -> str:
        """The one line ``dashboard --status`` prints for the background account."""
        with self._lock:
            now_mono = time.monotonic()
            total_60s = sum(ledger.window(now_mono=now_mono)[0] for ledger in self._owners.values())
            share = total_60s / WINDOW_SECONDS
            wakes_60s = sum(self._window_wakes_locked(now_mono)[0].values())
            return (
                f"background work: {100.0 * share:.1f}% of one processor over the last 60 s "
                f"({wakes_60s} projection wakes)"
            )

    def _note_process_sample(self, now_mono: float) -> None:
        cpu = _process_cpu_seconds()
        self._process_samples.append((now_mono, cpu))
        cutoff = now_mono - WINDOW_SECONDS
        # Prune past the share's boundary: the newest sample at or before the cutoff stays,
        # everything older leaves. At most PROCESS_SAMPLES readings exist at any moment.
        while (
            len(self._process_samples) > 2
            and self._process_samples[0][0] < cutoff
            and self._process_samples[1][0] <= cutoff
        ):
            self._process_samples.popleft()

    def _process_share_locked(self, now_mono: float) -> tuple[float | None, float, bool]:
        """This process's CPU share over the trailing window, with its honest span (F04).

        The share runs from the newest sample at or before the cutoff to now; the span
        says how far back that reaches. Only a sample exactly at the cutoff proves a
        complete trailing window: an older boundary extends the interval, and a newer
        one shortens it. Both are reported as incomplete, with their actual span;
        CPU between samples is never inferred by interpolation.
        """

        cpu_now = _process_cpu_seconds()
        cutoff = now_mono - WINDOW_SECONDS
        boundary: tuple[float, float] | None = None
        for sample in self._process_samples:
            if sample[0] <= cutoff:
                boundary = sample
            else:
                break
        if boundary is None:
            if not self._process_samples:
                return None, 0.0, False
            boundary = self._process_samples[0]
        span = now_mono - boundary[0]
        if span <= 0:
            return None, 0.0, False
        return max(0.0, (cpu_now - boundary[1]) / span), span, boundary[0] == cutoff


def describe_snapshot(payload: dict[str, object]) -> str:
    """The lines ``dashboard --status`` prints for a diagnostics payload (F03).

    The first line keeps the aggregate the route always had; the following lines expose
    the required cumulative and trailing per-owner totals plus projector wake
    diagnostics. Best-effort: anything unreadable in the payload becomes an unavailable
    line, never an exception, because the status command must keep working when the
    route is old, new, or failing.
    """
    try:
        owners = payload.get("owners")
        wakes = payload.get("wakes")
        if not isinstance(owners, dict) or not isinstance(wakes, dict):
            raise ValueError("unreadable background diagnostics")
        window = payload.get("windowSeconds", WINDOW_SECONDS)
        window_float = float(window) if isinstance(window, (int, float)) else WINDOW_SECONDS
        lines: list[str] = []
        total_60s = 0.0
        owner_lines: list[str] = []
        for name in sorted(owners):
            owner = owners[name]
            if not isinstance(owner, dict):
                continue
            units = owner.get("units", 0)
            cpu_total = owner.get("cpuSecondsTotal")
            wall_total = owner.get("wallSecondsTotal")
            cpu_window = owner.get("cpuSecondsLast60s")
            wall_window = owner.get("wallSecondsLast60s")
            units_window = owner.get("unitsLast60s", units)
            window_complete = owner.get("windowComplete", True)
            if isinstance(cpu_window, (int, float)):
                total_60s += float(cpu_window)
            completeness = "" if window_complete else " (incomplete window)"
            owner_lines.append(
                f"  {name}: {units} units, "
                f"{_format_number(cpu_total)} CPU-s total / "
                f"{_format_number(cpu_window)} CPU-s last 60 s, "
                f"{_format_number(wall_total)} wall-s total / "
                f"{_format_number(wall_window)} wall-s last 60 s, "
                f"{units_window} units last 60 s{completeness}"
            )
        last_60s = wakes.get("last60s", 0)
        count = int(last_60s) if isinstance(last_60s, int) else 0
        wakes_complete = wakes.get("windowComplete", True)
        wakes_completeness = "" if wakes_complete else " (incomplete window)"
        share = total_60s / window_float if window_float > 0 else 0.0
        lines.append(
            f"background work: {100.0 * share:.1f}% of one processor over the last 60 s "
            f"({count} projection wakes{wakes_completeness})"
        )
        lines.extend(owner_lines)
        by_reason = wakes.get("byReason", {})
        last_by_reason = wakes.get("last60sByReason", {})
        if isinstance(by_reason, dict) or isinstance(last_by_reason, dict):
            lines.append(
                f"  wakes: {wakes.get('total', 0)} total "
                f"({_format_counts(by_reason)}), last 60 s: {count} "
                f"({_format_counts(last_by_reason)}){wakes_completeness}"
            )
        recent = wakes.get("recent", [])
        if isinstance(recent, list) and recent:
            lines.extend(_format_recent_wakes(recent))
        process_share = payload.get("processCpuShareLast60s")
        process_window = payload.get("processCpuWindow", {})
        span = process_window.get("spanSeconds") if isinstance(process_window, dict) else None
        span_text = f" over {_format_number(span)} s" if isinstance(span, (int, float)) else ""
        process_complete = (
            isinstance(process_window, dict) and process_window.get("complete") is True
        )
        process_detail = "" if process_complete else " (incomplete window)"
        lines.append(
            f"  process CPU share: {_format_share(process_share)}{span_text}{process_detail}"
        )
        return "\n".join(lines)
    except (ValueError, TypeError, AttributeError) as error:
        return f"background work: unavailable ({error})"


def _format_number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unknown"
    return f"{float(value):.3f}"


def _format_counts(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return "none"
    parts = []
    for reason in sorted(value):
        count = value[reason]
        parts.append(f"{reason} {count}" if isinstance(count, int) else f"{reason} unknown")
    return ", ".join(parts)


def _format_share(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unknown"
    return f"{100.0 * float(value):.1f}% of one processor"


def _format_recent_wakes(recent: list[object]) -> list[str]:
    """The latest wake diagnostics: reason, domains and contributing paths (F03)."""

    lines: list[str] = []
    for entry in recent[-5:]:
        if not isinstance(entry, dict):
            continue
        reason = entry.get("reason", "unknown")
        domains = entry.get("domains", [])
        domain_text = (
            ",".join(str(domain) for domain in domains) if isinstance(domains, list) else ""
        )
        path_count = entry.get("pathCount", 0)
        paths = entry.get("samplePaths", [])
        sample_text = ",".join(str(path) for path in paths[:3]) if isinstance(paths, list) else ""
        lines.append(
            f"  wake {reason} [{domain_text}]: {path_count} paths"
            + (f" ({sample_text})" if sample_text else "")
        )
    return lines


def _process_cpu_seconds() -> float:
    """This process's total user + system CPU seconds across all its threads."""
    times = os.times()
    return times.user + times.system


@contextmanager
def measured(owner: str, ledger: BackgroundLedger | None) -> Iterator[None]:
    """Record the calling thread's CPU and wall cost of one synchronous unit under ``owner``.

    A no-ledger call measures nothing, so instrumented code needs no branch.
    ``thread_time`` is the calling thread's own processor time (MIK-R81 rule 1:
    "thread time before and after"), which is why synchronous units are measured
    on the thread that runs them.
    """
    if ledger is None:
        yield
        return
    cpu_before = time.thread_time()
    wall_before = time.monotonic()
    try:
        yield
    finally:
        ledger.record(owner, time.thread_time() - cpu_before, time.monotonic() - wall_before)


class _BackgroundPool:
    """The one executor, created on first use and never resized."""

    executor: ThreadPoolExecutor | None = None


_BACKGROUND_POOL_LOCK = threading.Lock()


def _background_pool() -> ThreadPoolExecutor:
    """The background owners' own threads, created once and reused.

    A read that runs on the loop's default executor never queues behind background work, and
    background work never takes a thread a read needs. The pool is process-wide because the
    owners are, and it is never resized: a size that followed the moment would reintroduce the
    contention this exists to remove.
    """

    with _BACKGROUND_POOL_LOCK:
        if _BackgroundPool.executor is None:
            _BackgroundPool.executor = ThreadPoolExecutor(
                max_workers=BACKGROUND_WORKERS, thread_name_prefix="ar-background"
            )
        return _BackgroundPool.executor


async def _background_thread[R](func: Callable[[], R]) -> R:
    """One owned thread unit, with the same task boundary as ``asyncio.to_thread``."""
    context = contextvars.copy_context()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_background_pool(), context.run, func)


async def offload[R](
    owner: str,
    ledger: BackgroundLedger | None,
    function: Callable[..., R],
    /,
    *args: object,
    **kwargs: object,
) -> R:
    """Run ``function`` in a worker thread, attributing that thread's CPU to ``owner`` (F01).

    The async owner awaiting the thread must never measure itself: the event loop's wait
    covers sleep and every other owner's overlapping work, while the worker thread's own
    ``thread_time`` is exactly this unit. Failures record too (``measured`` finally),
    so the error path carries the same attribution as the success path. Cancellation
    drains: the worker thread cannot be cancelled, so a cancelled wait still awaits the
    worker's completion before the cancellation propagates, and the unit's final delta is
    recorded rather than lost with the waiter.
    """

    def _run() -> R:
        with measured(owner, ledger):
            return function(*args, **kwargs)

    _run.__wrapped__ = function  # type: ignore[attr-defined]
    # Keep an explicit task for the exact thread unit: shutdown shields and drains this
    # task even when its owner is cancelled repeatedly. Its executor stays background-only.
    worker = asyncio.create_task(_background_thread(_run))
    try:
        return await asyncio.shield(worker)
    except BaseException:
        while not worker.done():
            try:
                await asyncio.wait((worker,))
            except asyncio.CancelledError:
                # A second shutdown cancellation must not orphan the still-running unit.
                continue
        if not worker.cancelled():
            worker.exception()
        raise


def record_sync(
    owner: str, ledger: BackgroundLedger | None, cpu_seconds: float, wall_seconds: float
) -> None:
    """Record one finished synchronous unit; a no-ledger call is a no-op."""
    if ledger is not None:
        ledger.record(owner, cpu_seconds, wall_seconds)
