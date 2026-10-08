"""The one git runner: redirection safety, timeouts, stdin, and no second runner.

This module exists because the guard it tests used to live in the wrong place. Six
copies of ``run_git`` had drifted apart -- only the kernel's stripped the repository
selectors -- and the suite could not see it, because ``mcp/tests/conftest.py`` removes
those variables from ``os.environ`` at import. That strip is correct for fixture safety
and stays, but it also meant no test anywhere could observe a call site that failed to
strip them: the mitigation for the production hazard was installed in the only place
that could have detected it.
So every redirection test below re-SETS the selectors inside its own scope, defeating
the conftest strip on purpose. They pass because production strips, not because the
harness did; delete the conftest lines and these still pass, and delete the ``env=``
from the runner and these fail.
"""

from __future__ import annotations

import errno
import itertools
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.git_command import (
    GIT_REPOSITORY_SELECTOR_ENV,
    GitRunnerOptions,
    admit_private_git_preparation,
    inspect_git_preparation,
    preparation_command,
    read_git_blobs_bytes,
    read_git_commit_bytes,
    run_git,
    run_git_preparation,
    run_git_with_index,
    shared_blob_reads,
)
from agents_remember.kernel.git_preparation import (
    GitPreparationError,
    PrivateGitPreparationBinding,
    PrivateGitPreparationCapability,
)
from agents_remember.models.lifecycles.mutation_evidence import GitMutationSnapshot
from agents_remember.worktrees.modules import git as git_owner
from agents_remember.worktrees.modules.git import (
    CommitPublicationRefusal,
    PublicationHooks,
    commit_if_dirty,
    head_commit,
    publish_tree_commit,
    worktree_candidate_tree,
)

PACKAGE_ROOT = MCP_SRC / "agents_remember"

# The spawn entry points of the stdlib's subprocess module. A module reaches one of these
# either through the module object (``subprocess.run``) or through a name it imported from
# it (``from subprocess import run``); the sweep below follows both.
SPAWN_FUNCTIONS = frozenset({"run", "Popen", "check_output", "check_call", "call"})


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "git-command@example.invalid"],
        ["config", "user.name", "Git Command Tests"],
    ):
        assert run_git(repo, args).returncode == 0, args


def _commit(repo: Path, name: str, body: str) -> str:
    (repo / name).write_text(body, encoding="utf-8")
    assert run_git(repo, ["add", "-A"]).returncode == 0
    assert run_git(repo, ["commit", "-m", f"add {name}"]).returncode == 0
    return run_git(repo, ["rev-parse", "HEAD"]).stdout.strip()


def _selectors(decoy: Path) -> dict[str, str]:
    """Every selector pointed at ``decoy``, i.e. the worst case the runner must survive."""
    return {
        "GIT_DIR": str(decoy / ".git"),
        "GIT_WORK_TREE": str(decoy),
        "GIT_INDEX_FILE": str(decoy / ".git" / "index"),
        "GIT_OBJECT_DIRECTORY": str(decoy / ".git" / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(decoy / ".git" / "objects"),
        "GIT_COMMON_DIR": str(decoy / ".git"),
        "GIT_NAMESPACE": "redirected",
        "GIT_PREFIX": "redirected/",
    }


class DecoyRepositoryTests(unittest.TestCase):
    """A decoy repository named by GIT_DIR must never receive the real repo's writes."""

    def test_a_commit_lands_in_the_real_repository_not_the_decoy(self) -> None:
        # The exact defect: `commit_if_dirty` is what closeout runs, and it used to go
        # through a runner with no `env=`. With GIT_DIR exported that commit landed in
        # the decoy -- the real branch never moved and the decoy grew a commit nobody
        # asked for. Both halves are asserted; checking only the real repo would still
        # pass if the write were duplicated into the decoy.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real, decoy = root / "real", root / "decoy"
            _init(real)
            _init(decoy)
            real_before = _commit(real, "real.txt", "one\n")
            decoy_before = _commit(decoy, "decoy.txt", "decoy\n")
            (real / "real.txt").write_text("two\n", encoding="utf-8")

            with patch.dict(os.environ, _selectors(decoy)):
                # The conftest strip is undone inside this block on purpose.
                self.assertTrue(set(GIT_REPOSITORY_SELECTOR_ENV).issubset(os.environ))
                committed = commit_if_dirty(real, "carry the change")

            self.assertNotEqual(committed, real_before)  # the real branch advanced
            self.assertEqual(head_commit(real), committed)
            self.assertEqual(head_commit(decoy), decoy_before)  # the decoy did NOT
            self.assertEqual((real / "real.txt").read_text(encoding="utf-8"), "two\n")
            self.assertFalse((decoy / "real.txt").exists())


class SharedBlobReadTests(unittest.TestCase):
    """Within a shared-read block a blob is read from a repository once (MIK-R42)."""

    def test_a_repeated_read_is_answered_from_the_block_and_never_crosses_repositories(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "first", Path(tmp) / "second"
            for repo in (first, second):
                _init(repo)
            _commit(first, "a.txt", "alpha\n")
            _commit(first, "b.txt", "beta\n")
            alpha, beta = (
                run_git(first, ["rev-parse", f"HEAD:{name}"]).stdout.strip()
                for name in ("a.txt", "b.txt")
            )
            spawned = []
            real = subprocess.run

            def counting(argv: list[str], *args: object, **kwargs: object) -> object:
                spawned.append(argv)
                return real(argv, *args, **kwargs)  # type: ignore[call-overload]

            with patch("agents_remember.kernel.git_command.subprocess.run", counting):
                read_git_blobs_bytes(first, [alpha])
                read_git_blobs_bytes(first, [alpha])
                assert len(spawned) == 2  # outside a block every read goes to Git
                spawned.clear()
                with shared_blob_reads():
                    one = read_git_blobs_bytes(first, [alpha, beta])
                    again = read_git_blobs_bytes(first, [beta, alpha])
                    assert len(spawned) == 1 and one == again
                    assert list(again) == sorted([alpha, beta])
                    read_git_blobs_bytes(first, [alpha])
                    assert len(spawned) == 1
                    # The same blob ID is not the same object in a repository that lacks it.
                    with self.assertRaises(GitPreparationError):
                        read_git_blobs_bytes(second, [alpha])
                spawned.clear()
                read_git_blobs_bytes(first, [alpha])
                assert len(spawned) == 1  # the block's answers did not outlive it


def _admitted(repo: Path) -> GitMutationSnapshot:
    """The snapshot a closeout would have admitted: only the fields publication reads are real."""

    git = lambda *args: run_git(repo, list(args)).stdout.strip()  # noqa: E731
    return GitMutationSnapshot(
        headRef=git("symbolic-ref", "HEAD"),
        head=git("rev-parse", "HEAD"),
        headTree=git("rev-parse", "HEAD^{tree}"),
        refLogFingerprint="0" * 64,
        indexTree=git("write-tree"),
        candidateTree=git("write-tree"),
        statusFingerprint="0" * 64,
    )


class PublishTreeCommitTests(unittest.TestCase):
    """A commit built from a judged tree is published like the porcelain commit it replaced, but
    only on the admitted branch tip."""

    def _repo(self, root: Path, name: str, *, signed: bool) -> Path:
        repo = root / name
        _init(repo)
        if signed:
            gpg = root / "fake-gpg.sh"
            gpg.write_text(
                "#!/bin/sh\ncat >/dev/null\n"
                'echo "[GNUPG:] SIG_CREATED D 1 8 00 0 0 0 0 0" >&2\n'
                'printf -- "-----BEGIN PGP SIGNATURE-----\\nfake\\n-----END PGP SIGNATURE-----\\n"\n',
                encoding="utf-8",
            )
            gpg.chmod(0o755)
            run_git(repo, ["config", "gpg.program", str(gpg)])
            run_git(repo, ["config", "commit.gpgsign", "true"])
        _commit(repo, "base.txt", "base\n")
        return repo

    def _stage(self, repo: Path) -> str:
        (repo / "new.txt").write_text("new\n", encoding="utf-8")
        run_git(repo, ["add", "-A"])
        return run_git(repo, ["write-tree"]).stdout.strip()

    def test_identity_message_and_signing_match_the_porcelain_commit(self) -> None:
        message = "Subject line\n\nBody paragraph.\n\nCode-Commit: 1111111111111111111111111111111111111111"
        for signed in (False, True):
            with self.subTest(signed=signed), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                published, porcelain = (
                    self._repo(root, "published", signed=signed),
                    self._repo(root, "porcelain", signed=signed),
                )
                tree = self._stage(published)
                before = _admitted(published)
                commit = publish_tree_commit(published, message, tree=tree, before=before)
                (porcelain / "new.txt").write_text("new\n", encoding="utf-8")
                run_git(porcelain, ["add", "-A"])
                run_git(porcelain, ["commit", "--no-verify", "-m", message])
                fields = ["log", "-1", "--format=%an|%ae|%cn|%ce|%B"]
                self.assertEqual(
                    run_git(published, fields).stdout, run_git(porcelain, fields).stdout
                )
                for repo, tip in ((published, commit), (porcelain, "HEAD")):
                    raw = run_git(repo, ["cat-file", "-p", tip]).stdout
                    self.assertEqual("gpgsig" in raw, signed, raw)
                self.assertEqual(head_commit(published), commit)
                self.assertEqual(
                    run_git(published, ["rev-parse", f"{commit}^"]).stdout.strip(), before.head
                )
                self.assertEqual(run_git(published, ["status", "--porcelain"]).stdout, "")

    def test_a_moved_branch_or_a_switched_head_publishes_nothing(self) -> None:
        for case in ("moved", "switched", "detached"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                repo = self._repo(Path(tmp), "repo", signed=False)
                run_git(repo, ["branch", "elsewhere"])
                tree = self._stage(repo)
                before = _admitted(repo)
                if case == "moved":
                    foreign = run_git(
                        repo, ["commit-tree", before.headTree, "-p", "HEAD", "-m", "x"]
                    )
                    run_git(repo, ["update-ref", "HEAD", foreign.stdout.strip()])
                elif case == "switched":
                    run_git(repo, ["symbolic-ref", "HEAD", "refs/heads/elsewhere"])
                else:
                    run_git(repo, ["update-ref", "--no-deref", "HEAD", before.head])
                tip = run_git(repo, ["rev-parse", "HEAD"]).stdout.strip()
                with self.assertRaisesRegex(CommitPublicationRefusal, "nothing was published"):
                    publish_tree_commit(repo, "m", tree=tree, before=before)
                self.assertEqual(run_git(repo, ["rev-parse", "HEAD"]).stdout.strip(), tip)
                self.assertEqual(
                    run_git(repo, ["rev-list", "--all", "--count"]).stdout.strip(),
                    run_git(repo, ["rev-list", "--branches", "--count"]).stdout.strip(),
                )


class PublishTreeCommitGuardTests(unittest.TestCase):
    """What the shared publication refuses, serializes and undoes."""

    def _repo(self, root: Path) -> Path:
        repo = root / "repo"
        _init(repo)
        _commit(repo, "base.txt", "base\n")
        run_git(repo, ["branch", "elsewhere"])
        return repo

    def _tree(self, repo: Path, name: str, body: str) -> str:
        """A tree object holding HEAD's files plus ``name``, built without touching the index."""

        index = repo / ".git" / f"scratch-{name.replace('/', '-')}.index"
        for args in (["read-tree", "HEAD"],):
            run_git_with_index(repo, args, index)
        blob = run_git(repo, ["hash-object", "-w", "--stdin"], GitRunnerOptions(input_text=body))
        run_git_with_index(
            repo,
            ["update-index", "--add", "--cacheinfo", f"100644,{blob.stdout.strip()},{name}"],
            index,
        )
        return run_git_with_index(repo, ["write-tree"], index).stdout.strip()

    def _index(self, repo: Path) -> str:
        return run_git(repo, ["ls-files", "--stage"]).stdout

    def _shape(self, repo: Path) -> tuple[str, ...]:
        """The index with its flags (``ls-files -v``), its entries, and what ``status`` shows."""

        return tuple(
            run_git(repo, args).stdout
            for args in (["ls-files", "-v"], ["ls-files", "--stage"], ["status", "--porcelain"])
        )

    def _hook(self, repo: Path, name: str, body: str) -> None:
        hook = repo / ".git" / "hooks" / name
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
        hook.chmod(0o755)

    def _message(self, repo: Path) -> bytes:
        """The message of ``HEAD`` exactly as the commit object stores it."""

        return read_git_commit_bytes(repo, head_commit(repo)).split(b"\n\n", 1)[1]

    def test_the_branch_move_is_an_expected_old_swap_not_only_a_check(self) -> None:
        """Moved after the tip check and after the commit object exists: only Git's expected-old
        update can refuse it, and it must leave the foreign commit in place."""

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            tree = self._tree(repo, "new.txt", "new\n")
            before = _admitted(repo)
            index = self._index(repo)
            native = git_owner.run_git
            foreign: list[str] = []

            def race(path, args, *extra):
                result = native(path, args, *extra)
                if args[0] == "commit-tree" and not foreign:
                    foreign.append(
                        run_git(
                            repo, ["commit-tree", before.headTree, "-p", "HEAD", "-m", "f"]
                        ).stdout.strip()
                    )
                    run_git(repo, ["update-ref", "HEAD", foreign[0]])
                return result

            with (
                patch.object(git_owner, "run_git", race),
                self.assertRaisesRegex(CommitPublicationRefusal, "moved while this closeout"),
            ):
                publish_tree_commit(repo, "m", tree=tree, before=before)
            self.assertEqual(head_commit(repo), foreign[0])
            self.assertEqual(self._index(repo), index)

    def _switch_head_at_the_move(self, root: Path, *, meanwhile: bool):
        """Publish while ``HEAD`` is switched just before the branch move. With ``meanwhile``
        another writer's commit lands on the branch after that move and before the take-back."""

        repo = self._repo(root)
        tree = self._tree(repo, "new.txt", "new\n")
        before = _admitted(repo)
        native = git_owner.run_git
        foreign: list[str] = []

        def race(path, args, *extra):
            moving = args[0] == "update-ref" and "-m" in args and "HEAD" not in args[:2]
            if moving:
                native(path, ["symbolic-ref", "HEAD", "refs/heads/elsewhere"])
            result = native(path, args, *extra)
            if moving and meanwhile and not foreign:
                made = run_git(repo, ["commit-tree", tree, "-p", before.headRef, "-m", "f"])
                foreign.append(made.stdout.strip())
                run_git(repo, ["update-ref", before.headRef, foreign[0]])
            return result

        with patch.object(git_owner, "run_git", race), self.assertRaises(RuntimeError) as failed:
            publish_tree_commit(repo, "m", tree=tree, before=before)
        return repo, before, foreign, failed.exception

    def test_a_head_switched_between_the_check_and_the_move_is_taken_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, before, _, refusal = self._switch_head_at_the_move(Path(tmp), meanwhile=False)
            self.assertIsInstance(refusal, CommitPublicationRefusal)
            self.assertRegex(str(refusal), "HEAD switched to refs/heads/else")
            main = run_git(repo, ["rev-parse", before.headRef]).stdout.strip()
            self.assertEqual(main, before.head)  # taken back
            self.assertEqual(
                run_git(repo, ["rev-list", "--all", "--count"]).stdout.strip(),
                run_git(repo, ["rev-list", "--branches", "--count"]).stdout.strip(),
            )
        # The take-back is an expected-old move too. A commit another writer put on the branch
        # meanwhile is not overwritten; that is no refusal, because the new commit is published,
        # and the error says that it is still on the branch.
        with tempfile.TemporaryDirectory() as tmp:
            repo, before, foreign, failure = self._switch_head_at_the_move(
                Path(tmp), meanwhile=True
            )
            self.assertNotIsInstance(failure, CommitPublicationRefusal)
            self.assertRegex(
                str(failure), f"(?s)could not be taken back.*is still on {before.headRef}"
            )
            main = run_git(repo, ["rev-parse", before.headRef]).stdout.strip()
            self.assertEqual(main, foreign[0])  # the foreign commit survives
            grandparent = run_git(repo, ["rev-parse", f"{main}^^"]).stdout.strip()
            self.assertEqual(grandparent, before.head)  # with the published commit below it

    def test_two_racing_publishers_exactly_one_wins_and_the_loser_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            trees = [self._tree(repo, f"{name}.txt", f"{name}\n") for name in ("a", "b")]
            before = _admitted(repo)
            gate = threading.Barrier(2)
            outcomes: list[object] = []

            def publish(tree: str) -> None:
                gate.wait()
                try:
                    outcomes.append(publish_tree_commit(repo, "m", tree=tree, before=before))
                except CommitPublicationRefusal as refusal:
                    outcomes.append(refusal)

            threads = [threading.Thread(target=publish, args=(tree,)) for tree in trees]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            won = [one for one in outcomes if isinstance(one, str)]
            lost = [one for one in outcomes if isinstance(one, CommitPublicationRefusal)]
            self.assertEqual((len(won), len(lost)), (1, 1))
            self.assertIn("moved from", str(lost[0]))
            self.assertEqual(head_commit(repo), won[0])
            winning = run_git(repo, ["rev-parse", "HEAD^{tree}"]).stdout.strip()
            self.assertEqual(
                run_git(repo, ["write-tree"]).stdout.strip(), winning
            )  # winner's index
            self.assertEqual(run_git(repo, ["diff", "--cached", "--name-only"]).stdout, "")

    def test_a_failure_while_staging_gives_the_index_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            tree = self._tree(repo, "new.txt", "new\n")
            before = _admitted(repo)
            index = self._index(repo)

            def half_staged(path: Path, staged: str) -> None:
                run_git(path, ["read-tree", staged])
                raise RuntimeError("staging failed")

            with (
                patch.object(git_owner, "stage_tree", half_staged),
                self.assertRaisesRegex(RuntimeError, "staging failed"),
            ):
                publish_tree_commit(repo, "m", tree=tree, before=before)
            self.assertEqual(self._index(repo), index)
            self.assertEqual(head_commit(repo), before.head)

    def test_an_empty_message_and_an_unfinished_action_publish_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            tree = self._tree(repo, "new.txt", "new\n")
            before = _admitted(repo)
            index = self._index(repo)
            with self.assertRaisesRegex(CommitPublicationRefusal, "message is empty"):
                publish_tree_commit(repo, " \n\t\n", tree=tree, before=before)
            git_dir = Path(run_git(repo, ["rev-parse", "--absolute-git-dir"]).stdout.strip())
            for marker, action in (
                ("MERGE_HEAD", "a merge"),
                ("CHERRY_PICK_HEAD", "a cherry-pick"),
                ("REVERT_HEAD", "a revert"),
                ("rebase-merge", "a rebase"),
                ("rebase-apply", "a rebase"),
            ):
                path = git_dir / marker
                if marker.startswith("rebase"):
                    path.mkdir()
                else:
                    path.write_text(before.head + "\n", encoding="utf-8")
                with self.assertRaisesRegex(CommitPublicationRefusal, f"{marker}.*{action}"):
                    publish_tree_commit(repo, "m", tree=tree, before=before)
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()
            self.assertEqual(self._index(repo), index)
            self.assertEqual(head_commit(repo), before.head)
            # A real merge stopped on a conflict leaves unmerged index entries, which
            # ``git write-tree`` cannot write: the designed refusal has to come before that read.
            run_git(repo, ["checkout", "-q", "elsewhere"])
            _commit(repo, "base.txt", "theirs\n")
            run_git(repo, ["checkout", "-q", "main"])
            _commit(repo, "base.txt", "ours\n")
            before = _admitted(repo)
            self.assertNotEqual(run_git(repo, ["merge", "elsewhere"]).returncode, 0)
            conflict = run_git(repo, ["ls-files", "--unmerged"]).stdout
            self.assertEqual(len(conflict.splitlines()), 3, conflict)
            with self.assertRaisesRegex(CommitPublicationRefusal, "MERGE_HEAD.*a merge"):
                publish_tree_commit(repo, "m", tree=tree, before=before)
            self.assertEqual(run_git(repo, ["ls-files", "--unmerged"]).stdout, conflict)
            self.assertEqual(head_commit(repo), before.head)
            self.assertTrue((git_dir / "MERGE_HEAD").is_file())

    def test_the_message_is_what_git_commit_m_stores_and_a_refusal_where_it_aborts(self) -> None:
        """Every ``commit.cleanup`` mode with every comment setting, and ``commit.verbose``,
        against a real ``git commit -m`` in the same repository. The first message is published
        through the primitive; the others are compared as the primitive cleans them, and each one
        the porcelain commit aborts on is refused by the primitive with nothing changed."""

        scissors = "------------------------ >8 ------------------------"
        messages = (
            "  Subject  \n\n\n# a hash line\nBody line   \n; a semicolon line\n\n"
            f"Code-Commit: {'1' * 40}\n\n\n# {scissors}\nbelow the scissors",
            "# only a hash line\n",
            "; only a semicolon line\n",
            "Subject\nbody\n#a trailing hash line\n",
            "Subject\n" + "".join(f"{one} line\n" for one in "#;@!$%^&|:"),
            " \n\t\n",
            "Signed-off-by: A <a@example.invalid>\n",
        )
        cleanups = (None, "default", "whitespace", "strip", "verbatim", "scissors", "invalid")
        settings = [
            {"commit.cleanup": cleanup, "core.commentChar": comment}
            for cleanup, comment in itertools.product(cleanups, (None, ";", "auto"))
        ]
        settings += [{"commit.verbose": "true", "commit.cleanup": mode} for mode in (None, "strip")]
        stored, refused = 0, 0
        for setting in settings:
            with self.subTest(**setting), tempfile.TemporaryDirectory() as tmp:
                repo = self._repo(Path(tmp))
                for key, value in setting.items():
                    if value is not None:
                        run_git(repo, ["config", key, value])
                tree = self._tree(repo, "new.txt", "new\n")
                for message in messages:
                    porcelain = run_git(
                        repo, ["commit", "--allow-empty", "--no-verify", "-m", message]
                    )
                    state = head_commit(repo), self._index(repo)
                    if porcelain.returncode != 0:
                        refused += 1
                        with self.assertRaisesRegex(
                            CommitPublicationRefusal,
                            "Invalid cleanup mode invalid"
                            if setting["commit.cleanup"] == "invalid"
                            else "git commit refuses it too",
                        ):
                            publish_tree_commit(repo, message, tree=tree, before=_admitted(repo))
                        self.assertEqual((head_commit(repo), self._index(repo)), state)
                        continue
                    stored += 1
                    expected = self._message(repo)
                    cleaned = git_owner._cleaned_message(repo, message)
                    self.assertEqual(cleaned.encode(), expected, message)
                    if message is messages[0]:
                        publish_tree_commit(repo, message, tree=tree, before=_admitted(repo))
                        self.assertNotEqual(head_commit(repo), state[0])
                        self.assertEqual(self._message(repo), expected)
        self.assertGreater(stored, 60)
        self.assertGreater(refused, 40)

    def test_only_a_lock_that_would_block_is_waited_for(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            tree = self._tree(repo, "new.txt", "new\n")
            before = _admitted(repo)
            index = self._index(repo)
            for error, said, waited in (
                (OSError(errno.ENOLCK, "No locks available"), "No locks available", False),
                (BlockingIOError(errno.EWOULDBLOCK, "held"), "another publication", True),
            ):
                attempts: list[int] = []

                def flock(_handle, _operation, error=error, attempts=attempts):
                    attempts.append(1)
                    raise error

                locks = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8, flock=flock)
                with (
                    self.subTest(error=error),
                    patch.object(git_owner, "_fcntl", locks),
                    patch.object(git_owner, "_PUBLICATION_LOCK_SECONDS", 0.2),
                    self.assertRaisesRegex(CommitPublicationRefusal, said) as refused,
                ):
                    publish_tree_commit(repo, "m", tree=tree, before=before)
                self.assertIn("nothing was published", str(refused.exception))
                self.assertEqual("ar-publication.lock" in str(refused.exception), not waited)
                self.assertEqual(len(attempts) > 1, waited, attempts)
                self.assertEqual((head_commit(repo), self._index(repo)), (before.head, index))

    def test_no_commit_hook_runs_and_a_refusing_ref_transaction_hook_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            ran = repo / ".git" / "hooks-that-ran"
            for name in ("pre-commit", "commit-msg", "prepare-commit-msg", "post-commit"):
                self._hook(repo, name, f"echo {name} >> '{ran}'\nexit 1\n")
            tree = self._tree(repo, "new.txt", "new\n")
            commit = publish_tree_commit(repo, "m", tree=tree, before=_admitted(repo))
            self.assertEqual(head_commit(repo), commit)
            self.assertFalse(ran.exists(), "a publication ran a commit hook")
            # The hooks are armed: the porcelain commit runs the first one, and it refuses.
            self.assertNotEqual(run_git(repo, ["commit", "--allow-empty", "-m", "p"]).returncode, 0)
            self.assertEqual(ran.read_text(encoding="utf-8").split(), ["pre-commit"])
            # The ref move is a Git ref transaction, so this hook runs. When it refuses, the branch
            # has not moved, and the refusal carries the hook's text instead of "the branch moved".
            self._hook(
                repo,
                "reference-transaction",
                'cat >/dev/null\nif [ "$1" = prepared ]; then echo "policy: frozen" >&2; exit 1; fi\n',
            )
            before = _admitted(repo)
            index = self._index(repo)
            later = self._tree(repo, "later.txt", "later\n")
            with self.assertRaisesRegex(CommitPublicationRefusal, "policy: frozen") as refused:
                publish_tree_commit(repo, "m", tree=later, before=before)
            self.assertIn("nothing was published", str(refused.exception))
            self.assertNotIn("moved", str(refused.exception))
            self.assertEqual((head_commit(repo), self._index(repo)), (before.head, index))

    def test_the_index_keeps_what_no_tree_records_after_a_publication_and_a_refusal(self) -> None:
        """Skip-worktree and assume-unchanged flags, an intent-to-add entry and the entries outside
        a sparse checkout's cone: each survives a publication that does not touch its path, comes
        back with the index a refusal returns, and stays on an entry the commit changes."""

        def flag(option: str):
            return lambda repo: run_git(repo, ["update-index", option, "side.txt"])

        def intend(repo: Path) -> None:
            (repo / "planned.txt").write_text("planned\n", encoding="utf-8")
            run_git(repo, ["add", "-N", "planned.txt"])

        def sparse(repo: Path) -> None:
            run_git(repo, ["sparse-checkout", "init", "--cone"])
            run_git(repo, ["sparse-checkout", "set", "kept"])

        kinds = {
            "skip-worktree": ("side.txt", "S side.txt", flag("--skip-worktree")),
            "assume-unchanged": ("side.txt", "h side.txt", flag("--assume-unchanged")),
            "intent-to-add": ("planned.txt", "H planned.txt", intend),
            "sparse checkout": ("away/b.txt", "S away/b.txt", sparse),
        }
        for kind, (path, listed, arm) in kinds.items():
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                repo = self._repo(Path(tmp))
                for name in ("away", "kept"):
                    (repo / name).mkdir()
                    (repo / name / "b.txt").write_text("b\n", encoding="utf-8")
                _commit(repo, "side.txt", "side\n")
                arm(repo)
                self.assertIn(listed, self._shape(repo)[0].splitlines())
                # A publication that does not touch the path.
                first = self._tree(repo, "new.txt", "new\n")
                (repo / "new.txt").write_text("new\n", encoding="utf-8")
                flags, _, status = self._shape(repo)
                commit = publish_tree_commit(repo, "m", tree=first, before=_admitted(repo))
                self.assertEqual(
                    self._shape(repo)[0].splitlines(),
                    sorted([*flags.splitlines(), "H new.txt"], key=lambda row: row[2:]),
                )
                self.assertEqual(self._shape(repo)[2], status.replace("?? new.txt\n", ""))
                self._assert_tree(repo, commit, first)
                # A refused publication of a tree that does change the path.
                second = self._tree(repo, path, "changed\n")
                found = self._shape(repo)

                def late() -> None:
                    raise RuntimeError("changed since the judgment")

                with self.assertRaisesRegex(RuntimeError, "changed since the judgment"):
                    publish_tree_commit(
                        repo,
                        "m",
                        tree=second,
                        before=_admitted(repo),
                        hooks=PublicationHooks(confirm=late),
                    )
                self.assertEqual(self._shape(repo), found)
                self.assertEqual(head_commit(repo), commit)
                # The same tree published: the entry is the commit's, and its flag is still set.
                commit = publish_tree_commit(repo, "m", tree=second, before=_admitted(repo))
                self.assertIn(listed, self._shape(repo)[0].splitlines())
                self.assertNotIn(" D ", self._shape(repo)[2])
                self.assertIn(f"\t{path}\n", self._index(repo))
                self._assert_tree(repo, commit, second)

    def _assert_tree(self, repo: Path, commit: str, tree: str) -> None:
        """The commit is exactly ``tree``, and so is the index after its publication."""

        self.assertEqual(head_commit(repo), commit)
        self.assertEqual(run_git(repo, ["rev-parse", f"{commit}^{{tree}}"]).stdout.strip(), tree)
        self.assertEqual(run_git(repo, ["write-tree"]).stdout.strip(), tree)


class RunnerContractTests(unittest.TestCase):
    def test_an_explicit_timeout_still_bounds_a_stalled_command(self) -> None:
        # The other side of per-call timeouts: raising the default must not amount to
        # removing the bound. A caller that names a short one still gets it.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init(repo)
            _commit(repo, "file.txt", "one\n")
            stall = ["-c", "alias.stall=!sleep 5", "stall"]

            with self.assertRaises(subprocess.TimeoutExpired):
                run_git(
                    repo,
                    stall,
                    # load-independent: this bound must expire against the deliberately stalled command.
                    GitRunnerOptions(timeout=1),
                )


class CandidateTreeConcurrencyTests(unittest.TestCase):
    def test_candidate_tree_isolates_concurrent_observers_with_one_scratch_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            _init(repo)
            _commit(repo, "file.txt", "one\n")
            (repo / "untracked.txt").write_text("candidate\n", encoding="utf-8")
            index = root / "candidate.index"

            with ThreadPoolExecutor(max_workers=8) as executor:
                trees = list(
                    executor.map(lambda _: worktree_candidate_tree(repo, index), range(24))
                )

            self.assertEqual(len(set(trees)), 1)
            self.assertFalse(index.exists())
            self.assertEqual(list(root.glob(f".{index.name}-*")), [])


class PrivateGitPreparationTests(unittest.TestCase):
    """Real Git state behind the primitive; lifecycle admission belongs to its caller."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "logical"
        _init(self.repo)
        self.parent = _commit(self.repo, "source.txt", "original\n")
        (self.repo / "source.txt").write_text("candidate\n", encoding="utf-8")
        self.assertEqual(run_git(self.repo, ["add", "source.txt"]).returncode, 0)
        tree = run_git(self.repo, ["write-tree"]).stdout.strip()
        self.common = self.repo / ".git"
        self.before_index = (self.common / "index").read_bytes()
        self.binding = PrivateGitPreparationBinding(
            self.repo,
            "refs/heads/main",
            self.parent,
            self.common,
            self.root / "named-output",
            self.parent,
            tree,
            "prepared output",
            "strict-code-no-verify",
            "operation-1",
            1,
            "a" * 64,
        )
        self.authorizations: list[PrivateGitPreparationBinding] = []
        self.cancelled = False

    def authorize(self, binding: PrivateGitPreparationBinding) -> None:
        self.authorizations.append(binding)
        if self.cancelled:
            raise RuntimeError("operation cancelled")

    def _materialize(self) -> PrivateGitPreparationCapability:
        capability = admit_private_git_preparation(self.binding, authorize=self.authorize)
        self.assertEqual(run_git_preparation(capability, "create").returncode, 0)
        self.assertEqual(run_git_preparation(capability, "materialize").returncode, 0)
        return capability

    def _unchanged_logical(self) -> None:
        self.assertEqual(run_git(self.repo, ["rev-parse", "HEAD"]).stdout.strip(), self.parent)
        self.assertEqual((self.common / "index").read_bytes(), self.before_index)
        self.assertEqual((self.repo / "source.txt").read_text(), "candidate\n")

    def test_exact_private_commit_preserves_logical_state_and_normal_hook_policy(self) -> None:
        log = self.common / "hook-invocations"
        for hook in ("pre-commit", "commit-msg", "prepare-commit-msg", "post-commit"):
            path = self.common / "hooks" / hook
            path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{hook}' >> '{log}'\n", encoding="utf-8")
            path.chmod(0o755)
        for policy, expected in (
            ("strict-code-no-verify", ["prepare-commit-msg", "post-commit"]),
            ("ordinary", ["pre-commit", "prepare-commit-msg", "commit-msg", "post-commit"]),
        ):
            with self.subTest(policy=policy):
                self.binding = replace(
                    self.binding, private_root=self.root / policy, hook_policy=policy
                )
                if log.exists():
                    log.unlink()
                capability = self._materialize()
                command = preparation_command(self.binding, "commit")
                with patch.object(subprocess, "run", wraps=subprocess.run) as spawned:
                    self.assertEqual(run_git_preparation(capability, "commit").returncode, 0)
                self.assertIn(list(command.argv), [call.args[0] for call in spawned.call_args_list])
                self.assertEqual(command.cwd, self.binding.private_root)
                observed = inspect_git_preparation(capability)
                self.assertEqual(observed.state, "committed")
                self.assertEqual(observed.tree, self.binding.admitted_tree)
                self.assertEqual(observed.parents, (self.parent,))
                self.assertIsNotNone(observed.raw_commit)
                self.assertEqual(log.read_text().splitlines(), expected)
                with self.assertRaisesRegex(GitPreparationError, "not admitted from committed"):
                    run_git_preparation(capability, "commit")
                self.assertEqual(log.read_text().splitlines(), expected)
                self._unchanged_logical()
        self.assertGreater(len(self.authorizations), 12)

    def test_raw_commit_readback_preserves_crlf_and_opaque_signature_header(self) -> None:
        capability = self._materialize()
        self.assertEqual(run_git_preparation(capability, "commit").returncode, 0)
        original = inspect_git_preparation(capability)
        assert original.raw_commit is not None and original.head is not None
        headers = original.raw_commit.split(b"\n\n", 1)[0]
        raw = (
            headers
            + b"\ngpgsig -----BEGIN PGP SIGNATURE-----\r\n opaque\r\n -----END PGP SIGNATURE-----\n\nopaque\r\nmessage\xff\r\n"
        )
        # An actual Git object tests lossless transport; the opaque header claims no verified signature.
        written = run_git(
            self.repo,
            ["hash-object", "-t", "commit", "-w", "--stdin"],
            GitRunnerOptions(input_text=raw.decode("utf-8", "surrogateescape")),
        )
        self.assertEqual(written.returncode, 0, written.stderr)
        object_id = written.stdout.strip()
        self.assertEqual(
            run_git(
                self.binding.private_root, ["update-ref", "HEAD", object_id, original.head]
            ).returncode,
            0,
        )
        observed = inspect_git_preparation(capability)
        self.assertEqual(observed.raw_commit, raw)
        self.assertEqual(read_git_commit_bytes(self.repo, object_id), raw)
        self.assertEqual(observed.head, object_id)
        self._unchanged_logical()

    def test_cancelled_owner_and_forged_capability_start_no_commit(self) -> None:
        capability = self._materialize()
        forged = PrivateGitPreparationCapability(self.binding, self.authorize, object())
        with self.assertRaisesRegex(GitPreparationError, "not admitted"):
            run_git_preparation(forged, "commit")
        self.cancelled = True
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            run_git_preparation(capability, "commit")
        self.assertEqual(
            run_git(self.binding.private_root, ["rev-parse", "HEAD"]).stdout.strip(), self.parent
        )
        self._unchanged_logical()

    def test_hidden_index_flags_and_changed_physical_bytes_refuse_commit(self) -> None:
        capability = self._materialize()
        private = self.binding.private_root
        self.assertEqual(
            run_git(private, ["update-index", "--assume-unchanged", "source.txt"]).returncode, 0
        )
        with self.assertRaisesRegex(GitPreparationError, "hidden source flags"):
            run_git_preparation(capability, "commit")
        self.assertEqual(
            run_git(private, ["update-index", "--no-assume-unchanged", "source.txt"]).returncode, 0
        )
        (private / "source.txt").write_text("tampered!\n", encoding="utf-8")
        with self.assertRaisesRegex(GitPreparationError, "bytes differ"):
            run_git_preparation(capability, "commit")
        self.assertEqual(run_git(private, ["rev-parse", "HEAD"]).stdout.strip(), self.parent)
        self._unchanged_logical()

    def test_stale_logical_tip_and_rebound_private_metadata_refuse_before_mutation(self) -> None:
        capability = self._materialize()
        other = self.root / "other-output"
        self.assertEqual(
            run_git(
                self.repo, ["worktree", "add", "--detach", "--no-checkout", str(other), self.parent]
            ).returncode,
            0,
        )
        dot_git = self.binding.private_root / ".git"
        original_pointer = dot_git.read_bytes()
        dot_git.write_bytes((other / ".git").read_bytes())
        with self.assertRaisesRegex(GitPreparationError, "backlink changed"):
            run_git_preparation(capability, "commit")
        dot_git.write_bytes(original_pointer)
        self.assertEqual(
            run_git(self.repo, ["commit", "-m", "foreign logical update"]).returncode, 0
        )
        moved = run_git(self.repo, ["rev-parse", "HEAD"]).stdout.strip()
        with self.assertRaisesRegex(GitPreparationError, "logical Git preparation binding changed"):
            run_git_preparation(capability, "commit")
        self.assertEqual(run_git(self.repo, ["rev-parse", "HEAD"]).stdout.strip(), moved)
        self.assertEqual(
            run_git(self.binding.private_root, ["rev-parse", "HEAD"]).stdout.strip(), self.parent
        )

    def test_failed_hook_returns_original_failure_and_does_not_retry(self) -> None:
        self.binding = replace(self.binding, hook_policy="ordinary")
        capability = self._materialize()
        hook = self.common / "hooks" / "pre-commit"
        log = self.common / "failed-hook-count"
        hook.write_text(
            f"#!/bin/sh\necho called >> '{log}'\necho original-refusal >&2\nexit 1\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        result = run_git_preparation(capability, "commit")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("original-refusal", result.stderr)
        self.assertEqual(log.read_text().splitlines(), ["called"])
        self.assertEqual(inspect_git_preparation(capability).state, "materialized")
        self._unchanged_logical()


if __name__ == "__main__":
    unittest.main()
