"""Bounded native abandonment outcomes owned by their exact retained native record.

The archive precedes deletion and carries the pre-success contract. Its outcome receipt is
published as the native guard's last commit operation, under a lease that recovery never waits
for. It is immutable per archive generation and has that archive's retention and reclamation
owner. An admitted no-remains outcome instead lives beside its retained canonical contract,
with that contract's lifetime. Both use the same bounded receipt, lease and final-write owner;
neither creates an independent history, retry journal or runtime store.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.errors import LockCapabilityError
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.file_lock import try_file_lease
from agents_remember.worktrees.integration.lifecycle.lifecycle_enclosure_terminal import (
    terminal_enclosure_archive_paths,
    validate_terminal_proof,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    inspect_lifecycle_operation_locator,
)
from agents_remember.worktrees.integration.terminal_enclosure_evidence import _read_regular_file
from agents_remember.worktrees.modules.terminal_inventory import terminal_without_remains_reason
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    ContractError,
    WorktreeContract,
    amend_contract,
    contract_publication_text,
    parse_contract_text,
)

MAX_ABANDON_COMMIT_BYTES = 16 * 1024


class TerminalAbandonCommitRecord(BaseModel):
    """The exact successful result of one archive generation, including its terminal note."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["1.0"] = "1.0"
    cleanupRequestId: str = Field(pattern=r"^[0-9a-f]{64}$")
    contractPath: str = Field(min_length=1, max_length=4096)
    committedContractSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    terminalNote: str = Field(max_length=4096)


class NoRemainsAbandonCommitRecord(BaseModel):
    """The exact successful no-remains result retained with its canonical contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["1.0"] = "1.0"
    authorityKind: Literal["no-remains"]
    contractPath: str = Field(min_length=1, max_length=4096)
    lifecycleId: str = Field(min_length=1, max_length=4096)
    committedContractSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    terminalNote: str = Field(max_length=4096)


type AbandonCommitRecord = TerminalAbandonCommitRecord | NoRemainsAbandonCommitRecord


def terminal_abandon_commit_record_path(coordination_root: Path, request_id: str) -> Path:
    archive, _ = terminal_enclosure_archive_paths(coordination_root, request_id)
    return archive.with_suffix(".abandon-committed.json")


def _read_record[Record: BaseModel](path: Path, model: type[Record]) -> Record | None:
    if not path.exists():
        return None
    if path.stat().st_size > MAX_ABANDON_COMMIT_BYTES:
        raise ContractError("terminal abandonment outcome exceeds its fixed byte bound")
    text = _read_regular_file(path, owner="terminal abandonment outcome")
    return model.model_validate_json(text)


def terminal_abandon_commit_record(
    coordination_root: Path, request_id: str
) -> TerminalAbandonCommitRecord | None:
    """Read a committed result only while no native transaction can still roll it back."""

    return _read_committed(
        terminal_abandon_commit_record_path(coordination_root, request_id),
        TerminalAbandonCommitRecord,
    )


def _read_committed[Record: BaseModel](path: Path, model: type[Record]) -> Record | None:
    try:
        if not path.exists():
            return None
        lease = try_file_lease(path, "terminal abandonment outcome")
        if lease is None:
            return None
        try:
            return _read_record(path, model)
        finally:
            lease.close()
    except (OSError, UnicodeError, RuntimeError, ValueError):
        return None


def _contract_digest(contract: WorktreeContract) -> str:
    return hashlib.sha256(
        contract_publication_text(contract.contract_path, contract).encode("utf-8")
    ).hexdigest()


def no_remains_abandon_committed(contract: WorktreeContract) -> bool:
    """Only an exact leased native receipt proves a retained no-remains result committed."""

    record = _read_committed(
        contract.contract_path.with_suffix(".abandon-committed.json"),
        NoRemainsAbandonCommitRecord,
    )
    return (
        record is not None
        and contract.cleanup == "abandoned"
        and record.contractPath == contract.contract_path.as_posix()
        and record.lifecycleId == contract.lifecycle_id
        and record.terminalNote == contract.terminal_note
        and record.committedContractSha256 == _contract_digest(contract)
    )


@dataclass
class NativeAbandonCommit:
    """The last native commit write; it owns rollback only until that write succeeds."""

    path: Path
    record: AbandonCommitRecord
    committed: bool = False

    def publish(self) -> None:
        if self.committed:
            return
        text = self.record.model_dump_json(indent=2) + "\n"
        if len(text.encode("utf-8")) > MAX_ABANDON_COMMIT_BYTES:
            raise ContractError("terminal abandonment outcome exceeds its fixed byte bound")
        atomic_write_text(self.path, text)
        self.committed = True

    def restore(self) -> None:
        if not self.committed:
            self.path.unlink(missing_ok=True)


def _archived_outcome(
    committed: WorktreeContract,
) -> tuple[Path, TerminalAbandonCommitRecord]:
    observation = inspect_lifecycle_operation_locator(
        committed.coordination_root, committed.contract_path
    )
    if observation.locator is None:
        raise ContractError("abandonment has no exact native terminal archive")
    archive = validate_terminal_proof(
        committed.coordination_root, committed.contract_path, observation.locator
    )
    if archive is None or archive.cleanupOperation != "worktree_abandon":
        raise ContractError("abandonment has no exact native abandonment archive")
    archived = parse_contract_text(archive.contractText, path=committed.contract_path)
    expected = amend_contract(
        archived, ContractCells(cleanup="abandoned", terminal_note=committed.terminal_note)
    )
    if committed != expected:
        raise ContractError(
            "abandonment outcome changes facts outside the archived terminal result"
        )
    record = TerminalAbandonCommitRecord(
        cleanupRequestId=archive.cleanupRequestId,
        contractPath=archive.contractPath,
        committedContractSha256=_contract_digest(committed),
        terminalNote=committed.terminal_note,
    )
    path = terminal_abandon_commit_record_path(
        committed.coordination_root, archive.locator.publicationRequestId
    )
    return path, record


def _no_remains_outcome(
    original: WorktreeContract, committed: WorktreeContract
) -> tuple[Path, NoRemainsAbandonCommitRecord]:
    expected = amend_contract(
        original, ContractCells(cleanup="abandoned", terminal_note=committed.terminal_note)
    )
    if committed != expected:
        raise ContractError("abandonment outcome changes facts outside the no-remains result")
    return committed.contract_path.with_suffix(".abandon-committed.json"), (
        NoRemainsAbandonCommitRecord(
            authorityKind="no-remains",
            contractPath=committed.contract_path.as_posix(),
            lifecycleId=committed.lifecycle_id,
            committedContractSha256=_contract_digest(committed),
            terminalNote=committed.terminal_note,
        )
    )


@contextmanager
def terminal_abandon_commit(
    original: WorktreeContract, committed: WorktreeContract
) -> Iterator[NativeAbandonCommit]:
    if terminal_without_remains_reason(original, operation="worktree_abandon") is not None:
        path, record = _no_remains_outcome(original, committed)
    else:
        path, record = _archived_outcome(committed)
    lease = try_file_lease(path, "terminal abandonment outcome")
    if lease is None:
        raise LockCapabilityError("the native abandonment outcome is already being published")
    try:
        previous = _read_record(path, type(record))
        if previous is not None and previous != record:
            raise ContractError("the native abandonment outcome belongs to a different result")
        yield NativeAbandonCommit(path, record, committed=previous is not None)
    finally:
        lease.close()
