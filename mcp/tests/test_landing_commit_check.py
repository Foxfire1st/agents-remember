"""A landing passes the repository's commit check (MIK-R152).

Every test runs on throwaway repositories with a small hook of their own: the
product's own repositories are never the proof (MIK-R152 preservation
boundary). One test per rule; the rule is named in each docstring.
"""

from __future__ import annotations

import gc
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import suppress
from pathlib import Path
from unittest import mock

from agents_remember.errors import CertificationContractError
from agents_remember.kernel.git_command import (
    admit_private_git_preparation,
    preparation_command,
    run_git,
    run_git_preparation,
)
from agents_remember.kernel.git_preparation import (
    PrivateGitPreparationBinding,
    PrivateGitPreparationCapability,
)
from agents_remember.memory.baseline import _publish_validated_tree
from agents_remember.models.lifecycles.mutation_evidence import GitMutationSnapshot
from agents_remember.worktrees.direct_landing import _publish_provisional_code_commit
from agents_remember.worktrees.integration.closeout.preparation import (
    private_execution as preparation,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_errors import (
    DirectLandingPublicationRefused,
)
from agents_remember.worktrees.integration.integration_ref_transaction import (
    _require_landing_commit_check,
)
from agents_remember.worktrees.modules import git as git_module
from agents_remember.worktrees.modules import landing_commit_check as commit_check_module
from agents_remember.worktrees.modules.git import publish_tree_commit, stage_tree
from agents_remember.worktrees.modules.landing_commit_check import (
    COMMIT_CHECK_TRAILER_PREFIX,
    LandingCommitCheckRefusal,
    commit_check_fact,
    commit_check_trailer,
    run_landing_commit_check,
)

PASS_HOOK = '#!/bin/sh\necho "[gate] result: unit PASS"\nexit 0\n'
FAIL_HOOK = '#!/bin/sh\necho "[gate] result: ruff FAIL"\necho "[gate] ruff found one unused import" >&2\nexit 1\n'


def _git(repo: Path, *args: str) -> str:
    result = run_git(repo, list(args))
    assert result.returncode == 0, (args, result.stderr)
    return result.stdout.strip()


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "commit-check@example.invalid")
    _git(repo, "config", "user.name", "Commit Check Tests")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")


def _hook(repo: Path, body: str, *, executable: bool = True) -> Path:
    path = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    hook = path / "hooks" / "pre-commit"
    hook.write_text(body, encoding="utf-8")
    hook.chmod(0o755 if executable else 0o644)
    return hook


def _stage(repo: Path, name: str, body: str) -> str:
    (repo / name).write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    return _git(repo, "write-tree")


def _write_tree(repo: Path) -> str:
    return _git(repo, "write-tree")


def _status(repo: Path) -> str:
    return _git(repo, "status", "--porcelain")


def _admitted(repo: Path) -> GitMutationSnapshot:
    return GitMutationSnapshot(
        headRef=_git(repo, "symbolic-ref", "HEAD"),
        head=_git(repo, "rev-parse", "HEAD"),
        headTree=_git(repo, "rev-parse", "HEAD^{tree}"),
        refLogFingerprint="0" * 64,
        indexTree=_write_tree(repo),
        candidateTree=_write_tree(repo),
        statusFingerprint="0" * 64,
    )


class PassingHookTests(unittest.TestCase):
    def test_a_passing_hook_passes_naming_hook_log_and_time(self) -> None:
        """MIK-R152 rule 2: the hook Git would run judges the staged tree."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            _hook(repo, PASS_HOOK)
            tree = _stage(repo, "new.txt", "new\n")
            passed = run_landing_commit_check(repo, tree=tree)
            self.assertEqual(passed.tree, tree)
            self.assertTrue(passed.hook.endswith("hooks/pre-commit"), passed.hook)
            self.assertIn("[gate] result: unit PASS", passed.log.read_text(encoding="utf-8"))
            self.assertGreaterEqual(passed.seconds, 0.0)
            self.assertEqual(_write_tree(repo), tree)
            self.assertEqual(passed.hook, ".git/hooks/pre-commit")

    def test_each_repository_runs_its_own_hook(self) -> None:
        """MIK-R152 rule 5: code and memory repositories each run their own hook."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, memory = root / "code", root / "memory"
            _init(code)
            _init(memory)
            _hook(code, PASS_HOOK)
            _hook(memory, FAIL_HOOK)
            code_tree = _stage(code, "new.txt", "new\n")
            memory_tree = _stage(memory, "new.txt", "new\n")
            passed = run_landing_commit_check(code, tree=code_tree)
            self.assertTrue(passed.hook.endswith("hooks/pre-commit"), passed.hook)
            with self.assertRaises(LandingCommitCheckRefusal) as refused:
                run_landing_commit_check(memory, tree=memory_tree)
            self.assertEqual(refused.exception.reason, "failed")
            self.assertEqual(refused.exception.step, "ruff")

    def test_no_hook_passes_saying_so(self) -> None:
        """MIK-R152 rule 5: a repository without a hook lands as before."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            tree = _stage(repo, "new.txt", "new\n")
            passed = run_landing_commit_check(repo, tree=tree)
            self.assertEqual(passed.hook, "none")
            self.assertIn("no pre-commit hook", passed.log.read_text(encoding="utf-8"))


class FailingHookTests(unittest.TestCase):
    def test_a_failing_hook_refuses_the_publication_keeping_state(self) -> None:
        """MIK-R152 rules 1+4: every publishing route runs the check; a failure
        refuses with one line, the failing step and a log, moving no ref."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            _hook(repo, FAIL_HOOK)
            tree = _stage(repo, "new.txt", "new\n")
            head = _git(repo, "rev-parse", "HEAD")
            before_index = _write_tree(repo)
            before_status = _status(repo)
            with self.assertRaises(LandingCommitCheckRefusal) as refused:
                publish_tree_commit(repo, "landing", tree=tree, before=_admitted(repo))
            error = refused.exception
            self.assertEqual(error.reason, "failed")
            self.assertEqual(error.step, "ruff")
            text = str(error)
            self.assertIn("commit check failed", text)
            self.assertIn("ruff", text)
            assert error.log is not None
            self.assertIn(error.log.as_posix(), text)
            self.assertIn("same call again", text)
            self.assertEqual(_git(repo, "rev-parse", "HEAD"), head)
            # The index comes back as the call found it: staged, with the file.
            self.assertEqual(_write_tree(repo), before_index)
            self.assertEqual(_status(repo), before_status)
            self.assertIn("A  new.txt", before_status)

    def test_a_hook_that_rewrites_a_tracked_file_refuses_naming_it(self) -> None:
        """MIK-R152 rule 3: the tree the hook judged is the tree of the commit."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            _hook(repo, "#!/bin/sh\necho rewritten >> new.txt\nexit 0\n")
            before_index = _write_tree(repo)
            tree = _stage(repo, "new.txt", "new\n")
            with self.assertRaises(LandingCommitCheckRefusal) as refused:
                run_landing_commit_check(
                    repo, tree=tree, restore_index=lambda: stage_tree(repo, before_index)
                )
            self.assertEqual(refused.exception.reason, "changed")
            self.assertIn("new.txt", list(refused.exception.changed_paths))
            self.assertEqual(_write_tree(repo), before_index)

    def test_a_hook_that_stages_a_change_refuses_and_gives_the_index_back(self) -> None:
        """MIK-R152 rules 3+4: an index the hook's run changed refuses; the
        caller's index comes back as found."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            _hook(repo, "#!/bin/sh\necho staged >> new.txt\ngit add new.txt\nexit 0\n")
            before_index = _write_tree(repo)
            tree = _stage(repo, "new.txt", "new\n")
            with self.assertRaises(LandingCommitCheckRefusal) as refused:
                run_landing_commit_check(
                    repo, tree=tree, restore_index=lambda: stage_tree(repo, before_index)
                )
            self.assertEqual(refused.exception.reason, "changed")
            self.assertEqual(_write_tree(repo), before_index)


class UnrunnableHookTests(unittest.TestCase):
    def test_a_non_executable_hook_file_refuses(self) -> None:
        """MIK-R152 rule 6: a hook file Git would not run is a refusal, never a pass."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            _hook(repo, PASS_HOOK, executable=False)
            tree = _stage(repo, "new.txt", "new\n")
            with self.assertRaises(LandingCommitCheckRefusal) as refused:
                run_landing_commit_check(repo, tree=tree)
            self.assertEqual(refused.exception.reason, "unrunnable")
            self.assertIn("not executable", refused.exception.step or "")

    def test_a_missing_interpreter_refuses_with_its_reason(self) -> None:
        """MIK-R152 rule 6: a hook that cannot start refuses with its reason."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            _hook(repo, "#!/nonexistent-interpreter-xyz\nexit 0\n")
            tree = _stage(repo, "new.txt", "new\n")
            with self.assertRaises(LandingCommitCheckRefusal) as refused:
                run_landing_commit_check(repo, tree=tree)
            self.assertEqual(refused.exception.reason, "failed")
            assert refused.exception.step is not None
            self.assertIn("nonexistent-interpreter-xyz", refused.exception.step)

    def test_a_hanging_hook_is_stopped_with_its_children(self) -> None:
        """MIK-R152 rule 6: a run past the bound is stopped with its children."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            pidfile = repo / "sleeper.pid"
            _hook(
                repo,
                f"#!/bin/sh\nsleep 60 & echo $! > {pidfile.as_posix()}\nwait\n",
            )
            tree = _stage(repo, "new.txt", "new\n")
            before_index = _write_tree(repo)
            with self.assertRaises(LandingCommitCheckRefusal) as refused:
                # load-independent: this bound must expire against the deliberately hanging hook.
                run_landing_commit_check(repo, tree=tree, timeout_s=2)
            self.assertEqual(refused.exception.reason, "timeout")
            sleeper = int(pidfile.read_text(encoding="utf-8").strip())
            with self.assertRaises(ProcessLookupError):
                os.kill(sleeper, 0)
            self.assertEqual(_write_tree(repo), before_index)

    def test_the_settings_bound_governs_the_run(self) -> None:
        """MIK-R152 rule 6: the bound the settings name is honored; a malformed
        landing block refuses naming the file."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            _hook(repo, "#!/bin/sh\nsleep 60\nexit 0\n")
            system = repo / "system"
            system.mkdir()
            (system / "settings.json").write_text(
                '{"orchestration": {"landing": {"commitCheckTimeoutSeconds": 2}}}',
                encoding="utf-8",
            )
            tree = _stage(repo, "new.txt", "new\n")
            with self.assertRaises(LandingCommitCheckRefusal) as refused:
                run_landing_commit_check(repo, tree=tree)
            self.assertEqual(refused.exception.reason, "timeout")
            (system / "settings.json").write_text(
                '{"orchestration": {"landing": {"bogus": 1}}}', encoding="utf-8"
            )
            with self.assertRaises(LandingCommitCheckRefusal) as malformed:
                run_landing_commit_check(repo, tree=tree)
            self.assertEqual(malformed.exception.reason, "unrunnable")
            self.assertIn("bogus", malformed.exception.step or "")


class StashRecoveryTests(unittest.TestCase):
    def test_an_unrecoverable_isolation_stash_is_named_never_touched(self) -> None:
        """MIK-R152 rule 6: a stash the stopped run left behind and cannot take
        back is named for the operator; an older entry is never touched."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            _hook(
                repo,
                "#!/bin/sh\n"
                "echo mod >> new.txt\n"
                'git stash push --quiet -m "agents-remember fast gate: staged-content isolation"\n'
                "echo other > new.txt\n"
                "git add new.txt\n"
                "exit 0\n",
            )
            before_index = _write_tree(repo)
            tree = _stage(repo, "new.txt", "new\n")
            with self.assertRaises(LandingCommitCheckRefusal) as refused:
                run_landing_commit_check(
                    repo, tree=tree, restore_index=lambda: stage_tree(repo, before_index)
                )
            error = refused.exception
            self.assertEqual(error.reason, "stash-left")
            self.assertTrue(error.step, "the refusal names the leftover stash entry")
            self.assertIn("stash apply --index", str(error))
            self.assertEqual(_write_tree(repo), before_index)
            self.assertNotEqual(_git(repo, "stash", "list"), "")


class TrailerFactTests(unittest.TestCase):
    def test_the_trailer_round_trips_with_last_wins(self) -> None:
        """MIK-R152 rule 8: the pass fact rides the commit; the last one wins."""

        line = commit_check_trailer("abc123", ".githooks/pre-commit")
        self.assertTrue(line.startswith(COMMIT_CHECK_TRAILER_PREFIX))
        message = f"landing\n\n{line}\n{COMMIT_CHECK_TRAILER_PREFIX} tree=def456 hook=none\n"
        self.assertEqual(commit_check_fact(message), ("def456", "none"))
        self.assertIsNone(commit_check_fact("landing\n\nno trailer\n"))

    def test_the_published_commit_carries_the_fact(self) -> None:
        """MIK-R152 rules 1+8: the landing's record names the tree that passed."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            _hook(repo, PASS_HOOK)
            tree = _stage(repo, "new.txt", "new\n")
            commit = publish_tree_commit(repo, "landing", tree=tree, before=_admitted(repo))
            body = _git(repo, "log", "-1", "--format=%B", commit)
            fact = commit_check_fact(body)
            self.assertIsNotNone(fact)
            assert fact is not None
            self.assertEqual(fact[0], tree)


class RouteCoverageTests(unittest.TestCase):
    def test_the_prepared_commit_carries_no_skip(self) -> None:
        """MIK-R152 rules 1+7: the prepared commit runs the hook; no flag, setting
        or policy publishes past it."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "logical"
            private = Path(tmp) / "private"
            root.mkdir()
            private.mkdir()
            for policy in ("ordinary", "strict-code-no-verify"):
                binding = PrivateGitPreparationBinding(
                    logical_root=root,
                    logical_ref="refs/heads/main",
                    expected_logical_commit="0" * 40,
                    common_directory=root / ".git",
                    private_root=private,
                    parent_commit="0" * 40,
                    admitted_tree="0" * 40,
                    message="m",
                    hook_policy=policy,  # type: ignore[arg-type]
                    operation_key="op",
                    generation=1,
                    intent_digest="0" * 64,
                )
                plan = preparation_command(binding, "commit")
                self.assertNotIn("--no-verify", plan.args)

    def test_the_provisional_path_runs_the_hook_before_the_move(self) -> None:
        """MIK-R152 rule 1: the classic direct landing judges the tree before a
        ref names the commit."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            _hook(repo, FAIL_HOOK)
            tree = _stage(repo, "new.txt", "new\n")
            head = _git(repo, "rev-parse", "HEAD")
            provisional = _git(repo, "commit-tree", tree, "-p", head, "-m", "direct")
            before = _admitted(repo)
            with self.assertRaises(DirectLandingPublicationRefused) as refused:
                _publish_provisional_code_commit(repo, before, provisional, tree)
            self.assertIn("commit-check-failed", refused.exception.status)
            self.assertEqual(_git(repo, "rev-parse", "HEAD"), head)

    def test_integration_trusts_the_fact_and_runs_without_one(self) -> None:
        """MIK-R152 rule 8: a commit with the fact moves; one without is checked here."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            marker = repo / "hook-ran.txt"
            _hook(repo, f"#!/bin/sh\ntouch {marker.as_posix()}\nexit 0\n")
            tree = _stage(repo, "new.txt", "new\n")
            with_fact = _git(
                repo,
                "commit-tree",
                tree,
                "-p",
                "HEAD",
                "-m",
                f"landing\n\n{commit_check_trailer(tree, 'hooks/pre-commit')}",
            )
            _require_landing_commit_check(repo, with_fact, side="code")
            self.assertFalse(marker.exists(), "the check must not run where the fact stands")
            without_fact = _git(repo, "commit-tree", tree, "-p", "HEAD", "-m", "landing")
            _require_landing_commit_check(repo, without_fact, side="code")
            self.assertTrue(marker.exists(), "the check runs where no fact exists")

    def test_integration_refuses_a_failing_candidate_without_a_fact(self) -> None:
        """MIK-R152 rule 8: the master line moves only to a tree that passed."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            _hook(repo, FAIL_HOOK)
            tree = _stage(repo, "new.txt", "new\n")
            candidate = _git(repo, "commit-tree", tree, "-p", "HEAD", "-m", "landing")
            with self.assertRaises(LandingCommitCheckRefusal):
                _require_landing_commit_check(repo, candidate, side="code")


class AnswerAndTextTests(unittest.TestCase):
    def test_the_answer_stays_small_with_output_in_the_log(self) -> None:
        """MIK-R152 rule 9: result, failing step and log path; output and time
        live in the log."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            _hook(repo, FAIL_HOOK)
            tree = _stage(repo, "new.txt", "new\n")
            with self.assertRaises(LandingCommitCheckRefusal) as refused:
                run_landing_commit_check(repo, tree=tree)
            error = refused.exception
            text = str(error)
            self.assertLess(len(text), 1000)
            assert error.log is not None
            log_text = error.log.read_text(encoding="utf-8")
            self.assertIn("[gate] result: ruff FAIL", log_text)

    def test_texts_state_the_new_behavior(self) -> None:
        """MIK-R152 rule 10: the texts that stated the old behavior are corrected."""

        package = Path(__file__).resolve().parent.parent / "src" / "agents_remember"
        checkout = Path(__file__).resolve().parent.parent.parent
        canonical = (checkout / "skills" / "c-12-closeout" / "SKILL.md").read_text(encoding="utf-8")
        for skill in (
            canonical,
            (
                package / "package_data" / "runtime" / "skills" / "c-12-closeout" / "SKILL.md"
            ).read_text(encoding="utf-8"),
        ):
            self.assertIn("no landing publishes without", skill)
            self.assertNotIn("uses `--no-verify`", skill)
            self.assertNotIn("runs no `pre-commit`", skill)

        self.assertNotIn("runs no Git commit hook", git_module.publish_tree_commit.__doc__ or "")
        hook_head = (checkout / ".githooks" / "pre-commit").read_text(encoding="utf-8")
        self.assertIn("MIK-R152", hook_head)
        self.assertNotIn("not automatic closeout", hook_head)

    def test_canonical_and_generated_skills_match(self) -> None:
        """MIK-R152 rule 10: the generated copies come from the canonical skill.

        The repository's own generator check passes: every copy of c-12-closeout
        (package data and harness starters) matches the canonical source.
        """

        checkout = Path(__file__).resolve().parent.parent.parent
        result = subprocess.run(
            [sys.executable, "scripts/sync-skills.py", "--check"],
            cwd=checkout,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_no_publishing_route_skips_the_check(self) -> None:
        """MIK-R152 rules 1+7 with evidence 4: no flag, setting or policy in the
        product publishes past the check."""

        package = Path(__file__).resolve().parent.parent / "src" / "agents_remember"
        # Journal-compatibility spellings that never skip the check: the retired
        # policy name stays loadable for intents written before MIK-R152, and the
        # docstrings that say `--no-verify` is gone. Anything else is a way past.
        allowed = {
            "kernel/git_command.py",
            "kernel/git_preparation.py",
            "models/lifecycles/preparation.py",
            "worktrees/modules/git.py",
        }
        offenders: list[str] = []
        for path in sorted(package.rglob("*.py")):
            relative = path.relative_to(package).as_posix()
            if relative in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            if "--no-verify" in text:
                offenders.append(f"{relative}: --no-verify")
            if "strict-code-no-verify" in text:
                offenders.append(f"{relative}: strict-code-no-verify")
        self.assertEqual(offenders, [])

    def test_commit_tree_census_runs_the_check_on_new_trees(self) -> None:
        """MIK-R152 rules 1+7 with evidence 4: the complete commit-tree census.

        Every ``commit-tree`` call that publishes a NEW tree runs the landing
        commit check on that exact tree first (ordinary, direct, baseline
        adoption). The history migration rebuilds byte-identical trees but
        publishes NEW commit objects onto operator-named (possibly covered)
        refs, so it judges every distinct rewritten tree through an injected
        check runner before any ref moves — and refuses with no runner bound.
        Porcelain commits (prepared closeout, sync's native merges,
        commit_if_dirty) run the repository's hooks natively through Git
        itself — the no-``--no-verify`` scan pins that half.
        """

        package = Path(__file__).resolve().parent.parent / "src" / "agents_remember"
        sites = {}
        for path in sorted(package.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if '"commit-tree"' in text:
                sites[path.relative_to(package).as_posix()] = text
        self.assertEqual(
            sorted(sites),
            [
                "kernel/memory_backfill.py",
                "memory/baseline.py",
                "worktrees/direct_landing.py",
                "worktrees/modules/git.py",
            ],
        )
        for relative in (
            "worktrees/direct_landing.py",
            "worktrees/modules/git.py",
        ):
            self.assertIn("run_landing_commit_check", sites[relative])
        self.assertIn("check_published_tree", sites["memory/baseline.py"])
        backfill = sites["kernel/memory_backfill.py"]
        self.assertIn("_require_commit_check_on_rewritten_trees", backfill)
        self.assertIn("commit_check", backfill)
        self.assertNotIn("exclusion", backfill)

    def test_baseline_adoption_runs_the_commit_check(self) -> None:
        """MIK-R152 rules 1+7: baseline adoption refuses on a failing hook, moving no ref."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "memory"
            _init(repo)
            (repo / "adopted.txt").write_text("adopted\n", encoding="utf-8")
            _git(repo, "add", "-A")
            tree = _git(repo, "write-tree")
            head = _git(repo, "rev-parse", "HEAD")
            _hook(repo, FAIL_HOOK)
            with self.assertRaises(RuntimeError, msg="baseline") as refused:
                _publish_validated_tree(repo, tree, "adopt")
            self.assertIn("commit check", str(refused.exception))
            self.assertEqual(_git(repo, "rev-parse", "HEAD"), head)
            _hook(repo, PASS_HOOK)
            published = _publish_validated_tree(repo, tree, "adopt")
            self.assertNotEqual(published, head)
            self.assertEqual(_git(repo, "rev-parse", f"{published}^{{tree}}"), tree)
            self.assertEqual(_git(repo, "rev-parse", "HEAD"), published)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class RoundOneFindingTests(unittest.TestCase):
    """Sealed round-1 findings F01/F02/F08/F10/F12: byte-level guards, group kill,
    unrunnable prepared hooks, elapsed logs and bounded output."""

    def test_no_unclosed_pipe_readers_across_hook_runs(self) -> None:
        """Gate: every hook run closes the pipe reader it owns, on every path.

        The runner owns its stdout pipe on all outcomes (pass, fail, timeout);
        a run that returns without closing it leaks the BufferedReader past
        teardown, where the ordinary warning policy fails the suite. Three
        runs plus forced collection prove it: any unclosed reader surfaces
        through the unraisable hook, with no warning suppressed.
        """

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            _hook(repo, PASS_HOOK)
            tree = _stage(repo, "new.txt", "new\n")
            seen: list = []
            previous = sys.unraisablehook
            sys.unraisablehook = seen.append  # type: ignore[method-assign]
            try:
                for _ in range(3):
                    run_landing_commit_check(repo, tree=tree)
                gc.collect()
                gc.collect()
            finally:
                sys.unraisablehook = previous
            leaks = [
                args
                for args in seen
                if isinstance(args.exc_value, ResourceWarning) and "unclosed" in str(args.exc_value)
            ]
            self.assertEqual(leaks, [])

    def test_escaped_term_ignoring_holder_is_dead_at_timeout_return(self) -> None:
        """F02: a group-escaped pipe holder that ignores TERM is dead at return.

        The hook backgrounds a setsid child that ignores SIGTERM and holds the
        output pipe open. Past the bound the runner stops the group, drains,
        and runs bounded scan-ask-stop-verify rounds; the KILL is verified
        before the return, so the timeout never claims children stopped while
        one lives.
        """

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            pidfile = Path(tmp) / "escaped.pid"
            _hook(
                repo,
                "#!/bin/sh\n"
                f"setsid sh -c 'echo $$ > {pidfile.as_posix()}; trap \"\" TERM; sleep 60' &\n"
                "sleep 5\n",
            )
            tree = _stage(repo, "new.txt", "new\n")
            strays: list[int] = []
            self.addCleanup(self._kill_strays, strays)
            with self.assertRaises(LandingCommitCheckRefusal) as refused:
                # load-independent: the bound must expire against the deliberately escaping hook.
                run_landing_commit_check(repo, tree=tree, timeout_s=1)
            self.assertEqual(refused.exception.reason, "timeout")
            self.assertEqual(refused.exception.escaped_survivors, ())
            self.assertIn("stopped with its children", str(refused.exception))
            holder = int(pidfile.read_text(encoding="utf-8").strip())
            strays.append(holder)
            # load-independent: bounded hang guard for reaping lag — the runner verified the KILL before returning.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    os.kill(holder, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail(f"escaped holder {holder} is alive after the timeout return")

    @staticmethod
    def _kill_strays(strays: list[int]) -> None:
        for pid in strays:
            with suppress(OSError):
                os.kill(pid, signal.SIGKILL)

    def test_tracked_mutation_between_pass_and_commit_refuses(self) -> None:
        """F01: a tracked rewrite after the pass still refuses at publication.

        The reviewer's probe wraps the real hook runner and changes only the
        disposable tracked file after it returns; the real publication moves
        HEAD. The recheck between the pass and the commit compares the live
        worktree bytes, not just the admitted tree, so the publish refuses
        ``changed`` naming base.txt and moves no ref.
        """

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            _hook(repo, PASS_HOOK)
            tree = _stage(repo, "new.txt", "new\n")
            head = _git(repo, "rev-parse", "HEAD")
            real_check = commit_check_module.run_landing_commit_check

            def _pass_then_mutate(*args: object, **kwargs: object) -> object:
                passed = real_check(*args, **kwargs)  # type: ignore[arg-type]
                (repo / "base.txt").write_text("changed after hook pass\n", encoding="utf-8")
                return passed

            with (
                mock.patch.object(
                    commit_check_module, "run_landing_commit_check", _pass_then_mutate
                ),
                self.assertRaises(LandingCommitCheckRefusal) as refused,
            ):
                publish_tree_commit(repo, "landing", tree=tree, before=_admitted(repo))
            self.assertEqual(refused.exception.reason, "changed")
            self.assertIn("base.txt", refused.exception.changed_paths)
            self.assertEqual(_git(repo, "rev-parse", "HEAD"), head)

    def test_already_dirty_rewrite_refuses_naming_the_path(self) -> None:
        """F01: a hook rewrite that keeps the status labels still refuses.

        base.txt is dirty before the run (` M`); the hook rewrites it to other
        bytes (still ` M`). Labels match, blob identities do not: the landing
        refuses `changed` naming base.txt and moves no ref.
        """

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            (repo / "base.txt").write_text("owner bytes\n", encoding="utf-8")
            tree = _write_tree(repo)
            head = _git(repo, "rev-parse", "HEAD")
            _hook(repo, "#!/bin/sh\nprintf 'hook bytes\\n' > base.txt\nexit 0\n")
            with self.assertRaises(LandingCommitCheckRefusal) as refused:
                run_landing_commit_check(repo, tree=tree)
            error = refused.exception
            self.assertEqual(error.reason, "changed")
            self.assertIn("base.txt", list(error.changed_paths))
            self.assertEqual(_git(repo, "rev-parse", "HEAD"), head)

    def test_term_ignoring_descendant_does_not_complete_work(self) -> None:
        """F02: the bound stops a TERM-ignoring descendant; it completes nothing."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            tree = _stage(repo, "new.txt", "new\n")
            marker = Path(tmp) / "survived.txt"
            pidfile = Path(tmp) / "child.pid"
            _hook(
                repo,
                f"#!/bin/sh\n(trap '' TERM; echo $$ > {pidfile}; sleep 40; echo done > {marker}) &\nsleep 30\n",
            )
            with self.assertRaises(LandingCommitCheckRefusal) as refused:
                # load-independent: the bound must expire against the deliberately TERM-ignoring hook.
                run_landing_commit_check(repo, tree=tree, timeout_s=2)
            self.assertEqual(refused.exception.reason, "timeout")
            child = int(pidfile.read_text(encoding="utf-8").strip())
            self.assertFalse(_pid_alive(child), child)
            self.assertFalse(marker.exists())

    def test_descendant_holding_the_pipe_is_stopped_and_named(self) -> None:
        """F02: a descendant that escapes the process group, ignores TERM and
        holds stdout open is stopped by PID; it completes nothing."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            tree = _stage(repo, "new.txt", "new\n")
            marker = Path(tmp) / "pipe-survived.txt"
            pidfile = Path(tmp) / "escaped.pid"
            _hook(
                repo,
                "#!/bin/sh\n"
                f"(setsid sh -c 'trap \"\" TERM; echo $$ > {pidfile}; sleep 60; echo done > {marker}') &\n"
                "sleep 30\n",
            )
            started = time.monotonic()
            with self.assertRaises(LandingCommitCheckRefusal) as refused:
                # load-independent: the bound must expire against the deliberately escaping hook.
                run_landing_commit_check(repo, tree=tree, timeout_s=2)
            elapsed = time.monotonic() - started
            self.assertEqual(refused.exception.reason, "timeout")
            # The escaped 60s sleeper holding the pipe did not delay the
            # answer past the bound plus the grace windows: it was stopped.
            self.assertLess(elapsed, 2 + 30 + 10 + 10 + 25)
            escaped = int(pidfile.read_text(encoding="utf-8").strip())
            self.assertFalse(_pid_alive(escaped), escaped)
            assert refused.exception.log is not None
            log = refused.exception.log.read_text(encoding="utf-8", errors="replace")
            self.assertIn(f"pid={escaped}", log)
            self.assertFalse(marker.exists())

    def test_hook_output_sizes_stream_whole_with_elapsed_footer(self) -> None:
        """F12: empty, chunk-boundary and multi-chunk outputs all land whole."""

        with tempfile.TemporaryDirectory() as tmp:
            for size in (0, 65537, 200000):
                with self.subTest(size=size):
                    repo = Path(tmp) / f"code-{size}"
                    _init(repo)
                    tree = _stage(repo, "new.txt", "new\n")
                    if size:
                        _hook(
                            repo,
                            "#!/bin/sh\npython3 -c "
                            f"\"import sys; sys.stdout.write('y' * {size})\"\nexit 0\n",
                        )
                    else:
                        _hook(repo, "#!/bin/sh\nexit 0\n")
                    passed = run_landing_commit_check(repo, tree=tree, timeout_s=120)
                    body = passed.log.read_bytes()
                    middle = body.split(b"\n", 1)[1]
                    middle = re.sub(rb"elapsed: \d+\.\d+s exit=0\n", b"", middle)
                    middle = re.sub(rb"elapsed: \d+\.\d+s pass\n$", b"", middle)
                    self.assertEqual(middle, b"y" * size)
                    tail = body[-200:].decode("utf-8", "replace")
                    self.assertRegex(tail, r"elapsed: \d+\.\d+s")

    def test_large_hook_output_lands_whole_with_elapsed_footer(self) -> None:
        """F10/F12: megabytes of hook output stream to the log whole, with time."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            tree = _stage(repo, "new.txt", "new\n")
            _hook(
                repo,
                "#!/bin/sh\npython3 -c \"import sys; sys.stdout.write('x' * 5000000)\"\nexit 0\n",
            )
            passed = run_landing_commit_check(repo, tree=tree, timeout_s=120)
            body = passed.log.read_bytes()
            middle = body.split(b"\n", 1)[1]
            middle = re.sub(rb"elapsed: \d+\.\d+s exit=0\n", b"", middle)
            middle = re.sub(rb"elapsed: \d+\.\d+s pass\n$", b"", middle)
            self.assertEqual(middle, b"x" * 5000000)
            tail = body[-200:].decode("utf-8", "replace")
            self.assertRegex(tail, r"elapsed: \d+\.\d+s")

    def test_hook_logs_are_reclaimed_to_newest_twenty(self) -> None:
        """F12: per-repo hook logs do not accumulate without bound."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            tree = _stage(repo, "new.txt", "new\n")
            common = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
            for index in range(25):
                (common / f"agents-remember-commit-check-test{index:02}.log").write_text(
                    "old\n", encoding="utf-8"
                )
            run_landing_commit_check(repo, tree=tree)
            remaining = sorted(common.glob("agents-remember-commit-check-*.log"))
            self.assertLessEqual(len(remaining), 21)

    def test_stdin_reading_hook_gets_eof_not_a_terminal(self) -> None:
        """F12: a hook reading stdin terminates on EOF instead of hanging."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "code"
            _init(repo)
            tree = _stage(repo, "new.txt", "new\n")
            _hook(repo, "#!/bin/sh\nread line || echo got-eof\nexit 0\n")
            passed = run_landing_commit_check(repo, tree=tree, timeout_s=30)
            self.assertIn("got-eof", passed.log.read_text(encoding="utf-8"))

    def test_prepared_failures_keep_whole_output_with_time(self) -> None:
        """F10: a prepared failure persists its whole output and elapsed time, not hashes."""

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "hook.log"
            result = subprocess.CompletedProcess(
                args=["git", "commit"],
                returncode=1,
                stdout="[gate] result: unit FAIL\nruff found one unused import\n",
                stderr="",
            )
            pointer = preparation._write_preparation_hook_log(log, result, 1.5)
            body = log.read_text(encoding="utf-8")
            self.assertIn("[gate] result: unit FAIL", body)
            self.assertIn("ruff found one unused import", body)
            self.assertIn("elapsed: 1.500s", body)
            self.assertIn("failing-step: unit", body)
            self.assertIn("failing-step=unit", pointer)
            self.assertIn("elapsed=1.500s", pointer)
            self.assertIn(log.as_posix(), pointer)


class PreparedLegProductionTests(unittest.TestCase):
    """F08/F10: both prepared legs answer through the production commit path.

    Each leg is a real admitted preparation (create + materialize) on its own
    repository. The tests drive the exact per-leg sequence the closeout uses
    for a commit (unrunnable gate, snapshot, real run, log write, changed
    check, refusal) and assert the response code, the written log with its
    pointer, and the unmoved ref.
    """

    def _admit(self, root: Path, name: str) -> tuple[Path, PrivateGitPreparationCapability]:
        repo = root / name
        _init(repo)
        (repo / "source.txt").write_text("candidate\n", encoding="utf-8")
        _git(repo, "add", "source.txt")
        tree = _git(repo, "write-tree")
        parent = _git(repo, "rev-parse", "HEAD")
        common = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        binding = PrivateGitPreparationBinding(
            repo,
            "refs/heads/main",
            parent,
            common,
            root / f"{name}-output",
            parent,
            tree,
            "prepared output",
            "ordinary",
            "operation-1",
            1,
            "a" * 64,
        )
        capability = admit_private_git_preparation(binding, authorize=lambda _binding: None)
        run_git_preparation(capability, "create")
        run_git_preparation(capability, "materialize")
        return repo, capability

    def _hooks_dir(self, capability: PrivateGitPreparationCapability) -> Path:
        private_root = capability.binding.private_root
        common = run_git(
            private_root, ["rev-parse", "--path-format=absolute", "--git-common-dir"]
        ).stdout.strip()
        hooks = Path(common) / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        return hooks

    def _pointer_log(self, pointer: str) -> Path:
        match = re.search(r"log=(\S+)", pointer)
        assert match is not None, pointer
        return Path(match.group(1))

    def test_both_legs_refuse_an_unrunnable_hook_with_log_and_pointer(self) -> None:
        """F08: both prepared legs refuse a nonexecutable hook with a log, moving no ref.

        Drives the exact commit function the closeout calls for every prepared
        leg, on a real admitted code preparation and a real admitted memory
        preparation.
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for leg in ("code", "memory"):
                with self.subTest(leg=leg):
                    repo, capability = self._admit(root, leg)
                    hooks = self._hooks_dir(capability)
                    hook = hooks / "pre-commit"
                    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    hook.chmod(0o644)
                    private_root = capability.binding.private_root
                    logical_head = run_git(repo, ["rev-parse", "HEAD"]).stdout.strip()
                    head = run_git(private_root, ["rev-parse", "HEAD"]).stdout.strip()
                    with self.assertRaises(CertificationContractError) as refused:
                        preparation._commit_prepared_leg(capability)
                    findings = refused.exception.findings
                    self.assertEqual(findings[0]["code"], "private-preparation-hook-unrunnable")
                    observed = str(findings[0]["observed"])
                    self.assertIn("not executable", observed)
                    log = self._pointer_log(observed)
                    body = log.read_text(encoding="utf-8")
                    self.assertIn("not executable", body)
                    self.assertRegex(body, r"elapsed: \d+\.\d+s")
                    self.assertEqual(
                        run_git(private_root, ["rev-parse", "HEAD"]).stdout.strip(), head
                    )
                    self.assertEqual(
                        run_git(repo, ["rev-parse", "HEAD"]).stdout.strip(), logical_head
                    )

    def test_timeout_persists_partial_output_with_pointer(self) -> None:
        """F10: a prepared leg's timeout keeps partial output, time and pointer.

        Drives the exact commit function the closeout calls, with a hanging
        hook and a one-second settings bound: the partial-output log is
        written, the timeout carries its pointer, and nothing is published.
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, capability = self._admit(root, "code")
            system = repo / "system"
            system.mkdir(exist_ok=True)
            (system / "settings.json").write_text(
                '{"orchestration": {"landing": {"commitCheckTimeoutSeconds": 1}}}',
                encoding="utf-8",
            )
            hooks = self._hooks_dir(capability)
            (hooks / "pre-commit").write_text(
                "#!/bin/sh\necho hook started\nsleep 30\n", encoding="utf-8"
            )
            (hooks / "pre-commit").chmod(0o755)
            private_root = capability.binding.private_root
            logical_head = run_git(repo, ["rev-parse", "HEAD"]).stdout.strip()
            head = run_git(private_root, ["rev-parse", "HEAD"]).stdout.strip()
            with self.assertRaises(preparation._PreparedLegHookTimeout) as timed_out:
                preparation._commit_prepared_leg(capability)
            pointer = timed_out.exception.hook_pointer
            self.assertIsInstance(timed_out.exception.__cause__, subprocess.TimeoutExpired)
            log = self._pointer_log(pointer)
            self.assertIn("timeout", pointer)
            body = log.read_text(encoding="utf-8")
            self.assertIn("hook started", body)
            self.assertIn("exit=timeout", body)
            self.assertRegex(body, r"elapsed: \d+\.\d+s")
            self.assertIn("nothing was published", str(timed_out.exception))
            self.assertEqual(run_git(private_root, ["rev-parse", "HEAD"]).stdout.strip(), head)
            self.assertEqual(run_git(repo, ["rev-parse", "HEAD"]).stdout.strip(), logical_head)

    def test_failing_hook_commit_persists_the_whole_output_log(self) -> None:
        """F10: a prepared leg's failing commit persists whole output, step and pointer.

        Drives the exact commit function the closeout calls on a real admitted
        memory preparation: the commit runs, the whole hook output with the
        failing step and elapsed time is persisted, and nothing is published.
        (The closeout refuses the returned nonzero result with that step and
        pointer; the three-line refusal raise is pinned by
        test_prepared_failures_keep_whole_output_with_time.)
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, capability = self._admit(root, "memory")
            hooks = self._hooks_dir(capability)
            (hooks / "pre-commit").write_text(
                "#!/bin/sh\n"
                "echo '[gate] result: unit FAIL'\n"
                "echo '[gate] ruff found one unused import' >&2\n"
                "exit 1\n",
                encoding="utf-8",
            )
            (hooks / "pre-commit").chmod(0o755)
            private_root = capability.binding.private_root
            logical_head = run_git(repo, ["rev-parse", "HEAD"]).stdout.strip()
            head = run_git(private_root, ["rev-parse", "HEAD"]).stdout.strip()
            result, pointer = preparation._commit_prepared_leg(capability)
            self.assertEqual(result.returncode, 1)
            self.assertIn("failing-step=unit", pointer)
            log = self._pointer_log(pointer)
            body = log.read_text(encoding="utf-8")
            self.assertIn("[gate] result: unit FAIL", body)
            self.assertIn("ruff found one unused import", body)
            self.assertRegex(body, r"elapsed: \d+\.\d+s")
            self.assertEqual(run_git(private_root, ["rev-parse", "HEAD"]).stdout.strip(), head)
            self.assertEqual(run_git(repo, ["rev-parse", "HEAD"]).stdout.strip(), logical_head)

    def test_passing_hook_commit_publishes_the_admitted_tree(self) -> None:
        """Both legs' commit publishes exactly the admitted tree on a pass."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for leg in ("code", "memory"):
                with self.subTest(leg=leg):
                    repo, capability = self._admit(root, leg)
                    hooks = self._hooks_dir(capability)
                    (hooks / "pre-commit").write_text(
                        "#!/bin/sh\necho '[gate] result: unit PASS'\nexit 0\n", encoding="utf-8"
                    )
                    (hooks / "pre-commit").chmod(0o755)
                    private_root = capability.binding.private_root
                    logical_head = run_git(repo, ["rev-parse", "HEAD"]).stdout.strip()
                    head = run_git(private_root, ["rev-parse", "HEAD"]).stdout.strip()
                    result, pointer = preparation._commit_prepared_leg(capability)
                    self.assertEqual(result.returncode, 0)
                    self.assertNotIn("failing-step=", pointer)
                    published = run_git(private_root, ["rev-parse", "HEAD"]).stdout.strip()
                    self.assertNotEqual(published, head)
                    self.assertEqual(
                        run_git(
                            private_root, ["rev-parse", f"{published}^{{tree}}"]
                        ).stdout.strip(),
                        capability.binding.admitted_tree,
                    )
                    self.assertEqual(
                        run_git(repo, ["rev-parse", "HEAD"]).stdout.strip(), logical_head
                    )

    def test_prepared_legs_refuse_an_unrunnable_hook(self) -> None:
        """F08: both prepared legs refuse a present nonexecutable hook before committing."""

        with tempfile.TemporaryDirectory() as tmp:
            for leg in ("code", "memory"):
                repo = Path(tmp) / leg
                _init(repo)
                head = _git(repo, "rev-parse", "HEAD")
                _hook(repo, "#!/bin/sh\nexit 0\n", executable=False)
                log = Path(tmp) / f"{leg}-hook.log"
                with self.assertRaises(CertificationContractError) as refused:
                    preparation._refuse_unrunnable_preparation_hook(repo, log)
                findings = refused.exception.findings
                self.assertEqual(findings[0]["code"], "private-preparation-hook-unrunnable")
                observed = str(findings[0]["observed"])
                self.assertIn("not executable", observed)
                self.assertIn(f"log={log.as_posix()}", observed)
                body = log.read_text(encoding="utf-8")
                self.assertIn("not executable", body)
                self.assertRegex(body, r"elapsed: \d+\.\d+s")
                self.assertEqual(_git(repo, "rev-parse", "HEAD"), head)
            present = Path(tmp) / "present"
            _init(present)
            _hook(present, "#!/bin/sh\nexit 0\n")
            self.assertIsNone(
                preparation._refuse_unrunnable_preparation_hook(
                    present, Path(tmp) / "present-hook.log"
                )
            )
            absent = Path(tmp) / "absent"
            _init(absent)
            self.assertIsNone(
                preparation._refuse_unrunnable_preparation_hook(
                    absent, Path(tmp) / "absent-hook.log"
                )
            )


def _pid_alive(pid: int) -> bool:
    """Whether the process still exists (a zombie counts as gone for this purpose)."""

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
        return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
