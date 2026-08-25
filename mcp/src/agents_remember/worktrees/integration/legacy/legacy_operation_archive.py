"""Evidence validation and crash-safe publication for schema-1 archives."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.models.lifecycles.operation_kinds import LifecycleOperationKind
from agents_remember.worktrees.integration.legacy.legacy_operation_failures import (
    LegacyBridgeError,
    LegacyIoFailure,
    legacy_io_error,
)
from agents_remember.worktrees.integration.legacy.legacy_operation_schema import (
    LegacyArchive,
    LegacyRecoveryCommits,
    LegacySchemaOneRecord,
)
from agents_remember.worktrees.modules.git import branch_commit, head_commit, is_ancestor
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class _ReceiptWrite:
    source_path: Path
    archive_path: Path
    archive: LegacyArchive
    original: bytes
    payload: str


def terminal_archive_evidence(
    contract: WorktreeContract,
    legacy: LegacySchemaOneRecord,
) -> dict[str, object]:
    """Prove that live refs still contain the exact terminal schema-1 output."""

    _require_terminal_state(legacy)
    commits = _required_recovery_commits(legacy)
    live_code = _archive_output_evidence(contract, legacy.operationKind, commits)
    return {
        "workerAuthority": "absent",
        "status": legacy.status,
        "phase": legacy.phase,
        "contractStatus": (
            contract.closeout_status
            if legacy.operationKind == "closeout"
            else contract.integration_status
        ),
        "recoveryCommits": commits.model_dump(mode="json"),
        "liveCode": live_code,
    }


def _require_terminal_state(legacy: LegacySchemaOneRecord) -> None:
    observed = (legacy.workerPid, legacy.status, legacy.phase)
    if observed != (None, "completed", "completed"):
        raise LegacyBridgeError(
            "legacy-archive-terminal-proof-required",
            "archive requires terminal completed state with no live worker authority",
        )


def _required_recovery_commits(legacy: LegacySchemaOneRecord) -> LegacyRecoveryCommits:
    if legacy.recoveryCommits is None:
        raise LegacyBridgeError(
            "legacy-archive-output-proof-required",
            "archive requires exact operation-specific recovery commits",
        )
    return legacy.recoveryCommits


def _archive_output_evidence(
    contract: WorktreeContract,
    operation_kind: LifecycleOperationKind,
    commits: LegacyRecoveryCommits,
) -> str:
    if operation_kind == "closeout":
        return _closeout_archive_evidence(contract, commits)
    if operation_kind == "integrate":
        return _integration_archive_evidence(contract, commits)
    raise LegacyBridgeError(
        "legacy-archive-kind-unsupported",
        "unknown schema-1 operation kind cannot be archived",
    )


def _closeout_archive_evidence(
    contract: WorktreeContract,
    commits: LegacyRecoveryCommits,
) -> str:
    expected = _closeout_archive_expected(contract)
    observed = commits.model_dump(mode="json")
    repository = contract.code_repo_path if contract.kind == "series" else contract.code_worktree
    live_code = (
        branch_commit(repository, contract.code_work_branch)
        if contract.kind == "series"
        else head_commit(repository)
    )
    exact = (
        contract.closeout_status,
        observed,
        is_ancestor(repository, commits.codeCommit, live_code),
    )
    if exact != ("completed", expected, True):
        raise LegacyBridgeError(
            "legacy-closeout-archive-evidence-mismatch",
            "contract and live Git do not prove this terminal closeout output",
            expected=expected,
            observed={**observed, "liveCode": live_code},
        )
    return live_code


def _integration_archive_evidence(
    contract: WorktreeContract,
    commits: LegacyRecoveryCommits,
) -> str:
    expected = _integrate_archive_expected(contract)
    observed = commits.model_dump(mode="json")
    live_code = branch_commit(contract.code_repo_path, contract.code_source_branch)
    exact = (contract.integration_status, observed, live_code)
    if exact != ("completed", expected, contract.integrated_code_commit):
        raise LegacyBridgeError(
            "legacy-integrate-archive-evidence-mismatch",
            "contract and protected refs do not prove this terminal integration output",
            expected=expected,
            observed={**observed, "liveCode": live_code},
        )
    _require_external_memory_archive_evidence(contract)
    return live_code


def _require_external_memory_archive_evidence(contract: WorktreeContract) -> None:
    if contract.memory_mode != "external":
        return
    if contract.memory_repo_path is None:
        raise LegacyBridgeError(
            "legacy-integrate-archive-evidence-mismatch",
            "external integration archive has no memory repository authority",
        )
    live_memory = branch_commit(contract.memory_repo_path, contract.memory_source_branch)
    if live_memory != contract.integrated_ledger_commit:
        raise LegacyBridgeError(
            "legacy-integrate-archive-evidence-mismatch",
            "protected memory ref does not prove the terminal ledger output",
            expected={"memoryRef": contract.integrated_ledger_commit},
            observed={"memoryRef": live_memory},
        )


def _closeout_archive_expected(contract: WorktreeContract) -> dict[str, object]:
    return {
        "codeCommit": contract.code_commit,
        "memoryContentCommit": contract.memory_content_commit,
        "ledgerCommit": contract.ledger_commit,
    }


def _integrate_archive_expected(contract: WorktreeContract) -> dict[str, object]:
    return {
        "codeCommit": contract.integrated_code_commit,
        "memoryContentCommit": contract.integrated_memory_content_commit,
        "ledgerCommit": contract.integrated_ledger_commit,
    }


def publish_archive(
    path: Path,
    archive_path: Path,
    archive: LegacyArchive,
    *,
    original: bytes,
) -> None:
    """Publish one exact archive receipt, then remove only its proven source bytes."""

    payload = archive.model_dump_json(indent=2) + "\n"
    if archive_path.exists():
        _require_existing_archive(archive_path, payload)
    else:
        _write_archive_receipt(path, archive_path, archive, original, payload)
    finish_archive_unlink(path, archive, archive_path)


def _require_existing_archive(archive_path: Path, payload: str) -> None:
    try:
        existing = archive_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise legacy_io_error(
            "legacy-archive-invalid",
            "legacy archive receipt is unreadable",
            failure=LegacyIoFailure("legacy-archive-read", "archive", archive_path.name, exc),
        ) from exc
    if existing != payload:
        raise LegacyBridgeError(
            "legacy-archive-conflict",
            "an archive with different evidence already exists",
        )


def _write_archive_receipt(
    path: Path,
    archive_path: Path,
    archive: LegacyArchive,
    original: bytes,
    payload: str,
) -> None:
    try:
        atomic_write_text(archive_path, payload)
    except OSError as exc:
        _resolve_receipt_write_failure(
            _ReceiptWrite(path, archive_path, archive, original, payload),
            exc,
        )


def _resolve_receipt_write_failure(
    request: _ReceiptWrite,
    error: OSError,
) -> None:
    receipt = read_publication_bytes(request.archive_path)
    source = read_publication_bytes(request.source_path)
    if receipt == request.payload.encode("utf-8"):
        return
    if (receipt, source) == (None, request.original):
        raise LegacyBridgeError(
            "legacy-archive-publication-interrupted",
            "archive receipt publication left the exact original bytes unchanged",
            expected={"legacyDigest": request.archive.originalSha256},
            observed={"sourcePresent": True, "archivePresent": False},
            next_action="archive",
        ) from error
    raise LegacyBridgeError(
        "legacy-archive-conflict",
        "archive receipt publication produced contradictory durable bytes",
        expected={"legacyDigest": request.archive.originalSha256},
        observed={
            "sourceDigest": hashlib.sha256(source or b"").hexdigest(),
            "archivePresent": receipt is not None,
        },
    ) from error


def finish_archive_unlink(path: Path, archive: LegacyArchive, archive_path: Path) -> None:
    """Converge an archive retry after its exact receipt became durable."""

    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        _resolve_archive_unlink_failure(path, archive, archive_path, exc)


def _resolve_archive_unlink_failure(
    path: Path,
    archive: LegacyArchive,
    archive_path: Path,
    error: OSError,
) -> None:
    source = read_publication_bytes(path)
    receipt = read_publication_bytes(archive_path)
    expected_receipt = (archive.model_dump_json(indent=2) + "\n").encode("utf-8")
    state = (
        source is None,
        _publication_digest(source) == archive.originalSha256,
        receipt == expected_receipt,
    )
    if state == (False, True, True):
        raise LegacyBridgeError(
            "legacy-archive-publication-pending",
            "the exact archive receipt is durable but original unlink was interrupted",
            expected={"legacyDigest": archive.originalSha256},
            observed={"sourcePresent": True, "archivePresent": True},
            next_action="archive",
        ) from error
    if state == (True, False, True):
        return
    raise LegacyBridgeError(
        "legacy-archive-conflict",
        "archive unlink observed contradictory source or receipt bytes",
        expected={"legacyDigest": archive.originalSha256},
        observed={
            "sourceDigest": hashlib.sha256(source or b"").hexdigest(),
            "sourcePresent": source is not None,
            "archivePresent": receipt is not None,
        },
    ) from error


def _publication_digest(payload: bytes | None) -> str:
    return hashlib.sha256(b"" if payload is None else payload).hexdigest()


def read_publication_bytes(path: Path) -> bytes | None:
    """Read optional publication bytes while translating every non-absence I/O failure."""

    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise legacy_io_error(
            "legacy-publication-evidence-unreadable",
            "legacy publication evidence is unreadable",
            failure=LegacyIoFailure("legacy-publication-read", "publication", path.name, exc),
        ) from exc
