"""Altitude routing for the quality gate on integration (260731-EFA-L17-R2/R5)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    SprintExecutionGraph,
    SprintExecutionNode,
    TaskDocument,
    write_task_doc,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    default_contract,
    default_series_contract,
    write_contract,
)
from repository_profile_test_support import (
    install_agents_remember_profile,
)
from test_worktree_support import init_repo


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def integration_contract(root: Path, *, kind: str = "leaf") -> WorktreeContract:
    coordination = root / "ar-coordination"
    repo = root / "repo"
    if not (repo / ".git").exists():
        init_repo(repo, "main")
        (repo / "ar-memory").mkdir()
        install_agents_remember_profile(repo)
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "Add repository certification profile")
    base = git(repo, "rev-parse", "main")
    if not git(repo, "branch", "--list", "super"):
        git(repo, "branch", "super", "main")
    if kind == "series":
        task_name = "master"
        source_branch = "super"
        work_branch = "ar/master"
        if not git(repo, "branch", "--list", work_branch):
            git(repo, "branch", work_branch, source_branch)
        contract = default_series_contract(
            ContractTask(
                task_name,
                "agents-remember",
                coordination,
                "light-task",
                "internal",
                parent_task_name="sprint",
            ),
            code=RepoBranchPlan(repo, source_branch, work_branch, base),
        )
        contract = replace(
            contract,
            closeout_status="completed",
            approved_for_commit=True,
            human_review_status="approved",
            code_commit=git(repo, "rev-parse", work_branch),
        )
        master_nature = "atomic"
    else:
        task_name = "master-task"
        source_branch = "ar/master"
        work_branch = "ar/l1"
        if not git(repo, "branch", "--list", source_branch):  # pragma: no cover
            git(repo, "branch", source_branch, "main")
        contract = default_contract(
            ContractTask(
                task_name,
                "agents-remember",
                coordination,
                "light-task",
                "internal",
            ),
            leaf=LeafIdentity(worktree_name="l1", leaf_id="l1"),
            code=RepoBranchPlan(repo, source_branch, work_branch, base),
        )
        if not contract.code_worktree.exists():  # pragma: no cover
            contract.code_worktree.parent.mkdir(parents=True, exist_ok=True)
            git(
                repo,
                "worktree",
                "add",
                "-b",
                work_branch,
                str(contract.code_worktree),
                source_branch,
            )
        master_nature = "organizational"
    master_ref = TaskDocumentRef(
        repository="agents-remember",
        path=f"{task_name}/task.json",
    )
    write_task_doc(
        contract.task_root,
        TaskDocument.model_validate(
            {
                "id": task_name.upper(),
                "slug": task_name,
                "title": task_name,
                "kind": "master",
                "repo": "agents-remember",
                "createdAt": "2026-08-15T00:00:00+00:00",
                "executionNature": master_nature,
                "subTasks": (
                    [
                        {
                            "number": "l1",
                            "name": "Leaf l1",
                            "file": "l1.md",
                            "status": "inProgress",
                        }
                    ]
                    if kind == "leaf"
                    else []
                ),
            }
        ),
    )
    write_task_doc(
        coordination / "tasks" / "agents-remember" / "sprint",
        TaskDocument.model_validate(
            {
                "id": "SPRINT",
                "slug": "sprint",
                "title": "Sprint",
                "kind": "master",
                "repo": "agents-remember",
                "createdAt": "2026-08-15T00:00:00+00:00",
                "orchestrates": [task_name],
                "integrationBranch": source_branch,
                "executionGraph": SprintExecutionGraph(nodes=[SprintExecutionNode(ref=master_ref)]),
            }
        ),
    )
    if kind == "leaf":
        write_task_doc(
            contract.task_root,
            TaskDocument.model_validate(
                {
                    "id": "l1",
                    "slug": "l1",
                    "title": "Leaf l1",
                    "kind": "subTask",
                    "repo": "agents-remember",
                    "createdAt": "2026-08-15T00:01:00+00:00",
                    "master": "task.md",
                }
            ),
        )
    write_contract(contract.contract_path, contract)
    publish_new_lifecycle_operation_location(
        contract,
        contract_text=contract.contract_path.read_text(encoding="utf-8"),
    )
    return contract


class IntegrationQualityGateAltitudeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()
