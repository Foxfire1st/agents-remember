"""Seat landing mechanics (260707-HFX2-L11): successful completion archives seats without killing them."""

from __future__ import annotations

from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry


def land_seats_for_leaf(
    catalog: TerminalCatalog,
    *,
    leaf_key: str,
    roles: frozenset[str],
    reason: str,
    edge: str,
    at: str,
) -> list[TerminalCatalogEntry]:
    """Mark every non-terminated matching seat as landed/archive, leaving tmux intact."""
    landed: list[TerminalCatalogEntry] = []
    for candidate in catalog.list(include_terminated=True):
        if candidate.leaf_key != leaf_key or candidate.status == "terminated":
            continue
        if candidate.binding_role not in roles:
            continue
        updated = catalog.mark_landed(candidate.id, at=at, reason=reason, edge=edge)
        if updated is not None and updated.status == "landed":
            landed.append(updated)
    return landed
