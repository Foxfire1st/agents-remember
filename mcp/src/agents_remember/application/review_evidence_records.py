"""The review's owner-produced assessments and collection availability (ICR-R14@v1).

Curator assessments and their evidence are read through the curator's durable owner. Dependency
currentness is measured against this resolution, including its recorded endpoints when a historical
comparison is requested. A damaged authority is unavailable rather than an observed empty one.

Detection runs, verification observations and evidence claims belonged to the retired canonical
store. Converted memory trees have no format or writer for those record classes, so their channels
remain visible as unavailable with no measured count. Proof entries and worklist history keep their
own text-store answers; neither is relabelled as a canonical supporting record.

The review matrix supplies authored effects only when a subject is selected. Its availability and
paging statement are taken from that read, and a task-context review reports it not selected.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

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
    ReviewCollectionPage,
    ReviewRecordChannel,
    ReviewRecordChannelState,
    ReviewRecordClassName,
    ReviewRefusal,
    ReviewSurfaceRequest,
)
from agents_remember.models.knowledge.view import ReviewMatrixRow

__all__ = [
    "AUTHORED_EFFECT_KINDS",
    "EVIDENCE_CLAIM_KINDS",
    "MatrixSelection",
    "review_records_for",
    "review_records_for_resolution",
    "review_records_of",
    "with_selection_channels",
    "without_selected_matrix",
]

# The record kinds the knowledge pane renders as mechanically-sourced *authored* records. Declared
# here, beside the channel that reports their availability, and imported by the review adapter --
# one declaration, so the collection a channel counts and the collection a pane renders cannot come
# to disagree about which kinds they are.
AUTHORED_EFFECT_KINDS: frozenset[str] = frozenset(
    {"invariant_effect_claim", "preservation_claim", "unresolved_question"}
)
EVIDENCE_CLAIM_KINDS: frozenset[str] = frozenset({"evidence_claim"})

_COLLECTION_OWNERS: Mapping[ReviewRecordClassName, str] = {
    "assessments": "curator_coherence.load_curator_coherence_generation",
    "detection_signals": "MIK-R26:retired-canonical-detection-records",
    "verification_observations": "MIK-R26:retired-canonical-verification-records",
    "authored_effects": "knowledge_views.read_knowledge_view:review_matrix",
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

# The collection the composition adds after reading the matrix, with the kinds it selects.
_SELECTION_KINDS: tuple[tuple[ReviewRecordClassName, frozenset[str]], ...] = (
    ("authored_effects", AUTHORED_EFFECT_KINDS),
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
            _retired_channel("evidence_claims"),
            currentness_channel(assessment_channel, measurement, assessments),
        ),
    )


def without_selected_matrix(records: ReviewRecordInputs) -> ReviewRecordInputs:
    """State the matrix-sourced collection for a review that read no matrix at all.

    A task-context review compares no subject, so it never asks the view for a row: this collection
    is ``not_selected`` rather than absent, because "this review did not ask" is a different fact
    from an owner answering that it holds none (``ICR-R14@v1``).
    """

    channels = tuple(
        _not_selected(
            name,
            "this review selected no knowledge subject, so no review-matrix row was read and this "
            "collection was neither supplied nor ruled out",
        )
        for name, _kinds in _SELECTION_KINDS
    )
    return replace(records, channels=(*records.channels, *channels))


@dataclass(frozen=True)
class MatrixSelection:
    """What the composition learned about the matrix selection it read.

    ``page`` is the page the payload publishes for it, or ``None`` when the request named no
    collection (or the owner refused to serve one); ``remaining`` is the owner's own extraction for
    the selection, measured whether or not a page was published; ``unreadable`` is the refusal that
    stopped a requested page, or ``None``. The three travel as one value because they describe one
    read, and a caller that could pass two of them could state a bound beside a cursor that does not
    belong to it.
    """

    page: ReviewCollectionPage | None = None
    remaining: int = 0
    unreadable: ReviewRefusal | None = None


def with_selection_channels(
    records: ReviewRecordInputs,
    rows: Sequence[ReviewMatrixRow],
    selection: MatrixSelection,
) -> ReviewRecordInputs:
    """Add the matrix-sourced collection's availability from the matrix's own rows.

    A review that selected a subject reports what the view returned, and the bound it reported travels
    with it: when the view rendered only a page of its selection, the count here is the page the
    review actually read and ``page`` names how much of the selection lies beyond it (ICR-R14), so an
    entry button's own record count is never read as the whole selection.

    ``unreadable`` is the third state and it is not an absence: a matrix read the composition *asked
    for* and could not be served -- a page cursor the view refused because its snapshot moved, a
    damaged index -- leaves the collection ``unavailable`` with that refusal as the reason and its
    next action. Reporting a measured zero there would be the collapse of "an owner holds none" and
    "this composition could not read it" that ``ICR-R14@v1`` exists to keep apart.

    ``selection`` is that read's own answer -- its page, its count and any refusal -- as one value;
    see :class:`MatrixSelection` for why the three travel together.
    """

    channels = tuple(
        _selection_channel(name, kinds, rows, selection) for name, kinds in _SELECTION_KINDS
    )
    return replace(records, channels=(*records.channels, *channels))


def _selection_channel(
    records: ReviewRecordClassName,
    kinds: frozenset[str],
    rows: Sequence[ReviewMatrixRow],
    selection: MatrixSelection,
) -> ReviewRecordChannel:
    """One matrix-sourced collection's availability, from the rows the view returned.

    The number this channel reports is the part of the matrix **this collection** selected -- the
    authored effects the page carried -- while the bound beside it is the whole selection's own, which
    is what makes the two readable together: a collection of this page and a walk of that selection.
    """

    if selection.unreadable is not None:
        return _unavailable(
            records,
            "the review matrix this collection comes from could not be read: "
            f"{selection.unreadable.detail}",
            next_action=selection.unreadable.next_action,
        )
    supplied = tuple(row for row in rows if row.subject.record_kind in kinds)
    return _answered(records, len(supplied), selection=selection)


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


def _answered(
    records: ReviewRecordClassName, count: int, *, selection: MatrixSelection | None = None
) -> ReviewRecordChannel:
    """One collection's owner answered: this many records were supplied, or a measured zero.

    ``bounded_by`` is the page the composition published for this collection, or ``None`` when it
    published none. It decides *which* honest sentence the bound earns: a published page carries the
    cursor that reaches the remainder, and a review that named no collection published no cursor, so
    it names the request that does reach it instead. A remainder is never stated without one of the
    two, which is the shape this packet exists to remove.
    """

    if count:
        return _recorded(
            records,
            count,
            detail=(
                f"{count} {records} record(s) supplied by their owner{_remaining_note(selection)}"
            ),
        )
    return _absent(
        records,
        f"the owner of {records} answered for this candidate and holds none of this class; this is a "
        f"measured absence, not an authority this composition failed to read"
        f"{_remaining_note(selection)}",
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


def _not_selected(records: ReviewRecordClassName, detail: str) -> ReviewRecordChannel:
    """One collection this composition did not read because the review selected no operand."""

    return _channel(
        records,
        _Answer(
            state="not_selected",
            detail=detail,
            next_action=(
                "open the review on a recorded subject: the review matrix selects its rows for a "
                "selected subject and never for the task alone"
            ),
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


def _remaining_note(selection: MatrixSelection | None) -> str:
    """The declared bound of one selection, always with the action that reaches the rest of it.

    Three states, one obligation. A published page whose owner issued a cursor names the cursor; a
    review that read a bounded selection without publishing a page -- the reader named no collection
    -- names the request that does publish one; and a review that asked for a page and was refused
    states neither, because the refusal is on the wire and there is no window to describe. No state
    states a remainder on its own, because a count with no reachable action is the non-conformance
    ``ICR-R10@v1`` names.
    """

    if selection is None or not selection.remaining:
        return ""
    page = selection.page
    if page is not None and page.continuation is not None:
        return (
            f"; the selection was bounded and {selection.remaining} further row(s) of it lie beyond "
            "the page this review rendered, which carries the cursor that reaches them"
        )
    if page is None:
        return (
            f"; this review read records at its declared bound and {selection.remaining} further "
            "row(s) of the selection lie beyond that page; request records as a page "
            "(pageOf=records) to reach them"
        )
    return (
        f"; the selection was bounded and {selection.remaining} further row(s) of it lie beyond the "
        "page this review rendered, and that page published no cursor, so the remainder is stated "
        "without a continuation rather than claimed to be reachable"
    )
