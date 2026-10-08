"""The raw Git boundary uses recorded commit evidence and names absent work-head proof."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.review_external_git_movement import (
    external_git_movement,
    external_git_movement_for_contract,
    external_git_movement_result_block,
    render_git_transition_support,
    supported_recovery,
)
from agents_remember.application.review_tree_comparison import comparison_directory
from agents_remember.models.knowledge.review_external_movement import ExternalGitMovement
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.worktrees.worktree_contract import WorktreeContract
from pydantic import ValidationError
from test_review_final_output_receipt import commit_file, git, tree_fixture

__all__ = ["tree_fixture"]

pytestmark = pytest.mark.integration


def measured(contract: WorktreeContract) -> ExternalGitMovement:
    """The boundary's report for a leaf that recorded a comparison, asserted rather than assumed."""

    movement = external_git_movement_for_contract(contract)
    assert movement is not None
    return movement


def record_committed_candidate(tree_fixture) -> ReviewTreeComparisonRecord:
    """Record a comparison whose code candidate is a commit, so the work branch can be measured.

    A candidate carries a commit only when the source branch's tip holds exactly its tree. The
    fixture's uncommitted code edit is therefore discarded: the leaf then changes memory only, and
    every declared identity stands at its recorded commit.
    """

    contract = tree_fixture.contract
    git(contract.code_worktree, "restore", "reviewed.py")
    reviewed = tree_fixture.record()
    assert reviewed.code_candidate.commit == contract.code_base_commit
    return reviewed


def test_uncommitted_tree_record_never_invents_an_observed_work_branch_head(tree_fixture):
    contract = tree_fixture.contract
    trees = tree_fixture.trees()
    reviewed = trees.record
    assert reviewed.code_candidate.commit is None
    movement = measured(contract)
    assert movement.binding_state == "not-measured"
    assert movement.comparison == reviewed
    assert movement.reason is not None
    assert "not an observed work-branch head" in movement.reason
    # The worktree is still on its declared branch, so the unmeasured head is not called a switch.
    assert movement.transitions == ()
    assert supported_recovery("cherry-pick").reconciliation == "unsupported"
    assert supported_recovery("revert").reconciliation == "unsupported"
    # The review read publishes the same measurement for the live comparison and none for a
    # comparison that is no longer live.
    assert external_git_movement(tree_fixture.resolution(trees)) == movement
    assert external_git_movement(tree_fixture.resolution(replace(trees, live=False))) is None


def test_raw_history_replacement_reports_retained_source_without_fabricated_uuid(tree_fixture):
    contract = tree_fixture.contract
    code = commit_file(contract.code_worktree, "reviewed.py", "VALUE = 2\n")
    git(contract.code_worktree, "branch", "-f", "main", code)
    reviewed = tree_fixture.record()
    assert reviewed.code_candidate.commit == code
    assert measured(contract).binding_state == "current"
    # The source branch is replaced with unrelated history, without changing the retained record.
    git(contract.code_worktree, "switch", "-q", "--orphan", "replacement")
    commit_file(contract.code_worktree, "replacement.py", "VALUE = 7\n")
    git(contract.code_worktree, "branch", "-f", "main", "HEAD")
    git(contract.code_worktree, "switch", "-q", "leaf")
    result = external_git_movement_result_block(contract, {"ok": True})["external_git_movement"]
    assert result["binding_state"] == "stale"
    # The retired generation identity is not a key of the block: nothing can fabricate one.
    assert "generation_id" not in result and "generation_index" not in result
    assert result["comparison"]["code_base"]["commit"] == reviewed.code_base.commit
    assert f"declared-source-branch:{reviewed.code_base.commit}" in result["moved_identities"]
    assert comparison_directory(contract.task_root, contract.leaf_id).is_dir()


def test_corrupt_source_and_absent_boundary_keep_their_distinct_result_states(tree_fixture):
    contract = tree_fixture.contract
    absent = external_git_movement_result_block(contract, {"ok": True})["external_git_movement"]
    assert absent["state"] == "not-measured"
    reviewed = tree_fixture.record()
    (
        comparison_directory(contract.task_root, contract.leaf_id) / f"{reviewed.number}.json"
    ).write_text("corrupt")
    unavailable = measured(contract)
    assert unavailable.binding_state == "unavailable"
    assert unavailable.reviewed_binding_digest is None


def test_documented_support_matrix_matches_the_measured_operations():
    docs = Path(__file__).resolve().parents[2] / "docs/reference/worktrees-c09.md"
    assert render_git_transition_support() in docs.read_text()


def test_work_branch_ancestry_tells_an_untouched_branch_from_an_advance_and_a_rewrite(
    tree_fixture,
):
    """The recorded candidate commit is compared with the work branch tip, and each answer differs.

    Untouched, every declared identity stands at its record, which is the only state that reports
    ``unchanged``. Advanced, the recorded commit is still in the branch's history, so nothing was
    replaced and the review stays current. Rewritten, the recorded commit is no longer in that
    history, and only this one makes the review stale and names the identity that was replaced.
    """

    contract = tree_fixture.contract
    reviewed = record_committed_candidate(tree_fixture)
    recorded = reviewed.code_candidate.commit
    assert recorded is not None

    untouched = measured(contract)
    assert untouched.binding_state == "current"
    assert untouched.transitions == ("unchanged",)
    assert untouched.transition_evidence == (
        ("code-work-branch", "current"),
        ("declared-source-branch", "current"),
        ("memory-work-branch", "current"),
    )
    assert untouched.moved_identities == () and untouched.reason is None
    assert untouched.observed_code_work_branch_head == recorded
    assert untouched.observed_memory_work_branch_head == reviewed.memory_base.commit

    tip = commit_file(contract.code_worktree, "continued.py", "VALUE = 3\n")
    advanced = measured(contract)
    assert advanced.binding_state == "current"
    assert advanced.transitions == ("ordinary-append",)
    assert advanced.transition_evidence[0] == ("code-work-branch", "advanced")
    assert advanced.moved_identities == ()
    assert advanced.observed_code_work_branch_head == tip

    git(contract.code_worktree, "reset", "-q", "--hard", recorded)
    git(contract.code_worktree, "commit", "-q", "--amend", "-m", "rewritten")
    rewritten = measured(contract)
    assert rewritten.binding_state == "stale"
    assert rewritten.transitions == ("rebase",)
    assert rewritten.transition_evidence[0] == ("code-work-branch", "replaced")
    assert rewritten.moved_identities == (f"code-work-branch:{recorded}",)
    assert rewritten.observed_code_work_branch_head == git(
        contract.code_worktree, "rev-parse", "HEAD"
    )
    assert supported_recovery("rebase").recovery_action in rewritten.successor_action
    # Measuring a movement records nothing: the reviewed comparison is still the one reported.
    assert rewritten.comparison == reviewed


def test_a_checkout_that_left_its_declared_branch_is_a_switch_and_never_a_movement(tree_fixture):
    """Off its declared branch the work-branch comparison is not taken, and that is not current."""

    contract = tree_fixture.contract
    record_committed_candidate(tree_fixture)
    for checkout, shown in (
        (("checkout", "-q", "--detach", "HEAD"), "no branch (a detached HEAD)"),
        (("switch", "-q", "main"), "is on main"),
    ):
        git(contract.code_worktree, *checkout)
        movement = measured(contract)
        assert movement.binding_state == "not-measured"
        assert movement.transitions == ("branch-switch",)
        assert movement.transition_evidence[0] == ("code-work-branch", "unavailable")
        assert movement.moved_identities == ()
        assert movement.observed_code_work_branch_head is None
        assert movement.reason is not None
        assert shown in movement.reason
        assert "not on the declared work branch leaf" in movement.reason
        assert supported_recovery("branch-switch").recovery_action in movement.successor_action
        git(contract.code_worktree, "switch", "-q", "leaf")
    assert measured(contract).transitions == ("unchanged",)


def test_a_channel_git_cannot_read_is_not_compared_and_is_neither_a_movement_nor_a_switch(
    tree_fixture,
):
    """A rewritten memory line is a replacement only while its recorded base can still be read.

    The memory work branch is rewritten, which is measured as a replacement naming the recorded
    base. That commit object is then removed and the declared source branch deleted: neither
    channel can be compared any more, so the report claims no movement and no checkout nobody
    performed, and names each channel it could not read with the reason.
    """

    contract = tree_fixture.contract
    reviewed = record_committed_candidate(tree_fixture)
    memory, base = contract.memory_repo_path, reviewed.memory_base.commit
    assert memory is not None and base is not None

    git(memory, "commit", "-q", "--amend", "-m", "rewritten base")
    rewritten = measured(contract)
    assert rewritten.binding_state == "stale"
    assert rewritten.transitions == ("rebase",)
    assert rewritten.transition_evidence[2] == ("memory-work-branch", "replaced")
    assert rewritten.moved_identities == (f"memory-work-branch:{base}",)
    assert rewritten.observed_memory_work_branch_head == git(memory, "rev-parse", "HEAD")

    (memory / ".git" / "objects" / base[:2] / base[2:]).unlink()
    git(contract.code_repo_path, "branch", "-q", "-D", "main")
    unread = measured(contract)
    assert unread.binding_state == "not-measured"
    assert unread.transitions == () and unread.moved_identities == ()
    assert unread.transition_evidence == (
        ("code-work-branch", "current"),
        ("declared-source-branch", "unavailable"),
        ("memory-work-branch", "unavailable"),
    )
    assert unread.observed_declared_source_branch_head is None
    assert unread.observed_memory_work_branch_head is None
    assert unread.reason is not None
    assert "declared-source-branch was not compared: main could not be resolved" in unread.reason
    assert f"the recorded identity {base} is not a readable commit object" in unread.reason


def test_the_boundary_report_refuses_a_state_its_own_fields_contradict(tree_fixture):
    """The report cannot claim a measurement it did not make, or one of another comparison.

    The published report of a rewritten work branch is the accepted control, and each forgery
    departs from it in the one clause it names, so removing a clause makes its forgery read as a
    valid report.
    """

    contract = tree_fixture.contract
    record_committed_candidate(tree_fixture)
    git(contract.code_worktree, "commit", "-q", "--amend", "-m", "rewritten")
    published = measured(contract).model_dump(mode="json")
    assert ExternalGitMovement.model_validate(published).binding_state == "stale"

    unchanged = "a movement recording 'unchanged'"
    forgeries: dict[str, tuple[dict[str, Any], str]] = {
        "a replaced identity beside a state that is not stale": (
            {"binding_state": "current"},
            "is stale, and this one records current",
        ),
        "a stale state with no replaced identity": (
            {"moved_identities": []},
            "a stale movement names the declared identity that was replaced",
        ),
        "a reason beside a measurement": (
            {"reason": "nothing was compared"},
            "a movement reported as stale carries no reason",
        ),
        "an absence with no reason": (
            {"binding_state": "not-measured", "moved_identities": []},
            "a movement reported as not-measured names the reason behind the absence",
        ),
        "an unreadable record beside a measurement taken from it": (
            {
                "binding_state": "unavailable",
                "moved_identities": [],
                "reason": "the record could not be read",
            },
            "a movement reported as unavailable records generation_readable=True",
        ),
        "unchanged beside another shape": (
            {
                "binding_state": "current",
                "moved_identities": [],
                "transitions": ["unchanged", "ordinary-append"],
            },
            unchanged,
        ),
        "unchanged in a state that is not current": (
            {
                "binding_state": "not-measured",
                "moved_identities": [],
                "reason": "the work branch was not compared",
                "transitions": ["unchanged"],
            },
            unchanged,
        ),
        "a measurement with no source comparison": (
            {"comparison": None},
            "a measured tree movement carries its source comparison",
        ),
        "the digest of another comparison": (
            {"reviewed_binding_digest": "0" * 64},
            "the digest must describe the complete source comparison",
        ),
        # The retired generation identity is not a field of the report at all.
        "a generation identity": (
            {"generation_id": "123e4567-e89b-12d3-a456-426614174000"},
            "Extra inputs are not permitted",
        ),
        "another movement version": (
            {"movement_version": "ar-review-external-movement/v1"},
            "Input should be 'ar-review-external-movement/v2'",
        ),
    }
    for why, (fields, refusal) in forgeries.items():
        with pytest.raises(ValidationError, match=refusal):
            ExternalGitMovement.model_validate({**published, **fields})
            pytest.fail(f"a report was built with {why}")
