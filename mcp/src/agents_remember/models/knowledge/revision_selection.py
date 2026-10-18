"""The selected revision of one invariant or family in each compared memory tree.

Each side records at most one revision. The retained/head lists are the empty or
singleton projected record identities, not database lineage or selectable history.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)

__all__ = ["ReviewRevisionSelection", "RevisionSelectionState"]

RevisionSelectionState = Literal["compared", "unresolved", "added", "removed"]


class ReviewRevisionSelection(KnowledgeModel):
    """A tree's own revision pair, one-sided record, or a subject neither side selected."""

    record_kind: Literal["invariant", "family"]
    record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    state: RevisionSelectionState
    before_revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    after_revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    before_heads: tuple[str, ...] = ()
    after_heads: tuple[str, ...] = ()
    before_retained: tuple[str, ...] = ()
    after_retained: tuple[str, ...] = ()
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_state_to_match_the_pair(self) -> ReviewRevisionSelection:
        before, after = self.before_revision_id, self.after_revision_id
        expected = {
            "compared": (True, True),
            "added": (False, True),
            "removed": (True, False),
            "unresolved": (False, False),
        }[self.state]
        if (before is not None, after is not None) != expected:
            raise ValueError(
                "the selection state must match the recorded before/after revision pair"
            )
        return self

    @model_validator(mode="after")
    def _require_the_pair_to_come_from_the_heads(self) -> ReviewRevisionSelection:
        for selected, heads in (
            (self.before_revision_id, self.before_heads),
            (self.after_revision_id, self.after_heads),
        ):
            if selected is not None and selected not in heads:
                raise ValueError("a selected revision must be one of the recorded heads")
        return self

    @model_validator(mode="after")
    def _require_the_heads_to_come_from_the_retained(self) -> ReviewRevisionSelection:
        for heads, retained in (
            (self.before_heads, self.before_retained),
            (self.after_heads, self.after_retained),
        ):
            if set(heads) - set(retained):
                raise ValueError("a recorded head must be a retained revision of its memory tree")
        return self
