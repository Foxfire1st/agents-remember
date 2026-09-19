"""Currentness of one assessment's binding to the exact inputs it examined.

The rule this module exists for is narrow and is stated twice in the design, so it is stated once
here in code: validation **checks whether the recorded identities still match their current values; it
does not decide that changed dependencies are equivalent** (``design/retrieval-review-design.md:370``;
``Doc13:373``). A resolver, a relocation, or byte-identical content at a new location is never a proof
of equivalence, and this module therefore has no notion of "equivalent" to reach for -- it compares
``(algorithm, digest)`` pairs for equality and reports the ones that differ.

What a mismatch means is also fixed: the assessment is marked **stale** and stays readable. Its
finding, rationale, author, role and disposition are preserved as historical fact; only its
currentness changes (requirement 5.3). Nothing here deletes a record, re-points an old finding at new
inputs, or interprets the old judgment for the new inputs (``Doc13:371``).

**The currentness field mapping** (``## Open Truth Gaps``: which shipped field carries
``binding_state=stale``). The packet's own audit found no shipped ``binding_state`` identifier and
listed three near misses. **That audit was taken before ``KS-R14@v1`` (L14) landed on this branch,
and L14 has since shipped the exact vocabulary**: ``DetectionRunCurrentness.binding_state`` is a
``Literal["current", "stale"]`` at :mod:`agents_remember.models.knowledge.detection`, and its
validator refuses a state that does not follow from its own version comparison. This leaf therefore
does not invent a third spelling. It maps the design's ``binding_state=stale`` onto
:attr:`AssessmentCurrentness.binding_state`, a field with **the same name and the same two-member
literal** as the shipped detection precedent. The two are separate fields on separate records -- a
detection run's currentness compares two version axes, an assessment's compares a whole declared
input set -- but a reader who has met one spelling has met both, and neither is free-form text.

Why not the two other candidates the packet names. ``currentnessStatus`` on
``CuratorCoherenceResponse`` is the *coherence authority's own* status and is free-form ``str``;
reusing it for one assessment would make two different facts share one spelling and would let a
mismatch be spelled as anything. ``bindingState`` on the task-leaf binding payload
(``worktrees/task_leaf_binding.py``) is a *plan's* state, not a record's currentness. The mapping is
recorded in the leaf's turn report and in the attempt journal, as the packet's gap asks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from agents_remember.models.lifecycles.evidence_dependencies import (
    EvidenceDependencies,
    require_evidence_dependencies,
)
from agents_remember.models.lifecycles.review_assessment import (
    ReviewAssessment,
    SubjectAssessmentState,
    assessment_state_for,
)

# The same two-member vocabulary the shipped ``DetectionRunCurrentness.binding_state`` uses. Named
# here rather than imported from the knowledge-detection module because the two records live in
# different halves of the tree and neither owns the other's status; the *spelling* is shared
# deliberately, which is what the mapping in the module docstring records.
CurrentnessStatus = Literal["current", "stale"]

# The one edge kind excluded from a staleness comparison, and the reason is structural rather than
# convenient. Every other kind binds an *input*; ``review-record`` names another content-addressed
# record, and the coherence record's own edge to this assessment is exactly such an edge. Comparing
# it here would ask an assessment to declare the digest of the record it is stored inside, which is
# the self-invalidating sequence ``design/retrieval-review-design.md:368`` forbids -- and the
# existing curator-coherence record already carries that edge from the other side.
SELF_REFERENTIAL_KINDS: frozenset[str] = frozenset({"review-record"})


@dataclass(frozen=True)
class BindingGap:
    """One recorded identity that no longer matches its current value."""

    kind: str
    name: str
    recordedDigest: str | None
    currentDigest: str | None

    @property
    def identity(self) -> str:
        return f"{self.kind}:{self.name}"

    @property
    def observed(self) -> str:
        """Return the current state in the shape a refusal reports."""

        return self.currentDigest if self.currentDigest is not None else "unreadable-or-absent"

    @property
    def expected(self) -> str:
        """Return the recorded state in the shape a refusal reports."""

        return self.recordedDigest if self.recordedDigest is not None else "not-recorded"


@dataclass(frozen=True)
class AssessmentCurrentness:
    """One assessment's binding state and, when it is stale, every identity that moved.

    ``binding_state`` carries the design's own ``binding_state=stale`` vocabulary under the same
    spelling the shipped detection record uses, so the mapping the packet's Open Truth Gap 1 asks for
    is a field a reader can find rather than a convention they have to be told about.
    """

    assessmentId: str
    binding_state: CurrentnessStatus
    gaps: tuple[BindingGap, ...] = ()

    @property
    def is_stale(self) -> bool:
        return self.binding_state == "stale"


def disputed_dependencies(
    assessment: ReviewAssessment,
    current: Mapping[tuple[str, str], tuple[str, str]],
) -> tuple[BindingGap, ...]:
    """Compare one assessment's recorded identities against their current values.

    ``current`` maps ``(kind, name) -> (algorithm, digest)`` and is *the caller's measurement of the
    current world*: this function never reads a store, a tree or a file, so the comparison is
    reproducible and a caller cannot accidentally ask it to re-derive what it is checking. A
    recorded identity with no current value is a mismatch, not a pass -- an input that can no longer
    be read is not an input that still matches, and reporting it as current would be exactly the
    silent inheritance requirement 5.5 forbids.
    """

    gaps: list[BindingGap] = []
    for edge in assessment.examinedInputs.declaration.edges:
        if edge.kind in SELF_REFERENTIAL_KINDS:
            continue
        observed = current.get(edge.identity)
        if observed is not None and observed == (edge.algorithm, edge.digest):
            continue
        gaps.append(
            BindingGap(
                kind=edge.kind,
                name=edge.name,
                recordedDigest=edge.digest,
                currentDigest=None if observed is None else observed[1],
            )
        )
    return tuple(gaps)


def assessment_currentness(
    assessment: ReviewAssessment,
    current: Mapping[tuple[str, str], tuple[str, str]],
) -> AssessmentCurrentness:
    """Report one assessment's currentness without raising.

    This is the read half of the rule and it is deliberately total: a stale assessment is a *state*
    to be reported, not an error to be raised, because requirement 5.4 keeps it visible. The
    refusing half, for callers that may only proceed against current inputs, is
    :func:`require_current_assessment_binding`.
    """

    gaps = disputed_dependencies(assessment, current)
    return AssessmentCurrentness(
        assessmentId=assessment.assessmentId,
        binding_state="stale" if gaps else "current",
        gaps=gaps,
    )


def require_current_assessment_binding(
    assessment: ReviewAssessment,
    current: Mapping[tuple[str, str], tuple[str, str]],
    *,
    operation: str,
) -> AssessmentCurrentness:
    """Refuse to use one assessment against inputs it was not authored over.

    The refusal carries the recorded digest, the observed state and the exact identity for every
    moved input, so a caller is told *which* dependency changed rather than only that one did. It
    never repairs the binding and never re-points the judgment: the recovery is a new authored
    assessment over the new inputs (``design/retrieval-review-design.md:370``).
    """

    currentness = assessment_currentness(assessment, current)
    if currentness.is_stale:
        first = currentness.gaps[0]
        raise AssessmentBindingStaleError(
            "review-assessment-binding-stale",
            f"{operation} cannot use assessment {assessment.assessmentId}: its recorded binding "
            f"does not match the current inputs ({first.identity})",
            assessment_id=assessment.assessmentId,
            operation=operation,
            gaps=currentness.gaps,
        )
    return currentness


def subject_state(
    assessments: Sequence[ReviewAssessment],
    *,
    current: Mapping[str, Mapping[tuple[str, str], tuple[str, str]]],
) -> SubjectAssessmentState:
    """Project every stored assessment of one subject onto the distinct reportable states.

    ``current`` is keyed by assessment id so a subject with several assessments over different
    comparisons reports each one's own currentness rather than a single verdict inherited from a
    sibling -- requirement 5.5's per-examined-item rule, in the one place a projection could break it.
    An assessment the caller supplied no current values for is reported ``stale`` rather than
    ``current``: "not measured" is not "still matches".
    """

    stale_ids = [
        assessment.assessmentId
        for assessment in assessments
        if assessment_currentness(assessment, current.get(assessment.assessmentId, {})).is_stale
    ]
    return assessment_state_for(assessments, stale_ids=stale_ids)


class AssessmentBindingStaleError(ValueError):
    """One assessment's recorded binding no longer matches the inputs it would be used against."""

    def __init__(
        self,
        status: str,
        detail: str,
        *,
        assessment_id: str,
        operation: str,
        gaps: Sequence[BindingGap],
    ) -> None:
        self.status = status
        self.detail = detail
        self.assessmentId = assessment_id
        self.operation = operation
        self.gaps = tuple(gaps)
        super().__init__(f"{status}: {detail}")

    def response_fields(self) -> dict[str, object]:
        """Return the typed refusal payload a wire response carries."""

        return {
            "status": self.status,
            "detail": self.detail,
            "assessmentId": self.assessmentId,
            "staleBindings": [
                {
                    "kind": gap.kind,
                    "name": gap.name,
                    "expected": gap.expected,
                    "observed": gap.observed,
                }
                for gap in self.gaps
            ],
        }


def require_assessment_dependencies(assessment: ReviewAssessment) -> EvidenceDependencies:
    """Require one assessment's declaration to satisfy the policy that owns its record type.

    A raise here is the structural refusal requirement 1.3 asks for: the caller gets an exact code
    and no row is written. It is separated from the model validator because a policy is the
    *contract's* rule about which kinds an assessment must declare, and the model is the record's
    rule about which facts it must carry; keeping them apart lets a policy change be a policy change.
    """

    return require_evidence_dependencies(
        assessment.examinedInputs.declaration,
        record_type="review-assessment/v1",
    )


__all__ = [
    "SELF_REFERENTIAL_KINDS",
    "AssessmentBindingStaleError",
    "AssessmentCurrentness",
    "BindingGap",
    "CurrentnessStatus",
    "assessment_currentness",
    "disputed_dependencies",
    "require_assessment_dependencies",
    "require_current_assessment_binding",
    "subject_state",
]
