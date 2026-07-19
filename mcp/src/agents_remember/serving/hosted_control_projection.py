"""Catalog projection for protocol-backed hosted harness state."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    pending_interaction_json,
)
from agents_remember.serving.terminal_catalog import (
    SeatTurnState,
    TerminalCatalog,
    TerminalCatalogEntry,
)

if TYPE_CHECKING:
    from agents_remember.serving.conversation.models import HarnessId


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


def snapshot_turn_state(
    snapshot: AdapterSnapshot,
    harness_id: HarnessId | None = None,
) -> SeatTurnState:
    """Project adapter evidence onto the seat vocabulary through the canonical
    conversation status authority (260718-CHATS-L1, R3).

    Orchestration no longer maps adapter fields on its own: the same canonical
    classification the Chats serving consumes produces the turn state, and the
    single seat projection rule translates it. Parity with the pre-canonical
    mapping is exact and pinned by tests.
    """

    # Deferred import: terminal_liveness imports this module, and the
    # conversation package __init__ imports the runtime that imports
    # terminal_liveness — a module-level import here closes that cycle.
    from agents_remember.serving.conversation.active.status import (  # noqa: PLC0415
        snapshot_seat_turn_state,
    )

    return snapshot_seat_turn_state(snapshot, harness_id)
