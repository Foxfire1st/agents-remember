"""Per-lifecycle event-log reads with a stat-gated parse cache (MIK-R81 rule 2).

Split out of ``projection_store`` so both the store and the path-directed input
refresh import the readers without a module cycle: this module reaches only the
observer store and events. The projection re-read + re-validated EVERY lifecycle's
full events.jsonl each tick; heartbeats append only every 15s (and stop entirely
once a leaf is parked past its inactivity cutoff), so most logs are byte-identical
between ticks. The cache reuses the parsed event list when a log's (mtime_ns, size)
is unchanged, bounding per-tick parse cost to the logs that actually changed. Keyed
by log path; pruned to the live set on every enumerating read so it cannot leak.
Mutated only on the projection worker thread.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from agents_remember.observer.events import Event
from agents_remember.observer.store import EventStore


@dataclass
class _LifecycleLogCacheEntry:
    mtime_ns: int
    size: int
    log_events: list[Event]


_lifecycle_log_cache: dict[str, _LifecycleLogCacheEntry] = {}


def read_lifecycle_logs(
    root: Path,
    only_ids: Collection[str] | None = None,
    *,
    by_id: dict[str, list[Event]] | None = None,
) -> list[list[Event]]:
    """Every per-lifecycle log under ``lifecycles/<id>/events.jsonl``, validated (cached).

    Heartbeats are coalesced into ``heartbeat.json`` sidecars, so a heartbeat tick changes one tiny
    JSON file instead of invalidating the cached parse of the full event log.

    With ``only_ids``, no directory enumeration happens: exactly those lifecycles are
    read through the same stat-gated cache, and the module cache is not pruned (F09).
    Callers pass ``only_ids`` only from a complete wake path sample; full refreshes
    enumerate and reconcile, which also prunes cache entries for removed lifecycles.

    With ``by_id``, every appended (non-empty) row is also keyed by lifecycle id: the
    retained id map for path-directed incremental merges, attributed at the only
    place that knows the enumeration order, with no second read.
    """
    store = EventStore(root)
    lifecycles_dir = root / "lifecycles"
    if not lifecycles_dir.is_dir():
        return []
    if only_ids is not None:
        logs: list[list[Event]] = []
        for lifecycle_id in sorted(only_ids):
            events = read_one_lifecycle_log(store, lifecycles_dir, lifecycle_id)
            if events:
                logs.append(events)
        return logs
    return _read_all_lifecycle_logs(store, lifecycles_dir, by_id)


def _read_all_lifecycle_logs(
    store: EventStore,
    lifecycles_dir: Path,
    by_id: dict[str, list[Event]] | None,
) -> list[list[Event]]:
    """Enumerate every lifecycle id and read each log through the shared cache."""
    logs: list[list[Event]] = []
    seen: set[str] = set()
    for entry in sorted(lifecycles_dir.iterdir()):
        if not entry.is_dir():
            continue
        events = read_one_lifecycle_log(store, lifecycles_dir, entry.name)
        if events is None:
            continue
        key = str(lifecycles_dir / entry.name / "events.jsonl")
        seen.add(key)
        if events:
            logs.append(events)
            if by_id is not None:
                by_id[entry.name] = events
    for stale in [key for key in _lifecycle_log_cache if key not in seen]:
        _lifecycle_log_cache.pop(stale, None)
    return logs


def read_one_lifecycle_log(
    store: EventStore, lifecycles_dir: Path, lifecycle_id: str
) -> list[Event] | None:
    """One lifecycle's cached events plus its heartbeat sidecar, None when absent (F09)."""
    path = lifecycles_dir / lifecycle_id / "events.jsonl"
    try:
        stat = path.stat()
    except OSError:
        return None
    key = str(path)
    cached = _lifecycle_log_cache.get(key)
    if cached is not None and cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
        log_events = cached.log_events
    else:
        log_events = store.read_log(lifecycle_id)
        _lifecycle_log_cache[key] = _LifecycleLogCacheEntry(
            mtime_ns=stat.st_mtime_ns, size=stat.st_size, log_events=log_events
        )
    events = list(log_events)
    heartbeat = store.read_heartbeat(lifecycle_id)
    if heartbeat is not None and (not events or heartbeat.ts >= events[-1].ts):
        events.append(heartbeat)
    return events
