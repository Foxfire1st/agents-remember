"""Durable phase-progress reporting for background provider setup (GitHub #53).

`SetupProgress` is the no-op sink provider setup announces phases through;
`SetupProgressFile` persists those events as JSON so `worktree_status` (and any
dashboard) can observe a running setup without holding the tool call open. The
file is heartbeat-stamped: a reader distinguishes a slow-but-alive phase (fresh
`updatedAt`) from a dead worker (stale heartbeat) — the signature that made a
healthy 6-minute setup look like a hang.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROGRESS_SCHEMA = "ar-provider-setup-progress/v1"
HEARTBEAT_SECONDS = 15.0
# Six missed heartbeats: generous enough that a paused laptop or busy disk does
# not flap a live setup to stale, small enough that a dead worker is detected
# before a developer starts debugging containers.
STALE_AFTER_SECONDS = 90.0

Clock = Callable[[], datetime]


def _default_clock() -> datetime:
    return datetime.now(UTC)


class SetupProgress:
    """No-op progress sink; provider setup announces phases through this interface."""

    def phase_start(
        self,
        provider: str,
        action: str,
        *,
        note: str | None = None,
        seed_fallback: dict[str, Any] | None = None,
    ) -> None:
        """Announce a phase began; `seed_fallback` flags the seed-refused transition."""

    def phase_update(self, metrics: dict[str, Any]) -> None:
        """Update in-flight phase metrics (reserved: itemsDone/itemsTotal/percent/unit)."""

    def phase_done(self, result: dict[str, Any]) -> None:
        """Record the finished phase's result."""


class SetupProgressFile(SetupProgress):
    """Progress sink that persists every event to a heartbeat-stamped JSON file.

    Progress reporting must never fail the setup it observes: write errors are
    remembered on the instance (and surfaced through `finish`) instead of
    raising into the provider setup chain.
    """

    def __init__(
        self,
        path: Path,
        *,
        identity: dict[str, Any] | None = None,
        clock: Clock = _default_clock,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._write_error: str | None = None
        now = self._now()
        self._state: dict[str, Any] = {
            "schema": PROGRESS_SCHEMA,
            **(identity or {}),
            "state": "running",
            "startedAt": now,
            "updatedAt": now,
            "currentPhase": None,
            "completedPhases": [],
            "seedFallback": None,
        }
        with self._lock:
            self._write_locked()
        self._ticker = threading.Thread(
            target=self._heartbeat,
            args=(heartbeat_seconds,),
            name=f"setup-progress-{path.stem}",
            daemon=True,
        )
        self._ticker.start()

    @property
    def path(self) -> Path:
        return self._path

    def phase_start(
        self,
        provider: str,
        action: str,
        *,
        note: str | None = None,
        seed_fallback: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._state["currentPhase"] = {
                "provider": provider,
                "action": action,
                "startedAt": self._now(),
                "note": note,
                "metrics": None,
            }
            if seed_fallback is not None:
                self._state["seedFallback"] = seed_fallback
            self._write_locked()

    def phase_update(self, metrics: dict[str, Any]) -> None:
        with self._lock:
            phase = self._state.get("currentPhase")
            if isinstance(phase, dict):
                phase["metrics"] = metrics
                self._write_locked()

    def phase_done(self, result: dict[str, Any]) -> None:
        with self._lock:
            phase = self._state.get("currentPhase")
            phase = phase if isinstance(phase, dict) else {}
            self._state["currentPhase"] = None
            self._state["completedPhases"].append(_completed_phase(phase, result, self._now()))
            self._write_locked()

    def finish(
        self,
        *,
        state: str,
        error: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        """Record the terminal state and stop the heartbeat ticker."""
        self._stop.set()
        with self._lock:
            self._state["state"] = state
            self._state["finishedAt"] = self._now()
            self._state["currentPhase"] = None
            if error is not None:
                self._state["error"] = error
            if summary is not None:
                self._state["summary"] = summary
            if self._write_error is not None:
                self._state["progressWriteError"] = self._write_error
            self._write_locked()

    def _now(self) -> str:
        return self._clock().isoformat()

    def _heartbeat(self, interval: float) -> None:
        while not self._stop.wait(interval):
            with self._lock:
                self._write_locked()

    def _write_locked(self) -> None:
        self._state["updatedAt"] = self._now()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._state, indent=2) + "\n", encoding="utf-8")
        except OSError as error:
            self._write_error = str(error)


def _completed_phase(
    phase: dict[str, Any], result: dict[str, Any], finished_at: str
) -> dict[str, Any]:
    entry = {
        "provider": phase.get("provider") or result.get("provider"),
        "action": phase.get("action") or result.get("action"),
        "startedAt": phase.get("startedAt"),
        "finishedAt": finished_at,
        "note": phase.get("note"),
        "ok": bool(result.get("ok")),
        "skipped": True if result.get("skipped") else None,
        "reason": result.get("reason"),
    }
    return {key: value for key, value in entry.items() if value is not None}


def read_setup_progress(path: Path) -> dict[str, Any] | None:
    """Read a progress file; None when absent, unreadable, or not schema v1."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema") != PROGRESS_SCHEMA:
        return None
    return data


def progress_status(
    progress: dict[str, Any],
    *,
    clock: Clock = _default_clock,
) -> dict[str, Any]:
    """Project a progress dict into the compact status shape tools report.

    A `running` progress whose heartbeat is older than `STALE_AFTER_SECONDS`
    projects as `stale`: the writer is gone (server died mid-setup), so the
    reader should offer the retry path rather than keep waiting.
    """
    state = str(progress.get("state") or "unknown")
    status: dict[str, Any] = {"state": state}
    for key in ("startedAt", "finishedAt", "seedFallback", "error"):
        if progress.get(key) is not None:
            status[key] = progress[key]
    if state == "running":
        _project_running(status, progress, clock())
    failed = [
        phase
        for phase in _completed_list(progress)
        if not phase.get("ok") and not phase.get("skipped")
    ]
    if failed:
        status["failedPhases"] = [_phase_line(phase) for phase in failed]
    return status


def _project_running(status: dict[str, Any], progress: dict[str, Any], now: datetime) -> None:
    heartbeat_age = _age_seconds(progress.get("updatedAt"), now)
    if heartbeat_age is not None:
        status["heartbeatAgeSeconds"] = round(heartbeat_age, 1)
        if heartbeat_age > STALE_AFTER_SECONDS:
            status["state"] = "stale"
    current = progress.get("currentPhase")
    if isinstance(current, dict):
        status["currentPhase"] = {
            key: value for key, value in current.items() if value is not None and key != "startedAt"
        }
        elapsed = _age_seconds(current.get("startedAt"), now)
        if elapsed is not None:
            status["currentPhase"]["elapsedSeconds"] = round(elapsed, 1)
    completed = [_phase_line(phase) for phase in _completed_list(progress)]
    if completed:
        status["completedPhases"] = completed


def _completed_list(progress: dict[str, Any]) -> list[dict[str, Any]]:
    completed = progress.get("completedPhases")
    if not isinstance(completed, list):
        return []
    return [phase for phase in completed if isinstance(phase, dict)]


def _phase_line(phase: dict[str, Any]) -> str:
    outcome = "skipped" if phase.get("skipped") else "ok" if phase.get("ok") else "failed"
    line = f"{phase.get('provider')} {phase.get('action')}: {outcome}"
    reason = phase.get("reason")
    return f"{line} ({reason})" if reason else line


def _age_seconds(stamp: Any, now: datetime) -> float | None:
    if not isinstance(stamp, str):
        return None
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return (now - then).total_seconds()
