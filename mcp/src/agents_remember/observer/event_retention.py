"""Retention policy for dashboard-served raw observer event history.

The Event River is an *agent-activity* feed, not a process-liveness keepalive, so
its history is retired by **inactivity**, per lifecycle type, rather than by a
written ``lifecycle.ended`` event -- real sessions almost never emit one (they die
silently). Heartbeats are liveness theater and do not count as activity here: a
parked lifecycle that only keeps beating is still "inactive" for retention, so its
throwaway event log can always be cleaned up regardless of lifecycle type.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Legacy terminal grace, kept for the explicit ``lifecycle.ended`` reader and the
# workspace TTL below.
LIFECYCLE_EVENT_GRACE_SECONDS = 60 * 60
WORKSPACE_EVENT_TTL_SECONDS = 60 * 60
# Inactivity TTLs: how long a log may sit with no real (non-heartbeat) activity
# before the dashboard retires it. Fleeting sessions and enclosure-backed
# (promoted) lifecycles get their own knob even though they currently match.
FLEETING_INACTIVE_TTL_SECONDS = 60 * 60
ENCLOSURE_INACTIVE_GRACE_SECONDS = 60 * 60
# A fresh connection replays at most this much recent history per active source,
# so a reload never re-streams an entire long-lived log from byte zero.
REPLAY_WINDOW_SECONDS = 60 * 60
WORKSPACE_SOURCE = "workspace"
HEARTBEAT_KIND = "lifecycle.heartbeat"


def initial_event_offsets(root: Path, *, now: datetime) -> dict[str, int]:
    """Starting offsets for a fresh `/api/events` connection.

    Dormant lifecycle logs start at end-of-file so ancient history is never
    replayed; active logs start at the recent retained window (not byte zero) so
    a reload keeps useful task-local history without re-streaming the whole log.
    """
    offsets: dict[str, int] = {}
    lifecycles_dir = root / "lifecycles"
    if lifecycles_dir.is_dir():
        cutoff = now - timedelta(seconds=REPLAY_WINDOW_SECONDS)
        for entry in sorted(lifecycles_dir.iterdir()):
            path = entry / "events.jsonl"
            if not entry.is_dir() or not path.is_file():
                continue
            if lifecycle_is_dormant(path, now=now):
                offsets[entry.name] = path.stat().st_size
            else:
                offsets[entry.name] = _first_retained_offset(path, cutoff=cutoff)

    workspace = root / WORKSPACE_SOURCE / "events.jsonl"
    if workspace.is_file():
        offsets[WORKSPACE_SOURCE] = _first_retained_offset(
            workspace, cutoff=now - timedelta(seconds=WORKSPACE_EVENT_TTL_SECONDS)
        )
    return offsets


def prune_expired_lifecycle_event_logs(
    root: Path,
    *,
    now: datetime,
    protected_lifecycle_ids: frozenset[str] | set[str] = frozenset(),
) -> list[Path]:
    """Delete lifecycle event logs that have gone dormant past their inactivity TTL.

    ``protected_lifecycle_ids`` are exempt from pruning regardless of inactivity: the dashboard passes
    the lifecycle ids of every leaf in a not-yet-retired master series (see
    ``worktree_provider_admission.series_retained_lifecycle_ids``), so a running durable task — and all
    of its sibling leaves — keep their full event history until the whole series is archived (plus a
    grace window). This supersedes the per-log inactivity TTL for durable, enclosure-backed work; only
    fleeting/standalone logs are still retired by inactivity alone.
    """
    lifecycles_dir = root / "lifecycles"
    if not lifecycles_dir.is_dir():
        return []
    removed: list[Path] = []
    for entry in sorted(lifecycles_dir.iterdir()):
        path = entry / "events.jsonl"
        if not entry.is_dir() or not path.is_file():
            continue
        if entry.name in protected_lifecycle_ids:
            continue  # part of a live master series -> never pruned by inactivity
        if not lifecycle_is_dormant(path, now=now):
            continue
        path.unlink()
        removed.append(path)
        with suppress(OSError):
            entry.rmdir()
    return removed


def lifecycle_is_dormant(path: Path, *, now: datetime) -> bool:
    """True when a log has had no real activity past its inactivity TTL.

    This is the cleanup key. ``last_activity`` ignores heartbeats, so a parked
    session that only keeps beating still counts as inactive; fleeting and
    enclosure-backed lifecycles each use their own TTL.
    """
    last_activity, is_fleeting = _retention_facts(path)
    if last_activity is None:
        return False
    ttl = FLEETING_INACTIVE_TTL_SECONDS if is_fleeting else ENCLOSURE_INACTIVE_GRACE_SECONDS
    return _seconds_since(last_activity, now) > ttl


def last_activity_at(path: Path) -> datetime | None:
    """Timestamp of the latest real (non-heartbeat) activity event in `path`."""
    return _retention_facts(path)[0]


def lifecycle_terminal_at(path: Path) -> datetime | None:
    """Timestamp of the latest written ``lifecycle.ended`` event in `path`, if any."""
    terminal_at: datetime | None = None
    for _, _, payload in _iter_event_payloads(path):
        if payload.get("kind") != "lifecycle.ended":
            continue
        ts = _event_time(payload)
        if ts is not None:
            terminal_at = ts
    return terminal_at


def _retention_facts(path: Path) -> tuple[datetime | None, bool]:
    """Single-pass ``(last_real_activity_ts, is_fleeting)`` for a lifecycle log.

    A lifecycle is fleeting when it started fleeting and was never promoted to a
    persistent (enclosure-backed) lifecycle.
    """
    last_activity: datetime | None = None
    fleeting = False
    promoted = False
    for _, _, payload in _iter_event_payloads(path):
        kind = payload.get("kind")
        if kind == HEARTBEAT_KIND:
            continue
        ts = _event_time(payload)
        if ts is not None:
            last_activity = ts
        if kind == "lifecycle.started":
            data = payload.get("data")
            fleeting = bool(isinstance(data, dict) and data.get("fleeting"))
        elif kind == "lifecycle.promoted":
            promoted = True
    return last_activity, (fleeting and not promoted)


def _first_retained_offset(path: Path, *, cutoff: datetime) -> int:
    """Offset of the first event we cannot prove is older than ``cutoff``.

    Events whose timestamp is unparseable are kept (we never silently drop an
    event we cannot age), so only events with a valid timestamp strictly older
    than the cutoff are skipped.
    """
    end = 0
    for start, offset_after, payload in _iter_event_payloads(path):
        end = offset_after
        ts = _event_time(payload)
        if ts is None or ts >= cutoff:
            return start
    return end


def _iter_event_payloads(path: Path) -> Iterator[tuple[int, int, dict[str, Any]]]:
    with path.open("rb") as handle:
        while True:
            start = handle.tell()
            raw = handle.readline()
            if not raw:
                return
            if not raw.endswith(b"\n"):
                return
            offset_after = handle.tell()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                yield start, offset_after, payload


def _event_time(payload: dict[str, Any]) -> datetime | None:
    ts = payload.get("ts")
    if not isinstance(ts, str) or not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _seconds_since(ts: datetime, now: datetime) -> float:
    now_utc = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return (now_utc - ts).total_seconds()


def _is_expired(terminal_at: datetime, now: datetime) -> bool:
    return _seconds_since(terminal_at, now) > LIFECYCLE_EVENT_GRACE_SECONDS
