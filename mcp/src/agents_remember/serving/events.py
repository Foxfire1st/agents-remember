"""The raw observer-event SSE channel: byte-offset tail of the append-only logs (4b).

The ``state`` channel (``app.py``) serves the *folded* projection and re-snapshots on
reconnect -- right for a bounded projection. This channel instead streams the underlying
``ar-observer-event/v1`` records **verbatim** with exact byte-offset resume, because the
event log is append-only and unbounded: a reconnecting client must resume where it left
off, not replay history. It powers the future event-log panel and sim scrubbing.

Each per-lifecycle log (``lifecycles/<id>/events.jsonl``) and the workspace log
(``workspace/events.jsonl``) is an independent append-only file, so the resume position is
a **per-source byte-offset map**, carried opaquely in the SSE ``id`` (base64url JSON).
On (re)connect the ``Last-Event-ID`` cursor is decoded and every source resumes from its
own offset -- so this stays a *separate* endpoint from ``/api/stream`` rather than mixing
byte-offset resume with the state channel's snapshot resume on one stream.

The tail reader is a pure function (``read_new_events``) over a root + offset map, kept
independent of the HTTP layer so resume, multi-source ordering, and partial-line handling
are unit-testable without a client.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.sse import ServerSentEvent

from agents_remember.observer.event_retention import (
    initial_event_offsets,
    prune_expired_lifecycle_event_logs,
)
from agents_remember.observer.paths import observer_root
from agents_remember.observer.store import (
    WORKSPACE_SOURCE,
    workspace_base_offset,
    workspace_river_lock,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from agents_remember.mcp.config import McpRuntimeConfig

# The workspace log has no lifecycle id; this reserved cursor key routes it. A lifecycle
# id can never collide (lifecycle ids are ULIDs -- Crockford base32, never "workspace").
_WORKSPACE = WORKSPACE_SOURCE

# Heartbeats are a liveness signal (already carried by the projection's status file), not agent
# activity, so the river never streams them. The compact on-disk wire form (``model_dump_json``)
# has no spaces, so the substring test is the fast path; non-heartbeat lines never carry the
# marker text and so are never parsed.
_HEARTBEAT_KIND = "lifecycle.heartbeat"
_HEARTBEAT_MARKER = f'"kind":"{_HEARTBEAT_KIND}"'
# A fresh connect streams its (already window-bounded) backlog in chunks, so the loop is never
# blocked materializing thousands of records before the first byte.
DEFAULT_EVENT_BATCH = 500
# Pruning walks every log; run it on connect and then on a slow cadence, not every tail tick.
PRUNE_INTERVAL_SECONDS = 60.0


@dataclass(frozen=True)
class RawEvent:
    """One raw event line plus the resume cursor a client would send to continue after it.

    ``data`` is the verbatim JSONL line (already the camelCase wire form on disk). It is parsed
    to an object at the SSE boundary (``stream_raw_events``) so ServerSentEvent single-encodes it
    like the state channel, rather than double-encoding the already-serialized string. ``cursor``
    is the encoded per-source offset map *after* this event, i.e. the ``Last-Event-ID`` to resume
    the whole stream from this point.
    """

    source: str
    data: str
    cursor: str


def encode_cursor(offsets: dict[str, int]) -> str:
    """Encode a per-source offset map as one opaque, newline-free SSE id (base64url JSON)."""
    raw = json.dumps(offsets, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str | None) -> dict[str, int]:
    """Decode a ``Last-Event-ID`` cursor back to an offset map (empty when absent/garbage)."""
    if not cursor:
        return {}
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): value
        for key, value in data.items()
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    }


def _source_path(root: Path, source: str) -> Path:
    if source == _WORKSPACE:
        return root / "workspace" / "events.jsonl"
    return root / "lifecycles" / source / "events.jsonl"


def _discover_sources(root: Path) -> list[str]:
    """Every readable log source under ``root``, lifecycles sorted then workspace last."""
    sources: list[str] = []
    lifecycles_dir = root / "lifecycles"
    if lifecycles_dir.is_dir():
        sources.extend(
            entry.name
            for entry in sorted(lifecycles_dir.iterdir())
            if entry.is_dir() and (entry / "events.jsonl").is_file()
        )
    if (root / "workspace" / "events.jsonl").is_file():
        sources.append(_WORKSPACE)
    return sources


def _read_lines_from(path: Path, start: int) -> tuple[list[tuple[str, int]], int]:
    """Complete lines (text, byte-offset-after) from ``start`` to the last newline, + the end.

    A trailing partial line (no terminating newline) is left unconsumed so a half-written
    append is never emitted; the end offset stays at the last complete-line boundary.
    """
    with path.open("rb") as handle:
        handle.seek(start)
        chunk = handle.read()
    lines: list[tuple[str, int]] = []
    consumed = 0
    while True:
        newline = chunk.find(b"\n", consumed)
        if newline == -1:
            break
        text = chunk[consumed:newline].decode("utf-8").strip()
        consumed = newline + 1
        lines.append((text, start + consumed))
    return lines, start + consumed


def _read_source_lines(root: Path, source: str, virtual_start: int) -> tuple[list[tuple[str, int]], int]:
    """Read complete lines and return offsets in the source's cursor coordinate system.

    Lifecycle logs remain physical-offset streams. The workspace river is physically compacted while
    live, so its SSE cursor is virtual: ``baseOffset`` (bytes already reclaimed) + current file offset.
    The same lock used by append/compact makes the base sidecar and the file a consistent pair while
    this read maps a client cursor to the current physical file.
    """
    path = _source_path(root, source)
    if source != _WORKSPACE:
        return _read_lines_from(path, virtual_start)
    with workspace_river_lock(root):
        base = workspace_base_offset(root)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        physical_start = min(max(virtual_start - base, 0), size)
        lines, physical_end = _read_lines_from(path, physical_start)
        return [(text, base + offset_after) for text, offset_after in lines], base + physical_end


def _is_heartbeat_line(text: str) -> bool:
    """True when a raw JSONL line is a ``lifecycle.heartbeat`` (liveness, not activity).

    Fast path: the compact on-disk wire form matches the substring marker directly. A
    tolerant parse is the fallback so alternate spacing/formatting can never let a heartbeat
    slip into the river. Lines that do not even contain the kind text are never parsed.
    """
    if _HEARTBEAT_MARKER in text:
        return True
    if _HEARTBEAT_KIND not in text:
        return False
    try:
        return json.loads(text).get("kind") == _HEARTBEAT_KIND
    except (ValueError, TypeError, AttributeError):
        return False


def read_new_events(
    root: Path, offsets: dict[str, int], *, limit: int | None = None
) -> tuple[list[RawEvent], dict[str, int]]:
    """Read complete lines past ``offsets`` across every source; return events + new offsets.

    Pure and deterministic: sources are walked in a fixed order (lifecycles sorted, workspace
    last) and each event carries the offset map snapshot *after* it, so a client resuming from
    any event's cursor skips exactly what it already received -- across all sources. Heartbeat
    lines advance the offset but are never emitted (liveness, not activity). When ``limit`` is
    set the read returns early after that many emitted events, with offsets pointing just past
    the last consumed line, so the caller can drain a large backlog in bounded chunks.
    """
    current = dict(offsets)
    events: list[RawEvent] = []
    for source in _discover_sources(root):
        lines, end = _read_source_lines(root, source, current.get(source, 0))
        for text, offset_after in lines:
            current[source] = offset_after
            if not text or _is_heartbeat_line(text):
                continue
            events.append(RawEvent(source=source, data=text, cursor=encode_cursor(current)))
            if limit is not None and len(events) >= limit:
                return events, current
        current[source] = end
    return events, current


async def stream_raw_events(
    config: McpRuntimeConfig, *, last_event_id: str | None = None, interval: float = 1.0
) -> AsyncGenerator[ServerSentEvent]:
    """Emit raw ``event`` SSE records, resuming from ``last_event_id`` then tailing new lines.

    Connect cost is bounded: the offset map is computed once (not re-scanned every tick),
    pruning runs on connect then on a slow cadence, and the backlog is streamed in bounded
    chunks -- yielding to the loop between chunks -- so a large history never blocks the event
    loop or materializes all at once before the first byte. The ``ready`` marker is sent once
    the (window-bounded) backlog has drained, signalling a coherent first snapshot.
    """
    root = observer_root(config)
    now = datetime.now(UTC)
    # CS-6 D1 (260707-HFX2-L12): the prune + initial-offset scan read the whole workspace river
    # backlog from byte 0. Run them in a worker thread so a large events.jsonl on a fresh connect
    # never blocks the shared asyncio loop (which serves every SSE stream + every API request),
    # matching how ``read_new_events`` below is already offloaded.
    await asyncio.to_thread(prune_expired_lifecycle_event_logs, root, now=now)
    cursor_offsets = decode_cursor(last_event_id)
    if cursor_offsets:
        offsets = cursor_offsets
    else:
        offsets = await asyncio.to_thread(initial_event_offsets, root, now=now)
    ready_sent = False
    last_prune = now
    while True:
        moment = datetime.now(UTC)
        if (moment - last_prune).total_seconds() >= PRUNE_INTERVAL_SECONDS:
            await asyncio.to_thread(prune_expired_lifecycle_event_logs, root, now=moment)
            last_prune = moment
        events, offsets = await asyncio.to_thread(
            read_new_events, root, offsets, limit=DEFAULT_EVENT_BATCH
        )
        for event in events:
            # ServerSentEvent JSON-encodes whatever it is given (the state channel passes dicts),
            # so emit the *parsed* object -- passing the pre-serialized JSONL string would
            # double-encode the wire (`data: "{...}"`) and force every client (dashboard, TUI,
            # agent) to JSON.parse twice. Single-encoded here matches `/api/stream`.
            yield ServerSentEvent(
                data=json.loads(event.data), event="event", id=event.cursor, retry=2000
            )
        if events:
            # More backlog may remain; drain it promptly but yield to the loop between chunks.
            await asyncio.sleep(0)
            continue
        if not ready_sent:
            ready_sent = True
            yield ServerSentEvent(
                data={"ready": True}, event="ready", id=encode_cursor(offsets), retry=2000
            )
        await asyncio.sleep(interval)
