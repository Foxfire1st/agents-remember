"""The parked worktree candidate inside the existing sync transaction.

A closeout-time leaf is dirty by definition, so the sync parks its WIP, carries the moved
source, and must return the candidate. These cases pin that contract at the transaction
level; the closeout boundary's own surface is pinned in ``test_source_lineage``.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
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
from agents_remember.worktrees import sync_transaction, sync_transaction_authority
from agents_remember.worktrees import sync_transaction_git as sync_git
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember.worktrees.sync_transaction_git import SyncGitProofError
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

    def test_cancel_with_later_work_is_refused_and_changes_nothing(self) -> None:
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
            parked_tree = snapshot_tree(worktree)
            parked_journal = journal_bytes(fixture)
            self.assertIsNotNone(parked_journal)

            refused = fixture.sync(resolution_action="cancel")

            self.assertEqual(refused.returncode, 2)
            self.assertEqual(
                refused.payload["state"], "sync-cancel-refused-later-work", refused.payload
            )
            summary = str(refused.payload["summary"])
            self.assertIn("notes.txt", summary)
            self.assertIn("later-notes.txt", summary)
            self.assertEqual(snapshot_tree(worktree), parked_tree)
            self.assertEqual(journal_bytes(fixture), parked_journal)

            again = fixture.sync(resolution_action="cancel")
            self.assertEqual(again.payload["state"], "sync-cancel-refused-later-work")
            self.assertEqual(str(again.payload["summary"]), summary)
            self.assertEqual(journal_bytes(fixture), parked_journal)

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

    def test_01_later_bytes_in_unmerged_path_refuse_cancel(self) -> None:
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
            before = snapshot_tree(worktree)
            journal = journal_bytes(fixture)

            refused = fixture.sync(resolution_action="cancel")

            self.assertEqual(refused.returncode, 2)
            self.assertEqual(
                refused.payload["state"], "sync-cancel-refused-later-work", refused.payload
            )
            self.assertIn("code/README.md", str(refused.payload["summary"]))
            self.assertIn(b"UNSTASHED LATER WORK", (worktree / "README.md").read_bytes())
            self.assertEqual(snapshot_tree(worktree), before)
            self.assertEqual(journal_bytes(fixture), journal)

            again = fixture.sync(resolution_action="cancel")
            self.assertEqual(again.payload["state"], "sync-cancel-refused-later-work")
            self.assertEqual(str(again.payload["summary"]), str(refused.payload["summary"]))
            self.assertEqual(journal_bytes(fixture), journal)

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

    def test_04_index_only_later_bytes_refuse_cancel(self) -> None:
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
            before_index = git(worktree, "show", ":notes.txt")
            before_tree = snapshot_tree(worktree)
            journal = journal_bytes(fixture)

            refused = fixture.sync(resolution_action="cancel")

            self.assertEqual(refused.returncode, 2)
            self.assertEqual(
                refused.payload["state"], "sync-cancel-refused-later-work", refused.payload
            )
            self.assertIn("code/notes.txt", str(refused.payload["summary"]))
            self.assertEqual(git(worktree, "show", ":notes.txt"), before_index)
            self.assertNotEqual(before_index, "parked notes\n")
            self.assertEqual(snapshot_tree(worktree), before_tree)
            self.assertEqual(journal_bytes(fixture), journal)

    def test_04b_staged_conflict_markers_refuse_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            (worktree / "README.md").write_text("parked version\n", encoding="utf-8")
            commit_file(fixture.code_repo, "README.md", "landed version")
            fixture.map_official_memory(git(fixture.code_repo, "rev-parse", "main"))
            retained = fixture.sync()
            self.assertEqual(retained.payload["state"], "sync-resolution-required")
            git(worktree, "add", "README.md")
            before_index = git(worktree, "show", ":README.md")
            before_tree = snapshot_tree(worktree)
            journal = journal_bytes(fixture)

            refused = fixture.sync(resolution_action="cancel")

            self.assertEqual(refused.returncode, 2)
            self.assertEqual(
                refused.payload["state"], "sync-cancel-refused-later-work", refused.payload
            )
            self.assertIn("code/README.md", str(refused.payload["summary"]))
            self.assertEqual(git(worktree, "show", ":README.md"), before_index)
            self.assertEqual(snapshot_tree(worktree), before_tree)
            self.assertEqual(journal_bytes(fixture), journal)

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

    def test_06b_retargeted_symlink_refuses_cancel(self) -> None:
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
            journal = journal_bytes(fixture)

            refused = fixture.sync(resolution_action="cancel")

            self.assertEqual(refused.returncode, 2)
            self.assertEqual(
                refused.payload["state"], "sync-cancel-refused-later-work", refused.payload
            )
            self.assertIn("code/link", str(refused.payload["summary"]))
            self.assertEqual(journal_bytes(fixture), journal)


class RoundTwoVariantTests(unittest.TestCase):
    """The round-2 fixed-list variants for L153-R1-01, -02 and -03."""

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
            before = snapshot_tree(worktree)
            resumed = fixture.sync()
            store = SyncOperationStore(fixture.contract.worktree_group)
            record = store.read()
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.phase, "code-resolution-required", resumed.payload)
            journal = journal_bytes(fixture)
            cancelled = fixture.sync(resolution_action="cancel")
            self.assertEqual(
                cancelled.payload["state"], "sync-cancel-refused-later-work", cancelled.payload
            )
            self.assertIn("README.md", str(cancelled.payload["summary"]))
            self.assertIn(b"UNSTASHED LATER AFTER CRASH", (worktree / "README.md").read_bytes())
            self.assertEqual(snapshot_tree(worktree), before)
            self.assertEqual(journal_bytes(fixture), journal)

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
                    sync_git,
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

    def test_07b_owner_write_after_interrupt_refuses_without_later_work_claim(
        self,
    ) -> None:
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
            journal = journal_bytes(lines.fixture)
            before = snapshot_tree(lines.leaf)
            refused = lines.sync(resolution_action="cancel")
            self.assertEqual(refused["state"], "sync-cancel-unproven-after-interrupt", refused)
            summary = str(refused.get("summary"))
            self.assertIn(APP_SIDECAR, summary)
            self.assertIn("cannot be shown", summary)
            self.assertNotIn("later work", summary)
            self.assertEqual(journal_bytes(lines.fixture), journal)
            self.assertEqual(snapshot_tree(lines.leaf), before)

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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
