"""Focused edge coverage for the stable resumable-sync journal."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.models.worktree import SyncPhase, SyncSide
from agents_remember.worktrees.sync_transaction_state import (
    SyncJournalReadError,
    SyncOperationRecord,
    SyncOperationStore,
    SyncQuarantineRecord,
    SyncSidePlan,
    SyncSideRecord,
    SyncSideState,
    observe_sync_operation,
)

SHA_BASE = "0" * 40
SHA_PRE = "1" * 40
SHA_SOURCE = "2" * 40


def _side(
    root: Path,
    *,
    side: SyncSide = "code",
    plan: SyncSidePlan = "merge",
    state: SyncSideState = "pending",
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
        state=state,
        conflictFiles=(f"{side}.txt",) if state == "resolution-required" else (),
    )


def _record(
    root: Path,
    phase: SyncPhase = "running-code",
    *,
    memory: bool = False,
) -> SyncOperationRecord:
    return SyncOperationRecord(
        generation=1,
        contractPath=(root / "series-contract.md").as_posix(),
        taskId="task",
        contractKind="leaf",
        codeBaseFrom=SHA_BASE,
        memoryBaseFrom=SHA_BASE if memory else "",
        phase=phase,
        memorySyncChoice="merge-memory" if memory else None,
        code=_side(
            root,
            state="resolution-required" if phase == "code-resolution-required" else "pending",
        ),
        memory=(
            _side(
                root,
                side="memory",
                state=(
                    "resolution-required" if phase == "memory-resolution-required" else "pending"
                ),
            )
            if memory
            else None
        ),
        createdAt="2026-08-26T00:00:00+00:00",
        updatedAt="2026-08-26T00:00:00+00:00",
    )


def test_store_translates_lstat_and_open_failures(tmp_path: Path) -> None:
    store = SyncOperationStore(tmp_path)
    with (
        mock.patch.object(Path, "lstat", side_effect=OSError("lstat failed")),
        pytest.raises(SyncJournalReadError, match="OSError"),
    ):
        store.read()

    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{}", encoding="utf-8")
    with (
        mock.patch(
            "agents_remember.worktrees.sync_transaction_state.os.open",
            side_effect=OSError("open failed"),
        ),
        pytest.raises(SyncJournalReadError, match="OSError"),
    ):
        store.read()


def test_semantic_read_error_preserves_nonregular_and_os_failures(tmp_path: Path) -> None:
    store = SyncOperationStore(tmp_path)
    store.path.mkdir(parents=True)
    nonregular = store.semantic_read_error("identity")
    assert nonregular.reason == "journal-nonregular"
    assert nonregular.raw == b""

    with mock.patch.object(Path, "lstat", side_effect=OSError("unreadable")):
        unreadable = store.semantic_read_error("identity")
    assert unreadable.reason == "OSError"
    assert unreadable.raw == b""


def test_raw_archive_is_idempotent_and_rejects_conflicting_evidence(tmp_path: Path) -> None:
    store = SyncOperationStore(tmp_path)
    error = SyncJournalReadError(store.path, b"malformed", "JSONDecodeError")

    metadata = store.archive_malformed(error)
    assert metadata is not None
    assert store.archive_malformed(error) == metadata

    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload["reason"] = "different"
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="metadata conflicts"):
        store.archive_malformed(error)


def test_raw_archive_rejects_preexisting_bytes_with_same_digest_path(tmp_path: Path) -> None:
    store = SyncOperationStore(tmp_path)
    raw = b"malformed"
    digest = hashlib.sha256(raw).hexdigest()
    archive = store.path.parent / "archive" / f"sync-operation-malformed-{digest}.raw"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"different")

    with pytest.raises(RuntimeError, match="raw archive bytes conflict"):
        store.archive_malformed(SyncJournalReadError(store.path, raw, "bad"))


def test_opaque_archive_records_absence_and_can_refuse_uninspectable_entry(
    tmp_path: Path,
) -> None:
    store = SyncOperationStore(tmp_path)
    missing = store.archive_malformed(SyncJournalReadError(store.path, b"", "missing"))
    assert missing is not None
    evidence = json.loads(missing.read_text(encoding="utf-8"))
    assert evidence["archiveKind"] == "absence"
    assert evidence["rawArchivePath"] is None

    with mock.patch.object(Path, "lstat", side_effect=OSError("blocked")):
        assert store.archive_malformed(SyncJournalReadError(store.path, b"", "blocked")) is None


def test_opaque_archive_does_not_overwrite_existing_destination(tmp_path: Path) -> None:
    store = SyncOperationStore(tmp_path)
    store.path.mkdir(parents=True)
    with mock.patch.object(Path, "exists", return_value=True):
        assert store.archive_malformed(SyncJournalReadError(store.path, b"", "directory")) is None


def test_observer_projects_malformed_and_quarantined_journals(tmp_path: Path) -> None:
    group = tmp_path / "group"
    store = SyncOperationStore(group)
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(b"{broken")

    malformed = observe_sync_operation(group)
    assert malformed is not None
    assert malformed.state == "journal-malformed"
    assert malformed.contractPath == ""
    assert malformed.nextArgs is None

    quarantine = SyncQuarantineRecord(
        contractPath=(tmp_path / "owned-contract.md").as_posix(),
        reason="malformed",
        evidencePath=(tmp_path / "evidence.json").as_posix(),
        createdAt="2026-08-26T00:00:00+00:00",
    )
    store.write_quarantine(quarantine)
    requested = tmp_path / "other-contract.md"
    projected = observe_sync_operation(group, contract_path=requested)
    assert projected is not None
    assert projected.state == "quarantined"
    assert projected.identityMismatch is True
    assert projected.journalContractPath == quarantine.contractPath

    projected_without_request = observe_sync_operation(group)
    assert projected_without_request is not None
    assert projected_without_request.contractPath == quarantine.contractPath
    assert projected_without_request.identityMismatch is False


@pytest.mark.parametrize(
    ("phase", "memory", "expected_state", "expected_side"),
    [
        ("running-code", False, "running", None),
        ("code-resolution-required", False, "resolution-required", "code"),
        ("memory-resolution-required", True, "resolution-required", "memory"),
        ("cancelling", False, "cancelling", None),
        ("completed", False, "completed", None),
        ("cancelled", False, "cancelled", None),
    ],
)
def test_active_projection_covers_each_durable_phase(
    tmp_path: Path,
    phase: SyncPhase,
    memory: bool,
    expected_state: str,
    expected_side: str | None,
) -> None:
    group = tmp_path / phase
    store = SyncOperationStore(group)
    record = _record(group, phase, memory=memory)
    store.write(record)

    projection = observe_sync_operation(group)
    assert projection is not None
    assert projection.state == expected_state
    assert projection.side == expected_side
    if expected_state in {"completed", "cancelled"}:
        assert projection.nextArgs is None
        assert projection.cancelArgs is None
    else:
        assert projection.nextArgs is not None
        assert projection.cancelArgs is not None
    if expected_side is not None:
        assert projection.conflictFiles == (f"{expected_side}.txt",)
        assert projection.nextArgs is not None
        assert projection.nextArgs["resolution_action"] == "continue"


def test_active_projection_fails_closed_on_requested_contract_mismatch(tmp_path: Path) -> None:
    group = tmp_path / "group"
    store = SyncOperationStore(group)
    record = _record(group)
    store.write(record)

    requested = tmp_path / "different-contract.md"
    projection = observe_sync_operation(group, contract_path=requested)
    assert projection is not None
    assert projection.state == "journal-identity-invalid"
    assert projection.identityMismatch is True
    assert projection.journalContractPath == record.contractPath
    assert projection.nextArgs is not None
    assert projection.nextArgs["resolution_action"] == "cancel"


def test_sync_side_record_rejects_invalid_literals_and_nonregular_mode_is_detected(
    tmp_path: Path,
) -> None:
    store = SyncOperationStore(tmp_path)
    store.path.mkdir(parents=True)
    assert stat.S_ISDIR(store.path.lstat().st_mode)
    with pytest.raises(SyncJournalReadError, match="journal-nonregular"):
        store.read()
