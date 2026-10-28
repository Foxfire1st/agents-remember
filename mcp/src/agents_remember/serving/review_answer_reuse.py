"""Bounded reuse of a recorded comparison's reviewer answers (MIK-R179 rule 3).

A recorded comparison is fixed: its ``intent``, ``entries``, ``trees`` and ``summary`` answers are
functions of the record, its directories and the Git objects it names, and of nothing a reader's
open does to a working tree. This module keeps such an answer for as long as those inputs hold and
serves it unchanged -- the same bytes a fresh computation of the same inputs would produce.

Optional typed JSON persistence lives below the same admission rule. Its question key, exact
current input stamps, model name and producing source namespace must agree before a disk answer
can be admitted. Disk reads are bounded and writes atomic; missing or invalid cache data is
recomputed by the original producer. Callers supply all source observations, never cached paths.

Four rules bound what may be served:

* a hit is served only when *every* observed input still carries the stamp it had when the answer
  was computed; one unreadable input is a miss, never an agreement;
* at most ``capacity`` answers are kept, least recently used first out. No age decides anything:
  an answer is reused, at any age, whenever every read it depends on observes identical. A bound
  only makes an answer absent, which is recomputed; it never refuses one whose reads hold;
* a value is kept only when one observation of its inputs taken *before* the computation equals one
  taken after it, so an answer composed while its inputs moved is never kept;
* a value its caller's ``keep`` refuses is never kept: an answer that names a failed read is not a
  function of its inputs alone, so it is served once and computed again next time (INV-5M9WY3).

A kept answer is stored on disk after its response, by one background writer (:class:`AnswerWrites`)
that no response waits for: storing moves off the response path, it does not get faster. A write
that fails or is dropped leaves the answer absent on disk, which a later read computes again.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import os
import queue
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import RLock
from time import time
from typing import cast

from pydantic import BaseModel

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.canonical_json import canonical_json_bytes, sha256_digest
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.recorded_reads import changed_observations, valid_observation
from agents_remember.serving.changeset_cache import (
    ChangesetCache,
    ObjectStoreRead,
    ReadInput,
    RepositoryRead,
)
from agents_remember.serving.changeset_generation import count_objects
from agents_remember.serving.review_open_observation import note_outcome

__all__ = [
    "ANSWER_WRITES",
    "AnswerCodec",
    "AnswerWrites",
    "DiskAnswers",
    "DiskBudget",
    "DiskPublication",
    "RecordedAnswerReuse",
]

logger = logging.getLogger(__name__)

#: A recorded comparison's answers are read from a record that does not change; a count and a
#: size bound what a long-lived process holds.
DEFAULT_CAPACITY = 64
DEFAULT_MAX_DISK_BYTES = 64 * 1024 * 1024
_DISK_FORMAT = "recorded-review-answer/v1"
_DISK_SUFFIX = ".review.json"

#: Stored answers waiting for the background writer; one beyond this is dropped (and counted),
#: never waited for. A dropped answer is absent on disk: a later read computes it again.
DEFAULT_WRITE_QUEUE = 64


@dataclass(frozen=True)
class AnswerCodec[T]:
    """The caller's typed JSON model; no persisted Python object is ever loaded."""

    model: type[BaseModel]
    # The cold resolver's exact logical identities; memory reuse revalidates the
    # retained physical inputs before it needs to reopen the indexes.
    binding: tuple[object, ...] = ()

    @property
    def name(self) -> str:
        return f"{self.model.__module__}.{self.model.__qualname__}"

    def encode(self, value: T) -> object:
        if not isinstance(value, self.model):
            raise TypeError("the answer does not match its declared persistence model")
        return cast(BaseModel, value).model_dump(mode="json", by_alias=True)

    def decode(self, value: object) -> T:
        return cast(T, self.model.model_validate(value))


@dataclass(frozen=True)
class DiskBudget:
    """One storage transaction's clock and size bounds."""

    now: float
    max_bytes: int
    capacity: int


class AnswerWrites:
    """One background writer of stored answers, detached from every request that hands it one.

    :meth:`submit` returns at once: no response waits for a write. The writer thread runs in an
    empty context, so no request's context -- its read cancellation included -- reaches a write,
    and a client that disconnects never cancels one. The queue is bounded: a write submitted while
    it is full is dropped and counted in :attr:`dropped`. A write that fails is logged with its
    reason and counted in :attr:`failed`; each file is published by temp-and-rename, so a failed
    write leaves the answer absent, never partly written.
    """

    def __init__(self, capacity: int = DEFAULT_WRITE_QUEUE) -> None:
        self._queue: queue.Queue[tuple[Callable[[], object], str]] = queue.Queue(capacity)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.dropped = 0
        self.failed = 0

    def submit(self, write: Callable[[], object], what: str) -> bool:
        """Queue one write without waiting; ``False`` when it was dropped on a full queue."""

        with self._lock:
            if self._thread is None:
                detached = contextvars.Context()
                self._thread = threading.Thread(
                    target=detached.run, args=(self._run,), name="review-answer-writes", daemon=True
                )
                self._thread.start()
        try:
            self._queue.put_nowait((write, what))
        except queue.Full:
            with self._lock:
                self.dropped += 1
                dropped = self.dropped
            logger.warning(
                "a stored review answer was dropped, the write queue is full (%d dropped): %s",
                dropped,
                what,
            )
            return False
        return True

    def drain(self) -> None:
        """Wait until every queued write has ended (tests and shutdown; never a response)."""

        self._queue.join()

    def _run(self) -> None:
        while True:
            write, what = self._queue.get()
            try:
                write()
            except Exception as error:  # a failed write is absent; the writer goes on
                with self._lock:
                    self.failed += 1
                logger.warning(
                    "a stored review answer was not written, it is absent on disk: %s: %r",
                    what,
                    error,
                )
            finally:
                self._queue.task_done()


#: The process's one answer writer, shared by every reuse that stores answers.
ANSWER_WRITES = AnswerWrites()


@dataclass(frozen=True)
class _Kept:
    value: object
    stamps: tuple[tuple[ReadInput, str | None], ...]
    current: Callable[[object], bool] | None = None
    # Reads the computation made beyond its question; each is re-observed before any reuse.
    reads: tuple[tuple[str, str], ...] = ()


@dataclass
class RecordedAnswerReuse:
    """Answers of one recorded comparison, keyed by the exact inputs they are a function of."""

    cache: ChangesetCache = field(default_factory=lambda: ChangesetCache(capacity=DEFAULT_CAPACITY))
    capacity: int = DEFAULT_CAPACITY
    directory: Path | None = None
    build_namespace: str | None = None
    max_disk_bytes: int = DEFAULT_MAX_DISK_BYTES
    wall_clock: Callable[[], float] = time
    writes: AnswerWrites = field(default_factory=lambda: ANSWER_WRITES)
    _disk: DiskAnswers | None = field(default=None, init=False)
    _values: OrderedDict[tuple[object, ...], _Kept] = field(default_factory=OrderedDict)
    _lock: RLock = field(default_factory=RLock)

    def __post_init__(self) -> None:
        if self.directory is not None:
            if not self.build_namespace:
                raise ValueError("persisted answers require an exact producing-build namespace")
            self._disk = DiskAnswers.open(self.directory, self.build_namespace)

    def answer[T](  # noqa: PLR0913 -- the reads a value made ride beside its keep rule
        self,
        key: tuple[object, ...],
        sources: tuple[ReadInput, ...],
        produce: Callable[[], T],
        *,
        keep: Callable[[T], bool] | None = None,
        codec: AnswerCodec[T] | None = None,
        made: Callable[[], Mapping[str, str]] | None = None,
    ) -> T:
        """The kept answer for ``key`` while ``sources`` hold, else one fresh computation.

        ``produce`` runs with no lock held: one slow read never delays another's answer, and two
        concurrent misses compute twice and the last answer wins -- the same answer, since a
        recorded comparison is a function of the inputs both observed. ``keep`` must be a pure
        predicate: every held value must still satisfy it on each read, including the trusted
        currentness observations captured when it was produced. It never mutates its value.
        ``made`` names the reads the computation made beyond its question. They are kept and
        stored with the value, and before any reuse -- in memory or after a restart -- each is
        re-observed and must compare identical. They are only compared: nothing is served through
        them and no read follows from them.
        """

        kept = self._fresh(key, sources)
        if kept is not None:
            note_outcome("read")
            return deepcopy(kept.value)  # type: ignore[return-value]
        before = self._observe(sources)
        persistent = self._disk is not None and codec is not None
        census_before = _census(sources) if persistent else None
        disk_before = self._disk_observe(before, census_before) if persistent else None
        restored = self._restore(key, before, disk_before, codec, keep)
        if restored is not None:
            note_outcome("restored")
            return restored
        value = produce()
        note_outcome("computed")
        if before is None or (keep is not None and not keep(value)):
            return value
        reads = tuple(sorted((made() if made is not None else {}).items()))
        if changed_observations(dict(reads)):
            return value
        after = self._observe(sources)
        census_after = _census(sources) if persistent else None
        if (
            after is None
            or after != before
            or (persistent and not _held_through(sources, census_before, census_after))
        ):
            return value
        kept = _Kept(deepcopy(value), after, cast(Callable[[object], bool] | None, keep), reads)
        self._keep(key, kept)
        # The disk's stamps are stricter than memory's: an object added to a shared store while
        # the answer was computed keeps it in memory, and only its stored copy is not written.
        disk_after = self._disk_observe(after, census_after) if persistent else None
        if codec is not None and disk_after is not None and disk_after == disk_before:
            self._store(key, cast(T, kept.value), disk_after, codec, dict(reads))
        return value

    def _store[T](
        self,
        key: tuple[object, ...],
        value: T,
        stamps: tuple[tuple[ReadInput, str | None], ...],
        codec: AnswerCodec[T],
        reads: dict[str, str],
    ) -> None:
        """Hand one kept answer to the background writer; the response does not wait for it.

        The value handed on is the kept copy, which nothing mutates: every read serves a copy.
        """

        disk = self._disk
        if disk is None:
            return
        budget = DiskBudget(self.wall_clock(), self.max_disk_bytes, self.capacity)
        self.writes.submit(
            lambda: disk.save_batch(((key, value),), stamps, codec, budget, reads=reads),
            f"{codec.name} {key[:1]!r}",
        )

    def _restore[T](
        self,
        key: tuple[object, ...],
        before: tuple[tuple[ReadInput, str | None], ...] | None,
        disk_before: tuple[tuple[ReadInput, str | None], ...] | None,
        codec: AnswerCodec[T] | None,
        keep: Callable[[T], bool] | None,
    ) -> T | None:
        if self._disk is None or before is None or disk_before is None or codec is None:
            return None
        restored = self._disk.load_with_reads(
            key,
            disk_before,
            codec,
            DiskBudget(self.wall_clock(), self.max_disk_bytes, self.capacity),
        )
        if restored is None:
            return None
        value, reads = restored
        if changed_observations(dict(reads)) or (keep is not None and not keep(value)):
            return None
        sources = tuple(source for source, _stamp in before)
        after = self._observe(sources)
        if after != before or self._disk_observe(after, _census(sources)) != disk_before:
            return None
        self._keep(
            key,
            _Kept(
                deepcopy(value),
                before,
                cast(Callable[[object], bool] | None, keep),
                reads,
            ),
        )
        return value

    def retained_answer(self, key: tuple[object, ...]) -> object | None:
        """Try the existing in-memory answer against every retained input and guard."""
        with self._lock:
            previous = self._values.get(key)
        if previous is None:
            return None
        kept = self._fresh(key, tuple(source for source, _stamp in previous.stamps))
        if kept is None:
            return None
        note_outcome("read")
        return deepcopy(kept.value)

    def retained(self) -> int:
        """How many answers are kept now; diagnostics and tests read this, no read depends on it."""

        with self._lock:
            return len(self._values)

    def _fresh(self, key: tuple[object, ...], sources: tuple[ReadInput, ...]) -> _Kept | None:
        """The kept answer while every input still carries its stamp and every read compares."""

        with self._lock:
            kept = self._values.get(key)
        if kept is None:
            return None
        # A trusted locator check runs before any old byte path can be followed.
        if kept.current is not None and not kept.current(kept.value):
            self._drop(key)
            return None
        if changed_observations(dict(kept.reads)):
            self._drop(key)
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

        stamps = tuple((source, self.cache.open_snapshot(source)) for source in sources)
        return None if any(stamp is None for _, stamp in stamps) else stamps

    def _disk_observe(
        self, stamps: tuple[tuple[ReadInput, str | None], ...] | None, census: _Census | None
    ) -> tuple[tuple[ReadInput, str | None], ...] | None:
        """Stable disk identity from Git's own ``census``; memory keeps its history-bearing one."""
        if stamps is None or census is None:
            return None
        observed: list[tuple[ReadInput, str | None]] = []
        for source, stamp in stamps:
            disk_stamp = stamp
            if isinstance(source, ObjectStoreRead):
                disk_stamp = sha256_digest(
                    ["object-census/v1", str(source.root), census[source.root]]
                )
            elif isinstance(source, RepositoryRead) and source.observe_objects:
                exact = self.cache.open_snapshot(replace(source, observe_objects=False))
                if exact is None:
                    return None
                disk_stamp = sha256_digest(["repository-census/v1", exact, census[source.root]])
            observed.append((source, disk_stamp))
        return tuple(observed)

    def _keep(self, key: tuple[object, ...], kept: _Kept) -> None:
        with self._lock:
            self._values[key] = kept
            self._values.move_to_end(key)
            while len(self._values) > self.capacity:
                self._values.popitem(last=False)

    def _drop(self, key: tuple[object, ...]) -> None:
        with self._lock:
            self._values.pop(key, None)


type _Census = dict[Path, tuple[int, int, tuple[str, ...]]]


def _census(sources: tuple[ReadInput, ...]) -> _Census | None:
    """Git's own object census of each store ``sources`` observe, read now: never one memoized.

    ``None`` when any cannot be read, which keeps nothing.
    """

    try:
        return {
            source.root: count_objects(source.root)
            for source in sources
            if isinstance(source, ObjectStoreRead)
            or (isinstance(source, RepositoryRead) and source.observe_objects)
        }
    except (OSError, RuntimeError, ValueError):
        return None


def _held_through(
    sources: tuple[ReadInput, ...], before: _Census | None, after: _Census | None
) -> bool:
    """Whether each store held through a computation as its input asks, by Git's own census.

    An answer's memory stamps may be one request's or one open's memo, so the census read before
    and after the computation is what shows a store moved meanwhile. A strict census
    (:class:`ObjectStoreRead`) holds only unchanged; a repository's tolerant one also holds when
    objects were only added -- another worktree's commit in the store they share -- and moves
    with a removal, a repack or an alternate-store change.
    """

    if before is None or after is None:
        return False
    strict = {source.root for source in sources if isinstance(source, ObjectStoreRead)}
    for root, (loose, packed, alternates) in before.items():
        now = after.get(root)
        if now is None:
            return False
        if root in strict:
            if now != (loose, packed, alternates):
                return False
        elif not (now[0] >= loose and now[1] >= packed and now[2] == alternates):
            return False
    return True


@contextlib.contextmanager
def _logged_write_failure() -> Iterator[None]:
    """A storage transaction whose failure leaves its answers absent, logged with the reason."""

    try:
        yield
    except OSError as error:
        logger.warning("a stored review answer was not written, it is absent on disk: %r", error)


@dataclass(frozen=True)
class DiskPublication:
    """Bytes published by one read, for cancellation-safe conditional withdrawal."""

    path: Path
    digest: str
    size: int
    previous: bytes | None


def _stored_envelope(
    envelope: object, identity: dict[str, object]
) -> tuple[object, tuple[tuple[str, str], ...]] | None:
    """A stored answer's body and reads, or ``None`` for anything not valid (then it is absent).

    No age is read: a stored answer is reused whenever its inputs and reads observe identical.
    """
    if not isinstance(envelope, dict) or envelope.get("identity") != identity:
        return None
    body = envelope.get("answer")
    if envelope.get("digest") != sha256_digest(body):
        return None
    reads = _stored_reads(envelope.get("reads", {}))
    return None if reads is None else (body, reads)


def _stored_reads(stored: object) -> tuple[tuple[str, str], ...] | None:
    """A stored answer's reads, or ``None`` when they are not a valid read set (then no answer)."""
    if not isinstance(stored, dict):
        return None
    reads = tuple(sorted(stored.items()))
    if not all(
        isinstance(path, str) and isinstance(seen, str) and valid_observation(path, seen)
        for path, seen in reads
    ):
        return None
    return reads


@dataclass(frozen=True)
class DiskAnswers:
    """Storage only: the reuse owner supplies and rechecks every current input stamp."""

    directory: Path
    namespace: str

    @classmethod
    def open(cls, directory: Path, namespace: str) -> DiskAnswers | None:
        existing = directory.resolve()
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        inside = run_git(existing, ["rev-parse", "--is-inside-work-tree"])
        if inside.returncode == 0 and inside.stdout.strip() == "true":
            raise ValueError("review answer cache must be outside Git working trees")
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return cls(directory.resolve(), namespace)

    def _path[T](self, key: tuple[object, ...], codec: AnswerCodec[T]) -> Path:
        digest = sha256_digest([_DISK_FORMAT, self.namespace, codec.name, codec.binding, key])
        return self.directory / f"{digest}{_DISK_SUFFIX}"

    def _identity[T](
        self,
        key: tuple[object, ...],
        stamps: tuple[tuple[ReadInput, str | None], ...],
        codec: AnswerCodec[T],
    ) -> dict[str, object]:
        return {
            "format": _DISK_FORMAT,
            "build": self.namespace,
            "model": codec.name,
            "binding": json.loads(canonical_json_bytes(codec.binding)),
            "question": json.loads(canonical_json_bytes(key)),
            # These are compared only to current caller-supplied observations. No stored path
            # is followed and no stored input chooses what this process reads.
            "inputs": [[repr(source), stamp] for source, stamp in stamps],
        }

    def load[T](
        self,
        key: tuple[object, ...],
        stamps: tuple[tuple[ReadInput, str | None], ...],
        codec: AnswerCodec[T],
        budget: DiskBudget,
    ) -> T | None:
        found = self.load_with_reads(key, stamps, codec, budget)
        return None if found is None else found[0]

    def load_with_reads[T](
        self,
        key: tuple[object, ...],
        stamps: tuple[tuple[ReadInput, str | None], ...],
        codec: AnswerCodec[T],
        budget: DiskBudget,
    ) -> tuple[T, tuple[tuple[str, str], ...]] | None:
        """The stored answer and the reads it made; the caller re-observes each read."""
        path = self._path(key, codec)
        try:
            with path.open("rb") as stream:
                raw = stream.read(budget.max_bytes + 1)
            if len(raw) > budget.max_bytes:
                return None
            stored = _stored_envelope(json.loads(raw), self._identity(key, stamps, codec))
            if stored is None:
                return None
            body, reads = stored
            value = codec.decode(body)
            os.utime(path, None)
            return value, reads
        except (OSError, ValueError, TypeError):
            return None

    def save[T](
        self,
        key: tuple[object, ...],
        stamps: tuple[tuple[ReadInput, str | None], ...],
        codec: AnswerCodec[T],
        value: T,
        budget: DiskBudget,
    ) -> None:
        self.save_batch(((key, value),), stamps, codec, budget)

    def _encoded[T](  # noqa: PLR0913 -- one envelope's question, inputs, model, body and reads
        self,
        key: tuple[object, ...],
        stamps: tuple[tuple[ReadInput, str | None], ...],
        codec: AnswerCodec[T],
        value: T,
        now: float,
        *,
        reads: Mapping[str, str],
    ) -> bytes:
        answer = codec.encode(value)
        envelope = {
            "identity": self._identity(key, stamps, codec),
            "answer": answer,
            "digest": sha256_digest(answer),
            "created": now,
            # Compared with a fresh observation before the answer is reused; never followed.
            **({"reads": dict(sorted(reads.items()))} if reads else {}),
        }
        return json.dumps(
            envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")

    def _batch_bytes[T](  # noqa: PLR0913 -- save_batch's facts, handed on as they are
        self,
        values: tuple[tuple[tuple[object, ...], T], ...],
        stamps: tuple[tuple[ReadInput, str | None], ...],
        codec: AnswerCodec[T],
        budget: DiskBudget,
        cancelled: Callable[[], bool],
        *,
        reads: Mapping[str, str],
    ) -> dict[Path, bytes]:
        pending: dict[Path, bytes] = {}
        size = 0
        for key, value in reversed(values):
            if cancelled() or budget.capacity < 1:
                return {}
            encoded = self._encoded(key, stamps, codec, value, budget.now, reads=reads)
            if len(pending) >= budget.capacity or size + len(encoded) > budget.max_bytes:
                continue
            path = self._path(key, codec)
            if path not in pending:
                pending[path] = encoded
                size += len(encoded)
        return pending

    def save_batch[T](  # noqa: PLR0913 -- a stored answer carries the reads it made
        self,
        values: tuple[tuple[tuple[object, ...], T], ...],
        stamps: tuple[tuple[ReadInput, str | None], ...],
        codec: AnswerCodec[T],
        budget: DiskBudget,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        reads: Mapping[str, str] | None = None,
    ) -> tuple[DiskPublication, ...]:
        """One bounded storage transaction, with no new answers after cancellation.

        Reserve disk capacity once for a master's many immutable ranges. The same
        writer/format handles a single recorded answer; no second cache protocol.
        """
        pending = self._batch_bytes(values, stamps, codec, budget, cancelled, reads=reads or {})
        if not pending or cancelled():
            return ()
        publications: list[DiskPublication] = []
        with (
            _logged_write_failure(),
            exclusive_file_lock(self.directory / "answers", "review answer cache"),
        ):
            if cancelled():
                return ()
            self._trim(
                budget.capacity - len(pending),
                budget.max_bytes - sum(map(len, pending.values())),
                exclude=frozenset(pending),
            )
            try:
                for path, encoded in reversed(tuple(pending.items())):
                    if cancelled():
                        break
                    previous = None
                    try:
                        with path.open("rb") as stream:
                            previous = stream.read(budget.max_bytes + 1)
                    except FileNotFoundError:
                        pass
                    if previous is not None and len(previous) > budget.max_bytes:
                        continue
                    publication = DiskPublication(
                        path, hashlib.sha256(encoded).hexdigest(), len(encoded), previous
                    )
                    # A post-replace durability error can leave published bytes.
                    publications.append(publication)
                    atomic_write_bytes(path, encoded)
            finally:
                if cancelled():
                    self._withdraw(publications)
                    publications.clear()
        return tuple(publications)

    def withdraw(self, publications: tuple[DiskPublication, ...]) -> None:
        """Withdraw only this read's bytes, preserving any later publisher's answer."""
        if not publications:
            return
        with (
            contextlib.suppress(OSError),
            exclusive_file_lock(self.directory / "answers", "review answer cache"),
        ):
            self._withdraw(publications)

    def _withdraw(self, publications: list[DiskPublication] | tuple[DiskPublication, ...]) -> None:
        for publication in publications:
            with contextlib.suppress(OSError):
                with publication.path.open("rb") as stream:
                    current = stream.read(publication.size + 1)
                if hashlib.sha256(current).hexdigest() != publication.digest:
                    continue
                if publication.previous is None:
                    publication.path.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(publication.path, publication.previous)

    def _trim(
        self,
        capacity: int,
        max_bytes: int,
        *,
        exclude: frozenset[Path] = frozenset(),
    ) -> None:
        """Reserve room for one encoded answer while the shared writer lock is held."""
        paths = sorted(
            (path for path in self.directory.glob(f"*{_DISK_SUFFIX}") if path not in exclude),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        size = 0
        for index, path in enumerate(paths):
            stat = path.stat()
            size += stat.st_size
            if index >= capacity or size > max_bytes:
                path.unlink(missing_ok=True)
