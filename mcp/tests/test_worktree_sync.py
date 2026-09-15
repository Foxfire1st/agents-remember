"""Issue #54: worktree_sync pulls the moved official line into a live worktree."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.kernel.memory_ledger import create_initial_ledger, write_ledger
from agents_remember.worktrees import sync_transaction_git
from agents_remember.worktrees.integration.closeout.door_evidence import memory_candidate_tree
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.startup.start_memory import prepare_memory_for_start
from agents_remember.worktrees.modules.sync import sync_result
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationStore,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    load_contract,
    write_contract,
)


class SyncFixture:
    """Live code/memory worktrees whose official lines can be moved."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.code_repo = root / "repo-a"
        self.code_base = make_repo(self.code_repo)
        self.memory_repo = root / "ar-coordination" / "memory-repos" / "ar-repo-a"
        memory_seed = make_repo(self.memory_repo)
        write_ledger(
            self.memory_repo / "memory.md",
            create_initial_ledger("repo-a", self.code_base, memory_seed),
        )
        git(self.memory_repo, "add", "memory.md")
        git(self.memory_repo, "commit", "-m", "Add memory ledger")
        self.memory_base = git(self.memory_repo, "rev-parse", "HEAD")
        self.contract = default_contract(
            ContractTask(
                name="Sync Thing",
                repo_name="repo-a",
                coordination_root=root / "ar-coordination",
                workflow_kind="light-task",
                memory_mode="external",
            ),
            leaf=LeafIdentity(worktree_name="sync-thing"),
            code=RepoBranchPlan(
                repo_path=self.code_repo,
                source_branch="main",
                work_branch="ar/sync-thing",
                base_commit=self.code_base,
            ),
            memory=RepoBranchPlan(
                repo_path=self.memory_repo,
                source_branch="main",
                work_branch="ar/sync-thing",
                base_commit=self.memory_base,
            ),
        )
        assert self.contract.memory_worktree is not None
        git(
            self.code_repo,
            "worktree",
            "add",
            "-b",
            self.contract.code_work_branch,
            str(self.contract.code_worktree),
            "main",
        )
        git(
            self.memory_repo,
            "worktree",
            "add",
            "-b",
            self.contract.memory_work_branch,
            str(self.contract.memory_worktree),
            "main",
        )
        write_contract(self.contract.contract_path, self.contract)

    def move_official_code(self) -> str:
        commit_file(self.code_repo, "src/new.py", "VALUE = 'landed'")
        return git(self.code_repo, "rev-parse", "main")

    def map_official_memory(self, code_tip: str) -> str:
        """Land memory content attributed to the admitted code commit."""
        target = self.memory_repo / "onboarding/src/new.py.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# new.py onboarding\n", encoding="utf-8")
        git(self.memory_repo, "add", "onboarding/src/new.py.md")
        git(
            self.memory_repo,
            "commit",
            "-m",
            render_memory_content_message("Update new.py onboarding", code_tip),
        )
        return git(self.memory_repo, "rev-parse", "main")

    def sync(self, **kwargs: Any):
        return sync_result(WorktreeArgs(contract_path=self.contract.contract_path, **kwargs))


class WorktreeSyncTests(unittest.TestCase):
    def test_pure_fast_forward_sync_advances_both_sides_and_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            code_tip = fixture.move_official_code()
            memory_tip = fixture.map_official_memory(code_tip)

            result = fixture.sync()

            self.assertEqual(result.payload["state"], "synced")
            self.assertEqual(section(result.payload, "code")["state"], "completed")
            self.assertEqual(section(result.payload, "code")["plan"], "fast-forward")
            self.assertEqual(section(result.payload, "memory")["state"], "completed")
            self.assertEqual(section(result.payload, "memory")["plan"], "fast-forward")
            self.assertEqual(git(fixture.contract.code_worktree, "rev-parse", "HEAD"), code_tip)
            assert fixture.contract.memory_worktree is not None
            self.assertEqual(git(fixture.contract.memory_worktree, "rev-parse", "HEAD"), memory_tip)
            reloaded = load_contract(fixture.contract.contract_path)
            self.assertEqual(reloaded.code_base_commit, code_tip)
            self.assertEqual(reloaded.memory_base_commit, memory_tip)
            self.assertEqual(len(reloaded.sync_log), 1)
            self.assertEqual(reloaded.sync_log[0]["codeBaseTo"], code_tip)

    def test_code_merge_conflict_is_retained_and_can_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            commit_file(fixture.contract.code_worktree, "README.md", "work-branch version")
            commit_file(fixture.code_repo, "README.md", "official version")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)

            pre_sync = git(fixture.contract.code_worktree, "rev-parse", "HEAD")
            result = fixture.sync()

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "sync-resolution-required")
            self.assertEqual(result.payload["status"], "agent-action-required")
            self.assertIn("README.md", section(result.payload, "resolution")["files"])
            self.assertEqual(
                git(fixture.contract.code_worktree, "rev-parse", "MERGE_HEAD"), code_tip
            )
            self.assertEqual(
                git(fixture.contract.code_worktree, "rev-parse", "HEAD"),
                pre_sync,
            )
            (fixture.contract.code_worktree / "README.md").write_text(
                "resolved version\n", encoding="utf-8"
            )
            git(fixture.contract.code_worktree, "add", "README.md")

            continued = fixture.sync(resolution_action="continue")

            self.assertEqual(continued.payload["state"], "synced")
            self.assertEqual(
                git(
                    fixture.contract.code_worktree, "rev-list", "--parents", "-n", "1", "HEAD"
                ).split()[1:],
                [pre_sync, code_tip],
            )

    def test_sync_uses_source_refs_when_the_cache_is_stale_missing_or_malformed(self) -> None:
        for cache_state in ("stale", "missing", "malformed"):
            with self.subTest(cache_state=cache_state), tempfile.TemporaryDirectory() as tmp:
                fixture = SyncFixture(Path(tmp))
                code_tip = fixture.move_official_code()
                cache = fixture.memory_repo / "memory.md"
                if cache_state == "missing":
                    cache.unlink()
                elif cache_state == "malformed":
                    cache.write_text("<<<<<<< broken cache\n", encoding="utf-8")
                if cache_state != "stale":
                    git(fixture.memory_repo, "add", "-A")
                    git(fixture.memory_repo, "commit", "-m", f"Historical {cache_state} cache")
                memory_tip = git(fixture.memory_repo, "rev-parse", "main")

                result = fixture.sync()

                self.assertEqual(result.returncode, 0, result.payload)
                self.assertEqual(result.payload["state"], "synced")
                self.assertEqual(git(fixture.contract.code_worktree, "rev-parse", "HEAD"), code_tip)
                assert fixture.contract.memory_worktree is not None
                self.assertEqual(
                    git(fixture.contract.memory_worktree, "rev-parse", "HEAD"), memory_tip
                )
                reloaded = load_contract(fixture.contract.contract_path)
                self.assertEqual(reloaded.code_base_commit, code_tip)
                self.assertEqual(reloaded.memory_base_commit, memory_tip)

    def test_start_and_memory_candidate_do_not_take_authority_from_the_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            contract = replace(fixture.contract, code_base_commit=fixture.move_official_code())
            assert contract.memory_worktree is not None
            source_cache = fixture.memory_repo / "memory.md"
            work_cache = contract.memory_worktree / "memory.md"
            original = source_cache.read_text(encoding="utf-8")
            tree = memory_candidate_tree(contract)
            for contents in (original, "<<<<<<< broken cache\n", None):
                for cache in (source_cache, work_cache):
                    if contents is None:
                        cache.unlink()
                    else:
                        cache.write_text(contents, encoding="utf-8")
                if git(fixture.memory_repo, "status", "--porcelain"):
                    git(fixture.memory_repo, "add", "-A")
                    git(fixture.memory_repo, "commit", "-m", "Historical cache contents")
                contract = replace(
                    contract, memory_base_commit=git(fixture.memory_repo, "rev-parse", "main")
                )

                prepared = prepare_memory_for_start(contract, WorktreeArgs(dry_run=True))

                self.assertEqual(prepared["state"], "compatible")
                self.assertEqual(prepared["lastVerifiedCodeCommit"], "")
                self.assertEqual(prepared["lastMemoryContentCommit"], "")
                self.assertEqual(memory_candidate_tree(contract), tree)
            assert contract.memory_worktree is not None
            (contract.memory_worktree / "real-memory.md").write_text(
                "new memory\n", encoding="utf-8"
            )
            self.assertNotEqual(memory_candidate_tree(contract), tree)

    def test_a_descendant_memory_ledger_that_dropped_a_source_row_is_current(self) -> None:
        """The projection is the ledger's authority, so a dropped row is not a sync refusal.

        Measured on the real master: thirteen rows the rebuild could not resolve (eleven
        stale duplicates whose code commits map to a different memory commit, two naming
        memory commits that exist nowhere) were dropped from the ledger by its own
        closeouts. This transaction required every row its source carried, so the master
        whose closeouts had been correct became permanently unsyncable, and the same rows
        refused the integration gate as well. The exclusion is the projection's to report
        (``read_ledger_source().excluded_rows`` -- pinned in ``test_memory_ledger``), and
        the sync is not a second place to restate that judgement.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            assert fixture.contract.memory_worktree is not None
            worktree = fixture.contract.memory_worktree
            # The work branch descends from its source and carries its own mapping for the
            # same code commit -- exactly the stale-duplicate shape, dropped from the table.
            commit_file(worktree, "onboarding/repo-a/README.md.md", "# README onboarding")
            content_commit = git(worktree, "rev-parse", "HEAD")
            write_ledger(
                worktree / "memory.md",
                create_initial_ledger("repo-a", fixture.code_base, content_commit),
            )
            git(worktree, "add", "memory.md")
            git(worktree, "commit", "-m", "Recompute the ledger from the commits")
            projected_head = git(worktree, "rev-parse", "HEAD")

            result = fixture.sync()

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "already-current")
            self.assertNotIn("dropped parent mapping", str(result.payload))
            # Nothing moved: the branch already carried the source, so the sync is a read.
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), projected_head)
            reloaded = load_contract(fixture.contract.contract_path)
            self.assertEqual(reloaded.memory_base_commit, fixture.memory_base)

    def test_memory_merge_discards_only_cache_conflicts_and_preserves_content_conflicts(
        self,
    ) -> None:
        for real_conflict in (False, True):
            with self.subTest(real_conflict=real_conflict), tempfile.TemporaryDirectory() as tmp:
                fixture = SyncFixture(Path(tmp))
                worktree = fixture.contract.memory_worktree
                assert worktree is not None
                (worktree / "work-content.md").write_text("work content\n", encoding="utf-8")
                (worktree / "memory.md").write_text("work cache\n", encoding="utf-8")
                if real_conflict:
                    (worktree / "README.md").write_text("work version\n", encoding="utf-8")
                git(worktree, "add", "-A")
                git(worktree, "commit", "-m", "Work content with a historical cache")
                before = git(worktree, "rev-parse", "HEAD")
                code_tip = fixture.move_official_code()
                source = fixture.memory_repo
                (source / "source-content.md").write_text("source content\n", encoding="utf-8")
                (source / "memory.md").write_text("source cache\n", encoding="utf-8")
                if real_conflict:
                    (source / "README.md").write_text("source version\n", encoding="utf-8")
                git(source, "add", "-A")
                git(
                    source,
                    "commit",
                    "-m",
                    render_memory_content_message("Source content", code_tip),
                )
                source_tip = git(source, "rev-parse", "HEAD")
                (worktree / "draft.md").write_text("uncommitted candidate\n", encoding="utf-8")
                (worktree / "memory.md").write_text("staged stale cache\n", encoding="utf-8")
                git(worktree, "add", "memory.md")

                result = (
                    fixture.sync(memory_sync_choice="merge-memory")
                    if real_conflict
                    else self._resume_staged_memory_with_unstaged_content(fixture)
                )

                if real_conflict:
                    self.assertEqual(
                        result.payload["state"], "sync-resolution-required", result.payload
                    )
                    self.assertEqual(section(result.payload, "resolution")["files"], ["README.md"])
                    self.assertEqual(git(worktree, "rev-parse", "HEAD"), before)
                    self.assertEqual(git(worktree, "rev-parse", "MERGE_HEAD"), source_tip)
                    self.assertEqual(
                        git(worktree, "diff", "--name-only", "--diff-filter=U"), "README.md"
                    )
                    (worktree / "README.md").write_text("resolved content\n", encoding="utf-8")
                    git(worktree, "add", "README.md")
                    (worktree / "memory.md").write_text("still malformed\n", encoding="utf-8")
                    preview = fixture.sync(resolution_action="continue", dry_run=True)
                    self.assertEqual(preview.returncode, 0, preview.payload)
                    self.assertEqual(git(worktree, "rev-parse", "HEAD"), before)
                    result = fixture.sync(resolution_action="continue")
                self.assertEqual(result.payload["state"], "synced", result.payload)
                merged = git(worktree, "rev-parse", "HEAD")
                self.assertEqual(
                    git(worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:],
                    [before, source_tip],
                )
                self.assertEqual(
                    git(worktree, "rev-list", merged, "--not", before, source_tip).split(), [merged]
                )
                self.assertEqual(
                    git(worktree, "ls-tree", "--name-only", "HEAD", "--", "memory.md"), ""
                )
                self.assertEqual(git(worktree, "ls-files", "--", "memory.md"), "")
                self.assertTrue((worktree / "memory.md").is_file())
                self.assertEqual(
                    (worktree / "draft.md").read_text(encoding="utf-8"), "uncommitted candidate\n"
                )
                self.assertEqual(
                    (worktree / "work-content.md").read_text(encoding="utf-8"),
                    "work content\n" if real_conflict else "post-admission content\n",
                )
                self.assertEqual(
                    (worktree / "source-content.md").read_text(encoding="utf-8"), "source content\n"
                )
                self.assertEqual(section(result.payload, "memory")["wip"]["paths"], ["draft.md"])
                self.assertEqual(git(worktree, "stash", "list"), "")

    def _resume_staged_memory_with_unstaged_content(self, fixture: SyncFixture):
        worktree = fixture.contract.memory_worktree
        assert worktree is not None
        with mock.patch.object(
            sync_transaction_git,
            "_finish_staged_memory_merge",
            side_effect=sync_transaction_git.SyncGitProofError("interrupted before merge commit"),
        ):
            stopped = fixture.sync(memory_sync_choice="merge-memory")
        self.assertEqual(stopped.payload["state"], "sync-git-proof-failed", stopped.payload)
        before = git(worktree, "rev-parse", "HEAD")
        merge_source = git(worktree, "rev-parse", "MERGE_HEAD")
        refs_before = tuple(
            git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
            for repo in (fixture.code_repo, fixture.memory_repo)
        )
        (worktree / "work-content.md").write_text("post-admission content\n", encoding="utf-8")
        (worktree / "memory.md").write_text("post-admission cache\n", encoding="utf-8")

        refused = fixture.sync()

        self.assertEqual(refused.payload["state"], "sync-git-proof-failed", refused.payload)
        self.assertIn("unstaged changes", str(refused.payload))
        self.assertEqual(git(worktree, "rev-parse", "HEAD"), before)
        self.assertEqual(git(worktree, "rev-parse", "MERGE_HEAD"), merge_source)
        self.assertEqual(
            tuple(
                git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
                for repo in (fixture.code_repo, fixture.memory_repo)
            ),
            refs_before,
        )
        git(worktree, "add", "work-content.md")
        return fixture.sync()

    def test_nonregular_journal_is_renamed_without_following_and_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            store = SyncOperationStore(fixture.contract.worktree_group)
            store.path.parent.mkdir(parents=True, exist_ok=True)
            target = Path(tmp) / "outside-journal-target"
            target.write_text("do not read or replace\n", encoding="utf-8")
            store.path.symlink_to(target)

            result = fixture.sync(resolution_action="cancel")

            self.assertEqual(result.payload["state"], "sync-cancelled-no-authority")
            metadata = json.loads(
                Path(str(result.payload["evidencePath"])).read_text(encoding="utf-8")
            )
            archived_entry = Path(metadata["rawArchivePath"])
            self.assertEqual(metadata["archiveKind"], "opaque-entry")
            self.assertTrue(archived_entry.is_symlink())
            self.assertEqual(Path(os.readlink(archived_entry)), target)
            self.assertEqual(target.read_text(encoding="utf-8"), "do not read or replace\n")


def section(payload: dict[str, object], key: str) -> dict[str, Any]:
    value = payload[key]
    assert isinstance(value, dict)
    return value


def make_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "agents-remember@example.invalid")
    git(path, "config", "user.name", "Agents Remember")
    commit_file(path, "README.md", "# Fixture")
    commit = git(path, "rev-parse", "HEAD")
    git(path, "update-ref", "refs/remotes/origin/main", commit)
    git(path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return commit


def commit_file(repo: Path, name: str, content: str) -> None:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content + "\n", encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", f"update {name}")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
