"""Save-first cancellation preserves deletions without treating them as untracked files."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agents_remember.application.worktree_services import (
    bind_worktree_services,
    build_default_worktree_services,
)
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember.worktrees.sync_transaction_state import SyncOperationRecord, SyncOperationStore
from test_worktree_sync import SyncFixture, commit_file, git


def _tree(worktree: Path) -> dict[str, bytes]:
    return {
        path.relative_to(worktree).as_posix(): path.read_bytes()
        for path in worktree.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(worktree).parts
    }


def _state(worktree: Path) -> tuple[str, ...]:
    return tuple(
        git(worktree, *args)
        for args in (
            ("rev-parse", "HEAD"),
            ("ls-files", "--stage"),
            ("diff", "--cached", "--binary", "HEAD"),
            ("diff", "--binary"),
            ("status", "--porcelain", "-uall"),
            ("stash", "list", "--format=%H"),
        )
    )


class CancelDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def test_cancel_saves_staged_deletion_and_restores_parked_partition(self) -> None:
        self._cancel_deletion(staged=True)

    def test_cancel_saves_unstaged_deletion_and_restores_parked_partition(self) -> None:
        self._cancel_deletion(staged=False)

    def _cancel_deletion(self, *, staged: bool) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            for name in ("parked-delete.txt", "later-delete.txt"):
                commit_file(worktree, name, "tracked binary-safe content\x00\r\n")
            (worktree / "older-stash.txt").write_bytes(b"unrelated stash\n")
            git(worktree, "stash", "push", "-u", "-m", "unrelated owner stash")
            (worktree / "README.md").write_bytes(b"parked edit\n")
            (worktree / "parked-delete.txt").unlink()
            if staged:
                git(worktree, "add", "parked-delete.txt")
            (worktree / "candidate.txt").write_bytes(b"parked untracked\x00\r\n")
            before_tree, before_state = _tree(worktree), _state(worktree)
            commit_file(fixture.code_repo, "README.md", "official edit")
            fixture.map_official_memory(git(fixture.code_repo, "rev-parse", "main"))

            stopped = fixture.sync()
            self.assertEqual(stopped.payload["state"], "sync-resolution-required", stopped.payload)
            store = SyncOperationStore(fixture.contract.worktree_group)
            record = store.read()
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(record.code.wipState, "restore-conflict")
            self.assertTrue(record.code.wipStash)
            self.assertIn(record.code.wipStash, git(worktree, "stash", "list", "--format=%H"))
            # Resolve the reapply conflict, then delete a different tracked path as later work.
            # Neither the saved deletion nor the parked deletion has bytes to mirror.
            (worktree / "README.md").write_bytes(b"owner resolution\n")
            git(worktree, "add", "README.md")
            (worktree / "later-delete.txt").unlink()
            if staged:
                git(worktree, "add", "later-delete.txt")
            (worktree / "owner-untracked.txt").write_bytes(b"later untracked\x00\r\n")
            saved_tree = _tree(worktree)
            saved_index = git(worktree, "write-tree")
            saved_head = git(worktree, "rev-parse", "HEAD")
            saved_staged = git(worktree, "diff", "--cached", "--binary", "HEAD")
            saved_unstaged = git(worktree, "diff", "--binary")
            commit_file(fixture.code_repo, "moved-again.txt", "new source tip")
            refused = fixture.sync(resolution_action="continue")
            self.assertEqual(refused.payload["state"], "sync-source-moved", refused.payload)

            cancelled = fixture.sync(resolution_action="cancel")
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self.assertEqual(_tree(worktree), before_tree)
            self.assertEqual(_state(worktree), before_state)
            final = store.read()
            assert isinstance(final, SyncOperationRecord)
            self.assertEqual(final.phase, "cancelled")
            self.assertNotIn(record.code.wipStash, git(worktree, "stash", "list", "--format=%H"))
            saved = (
                fixture.contract.worktree_group
                / "scratch"
                / f"sync-cancel-generation-{record.generation}"
                / "code"
            )
            self._assert_saved_deletion(
                saved, staged, (saved_staged, saved_unstaged), cancelled.payload
            )
            self._assert_replay(fixture, saved, (saved_head, saved_index, saved_tree))

    def _assert_replay(
        self,
        fixture: SyncFixture,
        saved: Path,
        expected: tuple[str, str, dict[str, bytes]],
    ) -> None:
        saved_head, saved_index, saved_tree = expected
        replay = fixture.root / "replay"
        git(fixture.root, "clone", "--shared", "--quiet", str(fixture.code_repo), str(replay))
        git(replay, "checkout", "--quiet", "--detach", saved_head)
        git(replay, "apply", "--index", str(saved / "staged.patch"))
        if (saved / "unstaged.patch").stat().st_size:
            git(replay, "apply", str(saved / "unstaged.patch"))
        (replay / "owner-untracked.txt").write_bytes(
            (saved / "untracked" / "owner-untracked.txt").read_bytes()
        )
        (replay / "candidate.txt").write_bytes(saved_tree["candidate.txt"])
        self.assertEqual(git(replay, "write-tree"), saved_index)
        self.assertEqual(_tree(replay), saved_tree)

    def _assert_saved_deletion(
        self,
        saved: Path,
        staged: bool,
        patches: tuple[str, str],
        payload: dict[str, object],
    ) -> None:
        staged_patch, unstaged_patch = patches
        self.assertEqual((saved / "staged.patch").read_text().strip(), staged_patch)
        self.assertEqual((saved / "unstaged.patch").read_text().strip(), unstaged_patch)
        deletion_patch = saved / ("staged.patch" if staged else "unstaged.patch")
        self.assertIn("later-delete.txt", deletion_patch.read_text())
        other_patch = saved / ("unstaged.patch" if staged else "staged.patch")
        self.assertNotIn("later-delete.txt", other_patch.read_text())
        self.assertFalse((saved / "untracked" / "later-delete.txt").exists())
        self.assertFalse((saved / "untracked" / "parked-delete.txt").exists())
        manifest = json.loads((saved / "saved-state.json").read_bytes())
        summary = str(payload["summary"])
        self.assertIn("could not attribute", summary)
        for name, digest in manifest["files"]:
            self.assertEqual(hashlib.sha256(Path(name).read_bytes()).hexdigest(), digest)
            self.assertIn(f"{name} (sha256:{digest})", summary)
        self.assertIn("later-delete.txt", (saved / "paths.txt").read_text().splitlines())
