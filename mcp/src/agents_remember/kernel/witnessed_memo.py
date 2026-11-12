"""A bounded memo of answers read from files, each kept with the file identity it was read under.

A caller stats its input first and is answered from memory only when the identity it holds now
equals the one the answer was stored with; any difference is a miss and the caller reads again.
The caller decides what the witness is (the readers here use device, inode, mode, size, mtime and
ctime, so a rewrite that restores mtime and size still differs) and stores an answer only when the
witness held from before its read until after it.

The table is least-recently-used and bounded by an entry count. An evicted entry is simply absent:
its next question is read from the file again and gives the same answer. The lock guards the table
only; no caller holds it while reading, so two threads that miss the same key both read and the
second ``put`` overwrites an equal answer.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Hashable

__all__ = ["WitnessedMemo"]


class WitnessedMemo[K: Hashable, W, V]:
    """At most ``capacity`` answers, each valid only for the witness it was stored with."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._entries: OrderedDict[K, tuple[W, V]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: K, witness: W) -> tuple[V] | None:
        """The answer as a one-tuple when ``witness`` is the stored one, else ``None``."""

        with self._lock:
            held = self._entries.get(key)
            if held is None or held[0] != witness:
                return None
            self._entries.move_to_end(key)
            return (held[1],)

    def put(self, key: K, witness: W, value: V) -> None:
        """Remember ``value`` under ``witness``, evicting the least recently used past the bound."""

        with self._lock:
            self._entries[key] = (witness, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)

    def discard(self, key: K) -> None:
        """Forget ``key``; its file is gone."""

        with self._lock:
            self._entries.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
