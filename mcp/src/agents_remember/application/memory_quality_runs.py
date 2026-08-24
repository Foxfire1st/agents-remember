"""Bounded single-flight registry for asynchronous memory-quality checks.

This process-local store is a working surface, never recovery evidence. Running
work is retained for polling, terminal history is evictable, and new unique work
is refused when live operations occupy the configured capacity.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from agents_remember.application.memory_scope import MemoryScopeIdentity

MAX_QUALITY_RUNS = 8
QUALITY_RUN_TTL_SECONDS = 1800

QualityRunStatus = Literal["running", "completed", "failed"]
QualityRunAdmissionState = Literal["started", "running", "capacity-reached"]


@dataclass(frozen=True)
class QualityRunIdentity:
    """Every canonical request semantic that affects work or its result."""

    repo_id: str
    scope: MemoryScopeIdentity
    checks: tuple[str, ...]
    detail_limit: int
    publish_curator_report: bool


@dataclass(frozen=True)
class QualityRunAdmission:
    state: QualityRunAdmissionState
    run_id: str | None = None

    def __post_init__(self) -> None:
        if (self.state == "capacity-reached") != (self.run_id is None):
            raise ValueError("memory-quality admission state and run id disagree")


@dataclass(frozen=True)
class QualityRunSnapshot:
    status: QualityRunStatus
    run_id: str
    result: Mapping[str, object] | None = None
    error: str | None = None


@dataclass
class _QualityRun:
    run_id: str
    identity: QualityRunIdentity
    status: QualityRunStatus = "running"
    started_at: float = field(default_factory=time.monotonic)
    completed_at: float | None = None
    result: dict[str, object] | None = None
    error: str | None = None


_registry: dict[str, _QualityRun] = {}
_lock = threading.Lock()


def start_quality_run(
    identity: QualityRunIdentity,
    fn: Callable[[], dict[str, object]],
) -> QualityRunAdmission:
    """Reuse active equivalent work or atomically admit one bounded worker."""

    with _lock:
        existing = next(
            (
                run
                for run in _registry.values()
                if run.identity == identity and run.status == "running"
            ),
            None,
        )
        if existing is not None:
            return QualityRunAdmission(state="running", run_id=existing.run_id)
        _prune_terminal_locked()
        if len(_registry) >= MAX_QUALITY_RUNS:
            return QualityRunAdmission(state="capacity-reached")

        run = _QualityRun(run_id=uuid.uuid4().hex[:16], identity=identity)
        worker = threading.Thread(
            target=_complete_quality_run,
            args=(run, fn),
            name=f"quality-run-{run.run_id}",
            daemon=True,
        )
        _registry[run.run_id] = run
        try:
            worker.start()
        except RuntimeError:
            del _registry[run.run_id]
            raise
        return QualityRunAdmission(state="started", run_id=run.run_id)


def poll_quality_run(repo_id: str, run_id: str) -> QualityRunSnapshot | None:
    """Return one repository-owned run without disclosing cross-repo existence."""

    with _lock:
        run = _registry.get(run_id)
        if run is None or run.identity.repo_id != repo_id:
            return None
        return QualityRunSnapshot(
            status=run.status,
            run_id=run.run_id,
            result=None if run.result is None else dict(run.result),
            error=run.error,
        )


def _complete_quality_run(
    run: _QualityRun,
    fn: Callable[[], dict[str, object]],
) -> None:
    try:
        result = fn()
    except Exception as exc:  # the retained run translates arbitrary worker failure
        with _lock:
            run.status = "failed"
            run.completed_at = time.monotonic()
            run.error = f"{type(exc).__name__}: {exc}"
        return
    with _lock:
        run.status = "completed"
        run.completed_at = time.monotonic()
        run.result = result


def _prune_terminal_locked() -> None:
    """Drop expired history, then only enough oldest terminal rows for admission."""

    now = time.monotonic()
    for run_id, run in tuple(_registry.items()):
        if (
            run.status != "running"
            and run.completed_at is not None
            and now - run.completed_at > QUALITY_RUN_TTL_SECONDS
        ):
            del _registry[run_id]

    terminal = sorted(
        (
            run
            for run in _registry.values()
            if run.status != "running" and run.completed_at is not None
        ),
        key=lambda run: run.completed_at or 0.0,
    )
    while len(_registry) >= MAX_QUALITY_RUNS and terminal:
        del _registry[terminal.pop(0).run_id]
