"""Bounded reuse of a recorded comparison's reviewer answers (MIK-R179 rule 3).

A recorded comparison is fixed: its ``intent``, ``entries``, ``trees`` and ``summary`` answers are
functions of the record, its directories and the Git objects it names, and of nothing a reader's
open does to a working tree. This module keeps such an answer for as long as those inputs hold and
serves it unchanged -- the same bytes a fresh computation of the same inputs would produce.

Three rules bound what may be served:

* a hit is served only when *every* observed input still carries the stamp it had when the answer
  was computed; one unreadable input is a miss, never an agreement;
* at most ``capacity`` answers are kept, least recently used first out, and none is served after
  ``max_age_seconds``;
* a value is kept only when one observation of its inputs taken *before* the computation equals one
  taken after it, so an answer composed while its inputs moved is never kept;
* a value its caller's ``keep`` refuses is never kept: an answer that names a failed read is not a
  function of its inputs alone, so it is served once and computed again next time (INV-5M9WY3).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from time import monotonic

from agents_remember.serving.changeset_cache import ChangesetCache, ReadInput

__all__ = ["RecordedAnswerReuse"]

#: A recorded comparison's answers are read from a record that does not change; the age bound
#: bounds what a long-lived process holds, and a size bound bounds how much.
DEFAULT_CAPACITY = 64
DEFAULT_MAX_AGE_SECONDS = 900.0


@dataclass(frozen=True)
class _Kept:
    value: object
    stamps: tuple[tuple[ReadInput, str | None], ...]
    admitted: float


@dataclass
class RecordedAnswerReuse:
    """Answers of one recorded comparison, keyed by the exact inputs they are a function of."""

    cache: ChangesetCache = field(default_factory=lambda: ChangesetCache(capacity=DEFAULT_CAPACITY))
    capacity: int = DEFAULT_CAPACITY
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS
    clock: Callable[[], float] = monotonic
    _values: OrderedDict[tuple[object, ...], _Kept] = field(default_factory=OrderedDict)
    _lock: RLock = field(default_factory=RLock)

    def answer[T](
        self,
        key: tuple[object, ...],
        sources: tuple[ReadInput, ...],
        produce: Callable[[], T],
        *,
        keep: Callable[[T], bool] | None = None,
    ) -> T:
        """The kept answer for ``key`` while ``sources`` hold, else one fresh computation.

        ``produce`` runs with no lock held: one slow read never delays another's answer, and two
        concurrent misses compute twice and the last answer wins -- the same answer, since a
        recorded comparison is a function of the inputs both observed. ``keep`` (every value, when
        omitted) says whether a fresh value may be kept at all.
        """

        kept = self._fresh(key, sources)
        if kept is not None:
            return deepcopy(kept.value)  # type: ignore[return-value]
        before = self._observe(sources)
        value = produce()
        if before is None or (keep is not None and not keep(value)):
            return value
        after = self._observe(sources)
        if after is not None and after == before:
            self._keep(key, _Kept(deepcopy(value), after, self.clock()))
        return value

    def retained(self) -> int:
        """How many answers are kept now; diagnostics and tests read this, no read depends on it."""

        with self._lock:
            self._expire()
            return len(self._values)

    def _fresh(self, key: tuple[object, ...], sources: tuple[ReadInput, ...]) -> _Kept | None:
        """The kept answer when its age holds and every input still carries its stamp."""

        with self._lock:
            self._expire()
            kept = self._values.get(key)
        if kept is None:
            return None
        observed = self._observe(sources)
        if observed is None or observed != kept.stamps:
            self._drop(key)
            return None
        with self._lock:
            if key in self._values:
                self._values.move_to_end(key)
        return kept

    def _observe(
        self, sources: tuple[ReadInput, ...]
    ) -> tuple[tuple[ReadInput, str | None], ...] | None:
        """One stamp per input; ``None`` when any input cannot be read, which is a miss."""

        stamps = tuple((source, self.cache.snapshot(source)) for source in sources)
        return None if any(stamp is None for _, stamp in stamps) else stamps

    def _keep(self, key: tuple[object, ...], kept: _Kept) -> None:
        with self._lock:
            self._values[key] = kept
            self._values.move_to_end(key)
            while len(self._values) > self.capacity:
                self._values.popitem(last=False)

    def _drop(self, key: tuple[object, ...]) -> None:
        with self._lock:
            self._values.pop(key, None)

    def _expire(self) -> None:
        now = self.clock()
        for key in [
            key for key, kept in self._values.items() if now - kept.admitted > self.max_age_seconds
        ]:
            self._values.pop(key, None)
