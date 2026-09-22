"""The task-context review: the source entry that needs no selected subject (ICR-R02).

A review of a *task* asks a different question from a review of a subject. The subject review asks
"what changed about this recorded invariant"; this one asks "what did this task change at all", and
its answer is the complete source inventory of the two bound code trees plus every collected fact
that does not depend on a selection. It exists as its own module because it is a different
composition, not a degraded one:

* **No comparison is made, and none is faked.** No selector is resolved, no dataset is opened for
  selection and no review matrix row is read, so a task whose knowledge half does not exist yet still
  has an openable review. The payload says so in the vocabulary's own words rather than by rendering
  an empty statement: the knowledge pane is ``task_context`` with its reason, and the staleness state
  is ``not_compared`` because there is no comparison binding to be current or stale against.
* **The source half is the whole point.** The inventory arrives already measured from the pair the
  resolution bound, and the pane renders it whether it is complete, partial or unavailable -- the
  three states the model keeps apart.
* **Nothing here selects, ranks or concludes.** The records the caller supplied are rendered by
  :mod:`agents_remember.application.review_record_rendering`, the same renderer the subject review
  uses, so an unassessed assessment collection reads identically in both. The two collections the
  *review matrix* owns are reported ``not_selected`` rather than omitted: this composition read no
  matrix, and a review that did not ask may not report an owner's absence (``ICR-R14@v1``).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import apsw

from agents_remember.application.knowledge_before_half import (
    read_dataset_identity,
    unreadable_half_refusal,
)
from agents_remember.application.knowledge_diff import open_diff_side
from agents_remember.application.review_attribution import (
    AttributionSideInput,
    review_attribution,
    selected_subject,
)
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    candidate_receipt_refusal,
    candidate_ref,
    missing_dataset_half,
    require_current_candidate_identity,
    review_namespace,
    unreadable_candidate_refusal,
)
from agents_remember.application.review_evidence_records import with_selection_channels
from agents_remember.application.review_record_rendering import (
    ReviewRecordInputs,
    assessment_displays,
    evidence_pane,
    refused,
    signal,
    subject_states,
    submission,
)
from agents_remember.application.review_source_inventory import (
    attribution_limitations,
    inventory_limitations,
    source_pane,
)
from agents_remember.memory.knowledge.diff_display import TreePaths
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.diff import ReadSide, SourceAttribution
from agents_remember.models.knowledge.read import KnowledgeReadSeed
from agents_remember.models.knowledge.review import (
    KnowledgeReviewPayload,
    KnowledgeReviewResult,
    ReviewKnowledgePane,
    ReviewRefusal,
    ReviewSideContent,
    ReviewSourceInventory,
    ReviewStaleness,
    ReviewSurfaceRequest,
)
from agents_remember.models.lifecycles.review_assessment import SubjectAssessmentState

__all__ = ["pair_attribution", "task_context_review"]


def task_context_review(
    resolved: ReviewCandidateResolution,
    request: ReviewSurfaceRequest,
    records: ReviewRecordInputs,
    inventory: ReviewSourceInventory,
    observed: TreePaths,
) -> KnowledgeReviewResult:
    """One review opened from the task alone: the complete source inventory and no compared operand.

    The captured candidate is re-derived immediately before the payload is built, exactly as the
    subject review re-derives it: a capture input that moved while the inventory was being measured
    is a named refusal rather than an inventory attributed to a candidate the leaf no longer holds.

    The pair's own preflight runs here too, and it is deliberately **stated rather than raised**: a
    half that is present but cannot be read as a dataset is a fact about the knowledge half, and this
    review compares no dataset -- refusing would trade the whole source review away for a knowledge
    state it never reads, which is the failure this entry exists to remove. The state reaches the
    caller as the pane's own reason and as a declared limitation, so it is never silent.

    The attribution partition is measured here, from the same two halves the inventory was measured
    from: a readable half's registered mappings attribute its changes even when the other half is
    absent or unreadable, and the changes it did not attribute stay *undetermined* rather than
    becoming confirmed unregistered -- which is what an unread snapshot cannot establish.
    """

    moved = require_current_candidate_identity(resolved)
    if moved is not None:
        return refused(request.repository_id, moved)
    records = with_selection_channels(records, (), selected=False)
    # Three ways the knowledge half can be unusable, all three *stated* here rather than raised: a
    # dataset that is present but unreadable, a candidate receipt that exists and does not validate
    # (which leaves no namespace to open either half under), and a half that is simply absent.
    receipt = candidate_receipt_refusal(resolved)
    attribution = pair_attribution(resolved, observed, request.selector, namespace_refusal=receipt)
    # Asked once more *after* the pair read: a receipt that read as one at the preflight and broke
    # before the sides were bound reaches the partition's side entries, and this second question is
    # what carries the same state into the pane and the declared limitations instead of leaving it one
    # level down. It costs one small read in the ordinary case, where the answer is already known.
    receipt = receipt or candidate_receipt_refusal(resolved)
    unreadable = (
        unreadable_half_refusal(resolved.baseline_database, resolved.candidate_database) or receipt
    )
    subjects = subject_states(records)
    return KnowledgeReviewResult(
        state="review",
        repository_id=request.repository_id,
        payload=KnowledgeReviewPayload(
            candidate=candidate_ref(
                resolved,
                repository_id=request.repository_id,
                master=request.master,
            ),
            comparison=None,
            knowledge=_task_context_pane(resolved, records, subjects, unreadable),
            # No comparison travels beside the inventory: this review selected no subject.
            source=source_pane(None, inventory, attribution),
            evidence=evidence_pane((), records, subjects),
            staleness=ReviewStaleness(
                state="not_compared",
                statement=(
                    "no knowledge subject was selected for this review, so no comparison binding "
                    "exists to be current or stale; the Source pane carries the complete inventory "
                    "of the bound source pair"
                ),
            ),
            submission=submission(stale=False),
            limitations=(
                "limitation:no_knowledge_subject_selected",
                *unreadable_half_limitations(unreadable),
                *attribution_limitations(attribution),
                *inventory_limitations(inventory),
            ),
        ),
    )


def unreadable_half_limitations(unreadable: ReviewRefusal | None) -> tuple[str, ...]:
    """The declared limit one unreadable knowledge half adds to a task-context review.

    A limit a reader has to open a pane to discover is a limit the response did not state, so the
    same fact the pane's reason carries is declared at the top level beside the inventory's own.
    """

    return () if unreadable is None else ("limitation:knowledge_half_unreadable",)


def _task_context_pane(
    resolved: ReviewCandidateResolution,
    records: ReviewRecordInputs,
    subjects: Mapping[str, SubjectAssessmentState],
    unreadable: ReviewRefusal | None = None,
) -> ReviewKnowledgePane:
    """Pane 1 for a task-context review: no operand compared, and the reason it was not.

    The two sides are ``unresolved`` rather than empty, because an empty statement is a statement
    about a snapshot and this pane made none. The records the caller supplied are rendered as they
    are in the subject path -- which assessment belongs to which subject is the applicability
    projection's own contract, and this composition grows no second rule for it.
    """

    detail = task_context_detail(resolved, unreadable=unreadable)
    side = ReviewSideContent(state="unresolved", language="text", detail=detail)
    return ReviewKnowledgePane(
        before_statement=side,
        after_statement=side,
        signals=tuple(signal(entry) for entry in records.signals),
        assessments=assessment_displays(records, subjects),
        selection_state="task_context",
        selection_detail=detail,
    )


def task_context_detail(
    resolved: ReviewCandidateResolution, *, unreadable: ReviewRefusal | None = None
) -> str:
    """Why a task-context review compared no operand, naming the dataset half that answers for it.

    Three states, each named with its own fact and in the order that decides them: a half whose bytes
    are there and cannot be read (the pair's own preflight, stated here because this review raises
    nothing for it), a half that is absent, and a pair that is present and simply was not selected
    over. The last is not a degraded first: it is what a review of the task alone is.
    """

    if unreadable is not None:
        return (
            "no invariant or family subject was selected for this review, so no knowledge operand "
            f"was compared -- and this pair could not have been compared anyway: {unreadable.detail}. "
            f"To act on it: {unreadable.next_action}. "
            "That state is stated here rather than raised, because this review reads no dataset and "
            "refusing would remove the source review with it; the Source pane carries the complete "
            "source change inventory of the bound pair"
        )
    absent = missing_dataset_half(resolved)
    if absent is None:
        return (
            "no invariant or family subject was selected for this review, so no knowledge operand "
            "was compared and none is reported as empty; the Source pane carries the complete source "
            "change inventory of the bound pair"
        )
    half, database = absent
    return (
        f"no subject was selected and the resolved {half} dataset is absent ({database.name}), so "
        "no knowledge operand was compared; the Source pane carries the complete source change "
        "inventory of the bound pair, which does not depend on knowledge availability"
    )


def pair_attribution(
    resolved: ReviewCandidateResolution,
    observed: TreePaths,
    selector: KnowledgeReadSeed | None,
    *,
    namespace_refusal: ReviewRefusal | None = None,
) -> SourceAttribution:
    """The attribution partition of one resolved pair, measured from the pair's own two datasets.

    This is the task-context path's own measurement, and it exists because a task review compares no
    dataset and therefore has no comparison to inherit one from -- while its source changes still have
    attribution, which is precisely the state a task with an absent or unreadable knowledge half must
    be able to report. Each half is opened read-only when it is there, and a half that is not becomes
    an *unavailable* side: no negative attribution conclusion is drawn from its silence, and a valid
    positive mapping on the readable side is still an attribution.

    ``namespace_refusal`` is the refusal the pair's own record earned, when the caller already asked
    (:func:`candidate_receipt_refusal`); a caller that passes none has the question asked here instead.
    Either way this reader is **total**: a pair whose shared namespace cannot be established still
    binds every side whose own bytes disclose theirs and carries the refusal on the side that cannot
    be bound, so no state of the candidate's record becomes an exception out of the composition.
    """

    failure = (
        candidate_receipt_refusal(resolved) if namespace_refusal is None else namespace_refusal
    )
    if failure is not None:
        return _pair_without_namespace(resolved, observed, selector, failure)
    halves: tuple[tuple[ReadSide, Path, Path | None, str | None], ...] = (
        (
            "before",
            resolved.baseline_database,
            resolved.baseline_code_root,
            resolved.baseline_code_tree_id,
        ),
        (
            "after",
            resolved.candidate_database,
            resolved.candidate_code_root,
            resolved.candidate_code_tree_id,
        ),
    )
    try:
        namespace = review_namespace(resolved.repository_id, resolved.candidate_database)
    except (KnowledgeStorageError, OSError, ValueError) as error:
        # The preflight read these same bytes a moment ago; this guard exists so that a receipt which
        # moves between the two reads is still a stated state rather than an escaping storage error.
        return _pair_without_namespace(
            resolved,
            observed,
            selector,
            unreadable_candidate_refusal(resolved, str(error)),
        )
    sides = tuple(
        _pair_side(side, database, root, tree_id, namespace)
        for side, database, root, tree_id in halves
    )
    return review_attribution(observed, sides=sides, subject=selected_subject(selector))


def _pair_without_namespace(
    resolved: ReviewCandidateResolution,
    observed: TreePaths,
    selector: KnowledgeReadSeed | None,
    failure: ReviewRefusal,
) -> SourceAttribution:
    """One side bound from the namespace its own bytes disclose, the other carrying the refusal.

    The pair's shared namespace comes from the candidate's sealed receipt, and when that record cannot
    be read the *candidate* half is the one that cannot be bound: a review reads a candidate through
    its admission, and substituting the namespace its dataset row happens to declare is the very
    substitution ``review_namespace`` refuses. The before half is a different matter -- it has no
    admission record to lose, and its own bytes disclose the namespace it is bound to, which
    ``open_diff_side`` verifies against them -- so it is bound and its registered mappings attribute
    their paths exactly as they do in every other single-sided state (``ICR-R04``: "a valid positive
    mapping on the readable side remains attributed with its incompleteness labeled"). The after side
    carries the refusal, and every path only it could have attributed stays undetermined.
    """

    after = AttributionSideInput(
        side="after", database=resolved.candidate_database, unreadable=failure.detail
    )
    before = _side_from_own_bytes(
        resolved.baseline_database,
        resolved.baseline_code_root,
        resolved.baseline_code_tree_id,
        failure.detail,
    )
    return review_attribution(observed, sides=(before, after), subject=selected_subject(selector))


def _side_from_own_bytes(
    database: Path,
    root: Path | None,
    tree_id: str | None,
    reason: str,
) -> AttributionSideInput:
    """One before half bound from the namespace its own dataset bytes record, or carrying ``reason``.

    The dataset is read as a dataset first (the shipped reader answers that with a reason rather than an
    exception), then its own recorded namespace is used as the binding ``open_diff_side`` verifies
    against those same bytes. Anything that fails along the way leaves the side unavailable with the
    reason the caller established, so this reader is total in every state.
    """

    reading = read_dataset_identity(database)
    if isinstance(reading, str):
        return AttributionSideInput(side="before", database=database, unreadable=reading)
    try:
        context = open_diff_side(
            database,
            reading.repository_id,
            repository_root=root,
            code_tree_id=tree_id,
        )
    except (KnowledgeStorageError, OSError, ValueError, apsw.Error):
        return AttributionSideInput(side="before", database=database, unreadable=reason)
    return AttributionSideInput(side="before", database=database, context=context)


def _pair_side(
    side: ReadSide,
    database: Path,
    root: Path | None,
    tree_id: str | None,
    namespace: str,
) -> AttributionSideInput:
    """One half of a resolved pair as an attribution side, or as a half nothing could be read from.

    The half is *read* as a dataset before a read context is bound to it, through the shipped reader
    that already answers "is this file a dataset of this code" with a reason rather than an exception.
    A half that fails that reading is carried with its reason: the partition then reports it as an
    uninspected snapshot, which is what keeps the other half's valid mappings attributed while
    preventing any negative conclusion about the paths it does not mention.
    """

    reading = read_dataset_identity(database)
    if isinstance(reading, str):
        return AttributionSideInput(side=side, database=database, unreadable=reading)
    try:
        context = open_diff_side(database, namespace, repository_root=root, code_tree_id=tree_id)
    except (KnowledgeStorageError, OSError, ValueError, apsw.Error) as error:
        return AttributionSideInput(
            side=side, database=database, unreadable=f"{type(error).__name__}: {error}"
        )
    return AttributionSideInput(side=side, database=database, context=context)
