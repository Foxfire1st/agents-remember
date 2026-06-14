"""Sim mode: replay a recorded event fixture through the byte-identical live path (4b).

Sim does not fork the transport -- it reuses the two ``Projector`` seams (note 15 Loop C):

* a **replay clock** drives ``now`` (1x / 10x / paused), and
* a **before-tick feeder** appends the next due fixture events into a throwaway sim
  coordination root just before each projection.

So as sim-time advances the sim log grows, the projection evolves, and the same
delta + raw-event SSE machinery fires -- the frontend cannot tell sim from live. The
fixture is read once (its recorded ``ar-observer-event/v1`` records, sorted by ts) and
fed progressively into a fresh temp root, so the shipped fixture is never mutated and the
reducer keeps its live "fold the whole log" behaviour (the *log* grows over replay time,
not the reducer).

The clock and feeder are deterministic given their inputs: the same fixture fed up to the
same moment yields the same log, hence the same projection and the same delta sequence.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from agents_remember.errors import AgentsRememberError
from agents_remember.observer.paths import observer_logs_root, observer_root
from agents_remember.observer.projection_store import read_lifecycle_logs
from agents_remember.observer.store import EventStore

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig
    from agents_remember.observer.events import Event

PAUSED = "paused"


class SimError(AgentsRememberError):
    """Raised when a sim fixture or speed is unusable."""


def _event_ts(event: Event) -> datetime:
    """A fixture event's timestamp as a tz-aware UTC datetime (naive is read as UTC)."""
    moment = datetime.fromisoformat(event.ts)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def parse_sim_speed(value: str) -> float:
    """``"paused"`` -> 0.0; otherwise a non-negative replay multiplier (e.g. ``1``, ``10``)."""
    if value == PAUSED:
        return 0.0
    try:
        speed = float(value)
    except ValueError as error:
        raise SimError(f"--sim-speed must be a number or 'paused', got {value!r}") from error
    if speed < 0:
        raise SimError("--sim-speed must be >= 0")
    return speed


def load_fixture(fixture_dir: Path) -> list[Event]:
    """Every recorded lifecycle event under ``<fixture>/logs/observer``, sorted by timestamp."""
    logs = read_lifecycle_logs(observer_logs_root(fixture_dir))
    events = [event for log in logs for event in log]
    events.sort(key=_event_ts)
    return events


class ReplayClock:
    """Maps wall-clock elapsed onto fixture time at ``speed`` (frozen at ``start`` when paused)."""

    def __init__(self, start: datetime, *, speed: float) -> None:
        self._start = start
        self._speed = speed
        self._wall_origin = datetime.now(UTC)

    def now(self) -> datetime:
        if self._speed <= 0:
            return self._start
        elapsed = (datetime.now(UTC) - self._wall_origin).total_seconds() * self._speed
        return self._start + timedelta(seconds=elapsed)


class ReplayFeeder:
    """Appends fixture events into the sim store as replay time crosses each event's ts."""

    def __init__(self, store: EventStore, events: list[Event]) -> None:
        self._store = store
        self._events = events
        self._index = 0

    def feed(self, moment: datetime) -> int:
        """Append every not-yet-fed event whose ts is <= ``moment``; return how many were fed."""
        written = 0
        while self._index < len(self._events) and _event_ts(self._events[self._index]) <= moment:
            self._store.append(self._events[self._index])
            self._index += 1
            written += 1
        return written

    @property
    def remaining(self) -> int:
        return len(self._events) - self._index


@dataclass
class SimSetup:
    """A ready sim: the temp-rooted config, the replay clock, and the event feeder.

    ``temp_dir`` is held so the throwaway sim coordination root lives as long as this setup
    is referenced (the CLI keeps it for the server's lifetime, then it is reclaimed).
    """

    config: McpRuntimeConfig
    clock: ReplayClock
    feeder: ReplayFeeder
    temp_dir: tempfile.TemporaryDirectory[str]


def _materialize_surfaces(fixture_dir: Path, root: Path) -> None:
    """Copy the fixture's structural surfaces into the fresh sim root so the projector reads them
    like a live coordination root: worktree contracts (-> enclosures + synthesized paused
    lifecycles), JSON task documents (the task content), provider current-state, memory ledgers,
    and persisted drift snapshots. The observer *event logs*
    (``logs/observer/{lifecycles,workspace}``) are excluded -- the feeder is their sole writer,
    replaying them over sim time, so copying them too would double-apply and defeat the replay.
    """
    shutil.copytree(fixture_dir, root, dirs_exist_ok=True)
    observer = observer_logs_root(root)
    for source in ("lifecycles", "workspace"):
        shutil.rmtree(observer / source, ignore_errors=True)


def build_sim(config: McpRuntimeConfig, fixture_dir: Path, *, speed: float) -> SimSetup:
    """Load a fixture and wire a sim over a fresh temp coordination root (no fixture mutation)."""
    events = load_fixture(fixture_dir)
    if not events:
        raise SimError(f"no observer events found in sim fixture: {fixture_dir}")
    temp_dir = tempfile.TemporaryDirectory(prefix="ar-dashboard-sim-")
    root = Path(temp_dir.name)
    _materialize_surfaces(fixture_dir, root)
    sim_config = replace(config, coordination_root=root)
    feeder = ReplayFeeder(EventStore(observer_root(sim_config)), events)
    clock = ReplayClock(_event_ts(events[0]), speed=speed)
    return SimSetup(config=sim_config, clock=clock, feeder=feeder, temp_dir=temp_dir)
