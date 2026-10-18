"""One app's bounded change-set reads, with native input generations and conditional HTTP.

Accepted gap for id-keyed reads (architect ruling 2026-10-18; sealed finding L174-R1-05
withdrawn): a read keyed only by commit or object ids — a commit-range diff, a ``read_git``
whose arguments are ids, ``commit_text_or_none`` on an id — is served again from its key
with **no observation on a hit**, as MIK-R176 v2 rule 5 requires. Its first read still
fails as Git fails and is not kept. What is therefore not observed per hit:

* objects it was computed from may be lost while the answer stays cached — it stays the
  true diff of those ids until the cache evicts it (the R1-07 bound) or the server
  restarts;
* working-tree inputs that can change a diff's presentation (``.gitattributes``, diff
  configuration) are not observed per hit either.

Both are named rather than silently accepted: ref-keyed and working reads keep their
observation.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, RLock
from typing import Any, cast

from fastapi import Request
from fastapi.responses import Response

from agents_remember.serving.changeset_generation import (
    RepositoryLayout,
    configuration_identity,
    count_objects,
    identity,
    repository_layout,
    tree_identity,
    working_generation,
)
from agents_remember.serving.conditional_read import if_none_match_matches

# A working view Git cannot describe (a repository whose HEAD tree is already gone)
# is not "unchanged": the stamp records that it is unknown, and only a refusal may be
# kept on it (L174-R1-04). A successful result is computed uncached instead.
UNKNOWN_STATUS = "unknown-status:"


@dataclass(frozen=True)
class RepositoryRead:
    root: Path
    working: bool = False
    references: tuple[str, ...] = ()
    # A change-set read does not need the object store's shape: an id-keyed read is
    # served from its key with no observation, and a working view is read natively,
    # with no cache at all. The review summary keeps the object-store signal because
    # a capture can create the objects it reads.
    observe_objects: bool = True


@dataclass(frozen=True)
class ObjectStoreRead:
    """A refused resolution's object-store proof, revoked by additions and removals.

    The proof is Git's own object census, never a walk or a hash of the objects.
    """

    root: Path


@dataclass(frozen=True)
class ContentRead:
    """Derived index bytes; native reuse touches their timestamps without changing content."""

    path: Path


@dataclass(frozen=True)
class DirectoryRead:
    """Selection names and direct child identities, without descending into their contents."""

    path: Path


type ReadInput = RepositoryRead | ObjectStoreRead | ContentRead | DirectoryRead | Path
type Generation = tuple[tuple[ReadInput, str | None], ...]


@dataclass(frozen=True)
class Retention[T]:
    """Which values a read may keep, and which may still settle on an unknown stamp.

    ``unknown`` is the L174-R1-04 case: a refusal may settle while Git cannot describe
    the working view, a success never may.
    """

    keep: Callable[[T], bool] | None = None
    unknown: Callable[[T], bool] | None = None


@dataclass(frozen=True)
class _Kept:
    value: object
    generation: Generation
    # A stamp that says "the working view could not be read" (L174-R1-04) may admit a
    # refusal, never a success: the caller says which its value is.
    retain_on_unknown: Callable[[object], bool] = lambda _: False


@dataclass
class _Read:
    cache: ChangesetCache
    inputs: dict[ReadInput, str | None] = field(default_factory=dict)
    pending: dict[tuple[object, ...], _Kept] = field(default_factory=dict)
    # One Git object census per repository and tolerance per request, so a summary
    # that names several roots asks Git once for each (MIK-R176 rule 5). Bounded and
    # least-recently-used like every other per-read map (L174-R1-07).
    objects: OrderedDict[tuple[Path, bool], str] = field(default_factory=OrderedDict)
    # The read's own abandonment, so admission can be withdrawn mid-finish (L174-R1-02).
    abandoned: Event | None = None

    def generation(self, inputs: tuple[ReadInput, ...]) -> Generation:
        for source in inputs:
            if source not in self.inputs:
                if len(self.inputs) >= self.cache.capacity:
                    self.inputs.pop(next(iter(self.inputs)))
                self.inputs[source] = self.cache.snapshot(source)
        return tuple((source, self.inputs.get(source)) for source in inputs)

    def object_revision(self, root: Path, *, tolerate_additions: bool) -> str:
        key = (root, tolerate_additions)
        if key in self.objects:
            self.objects.move_to_end(key)
            return self.objects[key]
        value = self.cache._object_revision(root, tolerate_additions=tolerate_additions)
        if len(self.objects) >= self.cache.capacity:
            self.objects.popitem(last=False)
        self.objects[key] = value
        return value

    def finish(self) -> None:
        """Admit this read's values, unless the read is abandoned at any point in here.

        Cancellation can arrive while the observations and puts below are running
        (L174-R1-02): the event is checked before each admission and once more at the end,
        and if it is set at any of those points the values this finish already admitted are
        withdrawn again, so an abandoned read leaves the cache exactly as it found it.
        """
        if self.abandoned is not None and self.abandoned.is_set():
            return
        after = {source: self.cache.snapshot(source) for source in self.inputs}
        if self.abandoned is not None and self.abandoned.is_set():
            return
        admitted: list[tuple[object, ...]] = []
        for key, kept in self.pending.items():
            settled = all(
                stamp is not None and after.get(source) == stamp
                for source, stamp in kept.generation
            )
            unknown = any(
                stamp is not None and stamp.startswith(UNKNOWN_STATUS)
                for _, stamp in kept.generation
            )
            if not settled or (unknown and not kept.retain_on_unknown(kept.value)):
                continue
            if self.abandoned is not None and self.abandoned.is_set():
                break
            self.cache.put(key, kept)
            admitted.append(key)
        if self.abandoned is not None and self.abandoned.is_set():
            with self.cache._lock:
                for key in admitted:
                    self.cache._values.pop(key, None)


_active: ContextVar[_Read | None] = ContextVar("changeset_read", default=None)


def _file_snapshot(source: ContentRead | DirectoryRead | Path) -> str:
    if isinstance(source, ContentRead):
        try:
            return hashlib.sha256(source.path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return "absent"
    if isinstance(source, DirectoryRead):
        path = source.path
        children = sorted(path.iterdir()) if path.is_dir() else ()
        return repr((identity(path), [(child.name, identity(child)) for child in children]))
    return tree_identity(source)


class ChangesetCache:
    """At most 1,024 results and repository layouts, LRU.

    A request shares observations of the same roots across its master rows. New
    values enter the cache only after one fresh observation of every consumed root
    agrees. Errors and changing/unreadable inputs never become reusable results.

    The lock covers only the cache's own maps. Freshness observation, filesystem
    work and Git calls run outside it, so one task's read never delays another's
    (MIK-R176 rule 3); a duplicate observation of one root computes twice and the
    last answer wins, which is the same answer.
    """

    def __init__(self, *, capacity: int = 1024) -> None:
        self.capacity = capacity
        self._values: OrderedDict[tuple[object, ...], _Kept] = OrderedDict()
        self._repositories: OrderedDict[Path, RepositoryLayout] = OrderedDict()
        # The last object census seen per repository and tolerance, with the revision
        # it maps to: an addition keeps the tolerant revision, a removal or a repack
        # moves both.
        self._objects: OrderedDict[tuple[Path, bool], tuple[int, int, tuple[str, ...], str]] = (
            OrderedDict()
        )
        self._lock = RLock()

    @contextmanager
    def reading(self, *, abandoned: Event | None = None) -> Iterator[None]:
        """One request's observations. An abandoned read commits nothing (L174-R1-02)."""

        read = _Read(self, abandoned=abandoned)
        token = _active.set(read)
        try:
            yield
            if abandoned is None or not abandoned.is_set():
                read.finish()
        finally:
            _active.reset(token)

    def snapshot(self, source: ReadInput) -> str | None:
        try:
            if isinstance(source, (ContentRead, DirectoryRead, Path)):
                return _file_snapshot(source)
            layout = self._layout(source.root)
            if isinstance(source, ObjectStoreRead):
                return self._absent_store_revision(source.root)
            facts = [repr(layout.environment)]
            if source.observe_objects:
                facts.append(self._repository_object_fact(source))
            facts.extend(
                repr((str(path), configuration_identity(path))) for path in layout.configuration
            )
            if source.working or source.references:
                facts.append(layout.metadata(working=source.working, references=source.references))
            else:
                facts.append(layout.references(()))
            if source.working:
                working = self._working_generation(source.root)
                facts.append(working)
            digest = hashlib.sha256("\n".join(facts).encode()).hexdigest()
            # The stamp itself says when the working view is unknown, so a retained
            # success can never be matched on it (L174-R1-04).
            return (
                f"{UNKNOWN_STATUS}{digest}"
                if source.working and working.startswith(UNKNOWN_STATUS)
                else digest
            )
        except (OSError, RuntimeError):
            return None

    def _working_generation(self, root: Path) -> str:
        """Git's own status decides the working view (MIK-R176 rule 5).

        When Git refuses to compute it — a repository whose HEAD tree is already
        missing — the state is unknown, not unchanged: the stamp says so and the
        cache admits only a refusal on it (L174-R1-04).
        """
        try:
            return working_generation(root)
        except OSError as error:
            return f"{UNKNOWN_STATUS}{error}"

    def _absent_store_revision(self, root: Path) -> str:
        read = _active.get()
        if read is not None:
            return read.object_revision(root, tolerate_additions=False)
        return self._object_revision(root)

    def _repository_object_fact(self, source: RepositoryRead) -> str:
        read = _active.get()
        revision = (
            read.object_revision(source.root, tolerate_additions=True)
            if read is not None
            else self._object_revision(source.root, tolerate_additions=True)
        )
        absence = ObjectStoreRead(source.root)
        if read is not None and absence not in read.inputs and len(read.inputs) < self.capacity - 1:
            # Capture alongside the repository's pre-read observation. A later
            # refusal can retain this proof without mistaking a post-failure
            # restoration for an input.
            read.inputs[absence] = read.object_revision(source.root, tolerate_additions=False)
        return revision

    def _layout(self, root: Path) -> RepositoryLayout:
        """The cached layout, or a fresh one; no lock is held across its discovery."""
        with self._lock:
            old = self._repositories.get(root)
        if old is not None and old.current():
            with self._lock:
                self._repositories.move_to_end(root)
            return old
        layout = repository_layout(root)
        with self._lock:
            self._repositories[root] = layout
            self._repositories.move_to_end(root)
            while len(self._repositories) > self.capacity:
                self._repositories.popitem(last=False)
        return layout

    def _object_revision(self, root: Path, *, tolerate_additions: bool = False) -> str:
        """Git's object census as a revision; an addition keeps it when tolerated.

        MIK-R176 rule 5: the count is Git's own, not a walk. A capture adds objects,
        which must not invalidate a read that already saw the pre-capture store; a
        removal, a repack or an alternate-store change moves the revision.
        """
        loose, packed, alternates = count_objects(root)
        key = (root, tolerate_additions)
        with self._lock:
            previous = self._objects.get(key)
        census = (loose, packed, alternates)
        unchanged = previous is not None and census == previous[:3]
        additions_tolerated = (
            tolerate_additions
            and previous is not None
            and loose >= previous[0]
            and packed >= previous[1]
            and alternates == previous[2]
        )
        if unchanged or additions_tolerated:
            revision = previous[3]  # type: ignore[index]
        else:
            revision = hashlib.sha256(
                repr((str(root), *census, previous[3] if previous else "")).encode()
            ).hexdigest()
        with self._lock:
            self._objects[key] = (loose, packed, alternates, revision)
            self._objects.move_to_end(key)
            while len(self._objects) > self.capacity:
                self._objects.popitem(last=False)
        return revision

    def put(self, key: tuple[object, ...], kept: _Kept) -> None:
        with self._lock:
            self._values[key] = kept
            self._values.move_to_end(key)
            while len(self._values) > self.capacity:
                self._values.popitem(last=False)

    def response(
        self,
        request: Request,
        produce: Callable[[], Response],
        *,
        abandoned: Event | None = None,
    ) -> Response:
        with self.reading(abandoned=abandoned):
            response = produce()
        if response.status_code != 200:
            return response
        revision = hashlib.sha256(response.body).hexdigest()
        headers = {"ETag": f'W/"{revision}"', "Cache-Control": "no-cache"}
        response.headers.update(headers)
        if if_none_match_matches(request.headers.get("if-none-match"), revision):
            return Response(status_code=304, headers=headers)
        return response

    @property
    def retained(self) -> tuple[int, int]:
        return len(self._values), len(self._repositories)


def cached_read[T](
    key: tuple[object, ...],
    inputs: tuple[ReadInput, ...],
    produce: Callable[[], T],
    *,
    retention: Retention[T] | None = None,
    additional_inputs: Callable[[], tuple[ReadInput, ...]] | None = None,
) -> T:
    """Keep only successful, stable reads. Copies isolate existing sidecar annotations.

    ``retain_on_unknown`` says which values may still be kept when a working-view
    stamp is unknown: a refusal may settle, a success may not (L174-R1-04).
    """
    read = _active.get()
    if read is None:
        return produce()
    cache = read.cache
    with cache._lock:
        kept = read.pending.get(key) or cache._values.get(key)
    sources = (
        tuple(dict.fromkeys((*inputs, *(source for source, _ in kept.generation))))
        if kept
        else inputs
    )
    generation = read.generation(sources)
    if (
        kept is not None
        and kept.generation == generation
        and all(stamp is not None for _, stamp in generation)
    ):
        with cache._lock:
            if key in cache._values:
                cache._values.move_to_end(key)
        return cast(T, deepcopy(kept.value))
    with cache._lock:
        cache._values.pop(key, None)
    value = produce()
    keep = retention.keep if retention is not None else None
    if keep is not None and not keep(value):
        return value
    if additional_inputs is not None:
        sources = tuple(dict.fromkeys((*inputs, *additional_inputs())))
        # Additional inputs must have been observed before their computation read
        # them. An unobserved or evicted input makes this value uncacheable.
        generation = tuple((source, read.inputs.get(source)) for source in sources)
    else:
        generation = read.generation(inputs)
    unknown_ok: Callable[[Any], bool] | None = (
        cast(Callable[[Any], bool], retention.unknown) if retention is not None else None
    )
    read.pending[key] = _Kept(deepcopy(value), generation, unknown_ok or (lambda _: False))
    while len(read.pending) > cache.capacity:
        read.pending.pop(next(iter(read.pending)))
    return value


def observe_inputs(inputs: tuple[ReadInput, ...]) -> None:
    """Observe newly resolved inputs before consuming them, within an active read."""
    read = _active.get()
    if read is not None:
        read.generation(inputs)


def observe_repository_capture(root: Path) -> None:
    """A successful stable capture precedes resolution of objects it just wrote."""
    read = _active.get()
    if read is None:
        return
    cache = read.cache
    directories = cache._layout(root).object_directories
    for source in list(read.inputs):
        if (
            isinstance(source, ObjectStoreRead)
            and cache._layout(source.root).object_directories == directories
        ):
            # A capture just wrote objects: the request's earlier census is stale.
            read.objects.pop((source.root, True), None)
            read.objects.pop((source.root, False), None)
            read.inputs[source] = cache.snapshot(source)
