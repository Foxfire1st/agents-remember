"""One bounded reader and terminal proof owner for enclosure lifecycle evidence."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from agents_remember.models.lifecycles.enclosure import (
    TerminalCleanupOperation,
    TerminalEnclosureArchiveEntry,
    TerminalEnclosureRemovedWorkingState,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees.integration.lifecycle.lifecycle_enclosure_adoption import (
    ADOPTION_RECEIPT,
    LifecycleEnclosureAdoptionReceipt,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocation,
    LifecycleOperationLocationError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    _require_record_matches_canonical_path,
)
from agents_remember.worktrees.integration.lifecycle.worker.state import project_worker_exit
from agents_remember.worktrees.sync_transaction_state import (
    SYNC_OPERATION_RECORD_NAME,
    observe_sync_operation,
)

_OPERATION_RECORD = re.compile(
    r"^(?:closeout|integrate|direct-landing)-operation"
    r"(?:\.generation-[1-9][0-9]*)?\.json$"
)
_LEGACY_MISSING_INTENT_RECORD = re.compile(
    r"^(?:closeout|direct-landing)-operation"
    r"\.legacy-missing-intent-generation-[1-9][0-9]*\.json$"
)
_MAX_CANONICAL_FILES = 1024
_MAX_CANONICAL_BYTES = 64 * 1024 * 1024
# The sync journal moved out of `.lifecycle/` into the group's `reports/` working
# directory; only the pre-move location is still seen here, and only until that enclosure
# is cleaned. These are the transaction states in which a journal holds no recovery
# authority; every other state must refuse rather than delete live authority.
_TERMINAL_SYNC_STATES = frozenset({"completed", "cancelled", "quarantined"})


@dataclass(frozen=True)
class _CanonicalRootEvidence:
    """Everything one canonical lifecycle root contributes to the terminal archive."""

    entries: list[TerminalEnclosureArchiveEntry]
    removed_working_state: list[TerminalEnclosureRemovedWorkingState]


def _canonical_entries(
    location: LifecycleOperationLocation,
    contract_path: Path,
    *,
    operation: TerminalCleanupOperation,
) -> _CanonicalRootEvidence:
    lifecycle = location.lifecycle_directory
    paths = _lifecycle_paths(lifecycle)
    entries: list[TerminalEnclosureArchiveEntry] = []
    removed: list[TerminalEnclosureRemovedWorkingState] = []
    total_bytes = 0
    for path in paths:
        if path.name.endswith((".lock", ".log")):
            continue
        if path.name == SYNC_OPERATION_RECORD_NAME:
            removed.append(
                _legacy_sync_working_state(
                    location,
                    path,
                    contract_path=contract_path,
                )
            )
            continue
        if not _is_canonical_artifact(path.name):
            raise _unowned_canonical_artifact(lifecycle, path.name)
        entry = _canonical_evidence_entry(path, operation=operation)
        total_bytes += entry.sizeBytes
        if len(entries) >= _MAX_CANONICAL_FILES or total_bytes > _MAX_CANONICAL_BYTES:
            raise RuntimeError(
                f"canonical lifecycle directory {lifecycle} exceeds the fixed file or byte bound "
                "of a terminal archive; remove the stray files from it and retry"
            )
        entries.append(entry)
    if not entries:
        raise RuntimeError(
            f"canonical lifecycle directory {lifecycle} holds no enclosure evidence to archive; "
            "restore its operation record there (it must not be empty) and retry"
        )
    return _CanonicalRootEvidence(entries=entries, removed_working_state=removed)


def _is_canonical_artifact(name: str) -> bool:
    return (
        name in {"enclosure-manifest.json", ADOPTION_RECEIPT}
        or _OPERATION_RECORD.fullmatch(name) is not None
        or _LEGACY_MISSING_INTENT_RECORD.fullmatch(name) is not None
    )


def _canonical_evidence_entry(
    path: Path,
    *,
    operation: TerminalCleanupOperation,
) -> TerminalEnclosureArchiveEntry:
    operation_record = _OPERATION_RECORD.fullmatch(path.name) is not None
    missing_intent_record = _LEGACY_MISSING_INTENT_RECORD.fullmatch(path.name) is not None
    payload = _read_regular_file(path, owner=f"canonical lifecycle artifact {path.name}")
    text = payload.decode("utf-8")
    if operation_record or missing_intent_record:
        record = LifecycleOperationRecord.model_validate_json(text)
        _require_record_matches_canonical_path(path, record)
        _require_archivable_operation(
            record,
            operation=operation,
            current=operation_record and ".generation-" not in path.name,
            name=path.name,
        )
    elif path.name == ADOPTION_RECEIPT:
        LifecycleEnclosureAdoptionReceipt.model_validate_json(text)
    return TerminalEnclosureArchiveEntry(
        relativePath=path.name,
        sha256=_sha256(payload),
        sizeBytes=len(payload),
        content=text,
    )


def _legacy_sync_working_state(
    location: LifecycleOperationLocation,
    path: Path,
    *,
    contract_path: Path,
) -> TerminalEnclosureRemovedWorkingState:
    """Account for a pre-move sync journal without archiving its bytes.

    The journal records one sync transaction's working state -- the refs it pins are
    retired when the transaction terminates -- so it is not terminal evidence of the
    leaf's landing. It is deleted with the enclosure root, and its identity is recorded
    so that deletion is explained rather than silent. A journal that is not terminal
    still holds recovery authority and refuses instead.
    """

    payload = _read_regular_file(path, owner=f"canonical lifecycle artifact {path.name}")
    projection = observe_sync_operation(location.worktree_group, contract_path=contract_path)
    state = projection.state if projection is not None else "absent"
    if state not in _TERMINAL_SYNC_STATES:
        raise LifecycleOperationLocationError(
            "terminal-archive-sync-transaction-active",
            f"{path.name} is not terminal sync working state (observed {state!r}), so "
            "cleanup cannot delete the enclosure while that transaction still holds "
            "recovery authority. Run worktree_sync with resolution_action='cancel' to roll "
            "it back, then clean up.",
            expected={"syncJournalState": sorted(_TERMINAL_SYNC_STATES)},
            observed={"syncJournal": path.name, "syncJournalState": state},
        )
    return TerminalEnclosureRemovedWorkingState(
        relativePath=path.name,
        sha256=_sha256(payload),
        sizeBytes=len(payload),
    )


def _unowned_canonical_artifact(lifecycle: Path, name: str) -> LifecycleOperationLocationError:
    """Refuse to delete a canonical-root file this scanner cannot classify.

    The guard's whole intent is that nothing unowned is discarded to make cleanup
    succeed, so an unrecognised file stays a refusal and never a deletion.
    """

    return LifecycleOperationLocationError(
        "terminal-archive-unowned-artifact",
        (
            f"{name} is not terminal enclosure evidence, so cleanup will not delete it. "
            "The scanner archives only enclosure-manifest.json, ADOPTION_RECEIPT, and "
            "(closeout|integrate|direct-landing)-operation records, and it removes a "
            "legacy sync-operation.json as sync working state. No automated remedy "
            "exists: inspect the named file and remove or relocate it, or extend this "
            "scanner when it is genuine enclosure evidence."
        ),
        expected={
            "canonicalArtifacts": [
                "enclosure-manifest.json",
                ADOPTION_RECEIPT,
                "(closeout|integrate|direct-landing)-operation*.json",
            ],
            "removedWorkingState": [SYNC_OPERATION_RECORD_NAME],
        },
        observed={"unownedArtifact": name, "lifecycleDirectory": lifecycle.as_posix()},
    )


def _require_archivable_operation(
    record: LifecycleOperationRecord,
    *,
    operation: TerminalCleanupOperation,
    current: bool,
    name: str,
) -> None:
    _require_terminal_operation(record, name)
    _require_cleanup_retry_disposition(record, operation, current, name)
    observed = project_worker_exit(record)
    _require_absent_worker_authority(observed, name)
    _require_resolved_worker_termination(observed, name)
    _require_resolved_mutations(record, name)
    _require_resolved_publications(record, name)


def _require_terminal_operation(record: LifecycleOperationRecord, name: str) -> None:
    if record.status not in {"completed", "failed", "cancelled"}:
        raise _operation_refusal(
            "terminal-archive-operation-nonterminal",
            f"{name} remains nonterminal at {record.status}",
            name=name,
            observed={"status": record.status},
        )


def _require_cleanup_retry_disposition(
    record: LifecycleOperationRecord,
    operation: TerminalCleanupOperation,
    current: bool,
    name: str,
) -> None:
    if current and operation == "worktree_cleanup" and record.status == "failed":
        raise _operation_refusal(
            "terminal-archive-operation-retryable",
            f"{name} remains retryable and cannot be collected by cleanup",
            name=name,
            observed={"status": record.status},
        )


def _require_absent_worker_authority(record: LifecycleOperationRecord, name: str) -> None:
    if record.workerPid is not None or record.workerLease is not None:
        raise _operation_refusal(
            "terminal-archive-operation-worker-active",
            f"{name} retains worker authority",
            name=name,
            observed={
                "workerPidPresent": record.workerPid is not None,
                "workerLeasePresent": record.workerLease is not None,
            },
        )


def _require_resolved_worker_termination(record: LifecycleOperationRecord, name: str) -> None:
    if record.workerTermination is not None and record.workerTermination.state != "exited":
        raise _operation_refusal(
            "terminal-archive-operation-worker-termination-active",
            f"{name} retains unresolved termination authority",
            name=name,
            observed={"workerTermination": record.workerTermination.state},
        )


def _require_resolved_mutations(record: LifecycleOperationRecord, name: str) -> None:
    if record.preparation is not None:
        raise _operation_refusal(
            "terminal-archive-operation-preparation-retained",
            f"{name} retains private preparation without an explicit retention disposition",
            name=name,
            observed={"preparationGeneration": record.preparation.generation},
        )
    if any(item.state == "mutation-intent" for item in record.mutationEvidence.values()):
        raise _operation_refusal(
            "terminal-archive-operation-mutation-ambiguous",
            f"{name} retains ambiguous Git mutation intent",
            name=name,
            observed={"mutationIntent": True},
        )


def _require_resolved_publications(record: LifecycleOperationRecord, name: str) -> None:
    _require_resolved_integration_claim(record, name)
    _require_terminal_organizational_publication(record, name)
    _require_resolved_door_publication(record, name)


def _require_resolved_integration_claim(record: LifecycleOperationRecord, name: str) -> None:
    publication = record.integrationPublication
    if publication is not None and publication.claimState == "intent":
        raise _operation_refusal(
            "terminal-archive-operation-publication-ambiguous",
            f"{name} retains an unproven integration claim publication",
            name=name,
            observed={"integrationClaimState": publication.claimState},
        )


def _require_terminal_organizational_publication(
    record: LifecycleOperationRecord,
    name: str,
) -> None:
    publication = record.integrationPublication
    if (
        publication is not None
        and publication.organizationalCompletion is not None
        and record.status != "completed"
    ):
        raise _operation_refusal(
            "terminal-archive-operation-publication-recoverable",
            f"{name} retains recoverable organizational publication intent",
            name=name,
            observed={"organizationalCompletionPresent": True},
        )


def _require_resolved_door_publication(record: LifecycleOperationRecord, name: str) -> None:
    if record.doorPublication is not None and record.doorPublication.state == "intent":
        raise _operation_refusal(
            "terminal-archive-operation-door-ambiguous",
            f"{name} retains an unproven closeout-door publication",
            name=name,
            observed={"doorPublicationState": record.doorPublication.state},
        )


def _operation_refusal(
    status: str,
    detail: str,
    *,
    name: str,
    observed: dict[str, object],
) -> LifecycleOperationLocationError:
    return LifecycleOperationLocationError(
        status,
        detail,
        expected={
            "operationRecord": name,
            "terminal": True,
            "liveAuthority": False,
            "ambiguousWal": False,
        },
        observed={"operationRecord": name, **observed},
    )


def _read_regular_file(path: Path, *, owner: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(
                f"{owner} ({path}) must be one regular non-symlink file; replace it with the "
                "real file or remove the link, then retry"
            )
        return path.read_bytes()
    except OSError as error:
        raise RuntimeError(
            f"{owner} ({path}) is unreadable: {error}; fix its permissions or restore it, then retry"
        ) from error


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _lifecycle_paths(lifecycle: Path) -> list[Path]:
    try:
        return sorted(lifecycle.iterdir(), key=lambda path: path.name)
    except OSError as error:
        raise RuntimeError(
            f"canonical lifecycle directory {lifecycle} is unreadable: {error}; fix its "
            "permissions or restore it, then retry"
        ) from error


def terminal_operation_evidence(group: Path) -> list[TerminalEnclosureArchiveEntry]:
    """Read all recognized current, historical and legacy-intent operation records without adoption.

    A missing canonical directory has no retained records. Present generations use exactly the
    archive's schema, regular-file reader and terminal authority checks, under the same bounds.
    """
    lifecycle = group / ".lifecycle"
    if not lifecycle.exists():
        return []
    entries: list[TerminalEnclosureArchiveEntry] = []
    total_bytes = 0
    for path in _lifecycle_paths(lifecycle):
        if (
            _OPERATION_RECORD.fullmatch(path.name) is None
            and _LEGACY_MISSING_INTENT_RECORD.fullmatch(path.name) is None
        ):
            continue
        try:
            entry = _canonical_evidence_entry(path, operation="worktree_abandon")
        except (ValueError, OSError, RuntimeError) as error:
            raise LifecycleOperationLocationError(
                "terminal-operation-evidence-refused",
                f"operation {path} cannot establish terminal authority: {error}",
                expected={"operationRecord": path.as_posix(), "terminal": True, "readable": True},
                observed={"errorType": type(error).__name__},
            ) from error
        total_bytes += entry.sizeBytes
        if len(entries) >= _MAX_CANONICAL_FILES or total_bytes > _MAX_CANONICAL_BYTES:
            raise RuntimeError(
                f"terminal operation evidence exceeds its fixed file or byte bound: {lifecycle}"
            )
        entries.append(entry)
    return entries
