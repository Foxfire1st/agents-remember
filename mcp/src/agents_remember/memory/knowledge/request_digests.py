"""Successful logical digests reused only within one synchronous review request.

The endpoint's database origin partitions answers, even for identical endpoint bytes. Within
one partition, the witness is the connection-visible SQLite image (including WAL), and every
field of its selected generation. A physically different image simply does not share an answer.
A same-connection commit counter guards against change-and-restore; failed reads are not retained.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import get_ident

import apsw

from agents_remember.memory.knowledge.schema_generations import SchemaGeneration


@dataclass
class _Request:
    thread: int
    active: bool = True
    results: dict[tuple[str, bytes, tuple[object, ...]], str] = field(default_factory=dict)


_REQUEST: ContextVar[_Request | None] = ContextVar("review_request_digests", default=None)


@contextmanager
def same_request_digests() -> Iterator[None]:
    """A fresh request, including for nesting; copied contexts retain no finished answer."""
    request = _Request(get_ident())
    token = _REQUEST.set(request)
    try:
        yield
    finally:
        request.active = False
        request.results.clear()
        _REQUEST.reset(token)


def _generation_key(generation: SchemaGeneration) -> tuple[object, ...]:
    # Snapshot all declaration values, including custom generations with the same friendly name.
    return (
        generation.schema_name,
        generation.user_version,
        tuple(generation.tables),
        tuple((key, tuple(value)) for key, value in sorted(generation.columns.items())),
        tuple((key, tuple(value)) for key, value in sorted(generation.primary_keys.items())),
        tuple((key, frozenset(value)) for key, value in sorted(generation.json_columns.items())),
        tuple(generation.features),
        tuple(sorted(generation.table_ddl.items())),
        tuple(generation.index_ddl),
        tuple(sorted(generation.triggers.items())),
        generation.fingerprint,
    )


def _version(connection: apsw.Connection) -> int:
    # PRAGMA starts a read, so another connection's commits are observed by this same handle.
    return int(next(iter(connection.execute("PRAGMA data_version")))[0])


def digest_in_request(
    connection: apsw.Connection,
    generation: SchemaGeneration,
    compute: Callable[[], str],
) -> str:
    """Reuse only a stable full witness; otherwise run the unchanged original computation."""
    request = _REQUEST.get()
    if request is None or not request.active or request.thread != get_ident():
        return compute()
    key = None
    version = 0
    try:
        # An attached/temp schema or custom trace changes the connection's read inputs; do not
        # assert that a main-database image describes those reads. Writable callers also retain
        # their original semantics, including their own uncommitted changes.
        eligible = not (
            not connection.readonly("main")
            or connection.get_row_trace() is not None
            or connection.get_exec_trace() is not None
            or [row[1] for row in connection.execute("PRAGMA database_list")] != ["main"]
        )
        if eligible:
            origin = connection.db_filename("main")
            version = _version(connection)
            image = connection.serialize("main")
            if origin and image is not None and _version(connection) == version:
                key = (origin, image, _generation_key(generation))
    except apsw.Error:
        # A corrupt/unreadable witness is not a successful read. Its original caller still owns
        # the computation and the refusal it raises; no observation or exception is cached.
        pass
    if key is None:
        return compute()
    previous = request.results.get(key)
    if previous is not None:
        return previous
    result = compute()
    try:
        if _version(connection) == version and _generation_key(generation) == key[2]:
            request.results[key] = result
    except apsw.Error:
        pass  # Preserve the completed original result, never turn a cache check into a refusal.
    return result
