"""The record half of one review: what the renderer is *given*, rendered as pane values (ICR-R02).

The review adapter composes operations; this module renders the collections a caller supplied. It is
separate for the file-size rail's reason and for one of its own: the same rendering is used by the
subject review and by the task-context review, so a second copy of it would be the place where the
two surfaces come to disagree about what an unassessed subject looks like.

Nothing here selects a record, resolves a reference or decides an outcome. Every function takes the
typed value another owner published and returns the surface's own display value:

* an assessment collection is projected per subject with its currentness **as measured**: the
  shipped comparison decides each binding over the identities the supplied measurement covers, and a
  binding it does not cover is reported ``not-measured`` -- neither ``current`` (a currency nobody
  established) nor ``stale`` (a movement nobody measured);
* an empty assessment collection is ``unassessed`` and an empty evidence collection is
  ``none_recorded``, and neither has a favourable member to default to;
* a detection signal is carried with its condition, inputs, versions and scope limitations, and there
  is no field in its display value for a voice, a severity or a disposition.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from agents_remember.application.review_comparison_generation import ComparisonArtifactReference
from agents_remember.models.knowledge.detection import DetectionSignalPayload
from agents_remember.models.knowledge.evidence import VerificationObservationPayload
from agents_remember.models.knowledge.review import (
    PROPOSED_ASSESSMENT_DISPOSITIONS,
    KnowledgeReviewResult,
    ReviewAssessmentDisplay,
    ReviewAuthoredEffect,
    ReviewEvidenceLink,
    ReviewEvidencePane,
    ReviewObservation,
    ReviewRecordChannel,
    ReviewRefusal,
    ReviewSignal,
    ReviewSubmission,
    ReviewUnresolvedReference,
)
from agents_remember.models.knowledge.review_applicability import (
    ReviewApplicabilityClass,
    ReviewApplicabilitySummary,
    ReviewContextRecord,
    ReviewDisplayedApplicability,
)
from agents_remember.models.knowledge.view import ReviewMatrixRow
from agents_remember.models.lifecycles.review_assessment import (
    ReviewAssessment,
    SubjectAssessmentState,
    assessment_state_for,
)
from agents_remember.models.lifecycles.review_assessment_binding import (
    AssessmentCurrentnessMeasurement,
    measured_binding_statuses,
)

__all__ = [
    "EMPTY_REVIEW_RECORDS",
    "EVIDENCE_APPLICABILITY_CLASSES",
    "KNOWLEDGE_APPLICABILITY_CLASSES",
    "ReviewClaimRecord",
    "ReviewRecordInputs",
    "assessment_displays",
    "authored_effects",
    "evidence_pane",
    "observation",
    "refused",
    "signal",
    "subject_states",
    "submission",
    "unresolved_authors",
]


@dataclass(frozen=True)
class ReviewClaimRecord:
    """One evidence claim's own recorded fields, as the evidence owner serves them.

    The claim's *identity* already reaches the surface through the review matrix's row, which names
    which claims a selection reaches. What the row deliberately does not do is restate the claim's
    content -- the view refuses to become a second renderer of a record group it does not own -- so
    the authored fields travel here instead, read from the claim's own owner: the author the
    admission recorded, the lifecycle the record was written under, the limitations the author
    declared (``""`` is that declaration, verbatim, and never "unknown") and the coverage endpoints
    the author asserted.

    ``subject_revision_id`` is the claim's own recorded **subject** -- the exact invariant revision
    the claim is about, read from the owner's own subject edge (``ICR-R26@v1``'s extension of this
    bundle, so the review can attribute a claim to a subject instead of displaying it unattributed).
    It is absent exactly when the claim records a facet-revision subject or none at all, and an
    absent subject is never filled with the candidate's or the selection's identity.

    It is a *renderer input* and not a record: nothing here is derived, ranked or filtered.
    """

    claim_id: str
    author_ref: str | None = None
    lifecycle: str | None = None
    limitations: str = ""
    claimed_coverage: tuple[str, ...] = ()
    assessment_refs: tuple[str, ...] = ()
    subject_revision_id: str | None = None


@dataclass(frozen=True)
class ReviewRecordInputs:
    """The records the renderer is *given*, rather than records it goes and selects for itself.

    Each collection belongs to another owner's read path, and the surface renders what it is handed.
    An empty collection is a stated absence and never a fabricated positive: no assessments means
    every subject is displayed ``unassessed``, and no observations means the evidence pane carries
    none -- neither is defaulted to a clearance.

    ``channels`` is the availability fact that goes with the collections: one entry per record class
    the composition read, saying whether its owner answered (and with what count), whether expected
    content could not be read, or whether the collection was not read at all. It is what keeps an
    empty tuple from standing for three different facts, and it is deliberately *not* derived from
    the collection lengths here -- an empty collection is exactly what this module must be able to
    render without claiming which of the three it is.

    ``currentness`` is the *measurement* the composition produced for the comparison being viewed,
    not a mapping whose presence stands for one: the shipped comparison decides each binding's state
    over the identities the measurement covers, and a binding the measurement does not cover is
    reported ``not-measured`` rather than current or stale (``ICR-R15@v1``). ``None`` is a bundle
    nobody measured, and it is a state of its own.

    ``artifacts`` carries validated immutable owner references for comparison retention. It holds no
    duplicate assessment content; the typed curator generation remains that content's owner.
    """

    assessments: tuple[ReviewAssessment, ...] = ()
    currentness: AssessmentCurrentnessMeasurement | None = None
    signals: tuple[DetectionSignalPayload, ...] = ()
    observations: tuple[VerificationObservationPayload, ...] = ()
    claims: tuple[ReviewClaimRecord, ...] = ()
    channels: tuple[ReviewRecordChannel, ...] = ()
    artifacts: tuple[ComparisonArtifactReference, ...] = ()


# Which supplied collections each pane displays as its own, in the order the pane lists them. The
# knowledge pane shows the assessments, the detection signals and the matrix's authored effects; the
# evidence pane shows the assessments, the execution observations and the matrix's evidence claims.
# Declared once so a pane's channels, its labelled values and its counts describe one population.
KNOWLEDGE_APPLICABILITY_CLASSES: tuple[ReviewApplicabilityClass, ...] = (
    "assessments",
    "detection_signals",
    "authored_effects",
)
EVIDENCE_APPLICABILITY_CLASSES: tuple[ReviewApplicabilityClass, ...] = (
    "assessments",
    "verification_observations",
    "evidence_claims",
)


class ReviewApplicabilityProjection(Protocol):
    """What a pane needs from the applicability projection, stated as the port it consumes.

    The projection itself is :mod:`agents_remember.application.review_record_applicability`, which
    reads the comparison, the recorded relationships and the known subjects -- all things this
    renderer must not reach. The port is what keeps the dependency one-way: this module renders the
    values it is handed, and the projection is the module that decided which values those are.
    """

    def label_of(self, record_id: str) -> ReviewDisplayedApplicability | None:
        """The treatment one supplied record earned, or ``None`` when it is not displayed here."""
        ...

    def context_of(
        self, classes: Sequence[ReviewApplicabilityClass]
    ) -> tuple[ReviewContextRecord, ...]:
        """The labelled context rows of the named collections, in recorded order."""
        ...

    def summaries_of(
        self, classes: Sequence[ReviewApplicabilityClass]
    ) -> tuple[ReviewApplicabilitySummary, ...]:
        """The six-way counts of the named collections, in the order given."""
        ...


# The empty record set, as one module-level value: a call in an argument default would rebuild it on
# every call, and the collections it holds are immutable tuples.
EMPTY_REVIEW_RECORDS = ReviewRecordInputs()


def refused(repository_id: str, refusal_value: ReviewRefusal) -> KnowledgeReviewResult:
    """One refused review read, carrying its named refusal and no payload."""

    return KnowledgeReviewResult(
        state="refused", repository_id=repository_id, refusal=refusal_value
    )


def submission(stale: bool) -> ReviewSubmission:
    """Whether an assessment may be submitted, and through what.

    This increment ships no serving route that publishes an assessment, so the surface is
    display-only and says so. It grows no private write path to compensate: the published
    dispositions are the existing authority's own vocabulary, and the next action names that
    authority rather than a control this surface invented.
    """

    if stale:
        return ReviewSubmission(
            state="disabled_stale",
            reason="Candidate changed — open a new comparison",
            next_action="open a new comparison against the candidate's current inputs",
            proposed_dispositions=PROPOSED_ASSESSMENT_DISPOSITIONS,
        )
    return ReviewSubmission(
        state="unavailable",
        reason=(
            "this increment mounts no serving route that publishes an assessment, so the surface "
            "displays only and does not grow a private write path to compensate"
        ),
        next_action=(
            "publish an assessment through the existing curator authority's publication action, "
            "which supplies the author, the role and the authority provenance"
        ),
        proposed_dispositions=PROPOSED_ASSESSMENT_DISPOSITIONS,
    )


def evidence_pane(
    rows: Sequence[ReviewMatrixRow],
    records: ReviewRecordInputs,
    subjects: Mapping[str, SubjectAssessmentState],
    applicability: ReviewApplicabilityProjection,
) -> ReviewEvidencePane:
    """Pane 3: evidence references, execution observations and the authored assessments.

    ``rows`` are only the rows the applicability projection kept for this subject, and every value
    rendered here carries the label its own recorded binding earned (``ICR-R26@v1``): a record of
    another subject is not in these collections at all, and the pane states the counts -- including
    the records it does not display as this subject's judgments -- beside them.
    """

    claims = {claim.claim_id: claim for claim in records.claims}
    links = tuple(
        _evidence_link(
            row, claims.get(row.subject.record_id), applicability.label_of(row.subject.record_id)
        )
        for row in rows
        if row.subject.record_kind == "evidence_claim"
        and applicability.label_of(row.subject.record_id) is not None
    )
    observations = tuple(
        observation(entry, applicability.label_of(entry.command_name))
        for entry in records.observations
        if applicability.label_of(entry.command_name) is not None
    )
    assessments = assessment_displays(records, subjects, applicability)
    return ReviewEvidencePane(
        evidence_state="recorded" if links or observations else "none_recorded",
        assessment_state="assessed" if assessments else "unassessed",
        evidence_links=links,
        observations=observations,
        assessments=assessments,
        source_inspection_available=True,
        channels=records.channels,
        context=applicability.context_of(EVIDENCE_APPLICABILITY_CLASSES),
        applicability=applicability.summaries_of(EVIDENCE_APPLICABILITY_CLASSES),
    )


def subject_states(records: ReviewRecordInputs) -> Mapping[str, SubjectAssessmentState]:
    """Every stored assessment projected per subject, with currentness left as measured.

    ``records.currentness`` is the measurement the composing read produced for the comparison being
    viewed; the shipped comparison decides each binding's state over the identities that measurement
    covers, and a binding it does not cover is reported ``not-measured``. So this surface neither
    promotes an unmeasured assessment to current nor demotes one to stale: a bundle nobody measured
    reports every record unmeasured, and an empty measurement covers nothing and is therefore not a
    measurement of anything.
    """

    statuses = measured_binding_statuses(records.assessments, records.currentness)
    grouped: dict[str, list[ReviewAssessment]] = {}
    for assessment in records.assessments:
        grouped.setdefault(assessment.subject.recordId, []).append(assessment)
    states: dict[str, SubjectAssessmentState] = {}
    for subject_id, stored in grouped.items():
        states[subject_id] = assessment_state_for(
            stored,
            statuses={
                assessment.assessmentId: statuses[assessment.assessmentId] for assessment in stored
            },
        )
    return states


def assessment_displays(
    records: ReviewRecordInputs,
    subjects: Mapping[str, SubjectAssessmentState],
    applicability: ReviewApplicabilityProjection,
) -> tuple[ReviewAssessmentDisplay, ...]:
    """The assessments this subject may be judged by, each with its binding status and its label.

    Only records the applicability projection kept are rendered: an assessment whose recorded subject
    is another identity is either labelled context or not displayed as this subject's judgment at
    all, and it never reaches this collection (``ICR-R26@v1``'s correction of F09).
    """

    by_id = {assessment.assessmentId: assessment for assessment in records.assessments}
    displayed: list[ReviewAssessmentDisplay] = []
    seen: set[str] = set()
    for state in subjects.values():
        for entry in state.assessments:
            record = by_id.get(entry.assessmentId)
            if record is None or entry.assessmentId in seen:
                continue
            label = applicability.label_of(entry.assessmentId)
            if label is None:
                continue
            seen.add(entry.assessmentId)
            displayed.append(_assessment_display(record, entry.currentness, label))
    return tuple(displayed)


def _assessment_display(
    record: ReviewAssessment, currentness: str, applicability: ReviewDisplayedApplicability
) -> ReviewAssessmentDisplay:
    """One stored assessment as the pane's own display value, with why it may be displayed here."""

    return ReviewAssessmentDisplay(
        applicability=applicability,
        assessment_id=record.assessmentId,
        disposition=record.disposition,
        finding=record.finding or record.rationale,
        rationale=record.rationale,
        author_ref=record.provenance.authorRef,
        role_ref=record.provenance.authorRole,
        examined_inputs=tuple(f"{kind}:{name}" for kind, name in record.examinedInputs.identities)
        or (record.comparisonRef,),
        binding_state=currentness,
        evidence_refs=tuple(reference.spelling for reference in record.evidenceRefs),
    )


def _evidence_link(
    row: ReviewMatrixRow,
    claim: ReviewClaimRecord | None,
    applicability: ReviewDisplayedApplicability | None,
) -> ReviewEvidenceLink:
    """One matrix row's claim, with the claim's own recorded fields where its owner supplied them.

    A claim this composition supplied carries its author, lifecycle, declared limitations and asserted
    coverage verbatim. A claim whose record could not be read keeps its identity and its assessment
    references and names the missing half as unresolved -- with the owner that would supply it -- so
    an unread claim is never rendered as a claim with no limitations.
    """

    if claim is not None:
        return ReviewEvidenceLink(
            applicability=applicability,
            claim_id=row.subject.record_id,
            revision_id=row.subject.revision_id,
            claimed_coverage=claim.claimed_coverage,
            # The authored field verbatim: an empty member is the author's own "no limitations
            # declared", which is a recorded fact and not an unknown.
            limitations=(claim.limitations,),
            author_ref=claim.author_ref,
            assessment_refs=row.assessment_ids,
            lifecycle=claim.lifecycle,
        )
    return ReviewEvidenceLink(
        applicability=applicability,
        claim_id=row.subject.record_id,
        revision_id=row.subject.revision_id,
        assessment_refs=row.assessment_ids,
        unresolved=(
            ReviewUnresolvedReference(
                field="claim_content",
                recorded_reference=row.subject.record_id,
                detail=(
                    "the review matrix publishes this claim's identity, lifecycle and assessment "
                    "references, and the evidence owner's own read of this claim supplied no "
                    "content for it, so its coverage, limitations and author are displayed as "
                    "unresolved rather than reported as absent"
                ),
            ),
        ),
    )


def authored_effects(
    rows: Sequence[ReviewMatrixRow],
    applicability: ReviewApplicabilityProjection,
) -> tuple[ReviewAuthoredEffect, ...]:
    """The matrix's authored effects, preservation claims and open questions, as recorded.

    Each carries the label its own recorded references earned: a row whose references name the
    selected subject's revisions or relationships is the subject's own record, and a row whose
    references name none of them is displayed with those references and the reason it could not be
    attributed (``ICR-R26@v1``) -- never as a judgment on the selected subject.
    """

    return tuple(
        _authored_effect(row, applicability.label_of(row.subject.record_id))
        for row in rows
        if applicability.label_of(row.subject.record_id) is not None
    )


def unresolved_authors(rows: Sequence[ReviewMatrixRow]) -> tuple[ReviewUnresolvedReference, ...]:
    """One unresolved author per displayed row, because the matrix publishes no author for them.

    The matrix view's own contract is that it publishes a record's identity and classification and
    not its author, so the attribution is displayed as unresolved rather than rendered anonymously
    or filled with the current actor. It is a fact about what the owner served, and it stays beside
    the row it is about.
    """

    return tuple(_unresolved_author(row) for row in rows)


def _authored_effect(
    row: ReviewMatrixRow, applicability: ReviewDisplayedApplicability | None
) -> ReviewAuthoredEffect:
    """One authored effect, preservation claim or unresolved question, as recorded."""

    return ReviewAuthoredEffect(
        applicability=applicability,
        record_kind=row.subject.record_kind,  # type: ignore[arg-type]
        record_id=row.subject.record_id,
        revision_id=row.subject.revision_id,
        label=row.provenance.provenance_class,
        rationale=None if row.consequence is None else row.consequence.detail,
        author_ref=None,
        examined_inputs=tuple(row.record_ids),
    )


def _unresolved_author(row: ReviewMatrixRow) -> ReviewUnresolvedReference:
    return ReviewUnresolvedReference(
        field="author",
        recorded_reference=row.subject.record_id,
        detail=(
            "the review matrix publishes this record's identity and classification but no author "
            "for it, so the attribution is displayed as unresolved rather than rendered "
            "anonymously or filled with the current actor"
        ),
    )


def observation(
    entry: VerificationObservationPayload,
    applicability: ReviewDisplayedApplicability | None,
) -> ReviewObservation:
    """One verification observation displayed exactly, with its authored limitations."""

    artifact = entry.result_artifact
    return ReviewObservation(
        applicability=applicability,
        observation_id=entry.command_name,
        tested_candidate=(
            None if entry.knowledge_candidate is None else entry.knowledge_candidate.logical_digest
        ),
        command_identity=entry.command_identity,
        result_artifact_ref=None if artifact is None else artifact.path,
        result_artifact_digest=None if artifact is None else artifact.sha256,
        execution_result=entry.execution_result,
        environment_identity=f"{entry.environment.host}/{entry.environment.interpreter}",
        # A verification observation carries no authored limitation field of its own; the pane
        # therefore adds none rather than inventing one, and the surface's own limitation list
        # states that no sufficiency claim is made from a result.
        limitations=(),
    )


def signal(
    entry: DetectionSignalPayload, applicability: ReviewDisplayedApplicability | None
) -> ReviewSignal:
    """One detection fact, carried with its inputs, versions and scope limitations only."""

    return ReviewSignal(
        applicability=applicability,
        signal_id=entry.signal_id,
        condition=entry.condition,
        input_set=entry.input_set.declared,
        detected_at=entry.governing_route_id,
        relationship_paths=tuple(
            f"{path.path_id}:{path.reached_item_id}" for path in entry.relationship_paths
        ),
        extractor_version=entry.extractor_version,
        policy_version=entry.policy_version,
        scope_limitations=tuple(entry.limitations),
    )
