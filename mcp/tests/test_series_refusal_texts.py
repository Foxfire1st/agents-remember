"""A master's series refusals name the master, the contract cell or folder, and the repair."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from agents_remember.tasks import SubTaskRef, TaskDocument, write_task_doc
from agents_remember.worktrees.integration import terminal_enclosure_evidence as evidence
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocation,
)
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.series_closeout import (
    capture_series_checkpoint_refs,
    refuse_series_workbench_commit,
    require_closeout_publication_authority,
    series_memory_closeout,
)
from agents_remember.worktrees.task_resolver import leaf_enclosure_path
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    WorktreeContract,
    default_series_contract,
    write_contract,
)
from test_worktree_support import commit_file, git, init_repo

CREATED = "2026-10-04T00:00:00+00:00"


def _landed_master(root: Path) -> WorktreeContract:
    """One completed atomic master whose single leaf landed on the series branch ``line``."""

    code = root / "code"
    base = init_repo(code)
    contract = default_series_contract(
        ContractTask(
            name="master",
            repo_name="repo-a",
            coordination_root=root / "coord",
            workflow_kind="light-task",
            memory_mode="disabled",
        ),
        code=RepoBranchPlan(
            repo_path=code, source_branch="main", work_branch="line", base_commit=base
        ),
    )
    landed = commit_file(code, "leaf.txt", "leaf", "leaf")
    git(code, "branch", "line", landed)
    leaf = replace(
        contract,
        kind="leaf",
        leaf_id="L0",
        contract_path=leaf_enclosure_path(contract.task_root, "L0"),
        parent_contract_path=contract.contract_path,
        parent_task_name="master",
        code_source_branch="line",
        code_work_branch="leaf",
        code_commit=landed,
        integration_status="completed",
        integrated_code_commit=landed,
        code_worktree=root / "leaf",
    )
    write_contract(leaf.contract_path, leaf)
    for document in (
        TaskDocument(
            id="L0",
            slug="l0",
            title="L0",
            kind="subTask",
            repo="repo-a",
            createdAt=CREATED,
            status="Completed",
        ),
        TaskDocument(
            id="MASTER",
            slug="master",
            title="Master",
            kind="master",
            repo="repo-a",
            createdAt=CREATED,
            status="Completed",
            executionNature="atomic",
            subTasks=[SubTaskRef(number="L0", name="L0", file="l0.md", status="Completed")],
        ),
    ):
        write_task_doc(contract.task_root, document)
    return contract


def _names(text: str, contract: WorktreeContract, *parts: str) -> None:
    """The refusal carries the master, the contract file and every named part, and no Git text."""

    for part in (repr(contract.task_id), contract.contract_path.as_posix(), *parts):
        assert part in text, (part, text)
    assert "fatal" not in text and "single revision" not in text, text


def test_closeout_of_a_master_whose_series_branch_is_gone_names_the_cell_and_the_repair(tmp_path):
    contract = _landed_master(tmp_path)
    code = contract.code_repo_path
    require_closeout_publication_authority(contract)
    tip = git(code, "rev-parse", "line")
    git(code, "branch", "-D", "line")

    with pytest.raises(CloseoutQueueError) as refused:
        require_closeout_publication_authority(contract)

    assert refused.value.status == "atomic-series-ref-unresolved"
    _names(
        str(refused.value),
        contract,
        "code branch 'line' does not resolve",
        "code.work_branch cell",
        "restore that branch at the master's last code commit",
        "retry closeout",
    )
    # The named repair is the whole repair: the same master closes out once the branch is back.
    git(code, "branch", "line", tip)
    require_closeout_publication_authority(contract)


def test_checkpoint_and_memory_capture_name_an_unresolved_series_branch(tmp_path):
    contract = _landed_master(tmp_path)
    code = contract.code_repo_path
    tip = git(code, "rev-parse", "line")
    external = replace(
        contract,
        memory_mode="external",
        memory_repo_path=code,
        memory_work_branch="memory-line",
        memory_base_commit=contract.code_base_commit,
    )

    with pytest.raises(RuntimeError) as memory_refused:
        series_memory_closeout(external, tip)

    _names(
        str(memory_refused.value),
        contract,
        "memory branch 'memory-line' does not resolve",
        "memory.work_branch cell",
        "retry the closeout or checkpoint landing",
    )
    git(code, "branch", "-D", "line")

    with pytest.raises(CloseoutQueueError) as checkpoint_refused:
        capture_series_checkpoint_refs(contract)

    assert checkpoint_refused.value.status == "atomic-series-checkpoint-no-code-ref"
    _names(
        str(checkpoint_refused.value),
        contract,
        "code branch 'line' does not resolve",
        "code.work_branch cell",
        "retry worktree_checkpoint_landing",
    )


def test_dirty_integration_worktree_names_closed_leaf_publication_or_discard(tmp_path):
    contract = _landed_master(tmp_path)
    git(contract.code_repo_path, "checkout", "line")
    (contract.code_repo_path / "unlanded.txt").write_text("uncommitted delivery")
    tip = git(contract.code_repo_path, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError) as refused:
        refuse_series_workbench_commit(contract)
    text = str(refused.value)
    assert "closeout cannot create code or memory commits" in text
    assert "deliver them through a closed leaf and its Owner's publication, or discard them" in text
    assert "commit or discard them there" not in text
    assert git(contract.code_repo_path, "rev-parse", "HEAD") == tip
    assert (contract.code_repo_path / "unlanded.txt").read_text() == "uncommitted delivery"


def _archive_census(lifecycle: Path) -> None:
    """Run the terminal archive's census of one canonical lifecycle directory."""

    location = cast(LifecycleOperationLocation, SimpleNamespace(lifecycle_directory=lifecycle))
    evidence._canonical_entries(
        location, lifecycle.parent / "series-contract.md", operation="worktree_cleanup"
    )


def test_an_archive_over_its_bound_names_the_directory_and_the_action(tmp_path, monkeypatch):
    lifecycle = tmp_path / ".lifecycle"
    lifecycle.mkdir()
    (lifecycle / "enclosure-manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(evidence, "_MAX_CANONICAL_BYTES", 1)

    with pytest.raises(RuntimeError) as refused:
        _archive_census(lifecycle)

    text = str(refused.value)
    assert f"canonical lifecycle directory {lifecycle} exceeds" in text
    assert "file or byte bound" in text and text.endswith("and retry")


def test_an_archive_without_evidence_names_the_directory_and_the_action(tmp_path):
    lifecycle = tmp_path / ".lifecycle"
    lifecycle.mkdir()
    (lifecycle / "closeout-operation.lock").write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError) as refused:
        _archive_census(lifecycle)

    text = str(refused.value)
    assert f"canonical lifecycle directory {lifecycle} holds no enclosure evidence" in text
    assert "restore its operation record there" in text and text.endswith("and retry")
