"""Issue #54: worktree_sync pulls the moved official line into a live worktree."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.memory_ledger import (
    create_initial_ledger,
    parse_ledger_text,
    prepend_mapping,
    write_ledger,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.sync import sync_result
from agents_remember.worktrees.sync_transaction import sync_contract_under_authority
from agents_remember.worktrees.sync_transaction_git import read_ref
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncOperationStore,
    sync_side_base_ref,
    sync_side_refs,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    default_series_contract,
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
        """Land an official memory change plus a ledger row mapping code_tip."""
        commit_file(self.memory_repo, "onboarding/src/new.py.md", "# new.py onboarding")
        content_commit = git(self.memory_repo, "rev-parse", "HEAD")
        ledger_path = self.memory_repo / "memory.md"
        ledger = parse_ledger_text(ledger_path.read_text(encoding="utf-8"))
        write_ledger(ledger_path, prepend_mapping(ledger, code_tip, content_commit))
        git(self.memory_repo, "add", "memory.md")
        git(self.memory_repo, "commit", "-m", "Map new code tip")
        return git(self.memory_repo, "rev-parse", "main")

    def sync(self, **kwargs: Any):
        return sync_result(WorktreeArgs(contract_path=self.contract.contract_path, **kwargs))


class SeriesSyncFixture:
    """Atomic-series integration refs with no persistent branch worktrees."""

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
        git(self.code_repo, "branch", "ar/master", self.code_base)
        git(self.memory_repo, "branch", "ar/master", self.memory_base)
        self.contract = default_series_contract(
            ContractTask(
                name="Master",
                repo_name="repo-a",
                coordination_root=root / "ar-coordination",
                workflow_kind="light-task",
                memory_mode="external",
            ),
            code=RepoBranchPlan(
                repo_path=self.code_repo,
                source_branch="main",
                work_branch="ar/master",
                base_commit=self.code_base,
            ),
            memory=RepoBranchPlan(
                repo_path=self.memory_repo,
                source_branch="main",
                work_branch="ar/master",
                base_commit=self.memory_base,
            ),
        )
        write_contract(self.contract.contract_path, self.contract)

    def move_official_pair(self) -> tuple[str, str]:
        commit_file(self.code_repo, "src/new.py", "VALUE = 'landed'")
        code_tip = git(self.code_repo, "rev-parse", "main")
        commit_file(self.memory_repo, "onboarding/src/new.py.md", "# new.py onboarding")
        content_commit = git(self.memory_repo, "rev-parse", "HEAD")
        ledger_path = self.memory_repo / "memory.md"
        ledger = parse_ledger_text(ledger_path.read_text(encoding="utf-8"))
        write_ledger(ledger_path, prepend_mapping(ledger, code_tip, content_commit))
        git(self.memory_repo, "add", "memory.md")
        git(self.memory_repo, "commit", "-m", "Map new code tip")
        return code_tip, git(self.memory_repo, "rev-parse", "main")


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

    def test_canonical_series_sync_uses_temporary_worktrees_and_cleans_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SeriesSyncFixture(Path(tmp))
            code_tip, memory_tip = fixture.move_official_pair()

            result = sync_contract_under_authority(
                fixture.contract,
                WorktreeArgs(contract_path=fixture.contract.contract_path),
                fetch={},
            )

            self.assertEqual(result.payload["state"], "synced")
            self.assertEqual(git(fixture.code_repo, "rev-parse", "ar/master"), code_tip)
            self.assertEqual(git(fixture.memory_repo, "rev-parse", "ar/master"), memory_tip)
            reloaded = load_contract(fixture.contract.contract_path)
            self.assertEqual(reloaded.code_base_commit, code_tip)
            self.assertEqual(reloaded.memory_base_commit, memory_tip)
            record = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(record, SyncOperationRecord)
            self.assertEqual(
                Path(record.code.worktree), fixture.contract.worktree_group / ".sync/code"
            )
            assert record.memory is not None
            self.assertEqual(
                Path(record.memory.worktree),
                fixture.contract.worktree_group / ".sync/memory",
            )
            self.assertFalse((fixture.contract.worktree_group / ".sync/code").exists())
            self.assertFalse((fixture.contract.worktree_group / ".sync/memory").exists())
            for side, repository in (
                (record.code, fixture.code_repo),
                (record.memory, fixture.memory_repo),
            ):
                for ref in (side.baseBackupRef, side.backupRef, side.sourceBackupRef):
                    self.assertIsNone(ref_value(repository, ref))

    def test_mid_cycle_official_line_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            fixture.move_official_code()  # no ledger mapping for the new tip

            result = fixture.sync()

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "blocked")
            self.assertIn("mid-cycle", str(result.payload["summary"]))

    def test_already_current_pair_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            result = fixture.sync()
            self.assertEqual(result.payload["state"], "already-current")

    def test_dry_run_previews_without_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)

            result = fixture.sync(dry_run=True)

            self.assertEqual(result.payload["state"], "would-sync")
            self.assertEqual(
                git(fixture.contract.code_worktree, "rev-parse", "HEAD"), fixture.code_base
            )
            reloaded = load_contract(fixture.contract.contract_path)
            self.assertEqual(reloaded.code_base_commit, fixture.code_base)
            self.assertEqual(reloaded.sync_log, ())

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

    def test_local_memory_commits_need_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            assert fixture.contract.memory_worktree is not None
            commit_file(
                fixture.contract.memory_worktree,
                "onboarding/local.md",
                "# local memory work",
            )
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)

            result = fixture.sync()

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "memory-sync-choice-required")
            self.assertEqual(section(result.payload, "memory")["state"], "pending")
            self.assertEqual(result.payload["nextRequiredArgs"], ["memory_sync_choice"])

    def test_invalid_memory_choice_refuses_before_journal_or_ref_admission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            result = fixture.sync(memory_sync_choice="invalid")

            self.assertEqual(result.payload["state"], "sync-input-invalid")
            self.assertFalse(SyncOperationStore(fixture.contract.worktree_group).path.exists())
            backup, source = sync_side_refs(fixture.contract.contract_path, "code")
            base = sync_side_base_ref(fixture.contract.contract_path, "code")
            self.assertEqual(ref_value(fixture.code_repo, base), None)
            self.assertEqual(ref_value(fixture.code_repo, backup), None)
            self.assertEqual(ref_value(fixture.code_repo, source), None)

    def test_conflict_cancel_preview_is_read_only_and_cancel_retry_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            commit_file(fixture.contract.code_worktree, "README.md", "work version")
            pre_sync = git(fixture.contract.code_worktree, "rev-parse", "HEAD")
            commit_file(fixture.code_repo, "README.md", "official version")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)
            blocked = fixture.sync()
            self.assertEqual(blocked.payload["state"], "sync-resolution-required")
            store = SyncOperationStore(fixture.contract.worktree_group)
            before = store.path.read_bytes()

            preview = fixture.sync(resolution_action="cancel", dry_run=True)

            self.assertEqual(preview.payload["state"], "would-cancel-sync")
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(
                git(fixture.contract.code_worktree, "rev-parse", "MERGE_HEAD"), code_tip
            )

            cancelled = fixture.sync(resolution_action="cancel")
            retried = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.payload["state"], "sync-cancelled")
            self.assertEqual(retried.payload["state"], "sync-cancelled")
            self.assertEqual(git(fixture.contract.code_worktree, "rev-parse", "HEAD"), pre_sync)
            self.assertIsNone(
                ref_value(
                    fixture.code_repo, sync_side_refs(fixture.contract.contract_path, "code")[0]
                )
            )

    def test_terminal_continue_preview_does_not_cleanup_leftover_authority_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)
            self.assertEqual(fixture.sync().payload["state"], "synced")
            record = SyncOperationStore(fixture.contract.worktree_group).read()
            assert isinstance(record, SyncOperationRecord)
            git(fixture.code_repo, "update-ref", record.code.baseBackupRef, record.code.baseCommit)

            replay = fixture.sync(resolution_action="continue", dry_run=True)

            self.assertEqual(replay.payload["state"], "synced")
            self.assertEqual(
                ref_value(fixture.code_repo, record.code.baseBackupRef),
                record.code.baseCommit,
            )

    def test_malformed_journal_without_refs_is_archived_and_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            store = SyncOperationStore(fixture.contract.worktree_group)
            store.path.parent.mkdir(parents=True, exist_ok=True)
            raw = b"\xffbroken-sync-journal"
            store.path.write_bytes(raw)

            result = fixture.sync(resolution_action="cancel")

            self.assertEqual(result.payload["state"], "sync-cancelled-no-authority")
            evidence = Path(str(result.payload["evidencePath"]))
            metadata = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(Path(metadata["rawArchivePath"]).read_bytes(), raw)
            quarantined = store.read()
            assert quarantined is not None
            self.assertEqual(getattr(quarantined, "state", None), "cancelled-no-authority")

    def test_identity_invalid_terminal_without_refs_uses_the_same_quarantine_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)
            self.assertEqual(fixture.sync().payload["state"], "synced")
            store = SyncOperationStore(fixture.contract.worktree_group)
            payload = json.loads(store.path.read_text(encoding="utf-8"))
            payload["contractPath"] = (Path(tmp) / "another-contract.md").as_posix()
            corrupted = (json.dumps(payload, indent=2) + "\n").encode()
            store.path.write_bytes(corrupted)

            result = fixture.sync(resolution_action="cancel")

            self.assertEqual(result.payload["state"], "sync-cancelled-no-authority")
            metadata = json.loads(
                Path(str(result.payload["evidencePath"])).read_text(encoding="utf-8")
            )
            self.assertEqual(Path(metadata["rawArchivePath"]).read_bytes(), corrupted)

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

    def test_partial_refs_restore_complete_side_and_emit_exact_manual_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            code_backup, code_source = sync_side_refs(fixture.contract.contract_path, "code")
            code_base = sync_side_base_ref(fixture.contract.contract_path, "code")
            code_head = git(fixture.contract.code_worktree, "rev-parse", "HEAD")
            git(fixture.code_repo, "update-ref", code_base, fixture.code_base)
            git(fixture.code_repo, "update-ref", code_backup, code_head)
            git(fixture.code_repo, "update-ref", code_source, fixture.code_base)
            memory_base_ref = sync_side_base_ref(fixture.contract.contract_path, "memory")
            git(fixture.memory_repo, "update-ref", memory_base_ref, fixture.memory_base)
            store = SyncOperationStore(fixture.contract.worktree_group)
            store.path.parent.mkdir(parents=True, exist_ok=True)
            store.path.write_bytes(b"{partial")

            result = fixture.sync(resolution_action="cancel")

            self.assertEqual(result.payload["state"], "sync-cancel-manual-repair-required")
            repair = section(result.payload, "manualRepair")
            self.assertEqual(section(result.payload, "nextArgs")["resolution_action"], "cancel")
            self.assertEqual(ref_value(fixture.code_repo, code_base), None)
            self.assertEqual(ref_value(fixture.code_repo, code_backup), None)
            self.assertEqual(ref_value(fixture.code_repo, code_source), None)
            self.assertEqual(ref_value(fixture.memory_repo, memory_base_ref), fixture.memory_base)
            memory = next(item for item in repair["sides"] if item["side"] == "memory")
            self.assertEqual(memory["refs"]["base"]["name"], memory_base_ref)

    def test_skip_memory_choice_advances_code_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            assert fixture.contract.memory_worktree is not None
            commit_file(
                fixture.contract.memory_worktree,
                "onboarding/local.md",
                "# local memory work",
            )
            code_tip = fixture.move_official_code()
            fixture.map_official_memory(code_tip)

            result = fixture.sync(memory_sync_choice="skip-memory")

            self.assertEqual(result.payload["state"], "sync-pass-completed-memory-skipped")
            self.assertEqual(section(result.payload, "memory")["state"], "completed")
            self.assertEqual(section(result.payload, "memory")["plan"], "skip")
            reloaded = load_contract(fixture.contract.contract_path)
            self.assertEqual(reloaded.code_base_commit, code_tip)
            self.assertEqual(reloaded.memory_base_commit, fixture.memory_base)

    def test_merge_memory_choice_merges_disjoint_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            assert fixture.contract.memory_worktree is not None
            commit_file(
                fixture.contract.memory_worktree,
                "onboarding/local.md",
                "# local memory work",
            )
            code_tip = fixture.move_official_code()
            memory_tip = fixture.map_official_memory(code_tip)

            result = fixture.sync(memory_sync_choice="merge-memory")

            self.assertEqual(result.payload["state"], "synced")
            self.assertEqual(section(result.payload, "memory")["state"], "completed")
            self.assertEqual(section(result.payload, "memory")["plan"], "merge")
            reloaded = load_contract(fixture.contract.contract_path)
            self.assertEqual(reloaded.memory_base_commit, memory_tip)

    def test_chosen_memory_conflict_is_retained_and_validated_on_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            assert fixture.contract.memory_worktree is not None
            commit_file(
                fixture.contract.memory_worktree,
                "onboarding/src/new.py.md",
                "# local interpretation",
            )
            code_tip = fixture.move_official_code()
            memory_tip = fixture.map_official_memory(code_tip)

            blocked = fixture.sync(memory_sync_choice="merge-memory")

            self.assertEqual(blocked.payload["state"], "sync-resolution-required")
            self.assertEqual(section(blocked.payload, "resolution")["side"], "memory")
            self.assertIn(
                "onboarding/src/new.py.md",
                section(blocked.payload, "resolution")["files"],
            )
            target = fixture.contract.memory_worktree / "onboarding/src/new.py.md"
            target.write_text("# resolved interpretation\n", encoding="utf-8")
            git(fixture.contract.memory_worktree, "add", "onboarding/src/new.py.md")

            continued = fixture.sync(resolution_action="continue")

            self.assertEqual(continued.payload["state"], "synced")
            self.assertEqual(
                load_contract(fixture.contract.contract_path).memory_base_commit, memory_tip
            )
            ledger = parse_ledger_text(
                (fixture.contract.memory_worktree / "memory.md").read_text(encoding="utf-8")
            )
            self.assertEqual(sum(row.code_commit == code_tip for row in ledger.rows), 1)


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


def ref_value(repo: Path, ref: str) -> str | None:
    return read_ref(repo, ref)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
