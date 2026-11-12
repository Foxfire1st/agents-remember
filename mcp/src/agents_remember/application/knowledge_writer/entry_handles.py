"""Same-document invariant handles: allocate authors before attaching entries (MIK-R54)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .handoff import HANDLE_PREFIX, EntryRequest, HandoffDocument

if TYPE_CHECKING:
    from .authoring import Authoring


def ordered_entries(document: HandoffDocument) -> tuple[EntryRequest, ...]:
    return tuple(
        sorted(
            document.entries,
            key=lambda entry: bool(
                entry.invariant_id and entry.invariant_id.startswith(HANDLE_PREFIX)
            ),
        )
    )


def assign_entries(operation: Authoring, document: HandoffDocument) -> None:
    authors = {entry.entry_id: entry for entry in document.entries if entry.invariant_id is None}
    for entry in ordered_entries(document):
        where = f"entry {entry.entry_id}"
        if entry.invariant_id is None:
            operation.handles[entry.entry_id] = operation.state.record_by_origin(
                "invariant", operation.owner, entry.entry_id
            ) or operation.mint("invariant")
        elif entry.invariant_id.startswith(HANDLE_PREFIX):
            key = entry.invariant_id.removeprefix(HANDLE_PREFIX)
            author = authors.get(key)
            if author is None:
                operation.problem(
                    where, f"{entry.invariant_id!r} names no authoring entry in this document"
                )
            elif entry.statement != author.statement:
                operation.problem(where, f"statement must equal the statement authored by {key}")
            else:
                operation.handles[entry.entry_id] = operation.handles[key]
        else:
            operation._require_kind(entry.invariant_id, "invariant", where)
            operation.handles[entry.entry_id] = entry.invariant_id
