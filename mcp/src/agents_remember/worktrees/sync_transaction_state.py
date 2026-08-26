"""Strict durable state for one resumable worktree-sync transaction."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agents_remember.kernel.atomic_write import (
    atomic_replace,
    atomic_write_bytes,
    atomic_write_text,
)
from agents_remember.models.worktree import (
    MemorySyncChoice,
    SyncOperationProjection,
    SyncOperationState,
    SyncPhase,
    SyncSide,
)

SyncSidePlan = Literal["already-current", "fast-forward", "merge", "skip"]
SyncSideState = Literal[
    "pending",
    "resolution-required",
    "completed",
    "rolled-back",
]


class SyncSideRecord(BaseModel):
    """Pinned authority and live progress for one repository side."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    side: SyncSide
    repository: str = Field(min_length=1, max_length=4096)
    worktree: str = Field(min_length=1, max_length=4096)
    sourceBranch: str = Field(min_length=1, max_length=4096)
    workBranch: str = Field(min_length=1, max_length=4096)
    sourceCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    preSyncHead: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    baseCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    backupRef: str = Field(pattern=r"^refs/agents-remember/sync/.+$", max_length=4096)
    sourceBackupRef: str = Field(pattern=r"^refs/agents-remember/sync/.+$", max_length=4096)
    baseBackupRef: str = Field(pattern=r"^refs/agents-remember/sync/.+$", max_length=4096)
    plan: SyncSidePlan
    state: SyncSideState = "pending"
    temporary: bool = False
    resultHead: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    conflictFiles: tuple[str, ...] = ()


class SyncOperationRecord(BaseModel):
    """One current sync generation stored below the stable enclosure root."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["1.0"] = "1.0"
    generation: int = Field(ge=1)
    contractPath: str = Field(min_length=1, max_length=4096)
    taskId: str = Field(min_length=1, max_length=512)
    contractKind: Literal["leaf", "series"]
    codeBaseFrom: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memoryBaseFrom: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    phase: SyncPhase
    memorySyncChoice: MemorySyncChoice | None = None
    code: SyncSideRecord
    memory: SyncSideRecord | None = None
    createdAt: str = Field(min_length=1, max_length=128)
    updatedAt: str = Field(min_length=1, max_length=128)


class SyncQuarantineRecord(BaseModel):
    """Terminal proof that corrupt bytes were archived without rollback authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["1.0"] = "1.0"
    recordKind: Literal["quarantine"] = "quarantine"
    generation: int = Field(default=1, ge=1)
    contractPath: str = Field(min_length=1, max_length=4096)
    state: Literal["cancelled-no-authority"] = "cancelled-no-authority"
    reason: str = Field(min_length=1, max_length=128)
    evidencePath: str = Field(min_length=1, max_length=4096)
    createdAt: str = Field(min_length=1, max_length=128)


SyncJournalRecord = SyncOperationRecord | SyncQuarantineRecord


class SyncJournalReadError(RuntimeError):
    """The stable journal exists but cannot be trusted as a transaction record."""

    def __init__(self, path: Path, raw: bytes, reason: str) -> None:
        super().__init__(reason)
        self.path = path
        self.raw = raw
        self.reason = reason


class MalformedSyncJournalEvidence(BaseModel):
    """Bounded metadata for an exact archived journal byte sequence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["1.0"] = "1.0"
    sourcePath: str = Field(min_length=1, max_length=4096)
    archiveKind: Literal["raw-bytes", "opaque-entry", "absence"]
    rawArchivePath: str | None = Field(default=None, max_length=4096)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=128)
    archivedAt: str = Field(min_length=1, max_length=128)


def sync_operation_path(worktree_group: Path) -> Path:
    return worktree_group / ".lifecycle" / "sync-operation.json"


def sync_ref_prefix(contract_path: Path) -> str:
    digest = hashlib.sha256(contract_path.resolve(strict=False).as_posix().encode()).hexdigest()
    return f"refs/agents-remember/sync/{digest[:32]}"


def sync_side_refs(contract_path: Path, side: SyncSide) -> tuple[str, str]:
    prefix = sync_ref_prefix(contract_path)
    return f"{prefix}/{side}/pre-sync", f"{prefix}/{side}/source"


def sync_side_base_ref(contract_path: Path, side: SyncSide) -> str:
    return f"{sync_ref_prefix(contract_path)}/{side}/base"


def operation_stamp() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class SyncOperationStore:
    """Atomic single-record store; repository authority lock serializes its writers."""

    def __init__(self, worktree_group: Path) -> None:
        self.path = sync_operation_path(worktree_group)

    def read(self) -> SyncJournalRecord | None:
        try:
            mode = self.path.lstat().st_mode
        except FileNotFoundError:
            return None
        except OSError as error:
            raise SyncJournalReadError(self.path, b"", type(error).__name__) from error
        if not stat.S_ISREG(mode):
            raise SyncJournalReadError(self.path, b"", "journal-nonregular")
        try:
            descriptor = os.open(
                self.path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            with os.fdopen(descriptor, "rb") as handle:
                raw = handle.read()
        except OSError as error:
            raise SyncJournalReadError(self.path, b"", type(error).__name__) from error
        try:
            payload = json.loads(raw.decode("utf-8"))
            try:
                return SyncOperationRecord.model_validate(payload)
            except ValidationError:
                return SyncQuarantineRecord.model_validate(payload)
        except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as error:
            raise SyncJournalReadError(self.path, raw, type(error).__name__) from error

    def write(self, record: SyncOperationRecord) -> None:
        checked = SyncOperationRecord.model_validate(record.model_dump(mode="json"))
        atomic_write_text(self.path, checked.model_dump_json(indent=2) + "\n")

    def write_quarantine(self, record: SyncQuarantineRecord) -> None:
        checked = SyncQuarantineRecord.model_validate(record.model_dump(mode="json"))
        atomic_write_text(self.path, checked.model_dump_json(indent=2) + "\n")

    def semantic_read_error(self, reason: str) -> SyncJournalReadError:
        """Capture the exact valid JSON bytes whose authority identity was rejected."""

        try:
            mode = self.path.lstat().st_mode
            if not stat.S_ISREG(mode):
                return SyncJournalReadError(self.path, b"", "journal-nonregular")
            descriptor = os.open(
                self.path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            with os.fdopen(descriptor, "rb") as handle:
                raw = handle.read()
        except OSError as error:
            return SyncJournalReadError(self.path, b"", type(error).__name__)
        return SyncJournalReadError(self.path, raw, reason)

    def archive_malformed(self, error: SyncJournalReadError) -> Path | None:
        if not error.raw:
            return self._archive_opaque_entry(error)
        digest = hashlib.sha256(error.raw).hexdigest()
        archive_root = self.path.parent / "archive"
        raw_archive = archive_root / f"sync-operation-malformed-{digest}.raw"
        metadata_path = archive_root / f"sync-operation-malformed-{digest}.json"
        if not raw_archive.exists():
            atomic_write_bytes(raw_archive, error.raw)
        if raw_archive.read_bytes() != error.raw:
            raise RuntimeError("sync malformed-journal raw archive bytes conflict")
        evidence = MalformedSyncJournalEvidence(
            sourcePath=error.path.as_posix(),
            archiveKind="raw-bytes",
            rawArchivePath=raw_archive.as_posix(),
            sha256=digest,
            size=len(error.raw),
            reason=error.reason,
            archivedAt=operation_stamp(),
        )
        if not metadata_path.exists():
            atomic_write_text(metadata_path, evidence.model_dump_json(indent=2) + "\n")
        else:
            existing = MalformedSyncJournalEvidence.model_validate_json(metadata_path.read_bytes())
            if (
                existing.sourcePath != evidence.sourcePath
                or existing.archiveKind != evidence.archiveKind
                or existing.rawArchivePath != evidence.rawArchivePath
                or existing.sha256 != evidence.sha256
                or existing.size != evidence.size
                or existing.reason != evidence.reason
            ):
                raise RuntimeError("sync malformed-journal archive metadata conflicts")
        return metadata_path

    def _archive_opaque_entry(self, error: SyncJournalReadError) -> Path | None:
        """Preserve an unreadable/nonregular directory entry without following it."""

        archive_root = self.path.parent / "archive"
        try:
            observed = self.path.lstat()
        except FileNotFoundError:
            descriptor = {
                "sourcePath": self.path.as_posix(),
                "reason": error.reason,
                "fileType": "absent",
            }
            archive_path: Path | None = None
            archive_kind = "absence"
            size = 0
        except OSError:
            return None
        else:
            file_type = (
                "symlink"
                if stat.S_ISLNK(observed.st_mode)
                else "directory"
                if stat.S_ISDIR(observed.st_mode)
                else "other"
            )
            descriptor = {
                "sourcePath": self.path.as_posix(),
                "reason": error.reason,
                "fileType": file_type,
                "mode": stat.S_IMODE(observed.st_mode),
                "size": observed.st_size,
                "symlinkTarget": os.readlink(self.path) if file_type == "symlink" else None,
            }
            archive_kind = "opaque-entry"
            size = observed.st_size
            digest = hashlib.sha256(
                json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            archive_path = archive_root / f"sync-operation-opaque-{digest}.entry"
            if archive_path.exists():
                return None
            archive_root.mkdir(parents=True, exist_ok=True)
            atomic_replace(self.path, archive_path)
        digest = hashlib.sha256(
            json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        metadata_path = archive_root / f"sync-operation-malformed-{digest}.json"
        evidence = MalformedSyncJournalEvidence(
            sourcePath=error.path.as_posix(),
            archiveKind=archive_kind,
            rawArchivePath=archive_path.as_posix() if archive_path is not None else None,
            sha256=digest,
            size=size,
            reason=error.reason,
            archivedAt=operation_stamp(),
        )
        atomic_write_text(metadata_path, evidence.model_dump_json(indent=2) + "\n")
        return metadata_path


def observe_sync_operation(
    worktree_group: Path,
    *,
    contract_path: Path | None = None,
) -> SyncOperationProjection | None:
    """Strictly project the stable journal without reading the task contract."""

    store = SyncOperationStore(worktree_group)
    try:
        record = store.read()
    except SyncJournalReadError as error:
        return _malformed_sync_projection(error, contract_path)
    if record is None:
        return None
    if isinstance(record, SyncQuarantineRecord):
        return _quarantined_sync_projection(record, contract_path)
    return _active_sync_projection(record, contract_path)


def _malformed_sync_projection(
    error: SyncJournalReadError, contract_path: Path | None
) -> SyncOperationProjection:
    address = contract_path.resolve(strict=False).as_posix() if contract_path else ""
    return SyncOperationProjection(
        state="journal-malformed",
        phase="journal-read",
        contractPath=address,
        summary="The stable sync journal is malformed; normal sync fails closed.",
        nextArgs=(
            {
                "contract_path": address,
                "resolution_action": "cancel",
                "dry_run": False,
            }
            if address
            else None
        ),
        evidencePath=error.path.as_posix(),
    )


def _quarantined_sync_projection(
    record: SyncQuarantineRecord, contract_path: Path | None
) -> SyncOperationProjection:
    address = (
        contract_path.resolve(strict=False).as_posix()
        if contract_path is not None
        else record.contractPath
    )
    mismatch = address != record.contractPath
    return SyncOperationProjection(
        state="quarantined",
        phase="quarantined",
        contractPath=address,
        journalContractPath=record.contractPath if mismatch else None,
        identityMismatch=mismatch,
        summary=(
            "Corrupt sync evidence was quarantined without branch rollback authority. "
            "The sync tool is usable; no heads-restored claim was made."
        ),
        evidencePath=record.evidencePath,
    )


def _active_sync_projection(
    record: SyncOperationRecord, contract_path: Path | None
) -> SyncOperationProjection:
    requested_address = (
        contract_path.resolve(strict=False).as_posix() if contract_path is not None else None
    )
    executable_address = requested_address or record.contractPath
    identity_mismatch = requested_address is not None and requested_address != record.contractPath
    side = (
        "code"
        if record.phase == "code-resolution-required"
        else "memory"
        if record.phase == "memory-resolution-required"
        else None
    )
    side_record = record.code if side == "code" else record.memory if side == "memory" else None
    state: SyncOperationState
    if identity_mismatch:
        state = "journal-identity-invalid"
        summary = (
            "The sync journal names another contract. Normal resume fails closed; explicit "
            "cancellation must recover through the locator-proven contract address."
        )
    elif side is not None:
        state = "resolution-required"
        summary = f"Resolve and stage the retained {side} merge, then continue worktree_sync."
    elif record.phase == "cancelling":
        state = "cancelling"
        summary = "The exact sync rollback is incomplete; rerun cancellation."
    elif record.phase == "completed":
        state = "completed"
        summary = "The sync transaction is completed."
    elif record.phase == "cancelled":
        state = "cancelled"
        summary = "The sync transaction is cancelled."
    else:
        state = "running"
        summary = "The journaled sync transaction can resume automatically."
    active = record.phase not in {"completed", "cancelled"}
    return SyncOperationProjection(
        state=state,
        phase=record.phase,
        contractPath=executable_address,
        journalContractPath=record.contractPath if identity_mismatch else None,
        identityMismatch=identity_mismatch,
        side=side,
        conflictFiles=side_record.conflictFiles if side_record is not None else (),
        summary=summary,
        nextArgs=(
            {
                "contract_path": executable_address,
                **(
                    {"resolution_action": "cancel"}
                    if identity_mismatch
                    else {"resolution_action": "continue"}
                    if side is not None
                    else {}
                ),
                "dry_run": False,
            }
            if active
            else None
        ),
        cancelArgs=(
            {
                "contract_path": executable_address,
                "resolution_action": "cancel",
                "dry_run": False,
            }
            if active
            else None
        ),
    )
