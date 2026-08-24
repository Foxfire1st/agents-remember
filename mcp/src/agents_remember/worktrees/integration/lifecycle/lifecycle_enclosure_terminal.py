"""Terminal enclosure archive proof and exact successor-generation authority."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from agents_remember.models.lifecycles.enclosure import (
    LifecycleEnclosureLocator,
    TerminalEnclosureArchive,
    TerminalEnclosurePredecessor,
    TerminalEnclosureReceipt,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location_errors import (
    LifecycleOperationLocationError,
    location_error,
    location_path_presence,
    read_location_bytes,
)
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    ContractError,
    WorktreeContract,
    amend_contract,
    parse_contract_text,
)

TerminalCleanupContractState = Literal["archive-ready", "cleanup-completed"]


@dataclass(frozen=True)
class TerminalCleanupContractAuthority:
    """Exact archived deletion authority plus the only legal surviving contract state."""

    locator: LifecycleEnclosureLocator
    archive: TerminalEnclosureArchive
    archived_contract: WorktreeContract
    current_contract: WorktreeContract
    state: TerminalCleanupContractState


def terminal_enclosure_archive_paths(
    coordination_root: Path,
    publication_request_id: str,
) -> tuple[Path, Path]:
    """Return the sole external archive and receipt addresses for one generation."""

    archive = (
        coordination_root.resolve(strict=False)
        / "controlplane"
        / "lifecycle-enclosure-archives"
        / f"{publication_request_id}.json"
    )
    return archive, archive.with_suffix(".receipt.json")


def terminal_predecessor(
    locator: LifecycleEnclosureLocator,
    contract_path: Path,
) -> TerminalEnclosurePredecessor:
    """Collapse one proven terminal locator into the next generation's typed link."""

    if (
        locator.state != "terminal-archived"
        or locator.terminalArchivePath is None
        or locator.terminalArchiveSha256 is None
        or locator.terminalReceiptPath is None
    ):
        raise location_error(
            "operation-location-terminal-proof-incomplete",
            "the prior enclosure is not backed by complete terminal archive evidence",
            contract_path=contract_path,
            observed={
                "publicationRequestId": locator.publicationRequestId,
                "state": locator.state,
                "terminalArchivePath": locator.terminalArchivePath or "",
                "terminalArchiveSha256": locator.terminalArchiveSha256 or "",
                "terminalReceiptPath": locator.terminalReceiptPath or "",
            },
        )
    return TerminalEnclosurePredecessor(
        publicationRequestId=locator.publicationRequestId,
        bindingFingerprint=locator.bindingFingerprint,
        worktreeGroup=locator.worktreeGroup,
        manifestPath=locator.manifestPath,
        expectedManifestSha256=locator.expectedManifestSha256,
        expectedInitialContractSha256=locator.expectedInitialContractSha256,
        terminalArchivePath=locator.terminalArchivePath,
        terminalArchiveSha256=locator.terminalArchiveSha256,
        terminalReceiptPath=locator.terminalReceiptPath,
    )


def validate_terminal_proof(
    coordination_root: Path,
    contract_path: Path,
    locator: LifecycleEnclosureLocator,
) -> TerminalEnclosureArchive | None:
    """Verify external archive bytes and their strict receipt before terminal use."""

    if locator.state != "terminal-archived":
        return None
    assert locator.terminalArchivePath is not None
    assert locator.terminalArchiveSha256 is not None
    assert locator.terminalReceiptPath is not None
    expected_archive, expected_receipt = terminal_enclosure_archive_paths(
        coordination_root,
        locator.publicationRequestId,
    )
    archive_path = Path(locator.terminalArchivePath).resolve(strict=False)
    receipt_path = Path(locator.terminalReceiptPath).resolve(strict=False)
    old_root = Path(locator.worktreeGroup).resolve(strict=False)
    expected = {
        "terminalArchivePath": expected_archive.as_posix(),
        "terminalReceiptPath": expected_receipt.as_posix(),
        "archiveOutsideWorktreeGroup": True,
        "terminalArchiveSha256": locator.terminalArchiveSha256,
    }
    observed = {
        "terminalArchivePath": archive_path.as_posix(),
        "terminalReceiptPath": receipt_path.as_posix(),
        "archiveOutsideWorktreeGroup": not archive_path.is_relative_to(old_root)
        and not receipt_path.is_relative_to(old_root),
        "terminalArchiveSha256": locator.terminalArchiveSha256,
    }
    if expected != observed:
        raise LifecycleOperationLocationError(
            "operation-location-terminal-proof-mismatch",
            "terminal archive authority is not at its exact external archive and receipt addresses",
            expected=expected,
            observed=observed,
        )
    _require_terminal_file(archive_path, contract_path, "terminal archive")
    archive_bytes = read_location_bytes(archive_path, "terminal archive", contract_path)
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    if archive_sha256 != locator.terminalArchiveSha256:
        raise LifecycleOperationLocationError(
            "operation-location-terminal-proof-mismatch",
            "the terminal enclosure archive bytes do not match the locator digest",
            expected={"terminalArchiveSha256": locator.terminalArchiveSha256},
            observed={"terminalArchiveSha256": archive_sha256},
        )
    try:
        archive = TerminalEnclosureArchive.model_validate_json(archive_bytes)
    except ValidationError as exc:
        raise location_error(
            "operation-location-terminal-proof-invalid",
            "the terminal enclosure archive is invalid",
            contract_path=contract_path,
            observed={
                "terminalArchivePath": archive_path.as_posix(),
                "errorType": type(exc).__name__,
            },
        ) from exc
    source_locator = LifecycleEnclosureLocator.model_validate(
        {
            **locator.model_dump(mode="json"),
            "state": "addressable",
            "terminalArchivePath": None,
            "terminalArchiveSha256": None,
            "terminalReceiptPath": None,
        }
    )
    if archive.locator != source_locator:
        raise LifecycleOperationLocationError(
            "operation-location-terminal-proof-mismatch",
            "the terminal archive does not preserve the exact pre-deletion locator",
            expected=source_locator.model_dump(mode="json"),
            observed=archive.locator.model_dump(mode="json"),
        )
    _require_terminal_file(receipt_path, contract_path, "terminal receipt")
    receipt = _read_terminal_receipt(receipt_path, contract_path)
    receipt_expected = _terminal_receipt(locator)
    if receipt != receipt_expected:
        raise LifecycleOperationLocationError(
            "operation-location-terminal-proof-mismatch",
            "the terminal enclosure receipt does not bind the exact locator and archive generation",
            expected=receipt_expected.model_dump(mode="json"),
            observed=receipt.model_dump(mode="json"),
        )
    return archive


def terminal_cleanup_contract_authority(
    coordination_root: Path,
    current_contract: WorktreeContract,
    locator: LifecycleEnclosureLocator,
) -> TerminalCleanupContractAuthority:
    """Bind terminal retry to archived bytes and one sanctioned cleanup-cell progression."""

    archive = validate_terminal_proof(
        coordination_root,
        current_contract.contract_path,
        locator,
    )
    if archive is None:
        raise LifecycleOperationLocationError(
            "operation-location-terminal-archive-required",
            "terminal cleanup authority requires an exact terminal locator and archive receipt",
            expected={"locatorState": "terminal-archived"},
            observed={"locatorState": locator.state},
        )
    try:
        archived_contract = parse_contract_text(
            archive.contractText,
            path=Path(archive.contractPath),
        )
    except (ContractError, OSError, UnicodeError, ValueError) as exc:
        raise LifecycleOperationLocationError(
            "terminal-archive-contract-invalid",
            "the accepted terminal archive does not contain one valid canonical contract",
            expected={
                "contractPath": archive.contractPath,
                "contractSha256": archive.contractSha256,
            },
            observed={"errorType": type(exc).__name__},
        ) from exc
    _require_archived_contract_identity(archive, archived_contract)
    cleanup_cells = (
        ContractCells(cleanup="completed")
        if archive.cleanupOperation == "worktree_cleanup"
        else ContractCells(cleanup="abandoned")
    )
    completed_contract = amend_contract(
        archived_contract,
        cleanup_cells,
    )
    if current_contract == archived_contract:
        state: TerminalCleanupContractState = "archive-ready"
    elif current_contract == completed_contract:
        state = "cleanup-completed"
    else:
        raise LifecycleOperationLocationError(
            "terminal-archive-contract-mismatch",
            "the surviving contract changed outside the accepted terminal cleanup progression",
            expected={
                "contractPath": archive.contractPath,
                "acceptedContractSha256": archive.contractSha256,
                "acceptedCleanupOperation": archive.cleanupOperation,
                "allowedStates": ["archive-ready", "cleanup-completed"],
            },
            observed={
                "contractPath": current_contract.contract_path.resolve(
                    strict=False
                ).as_posix(),
                "cleanup": current_contract.cleanup,
                "sameAcceptedAuthority": False,
            },
        )
    return TerminalCleanupContractAuthority(
        locator=locator,
        archive=archive,
        archived_contract=archived_contract,
        current_contract=current_contract,
        state=state,
    )


def _require_archived_contract_identity(
    archive: TerminalEnclosureArchive,
    contract: WorktreeContract,
) -> None:
    expected = {
        "contractPath": archive.contractPath,
        "repository": archive.locator.repository,
        "worktreeGroup": archive.locator.worktreeGroup,
        "taskId": archive.manifest.taskId,
        "taskName": archive.manifest.taskName,
        "leafId": archive.manifest.leafId,
        "lifecycleId": archive.manifest.lifecycleId,
    }
    observed = {
        "contractPath": contract.contract_path.resolve(strict=False).as_posix(),
        "repository": contract.repo_name,
        "worktreeGroup": contract.worktree_group.resolve(strict=False).as_posix(),
        "taskId": contract.task_id,
        "taskName": contract.task_name,
        "leafId": contract.leaf_id,
        "lifecycleId": contract.lifecycle_id,
    }
    if expected != observed:
        raise LifecycleOperationLocationError(
            "terminal-archive-contract-identity-mismatch",
            "the archived contract contradicts its immutable locator or enclosure manifest",
            expected=expected,
            observed=observed,
        )


def _require_terminal_file(path: Path, contract_path: Path, owner: str) -> None:
    if location_path_presence(path, contract_path, owner) == "missing":
        path_field = "terminalArchivePath" if owner == "terminal archive" else "terminalReceiptPath"
        raise location_error(
            "operation-location-terminal-proof-missing",
            f"the {owner} is missing",
            contract_path=contract_path,
            observed={path_field: path.as_posix()},
        )


def _read_terminal_receipt(
    receipt_path: Path,
    contract_path: Path,
) -> TerminalEnclosureReceipt:
    try:
        return TerminalEnclosureReceipt.model_validate_json(
            read_location_bytes(receipt_path, "terminal receipt", contract_path)
        )
    except ValidationError as exc:
        raise location_error(
            "operation-location-terminal-proof-invalid",
            "the terminal enclosure receipt is invalid",
            contract_path=contract_path,
            observed={
                "terminalReceiptPath": receipt_path.as_posix(),
                "errorType": type(exc).__name__,
            },
        ) from exc


def _terminal_receipt(locator: LifecycleEnclosureLocator) -> TerminalEnclosureReceipt:
    assert locator.terminalArchivePath is not None
    assert locator.terminalArchiveSha256 is not None
    assert locator.terminalReceiptPath is not None
    return TerminalEnclosureReceipt(
        locatorId=locator.locatorId,
        publicationRequestId=locator.publicationRequestId,
        bindingFingerprint=locator.bindingFingerprint,
        repository=locator.repository,
        contractPath=locator.stableAddress,
        worktreeGroup=locator.worktreeGroup,
        manifestPath=locator.manifestPath,
        expectedManifestSha256=locator.expectedManifestSha256,
        expectedInitialContractSha256=locator.expectedInitialContractSha256,
        terminalArchivePath=locator.terminalArchivePath,
        terminalArchiveSha256=locator.terminalArchiveSha256,
        terminalReceiptPath=locator.terminalReceiptPath,
    )


def require_successor_generation(
    contract: WorktreeContract,
    predecessor_contract: WorktreeContract,
    predecessor: TerminalEnclosurePredecessor,
) -> None:
    """Prove the exact restartable contract authorizes one successor generation."""

    expected = _successor_identity(
        contract,
        predecessor.worktreeGroup,
        kind="leaf",
        restartable=True,
    )
    observed = _successor_identity(
        predecessor_contract,
        predecessor_contract.worktree_group.resolve(strict=False).as_posix(),
        kind=predecessor_contract.kind,
        restartable=_restartable_predecessor_contract(predecessor_contract),
    )
    if expected != observed:
        raise LifecycleOperationLocationError(
            "operation-location-successor-mismatch",
            "the predecessor contract does not authorize this successor enclosure generation",
            expected=expected,
            observed=observed,
        )


def _successor_identity(
    contract: WorktreeContract,
    predecessor_worktree_group: str,
    *,
    kind: str,
    restartable: bool,
) -> dict[str, object]:
    return {
        "repository": contract.repo_name,
        "coordinationRoot": contract.coordination_root.resolve(strict=False).as_posix(),
        "contractPath": contract.contract_path.resolve(strict=False).as_posix(),
        "taskRoot": contract.task_root.resolve(strict=False).as_posix(),
        "taskId": contract.task_id,
        "taskName": contract.task_name,
        "parentTaskName": contract.parent_task_name,
        "parentContractPath": (
            contract.parent_contract_path.resolve(strict=False).as_posix()
            if contract.parent_contract_path is not None
            else ""
        ),
        "leafId": contract.leaf_id,
        "kind": kind,
        "predecessorWorktreeGroup": predecessor_worktree_group,
        "restartable": restartable,
    }


def _restartable_predecessor_contract(contract: WorktreeContract) -> bool:
    if contract.cleanup == "abandoned":
        return True
    if contract.cleanup != "reopened":
        return False
    return (
        contract.lifecycle_id == ""
        and contract.human_review_status == "pending-review"
        and not contract.approved_for_commit
        and contract.closeout_status == "not-started"
        and contract.integration_status == "not-started"
        and not any(
            (
                contract.code_commit,
                contract.memory_content_commit,
                contract.ledger_commit,
                contract.integrated_code_commit,
                contract.integrated_memory_content_commit,
                contract.integrated_ledger_commit,
            )
        )
    )


__all__ = [
    "require_successor_generation",
    "terminal_enclosure_archive_paths",
    "terminal_predecessor",
    "validate_terminal_proof",
]
