"""Real Git sync of authored ignore rules alongside the generated briefs rule."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents_remember.application import worktree_tools
from agents_remember.kernel.memory_ignore import BRIEFS_IGNORE_TEXT
from agents_remember.worktrees.services import CrossingPlanView
from agents_remember.worktrees.sync_transaction_git import _merge as sync_git_merge
from agents_remember.worktrees.worktree_contract import load_contract
from checkpoint_landing_test_support import (
    accumulate_master_line,
    advance_source_line,
    branch_checkout,
    committed_ignore,
    memory_repository,
    rev,
)
from test_closeout_queue import MASTER_B, QueueFixture
from test_worktree_support import git


class CheckpointMemoryIgnoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.fixture = QueueFixture(root, atomic_b=True, memory_mode="external")
        self.series = load_contract(self.fixture.tasks / "master-b" / "series-contract.md")
        self.scratch = root / "scratch"
        self.memory = memory_repository(self.series)

    def _seed_common_ignore(self, ignore: bytes) -> str:
        common = advance_source_line(self.series, self.scratch, label="common", ignore=ignore)
        synced = self._merge_source()
        self.assertEqual(synced["state"], "synced", synced)
        self.series = load_contract(self.series.contract_path)
        leaf = self.fixture.contracts[MASTER_B]
        synced_leaf = worktree_tools.worktree_sync_tool(
            self.fixture.cfg, contract_path=leaf.contract_path.as_posix()
        )
        self.assertEqual(synced_leaf["state"], "synced", synced_leaf)
        self.assertEqual(rev(self.memory, self.series.memory_work_branch), common.memory_commit)
        return common.memory_commit

    def _merge_source(self) -> dict[str, object]:
        return worktree_tools.worktree_sync_tool(
            self.fixture.cfg,
            contract_path=self.series.contract_path.as_posix(),
            memory_sync_choice="merge-memory",
        )

    def _retained_gitignore(self, stopped: dict[str, object], parents: dict[int, str]) -> Path:
        self.assertEqual(stopped["state"], "sync-resolution-required", stopped)
        resolution = stopped["resolution"]
        assert isinstance(resolution, dict), stopped
        self.assertEqual(resolution["side"], "memory")
        self.assertEqual(resolution["files"], [".gitignore"])
        worktree = Path(resolution["worktree"])
        self.assertEqual(rev(worktree, "HEAD"), parents[2])
        self.assertEqual(rev(worktree, "MERGE_HEAD"), parents[3])
        self.assertEqual(
            git(worktree, "ls-files", "--unmerged", "--", ".gitignore").splitlines(),
            [
                f"100644 {rev(worktree, f'{parent}:.gitignore')} {stage}\t.gitignore"
                for stage, parent in parents.items()
            ],
        )
        return worktree

    def _assert_retained_gitignore(self, parents: dict[int, str], *authored_rules: bytes) -> None:
        worktree = self._retained_gitignore(self._merge_source(), parents)
        conflict = (worktree / ".gitignore").read_bytes()
        self.assertEqual(conflict, committed_ignore(worktree, "AUTO_MERGE"))
        for rule in authored_rules:
            self.assertIn(rule, conflict)

    def test_authored_add_add_retains_git_conflict(self) -> None:
        own_ignore = b"/memory.md\n/leaf-notes/\n"
        source_ignore = b"/memory.md\n/source-notes/\n"
        own = accumulate_master_line(
            self.fixture, self.series, self.scratch, label="leaf", ignore=own_ignore
        )
        source = advance_source_line(
            self.series, self.scratch, label="source", ignore=source_ignore
        )
        base = git(self.memory, "merge-base", own.memory_commit, source.memory_commit)
        self.assertEqual(git(self.memory, "ls-tree", base, "--", ".gitignore"), "")
        self._assert_retained_gitignore(
            {2: own.memory_commit, 3: source.memory_commit}, b"/leaf-notes/\n", b"/source-notes/\n"
        )

    def test_generated_and_authored_appends_merge_together(self) -> None:
        common_ignore = b"/memory.md\n/base-notes/\n"
        common = self._seed_common_ignore(common_ignore)
        own = accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf")
        source_ignore = common_ignore + b"/source-notes/\n"
        source = advance_source_line(
            self.series, self.scratch, label="source", ignore=source_ignore
        )
        self.assertEqual(
            git(self.memory, "merge-base", own.memory_commit, source.memory_commit), common
        )
        self.assertEqual(committed_ignore(self.memory, common), common_ignore)
        self.assertEqual(
            committed_ignore(self.memory, own.memory_commit),
            common_ignore + b"\n" + BRIEFS_IGNORE_TEXT.encode(),
        )
        self.assertEqual(committed_ignore(self.memory, source.memory_commit), source_ignore)

        synced = self._merge_source()

        self.assertEqual(synced["state"], "synced", synced)
        merged = rev(self.memory, self.series.memory_work_branch)
        self.assertEqual(
            git(self.memory, "rev-list", "--parents", "-n", "1", merged).split(),
            [merged, own.memory_commit, source.memory_commit],
        )
        self.assertEqual(
            committed_ignore(self.memory, merged),
            source_ignore + b"\n" + BRIEFS_IGNORE_TEXT.encode(),
        )

    def test_overlapping_authored_changes_retain_git_conflict(self) -> None:
        common = self._seed_common_ignore(b"/memory.md\n/base-notes/\n")
        own = accumulate_master_line(
            self.fixture,
            self.series,
            self.scratch,
            label="leaf",
            ignore=b"/memory.md\n/leaf-notes/\n",
        )
        source = advance_source_line(
            self.series, self.scratch, label="source", ignore=b"/memory.md\n/source-notes/\n"
        )
        self._assert_retained_gitignore(
            {1: common, 2: own.memory_commit, 3: source.memory_commit},
            b"/leaf-notes/\n",
            b"/source-notes/\n",
        )

    def _publish_final_rule_replacement(self, common_ignore: bytes) -> dict[int, str]:
        common = self._seed_common_ignore(common_ignore)
        own = accumulate_master_line(self.fixture, self.series, self.scratch, label="leaf")
        source_ignore = b"/memory.md\n/revised-notes/\n"
        source = advance_source_line(
            self.series, self.scratch, label="source", ignore=source_ignore
        )
        self.assertEqual(
            git(self.memory, "merge-base", own.memory_commit, source.memory_commit), common
        )
        self.assertEqual(committed_ignore(self.memory, common), common_ignore)
        self.assertEqual(
            committed_ignore(self.memory, own.memory_commit),
            b"/memory.md\n/base-notes/\n\n" + BRIEFS_IGNORE_TEXT.encode(),
        )
        self.assertEqual(committed_ignore(self.memory, source.memory_commit), source_ignore)
        return {1: common, 2: own.memory_commit, 3: source.memory_commit}

    def _assert_final_rule_replacement_merges(self, common_ignore: bytes) -> None:
        parents = self._publish_final_rule_replacement(common_ignore)

        synced = self._merge_source()

        self.assertEqual(synced["state"], "synced", synced)
        merged = rev(self.memory, self.series.memory_work_branch)
        self.assertEqual(
            git(self.memory, "rev-list", "--parents", "-n", "1", merged).split(),
            [merged, parents[2], parents[3]],
        )
        self.assertEqual(
            committed_ignore(self.memory, merged),
            b"/memory.md\n/revised-notes/\n\n" + BRIEFS_IGNORE_TEXT.encode(),
        )
        with branch_checkout(
            self.memory, self.series.memory_work_branch, self.scratch / "settled"
        ) as worktree:
            ignored = git(worktree, "check-ignore", "--no-index", "memory.md", "briefs/src.md")
            self.assertEqual(ignored.splitlines(), ["memory.md", "briefs/src.md"])

    def test_terminated_final_rule_replacement_merges(self) -> None:
        self._assert_final_rule_replacement_merges(b"/memory.md\n/base-notes/\n")

    def test_unterminated_final_rule_replacement_merges(self) -> None:
        self._assert_final_rule_replacement_merges(b"/memory.md\n/base-notes/")

    def test_authored_interior_newline_retains_git_conflict(self) -> None:
        common = self._seed_common_ignore(b"/memory.md\n/base-notes/\n/kept/\n")
        own = accumulate_master_line(
            self.fixture,
            self.series,
            self.scratch,
            label="leaf",
            ignore=b"/memory.md\n/base-\nnotes/\n/kept/\n",
        )
        source = advance_source_line(
            self.series,
            self.scratch,
            label="source",
            ignore=b"/memory.md\n/revised-notes/\n/kept/\n",
        )
        self._assert_retained_gitignore(
            {1: common, 2: own.memory_commit, 3: source.memory_commit},
            b"/base-\nnotes/\n",
            b"/revised-notes/\n",
        )

    def test_owner_edit_preserves_auto_merge_conflict(self) -> None:
        parents = self._publish_final_rule_replacement(b"/memory.md\n/base-notes/\n")
        owner_edit = b"\n# Owner added this during resolution\n"
        apply_crossing = sync_git_merge.apply_crossing

        def edit_after_git_merge(worktree: Path, plan: CrossingPlanView) -> tuple[str, ...]:
            conflicts = apply_crossing(worktree, plan)
            path = worktree / ".gitignore"
            original = committed_ignore(worktree, "AUTO_MERGE")
            self.assertEqual(path.read_bytes(), original)
            path.write_bytes(original + owner_edit)
            return conflicts

        with mock.patch.object(sync_git_merge, "apply_crossing", side_effect=edit_after_git_merge):
            stopped = self._merge_source()

        worktree = self._retained_gitignore(stopped, parents)
        self.assertEqual(
            (worktree / ".gitignore").read_bytes(),
            committed_ignore(worktree, "AUTO_MERGE") + owner_edit,
        )
