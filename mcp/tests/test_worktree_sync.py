"""Issue #54: worktree_sync pulls the moved official line into a live worktree."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.memory_ledger import (
    create_initial_ledger,
    parse_ledger_text,
    prepend_mapping,
    write_ledger,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.sync import sync_result
from agents_remember.worktrees.worktree_contract import (
    default_contract,
    load_contract,
    write_contract,
)


class SyncFixture:
    """Live code/memory worktrees whose official lines can be moved."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.code_repo = root / "repo-a"
        self.code_base = make_repo(self.code_repo)
        self.memory_repo = root / "ar-coordination" / "memory-repos" / "ar-repo-a"
        memory_seed = make_repo(self.memory_repo)
        write_ledger(
            self.memory_repo / "memory.md",
            create_initial_ledger("repo-a", self.code_base, memory_seed),
        )
        git(self.memory_repo, "add", "memory.md")
        git(self.memory_repo, "commit", "-m", "Add memory ledger")
        self.memory_base = git(self.memory_repo, "rev-parse", "HEAD")
        self.contract = default_contract(
            task_name="Sync Thing",
            repo_name="repo-a",
            workflow_kind="light-task",
            memory_mode="external",
            coordination_root=root / "ar-coordination",
            code_repo_path=self.code_repo,
            code_source_branch="main",
            code_work_branch="ar/sync-thing",
            code_base_commit=self.code_base,
            worktree_name="sync-thing",
            memory_repo_path=self.memory_repo,
            memory_source_branch="main",
            memory_work_branch="ar/sync-thing",
            memory_base_commit=self.memory_base,
        )
        assert self.contract.memory_worktree is not None
        git(
            self.code_repo,
            "worktree", "add", "-b", self.contract.code_work_branch,
            str(self.contract.code_worktree), "main",
        )
        git(
            self.memory_repo,
            "worktree", "add", "-b", self.contract.memory_work_branch,
            str(self.contract.memory_worktree), "main",
        )
        write_contract(self.contract.contract_path, self.contract)

    def move_official_code(self) -> str:
        commit_file(self.code_repo, "src/new.py", "VALUE = 'landed'")
        return git(self.code_repo, "rev-parse", "main")

    def map_official_memory(self, code_tip: str) -> str:
        """Land an official memory change plus a ledger row mapping code_tip."""
        commit_file(self.memory_repo, "onboarding/src/new.py.md", "# new.py onboarding")
        content_commit = git(self.memory_repo, "rev-parse", "HEAD")
        ledger_path = self.memory_repo / "memory.md"
        ledger = parse_ledger_text(ledger_path.read_text(encoding="utf-8"))
        write_ledger(ledger_path, prepend_mapping(ledger, code_tip, content_commit))
        git(self.memory_repo, "add", "memory.md")
        git(self.memory_repo, "commit", "-m", "Map new code tip")
        return git(self.memory_repo, "rev-parse", "main")

    def sync(self, **kwargs: Any):
        return sync_result(WorktreeArgs(contract_path=self.contract.contract_path, **kwargs))


class WorktreeSyncTests(unittest.TestCase):
    def test_pure_fast_forward_sync_advances_both_sides_and_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            code_tip = fixture.move_official_code()
            memory_tip = fixture.map_official_memory(code_tip)

            result = fixture.sync()

            self.assertEqual(result.payload["state"], "synced")
            self.assertEqual(section(result.payload, "code")["state"], "merged")
            self.assertEqual(section(result.payload, "memory")["state"], "fast-forwarded")
            self.assertEqual(
                git(fixture.contract.code_worktree, "rev-parse", "HEAD"), code_tip
            )
            assert fixture.contract.memory_worktree is not None
            self.assertEqual(
                git(fixture.contract.memory_worktree, "rev-parse", "HEAD"), memory_tip
            )
            reloaded = load_contract(fixture.contract.contract_path)
            self.assertEqual(reloaded.code_base_commit, code_tip)
            self.assertEqual(reloaded.memory_base_commit, memory_tip)
            self.assertEqual(len(reloaded.sync_log), 1)
            self.assertEqual(reloaded.sync_log[0]["codeBaseTo"], code_tip)

    def test_mid_cycle_official_line_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            fixture.move_official_code()  # no ledger mapping for the new tip

            result = fixture.sync()

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "blocked")
            self.assertIn("mid-cycle", str(result.payload["summary"]))

    def test_already_current_pair_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            result = fixture.sync()
            self.assertEqual(result.payload["state"], "already-current")

    def test_dry_run_previews_without_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)

            result = fixture.sync(dry_run=True)

            self.assertEqual(result.payload["state"], "would-sync")
            self.assertEqual(
                git(fixture.contract.code_worktree, "rev-parse", "HEAD"), fixture.code_base
            )
            reloaded = load_contract(fixture.contract.contract_path)
            self.assertEqual(reloaded.code_base_commit, fixture.code_base)
            self.assertEqual(reloaded.sync_log, ())

    def test_code_merge_conflict_blocks_and_aborts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            commit_file(fixture.contract.code_worktree, "README.md", "work-branch version")
            commit_file(fixture.code_repo, "README.md", "official version")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)

            result = fixture.sync()

            self.assertEqual(result.returncode, 2)
            self.assertEqual(section(result.payload, "code")["state"], "conflicts")
            self.assertIn("README.md", section(result.payload, "code")["files"])
            merge_head = fixture.contract.code_worktree / ".git"
            self.assertNotIn(
                "MERGE_HEAD",
                git(fixture.contract.code_worktree, "status", "--porcelain"),
            )
            self.assertEqual(
                git(fixture.contract.code_worktree, "rev-parse", "HEAD"),
                git(fixture.contract.code_worktree, "rev-parse", "ar/sync-thing"),
            )
            _ = merge_head

    def test_local_memory_commits_need_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            assert fixture.contract.memory_worktree is not None
            commit_file(
                fixture.contract.memory_worktree,
                "onboarding/local.md",
                "# local memory work",
            )
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)

            result = fixture.sync()

            self.assertEqual(result.returncode, 2)
            self.assertEqual(section(result.payload, "memory")["state"], "needs-review")
            self.assertEqual(result.payload["nextRequiredArgs"], ["memory_sync_choice"])

    def test_skip_memory_choice_advances_code_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            assert fixture.contract.memory_worktree is not None
            commit_file(
                fixture.contract.memory_worktree,
                "onboarding/local.md",
                "# local memory work",
            )
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)

            result = fixture.sync(memory_sync_choice="skip-memory")

            self.assertEqual(result.payload["state"], "synced")
            self.assertEqual(section(result.payload, "memory")["state"], "skipped-by-choice")
            reloaded = load_contract(fixture.contract.contract_path)
            self.assertEqual(reloaded.code_base_commit, code_tip)
            self.assertEqual(reloaded.memory_base_commit, fixture.memory_base)

    def test_merge_memory_choice_merges_disjoint_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            assert fixture.contract.memory_worktree is not None
            commit_file(
                fixture.contract.memory_worktree,
                "onboarding/local.md",
                "# local memory work",
            )
            code_tip = fixture.move_official_code()
            memory_tip = fixture.map_official_memory(code_tip)

            result = fixture.sync(memory_sync_choice="merge-memory")

            self.assertEqual(result.payload["state"], "synced")
            self.assertEqual(section(result.payload, "memory")["state"], "merged")
            reloaded = load_contract(fixture.contract.contract_path)
            self.assertEqual(reloaded.memory_base_commit, memory_tip)


def section(payload: dict[str, object], key: str) -> dict[str, Any]:
    value = payload[key]
    assert isinstance(value, dict)
    return value


def make_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "agents-remember@example.invalid")
    git(path, "config", "user.name", "Agents Remember")
    commit_file(path, "README.md", "# Fixture")
    return git(path, "rev-parse", "HEAD")


def commit_file(repo: Path, name: str, content: str) -> None:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content + "\n", encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", f"update {name}")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


if __name__ == "__main__":
    unittest.main()
