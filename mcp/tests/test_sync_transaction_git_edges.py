"""Focused proof-boundary tests for resumable-sync Git mechanics."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from agents_remember.kernel.memory_ledger import LedgerError, LedgerRow
from agents_remember.models.worktree import SyncSide
from agents_remember.worktrees import sync_transaction_git as sync_git
from agents_remember.worktrees.sync_transaction_state import SyncSidePlan, SyncSideRecord

SHA_BASE = "0" * 40
SHA_PRE = "1" * 40
SHA_SOURCE = "2" * 40
SHA_RESULT = "3" * 40


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _side(
    root: Path,
    *,
    side: SyncSide = "code",
    plan: SyncSidePlan = "merge",
    temporary: bool = False,
) -> SyncSideRecord:
    return SyncSideRecord(
        side=side,
        repository=(root / f"{side}-repo").as_posix(),
        worktree=(root / f"{side}-worktree").as_posix(),
        sourceBranch="source",
        workBranch="work",
        sourceCommit=SHA_SOURCE,
        preSyncHead=SHA_PRE,
        baseCommit=SHA_BASE,
        backupRef=f"refs/agents-remember/sync/test/{side}/pre-sync",
        sourceBackupRef=f"refs/agents-remember/sync/test/{side}/source",
        baseBackupRef=f"refs/agents-remember/sync/test/{side}/base",
        plan=plan,
        temporary=temporary,
    )


def test_read_ref_rejects_invalid_and_unreadable_refs(tmp_path: Path) -> None:
    with (
        mock.patch.object(sync_git, "run_git", return_value=_result(1, stderr="invalid")),
        pytest.raises(sync_git.SyncGitProofError, match="invalid"),
    ):
        sync_git.read_ref(tmp_path, "bad ref")

    with (
        mock.patch.object(
            sync_git,
            "run_git",
            side_effect=[_result(), _result(2, stderr="cannot inspect")],
        ),
        pytest.raises(sync_git.SyncGitProofError, match="cannot inspect"),
    ):
        sync_git.read_ref(tmp_path, "refs/heads/test")


def test_create_pinned_ref_is_idempotent_and_rejects_changes(tmp_path: Path) -> None:
    with mock.patch.object(sync_git, "read_ref", return_value=SHA_SOURCE):
        sync_git.create_pinned_ref(tmp_path, "refs/test", SHA_SOURCE)
    with (
        mock.patch.object(sync_git, "read_ref", return_value=SHA_PRE),
        pytest.raises(sync_git.SyncGitProofError, match="pins another commit"),
    ):
        sync_git.create_pinned_ref(tmp_path, "refs/test", SHA_SOURCE)
    with (
        mock.patch.object(sync_git, "read_ref", return_value=None),
        mock.patch.object(sync_git, "run_git", return_value=_result(1, stderr="create failed")),
        pytest.raises(sync_git.SyncGitProofError, match="create failed"),
    ):
        sync_git.create_pinned_ref(tmp_path, "refs/test", SHA_SOURCE)


def test_delete_pinned_ref_rejects_changes_and_delete_failure(tmp_path: Path) -> None:
    with (
        mock.patch.object(sync_git, "read_ref", return_value=SHA_PRE),
        pytest.raises(sync_git.SyncGitProofError, match="changed before cleanup"),
    ):
        sync_git.delete_pinned_ref(tmp_path, "refs/test", SHA_SOURCE)
    with (
        mock.patch.object(sync_git, "read_ref", return_value=SHA_SOURCE),
        mock.patch.object(sync_git, "run_git", return_value=_result(1, stderr="delete failed")),
        pytest.raises(sync_git.SyncGitProofError, match="delete failed"),
    ):
        sync_git.delete_pinned_ref(tmp_path, "refs/test", SHA_SOURCE)


def test_temporary_worktree_creation_and_checkout_identity_fail_loud(tmp_path: Path) -> None:
    side = _side(tmp_path, temporary=True)
    with (
        mock.patch.object(sync_git, "run_git", return_value=_result(1, stderr="add failed")),
        pytest.raises(sync_git.SyncGitProofError, match="add failed"),
    ):
        sync_git.ensure_temporary_worktree(side)

    with (
        mock.patch.object(
            sync_git,
            "repository_identity",
            side_effect=[Path("/other"), Path("/repo")],
        ),
        pytest.raises(sync_git.SyncGitProofError, match="repository identity"),
    ):
        sync_git.require_side_checkout(side)
    with (
        mock.patch.object(sync_git, "repository_identity", return_value=Path("/repo")),
        mock.patch.object(sync_git, "current_branch", return_value="other"),
        pytest.raises(sync_git.SyncGitProofError, match="journaled branch"),
    ):
        sync_git.require_side_checkout(side)


def test_temporary_worktree_removal_handles_absence_dirty_state_and_failure(
    tmp_path: Path,
) -> None:
    side = _side(tmp_path, temporary=True)
    sync_git.remove_temporary_worktree(side)

    Path(side.worktree).mkdir(parents=True)
    with (
        mock.patch.object(sync_git, "require_side_checkout"),
        mock.patch.object(sync_git, "git_status", return_value="dirty"),
        pytest.raises(sync_git.SyncGitProofError, match="not clean"),
    ):
        sync_git.remove_temporary_worktree(side)
    with (
        mock.patch.object(sync_git, "require_side_checkout"),
        mock.patch.object(sync_git, "git_status", return_value=""),
        mock.patch.object(sync_git, "run_git", return_value=_result(1, stderr="remove failed")),
        pytest.raises(sync_git.SyncGitProofError, match="remove failed"),
    ):
        sync_git.remove_temporary_worktree(side)


def test_git_status_and_unmerged_paths_translate_command_failures(tmp_path: Path) -> None:
    with (
        mock.patch.object(sync_git, "run_git", return_value=_result(1, stderr="status failed")),
        pytest.raises(sync_git.SyncGitProofError, match="status failed"),
    ):
        sync_git.git_status(tmp_path)
    with (
        mock.patch.object(sync_git, "run_git", return_value=_result(1, stderr="diff failed")),
        pytest.raises(sync_git.SyncGitProofError, match="diff failed"),
    ):
        sync_git.unmerged_paths(tmp_path)


def test_side_merge_completed_validates_an_exact_operation_head(tmp_path: Path) -> None:
    side = _side(tmp_path)
    with mock.patch.object(sync_git, "merge_head", return_value=SHA_SOURCE):
        assert sync_git.side_merge_completed(side) is False
    with (
        mock.patch.object(sync_git, "merge_head", return_value=None),
        mock.patch.object(sync_git, "head_commit", return_value=SHA_RESULT),
        mock.patch.object(sync_git, "exact_created_head", return_value=True),
        mock.patch.object(sync_git, "validate_completed_side") as validate,
    ):
        assert sync_git.side_merge_completed(side) is True
    validate.assert_called_once_with(side, SHA_RESULT)


def test_start_side_merge_replays_retained_and_completed_states(tmp_path: Path) -> None:
    memory = _side(tmp_path, side="memory")
    with (
        mock.patch.object(sync_git, "require_side_checkout"),
        mock.patch.object(sync_git, "merge_head", return_value=SHA_SOURCE),
        mock.patch.object(sync_git, "unmerged_paths", return_value=()),
        mock.patch.object(
            sync_git,
            "_finish_staged_memory_merge",
            return_value=("completed", (), SHA_RESULT),
        ) as finish,
    ):
        assert sync_git.start_side_merge(memory) == ("completed", (), SHA_RESULT)
    finish.assert_called_once_with(memory)

    code = _side(tmp_path)
    with (
        mock.patch.object(sync_git, "require_side_checkout"),
        mock.patch.object(sync_git, "merge_head", return_value=SHA_SOURCE),
        mock.patch.object(sync_git, "unmerged_paths", return_value=("conflict.txt",)),
    ):
        assert sync_git.start_side_merge(code) == (
            "resolution-required",
            ("conflict.txt",),
            "",
        )

    with (
        mock.patch.object(sync_git, "require_side_checkout"),
        mock.patch.object(sync_git, "merge_head", return_value=None),
        mock.patch.object(sync_git, "side_merge_completed", return_value=True),
        mock.patch.object(sync_git, "head_commit", return_value=SHA_RESULT),
    ):
        assert sync_git.start_side_merge(code) == ("completed", (), SHA_RESULT)


def test_start_side_merge_rejects_dirty_or_unattributed_results(tmp_path: Path) -> None:
    code = _side(tmp_path)
    with (
        mock.patch.object(sync_git, "require_side_checkout"),
        mock.patch.object(sync_git, "merge_head", return_value=None),
        mock.patch.object(sync_git, "side_merge_completed", return_value=False),
        mock.patch.object(sync_git, "git_status", return_value="dirty"),
        pytest.raises(sync_git.SyncGitProofError, match="clean worktree"),
    ):
        sync_git.start_side_merge(code)

    with (
        mock.patch.object(sync_git, "require_side_checkout"),
        mock.patch.object(sync_git, "merge_head", return_value=None),
        mock.patch.object(sync_git, "side_merge_completed", return_value=False),
        mock.patch.object(sync_git, "git_status", return_value=""),
        mock.patch.object(sync_git, "run_git", return_value=_result()),
        mock.patch.object(sync_git, "head_commit", return_value=SHA_RESULT),
        mock.patch.object(sync_git, "exact_created_head", return_value=False),
        pytest.raises(sync_git.SyncGitProofError, match="exact admitted head"),
    ):
        sync_git.start_side_merge(code)

    with (
        mock.patch.object(sync_git, "require_side_checkout"),
        mock.patch.object(sync_git, "merge_head", side_effect=[None, None]),
        mock.patch.object(sync_git, "side_merge_completed", return_value=False),
        mock.patch.object(sync_git, "git_status", return_value=""),
        mock.patch.object(sync_git, "run_git", return_value=_result(1, stderr="merge failed")),
        mock.patch.object(sync_git, "unmerged_paths", return_value=()),
        pytest.raises(sync_git.SyncGitProofError, match="merge failed"),
    ):
        sync_git.start_side_merge(code)


def test_start_memory_merge_requires_its_pinned_merge_head(tmp_path: Path) -> None:
    memory = _side(tmp_path, side="memory")
    with (
        mock.patch.object(sync_git, "require_side_checkout"),
        mock.patch.object(sync_git, "merge_head", side_effect=[None, SHA_PRE]),
        mock.patch.object(sync_git, "side_merge_completed", return_value=False),
        mock.patch.object(sync_git, "git_status", return_value=""),
        mock.patch.object(sync_git, "run_git", return_value=_result()),
        pytest.raises(sync_git.SyncGitProofError, match="pinned MERGE_HEAD"),
    ):
        sync_git.start_side_merge(memory)


def test_finish_staged_memory_merge_returns_validation_and_commit_failures(
    tmp_path: Path,
) -> None:
    side = _side(tmp_path, side="memory")
    with mock.patch.object(
        sync_git,
        "_validate_parent_ledgers",
        side_effect=sync_git.SyncGitProofError("ledger conflict"),
    ):
        assert sync_git._finish_staged_memory_merge(side) == (
            "resolution-required",
            ("memory.md",),
            "ledger conflict",
        )
    with (
        mock.patch.object(sync_git, "_validate_parent_ledgers"),
        mock.patch.object(sync_git, "run_git", return_value=_result(1, stderr="commit failed")),
        pytest.raises(sync_git.SyncGitProofError, match="commit failed"),
    ):
        sync_git._finish_staged_memory_merge(side)
    with (
        mock.patch.object(sync_git, "_validate_parent_ledgers"),
        mock.patch.object(sync_git, "run_git", return_value=_result()),
        mock.patch.object(sync_git, "head_commit", return_value=SHA_RESULT),
        mock.patch.object(sync_git, "exact_created_head", return_value=False),
        pytest.raises(sync_git.SyncGitProofError, match="pinned parents"),
    ):
        sync_git._finish_staged_memory_merge(side)


def test_continue_side_merge_handles_disappeared_and_failed_commit_states(tmp_path: Path) -> None:
    side = _side(tmp_path)
    with (
        mock.patch.object(sync_git, "require_side_checkout"),
        mock.patch.object(sync_git, "merge_head", return_value=None),
        mock.patch.object(sync_git, "head_commit", return_value=SHA_RESULT),
        mock.patch.object(sync_git, "exact_created_head", return_value=False),
        pytest.raises(sync_git.SyncGitProofError, match="disappeared"),
    ):
        sync_git.continue_side_merge(side)
    with (
        mock.patch.object(sync_git, "require_side_checkout"),
        mock.patch.object(sync_git, "merge_head", return_value=None),
        mock.patch.object(sync_git, "head_commit", return_value=SHA_RESULT),
        mock.patch.object(sync_git, "exact_created_head", return_value=True),
        mock.patch.object(sync_git, "validate_completed_side") as validate,
    ):
        assert sync_git.continue_side_merge(side) == SHA_RESULT
    validate.assert_called_once_with(side, SHA_RESULT)

    with (
        mock.patch.object(sync_git, "require_side_checkout"),
        mock.patch.object(sync_git, "merge_head", return_value=SHA_SOURCE),
        mock.patch.object(sync_git, "validate_staged_resolution"),
        mock.patch.object(sync_git, "run_git", return_value=_result(1, stderr="commit failed")),
        pytest.raises(sync_git.SyncGitProofError, match="commit failed"),
    ):
        sync_git.continue_side_merge(side)
    with (
        mock.patch.object(sync_git, "require_side_checkout"),
        mock.patch.object(sync_git, "merge_head", return_value=SHA_SOURCE),
        mock.patch.object(sync_git, "validate_staged_resolution"),
        mock.patch.object(sync_git, "run_git", return_value=_result()),
        mock.patch.object(sync_git, "head_commit", return_value=SHA_RESULT),
        mock.patch.object(sync_git, "exact_created_head", return_value=False),
        pytest.raises(sync_git.SyncGitProofError, match="pinned parents"),
    ):
        sync_git.continue_side_merge(side)


@pytest.mark.parametrize(
    ("merge", "conflicts", "git_results", "message"),
    [
        (SHA_PRE, (), [], "MERGE_HEAD"),
        (SHA_SOURCE, ("conflict.txt",), [], "unmerged paths"),
        (SHA_SOURCE, (), [_result(1)], "unstaged changes"),
        (SHA_SOURCE, (), [_result(), _result(1, stderr="bad stage")], "bad stage"),
    ],
)
def test_validate_staged_resolution_rejects_each_incomplete_proof(
    tmp_path: Path,
    merge: str,
    conflicts: tuple[str, ...],
    git_results: list[SimpleNamespace],
    message: str,
) -> None:
    side = _side(tmp_path)
    with (
        mock.patch.object(sync_git, "require_side_checkout"),
        mock.patch.object(sync_git, "merge_head", return_value=merge),
        mock.patch.object(sync_git, "unmerged_paths", return_value=conflicts),
        mock.patch.object(sync_git, "run_git", side_effect=git_results),
        pytest.raises(sync_git.SyncGitProofError, match=message),
    ):
        sync_git.validate_staged_resolution(side)


def test_rollback_side_rejects_unowned_active_and_created_history(tmp_path: Path) -> None:
    side = _side(tmp_path)
    with (
        mock.patch.object(sync_git, "ensure_temporary_worktree"),
        mock.patch.object(sync_git, "head_commit", return_value=SHA_RESULT),
        mock.patch.object(sync_git, "merge_head", return_value=SHA_SOURCE),
        pytest.raises(sync_git.SyncGitProofError, match="outside sync authority"),
    ):
        sync_git.rollback_side(side)
    with (
        mock.patch.object(sync_git, "ensure_temporary_worktree"),
        mock.patch.object(sync_git, "head_commit", return_value=SHA_RESULT),
        mock.patch.object(sync_git, "merge_head", return_value=None),
        mock.patch.object(sync_git, "exact_created_head", return_value=False),
        pytest.raises(sync_git.SyncGitProofError, match="automatic rollback is unsafe"),
    ):
        sync_git.rollback_side(side)


def test_rollback_side_translates_abort_reset_and_post_restore_failures(tmp_path: Path) -> None:
    side = _side(tmp_path)
    with (
        mock.patch.object(sync_git, "ensure_temporary_worktree"),
        mock.patch.object(sync_git, "head_commit", return_value=SHA_PRE),
        mock.patch.object(sync_git, "merge_head", return_value=SHA_SOURCE),
        mock.patch.object(sync_git, "run_git", return_value=_result(1)),
        pytest.raises(sync_git.SyncGitProofError, match="merge abort"),
    ):
        sync_git.rollback_side(side)
    with (
        mock.patch.object(sync_git, "ensure_temporary_worktree"),
        mock.patch.object(sync_git, "head_commit", side_effect=[SHA_PRE, SHA_RESULT]),
        mock.patch.object(sync_git, "merge_head", return_value=SHA_SOURCE),
        mock.patch.object(sync_git, "run_git", return_value=_result()),
        pytest.raises(sync_git.SyncGitProofError, match="merge abort"),
    ):
        sync_git.rollback_side(side)
    with (
        mock.patch.object(sync_git, "ensure_temporary_worktree"),
        mock.patch.object(sync_git, "head_commit", return_value=SHA_RESULT),
        mock.patch.object(sync_git, "merge_head", return_value=None),
        mock.patch.object(sync_git, "exact_created_head", return_value=True),
        mock.patch.object(sync_git, "git_status", return_value="dirty"),
        pytest.raises(sync_git.SyncGitProofError, match="post-sync work"),
    ):
        sync_git.rollback_side(side)
    with (
        mock.patch.object(sync_git, "ensure_temporary_worktree"),
        mock.patch.object(sync_git, "head_commit", return_value=SHA_RESULT),
        mock.patch.object(sync_git, "merge_head", return_value=None),
        mock.patch.object(sync_git, "exact_created_head", return_value=True),
        mock.patch.object(sync_git, "git_status", return_value=""),
        mock.patch.object(sync_git, "run_git", return_value=_result(1, stderr="reset failed")),
        pytest.raises(sync_git.SyncGitProofError, match="reset failed"),
    ):
        sync_git.rollback_side(side)

    with (
        mock.patch.object(sync_git, "ensure_temporary_worktree"),
        mock.patch.object(sync_git, "head_commit", side_effect=[SHA_PRE, SHA_RESULT]),
        mock.patch.object(sync_git, "merge_head", return_value=None),
        pytest.raises(sync_git.SyncGitProofError, match="did not reach"),
    ):
        sync_git.rollback_side(side)

    with (
        mock.patch.object(sync_git, "ensure_temporary_worktree"),
        mock.patch.object(sync_git, "head_commit", side_effect=[SHA_RESULT, SHA_PRE]),
        mock.patch.object(sync_git, "merge_head", return_value=None),
        mock.patch.object(sync_git, "exact_created_head", return_value=True),
        mock.patch.object(sync_git, "git_status", side_effect=["", "residue"]),
        mock.patch.object(sync_git, "run_git", return_value=_result()),
        pytest.raises(sync_git.SyncGitProofError, match="manual repair"),
    ):
        sync_git.rollback_side(side)


def test_ledger_validation_preserves_same_code_history_and_rejects_missing_rows() -> None:
    first = LedgerRow("a" * 40, "b" * 40)
    with pytest.raises(sync_git.SyncGitProofError, match="dropped parent mapping"):
        sync_git._validate_required_ledger_rows([first], [])

    newer = LedgerRow(first.code_commit, "c" * 40)
    sync_git._validate_required_ledger_rows([first], [newer, first])


def test_ledger_rows_translate_missing_and_invalid_ledgers(tmp_path: Path) -> None:
    with (
        mock.patch.object(sync_git, "run_git", return_value=_result(1, stderr="missing ledger")),
        pytest.raises(sync_git.SyncGitProofError, match="missing ledger"),
    ):
        sync_git._ledger_rows(tmp_path, "HEAD:memory.md")
    with (
        mock.patch.object(sync_git, "run_git", return_value=_result(stdout="invalid")),
        mock.patch.object(
            sync_git,
            "parse_ledger_text",
            side_effect=LedgerError("bad ledger"),
        ),
        pytest.raises(sync_git.SyncGitProofError, match="bad ledger"),
    ):
        sync_git._ledger_rows(tmp_path, "HEAD:memory.md")
