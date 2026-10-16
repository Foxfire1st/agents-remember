"""Workspace Event River, served-state stores and projection assembly."""

from __future__ import annotations

from agents_remember.kernel.primitives.observer_paths import observer_root
from agents_remember.observer.events import (
    OBSERVER_EVENT_SCHEMA,
    Actor,
    Event,
    Trust,
    now_iso,
)
from agents_remember.observer.projection import (
    ActionAvailability,
    Analytics,
    DriftSnapshotNode,
    EnclosureNode,
    LedgerNode,
    Metrics,
    ProviderNode,
    RouteCoverageNode,
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
    project_workspace,
    staleness_histogram,
)
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid

__all__ = [
    "OBSERVER_EVENT_SCHEMA",
    "ActionAvailability",
    "Actor",
    "Analytics",
    "DriftSnapshotNode",
    "EnclosureNode",
    "Event",
    "EventStore",
    "LedgerNode",
    "Metrics",
    "ProviderNode",
    "RouteCoverageNode",
    "SetupProgressNode",
    "SetupSummaryNode",
    "SidecarStaleNode",
    "TaskDocNode",
    "ToolReportNode",
    "Trust",
    "WorkspaceProjection",
    "build_analytics",
    "enclosure_actions",
    "new_ulid",
    "now_iso",
    "observer_root",
    "project_workspace",
    "staleness_histogram",
]
