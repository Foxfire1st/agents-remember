"""The selected revision of one invariant or family in each compared memory tree.

Each side records at most one revision. The retained/head lists are the empty or
singleton projected record identities, not database lineage or selectable history.

Each selected revision of the reviewed subject also carries who wrote it, as that tree's history
files record it (MIK-R07): the review page names the source of a change the reviewed leaf did not
write (MIK-R89 v2 rules 1 and 6, amended 9dde2b09). A revision whose writer no file records says so;
no writer is inferred.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)

__all__ = [
    "ReviewRevisionSelection",
    "ReviewRevisionWriter",
    "RevisionSelectionState",
    "RevisionWriterState",
]

RevisionSelectionState = Literal["compared", "unresolved", "added", "removed"]

# ``recorded``: an owner's history row records writing this revision. ``introduced``: revision 1,
# which no row claims, written by the record's ``origin``. ``not_recorded``: no file names a writer.
# ``unavailable``: the tree's history could not be read in full, so no answer is given.
RevisionWriterState = Literal["recorded", "introduced", "not_recorded", "unavailable"]


class ReviewRevisionWriter(KnowledgeModel):
    """Who wrote one selected revision: the owners (leaf, wave or crossing ids) or why none."""

    state: RevisionWriterState
    owners: tuple[str, ...] = ()
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_owners_exactly_when_written(self) -> ReviewRevisionWriter:
        if bool(self.owners) != (self.state in {"recorded", "introduced"}):
            raise ValueError("a revision writer names owners exactly when one is recorded")
        return self


class ReviewRevisionSelection(KnowledgeModel):
    """A tree's own revision pair, one-sided record, or a subject neither side selected."""

    record_kind: Literal["invariant", "family"]
    record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    state: RevisionSelectionState
    before_revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    after_revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    # Each selected revision's own display version (``r3``), for the reader's "revision n → m".
    before_display_version: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    after_display_version: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    before_heads: tuple[str, ...] = ()
    after_heads: tuple[str, ...] = ()
    before_retained: tuple[str, ...] = ()
    after_retained: tuple[str, ...] = ()
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    # Who wrote each selected revision; ``None`` where the side selects none or no writer was read
    # (a family context's own selections are not read for it).
    before_writer: ReviewRevisionWriter | None = None
    after_writer: ReviewRevisionWriter | None = None

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
