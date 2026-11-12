"""Resolve planned row references at their owners before publication (MIK-R54)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents_remember.memory.knowledge.requirement_endpoint import resolve_requirement_endpoint
from agents_remember.models.knowledge_files.history import PlannedEffectRow

if TYPE_CHECKING:
    from .authoring_rows import HistoryRowAuthoring


def unresolved_planned_ref(operation: HistoryRowAuthoring, row: PlannedEffectRow) -> str | None:
    ref = row.ref
    if ref.requirement is not None and row.disposition == "deferred":
        endpoint = resolve_requirement_endpoint(operation.coordination_root, ref.requirement)
        if endpoint.state != "resolved":
            return f"the deferred row's requirement does not resolve: {endpoint.key} [{endpoint.code}] {endpoint.detail}; correct ref.requirement"
    if ref.decision is not None:
        if operation.decisions is None:
            return "a dropped planned row cites a decision of the leaf's task document, and this write has no task owner to resolve it"
        detail = operation.decisions(ref.decision)
        return None if detail is None else f"the dropped row's decision does not resolve: {detail}"
    if ref.invariant is not None:
        found = operation.state.record(ref.invariant)
        if found is None or found[1] != "invariant":
            return f"ref.invariant {ref.invariant} names no stored invariant"
    if ref.row is not None and ref.row not in operation.state.known_ids():
        return f"ref.row {ref.row} names no history row"
    return None
