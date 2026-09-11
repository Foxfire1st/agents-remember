"""The sync journal is working state, so it lives in ``reports/``, never in ``.lifecycle/``.

``.lifecycle/`` is the leaf's terminal enclosure evidence: the cleanup scanner admits the
enclosure manifest, the adoption receipt, and the terminal lifecycle operation records, and
deletes exactly what it admits with the root. A sync transaction journal is none of those --
one sync covers both repository sides, the next generation re-publishes it, and its pinned
refs are retired when the transaction terminates -- so it is filed beside the declared-door
journal in the group's ``reports/`` working directory (``sync_operation_path``).

Enclosures created before that move still carry ``sync-operation.json`` in ``.lifecycle/``.
The scanner tolerates exactly that one name there, as a transient pre-move artifact: it is
removed with the enclosure and reported, never archived and never silently dropped. A live
sync transaction still refuses, and so does any file the scanner cannot classify.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

from agents_remember.models.lifecycles.enclosure import (
    TerminalEnclosureArchive,
    TerminalWorktreeCleanupArguments,
)
from agents_remember.models.worktree import SyncPhase
from agents_remember.worktrees.integration.terminal_enclosure_archive import (
    terminal_archive_required_result,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncOperationStore,
    SyncSideRecord,
    legacy_sync_operation_path,
    sync_operation_path,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract
from integration_branch_authority_test_support import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
)


def _closed_leaf(root: Path) -> WorktreeContract:
    fixture = _authority_fixture(root, external_memory=True)
    closed: WorktreeContract = _closed_external_leaf_worktrees(
        fixture, root, publish_closeout_evidence=True
    )
    return closed


def _sync_journal(contract_path: str, *, phase: SyncPhase) -> SyncOperationRecord:
    """One schema-valid sync generation in the requested phase."""

    side = SyncSideRecord(
        side="code",
        repository="/repo",
        worktree="/repo-worktree",
        sourceBranch="main",
        workBranch="ar/leaf",
        sourceCommit="a" * 40,
        preSyncHead="a" * 40,
        baseCommit="a" * 40,
        backupRef="refs/agents-remember/sync/deadbeef/code/pre-sync",
        sourceBackupRef="refs/agents-remember/sync/deadbeef/code/source",
        baseBackupRef="refs/agents-remember/sync/deadbeef/code/base",
        plan="fast-forward",
        state="completed" if phase == "completed" else "pending",
        resultHead="a" * 40,
    )
    return SyncOperationRecord(
        generation=1,
        contractPath=contract_path,
        taskId="SYNC-JOURNAL",
        contractKind="leaf",
        codeBaseFrom="a" * 40,
        memoryBaseFrom="",
        phase=phase,
        code=side,
        createdAt="2026-09-10T00:00:00+00:00",
        updatedAt="2026-09-10T00:00:00+00:00",
    )


def _write_legacy_sync_journal(contract: WorktreeContract, *, phase: SyncPhase) -> Path:
    """Place one generation where a pre-move enclosure has it, and nowhere else."""

    path = legacy_sync_operation_path(contract.worktree_group)
    assert path.parent == contract.worktree_group / ".lifecycle"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _sync_journal(
            contract.contract_path.resolve(strict=False).as_posix(),
            phase=phase,
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    return path


def _cleanup(contract: WorktreeContract, *, dry_run: bool) -> WorktreeCommandResult:
    return terminal_archive_required_result(
        contract,
        operation="worktree_cleanup",
        arguments=TerminalWorktreeCleanupArguments(teardown_providers=True),
        dry_run=dry_run,
    )


def test_sync_write_relocates_the_journal_out_of_the_lifecycle_root(tmp_path: Path) -> None:
    """A write publishes to the working directory and retires the pre-move copy."""

    group = tmp_path / "enclosure-group"
    legacy = legacy_sync_operation_path(group)
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"pre": "move"}\n', encoding="utf-8")
    store = SyncOperationStore(group)
    record = _sync_journal("/coordination/tasks/repo/leaf/series-contract.md", phase="completed")

    store.write(record)

    assert store.path.parent.name == "reports"
    assert store.path.is_file()
    assert store.read() == record
    assert not legacy.exists()


def test_pre_move_sync_journal_is_removed_with_the_enclosure_never_archived(
    tmp_path: Path,
) -> None:
    """A legacy journal is reported as removed working state, not copied into the archive."""

    contract = _closed_leaf(tmp_path)
    journal = _write_legacy_sync_journal(contract, phase="completed")

    # The journal's canonical home is the group's working directory, never `.lifecycle/`;
    # the copy below is the pre-move one this test tolerates.
    canonical = sync_operation_path(contract.worktree_group)
    assert canonical.parent.name == "reports"
    assert canonical.parent != journal.parent

    result = _cleanup(contract, dry_run=False)
    assert result.returncode == 0, result.payload
    assert result.payload["state"] == "terminal-archive-proven"

    archived = {
        str(entry["relativePath"])
        for entry in cast(list[dict[str, object]], result.payload["canonicalEntries"])
    }
    assert journal.name not in archived
    removed = cast(list[dict[str, object]], result.payload["removedWorkingState"])
    assert removed == [
        {
            "relativePath": journal.name,
            "sha256": hashlib.sha256(journal.read_bytes()).hexdigest(),
            "sizeBytes": journal.stat().st_size,
            "disposition": "removed-with-enclosure",
        }
    ]

    # The report outlives the deletion because it is in the archive, not the command result.
    archive = TerminalEnclosureArchive.model_validate_json(
        Path(str(result.payload["archivePath"])).read_bytes()
    )
    assert [item.relativePath for item in archive.removedWorkingState] == [journal.name]
    assert journal.name not in {item.relativePath for item in archive.canonicalEntries}


def test_unrecognised_canonical_lifecycle_artifact_still_refuses_cleanup(
    tmp_path: Path,
) -> None:
    """A file the scanner cannot classify is never deleted to make cleanup succeed."""

    contract = _closed_leaf(tmp_path)
    lifecycle = contract.worktree_group / ".lifecycle"
    mystery = lifecycle / "mystery.json"
    mystery.write_text('{"who": "wrote this"}\n', encoding="utf-8")

    result = _cleanup(contract, dry_run=True)

    assert result.returncode == 2
    assert result.payload["state"] == "terminal-archive-unowned-artifact"
    assert result.payload["observed"] == {
        "unownedArtifact": "mystery.json",
        "lifecycleDirectory": lifecycle.as_posix(),
    }
    detail = str(result.payload["detail"])
    assert "mystery.json" in detail
    assert "No automated remedy exists" in detail
    assert result.payload["nextAction"] == "developer-decision"
    assert result.payload["developerDecisionRequired"] is True
    assert mystery.exists()


def test_live_sync_transaction_refuses_cleanup_with_a_named_remedy(tmp_path: Path) -> None:
    """Transient tolerance stops at a journal that still holds recovery authority."""

    contract = _closed_leaf(tmp_path)
    journal = _write_legacy_sync_journal(contract, phase="code-resolution-required")

    result = _cleanup(contract, dry_run=True)

    assert result.returncode == 2
    assert result.payload["state"] == "terminal-archive-sync-transaction-active"
    assert journal.name in str(result.payload["detail"])
    assert "resolution-required" in str(result.payload["detail"])
    assert result.payload["nextAction"] == "worktree_sync"
    assert result.payload["nextTool"] == "worktree_sync"
    assert result.payload["nextArgs"] == {
        "contract_path": contract.contract_path.resolve(strict=False).as_posix(),
        "resolution_action": "cancel",
        "dry_run": False,
    }
    assert journal.exists()
