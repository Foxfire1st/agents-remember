"""The Intent Reviewer surface's own vocabulary: what the three panes carry, and nothing more.

This module defines **no record kind**. Every value here is a rendering of records another owner
already stores, or a stated absence where no record exists. The one thing it does own is the shape
of a display, and that shape is where the display's prohibitions are enforced:

* **There is no field a conclusion could be assembled in.** No summary, no narrative, no severity,
  no score, no conflict verdict, no causal explanation and no approval exists anywhere in this
  vocabulary, so the surface cannot grow one by filling a blank that happens to be there.
* **Unassessed is the absence of a value, never a value.** An assessment is ``None`` or it is a
  record with an author and examined inputs; there is no "compatible", no "no concern recorded" and
  no default disposition that could stand in for a judgement nobody made.
* **A missing side is a state, not an empty string.** :class:`ReviewSideContent` refuses to carry
  text unless it is ``present``, so a missing operand renders as its own named state rather than as
  a blank that reads like an empty file.
* **The stale rule is structural.** A payload whose comparison is stale must carry the submission
  state that disables submission against it, and one that is current must not: the prohibition is a
  constructor check, not a note a renderer is asked to remember.
* **A signal is not a finding, and the vocabulary keeps them apart.** A mechanical detection fact
  carries its matched condition, its inputs, its versions and its scope limitations and has no
  field for a voice, a severity or a disposition.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.diff import SourceAttribution
from agents_remember.models.knowledge.read import KnowledgeReadSeed
from agents_remember.models.knowledge.review_records import (
    ReviewRecordChannel,
    ReviewRecordChannelState,
    ReviewRecordClassName,
)
from agents_remember.models.knowledge.review_relationships import (
    ReviewAuthoredLineage,
    ReviewRelationshipGap,
    ReviewRelationshipMovement,
    ReviewRelationshipSide,
    ReviewRelationshipTransition,
    ReviewRenameInference,
)
from agents_remember.models.knowledge.revision_selection import ReviewRevisionSelection
from agents_remember.models.knowledge.view import MAX_VIEW_ROWS

__all__ = [
    "KNOWLEDGE_REVIEW_SURFACE_VERSION",
    "MAXIMUM_REVIEW_PAGE_SIZE",
    "PROPOSED_ASSESSMENT_DISPOSITIONS",
    "REVIEW_PAGED_COLLECTIONS",
    "REVIEW_PAGE_RESET_NEXT_ACTION",
    "REVIEW_PANE_NAMES",
    "ComparisonIdentity",
    "KnowledgeReviewPayload",
    "KnowledgeReviewResult",
    "ReviewAssessmentDisplay",
    "ReviewAuthoredEffect",
    "ReviewAuthoredLineage",
    "ReviewCandidateRef",
    "ReviewChangedFile",
    "ReviewCollectionPage",
    "ReviewEntry",
    "ReviewEntryListResult",
    "ReviewEvidenceLink",
    "ReviewEvidencePane",
    "ReviewFieldChange",
    "ReviewKnowledgePane",
    "ReviewObservation",
    "ReviewRecordChannel",
    "ReviewRecordChannelState",
    "ReviewRecordClassName",
    "ReviewRefusal",
    "ReviewRefusalCode",
    "ReviewRelationshipGap",
    "ReviewRelationshipMovement",
    "ReviewRelationshipSide",
    "ReviewRelationshipTransition",
    "ReviewRemainingCount",
    "ReviewRemainingCountName",
    "ReviewRenameInference",
    "ReviewRevisionGroup",
    "ReviewSideContent",
    "ReviewSignal",
    "ReviewSourceInventory",
    "ReviewSourceLocation",
    "ReviewSourcePane",
    "ReviewStaleness",
    "ReviewSubjectKind",
    "ReviewSubjectPresence",
    "ReviewSubmission",
    "ReviewSurfaceRequest",
    "ReviewUnrepresentablePath",
    "ReviewUnresolvedReference",
]

# The surface's own version. It is a recorded value rather than a package version read at display
# time, so two payloads produced by different renderings are distinguishable from the payloads.
#
# ``/1`` admits BOTH payload shapes and is deliberately not bumped by ICR-R02: the subject payload,
# where ``comparison`` is present and names the knowledge comparison that was made, and the
# task-context payload, where ``comparison`` is **absent** and the staleness state is
# ``not_compared`` because no comparison was made at all. The addition is additive for every client
# that can already request the subject payload (it ignores what it does not read), and a bump would
# oblige a client migration that nothing validates -- there is no version validator anywhere in this
# package, so ``/2`` would be a claim with no enforcement behind it.
KNOWLEDGE_REVIEW_SURFACE_VERSION = "knowledge-review-surface/1"

REVIEW_PANE_NAMES: tuple[str, ...] = ("knowledge", "source", "evidence")

# ``RRD:366``'s three proposed dispositions, verbatim. They are published on the payload so a
# reviewer can see which judgements are expressible; none of them is publication approval, and the
# surface renders them as a statement about the authority rather than as a control of its own.
PROPOSED_ASSESSMENT_DISPOSITIONS: tuple[str, ...] = (
    "concern_found",
    "no_concern_found",
    "unresolved",
)

ReviewRefusalCode = Literal[
    "candidate_unresolved",
    "candidate_not_live",
    "candidate_dataset_absent",
    "subject_unresolved",
    "comparison_refused",
    "comparison_page_reset",
    "comparison_page_unreadable",
    "source_content_unresolved",
    "review_adapter_unavailable",
]

# The two bounded collections one review composes, declared once so the request, the payload and the
# transport cannot come to disagree about which one a cursor addresses (ICR-R10). They are separate
# because their cursors are separate shipped documents -- the comparison's own cursor positions a
# page in a union of two snapshots, and the view's continuation positions one in a single selection
# of one of them -- and presenting either to the other operation is a caller's mistake that the
# owners refuse rather than a slice this surface may reinterpret.
ReviewPagedCollection = Literal["knowledge", "records"]
REVIEW_PAGED_COLLECTIONS: tuple[ReviewPagedCollection, ...] = ("knowledge", "records")

# The one sentence a page cursor that no longer binds its comparison earns. It is the *new
# generation* action ICR-R10 requires of a moved snapshot: the cursor is not re-resolved, not
# silently answered from the new generation, and the reader is told exactly what to do instead.
#
# It belongs to ``comparison_page_reset`` alone. A cursor this surface did not mint for the
# collection the request names is a *different* fact -- nothing moved, the caller used the other
# walk's token -- and it is answered with ``comparison_page_unreadable`` and the owner's own next
# action, because telling that caller to open a new comparison would assert a generation change that
# did not happen.
REVIEW_PAGE_RESET_NEXT_ACTION = (
    "open a new comparison: a cursor is a position in one comparison of two named snapshots, so the "
    "moved one cannot be continued; the surface serves the first page of the comparison that is "
    "there now and this response names the comparison the cursor was minted at"
)

# The largest page a caller may ask this surface for, and it is the record owner's own declared
# bound rather than a second page limit invented here: ``ViewRequest.limit`` caps at
# ``MAX_VIEW_ROWS``, so a larger request would be narrowed by the owner while the response claimed
# the size the caller named. The knowledge comparison keeps its own owner's budget, which is why
# this is a ceiling for the request rather than the size of every page.
MAXIMUM_REVIEW_PAGE_SIZE = MAX_VIEW_ROWS

# The two subject kinds the surface reviews, declared once here so the transport, the entry list and
# the panes cannot come to disagree about which identities are reviewable. The name is the server's;
# the browser's ``ReviewSelectorKind`` is the same two spellings on the wire.
ReviewSubjectKind = Literal["invariant", "family"]

# Which of the comparison's two snapshots record one catalogue subject, stated per row. The names
# are the packet's own (ICR-R09): the knowledge history is append-only, so a subject the before
# snapshot records and the candidate does not is ``before_only`` -- retired, but neither gone nor
# unreviewable -- and one only the candidate records is ``after_only``. This is the catalogue's
# selection state: which snapshot selections reach the row.
ReviewSubjectPresence = Literal["before_only", "after_only", "both"]

ReviewSideState = Literal["present", "absent", "binary", "unresolved"]


class ReviewUnresolvedReference(KnowledgeModel):
    """One reference the surface could not resolve, named rather than dropped or made anonymous.

    It exists so that "cannot be resolved" is a *displayed* fact: a row whose author or examined
    inputs are missing carries one of these instead of an empty attribution, and no rendering is
    able to present the row as one whose basis is simply uninteresting.
    """

    field: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    recorded_reference: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class ReviewSideContent(KnowledgeModel):
    """One side's exact payload text, or the explicit state that says why there is none.

    ``text`` is carried only when the side is ``present``. A binary operand, an absent one and an
    unresolved one each name themselves, so none of them degenerates into an empty string that a
    reader would take for an empty document -- the rule the diff renderer is fed rather than asked
    to infer.
    """

    state: ReviewSideState
    text: str | None = None
    language: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_text_exactly_when_present(self) -> ReviewSideContent:
        if self.state == "present" and self.text is None:
            raise ValueError(
                "a present side carries its text; a present side with no text is absent"
            )
        if self.state != "present" and self.text is not None:
            raise ValueError(
                "a side that is not present carries no text: rendering an absent, binary or "
                "unresolved operand as text is how a missing side is read as an empty one"
            )
        return self


class ReviewSurfaceRequest(KnowledgeModel):
    """One review read: which task context, and which recorded subject is being compared.

    The transport parses this value from its query string and the composition consumes the same
    value, so there is one spelling of "what was asked" rather than a wire shape and a domain shape
    that have to be kept in agreement. ``selector`` is one of the read operation's own declared
    seeds and nothing else: no display version, no insertion instant and no "latest" flag is
    representable, so none of them can select.

    ``selector`` is **optional**, and its absence is the task context rather than an empty subject:
    a task that records no invariant and a task whose datasets do not exist yet both still have a
    declared source comparison, and this request is how a caller asks for it. A request with no
    selector compares no knowledge operand at all -- it does not select "everything", and the
    payload states which of the two it did.

    ``page_of`` names which of the two bounded collections this call continues, and ``continuation``
    is the cursor that collection's own owner minted for it (ICR-R10). They travel as a pair: a
    cursor presented without naming its collection would be a token this surface had to guess the
    owner of, and naming a collection without a cursor is the first page of it. The cursor is an
    *input* rather than a position the server recomputes, so a page is a function of the cursor and
    the same comparison -- never of whatever the dataset holds when the request lands.
    """

    repository_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    master: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    selector: KnowledgeReadSeed | None = None
    # One past the largest page either owner can return, so a caller cannot ask this surface for a
    # page bigger than the owner's own declared bound: ``ViewRequest.limit`` caps at
    # ``MAX_VIEW_ROWS``, and a budget above it would be silently narrowed by the owner -- a page the
    # caller asked for and the response did not bound.
    page_of: ReviewPagedCollection | None = None
    continuation: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    page_size: int = Field(default=0, ge=0, le=MAXIMUM_REVIEW_PAGE_SIZE)

    @model_validator(mode="after")
    def _require_the_cursor_and_its_collection_together(self) -> ReviewSurfaceRequest:
        """Refuse a continuation that names no collection, rather than guessing its owner."""

        if self.continuation is not None and self.page_of is None:
            raise ValueError(
                "a continuation names the bounded collection it continues; presenting a cursor "
                "without one would make this surface choose which owner's walk it belongs to"
            )
        return self


class ReviewCandidateRef(KnowledgeModel):
    """The candidate the surface reviews, resolved from canonical task context.

    Every field here is a task identity a caller may legitimately name. No filesystem path appears
    in it, which is the point: the browser chooses the task context and the resolution layer chooses
    the candidate, so a caller cannot address a database the resolution did not select.
    """

    repository_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    master: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    task_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)


class ReviewEntry(KnowledgeModel):
    """One subject of the comparison's before/after population, as the catalogue lists it.

    This is the value the task-view entry carries, and it exists so that entry never has to invent
    one. ``selector_kind`` and ``selector_id`` are the recorded identity the review is opened with;
    ``label`` is that identity's own recorded display label, read from the after snapshot when it
    records the identity and from the before snapshot otherwise. ``presence`` states which of the
    two snapshots record the identity, so a retired (before-only) subject and a newly added
    (after-only) one are listed beside the subjects both snapshots hold rather than dropped to
    imply a smaller complete population. There is no field here for a path, a file, a display
    version, a ranking, or a comparison count, so an entry cannot point at a dataset the
    resolution did not select, cannot be ordered by a preference this list invented, and cannot
    oblige the catalogue read to compare every subject before answering.
    """

    selector_kind: ReviewSubjectKind
    selector_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    presence: ReviewSubjectPresence


class ComparisonIdentity(KnowledgeModel):
    """The comparison the panes were rendered from, as the shared operation declared it.

    The surface renders this identity and never re-derives one. It carries the operation's own
    binding digest, its policy and version, both declared snapshot digests and both code tree ids --
    so "the same comparison" is a value a reviewer can compare between two responses rather than a
    claim the surface makes about itself.

    ``knowledge_compared`` is what keeps a **task-context** review from wearing a comparison it never
    made: a review opened with no recorded subject compares no knowledge operand, so it carries no
    selector digest and no snapshot digests, and the three absent fields are exactly the statement
    that nothing was selected. The two snapshot digests travel together or not at all -- one of them
    alone would describe a comparison between a measured dataset and nothing.
    """

    reference: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    policy_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    binding_digest: str = Field(pattern=SHA256_PATTERN)
    selector_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    before_snapshot_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    after_snapshot_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    before_code_tree_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    after_code_tree_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    knowledge_compared: bool = True

    @model_validator(mode="after")
    def _require_the_knowledge_half_to_be_all_or_nothing(self) -> ComparisonIdentity:
        digests = (self.selector_digest, self.before_snapshot_digest, self.after_snapshot_digest)
        if self.knowledge_compared and any(digest is None for digest in digests):
            raise ValueError(
                "a comparison that compared knowledge names its selector and both snapshot "
                "digests; a measured comparison missing one of them cannot be reproduced"
            )
        if not self.knowledge_compared and any(digest is not None for digest in digests):
            raise ValueError(
                "a comparison that compared no knowledge carries no selector or snapshot digest; "
                "one beside the statement is how an invented selection becomes readable"
            )
        return self


class ReviewRefusal(KnowledgeModel):
    """One typed review refusal, naming the offending input and the concrete next action.

    A refusal is a state and never a degraded success: a caller that receives one has no panes, and
    must not be able to read the absence of panes as a review of an empty candidate.
    """

    code: ReviewRefusalCode
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    next_action: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    offending_input: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    expected: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    observed: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)


class ReviewCollectionPage(KnowledgeModel):
    """One bounded collection's page: its counts, its active scope, and its reachable continuation.

    This is the value ICR-R10 exists for. The surface publishes **one** of these for the collection a
    request continued, and it is the same value whether the request opened the collection or advanced
    it: a first page and a later page are the same shape, so a reader never has to treat "page 1" as
    a special case and a renderer cannot grow a continuation control that only one of them has.

    ``returned`` and ``remaining`` are the owner's own numbers, not a subtraction this surface made:
    the comparison reports ``items_returned`` cumulative over the walk, and the view reports the rows
    it returned beside the rows it declared beyond them. Their sum is the collection's total, which is
    the arithmetic a caller has to be able to trust before "page 3 of 5" means anything -- so it is a
    constructor check here rather than a claim in prose.

    ``continuation`` is the next page's cursor, and it is present **exactly** when rows remain. That
    equivalence is the whole point: a response that reported ``remaining=100`` and offered no way to
    reach them is the non-conformance this packet names, and a payload with rows left and no cursor
    cannot be constructed. The token is the owner's own opaque cursor -- the comparison's for
    ``knowledge``, the view's for ``records`` -- carried through this surface rather than re-minted,
    because a second cursor format here would be a second pagination authority.

    ``scope`` names the filters that were **active** for this page, in the owner's own vocabulary, so
    a count is never read apart from what it counted: the reviewed subject, the record kinds the
    matrix selected, the page size the owner honoured, and the display filter the comparison applied.
    An empty active filter is carried as the empty tuple rather than omitted, so "no filter" and "a
    filter this response did not report" are distinguishable.
    """

    collection: ReviewPagedCollection
    state: Literal["first_page", "continued", "reset"]
    # What ``total`` counts, declared by the builder that read the owner's own numbers, because the
    # two owners measure it differently and one rendered sentence must not carry two meanings:
    # ``selection`` is the size of the *whole* selection the walk is a page of (the comparison's own
    # ``items_total``, constant on every page), and ``walk`` is the size of the selection this walk
    # still covers (the view measures its remainder from where the walk stands, so its total shrinks
    # as the walk advances). ``returned`` is cumulative only in the first case.
    total_basis: Literal["selection", "walk"]
    total: int = Field(ge=0)
    returned: int = Field(ge=0)
    remaining: int = Field(ge=0)
    continuation: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    scope: tuple[str, ...] = ()
    # The cursor this page was asked to continue, echoed back so a reader can see which position the
    # returned window was taken at. It is absent on a first page and on a reset, where no cursor was
    # followed -- ``state`` says which of the three happened rather than leaving it to be inferred.
    continued_from: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    # The refusal a cursor earned when it could no longer bind its comparison. It is the *new
    # generation* action of ICR-R10's failure behavior: the cursor is not re-resolved, the page
    # served is the current comparison's first page, and this field names the comparison the cursor
    # was minted at beside the one that is there now.
    reset: ReviewRefusal | None = None

    @model_validator(mode="after")
    def _require_one_walk(self) -> ReviewCollectionPage:
        if self.returned + self.remaining != self.total:
            raise ValueError(
                "returned plus remaining must equal the bounded collection's total; a page that "
                "reports otherwise cannot be continued to the whole collection"
            )
        if (self.remaining > 0) != (self.continuation is not None):
            raise ValueError(
                "a page with rows remaining carries the cursor that reaches them and one with none "
                "carries no cursor; a count with no reachable continuation is not a page of anything"
            )
        if (self.state == "reset") != (self.reset is not None):
            raise ValueError(
                "a reset page carries the refusal its cursor earned and no other state does; a "
                "reset stated without its reason is indistinguishable from a first page"
            )
        if (self.state == "continued") != (self.continued_from is not None):
            raise ValueError(
                "a continued page names the cursor it was asked to continue, and a first page or a "
                "reset continued nothing"
            )
        return self


class ReviewRevisionGroup(KnowledgeModel):
    """How many retained revisions of one identity one side's selection reached.

    One side's own count, kept per side because the two snapshots may hold different numbers of
    them: a page that showed one revision cannot be read as that identity having one revision.
    """

    side: Literal["before", "after"]
    record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    selected_revision_count: int = Field(ge=0)


class ReviewFieldChange(KnowledgeModel):
    """One mechanical field transition the comparison reported, rendered as a fact.

    It names the field and the two recorded values and carries no statement about what the move
    means. ``None`` on either value is itself the recorded fact that the field was absent there.
    """

    item_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    item_kind: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    field: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    before_value: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    after_value: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)


class ReviewAuthoredEffect(KnowledgeModel):
    """One authored effect or preservation claim, displayed separately from the mechanical diff.

    Its label is the author's own word from the closed vocabulary; its rationale is the author's
    prose. The record is displayed because an identified author wrote it, and the surface adds
    nothing to it -- there is no field here for a computed effect or a preservation the author did
    not claim.
    """

    record_kind: Literal["invariant_effect_claim", "preservation_claim", "unresolved_question"]
    record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    label: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    rationale: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    author_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    examined_inputs: tuple[str, ...] = ()
    unresolved: tuple[ReviewUnresolvedReference, ...] = ()


class ReviewSignal(KnowledgeModel):
    """One mechanical detection fact: the matched condition, its inputs, versions and its limits.

    There is deliberately no severity, no verdict, no causal explanation and no author on this
    record, because a detection signal has none: it is a fact the detector matched. The scope
    limitations travel with it so a partial detection can never be read as a whole one.
    """

    signal_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    condition: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    input_set: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    detected_at: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    relationship_paths: tuple[str, ...] = ()
    extractor_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    policy_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    scope_limitations: tuple[str, ...] = ()


class ReviewAssessmentDisplay(KnowledgeModel):
    """One authored assessment as displayed: disposition, author, examined inputs, binding status.

    ``binding_state`` is the authority's own recorded status, carried verbatim: the surface never
    upgrades a stale assessment to current, and it never re-labels a recorded disposition as its own
    conclusion.
    """

    assessment_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    disposition: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    finding: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    rationale: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    author_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    role_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    examined_inputs: tuple[str, ...] = Field(min_length=1)
    binding_state: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _require_the_basis_to_travel(self) -> ReviewAssessmentDisplay:
        """Refuse an assessment displayed without its author or without the inputs it examined.

        A disposition with no author is an anonymous verdict, and an assessment that examined
        nothing cannot have been made *about* the subject it is shown beside. Neither is a
        rendering of a recorded assessment, so neither is constructible.
        """

        if not self.author_ref.strip():
            raise ValueError(
                "a displayed assessment carries its author: an anonymous verdict is not a "
                "rendering of a recorded assessment"
            )
        if not self.examined_inputs:
            raise ValueError(
                "a displayed assessment carries the exact inputs it examined; an assessment that "
                "examined nothing cannot be the basis for the subject it is displayed with"
            )
        return self


class ReviewEvidenceLink(KnowledgeModel):
    """One evidence claim reference as recorded, with its own authored limitations."""

    claim_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    claimed_coverage: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    author_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    assessment_refs: tuple[str, ...] = ()
    lifecycle: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    unresolved: tuple[ReviewUnresolvedReference, ...] = ()


class ReviewObservation(KnowledgeModel):
    """One verification observation displayed exactly, including what it does not establish.

    Everything here is what the run recorded: the tested candidate, the command or gate identity,
    the result artifact and its digest, the execution result and the environment. The model has no
    field for a sufficiency verdict, which is what keeps a passing run from being rendered as an
    invariant being satisfied.
    """

    observation_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    tested_candidate: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    command_identity: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    result_artifact_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    result_artifact_digest: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    execution_result: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    environment_identity: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    limitations: tuple[str, ...] = ()


# The six persistent counts the source pane exposes, declared once so the pane's own vocabulary and
# the composition that fills it cannot come to disagree about which counts exist. The attribution
# counts are three and not two (ICR-R04): ``unattributed_changed_paths`` is the confirmed negative
# conclusion, ``unknown_attribution_changed_paths`` is the measured population that conclusion could
# not be drawn for, and a reader must be able to see both numbers beside each other -- a bare zero
# with no undetermined count next to it is the "measured zero implies completeness" reading this
# vocabulary exists to prevent.
ReviewRemainingCountName = Literal[
    "locations_remaining",
    "changed_paths_outside_selection",
    "unattributed_changed_paths",
    "unknown_attribution_changed_paths",
    "references_unresolved",
    "records_present_outside_selection",
]


class ReviewRemainingCount(KnowledgeModel):
    """One persistent count exposing what the selection did not reach.

    ``value`` is ``None`` exactly when the quantity has no meaning for this rendering, and the
    reason is then stated: a zero that means "none" and a zero that means "not measured here" are
    different facts and a reviewer must be able to tell them apart.
    """

    name: ReviewRemainingCountName
    value: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_a_stated_state(self) -> ReviewRemainingCount:
        if self.value is None and not (self.reason or "").strip():
            raise ValueError(
                "a count without a meaning states why; an unexplained absent count reads as a zero"
            )
        if self.value is not None and self.reason is not None:
            raise ValueError("a measured count carries its value and no not-applicable reason")
        return self


class ReviewSourceLocation(KnowledgeModel):
    """One selected source location under its recorded claim, as the read reported it.

    ``role`` is the author's recorded word and stays ``None`` when the record carries none: a
    missing role is displayed as unclassified rather than guessed from a path, and there is no
    field here for an importance or a ranking derived from a name.

    ``invariant_id`` is the **preserved canonical identity** the location's recorded relationship
    sits under (ICR-R08@v1), which is what makes two rows one association: a realization the author
    moved from A to B is two locations carrying the same ``invariant_id``, each naming the side of the
    movement its own address is (``recorded_side``) and carrying the whole relationship in
    ``movement``. ``counterpart_path`` is the other side's recorded address when the movement records
    exactly one there, and is absent when the other side records several or none -- the movement lists
    every one of them, and no single address is chosen to stand for them. The identity is absent
    exactly when the traversal could not establish it, and the movement beside it states that as a gap
    rather than the location inventing one.
    """

    claim_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    invariant_revision_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    role: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    rationale: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    recorded_source_identity: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    observed_source_identity: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    resolution: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    change_state: Literal["changed", "unchanged", "not_selected"]
    before_only: bool = False
    reached_via: tuple[str, ...] = ()
    invariant_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    transition: ReviewRelationshipTransition = "unchanged"
    recorded_side: Literal["before", "after"] | None = None
    counterpart_path: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    movement: ReviewRelationshipMovement | None = None


class ReviewKnowledgePane(KnowledgeModel):
    """Pane 1 -- Knowledge: identities, retained revisions, exact statements and authored records.

    The authored records and the mechanical signal facts are separate collections with separate
    element types, so a rendering cannot place a signal where a finding goes without the type
    system objecting first.

    ``selection_state`` says which question this pane answered. ``subject_selected`` means the
    reviewed subject was compared and the two statements below are that comparison's operands;
    ``task_context`` means the review was opened from the task alone, so **no operand was compared**
    and the pane carries unresolved sides rather than empty ones. The detail is required in exactly
    that second state, because "nothing was compared" without a reason reads like a subject whose
    snapshots hold no statement.
    """

    invariant_ids: tuple[str, ...] = ()
    family_ids: tuple[str, ...] = ()
    before_statement: ReviewSideContent
    after_statement: ReviewSideContent
    before_conditions: tuple[str, ...] = ()
    after_conditions: tuple[str, ...] = ()
    revision_groups: tuple[ReviewRevisionGroup, ...] = ()
    # The explicit before/after revision selection the two statements were rendered from
    # (ICR-R07@v1): the compared head pair, the one-sided head, or the explicit ambiguous or
    # unresolved selection that rendered no winner. It is absent exactly when no subject was
    # compared -- the task-context pane below -- because a review that compared no operand
    # selected no revision either.
    revision_selection: ReviewRevisionSelection | None = None
    field_changes: tuple[ReviewFieldChange, ...] = ()
    authored_effects: tuple[ReviewAuthoredEffect, ...] = ()
    signals: tuple[ReviewSignal, ...] = ()
    assessment: ReviewAssessmentDisplay | None = None
    assessments: tuple[ReviewAssessmentDisplay, ...] = ()
    unresolved: tuple[ReviewUnresolvedReference, ...] = ()
    selection_state: Literal["subject_selected", "task_context"] = "subject_selected"
    selection_detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_assessment_state_to_be_one_fact(self) -> ReviewKnowledgePane:
        """Keep the single assessment and the collection from describing two different states."""

        if self.assessment is not None and self.assessment not in self.assessments:
            raise ValueError(
                "the pane's own assessment must be one of the assessments it displays; two "
                "spellings of the same subject's state are how a pane comes to disagree with itself"
            )
        return self

    @model_validator(mode="after")
    def _require_the_selection_state_to_state_itself(self) -> ReviewKnowledgePane:
        if (self.selection_state == "task_context") != (self.selection_detail is not None):
            raise ValueError(
                "a task-context pane states why no subject was compared, and a pane that compared a "
                "subject carries no task-context reason"
            )
        return self

    @model_validator(mode="after")
    def _require_a_compared_subject_to_record_its_selection(self) -> ReviewKnowledgePane:
        # One direction only, and deliberately so: a recorded selection implies a compared
        # subject, but a compared subject need not carry one -- a selector that names no
        # identity (a path seed through the direct composition call) addresses no identity
        # item, and recording a selection there would invent the identity it never named.
        if self.revision_selection is not None and self.selection_state != "subject_selected":
            raise ValueError(
                "a recorded revision selection is a selection a compared subject was rendered "
                "from; a task-context pane that compared nothing records none"
            )
        return self


ReviewFileStatus = Literal["added", "deleted", "modified", "type_changed", "unknown"]

ReviewFileContent = Literal["text", "binary", "symlink", "submodule", "unknown"]


class ReviewChangedFile(KnowledgeModel):
    """One path the bound source pair differs at, as an address, a status and a renderability.

    This is the surface's own inventory entry, and it is deliberately three separate facts.
    ``path`` is the **raw filename** exactly as Git recorded it -- a tab or a newline inside it is
    part of the address the same file is expanded with and never a separator -- so the string here
    is the string a later read of that file must use. ``status`` is what happened to the path and
    carries no assessment of it. ``content`` states whether the path's content can be rendered at
    all, which is a property of the content and not of the change: a binary path, a symlink and a
    submodule pointer are listed exactly like a text file and are simply not text.

    ``mode_change`` separates "the bytes moved" from "only the permission word moved" without
    turning either into a different status, and ``detail`` is present exactly when ``content`` is
    ``unknown``, where it says whether the classification was not measured or was not reported for
    this path.
    """

    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    status: ReviewFileStatus
    content: ReviewFileContent
    mode_change: bool = False
    detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_an_unknown_content_to_state_its_reason(self) -> ReviewChangedFile:
        if (self.content == "unknown") != (self.detail is not None):
            raise ValueError(
                "an entry whose content cannot be classified states why, and a classified entry "
                "states no reason; an unexplained unknown reads like a measured absence"
            )
        return self


class ReviewUnrepresentablePath(KnowledgeModel):
    """One changed path whose **name** cannot be carried as text by this vocabulary.

    A Git pathname is bytes and this surface carries text. The runner preserves the bytes exactly, so
    a name that is not valid UTF-8 arrives as lone surrogates -- a value a ``str`` field refuses --
    and such a path would otherwise vanish from a response that claimed to be complete, or crash the
    response that was supposed to state the fact. It is carried here instead.

    ``path_bytes`` is the path's exact bytes rendered ASCII-safely (``b'src/caf\xe9-latin1.py'``), so
    the value is valid text and still says precisely which file changed; ``status`` and
    ``mode_change`` are what Git reported for it. Nothing is re-encoded: a re-encoded name would
    address a file this repository does not hold, which is worse than an unusual spelling.
    """

    # The byte form of a pathname is longer than the pathname (up to four characters per byte), so it
    # is bounded by the prose limit rather than by the path limit.
    path_bytes: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    status: ReviewFileStatus
    mode_change: bool = False
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)


class ReviewSourceInventory(KnowledgeModel):
    """The complete source change set of one bound pair: measured, partial, or unavailable.

    This is the value R02 exists for. It is measured from the two code trees the resolution bound
    and from nothing else, so it is present and complete for a candidate that records no invariant,
    for a candidate whose datasets do not exist, and for a comparison whose knowledge half is a
    different question entirely. The knowledge selection may filter *attribution*; it can never
    remove an entry from here.

    ``state`` is the honesty boundary. ``measured`` means this list is the whole change set of the
    pair, including the measured empty set two identical trees produce. ``unavailable`` means the
    measurement was not made at all, and then ``entries`` is empty **because nothing was observed**:
    the ``detail`` carries the reason, and the count is zero rather than reported as "no changes".
    ``partial`` is the third state and it is not a weaker measurement of the path set -- every
    changed path is listed and one field of some entries could not be classified, which each entry
    states for itself.

    ``listed_total`` is the length of the list above and is checked against it, so a count can never
    describe a population the response does not carry. Both code tree ids travel with the list, so a
    caller can reproduce the exact measurement the entries came from.

    ``unrepresentable_paths`` is the rest of the measured population, and it exists so the two lists
    together are the whole change set: a changed path whose *name* is not valid text cannot be
    ``entries`` without the value itself being unrepresentable, and dropping it would make a partial
    change set read as a complete one. An inventory that carries any of them is ``partial`` by
    construction, because it is exactly that.
    """

    state: Literal["measured", "unavailable"]
    entries: tuple[ReviewChangedFile, ...] = ()
    listed_total: int = Field(ge=0)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    partial: bool = False
    command: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    before_code_tree_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    after_code_tree_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    unrepresentable_paths: tuple[ReviewUnrepresentablePath, ...] = ()

    @model_validator(mode="after")
    def _require_the_count_to_describe_the_list(self) -> ReviewSourceInventory:
        if self.listed_total != len(self.entries):
            raise ValueError(
                "an inventory's count is the length of the list beside it; a count of a population "
                "the response does not carry is how a partial list is read as a whole one"
            )
        if self.state == "unavailable" and self.entries:
            raise ValueError(
                "an unavailable measurement lists nothing: entries beside it would be read as an "
                "observed change set"
            )
        if self.state == "unavailable" and self.partial:
            raise ValueError(
                "an unavailable measurement is not a partial one; there is no measured remainder "
                "to label"
            )
        if self.unrepresentable_paths and (self.state != "measured" or not self.partial):
            raise ValueError(
                "an inventory that could not carry some changed paths as text is a measured, "
                "partial one; an unrepresentable path beside a complete or unavailable inventory "
                "describes no measurement"
            )
        return self


class ReviewSourcePane(KnowledgeModel):
    """Pane 2 -- Source: the whole-task inventory, selected locations, and what the selection missed.

    ``inventory`` is required and comes first, because it is the pane's one fact that does not depend
    on a knowledge selection: the complete source change set of the comparison's bound pair. Every
    field below it is the *attribution* half -- which of those changes a recorded realization claim
    reaches -- and that half may legitimately be empty, filtered or unmeasured without shortening the
    inventory above it.

    ``attribution`` is that half as **one** accounting rather than three independent lists: the
    measured change population partitioned once into attributed, confirmed unregistered and
    undetermined, with each bound snapshot's inspection state beside it. The three path lists below it
    are that value's own buckets, so a path can appear in exactly one of them and a change nobody
    looked for is never listed as a change nobody registered.
    """

    inventory: ReviewSourceInventory
    locations: tuple[ReviewSourceLocation, ...] = ()
    # The recorded before/after relationship union this review traversed (ICR-R08@v1): every
    # realization, family membership, advertised frontier link and the reviewed identity's governing
    # route, each displayed with both sides. It is a separate collection from ``locations`` because
    # it holds the relationships that have no source address at all -- a membership and a route
    # association -- and because a movement whose two sides are one row at one address is still one
    # relationship rather than two locations.
    relationships: tuple[ReviewRelationshipMovement, ...] = ()
    remaining: tuple[ReviewRemainingCount, ...] = ()
    expansion_reference: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    expansion_command: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    unattributed_changed_paths: tuple[str, ...] = ()
    attributed_changed_paths: tuple[str, ...] = ()
    unknown_attribution_changed_paths: tuple[str, ...] = ()
    attribution: SourceAttribution | None = None
    unresolved: tuple[ReviewUnresolvedReference, ...] = ()


class ReviewEvidencePane(KnowledgeModel):
    """Pane 3 -- Evidence and assessment, with its two absence states stated rather than implied.

    ``evidence_state`` and ``assessment_state`` are separate because they are separate facts: a
    corpus can hold evidence and no assessment, an assessment and no evidence, or neither. Neither
    state has a favourable member, so no rendering can turn an absence into a clearance.

    ``channels`` is the composition's own supply of every record class this review read, one entry per
    class, with the availability fact each owner's answer earned. It is carried here because this is
    the pane that displays records rather than recorded knowledge, and it is carried *whole*: a class
    that was not read, or that could not be read, appears with that state instead of being absent from
    the list.
    """

    evidence_state: Literal["recorded", "none_recorded"]
    assessment_state: Literal["assessed", "unassessed"]
    evidence_links: tuple[ReviewEvidenceLink, ...] = ()
    observations: tuple[ReviewObservation, ...] = ()
    assessments: tuple[ReviewAssessmentDisplay, ...] = ()
    source_inspection_available: bool
    channels: tuple[ReviewRecordChannel, ...] = ()
    unresolved: tuple[ReviewUnresolvedReference, ...] = ()

    @model_validator(mode="after")
    def _require_the_states_to_match_their_collections(self) -> ReviewEvidencePane:
        if (self.evidence_state == "recorded") != bool(self.evidence_links or self.observations):
            raise ValueError(
                "the evidence pane's state must match the links and observations it carries; an "
                "empty corpus reported as recorded, or a populated one reported as empty, is a "
                "false statement about the evidence either way"
            )
        if (self.assessment_state == "assessed") != bool(self.assessments):
            raise ValueError(
                "the evidence pane's assessment state must match the assessments it carries"
            )
        return self


class ReviewStaleness(KnowledgeModel):
    """Whether the displayed comparison is still the candidate's comparison, and if not, what was.

    A stale payload keeps the last displayed comparison as a *labelled previous input*: the identity
    is retained and named as previous, so a reviewer can see what was reviewed while being unable to
    mistake it for a review of what is there now.

    ``not_compared`` is the task-context state and not a third flavour of current: a review opened
    from the task alone compared no knowledge operand, so there is no comparison binding that could
    be current or stale, and the response says that instead of borrowing the word for either.
    """

    state: Literal["current", "stale", "not_compared"]
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    previous_comparison_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    moved: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _require_the_previous_input_to_be_labelled(self) -> ReviewStaleness:
        if self.state == "stale" and self.previous_comparison_ref is None:
            raise ValueError(
                "a stale comparison retains the comparison it is labelling as previous input"
            )
        if self.state != "stale" and self.previous_comparison_ref is not None:
            raise ValueError("only a stale comparison has a previous input to label")
        return self


class ReviewSubmission(KnowledgeModel):
    """Whether an assessment may be submitted against this comparison, and through what.

    This increment ships the review surface **display-only**, and the state says so rather than
    leaving it implicit: there is no serving route that publishes an assessment, so the surface
    reports the absence and names the existing authority that does. ``proposed_dispositions``
    publishes which judgements the authority accepts, and ``none_is_approval`` states the boundary
    the vocabulary itself enforces -- none of the three is publication approval.
    """

    state: Literal["unavailable", "disabled_stale"]
    reason: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    next_action: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    proposed_dispositions: tuple[str, ...] = ()
    none_is_approval: bool = True


class KnowledgeReviewPayload(KnowledgeModel):
    """The whole surface one response renders: identity, three panes, staleness and submission.

    The stale rule is a constructor check here. A payload whose comparison is stale and whose
    submission state is not ``disabled_stale`` cannot be built, and neither can a current comparison
    whose submission claims to be disabled for staleness -- so "an assessment is never submitted
    against a comparison that has moved" is a property of the value rather than a rule a client is
    asked to honour.

    ``comparison`` is absent exactly when no knowledge comparison was made -- the task-context review
    R02 requires, in which the source pane still carries the complete inventory of the bound pair.
    The absence is not a blank: it is held in agreement with the staleness state below, so a payload
    cannot omit the comparison and still claim to be current.

    ``page`` is the bounded collection this response rendered as a page of (ICR-R10). It is present
    exactly when the request named one, and it carries that collection's total, returned and remaining
    counts, the filters that were active, and the cursor that reaches the rest -- so a reader is never
    shown a remainder without the way to reach it. A request that named no collection pages nothing
    and carries no page, which is a different fact from a page with nothing left in it.
    """

    surface_version: str = Field(
        default=KNOWLEDGE_REVIEW_SURFACE_VERSION, min_length=1, max_length=LABEL_MAX_LENGTH
    )
    candidate: ReviewCandidateRef
    comparison: ComparisonIdentity | None = None
    knowledge: ReviewKnowledgePane
    source: ReviewSourcePane
    evidence: ReviewEvidencePane
    staleness: ReviewStaleness
    submission: ReviewSubmission
    page: ReviewCollectionPage | None = None
    # The refusal a *requested* page earned when no page could be stated from it (ICR-R10). A page
    # value needs the owner's own counts, and a refused read has none, so the honest shape is the
    # refusal itself rather than a page of invented zeros: the code, the offending cursor and the
    # owner's expected/observed identities all reach the reader, and the client can offer the first
    # page of the collection as the live action. It is present exactly when a page was asked for and
    # none could be served, which is why it is checked against ``page`` here.
    page_refusal: ReviewRefusal | None = None
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _require_the_submission_state_to_follow_staleness(self) -> KnowledgeReviewPayload:
        stale = self.staleness.state == "stale"
        disabled = self.submission.state == "disabled_stale"
        if stale != disabled:
            raise ValueError(
                "submission is disabled exactly when the comparison is stale; a stale comparison "
                "offered for submission, or a current one refused for staleness, is a review that "
                "would be reused against inputs it never examined"
            )
        if self.submission.proposed_dispositions and set(
            self.submission.proposed_dispositions
        ) - set(PROPOSED_ASSESSMENT_DISPOSITIONS):
            raise ValueError(
                "the surface publishes the authority's declared dispositions and invents none"
            )
        return self

    @model_validator(mode="after")
    def _require_one_page_outcome(self) -> KnowledgeReviewPayload:
        """A payload states a page or the refusal a page earned, never both and never neither by accident."""

        if self.page is not None and self.page_refusal is not None:
            raise ValueError(
                "a payload carries the page it served or the refusal a requested page earned; a "
                "page beside its own refusal is two answers to one question"
            )
        return self

    @model_validator(mode="after")
    def _require_the_identity_and_staleness_to_agree(self) -> KnowledgeReviewPayload:
        absent = self.comparison is None
        if absent != (self.staleness.state == "not_compared"):
            raise ValueError(
                "a payload states that no knowledge comparison was made exactly when it carries no "
                "comparison identity; a missing identity beside a current or stale state claims a "
                "measurement nobody made"
            )
        compared = self.comparison is not None and self.comparison.knowledge_compared
        if compared != (self.knowledge.selection_state == "subject_selected"):
            raise ValueError(
                "the comparison identity and the knowledge pane answer the same question about "
                "which subject was compared, and two spellings of one state is how a payload comes "
                "to disagree with itself"
            )
        return self


class KnowledgeReviewResult(KnowledgeModel):
    """The typed outcome of one review read: a payload, or one typed refusal."""

    state: Literal["review", "refused"]
    operation: Literal["read_knowledge_review"] = "read_knowledge_review"
    repository_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    payload: KnowledgeReviewPayload | None = None
    refusal: ReviewRefusal | None = None

    @model_validator(mode="after")
    def _require_one_outcome(self) -> KnowledgeReviewResult:
        if self.state == "review" and (self.payload is None or self.refusal is not None):
            raise ValueError("a review result carries a payload and no refusal")
        if self.state == "refused" and (self.refusal is None or self.payload is not None):
            raise ValueError("a refused result carries its refusal and no payload")
        return self


class ReviewEntryListResult(KnowledgeModel):
    """The typed outcome of one entry read: the subjects the pair can be opened on, or a refusal.

    ``entries`` is empty exactly when the read is refused, and a refused read carries its refusal --
    so a caller cannot read "no entry was offered" as "there is nothing to review". An empty
    ``entries`` on an ``entries`` state is a pair that selected no reviewable subject, which is a
    fact about the datasets and is stated as one; the task-context source review stays reachable
    beside it. The three totals describe the population the entries come from -- every invariant
    and family identity the comparison's before/after pair records -- so a caller can verify the
    list it received is the whole catalogue rather than its first row.
    """

    state: Literal["entries", "refused"]
    operation: Literal["list_knowledge_review_entries"] = "list_knowledge_review_entries"
    repository_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    master: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    entries: tuple[ReviewEntry, ...] = ()
    refusal: ReviewRefusal | None = None
    # The labelled totals of the catalogue: every recorded subject, partitioned once into the two
    # reviewable kinds. A refused read measured no catalogue, so its totals are zero; an answered
    # read carries at least the page it returned, so a caller traversing the list can tell a whole
    # catalogue from a first row.
    total_subjects: int = Field(default=0, ge=0)
    invariant_total: int = Field(default=0, ge=0)
    family_total: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _require_one_outcome(self) -> ReviewEntryListResult:
        if self.state == "refused" and self.refusal is None:
            raise ValueError("a refused entry read carries its refusal")
        if self.state == "entries" and self.refusal is not None:
            raise ValueError("an entry read that answered carries no refusal")
        if self.state == "refused" and self.entries:
            raise ValueError(
                "a refused entry read offers no subject; an entry beside a refusal is "
                "how a caller comes to review a subject nothing admitted"
            )
        return self

    @model_validator(mode="after")
    def _require_the_totals_to_describe_the_catalogue(self) -> ReviewEntryListResult:
        if self.total_subjects != self.invariant_total + self.family_total:
            raise ValueError(
                "the catalogue total is the two kind totals added once; a total beside a "
                "partition it does not sum is how a caller comes to trust a count of a "
                "population the response does not carry"
            )
        if self.state == "refused" and (
            self.total_subjects or self.invariant_total or self.family_total
        ):
            raise ValueError(
                "a refused entry read measured no catalogue, so its totals are zero; totals "
                "beside a refusal would read as a population nothing admitted"
            )
        if self.state == "entries" and self.total_subjects < len(self.entries):
            raise ValueError(
                "the catalogue total describes the population the entries come from, so it "
                "carries at least the page beside it; a smaller total is how a whole "
                "catalogue comes to read as a first row"
            )
        return self
