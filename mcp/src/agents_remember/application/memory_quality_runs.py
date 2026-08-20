"""Bounded background registry for long-running memory-quality checks (L15-R7).

The full contract-scoped check exceeds the MCP client's request window; this
registry runs it on a daemon thread and lets the caller poll a bounded,
evictable result. Runtime store only (D4): a dropped or evicted run simply
needs a rerun -- the check is read-only plus one atomic checklist write.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

MAX_QUALITY_RUNS = 8
QUALITY_RUN_TTL_SECONDS = 1800


@dataclass
class _QualityRun:
    run_id: str
    key: str
    status: str = "running"  # running | completed | failed
    started_at: float = field(default_factory=time.monotonic)
    completed_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


_registry: dict[str, _QualityRun] = {}
_lock = threading.Lock()


def start_quality_run(key: str, fn: Callable[[], dict[str, Any]]) -> tuple[str, str]:
    """Start one background quality run, single-flight per key.

    Returns ``(run_id, status)``: ``("started", ...)`` for a new run, or the
    active run already serving the same key (``status="running"``) so two
    callers cannot race the same checklist write.
    """

    with _lock:
        existing = next(
            (run for run in _registry.values() if run.key == key and run.status == "running"),
            None,
        )
        if existing is not None:
            return existing.run_id, "running"
        _evict_locked()
        run = _QualityRun(run_id=uuid.uuid4().hex[:16], key=key)
        _registry[run.run_id] = run

    def _worker() -> None:
        try:
            result = fn()
        except Exception as exc:  # the run record carries the failure
            with _lock:
                run.status = "failed"
                run.completed_at = time.monotonic()
                run.error = f"{type(exc).__name__}: {exc}"
            return
        with _lock:
            run.status = "completed"
            run.completed_at = time.monotonic()
            run.result = result

    threading.Thread(target=_worker, name=f"quality-run-{run.run_id}", daemon=True).start()
    return run.run_id, "started"


def poll_quality_run(run_id: str) -> dict[str, Any] | None:
    """Return the run envelope, or ``None`` when the run id is unknown/evicted."""

    with _lock:
        run = _registry.get(run_id)
        if run is None:
            return None
        if run.status == "running":
            return {"status": "running", "runId": run.run_id}
        if run.status == "failed":
            return {"status": "failed", "runId": run.run_id, "error": run.error}
        return {"status": "completed", "runId": run.run_id, **(run.result or {})}


def _evict_locked() -> None:
    now = time.monotonic()
    for run_id, run in list(_registry.items()):
        if run.completed_at is not None and now - run.completed_at > QUALITY_RUN_TTL_SECONDS:
            del _registry[run_id]
    if len(_registry) >= MAX_QUALITY_RUNS:
        completed = sorted(
            (run for run in _registry.values() if run.completed_at is not None),
            key=lambda run: run.completed_at or 0.0,
        )
        if completed:
            del _registry[completed[0].run_id]
