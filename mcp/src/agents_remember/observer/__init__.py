"""Observer substrate: the append-only event log, the ambient lifecycle, and the
projection read side.

Slice 2a is the write side -- the ``ar-observer-event/v1`` envelope, local id
minting, and the append-only store. Slice 2b adds the ambient lifecycle: the
process-singleton state machine, the six ``lifecycle_*`` signals, the heartbeat
ticker, and the TTL project-and-prune sweep. Slice 2c adds resume + the save
gate. Slice 3a adds the projection read side -- the reducer that folds events
(plus structural file snapshots) into the resolved state tree.

This package exports the dependency-light core (the write side plus the *pure*
projection schema and reducer). The I/O readers that reuse other subsystems'
parsers (``snapshots``, ``projection_store``) are imported directly by their
consumers (the serving layer, tests), so importing the observer never drags in
the providers/worktrees machinery.

See ``docs/design/observable-lifecycle.md`` for the full design.
"""

from __future__ import annotations

from agents_remember.observer.ambient import (
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
from agents_remember.observer.paths import observer_root
from agents_remember.observer.projection import (
    ActionAvailability,
    EnclosureNode,
    LifecycleProjection,
    Metrics,
    ProviderNode,
    WorkspaceProjection,
)
from agents_remember.observer.reducer import (
    enclosure_actions,
    project_lifecycle,
    project_workspace,
)
from agents_remember.observer.store import EventStore
from agents_remember.observer.timeutil import (
    HEARTBEAT_SECONDS,
    STALE_AFTER_SECONDS,
    TTL_SECONDS,
    Clock,
    age_seconds,
)
from agents_remember.observer.ulid import new_ulid

__all__ = [
    "HEARTBEAT_SECONDS",
    "OBSERVER_EVENT_SCHEMA",
    "STALE_AFTER_SECONDS",
    "TTL_SECONDS",
    "ActionAvailability",
    "Actor",
    "AmbientLifecycle",
    "Clock",
    "EnclosureNode",
    "Event",
    "EventStore",
    "GuardedStartError",
    "LifecycleError",
    "LifecycleProjection",
    "LifecycleState",
    "Metrics",
    "Phase",
    "ProviderNode",
    "State",
    "Trust",
    "WorkspaceProjection",
    "age_seconds",
    "enclosure_actions",
    "install_ambient",
    "new_ulid",
    "now_iso",
    "observer_root",
    "project_lifecycle",
    "project_workspace",
    "require_ambient",
    "reset_ambient",
]
