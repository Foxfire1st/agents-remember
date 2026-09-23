"""Why one supplied record may be displayed beside one selected subject (``ICR-R26@v1``).

Complete evidence delivery and correct attribution are separately falsifiable. ``ICR-R14@v1``
supplies the owner-produced collections; this vocabulary is how the review says, per record, *why*
that record may appear where it appears -- and it is the shape that makes the F09 defect
unrepresentable rather than merely fixed.

**Five display treatments, each a different recorded fact.**

* ``direct`` -- the record's own recorded subject/revision binding names the selected subject, on a
  revision the selection retains, in the displayed generation. It is displayed as that subject's
  evidence.
* ``historical`` -- the record names the selected subject, and its own recorded generation input is
  not the displayed generation (or its revisions are outside the selection's retained population).
  It stays inspectable, labelled with the input identity it really examined, and never reads as this
  generation's result. Dependency *currentness* is a separate measurement (``ICR-R15@v1``).
* ``candidate`` -- the record's recorded binding is the compared candidate rather than a subject (a
  run the evidence owner selected by candidate binding, or a record supplied to a review that
  selected no subject at all). It is displayed as the candidate's own input, never as a judgment on
  a subject.
* ``unresolved`` -- a required binding could not be resolved. The record is displayed with the exact
  references it carries and the reason, so it is neither silently dropped nor readable as support
  for the selected subject.
* ``unrelated`` -- the record's own recorded subject is a subject this comparison records and the
  selection's recorded relationships do not reach. It is **not** displayed as a judgment on the
  selected subject at all, and the pane states how many records that is rather than dropping them
  silently.

``context`` is the sixth state and has its own value, :class:`ReviewContextRecord`: a record whose
recorded subject is *another* identity the selection reaches through an explicit recorded
relationship is displayed as **labelled context** -- with that identity, the recorded relationship
path that reached it and the record's own attribution visible, and with none of the judgment
content, which belongs to that other subject's own review.

Nothing here decides which treatment a record earns: the policy lives in
:mod:`agents_remember.application.review_record_applicability`, and this module makes the result
unrepresentable to misread. There is deliberately no field for a similarity, a confidence, a score
or a nearest match, so "this record probably applies" cannot be recorded here by a caller that
wanted to.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)
from agents_remember.models.knowledge.review_records import ReviewRecordClassName

__all__ = [
    "ReviewApplicabilityClass",
    "ReviewApplicabilityState",
    "ReviewApplicabilitySummary",
    "ReviewContextRecord",
    "ReviewDisplayedApplicability",
    "ReviewDisplayedApplicabilityState",
]

# The five collections a supplied record can come from, spelled as the record-class vocabulary
# minus its one measurement channel: ``assessment_currentness`` reports whether a measurement was
# supplied and is not a collection of records, so no record can be classified into it. The test
# module asserts this equality against ``ReviewRecordClassName`` so the two spellings cannot drift.
ReviewApplicabilityClass = Literal[
    "assessments",
    "detection_signals",
    "verification_observations",
    "authored_effects",
    "evidence_claims",
]
REVIEW_APPLICABILITY_CLASSES: tuple[ReviewApplicabilityClass, ...] = get_args(
    ReviewApplicabilityClass
)
assert set(REVIEW_APPLICABILITY_CLASSES) == set(get_args(ReviewRecordClassName)) - {
    "assessment_currentness"
}, "the applicability classes must be the record-class vocabulary minus its measurement channel"

# The six display treatments. ``unrelated`` is never attached to a displayed value -- a record that
# is unrelated is not displayed as a judgment on the selected subject -- so it exists here for the
# pane's stated count and in :class:`ReviewContextRecord`'s own vocabulary.
ReviewApplicabilityState = Literal[
    "direct", "historical", "context", "candidate", "unresolved", "unrelated"
]

# The five treatments a *displayed* record can carry. ``context`` is absent because a context record
# is displayed as its own labelled value rather than as the selected subject's record, and
# ``unrelated`` is absent because an unrelated record is not displayed as a judgment at all.
ReviewDisplayedApplicabilityState = Literal["direct", "historical", "candidate", "unresolved"]


class ReviewDisplayedApplicability(KnowledgeModel):
    """Why one displayed record may appear beside the selected subject, with its true subject.

    ``subject_kind``/``subject_id`` are the record's **own recorded subject** -- the identity its
    binding names -- and are absent only where the record records none. ``references`` are the exact
    recorded references the treatment was decided from, so a reader can re-read the decision instead
    of trusting the label; they are required for every state, because a label that names no recorded
    reference would be this surface's own opinion rather than a statement about the record.

    There is no field for a relevance, a similarity or a confidence: the treatment is one of four
    named facts about recorded bindings, and a fifth one cannot be spelled here.
    """

    records: ReviewApplicabilityClass
    record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    state: ReviewDisplayedApplicabilityState
    subject_kind: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    subject_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    subject_revision_ids: tuple[str, ...] = ()
    references: tuple[str, ...] = Field(min_length=1)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_subject_bearing_states_to_name_their_subject(
        self,
    ) -> ReviewDisplayedApplicability:
        """Refuse a "this is the subject's record" claim that names no subject.

        ``direct`` and ``historical`` are claims *about a subject*: the record's subject binding was
        resolved and it is the selected one (for ``direct``) or the same subject in another
        generation (for ``historical``). Either state without a subject identity would be an
        attribution this vocabulary could not show, which is the shape F09 had.
        """

        if self.state in ("direct", "historical") and (
            self.subject_kind is None or self.subject_id is None
        ):
            raise ValueError(
                f"a {self.state!r} record is displayed as a subject's own record, so it carries the "
                "recorded subject identity its binding named"
            )
        return self


class ReviewContextRecord(KnowledgeModel):
    """One record of another subject, displayed as the labelled context a recorded relationship admits.

    This is the value the packet's "related family/closure context" clause takes: the record's true
    subject, the recorded relationship path that reached it, and the record's own attribution
    (``author_ref``/``role_ref``) and references -- and deliberately **not** its judgment content.
    A sibling's finding, disposition or statement belongs to that sibling's own review; displaying
    it here would be the cross-subject contamination this requirement exists to prevent, while
    hiding the record entirely would erase recorded context the packet requires to stay visible.

    ``label`` names the record's **kind** -- the collection it came from, the condition a detection
    matched, the record kind a matrix row shows -- and never the record's judgment: a sibling's
    disposition, finding or rationale belongs to that sibling's own review, and a context row that
    carried one would be the cross-subject contamination this value exists to prevent.
    """

    records: ReviewApplicabilityClass
    record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    subject_kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    subject_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    subject_revision_ids: tuple[str, ...] = ()
    relationship: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    author_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    role_ref: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    references: tuple[str, ...] = ()
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class ReviewApplicabilitySummary(KnowledgeModel):
    """One displayed collection, counted by the treatment its records earned.

    ``supplied`` is the population this **pane's collection** was classified from, which is the same
    population its own channel counts for the three owner-read collections (``assessments``,
    ``detection_signals``, ``verification_observations``) and the rows the review matrix **page**
    returned for the two matrix-owned ones (``authored_effects``, ``evidence_claims``) -- there the
    page is the displayed population and the owner's complete collection count travels on
    ``ICR-R14@v1``'s channel, which the summary's own ``detail`` states. The six states partition it
    exactly, and the model refuses a partition that does not add up, because a count that silently
    loses a record is how filtering becomes erasure: the reader is told how many records were
    supplied, how many are displayed under each treatment, and how many are not displayed as this
    subject's judgments at all.

    ``unrelated`` records are named by count and not by identity: they are another subject's
    records, and this review is not the place that displays them. The detail says so and names the
    action that reaches them.
    """

    records: ReviewApplicabilityClass
    supplied: int = Field(ge=0)
    direct: int = Field(default=0, ge=0)
    historical: int = Field(default=0, ge=0)
    context: int = Field(default=0, ge=0)
    candidate: int = Field(default=0, ge=0)
    unresolved: int = Field(default=0, ge=0)
    unrelated: int = Field(default=0, ge=0)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_counts_to_partition_the_supplied_population(
        self,
    ) -> ReviewApplicabilitySummary:
        """Refuse a summary that does not account for every supplied record exactly once."""

        displayed = self.direct + self.historical + self.context + self.candidate + self.unresolved
        if displayed + self.unrelated != self.supplied:
            raise ValueError(
                f"the {self.records!r} summary must account for every supplied record exactly once; "
                f"{self.supplied} supplied, {displayed} displayed and {self.unrelated} not "
                "displayed as this subject's judgments"
            )
        return self

    @model_validator(mode="after")
    def _require_an_exclusion_to_state_itself(self) -> ReviewApplicabilitySummary:
        """Refuse an exclusion a reader cannot act on."""

        if self.unrelated and not self.detail.strip():
            raise ValueError(
                "a summary that excludes records states which records were excluded and how to "
                "reach them"
            )
        return self
