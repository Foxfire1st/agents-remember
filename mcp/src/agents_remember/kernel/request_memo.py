"""Retain shared computation owners for one MCP or detached worker request."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import cast


@dataclass(slots=True)
class _RequestOwners:
    values: dict[object, object] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)


_REQUEST_OWNERS: ContextVar[_RequestOwners | None] = ContextVar("request_memo_owners", default=None)


@contextmanager
def request_memo_scope() -> Iterator[None]:
    """Share narrowly scoped owners with request workers and clear them at completion."""

    request = _RequestOwners()
    token = _REQUEST_OWNERS.set(request)
    try:
        yield
    finally:
        with request.lock:
            request.values.clear()
        _REQUEST_OWNERS.reset(token)


def request_owner[V](key: object, create: Callable[[], V]) -> V | None:
    """Select this request's lazily created owner, or leave the outside owner to the caller."""

    request = _REQUEST_OWNERS.get()
    if request is None:
        return None
    with request.lock:
        if key not in request.values:
            request.values[key] = create()
        return cast(V, request.values[key])
