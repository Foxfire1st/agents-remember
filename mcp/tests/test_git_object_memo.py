"""One request makes the same Git lookup of the same object once (MIK-R81 rule 7).

Fail-first: every test below fails without the request-scoped object memo in
``kernel/git_command.py``. All git runs go to real temporary repositories; the
count is taken at the runner, so a child saved is a child counted.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock
from unittest.mock import patch

from agents_remember.kernel import git_command
from agents_remember.kernel.git_command import (
    GitRunnerOptions,
    memo_git_object_lookups,
    run_git,
    run_git_with_index,
)
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from agents_remember_test_support.testing.git_command import _commit, _init


def _init_repo(root: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "a.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "one"], cwd=root, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@contextmanager
def _counted_git_children() -> Iterator[list[int]]:
    """Count every git child started in the block; the count is shared by reference."""
    counts = [0]
    real_run = subprocess.run

    def counting(*args: Any, **kwargs: Any) -> Any:
        argv = args[0] if args else []
        if isinstance(argv, list) and argv and argv[0] == "git":
            counts[0] += 1
        return real_run(*args, **kwargs)

    old = subprocess.run
    subprocess.run = counting  # type: ignore[assignment]
    try:
        yield counts
    finally:
        subprocess.run = old  # type: ignore[assignment]


class ObjectMemoTests(unittest.TestCase):
    def test_same_object_lookup_twice_starts_one_child(self) -> None:
        """Bare-SHA verifies collapse; a peel suffix always runs fresh (F20)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            head = _init_repo(root)
            with _counted_git_children() as counts, memo_git_object_lookups():
                first = run_git(root, ["rev-parse", "--verify", "--quiet", head])
                second = run_git(root, ["rev-parse", "--verify", "--quiet", head])
                self.assertEqual(first.stdout, second.stdout)
                peeled = run_git(root, ["rev-parse", "--verify", "--quiet", f"{head}^{{tree}}"])
                repeated = run_git(root, ["rev-parse", "--verify", "--quiet", f"{head}^{{tree}}"])
                self.assertEqual(peeled.stdout, repeated.stdout)
            # One child for the bare pair; the peel pair runs fresh twice.
            self.assertEqual(counts[0], 3)

    def test_distinct_lookups_each_start_a_child(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            head = _init_repo(root)
            with _counted_git_children() as counts, memo_git_object_lookups():
                run_git(root, ["rev-parse", "--verify", "--quiet", head])
                run_git(root, ["rev-parse", "--verify", "--quiet", f"{head}^{{tree}}"])
                run_git(root, ["cat-file", "-t", f"{head}:a.txt"])
            self.assertEqual(counts[0], 3)

    def test_mutable_lookups_are_never_memoized(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            with _counted_git_children() as counts, memo_git_object_lookups():
                run_git(root, ["rev-parse", "HEAD"])
                run_git(root, ["rev-parse", "HEAD"])
                run_git(root, ["status", "--porcelain"])
                run_git(root, ["status", "--porcelain"])
            # Two mutable lookups, each run twice: four children, none saved.
            self.assertEqual(counts[0], 4)

    def test_outside_the_scope_nothing_is_memoized(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            head = _init_repo(root)
            with _counted_git_children() as counts:
                run_git(root, ["rev-parse", "--verify", "--quiet", f"{head}^{{tree}}"])
                run_git(root, ["rev-parse", "--verify", "--quiet", f"{head}^{{tree}}"])
            self.assertEqual(counts[0], 2)

    def test_failure_is_never_kept_later_creation_reads_fresh(self) -> None:
        """A failed lookup is not an answer: an object created later reads fresh (F20)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            (root / "later.txt").write_text("not yet stored\n", encoding="utf-8")
            blob = subprocess.run(
                ["git", "hash-object", "later.txt"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            with _counted_git_children() as counts, memo_git_object_lookups():
                missing = run_git(root, ["cat-file", "-p", blob])
                self.assertNotEqual(missing.returncode, 0)
                subprocess.run(["git", "hash-object", "-w", "later.txt"], cwd=root, check=True)
                found = run_git(root, ["cat-file", "-p", blob])
            self.assertEqual(found.returncode, 0)
            self.assertEqual(found.stdout, "not yet stored\n")
            # The miss, the writer and the fresh hit: three children, no stale failure.
            self.assertEqual(counts[0], 3)

    def test_supported_workdir_runs_separate(self) -> None:
        """The key is the exact effective execution: another work dir runs again (F20)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            head = _init_repo(root)
            nested = root / "sub"
            nested.mkdir()
            argv = ["rev-parse", "--verify", "--quiet", f"{head}^{{tree}}"]
            with _counted_git_children() as counts, memo_git_object_lookups():
                first = run_git(root, argv)
                second = run_git(root, argv, GitRunnerOptions(work_dir=nested))
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(counts[0], 2)

    def test_isolated_store_runs_separate(self) -> None:
        """Another isolated index never shares an answer, even for the same object (F20)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            head = _init_repo(root)
            argv = ["rev-parse", "--verify", "--quiet", f"{head}^{{tree}}"]
            with _counted_git_children() as counts, memo_git_object_lookups():
                first = run_git(root, argv)
                second = run_git_with_index(root, argv, root / "detached.index")
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(counts[0], 2)

    def test_unlisted_flag_runs_fresh(self) -> None:
        """A flag outside the verb's pure grammar is a different question (F20)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            head = _init_repo(root)
            with _counted_git_children() as counts, memo_git_object_lookups():
                run_git(root, ["rev-list", head])
                run_git(root, ["rev-list", head])
                run_git(root, ["rev-list", "--count", head])
                run_git(root, ["rev-list", "--count", head])
            # Graph walks honor replace refs and ref movement: every run is fresh.
            self.assertEqual(counts[0], 4)

    def test_repeated_existence_probe_of_one_tree_starts_one_child(self) -> None:
        """Guard probes of the same tree collapse within one request scope (F17).

        Bare-SHA probes collapse; the same probe through a peel suffix honors
        replace refs, so it always runs fresh (F20).
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            head = _init_repo(root)
            tree = run_git(root, ["rev-parse", f"{head}^{{tree}}"]).stdout.strip()
            with _counted_git_children() as counts, memo_git_object_lookups():
                for _ in range(4):
                    probe = run_git(root, ["cat-file", "-e", tree])
                    self.assertEqual(probe.returncode, 0)
                for _ in range(2):
                    peeled = run_git(root, ["cat-file", "-e", f"{head}^{{tree}}"])
                    self.assertEqual(peeled.returncode, 0)
            # The setup peel runs outside the counted scope: one child for the
            # bare quadruplet, two for the fresh peels.
            self.assertEqual(counts[0], 3)

    def test_repeated_index_location_of_one_execution_starts_one_child(self) -> None:
        """The worktree's own index location is execution-pure: asked once (F17)."""
        argv = ["rev-parse", "--path-format=absolute", "--git-path", "index", "--show-toplevel"]
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            with _counted_git_children() as counts, memo_git_object_lookups():
                first = run_git(root, argv)
                second = run_git(root, argv)
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(counts[0], 1)

    def test_replace_proofed_peel_collapses_and_ignores_replace_refs(self) -> None:
        """A flagged peel names the true object: one child, replace-proof bytes (F17+F20)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            head = _init_repo(root)
            tree = run_git(root, ["rev-parse", f"{head}^{{tree}}"]).stdout.strip()
            flagged = [
                "--no-replace-objects",
                "rev-parse",
                "--verify",
                "--quiet",
                f"{head}^{{tree}}",
            ]
            with _counted_git_children() as counts, memo_git_object_lookups():
                first = run_git(root, flagged)
                second = run_git(root, flagged)
                self.assertEqual(first.stdout.strip(), tree)
                self.assertEqual(second.stdout.strip(), tree)
            self.assertEqual(counts[0], 1)
            (root / "a.txt").write_text("hello again\n", encoding="utf-8")
            run_git(root, ["add", "-A"])
            assert run_git(root, ["commit", "-qm", "two"]).returncode == 0
            other = run_git(root, ["rev-parse", "HEAD"]).stdout.strip()
            self.assertNotEqual(other, head)
            with memo_git_object_lookups():
                assert run_git(root, ["replace", head, other]).returncode == 0
                try:
                    moved = run_git(root, ["rev-parse", "--verify", "--quiet", f"{head}^{{tree}}"])
                    proofed = run_git(root, flagged)
                    # The unflagged peel follows the replace; the flagged one
                    # keeps naming the true object -- the bytes the memo keeps.
                    self.assertNotEqual(moved.stdout.strip(), tree)
                    self.assertEqual(proofed.stdout.strip(), tree)
                finally:
                    run_git(root, ["replace", "-d", head])


class NativeMemoResultTests(unittest.TestCase):
    def test_exact_object_grammar_rejects_suffixes_for_all_supported_forms(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            head = _init_repo(root)
            original = git_command._memo_key
            eligibility = []

            def inspect(repo, args, execution, *, raw_output):
                result = original(repo, args, execution, raw_output=raw_output)
                eligibility.append(result is not None)
                return result

            with mock.patch.object(git_command, "_memo_key", inspect), memo_git_object_lookups():
                for size in (40, 64):
                    oid = head if size == 40 else "a" * 64
                    for suffix in ("", "^{tree}", "^{commit}"):
                        for verb in (["cat-file", "-e"], ["rev-parse", "--verify", "--quiet"]):
                            prefix = ["--no-replace-objects"] if suffix else []
                            exact = [*prefix, *verb, oid + suffix]
                            run_git(root, exact)
                            self.assertTrue(eligibility[-1], exact)
                            for extra in ("\n", "\r\n", " ", "\nother"):
                                malformed = [*prefix, *verb, oid + suffix + extra]
                                result = run_git(root, malformed)
                                self.assertFalse(eligibility[-1], malformed)
                                self.assertNotEqual(result.returncode, 0)

    def test_created_tree_probes_keep_native_argv_empty_stdout_and_foreign_absence(self) -> None:
        with TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            root, foreign = scratch / "repo", scratch / "foreign"
            root.mkdir()
            foreign.mkdir()
            _init_repo(root)
            _init_repo(foreign)
            (root / "unique.txt").write_text("a tree absent from the other repository\n")
            with memo_git_object_lookups():
                tree = worktree_candidate_tree(root, scratch / "capture.index")
                for verb in (["rev-parse", "--verify", "--quiet"], ["cat-file", "-e"]):
                    argv = ["--no-replace-objects", *verb, f"{tree}^{{tree}}"]
                    with _counted_git_children() as counts:
                        first = run_git(root, argv)
                        second = run_git(root, argv)
                        absent = run_git(foreign, argv)
                    self.assertEqual(counts[0], 2)
                    self.assertIs(first, second)
                    self.assertEqual(first.returncode, 0)
                    self.assertEqual(first.args[0], "git")
                    self.assertEqual(first.args[-len(argv) :], argv)
                    self.assertEqual(first.stdout, "" if verb[0] == "cat-file" else tree + "\n")
                    self.assertNotEqual(absent.returncode, 0)

    def test_every_effective_environment_difference_requires_a_native_lookup(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            head = _init_repo(root)
            argv = ["rev-parse", "--verify", "--quiet", head]
            with _counted_git_children() as counts, memo_git_object_lookups():
                first = run_git(root, argv)
                with mock.patch.dict(
                    os.environ,
                    {
                        "GIT_CONFIG_COUNT": "1",
                        "GIT_CONFIG_KEY_0": "core.repositoryformatversion",
                        "GIT_CONFIG_VALUE_0": "0",
                    },
                ):
                    other = run_git(root, argv)
                    self.assertIs(run_git(root, argv), other)
                self.assertIs(run_git(root, argv), first)
            self.assertEqual(counts[0], 2)


class ReplaceFreshnessTests(unittest.TestCase):
    """Replace-ref-dependent reads always run fresh, even inside a memo scope (F20)."""

    def test_cat_file_p_sees_a_replace_ref_placed_after_the_first_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            _init(repo)
            first = _commit(repo, "file.txt", "one\n")
            second = _commit(repo, "file.txt", "two\n")
            with memo_git_object_lookups():
                before = run_git(repo, ["cat-file", "-p", first])
                assert before.returncode == 0
                assert run_git(repo, ["replace", first, second]).returncode == 0
                after = run_git(repo, ["cat-file", "-p", first])
                assert after.returncode == 0
                second_tree = run_git(repo, ["rev-parse", f"{second}^{{tree}}"]).stdout.strip()
                self.assertNotEqual(before.stdout, after.stdout)
                self.assertIn(second_tree, after.stdout)

    def test_replace_dependent_forms_always_run_fresh(self) -> None:
        """Peels, types, dumps, listings, walks, and merges never memoize (F20)."""
        forms = [
            ["cat-file", "-p", "@SHA@"],
            ["cat-file", "commit", "@SHA@"],
            ["cat-file", "-t", "@SHA@"],
            ["cat-file", "-e", "@SHA@^{commit}"],
            ["rev-parse", "@SHA@^{tree}"],
            ["rev-parse", "--verify", "@SHA@^{commit}"],
            ["cat-file", "-e", "@SHA@:file.txt"],
            ["ls-tree", "@SHA@"],
            ["rev-list", "@SHA@"],
            ["merge-base", "@SHA@", "@SHA@"],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            _init(repo)
            commit = _commit(repo, "file.txt", "one\n")
            calls = 0
            real_run = subprocess.run

            def counting(*args: object, **kwargs: object) -> object:
                nonlocal calls
                argv = args[0] if args else []
                if isinstance(argv, list) and argv and argv[0] == "git":
                    calls += 1
                return real_run(*args, **kwargs)  # type: ignore[arg-type]

            with memo_git_object_lookups(), patch.object(subprocess, "run", counting):
                for form in forms:
                    with self.subTest(form=form[0:2]):
                        argv = [part.replace("@SHA@", commit) for part in form]
                        before = calls
                        assert run_git(repo, argv).returncode == 0
                        assert run_git(repo, argv).returncode == 0
                        # Two identical reads start two children: nothing is kept.
                        self.assertEqual(calls - before, 2)

    def test_replace_ref_moves_content_reads_placed_mid_scope(self) -> None:
        """A mid-scope replace moves content/peel/listing answers (F20)."""
        forms = [
            ["cat-file", "commit", "@SHA@"],
            ["rev-parse", "@SHA@^{tree}"],
            ["ls-tree", "@SHA@"],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            _init(repo)
            first = _commit(repo, "file.txt", "one\n")
            second = _commit(repo, "file.txt", "two\n")
            with memo_git_object_lookups():
                for form in forms:
                    with self.subTest(form=form[0:2]):
                        argv = [part.replace("@SHA@", first) for part in form]
                        before = run_git(repo, argv)
                        assert before.returncode == 0
                        assert run_git(repo, ["replace", first, second]).returncode == 0
                        after = run_git(repo, argv)
                        assert after.returncode == 0
                        assert run_git(repo, ["replace", "-d", first]).returncode == 0
                        restored = run_git(repo, argv)
                        assert restored.returncode == 0
                        # The mid-scope replace moved the answer; deleting it
                        # restores the first bytes. A memoized form would serve
                        # `before` for all three reads.
                        self.assertNotEqual(before.stdout, after.stdout)
                        self.assertEqual(before.stdout, restored.stdout)

    def test_content_addressed_probes_still_collapse_repeated_lookups(self) -> None:
        """Bare-SHA existence probes collapse; peel probes always run fresh (F20)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            _init(repo)
            commit = _commit(repo, "file.txt", "one\n")
            calls = 0
            real_run = subprocess.run

            def counting(*args: object, **kwargs: object) -> object:
                nonlocal calls
                argv = args[0] if args else []
                if isinstance(argv, list) and argv and argv[0] == "git":
                    calls += 1
                return real_run(*args, **kwargs)  # type: ignore[arg-type]

            with memo_git_object_lookups(), patch.object(subprocess, "run", counting):
                first = run_git(repo, ["cat-file", "-e", commit])
                second = run_git(repo, ["cat-file", "-e", commit])
                self.assertEqual(first.returncode, 0)
                self.assertEqual(second.returncode, 0)
                self.assertEqual(calls, 1)
                peeled = run_git(repo, ["cat-file", "-e", f"{commit}^{{commit}}"])
                repeated = run_git(repo, ["cat-file", "-e", f"{commit}^{{commit}}"])
                self.assertEqual(peeled.returncode, 0)
                self.assertEqual(repeated.returncode, 0)
                self.assertEqual(calls, 3)
