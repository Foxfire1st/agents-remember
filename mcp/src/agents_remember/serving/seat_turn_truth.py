"""Seat turn-truth writes: terminal projection and signal markers.

The catalog entry is a frozen record and the catalog classes carry a strict
surface budget; these module-level helpers are the state-signal relay's and
liveness projection's writes through the catalog's public atomic seams
(``get`` + ``upsert``), never a second mutation path.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from agents_remember.serving.terminal_catalog import (
    CatalogTurnEvidence,
    TerminalCatalog,
    TerminalCatalogEntry,
)

InterruptRequestBy = Literal["developer"]


def with_turn_evidence(
    entry: TerminalCatalogEntry,
    stamp: CatalogTurnEvidence,
) -> TerminalCatalogEntry:
    """A copy carrying one seat-turn projection: state plus terminal outcome."""
    return replace(
        entry,
        turn_state=stamp.state,
        turn_state_changed_at=(
            stamp.changed_at if stamp.state != entry.turn_state else entry.turn_state_changed_at
        ),
        terminal_outcome=stamp.terminal_outcome,
        terminal_outcome_at=stamp.terminal_outcome_at,
        terminal_evidence_id=stamp.terminal_evidence_id,
        interrupted_by=stamp.interrupted_by,
    )


def with_state_signal_emitted(
    entry: TerminalCatalogEntry,
    evidence_id: str,
) -> TerminalCatalogEntry:
    """A copy recording that one terminal evidence identity was already relayed."""
    return replace(entry, state_signal_emitted_for=evidence_id)


def with_non_reaction_emitted(
    entry: TerminalCatalogEntry,
    row_id: str,
) -> TerminalCatalogEntry:
    """A copy recording that one landed-row episode was already relayed as non-reaction."""
    return replace(entry, non_reaction_emitted_for=row_id)


def with_interrupt_request(
    entry: TerminalCatalogEntry,
    *,
    by: InterruptRequestBy,
    at: str,
    turn_id: str,
) -> TerminalCatalogEntry:
    """A copy stamped with one dashboard/interface interrupt request provenance."""
    return replace(
        entry,
        interrupt_requested_by=by,
        interrupt_requested_at=at,
        interrupt_requested_turn_id=turn_id,
    )


def record_turn_projection(
    catalog: TerminalCatalog,
    session_id: str,
    stamp: CatalogTurnEvidence,
) -> TerminalCatalogEntry | None:
    """Persist one seat-turn projection (state plus lifted terminal outcome), atomically."""
    entry = catalog.get(session_id)
    if entry is None:
        return None
    updated = with_turn_evidence(entry, stamp)
    if updated != entry:
        catalog.upsert(updated)
    return updated


def with_terminal_cursors(
    entry: TerminalCatalogEntry,
    *,
    evidence_sequence: int | None = None,
    native_cursor: str | None = None,
) -> TerminalCatalogEntry:
    """A copy advancing the terminal-evidence cursors (only the supplied, non-None ones)."""
    update: dict[str, object] = {}
    if evidence_sequence is not None:
        update["terminal_evidence_sequence"] = evidence_sequence
    if native_cursor is not None:
        update["terminal_native_cursor"] = native_cursor
    return replace(entry, **update) if update else entry


def record_terminal_cursors(
    catalog: TerminalCatalog,
    session_id: str,
    *,
    evidence_sequence: int | None = None,
    native_cursor: str | None = None,
) -> None:
    """Persist advanced terminal-evidence cursors after a successful read."""
    entry = catalog.get(session_id)
    if entry is None:
        return
    updated = with_terminal_cursors(
        entry,
        evidence_sequence=evidence_sequence,
        native_cursor=native_cursor,
    )
    if updated != entry:
        catalog.upsert(updated)


def record_state_signal_emitted(
    catalog: TerminalCatalog,
    session_id: str,
    evidence_id: str,
) -> None:
    """Record that one terminal evidence identity was already relayed to its owner."""
    entry = catalog.get(session_id)
    if entry is None or entry.state_signal_emitted_for == evidence_id:
        return
    catalog.upsert(with_state_signal_emitted(entry, evidence_id))


def record_non_reaction_emitted(
    catalog: TerminalCatalog,
    session_id: str,
    row_id: str,
) -> None:
    """Record that one landed-row episode was already relayed as a non-reaction fact."""
    entry = catalog.get(session_id)
    if entry is None or entry.non_reaction_emitted_for == row_id:
        return
    catalog.upsert(with_non_reaction_emitted(entry, row_id))


def record_interrupt_request(
    catalog: TerminalCatalog,
    session_id: str,
    *,
    by: InterruptRequestBy,
    at: str,
    turn_id: str,
) -> None:
    """Stamp dashboard/interface interrupt provenance onto a seat row."""
    entry = catalog.get(session_id)
    if entry is None:
        return
    updated = with_interrupt_request(entry, by=by, at=at, turn_id=turn_id)
    if updated != entry:
        catalog.upsert(updated)
