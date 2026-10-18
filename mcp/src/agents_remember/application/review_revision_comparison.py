"""Select each tree's recorded revision of the reviewed invariant or family.

A text record holds one revision. The comparison therefore has a pair, an addition,
a removal, or no selected record. There is no multi-head lineage selection here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from agents_remember.models.knowledge.diff import KnowledgeDiffItem
from agents_remember.models.knowledge.read import KnowledgeReadSeed
from agents_remember.models.knowledge.revision_selection import ReviewRevisionSelection

__all__ = ["SubjectRevisionSelection", "revision_heads", "select_subject_revisions"]


@dataclass(frozen=True)
class SubjectRevisionSelection:
    """The selected revision on each side and the comparison items carrying their text."""

    selection: ReviewRevisionSelection | None
    before_item: KnowledgeDiffItem | None
    after_item: KnowledgeDiffItem | None


def revision_heads(population: Sequence[str]) -> tuple[str, ...]:
    """List the recorded revisions deterministically; text records carry no lineage edges."""

    return tuple(sorted(population))


def select_subject_revisions(
    items: Sequence[KnowledgeDiffItem], selector: KnowledgeReadSeed | None
) -> SubjectRevisionSelection:
    """Select the subject's own revision per tree, not a collection-wide default."""

    identity = _selector_identity(selector)
    if identity is None:
        return SubjectRevisionSelection(selection=None, before_item=None, after_item=None)
    kind, record_id = identity
    before_item = _side_item(items, kind, record_id, "before")
    after_item = _side_item(items, kind, record_id, "after")
    before = (
        None
        if before_item is None or before_item.before is None
        else before_item.before.revision_id
    )
    after = None if after_item is None or after_item.after is None else after_item.after.revision_id
    before_ids = () if before is None else (before,)
    after_ids = () if after is None else (after,)
    state, statement = _selection_description(kind, record_id, before, after)
    return SubjectRevisionSelection(
        selection=ReviewRevisionSelection(
            record_kind=kind,
            record_id=record_id,
            state=state,
            before_revision_id=before,
            after_revision_id=after,
            before_heads=before_ids,
            after_heads=after_ids,
            before_retained=before_ids,
            after_retained=after_ids,
            statement=statement,
        ),
        before_item=before_item,
        after_item=after_item,
    )


def _selection_description(
    kind: str, record_id: str, before: str | None, after: str | None
) -> tuple[Literal["compared", "added", "removed", "unresolved"], str]:
    if before is not None and after is not None:
        return "compared", (
            f"the {kind} {record_id} comparison uses the before revision {before} and "
            f"the after revision {after}, each recorded by its own memory tree"
        )
    if after is not None:
        return "added", (
            f"the before snapshot retains no revision of the {kind} {record_id}, so "
            f"the after revision {after} is shown as an addition under the one-sided contract"
        )
    if before is not None:
        return "removed", (
            f"the after snapshot retains no revision of the {kind} {record_id}, so "
            f"the before revision {before} is shown as a removal under the one-sided contract"
        )
    return "unresolved", (
        f"no revision of the {kind} {record_id} was selected on either side, so no "
        "pair was established and no statements render as operands"
    )


def _selector_identity(
    selector: KnowledgeReadSeed | None,
) -> tuple[Literal["invariant", "family"], str] | None:
    kind = getattr(selector, "kind", None)
    if kind == "invariant":
        return "invariant", str(selector.invariant_id)  # type: ignore[attr-defined]
    if kind == "family":
        return "family", str(selector.family_id)  # type: ignore[attr-defined]
    return None


def _side_item(
    items: Sequence[KnowledgeDiffItem], kind: str, record_id: str, side: str
) -> KnowledgeDiffItem | None:
    return next(
        (
            item
            for item in items
            if item.kind == kind
            and item.record_id == record_id
            and (payload := item.before if side == "before" else item.after) is not None
            and payload.revision_id is not None
        ),
        None,
    )
