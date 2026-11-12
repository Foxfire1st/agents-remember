"""Render exact managed-sync tree measurements on the live review surface (ICR-R22)."""

from __future__ import annotations

from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_final_output_receipt import (
    comparison_key,
    select_review_comparison,
)
from agents_remember.application.review_sync_rebinding import (
    read_review_sync_rebinding,
    rebinding_names_the_generation,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.review_external_movement import ExternalGitMovement
from agents_remember.models.knowledge.review_final_output_receipt import tree_comparison_digest
from agents_remember.models.knowledge.review_staleness import (
    ReviewStaleness,
    ReviewSyncMovement,
    ReviewSyncMovementState,
)
from agents_remember.models.knowledge.review_sync_rebinding import (
    ReviewSyncRebinding,
    ReviewSyncRebindingVerdict,
)
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.worktrees.worktree_contract import WorktreeContract

_SUPERSESSION = "Open and record a successor tree comparison for the resolved pair; retain the reviewed source comparison as previous input."


def review_sync_movement(resolved: ReviewCandidateResolution) -> ReviewSyncMovement | None:
    contract = resolved.contract
    if (
        contract is None
        or contract.kind != "leaf"
        or resolved.closed_leaf is not None
        or (resolved.trees is not None and not resolved.trees.live)
    ):
        return None
    try:
        return _measured(contract)
    except (KnowledgeStorageError, OSError, RuntimeError, ValueError) as error:
        return _unavailable(None, str(error))


def _measured(contract: WorktreeContract) -> ReviewSyncMovement | None:
    selection = select_review_comparison(contract.task_root, contract.leaf_id)
    comparison = selection.comparison
    if comparison is None:
        return None if selection.state == "no-comparison" else _unavailable(None, selection.detail)
    read = read_review_sync_rebinding(
        contract.task_root, contract.leaf_id, comparison_key(comparison)
    )
    if read.state == "not-recorded":
        return None
    record = rebinding_names_the_generation(read, comparison)
    if record is None:
        return _unavailable(comparison, read.detail)
    return _project(record)


def _source_fields(comparison: ReviewTreeComparisonRecord | None) -> dict:
    return {
        "movement_version": "ar-review-sync-movement/v2",
        "comparison": comparison,
        "reviewed_binding_digest": None
        if comparison is None
        else tree_comparison_digest(comparison),
        "reviewed_candidate_code_tree_id": None
        if comparison is None
        else comparison.code_candidate.tree,
        "reviewed_candidate_memory_tree_id": None
        if comparison is None
        else comparison.memory_candidate.tree,
        "successor_action": _SUPERSESSION,
    }


def _unavailable(comparison: ReviewTreeComparisonRecord | None, reason: str) -> ReviewSyncMovement:
    return ReviewSyncMovement(
        binding_state="unavailable",
        reason=reason,
        statement=f"No usable exact code and memory tree measurement is available: {reason}",
        record_readable=False,
        **_source_fields(comparison),
    )


def _project(record: ReviewSyncRebinding) -> ReviewSyncMovement:
    code_moved = record.code_match == "differs-from-reviewed-input"
    memory_moved = record.memory_match == "differs-from-reviewed-input"
    states: dict[ReviewSyncRebindingVerdict, ReviewSyncMovementState] = {
        "current": "current",
        "moved": "stale",
        "unmeasured": "not-measured",
    }
    moved = tuple(
        name
        for name, happened in (
            (f"code-candidate-tree:{record.comparison.code_candidate.tree}", code_moved),
            (f"memory-candidate-tree:{record.comparison.memory_candidate.tree}", memory_moved),
        )
        if happened
    )
    return ReviewSyncMovement(
        binding_state=states[record.state],
        moved_identities=moved,
        reason=record.memory_detail if record.state == "unmeasured" else None,
        statement=record.statement(),
        resolved_code_head=record.resolved_code_head if code_moved else None,
        resolved_candidate_code_tree_id=record.resolved_candidate_code_tree_id
        if code_moved
        else None,
        resolved_candidate_memory_tree_id=record.resolved_candidate_memory_tree_id
        if memory_moved
        else None,
        **_source_fields(record.comparison),
    )


def review_staleness_with_external_movement(
    staleness: ReviewStaleness,
    movement: ExternalGitMovement | None,
    sync_movement: ReviewSyncMovement | None,
) -> ReviewStaleness:
    """Fold a measured raw-Git movement into the staleness a review publishes (ICR-R23@v1).

    **A replaced identity is the strongest fact either measurement can report**, and it outranks both
    the reader's carried comparison identity and a recorded managed-sync rebinding: R17's ``current``
    means "the displayed comparison is the candidate's current comparison", which stops being true the
    moment the branch was rewritten under it, and a rebinding that a *later* raw operation has already
    invalidated describes a pair the leaf no longer holds. The packet's failure clause is exactly this
    state -- an unrecognized transition reusing stale assessment as current -- so the movement both
    sets ``stale`` and publishes the exact identities that were replaced.

    **An absence is folded too, and it is not promoted into a movement.** When the boundary reports
    ``not-measured`` or ``unavailable`` -- a checkout that left its declared branch, a recorded object
    that is gone, a generation that could not be read -- this surface cannot claim the displayed
    comparison is the candidate's current one either, because the comparison was composed from
    whatever the checkout holds and nothing compared it against the leaf's declared identities. It
    becomes ``not-measured``, carrying the boundary's own sentence and reason, and it is deliberately
    *not* ``stale``: no movement was observed, and ``stale`` additionally disables submission, which
    is a consequence the absence has not earned. A recorded *measurement* of movement still wins over
    the absence -- R22's rebinding measured a moved input, and one measured movement outranks one
    unperformed comparison.
    """

    if movement is not None and movement.binding_state == "stale":
        return ReviewStaleness(
            state="stale",
            statement=movement.statement,
            previous_comparison_ref=(
                staleness.previous_comparison_ref or movement.reviewed_binding_digest
            ),
            moved=movement.moved_identities,
        )
    folded = review_staleness_with_sync_movement(staleness, sync_movement)
    if (
        movement is not None
        and movement.binding_state in {"not-measured", "unavailable"}
        and folded.state != "stale"
    ):
        return ReviewStaleness(state="not-measured", statement=movement.statement)
    return folded


def review_staleness_with_sync_movement(
    staleness: ReviewStaleness, movement: ReviewSyncMovement | None
) -> ReviewStaleness:
    """Fold a measured sync movement into the staleness a review publishes.

    **The movement outranks the reader's carried identity**, because it is the stronger fact: a
    recorded sync that moved the reviewed inputs means the review does not describe the pair whatever
    the reader was shown, and R17's ``current`` -- "the displayed comparison is the candidate's current
    comparison" -- would let the moved review keep reading as untouched, which is the packet's own
    non-conforming example. Submission follows automatically: the payload's constructor refuses a
    ``stale`` comparison offered for submission.

    The previous input labelled here is a real one in both cases: the identity the reader carried when
    they carried a mismatching one, and otherwise the comparison identity the reviewed generation
    bound -- never an identity nobody held. A movement that measured agreement changes nothing.
    """

    if movement is None:
        return staleness
    if movement.binding_state in {"not-measured", "unavailable"} and staleness.state != "stale":
        return ReviewStaleness(state="not-measured", statement=movement.statement)
    if movement.binding_state != "stale":
        return staleness
    carried = staleness.previous_comparison_ref
    return ReviewStaleness(
        state="stale",
        statement=movement.statement,
        previous_comparison_ref=carried or movement.reviewed_binding_digest,
        moved=movement.moved_identities,
    )
