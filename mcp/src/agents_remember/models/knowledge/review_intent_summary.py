"""The changed-intent summary one review comparison publishes before the reviewer is opened.

The task entry shows one compact ``Intent review +N -N`` control. Its two numbers are statements
about the **authored intent** of the comparison the entry opens, so this vocabulary fixes what they
count and what they cannot be:

* ``added`` (the ``+``) counts invariant and joint-guarantee revisions that are the current head on
  the after side only -- a new statement, or the new text of a revised one;
* ``removed`` (the minus) counts the revisions that are the current head on the before side only --
  a retired statement, or the superseded text of a revised one. A revised statement is therefore
  counted once on each side, and a statement both sides hold at the same head is in neither;
* every successor revision is a revision: new text, a changed origin state or acceptance reference,
  or a record-only successor (only the version moved) counts once on each side;
* a relationship-only change is in neither number: a successor whose text and record status are
  unchanged while an invariant's realizations or a family's members differ, or an unchanged head whose
  realizations moved. It is carried as its own typed count, so a caller can show it separately
  without folding it into plus/minus;
* an identity whose head revision cannot be established on one side is carried as ``unresolved`` and
  the whole answer is ``partial``: the counted numbers then describe the identities that could be
  counted, and the state says that they are not the whole comparison;
* a comparison whose knowledge cannot be read (no dataset yet, an unreadable half, an unresolved
  candidate) is ``unavailable`` with the owner's refusal and **no counts at all**. A missing dataset
  is never a measured ``+0 -0``.

Nothing here selects or compares: the application owner computes the counts from the two snapshots'
own head revisions and this module only fixes the shape and the arithmetic the counts must satisfy.

A tree comparison (MIK-R25) also carries ``attribution``: the unexplained-changes lane's file-level
count (:class:`~agents_remember.models.knowledge.review_lane.ReviewLaneSummary`), read in the same
request as the intent counts and never more eagerly. It is a separate fact from ``+N -N`` and never
folded into it; a dataset comparison carries none.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import REFERENCE_MAX_LENGTH, KnowledgeModel
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.review_lane import ReviewLaneSummary

__all__ = [
    "IntentHeadChanges",
    "ReviewIntentCounts",
    "ReviewIntentSummaryResult",
    "ReviewIntentSummaryState",
]

ReviewIntentSummaryState = Literal["counted", "partial", "unavailable"]


class IntentHeadChanges(KnowledgeModel):
    """One statement kind's head revisions that only one side holds."""

    after_only: int = Field(ge=0)
    before_only: int = Field(ge=0)


class ReviewIntentCounts(KnowledgeModel):
    """The changed-intent counts of one comparison, with the typed counts kept outside plus/minus.

    ``invariants`` and ``guarantees`` are the two statement kinds the plus/minus add up; the sums are
    a constructor check so a caller can never be handed a total that its own parts do not make.
    ``realization_only`` counts invariant identities whose statement and record status are unchanged
    while the realizations of its head differ, ``membership_only`` counts family identities whose
    guarantee and record status are unchanged while its members (by canonical invariant identity)
    differ, and ``unresolved`` counts identities whose head could not be established on a side.
    """

    added: int = Field(ge=0)
    removed: int = Field(ge=0)
    invariants: IntentHeadChanges
    guarantees: IntentHeadChanges
    realization_only: int = Field(default=0, ge=0)
    membership_only: int = Field(default=0, ge=0)
    unresolved: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _require_the_totals_to_be_their_parts(self) -> ReviewIntentCounts:
        if self.added != self.invariants.after_only + self.guarantees.after_only:
            raise ValueError(
                "the plus count is the after-only invariant and guarantee heads, added once"
            )
        if self.removed != self.invariants.before_only + self.guarantees.before_only:
            raise ValueError(
                "the minus count is the before-only invariant and guarantee heads, added once"
            )
        return self


class ReviewIntentSummaryResult(KnowledgeModel):
    """The typed outcome of one summary read: counts, partial counts, or the refusal that says why not.

    ``counts`` is present exactly when the comparison's knowledge was read, and ``refusal`` exactly
    when it was not, so an unavailable comparison cannot be rendered as a measured zero.
    """

    state: ReviewIntentSummaryState
    operation: Literal["read_review_intent_summary"] = "read_review_intent_summary"
    repository_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    master: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    counts: ReviewIntentCounts | None = None
    refusal: ReviewRefusal | None = None
    # The unexplained-changes lane's entry count, for a tree comparison only (MIK-R32 rule 9).
    attribution: ReviewLaneSummary | None = None

    @model_validator(mode="after")
    def _require_one_outcome(self) -> ReviewIntentSummaryResult:
        if self.state == "unavailable":
            if self.refusal is None or self.counts is not None:
                raise ValueError(
                    "an unavailable summary carries its refusal and no counts; a count beside an "
                    "unread comparison is how a missing dataset comes to read as +0 -0"
                )
            return self
        if self.counts is None or self.refusal is not None:
            raise ValueError("a counted summary carries its counts and no refusal")
        if (self.state == "partial") != (self.counts.unresolved > 0):
            raise ValueError("a summary is partial exactly when some identity's head is unresolved")
        return self
