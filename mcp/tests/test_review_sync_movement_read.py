"""The live review renders exact source-bound sync movement and corrupt measurement states."""

import json
from dataclasses import replace
from typing import Any

import pytest
from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_final_output_receipt import comparison_key
from agents_remember.application.review_sync_movement import (
    review_staleness_with_sync_movement,
    review_sync_movement,
)
from agents_remember.application.review_sync_rebinding import (
    rebinding_file_name,
    rebinding_result_block,
)
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.models.knowledge.review_final_output_receipt import tree_comparison_digest
from agents_remember.models.knowledge.review_staleness import ReviewStaleness, ReviewSyncMovement
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from pydantic import ValidationError
from test_review_final_output_receipt import tree_fixture
from test_review_sync_rebinding import SYNCED

__all__ = ["tree_fixture"]

pytestmark = pytest.mark.integration


def measured(resolved: ReviewCandidateResolution) -> ReviewSyncMovement:
    """The movement a recorded rebinding renders on the live review, asserted rather than assumed."""

    movement = review_sync_movement(resolved)
    assert movement is not None
    return movement


def test_live_review_labels_the_exact_memory_input_a_sync_moved(tree_fixture):
    contract = tree_fixture.contract
    trees = tree_fixture.trees()
    reviewed = trees.record
    (contract.memory_worktree / "notes/moved.md").write_text("moved\n")
    rebinding_result_block(contract, dict(SYNCED))
    movement = measured(tree_fixture.resolution(trees))
    assert movement.binding_state == "stale"
    assert movement.comparison == reviewed
    assert movement.moved_identities == (f"memory-candidate-tree:{reviewed.memory_candidate.tree}",)
    assert movement.resolved_candidate_memory_tree_id == tree_fixture.capture_memory()
    folded = review_staleness_with_sync_movement(
        ReviewStaleness(state="current", statement="current"), movement
    )
    assert folded.state == "stale"
    assert folded.previous_comparison_ref == movement.reviewed_binding_digest
    assert review_sync_movement(tree_fixture.resolution(replace(trees, live=False))) is None


def test_unreadable_or_forged_rebinding_is_unavailable_and_never_current(tree_fixture):
    contract = tree_fixture.contract
    trees = tree_fixture.trees()
    reviewed = trees.record
    block = rebinding_result_block(contract, dict(SYNCED))["review_rebinding"]
    evidence = block["evidence"]
    path = durable_reports_root(contract.task_root) / rebinding_file_name(
        contract.leaf_id, comparison_key(reviewed)
    )
    before = path.read_text()
    path.write_text("unreadable")
    assert measured(tree_fixture.resolution(trees)).binding_state == "unavailable"
    forged = json.loads(before)
    forged["comparison"]["recorded_at"] = "forged source"
    forged["comparison_digest"] = tree_comparison_digest(
        ReviewTreeComparisonRecord.model_validate(forged["comparison"])
    )
    path.write_text(json.dumps(forged))
    unavailable = measured(tree_fixture.resolution(trees))
    assert unavailable.binding_state == "unavailable" and not unavailable.record_readable
    assert evidence


def test_a_sync_movement_refuses_a_state_its_own_fields_contradict(tree_fixture):
    """The movement cannot report a measurement it did not make, or one of another comparison.

    The published stale movement is the accepted control, and each forgery departs from it in the
    one clause it names, so removing a clause makes its forgery read as a valid movement.
    """

    contract = tree_fixture.contract
    trees = tree_fixture.trees()
    (contract.memory_worktree / "notes/moved.md").write_text("moved\n")
    rebinding_result_block(contract, dict(SYNCED))
    published = measured(tree_fixture.resolution(trees)).model_dump(mode="json")
    assert ReviewSyncMovement.model_validate(published).binding_state == "stale"

    nothing_resolved: dict[str, Any] = {
        "moved_identities": [],
        "resolved_code_head": None,
        "resolved_candidate_code_tree_id": None,
        "resolved_candidate_memory_tree_id": None,
    }
    another_comparison = "must be those of the complete source comparison"
    forgeries: dict[str, tuple[dict[str, Any], str]] = {
        "a moved input beside a state that is not stale": (
            {"binding_state": "current"},
            "is stale, and this one records current",
        ),
        "a stale state with no moved input": (
            {"moved_identities": []},
            "a stale movement names the input that moved",
        ),
        "a stale state with no resolved identity": (
            {**nothing_resolved, "moved_identities": published["moved_identities"]},
            "a stale movement names the identities the sync resolved",
        ),
        "a resolved identity beside a state that is not stale": (
            {"binding_state": "current", "moved_identities": []},
            "a movement reported as current records no resolved identity",
        ),
        "a reason beside a measurement": (
            {"reason": "nothing was compared"},
            "a movement reported as stale carries no reason",
        ),
        "an absence with no reason": (
            {**nothing_resolved, "binding_state": "not-measured"},
            "a movement reported as not-measured names the reason behind the absence",
        ),
        "an unusable record reported as read": (
            {
                **nothing_resolved,
                "binding_state": "unavailable",
                "reason": "the record could not be read",
            },
            "a movement reported as unavailable records record_readable=True",
        ),
        "a measured record reported as unread": (
            {"record_readable": False},
            "a movement reported as stale records record_readable=False",
        ),
        "a measurement with no source comparison": (
            {"comparison": None},
            "a measured tree movement carries its source comparison",
        ),
        "the digest of another comparison": (
            {"reviewed_binding_digest": "0" * 64},
            another_comparison,
        ),
        "the code tree of another comparison": (
            {"reviewed_candidate_code_tree_id": "0" * 40},
            another_comparison,
        ),
        "the memory tree of another comparison": (
            {"reviewed_candidate_memory_tree_id": "0" * 40},
            another_comparison,
        ),
        # The retired generation and dataset identities are not fields of the movement at all.
        "a generation identity": (
            {"generation_id": "123e4567-e89b-12d3-a456-426614174000"},
            "Extra inputs are not permitted",
        ),
        "a dataset identity": (
            {"reviewed_knowledge_logical_digest": "0" * 64},
            "Extra inputs are not permitted",
        ),
        "another movement version": (
            {"movement_version": "ar-review-sync-movement/v1"},
            "Input should be 'ar-review-sync-movement/v2'",
        ),
    }
    for why, (fields, refusal) in forgeries.items():
        with pytest.raises(ValidationError, match=refusal):
            ReviewSyncMovement.model_validate({**published, **fields})
            pytest.fail(f"a movement was built with {why}")
