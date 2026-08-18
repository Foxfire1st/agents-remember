"""Tests for the worktree-start Windows long-path preflight."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.worktrees.modules import start as start_module
from agents_remember.worktrees.modules.git import longest_tracked_path_length


class LongPathBlockPayloadTests(unittest.TestCase):
    def test_under_budget_returns_none(self) -> None:
        payload = start_module.long_path_block_payload(
            label="code",
            worktree_path="C:/wt",
            longest_tracked=100,
            budget=250,
        )
        self.assertIsNone(payload)

    def test_over_budget_reports_numbers_and_remedies(self) -> None:
        worktree_path = "C:/" + "w" * 124  # 127 chars
        payload = start_module.long_path_block_payload(
            label="code",
            worktree_path=worktree_path,
            longest_tracked=140,
            budget=250,
        )
        assert payload is not None
        self.assertEqual(payload["state"], "blocked")
        self.assertEqual(payload["projectedPathLength"], 127 + 1 + 140)
        self.assertEqual(payload["pathBudget"], 250)
        self.assertEqual(payload["longestTrackedPathLength"], 140)
        remedies = cast(list[str], payload["remedies"])
        self.assertIn("LongPathsEnabled", remedies[0])
        self.assertIn("18 characters shorter", remedies[1])

    def test_exactly_at_budget_passes(self) -> None:
        payload = start_module.long_path_block_payload(
            label="code",
            worktree_path="C:/" + "w" * 106,  # 109 chars
            longest_tracked=140,  # 109 + 1 + 140 = 250
            budget=250,
        )
        self.assertIsNone(payload)


class LongestTrackedPathLengthTests(unittest.TestCase):
    def test_reports_longest_committed_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
            subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
            deep = repo / "a" / "much" / "deeper" / "nested" / "path"
            deep.mkdir(parents=True)
            (deep / "leaf-file.txt").write_text("x", encoding="utf-8")
            (repo / "short.txt").write_text("x", encoding="utf-8")
            subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
            subprocess.run([*git, "commit", "-q", "-m", "init"], cwd=repo, check=True)

            longest = longest_tracked_path_length(repo)

        self.assertEqual(longest, len("a/much/deeper/nested/path/leaf-file.txt"))

    def test_unborn_repo_reports_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self.assertEqual(longest_tracked_path_length(repo), 0)


class PreflightGateTests(unittest.TestCase):
    def _contract(self) -> Any:
        # Duck-typed stand-in for WorktreeContract; the preflight only reads
        # path/branch attributes.
        return SimpleNamespace(
            code_repo_path=Path("C:/repo"),
            code_worktree=Path("C:/" + "w" * 124),
            code_source_branch="main",
            memory_mode="external",
            memory_repo_path=Path("C:/memory"),
            memory_worktree=Path("C:/" + "m" * 124),
            memory_source_branch="main",
        )

    def test_no_block_when_long_paths_enabled(self) -> None:
        with mock.patch.object(start_module, "_windows_long_paths_enabled", return_value=True):
            self.assertIsNone(start_module._long_path_preflight(self._contract()))

    def test_blocks_on_code_repo_overflow(self) -> None:
        with (
            mock.patch.object(start_module, "_windows_long_paths_enabled", return_value=False),
            mock.patch.object(start_module, "longest_tracked_path_length", return_value=140),
        ):
            payload = start_module._long_path_preflight(self._contract())
        assert payload is not None
        self.assertEqual(payload["state"], "blocked")
        self.assertIn("code", str(payload["summary"]))

    def test_passes_when_paths_fit(self) -> None:
        with (
            mock.patch.object(start_module, "_windows_long_paths_enabled", return_value=False),
            mock.patch.object(start_module, "longest_tracked_path_length", return_value=40),
        ):
            self.assertIsNone(start_module._long_path_preflight(self._contract()))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
