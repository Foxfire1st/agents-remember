"""The unexplained-changes lane of a tree comparison: file buckets and hunk classification (MIK-R32).

Changed source that no recorded entry's range intersects is a review destination of its own. This
module fixes the three shapes the lane is served in, all over one comparison of four Git trees:

* :class:`ReviewLaneSummary` -- the bounded entry count: how many changed files are *unexplained*
  and how many of *unknown attribution*, beside the changed-intent summary. It is computed from the
  file buckets alone and never from hunks.
* :class:`ReviewUnexplainedLane` -- the two destinations of the reviewer's tree. ``Unexplained
  changes`` lists the unexplained files, then the attributed files with unexplained hunks (or a
  non-text change the gate holds as unexplained); ``Unknown attribution`` lists the files of unknown
  attribution, then the attributed files with attribution-unknown hunks. Each file carries its hunk
  counts; the destination reports its file and hunk totals separately.
* :class:`ReviewFileClassification` -- the one per-file response: the file's bucket and reason, each
  side's path, blob and knowledge availability with every recorded entry and the range it supplies
  (or why none), and every hunk with its classification. A linked hunk lists every intersecting
  entry per side with its invariant, the invariant's revision on that side, the range and each family
  occurrence of that revision with its membership state; an attribution-unknown hunk names the reason
  per side. The triage badges and the per-hunk markers read this response and nothing else.

**Buckets.** A path is *attributed* when an entry supplies a range on an available side;
*unexplained* when neither side records any entry for it and both sides were read; otherwise of
*attribution unknown* (a side whose knowledge is unavailable, or entries that supply no range). The
three sum to the changed-file total. An entry supplies a range on a side only when that side's blob
is exactly the blob it recorded; a stale, unresolved or unsupported entry supplies none.

**Hunks.** A hunk is *linked* when its changed lines on a side intersect a range supplied there;
otherwise *attribution unknown* when, on a side where it has changed lines, the knowledge is
unavailable or an entry at the path supplies no range; otherwise *unexplained*. ``linked`` asserts
intersection only -- never coverage, correctness or preservation.

A proof entry supplies ranges like a realization entry; it is carried with ``kind: proof`` (a test)
and its facet. Nothing here is an assessment, an approval or a suggested explanation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)

__all__ = [
    "LaneBucket",
    "LaneGateLinkage",
    "LaneHunkClass",
    "LaneMembershipState",
    "LaneRangeReason",
    "LaneSideName",
    "ReviewFileClassification",
    "ReviewLaneCounts",
    "ReviewLaneDestination",
    "ReviewLaneEntryRange",
    "ReviewLaneFamilyOccurrence",
    "ReviewLaneFile",
    "ReviewLaneHunk",
    "ReviewLaneLink",
    "ReviewLaneNonText",
    "ReviewLanePath",
    "ReviewLaneSide",
    "ReviewLaneSpan",
    "ReviewLaneSummary",
    "ReviewLaneUnknown",
    "ReviewUnexplainedLane",
]

LaneSideName = Literal["before", "after"]
LaneBucket = Literal["attributed", "unexplained", "attribution_unknown"]
LaneHunkClass = Literal["linked", "unexplained", "attribution_unknown"]
# Why an entry supplies no range on a side: its recorded blob is not that side's blob, the side has
# no regular file at the path, the locator binds nothing there, the locator kind (or the file's
# grammar) is not one the resolution reads, or a Git read failed.
LaneRangeReason = Literal[
    "recorded_blob_mismatch", "path_absent", "unresolved", "unsupported_locator", "unreadable"
]
LaneUnknownReason = Literal["knowledge_unavailable", "range_not_supplied"]
LaneMembershipState = Literal[
    "member", "before_only", "removed_or_reassigned", "confirmed_no_family", "membership_unknown"
]
# The gate's own file-level linkage of a non-text change (a ``file``-locator entry covers it), or
# ``unknown`` when a knowledge side could not be read.
LaneGateLinkage = Literal["linked", "unexplained", "unknown"]


class ReviewLaneSpan(KnowledgeModel):
    """One side of a zero-context hunk: ``count`` changed lines from ``start``.

    A side with ``count == 0`` changes nothing there; ``start`` is then the line after which the
    other side's lines were inserted or removed, exactly as Git prints it.
    """

    start: int = Field(ge=0)
    count: int = Field(ge=0)


class ReviewLaneEntryRange(KnowledgeModel):
    """One entry recorded at the path on one side, and the range it supplies there, or why none."""

    id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    kind: Literal["realization", "proof"]
    invariant: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    reason: LaneRangeReason | None = None
    detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _range_or_reason(self) -> ReviewLaneEntryRange:
        supplied = self.start_line is not None and self.end_line is not None
        if supplied == (self.reason is not None):
            raise ValueError("an entry either supplies a range or names why it supplies none")
        return self


class ReviewLaneFamilyOccurrence(KnowledgeModel):
    """One recorded family occurrence of an invariant's revision on one side, or its absence.

    ``family`` is ``None`` exactly for ``confirmed_no_family`` and for a ``membership_unknown`` that
    names no family (the side's family records could not all be read). ``family_key`` is the
    identity the landed review payload addresses the family by.
    """

    family: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    family_key: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    family_revision: int | None = Field(default=None, ge=1)
    state: LaneMembershipState


class ReviewLaneLink(KnowledgeModel):
    """One entry whose supplied range intersects a hunk's changed lines on one side.

    ``invariant_revision`` is the invariant record's ``revision`` in that side's memory tree (``None``
    when the tree holds no record for it); ``invariant_key`` and ``invariant_revision_key`` are the
    identities the landed review payload addresses the invariant and that revision by. A proof entry
    (a test) carries its ``facet``.
    """

    side: LaneSideName
    id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    kind: Literal["realization", "proof"]
    facet: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    invariant: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    invariant_key: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    invariant_revision: int | None = Field(default=None, ge=1)
    invariant_revision_key: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    families: tuple[ReviewLaneFamilyOccurrence, ...] = ()


class ReviewLaneUnknown(KnowledgeModel):
    """Why an attribution-unknown hunk is not unexplained, on one side where it has changed lines."""

    side: LaneSideName
    reason: LaneUnknownReason
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    entries: tuple[str, ...] = ()


class ReviewLaneHunk(KnowledgeModel):
    """One zero-context hunk of the path, keyed by its side line numbers, and its classification."""

    before: ReviewLaneSpan
    after: ReviewLaneSpan
    classification: LaneHunkClass
    links: tuple[ReviewLaneLink, ...] = ()
    unknown: tuple[ReviewLaneUnknown, ...] = ()

    @model_validator(mode="after")
    def _facts_follow_the_class(self) -> ReviewLaneHunk:
        if (self.classification == "linked") != bool(self.links):
            raise ValueError("a hunk is linked exactly when an entry's range intersects it")
        if (self.classification == "attribution_unknown") != bool(self.unknown):
            raise ValueError("an attribution-unknown hunk names its reason on each side")
        return self


class ReviewLaneSide(KnowledgeModel):
    """One side of the path: where it is, its blob, and what that side's knowledge records there."""

    side: LaneSideName
    path: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    blob: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    knowledge: Literal["available", "unavailable"]
    detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    entries: tuple[ReviewLaneEntryRange, ...] = ()


class ReviewLaneNonText(KnowledgeModel):
    """A change classified at file level only: binary, symlink, submodule, type or mode change.

    ``content`` is the change inventory's renderability state; ``gate`` is the gate's own linkage of
    the change (MIK-R08 definition 8), so a change the gate holds as unexplained is never invisible.
    """

    content: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    mode_change: bool = False
    gate: LaneGateLinkage


class ReviewLaneCounts(KnowledgeModel):
    """How many hunks the path has, and how many of each class."""

    hunks: int = Field(ge=0)
    linked: int = Field(ge=0)
    unexplained: int = Field(ge=0)
    attribution_unknown: int = Field(ge=0)

    @model_validator(mode="after")
    def _classes_sum_to_hunks(self) -> ReviewLaneCounts:
        if self.linked + self.unexplained + self.attribution_unknown != self.hunks:
            raise ValueError("every hunk takes exactly one class")
        return self


class ReviewFileClassification(KnowledgeModel):
    """The per-file classification response for one changed path of one comparison."""

    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    status: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    content: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    mode_change: bool = False
    bucket: LaneBucket
    reason: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    sides: tuple[ReviewLaneSide, ReviewLaneSide]
    hunks: tuple[ReviewLaneHunk, ...] = ()
    non_text: ReviewLaneNonText | None = None
    counts: ReviewLaneCounts


class ReviewLaneFile(KnowledgeModel):
    """One changed file as a lane destination lists it: its bucket, reason and hunk counts."""

    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    status: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    content: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    bucket: LaneBucket
    reason: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    counts: ReviewLaneCounts
    non_text: ReviewLaneNonText | None = None
    # The reasons of the file's attribution-unknown hunks, one sentence per side and reason.
    unknown_reasons: tuple[str, ...] = ()


class ReviewLaneDestination(KnowledgeModel):
    """One destination of the tree: its bucket's files first, then the attributed files it lists.

    ``bucket_files`` counts the files of the destination's own bucket (``files`` starts with them);
    ``attributed_files`` the attributed files listed after them; ``hunks`` the hunks of the
    destination's class across every listed file, and ``non_text`` the listed non-text changes. A
    hunk total never changes a file total.
    """

    files: tuple[ReviewLaneFile, ...] = ()
    bucket_files: int = Field(ge=0)
    attributed_files: int = Field(ge=0)
    hunks: int = Field(ge=0)
    non_text: int = Field(ge=0)

    @model_validator(mode="after")
    def _files_are_the_totals(self) -> ReviewLaneDestination:
        if len(self.files) != self.bucket_files + self.attributed_files:
            raise ValueError("a destination lists exactly its bucket files and attributed files")
        return self


class ReviewLanePath(KnowledgeModel):
    """One measured changed path and its bucket: what every other view of the comparison labels it."""

    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    bucket: LaneBucket


LaneMeasurement = Literal["measured", "partial", "unavailable"]


class ReviewUnexplainedLane(KnowledgeModel):
    """The two lane destinations of one comparison, with the bucket totals they reconcile to.

    ``partial`` lists what was measured and names the rest in ``unmeasured``; ``unavailable`` has no
    totals and no files, only the reason -- an unmeasured change set is never an empty lane.
    ``paths`` gives every measured changed path its bucket, so a view that labels the whole change
    inventory (the source explorer) shows this classification and never another.
    """

    state: LaneMeasurement
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    changed_total: int | None = Field(default=None, ge=0)
    attributed: int | None = Field(default=None, ge=0)
    unexplained: int | None = Field(default=None, ge=0)
    attribution_unknown: int | None = Field(default=None, ge=0)
    unexplained_changes: ReviewLaneDestination | None = None
    unknown_attribution: ReviewLaneDestination | None = None
    unmeasured: tuple[str, ...] = ()
    paths: tuple[ReviewLanePath, ...] = ()

    @model_validator(mode="after")
    def _buckets_reconcile(self) -> ReviewUnexplainedLane:
        totals = (self.changed_total, self.attributed, self.unexplained, self.attribution_unknown)
        problem = _totals_problem(totals, counted=self.state != "unavailable")
        if problem is None and len(self.paths) != (self.changed_total or 0):
            problem = "every measured changed path is listed with its bucket, once"
        if problem is not None:
            raise ValueError(problem)
        return self


LaneSummaryState = Literal["counted", "partial", "unavailable"]


class ReviewLaneSummary(KnowledgeModel):
    """The entry's attribution count: unexplained and unknown files, read from file buckets only.

    ``counted`` carries the three bucket totals. ``partial`` (the change set was measured with some
    paths not reportable whole) carries no count and names the unmeasured scope; ``unavailable``
    carries only its reason. Neither is ever a zero.
    """

    state: LaneSummaryState
    changed_total: int | None = Field(default=None, ge=0)
    attributed: int | None = Field(default=None, ge=0)
    unexplained: int | None = Field(default=None, ge=0)
    attribution_unknown: int | None = Field(default=None, ge=0)
    detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    unmeasured: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _counts_only_when_counted(self) -> ReviewLaneSummary:
        totals = (self.changed_total, self.attributed, self.unexplained, self.attribution_unknown)
        problem = _totals_problem(totals, counted=self.state == "counted")
        if problem is None and self.state != "counted" and not self.detail:
            problem = "a summary without counts says why"
        if problem is not None:
            raise ValueError(problem)
        return self


def _totals_problem(totals: tuple[int | None, ...], *, counted: bool) -> str | None:
    """Why ``(changed_total, attributed, unexplained, attribution_unknown)`` is not a valid shape.

    A counted value carries all four and its three buckets sum to the total; any other value
    carries none of them, so an unmeasured change set can never read as zero.
    """

    changed_total, *buckets = totals
    if not counted:
        return (
            None if all(value is None for value in totals) else "only a measured value has totals"
        )
    if changed_total is None or any(value is None for value in buckets):
        return "a measured value carries every bucket total"
    if sum(value or 0 for value in buckets) != changed_total:
        return "the three buckets sum to the changed-file total"
    return None
