from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MCP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MCP_ROOT.parent
MCP_SRC = MCP_ROOT / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.worktrees.worktree_contract import (  # noqa: E402
    default_contract,
    write_contract,
)

OLD_RESOLVER = (
    REPO_ROOT
    / "runtime"
    / "skills"
    / "U-01-core-skills"
    / "C-08-ar-coordination-context-resolver"
    / "scripts"
    / "ar_coordination_context_resolver.py"
)

PARITY_KEYS = (
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


class ResolverParityTests(unittest.TestCase):
    def test_external_memory_resolution_matches_old_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            fixture = create_external_fixture(root)

            old = run_old_resolver(
                "--code-repository-name",
                "agents-remember-md",
                "--workspace-root",
                str(fixture["workspace"]),
                "--coordination-root",
                str(fixture["coordination"]),
            )
            new = run_package_resolver(
                "--code-repository-name",
                "agents-remember-md",
                "--workspace-root",
                str(fixture["workspace"]),
                "--coordination-root",
                str(fixture["coordination"]),
            )

            assert_parity(self, old, new)

    def test_internal_memory_resolution_matches_old_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            fixture = create_internal_fixture(root)

            old = run_old_resolver(
                "--code-repository-name",
                "agents-remember-md",
                "--code-repository-root",
                str(fixture["repo"]),
                "--topology",
                "internal",
            )
            new = run_package_resolver(
                "--code-repository-name",
                "agents-remember-md",
                "--code-repository-root",
                str(fixture["repo"]),
                "--topology",
                "internal",
            )

            assert_parity(self, old, new)

    def test_worktree_contract_resolution_matches_old_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            fixture = create_external_fixture(root)
            repo = fixture["repo"]
            coordination = fixture["coordination"]
            memory = fixture["memory"]
            contract = default_contract(
                task_name="Resolver Parity",
                repo_name="agents-remember-md",
                workflow_kind="light",
                memory_mode="external",
                coordination_root=coordination,
                code_repo_path=repo,
                code_source_branch=current_branch(repo),
                code_work_branch="ar/resolver-parity",
                code_base_commit=current_head(repo),
                worktree_name="resolver-parity",
                memory_repo_path=memory,
                memory_source_branch="main",
                memory_work_branch="ar/resolver-parity-memory",
                memory_base_commit=current_head(repo),
            )
            write_contract(contract.contract_path, contract)

            old = run_old_resolver(
                "--code-repository-name",
                "agents-remember-md",
                "--workspace-root",
                str(fixture["workspace"]),
                "--contract-path",
                str(contract.contract_path),
            )
            new = run_package_resolver(
                "--code-repository-name",
                "agents-remember-md",
                "--workspace-root",
                str(fixture["workspace"]),
                "--contract-path",
                str(contract.contract_path),
            )

            assert_parity(self, old, new)


def assert_parity(
    testcase: unittest.TestCase,
    old: dict[str, object],
    new: dict[str, object],
) -> None:
    testcase.maxDiff = None
    testcase.assertEqual(
        {key: old[key] for key in PARITY_KEYS},
        {key: new[key] for key in PARITY_KEYS},
    )


def create_external_fixture(root: Path) -> dict[str, Path]:
    workspace = root / "workspace"
    coordination = root / "ar-coordination"
    repo = workspace / "agents-remember-md"
    adjacent = workspace / "repo-b"
    memory = coordination / "memory-repos" / "ar-agents-remember-md"
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
    repo = workspace / "agents-remember-md"
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


def run_old_resolver(*args: str) -> dict[str, object]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return run_json([sys.executable, str(OLD_RESOLVER), *args, "--format", "json"], env=env)


def run_package_resolver(*args: str) -> dict[str, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(MCP_SRC)
    return run_json(
        [
            sys.executable,
            "-m",
            "agents_remember.kernel.coordination_context_resolver",
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
