"""The three tree-review panes and their comparison-bound response contract.

Statements, source facts, history judgments and proofs keep their own recorded
meaning. Missing text is an explicit state, not an empty operand. A stale comparison
structurally disables submission, and a missing required judgment pane is invalid.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.read import KnowledgeReadSeed
from agents_remember.models.knowledge.review_external_movement import ExternalGitMovement
from agents_remember.models.knowledge.review_family_context import ReviewFamilyContext
from agents_remember.models.knowledge.review_judgment import ReviewJudgmentPane
from agents_remember.models.knowledge.review_meaning_effects import ReviewMeaningEffects
from agents_remember.models.knowledge.review_outline import ReviewOutline
from agents_remember.models.knowledge.review_refusal import ReviewRefusal, ReviewRefusalCode
from agents_remember.models.knowledge.review_relationships import (
    ReviewRelationshipGap,
    ReviewRelationshipMovement,
    ReviewRelationshipSide,
    ReviewRelationshipTransition,
    ReviewRenameInference,
)
from agents_remember.models.knowledge.review_source import (
    ReviewChangedFile,
    ReviewFileContent,
    ReviewFileStatus,
    ReviewRemainingCount,
    ReviewRemainingCountName,
    ReviewSourceInventory,
    ReviewSourceLocation,
    ReviewSourcePane,
    ReviewUnrepresentablePath,
    ReviewUnresolvedReference,
)
from agents_remember.models.knowledge.review_staleness import (
    ReviewStaleness,
    ReviewSubmission,
    ReviewSyncMovement,
)
from agents_remember.models.knowledge.revision_selection import ReviewRevisionSelection
from agents_remember.models.knowledge.view import MAX_VIEW_ROWS
from agents_remember.models.knowledge_files.shapes import Origin

__all__ = [
    "KNOWLEDGE_REVIEW_SURFACE_VERSION",
    "MAXIMUM_REVIEW_PAGE_SIZE",
    "PROPOSED_ASSESSMENT_DISPOSITIONS",
    "REVIEW_PAGED_COLLECTIONS",
    "REVIEW_PAGE_RESET_NEXT_ACTION",
    "REVIEW_PANE_NAMES",
    "ComparisonIdentity",
    "ExternalGitMovement",
    "KnowledgeReviewPayload",
    "KnowledgeReviewResult",
    "ReviewCandidateRef",
    "ReviewChangedFile",
    "ReviewCollectionPage",
    "ReviewEntry",
    "ReviewEntryListResult",
    "ReviewFieldChange",
    "ReviewFileContent",
    "ReviewFileStatus",
    "ReviewJudgmentPane",
    "ReviewKnowledgePane",
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
    "ReviewSourceInventory",
    "ReviewSourceLocation",
    "ReviewSourcePane",
    "ReviewStaleness",
    "ReviewSubjectKind",
    "ReviewSubjectPresence",
    "ReviewSubmission",
    "ReviewSurfaceRequest",
    "ReviewSyncMovement",
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

REVIEW_PANE_NAMES: tuple[str, ...] = ("knowledge", "source", "judgment")

# ``RRD:366``'s three proposed dispositions, verbatim. They are published on the payload so a
# reviewer can see which judgements are expressible; none of them is publication approval, and the
# surface renders them as a statement about the authority rather than as a control of its own.
PROPOSED_ASSESSMENT_DISPOSITIONS: tuple[str, ...] = (
    "concern_found",
    "no_concern_found",
    "unresolved",
)


# The three bounded collections one review composes, declared once so the request, the payload and
# the transport cannot come to disagree about which one a cursor addresses (ICR-R10). They are
# separate because their cursors are separate shipped documents -- the comparison's own cursor
# positions a page in a union of two snapshots, the view's continuation positions one in a single
# selection of one of them, and the family roster's cursor is the read operation's own position in
# one recorded family revision's scope (ICR-R31) -- and presenting one to another collection's owner
# is a caller's mistake that the owners refuse rather than a slice this surface may reinterpret.
ReviewPagedCollection = Literal["knowledge", "records", "family_members"]
REVIEW_PAGED_COLLECTIONS: tuple[ReviewPagedCollection, ...] = (
    "knowledge",
    "records",
    "family_members",
)

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

ReviewSideState = Literal["present", "absent", "unresolved"]

# Which record a review read is addressed to (ICR-R12). One value, because the surface addresses
# exactly one historical record: the leaf's own published comparison generation. A second spelling
# would be a second way to ask for the same record, and there is deliberately no way to ask for a
# generation that is not this leaf's.
ReviewHistoryRef = Literal["recorded"]


class ReviewSideContent(KnowledgeModel):
    """One side's exact payload text, or the explicit state that says why there is none.

    ``text`` is carried only when the side is ``present``. An absent operand and an
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
                "a side that is not present carries no text: rendering an absent or "
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

    ``history`` names **which record the caller is reading** (ICR-R12). Its absence is the live
    review, which is what every caller that names none asks for, and ``recorded`` asks for the
    comparison the leaf's own durable generation bound -- the same answer whether the leaf's worktree
    is still there or cleanup removed it, because the record and not the enclosure is what the review
    is read from. A caller cannot ask for "some other generation": the leaf's own record is the only
    historical comparison this surface addresses, and its own resolution refuses a leaf that
    recorded none.

    ``previous_binding_digest`` names the comparison the caller was already looking at when the read
    replaces one (ICR-R17@v1); its absence means the read replaces nothing, which is ``current``.
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
    history: ReviewHistoryRef | None = None
    # The binding identity a refresh is replacing: the *previous* identity, never a substitute for
    # the current one -- it selects no dataset, resolves no candidate and is never rendered as the
    # comparison. The pattern is the digest's published shape, so a value that names no generation
    # this surface could have published cannot be asserted as a previous input; the composition
    # answers a digest the comparison does not match as `stale` (ICR-R17@v1).
    previous_binding_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)

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


# The six persistent counts the source pane exposes, declared once so the pane's own vocabulary and
# the composition that fills it cannot come to disagree about which counts exist. The attribution
# counts are three and not two (ICR-R04): ``unattributed_changed_paths`` is the confirmed negative
# conclusion, ``unknown_attribution_changed_paths`` is the measured population that conclusion could
# not be drawn for, and a reader must be able to see both numbers beside each other -- a bare zero
# with no undetermined count next to it is the "measured zero implies completeness" reading this
# vocabulary exists to prevent.


class ReviewKnowledgePane(KnowledgeModel):
    """Pane 1 -- Knowledge: identities, retained revisions, exact statements and authored records.

    Each side carries the selected record's raw origin separately from its statement.

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
    before_origin: Origin | None = None
    after_origin: Origin | None = None
    before_conditions: tuple[str, ...] = ()
    after_conditions: tuple[str, ...] = ()
    revision_groups: tuple[ReviewRevisionGroup, ...] = ()
    # The explicit before/after revision selection the two statements were rendered from
    # (ICR-R07@v1): the compared revision pair, the one-sided revision, or the explicit
    # unresolved selection that rendered no winner. It is absent exactly when no subject was
    # compared -- the task-context pane below -- because a review that compared no operand
    # selected no revision either.
    revision_selection: ReviewRevisionSelection | None = None
    field_changes: tuple[ReviewFieldChange, ...] = ()
    unresolved: tuple[ReviewUnresolvedReference, ...] = ()
    selection_state: Literal["subject_selected", "task_context"] = "subject_selected"
    selection_detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

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
    and carries no page, which is a different fact from a page with nothing left in it. The one
    collection that is a *set* of walks rather than a single one is the family roster: a request names
    it with the cursor of the walk it continues, and naming it without one earns the refusal below
    instead of an arbitrary walk's page.
    """

    surface_version: str = Field(
        default=KNOWLEDGE_REVIEW_SURFACE_VERSION, min_length=1, max_length=LABEL_MAX_LENGTH
    )
    candidate: ReviewCandidateRef
    comparison: ComparisonIdentity | None = None
    knowledge: ReviewKnowledgePane
    source: ReviewSourcePane
    judgment: ReviewJudgmentPane
    # The recorded families the selected subject belongs to on each bound snapshot, with each selected
    # family revision's own guarantee and its recorded member roster (ICR-R31@v1). It is required, not
    # optional: every review answers the question, and a selection that read its scope and holds no
    # family states ``no_family_recorded`` rather than leaving an absent field to be read as one.
    family_context: ReviewFamilyContext
    # The recorded effect of this leaf's meaning changes (MIK-R43): the steps of the
    # selected record and of the families and members the family context returns, in
    # attempt order, or why there are none. Required, not optional: every review
    # answers the question, and a selection whose history holds no step states ``none``
    # rather than leaving an absent field to be read as one. A current build's answer
    # without this field is invalid and is shown as a failure, not as ``none``.
    meaning_effects: ReviewMeaningEffects
    staleness: ReviewStaleness
    submission: ReviewSubmission
    # What this leaf's own managed syncs measured against the generation it published (ICR-R22@v1),
    # or ``None`` when no rebinding is recorded for it -- "no sync has reported" is a different fact
    # from "a sync reported agreement", and the field states which. A stale movement is folded into
    # ``staleness`` beside it, so a review whose inputs a sync moved can never read as current.
    sync_movement: ReviewSyncMovement | None = None
    # What a *raw* Git operation moved under this generation (ICR-R23@v1): the declared identities
    # the repository no longer shows, the transition shape measured, and the recovery routes the
    # support matrix publishes. ``None`` is "no boundary was measured" -- a pair resolved without an
    # enclosure, a closed leaf's record, a branch with no publication -- a different fact from a
    # boundary that compared and found nothing replaced. A replaced identity is folded into
    # ``staleness`` beside it, so an unrecognized transition never leaves stale assessment able to
    # read as current; the vocabulary, matrix and states live in
    # ``models.knowledge.review_external_movement``.
    external_git_movement: ExternalGitMovement | None = None
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
    outline: ReviewOutline | None = None

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
