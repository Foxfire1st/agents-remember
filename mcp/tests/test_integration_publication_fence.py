"""Exact door-authority boundary for leaf and series integration."""

from __future__ import annotations

from pathlib import Path

from agents_remember.models.lifecycles.operation import IntegrationPublicationIntent
from agents_remember.worktrees.integration.integration_publication_fence import (
    classify_integration_door_authority,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    default_contract,
    default_series_contract,
)


def _branch_plan(root: Path) -> RepoBranchPlan:
    return RepoBranchPlan(
        repo_path=root / "repo",
        source_branch="super",
        work_branch="ar/master",
        base_commit="a" * 40,
    )


def _series_contract(root: Path) -> WorktreeContract:
    return default_series_contract(
        ContractTask(
            name="master",
            repo_name="agents-remember",
            coordination_root=root / "coordination",
            workflow_kind="light-task",
            memory_mode="disabled",
            parent_task_name="sprint",
        ),
        code=_branch_plan(root),
    )


def _leaf_contract(root: Path) -> WorktreeContract:
    return default_contract(
        ContractTask(
            name="master",
            repo_name="agents-remember",
            coordination_root=root / "coordination",
            workflow_kind="light-task",
            memory_mode="disabled",
        ),
        leaf=LeafIdentity(worktree_name="l1", leaf_id="l1"),
        code=_branch_plan(root),
    )


def _not_applicable_publication() -> IntegrationPublicationIntent:
    return IntegrationPublicationIntent(
        operationKey="b" * 64,
        generation=1,
        preparedAt="2026-08-31T00:00:00+00:00",
        claimState="not-applicable",
    )


def test_fresh_ordinary_series_integration_needs_no_closeout_door(tmp_path: Path) -> None:
    evidence = classify_integration_door_authority(_series_contract(tmp_path), None)

    assert evidence.valid
    assert evidence.state == "not-applicable"
    assert evidence.expected == {"contractKind": "series", "closeoutDoor": "absent"}


def test_journaled_ordinary_series_absence_remains_valid(tmp_path: Path) -> None:
    evidence = classify_integration_door_authority(
        _series_contract(tmp_path),
        _not_applicable_publication(),
    )

    assert evidence.valid
    assert evidence.state == "not-applicable"


def test_leaf_without_claimed_closeout_source_is_still_refused(tmp_path: Path) -> None:
    evidence = classify_integration_door_authority(_leaf_contract(tmp_path), None)

    assert not evidence.valid
    assert evidence.state == "preclaim-refused"
    assert evidence.status == "integration-closeout-door-not-claimed"


def test_leaf_retains_an_already_journaled_not_applicable_publication(tmp_path: Path) -> None:
    evidence = classify_integration_door_authority(
        _leaf_contract(tmp_path),
        _not_applicable_publication(),
    )

    assert evidence.valid
    assert evidence.state == "not-applicable"
