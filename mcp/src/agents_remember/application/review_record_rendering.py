"""The record half of one review: what the renderer is *given*, rendered as pane values (ICR-R02).

The review adapter composes operations; this module renders the collections a caller supplied. It is
separate for the file-size rail's reason and for one of its own: the same rendering is used by the
subject review and by the task-context review, so a second copy of it would be the place where the
two surfaces come to disagree about what an unassessed subject looks like.

Nothing here selects a record, resolves a reference or decides an outcome. Every function takes the
typed value another owner published and returns the surface's own display value:

* an assessment collection is projected per subject with its currentness **as measured** -- a caller
  that supplied no ``current`` mapping gets every assessment reported ``stale``, because the shipped
  projection refuses to promote an unmeasured assessment and this module does not improve on it;
* an empty assessment collection is ``unassessed`` and an empty evidence collection is
  ``none_recorded``, and neither has a favourable member to default to;
* a detection signal is carried with its condition, inputs, versions and scope limitations, and there
  is no field in its display value for a voice, a severity or a disposition.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agents_remember.models.knowledge.detection import DetectionSignalPayload
from agents_remember.models.knowledge.evidence import VerificationObservationPayload
from agents_remember.models.knowledge.review import (
    PROPOSED_ASSESSMENT_DISPOSITIONS,
    KnowledgeReviewResult,
    ReviewAssessmentDisplay,
    ReviewEvidenceLink,
    ReviewEvidencePane,
    ReviewObservation,
    ReviewRecordChannel,
    ReviewRefusal,
    ReviewSignal,
    ReviewSubmission,
    ReviewUnresolvedReference,
)
from agents_remember.models.knowledge.view import ReviewMatrixRow
from agents_remember.models.lifecycles.review_assessment import (
    ReviewAssessment,
    SubjectAssessmentState,
    assessment_state_for,
)

__all__ = [
    "EMPTY_REVIEW_RECORDS",
    "ReviewClaimRecord",
    "ReviewRecordInputs",
    "assessment_displays",
    "evidence_pane",
    "observation",
    "refused",
    "signal",
    "subject_states",
    "submission",
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

    It is a *renderer input* and not a record: nothing here is derived, ranked or filtered.
    """

    claim_id: str
    author_ref: str | None = None
    lifecycle: str | None = None
    limitations: str = ""
    claimed_coverage: tuple[str, ...] = ()
    assessment_refs: tuple[str, ...] = ()


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
    """

    assessments: tuple[ReviewAssessment, ...] = ()
    current: Mapping[str, Mapping[tuple[str, str], tuple[str, str]]] | None = None
    signals: tuple[DetectionSignalPayload, ...] = ()
    observations: tuple[VerificationObservationPayload, ...] = ()
    claims: tuple[ReviewClaimRecord, ...] = ()
    channels: tuple[ReviewRecordChannel, ...] = ()


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
) -> ReviewEvidencePane:
    """Pane 3: evidence references, execution observations and the authored assessments."""

    claims = {claim.claim_id: claim for claim in records.claims}
    links = tuple(
        _evidence_link(row, claims.get(row.subject.record_id))
        for row in rows
        if row.subject.record_kind == "evidence_claim"
    )
    observations = tuple(observation(entry) for entry in records.observations)
    assessments = assessment_displays(records, subjects)
    return ReviewEvidencePane(
        evidence_state="recorded" if links or observations else "none_recorded",
        assessment_state="assessed" if assessments else "unassessed",
        evidence_links=links,
        observations=observations,
        assessments=assessments,
        source_inspection_available=True,
        channels=records.channels,
    )


def subject_states(records: ReviewRecordInputs) -> Mapping[str, SubjectAssessmentState]:
    """Every stored assessment projected per subject, with currentness left as measured.

    A caller that supplied no ``current`` measurement gets every assessment reported ``stale``:
    the shipped projection refuses to promote an unmeasured assessment to current, and this surface
    does not improve on that by guessing.
    """

    grouped: dict[str, list[ReviewAssessment]] = {}
    for assessment in records.assessments:
        grouped.setdefault(assessment.subject.recordId, []).append(assessment)
    states: dict[str, SubjectAssessmentState] = {}
    for subject_id, stored in grouped.items():
        stale_ids = (
            ()
            if records.current is not None
            else tuple(assessment.assessmentId for assessment in stored)
        )
        states[subject_id] = assessment_state_for(stored, stale_ids=stale_ids)
    return states


def assessment_displays(
    records: ReviewRecordInputs,
    subjects: Mapping[str, SubjectAssessmentState],
) -> tuple[ReviewAssessmentDisplay, ...]:
    """The authored assessments as displayed, each with its own binding status."""

    by_id = {assessment.assessmentId: assessment for assessment in records.assessments}
    displayed: list[ReviewAssessmentDisplay] = []
    seen: set[str] = set()
    for state in subjects.values():
        for entry in state.assessments:
            record = by_id.get(entry.assessmentId)
            if record is None or entry.assessmentId in seen:
                continue
            seen.add(entry.assessmentId)
            displayed.append(_assessment_display(record, entry.currentness))
    return tuple(displayed)


def _assessment_display(record: ReviewAssessment, currentness: str) -> ReviewAssessmentDisplay:
    """One stored assessment as the pane's own display value."""

    return ReviewAssessmentDisplay(
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


def _evidence_link(row: ReviewMatrixRow, claim: ReviewClaimRecord | None) -> ReviewEvidenceLink:
    """One matrix row's claim, with the claim's own recorded fields where its owner supplied them.

    A claim this composition supplied carries its author, lifecycle, declared limitations and asserted
    coverage verbatim. A claim whose record could not be read keeps its identity and its assessment
    references and names the missing half as unresolved -- with the owner that would supply it -- so
    an unread claim is never rendered as a claim with no limitations.
    """

    if claim is not None:
        return ReviewEvidenceLink(
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


def observation(entry: VerificationObservationPayload) -> ReviewObservation:
    """One verification observation displayed exactly, with its authored limitations."""

    artifact = entry.result_artifact
    return ReviewObservation(
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


def signal(entry: DetectionSignalPayload) -> ReviewSignal:
    """One detection fact, carried with its inputs, versions and scope limitations only."""

    return ReviewSignal(
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
