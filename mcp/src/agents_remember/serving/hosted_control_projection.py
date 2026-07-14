"""Catalog projection for protocol-backed hosted harness state."""

from __future__ import annotations

from dataclasses import replace

from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    pending_interaction_json,
)
from agents_remember.serving.terminal_catalog import (
    SeatTurnState,
    TerminalCatalog,
    TerminalCatalogEntry,
)


def project_control_snapshot(
    catalog: TerminalCatalog,
    entry: TerminalCatalogEntry,
    snapshot: AdapterSnapshot,
) -> TerminalCatalogEntry:
    """Persist the latest adapter evidence without changing the catalog schema version."""

    projected = control_snapshot_entry(entry, snapshot)
    if projected != entry:
        catalog.upsert(projected)
    return projected


def control_snapshot_entry(
    entry: TerminalCatalogEntry, snapshot: AdapterSnapshot
) -> TerminalCatalogEntry:
    """Return the additive projection without persistence for read-only request paths."""

    return replace(
        entry,
        control_state=snapshot.control,
        control_activity=snapshot.activity,
        control_acceptance=snapshot.acceptance,
        control_vendor_session_id=snapshot.vendor_session_id,
        control_pending_interaction=pending_interaction_json(snapshot.pending_interaction),
        control_last_event_sequence=snapshot.last_event_sequence,
        control_raw=dict(snapshot.raw),
    )


def mark_legacy_control_unsupported(
    catalog: TerminalCatalog, entry: TerminalCatalogEntry
) -> TerminalCatalogEntry:
    """Label an existing raw-TUI row honestly; no protocol identity can be manufactured for it."""

    if entry.kind != "harness" or entry.control_state == "unsupported":
        return entry
    projected = replace(
        entry,
        control_state="unsupported",
        control_activity="unknown",
        control_acceptance="unsupported",
        control_raw={"detail": "legacy raw-TUI session has no protocol bridge"},
    )
    catalog.upsert(projected)
    return projected


def snapshot_turn_state(snapshot: AdapterSnapshot) -> SeatTurnState:
    if snapshot.activity in {"running", "settling"}:
        return "working"
    if snapshot.activity == "blocked":
        return "awaiting-input"
    if snapshot.activity == "idle" and snapshot.control == "ready":
        return "turn-ended"
    return "stale"
