"""Shared owner-scoped after-tree history reads for meaning steps and judgment rows."""

from __future__ import annotations

from agents_remember.memory.knowledge_index import HistoryRow, KnowledgeIndex
from agents_remember.models.knowledge_files.documents import owner_history_attempt
from agents_remember.models.knowledge_files.history import row_kind_for_subject


def raw_subject(
    index: KnowledgeIndex, addressed: str, *, identity_index: KnowledgeIndex | None = None
) -> str | None:
    """The raw record id ``addressed`` names, or ``None`` when it names no record.

    Projected UUIDs resolve through the after index's own reverse map; when the after
    map misses -- a retired record, or one the after tree no longer holds -- the open
    comparison baseline resolves the UUID it still maps, and history is still read
    from the after tree. A raw record id stands as itself, so callers that already
    hold raw ids -- the outline -- read unchanged. Anything else names no recorded
    invariant or family.
    """

    found = index.text_id(addressed)
    if found is not None:
        return found
    if identity_index is not None:
        found = identity_index.text_id(addressed)
        if found is not None:
            try:
                row_kind_for_subject(found)
            except ValueError:
                return None
            return found
    try:
        row_kind_for_subject(addressed)
    except ValueError:
        return None
    return addressed


def owner_rows(
    index: KnowledgeIndex, owner: str
) -> tuple[tuple[HistoryRow, ...] | None, str | None]:
    """The owner's history rows across every attempt file, or the unavailable reason.

    Rows of another owner are left out before anything else: they are never steps of
    this review, and they cannot make it unavailable either. Only files the path names
    as this owner's count; anything else filed under the owner is not one of its
    attempt files.
    """

    answer = index.history_rows_of(owner)
    if not answer.index.complete:
        return None, incomplete_detail(answer.index.problems)
    return (
        tuple(row for row in answer.value if owner_history_attempt(row.path, owner) is not None),
        None,
    )


def incomplete_detail(problems: tuple[tuple[str, str], ...]) -> str:
    named = ", ".join(path for path, _ in problems[:3])
    more = f", and {len(problems) - 3} more" if len(problems) > 3 else ""
    return (
        f"the after memory index is incomplete ({len(problems)} file(s) failed: "
        f"{named}{more}); history it did not read cannot be reported as absent"
    )
