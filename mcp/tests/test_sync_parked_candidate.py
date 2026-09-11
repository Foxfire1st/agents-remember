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

from agents_remember.models.worktree import SyncResolutionProjection
from agents_remember.worktrees import sync_transaction
from agents_remember.worktrees.sync_transaction_git import SyncGitProofError
from agents_remember.worktrees.worktree_contract import load_contract
from test_worktree_sync import SyncFixture, commit_file, git, section


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
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)

            result = fixture.sync()

            self.assertEqual(result.payload["state"], "synced")
            self.assertEqual(section(result.payload, "memory")["wip"]["state"], "restored")
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
