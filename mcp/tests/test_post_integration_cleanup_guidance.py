"""Reclamation is automatic and unprompted, and the published projection names its owner.

Integration used to end by asking the developer whether to remove the worktrees, and the
projection used to describe the procedure that replaced that prompt. It now describes the
procedure that owns it: a contract whose landing completed but whose enclosures still stand is
the moment before the terminal edge, so the move it offers is ``lifecycle_finalize_task`` --
the route that reclaims the code and memory worktrees and reconciles the leaf document and its
master row. Reclamation is still automatic and never a question; what changed is which
procedure reaches it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import get_args

from agents_remember.models.worktree import NextOperation
from agents_remember.worktrees.integration.closeout.curator_coherence import (
    curator_coherence_paths,
)
from agents_remember.worktrees.modules.guidance import carryover_done, lifecycle_guidance
from agents_remember.worktrees.worktree_contract import WorktreeContract
from test_worktree_support import git, init_repo


def _integrated_contract(root: Path) -> WorktreeContract:
    worktree_group = root / "worktrees" / "repo" / "leaf"
    return WorktreeContract(
        task_id="T",
        task_name="leaf",
        repo_name="repo",
        workflow_kind="light-task",
        memory_mode="disabled",
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


def test_a_pending_cleanup_offers_finalization_and_never_a_cleanup_decision(
    tmp_path: Path,
) -> None:
    contract = replace(_integrated_contract(tmp_path))

    guidance = lifecycle_guidance(contract)

    assert guidance["phase"] == "cleanup-pending"
    assert guidance["nextOperation"] == "finalize"
    assert guidance.get("nextTool") == "lifecycle_finalize_task"
    assert guidance.get("nextArgs", {})["contract_path"] == contract.contract_path.as_posix()
    # ``lifecycle_finalize_task`` is addressed by contract, so the projection must say so or an
    # operator is told to call a tool without the one argument that names the edge.
    assert guidance.get("nextRequiredArgs") == ["contract_path"]
    assert "finalizing the task edge" in guidance["summary"]
    assert "reclaims the code and memory worktrees" in guidance["summary"]
    assert "worktree_cleanup" not in guidance["summary"]


def test_the_cleanup_decision_is_no_longer_in_the_next_operation_vocabulary() -> None:
    """Both cleanup moves are gone from the vocabulary, not parked beside their replacement.

    ``request_cleanup_decision`` was the prompt this lane deleted. ``retry_cleanup`` was only
    ever written by the pending-cleanup phase, and that phase now offers ``finalize`` -- so the
    member has no writer left and is removed rather than kept as a nameable operation no
    procedure can produce.
    """

    assert "retry_cleanup" not in get_args(NextOperation)
    # The move that replaced the retry as the normal path has to be nameable, or the phase
    # above could not describe it at all.
    assert "finalize" in get_args(NextOperation)
    assert "request_cleanup_decision" not in get_args(NextOperation)


def test_external_completion_proves_landed_commits_without_reading_the_cache(
    tmp_path: Path,
) -> None:
    code_repo = tmp_path / "code"
    memory_repo = tmp_path / "memory"
    code = init_repo(code_repo)
    memory = init_repo(memory_repo)
    cache = memory_repo / "memory.md"
    contract = replace(
        _integrated_contract(tmp_path),
        memory_mode="external",
        code_repo_path=code_repo,
        code_commit=code,
        integrated_code_commit=code,
        memory_repo_path=memory_repo,
        memory_source_branch="main",
        memory_content_commit=memory,
        integrated_memory_content_commit=memory,
        ledger_path=cache,
    )
    for contents in (None, "<<<<<<< broken cache\n", "a stale row with no accepted pair\n"):
        if contents is not None:
            cache.write_text(contents, encoding="utf-8")
        done, date = carryover_done(contract)
        assert done and date
        guidance = lifecycle_guidance(contract)
        assert guidance["phase"] == "cleanup-pending"
        assert guidance["nextOperation"] == "finalize"

    git(memory_repo, "checkout", "-b", "unlanded")
    git(memory_repo, "commit", "--allow-empty", "-m", "Unlanded memory")
    unlanded = git(memory_repo, "rev-parse", "HEAD")
    git(memory_repo, "checkout", "main")
    assert not carryover_done(replace(contract, integrated_memory_content_commit=unlanded))[0]
    assert not carryover_done(replace(contract, integrated_code_commit="f" * 40))[0]
    assert not carryover_done(replace(contract, memory_repo_path=None))[0]


def test_a_checkpointed_series_keeps_working_instead_of_being_told_to_integrate(
    tmp_path: Path,
) -> None:
    """A checkpoint lands the series without closing it, so its position is still working.

    A checkpoint publishes the closeout edge while the series stays open. Without its own
    branch the projection therefore read as closeout-done-and-awaiting-integration:
    ``integration-pending`` with ``worktree_integrate``, the tool that refuses while the
    series is open. The move out of a checkpoint is the single one the checkpoint lands into,
    because the next thing that happens is the remaining work.

    This is a LANDING and not a pause, and the assertion on ``continue_work`` is what keeps
    that true: pausing is a separate operation that releases the master's atomic-series
    selection and moves no ref, so it must never be what a landed checkpoint reports. Reading
    this branch as the pause's next move is the hidden side effect the split exists to prevent.
    """

    contract = replace(_integrated_contract(tmp_path), integration_status="checkpointed")

    guidance = lifecycle_guidance(contract)

    assert guidance["phase"] == "worktree-started"
    assert guidance["nextOperation"] == "continue_work"
    assert guidance.get("nextTool") == "worktree_status"
    assert guidance.get("nextArgs", {})["contract_path"] == contract.contract_path.as_posix()
    assert "checkpointed" in guidance["summary"]
    assert "cleanup is deliberately not pending" in guidance["summary"]


def _external_memory_leaf(root: Path) -> WorktreeContract:
    """A leaf whose coherence route applies: external memory, with a memory worktree."""

    worktree_group = root / "worktrees" / "repo" / "leaf"
    return WorktreeContract(
        task_id="T",
        task_name="leaf",
        repo_name="repo",
        workflow_kind="light-task",
        memory_mode="external",
        coordination_root=root,
        task_root=root / "tasks" / "repo" / "leaf",
        contract_path=root
        / "tasks"
        / "repo"
        / "leaf"
        / "enclosures"
        / "leaf"
        / "series-contract.md",
        task_artifact=root / "tasks" / "repo" / "leaf" / "leaf.md",
        worktree_group=worktree_group,
        code_repo_path=root / "repo",
        code_source_branch="main",
        code_work_branch="ar/leaf",
        code_base_commit="abc1234",
        code_worktree=worktree_group / "leaf",
        memory_repo_path=root / "memory",
        memory_source_branch="main",
        memory_work_branch="ar/leaf",
        memory_base_commit="def5678",
        memory_worktree=worktree_group / "memory-leaf",
        leaf_id="leaf",
        parent_contract_path=root / "tasks" / "series-contract.md",
        closeout_status="completed",
    )


def test_a_published_coherence_authority_puts_validate_before_integration(tmp_path: Path) -> None:
    """D-25: the validate window closes at finalize, so the hint must name the step before it.

    ``lifecycle_finalize_task``'s automatic cleanup collects the enclosure root, and the standalone
    ``curator_coherence`` validate addresses exactly that location -- so a leaf that follows the old
    chain (closeout -> integrate -> finalize) can never re-prove the authority it published. The
    guidance now names the validation as the move out of closeout-completed, for precisely the leaf
    that has something to validate.
    """

    contract = _external_memory_leaf(tmp_path)
    paths = curator_coherence_paths(contract)
    paths.canonical.parent.mkdir(parents=True, exist_ok=True)
    paths.canonical.write_text(
        '{"schemaVersion": "ar-curator-coherence-authority/v1"}\n', encoding="utf-8"
    )

    guidance = lifecycle_guidance(contract)

    assert guidance["phase"] == "integration-pending"
    assert guidance.get("nextTool") == "curator_coherence"
    assert guidance["nextOperation"] == "request_integration_decision"
    args = guidance.get("nextArgs", {})
    assert args["contract_path"] == contract.contract_path.as_posix()
    assert args["action"] == "validate"
    assert args["caller"] == {
        "role": "curator",
        "task_document_ref": {"repository": "repo", "path": "leaf/leaf.json"},
    }
    # The reason has to travel with the step, or the next operator skips it again.
    assert "validate now" in guidance["summary"]
    assert "lifecycle_finalize_task's automatic cleanup collects it" in guidance["summary"]


def test_a_leaf_that_has_not_published_is_still_told_to_integrate(tmp_path: Path) -> None:
    """The step is conditional on having published: an unpublished leaf is not sent to a refusal."""

    contract = _external_memory_leaf(tmp_path)

    guidance = lifecycle_guidance(contract)

    assert guidance["phase"] == "integration-pending"
    assert guidance.get("nextTool") == "worktree_integrate"
    assert "curator_coherence" not in guidance["summary"]


def test_a_series_contract_is_never_routed_to_the_coherence_route(tmp_path: Path) -> None:
    """The coherence route is leaf-only by construction, and the hint must not invent a step for it."""

    series = replace(
        _integrated_contract(tmp_path),
        kind="series",
        integration_status="pending",
        closeout_status="completed",
    )

    guidance = lifecycle_guidance(series)

    assert guidance.get("nextTool") == "worktree_integrate"
