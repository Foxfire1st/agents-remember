"""Source-pair-scoped activation authority for durable atomic master work.

Series contracts prove that work exists.  This replace-in-place control-plane
snapshot separately selects which one may expose new implementation work.  The
queue only observes the snapshot; task-document mutation never reads it.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from agents_remember.controlplane.durable_store import StoreOwnership, exclusive_access
from agents_remember.kernel.atomic_write import (
    atomic_replace,
    atomic_write_bytes,
    atomic_write_text,
)
from agents_remember.models.structural.atomic_series_activation import (
    AtomicSeriesActivationArchiveEvidence,
    AtomicSeriesActivationRecord,
    AtomicSeriesObservedState,
    AtomicSeriesSelectionState,
    AtomicSeriesSourcePair,
    AtomicSeriesSourceRef,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.integration.integration_branch_repository import (
    canonical_local_branch,
)
from agents_remember.worktrees.modules.git import repository_identity
from agents_remember.worktrees.scheduling_mode import TERMINAL_SERIES_CLEANUP
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

ACTIVATION_OWNERSHIP = StoreOwnership(
    store="atomic-series-activation",
    writers=("mcp",),
    compaction_owner=None,
    rationale=(
        "atomic start/attach/dispatch selects one master per protected source pair; "
        "task truth and the disposable closeout projection are read-only consumers"
    ),
)


class AtomicSeriesActivationError(RuntimeError):
    """The selected source-pair authority is absent, malformed, or inconsistent."""

    def __init__(self, status: str, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class AtomicSeriesActivationObservation:
    """Strict read of one source-pair selection without creating store artifacts."""

    source_pair: AtomicSeriesSourcePair
    source_pair_fingerprint: str
    activation_path: Path
    state: AtomicSeriesObservedState
    record: AtomicSeriesActivationRecord | None = None
    error_type: str | None = None
    detail: str | None = None

    @property
    def selected_master(self) -> TaskDocumentRef | None:
        if self.record is None or self.state == "vacant":
            return None
        return self.record.selectedMaster

    @property
    def last_selected_master(self) -> TaskDocumentRef | None:
        """Retained audit identity, including a durable vacant release record."""

        return self.record.selectedMaster if self.record is not None else None

    def source_fact(self) -> dict[str, object]:
        fact: dict[str, object] = {
            "address": self.activation_path.as_posix(),
            "sourcePairFingerprint": self.source_pair_fingerprint,
            "state": self.state,
        }
        if self.record is not None:
            fact["record"] = self.record.model_dump(mode="json")
        if self.error_type is not None:
            fact["errorType"] = self.error_type
        return fact


def atomic_series_source_pair(contract: WorktreeContract) -> AtomicSeriesSourcePair:
    """Derive the exact normalized protected source pair from a canonical series contract."""

    _require_canonical_series_contract(contract)
    code = _atomic_series_source_ref(
        contract.code_repo_path,
        contract.code_source_branch,
        side="code",
    )
    memory: AtomicSeriesSourceRef | None = None
    if contract.memory_mode == "external":
        memory_repo = contract.memory_repo_path
        if memory_repo is None:
            raise AtomicSeriesActivationError(
                "atomic-series-memory-repository-missing",
                "external-memory atomic-series authority has no memory repository",
            )
        memory = _atomic_series_source_ref(
            memory_repo,
            contract.memory_source_branch,
            side="memory",
        )
    return AtomicSeriesSourcePair(code=code, memory=memory)


def _atomic_series_source_ref(
    repository: Path,
    branch: str,
    *,
    side: str,
) -> AtomicSeriesSourceRef:
    try:
        identity = repository_identity(repository)
        if identity is None:
            raise RuntimeError("repository identity is unavailable")
        canonical_branch = canonical_local_branch(repository, branch)
        return AtomicSeriesSourceRef(
            repositoryIdentity=identity.as_posix(),
            sourceBranch=canonical_branch,
        )
    except (OSError, RuntimeError, UnicodeError, ValidationError, ValueError) as error:
        raise AtomicSeriesActivationError(
            f"atomic-series-{side}-source-identity-unreadable",
            f"atomic-series {side} repository/source identity is unavailable: {error}",
        ) from error


def source_pair_fingerprint(source_pair: AtomicSeriesSourcePair) -> str:
    payload = json.dumps(
        source_pair.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def activation_path(
    coordination_root: Path,
    source_pair: AtomicSeriesSourcePair,
) -> Path:
    digest = source_pair_fingerprint(source_pair)
    return coordination_root / "controlplane" / "atomic-series-activation" / f"{digest}.json"


def observe_atomic_series(
    contract_or_pair: WorktreeContract | AtomicSeriesSourcePair,
    *,
    coordination_root: Path | None = None,
) -> AtomicSeriesActivationObservation:
    """Read one exact activation snapshot strictly; missing means vacant, never inferred."""

    if isinstance(contract_or_pair, WorktreeContract):
        contract = contract_or_pair
        source_pair = atomic_series_source_pair(contract)
        root = contract.coordination_root
    else:
        source_pair = contract_or_pair
        if coordination_root is None:
            raise ValueError("coordination_root is required when observing a source pair")
        root = coordination_root
    path = activation_path(root, source_pair)
    return observe_atomic_series_path(root, source_pair, path)


def publish_atomic_series_selection(
    contract: WorktreeContract,
    state: AtomicSeriesSelectionState,
    *,
    timestamp: str | None = None,
) -> AtomicSeriesActivationObservation:
    """Replace the exact pair selection, recovering corrupt bytes only through selection.

    The caller holds repository integration authority across the larger
    reconciling -> source-sync -> active transaction.  This store lock only
    serializes the snapshot read/archive/replace itself.
    """

    if _series_is_terminal(contract):
        raise AtomicSeriesActivationError(
            "atomic-series-terminal",
            "a terminal atomic-series contract cannot be selected",
        )
    ACTIVATION_OWNERSHIP.check_declared_writer()
    source_pair = atomic_series_source_pair(contract)
    pair_fingerprint = source_pair_fingerprint(source_pair)
    path = activation_path(contract.coordination_root, source_pair)
    selected_at = timestamp or _now_iso()
    selected_master = series_master_ref(contract)
    with exclusive_access(path, ACTIVATION_OWNERSHIP):
        previous = observe_atomic_series_path(contract.coordination_root, source_pair, path)
        if previous.state == "unreadable":
            _archive_unreadable_selection(
                path,
                previous,
                selected_master,
                archived_at=selected_at,
            )
        previous_record = previous.record
        if (
            previous_record is not None
            and previous.state == state
            and previous_record.selectedMaster == selected_master
            and Path(previous_record.contractPath).resolve(strict=False)
            == contract.contract_path.resolve(strict=False)
        ):
            return previous
        revision = previous_record.revision + 1 if previous_record is not None else 1
        record = AtomicSeriesActivationRecord(
            sourcePairFingerprint=pair_fingerprint,
            sourcePair=source_pair,
            selectedMaster=selected_master,
            contractPath=contract.contract_path.resolve().as_posix(),
            state=state,
            revision=revision,
            selectedAt=selected_at,
        )
        atomic_write_text(path, record.model_dump_json(indent=2) + "\n")
        return AtomicSeriesActivationObservation(
            source_pair,
            pair_fingerprint,
            path,
            state,
            record,
        )


def require_selected_atomic_series(
    contract: WorktreeContract,
    *,
    required_state: AtomicSeriesSelectionState = "reconciling",
) -> AtomicSeriesActivationObservation:
    """Prove continuation/cancellation addresses the already-selected exact contract."""

    observation = observe_atomic_series(contract)
    expected_master = series_master_ref(contract)
    record = observation.record
    if (
        observation.state != required_state
        or record is None
        or record.selectedMaster != expected_master
        or Path(record.contractPath).resolve(strict=False)
        != contract.contract_path.resolve(strict=False)
    ):
        raise AtomicSeriesActivationError(
            "atomic-series-activation-selected-contract-mismatch",
            "sync continuation/cancellation requires the exact selected reconciling series",
        )
    return observation


def require_atomic_series_cancellation_owner(
    contract: WorktreeContract,
) -> AtomicSeriesActivationObservation:
    """Prove cancel/replay addresses the selected or exactly last-released series."""

    observation = observe_atomic_series(contract)
    expected_master = series_master_ref(contract)
    record = observation.record
    if (
        observation.state not in {"reconciling", "vacant"}
        or record is None
        or record.selectedMaster != expected_master
        or Path(record.contractPath).resolve(strict=False)
        != contract.contract_path.resolve(strict=False)
    ):
        raise AtomicSeriesActivationError(
            "atomic-series-activation-selected-contract-mismatch",
            "sync cancellation requires the exact selected or last-released series",
        )
    return observation


def activation_waiting_reason(
    observation: AtomicSeriesActivationObservation,
    master_ref: TaskDocumentRef,
) -> str | None:
    """Project logical pause/reconciliation as waiting, never lifecycle ownership."""

    selected = observation.selected_master
    if observation.state == "vacant" or selected is None:
        return "atomic-series-not-selected"
    if selected != master_ref:
        return f"atomic-series-paused-by: {selected.key}"
    if observation.state == "reconciling":
        return "atomic-series-reconciling"
    return None


def observe_atomic_series_path(
    coordination_root: Path,
    source_pair: AtomicSeriesSourcePair,
    path: Path,
) -> AtomicSeriesActivationObservation:
    fingerprint = source_pair_fingerprint(source_pair)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return AtomicSeriesActivationObservation(
            source_pair,
            fingerprint,
            path,
            "vacant",
        )
    except OSError as exc:
        return _unreadable(source_pair, fingerprint, path, type(exc).__name__, str(exc))
    if not stat.S_ISREG(mode):
        return _unreadable(
            source_pair,
            fingerprint,
            path,
            "atomic-series-activation-nonregular",
            "atomic-series activation authority is not a regular file",
        )
    try:
        record = AtomicSeriesActivationRecord.model_validate_json(_read_regular_entry(path))
        _require_record_identity(record, source_pair, fingerprint, path)
        return _observation_from_record(
            coordination_root,
            source_pair,
            fingerprint,
            path,
            record,
        )
    except (
        AtomicSeriesActivationError,
        ContractError,
        OSError,
        RuntimeError,
        UnicodeError,
        ValidationError,
        ValueError,
    ) as exc:
        status = getattr(exc, "status", type(exc).__name__)
        detail = getattr(exc, "detail", str(exc))
        return _unreadable(source_pair, fingerprint, path, str(status), str(detail))


def _observation_from_record(
    coordination_root: Path,
    source_pair: AtomicSeriesSourcePair,
    fingerprint: str,
    path: Path,
    record: AtomicSeriesActivationRecord,
) -> AtomicSeriesActivationObservation:
    if record.state == "vacant":
        state: AtomicSeriesObservedState = "vacant"
    elif _series_is_terminal(_load_selected_contract(coordination_root, record)):
        state = "vacant"
    else:
        state = record.state
    return AtomicSeriesActivationObservation(
        source_pair,
        fingerprint,
        path,
        state,
        record,
    )


def _require_record_identity(
    record: AtomicSeriesActivationRecord,
    source_pair: AtomicSeriesSourcePair,
    fingerprint: str,
    path: Path,
) -> None:
    if record.sourcePair != source_pair or record.sourcePairFingerprint != fingerprint:
        raise AtomicSeriesActivationError(
            "atomic-series-activation-source-pair-mismatch",
            f"activation snapshot does not match its source-pair path: {path}",
        )


def _load_selected_contract(
    coordination_root: Path,
    record: AtomicSeriesActivationRecord,
) -> WorktreeContract:
    path = Path(record.contractPath)
    task_root = (coordination_root / "tasks" / record.selectedMaster.repository).resolve(
        strict=False
    )
    if not path.is_absolute() or not path.resolve(strict=False).is_relative_to(task_root):
        raise AtomicSeriesActivationError(
            "atomic-series-activation-contract-outside-task-root",
            "selected contract path is outside its canonical task repository",
        )
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode):
        raise AtomicSeriesActivationError(
            "atomic-series-activation-contract-nonregular",
            "selected contract authority is not a regular file",
        )
    contract = load_contract(path)
    _require_canonical_series_contract(contract)
    if atomic_series_source_pair(contract) != record.sourcePair:
        raise AtomicSeriesActivationError(
            "atomic-series-activation-contract-source-pair-mismatch",
            "selected contract no longer belongs to the recorded source pair",
        )
    if series_master_ref(contract) != record.selectedMaster:
        raise AtomicSeriesActivationError(
            "atomic-series-activation-master-mismatch",
            "selected contract does not belong to the recorded master",
        )
    return contract


def _require_canonical_series_contract(contract: WorktreeContract) -> None:
    expected = series_contract_path(contract.task_root)
    if (
        contract.kind != "series"
        or contract.contract_path.resolve(strict=False) != expected.resolve(strict=False)
        or contract.task_artifact.with_suffix(".json").resolve(strict=False)
        != (contract.task_root / "task.json").resolve(strict=False)
    ):
        raise AtomicSeriesActivationError(
            "atomic-series-contract-authority-invalid",
            "activation requires the exact canonical atomic-series contract",
        )


def series_master_ref(contract: WorktreeContract) -> TaskDocumentRef:
    repository_root = (contract.coordination_root / "tasks" / contract.repo_name).resolve(
        strict=False
    )
    task_path = (contract.task_root / "task.json").resolve(strict=False)
    if not task_path.is_relative_to(repository_root):
        raise AtomicSeriesActivationError(
            "atomic-series-task-outside-repository",
            "atomic-series task authority escapes its canonical task repository",
        )
    return TaskDocumentRef(
        repository=contract.repo_name,
        path=task_path.relative_to(repository_root).as_posix(),
    )


def _series_is_terminal(contract: WorktreeContract) -> bool:
    return bool(
        contract.integration_status == "completed" or contract.cleanup in TERMINAL_SERIES_CLEANUP
    )


def _archive_unreadable_selection(
    path: Path,
    observation: AtomicSeriesActivationObservation,
    replacement_master: TaskDocumentRef,
    *,
    archived_at: str,
) -> None:
    archive_root = path.parent / "archive" / observation.source_pair_fingerprint
    stamp = archived_at.replace(":", "-").replace("+", "_")
    archive_kind: Literal["raw-bytes", "opaque-entry", "absence"]
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        descriptor = _opaque_archive_descriptor(path, observation, "absent")
        raw = json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(raw).hexdigest()
        snapshot: Path | None = None
        archive_kind = "absence"
    except OSError as exc:
        raise AtomicSeriesActivationError(
            "atomic-series-activation-archive-refused",
            f"cannot inspect unreadable activation authority before repair: {exc}",
        ) from exc
    else:
        if stat.S_ISREG(mode):
            try:
                raw = _read_regular_entry(path)
            except OSError as exc:
                raise AtomicSeriesActivationError(
                    "atomic-series-activation-archive-refused",
                    f"cannot retain malformed activation bytes before repair: {exc}",
                ) from exc
            digest = hashlib.sha256(raw).hexdigest()
            snapshot = archive_root / f"{stamp}-{digest}.snapshot"
            atomic_write_bytes(snapshot, raw)
            archive_kind = "raw-bytes"
        else:
            file_type = (
                "symlink" if stat.S_ISLNK(mode) else "directory" if stat.S_ISDIR(mode) else "other"
            )
            descriptor = _opaque_archive_descriptor(path, observation, file_type)
            raw = json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()
            digest = hashlib.sha256(raw).hexdigest()
            snapshot = archive_root / f"{stamp}-{digest}.entry"
            if snapshot.exists() or snapshot.is_symlink():
                raise AtomicSeriesActivationError(
                    "atomic-series-activation-archive-refused",
                    "the exact opaque activation archive destination already exists",
                )
            archive_root.mkdir(parents=True, exist_ok=True)
            try:
                atomic_replace(path, snapshot)
            except OSError as exc:
                raise AtomicSeriesActivationError(
                    "atomic-series-activation-archive-refused",
                    f"cannot preserve the nonregular activation entry before repair: {exc}",
                ) from exc
            archive_kind = "opaque-entry"
    evidence_path = archive_root / f"{stamp}-{digest}.json"
    evidence = AtomicSeriesActivationArchiveEvidence(
        sourcePairFingerprint=observation.source_pair_fingerprint,
        activationPath=path.as_posix(),
        archiveKind=archive_kind,
        snapshotPath=snapshot.as_posix() if snapshot is not None else None,
        snapshotSha256=digest,
        snapshotSize=len(raw),
        errorType=observation.error_type or "atomic-series-activation-unreadable",
        detail=observation.detail or "activation snapshot could not be validated",
        replacementMaster=replacement_master,
        archivedAt=archived_at,
    )
    atomic_write_text(evidence_path, evidence.model_dump_json(indent=2) + "\n")


def _read_regular_entry(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read()


def _opaque_archive_descriptor(
    path: Path,
    observation: AtomicSeriesActivationObservation,
    file_type: str,
) -> dict[str, object]:
    target = os.readlink(path) if file_type == "symlink" else None
    return {
        "activationPath": path.as_posix(),
        "errorType": observation.error_type,
        "detail": observation.detail,
        "fileType": file_type,
        "symlinkTarget": target,
    }


def _unreadable(
    source_pair: AtomicSeriesSourcePair,
    fingerprint: str,
    path: Path,
    error_type: str,
    detail: str,
) -> AtomicSeriesActivationObservation:
    return AtomicSeriesActivationObservation(
        source_pair,
        fingerprint,
        path,
        "unreadable",
        error_type=error_type,
        detail=detail,
    )


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
