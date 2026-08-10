from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MCP_ROOT = Path(__file__).resolve().parents[1]
MCP_SRC = MCP_ROOT / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.worktrees.task_resolver import (
    TaskResolutionError,
    iter_active_series_contracts,
    resolve_active_task_root,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    default_series_contract,
    write_contract,
)

CONTEXT_KEYS = (
    "topology",
    "code_repository_name",
    "code_repository_root",
    "coordination_root",
    "memory_root",
    "memory_mode",
    "onboarding_root",
    "settings_path",
    "path_settings_path",
    "task_root",
    "temp_root",
    "docs_root",
    "system_root",
    "sources_path",
    "tools_path",
    "contract_path",
    "worktree_group",
    "code_worktree",
    "memory_worktree",
    "ledger_path",
    "storage",
    "pathRules",
    "crossRepo",
)


class ResolverCliTests(unittest.TestCase):
    def test_external_memory_resolution_reports_expected_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            fixture = create_external_fixture(root)

            context = run_package_resolver(
                "--code-repository-name",
                "agents-remember",
                "--workspace-root",
                str(fixture["workspace"]),
                "--coordination-root",
                str(fixture["coordination"]),
            )

            assert_context_shape(self, context)
            self.assertEqual(context["topology"], "external")
            self.assertEqual(context["coordination_root"], fixture["coordination"].as_posix())
            self.assertEqual(context["memory_root"], fixture["memory"].as_posix())

    def test_internal_memory_resolution_reports_expected_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            fixture = create_internal_fixture(root)

            context = run_package_resolver(
                "--code-repository-name",
                "agents-remember",
                "--code-repository-root",
                str(fixture["repo"]),
                "--topology",
                "internal",
            )

            assert_context_shape(self, context)
            self.assertEqual(context["topology"], "internal")
            self.assertEqual(context["code_repository_root"], fixture["repo"].as_posix())
            self.assertEqual(context["memory_root"], (fixture["repo"] / "ar-memory").as_posix())

    def test_worktree_contract_resolution_reports_expected_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            fixture = create_external_fixture(root)
            repo = fixture["repo"]
            coordination = fixture["coordination"]
            memory = fixture["memory"]
            contract = default_contract(
                ContractTask(
                    name="Resolver Parity",
                    repo_name="agents-remember",
                    coordination_root=coordination,
                    workflow_kind="light-task",
                    memory_mode="external",
                ),
                leaf=LeafIdentity(worktree_name="resolver-parity"),
                code=RepoBranchPlan(
                    repo_path=repo,
                    source_branch=current_branch(repo),
                    work_branch="ar/resolver-parity",
                    base_commit=current_head(repo),
                ),
                memory=RepoBranchPlan(
                    repo_path=memory,
                    source_branch="main",
                    work_branch="ar/resolver-parity-memory",
                    base_commit=current_head(repo),
                ),
            )
            write_contract(contract.contract_path, contract)

            context = run_package_resolver(
                "--code-repository-name",
                "agents-remember",
                "--workspace-root",
                str(fixture["workspace"]),
                "--contract-path",
                str(contract.contract_path),
            )

            assert_context_shape(self, context)
            self.assertEqual(context["contract_path"], contract.contract_path.as_posix())
            self.assertEqual(context["worktree_group"], contract.worktree_group.as_posix())
            self.assertEqual(context["code_worktree"], contract.code_worktree.as_posix())

            by_leaf = run_package_resolver(
                "--code-repository-name",
                "agents-remember",
                "--workspace-root",
                str(fixture["workspace"]),
                "--coordination-root",
                str(coordination),
                "--task-name",
                "Resolver Parity",
                "--leaf-id",
                "resolver-parity",
            )
            self.assertEqual(by_leaf["contract_path"], contract.contract_path.as_posix())
            self.assertEqual(by_leaf["task_root"], contract.task_root.as_posix())

    def test_parent_task_disambiguates_nested_task_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            fixture = create_external_fixture(root)
            repo = fixture["repo"]
            coordination = fixture["coordination"]
            head = current_head(repo)
            root_child = coordination / "tasks" / "agents-remember" / "child"
            nested_child = coordination / "tasks" / "agents-remember" / "parent" / "child"
            for task_root, branch in (
                (root_child, "ar/root-child"),
                (nested_child, "ar/parent-child"),
            ):
                write_contract(
                    task_root / "series-contract.md",
                    default_series_contract(
                        ContractTask(
                            name="child",
                            repo_name="agents-remember",
                            coordination_root=coordination,
                            workflow_kind="light-task",
                            memory_mode="disabled",
                        ),
                        code=RepoBranchPlan(
                            repo_path=repo,
                            source_branch=current_branch(repo),
                            work_branch=branch,
                            base_commit=head,
                        ),
                        task_root=task_root,
                    ),
                )

            with self.assertRaisesRegex(TaskResolutionError, "multiple active tasks"):
                resolve_active_task_root(
                    coordination,
                    "agents-remember",
                    "child",
                    fallback=False,
                )

            context = run_package_resolver(
                "--code-repository-name",
                "agents-remember",
                "--workspace-root",
                str(fixture["workspace"]),
                "--coordination-root",
                str(coordination),
                "--task-name",
                "child",
                "--parent-task",
                "parent",
            )

            assert_context_shape(self, context)
            self.assertEqual(context["task_root"], nested_child.as_posix())

    def test_active_series_discovery_excludes_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            fixture = create_external_fixture(root)
            repo = fixture["repo"]
            coordination = fixture["coordination"]
            repo_tasks = coordination / "tasks" / "agents-remember"
            active = repo_tasks / "active"
            archived = repo_tasks / "0_archive" / "archived"
            for task_root, task_name in ((active, "active"), (archived, "archived")):
                write_contract(
                    task_root / "series-contract.md",
                    default_series_contract(
                        ContractTask(
                            name=task_name,
                            repo_name="agents-remember",
                            coordination_root=coordination,
                            workflow_kind="light-task",
                            memory_mode="disabled",
                        ),
                        code=RepoBranchPlan(
                            repo_path=repo,
                            source_branch=current_branch(repo),
                            work_branch=f"ar/{task_name}",
                            base_commit=current_head(repo),
                        ),
                        task_root=task_root,
                    ),
                )

            discovered = list(iter_active_series_contracts(repo_tasks))

            self.assertEqual(discovered, [active / "series-contract.md"])
            with self.assertRaisesRegex(TaskResolutionError, "active task not found"):
                resolve_active_task_root(
                    coordination,
                    "agents-remember",
                    "archived",
                    fallback=False,
                )


def assert_context_shape(
    testcase: unittest.TestCase,
    context: dict[str, object],
) -> None:
    testcase.maxDiff = None
    testcase.assertEqual(set(CONTEXT_KEYS), set(context.keys()))


def create_external_fixture(root: Path) -> dict[str, Path]:
    workspace = root / "workspace"
    coordination = root / "ar-coordination"
    repo = workspace / "agents-remember"
    adjacent = workspace / "repo-b"
    memory = coordination / "memory-repos" / "ar-agents-remember"
    initialize_git_repo(repo)
    initialize_git_repo(adjacent)
    create_memory_settings(memory, topology="external", expected_branch=current_branch(adjacent))
    return {
        "workspace": workspace,
        "coordination": coordination,
        "repo": repo,
        "memory": memory,
    }


def create_internal_fixture(root: Path) -> dict[str, Path]:
    workspace = root / "workspace"
    repo = workspace / "agents-remember"
    memory = repo / "ar-memory"
    initialize_git_repo(repo)
    create_memory_settings(memory, topology="internal")
    return {"workspace": workspace, "repo": repo, "memory": memory}


def create_memory_settings(
    memory_root: Path,
    *,
    topology: str,
    expected_branch: str = "",
) -> None:
    (memory_root / "system").mkdir(parents=True, exist_ok=True)
    (memory_root / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory_root / "docs").mkdir(parents=True, exist_ok=True)
    (memory_root / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
    settings: dict[str, object] = {
        "version": 2,
        "onboarding": {
            "storage": {"mode": "memory-repo" if topology == "external" else "repo-sidecar"},
            "pathRules": [
                {
                    "path": "src",
                    "include": {"paths": ["**/*.py"], "fileTypes": [".py"]},
                    "exclude": {"paths": ["generated/**"], "fileTypes": [".png"]},
                }
            ],
        },
    }
    if expected_branch:
        settings["crossRepo"] = {
            "allow": [
                {
                    "repo": "repo-b",
                    "expectedBranch": expected_branch,
                    "includeCode": True,
                    "includeMemory": False,
                }
            ]
        }
    (memory_root / "system" / "settings.json").write_text(
        json.dumps(settings, indent=2),
        encoding="utf-8",
    )


def run_package_resolver(*args: str) -> dict[str, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(MCP_SRC)
    return run_json(
        [
            sys.executable,
            "-m",
            "agents_remember.cli.coordination_resolver",
            *args,
            "--format",
            "json",
        ],
        env=env,
    )


def run_json(command: list[str], *, env: dict[str, str]) -> dict[str, object]:
    result = subprocess.run(
        command,
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def initialize_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    run_git(repo, ["init"])
    run_git(repo, ["config", "user.email", "agents-remember@example.invalid"])
    run_git(repo, ["config", "user.name", "Agents Remember"])
    (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
    run_git(repo, ["add", "README.md"])
    run_git(repo, ["commit", "-m", "init"])
    run_git(repo, ["branch", "-M", "main"])


def current_branch(repo: Path) -> str:
    return git_output(repo, ["branch", "--show-current"])


def current_head(repo: Path) -> str:
    return git_output(repo, ["rev-parse", "HEAD"])


def git_output(repo: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def run_git(repo: Path, args: list[str]) -> None:
    git_output(repo, args)


if __name__ == "__main__":
    unittest.main()
