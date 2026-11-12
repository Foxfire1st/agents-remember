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

**A measurement is a value, and its absence is a state** (``ICR-R15@v1``).
:class:`AssessmentCurrentnessMeasurement` is what a caller *measured* -- the identities it read, the
identities the comparison publishes no value for, or the failure that stopped it -- and
:func:`measured_binding_status` turns that into the four states a projection may report:
``current`` (measured, fully covered, nothing moved), ``stale`` (a measured movement, whether or not
the rest was covered), ``not-measured`` (no completed measurement covered the whole declaration) and
``unavailable`` (the measurement failed). The distinction is *coverage*, not the presence of a
mapping: an empty mapping is a measurement that covers nothing and therefore marks nothing current,
and no measurement at all is not a staleness. :func:`assessment_currentness` below is unchanged and
stays the two-member equality answer for a caller that already has a measurement of the world.

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
from dataclasses import dataclass, field
from typing import Literal

from agents_remember.models.lifecycles.evidence_dependencies import (
    EvidenceDependencies,
    require_evidence_dependencies,
)
from agents_remember.models.lifecycles.review_assessment import (
    AssessmentBindingStatus,
    ReviewAssessment,
)

# The same two-member vocabulary the shipped ``DetectionRunCurrentness.binding_state`` uses. Named
# here rather than imported from the knowledge-detection module because the two records live in
# different halves of the tree and neither owns the other's status; the *spelling* is shared
# deliberately, which is what the mapping in the module docstring records.
CurrentnessStatus = Literal["current", "stale"]

# The three states a *measurement* can be in (``ICR-R15@v1``). ``measured`` means a world was read and
# ``values`` are what it holds; ``not-measured`` means no measurement of the world was supplied or
# produced; ``unavailable`` means one was attempted and failed. The three are separate because a
# missing measurement, a failed measurement and a measured world are three different facts, and a
# projection that could not tell them apart would have to spell one of them as another.
CurrentnessMeasurementState = Literal["measured", "not-measured", "unavailable"]

# The two sentences a projection states when it was handed no measurement at all: one for the state,
# and one for the already-measured mapping a caller supplied directly. They are stated once because
# every caller that reports an unmeasured record has to say what would measure it, and three copies
# of the sentence is how three surfaces come to describe one state differently.
NO_MEASUREMENT_DETAIL = (
    "no dependency-currentness measurement of this comparison was supplied, so nothing established "
    "whether these records still match the inputs they examined; an unmeasured binding is never "
    "reported current and never reported stale"
)
SUPPLIED_MEASUREMENT_DETAIL = (
    "the caller supplied its own measurement of the current world; each record's status is the "
    "shipped dependency comparison's answer over the identities that measurement covers"
)

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


@dataclass(frozen=True)
class AssessmentCurrentnessMeasurement:
    """One measured current world for one viewed comparison, or the state that says why there is none.

    ``values`` maps ``(kind, name) -> (algorithm, digest)`` and is *a measurement*: it holds a value
    exactly for the identities somebody read for this comparison. Its **keys are the coverage**,
    which is what makes a partial measurement answerable without inventing an answer -- an identity
    with no value here was not measured, and an assessment that declares it is ``not-measured``
    rather than either ``current`` (a currency nobody established) or ``stale`` (a movement nobody
    measured).

    ``unmeasured`` names the identities the comparison itself publishes no value for, with
    ``unmeasured_detail`` stating why, so a reader of an unmeasured binding can see which owner was
    never asked rather than only that something is missing. The state is separate from the values
    because "nobody measured this" and "the measurement failed" are different facts from "the world
    was read and holds these values" -- including when the world was read and holds none, which is a
    measured world that establishes nothing about a binding that declares something.
    """

    state: CurrentnessMeasurementState
    values: Mapping[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    detail: str = ""
    unmeasured: tuple[str, ...] = ()
    unmeasured_detail: str = ""


def no_currentness_measurement(
    detail: str,
    *,
    unmeasured: Sequence[str] = (),
    unmeasured_detail: str = "",
) -> AssessmentCurrentnessMeasurement:
    """Return the measurement nothing produced, with what would produce one stated in ``detail``."""

    return AssessmentCurrentnessMeasurement(
        state="not-measured",
        detail=detail,
        unmeasured=tuple(unmeasured),
        unmeasured_detail=unmeasured_detail,
    )


def measured_currentness(
    values: Mapping[tuple[str, str], tuple[str, str]],
    *,
    detail: str,
    unmeasured: Sequence[str] = (),
    unmeasured_detail: str = "",
) -> AssessmentCurrentnessMeasurement:
    """Return one measured world: the values that were read, and the identities nothing read."""

    return AssessmentCurrentnessMeasurement(
        state="measured",
        values=dict(values),
        detail=detail,
        unmeasured=tuple(unmeasured),
        unmeasured_detail=unmeasured_detail,
    )


def unmeasured_identities(
    assessment: ReviewAssessment, measurement: AssessmentCurrentnessMeasurement
) -> tuple[str, ...]:
    """Return the declared identities one measurement holds no value for, as ``kind:name`` spellings.

    The measurement's keys are its coverage, so this is derivable rather than declared twice: an
    identity is unmeasured exactly when the world it was measured against holds no value for it. The
    spellings travel in declaration order and never deduplicate two distinct declarations apart,
    because a reader has to see every input nothing checked.
    """

    return tuple(
        gap.identity
        for gap in disputed_dependencies(assessment, measurement.values)
        if (gap.kind, gap.name) not in measurement.values
    )


def measured_binding_status(
    assessment: ReviewAssessment,
    measurement: AssessmentCurrentnessMeasurement,
) -> AssessmentBindingStatus:
    """Report one assessment's binding status under one measurement, in the four distinct states.

    The equality decision is the shipped comparison's -- :func:`disputed_dependencies` over the
    measured values -- and this function adds only the two facts a projection owes: whether the
    measurement *completed*, and whether it covered the whole declaration. So:

    * a failed measurement reports ``unavailable``, because nothing was established;
    * a measured disagreement reports ``stale``, naming the moved axes through the comparison's own
      gaps (a measured movement is a fact whether or not the rest of the declaration was covered);
    * a completed measurement that covered every declared identity and disagreed nowhere reports
      ``current``;
    * everything else reports ``not-measured`` -- including the empty measurement, which covers none
      of the declaration and therefore establishes neither currency nor movement.

    Presence of a mapping is never the decision here: an empty one is measured and covers nothing.
    """

    if measurement.state == "unavailable":
        return "unavailable"
    if measurement.state == "not-measured":
        return "not-measured"
    if any(
        (gap.kind, gap.name) in measurement.values
        for gap in disputed_dependencies(assessment, measurement.values)
    ):
        return "stale"
    if unmeasured_identities(assessment, measurement):
        return "not-measured"
    return "current"


def measured_binding_statuses(
    assessments: Sequence[ReviewAssessment],
    measurement: AssessmentCurrentnessMeasurement | None,
) -> dict[str, AssessmentBindingStatus]:
    """Report every assessment's binding status under one measurement, keyed by assessment identity.

    ``None`` is no measurement at all -- a bundle a caller assembled without measuring anything --
    and every record then reports ``not-measured``. Every stored assessment gets an entry, so a
    caller cannot read a missing key as an answer. This is the shape the *composition* uses, where
    one comparison publishes one world and every stored binding is measured against it.
    """

    if measurement is None:
        measurement = no_currentness_measurement(NO_MEASUREMENT_DETAIL)
    return {
        assessment.assessmentId: measured_binding_status(assessment, measurement)
        for assessment in assessments
    }


def supplied_measurement_statuses(
    assessments: Sequence[ReviewAssessment],
    current: Mapping[str, Mapping[tuple[str, str], tuple[str, str]]] | None,
) -> dict[str, AssessmentBindingStatus]:
    """Report every assessment's status from a caller's *per-record* measurement of the world.

    This is the shape a caller uses when it measured each record's declared inputs itself:
    ``assessmentId -> (kind, name) -> (algorithm, digest)``. ``None`` means the caller measured
    nothing, and an assessment the caller supplied no values for was not measured -- reported
    ``not-measured`` rather than ``stale``, because a record nobody measured did not move. Each
    record is classified against its own values, which is requirement 5.5's per-examined-item rule at
    the level of the measurement: one record's measurement never decides another's state.
    """

    if current is None:
        return measured_binding_statuses(
            assessments, no_currentness_measurement(NO_MEASUREMENT_DETAIL)
        )
    return {
        assessment.assessmentId: measured_binding_status(
            assessment,
            measured_currentness(
                current.get(assessment.assessmentId, {}), detail=SUPPLIED_MEASUREMENT_DETAIL
            ),
        )
        for assessment in assessments
    }


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
    "NO_MEASUREMENT_DETAIL",
    "SELF_REFERENTIAL_KINDS",
    "SUPPLIED_MEASUREMENT_DETAIL",
    "AssessmentBindingStaleError",
    "AssessmentCurrentness",
    "AssessmentCurrentnessMeasurement",
    "BindingGap",
    "CurrentnessMeasurementState",
    "CurrentnessStatus",
    "assessment_currentness",
    "disputed_dependencies",
    "measured_binding_status",
    "measured_binding_statuses",
    "measured_currentness",
    "no_currentness_measurement",
    "require_assessment_dependencies",
    "require_current_assessment_binding",
    "supplied_measurement_statuses",
    "unmeasured_identities",
]
