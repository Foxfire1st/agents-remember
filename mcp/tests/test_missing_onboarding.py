from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.coordination_context_resolver import StorageSettings
from agents_remember.memory_quality.integrity.check_missing_onboarding import (
    check_missing_onboarding,
    main,
)


class MissingOnboardingTests(unittest.TestCase):
    def test_untracked_added_file_reports_missing_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo, onboarding = initialize_repo(Path(tmp_dir))
            write_source(repo / "src" / "new.py")

            result = check_missing_onboarding(
                code_repository_root=repo,
                onboarding_root=onboarding,
                settings=StorageSettings(mode="memory-repo", default="memory-repo"),
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["missingCount"], 1)
            self.assertEqual(result["missing"][0]["sourceFile"], "src/new.py")
            self.assertTrue(result["missing"][0]["expectedOnboarding"].endswith("src/new.py.md"))

    def test_staged_added_file_with_sidecar_is_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo, onboarding = initialize_repo(Path(tmp_dir))
            write_source(repo / "src" / "new.py")
            write_sidecar(onboarding / "src" / "new.py.md", "src/new.py")
            run_git(repo, ["add", "src/new.py"])

            result = check_missing_onboarding(
                code_repository_root=repo,
                onboarding_root=onboarding,
                settings=StorageSettings(mode="memory-repo", default="memory-repo"),
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["sourceCount"], 1)
            self.assertEqual(result["missingCount"], 0)

    def test_excluded_added_file_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo, onboarding = initialize_repo(Path(tmp_dir))
            write_source(repo / "generated" / "out.py")
            settings = StorageSettings(
                mode="memory-repo",
                default="memory-repo",
                path_rules=[
                    {
                        "includes": ["*"],
                        "excludes": ["generated/**"],
                    }
                ],
            )

            result = check_missing_onboarding(
                code_repository_root=repo,
                onboarding_root=onboarding,
                settings=settings,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["sourceCount"], 1)
            self.assertEqual(result["missingCount"], 0)

    def test_renamed_target_requires_target_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo, onboarding = initialize_repo(Path(tmp_dir))
            write_source(repo / "src" / "old.py")
            run_git(repo, ["add", "src/old.py"])
            run_git(repo, ["commit", "-m", "add old"])
            run_git(repo, ["mv", "src/old.py", "src/new.py"])

            result = check_missing_onboarding(
                code_repository_root=repo,
                onboarding_root=onboarding,
                settings=StorageSettings(mode="memory-repo", default="memory-repo"),
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["missingCount"], 1)
            self.assertEqual(result["missing"][0]["sourceFile"], "src/new.py")

    def test_cli_uses_git_common_dir_name_for_renamed_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            repo, _unused_onboarding = initialize_repo(root, repo_name="agents-remember")
            worktree = root / "worktrees" / "mcp_and_refactor"
            run_git(repo, ["worktree", "add", "-b", "feature/mcp-and-refactor", str(worktree)])
            write_source(worktree / "mcp" / "src" / "new.py")

            coordination_root = root / "ar-coordination"
            memory_root = coordination_root / "memory-repos" / "ar-agents-remember"
            onboarding = memory_root / "onboarding"
            write_sidecar(
                onboarding / "mcp" / "src" / "new.py.md",
                "mcp/src/new.py",
                repo_name="agents-remember",
            )
            system_root = memory_root / "system"
            system_root.mkdir(parents=True, exist_ok=True)
            (system_root / "settings.md").write_text("# Settings\n", encoding="utf-8")
            (system_root / "settings.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "onboarding": {
                            "storage": {"mode": "memory-repo"},
                            "pathRules": {
                                "include": {"paths": ["mcp/**"], "fileTypes": [".py"]},
                                "exclude": {"paths": [], "fileTypes": []},
                            },
                        },
                        "crossRepo": {"allow": []},
                    }
                ),
                encoding="utf-8",
            )

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--code-repository-root",
                        str(worktree),
                        "--topology",
                        "external",
                        "--coordination-root",
                        str(coordination_root),
                        "--format",
                        "json",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["sourceCount"], 1)
            self.assertEqual(payload["missingCount"], 0)


def initialize_repo(root: Path, repo_name: str = "repo-a") -> tuple[Path, Path]:
    repo = root / "workspace" / repo_name
    onboarding = root / "memory" / "onboarding"
    repo.mkdir(parents=True)
    onboarding.mkdir(parents=True)
    run_git(repo, ["init"])
    run_git(repo, ["config", "user.email", "agents-remember@example.invalid"])
    run_git(repo, ["config", "user.name", "Agents Remember"])
    (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
    run_git(repo, ["add", "README.md"])
    run_git(repo, ["commit", "-m", "init"])
    return repo, onboarding


def write_source(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("print('hello')\n", encoding="utf-8")


def write_sidecar(path: Path, source_path: str, repo_name: str = "repo-a") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# {source_path}",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| repository | {repo_name} |",
                f"| path | `{source_path}` |",
                "| doc_type | `file-level-onboarding` |",
                "",
            ]
        ),
        encoding="utf-8",
    )


def run_git(repo: Path, args: list[str]) -> None:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
