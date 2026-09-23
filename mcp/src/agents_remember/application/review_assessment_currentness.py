"""Measured assessment currentness against the comparison a review is viewing (``ICR-R15@v1``).

An assessment records the *exact* inputs it examined, and the shipped dependency comparison
(:mod:`agents_remember.models.lifecycles.review_assessment_binding`) decides whether a recorded
identity still matches the value measured for it. What that comparison cannot decide -- because it is
handed a measurement, not a store -- is *whether anybody measured anything*, and the review surface
used to answer that question from the presence of a mapping: an absent measurement rendered every
stored assessment ``stale``, and an empty mapping rendered every one of them ``current``. Both are
facts the store never held.

This module owns the missing half for the review read: **one measurement of the identities the viewed
comparison itself publishes**, produced from the comparison's own resolution, plus the availability
statement that goes with it. It answers three questions and invents none:

* :func:`comparison_currentness_measurement` reads the identities the resolution binds -- the
  comparison's two code endpoints, the scope-manifest and comparison references it records, and the
  validator versions the shipped assessment publication declares -- and reports them as a
  :class:`~agents_remember.models.lifecycles.review_assessment_binding.AssessmentCurrentnessMeasurement`.
  Identities this comparison publishes no value for are *not* in the measurement: the projection then
  reports the bindings that declare them ``not-measured``, rather than reading silence as agreement.
* the recorded generation is the one the resolution bound, so a review reopened from a leaf's durable
  record measures its assessments against **that** generation's endpoints, and a live review measures
  them against the candidate captured now. Selecting which of the two is being viewed is the
  resolution's own explicit choice (``history == "recorded"``, ICR-R12) and is never a fallback here.
* :func:`currentness_channel` states that measurement's availability on the ``assessment_currentness``
  channel. The three states a resolved candidate can earn are ``recorded`` (a measurement was made and
  bindings were compared against it), ``none_recorded`` (the authority answered and holds no
  assessment, so there was no binding to measure) and ``unavailable`` (the authority could not be
  read, so nothing was measurable).

**What this module does not do.** It does not re-run the curator-coherence observation: the pair
identity, the registered topology fingerprint, the task-intent digest and the memory candidate tree
are that observation's own inputs, and re-deriving them in a review read would be a second
implementation of an existing owner. They are reported as identities this comparison publishes no
value for, named per binding, which is the honest state and not a manufactured one. It authors
nothing, approves nothing, and never repairs a binding: a moved input is a fact to report, and the
recovery is a new authored assessment through the existing publication path.
"""

from __future__ import annotations

from collections.abc import Sequence

from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.models.knowledge.review import ReviewRecordChannel
from agents_remember.models.lifecycles.evidence_dependencies import canonical_sha256
from agents_remember.models.lifecycles.review_assessment import ReviewAssessment
from agents_remember.models.lifecycles.review_assessment_binding import (
    AssessmentCurrentnessMeasurement,
    measured_binding_statuses,
    measured_currentness,
    unmeasured_identities,
)
from agents_remember.worktrees.integration.closeout.curator_coherence_publication import (
    ASSESSMENT_POLICY_VERSION,
    ASSESSMENT_RESOLVER_VERSION,
)

__all__ = [
    "CURRENTNESS_OWNER",
    "comparison_currentness_measurement",
    "currentness_channel",
]

# The owner whose read answers for this collection. The measurement is this module's, and the
# equality decision inside it is the shipped comparison's, named here so a reader of the channel
# knows which code path answered rather than which one happened to run.
CURRENTNESS_OWNER = "review_assessment_currentness.comparison_currentness_measurement"

_REPAIR_ACTION = (
    "repair or republish the candidate's curator-coherence authority, then reopen the review; a "
    "failed measurement establishes neither currency nor movement"
)

# The identities this comparison publishes a value for, in the order the measurement reads them. Each
# is a declaration identity the shipped publication writes, so the names are the *same* names an
# assessment declares -- a second spelling here would compare two identities that never meet.
_SCOPE_MANIFEST = ("evidence-bytes", "scope-manifest")
_COMPARISON = ("evidence-bytes", "comparison")


def comparison_currentness_measurement(
    resolved: ReviewCandidateResolution,
) -> AssessmentCurrentnessMeasurement:
    """Measure the identities the viewed comparison publishes.

    ``resolved`` is the comparison's own resolution: the two code endpoints it bound, the leaf it
    names and the enclosure contract it read. Nothing else is read, so this measurement performs no
    I/O and cannot fail halfway, and its state is therefore **always** ``measured``: the two validator
    versions below are the shipped assessment publication's own constants, so a resolution that binds
    no endpoint at all still publishes those two identities. What varies between resolutions is *how
    many* identities the measurement holds -- both endpoints and the two recorded references for a
    resolved live comparison, fewer for a caller-assembled pair -- and an identity outside that list
    is reported ``not-measured`` by the projection rather than as agreement.
    """

    values: dict[tuple[str, str], tuple[str, str]] = {}
    for name, tree in (
        ("baseline", resolved.baseline_code_tree_id),
        ("candidate", resolved.candidate_code_tree_id),
    ):
        if tree is not None:
            values[("code-tree", name)] = ("git-object", tree)
    if resolved.leaf_id:
        # The scope manifest a review of this leaf records is the leaf identity itself, digested by
        # the same function the declaration used; measuring it is re-deriving one recorded input, not
        # guessing a value for it.
        values[_SCOPE_MANIFEST] = ("sha256", canonical_sha256(resolved.leaf_id))
    contract = resolved.contract
    if contract is not None:
        values[_COMPARISON] = ("sha256", canonical_sha256(contract.contract_path.as_posix()))
    for version in (ASSESSMENT_RESOLVER_VERSION, ASSESSMENT_POLICY_VERSION):
        values[("validator", version)] = ("sha256", canonical_sha256(version))
    return measured_currentness(
        values,
        detail=(
            "measured the identities this comparison publishes: "
            + ", ".join(sorted(f"{kind}:{name}" for kind, name in values))
            + "; every identity outside that list is one this comparison publishes no value for, and "
            "is reported unmeasured rather than as agreement"
        ),
    )


def currentness_channel(
    collection: ReviewRecordChannel,
    measurement: AssessmentCurrentnessMeasurement,
    assessments: Sequence[ReviewAssessment],
) -> ReviewRecordChannel:
    """State the currentness collection's availability from the measurement and the assessment read.

    Three facts, three sentences, and none of them is a favourable default -- these are the only
    states the ``assessment_currentness`` collection can be in for a resolved candidate:

    * the assessment authority could not be read, so nothing could be measured -- ``unavailable``,
      carrying that authority's own reason rather than a measurement this composition did not make;
    * the authority answered and records no assessment -- ``none_recorded`` with a real zero, because
      there was no binding to measure at all;
    * a measurement was performed -- ``recorded`` with the number of stored bindings it was compared
      against, and the declared identities it holds no value for named as ``unreadable`` so a partial
      measurement is stated rather than absorbed.

    ``not_measured`` is deliberately not among them: the composing read always measures a resolved
    candidate (:func:`comparison_currentness_measurement`), so a bundle nobody measured never reaches
    this function -- a bundle with no measurement carries no channels at all -- and rendering one here
    as ``recorded`` would state a comparison that never happened. That contract is asserted rather
    than guessed at, in the shipped style of an operation's own precondition.

    The count is the number of *bindings the measurement was compared against*, not a count of
    currency: an assessment whose declaration this comparison cannot cover is reported
    ``not-measured`` on its own display, and this channel states how many bindings were compared
    rather than how many matched.
    """

    if collection.state == "unavailable":
        return _unavailable(collection.detail, (), next_action=collection.next_action)
    if measurement.state == "unavailable":
        return _unavailable(measurement.detail, (), next_action=_REPAIR_ACTION)
    if collection.state == "none_recorded":
        return ReviewRecordChannel(
            records="assessment_currentness",
            state="none_recorded",
            owner=CURRENTNESS_OWNER,
            record_count=0,
            detail=(
                "this candidate records no assessment, so no binding existed to measure; a measured "
                "zero of assessments is not a measured binding"
            ),
        )
    assert measurement.state == "measured", (
        "the currentness channel states the measurement the composing read made, and that read "
        "always measures a resolved candidate; a measurement nobody performed has no channel here"
    )
    return ReviewRecordChannel(
        records="assessment_currentness",
        state="recorded",
        owner=CURRENTNESS_OWNER,
        record_count=collection.record_count,
        detail=_measured_detail(assessments, measurement),
        unreadable=_unmeasured_spellings(assessments, measurement),
    )


def _measured_detail(
    assessments: Sequence[ReviewAssessment], measurement: AssessmentCurrentnessMeasurement
) -> str:
    """One sentence stating what the measurement established, counted over the stored bindings."""

    statuses = measured_binding_statuses(assessments, measurement)
    counts = {
        state: sum(1 for value in statuses.values() if value == state)
        for state in ("current", "stale", "not-measured")
    }
    return (
        f"{measurement.detail}; {len(statuses)} stored binding(s) were compared against it "
        f"({counts['current']} measured current, {counts['stale']} measured stale, "
        f"{counts['not-measured']} not measured), and a binding counted not measured is one whose "
        "declared identities this comparison publishes no value for"
    )


def _unmeasured_spellings(
    assessments: Sequence[ReviewAssessment], measurement: AssessmentCurrentnessMeasurement
) -> tuple[str, ...]:
    """Every declared identity no value was measured for, deduplicated in declaration order."""

    spellings: dict[str, None] = {}
    for assessment in assessments:
        for spelling in unmeasured_identities(assessment, measurement):
            spellings.setdefault(spelling, None)
    return tuple(spellings)


def _unavailable(
    detail: str, unreadable: Sequence[str], *, next_action: str | None
) -> ReviewRecordChannel:
    """One currentness collection nothing could be measured for, with its own reason and remedy."""

    return ReviewRecordChannel(
        records="assessment_currentness",
        state="unavailable",
        owner=CURRENTNESS_OWNER,
        detail=detail,
        unreadable=tuple(unreadable),
        next_action=next_action or _REPAIR_ACTION,
    )
