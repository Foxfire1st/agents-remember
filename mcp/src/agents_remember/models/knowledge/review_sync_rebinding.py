"""The typed record binding one comparison generation to the pair a managed sync resolved (ICR-R22@v1).

A comparison generation (:mod:`agents_remember.application.review_comparison_generation`) records what
a review *read*: the candidate source tree captured from the leaf's own worktree, and the knowledge
dataset the review compared. A managed sync
(:mod:`agents_remember.worktrees.sync_transaction`) then moves both -- the work branch is carried onto
the official line, so the leaf's ``HEAD`` is the merge commit rather than the head the review captured,
and the memory worktree's published dataset is the union the merge produced rather than the dataset the
review compared. A reader holding only "a generation was frozen" cannot tell whether that generation
still describes the pair the leaf now holds, which is what the packet's conforming example requires and
its non-conforming example denies.

:class:`ReviewSyncRebinding` is the value itself: the generation the review published, the code
identity that generation reviewed beside the code identity the leaf holds after the sync, the knowledge
dataset the review compared beside the dataset standing at the repository's declared publication
location after the sync, and the match each side earned. Every field is an identity an owner produced
-- the generation's own sealed manifest, :mod:`agents_remember.worktrees.modules.future_code_candidate`,
and :mod:`agents_remember.application.published_intent` -- so a reader compares the record's own values
instead of trusting a sentence about them. The one sentence the record publishes,
:meth:`ReviewSyncRebinding.statement`, is derived from those values and cannot outrun them.

**The verdict can only claim coverage it measured.** ``current`` is the single value that says the
reviewed generation still describes the resolved pair, and it requires a *measured* match on every
channel the generation actually retained: a record whose knowledge channel resolved to no dataset must
never read as coverage, because "nothing was there to compare" and "the comparison agreed" are
different facts. ``moved`` either channel earns when it was compared and differed. ``unmeasured`` is
the honest answer for a retained knowledge operand that could not be compared at all -- the declared
publication location held nothing this code can read -- and it is not a softer ``moved``: it is never
coverage, and the validator refuses it beside a location that did hold a readable dataset.

**A source side that could not be captured produces no record at all.** The resolved side is measured
by re-deriving the leaf's own capture, and a worktree the merge left unable to produce one (a dirty
candidate, an unreadable repository) is a real outcome of a sync. No candidate tree is invented for it:
the operation reports that state instead of publishing a record whose source identity nobody observed,
because an identity this vocabulary did not measure is exactly the invented input it refuses to carry.

**This record supersedes; it does not replace.** It changes no dataset, publishes no successor
generation and grants no clearance: the successor is
:func:`agents_remember.application.review_comparison_freeze.freeze_review_comparison` with the recorded
generation as its ``parent``, and the action this record names is that call. A record that overwrote
the generation it judged, or that re-ran the comparison itself, would be the parallel review authority
the packet forbids.
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
    "REVIEW_SYNC_REBINDING_VERSION",
    "REVIEW_SYNC_SELECTION_RULE",
    "ReviewSyncRebinding",
    "ReviewSyncRebindingVerdict",
    "SyncChannelMatch",
    "SyncKnowledgeObservation",
    "code_channel_match",
    "knowledge_channel_match",
    "review_sync_verdict",
]

# The record's own version, a literal of this vocabulary's rather than a package version read at run
# time, so two rebindings produced by different layouts are distinguishable from the records.
REVIEW_SYNC_REBINDING_VERSION: Literal["ar-review-sync-rebinding/v1"] = (
    "ar-review-sync-rebinding/v1"
)

# Which generation a rebinding judges. The same rule the final-output receipt names, spelled here so a
# reader of this record does not have to import the owner that produced it to learn what it selected.
REVIEW_SYNC_SELECTION_RULE: Literal["latest-published-generation"] = "latest-published-generation"

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


class ReviewSyncRebinding(KnowledgeModel):
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

    rebinding_version: Literal["ar-review-sync-rebinding/v1"] = REVIEW_SYNC_REBINDING_VERSION
    recorded_at: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    repository_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    master: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    task_root: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    contract_path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    selection_rule: Literal["latest-published-generation"] = REVIEW_SYNC_SELECTION_RULE
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
    def _the_rebinding_agrees_with_itself(self) -> ReviewSyncRebinding:
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
        """Whether this record established that the review still describes the resolved pair.

        The predicate exists so a consumer branches on one method rather than re-deriving the rule:
        ``current`` is the only verdict that covers the pair, and it is reachable only through the
        validator above.
        """

        return self.state == "current"

    def statement(self) -> str:
        """Return the one sentence this record publishes, derived from its own fields.

        One clause per verdict, and each says only what the record measured. ``moved`` names the
        channel that moved and the supersession remedy; ``unmeasured`` says the retained knowledge
        operand was never compared and names why the location answered nothing; and ``current`` -- the
        only value that claims coverage -- says the reviewed comparison still describes both sides of
        the resolved pair.
        """

        if self.state == "moved":
            verdict = (
                "the reviewed comparison no longer describes it; publish a successor generation "
                "naming that one as its predecessor, or read the review as covering the inputs it "
                "recorded"
            )
        elif self.state == "unmeasured":
            verdict = (
                "the knowledge operand this generation retained was not compared against any dataset "
                "at the declared publication location, so this rebinding covers the resolved source "
                "and leaves the resolved knowledge unmeasured"
            )
        else:
            verdict = "the reviewed comparison still describes both sides of it"
        return (
            f"the managed sync resolved this leaf's pair and comparison generation "
            f"{self.supersedes_generation_id} (index {self.supersedes_generation_index}) was measured "
            f"against it: {self._code_clause()}; {self._knowledge_clause()}; {verdict}."
        )

    def _code_clause(self) -> str:
        """The source half of the sentence, which names every tree it compared.

        **The head locates the capture; it does not carry it.** The resolved candidate is the shipped
        capture owner's add-all tree, computed in an isolated index from the worktree's whole content,
        so it equals the head's own tree only while that worktree is clean. The state where it does
        not is exactly the state the packet requires a sync to preserve -- the curator's uncommitted
        work, parked and handed back -- and a sentence claiming the head *carries* that tree is denied
        by Git there. Naming the head as where the capture was taken is true in both states.
        """

        capture = self.resolved_candidate_code_tree_id
        located = (
            f"candidate tree {capture} captured from the leaf's worktree at work branch head "
            f"{self.resolved_code_head}"
        )
        if self.code_match == "matches-reviewed-input":
            return f"{located}, the candidate tree the review captured"
        return (
            f"{located}, not the review's captured candidate tree "
            f"{self.reviewed_candidate_code_tree_id}"
        )

    def _knowledge_clause(self) -> str:
        """The knowledge half of the sentence, which never claims an unmeasured comparison."""

        observed = self.resolved_knowledge
        digest = None if observed.dataset is None else observed.dataset.logical_digest
        if self.knowledge_match == "matches-reviewed-input":
            return (
                f"the dataset {digest} at {observed.path} is the candidate dataset the review "
                "compared"
            )
        if self.knowledge_match == "differs-from-reviewed-input":
            return (
                f"the dataset {digest} at {observed.path} is not the candidate dataset the review "
                f"compared ({self.reviewed_knowledge_logical_digest})"
            )
        if self.reviewed_knowledge_state != "retained":
            return (
                f"generation {self.supersedes_generation_id} records "
                f"{self.reviewed_knowledge_state} for its knowledge operand, so no dataset was "
                "compared to a reviewed one"
            )
        return (
            f"the declared publication location {observed.path} is {observed.state}, so the dataset "
            f"it holds was not compared to the reviewed candidate "
            f"{self.reviewed_knowledge_logical_digest}"
        )
