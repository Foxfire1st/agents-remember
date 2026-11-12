"""Real recovery and hook witnesses for L61's sealed F06/F09/F10 findings."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import signal
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout, suppress
from pathlib import Path

import test_knowledge_validator_routes as validator_tests
import test_landing_commit_check as landing_tests
import test_memory_backfill as backfill_tests
from agents_remember.application.worktree_services import (
    bind_worktree_services,
    build_default_worktree_services,
)
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember.worktrees.sync_transaction_state import SyncOperationRecord, SyncOperationStore
from agents_remember.worktrees.worktree_contract import load_contract
from test_worktree_sync import SyncFixture, commit_file, git


class RemainingRouteMutationLogTests(unittest.TestCase):
    def test_remaining_routes_keep_mutation_log_in_refusal(self) -> None:
        """F10: actual callers retain the mutation path, whole output/time and log pointer."""
        harness = landing_tests.RemainingRouteProductionTests()
        for route in ("ordinary", "direct"):
            for leg in ("code", "memory"):
                with self.subTest(route=route, leg=leg), tempfile.TemporaryDirectory() as tmp:
                    repo = harness._repo(Path(tmp), leg)
                    landing_tests._hook(
                        repo,
                        "#!/bin/sh\necho mutation-stdout\necho mutation-stderr >&2\n"
                        "printf 'hook edit\\n' > source.txt\nexit 1\n",
                    )
                    head, index = git(repo, "rev-parse", "HEAD"), harness._index(repo)
                    with self.assertRaises(
                        (
                            landing_tests.LandingCommitCheckRefusal,
                            landing_tests.DirectLandingPublicationRefused,
                        )
                    ) as refused:
                        harness._publish(repo, route)
                    error = harness._refusal(refused.exception)
                    self.assertEqual(error.reason, "changed")
                    self.assertIn("source.txt", str(refused.exception))
                    assert error.log is not None
                    self.assertIn(error.log.as_posix(), str(refused.exception))
                    output = error.log.read_text(encoding="utf-8")
                    self.assertIn("mutation-stdout", output)
                    self.assertIn("mutation-stderr", output)
                    self.assertRegex(output, r"elapsed: \d+\.\d+s")
                    self.assertEqual(git(repo, "rev-parse", "HEAD"), head)
                    self.assertEqual(harness._index(repo), index)


class MaintenanceHookWitnessTests(unittest.TestCase):
    def test_public_backfill_on_covered_refs_refuses_then_checks_every_rewritten_tree(self) -> None:
        """F09: public maintenance reaches task/master refs and runs the real repository hook."""
        for passes in (False, True):
            with self.subTest(passes=passes):
                fixture = backfill_tests.BackfillFixture(memory_branch="ar/leaf")
                self.addCleanup(fixture.close)
                code = fixture.build_code(2)
                memory = fixture.build_memory(3)
                fixture.table([(code[0], memory[2]), (code[1], memory[1])])
                harness = backfill_tests.MemoryBackfillCliTests()
                harness.fixture = fixture
                contract = harness.contract_path()
                repo = fixture.memory
                before = fixture.tip()
                git(repo, "branch", "ar/master", before)
                index = Path(
                    git(repo, "rev-parse", "--path-format=absolute", "--git-path", "index")
                )
                index_before = index.read_bytes()
                common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
                judged = common / "judged-trees.txt"
                hook = common / "hooks" / "pre-commit"
                hook.write_text(
                    '#!/bin/sh\ngit write-tree >> "$(git rev-parse --git-common-dir)/judged-trees.txt"\n'
                    "echo maintenance-stdout\necho maintenance-stderr >&2\n"
                    + (
                        "echo '[gate] result: maintenance PASS'\nexit 0\n"
                        if passes
                        else "echo '[gate] result: maintenance FAIL'\nexit 1\n"
                    ),
                    encoding="utf-8",
                )
                hook.chmod(0o755)
                output = io.StringIO()
                with redirect_stdout(output):
                    result = harness.invoke(
                        "--contract",
                        str(contract),
                        "--apply",
                        "--ref",
                        "refs/heads/ar/leaf",
                        "--ref",
                        "refs/heads/ar/master",
                    )
                text = output.getvalue()
                self.assertEqual(result, 0 if passes else 2, text)
                checked = judged.read_text(encoding="utf-8").splitlines()
                self.assertTrue(checked)
                if passes:
                    after = fixture.tip()
                    self.assertNotEqual(after, before)
                    self.assertEqual(git(repo, "rev-parse", "ar/master"), after)
                    old_line = git(repo, "rev-list", before).splitlines()
                    new_line = git(repo, "rev-list", after).splitlines()
                    expected = {
                        git(repo, "rev-parse", f"{new}^{{tree}}")
                        for old, new in zip(old_line, new_line, strict=True)
                        if old != new
                    }
                    self.assertEqual(set(checked), expected)
                    self.assertEqual(len(checked), len(expected))
                else:
                    self.assertEqual(fixture.tip(), before)
                    self.assertEqual(git(repo, "rev-parse", "ar/master"), before)
                    self.assertEqual(index.read_bytes(), index_before)
                    self.assertIn("no named ref moved", text)
                    self.assertIn("step: maintenance", text)
                    pointer = re.search(r"log: ([^;\n]+)", text)
                    self.assertIsNotNone(pointer, text)
                    assert pointer is not None
                    log = Path(pointer.group(1).strip())
                    body = log.read_text(encoding="utf-8")
                    self.assertIn("maintenance-stdout", body)
                    self.assertIn("maintenance-stderr", body)
                    self.assertRegex(body, r"elapsed: \d+\.\d+s")


class FinalizationDeathWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def test_killed_after_contract_before_terminal_cancel_restores_exact_before_state(self) -> None:
        """F06: real SIGKILL between contract replacement and terminal journal replacement.

        CPython's filesystem audit event pauses the actual process immediately
        before the terminal journal's os.replace. No product function or Git
        operation is replaced; the parent proves both durable files' states
        before killing the process group and calling the public cancel.
        """
        with tempfile.TemporaryDirectory() as tmp:
            fixture, before = self._fixture(tmp)
            store = SyncOperationStore(fixture.contract.worktree_group)
            pid = self._pause_before_write(fixture, phase="completed")
            try:
                record = store.read()
                assert isinstance(record, SyncOperationRecord)
                self.assertEqual(record.phase, "finalizing")
                finalized = load_contract(fixture.contract.contract_path)
                self.assertEqual(finalized.code_base_commit, record.code.sourceCommit)
                self.assertEqual(len(finalized.sync_log), 1)
                os.killpg(pid, signal.SIGKILL)
                _, killed = os.waitpid(pid, 0)
                self.assertTrue(os.WIFSIGNALED(killed))
                self.assertEqual(os.WTERMSIG(killed), signal.SIGKILL)
                self._assert_cancelled(fixture, before)
            finally:
                with suppress(ProcessLookupError, ChildProcessError):
                    os.killpg(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)

    def test_killed_before_each_automatic_journal_write_cancels_exactly(self) -> None:
        """F06: admission, each side's restore/advance, and finalization are separate real cuts."""
        for phase, occurrence in (
            ("running-code", 1),
            ("running-code", 2),
            ("running-memory", 1),
            ("running-memory", 2),
            ("finalizing", 1),
        ):
            with (
                self.subTest(phase=phase, occurrence=occurrence),
                tempfile.TemporaryDirectory() as tmp,
            ):
                fixture, before = self._fixture(tmp)
                pid = self._pause_before_write(fixture, phase=phase, occurrence=occurrence)
                self._kill(pid)
                self._assert_cancelled(fixture, before)

    def test_killed_before_each_admission_pin_cancels_exactly(self) -> None:
        """F06: every partially pinned admission still has a durable restoration intent."""
        for occurrence in range(1, 7):
            with self.subTest(occurrence=occurrence), tempfile.TemporaryDirectory() as tmp:
                fixture, before = self._fixture(tmp)
                pid = self._pause_before_write(fixture, phase="pin", occurrence=occurrence)
                self._kill(pid)
                self._assert_cancelled(fixture, before)

    def test_killed_between_side_parks_cancel_keeps_the_unparked_side(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture, before = self._fixture(tmp)
            self._kill(self._pause_before_write(fixture, phase="park", occurrence=2))
            self._assert_cancelled(fixture, before)

    def test_killed_before_each_cancel_journal_write_second_cancel_restores_exactly(self) -> None:
        """F06: cancel admission, both restored indexes, and reversed-contract/terminal cut."""
        for phase, occurrence in (
            ("cancelling", 1),
            ("cancelling", 2),
            ("cancelling", 3),
            ("cancelled", 1),
        ):
            with (
                self.subTest(phase=phase, occurrence=occurrence),
                tempfile.TemporaryDirectory() as tmp,
            ):
                fixture, before = self._fixture(tmp)
                self._kill(self._pause_before_write(fixture, phase="completed"))
                pid = self._pause_before_write(
                    fixture, phase=phase, occurrence=occurrence, action="cancel"
                )
                self._kill(pid)
                self._assert_cancelled(fixture, before)

    def test_killed_before_recovered_journal_write_cancels_missing_and_corrupt_records(
        self,
    ) -> None:
        """F06: archive/ref recovery itself has a real journal replacement cut."""
        for damage in ("missing", "malformed"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as tmp:
                fixture, before = self._fixture(tmp)
                self._kill(self._pause_before_write(fixture, phase="running-memory"))
                store = SyncOperationStore(fixture.contract.worktree_group)
                if damage == "missing":
                    store.path.unlink()
                else:
                    store.path.write_text("{corrupt journal", encoding="utf-8")
                self._kill(self._pause_before_write(fixture, phase="cancelling", action="cancel"))
                self._assert_cancelled(fixture, before)

    def test_killed_before_validation_fingerprint_and_refusal_writes_cancels_exactly(self) -> None:
        """F06: both validator paths, including the staged-merge fingerprint/refusal interval."""
        for merged, occurrence in ((False, 2), (True, 2), (True, 3)):
            with (
                self.subTest(merged=merged, occurrence=occurrence),
                tempfile.TemporaryDirectory() as tmp,
            ):
                fixture, before = self._validation_fixture(tmp, merged)
                self._kill(
                    self._pause_before_write(
                        fixture,
                        phase="running-memory",
                        occurrence=occurrence,
                        choice="merge-memory" if merged else None,
                    )
                )
                self._assert_cancelled(fixture, before)
                if merged and occurrence == 2:
                    saved = (
                        fixture.contract.worktree_group / "scratch/sync-cancel-generation-1/memory"
                    )
                    self.assertIn(b"INV-D0P111", (saved / "staged.patch").read_bytes())

    def _validation_fixture(self, tmp: str, merged: bool) -> tuple[SyncFixture, list[tuple]]:
        fixture = SyncFixture(Path(tmp))
        worktree = fixture.contract.memory_worktree
        assert worktree is not None
        content = validator_tests.fixture_tree_files()
        duplicate = validator_tests.encode(validator_tests.invariant_document("INV-D0P111"))
        if merged:
            validator_tests._commit(
                worktree, {**content, validator_tests.LEFT_DUPLICATE: duplicate}, "Work: mint"
            )
        code_tip = fixture.move_official_code()
        source = {**content, validator_tests.RIGHT_DUPLICATE: duplicate}
        if not merged:
            source[validator_tests.LEFT_DUPLICATE] = duplicate
        validator_tests._commit(
            fixture.memory_repo, source, render_memory_content_message("Source mint", code_tip)
        )
        for root in (fixture.contract.code_worktree, worktree):
            (root / "README.md").write_text("staged candidate\n", encoding="utf-8")
            git(root, "add", "README.md")
            (root / "README.md").write_text(
                "staged candidate\nunstaged candidate\n", encoding="utf-8"
            )
            (root / "note.txt").write_bytes(b"untracked candidate\n")
        return fixture, [
            self._snapshot(root) for root in (fixture.contract.code_worktree, worktree)
        ]

    def test_killed_before_retaining_or_continuing_each_resolution_outcome_cancels_exactly(
        self,
    ) -> None:
        """F06: retained merge, incomplete continuation, failed hook, and successful restore cuts."""
        for outcome in (
            "retain",
            "parked-retain",
            "incomplete",
            "hook-fail",
            "pass",
            "parked-settle",
        ):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                fixture, before = self._conflict_fixture(tmp, parked=outcome.startswith("parked-"))
                worktree = fixture.contract.code_worktree
                action = None
                if outcome not in {"retain", "parked-retain"}:
                    retained = fixture.sync()
                    self.assertEqual(
                        retained.payload["state"], "sync-resolution-required", retained.payload
                    )
                    action = "continue"
                    if outcome != "incomplete":
                        (worktree / "README.md").write_text(
                            "owner resolved both lines\n", encoding="utf-8"
                        )
                        git(worktree, "add", "README.md")
                    if outcome == "hook-fail":
                        common = Path(
                            git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
                        )
                        hook = common / "hooks/pre-commit"
                        hook.write_text(
                            "#!/bin/sh\necho '[gate] result: fixture FAIL'\nexit 1\n",
                            encoding="utf-8",
                        )
                        hook.chmod(0o755)
                pid = self._pause_before_write(
                    fixture, phase="code-resolution-required", action=action
                )
                self._kill(pid)
                self._assert_cancelled(fixture, before)

    def _conflict_fixture(self, tmp: str, *, parked: bool) -> tuple[SyncFixture, list[tuple]]:
        fixture = SyncFixture(Path(tmp))
        worktree = fixture.contract.code_worktree
        if not parked:
            commit_file(worktree, "README.md", "leaf committed line")
        else:
            (worktree / "README.md").write_text("parked candidate line\n", encoding="utf-8")
            git(worktree, "add", "README.md")
            (worktree / "README.md").write_text(
                "parked candidate line\nunstaged line\n", encoding="utf-8"
            )
        (worktree / "note.txt").write_bytes(b"untracked candidate\n")
        assert fixture.contract.memory_worktree is not None
        before = [self._snapshot(root) for root in (worktree, fixture.contract.memory_worktree)]
        commit_file(fixture.code_repo, "README.md", "source committed line")
        fixture.map_official_memory(git(fixture.code_repo, "rev-parse", "main"))
        return fixture, before

    def test_killed_after_save_and_discard_before_cancelling_names_saved_files(self) -> None:
        """F06: the save/discard -> first cancelling write cut retains the saved-file answer."""
        for later_write in (False, True):
            with self.subTest(later_write=later_write), tempfile.TemporaryDirectory() as tmp:
                fixture, before = self._conflict_fixture(tmp, parked=False)
                retained = fixture.sync()
                self.assertEqual(retained.payload["state"], "sync-resolution-required")
                worktree = fixture.contract.code_worktree
                (worktree / "README.md").write_text(
                    "unattributable owner resolution\n", encoding="utf-8"
                )
                git(worktree, "add", "README.md")
                self._kill(self._pause_before_write(fixture, phase="cancelling", action="cancel"))
                saved = fixture.contract.worktree_group / "scratch/sync-cancel-generation-1/code"
                first = (saved / "staged.patch").read_bytes()
                manifest = (saved / "saved-state.json").read_bytes()
                self.assertIn(b"unattributable owner resolution", first)
                if later_write:
                    (worktree / "README.md").write_text("later owner write\n", encoding="utf-8")
                    git(worktree, "add", "README.md")
                self._assert_cancelled(fixture, before)
                self.assertEqual((saved / "staged.patch").read_bytes(), first)
                self.assertEqual((saved / "saved-state.json").read_bytes(), manifest)
                if later_write:
                    patches = list(saved.glob("saved-*/staged.patch"))
                    self.assertEqual(len(patches), 1)
                    self.assertIn(b"later owner write", patches[0].read_bytes())

    def test_failed_saved_discard_stop_then_public_cancel_preserves_every_byte(self) -> None:
        """F06: a real checkout permission failure returns the saved-discard stop."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture, before = self._conflict_fixture(tmp, parked=True)
            retained = fixture.sync()
            self.assertEqual(retained.payload["state"], "sync-resolution-required")
            worktree = fixture.contract.code_worktree
            store = SyncOperationStore(fixture.contract.worktree_group)
            record = store.read()
            assert isinstance(record, SyncOperationRecord)
            parked = record.code.wipStash
            self.assertTrue(parked)
            parked_tree = git(worktree, "rev-parse", f"{parked}^2^{{tree}}")
            self.assertEqual(parked_tree, before[0][1])
            (worktree / "README.md").write_bytes(b"owner staged resolution\n")
            git(worktree, "add", "README.md")
            (worktree / "README.md").write_bytes(b"owner staged resolution\nowner unstaged\n")
            (worktree / "extra.txt").write_bytes(b"owner untracked\n")
            held = self._snapshot(worktree)
            journal = store.path.read_bytes()
            contract = fixture.contract.contract_path.read_bytes()
            mode = worktree.stat().st_mode
            worktree.chmod(0o500)
            try:
                stopped = fixture.sync(resolution_action="cancel")
            finally:
                worktree.chmod(mode)
            self.assertEqual(stopped.payload["state"], "sync-cancel-saved-discard-failed")
            self.assertIn("Permission denied", str(stopped.payload["summary"]))
            self.assertEqual(self._snapshot(worktree), held)
            self.assertEqual(store.path.read_bytes(), journal)
            self.assertEqual(fixture.contract.contract_path.read_bytes(), contract)
            self.assertEqual(git(worktree, "rev-parse", f"{parked}^2^{{tree}}"), parked_tree)
            saved = fixture.contract.worktree_group / "scratch/sync-cancel-generation-1/code"
            saved_bytes = {path: path.read_bytes() for path in saved.rglob("*") if path.is_file()}
            self.assertIn(b"owner staged resolution", (saved / "staged.patch").read_bytes())
            self.assertIn(b"owner unstaged", (saved / "unstaged.patch").read_bytes())
            self.assertEqual((saved / "untracked/extra.txt").read_bytes(), b"owner untracked\n")
            self._assert_cancelled(fixture, before)
            self.assertFalse((worktree / "extra.txt").exists())
            self.assertEqual({path: path.read_bytes() for path in saved_bytes}, saved_bytes)

    def _fixture(self, tmp: str) -> tuple[SyncFixture, list[tuple]]:
        fixture = SyncFixture(Path(tmp))
        for worktree in (fixture.contract.code_worktree, fixture.contract.memory_worktree):
            assert worktree is not None
            (worktree / "README.md").write_text("staged candidate\n", encoding="utf-8")
            git(worktree, "add", "README.md")
            (worktree / "README.md").write_text(
                "staged candidate\nunstaged candidate\n", encoding="utf-8"
            )
            (worktree / "note.txt").write_bytes(b"untracked candidate\n")
        before = [
            self._snapshot(root)
            for root in (fixture.contract.code_worktree, fixture.contract.memory_worktree)
            if root is not None
        ]
        commit_file(fixture.code_repo, "other.txt", "the official line moved")
        fixture.map_official_memory(git(fixture.code_repo, "rev-parse", "main"))
        return fixture, before

    def _assert_cancelled(self, fixture: SyncFixture, before: list[tuple]) -> None:
        cancelled = fixture.sync(resolution_action="cancel")
        self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
        self.assertEqual(
            [
                self._snapshot(root)
                for root in (fixture.contract.code_worktree, fixture.contract.memory_worktree)
                if root is not None
            ],
            before,
        )
        restored = load_contract(fixture.contract.contract_path)
        self.assertEqual(restored.code_base_commit, fixture.contract.code_base_commit)
        self.assertEqual(restored.memory_base_commit, fixture.contract.memory_base_commit)
        self.assertEqual(restored.sync_log, fixture.contract.sync_log)
        terminal = SyncOperationStore(fixture.contract.worktree_group).read()
        assert isinstance(terminal, SyncOperationRecord)
        self.assertEqual(terminal.phase, "cancelled")
        for saved in (fixture.contract.worktree_group / "scratch").glob(
            "sync-cancel-generation-*/*"
        ):
            for path in saved.rglob("*"):
                if path.is_file():
                    self.assertIn(
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                        str(cancelled.payload["summary"]),
                    )

    def _kill(self, pid: int) -> None:
        os.killpg(pid, signal.SIGKILL)
        _, killed = os.waitpid(pid, 0)
        self.assertTrue(os.WIFSIGNALED(killed))
        self.assertEqual(os.WTERMSIG(killed), signal.SIGKILL)

    def _matches_cut(self, event: str, args: tuple, path: Path, phase: str) -> bool:
        if phase == "park":
            return event == "subprocess.Popen" and "stash" in args[1] and "push" in args[1]
        if phase == "pin":
            if event != "subprocess.Popen":
                return False
            command = args[1]
            return (
                "update-ref" in command
                and "-d" not in command
                and any(str(part).startswith("refs/agents-remember/sync/") for part in command)
            )
        if event != "os.rename" or Path(args[1]) != path:
            return False
        pending = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        return pending.get("phase") == phase

    def _pause_before_write(
        self,
        fixture: SyncFixture,
        *,
        phase: str,
        action: str | None = None,
        occurrence: int = 1,
        choice: str | None = None,
    ) -> int:
        store = SyncOperationStore(fixture.contract.worktree_group)
        pid = os.fork()
        if pid == 0:
            try:
                os.setsid()
                seen = 0

                def pause(event: str, args: tuple) -> None:
                    nonlocal seen
                    if self._matches_cut(event, args, store.path, phase):
                        seen += 1
                        if seen == occurrence:
                            os.kill(os.getpid(), signal.SIGSTOP)

                sys.addaudithook(pause)
                fixture.sync(resolution_action=action, memory_sync_choice=choice)
            finally:
                os._exit(42)
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline:
                waited, status = os.waitpid(pid, os.WNOHANG | os.WUNTRACED)
                if waited:
                    self.assertTrue(os.WIFSTOPPED(status), (phase, occurrence, status))
                    return pid
                time.sleep(0.001)
            self.fail(f"journal replacement {phase}/{occurrence} was not reached")
        except BaseException:
            with suppress(ProcessLookupError, ChildProcessError):
                os.killpg(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            raise

    def _snapshot(self, root: Path) -> tuple:
        return (
            git(root, "rev-parse", "HEAD"),
            git(root, "write-tree"),
            git(root, "diff", "--binary"),
            git(root, "diff", "--cached", "--binary"),
            git(root, "status", "--porcelain"),
            git(root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/stash"),
            (root / "note.txt").read_bytes() if (root / "note.txt").exists() else None,
        )
