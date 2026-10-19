"""The request-local capture proof, including Git-native ignore enumeration (L177)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents_remember.kernel.git_command import run_git
from agents_remember.worktrees.modules import capture_baseline
from agents_remember.worktrees.modules.capture_baseline import (
    begin_capture_baseline,
    capture_baseline_unchanged,
    capture_entries,
    memo_capture_baselines,
    note_capture_baseline,
)
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from agents_remember_test_support.testing.git_command import _commit, _init


class CaptureBaselineTests(unittest.TestCase):
    """The fast recheck: an unmoved worktree proves itself without re-deriving (F17)."""

    def _repo(self, root: Path) -> Path:
        repo = root / "repo"
        _init(repo)
        _commit(repo, "file.txt", "one\n")
        return repo

    def _capture(
        self, repo: Path, root: Path
    ) -> tuple[str, tuple[tuple[str, int, int, int, int], ...]]:
        """One capture through the same hook the review's capture uses."""

        captured: list[tuple[str, int, int, int, int]] = []

        def observe(index: Path) -> None:
            entries = capture_entries(index)
            captured.extend(entries or ())

        tree = worktree_candidate_tree(repo, root / "note.index", captured=observe)
        return tree, tuple(captured)

    def test_an_unmoved_dirty_worktree_proves_itself(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            (repo / "file.txt").write_text("dirty\n", encoding="utf-8")
            with memo_capture_baselines():
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                self.assertTrue(capture_baseline_unchanged(repo, tree=tree))

    def test_a_file_edit_is_a_doubt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            with memo_capture_baselines():
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                (repo / "file.txt").write_text("edited\n", encoding="utf-8")
                self.assertFalse(capture_baseline_unchanged(repo, tree=tree))

    def test_a_staged_change_is_a_doubt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            with memo_capture_baselines():
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
                assert run_git(repo, ["add", "staged.txt"]).returncode == 0
                self.assertFalse(capture_baseline_unchanged(repo, tree=tree))

    def test_a_new_commit_is_a_doubt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            with memo_capture_baselines():
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                _commit(repo, "second.txt", "two\n")
                self.assertFalse(capture_baseline_unchanged(repo, tree=tree))

    def test_a_head_only_move_is_not_a_tree_doubt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            with memo_capture_baselines():
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                assert run_git(repo, ["branch", "other"]).returncode == 0
                assert run_git(repo, ["symbolic-ref", "HEAD", "refs/heads/other"]).returncode == 0
                # The HEAD moved with the index and every file untouched: the same
                # derivation inputs capture to the same tree, so the proof stands.
                self.assertTrue(capture_baseline_unchanged(repo, tree=tree))

    def test_without_a_scope_or_a_baseline_nothing_is_proven(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            tree, _entries = self._capture(repo, root)
            self.assertFalse(capture_baseline_unchanged(repo, tree=tree))
            with memo_capture_baselines():
                self.assertFalse(capture_baseline_unchanged(repo, tree=tree))
                self.assertFalse(
                    capture_baseline_unchanged(repo, tree="0" * 40),
                )

    def test_a_change_inside_an_ignored_folder_is_not_a_tree_doubt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
            ignored = repo / "node_modules" / "pkg"
            ignored.mkdir(parents=True)
            (ignored / "index.js").write_text("one\n", encoding="utf-8")
            with memo_capture_baselines():
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                # A capture never reads an ignored path, so an edit there cannot change its tree.
                (ignored / "index.js").write_text("two\n", encoding="utf-8")
                (ignored / "added.js").write_text("three\n", encoding="utf-8")
                self.assertTrue(capture_baseline_unchanged(repo, tree=tree))

    def test_a_change_to_an_already_captured_path_is_a_doubt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            (repo / "file.txt").write_text("dirty\n", encoding="utf-8")
            with memo_capture_baselines():
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                # The path was in the census already; its own stat still has to move.
                (repo / "file.txt").write_text("dirty again\n", encoding="utf-8")
                self.assertFalse(capture_baseline_unchanged(repo, tree=tree))

    def test_a_new_untracked_file_is_a_doubt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            package = repo / "pkg"
            package.mkdir()
            # Relative to pkg/, this ignores pkg/pkg/new, never pkg/new (R1-01).
            (package / ".gitignore").write_text("pkg/new\n", encoding="utf-8")
            with memo_capture_baselines():
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                (package / "new").write_text("new\n", encoding="utf-8")
                self.assertEqual(run_git(repo, ["check-ignore", "pkg/new"]).returncode, 1)
                self.assertFalse(capture_baseline_unchanged(repo, tree=tree))

    def test_a_new_file_in_a_new_directory_is_a_doubt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            with memo_capture_baselines():
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                # Nothing moved in any captured file; the directory that gained one did.
                (repo / "pkg" / "nested").mkdir(parents=True)
                (repo / "pkg" / "nested" / "new.py").write_text("x = 1\n", encoding="utf-8")
                self.assertFalse(capture_baseline_unchanged(repo, tree=tree))

    def test_an_ignore_rule_edit_is_a_doubt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
            assert run_git(repo, ["add", ".gitignore"]).returncode == 0
            assert run_git(repo, ["commit", "-qm", "ignore"]).returncode == 0
            with memo_capture_baselines():
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                # The rule is a tracked file and a capture reads it, so an edit moves the proof.
                (repo / ".gitignore").write_text("", encoding="utf-8")
                self.assertFalse(capture_baseline_unchanged(repo, tree=tree))

    def test_an_eligible_addition_under_a_wildcard_negation_is_a_doubt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            # Re-open the parent too: Git never re-includes a child of an excluded directory.
            (repo / ".gitignore").write_text(
                "node_modules/*\n!node_modules/pkg/\nnode_modules/pkg/*\n!**/keep.js\n",
                encoding="utf-8",
            )
            package = repo / "node_modules" / "pkg"
            package.mkdir(parents=True)
            (package / "index.js").write_text("one\n", encoding="utf-8")
            with memo_capture_baselines():
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                (package / "keep.js").write_text("two\n", encoding="utf-8")
                self.assertFalse(capture_baseline_unchanged(repo, tree=tree))

    def test_a_pattern_with_a_significant_leading_space_closes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            # The leading space is part of the name git matches, so node_modules is not ignored.
            (repo / ".gitignore").write_text(" node_modules/\n", encoding="utf-8")
            package = repo / "node_modules" / "pkg"
            package.mkdir(parents=True)
            (package / "index.js").write_text("one\n", encoding="utf-8")
            with memo_capture_baselines():
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                (package / "added.js").write_text("two\n", encoding="utf-8")
                self.assertFalse(capture_baseline_unchanged(repo, tree=tree))

    def test_a_restored_directory_mtime_cannot_certify_an_unchanged_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            package = repo / "pkg"
            package.mkdir()
            (package / "kept.py").write_text("x = 1\n", encoding="utf-8")
            with memo_capture_baselines():
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                stamp = package.stat()
                (package / "added.py").write_text("y = 2\n", encoding="utf-8")
                # Putting the directory's own times back does not hide the new name.
                os.utime(package, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                self.assertFalse(capture_baseline_unchanged(repo, tree=tree))

    def test_observation_does_not_descend_into_a_wildcard_ignored_dependency_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            nested = repo / "pkg"
            nested.mkdir()
            (nested / ".gitignore").write_text("/node_modules*/\n", encoding="utf-8")
            package = nested / "node_modules" / "pkg"
            package.mkdir(parents=True)
            (package / "index.js").write_text("one\n", encoding="utf-8")
            opened: list[str] = []
            original = os.scandir

            def recorded(path: str = ".") -> object:
                opened.append(str(path))
                return original(path)

            with (
                memo_capture_baselines(),
                patch.object(capture_baseline.os, "scandir", recorded),
            ):
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                self.assertTrue(capture_baseline_unchanged(repo, tree=tree))
            self.assertEqual([path for path in opened if "node_modules" in path], [])

    def test_restoring_a_deleted_tracked_file_requires_recapture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            (repo / "file.txt").unlink()
            with memo_capture_baselines():
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                (repo / "file.txt").write_text("one\n", encoding="utf-8")
                self.assertFalse(capture_baseline_unchanged(repo, tree=tree))
                self.assertNotEqual(self._capture(repo, root)[0], tree)

    def test_external_ignore_changes_require_recapture_in_both_directions(self) -> None:
        for first, second in (("", "new.py\n"), ("new.py\n", "")):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo = self._repo(root)
                (repo / "new.py").write_text("eligible or ignored\n", encoding="utf-8")
                rules = repo / ".git" / "info" / "exclude"
                rules.write_text(first, encoding="utf-8")
                with memo_capture_baselines():
                    before = begin_capture_baseline(repo)
                    tree, entries = self._capture(repo, root)
                    note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                    rules.write_text(second, encoding="utf-8")
                    self.assertFalse(capture_baseline_unchanged(repo, tree=tree))
                    self.assertNotEqual(self._capture(repo, root)[0], tree)

    def test_a_staged_addition_cannot_hide_a_changed_external_ignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self._repo(root)
            (repo / "new.py").write_text("new\n", encoding="utf-8")
            assert run_git(repo, ["add", "new.py"]).returncode == 0
            with memo_capture_baselines():
                before = begin_capture_baseline(repo)
                tree, entries = self._capture(repo, root)
                note_capture_baseline(repo, tree=tree, entries=entries, before=before)
                (repo / ".git" / "info" / "exclude").write_text("new.py\n", encoding="utf-8")
                self.assertFalse(capture_baseline_unchanged(repo, tree=tree))
                self.assertNotEqual(self._capture(repo, root)[0], tree)
