"""Observer events for seat landing / retirement / rename / turn-state.

Shared between the ``session_retire``/``session_rename`` MCP tools and the live liveness-sweep
turn-state classifier (``serving/app.py``'s wiring of ``TerminalCatalogLivenessSweeper``) so both
paths log identically-shaped ``ar-observer-event/v1`` records for the watcher/architect
NEEDS-ATTENTION feed. Mirrors the existing ``orchestration_nudge_manager`` event-logging pattern
(``mcp/tools/orchestration.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents_remember.observer.events import Event, now_iso
from agents_remember.observer.paths import observer_root
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving.terminal_catalog import TerminalCatalogEntry

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig


def log_retire_event(config: McpRuntimeConfig, entry: TerminalCatalogEntry) -> None:
    EventStore(observer_root(config)).append(
        Event(
            id=new_ulid(),
            ts=entry.retired_at or now_iso(),
            kind="seat.retired",
            trust="observed",
            actor="system" if entry.retired_by_session is None else "model",
            sessionId=entry.id,
            lifecycleId=entry.lifecycle_id,
            data={
                "session": entry.id,
                "label": entry.label,
                "spawnRole": entry.spawn_role,
                "seatRole": entry.binding_role,
                "leafKey": entry.leaf_key,
                "retiredBySession": entry.retired_by_session,
                "retiredReason": entry.retired_reason,
                "retiredEdge": entry.retired_edge,
            },
        )
    )


def log_landed_event(config: McpRuntimeConfig, entry: TerminalCatalogEntry) -> None:
    EventStore(observer_root(config)).append(
        Event(
            id=new_ulid(),
            ts=entry.landed_at or now_iso(),
            kind="seat.landed",
            trust="observed",
            actor="system",
            sessionId=entry.id,
            lifecycleId=entry.lifecycle_id,
            data={
                "session": entry.id,
                "label": entry.label,
                "spawnRole": entry.spawn_role,
                "seatRole": entry.binding_role,
                "leafKey": entry.leaf_key,
                "landedReason": entry.landed_reason,
                "landedEdge": entry.landed_edge,
            },
        )
    )


def log_rename_event(config: McpRuntimeConfig, entry: TerminalCatalogEntry) -> None:
    EventStore(observer_root(config)).append(
        Event(
            id=new_ulid(),
            ts=now_iso(),
            kind="seat.renamed",
            trust="observed",
            actor="model",
            sessionId=entry.id,
            lifecycleId=entry.lifecycle_id,
            data={
                "session": entry.id,
                "label": entry.label,
                "spawnedLabel": entry.spawned_label,
                "spawnRole": entry.spawn_role,
                "seatRole": entry.binding_role,
            },
        )
    )


def log_turn_state_change_event(config: McpRuntimeConfig, entry: TerminalCatalogEntry) -> None:
    EventStore(observer_root(config)).append(
        Event(
            id=new_ulid(),
            ts=entry.turn_state_changed_at or now_iso(),
            kind="seat.turn-state-changed",
            trust="inferred",
            actor="system",
            sessionId=entry.id,
            lifecycleId=entry.lifecycle_id,
            data={
                "session": entry.id,
                "label": entry.label,
                "turnState": entry.turn_state,
                "spawnRole": entry.spawn_role,
                "seatRole": entry.binding_role,
            },
        )
    )
