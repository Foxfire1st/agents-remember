"""Contract-scoped activation authority for durable atomic master work.

Series contracts prove that work exists.  This replace-in-place control-plane
snapshot separately records which of them is currently reconciling or active.
The record is keyed by the canonical series contract, so two atomic masters
that share one protected source pair never share this state: each tracks only
its own ``reconciling -> active`` transition.  Real wave dependencies remain
the sprint execution graph's job, and the queue only observes the snapshot;
task-document mutation never reads it.
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
)
from agents_remember.models.task_document_ref import TaskDocumentRef
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
        "atomic start/attach/dispatch records one reconciling -> active transition per "
        "canonical series contract; task truth and the disposable closeout projection "
        "are read-only consumers"
    ),
)

_PUBLIC_ACTIVATION_DETAIL_MAX = 8192
_PUBLIC_ACTIVATION_DETAIL_TRUNCATION = "\u2026 [detail truncated]"


def bounded_activation_detail(detail: str | None) -> str | None:
    """Keep malformed-authority diagnostics inside the public response contract."""

    if detail is None or len(detail) <= _PUBLIC_ACTIVATION_DETAIL_MAX:
        return detail
    return detail[: _PUBLIC_ACTIVATION_DETAIL_MAX - len(_PUBLIC_ACTIVATION_DETAIL_TRUNCATION)] + (
        _PUBLIC_ACTIVATION_DETAIL_TRUNCATION
    )


class AtomicSeriesActivationError(RuntimeError):
    """The addressed contract's activation authority is absent, malformed, or inconsistent."""

    def __init__(
        self,
        status: str,
        detail: str,
        *,
        observation: AtomicSeriesActivationObservation | None = None,
        expected: dict[str, object] | None = None,
        observed: dict[str, object] | None = None,
    ) -> None:
        self.status = status
        self.detail = detail
        self.observation = observation
        self.expected = expected
        self.observed = observed
        super().__init__(detail)


@dataclass(frozen=True)
class AtomicSeriesActivationObservation:
    """Strict read of one contract's activation record without creating store artifacts."""

    contract_path: Path
    contract_fingerprint: str
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
            "contractFingerprint": self.contract_fingerprint,
            "state": self.state,
        }
        if self.record is not None:
            fact["record"] = self.record.model_dump(mode="json")
        if self.error_type is not None:
            fact["errorType"] = self.error_type
        if self.detail is not None:
            fact["detail"] = bounded_activation_detail(self.detail)
        return fact


def contract_fingerprint(contract: WorktreeContract) -> str:
    """The stable per-contract identity that names exactly one activation record."""

    payload = contract.contract_path.resolve(strict=False).as_posix()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def activation_path(
    coordination_root: Path,
    contract: WorktreeContract,
) -> Path:
    digest = contract_fingerprint(contract)
    return coordination_root / "controlplane" / "atomic-series-activation" / f"{digest}.json"


def observe_atomic_series(
    contract: WorktreeContract,
) -> AtomicSeriesActivationObservation:
    """Read this contract's own activation snapshot strictly; missing means vacant."""

    _require_canonical_series_contract(contract)
    path = activation_path(contract.coordination_root, contract)
    return observe_atomic_series_path(contract, path)


def publish_atomic_series_selection(
    contract: WorktreeContract,
    state: AtomicSeriesSelectionState,
    *,
    timestamp: str | None = None,
) -> AtomicSeriesActivationObservation:
    """Replace this contract's own selection, recovering corrupt bytes only through selection.

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
    fingerprint = contract_fingerprint(contract)
    path = activation_path(contract.coordination_root, contract)
    selected_at = timestamp or _now_iso()
    selected_master = series_master_ref(contract)
    with exclusive_access(path, ACTIVATION_OWNERSHIP):
        previous = observe_atomic_series_path(contract, path)
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
            contractFingerprint=fingerprint,
            selectedMaster=selected_master,
            contractPath=contract.contract_path.resolve().as_posix(),
            state=state,
            revision=revision,
            selectedAt=selected_at,
        )
        atomic_write_text(path, record.model_dump_json(indent=2) + "\n")
        return AtomicSeriesActivationObservation(
            contract.contract_path,
            fingerprint,
            path,
            state,
            record,
        )


def require_selected_atomic_series(
    contract: WorktreeContract,
    *,
    required_state: AtomicSeriesSelectionState = "reconciling",
) -> AtomicSeriesActivationObservation:
    """Prove continuation/cancellation addresses this contract's own selected state."""

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
            observation=observation,
            expected={
                "master": expected_master.model_dump(mode="json"),
                "contractPath": contract.contract_path.as_posix(),
                "state": required_state,
            },
            observed=observation.source_fact(),
        )
    return observation


def require_atomic_series_cancellation_owner(
    contract: WorktreeContract,
) -> AtomicSeriesActivationObservation:
    """Prove cancel/replay addresses this contract's selected or last-released state."""

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
            observation=observation,
            expected={
                "master": expected_master.model_dump(mode="json"),
                "contractPath": contract.contract_path.as_posix(),
                "state": ["reconciling", "vacant"],
            },
            observed=observation.source_fact(),
        )
    return observation


def activation_waiting_reason(
    observation: AtomicSeriesActivationObservation,
) -> str | None:
    """Project this contract's own in-flight reconciliation as waiting.

    Selection is per contract, so another master's state is never this
    contract's reason to wait; genuine wave dependencies stay with the sprint
    execution graph's own ``predecessor-incomplete:`` waiting reasons.
    """

    if observation.state == "reconciling":
        return "atomic-series-reconciling"
    return None


def observe_atomic_series_path(
    contract: WorktreeContract,
    path: Path,
) -> AtomicSeriesActivationObservation:
    fingerprint = contract_fingerprint(contract)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return AtomicSeriesActivationObservation(
            contract.contract_path,
            fingerprint,
            path,
            "vacant",
        )
    except OSError as exc:
        return _unreadable(contract, fingerprint, path, type(exc).__name__, str(exc))
    if not stat.S_ISREG(mode):
        return _unreadable(
            contract,
            fingerprint,
            path,
            "atomic-series-activation-nonregular",
            "atomic-series activation authority is not a regular file",
        )
    try:
        record = AtomicSeriesActivationRecord.model_validate_json(_read_regular_entry(path))
        _require_record_identity(record, contract, fingerprint, path)
        return _observation_from_record(
            contract.coordination_root,
            contract,
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
        return _unreadable(contract, fingerprint, path, str(status), str(detail))


def _observation_from_record(
    coordination_root: Path,
    contract: WorktreeContract,
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
        contract.contract_path,
        fingerprint,
        path,
        state,
        record,
    )


def _require_record_identity(
    record: AtomicSeriesActivationRecord,
    contract: WorktreeContract,
    fingerprint: str,
    path: Path,
) -> None:
    if record.contractFingerprint != fingerprint or Path(record.contractPath).resolve(
        strict=False
    ) != contract.contract_path.resolve(strict=False):
        raise AtomicSeriesActivationError(
            "atomic-series-activation-contract-mismatch",
            f"activation snapshot does not match its contract path: {path}",
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


def atomic_series_status_projection(contract: WorktreeContract) -> dict[str, object]:
    """Read the activation fact for status without creating or repairing state."""

    try:
        return observe_atomic_series(contract).source_fact()
    except AtomicSeriesActivationError as error:
        return {
            "state": "unreadable",
            "errorType": error.status,
            "detail": bounded_activation_detail(error.detail),
        }


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
    archive_root = path.parent / "archive" / observation.contract_fingerprint
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
        contractFingerprint=observation.contract_fingerprint,
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
    contract: WorktreeContract,
    fingerprint: str,
    path: Path,
    error_type: str,
    detail: str,
) -> AtomicSeriesActivationObservation:
    return AtomicSeriesActivationObservation(
        contract.contract_path,
        fingerprint,
        path,
        "unreadable",
        error_type=error_type,
        detail=detail,
    )


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
