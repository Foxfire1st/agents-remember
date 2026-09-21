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

from agents_remember.application.knowledge_before_half import unreadable_half_refusal
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    candidate_ref,
    missing_dataset_half,
    require_current_candidate_identity,
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
    inventory_limitations,
    source_pane,
)
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

__all__ = ["task_context_review"]


def task_context_review(
    resolved: ReviewCandidateResolution,
    request: ReviewSurfaceRequest,
    records: ReviewRecordInputs,
    inventory: ReviewSourceInventory,
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
    """

    moved = require_current_candidate_identity(resolved)
    if moved is not None:
        return refused(request.repository_id, moved)
    unreadable = unreadable_half_refusal(resolved.baseline_database, resolved.candidate_database)
    records = with_selection_channels(records, (), selected=False)
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
            source=source_pane(None, inventory),
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
