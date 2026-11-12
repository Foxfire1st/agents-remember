"""Timeline-specific resident LRU with one architect-admitted anonymous disk history.

All keys and manifest metadata count against the unchanged resident budget. Only one entry
may own spilled bytes. Its disk cost is that history's complete bytes and offsets, with no
per-history cap (L57 F07 resource ruling), not an unpriced second in-memory cache.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable

from agents_remember.application.knowledge_reader.timeline_manifest import (
    TimelineKey,
    TimelineManifest,
)
from agents_remember.application.knowledge_reader.timeline_spill import SpilledEvents


class TimelineMemo:
    """Keep immutable manifests; evicted backing dies with the last in-flight page reference."""

    def __init__(self, capacity: int, *, weigh: Callable[[TimelineManifest], int]) -> None:
        self._capacity = capacity
        self._weigh = weigh
        self._entries: OrderedDict[TimelineKey, tuple[TimelineManifest, int]] = OrderedDict()
        self._weight = 0
        self._spilled_key: TimelineKey | None = None
        self._lock = threading.Lock()

    def get(self, key: TimelineKey) -> tuple[TimelineManifest] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return (entry[0],)

    def put(self, key: TimelineKey, value: TimelineManifest) -> None:
        weight = self._weigh(value)
        if weight > self._capacity:
            return
        with self._lock:
            self._drop(key)
            if isinstance(value.events, SpilledEvents):
                if self._spilled_key is not None:
                    self._drop(self._spilled_key)
                self._spilled_key = key
            self._entries[key] = (value, weight)
            self._weight += weight
            while self._weight > self._capacity:
                self._drop(next(iter(self._entries)))

    def _drop(self, key: TimelineKey) -> None:
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._weight -= previous[1]
        if self._spilled_key == key:
            self._spilled_key = None

    def discard(self, key: TimelineKey, value: TimelineManifest) -> None:
        """Forget failed backing, but never discard a concurrent replacement for the same key."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[0] is value:
                self._drop(key)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._weight = 0
            self._spilled_key = None

    @property
    def weight(self) -> int:
        with self._lock:
            return self._weight

    @property
    def spilled_bytes(self) -> int:
        """The separately admitted disk cost: at most one retained complete history."""
        with self._lock:
            if self._spilled_key is None:
                return 0
            events = self._entries[self._spilled_key][0].events
            return events.storage_bytes if isinstance(events, SpilledEvents) else 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
