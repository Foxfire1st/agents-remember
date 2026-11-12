"""MIK-R116 tests for the small fixed-list review state machine.

The limit comes from the settings (five when unnamed); a round beyond it needs
a recorded architect or developer approval; a check of a repaired Worker record
alone, with the candidate unchanged, is no round.
"""

from __future__ import annotations

import pytest
from agents_remember.tasks import ReviewState, TaskDocument
from agents_remember.worktrees.review_history import (
    ReviewHistoryError,
    begin_task_review,
    record_task_review,
    require_pending_review,
)

CANDIDATE = "ab" * 20
CHANGED_CANDIDATE = "cd" * 20
# The authority-resolved binding a task-doc layer would supply for an unchanged
# fixture candidate; the pure state machine compares it, never resolves it.
ACTUAL = "candidate-v1:unchanged-fixture"
CHANGED_ACTUAL = "candidate-v1:mutated-fixture"


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
    assert first.reviewState.limit == 5
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
    assert started.reviewState.limit == 5


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
        actual_candidate=ACTUAL,
    )
    assert baseline.reviewState is not None
    assert baseline.reviewState.remainingFindingIds == ["A", "B"]

    state = baseline
    for expected_round in (2, 3, 4, 5):
        state = begin_task_review(state)
        assert state.reviewState is not None
        assert state.reviewState.round == expected_round
        assert state.reviewState.limit == 5
        state = record_task_review(
            state,
            {"verdict": "block", "remainingFindingIds": ["B"]},
            actual_candidate=ACTUAL,
        )
    assert state.reviewState is not None
    assert state.reviewState.round == 5
    assert state.reviewState.remainingFindingIds == ["B"]
    assert state.reviewState.baselineFindings[0].description == "first"

    final = record_task_review(
        state,
        {"verdict": "pass", "remainingFindingIds": []},
        actual_candidate=ACTUAL,
    )
    assert final.reviewState is not None
    assert final.reviewState.round == 5
    assert final.reviewState.remainingFindingIds == []

    # A sealed clean review is not permanent: a later candidate may open a new round
    # (bounded by the settings budget), and the sealed definitions survive.
    with pytest.raises(ReviewHistoryError, match="count=5, limit=5") as budget:
        begin_task_review(final)
    assert budget.value.status == "review-budget-exhausted"

    reopened = begin_task_review(
        final,
        {
            "approval": {"by": "architect", "text": "Sealed list shrank; repair is concrete."},
            "additionalRounds": 1,
        },
    )
    assert reopened.reviewState is not None
    assert reopened.reviewState.round == 6
    assert reopened.reviewState.pending is True
    assert reopened.reviewState.limit == 6
    assert reopened.reviewState.approval is not None
    assert reopened.reviewState.approval.by == "architect"
    assert reopened.reviewState.developerApproval is None
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


def _state_with_five_unresolved_rounds() -> TaskDocument:
    state = record_task_review(
        begin_task_review(_document()),
        {
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "first"}],
        },
    )
    for _ in range(4):
        state = record_task_review(
            begin_task_review(state),
            {"verdict": "block", "remainingFindingIds": ["A"]},
        )
    return state


def test_default_five_round_limit_refuses_sixth_with_actionable_count() -> None:
    exhausted = _state_with_five_unresolved_rounds()
    with pytest.raises(ReviewHistoryError, match="count=5, limit=5") as refusal:
        begin_task_review(exhausted)
    assert refusal.value.status == "review-budget-exhausted"
    assert exhausted.reviewState is not None
    assert exhausted.reviewState.round == 5
    assert exhausted.reviewState.limit == 5
    assert exhausted.reviewState.remainingFindingIds == ["A"]


def test_explicit_limit_param_sets_the_cap() -> None:
    state = record_task_review(
        begin_task_review(_document(), limit=2),
        {
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "first"}],
        },
    )
    assert state.reviewState is not None
    assert state.reviewState.limit == 2
    second = record_task_review(
        begin_task_review(state, limit=2),
        {"verdict": "block", "remainingFindingIds": ["A"]},
    )
    assert second.reviewState is not None
    assert second.reviewState.round == 2
    with pytest.raises(ReviewHistoryError, match="count=2, limit=2") as refusal:
        begin_task_review(second, limit=2)
    assert refusal.value.status == "review-budget-exhausted"


def test_limit_follows_the_settings_number_named_at_each_begin() -> None:
    state = record_task_review(
        begin_task_review(_document(), limit=8),
        {
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "first"}],
        },
    )
    for expected_round in range(2, 9):
        state = record_task_review(
            begin_task_review(state, limit=8),
            {"verdict": "block", "remainingFindingIds": ["A"]},
        )
        assert state.reviewState is not None
        assert state.reviewState.round == expected_round
        assert state.reviewState.limit == 8
    with pytest.raises(ReviewHistoryError, match="count=8, limit=8"):
        begin_task_review(state, limit=8)


def test_one_explicit_extra_round_preserves_count_and_issue_list_then_refuses_next() -> None:
    exhausted = _state_with_five_unresolved_rounds()
    authorized = begin_task_review(
        exhausted,
        {
            "developerApproval": "Developer authorizes one extra fix verification.",
            "additionalRounds": 1,
        },
    )
    assert authorized.reviewState is not None
    assert authorized.reviewState.round == 6
    assert authorized.reviewState.pending is True
    assert authorized.reviewState.limit == 6
    assert authorized.reviewState.additionalRounds == 1
    # A legacy approval is recorded in the approval shape, naming the developer.
    assert authorized.reviewState.approval is not None
    assert authorized.reviewState.approval.by == "developer"
    assert (
        authorized.reviewState.approval.text == "Developer authorizes one extra fix verification."
    )
    assert authorized.reviewState.developerApproval is None
    assert authorized.reviewState.remainingFindingIds == ["A"]
    assert [item.findingId for item in authorized.reviewState.baselineFindings] == ["A"]

    completed = record_task_review(
        authorized,
        {"verdict": "block", "remainingFindingIds": ["A"]},
    )
    with pytest.raises(ReviewHistoryError, match="count=6, limit=6") as refusal:
        begin_task_review(completed)
    assert refusal.value.status == "review-budget-exhausted"
    assert completed.reviewState is not None
    assert completed.reviewState.round == 6
    assert completed.reviewState.remainingFindingIds == ["A"]

    reauthorized = begin_task_review(
        completed,
        {
            "approval": {
                "by": "developer",
                "text": "Developer authorizes one further verification.",
            },
            "additionalRounds": 1,
        },
    )
    assert reauthorized.reviewState is not None
    assert reauthorized.reviewState.round == 7
    assert reauthorized.reviewState.limit == 7
    assert reauthorized.reviewState.additionalRounds == 2
    assert reauthorized.reviewState.remainingFindingIds == ["A"]


def test_legacy_developer_approval_record_stays_readable_and_counts() -> None:
    legacy = ReviewState.model_validate(
        {
            "round": 6,
            "pending": False,
            "baselineFindings": [{"findingId": "A", "description": "first"}],
            "remainingFindingIds": ["A"],
            "developerApproval": "Developer authorized one extra fix verification.",
            "additionalRounds": 1,
        }
    )
    assert legacy.limit == 0
    document = _document().model_copy(update={"reviewState": legacy})
    with pytest.raises(ReviewHistoryError, match="count=6, limit=6") as refusal:
        begin_task_review(document)
    assert refusal.value.status == "review-budget-exhausted"


@pytest.mark.parametrize(
    "payload",
    [
        {"additionalRounds": 1},
        {"developerApproval": "  ", "additionalRounds": 1},
        {"developerApproval": "approved", "additionalRounds": 0},
        {"developerApproval": "approved", "additionalRounds": True},
        {"approval": {"by": "manager", "text": "words"}, "additionalRounds": 1},
        {"approval": {"by": "architect", "text": "  "}, "additionalRounds": 1},
        {"approval": {"by": "architect"}, "additionalRounds": 1},
        {
            "approval": {"by": "architect", "text": "words"},
            "developerApproval": "words",
            "additionalRounds": 1,
        },
        {"approval": {"by": "architect", "text": "words"}},
    ],
)
def test_invalid_approval_leaves_exhausted_state_unchanged(
    payload: dict[str, object],
) -> None:
    exhausted = _state_with_five_unresolved_rounds()
    before = exhausted.reviewState
    assert before is not None
    with pytest.raises(ReviewHistoryError, match=r"developer|additionalRounds|approval"):
        begin_task_review(exhausted, payload)
    assert exhausted.reviewState == before


def _recorded_round_with_candidate() -> TaskDocument:
    pending = begin_task_review(_document())
    return record_task_review(
        pending,
        {
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "first"}],
            "candidateTree": CANDIDATE,
        },
        actual_candidate=ACTUAL,
    )


def test_record_only_check_on_unchanged_candidate_is_no_round() -> None:
    recorded = _recorded_round_with_candidate()
    assert recorded.reviewState is not None
    assert recorded.reviewState.candidateTree == CANDIDATE
    assert recorded.reviewState.candidateIdentity == ACTUAL
    checked = record_task_review(
        recorded,
        {"verdict": "pass", "remainingFindingIds": [], "candidateTree": CANDIDATE},
        actual_candidate=ACTUAL,
    )
    assert checked.reviewState is not None
    assert checked.reviewState.round == 1
    assert checked.reviewState.pending is False
    assert checked.reviewState.remainingFindingIds == []
    assert checked.reviewState.candidateTree == CANDIDATE


def test_record_only_check_on_changed_candidate_names_begin_review() -> None:
    recorded = _recorded_round_with_candidate()
    with pytest.raises(ReviewHistoryError, match="begin_review") as refusal:
        record_task_review(
            recorded,
            {"verdict": "pass", "remainingFindingIds": [], "candidateTree": CHANGED_CANDIDATE},
        )
    assert refusal.value.status == "review-candidate-changed"
    with pytest.raises(ReviewHistoryError, match="begin_review") as missing:
        record_task_review(recorded, {"verdict": "pass", "remainingFindingIds": []})
    assert missing.value.status == "review-candidate-changed"


def test_record_only_check_still_shrinks_only_and_rejects_bad_identity() -> None:
    recorded = _recorded_round_with_candidate()
    with pytest.raises(ReviewHistoryError, match="subset"):
        record_task_review(
            recorded,
            {"verdict": "block", "remainingFindingIds": ["B"], "candidateTree": CANDIDATE},
            actual_candidate=ACTUAL,
        )
    with pytest.raises(ReviewHistoryError, match="candidateTree"):
        record_task_review(
            recorded,
            {"verdict": "pass", "remainingFindingIds": [], "candidateTree": "not-a-tree"},
        )


def test_same_round_record_refuses_a_changed_actual_despite_a_replayed_claim() -> None:
    recorded = _recorded_round_with_candidate()
    with pytest.raises(ReviewHistoryError, match="begin_review") as refusal:
        record_task_review(
            recorded,
            {"verdict": "pass", "remainingFindingIds": [], "candidateTree": CANDIDATE},
            actual_candidate=CHANGED_ACTUAL,
        )
    assert refusal.value.status == "review-candidate-changed"
    assert recorded.reviewState is not None
    assert recorded.reviewState.round == 1
    assert recorded.reviewState.remainingFindingIds == ["A"]


def test_same_round_record_refuses_without_a_recorded_binding() -> None:
    pending = begin_task_review(_document())
    unbound = record_task_review(
        pending,
        {
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "first"}],
            "candidateTree": CANDIDATE,
        },
    )
    assert unbound.reviewState is not None
    assert unbound.reviewState.candidateIdentity is None
    with pytest.raises(ReviewHistoryError, match="begin_review") as refusal:
        record_task_review(
            unbound,
            {"verdict": "pass", "remainingFindingIds": [], "candidateTree": CANDIDATE},
            actual_candidate=ACTUAL,
        )
    assert refusal.value.status == "review-candidate-changed"


def test_same_round_record_refuses_an_unresolvable_current_identity() -> None:
    recorded = _recorded_round_with_candidate()
    with pytest.raises(ReviewHistoryError, match="begin_review") as refusal:
        record_task_review(
            recorded,
            {"verdict": "pass", "remainingFindingIds": [], "candidateTree": CANDIDATE},
            actual_candidate=None,
        )
    assert refusal.value.status == "review-candidate-changed"


def test_clean_recorded_round_does_not_reseal_new_findings_without_begin() -> None:
    pending = begin_task_review(_document())
    clean = record_task_review(
        pending, {"verdict": "pass", "findings": []}, actual_candidate=ACTUAL
    )
    assert clean.reviewState is not None
    assert clean.reviewState.pending is False
    with pytest.raises(ReviewHistoryError, match=r"new findings|same round|begin_review"):
        record_task_review(
            clean,
            {
                "verdict": "block",
                "findings": [{"findingId": "NEW", "description": "new criterion"}],
            },
            actual_candidate=ACTUAL,
        )
    assert clean.reviewState.round == 1
    assert clean.reviewState.remainingFindingIds == []


def test_pending_branch_persists_the_binding_for_the_round() -> None:
    pending = begin_task_review(_document())
    recorded = record_task_review(
        pending,
        {
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "first"}],
        },
        actual_candidate=ACTUAL,
    )
    assert recorded.reviewState is not None
    assert recorded.reviewState.candidateIdentity == ACTUAL


def test_direct_record_backfills_the_settings_limit() -> None:
    recorded = record_task_review(
        _document(),
        {"verdict": "block", "findings": [{"findingId": "A", "description": "issue"}]},
        settings_limit=5,
    )
    assert recorded.reviewState is not None
    assert recorded.reviewState.limit == 5
    assert recorded.reviewState.round == 1


def test_legacy_zero_limit_record_backfills_settings_plus_extra_rounds() -> None:
    document = TaskDocument.model_validate(
        _document().model_dump(mode="json")
        | {
            "reviewState": {
                "round": 2,
                "pending": True,
                "baselineFindings": [{"findingId": "A", "description": "issue"}],
                "remainingFindingIds": ["A"],
                "limit": 0,
                "developerApproval": "two extra rounds",
                "additionalRounds": 2,
            }
        }
    )
    recorded = record_task_review(
        document,
        {"verdict": "block", "remainingFindingIds": ["A"]},
        settings_limit=5,
        actual_candidate=ACTUAL,
    )
    assert recorded.reviewState is not None
    assert recorded.reviewState.limit == 7
    assert recorded.reviewState.additionalRounds == 2
    assert recorded.reviewState.developerApproval == "two extra rounds"
    assert recorded.reviewState.remainingFindingIds == ["A"]


def test_record_never_rewrites_a_stored_post_limit_number() -> None:
    begun = begin_task_review(_document())
    assert begun.reviewState is not None
    assert begun.reviewState.limit == 5
    kept = record_task_review(
        begun,
        {
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "first"}],
        },
        settings_limit=9,
        actual_candidate=ACTUAL,
    )
    assert kept.reviewState is not None
    assert kept.reviewState.limit == 5


def test_zero_round_state_rejects_a_candidate_binding() -> None:
    with pytest.raises(Exception, match="candidate"):
        TaskDocument.model_validate(
            _document().model_dump(mode="json")
            | {"reviewState": {"round": 0, "candidateIdentity": ACTUAL}}
        )
