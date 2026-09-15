"""Shared exact-authority fixtures for the split L4 forcing suites."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.kernel.memory_cache import prepare_memory_cache, refresh_memory_cache
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    SprintExecutionGraph,
    SprintExecutionNode,
    TaskDocument,
    read_task_doc,
    write_task_doc,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    WorktreeContract,
    contract_publication_text,
    default_series_contract,
    load_contract,
    write_contract,
)
from closeout_input_test_support import (
    closeout_operation_input,
    finish_operation_record,
    publish_closeout_finalization,
    start_closeout_operation,
    start_operation_record,
)
from repository_profile_test_support import AGENTS_REMEMBER_PROFILE_REFERENCE
from selected_lifecycle_test_support import selected_closeout_operation_input
from test_source_lineage import _fixture, _git


def _closed_external_leaf_worktrees(
    fixture,
    _root: Path,
    *,
    publish_closeout_evidence: bool = True,
):
    memory_repo = fixture.leaf_contract.memory_repo_path
    assert memory_repo is not None
    code_worktree = fixture.leaf_contract.code_worktree
    memory_worktree = fixture.leaf_contract.memory_worktree
    assert memory_worktree is not None
    code_worktree.parent.mkdir(parents=True, exist_ok=True)
    memory_worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(fixture.code_repo, "worktree", "add", code_worktree.as_posix(), "leaf")
    _git(memory_repo, "worktree", "add", memory_worktree.as_posix(), "leaf")
    (memory_worktree / "onboarding").mkdir()
    (code_worktree / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    _git(code_worktree, "add", "candidate.txt")
    _git(code_worktree, "commit", "-m", "closed code")
    code_commit = _git(code_worktree, "rev-parse", "HEAD")
    (memory_worktree / "candidate.md").write_text("# Candidate\n", encoding="utf-8")
    prepare_memory_cache(memory_worktree)
    _git(memory_worktree, "add", "-A")
    _git(
        memory_worktree, "commit", "-m", render_memory_content_message("closed memory", code_commit)
    )
    memory_commit = _git(memory_worktree, "rev-parse", "HEAD")
    refresh_memory_cache(memory_worktree, repo_name=fixture.leaf_contract.repo_name)
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
    )
    write_contract(closed.contract_path, closed)
    if publish_closeout_evidence:
        return _publish_completed_closeout_fixture(fixture, closed)
    fixture.leaf_contract = closed
    return closed


def _publish_completed_closeout_fixture(
    fixture, closed: WorktreeContract, *, final_source_branch: str | None = None
) -> WorktreeContract:
    """Attach integration evidence after valid admission, optionally varying its final target."""

    input_factory = (
        selected_closeout_operation_input if closed.kind == "leaf" else closeout_operation_input
    )
    operation_input = input_factory(
        closed,
        config_path=fixture.config_path,
        approval_note="approved closed integration fixture",
    )
    start_closeout_operation(operation_input, launcher=lambda *_: None)
    store = LifecycleOperationStore(operation_record_path(closed.worktree_group, "closeout"))
    start_operation_record(store)
    finalized = replace(
        load_contract(closed.contract_path),
        code_source_branch=final_source_branch or closed.code_source_branch,
        closeout_status="completed",
        approved_for_commit=True,
        human_review_status="approved",
        code_commit=closed.code_commit,
        memory_content_commit=closed.memory_content_commit,
    )
    write_contract(finalized.contract_path, finalized)
    publish_closeout_finalization(store, finalized)
    finish_operation_record(store, {"state": "closed"}, ok=True)
    if finalized.kind == "series":
        fixture.master_contract = finalized
    else:
        fixture.leaf_contract = finalized
    return load_contract(finalized.contract_path)


def _authority_fixture(root: Path, *, external_memory: bool = False) -> Any:
    fixture: Any = _fixture(
        root,
        external_memory=external_memory,
        publish_locations=False,
        selected_profile=True,
    )
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
                "repositories": {
                    "repo": {"certificationProfile": AGENTS_REMEMBER_PROFILE_REFERENCE.as_posix()}
                },
            }
        ),
        encoding="utf-8",
    )
    fixture.config_path = config_path
    task_root = fixture.coordination / "tasks" / "repo"
    for repo in filter(None, (fixture.code_repo, fixture.leaf_contract.memory_repo_path)):
        _git(repo, "branch", "ar/atomic-two", "super")
    if external_memory:
        memory_repo = fixture.leaf_contract.memory_repo_path
        assert memory_repo is not None
        code_head = _git(fixture.code_repo, "rev-parse", "ar/master")
        _git(memory_repo, "switch", "ar/master")
        (memory_repo / "base.md").write_text("# Base memory\n", encoding="utf-8")
        prepare_memory_cache(memory_repo)
        _git(memory_repo, "add", "-A")
        _git(
            memory_repo,
            "commit",
            "-m",
            render_memory_content_message("base memory content", code_head),
        )
        refresh_memory_cache(memory_repo, repo_name="repo")
        _git(memory_repo, "branch", "-f", "leaf", "ar/master")
        _git(memory_repo, "branch", "-f", "super", "ar/master")
        _git(memory_repo, "switch", "super")
    master_contract = replace(
        fixture.master_contract,
        memory_mode=memory_mode,
        code_work_branch="ar/master",
        memory_work_branch=("ar/master" if external_memory else ""),
        memory_base_commit=(
            _git(cast(Path, fixture.leaf_contract.memory_repo_path), "rev-parse", "ar/master")
            if external_memory
            else ""
        ),
    )
    write_contract(master_contract.contract_path, master_contract)
    publish_new_lifecycle_operation_location(
        master_contract,
        contract_text=contract_publication_text(
            master_contract.contract_path,
            master_contract,
        ),
    )
    fixture.master_contract = master_contract
    fixture.leaf_contract = replace(
        fixture.leaf_contract,
        memory_mode=memory_mode,
        code_source_branch="ar/master",
        memory_source_branch=("ar/master" if external_memory else ""),
        memory_base_commit=(
            _git(cast(Path, fixture.leaf_contract.memory_repo_path), "rev-parse", "ar/master")
            if external_memory
            else ""
        ),
    )
    write_contract(fixture.leaf_contract.contract_path, fixture.leaf_contract)
    publish_new_lifecycle_operation_location(
        fixture.leaf_contract,
        contract_text=contract_publication_text(
            fixture.leaf_contract.contract_path,
            fixture.leaf_contract,
        ),
    )
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
                    nodes=[
                        SprintExecutionNode(ref=master_ref),
                        SprintExecutionNode(ref=sibling_ref),
                    ],
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
