"""One app's bounded change-set reads, with native input generations and conditional HTTP."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import cast

from fastapi import Request
from fastapi.responses import Response

from agents_remember.serving.changeset_generation import (
    ObjectGeneration,
    RepositoryLayout,
    WorkingManifest,
    configuration_identity,
    identity,
    repository_layout,
    tree_identity,
    working_manifest,
)
from agents_remember.serving.conditional_read import if_none_match_matches


@dataclass(frozen=True)
class RepositoryRead:
    root: Path
    working: bool = False
    references: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObjectStoreRead:
    """A refused resolution's object-absence proof, revoked by additions as well as removals."""

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
class _Kept:
    value: object
    generation: Generation


@dataclass
class _Read:
    cache: ChangesetCache
    inputs: dict[ReadInput, str | None] = field(default_factory=dict)
    pending: dict[tuple[object, ...], _Kept] = field(default_factory=dict)

    def generation(self, inputs: tuple[ReadInput, ...]) -> Generation:
        for source in inputs:
            if source not in self.inputs:
                if len(self.inputs) >= self.cache.capacity:
                    self.inputs.pop(next(iter(self.inputs)))
                self.inputs[source] = self.cache.snapshot(source)
        return tuple((source, self.inputs.get(source)) for source in inputs)

    def finish(self) -> None:
        after = {source: self.cache.snapshot(source) for source in self.inputs}
        for key, kept in self.pending.items():
            if all(
                stamp is not None and after.get(source) == stamp
                for source, stamp in kept.generation
            ):
                self.cache.put(key, kept)


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
    """At most 1,024 results, repository manifests and object observations each, LRU.

    A request shares observations of the same roots across its master rows. New
    values enter the cache only after one fresh observation of every consumed root
    agrees. Errors and changing/unreadable inputs never become reusable results.
    """

    def __init__(self, *, capacity: int = 1024) -> None:
        self.capacity = capacity
        self._values: OrderedDict[tuple[object, ...], _Kept] = OrderedDict()
        self._repositories: OrderedDict[Path, tuple[RepositoryLayout, WorkingManifest | None]] = (
            OrderedDict()
        )
        self._lock = RLock()
        self._objects: OrderedDict[tuple[Path, ...], ObjectGeneration] = OrderedDict()

    @contextmanager
    def reading(self) -> Iterator[None]:
        read = _Read(self)
        token = _active.set(read)
        try:
            yield
            read.finish()
        finally:
            _active.reset(token)

    def snapshot(self, source: ReadInput) -> str | None:
        with self._lock:
            return self._snapshot(source)

    def _snapshot(self, source: ReadInput) -> str | None:
        try:
            if isinstance(source, (ContentRead, DirectoryRead, Path)):
                return _file_snapshot(source)
            layout, manifest = self._repository(
                source.root, working=isinstance(source, RepositoryRead) and source.working
            )
            objects = layout.object_directories
            observation = self._objects.setdefault(objects, ObjectGeneration())
            self._objects.move_to_end(objects)
            while len(self._objects) > self.capacity:
                self._objects.popitem(last=False)
            facts = [repr(layout.environment), observation.observe(objects)]
            if isinstance(source, ObjectStoreRead):
                return observation.complete_revision
            read = _active.get()
            absence = ObjectStoreRead(source.root)
            if (
                read is not None
                and absence not in read.inputs
                and len(read.inputs) < self.capacity - 1
            ):
                # Capture alongside the repository's pre-read observation. A later refusal
                # can retain this proof without mistaking a post-failure restoration for input.
                read.inputs[absence] = observation.complete_revision
            facts.extend(
                repr((str(path), configuration_identity(path))) for path in layout.configuration
            )
            if source.working or source.references:
                facts.append(layout.metadata(working=source.working, references=source.references))
            else:
                facts.append(layout.references(()))
            if source.working:
                assert manifest is not None
                facts.append(tree_identity(source.root, skip=manifest.skip))
            return hashlib.sha256("\n".join(facts).encode()).hexdigest()
        except (OSError, RuntimeError):
            return None

    def _repository(
        self, root: Path, *, working: bool
    ) -> tuple[RepositoryLayout, WorkingManifest | None]:
        old = self._repositories.get(root)
        layout, manifest = old if old is not None else (repository_layout(root), None)
        if not layout.current():
            layout, manifest = repository_layout(root), None
        if working and (manifest is None or not manifest.current()):
            manifest = working_manifest(layout)
        self._repositories[root] = (layout, manifest)
        self._repositories.move_to_end(root)
        while len(self._repositories) > self.capacity:
            self._repositories.popitem(last=False)
        return layout, manifest

    def put(self, key: tuple[object, ...], kept: _Kept) -> None:
        with self._lock:
            self._values[key] = kept
            self._values.move_to_end(key)
            while len(self._values) > self.capacity:
                self._values.popitem(last=False)

    def response(self, request: Request, produce: Callable[[], Response]) -> Response:
        with self.reading():
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
    def retained(self) -> tuple[int, int, int]:
        return len(self._values), len(self._repositories), len(self._objects)


def cached_read[T](
    key: tuple[object, ...],
    inputs: tuple[ReadInput, ...],
    produce: Callable[[], T],
    *,
    retain_when: Callable[[T], bool] | None = None,
    additional_inputs: Callable[[], tuple[ReadInput, ...]] | None = None,
) -> T:
    """Keep only successful, stable reads. Copies isolate existing sidecar annotations."""
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
    if retain_when is not None and not retain_when(value):
        return value
    if additional_inputs is not None:
        sources = tuple(dict.fromkeys((*inputs, *additional_inputs())))
        # Additional inputs must have been observed before their computation read
        # them. An unobserved or evicted input makes this value uncacheable.
        generation = tuple((source, read.inputs.get(source)) for source in sources)
    else:
        generation = read.generation(inputs)
    read.pending[key] = _Kept(deepcopy(value), generation)
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
    with cache._lock:
        directories = cache._repository(root, working=False)[0].object_directories
        for source in list(read.inputs):
            if (
                isinstance(source, ObjectStoreRead)
                and cache._repository(source.root, working=False)[0].object_directories
                == directories
            ):
                read.inputs[source] = cache.snapshot(source)
