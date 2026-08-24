"""Tests for the worktree memory mtime-sync (OQ4: grepai clone reuse).

git checkout stamps fresh mtimes; grepai's watcher skips unchanged files by ModTime,
so the worktree memory must inherit the source repo's mtimes for the cloned index to be
reused instead of fully re-embedded.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from agents_remember.worktrees.modules.startup.start_memory import _sync_worktree_memory_mtimes
from agents_remember.worktrees.worktree_contract import WorktreeContract

_OLD = 1577836800.0  # 2020-01-01 UTC
_NEW = 1893456000.0  # 2030-01-01 UTC (stands in for "freshly checked out")


def _write(path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


class MtimeSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.source = root / "memory-repos" / "ar-x"
        self.target = root / "worktrees" / "x" / "memory-x"
        # matching files in both, source stamped OLD, target stamped NEW
        for rel in ("memory.md", "onboarding/overview.md"):
            _write(self.source / rel, _OLD)
            _write(self.target / rel, _NEW)
        # a target-only file (not in source) + a .git file (must be skipped)
        _write(self.target / "only-in-target.md", _NEW)
        _write(self.target / ".git" / "HEAD", _NEW)
        self.contract = cast(
            WorktreeContract,
            SimpleNamespace(memory_repo_path=self.source, memory_worktree=self.target),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_syncs_matching_files_to_source_mtime(self) -> None:
        result = _sync_worktree_memory_mtimes(self.contract, dry_run=False)
        self.assertEqual(result["state"], "synced")
        self.assertEqual(result["filesSynced"], 2)  # the two matching files
        self.assertEqual(result["filesMissingInSource"], 1)  # only-in-target.md
        for rel in ("memory.md", "onboarding/overview.md"):
            self.assertAlmostEqual((self.target / rel).stat().st_mtime, _OLD, delta=1)

    def test_target_only_file_is_left_untouched(self) -> None:
        _sync_worktree_memory_mtimes(self.contract, dry_run=False)
        self.assertAlmostEqual((self.target / "only-in-target.md").stat().st_mtime, _NEW, delta=1)

    def test_git_dir_is_skipped(self) -> None:
        _sync_worktree_memory_mtimes(self.contract, dry_run=False)
        # .git/HEAD must not be counted or retimed
        self.assertAlmostEqual((self.target / ".git" / "HEAD").stat().st_mtime, _NEW, delta=1)

    def test_dry_run_changes_nothing(self) -> None:
        result = _sync_worktree_memory_mtimes(self.contract, dry_run=True)
        self.assertEqual(result["state"], "skipped")
        self.assertAlmostEqual((self.target / "memory.md").stat().st_mtime, _NEW, delta=1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
