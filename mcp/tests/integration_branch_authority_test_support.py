"""Shared exact-authority fixtures for the split L4 forcing suites."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents_remember.kernel.memory_ledger import create_initial_ledger, write_ledger
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    SprintExecutionGraph,
    TaskDocument,
    read_task_doc,
    write_task_doc,
)
from agents_remember.worktrees.closeout_queue import (
    CloseoutQueueRequest,
    QueueActor,
    closeout_queue_tool,
)
from agents_remember.worktrees.modules import start_contract
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    default_series_contract,
    write_contract,
)
from test_source_lineage import _fixture, _git


def _closed_leaf_worktree(fixture, _root: Path, *, candidate_commit: bool):
    worktree = fixture.leaf_contract.code_worktree
    worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(fixture.code_repo, "worktree", "add", worktree.as_posix(), "leaf")
    if candidate_commit:
        (worktree / "candidate.txt").write_text("candidate\n", encoding="utf-8")
        _git(worktree, "add", "candidate.txt")
        _git(worktree, "commit", "-m", "closed leaf candidate")
    _git(fixture.code_repo, "switch", "ar/master")
    return replace(
        fixture.leaf_contract,
        code_worktree=worktree,
        code_source_branch="ar/master",
        code_work_branch="leaf",
        closeout_status="completed",
        approved_for_commit=True,
        human_review_status="approved",
        code_commit=_git(worktree, "rev-parse", "HEAD"),
    )


def _closed_external_leaf_worktrees(fixture, _root: Path):
    memory_repo = fixture.leaf_contract.memory_repo_path
    assert memory_repo is not None
    code_worktree = fixture.leaf_contract.code_worktree
    memory_worktree = fixture.leaf_contract.memory_worktree
    assert memory_worktree is not None
    code_worktree.parent.mkdir(parents=True, exist_ok=True)
    memory_worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(fixture.code_repo, "worktree", "add", code_worktree.as_posix(), "leaf")
    _git(memory_repo, "worktree", "add", memory_worktree.as_posix(), "leaf")
    (code_worktree / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    _git(code_worktree, "add", "candidate.txt")
    _git(code_worktree, "commit", "-m", "closed code")
    code_commit = _git(code_worktree, "rev-parse", "HEAD")
    (memory_worktree / "candidate.md").write_text("# Candidate\n", encoding="utf-8")
    _git(memory_worktree, "add", "candidate.md")
    _git(memory_worktree, "commit", "-m", "closed memory")
    memory_commit = _git(memory_worktree, "rev-parse", "HEAD")
    write_ledger(
        memory_worktree / "memory.md",
        create_initial_ledger("repo", code_commit, memory_commit),
    )
    _git(memory_worktree, "add", "memory.md")
    _git(memory_worktree, "commit", "-m", "closed ledger")
    ledger_commit = _git(memory_worktree, "rev-parse", "HEAD")
    closed = replace(
        fixture.leaf_contract,
        code_worktree=code_worktree,
        memory_worktree=memory_worktree,
        ledger_path=memory_worktree / "memory.md",
        closeout_status="completed",
        approved_for_commit=True,
        human_review_status="approved",
        code_commit=code_commit,
        memory_content_commit=memory_commit,
        ledger_commit=ledger_commit,
    )
    write_contract(closed.contract_path, closed)
    return closed


def _authority_fixture(root: Path, *, external_memory: bool = False) -> Any:
    fixture: Any = _fixture(root, external_memory=external_memory)
    configured_code = root / "repo"
    configured_code.symlink_to(fixture.code_repo, target_is_directory=True)
    memory_mode = "external" if external_memory else "internal"
    if not external_memory:
        (configured_code / "ar-memory").mkdir()
    if fixture.leaf_contract.memory_repo_path is not None:
        configured_memory = fixture.coordination / "memory-repos" / "ar-repo"
        configured_memory.parent.mkdir(parents=True, exist_ok=True)
        configured_memory.symlink_to(
            fixture.leaf_contract.memory_repo_path,
            target_is_directory=True,
        )
    config_path = root / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": fixture.coordination.as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {"repo": {}},
            }
        ),
        encoding="utf-8",
    )
    fixture.config_path = config_path
    task_root = fixture.coordination / "tasks" / "repo"
    for repo in filter(None, (fixture.code_repo, fixture.leaf_contract.memory_repo_path)):
        _git(repo, "branch", "ar/atomic-two", "super")
    master_contract = replace(
        fixture.master_contract,
        memory_mode=memory_mode,
        code_work_branch="ar/master",
        memory_work_branch=("ar/master" if external_memory else ""),
    )
    write_contract(master_contract.contract_path, master_contract)
    fixture.master_contract = master_contract
    fixture.leaf_contract = replace(
        fixture.leaf_contract,
        memory_mode=memory_mode,
        code_source_branch="ar/master",
        memory_source_branch=("ar/master" if external_memory else ""),
    )
    write_contract(fixture.leaf_contract.contract_path, fixture.leaf_contract)
    master_doc = read_task_doc(task_root / "master" / "task.json")
    write_task_doc(
        task_root / "master",
        master_doc.model_copy(update={"executionNature": "atomic"}),
    )
    sprint = read_task_doc(task_root / "sprint" / "task.json")
    master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
    sibling_ref = TaskDocumentRef(repository="repo", path="atomic-two/task.json")
    write_task_doc(
        task_root / "sprint",
        sprint.model_copy(
            update={
                "integrationBranch": "super",
                "orchestrates": ["master", "atomic-two"],
                "executionGraph": SprintExecutionGraph(
                    nodes=[master_ref, sibling_ref],
                    edges=[],
                ),
            }
        ),
    )
    write_task_doc(
        task_root / "atomic-two",
        _doc(
            id="ATOMIC-TWO",
            slug="atomic-two",
            title="Atomic Two",
            kind="master",
            executionNature="atomic",
        ),
    )
    for repo in filter(None, (fixture.code_repo, fixture.leaf_contract.memory_repo_path)):
        _git(repo, "update-ref", "refs/remotes/origin/main", _git(repo, "rev-parse", "main"))
        _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    memory_repo = fixture.leaf_contract.memory_repo_path
    sibling = default_series_contract(
        ContractTask(
            "atomic-two",
            "repo",
            fixture.coordination,
            "light-task",
            memory_mode,
        ),
        code=RepoBranchPlan(
            fixture.code_repo,
            "super",
            "ar/atomic-two",
            _git(fixture.code_repo, "rev-parse", "super"),
        ),
        memory=(
            RepoBranchPlan(
                memory_repo,
                "super",
                "ar/atomic-two",
                _git(memory_repo, "rev-parse", "super"),
            )
            if memory_repo is not None
            else None
        ),
        task_root=task_root / "atomic-two",
    )
    write_contract(sibling.contract_path, sibling)
    return fixture


def _doc(**values: object) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "repo": "repo",
            "createdAt": "2026-08-15T00:00:00+00:00",
            **values,
        }
    )


def _add_atomic_master_to_sprint(fixture, task_root: Path) -> None:
    write_task_doc(
        task_root,
        _doc(
            id="ATOMIC-THREE",
            slug="atomic-three",
            title="Atomic Three",
            kind="master",
            executionNature="atomic",
        ),
    )
    sprint_path = fixture.coordination / "tasks" / "repo" / "sprint" / "task.json"
    sprint = read_task_doc(sprint_path)
    atomic_ref = TaskDocumentRef(repository="repo", path="atomic-three/task.json")
    assert sprint.executionGraph is not None
    write_task_doc(
        sprint_path.parent,
        sprint.model_copy(
            update={
                "orchestrates": [*sprint.orchestrates, "atomic-three"],
                "executionGraph": sprint.executionGraph.model_copy(
                    update={"nodes": [*sprint.executionGraph.nodes, atomic_ref]}
                ),
            }
        ),
    )


def _complete_atomic_master(fixture) -> None:
    master_path = fixture.coordination / "tasks" / "repo" / "master" / "task.json"
    master = read_task_doc(master_path)
    write_task_doc(
        master_path.parent,
        master.model_copy(
            update={
                "status": "Completed",
                "subTasks": [
                    row.model_copy(update={"status": "Completed"}) for row in master.subTasks
                ],
            }
        ),
    )


def _record_atomic_leaf_landing(
    fixture,
    code_commit: str,
    *,
    memory_content_commit: str = "",
    ledger_commit: str = "",
):
    """Persist the exact child landing facts consumed by the atomic-series seal."""

    landed = replace(
        fixture.leaf_contract,
        closeout_status="completed",
        approved_for_commit=True,
        human_review_status="approved",
        code_commit=code_commit,
        memory_content_commit=memory_content_commit,
        ledger_commit=ledger_commit,
        integration_status="completed",
        integrated_code_commit=code_commit,
        integrated_memory_content_commit=memory_content_commit,
        integrated_ledger_commit=ledger_commit,
        queue_sprint_task_document="repo/sprint/task.json",
        queue_candidate_task_document=fixture.leaf_ref.key,
    )
    write_contract(landed.contract_path, landed)
    fixture.leaf_contract = landed
    return landed


def _acquire_atomic_barrier(fixture) -> None:
    sprint_ref = TaskDocumentRef(repository="repo", path="sprint/task.json")
    master_ref = TaskDocumentRef(repository="repo", path="master/task.json")
    closeout_queue_tool(
        load_config(fixture.config_path),
        CloseoutQueueRequest(
            action="acquire-barrier",
            sprint_task_document_ref=sprint_ref,
            request_id="series-closeout-barrier",
            expected_revision=0,
            barrier_master_ref=master_ref,
            rationale="Finish the isolated atomic block before its one super landing.",
        ),
        actor=QueueActor(role="orchestrator", task_document_ref=sprint_ref),
        now="2026-08-15T00:00:00+00:00",
    )


def _atomic_three_spec(fixture, task_root: Path) -> start_contract.MasterSeriesContractSpec:
    return start_contract.MasterSeriesContractSpec(
        coordination_root=fixture.coordination,
        repo_name="repo",
        code_repo=fixture.code_repo,
        memory_root=None,
        task_root=task_root,
        task_name="atomic-three",
        parent_task_name="sprint",
        protected_branch="super",
    )


def _assert_exact_series_preview(
    test: unittest.TestCase,
    preview: WorktreeCommandResult,
) -> None:
    changed_code_paths = preview.payload["changed_code_paths"]
    proposed = preview.payload["proposed_commits"]
    assert isinstance(changed_code_paths, dict)
    assert isinstance(proposed, dict)
    code_proposed = proposed["code"]
    memory_proposed = proposed["memory"]
    ledger_proposed = proposed["ledger"]
    assert isinstance(code_proposed, dict)
    assert isinstance(memory_proposed, dict)
    assert isinstance(ledger_proposed, dict)
    test.assertEqual(changed_code_paths["count"], 0)
    test.assertEqual(
        (code_proposed["would_commit"], code_proposed["ref"], "worktree" in code_proposed),
        (False, "refs/heads/ar/master", False),
    )
    test.assertEqual(
        (
            memory_proposed["would_commit"],
            memory_proposed["ref"],
            "worktree" in memory_proposed,
        ),
        (False, "refs/heads/ar/master", False),
    )
    test.assertFalse(memory_proposed["metadata_refresh_after_code_commit"])
    test.assertFalse(memory_proposed["entity_fingerprint_refresh_after_code_commit"])
    test.assertFalse(memory_proposed["route_refresh_after_code_commit"])
    test.assertFalse(memory_proposed["memory_quality_check_before_commit"])
    test.assertFalse(ledger_proposed["would_update"])
    test.assertEqual(
        preview.payload["closeout_order"],
        [
            "read-exact-series-code-ref",
            "read-exact-series-memory-ref",
            "verify-existing-ledger-maps-exact-series-commits",
            "record-existing-series-commits-in-contract",
        ],
    )
    for key, subkey, expected in (
        ("onboarding_metadata_refresh", "required", {"count": 0}),
        ("entity_fingerprint_refresh", "required", []),
        ("route_overview_metadata_refresh", "required", []),
        ("route_index_refresh", "written", 0),
    ):
        section = preview.payload[key]
        assert isinstance(section, dict)
        found = section[subkey]
        if isinstance(expected, dict):
            assert isinstance(found, dict)
            test.assertEqual(found["count"], expected["count"])
        else:
            test.assertEqual(found, expected)
