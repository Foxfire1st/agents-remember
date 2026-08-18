"""Production start forcing for organizational direct-super execution."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.kernel.memory_ledger import create_initial_ledger, write_ledger
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees import git_worktree_manager as worktree_manager
from agents_remember.worktrees.task_resolver import (
    leaf_enclosure_path,
    series_contract_path,
)
from agents_remember.worktrees.worktree_contract import load_contract
from test_worktree_support import git, init_repo


class OrganizationalWorktreeStartTests(unittest.TestCase):
    def test_organizational_master_starts_direct_super_leaf_without_series(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_repo = workspace / "repo-a"
            code_base = init_repo(code_repo, "main")
            git(code_repo, "branch", "super", "main")
            coordination_root = workspace / "ar-coordination"
            external_memory = coordination_root / "memory-repos" / "ar-repo-a"
            memory_content = init_repo(external_memory, "main")
            write_ledger(
                external_memory / "memory.md",
                create_initial_ledger("repo-a", code_base, memory_content),
            )
            git(external_memory, "add", "memory.md")
            git(external_memory, "commit", "-m", "Add exact code-memory baseline")
            memory_base = git(external_memory, "rev-parse", "HEAD")
            git(external_memory, "branch", "super", memory_base)
            task_root = coordination_root / "tasks" / "repo-a" / "260624_org"
            write_task_doc(
                coordination_root / "tasks" / "repo-a" / "260624_sprint",
                TaskDocument.model_validate(
                    {
                        "id": "sprint",
                        "slug": "task",
                        "title": "Sprint",
                        "kind": "master",
                        "status": "inProgress",
                        "repo": "repo-a",
                        "createdAt": "2026-06-24T01:00",
                        "orchestrates": ["260624_org"],
                        "integrationBranch": "super",
                        "executionGraph": {
                            "nodes": [
                                {
                                    "repository": "repo-a",
                                    "path": "260624_org/task.json",
                                }
                            ],
                            "edges": [],
                        },
                    }
                ),
            )
            write_task_doc(
                task_root,
                TaskDocument.model_validate(
                    {
                        "id": "org-master",
                        "slug": "task",
                        "title": "Organizational Master",
                        "kind": "master",
                        "status": "inProgress",
                        "repo": "repo-a",
                        "createdAt": "2026-06-24T02:00",
                        "executionNature": "organizational",
                        "subTasks": [
                            {
                                "number": "15",
                                "name": "Leaf task",
                                "file": "15_leaf.md",
                                "status": "inProgress",
                            }
                        ],
                    }
                ),
            )
            write_task_doc(
                task_root,
                TaskDocument.model_validate(
                    {
                        "id": "15",
                        "slug": "15_leaf",
                        "title": "Leaf task",
                        "kind": "subTask",
                        "status": "inProgress",
                        "repo": "repo-a",
                        "createdAt": "2026-06-24T03:00",
                        "steps": [{"id": "S1", "title": "Ready", "status": "inProgress"}],
                    }
                ),
            )

            result = worktree_manager.start_result(
                worktree_manager.WorktreeArgs(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                    coordination_root=coordination_root,
                    code_repository_root=code_repo,
                    topology="external",
                    task_name="260624_org",
                    worktree_name="15_leaf",
                    leaf_id="15",
                    workflow_kind="light-task",
                    memory_mode="external",
                    skip_provider_setup=True,
                    lifecycle_id="LC-ORG-LEAF",
                )
            )

            self.assertEqual(result.returncode, 0, result.payload)
            leaf_contract = load_contract(leaf_enclosure_path(task_root, "15"))
            self.assertEqual(leaf_contract.code_source_branch, "super")
            self.assertEqual(leaf_contract.code_base_commit, code_base)
            self.assertEqual(leaf_contract.memory_mode, "external")
            self.assertEqual(leaf_contract.memory_source_branch, "super")
            self.assertEqual(leaf_contract.memory_base_commit, memory_base)
            self.assertIsNotNone(leaf_contract.memory_worktree)
            assert leaf_contract.memory_worktree is not None
            self.assertTrue(leaf_contract.memory_worktree.is_dir())
            self.assertNotEqual(leaf_contract.code_work_branch, "super")
            self.assertNotEqual(leaf_contract.memory_work_branch, "super")
            self.assertNotEqual(leaf_contract.code_worktree, leaf_contract.memory_worktree)
            self.assertIsNone(leaf_contract.parent_contract_path)
            self.assertFalse(series_contract_path(task_root).exists())
            self.assertNotIn(
                "ar/260624_org",
                git(code_repo, "branch", "--list", "ar/260624_org"),
            )
            self.assertIn("ar/15_leaf", git(code_repo, "branch", "--list", "ar/15_leaf"))
            self.assertNotIn(
                "ar/260624_org",
                git(external_memory, "branch", "--list", "ar/260624_org"),
            )
            self.assertIn(
                leaf_contract.memory_work_branch,
                git(
                    external_memory,
                    "branch",
                    "--list",
                    leaf_contract.memory_work_branch,
                ),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
