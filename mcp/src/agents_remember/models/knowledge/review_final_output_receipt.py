"""The typed record binding one comparison generation to the output a task actually delivered (ICR-R21@v1).

:class:`FinalOutputReceipt` is the value itself: a sealed set of identities -- the generation's own, the
delivered code commit and its tree, the delivered memory-content commit and its tree, and the published
knowledge identity read back at the repository's declared location -- beside the two match verdicts that
say whether the delivered output *is* the reviewed input. Its owner, the selection rule that produced
the generation, the durable publication and the read-back are all
:mod:`agents_remember.application.review_final_output_receipt`; this module is the vocabulary alone, so a
consumer can hold and validate a receipt without importing the operation that produced it.

Every field is a fact an owner produced and none is authored prose, which is what lets a reader compare
the record's own values instead of trusting a sentence about them. The one sentence the record
publishes, :meth:`FinalOutputReceipt.statement`, is derived from those values and cannot outrun them.
"""

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

__all__ = [
    "FINAL_OUTPUT_RECEIPT_VERSION",
    "FINAL_OUTPUT_SELECTION_RULE",
    "FinalOutputPhase",
    "FinalOutputReceipt",
    "FinalOutputVerdict",
    "MatchState",
    "PublishedKnowledgeState",
    "code_match_state",
    "final_output_verdict",
    "knowledge_match_state",
]

# The record's own version: a literal of this vocabulary's rather than a package version read at run
# time, so two receipts produced by different layouts are distinguishable from the receipts.
FINAL_OUTPUT_RECEIPT_VERSION: Literal["ar-review-final-output-receipt/v1"] = (
    "ar-review-final-output-receipt/v1"
)

# The one selection rule the record names. It is a field rather than a comment because "which
# generation is this?" must travel with the receipt instead of living in whichever reader is asking.
FINAL_OUTPUT_SELECTION_RULE: Literal["latest-published-generation"] = "latest-published-generation"

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


class FinalOutputReceipt(KnowledgeModel):
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

    receipt_version: Literal["ar-review-final-output-receipt/v1"] = FINAL_OUTPUT_RECEIPT_VERSION
    phase: FinalOutputPhase
    recorded_at: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    repository_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    master: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    task_root: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    contract_path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    selection_rule: Literal["latest-published-generation"] = FINAL_OUTPUT_SELECTION_RULE
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
    def _the_receipt_agrees_with_itself(self) -> FinalOutputReceipt:
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
        """Return the one sentence this receipt publishes, derived from its own fields.

        One clause per verdict, and each says only what the record measured. ``moved`` names the
        mismatch and the supersession remedy; ``unmeasured`` says the selected knowledge operand was
        never compared, which is why the receipt does not cover the delivery, and names the two ways to
        make it measurable; and ``bound`` -- reachable only when every selected channel was compared and
        matched, or when the generation selected no knowledge operand at all -- claims coverage.
        """

        if self.state == "bound":
            verdict = "every selected input this generation records is the delivered output"
        elif self.state == "unmeasured":
            verdict = (
                "the knowledge operand this generation selected was not compared against any delivered "
                "dataset, so this generation covers the delivered code and leaves the delivered "
                "knowledge unmeasured; publish the reviewed dataset at the declared location, or "
                "publish a successor generation that compares what was delivered"
            )
        else:
            verdict = (
                "this generation does not cover the delivered output; publish a successor generation "
                "naming it as its predecessor, or read the review as covering the inputs it recorded"
            )
        return (
            f"{self.phase} recorded comparison generation {self.generation_id} "
            f"(index {self.generation_index}) against the output it delivered: "
            f"{self._code_clause()}; {self._knowledge_clause()}; {verdict}."
        )

    def _code_clause(self) -> str:
        """The code half of the sentence, which names both trees it compared."""

        delivered = f"the code commit {self.delivered_code_commit} carries tree {self.delivered_code_tree_id}"
        if self.code_match == "matches-reviewed-input":
            return f"{delivered}, the reviewed candidate tree"
        return (
            f"{delivered}, not the reviewed candidate tree {self.reviewed_candidate_code_tree_id}"
        )

    def _knowledge_clause(self) -> str:
        """The knowledge half of the sentence, which never claims an unmeasured comparison."""

        path = self.published_knowledge_path
        reviewed = self.reviewed_knowledge_logical_digest
        if self.published_knowledge is not None:
            digest = self.published_knowledge.logical_digest
            if self.knowledge_match == "matches-reviewed-input":
                return f"the published dataset {digest} at {path} is the reviewed candidate dataset"
            if self.knowledge_match == "differs-from-reviewed-input":
                return f"the published dataset {digest} at {path} is not the reviewed candidate {reviewed}"
        if self.reviewed_knowledge_state != "retained":
            return (
                f"generation {self.generation_id} records {self.reviewed_knowledge_state} for its "
                "knowledge operand, so no published dataset was compared to a reviewed one"
            )
        return (
            f"the declared publication location {path} is {self.published_knowledge_state}, so the "
            f"published dataset was not compared to the reviewed candidate {reviewed}"
        )
