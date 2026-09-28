"""A bounded, thread-safe memo for facts that are functions of complete Git object ids.

Anchor observation asks the same questions of the same objects many times: one review reads each
recorded anchor once per owner that selects it, and every subject of one comparison names the same
handful of trees and blobs. The answers -- which entry a tree holds at a path, which bytes a blob
holds, which names those bytes define -- are functions of the object id alone, so a remembered
answer can never go stale and nothing ever needs invalidating.

What is *not* a function of the id is whether a repository still holds the object: history can be
released and pruned, an alternate can be revoked, an object can become unreadable. That fact is
never remembered here. The caller asks Git whether the tree is present every time it builds a
resolver and consults these tables only behind a probe that succeeded, so a tree that is gone is
reported unavailable rather than answered from memory (see :mod:`.read_anchors` for the two cases
that guard does not cover).

What makes that true is the key, and this module owns the one test for it: only a **complete**
object id (40 or 64 lowercase hex digits) is admitted. A ref, a branch name, ``HEAD`` or an
abbreviation names different objects over time -- an abbreviation can even become ambiguous when an
object is added -- so a question asked with one is never remembered.

The memo stores answers only. A caller puts a value after an operation *succeeded*; a failure is
never passed in, so an object that was unreadable once is asked about again next time and a later
success is observed.

It is bounded by a total weight with least-recently-used eviction. The lock guards the table only:
no caller holds it while running Git or a parser, so two threads that miss the same key both compute
the same answer and the second ``put`` is a harmless overwrite of an equal value.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable, Mapping

from agents_remember.models.knowledge.base import GIT_OBJECT_PATTERN

__all__ = [
    "BLOB_DEFINITIONS",
    "BLOB_LINES",
    "TREE_ENTRIES",
    "BoundedMemo",
    "Definitions",
    "is_complete_object_id",
]

_COMPLETE_OBJECT_ID = re.compile(GIT_OBJECT_PATTERN)


def is_complete_object_id(value: str) -> bool:
    """Whether ``value`` is a full SHA-1 or SHA-256 object id, never a ref or an abbreviation."""

    return _COMPLETE_OBJECT_ID.fullmatch(value) is not None


def _unit(_value: object) -> int:
    return 1


class BoundedMemo[K: Hashable, V]:
    """A least-recently-used table whose entries' total weight never exceeds ``capacity``.

    ``weigh`` prices one value; the default prices every entry at one, which makes ``capacity`` an
    entry count. A value heavier than the whole capacity is not stored at all rather than evicting
    everything else to make room for it.
    """

    def __init__(self, capacity: int, weigh: Callable[[V], int] = _unit) -> None:
        self._capacity = capacity
        self._weigh = weigh
        self._entries: OrderedDict[K, tuple[V, int]] = OrderedDict()
        self._weight = 0
        self._lock = threading.Lock()

    def get(self, key: K) -> tuple[V] | None:
        """The remembered value as a one-tuple, or ``None`` on a miss.

        The one-tuple is what lets a remembered ``None`` -- a definite "the tree holds no entry at
        this path" -- be told apart from a key that was never answered.
        """

        with self._lock:
            held = self._entries.get(key)
            if held is None:
                return None
            self._entries.move_to_end(key)
            return (held[0],)

    def put(self, key: K, value: V) -> None:
        """Remember ``value`` for ``key``, evicting the least recently used entries past the bound."""

        weight = self._weigh(value)
        if weight > self._capacity:
            return
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._weight -= previous[1]
            self._entries[key] = (value, weight)
            self._weight += weight
            while self._weight > self._capacity:
                _evicted, (_value, evicted_weight) = self._entries.popitem(last=False)
                self._weight -= evicted_weight

    def clear(self) -> None:
        """Forget every entry."""

        with self._lock:
            self._entries.clear()
            self._weight = 0

    @property
    def weight(self) -> int:
        """The total weight currently held."""

        with self._lock:
            return self._weight

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# One blob's defining names, each with its distinct ``(start, end)`` extents in document order.
Definitions = Mapping[str, tuple[tuple[int, int], ...]]


def _lines_weight(lines: tuple[str, ...]) -> int:
    """Approximate resident bytes of one blob's lines: its characters plus a per-string header."""

    return sum(map(len, lines)) + 64 * len(lines)


def _definitions_weight(definitions: Definitions) -> int:
    """One unit per defined name and per extent, the units a definitions table grows by."""

    return len(definitions) + sum(map(len, definitions.values()))


# The anchor-observation tables. Every key begins with the repository root Git is run against, so
# one repository's answer is never served for another's, followed by the complete object id(s) the
# answer is a function of. The bounds cap memory only -- no content answer here is ever stale -- and
# sit far above one comparison's working set (tens of tree entries and blobs), so a review does not
# evict what it is about to reuse.
#
# The bounds are approximate weights, not a worst-case byte budget. Blob lines are weighed in
# characters plus a per-line allowance, which is close to resident bytes for ASCII source (about
# 32 MiB when full) but under-prices wide text: non-Latin, astral or surrogate-escaped characters
# can take two to four times their weight, so a table full of such blobs holds on the order of
# 64-128 MiB. Tree entries are priced at one unit each whatever their path length (a path may run to
# thousands of characters), so a full table of very long paths can add tens of MiB more. Typical
# source keeps the three tables together within a few tens of MiB.
TREE_ENTRIES: BoundedMemo[tuple[str, str, str], tuple[str, str, str] | None] = BoundedMemo(16_384)
BLOB_LINES: BoundedMemo[tuple[str, str], tuple[str, ...]] = BoundedMemo(
    32 * 1024 * 1024, weigh=_lines_weight
)
BLOB_DEFINITIONS: BoundedMemo[tuple[str, str, str], Definitions] = BoundedMemo(
    65_536, weigh=_definitions_weight
)
