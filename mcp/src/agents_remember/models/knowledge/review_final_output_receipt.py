"""Tree-bound final-output receipt v2, plus the read-only v1 decoder for historical receipts."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from agents_remember.kernel.canonical_json import canonical_json_bytes
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
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord

__all__ = [
    "FINAL_OUTPUT_RECEIPT_VERSION",
    "FINAL_OUTPUT_SELECTION_RULE",
    "FinalOutputPhase",
    "FinalOutputReceipt",
    "FinalOutputVerdict",
    "LegacyFinalOutputReceipt",
    "MatchState",
    "PublishedKnowledgeState",
    "code_match_state",
    "final_output_verdict",
    "knowledge_match_state",
    "tree_comparison_digest",
    "tree_match_state",
    "tree_output_verdict",
]

# The record's own version: a literal of this vocabulary's rather than a package version read at run
# time, so two receipts produced by different layouts are distinguishable from the receipts.
FINAL_OUTPUT_RECEIPT_VERSION: Literal["ar-review-final-output-receipt/v2"] = (
    "ar-review-final-output-receipt/v2"
)

# The one selection rule the record names. It is a field rather than a comment because "which
# generation is this?" must travel with the receipt instead of living in whichever reader is asking.
FINAL_OUTPUT_SELECTION_RULE: Literal["latest-recorded-tree-comparison"] = (
    "latest-recorded-tree-comparison"
)

# The two phases that record a receipt. Closeout creates the commits; integration lands them on the
# source branches. Separate records rather than one updated record, because each is a measurement taken
# at its own moment -- the published dataset can move between the two -- and a record rewritten to
# describe a later moment stops being evidence of the earlier one.
FinalOutputPhase = Literal["closeout", "integration"]

# Whether one delivered identity is the reviewed input it descends from. ``not-comparable`` is not a
# softer ``differs``: it is the state of a channel with nothing on one side to compare -- a generation
# that selected no knowledge operand, or a location holding no readable dataset -- and it never makes
# the receipt ``moved``.
MatchState = Literal["matches-reviewed-input", "differs-from-reviewed-input", "not-comparable"]

# What a reader found at the repository's declared publication location, in the vocabulary of the route
# that resolves it: a published dataset, a location holding nothing at all, and a location holding
# something that is not a dataset this code can read.
PublishedKnowledgeState = Literal["published", "not-recorded", "unusable"]

# The record's one verdict, and the reason there are three rather than two. ``moved`` is a mismatch on a
# channel that was compared. ``unmeasured`` is *not* a mismatch: the generation selected a knowledge
# operand and no delivered dataset was ever compared against it (the declared location holds no
# publication, or holds something this code cannot read as one), so the delivery's knowledge channel is
# simply unknown. ``bound`` is the only value that claims coverage, and it requires a *measured* match on
# every channel the generation actually selected -- a receipt whose knowledge channel was never compared
# must never read as coverage, because that is the claim a consumer keys on.
FinalOutputVerdict = Literal["bound", "unmeasured", "moved"]


def code_match_state(reviewed_candidate_tree: str, delivered_code_tree: str) -> MatchState:
    """Whether the delivered code tree is the candidate tree the generation reviewed."""

    if delivered_code_tree == reviewed_candidate_tree:
        return "matches-reviewed-input"
    return "differs-from-reviewed-input"


def knowledge_match_state(
    reviewed_state: str,
    reviewed_digest: str | None,
    published_state: PublishedKnowledgeState,
    published_digest: str | None,
) -> MatchState:
    """Whether the published dataset is the reviewed one, or why the two cannot be compared.

    ``not-comparable`` is the answer whenever one side is not a dataset at all -- a generation whose
    knowledge operand carries a typed absence, or a declared location holding no readable dataset --
    because the alternative reports a difference between two things that were never both read. It is
    never ``matches``: nothing was compared, and claiming otherwise claims coverage nobody measured.
    """

    if reviewed_state != "retained" or published_state != "published":
        return "not-comparable"
    if reviewed_digest is None or published_digest is None:
        return "not-comparable"
    if reviewed_digest == published_digest:
        return "matches-reviewed-input"
    return "differs-from-reviewed-input"


def final_output_verdict(
    code_match: MatchState,
    knowledge_match: MatchState,
    reviewed_knowledge_state: str,
) -> FinalOutputVerdict:
    """The one verdict rule, so a writer and the record's own validator cannot disagree about it.

    A mismatch on either channel is ``moved``. Otherwise, a generation that **selected** a knowledge
    operand -- one whose after side is ``retained`` -- with no compared delivery is ``unmeasured``: the
    code channel matched, and the knowledge channel was never read, so the receipt covers part of the
    delivery rather than all of it. Only a delivery whose every selected channel matched is ``bound``;
    a generation that selected no knowledge operand has nothing to leave unmeasured, so it can be
    ``bound`` on the code channel alone, which is exactly what it selected.
    """

    if "differs-from-reviewed-input" in (code_match, knowledge_match):
        return "moved"
    if reviewed_knowledge_state == "retained" and knowledge_match == "not-comparable":
        return "unmeasured"
    return "bound"


class LegacyFinalOutputReceipt(KnowledgeModel):
    """One generation bound to the outputs a normal closeout or integration actually delivered.

    Every field is a fact an owner produced: the generation's identities come from its sealed manifest,
    the delivered commits and trees are read out of the repositories that hold them, and the published
    knowledge identity is what the ordinary read route resolves at the declared location. The record
    carries no authored prose, so a reader compares its own fields rather than trusting a sentence about
    them; the one sentence it publishes, :meth:`statement`, is derived from those fields.
    ``delivered_memory_*`` are absent together exactly when a phase recorded no memory output, which
    ``memory_output_state`` states rather than leaving to be inferred from a missing value.

    ``state`` is the record's one verdict and it has three values, because two would collapse two very
    different facts: a delivery whose selected knowledge channel was *never compared* is not a delivery
    that matched, and ``bound`` -- the value a consumer keys on as coverage -- is reserved for a
    measured match on every channel the generation actually selected
    (:func:`final_output_verdict`, applied by the validator below rather than trusted from a writer).
    """

    receipt_version: Literal["ar-review-final-output-receipt/v1"] = (
        "ar-review-final-output-receipt/v1"
    )
    phase: FinalOutputPhase
    recorded_at: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    repository_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    master: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    task_root: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    contract_path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    selection_rule: Literal["latest-published-generation"] = "latest-published-generation"
    generation_id: str = Field(pattern=UUID_PATTERN)
    generation_index: int = Field(ge=1)
    # The generation's own seal and the digest of the manifest bytes that carried it. Both carried
    # rather than recomputed: a reader compares the receipt against the published record, and a digest
    # this vocabulary derived from a field set of its own would be a second identity to trust.
    binding_digest: str = Field(pattern=SHA256_PATTERN)
    manifest_digest: str = Field(pattern=SHA256_PATTERN)
    reviewed_baseline_code_tree_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    reviewed_candidate_code_tree_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    delivered_code_commit: str = Field(pattern=GIT_OBJECT_PATTERN)
    delivered_code_tree_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    code_match: MatchState
    memory_output_state: Literal["recorded", "not-recorded"]
    delivered_memory_content_commit: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    delivered_memory_tree_id: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    # Whether the generation retained a dataset on its after side at all, in the generation owner's own
    # vocabulary, beside the digest it retained -- so "the review compared this dataset" and "the review
    # selected no knowledge operand" are distinguishable without reopening the generation.
    reviewed_knowledge_state: Literal["retained", "not-recorded", "not-selected"]
    reviewed_knowledge_logical_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    published_knowledge_state: PublishedKnowledgeState
    published_knowledge: SnapshotIdentity | None = None
    published_knowledge_path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    published_knowledge_detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    knowledge_match: MatchState
    state: FinalOutputVerdict

    @model_validator(mode="after")
    def _the_receipt_agrees_with_itself(self) -> LegacyFinalOutputReceipt:
        """Refuse a record whose verdicts do not follow from the identities it carries.

        Every rule compares two values the record already holds, so an inconsistent record is
        detectable without the store: a receipt claiming coverage beside a mismatching tree, or a
        memory identity with no memory tree (or the reverse), is not one any owner could have produced
        -- and reading it as one is how a false success is manufactured.
        """

        code = code_match_state(self.reviewed_candidate_code_tree_id, self.delivered_code_tree_id)
        if self.code_match != code:
            raise ValueError(
                "the code match does not follow from the trees carried: reviewed candidate "
                f"{self.reviewed_candidate_code_tree_id}, delivered {self.delivered_code_tree_id}, "
                f"recorded {self.code_match}"
            )
        named_memory = (
            self.delivered_memory_content_commit is not None
            and self.delivered_memory_tree_id is not None
        )
        if (self.memory_output_state == "recorded") != named_memory:
            raise ValueError(
                "memory output is recorded exactly when both the memory commit and its tree are "
                f"named, and this receipt says {self.memory_output_state} with commit "
                f"{self.delivered_memory_content_commit} and tree {self.delivered_memory_tree_id}"
            )
        if (self.reviewed_knowledge_state == "retained") != (
            self.reviewed_knowledge_logical_digest is not None
        ):
            raise ValueError(
                "the reviewed dataset's digest is named exactly when the generation retained one: "
                f"state {self.reviewed_knowledge_state}, digest "
                f"{self.reviewed_knowledge_logical_digest}"
            )
        if (self.published_knowledge_state == "published") != (
            self.published_knowledge is not None
        ):
            raise ValueError(
                f"the published location is reported as {self.published_knowledge_state} while "
                f"carrying {self.published_knowledge}: an identity is carried exactly when a dataset "
                "was read, so an identity beside an unreadable location is an invented publication "
                "and a published location with no identity is a claim with nothing behind it"
            )
        knowledge = knowledge_match_state(
            self.reviewed_knowledge_state,
            self.reviewed_knowledge_logical_digest,
            self.published_knowledge_state,
            None if self.published_knowledge is None else self.published_knowledge.logical_digest,
        )
        if self.knowledge_match != knowledge:
            raise ValueError(
                "the knowledge match does not follow from the dataset named: reviewed "
                f"{self.reviewed_knowledge_state}/{self.reviewed_knowledge_logical_digest}, "
                f"published {self.published_knowledge_state}, recorded {self.knowledge_match}"
            )
        moved = "differs-from-reviewed-input" in (self.code_match, self.knowledge_match)
        verdict = final_output_verdict(
            self.code_match, self.knowledge_match, self.reviewed_knowledge_state
        )
        if self.state != verdict:
            raise ValueError(
                "a receipt's verdict follows from the channels it measured: a compared channel that "
                "differs is 'moved', a selected knowledge channel that was never compared is "
                "'unmeasured', and only a measured match on every selected channel is 'bound'. This "
                f"one records code {self.code_match}, knowledge {self.knowledge_match}, reviewed "
                f"knowledge {self.reviewed_knowledge_state} and verdict {self.state}"
                + (" ('moved' claimed)" if moved else "")
            )
        return self

    def statement(self) -> str:
        return (
            f"Historical v1 {self.phase} receipt {self.generation_id} retained reviewed code tree "
            f"{self.reviewed_candidate_code_tree_id} and delivered code tree "
            f"{self.delivered_code_tree_id}, with historical verdict {self.state}; "
            "the exact reviewed memory tree was not recorded, so tree-pair coverage is unavailable."
        )


class FinalOutputReceipt(KnowledgeModel):
    """Exact delivered trees beside the source record the review actually opened (ICR-R21)."""

    receipt_version: Literal["ar-review-final-output-receipt/v2"] = FINAL_OUTPUT_RECEIPT_VERSION
    phase: FinalOutputPhase
    recorded_at: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    repository_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    master: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    task_root: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    contract_path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    selection_rule: Literal["latest-recorded-tree-comparison"] = FINAL_OUTPUT_SELECTION_RULE
    comparison: ReviewTreeComparisonRecord
    comparison_digest: str = Field(pattern=SHA256_PATTERN)
    delivered_code_commit: str = Field(pattern=GIT_OBJECT_PATTERN)
    delivered_code_tree_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    delivered_memory_content_commit: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    delivered_memory_tree_id: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    code_match: MatchState
    memory_match: MatchState
    state: FinalOutputVerdict

    @model_validator(mode="after")
    def _validate_tree_binding(self) -> FinalOutputReceipt:
        if self.leaf_id != self.comparison.leaf_id:
            raise ValueError("the receipt must name the source comparison's leaf")
        if self.comparison_digest != tree_comparison_digest(self.comparison):
            raise ValueError("the comparison digest must describe the complete source record")
        if (self.delivered_memory_content_commit is None) != (
            self.delivered_memory_tree_id is None
        ):
            raise ValueError("the delivered memory commit and tree are present together")
        code = tree_match_state(self.comparison.code_candidate.tree, self.delivered_code_tree_id)
        memory = tree_match_state(
            self.comparison.memory_candidate.tree, self.delivered_memory_tree_id
        )
        if (self.code_match, self.memory_match) != (code, memory):
            raise ValueError("matches must follow from both exact candidate trees")
        if self.state != tree_output_verdict(code, memory):
            raise ValueError("coverage requires a measured match on both code and memory trees")
        return self

    def statement(self) -> str:
        return (
            f"{self.phase} measured tree comparison {self.comparison.task_id}/"
            f"{self.comparison.leaf_id}/{self.comparison.number}: reviewed code tree "
            f"{self.comparison.code_candidate.tree}, delivered {self.delivered_code_tree_id} "
            f"({self.code_match}); reviewed memory tree {self.comparison.memory_candidate.tree}, "
            f"delivered {self.delivered_memory_tree_id} ({self.memory_match}); {self.state}. "
            + (
                "Record a successor tree comparison for the delivered pair."
                if self.state != "bound"
                else "Both delivered trees are exactly the reviewed candidates."
            )
        )


def tree_comparison_digest(comparison: ReviewTreeComparisonRecord) -> str:
    """Digest the complete retained source record, including repositories, commits and refs."""

    return hashlib.sha256(
        canonical_json_bytes(comparison.model_dump(mode="json", by_alias=True))
    ).hexdigest()


def tree_match_state(reviewed: str, observed: str | None) -> MatchState:
    if observed is None:
        return "not-comparable"
    return code_match_state(reviewed, observed)


def tree_output_verdict(code: MatchState, memory: MatchState) -> FinalOutputVerdict:
    if "differs-from-reviewed-input" in (code, memory):
        return "moved"
    if "not-comparable" in (code, memory):
        return "unmeasured"
    return "bound"
