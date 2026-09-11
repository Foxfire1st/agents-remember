"""Shared contract and task-document fixtures for task reopen tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.tasks import SprintExecutionGraph, TaskDocument, write_task_doc
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    contract_publication_text,
    default_contract,
    write_contract,
)
from lifecycle_enclosure_test_support import (
    publish_test_enclosure,
    terminalize_test_enclosure,
)
from test_worktree_support import git, init_repo


def _publish_restamp(task_root: Path, document: TaskDocument) -> object:
    return write_task_doc(task_root, document)


def _publish_terminal_reopen_predecessor(contract: WorktreeContract) -> None:
    """Publish the completed generation that authorizes one reopened successor."""

    terminal = replace(
        contract,
        lifecycle_id="LC-OLD",
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=contract.code_base_commit,
        integration_status="completed",
        integrated_code_commit=contract.code_base_commit,
        cleanup="completed",
    )
    location = publish_test_enclosure(
        terminal,
        contract_publication_text(terminal.contract_path, terminal),
    )
    terminalize_test_enclosure(location)


def _completed_leaf_contract(workspace: Path) -> WorktreeContract:
    coordination_root = workspace / "ar-coordination"
    code_repo = workspace / "repo-a"
    base = init_repo(code_repo, "main")
    git(code_repo, "branch", "super", "main")
    git(code_repo, "branch", "ar/01-demo-leaf", "super")
    task = ContractTask(
        name="260698_demo-series",
        repo_name="repo-a",
        coordination_root=coordination_root,
        workflow_kind="light-task",
        memory_mode="disabled",
    )
    contract = default_contract(
        task,
        leaf=LeafIdentity(
            worktree_name="01-demo-leaf",
            leaf_id="260698-l1",
            lifecycle_id="LC-OLD",
        ),
        code=RepoBranchPlan(
            repo_path=code_repo,
            source_branch="super",
            work_branch="ar/01-demo-leaf",
            base_commit=base,
        ),
    )
    contract = replace(
        contract,
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=base,
        integration_status="completed",
        integrated_code_commit=base,
        cleanup="completed",
    )
    write_contract(contract.contract_path, contract)
    location = publish_test_enclosure(
        contract,
        contract.contract_path.read_text(encoding="utf-8"),
    )
    terminalize_test_enclosure(location)
    return contract


def _runtime_config(root: Path, contract: WorktreeContract) -> McpRuntimeConfig:
    repository = RepositoryScope(repo_id=contract.repo_name, path=contract.code_repo_path)
    return McpRuntimeConfig(
        config_path=contract.coordination_root / "mcp.settings.json",
        coordination_root=contract.coordination_root,
        workspace_root=root,
        transcript_root=contract.coordination_root / "logs",
        repositories={contract.repo_name: repository},
    )


def _external_memory_dirs(coordination_root: Path) -> None:
    for name in ("system", "onboarding"):
        (coordination_root / "memory-repos" / "ar-repo-a" / name).mkdir(parents=True)


def _leaf_doc(
    task_root: Path,
    *,
    lifecycle_id: str | None = "LC-OLD",
    master: str | None = "task.md",
    status: str = "Completed",
    step: dict[str, object] | None = None,
) -> Path:
    doc = TaskDocument.model_validate(
        {
            "id": "260698-L1",
            "slug": "01_demo-leaf",
            "title": "L1 — Demo leaf",
            "kind": "subTask",
            "status": status,
            "repo": "repo-a",
            "createdAt": "2026-07-01T10:00",
            "lifecycleId": lifecycle_id,
            "master": master,
            "steps": [step or {"id": "S1", "title": "do the thing", "status": "done"}],
        }
    )
    json_path, _ = write_task_doc(task_root, doc)
    return json_path


def _master_doc(
    task_root: Path,
    *,
    duplicate_row: bool = False,
    row_number: str = "260698-L1",
    row_file: str = "01_demo-leaf.md",
    statuses: tuple[str, str] = ("Completed", "Completed"),
) -> Path:
    write_task_doc(
        task_root.parent / "260698_demo-sprint",
        TaskDocument(
            id="260698_DEMO-SPRINT",
            slug="task",
            title="Demo Sprint",
            kind="master",
            status="inProgress",
            repo="repo-a",
            createdAt="2026-07-01T08:00",
            orchestrates=[task_root.name],
            integrationBranch="super",
            executionGraph=SprintExecutionGraph.model_validate(
                {
                    "nodes": [
                        {
                            "repository": "repo-a",
                            "path": f"{task_root.name}/task.json",
                        }
                    ],
                    "edges": [],
                }
            ),
        ),
    )
    status, row_status = statuses
    row = {
        "number": row_number,
        "name": "L1 — Demo leaf",
        "file": row_file,
        "status": row_status,
    }
    doc = TaskDocument.model_validate(
        {
            "id": "260698_DEMO-SERIES",
            "slug": "task",
            "title": "Demo Series",
            "kind": "master",
            "status": status,
            "repo": "repo-a",
            "createdAt": "2026-07-01T09:00",
            "executionNature": "organizational",
            "subTasks": [row, dict(row)] if duplicate_row else [row],
        }
    )
    json_path, _ = write_task_doc(task_root, doc)
    return json_path
