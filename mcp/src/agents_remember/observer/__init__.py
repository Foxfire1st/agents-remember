"""Observer substrate: the append-only event log for the observable lifecycle.

Slice 2a is the write side -- the ``ar-observer-event/v1`` envelope, local id
minting, and the append-only store. The ambient lifecycle + signal tools (2b)
and the projection/reducer read side (a later slice) build on this.

See ``docs/design/observable-lifecycle.md`` for the full design.
"""

from __future__ import annotations

from agents_remember.observer.events import (
    OBSERVER_EVENT_SCHEMA,
    Actor,
    Event,
    Trust,
    now_iso,
)
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid

__all__ = [
    "OBSERVER_EVENT_SCHEMA",
    "Actor",
    "Event",
    "EventStore",
    "Trust",
    "new_ulid",
    "now_iso",
]
