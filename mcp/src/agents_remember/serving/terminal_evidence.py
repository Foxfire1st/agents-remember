"""Lift per-vendor turn terminal evidence onto the catalog seat truth.

The conversation projectors already map native frames to canonical outcomes
(completed/interrupted/failed/unknown). This module reuses those exact mappers
over the daemon's evidence page so the catalog projection consumes the same
per-vendor evidence the conversation layer does -- no second adapter
interpretation of vendor shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from agents_remember.models.conversations.evidence import EvidencePage, NativeEvidencePage
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.harness_control_client import read_control_evidence

if TYPE_CHECKING:
    from agents_remember.serving.conversation.active.status import TurnTerminalEvidence

InterruptOriginValue = Literal["developer", "unknown"]

MAX_NATIVE_LIFT_PAGES = 8
"""Upper bound on native pages walked to reach the pi tail in one lift. Each page is
200 entries, so a single sweep can advance up to 1600 entries; a longer history
continues from the persisted cursor on the next sweep (level-triggered)."""


def read_entry_evidence(entry: TerminalCatalogEntry) -> EvidencePage:
    """Read the evidence page after the catalog's last processed terminal-evidence sequence."""
    return read_control_evidence(entry, after_sequence=entry.terminal_evidence_sequence or 0)


@dataclass(frozen=True)
class TerminalEvidenceProjection:
    """One terminal observation lifted from the evidence stream.

    ``evidence_id`` is the per-seat+turn dedupe identity: the native turn id when the
    harness provides one, otherwise the evidence sequence (one terminal result per
    turn on the claude/pi surfaces).
    """

    evidence: TurnTerminalEvidence
    evidence_id: str
    observed_at: str


@dataclass(frozen=True)
class TerminalEvidenceRead:
    """One terminal-evidence read: the lifted projection (if any) and the next cursor.

    Cursors are returned only when the read succeeded; a failed read returns no advance,
    so the catalog row keeps the previous cursor and the next sweep re-reads the same
    window (no-loss retry, never a skipped evidence window).
    """

    projection: TerminalEvidenceProjection | None
    evidence_sequence: int | None = None
    native_cursor: str | None = None


def latest_terminal_evidence(
    page: EvidencePage,
    harness_id: str | None,
) -> TerminalEvidenceProjection | None:
    """Map a page of native evidence frames and return the newest terminal outcome, if any."""
    if harness_id is None:
        return None
    from agents_remember.serving.conversation.active.status import (  # noqa: PLC0415
        TurnTerminalEvidence,
    )
    from agents_remember.serving.conversation.projectors import projector_for  # noqa: PLC0415
    from agents_remember.serving.conversation.projectors.common import (  # noqa: PLC0415
        MappedTurnOutcome,
    )

    projector = projector_for(harness_id)
    if projector is None:
        return None
    latest: TerminalEvidenceProjection | None = None
    for frame in page.frames:
        outputs = projector.map_evidence_frame(
            frame,
            evidence_ref=f"catalog-evidence:{frame.sequence}",
        )
        for output in outputs:
            if not isinstance(output, MappedTurnOutcome):
                continue
            turn_id = output.turn_id
            latest = TerminalEvidenceProjection(
                evidence=TurnTerminalEvidence(
                    outcome=output.outcome,
                    turn_id=turn_id,
                    stop_reason=output.stop_reason,
                ),
                evidence_id=turn_id or f"evidence:{frame.sequence}",
                observed_at=frame.created_at,
            )
    return latest


def latest_native_terminal_evidence(
    page: NativeEvidencePage,
    harness_id: str | None,
) -> TerminalEvidenceProjection | None:
    """Map a native-history page (pi durable entries) and return the newest terminal outcome."""
    if harness_id is None:
        return None
    from agents_remember.serving.conversation.active.status import (  # noqa: PLC0415
        TurnTerminalEvidence,
    )
    from agents_remember.serving.conversation.projectors import projector_for  # noqa: PLC0415
    from agents_remember.serving.conversation.projectors.common import (  # noqa: PLC0415
        MappedTurnOutcome,
        UnmappableShape,
    )

    projector = projector_for(harness_id)
    if projector is None:
        return None
    latest: TerminalEvidenceProjection | None = None
    for frame in page.frames:
        try:
            outputs = projector.map_native_frame(
                frame, evidence_ref=f"catalog-native:{frame.native_id}"
            )
        except UnmappableShape:
            continue
        for output in outputs:
            if not isinstance(output, MappedTurnOutcome):
                continue
            latest = TerminalEvidenceProjection(
                evidence=TurnTerminalEvidence(
                    outcome=output.outcome,
                    turn_id=output.turn_id,
                    stop_reason=output.stop_reason,
                ),
                evidence_id=f"native:{frame.native_id}",
                observed_at=frame.created_at or "",
            )
    return latest


def read_entry_terminal_evidence(
    entry: TerminalCatalogEntry,
) -> TerminalEvidenceRead:
    """The daemon-side lift for one catalog row: per-harness, from the surfaces the
    conversation projectors already consume (evidence frames for codex/claude, native
    durable entries for pi). A failed/evicted read yields no new claim AND no cursor
    advance this sweep, so the missed window is re-read next sweep."""
    if entry.kind != "harness" or entry.harness is None or entry.control_endpoint is None:
        return TerminalEvidenceRead(projection=None)
    if entry.harness == "pi":
        return _read_pi_terminal_evidence(entry)
    page = read_entry_evidence(entry)
    return TerminalEvidenceRead(
        projection=latest_terminal_evidence(page, entry.harness),
        evidence_sequence=page.latest_sequence,
    )


def _read_pi_terminal_evidence(entry: TerminalCatalogEntry) -> TerminalEvidenceRead:
    """Walk the pi durable-entry tail from the persisted cursor and lift the newest outcome.

    ``get_entries`` returns entries after ``since``; each page windows up to 200 of them.
    The walk continues with the page's ``next_cursor`` until the tail (bounded by
    ``MAX_NATIVE_LIFT_PAGES``), then returns the persisted cursor for the next sweep.
    """
    from agents_remember.serving.harness_control_client import (  # noqa: PLC0415
        read_control_native_page,
    )

    cursor = entry.terminal_native_cursor
    terminal: TerminalEvidenceProjection | None = None
    last_id = cursor
    pages = 0
    while pages < MAX_NATIVE_LIFT_PAGES:
        page = read_control_native_page(entry, cursor=cursor, limit=200)
        mapped = latest_native_terminal_evidence(page, "pi")
        if mapped is not None:
            terminal = mapped
        if not page.frames:
            break
        last_id = page.frames[-1].native_id
        if not page.truncated or page.next_cursor is None:
            break
        cursor = page.next_cursor
        pages += 1
    return TerminalEvidenceRead(projection=terminal, native_cursor=last_id)


def interrupted_origin(
    entry: TerminalCatalogEntry,
    terminal: TurnTerminalEvidence | None,
) -> InterruptOriginValue | None:
    """Attribute an interrupted terminal outcome: developer when the dashboard interrupt
    action stamped provenance on the seat for that turn, otherwise unknown."""
    if terminal is None or terminal.outcome != "interrupted":
        return None
    if entry.interrupt_requested_by != "developer":
        return "unknown"
    requested_turn = entry.interrupt_requested_turn_id
    if terminal.turn_id is not None and requested_turn is not None:
        return "developer" if requested_turn == terminal.turn_id else "unknown"
    return "developer"
