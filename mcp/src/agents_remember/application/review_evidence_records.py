"""The complete owner-produced record collection for one review (``ICR-R14@v1``).

A review's records are produced by owners that already exist -- the detection owner records a run and
its signals, the evidence owner records claims and verification observations, the curator authority
publishes the authored assessments -- and this module is the one composition that reads **all** of
them for one resolved candidate and hands them over as the bundle the surface freezes and renders. It
produces no record, re-derives no owner's content and decides nothing: every collection below is the
owner's own answer, read through the owner's own read operation.

**Availability is a fact per collection, and an empty tuple never stands for three different ones.**
Each collection carries a :class:`~agents_remember.models.knowledge.review.ReviewRecordChannel`
naming the state its owner's answer earned:

* ``recorded`` / ``none_recorded`` -- the owner answered, and this is what it holds;
* ``unavailable`` -- expected content that could not be read (an absent dataset, an unreadable
  authority, a damaged record), carried with the owner's own refusal text as its provenance;
* ``not_measured`` -- a quantity nothing measured (dependency currentness, whose measurement owner is
  named rather than guessed at);
* ``not_selected`` -- a collection this composition did not read because the review selected no
  operand that reaches it.

A loader failure is therefore never reported as "no records ever existed", and no channel reports a
positive it did not measure: an unreadable authority has no count, and an unmeasured assessment is
left to the shipped projection's own unmeasured state.

**One collection that cannot be read does not withdraw the others.** Every read below is guarded
per collection, so a damaged detection run leaves the observations, claims and assessments supplied
and names the run it could not serve -- the failure behavior ``ICR-R14@v1`` states.

**What this module does not do.** It does not filter records by subject or generation (that is
``ICR-R26@v1``'s applicability classification), it does not measure dependency currentness
(``ICR-R15@v1``), and it does not re-run detection, execute a command or author anything.

**Authored effects are the matrix's collection, and the matrix reports them.** They live in the
knowledge dataset and are read by the shipped review-matrix view inside the composition, which is why
:func:`with_selection_channels` states their availability from that read's own answer rather than from
a listing this module would have to invent. Evidence claims are the evidence owner's collection: this
module reads every claim the candidate records, with the author, lifecycle, declared limitations and
asserted coverage the owner holds, so the matrix's row selects *which* claims a selection reaches
while the claim's own fields arrive intact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import apsw
from pydantic import ValidationError

from agents_remember.application.knowledge_diff import open_diff_side
from agents_remember.application.knowledge_evidence import read_evidence_scope
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
    review_namespace,
)
from agents_remember.application.review_record_rendering import (
    ReviewClaimRecord,
    ReviewRecordInputs,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge import evidence_records
from agents_remember.memory.knowledge.detection import read_detection_run, recorded_run_ids
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge.store import (
    OpenedKnowledgeStore,
    open_existing_knowledge_store,
)
from agents_remember.models.knowledge.detection import DetectionSignalPayload
from agents_remember.models.knowledge.evidence import (
    VerificationObservationPayload,
    coverage_identity,
)
from agents_remember.models.knowledge.evidence_read import (
    ClaimCoverage,
    EvidenceClaimRecord,
    EvidenceReadRequest,
    EvidenceReadResult,
    ObservationCandidateSeed,
    VerificationObservationItem,
)
from agents_remember.models.knowledge.read import KnowledgeReadContext
from agents_remember.models.knowledge.review import (
    ReviewCollectionPage,
    ReviewRecordChannel,
    ReviewRecordChannelState,
    ReviewRecordClassName,
    ReviewRefusal,
    ReviewSurfaceRequest,
)
from agents_remember.models.knowledge.view import ReviewMatrixRow
from agents_remember.models.lifecycles.review_assessment import ReviewAssessment
from agents_remember.worktrees.integration.closeout.curator_coherence import (
    CuratorCoherenceError,
    curator_coherence_paths,
    load_curator_coherence_authority,
)

__all__ = [
    "AUTHORED_EFFECT_KINDS",
    "EVIDENCE_CLAIM_KINDS",
    "MatrixSelection",
    "review_records_for",
    "with_selection_channels",
    "without_selected_matrix",
]

# What one owner's own record read raises when the bytes it holds are damaged: the storage refusal a
# seal or row check reports, and the validation refusal a payload that no longer parses reports. One
# damaged record is named rather than fatal, so both are caught per record and never around a whole
# collection.
_DAMAGED_RECORD_ERRORS: tuple[type[Exception], ...] = (KnowledgeStorageError, ValidationError)

# The record kinds the knowledge pane renders as mechanically-sourced *authored* records. Declared
# here, beside the channel that reports their availability, and imported by the review adapter --
# one declaration, so the collection a channel counts and the collection a pane renders cannot come
# to disagree about which kinds they are.
AUTHORED_EFFECT_KINDS: frozenset[str] = frozenset(
    {"invariant_effect_claim", "preservation_claim", "unresolved_question"}
)
EVIDENCE_CLAIM_KINDS: frozenset[str] = frozenset({"evidence_claim"})

# Every collection this composition reports, with the owner whose read answers for it. The owner name
# travels on the channel so a reader of an absent or unreadable collection knows which authority to
# look at rather than which code path happened to run.
_COLLECTION_OWNERS: Mapping[ReviewRecordClassName, str] = {
    "assessments": "curator_coherence.load_curator_coherence_authority",
    "detection_signals": "detection.read_detection_run",
    "verification_observations": "knowledge_evidence.read_evidence_scope",
    "authored_effects": "knowledge_views.read_knowledge_view:review_matrix",
    "evidence_claims": "evidence_records.claim_record",
    "assessment_currentness": "review_assessment_store.assessment_currentness_for_record",
}

# The five collections a review of a resolved candidate supplies, in the order the surface reads them
# and the order a payload renders them. ``assessment_currentness`` is reported separately because it
# is a measurement rather than a collection.
_COLLECTION_NAMES: tuple[ReviewRecordClassName, ...] = (
    "assessments",
    "detection_signals",
    "verification_observations",
    "authored_effects",
    "evidence_claims",
)

_CURRENTNESS = ReviewRecordChannel(
    records="assessment_currentness",
    state="not_measured",
    owner=_COLLECTION_OWNERS["assessment_currentness"],
    detail=(
        "no dependency-currentness measurement was supplied with this bundle, so every stored "
        "assessment keeps the unmeasured state the shipped projection reports for it; an unmeasured "
        "assessment is never promoted to current, and an empty measurement is not a measurement"
    ),
    next_action=(
        "measure each stored assessment's recorded dependencies through the shipped "
        "dependency-currentness owner (ICR-R15@v1); this composition supplies no measurement of its "
        "own and reports none"
    ),
)

# The two collections the composition adds once it has read the review matrix, with the kinds each
# one selects. Declared as one value so a channel and the pane that renders it cannot drift apart.
_SELECTION_KINDS: tuple[tuple[ReviewRecordClassName, frozenset[str]], ...] = (
    ("authored_effects", AUTHORED_EFFECT_KINDS),
)

# The one next action an unopened candidate dataset earns: it rules nothing out, so a reader must not
# read its absence as an empty collection.
_PLACE_DATASET = (
    "place the candidate's dataset in the leaf's disposable knowledge root, then reopen the review; "
    "an unopened dataset rules nothing out"
)


def review_records_for(
    config: McpRuntimeConfig, request: ReviewSurfaceRequest
) -> ReviewRecordInputs:
    """The complete available record collection for the candidate this request resolves to.

    The candidate is resolved exactly as the surface resolves it, so the records a caller is handed
    belong to the comparison the same request renders. A candidate that does not resolve supplies no
    records and says so per collection -- the surface refuses that request, and a bundle that claimed
    an absence instead would be reporting an unresolvable candidate as a candidate with no records.
    """

    resolved = resolve_review_candidate(
        config, request.repository_id, request.master, request.leaf_id
    )
    if isinstance(resolved, ReviewRefusal):
        return ReviewRecordInputs(channels=_unresolved_channels(resolved))
    if resolved.contract is None:  # pragma: no cover - a resolved candidate always names a contract
        return ReviewRecordInputs(channels=_unresolved_channels(None))
    return _resolved_records(resolved)


def _resolved_records(resolved: ReviewCandidateResolution) -> ReviewRecordInputs:
    """Every collection one resolved candidate's owners can answer for, with its own availability."""

    assessments, assessment_channel = _assessments(resolved)
    signals, signal_channel = _detection_signals(resolved)
    observations, observation_channel = _observations(resolved)
    claims, claim_channel = _evidence_claims(resolved)
    return ReviewRecordInputs(
        assessments=assessments,
        signals=signals,
        observations=observations,
        claims=claims,
        channels=(
            assessment_channel,
            signal_channel,
            observation_channel,
            claim_channel,
            _CURRENTNESS,
        ),
    )


def without_selected_matrix(records: ReviewRecordInputs) -> ReviewRecordInputs:
    """State both matrix-sourced collections for a review that read no matrix at all.

    A task-context review compares no subject, so it never asks the view for a row: both collections
    are ``not_selected`` rather than absent, because "this review did not ask" is a different fact
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
    """Add the two matrix-sourced collections' availability to a bundle, from the matrix's own rows.

    A review that selected a subject reports what the view returned, and the bound it reported travels
    with it: when the view rendered only a page of its selection, the count here is the page the
    review actually read and ``page`` names how much of the selection lies beyond it (ICR-R14), so an
    entry button's own record count is never read as the whole selection.

    ``unreadable`` is the third state and it is not an absence: a matrix read the composition *asked
    for* and could not be served -- a page cursor the view refused because its snapshot moved, a
    damaged dataset -- leaves both collections ``unavailable`` with that refusal as the reason and its
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


def _assessments(
    resolved: ReviewCandidateResolution,
) -> tuple[tuple[ReviewAssessment, ...], ReviewRecordChannel]:
    """The published assessment collection, or the state its authority's answer earned.

    Three facts are separated here, and the F09 defect was the collapse of two of them: an authority
    that has never been published (``none_recorded`` -- this candidate has no published assessment),
    an authority whose bytes are there and cannot be read (``unavailable`` with its own refusal as
    provenance), and a published authority holding assessments (``recorded``).
    """

    contract = resolved.contract
    assert contract is not None
    try:
        paths = curator_coherence_paths(contract)
    except CuratorCoherenceError as error:
        return (), _unavailable(
            "assessments",
            _provenance("the curator authority could not be located", error.status, error.detail),
            next_action=error.next_action,
        )
    if not paths.canonical.is_file():
        return (), _absent(
            "assessments",
            "no curator assessment authority has been published for this candidate "
            f"({paths.canonical.name} is absent), so this candidate records no assessment; that is "
            "an absence the owner answered, not an authority this composition could not read",
        )
    try:
        validated = load_curator_coherence_authority(contract)
    except (CuratorCoherenceError, OSError, ValueError) as error:
        return (), _unavailable(
            "assessments",
            _provenance("the published curator authority could not be read", None, str(error)),
            next_action=(
                "repair or republish the candidate's curator-coherence authority through the curator "
                "publication owner, then reopen the review"
            ),
            unreadable=(paths.canonical.name,),
        )
    stored = tuple(validated.record.assessments)
    return stored, _answered("assessments", len(stored))


def _detection_signals(
    resolved: ReviewCandidateResolution,
) -> tuple[tuple[DetectionSignalPayload, ...], ReviewRecordChannel]:
    """Every detection signal the candidate's recorded runs hold, read through the detection owner.

    The runs are the recorded identities; each run's signals are read through
    :func:`~agents_remember.memory.knowledge.detection.read_detection_run`, which recomputes the
    stored revision's own seal. A damaged run is named and left out while its siblings are still
    supplied, and a namespace that records no run at all is a measured zero rather than a silence.
    """

    opened = _open_candidate_store(resolved, "detection_signals")
    if isinstance(opened, ReviewRecordChannel):
        return (), opened
    store = opened
    try:
        runs = recorded_run_ids(store)
        signals, unreadable = _read_signal_runs(store, runs)
    except KnowledgeStorageError as error:
        return (), _unavailable(
            "detection_signals",
            _provenance("the recorded detection runs could not be read", None, str(error)),
            next_action="repair the candidate dataset, then reopen the review",
        )
    finally:
        store.close()
    return _signal_channel(signals, runs, unreadable)


def _signal_channel(
    signals: Sequence[DetectionSignalPayload],
    runs: Sequence[str],
    unreadable: Sequence[str],
) -> tuple[tuple[DetectionSignalPayload, ...], ReviewRecordChannel]:
    """The supplied signals and the state the runs that produced them earned."""

    if not runs:
        return (), _absent(
            "detection_signals",
            "the candidate dataset records no detection run, so no detector signal exists for this "
            "review; the detection owner answered for the runs it holds",
        )
    if not signals:
        return (), _unavailable(
            "detection_signals",
            f"all {len(runs)} recorded detection run(s) could not be read, so no signal was "
            "supplied; their identities are named rather than reported as a run that produced nothing",
            next_action="rerecord the runs whose stored revision is damaged, then reopen the review",
            unreadable=unreadable,
        )
    return tuple(signals), _recorded(
        "detection_signals",
        len(signals),
        detail=(
            f"{len(signals)} detector signal(s) read from {len(runs)} recorded run(s)"
            + _unreadable_note(unreadable)
        ),
        unreadable=unreadable,
    )


def _read_signal_runs(
    store: OpenedKnowledgeStore, runs: Sequence[str]
) -> tuple[tuple[DetectionSignalPayload, ...], tuple[str, ...]]:
    """Every readable run's signals, plus the identities of the runs that could not be served.

    Each run is read under its own guard, because the detection owner reports a *damaged* revision by
    raising -- :func:`~agents_remember.memory.knowledge.detection.read_detection_run` recomputes the
    stored seal and raises :class:`~agents_remember.memory.knowledge.refusals.KnowledgeStorageError`
    when it no longer matches -- while a run that is simply absent from the sequence answers with a
    refusal. Both are one damaged identity here, named on the channel, and neither withdraws the
    siblings that were read: a collection is only ``unavailable`` when nothing in it could be served.
    """

    signals: list[DetectionSignalPayload] = []
    unreadable: list[str] = []
    for run_id in runs:
        try:
            result = read_detection_run(store, run_id)
        except _DAMAGED_RECORD_ERRORS:
            unreadable.append(run_id)
            continue
        if result.state == "read":
            signals.extend(result.signals)
        else:
            unreadable.append(run_id)
    return tuple(signals), tuple(unreadable)


def _observations(
    resolved: ReviewCandidateResolution,
) -> tuple[tuple[VerificationObservationPayload, ...], ReviewRecordChannel]:
    """Every verification observation recorded against this candidate, through the evidence owner.

    The seed names both halves of the candidate the way a record names them -- the candidate dataset's
    own logical identity and the captured code tree -- so an observation recorded against either half
    is selected. An empty result is the evidence owner's own ``selector_absent`` answer and becomes a
    measured zero; every other refusal (an absent dataset, an unreadable snapshot, a selection past
    its bound) is unavailability with that refusal as provenance, never an empty collection.
    """

    context = _candidate_context(resolved)
    if isinstance(context, ReviewRecordChannel):
        return (), context
    result = read_evidence_scope(
        resolved.candidate_database,
        context,
        EvidenceReadRequest(
            seed=ObservationCandidateSeed(
                knowledge_logical_digest=context.knowledge.logical_digest,
                code_candidate_tree_id=resolved.candidate_code_tree_id,
            )
        ),
    )
    if result.state == "refused":
        return (), _observation_refusal(result)
    observations = tuple(
        item.observation.payload
        for item in _page_items(result)
        if isinstance(item, VerificationObservationItem)
    )
    return observations, _answered("verification_observations", len(observations))


def _observation_refusal(result: EvidenceReadResult) -> ReviewRecordChannel:
    """The state one refused observation read earns: a measured zero, or unavailability."""

    refusal_value = result.refusal
    code = None if refusal_value is None else refusal_value.code
    if code == "selector_absent":
        return _absent(
            "verification_observations",
            "the candidate's dataset answers for this exact candidate and records no verification "
            "observation for it; the evidence owner's own answer is a measured zero, not an unread "
            "authority",
        )
    return _unavailable(
        "verification_observations",
        _provenance(
            "the evidence owner refused to read this candidate's observations",
            code,
            _detail(result),
        ),
        next_action=(
            "repair the candidate's dataset or receipt, then reopen the review"
            if refusal_value is None
            else refusal_value.next_action
        ),
    )


def _evidence_claims(
    resolved: ReviewCandidateResolution,
) -> tuple[tuple[ReviewClaimRecord, ...], ReviewRecordChannel]:
    """Every evidence claim this candidate records, read through the evidence record owner.

    The collection is the owner's *complete* one -- every claim identity the candidate records, read
    one at a time through the owner's own single-record reader -- rather than the matrix page, because
    the matrix selects which claims a subject reaches and this module supplies what the candidate
    records. Reading one record at a time is what makes the isolation real on this collection too: a
    claim whose envelope, payload or coverage cannot be read is named on the channel while its
    siblings are still supplied, instead of the whole collection becoming one refusal.
    """

    opened = _open_candidate_store(resolved, "evidence_claims")
    if isinstance(opened, ReviewRecordChannel):
        return (), opened
    store = opened
    try:
        claims, unreadable = _claim_records(store)
    except _DAMAGED_RECORD_ERRORS as error:
        return (), _unavailable(
            "evidence_claims",
            _provenance("the recorded evidence claims could not be listed", None, str(error)),
            next_action="repair the candidate dataset, then reopen the review",
        )
    finally:
        store.close()
    if not claims:
        return (), _absent(
            "evidence_claims",
            "the candidate dataset records no evidence claim, so no authored evidence exists for "
            "this review; the evidence owner answered for the claims it holds",
        )
    return claims, _recorded(
        "evidence_claims",
        len(claims),
        detail=(
            f"{len(claims)} evidence claim(s) read from their own owner with the author, lifecycle, "
            f"declared limitations and asserted coverage each records{_unreadable_note(unreadable)}"
        ),
        unreadable=unreadable,
    )


def _claim_records(
    store: OpenedKnowledgeStore,
) -> tuple[tuple[ReviewClaimRecord, ...], tuple[str, ...]]:
    """One renderer input per readable claim, plus the identities that could not be read.

    Each identity is read through the evidence owner's own single-record reader rather than through
    the owner's all-or-nothing enumerator, so one damaged claim is named while its siblings are
    supplied: the same per-record isolation the detection channel has, for the same reason.
    """

    claims: list[ReviewClaimRecord] = []
    unreadable: list[str] = []
    for claim_id in evidence_records.claim_ids(store):
        try:
            record = evidence_records.claim_record(store, claim_id)
            coverage = evidence_records.claimed_coverage_of_claim(store, claim_id)
        except _DAMAGED_RECORD_ERRORS:
            unreadable.append(claim_id)
            continue
        if record is None:  # a listed identity whose own envelope cannot be read is damaged
            unreadable.append(claim_id)
            continue
        claims.append(_claim_record(record, coverage))
    return tuple(claims), tuple(unreadable)


def _claim_record(
    record: EvidenceClaimRecord, coverage: Sequence[ClaimCoverage]
) -> ReviewClaimRecord:
    """One claim's authored fields, rendered as the pane's own input value."""

    return ReviewClaimRecord(
        claim_id=record.claim_id,
        author_ref=record.provenance.actor_ref,
        lifecycle=record.state_at_origin,
        limitations=record.payload.limitations,
        claimed_coverage=tuple(
            f"{edge.endpoint.kind}:{coverage_identity(edge.endpoint)}" for edge in coverage
        ),
        assessment_refs=tuple(record.payload.assessment_refs),
    )


def _candidate_context(
    resolved: ReviewCandidateResolution,
) -> KnowledgeReadContext | ReviewRecordChannel:
    """The candidate's own read context, or the unavailable channel its absence earns.

    An absent dataset is named as absent rather than reported as a zero: the records it would hold are
    not knowable from its absence, and ``apsw``'s own open failure is caught here as well so a file
    that exists but cannot be read reaches the caller as a state instead of an exception.
    """

    if not resolved.candidate_database.is_file():
        return _absent_dataset(
            "verification_observations", f"{resolved.candidate_database.name} is not present"
        )
    try:
        return open_diff_side(
            resolved.candidate_database,
            review_namespace(resolved.repository_id, resolved.candidate_database),
            repository_root=resolved.candidate_code_root,
            code_tree_id=resolved.candidate_code_tree_id,
        )
    except (KnowledgeStorageError, ValueError, OSError, apsw.Error) as error:
        return _absent_dataset("verification_observations", str(error))


def _page_items(result: EvidenceReadResult) -> tuple[object, ...]:
    """Every item one served evidence page carries, or nothing for a page that was not served."""

    page = result.page
    return () if page is None else tuple(page.items)


def _detail(result: EvidenceReadResult) -> str:
    """The evidence owner's own refusal detail, or the fact that it refused without one."""

    return "the read refused without a detail" if result.refusal is None else result.refusal.detail


def _open_candidate_store(
    resolved: ReviewCandidateResolution, records: ReviewRecordClassName
) -> OpenedKnowledgeStore | ReviewRecordChannel:
    """The candidate's dataset as a store, or the unavailable channel its absence earns."""

    if not resolved.candidate_database.is_file():
        return _absent_dataset(records, f"{resolved.candidate_database.name} is not present")
    try:
        return open_existing_knowledge_store(
            resolved.candidate_database,
            review_namespace(resolved.repository_id, resolved.candidate_database),
        )
    except (KnowledgeStorageError, ValueError, OSError, apsw.Error) as error:
        return _absent_dataset(records, str(error))


def _absent_dataset(records: ReviewRecordClassName, detail: str) -> ReviewRecordChannel:
    """One collection whose dataset is absent or cannot be opened: unreadable, never empty."""

    return _unavailable(
        records,
        _provenance(
            "the candidate dataset that would hold these records could not be opened", None, detail
        ),
        next_action=_PLACE_DATASET,
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
    return (*channels, _CURRENTNESS)


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


def _unreadable_note(unreadable: Sequence[str]) -> str:
    """The named identities inside a supplied collection that could not be read."""

    if not unreadable:
        return ""
    return (
        f"; {len(unreadable)} further record(s) of this class could not be read and are named rather "
        "than dropped"
    )


def _provenance(statement: str, status: str | None, detail: str) -> str:
    """One unavailability statement: what failed, under which owner status, and the owner's words."""

    named = "" if status is None else f" ({status})"
    return f"{statement}{named}: {detail}"
