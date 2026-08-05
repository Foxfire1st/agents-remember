"""Supervisor self-liveness (260707-HFX2-L2, R5): "the watcher must be code AND watched" (#15).

The supervisor sweep writes its own heartbeat tick row on every completed sweep -- the same
durable-row-not-a-timer posture the rest of this leaf uses (a daemon restart must not blind the
staleness check). Two independent readers consume the tick age: an MCP tool call opportunistically
attaches a fail-loud banner when it is stale (``mcp/tools/base.py``), and the dashboard header
renders it directly (``/api/state`` / ``/api/stream``, mirroring ``servingBuild``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agents_remember.kernel.atomic_write import atomic_write_text


@dataclass(frozen=True)
class SupervisorHeartbeat:
    lastTickAt: str
    sweepCount: int
    pendingInboxCount: int = 0
    redeliverableInboxCount: int = 0
    lastSweepDurationSeconds: float | None = None


class SupervisorHeartbeatPayload(BaseModel):
    """The declared wire form of the tick age, as it rides ``supervisorHeartbeat``.

    Distinct from :class:`SupervisorHeartbeat`, which is the durable ROW. This is what a
    reader computes ABOUT that row at response time: the row's own counters plus the age
    and the staleness verdict against the configured cutoff. Serving-layer arithmetic on a
    tick-time artifact, which is exactly why it is not a projection field.

    Serialized WITHOUT ``exclude_none``: a supervisor that has never ticked reports
    ``lastTickAt``/``ageSeconds`` as explicit nulls, because the cockpit distinguishes
    "never ticked" from "this server does not report a heartbeat at all".
    """

    model_config = ConfigDict(extra="forbid")

    lastTickAt: str | None = None
    ageSeconds: float | None = None
    staleCutoffSeconds: float
    stale: bool
    pendingInboxCount: int = 0
    redeliverableInboxCount: int = 0
    lastSweepDurationSeconds: float | None = None


def supervisor_heartbeat_path(observer_root: Path) -> Path:
    return observer_root / "workspace" / "supervisor-heartbeat.json"


class SupervisorHeartbeatStore:
    """JSON-backed heartbeat row: one atomic overwrite per tick (not an append log -- there is
    exactly one current tick, never a history worth folding)."""

    def __init__(self, observer_root: Path) -> None:
        self._path = supervisor_heartbeat_path(observer_root)

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> SupervisorHeartbeat | None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return SupervisorHeartbeat(
                lastTickAt=str(data["lastTickAt"]),
                sweepCount=int(data["sweepCount"]),
                pendingInboxCount=int(data.get("pendingInboxCount", 0)),
                redeliverableInboxCount=int(data.get("redeliverableInboxCount", 0)),
                lastSweepDurationSeconds=(
                    float(data["lastSweepDurationSeconds"])
                    if data.get("lastSweepDurationSeconds") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def tick(
        self,
        *,
        now: datetime,
        pending_inbox_count: int = 0,
        redeliverable_inbox_count: int = 0,
        last_sweep_duration_seconds: float | None = None,
    ) -> SupervisorHeartbeat:
        previous = self.read()
        heartbeat = SupervisorHeartbeat(
            lastTickAt=now.isoformat(),
            sweepCount=(previous.sweepCount if previous else 0) + 1,
            pendingInboxCount=pending_inbox_count,
            redeliverableInboxCount=redeliverable_inbox_count,
            lastSweepDurationSeconds=last_sweep_duration_seconds,
        )
        atomic_write_text(
            self._path,
            json.dumps(
                {
                    "lastTickAt": heartbeat.lastTickAt,
                    "sweepCount": heartbeat.sweepCount,
                    "pendingInboxCount": heartbeat.pendingInboxCount,
                    "redeliverableInboxCount": heartbeat.redeliverableInboxCount,
                    "lastSweepDurationSeconds": heartbeat.lastSweepDurationSeconds,
                }
            )
            + "\n",
        )
        return heartbeat


def heartbeat_age_seconds(heartbeat: SupervisorHeartbeat | None, *, now: datetime) -> float | None:
    """Seconds since the last tick, or ``None`` when there has never been one."""
    if heartbeat is None:
        return None
    try:
        last = datetime.fromisoformat(heartbeat.lastTickAt)
    except ValueError:
        return None
    return (now - last).total_seconds()


def supervisor_staleness_banner(
    observer_root: Path, *, now: datetime, stale_cutoff_seconds: float
) -> str | None:
    """A fail-loud one-liner once the supervisor tick goes stale past ``stale_cutoff_seconds``,
    else ``None``. A heartbeat that has NEVER ticked is deliberately silent, not a banner: the
    dashboard/supervisor is opt-in (``dashboard.autoStart``), so "no row yet" in a repo that has
    never run it is not evidence of anything wrong -- only a row that STARTED ticking and then
    went quiet is. Best-effort: never raises -- an unreadable heartbeat file degrades to silence
    rather than blocking the caller's tool response."""
    heartbeat = SupervisorHeartbeatStore(observer_root).read()
    if heartbeat is None:
        return None
    age = heartbeat_age_seconds(heartbeat, now=now)
    if age is None or age < stale_cutoff_seconds:
        return None
    minutes = age / 60.0
    return f"supervisor stale {minutes:.1f}m (past the {stale_cutoff_seconds:.0f}s cutoff)"
