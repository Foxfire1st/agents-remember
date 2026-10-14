"""Observer substrate: the append-only event log, the ambient lifecycle, and the
projection read side.

The write side is the ``ar-observer-event/v1`` envelope, local id minting, and the
append-only store. The ambient lifecycle is the process-singleton state of the
lifecycle a tool server works in: it adopts a worktree lifecycle, writes the event
of a tool call, runs the heartbeat ticker and ends the lifecycle. The projection
read side is the reducer that folds events (plus structural file snapshots) into
the resolved state tree.

This package exports the dependency-light core (the write side plus the *pure*
projection schema and reducer). The I/O readers that reuse other subsystems'
parsers (``snapshots``, ``projection_store``) are imported directly by their
consumers (the serving layer, tests), so importing the observer never drags in
the providers/worktrees machinery.

See ``docs/design/observable-lifecycle.md`` for the full design.
"""

from __future__ import annotations

from agents_remember.kernel.primitives.observer_paths import observer_root
from agents_remember.observer.ambient import (
    AmbientLifecycle,
    install_ambient,
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
    LifecycleError,
    LifecycleState,
    Phase,
    State,
)
from agents_remember.observer.projection import (
    ActionAvailability,
    Analytics,
    DriftSnapshotNode,
    EnclosureNode,
    LedgerNode,
    LifecycleProjection,
    Metrics,
    ProviderNode,
    RouteCoverageNode,
    SeriesNode,
    SetupProgressNode,
    SetupSummaryNode,
    SidecarStaleNode,
    TaskDocNode,
    ToolReportNode,
    WorkspaceProjection,
)
from agents_remember.observer.reducer import (
    build_analytics,
    enclosure_actions,
    project_lifecycle,
    project_workspace,
    staleness_histogram,
)
from agents_remember.observer.served_store import (
    SERVED_RECORD_SCHEMA,
    ServedLedger,
    ServedRecord,
    ServedStore,
    served_key,
)
from agents_remember.observer.store import EventStore
from agents_remember.observer.timeutil import (
    HEARTBEAT_SECONDS,
    STALE_AFTER_SECONDS,
    TTL_SECONDS,
    Clock,
)
from agents_remember.observer.ulid import new_ulid

__all__ = [
    "HEARTBEAT_SECONDS",
    "OBSERVER_EVENT_SCHEMA",
    "SERVED_RECORD_SCHEMA",
    "STALE_AFTER_SECONDS",
    "TTL_SECONDS",
    "ActionAvailability",
    "Actor",
    "AmbientLifecycle",
    "Analytics",
    "Clock",
    "DriftSnapshotNode",
    "EnclosureNode",
    "Event",
    "EventStore",
    "LedgerNode",
    "LifecycleError",
    "LifecycleProjection",
    "LifecycleState",
    "Metrics",
    "Phase",
    "ProviderNode",
    "RouteCoverageNode",
    "SeriesNode",
    "ServedLedger",
    "ServedRecord",
    "ServedStore",
    "SetupProgressNode",
    "SetupSummaryNode",
    "SidecarStaleNode",
    "State",
    "TaskDocNode",
    "ToolReportNode",
    "Trust",
    "WorkspaceProjection",
    "build_analytics",
    "enclosure_actions",
    "install_ambient",
    "new_ulid",
    "now_iso",
    "observer_root",
    "project_lifecycle",
    "project_workspace",
    "reset_ambient",
    "served_key",
    "staleness_histogram",
]
