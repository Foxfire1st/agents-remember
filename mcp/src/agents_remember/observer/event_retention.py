"""Retention policy for dashboard-served raw observer event history."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

LIFECYCLE_EVENT_GRACE_SECONDS = 60 * 60
WORKSPACE_EVENT_TTL_SECONDS = 60 * 60
WORKSPACE_SOURCE = "workspace"


def initial_event_offsets(root: Path, *, now: datetime) -> dict[str, int]:
    """Starting offsets for a fresh `/api/events` connection.

    Active lifecycle logs and recently-terminal lifecycle logs start at zero so a
    refresh keeps useful task-local history. Expired terminal lifecycles and old
    workspace rows start at the retained boundary so ancient history is not
    replayed on every dashboard reload.
    """
    offsets: dict[str, int] = {}
    lifecycles_dir = root / "lifecycles"
    if lifecycles_dir.is_dir():
        for entry in sorted(lifecycles_dir.iterdir()):
            path = entry / "events.jsonl"
            if not entry.is_dir() or not path.is_file():
                continue
            terminal_at = lifecycle_terminal_at(path)
            if terminal_at is not None and _is_expired(terminal_at, now):
                offsets[entry.name] = path.stat().st_size

    workspace = root / WORKSPACE_SOURCE / "events.jsonl"
    if workspace.is_file():
        offsets[WORKSPACE_SOURCE] = _first_retained_offset(
            workspace, cutoff=now - timedelta(seconds=WORKSPACE_EVENT_TTL_SECONDS)
        )
    return offsets


def prune_expired_lifecycle_event_logs(root: Path, *, now: datetime) -> list[Path]:
    """Delete terminal lifecycle event logs after the grace window has expired."""
    lifecycles_dir = root / "lifecycles"
    if not lifecycles_dir.is_dir():
        return []
    removed: list[Path] = []
    for entry in sorted(lifecycles_dir.iterdir()):
        path = entry / "events.jsonl"
        if not entry.is_dir() or not path.is_file():
            continue
        terminal_at = lifecycle_terminal_at(path)
        if terminal_at is None or not _is_expired(terminal_at, now):
            continue
        path.unlink()
        removed.append(path)
        with suppress(OSError):
            entry.rmdir()
    return removed


def lifecycle_terminal_at(path: Path) -> datetime | None:
    """Timestamp of the latest written lifecycle terminal event in `path`."""
    terminal_at: datetime | None = None
    for _, _, payload in _iter_event_payloads(path):
        if payload.get("kind") != "lifecycle.ended":
            continue
        ts = _event_time(payload)
        if ts is not None:
            terminal_at = ts
    return terminal_at


def _first_retained_offset(path: Path, *, cutoff: datetime) -> int:
    end = 0
    for start, offset_after, payload in _iter_event_payloads(path):
        end = offset_after
        ts = _event_time(payload)
        if ts is not None and ts >= cutoff:
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


def _is_expired(terminal_at: datetime, now: datetime) -> bool:
    now_utc = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return (now_utc - terminal_at).total_seconds() > LIFECYCLE_EVENT_GRACE_SECONDS
