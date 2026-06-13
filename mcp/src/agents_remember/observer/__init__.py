"""Observer substrate: the append-only event log plus the ambient lifecycle.

Slice 2a is the write side -- the ``ar-observer-event/v1`` envelope, local id
minting, and the append-only store. Slice 2b adds the ambient lifecycle: the
process-singleton state machine, the six ``lifecycle_*`` signals, the heartbeat
ticker, and the TTL project-and-prune sweep. The projection/reducer read side
builds on this in a later slice.

See ``docs/design/observable-lifecycle.md`` for the full design.
"""

from __future__ import annotations

from agents_remember.observer.ambient import (
    HEARTBEAT_SECONDS,
    STALE_AFTER_SECONDS,
    TTL_SECONDS,
    AmbientLifecycle,
    install_ambient,
    require_ambient,
    reset_ambient,
)
from agents_remember.observer.events import (
    OBSERVER_EVENT_SCHEMA,
    Actor,
    Event,
    Trust,
    now_iso,
)
from agents_remember.observer.lifecycle_state import (
    GuardedStartError,
    LifecycleError,
    LifecycleState,
    Phase,
    State,
)
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid

__all__ = [
    "HEARTBEAT_SECONDS",
    "OBSERVER_EVENT_SCHEMA",
    "STALE_AFTER_SECONDS",
    "TTL_SECONDS",
    "Actor",
    "AmbientLifecycle",
    "Event",
    "EventStore",
    "GuardedStartError",
    "LifecycleError",
    "LifecycleState",
    "Phase",
    "State",
    "Trust",
    "install_ambient",
    "new_ulid",
    "now_iso",
    "require_ambient",
    "reset_ambient",
]
