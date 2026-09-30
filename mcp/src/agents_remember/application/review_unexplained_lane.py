"""The unexplained-changes lane of a tree comparison, its entry count and its per-file response.

Three reads over one comparison of four Git trees, all through the one classification of
:mod:`.review_lane_classification`:

* :func:`lane_summary` -- the entry's attribution count. It reads the change inventory and each
  changed path's file bucket, and nothing else: no hunk, no subject catalogue, no family roster.
  A change inventory that could not be measured whole is ``partial`` (naming what was not measured)
  or ``unavailable``, never a count.
* :func:`unexplained_lane` -- the two destinations of the reviewer's tree. ``Unexplained changes``
  lists the unexplained files, then every attributed file with an unexplained hunk or a non-text
  change the gate holds as unexplained. ``Unknown attribution`` lists the files of unknown
  attribution with their reason, then every attributed file with an attribution-unknown hunk (or a
  non-text change whose gate linkage is unknown). No allowlist or file-type rule hides a file.
  It also gives every measured changed path its bucket (``paths``): the source explorer of a tree
  comparison labels the change inventory from it, so the explorer and the lane never disagree.
* :func:`classify_changed_path` -- the per-file classification response for one changed path:
  both sides' knowledge and entries, every hunk and its class, and for each linked hunk every
  intersecting entry with its invariant revision and that revision's family occurrences on the side.

A family occurrence's membership state compares the side's family record with the other side's:
an after-side occurrence is a ``member``; a before-side one is a ``member`` when the after record
still lists the invariant, ``removed_or_reassigned`` when it no longer does, ``before_only`` when the
after tree holds no record of the family, and ``membership_unknown`` when the after knowledge could
not be read. An invariant no family lists is ``confirmed_no_family`` only when every family record
of the side was read.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

import apsw

from agents_remember.application.review_lane_classification import (
    FileResult,
    HunkResult,
    LaneReadError,
    Placed,
    SideReading,
    TreeLane,
    bounded_text,
    open_tree_lane,
)
from agents_remember.application.review_source_inventory import byte_form
from agents_remember.application.review_tree_comparison import ReviewTrees
from agents_remember.memory.knowledge.tree_observation import TreeChange, TreePaths
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH, REFERENCE_MAX_LENGTH
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.review_lane import (
    LaneBucket,
    LaneGateLinkage,
    LaneHunkClass,
    LaneMembershipState,
    LaneSideName,
    ReviewFileClassification,
    ReviewLaneDestination,
    ReviewLaneEntryRange,
    ReviewLaneFamilyOccurrence,
    ReviewLaneFile,
    ReviewLaneHunk,
    ReviewLaneLink,
    ReviewLanePath,
    ReviewLaneSide,
    ReviewLaneSpan,
    ReviewLaneSummary,
    ReviewUnexplainedLane,
)

__all__ = ["classify_changed_path", "lane_summary", "unexplained_lane"]

# A code tree Git cannot list or compare, or a knowledge index that fails mid-read: named, never
# answered as an empty lane or a zero count.
_UNREADABLE = "the comparison's trees cannot be read or compared: {error}"
# The hunk class each destination's bucket files are listed with.
_BUCKET_CLASS: Mapping[LaneBucket, LaneHunkClass] = {
    "unexplained": "unexplained",
    "attribution_unknown": "attribution_unknown",
}


# -- the entry's count ------------------------------------------------------------------------------


def lane_summary(trees: ReviewTrees) -> ReviewLaneSummary:
    """How many changed files are unexplained and how many of unknown attribution (file level only)."""

    try:
        with open_tree_lane(trees) as lane:
            observed = lane.observe()
            if not observed.available:
                return ReviewLaneSummary(state="unavailable", detail=observed.detail)
            if observed.partial:
                return ReviewLaneSummary(
                    state="partial", detail=observed.detail, unmeasured=_unmeasured(observed)
                )
            buckets: Counter[LaneBucket] = Counter(
                lane.bucket(path)[0] for path in sorted(set(observed.paths))
            )
    except (LaneReadError, apsw.Error) as error:
        return ReviewLaneSummary(state="unavailable", detail=_UNREADABLE.format(error=error))
    return ReviewLaneSummary(
        state="counted",
        changed_total=sum(buckets.values()),
        attributed=buckets["attributed"],
        unexplained=buckets["unexplained"],
        attribution_unknown=buckets["attribution_unknown"],
    )


def _unmeasured(observed: TreePaths) -> tuple[str, ...]:
    """The changed paths the inventory could not report whole: undecodable names, unclassified content."""

    return (
        *(f"{byte_form(change.path)} (name is not text)" for change in observed.unrepresentable),
        *(
            f"{change.path} (content not classified)"
            for change in observed.entries
            if change.content == "unknown"
        ),
    )


# -- the lane --------------------------------------------------------------------------------------


def unexplained_lane(trees: ReviewTrees) -> ReviewUnexplainedLane:
    """The ``Unexplained changes`` and ``Unknown attribution`` destinations of one comparison."""

    try:
        with open_tree_lane(trees) as lane:
            observed = lane.observe()
            if not observed.available:
                return ReviewUnexplainedLane(state="unavailable", detail=observed.detail)
            results = [lane.classify(change) for change in observed.entries]
    except (LaneReadError, apsw.Error) as error:
        return ReviewUnexplainedLane(state="unavailable", detail=_UNREADABLE.format(error=error))
    buckets: Counter[LaneBucket] = Counter(result.bucket for result in results)
    return ReviewUnexplainedLane(
        state="partial" if observed.partial else "measured",
        detail=_lane_detail(buckets, observed),
        changed_total=len(results),
        attributed=buckets["attributed"],
        unexplained=buckets["unexplained"],
        attribution_unknown=buckets["attribution_unknown"],
        unexplained_changes=_destination(results, "unexplained", "unexplained"),
        unknown_attribution=_destination(results, "attribution_unknown", "unknown"),
        unmeasured=_unmeasured(observed) if observed.partial else (),
        paths=tuple(
            ReviewLanePath(path=one.change.path, bucket=one.bucket)
            for one in sorted(results, key=_path)
        ),
    )


def _lane_detail(buckets: Counter[LaneBucket], observed: TreePaths) -> str:
    total = sum(buckets.values())
    detail = (
        f"{total} changed file(s): {buckets['attributed']} attributed, "
        f"{buckets['unexplained']} unexplained, {buckets['attribution_unknown']} of unknown "
        "attribution"
    )
    if observed.partial:
        detail += f"; the change inventory is partial: {observed.detail}"
    return detail


def _destination(
    results: Sequence[FileResult], bucket: LaneBucket, gate: LaneGateLinkage
) -> ReviewLaneDestination:
    """One destination: the files of ``bucket``, then the attributed files carrying its class."""

    hunk_class = _BUCKET_CLASS[bucket]
    own = sorted((one for one in results if one.bucket == bucket), key=_path)
    attributed = sorted((one for one in results if _carries(one, hunk_class, gate)), key=_path)
    listed = [*own, *attributed]
    return ReviewLaneDestination(
        files=tuple(_lane_file(one) for one in listed),
        bucket_files=len(own),
        attributed_files=len(attributed),
        hunks=sum(_of_class(one, hunk_class) for one in listed),
        non_text=sum(1 for one in listed if one.non_text is not None),
    )


def _of_class(result: FileResult, hunk_class: LaneHunkClass) -> int:
    return sum(1 for hunk in result.hunks if hunk.classification == hunk_class)


def _carries(result: FileResult, hunk_class: LaneHunkClass, gate: LaneGateLinkage) -> bool:
    """Whether an attributed file carries a hunk of ``hunk_class``, or a non-text change whose gate
    linkage is ``gate`` (a gate-held change is never invisible in the lane)."""

    if result.bucket != "attributed":
        return False
    if any(hunk.classification == hunk_class for hunk in result.hunks):
        return True
    return result.non_text is not None and result.non_text.gate == gate


def _path(result: FileResult) -> str:
    return result.change.path


def _lane_file(result: FileResult) -> ReviewLaneFile:
    reasons = sorted(
        {f"{unknown.side}: {unknown.detail}" for hunk in result.hunks for unknown in hunk.unknown}
    )
    return ReviewLaneFile(
        path=result.change.path,
        status=result.change.status,
        content=result.change.content,
        bucket=result.bucket,
        reason=result.reason,
        counts=result.counts(),
        non_text=result.non_text,
        unknown_reasons=tuple(reasons),
    )


# -- the per-file response -------------------------------------------------------------------------


def classify_changed_path(
    trees: ReviewTrees, path: str
) -> ReviewFileClassification | ReviewRefusal:
    """The per-file classification response for ``path``, a changed path of the comparison."""

    try:
        with open_tree_lane(trees) as lane:
            observed = lane.observe()
            if not observed.available:
                return _refusal(observed.detail, path)
            change = next((one for one in observed.entries if one.path == path), None)
            if change is None:
                return _refusal(
                    f"{path} is not a changed path of tree comparison {trees.record.number}",
                    path,
                )
            return _classification(lane, lane.classify(change))
    except (LaneReadError, apsw.Error) as error:
        return _refusal(_UNREADABLE.format(error=error), path)


def _refusal(detail: str, path: str) -> ReviewRefusal:
    """The typed refusal; a path longer than the refusal's input field is named by its clipped prefix."""

    return ReviewRefusal(
        code="source_content_unresolved",
        detail=bounded_text(detail, PROSE_MAX_LENGTH),
        next_action="name a path the comparison's change inventory lists, or reopen the review",
        offending_input=bounded_text(path, REFERENCE_MAX_LENGTH),
    )


def _classification(lane: TreeLane, result: FileResult) -> ReviewFileClassification:
    change = result.change
    return ReviewFileClassification(
        path=change.path,
        status=change.status,
        content=change.content,
        mode_change=change.mode_change,
        bucket=result.bucket,
        reason=result.reason,
        sides=(_side(result.before, change), _side(result.after, change)),
        hunks=tuple(_hunk(lane, one) for one in result.hunks),
        non_text=result.non_text,
        counts=result.counts(),
    )


def _side(reading: SideReading, change: TreeChange) -> ReviewLaneSide:
    """One side of the path. The inventory lists a rename as a deletion and an addition, so a
    side's path is the changed path itself, or ``None`` on the side where it does not exist."""

    name = reading.side.name
    absent = (name == "before" and change.status == "added") or (
        name == "after" and change.status == "deleted"
    )
    return ReviewLaneSide(
        side=name,
        path=None if absent else change.path,
        blob=reading.blob,
        knowledge="unavailable" if reading.unavailable is not None else "available",
        detail=reading.unavailable,
        entries=tuple(_entry_range(one) for one in reading.placed),
    )


def _entry_range(placed: Placed) -> ReviewLaneEntryRange:
    start, end = placed.span if placed.span is not None else (None, None)
    return ReviewLaneEntryRange(
        id=placed.entry.id,
        kind=placed.entry.kind,
        invariant=placed.entry.invariant,
        start_line=start,
        end_line=end,
        reason=placed.reason,
        detail=placed.detail,
    )


def _hunk(lane: TreeLane, result: HunkResult) -> ReviewLaneHunk:
    hunk = result.hunk
    return ReviewLaneHunk(
        before=ReviewLaneSpan(start=hunk.old_start, count=hunk.old_count),
        after=ReviewLaneSpan(start=hunk.new_start, count=hunk.new_count),
        classification=result.classification,
        links=tuple(_link(lane, side, placed) for side, placed in result.links),
        unknown=result.unknown,
    )


def _link(lane: TreeLane, side: LaneSideName, placed: Placed) -> ReviewLaneLink:
    entry = placed.entry
    assert placed.span is not None  # only a supplied range links a hunk
    revision = lane.side(side).revision(entry.invariant)
    facet = entry.document.get("facet") if entry.kind == "proof" else None
    return ReviewLaneLink(
        side=side,
        id=entry.id,
        kind=entry.kind,
        facet=facet if isinstance(facet, str) and facet.strip() else None,
        invariant=entry.invariant,
        invariant_key=text_uuid("identity", entry.invariant),
        invariant_revision=revision,
        invariant_revision_key=(
            None if revision is None else text_uuid("revision", f"{entry.invariant}@{revision}")
        ),
        start_line=placed.span[0],
        end_line=placed.span[1],
        families=_occurrences(lane, side, entry.invariant),
    )


def _occurrences(
    lane: TreeLane, side: LaneSideName, invariant: str
) -> tuple[ReviewLaneFamilyOccurrence, ...]:
    own = lane.side(side)
    families = own.families_of(invariant)
    if not families:
        state: LaneMembershipState = (
            "confirmed_no_family" if own.families_complete else "membership_unknown"
        )
        return (ReviewLaneFamilyOccurrence(state=state),)
    return tuple(
        ReviewLaneFamilyOccurrence(
            family=family,
            family_key=text_uuid("identity", family),
            family_revision=own.revision(family),
            state=_membership(lane, side, family, invariant),
        )
        for family in families
    )


def _membership(
    lane: TreeLane, side: LaneSideName, family: str, invariant: str
) -> LaneMembershipState:
    if side == "after":
        return "member"
    after = lane.other(side)
    if after.index is None:
        return "membership_unknown"
    members = after.family_members(family)
    if members is None:
        return "before_only" if after.families_complete else "membership_unknown"
    return "member" if invariant in members else "removed_or_reassigned"
