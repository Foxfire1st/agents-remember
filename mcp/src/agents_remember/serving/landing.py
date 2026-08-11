"""Seat landing mechanics (260707-HFX2-L11): successful completion archives seats without killing them."""

from __future__ import annotations

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
)
from agents_remember.serving.ports import TerminalCatalogPort
from agents_remember.serving.retire import SeatClosure


def land_seats_for_task(
    catalog: TerminalCatalogPort,
    closure: SeatClosure,
    *,
    task_document_ref: TaskDocumentRef,
    roles: frozenset[str],
) -> list[TerminalCatalogEntry]:
    """Mark every non-terminated matching seat as landed/archive, leaving tmux intact."""
    landed: list[TerminalCatalogEntry] = []
    for candidate in catalog.list(include_terminated=True):
        if candidate.task_document_ref != task_document_ref or candidate.status == "terminated":
            continue
        if candidate.binding_role not in roles:
            continue
        updated = catalog.mark_landed(
            candidate.id, at=closure.at, reason=closure.reason, edge=closure.edge
        )
        if updated is not None and updated.status == "landed":
            landed.append(updated)
    return landed
