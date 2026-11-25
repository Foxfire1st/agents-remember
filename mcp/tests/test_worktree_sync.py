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

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))


from agents_remember.kernel.git_command import GitRunnerOptions
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.kernel.memory_ledger import create_initial_ledger, write_ledger
from agents_remember.worktrees import sync_transaction_git
from agents_remember.worktrees.integration.closeout.door_evidence import memory_candidate_tree
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import (
    _terminal_mutation_authority,
    remove_registered_worktree,
)
from agents_remember.worktrees.modules.startup.start_memory import prepare_memory_for_start
from agents_remember.worktrees.modules.sync import sync_result
from agents_remember.worktrees.modules.terminal_validation import terminal_preflight
from agents_remember.worktrees.sync_transaction_git import _merge as sync_git_merge
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

    def __init__(self, root: Path, *, converted: bool = True, source_branch: str = "main") -> None:
        self.root = root
        self.source_branch = source_branch
        self.code_repo = root / "repo-a"
        self.code_base = make_repo(self.code_repo)
        if source_branch != "main":
            git(self.code_repo, "switch", "-c", source_branch)
        self.memory_repo = root / "ar-coordination" / "memory-repos" / "ar-repo-a"
        memory_seed = make_repo(self.memory_repo)
        if source_branch != "main":
            git(self.memory_repo, "switch", "-c", source_branch)
        if converted:
            # MIK-R46 rule 8: shared fixtures build converted memory -- the layout marker
            # rides the base commit, so ordinary syncs run the converted routes.
            (self.memory_repo / "knowledge").mkdir(parents=True, exist_ok=True)
            (self.memory_repo / "knowledge" / "layout.json").write_text(
                '{\n  "conversion": "1",\n  "schema": "ar-memory-layout/v2"\n}\n',
                encoding="utf-8",
            )
        write_ledger(
            self.memory_repo / "memory.md",
            create_initial_ledger("repo-a", self.code_base, memory_seed),
        )
        if converted:
            git(self.memory_repo, "add", "memory.md", "knowledge/layout.json")
        else:
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
                source_branch=source_branch,
                work_branch="ar/sync-thing",
                base_commit=self.code_base,
            ),
            memory=RepoBranchPlan(
                repo_path=self.memory_repo,
                source_branch=source_branch,
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
            source_branch,
        )
        git(
            self.memory_repo,
            "worktree",
            "add",
            "-b",
            self.contract.memory_work_branch,
            str(self.contract.memory_worktree),
            source_branch,
        )
        write_contract(self.contract.contract_path, self.contract)

    def move_official_code(self) -> str:
        commit_file(self.code_repo, "src/new.py", "VALUE = 'landed'")
        return git(self.code_repo, "rev-parse", self.source_branch)

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
        return git(self.memory_repo, "rev-parse", self.source_branch)

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

    def test_terminal_removal_discards_only_the_memory_cache(self) -> None:
        for state in ("tracked", "missing", "staged", "untracked"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as tmp:
                fixture = SyncFixture(Path(tmp))
                contract = fixture.contract
                memory = contract.memory_worktree
                assert memory is not None
                cache = memory / "memory.md"
                if state == "untracked":
                    git(memory, "rm", "--cached", "memory.md")
                    git(memory, "commit", "-m", "Fixture without a tracked cache")
                if state == "missing":
                    cache.unlink()
                else:
                    cache.write_text("<<<<<<< disposable cache\n", encoding="utf-8")
                if state == "staged":
                    git(memory, "add", "-f", "memory.md")
                plan = terminal_preflight(contract, mode="abandon", force=False)
                self.assertFalse([row for row in plan.blockers if "worktree" in row])

                real = memory / "memory.md.other"
                real.write_text("keep real memory\n", encoding="utf-8")
                plan = terminal_preflight(contract, mode="abandon", force=False)
                self.assertIn({"worktree": "memory", "reason": "dirty"}, plan.blockers)
                authority = _terminal_mutation_authority(contract, operation="worktree_abandon")
                refused = remove_registered_worktree(
                    fixture.memory_repo, memory, False, authority=authority
                )
                self.assertFalse(refused["removed"], refused)
                self.assertEqual(real.read_text(encoding="utf-8"), "keep real memory\n")
                real.unlink()

                code_file = contract.code_worktree / "memory.md"
                code_file.write_text("keep code content\n", encoding="utf-8")
                refused = remove_registered_worktree(
                    fixture.code_repo, contract.code_worktree, False, authority=authority
                )
                self.assertFalse(refused["removed"], refused)
                removed = remove_registered_worktree(
                    fixture.memory_repo, memory, False, authority=authority
                )
                self.assertTrue(removed["removed"], removed)
                self.assertEqual(code_file.read_text(encoding="utf-8"), "keep code content\n")

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

    def test_memory_merge_settles_content_conflicts_in_the_transaction(self) -> None:
        """A memory merge of unconverted lines: the clean one resumes, the conflicted one is retained.

        Knowledge is text in Git and merges structurally (MIK-R26 rule 2); those cases are in
        ``test_worktree_sync_knowledge_merge.py``. This case is the plain Git merge that remains
        for lines that hold no converted memory.
        """

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

    def test_cancel_after_a_memory_conflict_restores_the_branch_and_its_tracked_cache(
        self,
    ) -> None:
        """L37 (P2 task 1): a line that still tracks ``memory.md`` cancels a conflicted memory sync.

        The memory merge untracks the cache; without restoring it first, ``git merge --abort`` met
        it as an untracked file and the cancel was refused, leaving the merge in place.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))  # tracks memory.md, the dormant lines' shape
            worktree = fixture.contract.memory_worktree
            assert worktree is not None
            (worktree / "README.md").write_text("work version\n", encoding="utf-8")
            git(worktree, "commit", "-qam", "leaf edits README")
            before = git(worktree, "rev-parse", "HEAD")
            code_tip = fixture.move_official_code()
            (fixture.memory_repo / "README.md").write_text("source version\n", encoding="utf-8")
            git(fixture.memory_repo, "add", "README.md")
            git(
                fixture.memory_repo,
                "commit",
                "-m",
                render_memory_content_message("Source", code_tip),
            )
            conflicted = fixture.sync(memory_sync_choice="merge-memory")
            self.assertEqual(conflicted.payload["state"], "sync-resolution-required")

            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), before)
            with self.assertRaises(AssertionError):
                git(worktree, "rev-parse", "-q", "--verify", "MERGE_HEAD")
            self.assertEqual(git(worktree, "status", "--porcelain"), "")
            self.assertEqual(git(worktree, "ls-files", "--", "memory.md"), "memory.md")
            again = fixture.sync(memory_sync_choice="merge-memory")
            self.assertEqual(again.payload["state"], "sync-resolution-required", again.payload)

    def _resume_staged_memory_with_unstaged_content(self, fixture: SyncFixture):
        worktree = fixture.contract.memory_worktree
        assert worktree is not None
        with mock.patch.object(
            sync_git_merge,
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


def _retained_code_merge(tmp_path: Path, incoming_file: str) -> SyncFixture:
    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.code_worktree
    source = fixture.code_repo
    if incoming_file == "shared.txt":
        # A common file with edits far enough apart for Git to merge without a conflict.
        commit_file(source, incoming_file, "\n".join(f"line {n}" for n in range(15)))
        git(worktree, "merge", "--ff-only", git(source, "rev-parse", "HEAD"))
        shared = (worktree / incoming_file).read_text(encoding="utf-8")
        commit_file(worktree, incoming_file, shared.replace("line 0\n", "task edit\n").rstrip())
        incoming = shared.replace("line 14\n", "incoming edit \n").removesuffix("\n")
        commit_file(source, incoming_file, incoming)
    else:
        commit_file(source, incoming_file, "incoming edit ")
    _stage_code_conflict_resolution(fixture)
    return fixture


def _stage_code_conflict_resolution(fixture: SyncFixture) -> None:
    worktree = fixture.contract.code_worktree
    source = fixture.code_repo
    commit_file(worktree, "README.md", "task version")
    commit_file(source, "README.md", "incoming version")
    fixture.map_official_memory(git(source, "rev-parse", "HEAD"))

    retained = fixture.sync()
    assert retained.payload["state"] == "sync-resolution-required", retained.payload
    assert section(retained.payload, "resolution")["files"] == ["README.md"]
    (worktree / "README.md").write_text("resolved version\n", encoding="utf-8")
    git(worktree, "add", "--", "README.md")


@pytest.mark.parametrize("incoming_file", ["incoming.txt", "shared.txt"])
def test_sync_resolution_accepts_incoming_whitespace(tmp_path: Path, incoming_file: str) -> None:
    fixture = _retained_code_merge(tmp_path, incoming_file)
    worktree = fixture.contract.code_worktree
    task_head = git(worktree, "rev-parse", "HEAD")
    source_head = git(worktree, "rev-parse", "MERGE_HEAD")

    preview = fixture.sync(resolution_action="continue", dry_run=True)
    continued = fixture.sync(resolution_action="continue")

    assert preview.payload["state"] == "would-continue-sync-resolution", preview.payload
    assert continued.payload["state"] == "synced", continued.payload
    parents = git(worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
    assert parents == [task_head, source_head]
    carried = (worktree / incoming_file).read_text(encoding="utf-8")
    assert "incoming edit \n" in carried
    if incoming_file == "shared.txt":
        assert carried.startswith("task edit\n")


@pytest.mark.parametrize(
    ("path", "text", "diagnosis"),
    [
        ("README.md", "<<<<<<< resolution\n", "leftover conflict marker"),
        ("beside.txt", "resolution edit \n", "trailing whitespace."),
    ],
)
def test_sync_resolution_refuses_new_findings(
    tmp_path: Path, path: str, text: str, diagnosis: str
) -> None:
    fixture = _retained_code_merge(tmp_path, "incoming.txt")
    worktree = fixture.contract.code_worktree
    task_head = git(worktree, "rev-parse", "HEAD")
    (worktree / path).write_text(text, encoding="utf-8")
    git(worktree, "add", "--", path)

    preview = fixture.sync(resolution_action="continue", dry_run=True)
    continued = fixture.sync(resolution_action="continue")

    for result in (preview, continued):
        assert result.payload["state"] == "sync-resolution-incomplete", result.payload
        summary = str(result.payload["summary"])
        assert f"{path}:1: {diagnosis}" in summary
        assert "incoming.txt:" not in summary
    assert git(worktree, "rev-parse", "HEAD") == task_head


def test_sync_resolution_reports_commit_check_with_incoming_whitespace(tmp_path: Path) -> None:
    fixture = _retained_code_merge(tmp_path, "incoming.txt")
    hook = fixture.code_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\necho 'fixture commit check refused' >&2\nexit 1\n", encoding="utf-8"
    )
    hook.chmod(0o755)

    stopped = fixture.sync(resolution_action="continue")

    assert stopped.payload["state"] == "sync-commit-check-failed", stopped.payload
    summary = str(stopped.payload["summary"])
    assert "fixture commit check refused" in summary
    assert "incoming.txt: from the carried line" in summary


@pytest.mark.parametrize("command", ["--check", "--name-only"], ids=["diagnostic", "paths"])
def test_sync_resolution_reports_failed_whitespace_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    fixture = _retained_code_merge(tmp_path, "incoming.txt")
    worktree = fixture.contract.code_worktree
    (worktree / "beside.txt").write_text("authored whitespace \n", encoding="utf-8")
    git(worktree, "add", "--", "beside.txt")
    task_head = git(worktree, "rev-parse", "HEAD")
    source_head = git(worktree, "rev-parse", "MERGE_HEAD")
    compare = sync_git_merge.run_git_bytes

    def fail_incoming_comparison(
        repo: Path, args: list[str], options: GitRunnerOptions | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        if repo == worktree and args[:3] == ["diff", "--cached", command] and source_head in args:
            return subprocess.CompletedProcess(args, 128, b"", b"fatal: unreadable incoming tree\n")
        return compare(repo, args, options)

    monkeypatch.setattr(sync_git_merge, "run_git_bytes", fail_incoming_comparison)
    preview = fixture.sync(resolution_action="continue", dry_run=True)
    continued = fixture.sync(resolution_action="continue")

    for result in (preview, continued):
        assert result.payload["state"] == "sync-resolution-incomplete", result.payload
        assert "fatal: unreadable incoming tree" in str(result.payload["summary"])
    assert "resolution_action='cancel'" in str(continued.payload["summary"])
    assert git(worktree, "rev-parse", "HEAD") == task_head
    assert git(worktree, "rev-parse", "MERGE_HEAD") == source_head


def test_sync_resolution_reports_a_transient_comparison_failure_before_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _retained_code_merge(tmp_path, "incoming.txt")
    worktree = fixture.contract.code_worktree
    task_head = git(worktree, "rev-parse", "HEAD")
    source_head = git(worktree, "rev-parse", "MERGE_HEAD")
    hook = fixture.code_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nprintf called > hook-called\n", encoding="utf-8")
    hook.chmod(0o755)
    compare = sync_git_merge.run_git_bytes
    attempts = 0

    def fail_once(
        repo: Path, args: list[str], options: GitRunnerOptions | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal attempts
        if repo == worktree and args[:3] == ["diff", "--cached", "--check"] and source_head in args:
            attempts += 1
            if attempts == 1:
                return subprocess.CompletedProcess(
                    args, 128, b"", b"fatal: incoming comparison temporarily unavailable\n"
                )
        return compare(repo, args, options)

    monkeypatch.setattr(sync_git_merge, "run_git_bytes", fail_once)
    stopped = fixture.sync(resolution_action="continue")

    assert stopped.payload["state"] == "sync-resolution-incomplete", stopped.payload
    assert "fatal: incoming comparison temporarily unavailable" in str(stopped.payload["summary"])
    assert attempts == 1
    assert not (worktree / "hook-called").exists()
    assert git(worktree, "rev-parse", "HEAD") == task_head
    assert git(worktree, "rev-parse", "MERGE_HEAD") == source_head


@pytest.mark.skipif(os.name == "nt", reason="Git on Windows rejects LF in paths")
def test_sync_resolution_keeps_identical_multiline_diagnostics_with_their_paths(
    tmp_path: Path,
) -> None:
    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.code_worktree
    multiline = "alias:1: leftover conflict marker\nsecond"
    commit_file(fixture.code_repo, multiline, "bad ")
    commit_file(worktree, "alias", "<<<<<<< inherited marker")
    commit_file(worktree, "second", "bad ")
    _stage_code_conflict_resolution(fixture)

    task_head = git(worktree, "rev-parse", "HEAD")
    source_head = git(worktree, "rev-parse", "MERGE_HEAD")
    for parent, names in ((task_head, ("alias", "second")), (source_head, (multiline,))):
        for path in names:
            assert git(worktree, "rev-parse", f"{parent}:{path}") == git(
                worktree, "rev-parse", f":{path}"
            )
        check = subprocess.run(
            ["git", "diff", "--cached", "--check", "--no-color", parent, "--"],
            cwd=worktree,
            capture_output=True,
            check=False,
        )
        assert check.returncode == 2
        assert (
            check.stdout
            == b"alias:1: leftover conflict marker\nsecond:1: trailing whitespace.\n+bad \n"
        )

    preview = fixture.sync(resolution_action="continue", dry_run=True)
    continued = fixture.sync(resolution_action="continue")

    assert preview.payload["state"] == "would-continue-sync-resolution", preview.payload
    assert continued.payload["state"] == "synced", continued.payload


@pytest.mark.skipif(os.name == "nt", reason="Git on Windows rejects LF in paths")
def test_sync_resolution_names_ambiguous_incoming_diagnostic(tmp_path: Path) -> None:
    fixture = SyncFixture(tmp_path)
    source = fixture.code_repo
    commit_file(source, "alias", "<<<<<<< inherited marker")
    commit_file(source, "second", "bad ")
    commit_file(source, "alias:1: leftover conflict marker\nsecond", "bad ")
    _stage_code_conflict_resolution(fixture)

    worktree = fixture.contract.code_worktree
    task_head = git(worktree, "rev-parse", "HEAD")
    source_head = git(worktree, "rev-parse", "MERGE_HEAD")
    assert git(worktree, "diff", "--cached", "--check", source_head, "--") == ""
    preview = fixture.sync(resolution_action="continue", dry_run=True)

    assert preview.payload["state"] == "sync-resolution-incomplete", preview.payload
    summary = str(preview.payload["summary"])
    assert f"Cannot attribute a staged Git diagnostic against {task_head}" in summary
    assert "to exactly one changed path" in summary
    assert "alias:1: leftover conflict marker" in summary


@pytest.mark.skipif(os.name == "nt", reason="Git on Windows rejects LF in paths")
def test_sync_resolution_refuses_authored_whitespace_in_multiline_path(tmp_path: Path) -> None:
    fixture = _retained_code_merge(tmp_path, "incoming.txt")
    worktree = fixture.contract.code_worktree
    path = "authored\nresolution.txt"
    (worktree / path).write_text("resolution edit \n", encoding="utf-8")
    git(worktree, "add", "--", path)

    continued = fixture.sync(resolution_action="continue")

    assert continued.payload["state"] == "sync-resolution-incomplete", continued.payload
    summary = str(continued.payload["summary"])
    assert f"{path}:1: trailing whitespace." in summary
    assert "incoming.txt:" not in summary


def test_sync_resolution_preserves_rename_and_whitespace_attribute_checks(tmp_path: Path) -> None:
    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.code_worktree
    source = fixture.code_repo
    original = "carried whitespace \n" + "".join(f"unchanged line {n}\n" for n in range(12))
    commit_file(source, "before.txt", original.removesuffix("\n"))
    commit_file(source, ".gitattributes", "ignored.txt -whitespace")
    git(worktree, "merge", "--ff-only", git(source, "rev-parse", "HEAD"))
    _stage_code_conflict_resolution(fixture)

    git(worktree, "mv", "--", "before.txt", "after.txt")
    (worktree / "ignored.txt").write_text("exempt authored whitespace \n", encoding="utf-8")
    git(worktree, "add", "--", "ignored.txt")
    preview = fixture.sync(resolution_action="continue", dry_run=True)
    assert preview.payload["state"] == "would-continue-sync-resolution", preview.payload

    (worktree / "after.txt").write_text(
        original + "checked authored whitespace \n", encoding="utf-8"
    )
    git(worktree, "add", "--", "after.txt")
    assert git(
        worktree, "diff", "--cached", "--name-status", "--", "before.txt", "after.txt"
    ).startswith("R")
    continued = fixture.sync(resolution_action="continue")

    assert continued.payload["state"] == "sync-resolution-incomplete", continued.payload
    summary = str(continued.payload["summary"])
    assert "after.txt:14: trailing whitespace." in summary
    assert "after.txt:1:" not in summary
    assert "ignored.txt:" not in summary


def test_automatic_memory_merge_accepts_incoming_whitespace(tmp_path: Path) -> None:
    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    commit_file(worktree, "task.txt", "task memory")
    source = fixture.memory_repo
    code_tip = fixture.move_official_code()
    (source / "incoming.txt").write_text("incoming memory \n", encoding="utf-8")
    git(source, "add", "--", "incoming.txt")
    git(source, "commit", "-m", render_memory_content_message("Incoming memory", code_tip))
    task_head = git(worktree, "rev-parse", "HEAD")
    source_head = git(source, "rev-parse", "HEAD")

    result = fixture.sync(memory_sync_choice="merge-memory")

    assert result.payload["state"] == "synced", result.payload
    parents = git(worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
    assert parents == [task_head, source_head]
    assert (worktree / "incoming.txt").read_text(encoding="utf-8") == "incoming memory \n"


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
