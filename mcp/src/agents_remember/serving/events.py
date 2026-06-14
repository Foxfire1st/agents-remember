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
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.sse import ServerSentEvent

from agents_remember.observer.paths import observer_root

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from agents_remember.mcp.config import McpRuntimeConfig

# The workspace log has no lifecycle id; this reserved cursor key routes it. A lifecycle
# id can never collide (lifecycle ids are ULIDs -- Crockford base32, never "workspace").
_WORKSPACE = "workspace"


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


def read_new_events(root: Path, offsets: dict[str, int]) -> tuple[list[RawEvent], dict[str, int]]:
    """Read all complete lines past ``offsets`` across every source; return events + new offsets.

    Pure and deterministic: sources are walked in a fixed order (lifecycles sorted, workspace
    last) and each event carries the offset map snapshot *after* it, so a client resuming from
    any event's cursor skips exactly what it already received -- across all sources.
    """
    current = dict(offsets)
    events: list[RawEvent] = []
    for source in _discover_sources(root):
        path = _source_path(root, source)
        lines, end = _read_lines_from(path, current.get(source, 0))
        for text, offset_after in lines:
            current[source] = offset_after
            if text:
                events.append(RawEvent(source=source, data=text, cursor=encode_cursor(current)))
        current[source] = end
    return events, current


async def stream_raw_events(
    config: McpRuntimeConfig, *, last_event_id: str | None = None, interval: float = 1.0
) -> AsyncGenerator[ServerSentEvent]:
    """Emit raw ``event`` SSE records, resuming from ``last_event_id`` then tailing new lines."""
    root = observer_root(config)
    offsets = decode_cursor(last_event_id)
    while True:
        events, offsets = await asyncio.to_thread(read_new_events, root, offsets)
        for event in events:
            # ServerSentEvent JSON-encodes whatever it is given (the state channel passes dicts),
            # so emit the *parsed* object -- passing the pre-serialized JSONL string would
            # double-encode the wire (`data: "{...}"`) and force every client (dashboard, TUI,
            # agent) to JSON.parse twice. Single-encoded here matches `/api/stream`.
            yield ServerSentEvent(
                data=json.loads(event.data), event="event", id=event.cursor, retry=2000
            )
        await asyncio.sleep(interval)
