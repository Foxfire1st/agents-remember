"""The review's owner-produced assessments and collection availability (ICR-R14@v1).

Curator assessments and their evidence are read through the curator's durable owner. Dependency
currentness is measured against this resolution, including its recorded endpoints when a historical
comparison is requested. A damaged authority is unavailable rather than an observed empty one.

Detection runs, verification observations and evidence claims belonged to the retired canonical
store. Converted memory trees have no format or writer for those record classes, so their channels
remain visible as unavailable with no measured count. Proof entries and worklist history keep their
own text-store answers; neither is relabelled as a canonical supporting record.

The authored-effects collection was the review matrix's own selection over the retired
envelope tables. The matrix left with those tables (MIK-R74 rule 1), so its channel stays
visible as unavailable with no measured count rather than vanishing or reading as zero.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agents_remember.application.review_assessment_currentness import (
    CURRENTNESS_OWNER,
    comparison_currentness_measurement,
    currentness_channel,
)
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
)
from agents_remember.application.review_curator_records import (
    records_from_curator_generation,
    review_curator_records,
)
from agents_remember.application.review_record_rendering import (
    ReviewRecordInputs,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review import (
    ReviewRecordChannel,
    ReviewRecordChannelState,
    ReviewRecordClassName,
    ReviewRefusal,
    ReviewSurfaceRequest,
)

__all__ = [
    "EVIDENCE_CLAIM_KINDS",
    "review_records_for",
    "review_records_for_resolution",
    "review_records_of",
]

EVIDENCE_CLAIM_KINDS: frozenset[str] = frozenset({"evidence_claim"})

_COLLECTION_OWNERS: Mapping[ReviewRecordClassName, str] = {
    "assessments": "curator_coherence.load_curator_coherence_generation",
    "detection_signals": "MIK-R26:retired-canonical-detection-records",
    "verification_observations": "MIK-R26:retired-canonical-verification-records",
    "authored_effects": "MIK-R74:removed-review-matrix",
    "evidence_claims": "MIK-R26:retired-canonical-evidence-records",
    "assessment_currentness": CURRENTNESS_OWNER,
}

# The five collection keys a review reports. ``assessment_currentness`` is separate because it
# is a measurement rather than a collection.
_COLLECTION_NAMES: tuple[ReviewRecordClassName, ...] = (
    "assessments",
    "detection_signals",
    "verification_observations",
    "authored_effects",
    "evidence_claims",
)


def review_records_for(
    config: McpRuntimeConfig, request: ReviewSurfaceRequest
) -> ReviewRecordInputs:
    """The complete available record collection for the candidate this request resolves to.

    The candidate is resolved exactly as the surface resolves it -- including *which record* the
    request named (ICR-R12), so a review of a leaf's recorded comparison is handed the records of
    that same recorded pair rather than of whatever the leaf holds now -- so the records a caller is
    handed belong to the comparison the same request renders. A candidate that does not resolve
    supplies no records and says so per collection -- the surface refuses that request, and a bundle
    that claimed an absence instead would be reporting an unresolvable candidate as a candidate with
    no records.
    """

    return review_records_of(
        resolve_review_candidate(
            config,
            request.repository_id,
            request.master,
            request.leaf_id,
            recorded=request.history == "recorded",
        )
    )


def review_records_of(
    resolved: ReviewCandidateResolution | ReviewRefusal,
) -> ReviewRecordInputs:
    """The record collection of one resolution a caller already holds, or of its refusal.

    A reviewer request resolves its candidate once and hands that resolution to both halves -- the
    records here and the composition (MIK-R40 rule 2) -- so the two can never describe two different
    candidates, and the worktrees are not captured a second time for the same answer.
    """

    if isinstance(resolved, ReviewRefusal):
        return ReviewRecordInputs(channels=_unresolved_channels(resolved))
    if resolved.contract is None:  # pragma: no cover - a resolved candidate always names a contract
        return ReviewRecordInputs(channels=_unresolved_channels(None))
    return review_records_for_resolution(resolved)


def review_records_for_resolution(
    resolved: ReviewCandidateResolution, *, curator_record_digest: str | None = None
) -> ReviewRecordInputs:
    """Every collection one resolved candidate's owners can answer for, with its own availability.

    The dependency-currentness measurement is produced here, from the comparison this resolution
    bound, and it is a measurement rather than a bare mapping: the identities the comparison
    publishes are compared against the stored bindings, and the ones it publishes no value for stay
    unmeasured instead of being read as agreement (``ICR-R15@v1``). It is measured against **this**
    resolution -- the recorded generation when the request named one, the live candidate otherwise --
    so a historical assessment is never silently measured against today's branch.
    """

    curator = (
        review_curator_records(resolved)
        if curator_record_digest is None
        else records_from_curator_generation(resolved, curator_record_digest)
    )
    assessments, assessment_channel = curator.assessments, curator.channel
    measurement = comparison_currentness_measurement(resolved)
    return ReviewRecordInputs(
        assessments=assessments,
        artifacts=curator.artifacts,
        currentness=measurement,
        channels=(
            assessment_channel,
            _retired_channel("detection_signals"),
            _retired_channel("verification_observations"),
            _removed_matrix_channel(),
            _retired_channel("evidence_claims"),
            currentness_channel(assessment_channel, measurement, assessments),
        ),
    )


def _removed_matrix_channel() -> ReviewRecordChannel:
    """Keep the matrix-sourced collection visible without claiming an owner measured zero.

    Authored effects were the review matrix's own selection over the retired envelope tables.
    The matrix left with those tables (MIK-R74 rule 1): no review reads it now, so the collection
    is unavailable with the removal as its reason rather than absent, unselected or zero.
    """

    return _unavailable(
        "authored_effects",
        "authored effects were the review matrix's own selection over the retired envelope "
        "tables, and the matrix left with those tables under MIK-R74 rule 1; no review reads "
        "this collection now, so it is neither supplied nor a measured zero",
        next_action=(
            "read the knowledge comparison, a family roster, or the recorded evidence panes this "
            "review publishes; none of them selects matrix rows"
        ),
    )


def _retired_channel(records: ReviewRecordClassName) -> ReviewRecordChannel:
    """Keep a retired record class visible without claiming the converted tree measured zero."""

    return _unavailable(
        records,
        f"{records} were canonical database records. Converted memory trees have no writer or "
        "record format for this class, so this review cannot supply or count it; the canonical "
        "reader was retired under MIK-R26",
        next_action=(
            "inspect the candidate's proof entries and worklist history through their text-store "
            "views; those records keep their own meaning and do not supply this retired class"
        ),
    )


def _unresolved_channels(refusal_value: ReviewRefusal | None) -> tuple[ReviewRecordChannel, ...]:
    """Every collection, unavailable, for a candidate this composition could not resolve.

    The surface refuses this request and renders no pane, but the bundle still states the fact per
    collection rather than presenting five empty tuples: nothing was read because nothing resolved.
    """

    detail = (
        "no candidate resolved for this request, so no owner was asked for this collection"
        if refusal_value is None
        else f"no candidate resolved for this request: {refusal_value.code}: {refusal_value.detail}"
    )
    next_action = (
        "open the review for an admitted live curator candidate whose enclosure contract exists"
        if refusal_value is None
        else refusal_value.next_action
    )
    channels = tuple(
        _unavailable(name, detail, next_action=next_action) for name in _COLLECTION_NAMES
    )
    currentness = ReviewRecordChannel(
        records="assessment_currentness",
        state="unavailable",
        owner=CURRENTNESS_OWNER,
        detail=detail,
        next_action=next_action,
    )
    return (*channels, currentness)


def _answered(records: ReviewRecordClassName, count: int) -> ReviewRecordChannel:
    """One collection's owner answered: this many records were supplied, or a measured zero."""

    if count:
        return _recorded(
            records,
            count,
            detail=(f"{count} {records} record(s) supplied by their owner"),
        )
    return _absent(
        records,
        f"the owner of {records} answered for this candidate and holds none of this class; this is a "
        "measured absence, not an authority this composition failed to read",
    )


@dataclass(frozen=True)
class _Answer:
    """One collection's raw answer, before the channel attaches the owner that gave it.

    It is a value rather than six keyword arguments so the one construction point stays readable: a
    channel is an answer plus the owner of the collection it came from, and only the answer varies
    between call sites.
    """

    state: ReviewRecordChannelState
    detail: str
    count: int | None = None
    unreadable: tuple[str, ...] = ()
    next_action: str | None = None


def _recorded(
    records: ReviewRecordClassName,
    count: int,
    *,
    detail: str,
    unreadable: Sequence[str] = (),
) -> ReviewRecordChannel:
    """One collection the owner supplied, with the count it answered and any record it could not."""

    return _channel(
        records, _Answer(state="recorded", detail=detail, count=count, unreadable=tuple(unreadable))
    )


def _absent(records: ReviewRecordClassName, detail: str) -> ReviewRecordChannel:
    """One collection the owner answered for and holds none of: a measured zero, never a silence."""

    return _channel(records, _Answer(state="none_recorded", detail=detail, count=0))


def _unavailable(
    records: ReviewRecordClassName,
    detail: str,
    *,
    next_action: str,
    unreadable: Sequence[str] = (),
) -> ReviewRecordChannel:
    """One collection whose expected content could not be read, with its provenance and remedy."""

    return _channel(
        records,
        _Answer(
            state="unavailable",
            detail=detail,
            unreadable=tuple(unreadable),
            next_action=next_action,
        ),
    )


def _channel(records: ReviewRecordClassName, answer: _Answer) -> ReviewRecordChannel:
    """One channel value, with the owner of its collection taken from the one declaration above."""

    return ReviewRecordChannel(
        records=records,
        state=answer.state,
        owner=_COLLECTION_OWNERS[records],
        record_count=answer.count,
        detail=answer.detail,
        unreadable=tuple(answer.unreadable),
        next_action=answer.next_action,
    )
