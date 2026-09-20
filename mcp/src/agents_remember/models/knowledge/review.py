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
from agents_remember.models.knowledge.read import KnowledgeReadSeed

__all__ = [
    "KNOWLEDGE_REVIEW_SURFACE_VERSION",
    "PROPOSED_ASSESSMENT_DISPOSITIONS",
    "REVIEW_PANE_NAMES",
    "ComparisonIdentity",
    "KnowledgeReviewPayload",
    "KnowledgeReviewResult",
    "ReviewAssessmentDisplay",
    "ReviewAuthoredEffect",
    "ReviewCandidateRef",
    "ReviewEntry",
    "ReviewEntryListResult",
    "ReviewEvidenceLink",
    "ReviewEvidencePane",
    "ReviewFieldChange",
    "ReviewKnowledgePane",
    "ReviewObservation",
    "ReviewRefusal",
    "ReviewRefusalCode",
    "ReviewRemainingCount",
    "ReviewRevisionGroup",
    "ReviewSideContent",
    "ReviewSignal",
    "ReviewSourceLocation",
    "ReviewSourcePane",
    "ReviewStaleness",
    "ReviewSubjectKind",
    "ReviewSubmission",
    "ReviewSurfaceRequest",
    "ReviewUnresolvedReference",
]

# The surface's own version. It is a recorded value rather than a package version read at display
# time, so two payloads produced by different renderings are distinguishable from the payloads.
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
    "review_adapter_unavailable",
]

# The two subject kinds the surface reviews, declared once here so the transport, the entry list and
# the panes cannot come to disagree about which identities are reviewable. The name is the server's;
# the browser's ``ReviewSelectorKind`` is the same two spellings on the wire.
ReviewSubjectKind = Literal["invariant", "family"]

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
    """

    repository_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    master: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    selector: KnowledgeReadSeed


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
    """One subject the resolved pair can be compared on, as the comparison itself selected it.

    This is the value the task-view entry carries, and it exists so that entry never has to invent
    one: ``selector_id`` is the recorded identity of a subject the shipped comparison reached on
    both of the candidate's own sides, and ``label`` is that identity's own recorded display label.
    There is no field here for a path, a file, a display version or a ranking, so an entry cannot
    point at a dataset the resolution did not select and cannot be ordered by a preference this
    list invented.
    """

    selector_kind: ReviewSubjectKind
    selector_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    # How many of the comparison's own items the subject's selection reached. It is the operation's
    # count and not a score: a subject with zero reached items is not offered at all.
    selected_item_count: int = Field(ge=0)


class ComparisonIdentity(KnowledgeModel):
    """The comparison the panes were rendered from, as the shared operation declared it.

    The surface renders this identity and never re-derives one. It carries the operation's own
    binding digest, its policy and version, both declared snapshot digests and both code tree ids --
    so "the same comparison" is a value a reviewer can compare between two responses rather than a
    claim the surface makes about itself.
    """

    reference: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    policy_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    binding_digest: str = Field(pattern=SHA256_PATTERN)
    selector_digest: str = Field(pattern=SHA256_PATTERN)
    before_snapshot_digest: str = Field(pattern=SHA256_PATTERN)
    after_snapshot_digest: str = Field(pattern=SHA256_PATTERN)
    before_code_tree_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    after_code_tree_id: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)


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


class ReviewRemainingCount(KnowledgeModel):
    """One persistent count exposing what the selection did not reach.

    ``value`` is ``None`` exactly when the quantity has no meaning for this rendering, and the
    reason is then stated: a zero that means "none" and a zero that means "not measured here" are
    different facts and a reviewer must be able to tell them apart.
    """

    name: Literal[
        "locations_remaining",
        "changed_paths_outside_selection",
        "unattributed_changed_paths",
        "references_unresolved",
        "records_present_outside_selection",
    ]
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


class ReviewKnowledgePane(KnowledgeModel):
    """Pane 1 -- Knowledge: identities, retained revisions, exact statements and authored records.

    The authored records and the mechanical signal facts are separate collections with separate
    element types, so a rendering cannot place a signal where a finding goes without the type
    system objecting first.
    """

    invariant_ids: tuple[str, ...] = ()
    family_ids: tuple[str, ...] = ()
    before_statement: ReviewSideContent
    after_statement: ReviewSideContent
    before_conditions: tuple[str, ...] = ()
    after_conditions: tuple[str, ...] = ()
    revision_groups: tuple[ReviewRevisionGroup, ...] = ()
    field_changes: tuple[ReviewFieldChange, ...] = ()
    authored_effects: tuple[ReviewAuthoredEffect, ...] = ()
    signals: tuple[ReviewSignal, ...] = ()
    assessment: ReviewAssessmentDisplay | None = None
    assessments: tuple[ReviewAssessmentDisplay, ...] = ()
    unresolved: tuple[ReviewUnresolvedReference, ...] = ()

    @model_validator(mode="after")
    def _require_the_assessment_state_to_be_one_fact(self) -> ReviewKnowledgePane:
        """Keep the single assessment and the collection from describing two different states."""

        if self.assessment is not None and self.assessment not in self.assessments:
            raise ValueError(
                "the pane's own assessment must be one of the assessments it displays; two "
                "spellings of the same subject's state are how a pane comes to disagree with itself"
            )
        return self


class ReviewSourcePane(KnowledgeModel):
    """Pane 2 -- Source: selected locations, expansion, and what the selection did not reach."""

    locations: tuple[ReviewSourceLocation, ...] = ()
    remaining: tuple[ReviewRemainingCount, ...] = ()
    expansion_reference: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    expansion_command: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    unattributed_changed_paths: tuple[str, ...] = ()
    attributed_changed_paths: tuple[str, ...] = ()
    unresolved: tuple[ReviewUnresolvedReference, ...] = ()


class ReviewEvidencePane(KnowledgeModel):
    """Pane 3 -- Evidence and assessment, with its two absence states stated rather than implied.

    ``evidence_state`` and ``assessment_state`` are separate because they are separate facts: a
    corpus can hold evidence and no assessment, an assessment and no evidence, or neither. Neither
    state has a favourable member, so no rendering can turn an absence into a clearance.
    """

    evidence_state: Literal["recorded", "none_recorded"]
    assessment_state: Literal["assessed", "unassessed"]
    evidence_links: tuple[ReviewEvidenceLink, ...] = ()
    observations: tuple[ReviewObservation, ...] = ()
    assessments: tuple[ReviewAssessmentDisplay, ...] = ()
    source_inspection_available: bool
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
    """

    state: Literal["current", "stale"]
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    previous_comparison_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    moved: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _require_the_previous_input_to_be_labelled(self) -> ReviewStaleness:
        if self.state == "stale" and self.previous_comparison_ref is None:
            raise ValueError(
                "a stale comparison retains the comparison it is labelling as previous input"
            )
        if self.state == "current" and self.previous_comparison_ref is not None:
            raise ValueError("a current comparison has no previous input to label")
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
    """

    surface_version: str = Field(
        default=KNOWLEDGE_REVIEW_SURFACE_VERSION, min_length=1, max_length=LABEL_MAX_LENGTH
    )
    candidate: ReviewCandidateRef
    comparison: ComparisonIdentity
    knowledge: ReviewKnowledgePane
    source: ReviewSourcePane
    evidence: ReviewEvidencePane
    staleness: ReviewStaleness
    submission: ReviewSubmission
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
    fact about the datasets and is stated as one.
    """

    state: Literal["entries", "refused"]
    operation: Literal["list_knowledge_review_entries"] = "list_knowledge_review_entries"
    repository_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    master: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    entries: tuple[ReviewEntry, ...] = ()
    refusal: ReviewRefusal | None = None

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
