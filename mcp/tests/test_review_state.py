"""Focused R27 tests for the small fixed-list review state machine."""

from __future__ import annotations

import pytest
from agents_remember.tasks import TaskDocument
from agents_remember.worktrees.review_history import (
    ReviewHistoryError,
    begin_task_review,
    record_task_review,
    require_pending_review,
)


def _document() -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": "review-state",
            "slug": "review-state",
            "title": "Review state fixture",
            "kind": "light",
            "repo": "fixture",
            "createdAt": "2026-09-08T00:00:00Z",
            "objective": "exercise the bounded review state",
            "requirements": ["The review state converges monotonically."],
        }
    )


def test_missing_state_starts_round_one_and_replay_is_pending_idempotent() -> None:
    first = begin_task_review(_document())
    assert first.reviewState is not None
    assert first.reviewState.round == 1
    assert first.reviewState.pending is True
    assert begin_task_review(first).reviewState == first.reviewState
    assert require_pending_review(first) == first.reviewState

    replay_with_exception = begin_task_review(
        first,
        {"developerApproval": "one extra round", "additionalRounds": 1},
    )
    assert replay_with_exception.reviewState == first.reviewState
    assert replay_with_exception.reviewState is not None
    assert replay_with_exception.reviewState.additionalRounds == 0


def test_explicit_zero_state_starts_round_one() -> None:
    document = TaskDocument.model_validate(
        _document().model_dump(mode="json") | {"reviewState": {}}
    )
    started = begin_task_review(document)
    assert started.reviewState is not None
    assert started.reviewState.round == 1
    assert started.reviewState.pending is True


def test_baseline_seals_findings_and_successors_only_shrink_remaining() -> None:
    pending = begin_task_review(_document())
    baseline = record_task_review(
        pending,
        {
            "verdict": "block",
            "findings": [
                {"findingId": "A", "description": "first"},
                {"findingId": "B", "description": "second"},
            ],
        },
    )
    assert baseline.reviewState is not None
    assert baseline.reviewState.remainingFindingIds == ["A", "B"]

    successor = begin_task_review(baseline)
    successor = record_task_review(
        successor,
        {"verdict": "block", "remainingFindingIds": ["B"]},
    )
    assert successor.reviewState is not None
    assert successor.reviewState.round == 2
    assert successor.reviewState.remainingFindingIds == ["B"]
    assert successor.reviewState.baselineFindings[0].description == "first"

    final = begin_task_review(successor)
    final = record_task_review(final, {"verdict": "pass", "remainingFindingIds": []})
    assert final.reviewState is not None
    assert final.reviewState.round == 3
    assert final.reviewState.remainingFindingIds == []

    # A sealed clean review is not permanent: a later candidate may open a new round
    # (bounded by the ordinary three-round budget), and the sealed definitions survive.
    with pytest.raises(ReviewHistoryError, match="count=3, limit=3") as budget:
        begin_task_review(final)
    assert budget.value.status == "review-budget-exhausted"

    reopened = begin_task_review(
        final,
        {
            "developerApproval": "Developer authorizes one more verification round.",
            "additionalRounds": 1,
        },
    )
    assert reopened.reviewState is not None
    assert reopened.reviewState.round == 4
    assert reopened.reviewState.pending is True
    assert reopened.reviewState.baselineFindings == final.reviewState.baselineFindings
    assert reopened.reviewState.remainingFindingIds == []


def test_successor_rejects_new_duplicate_reintroduced_and_unresolved_passing_ids() -> None:
    baseline = record_task_review(
        begin_task_review(_document()),
        {
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "first"}],
        },
    )
    successor = begin_task_review(baseline)
    with pytest.raises(ReviewHistoryError, match="subset"):
        record_task_review(successor, {"verdict": "block", "remainingFindingIds": ["B"]})

    successor = begin_task_review(baseline)
    with pytest.raises(ReviewHistoryError, match="unique"):
        record_task_review(
            successor,
            {"verdict": "block", "remainingFindingIds": ["A", "A"]},
        )

    successor = begin_task_review(baseline)
    with pytest.raises(ReviewHistoryError, match="passing"):
        record_task_review(successor, {"verdict": "pass", "remainingFindingIds": ["A"]})


def test_record_without_begin_starts_the_baseline_and_still_requires_a_verdict() -> None:
    recorded = record_task_review(_document(), {"verdict": "block", "findings": []})
    assert recorded.reviewState is not None
    assert recorded.reviewState.round == 1
    assert recorded.reviewState.pending is False
    pending = begin_task_review(_document())
    with pytest.raises(ReviewHistoryError, match="requires verdict"):
        record_task_review(pending, {"findings": []})


def _state_with_three_unresolved_rounds() -> TaskDocument:
    state = record_task_review(
        begin_task_review(_document()),
        {
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "first"}],
        },
    )
    for _ in range(2):
        state = record_task_review(
            begin_task_review(state),
            {"verdict": "block", "remainingFindingIds": ["A"]},
        )
    return state


def test_default_three_round_limit_refuses_fourth_with_actionable_count() -> None:
    exhausted = _state_with_three_unresolved_rounds()
    with pytest.raises(ReviewHistoryError, match="count=3, limit=3") as refusal:
        begin_task_review(exhausted)
    assert refusal.value.status == "review-budget-exhausted"
    assert exhausted.reviewState is not None
    assert exhausted.reviewState.round == 3
    assert exhausted.reviewState.remainingFindingIds == ["A"]


def test_one_explicit_extra_round_preserves_count_and_issue_list_then_refuses_fifth() -> None:
    exhausted = _state_with_three_unresolved_rounds()
    authorized = begin_task_review(
        exhausted,
        {
            "developerApproval": "Developer authorizes one extra fix verification.",
            "additionalRounds": 1,
        },
    )
    assert authorized.reviewState is not None
    assert authorized.reviewState.round == 4
    assert authorized.reviewState.pending is True
    assert authorized.reviewState.additionalRounds == 1
    assert authorized.reviewState.developerApproval == (
        "Developer authorizes one extra fix verification."
    )
    assert authorized.reviewState.remainingFindingIds == ["A"]
    assert [item.findingId for item in authorized.reviewState.baselineFindings] == ["A"]

    completed = record_task_review(
        authorized,
        {"verdict": "block", "remainingFindingIds": ["A"]},
    )
    with pytest.raises(ReviewHistoryError, match="count=4, limit=4") as refusal:
        begin_task_review(completed)
    assert refusal.value.status == "review-budget-exhausted"
    assert completed.reviewState is not None
    assert completed.reviewState.round == 4
    assert completed.reviewState.remainingFindingIds == ["A"]

    reauthorized = begin_task_review(
        completed,
        {
            "developerApproval": "Developer authorizes one further verification.",
            "additionalRounds": 1,
        },
    )
    assert reauthorized.reviewState is not None
    assert reauthorized.reviewState.round == 5
    assert reauthorized.reviewState.additionalRounds == 2
    assert reauthorized.reviewState.remainingFindingIds == ["A"]


@pytest.mark.parametrize(
    "payload",
    [
        {"additionalRounds": 1},
        {"developerApproval": "  ", "additionalRounds": 1},
        {"developerApproval": "approved", "additionalRounds": 0},
        {"developerApproval": "approved", "additionalRounds": True},
    ],
)
def test_invalid_developer_exception_leaves_exhausted_state_unchanged(
    payload: dict[str, object],
) -> None:
    exhausted = _state_with_three_unresolved_rounds()
    before = exhausted.reviewState
    assert before is not None
    with pytest.raises(ReviewHistoryError, match=r"developer|additionalRounds"):
        begin_task_review(exhausted, payload)
    assert exhausted.reviewState == before
