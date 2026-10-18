"""Immutable timeline retention: byte-weighted manifests, decoded only for the requested page.

The cache retains compact UTF-8 event bytes, not mutable response dictionaries. Its weight is
resident bytes of those objects, the tuple, sources, digest and complete key, plus 512 bytes per
entry for memo bookkeeping (LRU node, value/weight pair and allocator allowance). Weight is
computed once, at admission, never by walking the history on a cache hit. The owner evicts LRU
manifests above an 8 MiB resident weight. An oversized history uses anonymous temporary disk,
including its offsets; the owner retains at most one such complete history with no per-history
cap (the explicit L57 F07 resource decision). This bounds retained memory, not the transient cost
of building one uncached history or that one history's separately named temporary disk bytes.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Final

from agents_remember.application.knowledge_reader.timeline_spill import SpilledEvents
from agents_remember.kernel.canonical_json import canonical_json_bytes

TimelineKey = tuple[str, str, str, str]
TIMELINE_CACHE_BYTES: Final = 8 * 1024 * 1024
_ENTRY_OVERHEAD: Final = 512


@dataclass(frozen=True, slots=True)
class TimelineManifest:
    """An immutable digest and the complete, ordered events that digest seals."""

    sources: bytes
    events: tuple[bytes, ...] | SpilledEvents
    manifest_digest: str
    retained_bytes: int


def freeze_timeline(
    key: TimelineKey, timeline: dict[str, Any], manifest_digest: str
) -> TimelineManifest:
    """Detach a materialized history from its producer and account for every retained object."""

    sources = canonical_json_bytes(timeline["sources"])
    events = tuple(canonical_json_bytes(event) for event in timeline["events"])
    manifest = TimelineManifest(sources, events, manifest_digest, 0)
    event_weight = sys.getsizeof(events) + sum(map(sys.getsizeof, events))
    weight = (
        sys.getsizeof(manifest)
        + sys.getsizeof(sources)
        + event_weight
        + sys.getsizeof(manifest_digest)
        + sys.getsizeof(key)
        + sum(map(sys.getsizeof, key))
        + sys.getsizeof(0)
        + _ENTRY_OVERHEAD
    )
    if weight > TIMELINE_CACHE_BYTES:
        spilled = SpilledEvents(events)
        return TimelineManifest(
            sources, spilled, manifest_digest, weight - event_weight + spilled.retained_bytes
        )
    return TimelineManifest(sources, events, manifest_digest, weight)


def timeline_weight(manifest: TimelineManifest) -> int:
    """Resident-byte weight, including the memo key and per-entry bookkeeping allowance."""

    return manifest.retained_bytes
