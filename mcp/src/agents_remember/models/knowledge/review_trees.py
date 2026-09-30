"""A review comparison as four Git trees, and the reviewer's tree view of it (MIK-R25).

With knowledge as text in Git (D18), every committed state of both repositories is already a Git
tree, so a review comparison is four trees: code base, code candidate, memory base and memory
candidate. This module fixes two shapes:

* :class:`ReviewTreeComparisonRecord` -- the durable record of one comparison
  (``ar-review-tree-comparison/v1``): the four tree ids, and for an uncommitted candidate the Git ref
  that pins it (``refs/ar/review/<task-id>/<leaf-id>/<n>``, the same name in the code and in the
  memory repository). A committed side names its commit and needs no ref. A before side that was
  unconverted when compared names the conversion it was read as (MIK-R24 rule 7).
* :class:`ReviewTreesResult` -- what the tree view route returns for one leaf: the comparison, each
  knowledge side's index state, the Git diff of the two memory trees grouped by record and by
  source path, each invariant's MIK-R03 currentness per side, and the MIK-R08 worklist view.

No database copy is part of either shape: each knowledge side is the derived index of its tree
(MIK-R23), which is rebuilt from the tree and never retained with the comparison.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.review_lane import (
    ReviewFileClassification,
    ReviewUnexplainedLane,
)
from agents_remember.models.knowledge.review_tree_entries import ReviewTreeEntry

__all__ = [
    "REVIEW_REF_NAMESPACE",
    "REVIEW_TREE_COMPARISON_SCHEMA",
    "ReviewCodeSide",
    "ReviewConvertedBase",
    "ReviewKnowledgeFileChange",
    "ReviewKnowledgeRecordGroup",
    "ReviewKnowledgeSide",
    "ReviewKnowledgeSourceGroup",
    "ReviewKnowledgeTreeDiff",
    "ReviewTreeComparisonRecord",
    "ReviewTreeSide",
    "ReviewTreeSideState",
    "ReviewTreesResult",
    "ReviewTreesState",
    "ReviewWorklistView",
]

REVIEW_TREE_COMPARISON_SCHEMA: Literal["ar-review-tree-comparison/v1"] = (
    "ar-review-tree-comparison/v1"
)
# The one ref namespace review pins live under, in both repositories. It is outside ``refs/heads``
# and ``refs/remotes``, so nothing fetches, pushes or merges it, and the task's archive hook finds
# every pin of a task with one ``for-each-ref`` over ``refs/ar/review/<task-id>/``.
REVIEW_REF_NAMESPACE = "refs/ar/review"

# How one side of a comparison resolves when it is read back.
#
# * ``available`` -- Git still produces the recorded tree;
# * ``unavailable-history`` -- Git can no longer produce it; the tree is named and nothing is
#   substituted for it (rule 4);
# * ``legacy-unavailable`` -- a knowledge side recorded before the repository's conversion, which
#   the reviewer does not read (no database is read for a review).
ReviewTreeSideState = Literal["available", "unavailable-history", "legacy-unavailable"]


class ReviewTreeSide(KnowledgeModel):
    """One of the four trees: the repository holding it, the tree, and how it is kept alive.

    ``commit`` is the commit whose tree this is when the side is committed; ``ref`` is the pinning
    ref of an uncommitted side. Exactly one of the two is present on a side this reviewer recorded.
    """

    repository: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    tree: str = Field(pattern=GIT_OBJECT_PATTERN)
    commit: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    ref: str | None = Field(default=None, min_length=1, max_length=REFERENCE_MAX_LENGTH)


class ReviewConvertedBase(KnowledgeModel):
    """The conversion an unconverted memory base was read as (MIK-R24 rule 7).

    The conversion is a pure function of the memory commit, the pinned conversion-format version
    and the code commit it was anchored at, so it is recorded by those three inputs plus the tree id
    it produced; a reopen re-derives it and checks the tree id rather than pinning it.
    """

    commit: str = Field(pattern=GIT_OBJECT_PATTERN)
    version: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    code_commit: str = Field(pattern=GIT_OBJECT_PATTERN)
    tree: str = Field(pattern=GIT_OBJECT_PATTERN)


class ReviewTreeComparisonRecord(KnowledgeModel):
    """One recorded comparison: four trees, and the refs that pin the uncommitted ones (rule 1).

    ``number`` is the ``<n>`` of the comparison's refs and file. ``0`` marks a comparison between a
    closed leaf's recorded committed endpoints, which needs no ref and no record of its own.
    """

    schema_: Literal["ar-review-tree-comparison/v1"] = Field(
        default=REVIEW_TREE_COMPARISON_SCHEMA, alias="schema"
    )
    task_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    number: int = Field(ge=0)
    code_base: ReviewTreeSide
    code_candidate: ReviewTreeSide
    memory_base: ReviewTreeSide
    memory_candidate: ReviewTreeSide
    converted_base: ReviewConvertedBase | None = None
    recorded_at: str = Field(min_length=1, max_length=64)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    def same_trees(self, other: ReviewTreeComparisonRecord) -> bool:
        """Whether two records are one comparison: the same task, leaf and four trees."""

        def key(record: ReviewTreeComparisonRecord) -> tuple[tuple[str, str], ...]:
            return tuple(
                (side.repository, side.tree)
                for side in (
                    record.code_base,
                    record.code_candidate,
                    record.memory_base,
                    record.memory_candidate,
                )
            )

        return (
            (self.task_id, self.leaf_id) == (other.task_id, other.leaf_id)
            and key(self) == key(other)
            and self.converted_base == other.converted_base
        )


class ReviewKnowledgeSide(KnowledgeModel):
    """One memory side as the reviewer read it: its tree, its state and its index's state.

    ``tree`` is the tree the index was built from -- the converted tree for a converted base.
    ``index_state`` is ``partial`` when some file failed its schema; ``problems`` names them, so a
    side read from a partial index is never presented as complete (Failure and Recovery).
    """

    side: Literal["before", "after"]
    state: ReviewTreeSideState
    tree: str = Field(pattern=GIT_OBJECT_PATTERN)
    index_state: Literal["complete", "partial"] | None = None
    problems: tuple[tuple[str, str], ...] = ()
    detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)


class ReviewCodeSide(KnowledgeModel):
    """One code tree of a reopened comparison: ``available``, or ``unavailable-history``, named."""

    side: Literal["base", "candidate"]
    state: Literal["available", "unavailable-history"]
    tree: str = Field(pattern=GIT_OBJECT_PATTERN)
    detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)


class ReviewKnowledgeFileChange(KnowledgeModel):
    """One changed knowledge file between the two memory trees, with its Git patch."""

    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    old_path: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    status: Literal["added", "deleted", "modified", "renamed", "type_changed"]
    patch: str = Field(max_length=PROSE_MAX_LENGTH)
    truncated: bool = False


class ReviewKnowledgeRecordGroup(KnowledgeModel):
    """Every change about one record: its record file, and the entries naming it that changed.

    ``entries`` names each realization or proof entry whose sidecar item was added, removed or
    changed, as ``(entry id, source path, change)``, so a record's code-side changes are visible
    under the record as well as under their source path.
    """

    record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    kind: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    files: tuple[ReviewKnowledgeFileChange, ...] = ()
    entries: tuple[tuple[str, str, str], ...] = ()


class ReviewKnowledgeSourceGroup(KnowledgeModel):
    """Every change to one source path's sidecar, with the records its changed entries name."""

    source_path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    files: tuple[ReviewKnowledgeFileChange, ...]
    records: tuple[str, ...] = ()


class ReviewKnowledgeTreeDiff(KnowledgeModel):
    """The Git diff of the two memory trees' knowledge files (rule 2).

    ``records`` and ``sources`` are the two groupings; ``history`` holds the changed per-leaf history
    files; ``other`` any other changed knowledge file (a layout marker, a route sidecar).
    """

    before_tree: str = Field(pattern=GIT_OBJECT_PATTERN)
    after_tree: str = Field(pattern=GIT_OBJECT_PATTERN)
    changed_files: int = Field(ge=0)
    records: tuple[ReviewKnowledgeRecordGroup, ...] = ()
    sources: tuple[ReviewKnowledgeSourceGroup, ...] = ()
    history: tuple[ReviewKnowledgeFileChange, ...] = ()
    other: tuple[ReviewKnowledgeFileChange, ...] = ()


class ReviewWorklistView(KnowledgeModel):
    """The leaf's MIK-R08 worklist as the reviewer shows it (rule 3).

    ``items`` are the worklist's own items, verbatim. ``history_rows`` are the rows about their
    subjects in the after memory tree, shown without a current or stale mark: the gate leaf
    (MIK-R09) owns that rule. ``changes`` is the worklist's gate linkage, each text hunk marked
    ``linked`` or not (unexplained). ``source`` says where the worklist came from, and ``bound``
    whether its recorded pairing is exactly this comparison's four trees.
    """

    source: Literal["computed", "persisted", "absent"]
    bound: bool = False
    state: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    items: tuple[dict[str, Any], ...] = ()
    history_rows: tuple[dict[str, Any], ...] = ()
    changes: tuple[dict[str, Any], ...] = ()
    incomplete: tuple[dict[str, Any], ...] = ()


ReviewTreesState = Literal["trees", "not-converted", "refused"]


class ReviewTreesResult(KnowledgeModel):
    """The reviewer's tree view of one leaf, or why there is none.

    ``not-converted`` is the leaf whose memory is unconverted: its review is the dataset review
    exactly as before, and nothing here applies. ``refused`` carries the typed refusal.
    """

    state: ReviewTreesState
    repository_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    master: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    comparison: ReviewTreeComparisonRecord | None = None
    knowledge_sides: tuple[ReviewKnowledgeSide, ...] = ()
    code_sides: tuple[ReviewCodeSide, ...] = ()
    knowledge_diff: ReviewKnowledgeTreeDiff | None = None
    currentness: dict[str, dict[str, Any]] | None = None
    worklist: ReviewWorklistView | None = None
    # Every realization and proof entry, on both code sides, for the focused cards (MIK-R31).
    entries: tuple[ReviewTreeEntry, ...] = ()
    # The unexplained-changes lane's destinations, or one changed path's classification (MIK-R32).
    lane: ReviewUnexplainedLane | None = None
    file_classification: ReviewFileClassification | None = None
    refusal: ReviewRefusal | None = None
