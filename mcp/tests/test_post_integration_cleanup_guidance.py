"""Cleanup after integration is automatic, and the published projection says so.

Integration used to end by asking the developer whether to remove the worktrees. The
projection must not keep describing that decision once the procedure runs itself: a contract
whose cleanup did not complete is a recovery, and the move it offers is the retry, addressed
to the same tool the automatic path already calls.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import get_args

from agents_remember.models.worktree import NextOperation
from agents_remember.worktrees.modules.guidance import lifecycle_guidance
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _integrated_contract(root: Path) -> WorktreeContract:
    worktree_group = root / "worktrees" / "repo" / "leaf"
    return WorktreeContract(
        task_id="T",
        task_name="leaf",
        repo_name="repo",
        workflow_kind="light-task",
        memory_mode="internal",
        coordination_root=root,
        task_root=root / "tasks",
        contract_path=root / "tasks" / "series-contract.md",
        task_artifact=root / "tasks" / "task.md",
        worktree_group=worktree_group,
        code_repo_path=root / "repo",
        code_source_branch="main",
        code_work_branch="ar/leaf",
        code_base_commit="abc1234",
        code_worktree=worktree_group / "leaf",
        leaf_id="L1",
        closeout_status="completed",
        integration_status="completed",
        cleanup="pending",
    )


def test_a_pending_cleanup_offers_a_retry_and_never_a_cleanup_decision(tmp_path: Path) -> None:
    contract = replace(_integrated_contract(tmp_path))

    guidance = lifecycle_guidance(contract)

    assert guidance["phase"] == "cleanup-pending"
    assert guidance["nextOperation"] == "retry_cleanup"
    assert guidance.get("nextTool") == "worktree_cleanup"
    assert guidance.get("nextArgs", {})["contract_path"] == contract.contract_path.as_posix()
    assert "automatic" in guidance["summary"]
    assert "retry worktree_cleanup" in guidance["summary"]


def test_the_cleanup_decision_is_no_longer_in_the_next_operation_vocabulary() -> None:
    """The vocabulary outgrew its writer when the prompt was removed; the member is gone."""

    assert "retry_cleanup" in get_args(NextOperation)
    assert "request_cleanup_decision" not in get_args(NextOperation)


def test_a_checkpointed_series_keeps_working_instead_of_being_told_to_integrate(
    tmp_path: Path,
) -> None:
    """A checkpoint lands the series without closing it, so its position is still working.

    A checkpoint publishes the closeout edge while the series stays open. Without its own
    branch the projection therefore read as closeout-done-and-awaiting-integration:
    ``integration-pending`` with ``worktree_integrate``, the tool that refuses while the
    series is open. The move out of a checkpoint is the single one the checkpoint lands into,
    because the next thing that happens is the remaining work.
    """

    contract = replace(_integrated_contract(tmp_path), integration_status="checkpointed")

    guidance = lifecycle_guidance(contract)

    assert guidance["phase"] == "worktree-started"
    assert guidance["nextOperation"] == "continue_work"
    assert guidance.get("nextTool") == "worktree_status"
    assert guidance.get("nextArgs", {})["contract_path"] == contract.contract_path.as_posix()
    assert "checkpointed" in guidance["summary"]
    assert "cleanup is deliberately not pending" in guidance["summary"]
