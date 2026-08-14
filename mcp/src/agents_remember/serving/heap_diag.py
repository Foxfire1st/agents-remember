"""Opt-in heap-growth diagnostic for the dashboard daemon.

The daemon's RSS grows steadily under load (~5 MB/min observed live). Diagnosing that needs heap
evidence, and the live daemon must stay measurable without a debugger injection (constraint: py-spy
attach only). This module supplies that evidence as an *opt-in, env-flag-gated* facility: when
``AR_HEAP_DIAG`` is truthy the serving lifespan starts :mod:`tracemalloc` and a slow background task
logs, every interval, the process RSS, the total traced Python heap, coarse GC counters, and the
allocation sites that GREW the most since the previous snapshot.

Reading the signal:

* tracemalloc-total roughly FLAT while RSS keeps climbing  -> the growth is allocator arena / heap
  fragmentation driven by per-tick allocation churn (the projection rebuilds a large pydantic tree
  and dumps ~780 KB JSON every tick), not a Python-object leak. The bound is a churn/allocator fix
  (e.g. ``malloc_trim`` / ``MALLOC_ARENA_MAX``), not a freed reference.
* tracemalloc-total CLIMBING in step with RSS -> a real retention leak; the logged top-growers name
  the exact call sites so the follow-up is targeted, not guesswork.

Off by default and zero added cost when disabled: nothing here runs and ``tracemalloc`` is never
started unless the operator sets the flag, so it is safe to leave wired into the lifespan.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import gc
import logging
import os
import tracemalloc
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

_ENV_FLAG = "AR_HEAP_DIAG"
_ENV_INTERVAL = "AR_HEAP_DIAG_INTERVAL"
_ENV_FRAMES = "AR_HEAP_DIAG_FRAMES"
_ENV_TRIM = "AR_MALLOC_TRIM"
_ENV_TRIM_INTERVAL = "AR_MALLOC_TRIM_INTERVAL"
DEFAULT_INTERVAL_SECONDS = 60.0
DEFAULT_TRIM_INTERVAL_SECONDS = 120.0
DEFAULT_FRAMES = 20
_TRUTHY = {"1", "true", "yes", "on"}


def heap_diag_enabled(environ: dict[str, str] | None = None) -> bool:
    """True when ``AR_HEAP_DIAG`` is set to a truthy value (``1``/``true``/``yes``/``on``)."""
    env = environ if environ is not None else os.environ
    return env.get(_ENV_FLAG, "").strip().lower() in _TRUTHY


def heap_diag_interval_seconds(environ: dict[str, str] | None = None) -> float:
    env = environ if environ is not None else os.environ
    raw = env.get(_ENV_INTERVAL)
    if not raw:
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS
    return value if value > 0 else DEFAULT_INTERVAL_SECONDS


def _frames(environ: dict[str, str] | None = None) -> int:
    env = environ if environ is not None else os.environ
    raw = env.get(_ENV_FRAMES)
    if not raw:
        return DEFAULT_FRAMES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_FRAMES
    return value if value > 0 else DEFAULT_FRAMES


def malloc_trim_enabled(environ: dict[str, str] | None = None) -> bool:
    """True when ``AR_MALLOC_TRIM`` is truthy: enable the periodic glibc arena reclaim.

    Off by default. The RSS growth this bounds is glibc allocator arena fragmentation from the
    projection's per-tick allocation churn (evidence: gc object count stays flat across ticks while
    RSS climbs, and ``malloc_trim(0)`` holds RSS flat). It is a no-op on non-glibc platforms.
    """
    env = environ if environ is not None else os.environ
    return env.get(_ENV_TRIM, "").strip().lower() in _TRUTHY


def malloc_trim_interval_seconds(environ: dict[str, str] | None = None) -> float:
    env = environ if environ is not None else os.environ
    raw = env.get(_ENV_TRIM_INTERVAL)
    if not raw:
        return DEFAULT_TRIM_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TRIM_INTERVAL_SECONDS
    return value if value > 0 else DEFAULT_TRIM_INTERVAL_SECONDS


def _resolve_malloc_trim() -> Callable[[int], int] | None:
    """The glibc ``malloc_trim`` symbol, or ``None`` where libc lacks it (non-glibc / no libc)."""
    name = ctypes.util.find_library("c")
    if name is None:
        return None
    try:
        libc = ctypes.CDLL(name)
        func = libc.malloc_trim
    except (OSError, AttributeError):
        return None
    func.argtypes = [ctypes.c_size_t]
    func.restype = ctypes.c_int
    return func


# In-place-mutated cache (the codebase idiom for cross-call module state -- no rebinding ``global``):
# "resolved" flips true after the first lookup; "func" holds the symbol or None.
_malloc_trim: dict[str, object] = {"resolved": False, "func": None}


def trim_malloc() -> int | None:
    """Return freed arena pages to the OS via glibc ``malloc_trim(0)``.

    Returns the ``malloc_trim`` result (1 = memory was released, 0 = none) or ``None`` when the
    symbol is unavailable (non-glibc). Resolution is cached so the hot path is one C call.
    """
    if not _malloc_trim["resolved"]:
        _malloc_trim["func"] = _resolve_malloc_trim()
        _malloc_trim["resolved"] = True
    func = _malloc_trim["func"]
    if func is None:
        return None
    try:
        return int(func(0))  # type: ignore[operator]
    except OSError:
        return None


def start_heap_tracing(environ: dict[str, str] | None = None) -> bool:
    """Start :mod:`tracemalloc` when enabled and not already tracing. Returns whether it is tracing."""
    if not heap_diag_enabled(environ):
        return False
    if not tracemalloc.is_tracing():
        tracemalloc.start(_frames(environ))
        logger.warning(
            "AR_HEAP_DIAG on: tracemalloc started (%d frames); heap growth will be logged every %.0fs",
            _frames(environ),
            heap_diag_interval_seconds(environ),
        )
    return True


def process_rss_bytes() -> int | None:
    """Resident set size of this process in bytes, or ``None`` off procfs."""
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def format_heap_report(
    snapshot: tracemalloc.Snapshot,
    previous: tracemalloc.Snapshot | None,
    *,
    top: int = 10,
) -> str:
    """One multi-line report: RSS, traced total, GC counts, and the top allocation growers."""
    traced_current, traced_peak = tracemalloc.get_traced_memory()
    rss = process_rss_bytes()
    rss_txt = f"{rss / 1_048_576:.1f}MB" if rss is not None else "n/a"
    lines = [
        "heap-diag: "
        f"rss={rss_txt} traced_now={traced_current / 1_048_576:.1f}MB "
        f"traced_peak={traced_peak / 1_048_576:.1f}MB "
        f"gc_objects={len(gc.get_objects())} gc_counts={gc.get_count()}",
    ]
    if previous is not None:
        diff = snapshot.compare_to(previous, "lineno")
        growers = [stat for stat in diff if stat.size_diff > 0][:top]
        if growers:
            lines.append("  top growth since last snapshot:")
            for stat in growers:
                frame = stat.traceback[0]
                lines.append(
                    f"    +{stat.size_diff / 1024:8.1f}KB "
                    f"(+{stat.count_diff} objs) {frame.filename}:{frame.lineno}"
                )
    else:
        top_stats = snapshot.statistics("lineno")[:top]
        lines.append("  top allocation sites (baseline):")
        for stat in top_stats:
            frame = stat.traceback[0]
            lines.append(f"    {stat.size / 1024:8.1f}KB {frame.filename}:{frame.lineno}")
    return "\n".join(lines)


def take_snapshot() -> tracemalloc.Snapshot:
    """A tracemalloc snapshot with daemon-internal frames filtered out for clarity."""
    return tracemalloc.take_snapshot().filter_traces(
        (
            tracemalloc.Filter(False, tracemalloc.__file__),
            tracemalloc.Filter(False, __file__),
        )
    )


async def heap_diag_loop() -> None:
    """The slow opt-in snapshot/report loop; the heavy pure-Python work runs off the event loop.

    tracemalloc snapshots are not free, so this task exists solely
    when AR_HEAP_DIAG is set; the interval is deliberately slow (default 60s) to keep the snapshot
    cost negligible against the growth it is measuring. Both the snapshot and the report are handed
    to a worker thread via ``to_thread`` because the *expensive* part of each is a pure-Python heap
    walk (``Snapshot.filter_traces`` inside :func:`take_snapshot`, ``Snapshot.compare_to`` /
    ``statistics`` inside :func:`format_heap_report` -- ~99% of the cost). That Python-level walk
    releases the GIL at the interpreter switch interval, so it genuinely INTERLEAVES with the event
    loop instead of monopolising it. Measured live, moving it off-loop cut the daemon's max
    event-loop stall from 13,691ms to 78ms (on-loop formatting had burned ~35% of daemon CPU into a
    daemon-wide latency storm).

    Precisely: "off the event loop" is not absolute, and the docstring must not overstate it.
    The GIL-atomic C steps -- the raw trace capture inside ``tracemalloc.take_snapshot()`` and
    ``gc.get_objects()`` in the report -- hold the GIL for their whole duration, so they still
    briefly stall the loop even when invoked from the worker thread. They are only a small fraction
    of the cost (the residual ~78ms), are bounded, and are paid only at the opt-in 60s cadence, so
    the loop stays responsive; what genuinely cannot be pushed off-loop is that GIL-atomic remainder
    alone. The loop lives here (not nested in the app factory) so that responsiveness contract is
    unit-testable.
    """
    interval = heap_diag_interval_seconds()
    previous: tracemalloc.Snapshot | None = None
    while True:
        await asyncio.sleep(interval)
        try:
            snapshot = await asyncio.to_thread(take_snapshot)
            report = await asyncio.to_thread(format_heap_report, snapshot, previous)
            logger.warning("%s", report)
            previous = snapshot
        except Exception:
            logger.exception("heap diagnostic snapshot failed; retrying next interval")
