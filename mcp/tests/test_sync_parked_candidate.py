"""The parked worktree candidate inside the existing sync transaction.

A closeout-time leaf is dirty by definition, so the sync parks its WIP, carries the moved
source, and must return the candidate. These cases pin that contract at the transaction
level; the closeout boundary's own surface is pinned in ``test_source_lineage``.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import suppress
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application import worktree_tools
from agents_remember.application.worktree_services import (
    bind_worktree_services,
    build_default_worktree_services,
)
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.models.worktree import SyncResolutionProjection
from agents_remember.worktrees import (
    sync_transaction,
    sync_transaction_authority,
    sync_transaction_recovery,
)
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember.worktrees.sync_transaction_git import SyncGitProofError, clear_stale_git_locks
from agents_remember.worktrees.sync_transaction_git import _proof as sync_git_proof
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncOperationStore,
    SyncSideRecord,
)
from agents_remember.worktrees.worktree_contract import load_contract
from test_review_result_blocks_through_real_tools import (
    _config as _review_sync_config,
)
from test_review_result_blocks_through_real_tools import (
    _record_review as _record_reviewed_pair,
)
from test_worktree_sync import SyncFixture, commit_file, git, section
from test_worktree_sync_knowledge_merge import (
    APP_SIDECAR,
    Lines,
    _converted_lines,
    _edited,
    _sidecar,
)


def snapshot_tree(worktree: Path) -> dict[str, bytes]:
    """Every worktree byte below the checkout, keyed by relative path."""
    snapshot: dict[str, bytes] = {}
    for path in sorted(worktree.rglob("*")):
        if ".git" in path.parts:
            continue
        if path.is_symlink() or path.is_file():
            snapshot[path.relative_to(worktree).as_posix()] = path.read_bytes()
        elif path.is_dir():
            continue
        else:  # pragma: no cover - no sockets or devices in a test worktree
            raise AssertionError(f"unsnapshotable worktree entry: {path}")
    return snapshot


def journal_bytes(fixture: SyncFixture) -> bytes | None:
    store = SyncOperationStore(fixture.contract.worktree_group)
    try:
        return store.path.read_bytes()
    except FileNotFoundError:
        return None


class ParkedCandidateTests(unittest.TestCase):
    """A dirty closeout candidate is parked, carried, and returned by the same transaction."""

    def setUp(self) -> None:
        # Fast-forwards onto converted memory run the validator (MIK-R61 rule 4).
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def test_parked_candidate_is_carried_and_returned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "candidate.py").write_text("VALUE = 'candidate'\n", encoding="utf-8")
            (worktree / "README.md").write_text("candidate edit\n", encoding="utf-8")
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)

            result = fixture.sync()

            self.assertEqual(result.payload["state"], "synced")
            self.assertEqual(section(result.payload, "code")["wip"]["state"], "restored")
            self.assertEqual(
                (worktree / "candidate.py").read_text(encoding="utf-8"),
                "VALUE = 'candidate'\n",
            )
            self.assertEqual(
                (worktree / "README.md").read_text(encoding="utf-8"), "candidate edit\n"
            )
            self.assertEqual(git(worktree, "stash", "list"), "")
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), code_tip)
            self.assertEqual(
                load_contract(fixture.contract.contract_path).code_base_commit, code_tip
            )

    def test_parked_memory_candidate_is_carried_and_returned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            memory_worktree = fixture.contract.memory_worktree
            assert memory_worktree is not None
            (memory_worktree / "onboarding-draft.md").write_text("# draft\n", encoding="utf-8")
            (memory_worktree / "memory.md").write_text("staged cache data\n", encoding="utf-8")
            git(memory_worktree, "add", "memory.md")
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)

            result = fixture.sync()

            self.assertEqual(result.payload["state"], "synced")
            self.assertEqual(section(result.payload, "memory")["wip"]["state"], "restored")
            self.assertEqual(
                section(result.payload, "memory")["wip"]["paths"], ["onboarding-draft.md"]
            )
            self.assertEqual(
                (memory_worktree / "onboarding-draft.md").read_text(encoding="utf-8"),
                "# draft\n",
            )
            self.assertEqual(git(memory_worktree, "stash", "list"), "")

    def test_parked_candidate_reapply_conflict_is_retained_and_cancel_returns_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "README.md").write_text("candidate edit\n", encoding="utf-8")
            pre_sync = git(worktree, "rev-parse", "HEAD")
            commit_file(fixture.code_repo, "README.md", "official edit")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)

            retained = fixture.sync()

            self.assertEqual(retained.returncode, 2)
            self.assertEqual(retained.payload["state"], "sync-resolution-required")
            self.assertTrue(section(retained.payload, "resolution")["wipRestore"])
            self.assertIn("README.md", section(retained.payload, "resolution")["files"])
            # The raw dict assertions above pass even when the public projection refuses the
            # payload, which is exactly how a producer emitting `resolution.wipRestore` while
            # `SyncResolutionProjection` did not declare it stayed invisible: the agent got
            # `extra_forbidden` instead of the resolution it needed. Validate the projection
            # model, not just the dict.
            projected = SyncResolutionProjection.model_validate(
                section(retained.payload, "resolution")
            )
            self.assertTrue(projected.wipRestore)
            self.assertIn("README.md", projected.files)
            self.assertNotEqual(git(worktree, "stash", "list"), "")

            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.payload["state"], "sync-cancelled")
            self.assertEqual(
                (worktree / "README.md").read_text(encoding="utf-8"), "candidate edit\n"
            )
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), pre_sync)
            self.assertEqual(git(worktree, "stash", "list"), "")

    def test_resume_returns_the_candidate_a_crash_left_parked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)

            with mock.patch.object(
                sync_transaction,
                "restore_parked_wip",
                side_effect=SyncGitProofError("simulated crash before the candidate returned"),
            ):
                crashed = fixture.sync()

            self.assertEqual(crashed.payload["state"], "sync-git-proof-failed")
            self.assertFalse((worktree / "candidate.py").exists())
            self.assertNotEqual(git(worktree, "stash", "list"), "")

            resumed = fixture.sync()

            self.assertEqual(resumed.payload["state"], "synced")
            self.assertEqual(section(resumed.payload, "code")["wip"]["state"], "restored")
            self.assertEqual((worktree / "candidate.py").read_text(encoding="utf-8"), "VALUE = 1\n")
            self.assertEqual(git(worktree, "stash", "list"), "")
            self.assertEqual(
                load_contract(fixture.contract.contract_path).code_base_commit, code_tip
            )

    def test_resolving_a_retained_merge_returns_the_parked_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "candidate.py").write_text("VALUE = 2\n", encoding="utf-8")
            commit_file(worktree, "README.md", "work version")
            commit_file(fixture.code_repo, "README.md", "official version")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)

            retained = fixture.sync()

            self.assertEqual(retained.payload["state"], "sync-resolution-required")
            self.assertNotIn("wipRestore", section(retained.payload, "resolution"))
            self.assertNotEqual(git(worktree, "stash", "list"), "")
            (worktree / "README.md").write_text("resolved version\n", encoding="utf-8")
            git(worktree, "add", "README.md")

            continued = fixture.sync(resolution_action="continue")

            self.assertEqual(continued.payload["state"], "synced")
            self.assertEqual((worktree / "candidate.py").read_text(encoding="utf-8"), "VALUE = 2\n")
            self.assertEqual(git(worktree, "stash", "list"), "")
            self.assertEqual(
                load_contract(fixture.contract.contract_path).code_base_commit, code_tip
            )

    def test_unmerged_index_entries_still_refuse_the_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            commit_file(worktree, "README.md", "work version")
            commit_file(fixture.code_repo, "README.md", "official version")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)
            git_unchecked(worktree, "merge", "--no-commit", "main")

            result = fixture.sync()

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "sync-side-preflight-failed")
            self.assertIn("unmerged paths", str(result.payload["summary"]))
            self.assertEqual(git(worktree, "stash", "list"), "")


def git_unchecked(repo: Path, *args: str) -> str:
    """Run a git command whose non-zero exit is the expected outcome (a real conflict)."""

    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    return result.stdout.strip() + result.stderr.strip()


class AlreadyHeldParkedEditTests(unittest.TestCase):
    """MIK-R153: a sync finishes when the landed line already holds a parked edit."""

    def setUp(self) -> None:
        # Fast-forwards onto converted memory run the validator (MIK-R61 rule 4).
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def _land_already_held_blank(self, fixture: SyncFixture) -> dict[str, str]:
        """Build the L149 shape in small: base notes, a parked blank line, a landing
        that holds the same blank line beside other changes in the same file."""
        worktree = fixture.contract.code_worktree
        base = "".join(f"line {index:02d}\n" for index in range(1, 21))
        (worktree / "notes.txt").write_text(base, encoding="utf-8")
        git(worktree, "add", "notes.txt")
        git(worktree, "commit", "-m", "base notes")
        git(fixture.code_repo, "merge", "--ff-only", "ar/sync-thing")
        pre_sync = git(worktree, "rev-parse", "HEAD")
        parked = base[: len("line 01\nline 02\n")] + "\n" + base[len("line 01\nline 02\n") :]
        (worktree / "notes.txt").write_text(parked, encoding="utf-8")
        landed = (
            base[: len("line 01\nline 02\n")]
            + "\n"
            + "".join(f"line {index:02d}\n" for index in range(3, 15))
            + "".join(f"landed {index:02d}\n" for index in range(15, 21))
        )
        git(fixture.code_repo, "checkout", "main")
        (fixture.code_repo / "notes.txt").write_text(landed, encoding="utf-8")
        git(fixture.code_repo, "add", "notes.txt")
        git(fixture.code_repo, "commit", "-m", "land the blank line with other changes")
        code_tip = git(fixture.code_repo, "rev-parse", "main")
        fixture.map_official_memory(code_tip)
        return {"pre_sync": pre_sync, "landed": landed, "tip": code_tip}

    def test_already_held_edit_finishes_the_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            shaped = self._land_already_held_blank(fixture)
            before = snapshot_tree(worktree)

            result = fixture.sync()

            self.assertEqual(result.payload["state"], "synced", result.payload)
            self.assertEqual(section(result.payload, "code")["wip"]["state"], "restored")
            expected = dict(before)
            expected["notes.txt"] = shaped["landed"].encode("utf-8")
            self.assertEqual(snapshot_tree(worktree), expected)
            self.assertEqual(git(worktree, "stash", "list"), "")
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), shaped["tip"])

    def test_truly_missing_parked_byte_fails_the_proof_and_names_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "other.txt").write_text("base line\n", encoding="utf-8")
            git(worktree, "add", "other.txt")
            git(worktree, "commit", "-m", "base other")
            git(fixture.code_repo, "merge", "--ff-only", "ar/sync-thing")
            self._land_already_held_blank(fixture)
            (worktree / "other.txt").write_text("base line\nparked byte\n", encoding="utf-8")
            base_other = "base line\n"

            real_apply = sync_transaction_authority.apply_parked_wip

            def apply_then_drop_byte(side):  # type: ignore[no-untyped-def]
                state, conflicts = real_apply(side)
                if side.side == "code":
                    (Path(side.worktree) / "other.txt").write_text(base_other, encoding="utf-8")
                return state, conflicts

            with mock.patch.object(
                sync_transaction_authority, "apply_parked_wip", side_effect=apply_then_drop_byte
            ):
                result = fixture.sync()

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "sync-git-proof-failed", result.payload)
            summary = str(result.payload["summary"])
            self.assertIn("other.txt", summary)
            self.assertIn("stash", summary)
            self.assertIn("cancel", summary)
            self.assertIn("Do not edit", summary)
            self.assertNotEqual(git(worktree, "stash", "list"), "")
            store = SyncOperationStore(fixture.contract.worktree_group)
            record = store.read()
            self.assertIsInstance(record, SyncOperationRecord)
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.phase, "running-code")
            self.assertEqual(record.code.wipState, "parked")

    def test_cancel_after_a_failed_proof_restores_heads_and_parked_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "other.txt").write_text("base line\n", encoding="utf-8")
            git(worktree, "add", "other.txt")
            git(worktree, "commit", "-m", "base other")
            git(fixture.code_repo, "merge", "--ff-only", "ar/sync-thing")
            self._land_already_held_blank(fixture)
            (worktree / "other.txt").write_text("base line\nparked byte\n", encoding="utf-8")
            (worktree / "third.txt").write_text("ordinary parked edit\n", encoding="utf-8")
            parked_tree = snapshot_tree(worktree)
            pre_sync = git(worktree, "rev-parse", "HEAD")
            base_other = "base line\n"

            real_apply = sync_transaction_authority.apply_parked_wip

            def apply_then_drop_byte(side):  # type: ignore[no-untyped-def]
                state, conflicts = real_apply(side)
                if side.side == "code":
                    (Path(side.worktree) / "other.txt").write_text(base_other, encoding="utf-8")
                return state, conflicts

            with mock.patch.object(
                sync_transaction_authority, "apply_parked_wip", side_effect=apply_then_drop_byte
            ):
                stopped = fixture.sync()
            self.assertEqual(stopped.payload["state"], "sync-git-proof-failed", stopped.payload)

            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), pre_sync)
            self.assertEqual(snapshot_tree(worktree), parked_tree)
            self.assertEqual(git(worktree, "stash", "list"), "")

    def test_cancel_with_later_work_saves_first_then_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "notes.txt").write_text("shared base\n", encoding="utf-8")
            git(worktree, "add", "notes.txt")
            git(worktree, "commit", "-m", "base notes")
            git(fixture.code_repo, "merge", "--ff-only", "ar/sync-thing")
            (worktree / "README.md").write_text("candidate edit\n", encoding="utf-8")
            commit_file(fixture.code_repo, "README.md", "official version")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)

            retained = fixture.sync()
            self.assertEqual(retained.payload["state"], "sync-resolution-required")

            (worktree / "notes.txt").write_text("shared base\nlater work\n", encoding="utf-8")
            (worktree / "later-notes.txt").write_text("later work\n", encoding="utf-8")
            record = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(record, SyncOperationRecord)
            generation = record.generation

            # MIK-R61 rule 12: no way out is manual. Tracked later edits and a
            # later untracked file are saved first, then the cancel completes.
            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.returncode, 0)
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            summary = str(cancelled.payload["summary"])
            self.assertIn("could not attribute", summary)
            saved = (
                fixture.contract.worktree_group
                / "scratch"
                / f"sync-cancel-generation-{generation}"
                / "code"
            )
            listed = (saved / "paths.txt").read_text(encoding="utf-8").splitlines()
            self.assertIn("notes.txt", listed)
            self.assertIn("later-notes.txt", listed)
            held = (saved / "staged.patch").read_bytes() + (saved / "unstaged.patch").read_bytes()
            self.assertIn(b"later work", held)
            mirrored = saved / "untracked" / "later-notes.txt"
            self.assertTrue(mirrored.is_file(), saved)
            self.assertEqual(mirrored.read_bytes(), b"later work\n")
            final = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(final, SyncOperationRecord)
            self.assertEqual(final.phase, "cancelled")

    def test_stash_push_with_ignore_rule_restores_staged_partition(self) -> None:
        # MIK-R61 rule 7: a stashed ignore rule can turn ignored files untracked.
        # The refusal gives the stash back with the index partition intact (the
        # staged ignore rule is staged again, not unstaged) and drops it only
        # after proving the pre-push state.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            commit_file(fixture.code_repo, "landed.txt", "landed\n")
            worktree = fixture.contract.code_worktree
            (worktree / "scratch.tmp").write_text("scratch\n", encoding="utf-8")
            (worktree / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
            git(worktree, "add", ".gitignore")
            before_index = git(worktree, "write-tree")
            self.assertEqual(git(worktree, "status", "--porcelain"), "A  .gitignore")

            parked = fixture.sync()

            self.assertEqual(parked.payload["state"], "sync-side-preflight-failed", parked.payload)
            self.assertEqual(git(worktree, "write-tree"), before_index)
            self.assertEqual(git(worktree, "status", "--porcelain"), "A  .gitignore")
            self.assertEqual(git(worktree, "stash", "list"), "")

    def test_untracked_parked_file_the_source_already_holds_finishes_the_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "draft.txt").write_text("shared draft\n", encoding="utf-8")
            git(fixture.code_repo, "checkout", "main")
            (fixture.code_repo / "draft.txt").write_text("shared draft\n", encoding="utf-8")
            git(fixture.code_repo, "add", "draft.txt")
            git(fixture.code_repo, "commit", "-m", "land the same draft")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)
            before = snapshot_tree(worktree)

            result = fixture.sync()

            self.assertEqual(result.payload["state"], "synced", result.payload)
            self.assertEqual(section(result.payload, "code")["wip"]["state"], "restored")
            self.assertEqual(snapshot_tree(worktree), before)
            self.assertEqual((worktree / "draft.txt").read_text(encoding="utf-8"), "shared draft\n")
            self.assertEqual(git(worktree, "stash", "list"), "")
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), code_tip)

    def test_parked_and_landed_deletion_finishes_the_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "victim.txt").write_text("remove me\n", encoding="utf-8")
            git(worktree, "add", "victim.txt")
            git(worktree, "commit", "-m", "base victim")
            git(fixture.code_repo, "merge", "--ff-only", "ar/sync-thing")
            code_tip = fixture.move_official_code()
            git(fixture.code_repo, "checkout", "main")
            (fixture.code_repo / "victim.txt").unlink()
            git(fixture.code_repo, "add", "victim.txt")
            git(fixture.code_repo, "commit", "-m", "official deletes victim too")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)
            (worktree / "victim.txt").unlink()
            before = snapshot_tree(worktree)

            result = fixture.sync()

            self.assertEqual(result.payload["state"], "synced", result.payload)
            self.assertEqual(section(result.payload, "code")["wip"]["state"], "restored")
            expected = dict(before)
            expected["src/new.py"] = b"VALUE = 'landed'\n"
            self.assertEqual(snapshot_tree(worktree), expected)
            self.assertFalse((worktree / "victim.txt").exists())
            self.assertEqual(git(worktree, "stash", "list"), "")
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), code_tip)

    def test_memory_side_already_held_edit_finishes_the_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            memory_worktree = fixture.contract.memory_worktree
            assert memory_worktree is not None
            base = "".join(f"guide {index:02d}\n" for index in range(1, 21))
            guide = memory_worktree / "onboarding" / "guide.md"
            guide.parent.mkdir(parents=True, exist_ok=True)
            guide.write_text(base, encoding="utf-8")
            git(memory_worktree, "add", "onboarding/guide.md")
            git(memory_worktree, "commit", "-m", "base guide")
            git(fixture.memory_repo, "merge", "--ff-only", "ar/sync-thing")
            parked = (
                base[: len("guide 01\nguide 02\n")] + "\n" + base[len("guide 01\nguide 02\n") :]
            )
            guide.write_text(parked, encoding="utf-8")
            landed = (
                base[: len("guide 01\nguide 02\n")]
                + "\n"
                + "".join(f"guide {index:02d}\n" for index in range(3, 15))
                + "".join(f"landed {index:02d}\n" for index in range(15, 21))
            )
            code_tip = fixture.move_official_code()
            git(fixture.memory_repo, "checkout", "main")
            (fixture.memory_repo / "onboarding" / "guide.md").write_text(landed, encoding="utf-8")
            git(fixture.memory_repo, "add", "onboarding/guide.md")
            git(
                fixture.memory_repo,
                "commit",
                "-m",
                render_memory_content_message("Land the blank line", code_tip),
            )
            before = snapshot_tree(memory_worktree)

            result = fixture.sync()

            self.assertEqual(result.payload["state"], "synced", result.payload)
            self.assertEqual(section(result.payload, "memory")["wip"]["state"], "restored")
            expected = dict(before)
            expected["onboarding/guide.md"] = landed.encode("utf-8")
            self.assertEqual(snapshot_tree(memory_worktree), expected)
            self.assertEqual(git(memory_worktree, "stash", "list"), "")


class SealedFindingRepairTests(unittest.TestCase):
    """L153-R1-01 through -06: independent refutation properties, repaired whole."""

    def setUp(self) -> None:
        # Fast-forwards onto converted memory run the validator (MIK-R61 rule 4).
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def test_01_later_bytes_in_unmerged_path_save_first_then_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "README.md").write_text("parked version\n", encoding="utf-8")
            commit_file(fixture.code_repo, "README.md", "landed version")
            fixture.map_official_memory(git(fixture.code_repo, "rev-parse", "main"))
            retained = fixture.sync()
            self.assertEqual(retained.payload["state"], "sync-resolution-required")
            with (worktree / "README.md").open("a", encoding="utf-8") as stream:
                stream.write("UNSTASHED LATER WORK\n")
            journal = journal_bytes(fixture)
            record = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(record, SyncOperationRecord)
            generation = record.generation

            # MIK-R61 rule 12: no way out is manual. The cancel saves the later
            # work first, then completes; nothing is refused.
            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.returncode, 0)
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            summary = str(cancelled.payload["summary"])
            self.assertIn("could not attribute", summary)
            self.assertIn("code:", summary)
            saved = (
                fixture.contract.worktree_group
                / "scratch"
                / f"sync-cancel-generation-{generation}"
                / "code"
            )
            self.assertTrue((saved / "staged.patch").is_file(), saved)
            self.assertTrue((saved / "unstaged.patch").is_file(), saved)
            listed = (saved / "paths.txt").read_text(encoding="utf-8").splitlines()
            self.assertIn("README.md", listed)
            held = (saved / "staged.patch").read_bytes() + (saved / "unstaged.patch").read_bytes()
            self.assertIn(b"UNSTASHED LATER WORK", held)
            final = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(final, SyncOperationRecord)
            self.assertEqual(final.phase, "cancelled")
            self.assertIsNotNone(journal)

            again = fixture.sync(resolution_action="cancel")
            self.assertNotEqual(again.payload["state"], "sync-cancel-refused-later-work")
            self.assertNotIn("Save those paths elsewhere", str(again.payload.get("summary", "")))

    def test_01_untouched_conflict_still_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "README.md").write_text("parked version\n", encoding="utf-8")
            commit_file(fixture.code_repo, "README.md", "landed version")
            fixture.map_official_memory(git(fixture.code_repo, "rev-parse", "main"))
            retained = fixture.sync()
            self.assertEqual(retained.payload["state"], "sync-resolution-required")

            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self.assertEqual(
                (worktree / "README.md").read_text(encoding="utf-8"), "parked version\n"
            )
            self.assertEqual(git(worktree, "stash", "list"), "")

    def test_02_restored_code_wip_returns_on_memory_stop_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            memory_worktree = fixture.contract.memory_worktree
            assert memory_worktree is not None
            (worktree / "draft.txt").write_text("parked code\n", encoding="utf-8")
            code_before = snapshot_tree(worktree)
            (memory_worktree / "README.md").write_text("parked memory\n", encoding="utf-8")
            memory_before = snapshot_tree(memory_worktree)
            fixture.move_official_code()
            commit_file(fixture.memory_repo, "README.md", "landed memory")

            stopped = fixture.sync()
            self.assertEqual(stopped.payload["state"], "sync-resolution-required")
            store = SyncOperationStore(fixture.contract.worktree_group)
            record = store.read()
            self.assertIsInstance(record, SyncOperationRecord)
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.code.wipState, "restored")
            self.assertNotEqual(git(worktree, "stash", "list"), "")

            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self.assertEqual(snapshot_tree(worktree), code_before)
            self.assertEqual(snapshot_tree(memory_worktree), memory_before)
            self.assertEqual(git(worktree, "stash", "list"), "")
            self.assertEqual(git(memory_worktree, "stash", "list"), "")

            again = fixture.sync(resolution_action="cancel")
            self.assertEqual(again.payload["state"], "sync-cancelled")

    def test_03a_missing_path_beyond_sample_fails_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            for index in range(130):
                (worktree / f"parked-{index:03}.txt").write_text(
                    f"parked {index}\n", encoding="utf-8"
                )
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)
            real_apply = sync_transaction_authority.apply_parked_wip
            calls: list[int] = []

            def reapply_then_remove(side):  # type: ignore[no-untyped-def]
                answer = real_apply(side)
                if side.side == "code":
                    calls.append(side.wipPathCount)
                    (Path(side.worktree) / "parked-129.txt").unlink()
                return answer

            with mock.patch.object(
                sync_transaction_authority, "apply_parked_wip", side_effect=reapply_then_remove
            ):
                result = fixture.sync()

            self.assertEqual(calls, [130])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "sync-git-proof-failed", result.payload)
            self.assertIn("parked-129.txt", str(result.payload["summary"]))
            self.assertNotEqual(git(worktree, "stash", "list"), "")

    def test_03c_restore_of_300_paths_with_one_differing_beyond_sample_fails_proof(
        self,
    ) -> None:
        # MIK-R61 rule 8: the proof covers the full stash inventory, never the
        # journal's path sample -- one path made to differ beyond it fails the proof
        # and keeps the stash.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            for index in range(300):
                (worktree / f"parked-{index:03}.txt").write_text(
                    f"parked {index}\n", encoding="utf-8"
                )
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)
            real_apply = sync_transaction_authority.apply_parked_wip

            def reapply_then_change_one(side):  # type: ignore[no-untyped-def]
                state, conflicts = real_apply(side)
                if side.side == "code":
                    (Path(side.worktree) / "parked-299.txt").write_text(
                        "changed after the reapply\n", encoding="utf-8"
                    )
                return state, conflicts

            with mock.patch.object(
                sync_transaction_authority, "apply_parked_wip", side_effect=reapply_then_change_one
            ):
                result = fixture.sync()

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "sync-git-proof-failed", result.payload)
            self.assertIn("parked-299.txt", str(result.payload.get("summary")))
            self.assertNotEqual(git(worktree, "stash", "list"), "")

    def test_03b_many_already_held_paths_finish_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            for index in range(130):
                (worktree / f"held-{index:03}.txt").write_text(f"held {index}\n", encoding="utf-8")
            git(fixture.code_repo, "checkout", "main")
            for index in range(130):
                (fixture.code_repo / f"held-{index:03}.txt").write_text(
                    f"held {index}\n", encoding="utf-8"
                )
            git(fixture.code_repo, "add", ".")
            git(fixture.code_repo, "commit", "-m", "land the same 130 files")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)
            before = snapshot_tree(worktree)

            result = fixture.sync()

            self.assertEqual(result.payload["state"], "synced", result.payload)
            self.assertEqual(section(result.payload, "code")["wip"]["state"], "restored")
            self.assertEqual(snapshot_tree(worktree), before)
            self.assertEqual(git(worktree, "stash", "list"), "")
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), code_tip)

    def test_04_index_only_later_bytes_save_first_then_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "notes.txt").write_text("base notes\n", encoding="utf-8")
            git(worktree, "add", "notes.txt")
            git(worktree, "commit", "-m", "base notes")
            git(fixture.code_repo, "merge", "--ff-only", "ar/sync-thing")
            (worktree / "notes.txt").write_text("parked notes\n", encoding="utf-8")
            (worktree / "README.md").write_text("parked conflict\n", encoding="utf-8")
            commit_file(fixture.code_repo, "README.md", "landed conflict")
            fixture.map_official_memory(git(fixture.code_repo, "rev-parse", "HEAD"))
            retained = fixture.sync()
            self.assertEqual(retained.payload["state"], "sync-resolution-required")
            (worktree / "notes.txt").write_text("UNSTASHED INDEX CONTENT\n", encoding="utf-8")
            git(worktree, "add", "notes.txt")
            (worktree / "notes.txt").write_text("parked notes\n", encoding="utf-8")
            record = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(record, SyncOperationRecord)
            generation = record.generation

            # MIK-R61 rule 12: staged-only later bytes are saved first, then the
            # cancel completes; nothing is refused.
            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.returncode, 0)
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            saved = (
                fixture.contract.worktree_group
                / "scratch"
                / f"sync-cancel-generation-{generation}"
                / "code"
            )
            listed = (saved / "paths.txt").read_text(encoding="utf-8").splitlines()
            self.assertIn("notes.txt", listed)
            self.assertIn(b"UNSTASHED INDEX CONTENT", (saved / "staged.patch").read_bytes())
            final = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(final, SyncOperationRecord)
            self.assertEqual(final.phase, "cancelled")

    def test_04b_staged_conflict_markers_save_first_then_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "README.md").write_text("parked version\n", encoding="utf-8")
            commit_file(fixture.code_repo, "README.md", "landed version")
            fixture.map_official_memory(git(fixture.code_repo, "rev-parse", "main"))
            retained = fixture.sync()
            self.assertEqual(retained.payload["state"], "sync-resolution-required")
            git(worktree, "add", "README.md")
            record = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(record, SyncOperationRecord)
            generation = record.generation

            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.returncode, 0)
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            saved = (
                fixture.contract.worktree_group
                / "scratch"
                / f"sync-cancel-generation-{generation}"
                / "code"
            )
            listed = (saved / "paths.txt").read_text(encoding="utf-8").splitlines()
            self.assertIn("README.md", listed)
            final = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(final, SyncOperationRecord)
            self.assertEqual(final.phase, "cancelled")

    def test_05_ordinary_merge_output_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            commit_file(worktree, "README.md", "work committed version")
            pre_sync = git(worktree, "rev-parse", "HEAD")
            before = snapshot_tree(worktree)
            commit_file(fixture.code_repo, "README.md", "source committed version")
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)
            stopped = fixture.sync()
            self.assertEqual(stopped.payload["state"], "sync-resolution-required")

            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), pre_sync)
            self.assertEqual(snapshot_tree(worktree), before)

    def test_06a_uncomputable_symlink_cancel_leads_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "link").symlink_to("README.md")
            pre_sync = git(worktree, "rev-parse", "HEAD")
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)
            stopped = fixture.sync()
            self.assertEqual(stopped.payload["state"], "sync-git-proof-failed", stopped.payload)
            self.assertIn("link", str(stopped.payload["summary"]))
            store = SyncOperationStore(fixture.contract.worktree_group)
            record = store.read()
            self.assertIsInstance(record, SyncOperationRecord)
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.phase, "running-code")
            self.assertEqual(record.code.wipState, "parked")

            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), pre_sync)
            self.assertTrue((worktree / "link").is_symlink())
            self.assertEqual(worktree.joinpath("link").readlink().as_posix(), "README.md")
            self.assertEqual(git(worktree, "stash", "list"), "")

    def test_06b_retargeted_symlink_saves_first_then_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "link").symlink_to("README.md")
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)
            stopped = fixture.sync()
            self.assertEqual(stopped.payload["state"], "sync-git-proof-failed", stopped.payload)
            (worktree / "link").unlink()
            (worktree / "link").symlink_to("draft.txt")
            record = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(record, SyncOperationRecord)
            generation = record.generation

            # MIK-R61 rule 12: the retargeted symlink is saved first (its link
            # target is mirrored), then the cancel completes.
            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.returncode, 0)
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            saved = (
                fixture.contract.worktree_group
                / "scratch"
                / f"sync-cancel-generation-{generation}"
                / "code"
            )
            listed = (saved / "paths.txt").read_text(encoding="utf-8").splitlines()
            self.assertIn("link", listed)
            final = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(final, SyncOperationRecord)
            self.assertEqual(final.phase, "cancelled")


class RoundTwoVariantTests(unittest.TestCase):
    """The round-2 fixed-list variants for L153-R1-01, -02 and -03."""

    def setUp(self) -> None:
        # Fast-forwards onto converted memory run the validator (MIK-R61 rule 4).
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def test_01b_crash_window_owner_bytes_are_never_blessed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "README.md").write_text("parked version\n")
            commit_file(fixture.code_repo, "README.md", "landed version")
            fixture.map_official_memory(git(fixture.code_repo, "rev-parse", "main"))
            crashed = mock.patch.object(
                sync_transaction_authority,
                "_retain_parked_conflict",
                side_effect=SyncGitProofError("crash before retain journal"),
            )
            with crashed as intercepted:
                stopped = fixture.sync()
            self.assertEqual(intercepted.call_count, 1)
            self.assertEqual(stopped.payload["state"], "sync-git-proof-failed", stopped.payload)
            with (worktree / "README.md").open("a") as stream:
                stream.write("UNSTASHED LATER AFTER CRASH\n")
            resumed = fixture.sync()
            store = SyncOperationStore(fixture.contract.worktree_group)
            record = store.read()
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.phase, "code-resolution-required", resumed.payload)
            generation = record.generation
            # The crash window's later bytes are saved first with the unproven
            # state, then the cancel completes; nothing is refused.
            cancelled = fixture.sync(resolution_action="cancel")
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            saved = (
                fixture.contract.worktree_group
                / "scratch"
                / f"sync-cancel-generation-{generation}"
                / "code"
            )
            held = (saved / "staged.patch").read_bytes() + (saved / "unstaged.patch").read_bytes()
            self.assertIn(b"UNSTASHED LATER AFTER CRASH", held)

    def test_01c_untouched_crash_reapply_verifies_and_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "README.md").write_text("parked version\n")
            commit_file(fixture.code_repo, "README.md", "landed version")
            fixture.map_official_memory(git(fixture.code_repo, "rev-parse", "main"))
            with mock.patch.object(
                sync_transaction_authority,
                "_retain_parked_conflict",
                side_effect=SyncGitProofError("crash before retain journal"),
            ):
                stopped = fixture.sync()
            self.assertEqual(stopped.payload["state"], "sync-git-proof-failed", stopped.payload)
            resumed = fixture.sync()
            store = SyncOperationStore(fixture.contract.worktree_group)
            record = store.read()
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.phase, "code-resolution-required", resumed.payload)
            assert record.code is not None
            self.assertTrue(record.code.retainedTreeDigests)
            cancelled = fixture.sync(resolution_action="cancel")
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self.assertEqual((worktree / "README.md").read_bytes(), b"parked version\n")
            self.assertEqual(git(worktree, "stash", "list"), "")

    def test_02b_cancel_crash_after_first_side_restore_finishes_on_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            memory = fixture.contract.memory_worktree
            assert memory is not None
            (worktree / "draft.txt").write_text("parked code\n")
            before_code = snapshot_tree(worktree)
            (memory / "README.md").write_text("parked memory\n")
            before_memory = snapshot_tree(memory)
            fixture.move_official_code()
            commit_file(fixture.memory_repo, "README.md", "landed memory")
            stopped = fixture.sync()
            self.assertEqual(stopped.payload["state"], "sync-resolution-required", stopped.payload)
            real = sync_transaction_authority.update_record
            calls: list[str] = []

            def crash_after_memory_mark(*args: object, **kwargs: object) -> object:
                side = kwargs.get("side")
                if (
                    kwargs.get("phase") == "cancelling"
                    and side is not None
                    and getattr(side, "side", None) == "memory"
                    and getattr(side, "wipState", None) == "restored"
                ):
                    calls.append(str(getattr(side, "wipStash", "")))
                    raise SyncGitProofError("crash after memory restore before journal")
                return real(*args, **kwargs)  # type: ignore[arg-type]

            with mock.patch.object(
                sync_transaction_authority, "update_record", side_effect=crash_after_memory_mark
            ):
                first = fixture.sync(resolution_action="cancel")
            self.assertEqual(len(calls), 1)
            store = SyncOperationStore(fixture.contract.worktree_group)
            record = store.read()
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(
                first.payload["state"], "sync-cancel-wip-restore-failed", first.payload
            )
            self.assertEqual(record.phase, "cancelling")
            assert record.memory is not None
            self.assertEqual(record.memory.wipState, "restore-conflict")
            second = fixture.sync(resolution_action="cancel")
            self.assertEqual(second.payload["state"], "sync-cancelled", second.payload)
            self.assertEqual(snapshot_tree(worktree), before_code)
            self.assertEqual(snapshot_tree(memory), before_memory)
            self.assertEqual(git(worktree, "stash", "list"), "")
            self.assertEqual(git(memory, "stash", "list"), "")

    def test_03b_failed_full_inventory_never_accepts_the_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            for index in range(130):
                (worktree / f"parked-{index:03}.txt").write_text(f"parked {index}\n")
            tip = fixture.move_official_code()
            fixture.map_official_memory(tip)
            real = sync_transaction_authority.apply_parked_wip
            calls: list[int] = []

            def reapply_then_remove(side: SyncSideRecord) -> tuple[str, tuple[str, ...]]:
                answer = real(side)
                if side.side == "code":
                    calls.append(side.wipPathCount)
                    (Path(side.worktree) / "parked-129.txt").unlink()
                return answer

            with (
                mock.patch.object(
                    sync_transaction_authority, "apply_parked_wip", side_effect=reapply_then_remove
                ),
                mock.patch.object(
                    sync_git_proof,
                    "stashed_parked_paths",
                    side_effect=SyncGitProofError("failed full inventory"),
                ),
            ):
                result = fixture.sync()
            self.assertEqual(calls, [130])
            self.assertNotEqual(result.payload["state"], "synced", result.payload)
            self.assertIn("inventoried", str(result.payload.get("summary")))
            self.assertFalse((worktree / "parked-129.txt").exists())
            self.assertNotEqual(git(worktree, "stash", "list"), "")


class ResumedConvertedMemoryMergeTests(unittest.TestCase):
    """Finding L153-R3-07: cancel after a resumed converted-memory merge."""

    def setUp(self) -> None:
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def _interrupted_merge(self, tmp: str) -> tuple[Lines, dict[str, bytes]]:
        lines = _converted_lines(Path(tmp))
        base = _sidecar(lines.leaf)
        lines.on_leaf({APP_SIDECAR: _edited(base, "1", note="Leaf wording.")})
        lines.on_line({APP_SIDECAR: _edited(base, "1", note="Line wording.")})
        before = snapshot_tree(lines.leaf)
        before.pop("memory.md", None)
        real = sync_transaction.update_record
        calls: list[str] = []

        def crash_before_retained_journal(*args: object, **kwargs: object) -> object:
            side = kwargs.get("side")
            if kwargs.get("phase") == "memory-resolution-required" and side is not None:
                calls.append(str(getattr(side, "side", "")))
                raise SyncGitProofError("crash before memory retain journal")
            return real(*args, **kwargs)  # type: ignore[arg-type]

        with mock.patch.object(
            sync_transaction, "update_record", side_effect=crash_before_retained_journal
        ):
            stopped = lines.merge()
        self.assertEqual(calls, ["memory"])
        self.assertEqual(stopped["state"], "sync-git-proof-failed", stopped)
        return lines, before

    def test_07a_untouched_resume_cancels_both_trees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lines, before = self._interrupted_merge(tmp)
            leaf_head = git(lines.leaf, "rev-parse", "HEAD")
            resumed = lines.merge()
            store = SyncOperationStore(lines.fixture.contract.worktree_group)
            record = store.read()
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.phase, "memory-resolution-required", resumed)
            assert record.memory is not None
            self.assertTrue(record.memory.retainedTreeDigests)
            retained_tree = snapshot_tree(lines.leaf)
            cancelled = lines.sync(resolution_action="cancel")
            self.assertEqual(cancelled["state"], "sync-cancelled", cancelled)
            after = snapshot_tree(lines.leaf)
            after.pop("memory.md", None)
            self.assertEqual(after, before)
            self.assertNotEqual(snapshot_tree(lines.leaf), retained_tree)
            self.assertEqual(git(lines.leaf, "rev-parse", "HEAD"), leaf_head)
            self.assertEqual(_sidecar(lines.leaf)["references"]["1"]["note"], "Leaf wording.")

    def test_07b_owner_write_after_interrupt_saves_first_then_cancels(
        self,
    ) -> None:
        # MIK-R61 rule 12: a cancel does not refuse over bytes it cannot attribute.
        # Where a merge is still in progress it first saves the staged state, the
        # unstaged state and the path list as files in the leaf's scratch folder,
        # names them with their digests, and then ends the sync.
        with tempfile.TemporaryDirectory() as tmp:
            lines, _ = self._interrupted_merge(tmp)
            target = lines.leaf / APP_SIDECAR
            with target.open("ab") as stream:
                stream.write(b"\nOWNER AFTER INTERRUPT\n")
            resumed = lines.merge()
            store = SyncOperationStore(lines.fixture.contract.worktree_group)
            record = store.read()
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.phase, "memory-resolution-required", resumed)
            assert record.memory is not None
            self.assertEqual(record.memory.retainedTreeDigests, {})
            generation = record.generation
            leaf_head = git(lines.leaf, "rev-parse", "HEAD")
            cancelled = lines.sync(resolution_action="cancel")
            self.assertEqual(cancelled["state"], "sync-cancelled", cancelled)
            summary = str(cancelled.get("summary"))
            self.assertIn("could not attribute", summary)
            saved = (
                lines.fixture.contract.worktree_group
                / "scratch"
                / f"sync-cancel-generation-{generation}"
                / "memory"
            )
            self.assertTrue((saved / "staged.patch").is_file(), saved)
            self.assertTrue((saved / "unstaged.patch").is_file(), saved)
            listed = (saved / "paths.txt").read_text(encoding="utf-8").splitlines()
            self.assertIn(APP_SIDECAR, listed)
            held = (saved / "staged.patch").read_bytes() + (saved / "unstaged.patch").read_bytes()
            self.assertIn(b"OWNER AFTER INTERRUPT", held)
            self.assertEqual(git(lines.leaf, "rev-parse", "HEAD"), leaf_head)
            self.assertEqual(git(lines.leaf, "stash", "list"), "")
            final = store.read()
            assert isinstance(final, SyncOperationRecord)
            self.assertEqual(final.phase, "cancelled")

    def test_07c_identical_bytes_carried_line_measures_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lines = _converted_lines(Path(tmp))
            code_worktree = lines.fixture.contract.code_worktree
            assert code_worktree is not None
            (code_worktree / "same.py").write_text("SAME = 1\n", encoding="utf-8")
            contract, _reviewed = _record_reviewed_pair(lines)
            config = _review_sync_config(lines)
            target = lines.fixture.code_repo / "same.py"
            target.write_text("SAME = 1\n", encoding="utf-8")
            git(lines.fixture.code_repo, "add", "-A")
            git(lines.fixture.code_repo, "commit", "-q", "-m", "landed same bytes")
            result = worktree_tools.worktree_sync_tool(
                config,
                contract_path=contract.contract_path.as_posix(),
                memory_sync_choice="merge-memory",
            )
            block = result["review_rebinding"]
            self.assertEqual(block["state"], "current", block)


class InterruptedCodeMergeCancelTests(unittest.TestCase):
    """MIK-R61 rule 12: a sync stopped in its code merge always ends through the product."""

    def setUp(self) -> None:
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def _interrupted_code_merge(self, tmp: str) -> SyncFixture:
        """A code merge conflict retained with no reference map, as after a crash."""
        fixture = SyncFixture(Path(tmp))
        worktree = fixture.contract.code_worktree
        commit_file(worktree, "README.md", "work-branch version")
        commit_file(fixture.code_repo, "README.md", "official version")
        code_tip = git(fixture.code_repo, "rev-parse", "main")
        fixture.map_official_memory(code_tip)
        real = sync_transaction.update_record

        def crash_before_retain(*args: object, **kwargs: object) -> object:
            if kwargs.get("phase") == "code-resolution-required" and kwargs.get("side") is not None:
                raise SyncGitProofError("crash before code retain journal")
            return real(*args, **kwargs)  # type: ignore[arg-type]

        with mock.patch.object(sync_transaction, "update_record", side_effect=crash_before_retain):
            stopped = fixture.sync()
        self.assertEqual(stopped.payload["state"], "sync-git-proof-failed", stopped.payload)
        return fixture

    def _retained_code_conflict(self, tmp: str) -> SyncFixture:
        """A code merge conflict retained with its reference map, no crash involved."""
        fixture = SyncFixture(Path(tmp))
        worktree = fixture.contract.code_worktree
        commit_file(worktree, "README.md", "work-branch version")
        commit_file(fixture.code_repo, "README.md", "official version")
        code_tip = git(fixture.code_repo, "rev-parse", "main")
        fixture.map_official_memory(code_tip)
        result = fixture.sync()
        self.assertEqual(result.payload["state"], "sync-resolution-required", result.payload)
        return fixture

    def test_second_cancel_finishes_an_interrupted_cancel(self) -> None:
        # MIK-R61 rule 12: a cancel an interruption cut short is finished by a second
        # cancel; each step reads the journal and the repository and does only what
        # is still missing.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._interrupted_code_merge(tmp)
            worktree = fixture.contract.code_worktree
            leaf_head = git(worktree, "rev-parse", "HEAD")
            real = sync_transaction.update_record

            def crash_in_cancelling(*args: object, **kwargs: object) -> object:
                if kwargs.get("phase") == "cancelling":
                    raise SyncGitProofError("crash inside cancel")
                return real(*args, **kwargs)  # type: ignore[arg-type]

            with mock.patch.object(
                sync_transaction_recovery, "update_record", side_effect=crash_in_cancelling
            ):
                killed = fixture.sync(resolution_action="cancel")
            self.assertEqual(killed.payload["state"], "sync-operation-refused", killed.payload)
            cancelled = fixture.sync(resolution_action="cancel")
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), leaf_head)
            self.assertEqual(git(worktree, "status", "--porcelain"), "")
            self.assertEqual(git(worktree, "stash", "list"), "")

    def test_cancel_with_unwritable_scratch_refuses_unchanged(self) -> None:
        # MIK-R61 rule 12 failure behaviour: the one refusal of a cancel that leaves
        # the sync open names the path it could not write and the folder to free,
        # and changes nothing.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._interrupted_code_merge(tmp)
            worktree = fixture.contract.code_worktree
            store = SyncOperationStore(fixture.contract.worktree_group)
            record = store.read()
            assert isinstance(record, SyncOperationRecord)
            generation = record.generation
            block = (
                fixture.contract.worktree_group
                / "scratch"
                / f"sync-cancel-generation-{generation}"
                / "code"
            )
            block.parent.mkdir(parents=True, exist_ok=True)
            block.write_text("blocking the save\n", encoding="utf-8")
            journal = journal_bytes(fixture)
            before = snapshot_tree(worktree)
            refused = fixture.sync(resolution_action="cancel")
            self.assertEqual(refused.payload["state"], "sync-cancel-save-failed", refused.payload)
            summary = str(refused.payload.get("summary"))
            self.assertIn(block.as_posix(), summary)
            self.assertIn("scratch", summary)
            self.assertIn("resolution_action='cancel'", summary)
            self.assertEqual(journal_bytes(fixture), journal)
            self.assertEqual(snapshot_tree(worktree), before)

    def test_continue_with_unstaged_changes_names_continue_and_cancel(self) -> None:
        # MIK-R61 rule 12: no answer of continue leaves the sync open without naming
        # the worktree_sync action that ends it.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._retained_code_conflict(tmp)
            worktree = fixture.contract.code_worktree
            (worktree / "README.md").write_text("resolved version\n", encoding="utf-8")
            git(worktree, "add", "README.md")
            with (worktree / "README.md").open("ab") as stream:
                stream.write(b"unstaged resolution\n")
            stopped = fixture.sync(resolution_action="continue")
            self.assertEqual(
                stopped.payload["state"], "sync-resolution-incomplete", stopped.payload
            )
            summary = str(stopped.payload.get("summary"))
            self.assertIn("unstaged changes", summary)
            self.assertIn("resolution_action='continue'", summary)
            self.assertIn("resolution_action='cancel'", summary)
            record = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.phase, "code-resolution-required")

    def test_continue_onto_a_moved_line_is_refused_for_cancel(self) -> None:
        # MIK-R61 rule 12: a sync journaled onto a tip the line left behind is ended
        # with cancel, not continued onto it.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._retained_code_conflict(tmp)
            commit_file(fixture.code_repo, "other.py", "the line moved on")
            refused = fixture.sync(resolution_action="continue")
            self.assertEqual(refused.payload["state"], "sync-source-moved", refused.payload)
            summary = str(refused.payload.get("summary"))
            self.assertIn("resolution_action='cancel'", summary)
            self.assertIn("not continued", summary)
            record = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.phase, "code-resolution-required")

    def test_continue_stopped_at_commit_check_names_files_and_cancel(self) -> None:
        # MIK-R61 rule 12: when continue stops at the commit check on the merged tree,
        # the answer says for each file whether the task changed it, and names cancel.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            commit_file(worktree, "README.md", "work-branch version")
            (worktree / "README.md").write_text(
                "work-branch version\nparked tweak\n", encoding="utf-8"
            )
            commit_file(fixture.code_repo, "README.md", "official version")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)
            hook = fixture.code_repo / ".git" / "hooks" / "pre-commit"
            hook.write_text(
                "#!/bin/sh\necho 'pre-commit: ruff failed on README.md'\nexit 1\n", encoding="utf-8"
            )
            hook.chmod(0o755)
            result = fixture.sync()
            self.assertEqual(result.payload["state"], "sync-resolution-required", result.payload)
            (worktree / "README.md").write_text(
                "work-branch version\nofficial version\nparked tweak\n", encoding="utf-8"
            )
            git(worktree, "add", "README.md")
            stopped = fixture.sync(resolution_action="continue")
            self.assertEqual(stopped.payload["state"], "sync-commit-check-failed", stopped.payload)
            summary = str(stopped.payload.get("summary"))
            self.assertIn("README.md", summary)
            self.assertIn("changed by the task", summary)
            self.assertIn("resolution_action='cancel'", summary)
            self.assertIn("resolution_action='continue'", summary)
            record = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.phase, "code-resolution-required")
            hook.unlink()
            resumed = fixture.sync(resolution_action="continue")
            # The merge commit landed; the parked candidate reapply still conflicts,
            # so one more resolve-and-continue round settles it.
            self.assertEqual(resumed.payload["state"], "sync-resolution-required", resumed.payload)
            self.assertTrue(
                section(resumed.payload, "resolution").get("wipRestore"), resumed.payload
            )
            (worktree / "README.md").write_text(
                "work-branch version\nofficial version\nparked tweak\n", encoding="utf-8"
            )
            git(worktree, "add", "README.md")
            landed = fixture.sync(resolution_action="continue")
            self.assertEqual(landed.payload["state"], "synced", landed.payload)

    def test_retired_knowledge_in_flight_refuses_continue_but_cancels(self) -> None:
        # MIK-R61 rule 6: a journal holding a non-empty database conflict refuses a
        # continued sync and names cancel; cancelling works; empty values are dropped.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            commit_file(worktree, "README.md", "work-branch version")
            commit_file(fixture.code_repo, "README.md", "official version")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)
            result = fixture.sync()
            self.assertEqual(result.payload["state"], "sync-resolution-required", result.payload)
            store = SyncOperationStore(fixture.contract.worktree_group)
            record = store.read()
            assert isinstance(record, SyncOperationRecord)
            assert record.code is not None
            elder = record.model_copy(
                update={
                    "code": record.code.model_copy(
                        update={
                            "knowledgeConflict": {"database": "old conflict"},
                            "knowledgeReconciliations": [],
                        }
                    )
                }
            )
            store.write(elder)
            reread = store.read()
            assert isinstance(reread, SyncOperationRecord)
            assert reread.code is not None
            self.assertEqual(reread.code.knowledgeConflict, {"database": "old conflict"})
            self.assertIsNone(reread.code.knowledgeReconciliations)
            refused = fixture.sync(resolution_action="continue")
            self.assertEqual(
                refused.payload["state"], "sync-retired-knowledge-in-flight", refused.payload
            )
            self.assertIn("resolution_action='cancel'", str(refused.payload.get("summary")))
            cancelled = fixture.sync(resolution_action="cancel")
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)


class RenamedParkedEditTests(unittest.TestCase):
    """MIK-R61 rule 13: a parked edit follows a rename of the carried line."""

    def setUp(self) -> None:
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def test_parked_edit_follows_rename_to_new_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "README.md").write_text("# Fixture\nparked line\n", encoding="utf-8")
            git(fixture.code_repo, "mv", "README.md", "renamed.md")
            git(fixture.code_repo, "commit", "-q", "-m", "rename readme")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)
            result = fixture.sync()
            self.assertEqual(result.payload["state"], "synced", result.payload)
            self.assertFalse((worktree / "README.md").exists())
            self.assertEqual(
                (worktree / "renamed.md").read_text(encoding="utf-8"),
                "# Fixture\nparked line\n",
            )
            self.assertEqual(git(worktree, "stash", "list"), "")

    def test_cancel_with_staged_later_work_on_new_name_saves_and_names_it(self) -> None:
        # MIK-R61 rule 13: later work on the new name, also when only staged, is
        # still saved and named; the cancel takes the new name as the transaction's
        # own and ends the sync.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "README.md").write_text("# Fixture\nparked line\n", encoding="utf-8")
            git(fixture.code_repo, "mv", "README.md", "renamed.md")
            # A short carried edit keeps the rename above git's similarity bar, so the
            # proof and the cancel read it as a rename of the parked file.
            (fixture.code_repo / "renamed.md").write_text("# Fixture\nok\n", encoding="utf-8")
            git(fixture.code_repo, "add", "-A")
            git(fixture.code_repo, "commit", "-q", "-m", "rename and edit readme")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)
            result = fixture.sync()
            self.assertEqual(result.payload["state"], "sync-resolution-required", result.payload)
            with (worktree / "renamed.md").open("ab") as stream:
                stream.write(b"owner later line\n")
            git(worktree, "add", "renamed.md")
            store = SyncOperationStore(fixture.contract.worktree_group)
            record = store.read()
            assert isinstance(record, SyncOperationRecord)
            generation = record.generation
            cancelled = fixture.sync(resolution_action="cancel")
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            summary = str(cancelled.payload.get("summary"))
            self.assertIn("could not attribute", summary)
            saved = (
                fixture.contract.worktree_group
                / "scratch"
                / f"sync-cancel-generation-{generation}"
                / "code"
            )
            listed = (saved / "paths.txt").read_text(encoding="utf-8").splitlines()
            self.assertIn("renamed.md", listed)
            held = (saved / "staged.patch").read_bytes() + (saved / "unstaged.patch").read_bytes()
            self.assertIn(b"owner later line", held)
            self.assertEqual(
                (worktree / "README.md").read_text(encoding="utf-8"),
                "# Fixture\nparked line\n",
            )
            self.assertFalse((worktree / "renamed.md").exists())
            self.assertEqual(git(worktree, "stash", "list"), "")

    def test_cancel_with_later_and_unproven_saves_both_then_cancels(self) -> None:
        # MIK-R61 rule 12: where later work stands beside unproven paths the cancel
        # saves both first, then completes; nothing is refused and no path is
        # claimed for the transaction.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "README.md").write_text("# Fixture\nparked line\n", encoding="utf-8")
            git(fixture.code_repo, "mv", "README.md", "renamed.md")
            (fixture.code_repo / "renamed.md").write_text("# Fixture\nok\n", encoding="utf-8")
            git(fixture.code_repo, "add", "-A")
            git(fixture.code_repo, "commit", "-q", "-m", "rename and edit readme")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)
            result = fixture.sync()
            self.assertEqual(result.payload["state"], "sync-resolution-required", result.payload)
            with (worktree / "renamed.md").open("ab") as stream:
                stream.write(b"owner later line\n")
            (worktree / "foreign.txt").write_text("not the transaction's\n", encoding="utf-8")
            store = SyncOperationStore(fixture.contract.worktree_group)
            record = store.read()
            assert isinstance(record, SyncOperationRecord)
            generation = record.generation
            cancelled = fixture.sync(resolution_action="cancel")
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            summary = str(cancelled.payload.get("summary"))
            self.assertIn("could not attribute", summary)
            saved = (
                fixture.contract.worktree_group
                / "scratch"
                / f"sync-cancel-generation-{generation}"
                / "code"
            )
            listed = (saved / "paths.txt").read_text(encoding="utf-8").splitlines()
            self.assertIn("renamed.md", listed)
            self.assertIn("foreign.txt", listed)
            held = (saved / "staged.patch").read_bytes() + (saved / "unstaged.patch").read_bytes()
            self.assertIn(b"owner later line", held)
            mirrored = saved / "untracked" / "foreign.txt"
            self.assertTrue(mirrored.is_file(), saved)
            self.assertEqual(mirrored.read_bytes(), b"not the transaction's\n")
            final = store.read()
            assert isinstance(final, SyncOperationRecord)
            self.assertEqual(final.phase, "cancelled")


class KilledProcessCancelTests(unittest.TestCase):
    """MIK-R61 rule 12: real process death between durable writes recovers through the product.

    Every window uses the native product and Git, with no function replaced.
    The parent forks a child in its own process group and SIGKILLs the group
    at the named durable state. Journal-phase cuts stop at a filesystem audit
    event; partial restores stop an identity Git filter before the second
    path is returned. Those holds cannot close while the parent is delayed.
    Other cuts poll on-disk state and fail or retry if the window is missed.
    Recovery uses the product surface, never repairs state by hand.

    Death-window table (journal writes bracketing each window name the exact
    update_record sites in sync_transaction.py / authority / recovery):

    - W1 death before any stash push (no journal write yet): no journal, no
      stash, heads/trees/status unchanged; the rerun is a fresh admission.
      Arming: pipe handshake, kill microseconds into admission.
      Node: test_killed_before_park_reruns_fresh.
    - W2 death after the code stash push before the admission journal write:
      an orphan stash no record names; the rerun adopts it (clean tree, exact
      contract message, stash parent is the pinned head) and retains.
      Arming: poll the stash list, assert the journal is still absent.
      Node: test_killed_after_park_adopts_orphan_and_completes.
    - W2b orphan that cannot be adopted (new dirt over it): the rerun refuses
      sync-orphaned-parked-wip naming the entry and its recovery; nothing moves.
      Arming: as W2.
      Node: test_orphan_with_new_dirt_refuses_naming_it.
    - W3 death with a retained merge journaled: cancel saves first (later work
      included) and completes; the save names every file with digests.
      Arming: stop at the legacy-journal unlink audit event after the native
      journal replacement records phase code-resolution-required.
      Node: test_killed_during_retained_merge_cancels_save_first.
    - W3b death mid-restore (some parked files back, the kept stash still
      listed): the rerun recovers with no loss — restored whole where the
      kill landed late, a clean proof failure naming the kept stash (then
      cancel restores it whole), or the retained conflict; every parked
      byte survives in the worktree or that stash.
      Arming: an identity smudge filter holds the second bulk path; native
      Git has restored the first path and cannot finish until released.
      Node: test_killed_mid_restore_recovers_without_loss.
    - W4 death inside the cancel save: the rerun save overwrites from scratch
      (atomic temp+replace; the orphan temp is reclaimed) and the cancel
      completes; saved patches verify whole by digest.
      Arming: poll the save directory for the atomic-write temp.
      Node: test_killed_during_save_second_cancel_completes.
    - W5 death after the terminal journal write: the authority may be gone;
      a further cancel answers terminally without manual repair.
      Arming: the same post-replacement audit hold, for phase cancelled.
      Node: test_killed_after_terminal_cancel_answers_terminally.
    - W4b/W5b death in the completion tail after the terminal journal write
      (residue steps still in flight): the rerun's residue cleanup is
      idempotent, the bases advance exactly once (one sync-log entry), and
      the terminal replays without moving anything — continue recovers the
      completed result, cancel answers sync-already-completed. (The
      write-contract/write-terminal pair inside finalization has no
      load-independent arm — microseconds with no on-disk marker — so the
      tail test proves exactly-once past that pair instead of inside it.)
      Arming: poll the journal for phase completed while the child lives.
      Node: test_killed_in_completion_tail_replays_completed_once.
    - W-FF death after a memory fast-forward moved its ref while the parked
      restore is still in flight: the rerun adopts the moved ref (reflog
      proves the single move — it is never repeated) and finishes; every
      parked byte survives. A refused incoming tree still moves no ref
      (pinned by the validator-route tests).
      Arming: poll the restored-file count into the open interval while the
      child lives.
      Node: test_killed_after_fast_forward_move_completes_without_removing.
    - W2m death after the memory stash push before the admission journal
      write: the memory orphan is adopted like the code one and the sync
      retains; both orphans journaled, neither lost.
      Node: test_killed_after_memory_park_adopts_orphan_and_retains.
    - L111 killed inside the merge, then the source moves, then continue,
      then cancel: the rerun resumes to the retained merge; continue answers
      sync-source-moved (the stale line is judged first, the failing hook is
      never consulted); cancel saves the staged resolution first and
      completes whole by digest.
      Arming: poll the worktree gitdir for MERGE_HEAD while the child lives
      (a wide conflicting file keeps the merge in flight).
      Node: test_killed_inside_merge_source_moved_continue_then_cancel_saves.
    """

    def setUp(self) -> None:
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def _fork_sync_child(self, target, *, audit=None) -> int:
        """Fork a child in its own process group running target; return its pid."""
        pid = os.fork()
        if pid == 0:
            try:
                os.setsid()
                if audit is not None:
                    sys.addaudithook(audit)
                target()
            except BaseException:
                pass
            finally:
                os._exit(42)
        return pid

    def _kill_group(self, pid: int) -> None:
        """SIGKILL the child's whole process group; assert real signal death."""
        os.killpg(pid, signal.SIGKILL)
        _, status = os.waitpid(pid, 0)
        self.assertTrue(os.WIFSIGNALED(status), status)
        self.assertEqual(os.WTERMSIG(status), signal.SIGKILL, status)

    def _poll(self, predicate, timeout_s: float, what: str) -> None:
        """Poll predicate until true; fail loudly naming what never appeared."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except Exception:
                pass
            time.sleep(0.001)
        self.fail(f"timed out waiting for {what}")

    def _child_alive(self, pid: int) -> bool:
        with suppress(ProcessLookupError):
            try:
                os.kill(pid, 0)
            except PermissionError:
                return True
            return True
        return False

    def _journal(self, fixture: SyncFixture):
        return SyncOperationStore(fixture.contract.worktree_group).read()

    def _window_open(self, poll_true, alive_pid: int, timeout_s: float) -> bool:
        """Poll until poll_true; True only if it opened while the child lived."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                opened = poll_true()
            except Exception:
                opened = False
            if opened:
                return self._child_alive(alive_pid)
            if not self._child_alive(alive_pid):
                return False
            time.sleep(0.001)
        return False

    def _reap(self, pid: int) -> None:
        with suppress(ProcessLookupError, ChildProcessError):
            with suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
            _, _ = os.waitpid(pid, 0)

    def _kill_after_phase(self, tmp_holder: list, phase: str, build, target) -> SyncFixture:
        """Hold the native child immediately after the requested journal is durable."""
        holder = tempfile.TemporaryDirectory()
        tmp_holder.append(holder)
        fixture = build(holder.name)
        store = SyncOperationStore(fixture.contract.worktree_group)

        def pause(event: str, args: tuple) -> None:
            # write() replaces the journal before retiring its legacy copy. This
            # audit event runs before that unlink; the phase is already on disk.
            if event == "os.remove" and Path(args[0]) == store.legacy_path:
                record = store.read()
                if isinstance(record, SyncOperationRecord) and record.phase == phase:
                    os.kill(os.getpid(), signal.SIGSTOP)

        pid = self._fork_sync_child(lambda: target(fixture), audit=pause)
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                waited, status = os.waitpid(pid, os.WNOHANG | os.WUNTRACED)
                if waited:
                    self.assertTrue(os.WIFSTOPPED(status), (phase, status))
                    self.assertEqual(os.WSTOPSIG(status), signal.SIGSTOP)
                    break
                time.sleep(0.001)
            else:
                self.fail(f"native journal checkpoint {phase} was not reached")
            self._kill_group(pid)
            record = self._journal(fixture)
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.phase, phase)
            return fixture
        finally:
            self._reap(pid)

    def _conflicted_code_fixture(self, tmp: str):
        fixture = SyncFixture(Path(tmp))
        worktree = fixture.contract.code_worktree
        (worktree / "README.md").write_text("parked version\n", encoding="utf-8")
        commit_file(fixture.code_repo, "README.md", "landed version")
        fixture.map_official_memory(git(fixture.code_repo, "rev-parse", "main"))
        return fixture

    def _retained_code_fixture(self, tmp: str):
        fixture = self._conflicted_code_fixture(tmp)
        retained = fixture.sync()
        self.assertEqual(retained.payload["state"], "sync-resolution-required", retained.payload)
        return fixture

    def test_killed_before_park_reruns_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._conflicted_code_fixture(tmp)
            worktree = fixture.contract.code_worktree
            head_before = git(worktree, "rev-parse", "HEAD")
            tree_before = git(worktree, "write-tree")
            status_before = git(worktree, "status", "--porcelain")
            read_fd, write_fd = os.pipe()
            pid = os.fork()
            if pid == 0:
                try:
                    os.setsid()
                    os.close(read_fd)
                    os.write(write_fd, b"go")
                    os.close(write_fd)
                    fixture.sync()
                except BaseException:
                    pass
                finally:
                    os._exit(42)
            os.close(write_fd)
            self.assertEqual(os.read(read_fd, 2), b"go")
            os.close(read_fd)
            # The child is microseconds into admission: no git subprocess could
            # have finished a stash push. The kill lands before any durable
            # write; the assertions below fail loudly if the race is ever lost.
            os.killpg(pid, signal.SIGKILL)
            _, status = os.waitpid(pid, 0)
            self.assertTrue(os.WIFSIGNALED(status), status)
            # No journal, no stash, nothing moved: the rerun admits fresh.
            self.assertEqual(git(worktree, "stash", "list"), "")
            self.assertIsNone(self._journal(fixture))
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), head_before)
            self.assertEqual(git(worktree, "write-tree"), tree_before)
            self.assertEqual(git(worktree, "status", "--porcelain"), status_before)
            rerun = fixture.sync()
            self.assertEqual(rerun.payload["state"], "sync-resolution-required", rerun.payload)

    def _kill_between_push_and_journal(self, tmp_holder: list) -> SyncFixture:
        """Fork a syncing child per attempt; return a fixture killed in the orphan window.

        The window opens when the park entry appears on the stash stack and
        closes when the admission journal write lands. A missed window (journal
        already written, or child exited) reaps the child and retries on a
        fresh fixture; exhaustion fails loudly.

        The poll requires the worktree clean as well as the entry present: git
        publishes the stash ref before it resets the worktree, so entry-only
        polling can catch the push mid-flight (dirty tree, no journal), which
        is not the orphan window.
        """

        for _ in range(3):
            holder = tempfile.TemporaryDirectory()
            tmp_holder.append(holder)
            fixture = self._conflicted_code_fixture(holder.name)
            worktree = fixture.contract.code_worktree
            pid = self._fork_sync_child(fixture.sync)
            try:
                self._poll(
                    lambda worktree=worktree: (
                        "parked code WIP" in git(worktree, "stash", "list")
                        and git(worktree, "status", "--porcelain") == ""
                    ),
                    60,
                    "the orphan park",
                )
            except AssertionError:
                _, _ = os.waitpid(pid, 0)
                continue
            if self._journal(fixture) is not None:
                with suppress(ProcessLookupError, ChildProcessError):
                    os.killpg(pid, signal.SIGKILL)
                    _, _ = os.waitpid(pid, 0)
                continue
            self._kill_group(pid)
            self.assertIsNone(self._journal(fixture))
            return fixture
        self.fail("missed the push-to-journal window three times")

    def test_killed_after_park_adopts_orphan_and_completes(self) -> None:
        holders: list = []
        fixture = self._kill_between_push_and_journal(holders)
        try:
            worktree = fixture.contract.code_worktree
            # The orphan stash holds the candidate; the worktree is clean.
            orphans = git(worktree, "stash", "list")
            self.assertIn("parked code WIP", orphans)
            self.assertEqual(git(worktree, "status", "--porcelain"), "")
            # The rerun adopts the orphan (clean tree, exact message, pinned
            # parent) and the retained merge completes the same way; the adopted
            # stash stays journaled on the stack until it is restored.
            rerun = fixture.sync()
            self.assertEqual(rerun.payload["state"], "sync-resolution-required", rerun.payload)
            record = self._journal(fixture)
            assert isinstance(record, SyncOperationRecord)
            assert record.code.wipStash, record.code
            adopted = git(worktree, "stash", "list")
            self.assertIn("parked code WIP", adopted)
            self.assertIn(b"parked version", (worktree / "README.md").read_bytes())
        finally:
            for holder in holders:
                holder.cleanup()

    def test_orphan_with_new_dirt_refuses_naming_it(self) -> None:
        holders: list = []
        fixture = self._kill_between_push_and_journal(holders)
        try:
            worktree = fixture.contract.code_worktree
            # New dirt over the orphan keeps it unadoptable: the rerun refuses
            # sync-orphaned-parked-wip, naming the entry and the rerun that
            # heals it; nothing moves.
            head_before = git(worktree, "rev-parse", "HEAD")
            (worktree / "new-dirt.txt").write_text("after the death\n", encoding="utf-8")
            refused = fixture.sync()
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(refused.payload["state"], "sync-orphaned-parked-wip", refused.payload)
            summary = str(refused.payload["summary"])
            self.assertIn("stash", summary)
            self.assertIn("worktree_sync again", summary)
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), head_before)
            self.assertIn("parked code WIP", git(worktree, "stash", "list"))
        finally:
            for holder in holders:
                holder.cleanup()

    def test_killed_during_retained_merge_cancels_save_first(self) -> None:
        holders: list = []
        fixture = self._kill_after_phase(
            holders,
            "code-resolution-required",
            self._conflicted_code_fixture,
            lambda fixture: fixture.sync(),
        )
        try:
            worktree = fixture.contract.code_worktree
            # The journal stands at the retained merge; later work arrives after
            # the death and the cancel saves it first, then completes. The
            # cancel restores the pre-sync heads by design, so the head after
            # the cancel equals the journal's pinned pre-sync head.
            (worktree / "README.md").write_text("parked version\nlater after death\n")
            cancelled = fixture.sync(resolution_action="cancel")
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            record = self._journal(fixture)
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.phase, "cancelled")
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), record.code.preSyncHead)
            saved = (
                fixture.contract.worktree_group
                / "scratch"
                / f"sync-cancel-generation-{record.generation}"
                / "code"
            )
            staged = (saved / "staged.patch").read_bytes()
            unstaged = (saved / "unstaged.patch").read_bytes()
            self.assertIn(b"later after death", staged + unstaged)
            listed = (saved / "paths.txt").read_text(encoding="utf-8").splitlines()
            self.assertIn("README.md", listed)
            summary = str(cancelled.payload["summary"])
            self.assertIn(f"(sha256:{hashlib.sha256(staged).hexdigest()})", summary)
            self.assertIn("sha256:", summary)
        finally:
            for holder in holders:
                holder.cleanup()

    def test_killed_during_save_second_cancel_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._retained_code_fixture(tmp)
            worktree = fixture.contract.code_worktree
            big = b"\0" * 60_000_000
            (worktree / "big.bin").write_bytes(big)
            (worktree / "README.md").write_text("parked version\nlater work\n")
            pid = self._fork_sync_child(lambda: fixture.sync(resolution_action="cancel"))
            save_root = fixture.contract.worktree_group / "scratch"
            # The atomic save publishes through a hidden temp beside its
            # destination; the 60MB mirror keeps that temp in flight long
            # enough to kill inside the write with no code replaced.
            self._poll(
                lambda: (
                    list(save_root.glob("sync-cancel-generation-*/code/untracked/.big.bin.*.tmp"))
                    != []
                ),
                120,
                "the save temp",
            )
            self._kill_group(pid)
            # The orphan temp is debris; the rerun reclaims it, rewrites the
            # save whole, and the cancel completes.
            cancelled = fixture.sync(resolution_action="cancel")
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            record = self._journal(fixture)
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.phase, "cancelled")
            saved = save_root / f"sync-cancel-generation-{record.generation}" / "code"
            for name in ("staged.patch", "unstaged.patch", "paths.txt"):
                self.assertTrue((saved / name).is_file(), name)
            self.assertEqual(list(saved.rglob(".*.tmp")), [])
            staged = (saved / "staged.patch").read_bytes()
            unstaged = (saved / "unstaged.patch").read_bytes()
            self.assertIn(b"later work", staged + unstaged)
            mirrored = (saved / "untracked" / "big.bin").read_bytes()
            self.assertEqual(hashlib.sha256(mirrored).hexdigest(), hashlib.sha256(big).hexdigest())
            summary = str(cancelled.payload["summary"])
            self.assertIn(f"(sha256:{hashlib.sha256(mirrored).hexdigest()})", summary)

    def test_killed_after_terminal_cancel_answers_terminally(self) -> None:
        holders: list = []
        fixture = self._kill_after_phase(
            holders,
            "cancelled",
            self._retained_code_fixture,
            lambda fixture: fixture.sync(resolution_action="cancel"),
        )
        try:
            # The authority may be gone with the terminal phase; a further
            # cancel answers terminally without manual repair.
            again = fixture.sync(resolution_action="cancel")
            self.assertEqual(again.payload["state"], "sync-cancelled", again.payload)
            self.assertIn("already cancelled", str(again.payload["summary"]))
            self.assertNotIn(
                "manual",
                str(again.payload.get("summary", "")).lower(),
            )
            self.assertNotEqual(again.payload["state"], "sync-cancel-save-failed")
        finally:
            for holder in holders:
                holder.cleanup()

    def _bulk_restore_fixture(self, tmp: str, *, side: str = "code", count: int = 2000):
        """A clean-merge setup whose parked WIP is thousands of untracked files.

        The official line moves an unrelated file, so the merge (or
        fast-forward) is clean. The identity filter below holds its native
        restore at a strictly partial state. Returns the fixture and the exact
        expected bytes, so the rerun proves no loss byte by byte.
        """

        fixture = SyncFixture(Path(tmp))
        expected: dict[str, bytes] = {}
        if side == "code":
            worktree = fixture.contract.code_worktree
            repo = fixture.code_repo
        else:
            memory_worktree = fixture.contract.memory_worktree
            assert memory_worktree is not None
            worktree = memory_worktree
            repo = fixture.memory_repo
        for i in range(count):
            content = f"parked bulk {i}\n".encode()
            expected[f"bulk-{i:05d}.txt"] = content
            (worktree / f"bulk-{i:05d}.txt").write_bytes(content)
        commit_file(repo, "other.txt", "the official line moved elsewhere")
        code_tip = git(fixture.code_repo, "rev-parse", "main")
        fixture.map_official_memory(code_tip)
        return fixture, expected

    def _restored_count(self, worktree: Path, total: int) -> int:
        present = 0
        for i in range(total):
            if (worktree / f"bulk-{i:05d}.txt").is_file():
                present += 1
        return present

    def _kill_mid_restore(self, tmp_holder: list, build, side: str = "code"):
        """Kill native Git's restore while an identity filter holds its second path.

        Serial checkout restores the first bulk path before calling this fixture's
        filter for the second. The filter acknowledges the checkpoint and stops
        before returning bytes. Git and the sync child remain alive waiting for it,
        so no parent scheduling delay can turn the partial count into a full restore.
        Its marker makes the filter pass bytes unchanged on the public recovery.
        """
        holder = tempfile.TemporaryDirectory()
        tmp_holder.append(holder)
        fixture, expected = build(holder.name)
        worktree = (
            fixture.contract.code_worktree if side == "code" else fixture.contract.memory_worktree
        )
        assert worktree is not None
        marker = fixture.contract.worktree_group / "restore-ready"
        script = fixture.contract.worktree_group / "restore-filter.py"
        script.write_text(
            "from pathlib import Path\n"
            "import os, signal, sys\n"
            "content = sys.stdin.buffer.read()\n"
            "marker = Path(sys.argv[1])\n"
            "if not marker.exists():\n"
            "    marker.write_text('ready')\n"
            "    os.kill(os.getpid(), signal.SIGSTOP)\n"
            "sys.stdout.buffer.write(content)\n",
            encoding="utf-8",
        )
        attributes = Path(git(worktree, "rev-parse", "--git-path", "info/attributes"))
        if not attributes.is_absolute():
            attributes = worktree / attributes
        attributes.parent.mkdir(parents=True, exist_ok=True)
        attributes.write_text("bulk-00001.txt filter=l61-restore-checkpoint\n", encoding="utf-8")
        git(
            worktree,
            "config",
            "filter.l61-restore-checkpoint.smudge",
            shlex.join([sys.executable, str(script), str(marker)]),
        )
        git(worktree, "config", "filter.l61-restore-checkpoint.clean", "cat")
        git(worktree, "config", "filter.l61-restore-checkpoint.required", "true")
        git(worktree, "config", "checkout.workers", "1")
        pid = self._fork_sync_child(fixture.sync)
        try:
            self._poll(
                lambda: marker.is_file() and marker.read_bytes() == b"ready",
                120,
                "native second-path restore checkpoint",
            )
            self._kill_group(pid)
            return fixture, expected
        finally:
            self._reap(pid)

    def _assert_no_bulk_loss(
        self, worktree: Path, expected: dict[str, bytes], stash_entry: str
    ) -> None:
        """Every parked byte is in the worktree or in the kept stash's tree.

        Worktree files compare by bytes; absentees must sit in the kept
        stash's untracked tree with the blob identity git recorded — one
        ls-tree for the whole stash, blob headers hashed in Python, so the
        proof costs one subprocess however many files the kill stranded.
        """

        absent = [
            name
            for name in expected
            if not (worktree / name).is_file() or (worktree / name).read_bytes() != expected[name]
        ]
        if not absent:
            return
        stashes = git(worktree, "stash", "list")
        self.assertIn(stash_entry, stashes)
        listed: dict[str, str] = {}
        for line in git(worktree, "ls-tree", "-r", "stash@{0}^3").splitlines():
            cells = line.split()
            if len(cells) >= 4:
                listed[cells[3]] = cells[2]
        for name in absent:
            content = expected[name]
            digest = hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()
            self.assertEqual(listed.get(name), digest, name)

    def test_killed_mid_restore_recovers_without_loss(self) -> None:
        holders: list = []
        fixture, expected = self._kill_mid_restore(
            holders, lambda tmp: self._bulk_restore_fixture(tmp, side="code")
        )
        try:
            worktree = fixture.contract.code_worktree
            total = len(expected)
            # The kill landed strictly inside the restore: some parked files
            # back, the kept stash still listed, the journal pre-terminal.
            partial = self._restored_count(worktree, total)
            self.assertGreater(partial, 0)
            self.assertLess(partial, total)
            self.assertIn("parked code WIP", git(worktree, "stash", "list"))
            record = self._journal(fixture)
            assert isinstance(record, SyncOperationRecord)
            self.assertNotIn(record.phase, ("completed", "cancelled"))
            # The rerun recovers through the product: restored whole where the
            # kill landed late, a clean proof failure naming the kept stash
            # where the re-apply could not prove, or the retained conflict.
            # Every terminal keeps the stash and names the way on; every
            # parked byte survives.
            rerun = fixture.sync()
            self.assertIn(
                rerun.payload["state"],
                ("synced", "sync-resolution-required", "sync-git-proof-failed"),
                rerun.payload,
            )
            if rerun.payload["state"] == "synced":
                self.assertEqual(git(worktree, "stash", "list"), "")
            elif rerun.payload["state"] == "sync-resolution-required":
                self.assertIn("parked code WIP", git(worktree, "stash", "list"))
                self.assertIn("parked code WIP", str(rerun.payload.get("summary", "")))
            else:
                summary = str(rerun.payload.get("summary", ""))
                kept = re.search(r"stays in stash ([0-9a-f]{40})", summary)
                self.assertIsNotNone(kept, summary)
                assert kept is not None
                self.assertEqual(git(worktree, "rev-parse", "stash@{0}"), kept.group(1))
                self.assertIn("parked code WIP", git(worktree, "stash", "list"))
                # The named recovery is cancel: it restores the parked work
                # and the pre-sync heads, dropping nothing.
                cancelled = fixture.sync(resolution_action="cancel")
                self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
                self.assertEqual(git(worktree, "stash", "list"), "")
            self._assert_no_bulk_loss(worktree, expected, "parked code WIP")
        finally:
            for holder in holders:
                holder.cleanup()

    def _memory_wip_fixture(self, tmp: str, count: int = 500):
        """A memory-side WIP with a moved memory source; the code side stays current."""

        fixture = SyncFixture(Path(tmp))
        memory_worktree = fixture.contract.memory_worktree
        assert memory_worktree is not None
        for i in range(count):
            (memory_worktree / f"mem-{i:05d}.txt").write_bytes(f"parked memory {i}\n".encode())
        commit_file(fixture.memory_repo, "other.txt", "the memory line moved")
        code_tip = git(fixture.code_repo, "rev-parse", "main")
        fixture.map_official_memory(code_tip)
        return fixture

    def test_killed_after_memory_park_adopts_orphan_and_retains(self) -> None:
        """W2m: a death after the memory stash push adopts the memory orphan.

        The memory orphan is adopted exactly like the code one — clean tree,
        exact contract message, stash parent the pinned head — and the sync
        proceeds; the adopted stash stays journaled until it is restored and
        no byte is lost.
        """

        holders: list = []
        try:
            fixture = None
            for _ in range(3):
                holder = tempfile.TemporaryDirectory()
                holders.append(holder)
                candidate = self._memory_wip_fixture(holder.name)
                memory_worktree = candidate.contract.memory_worktree
                assert memory_worktree is not None
                pid = self._fork_sync_child(candidate.sync)
                if not self._window_open(
                    lambda memory_worktree=memory_worktree, candidate=candidate: (
                        "parked memory WIP" in git(memory_worktree, "stash", "list")
                        and git(memory_worktree, "status", "--porcelain") == ""
                        and self._journal(candidate) is None
                    ),
                    pid,
                    120,
                ):
                    self._reap(pid)
                    continue
                self._kill_group(pid)
                fixture = candidate
                break
            assert fixture is not None, "missed the memory orphan window three times"
            memory_worktree = fixture.contract.memory_worktree
            assert memory_worktree is not None
            # The orphan stash holds the memory candidate; the worktree is clean.
            self.assertIn("parked memory WIP", git(memory_worktree, "stash", "list"))
            self.assertEqual(git(memory_worktree, "status", "--porcelain"), "")
            # The rerun adopts the memory orphan and proceeds; the adopted
            # stash stays journaled on the stack until it is restored.
            rerun = fixture.sync()
            record = self._journal(fixture)
            assert isinstance(record, SyncOperationRecord)
            assert record.memory is not None
            self.assertTrue(record.memory.wipStash, record.memory)
            self.assertIn(rerun.payload["state"], ("synced", "memory-resolution-required"))
            if rerun.payload["state"] == "synced":
                self.assertEqual(git(memory_worktree, "stash", "list"), "")
                restored = sorted(memory_worktree.glob("mem-*.txt"))
                self.assertEqual(len(restored), 500)
                self.assertEqual(restored[0].read_bytes(), b"parked memory 0\n")
                self.assertEqual(restored[-1].read_bytes(), b"parked memory 499\n")
            else:
                self.assertIn("parked memory WIP", git(memory_worktree, "stash", "list"))
        finally:
            for holder in holders:
                holder.cleanup()

    def test_killed_after_fast_forward_move_completes_without_removing(self) -> None:
        """W-FF: a death after the memory fast-forward moved its ref.

        The ref already descends from the source when the rerun arrives, so
        the rerun adopts the moved ref — the reflog proves the single move,
        never repeated — and finishes; every parked byte survives. (A refused
        incoming tree moves no ref: pinned by the validator-route FF tests.)
        """

        holders: list = []
        fixture, expected = self._kill_mid_restore(
            holders,
            lambda tmp: self._bulk_restore_fixture(tmp, side="memory"),
            side="memory",
        )
        try:
            memory_worktree = fixture.contract.memory_worktree
            assert memory_worktree is not None
            total = len(expected)
            partial = self._restored_count(memory_worktree, total)
            self.assertGreater(partial, 0)
            self.assertLess(partial, total)
            record = self._journal(fixture)
            assert isinstance(record, SyncOperationRecord)
            assert record.memory is not None
            source = record.memory.sourceCommit
            # The fast-forward moved the work branch onto the source before
            # the death; the kill landed in the restore after it.
            self.assertEqual(git(memory_worktree, "rev-parse", "HEAD"), source)
            rerun = fixture.sync()
            self.assertIn(
                rerun.payload["state"],
                ("synced", "memory-resolution-required", "sync-git-proof-failed"),
                rerun.payload,
            )
            # The ref moved exactly once: one fast-forward entry in the work
            # branch reflog, and the head is the source — the rerun adopted
            # the move instead of repeating it.
            reflog = git(
                fixture.memory_repo, "log", "-g", "--format=%gs", record.memory.workBranch
            ).splitlines()
            self.assertEqual(sum("Fast-forward" in entry for entry in reflog), 1)
            self.assertEqual(git(memory_worktree, "rev-parse", "HEAD"), source)
            if rerun.payload["state"] == "sync-git-proof-failed":
                cancelled = fixture.sync(resolution_action="cancel")
                self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self._assert_no_bulk_loss(memory_worktree, expected, "parked memory WIP")
        finally:
            for holder in holders:
                holder.cleanup()

    def test_stale_tip_with_failing_check_answers_source_moved(self) -> None:
        # L111 combined: a stale tip plus a failing commit check. The stale line
        # is judged first, so continue answers sync-source-moved and never blames
        # the check for a tree the line left behind.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._retained_code_fixture(tmp)
            worktree = fixture.contract.code_worktree
            common = git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
            logs_before = set(Path(common).glob("agents-remember-commit-check-*.log"))
            # The line moves first (a commit the hook would block comes before it exists).
            commit_file(fixture.code_repo, "other.py", "the line moved on")
            hook = Path(common) / "hooks" / "pre-commit"
            hook.write_text("#!/bin/sh\necho hook says no\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
            try:
                refused = fixture.sync(resolution_action="continue")
            finally:
                hook.unlink()
            self.assertEqual(refused.payload["state"], "sync-source-moved", refused.payload)
            self.assertIn("not continued", str(refused.payload.get("summary", "")))
            # The failing hook was never consulted: no check log appeared.
            self.assertEqual(
                set(Path(common).glob("agents-remember-commit-check-*.log")), logs_before
            )
            record = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.phase, "code-resolution-required")

    def _wide_merge_fixture(self, tmp: str, lines: int = 20000):
        """A diverged setup with one wide conflicting file.

        Both sides rewrite every line, so the code merge stays in flight for
        on the order of a second — the kill window below. Returns the fixture;
        the operator resolves wide.txt after the death.
        """

        fixture = SyncFixture(Path(tmp))
        worktree = fixture.contract.code_worktree
        leaf = "".join(f"leaf line {i}\n" for i in range(lines))
        (worktree / "wide.txt").write_text(leaf, encoding="utf-8")
        commit_file(worktree, "wide.txt", leaf)
        commit_file(
            fixture.code_repo, "wide.txt", "".join(f"official line {i}\n" for i in range(lines))
        )
        code_tip = git(fixture.code_repo, "rev-parse", "main")
        fixture.map_official_memory(code_tip)
        return fixture

    def _kill_inside_merge(self, tmp_holder: list) -> SyncFixture:
        """Fork per attempt; return a fixture killed with the merge open.

        The window opens when MERGE_HEAD appears in the worktree gitdir and
        closes when the child records the retained merge or exits; the kill
        lands only while the child lives and the journal still says
        running-code. A miss reaps the child and retries on a fresh fixture.
        """

        for _ in range(3):
            holder = tempfile.TemporaryDirectory()
            tmp_holder.append(holder)
            candidate = self._wide_merge_fixture(holder.name)
            worktree = candidate.contract.code_worktree
            pid = self._fork_sync_child(candidate.sync)
            gitdir = Path(git(worktree, "rev-parse", "--absolute-git-dir"))
            if not self._window_open(
                lambda gitdir=gitdir: (gitdir / "MERGE_HEAD").is_file(), pid, 120
            ):
                self._reap(pid)
                continue
            self._kill_group(pid)
            record = self._journal(candidate)
            assert isinstance(record, SyncOperationRecord)
            if record.phase != "running-code":
                continue
            return candidate
        self.fail("missed the inside-merge window three times")

    def test_killed_inside_merge_source_moved_continue_then_cancel_saves(self) -> None:
        """L111: a kill inside the merge, then the source moves, then continue, then cancel.

        The rerun resumes the pinned merge to the retained state; continue
        answers sync-source-moved — the stale line is judged first, so the
        failing hook is never consulted (no check log appears); cancel saves
        the staged resolution first and completes whole by digest. Extends
        test_stale_tip_with_failing_check_answers_source_moved with a real
        death before the retained journal write.
        """

        holders: list = []
        try:
            fixture = self._kill_inside_merge(holders)
            worktree = fixture.contract.code_worktree
            # Killed inside the merge: the merge state is open, the journal
            # still says running-code, the pinned source is untouched.
            gitdir = Path(git(worktree, "rev-parse", "--absolute-git-dir"))
            self.assertTrue((gitdir / "MERGE_HEAD").is_file())
            record = self._journal(fixture)
            assert isinstance(record, SyncOperationRecord)
            source_before = record.code.sourceCommit
            pre_head = record.code.preSyncHead
            # The operator resolves and stages the pinned merge.
            resolved = "".join(f"resolved line {i}\n" for i in range(20000))
            (worktree / "wide.txt").write_text(resolved, encoding="utf-8")
            git(worktree, "add", "wide.txt")
            # The line moves, and a hook that would fail is installed: the
            # stale line must be judged first, the hook never consulted.
            commit_file(fixture.code_repo, "other.py", "the line moved on")
            common = git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
            logs_before = set(Path(common).glob("agents-remember-commit-check-*.log"))
            hook = Path(common) / "hooks" / "pre-commit"
            hook.write_text("#!/bin/sh\necho hook says no\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
            try:
                rerun = fixture.sync()
                self.assertEqual(rerun.payload["state"], "sync-resolution-required", rerun.payload)
                refused = fixture.sync(resolution_action="continue")
            finally:
                hook.unlink()
            self.assertEqual(refused.payload["state"], "sync-source-moved", refused.payload)
            self.assertIn("not continued", str(refused.payload.get("summary", "")))
            self.assertEqual(
                set(Path(common).glob("agents-remember-commit-check-*.log")), logs_before
            )
            # The staged resolution survived the refusal; the operator annotates
            # it and the cancel saves the staged whole first, then completes.
            with (worktree / "wide.txt").open("a", encoding="utf-8") as handle:
                handle.write("operator note: source moved, cancelling\n")
            git(worktree, "add", "wide.txt")
            cancelled = fixture.sync(resolution_action="cancel")
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            final = self._journal(fixture)
            assert isinstance(final, SyncOperationRecord)
            self.assertEqual(final.phase, "cancelled")
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), pre_head)
            saved = (
                fixture.contract.worktree_group
                / "scratch"
                / f"sync-cancel-generation-{final.generation}"
                / "code"
            )
            staged = (saved / "staged.patch").read_bytes()
            self.assertIn(b"operator note: source moved, cancelling", staged)
            self.assertIn(b"resolved line 19999", staged)
            summary = str(cancelled.payload["summary"])
            self.assertIn(f"(sha256:{hashlib.sha256(staged).hexdigest()})", summary)
            # The stale merge was never committed: the head is back at the
            # pre-sync commit, not at the carried source.
            self.assertNotEqual(git(worktree, "rev-parse", "HEAD"), source_before)
        finally:
            for holder in holders:
                holder.cleanup()

    def _completing_fixture(self, tmp: str):
        """A sync that completes: one official commit, one small parked file."""

        fixture = SyncFixture(Path(tmp))
        worktree = fixture.contract.code_worktree
        (worktree / "note.txt").write_text("candidate note\n", encoding="utf-8")
        commit_file(fixture.code_repo, "other.txt", "the official line moved")
        code_tip = git(fixture.code_repo, "rev-parse", "main")
        fixture.map_official_memory(code_tip)
        return fixture

    def test_killed_in_completion_tail_replays_completed_once(self) -> None:
        """W4b/W5b: a death in the completion tail replays completed exactly once.

        The kill lands after the terminal journal write while residue steps
        (stash retire, worktree cleanup, worklist recompute) are still in
        flight. The rerun's residue cleanup is idempotent, the bases advance
        exactly once — one sync-log entry — and the terminal replays without
        moving anything: continue recovers the completed result, cancel
        answers sync-already-completed naming continue. Nothing asks for
        manual repair.
        """

        holders: list = []
        try:
            fixture = None
            for _ in range(3):
                holder = tempfile.TemporaryDirectory()
                holders.append(holder)
                candidate = self._completing_fixture(holder.name)
                pid = self._fork_sync_child(candidate.sync)
                if not self._window_open(
                    lambda candidate=candidate: (
                        getattr(self._journal(candidate), "phase", None) == "completed"
                    ),
                    pid,
                    120,
                ):
                    self._reap(pid)
                    continue
                self._kill_group(pid)
                try:
                    record = self._journal(candidate)
                except Exception:
                    continue
                assert isinstance(record, SyncOperationRecord)
                if record.phase != "completed":
                    continue
                fixture = candidate
                break
            assert fixture is not None, "missed the completion tail three times"
            worktree = fixture.contract.code_worktree
            record = self._journal(fixture)
            assert isinstance(record, SyncOperationRecord)
            # Exactly once: the bases name the carried sources with a single
            # sync-log entry, the parked file stands restored.
            contract = load_contract(fixture.contract.contract_path)
            self.assertEqual(contract.code_base_commit, record.code.sourceCommit)
            self.assertEqual(len(contract.sync_log), 1)
            self.assertEqual(
                (worktree / "note.txt").read_text(encoding="utf-8"), "candidate note\n"
            )
            code_head = git(worktree, "rev-parse", "HEAD")
            # Continue replays the completed result without moving anything.
            replayed = fixture.sync(resolution_action="continue")
            self.assertEqual(replayed.returncode, 0)
            self.assertEqual(replayed.payload["state"], "synced", replayed.payload)
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), code_head)
            self.assertEqual(
                load_contract(fixture.contract.contract_path).sync_log,
                contract.sync_log,
            )
            # Cancel after completion is refused terminally: it names the
            # continue that recovers the result, never manual repair.
            refused = fixture.sync(resolution_action="cancel")
            self.assertEqual(refused.payload["state"], "sync-already-completed")
            self.assertIn("resolution_action='continue'", str(refused.payload.get("summary", "")))
            self.assertNotIn("manual", str(refused.payload.get("summary", "")).lower())
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), code_head)
            # No residue: no parked stash survives on either worktree.
            self.assertEqual(git(worktree, "stash", "list"), "")
            memory_worktree = fixture.contract.memory_worktree
            assert memory_worktree is not None
            self.assertEqual(git(memory_worktree, "stash", "list"), "")
        finally:
            for holder in holders:
                holder.cleanup()


class StaleLockClearingTests(unittest.TestCase):
    """MIK-R61 rule 12: entry lock clearing never refuses the admission it guards."""

    def setUp(self) -> None:
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def test_a_not_yet_created_worktree_clears_to_nothing(self) -> None:
        # A series side's temporary worktree does not exist until the sync
        # creates it; clearing its locks must answer empty, never raise, so
        # the admission proceeds instead of refusing a safe transition.
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / ".sync" / "code"
            self.assertFalse(missing.exists())
            self.assertEqual(clear_stale_git_locks([missing]), ())

    def test_a_live_lock_is_left_alone(self) -> None:
        # A lock a live process holds open (a genuinely running git) is not
        # stale: it stays, and the next git operation names it.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            subprocess.run(
                ["git", "init", "-q", "-b", "main", str(repo)],
                check=True,
                capture_output=True,
            )
            gitdir = Path(
                subprocess.run(
                    ["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            )
            lock = gitdir / "index.lock"
            with lock.open("wb") as held:
                held.write(b"live\n")
                held.flush()
                self.assertEqual(clear_stale_git_locks([repo]), ())
                self.assertTrue(lock.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
