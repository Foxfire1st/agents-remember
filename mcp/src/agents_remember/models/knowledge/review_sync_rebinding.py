"""Tree-bound sync rebinding v2, plus the read-only v1 decoder for retained historical receipts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.review_final_output_receipt import tree_comparison_digest
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord

__all__ = [
    "REVIEW_SYNC_REBINDING_VERSION",
    "REVIEW_SYNC_SELECTION_RULE",
    "LegacyReviewSyncRebinding",
    "ReviewSyncRebinding",
    "ReviewSyncRebindingVerdict",
    "SyncChannelMatch",
    "SyncKnowledgeObservation",
    "code_channel_match",
    "knowledge_channel_match",
    "review_sync_verdict",
    "tree_sync_verdict",
]

# The record's own version, a literal of this vocabulary's rather than a package version read at run
# time, so two rebindings produced by different layouts are distinguishable from the records.
REVIEW_SYNC_REBINDING_VERSION: Literal["ar-review-sync-rebinding/v2"] = (
    "ar-review-sync-rebinding/v2"
)

# Which generation a rebinding judges. The same rule the final-output receipt names, spelled here so a
# reader of this record does not have to import the owner that produced it to learn what it selected.
REVIEW_SYNC_SELECTION_RULE: Literal["latest-recorded-tree-comparison"] = (
    "latest-recorded-tree-comparison"
)

# Whether one channel's post-sync identity is the identity the review recorded. ``selected-not-retained``
# is not a fourth kind of mismatch: it is the state of a channel the generation itself declined to
# compare -- a half recorded as a typed absence -- and it is a fact about the review rather than about
# the sync.
SyncChannelMatch = Literal["matches-reviewed-input", "differs-from-reviewed-input", "unmeasured"]

# The record's one verdict, and the reason it has three values. ``current`` is the only value that
# claims the review still describes the resolved pair, so it requires a measured match on every channel
# the generation actually retained -- a comparison the review never made must never read as agreement.
# ``moved`` is a measured difference on either channel. ``unmeasured`` is neither: the generation
# retained a knowledge operand and the declared publication location held no dataset to compare it
# with, so the pair's knowledge half is simply unknown at this moment, and the validator refuses that
# value beside a location that did hold a readable dataset.
ReviewSyncRebindingVerdict = Literal["current", "moved", "unmeasured"]

# The two states a knowledge channel can hold instead of a readable dataset, in the vocabulary of the
# route that resolves the declared publication location (ICR-R20@v1): ``not-recorded`` is a location
# holding no file at all, and ``unusable`` is a location holding something this code cannot read as a
# dataset of this repository. Both are reported as the facts they are, and neither is a measured
# mismatch.
SyncKnowledgeState = Literal["published", "not-recorded", "unusable"]


def code_channel_match(reviewed_code_tree: str, resolved_code_tree: str) -> SyncChannelMatch:
    """Whether the resolved candidate tree is the tree the review captured.

    A pure comparison of two identities the same owner produced
    (:func:`agents_remember.worktrees.modules.future_code_candidate.capture_future_code_candidate`), so
    the verdict is re-derivable from the record without reopening either tree.
    """

    if reviewed_code_tree == resolved_code_tree:
        return "matches-reviewed-input"
    return "differs-from-reviewed-input"


def knowledge_channel_match(
    reviewed_state: str,
    reviewed_digest: str | None,
    resolved: SyncKnowledgeObservation,
) -> SyncChannelMatch:
    """Whether the dataset at the declared location is the one the review compared, measured only.

    ``unmeasured`` is the answer whenever one side is not a dataset at all -- a generation that
    recorded a typed absence for its knowledge half, or a declared location holding nothing this code
    can read -- because reporting a difference between two things that were never both read is
    inventing an observation. It is never ``matches-reviewed-input``: nothing was compared.
    """

    if reviewed_state != "retained" or resolved.state != "published":
        return "unmeasured"
    if reviewed_digest is None or resolved.dataset is None:
        return "unmeasured"
    if reviewed_digest == resolved.dataset.logical_digest:
        return "matches-reviewed-input"
    return "differs-from-reviewed-input"


def review_sync_verdict(
    code_match: SyncChannelMatch,
    knowledge_match: SyncChannelMatch,
    reviewed_knowledge_state: str,
) -> ReviewSyncRebindingVerdict:
    """The one verdict rule, so a writer and the record's own validator cannot disagree about it.

    A measured difference on either channel is ``moved``. Otherwise a generation that **retained** a
    knowledge operand which was never compared is ``unmeasured`` -- the source side agreed and the
    knowledge side is unknown, so the generation covers part of the resolved pair. ``current`` is
    reserved for a measured match on every channel the generation actually retained, and a generation
    that retained no knowledge operand has nothing to leave unmeasured, so its source channel alone can
    make it ``current`` -- which is exactly what it recorded.
    """

    if "differs-from-reviewed-input" in (code_match, knowledge_match):
        return "moved"
    if reviewed_knowledge_state == "retained" and knowledge_match != "matches-reviewed-input":
        return "unmeasured"
    return "current"


class SyncKnowledgeObservation(KnowledgeModel):
    """What the declared publication location held when the rebinding measured it.

    ``dataset`` is the identity the ordinary read route answered with, and it is carried exactly when a
    dataset was read -- never beside ``not-recorded`` or ``unusable``, because an identity beside a
    location that was not read is an invented publication and a read location with no identity is a
    claim with nothing behind it. ``path`` and ``detail`` are the route's own answers, carried rather
    than restated so a reader is not told a different story than the reader that produced them.
    """

    state: SyncKnowledgeState
    dataset: SnapshotIdentity | None = None
    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _an_identity_is_carried_exactly_when_a_dataset_was_read(
        self,
    ) -> SyncKnowledgeObservation:
        if (self.state == "published") != (self.dataset is not None):
            raise ValueError(
                f"the declared location is reported as {self.state} while carrying "
                f"{self.dataset}: a dataset identity is recorded exactly when one was read, so "
                "neither an identity beside an unread location nor a published location with "
                "nothing behind it is a record any owner could have produced"
            )
        return self


class LegacyReviewSyncRebinding(KnowledgeModel):
    """One generation beside the source/knowledge pair a managed sync resolved, and the verdict.

    The reviewed identities are read from the generation's own sealed manifest and the resolved ones
    from the owners that produced them, so the two sides of every comparison are owner values rather
    than this record's own derivation. ``resolved_code_head`` is the work branch head the transaction
    left behind, carried beside the captured tree because a tree id alone does not say *which* commit
    the leaf now holds -- and the packet's boundary example needs both: the tree the review captured and
    the head the sync created are different facts about the same side.

    ``supersedes`` names the generation this record judges, in the vocabulary the generation store
    already publishes, and ``successor_action`` names the one call that produces a current comparison.
    Neither writes anything: a supersession becomes durable when the successor's own lineage names its
    predecessor, which is the freeze owner's act and not this record's.
    """

    rebinding_version: Literal["ar-review-sync-rebinding/v1"] = "ar-review-sync-rebinding/v1"
    recorded_at: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    repository_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    master: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    task_root: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    contract_path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    selection_rule: Literal["latest-published-generation"] = "latest-published-generation"
    # The generation this record judges: its own id, index, seal and the digest of the manifest bytes
    # that carried them. All four are carried rather than recomputed, because a reader compares this
    # record against the published generation and a digest derived here would be a second identity.
    supersedes_generation_id: str = Field(pattern=UUID_PATTERN)
    supersedes_generation_index: int = Field(ge=1)
    supersedes_binding_digest: str = Field(pattern=SHA256_PATTERN)
    supersedes_manifest_digest: str = Field(pattern=SHA256_PATTERN)
    # The reviewed source side: the recorded task baseline and the candidate tree the review captured.
    reviewed_baseline_code_tree_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    reviewed_candidate_code_tree_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    # The resolved source side: the leaf's captured candidate after the sync, and the work branch head
    # it was captured from.
    resolved_code_head: str = Field(pattern=GIT_OBJECT_PATTERN)
    resolved_candidate_code_tree_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    code_match: SyncChannelMatch
    # The reviewed knowledge side, in the generation owner's own vocabulary, beside the digest it
    # retained -- so "the review compared this dataset" and "the review selected no knowledge operand"
    # are distinguishable without reopening the generation.
    reviewed_knowledge_state: Literal["retained", "not-recorded", "not-selected"]
    reviewed_knowledge_logical_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    resolved_knowledge: SyncKnowledgeObservation
    knowledge_match: SyncChannelMatch
    state: ReviewSyncRebindingVerdict
    successor_action: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _the_rebinding_agrees_with_itself(self) -> LegacyReviewSyncRebinding:
        """Refuse a record whose verdicts do not follow from the identities it carries.

        Every rule compares two values the record already holds, so an inconsistent record is
        detectable without the repository: a rebinding claiming the reviewed comparison is current
        beside a differing tree, or naming a reviewed dataset digest for a generation that retained
        none, is not one any owner could have produced -- and reading it as one is how a false success
        is manufactured.
        """

        code = code_channel_match(
            self.reviewed_candidate_code_tree_id, self.resolved_candidate_code_tree_id
        )
        if self.code_match != code:
            raise ValueError(
                "the code channel's verdict does not follow from the trees carried: reviewed "
                f"candidate {self.reviewed_candidate_code_tree_id}, resolved candidate "
                f"{self.resolved_candidate_code_tree_id}, recorded {self.code_match}"
            )
        if (self.reviewed_knowledge_state == "retained") != (
            self.reviewed_knowledge_logical_digest is not None
        ):
            raise ValueError(
                "the reviewed dataset's digest is named exactly when the generation retained one: "
                f"state {self.reviewed_knowledge_state}, digest "
                f"{self.reviewed_knowledge_logical_digest}"
            )
        knowledge = knowledge_channel_match(
            self.reviewed_knowledge_state,
            self.reviewed_knowledge_logical_digest,
            self.resolved_knowledge,
        )
        if self.knowledge_match != knowledge:
            raise ValueError(
                "the knowledge channel's verdict does not follow from the datasets carried: "
                f"reviewed {self.reviewed_knowledge_state}/"
                f"{self.reviewed_knowledge_logical_digest}, resolved "
                f"{self.resolved_knowledge.state}, recorded {self.knowledge_match}"
            )
        if knowledge == "unmeasured" and self.resolved_knowledge.state == "published":
            raise ValueError(
                "the knowledge channel is recorded as unmeasured while the declared location is "
                f"reported as holding a readable dataset ({self.resolved_knowledge.dataset}): a "
                "dataset that was read was compared, so this record would be declining to report a "
                "measurement it holds"
            )
        verdict = review_sync_verdict(
            self.code_match, self.knowledge_match, self.reviewed_knowledge_state
        )
        if self.state != verdict:
            raise ValueError(
                "a rebinding's verdict follows from the channels it measured: a compared channel "
                "that differs is 'moved', a retained knowledge operand that was never compared is "
                "'unmeasured', and only a measured match on every retained channel is 'current'. "
                f"This one records code {self.code_match}, knowledge {self.knowledge_match}, "
                f"reviewed knowledge {self.reviewed_knowledge_state} and verdict {self.state}"
            )
        return self

    def covers_resolved_pair(self) -> bool:
        """The retired dataset record has no exact memory-tree proof."""
        return False

    def statement(self) -> str:
        return (
            f"Historical v1 rebinding {self.supersedes_generation_id} retained reviewed code tree "
            f"{self.reviewed_candidate_code_tree_id} and resolved code tree "
            f"{self.resolved_candidate_code_tree_id}, with historical verdict {self.state}; "
            "the exact reviewed memory tree was not recorded, so tree-pair coverage is unavailable."
        )


class ReviewSyncRebinding(KnowledgeModel):
    """A managed sync's exact code and memory capture beside the retained source comparison."""

    rebinding_version: Literal["ar-review-sync-rebinding/v2"] = REVIEW_SYNC_REBINDING_VERSION
    recorded_at: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    repository_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    master: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    task_root: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    contract_path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    selection_rule: Literal["latest-recorded-tree-comparison"] = REVIEW_SYNC_SELECTION_RULE
    comparison: ReviewTreeComparisonRecord
    comparison_digest: str = Field(pattern=SHA256_PATTERN)
    resolved_code_head: str = Field(pattern=GIT_OBJECT_PATTERN)
    resolved_candidate_code_tree_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    resolved_memory_head: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    resolved_candidate_memory_tree_id: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    memory_detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    code_match: SyncChannelMatch
    memory_match: SyncChannelMatch
    state: ReviewSyncRebindingVerdict
    successor_action: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _validate_tree_binding(self) -> ReviewSyncRebinding:
        if self.leaf_id != self.comparison.leaf_id:
            raise ValueError("the rebinding must name the source comparison's leaf")
        if self.comparison_digest != tree_comparison_digest(self.comparison):
            raise ValueError("the comparison digest must describe the complete source record")
        if (self.resolved_memory_head is None) != (self.resolved_candidate_memory_tree_id is None):
            raise ValueError("the resolved memory head and tree are present together")
        code = code_channel_match(
            self.comparison.code_candidate.tree, self.resolved_candidate_code_tree_id
        )
        memory = (
            "unmeasured"
            if self.resolved_candidate_memory_tree_id is None
            else code_channel_match(
                self.comparison.memory_candidate.tree, self.resolved_candidate_memory_tree_id
            )
        )
        if (self.code_match, self.memory_match) != (code, memory):
            raise ValueError("matches must follow from both exact candidate trees")
        if self.state != tree_sync_verdict(code, memory):
            raise ValueError("currency requires a measured match on both code and memory trees")
        return self

    def covers_resolved_pair(self) -> bool:
        return self.state == "current"

    def statement(self) -> str:
        return (
            f"Managed sync measured tree comparison {self.comparison.task_id}/"
            f"{self.comparison.leaf_id}/{self.comparison.number}: reviewed code tree "
            f"{self.comparison.code_candidate.tree}, resolved {self.resolved_candidate_code_tree_id} "
            f"({self.code_match}); reviewed memory tree {self.comparison.memory_candidate.tree}, "
            f"resolved {self.resolved_candidate_memory_tree_id} ({self.memory_match}); {self.state}. "
            + (
                self.successor_action
                if self.state != "current"
                else "Both resolved trees remain the reviewed candidates."
            )
        )


def tree_sync_verdict(
    code: SyncChannelMatch, memory: SyncChannelMatch
) -> ReviewSyncRebindingVerdict:
    if "differs-from-reviewed-input" in (code, memory):
        return "moved"
    if "unmeasured" in (code, memory):
        return "unmeasured"
    return "current"
