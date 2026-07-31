"""Issue #54: worktree_start stale-base preflight + memory source branch auto-template."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.memory_ledger import create_initial_ledger, write_ledger
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.start import (
    _ensure_memory_source_branch,
    _stale_base_preflight,
    prepare_memory_for_start,
)
from agents_remember.worktrees.worktree_contract import default_contract

CONTEXT = SimpleNamespace(code_repository_name="repo-a")


class StaleBasePreflightTests(unittest.TestCase):
    def test_no_upstream_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo = make_repo(root / "repo-a")
            contract = make_contract(root, code_repo)
            self.assertIsNone(_stale_base_preflight(CONTEXT, contract, WorktreeArgs()))

    def test_behind_code_source_branch_blocks_with_recovery_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo, other = make_clone_pair(root)
            commit_file(other, "remote.txt", "remote change")
            git(other, "push", "origin", "HEAD")
            contract = make_contract(root, code_repo)

            block = _stale_base_preflight(CONTEXT, contract, WorktreeArgs())

            assert block is not None
            self.assertEqual(block["state"], "blocked")
            self.assertEqual(block["nextOperation"], "choose_stale_base_recovery")
            self.assertEqual(block["nextRequiredArgs"], ["stale_base_choice"])
            stale: list[dict[str, Any]] = block["staleBases"]  # type: ignore[assignment]
            self.assertEqual([finding["side"] for finding in stale], ["code"])
            self.assertEqual(stale[0]["state"], "behind")
            self.assertEqual(stale[0]["behind"], 1)

    def test_proceed_stale_overrides_the_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo, other = make_clone_pair(root)
            commit_file(other, "remote.txt", "remote change")
            git(other, "push", "origin", "HEAD")
            contract = make_contract(root, code_repo)

            self.assertIsNone(
                _stale_base_preflight(
                    CONTEXT, contract, WorktreeArgs(stale_base_choice="proceed-stale")
                )
            )

    def test_fast_forward_recovers_non_checked_out_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo, other = make_clone_pair(root)
            branch = git(code_repo, "branch", "--show-current")
            git(code_repo, "checkout", "-b", "parked")
            commit_file(other, "remote.txt", "remote change")
            git(other, "push", "origin", "HEAD")
            remote_head = git(other, "rev-parse", "HEAD")
            contract = make_contract(root, code_repo, source_branch=branch)

            block = _stale_base_preflight(
                CONTEXT, contract, WorktreeArgs(stale_base_choice="fast-forward")
            )

            self.assertIsNone(block)
            self.assertEqual(git(code_repo, "rev-parse", branch), remote_head)

    def test_fast_forward_recovers_checked_out_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo, other = make_clone_pair(root)
            branch = git(code_repo, "branch", "--show-current")
            commit_file(other, "remote.txt", "remote change")
            git(other, "push", "origin", "HEAD")
            remote_head = git(other, "rev-parse", "HEAD")
            contract = make_contract(root, code_repo, source_branch=branch)

            block = _stale_base_preflight(
                CONTEXT, contract, WorktreeArgs(stale_base_choice="fast-forward")
            )

            self.assertIsNone(block)
            self.assertEqual(git(code_repo, "rev-parse", branch), remote_head)

    def test_fast_forward_cannot_recover_diverged_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo, other = make_clone_pair(root)
            commit_file(other, "remote.txt", "remote change")
            git(other, "push", "origin", "HEAD")
            commit_file(code_repo, "local.txt", "local change")
            contract = make_contract(root, code_repo)

            block = _stale_base_preflight(
                CONTEXT, contract, WorktreeArgs(stale_base_choice="fast-forward")
            )

            assert block is not None
            self.assertEqual(block["state"], "blocked")
            stale: list[dict[str, Any]] = block["staleBases"]  # type: ignore[assignment]
            self.assertEqual(stale[0]["state"], "diverged")
            self.assertIn("fast-forwarded", stale[0]["recovery_error"])

    def test_offline_fetch_reports_unknown_and_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo, _ = make_clone_pair(root)
            missing = (root / "missing-origin.git").as_posix()
            git(code_repo, "remote", "set-url", "origin", missing)
            contract = make_contract(root, code_repo)

            self.assertIsNone(_stale_base_preflight(CONTEXT, contract, WorktreeArgs()))

    def test_behind_memory_source_branch_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo = make_repo(root / "repo-a")
            memory_repo, memory_other = make_clone_pair(root / "mem", name="ar-repo-a")
            commit_file(memory_other, "onboarding-note.md", "newer official memory")
            git(memory_other, "push", "origin", "HEAD")
            contract = make_contract(root, code_repo, memory_repo=memory_repo)

            block = _stale_base_preflight(CONTEXT, contract, WorktreeArgs())

            assert block is not None
            stale: list[dict[str, Any]] = block["staleBases"]  # type: ignore[assignment]
            self.assertEqual([finding["side"] for finding in stale], ["memory"])


class MemorySourceBranchTemplateTests(unittest.TestCase):
    def test_missing_memory_source_branch_is_created_from_official_tip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code_repo = make_repo(root / "repo-a")
            code_base = git(code_repo, "rev-parse", "HEAD")
            memory_repo = root / "ar-coordination" / "memory-repos" / "ar-repo-a"
            make_repo(memory_repo)
            memory_seed = git(memory_repo, "rev-parse", "HEAD")
            write_ledger(
                memory_repo / "memory.md",
                create_initial_ledger("repo-a", code_base, memory_seed),
            )
            git(memory_repo, "add", "memory.md")
            git(memory_repo, "commit", "-m", "Add memory ledger")
            memory_base = git(memory_repo, "rev-parse", "HEAD")
            contract = make_contract(
                root,
                code_repo,
                memory_repo=memory_repo,
                source_branch="fix/new-task",
                code_base_commit=code_base,
                memory_base_commit=memory_base,
            )

            result = prepare_memory_for_start(contract, WorktreeArgs(dry_run=False))

            self.assertEqual(result["state"], "compatible")
            created: dict[str, Any] = result["memorySourceBranch"]  # type: ignore[assignment]
            self.assertEqual(created["state"], "created-from-official-tip")
            self.assertEqual(created["base"], memory_base)
            self.assertEqual(git(memory_repo, "rev-parse", "fix/new-task"), memory_base)

    def test_dry_run_reports_would_create_without_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_repo = make_repo(root / "mem-repo")
            base = git(memory_repo, "rev-parse", "HEAD")
            contract = make_contract(
                root,
                make_repo(root / "repo-a"),
                memory_repo=memory_repo,
                source_branch="fix/new-task",
                memory_base_commit=base,
            )

            state = _ensure_memory_source_branch(contract, dry_run=True)

            self.assertEqual(state["state"], "would-create-from-official-tip")
            branches = git(memory_repo, "branch", "--list", "fix/new-task")
            self.assertEqual(branches, "")

    def test_existing_memory_source_branch_is_reported_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_repo = make_repo(root / "mem-repo")
            branch = git(memory_repo, "branch", "--show-current")
            contract = make_contract(
                root,
                make_repo(root / "repo-a"),
                memory_repo=memory_repo,
                source_branch=branch,
            )

            state = _ensure_memory_source_branch(contract, dry_run=False)

            self.assertEqual(state, {"state": "existing", "branch": branch})


def make_contract(
    root: Path,
    code_repo: Path,
    *,
    source_branch: str | None = None,
    memory_repo: Path | None = None,
    code_base_commit: str = "c1",
    memory_base_commit: str = "m1",
):
    branch = source_branch or git(code_repo, "branch", "--show-current")
    return default_contract(
        task_name="Fix Thing",
        repo_name="repo-a",
        workflow_kind="light-task",
        memory_mode="external",
        coordination_root=root / "ar-coordination",
        code_repo_path=code_repo,
        code_source_branch=branch,
        code_work_branch="ar/fix-thing",
        code_base_commit=code_base_commit,
        worktree_name="fix-thing",
        memory_repo_path=memory_repo,
        memory_source_branch=branch,
        memory_work_branch="ar/fix-thing",
        memory_base_commit=memory_base_commit,
    )


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init")
    git(path, "config", "user.email", "agents-remember@example.invalid")
    git(path, "config", "user.name", "Agents Remember")
    commit_file(path, "README.md", "# Fixture")
    return path


def make_clone_pair(root: Path, name: str = "repo-a") -> tuple[Path, Path]:
    seed = make_repo(root / f"{name}-seed")
    bare = root / f"{name}-origin.git"
    git(root, "clone", "--bare", str(seed), str(bare))
    clone = root / name
    other = root / f"{name}-other"
    git(root, "clone", str(bare), str(clone))
    git(root, "clone", str(bare), str(other))
    for repo in (clone, other):
        git(repo, "config", "user.email", "agents-remember@example.invalid")
        git(repo, "config", "user.name", "Agents Remember")
    return clone, other


def commit_file(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content + "\n", encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", f"add {name}")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


if __name__ == "__main__":
    unittest.main()
