"""Small explicit builders for lifecycle-enclosure addressability forcing."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from agents_remember.models.lifecycles.enclosure import (
    LifecycleEnclosureLocator,
    TerminalWorktreeAbandonArguments,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocation,
    inspect_lifecycle_operation_locator,
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.terminal_enclosure_archive import (
    terminal_archive_required_result,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    contract_publication_text,
    default_contract,
    default_series_contract,
    load_contract,
)


def enclosure_contract(
    root: Path,
    *,
    kind: Literal["leaf", "series"] = "leaf",
    worktree_name: str = "enclosure-leaf",
    leaf_id: str = "L12",
) -> tuple[WorktreeContract, str]:
    coordination = root / "coordination"
    task = ContractTask(
        name="lifecycle-enclosure",
        repo_name="repo",
        coordination_root=coordination,
        workflow_kind="light-task",
        memory_mode="disabled",
    )
    code = RepoBranchPlan(
        repo_path=root / "repo",
        source_branch="main",
        work_branch=f"ar/{worktree_name}",
        base_commit="a" * 40,
    )
    if kind == "series":
        contract = default_series_contract(task, code=code)
    else:
        contract = default_contract(
            task,
            leaf=LeafIdentity(worktree_name=worktree_name, leaf_id=leaf_id),
            code=code,
        )
    return contract, contract_publication_text(contract.contract_path, contract)


def publish_test_enclosure(contract: WorktreeContract, text: str) -> LifecycleOperationLocation:
    return publish_new_lifecycle_operation_location(contract, contract_text=text)


def terminalize_test_enclosure(
    location: LifecycleOperationLocation,
) -> LifecycleEnclosureLocator:
    """Drive the production archive/readback/receipt seam, then cut after deletion."""

    result = terminal_archive_required_result(
        _contract_for_location(location),
        operation="worktree_abandon",
        arguments=TerminalWorktreeAbandonArguments(force=False),
        dry_run=False,
    )
    assert result.returncode == 0, result.payload
    shutil.rmtree(location.worktree_group)
    observed = inspect_lifecycle_operation_locator(
        location.locator_path.parent.parent.parent,
        location.contract_path,
    )
    assert observed.state == "terminal-archived"
    assert observed.locator is not None
    return observed.locator


def _contract_for_location(location: LifecycleOperationLocation) -> WorktreeContract:
    return load_contract(location.contract_path)


def byte_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
