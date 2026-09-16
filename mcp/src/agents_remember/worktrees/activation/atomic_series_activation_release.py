"""Strict durable release transitions for atomic-series activation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agents_remember.controlplane.durable_store import exclusive_access
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.models.structural.atomic_series_activation import AtomicSeriesActivationRecord
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.activation.atomic_series_activation import (
    ACTIVATION_OWNERSHIP,
    AtomicSeriesActivationError,
    AtomicSeriesActivationObservation,
    activation_path,
    observe_atomic_series_path,
    series_master_ref,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

SELECTION_MISSING_STATUS = "atomic-series-activation-selection-missing"
"""The refusal a release raises when this contract holds no exact series selection.

``worktrees/modules/pause.py`` answers exactly this status itself, because the vacancy it names
is the state a pause produces, so the literal is declared once -- here, where it is raised --
and read from here by the one other module that has to recognise it.
"""


def release_atomic_series_selection(
    contract: WorktreeContract,
    *,
    timestamp: str | None = None,
) -> AtomicSeriesActivationObservation:
    """Release only the exact currently selected contract to durable vacancy."""

    ACTIVATION_OWNERSHIP.check_declared_writer()
    path = activation_path(contract.coordination_root, contract)
    selected_master = series_master_ref(contract)
    selected_at = timestamp or _now_iso()
    with exclusive_access(path, ACTIVATION_OWNERSHIP):
        previous = observe_atomic_series_path(contract, path)
        if previous.state == "unreadable":
            raise AtomicSeriesActivationError(
                "atomic-series-activation-release-unreadable",
                previous.detail
                or "the selected activation snapshot is unreadable and cannot be released",
            )
        record = previous.record
        if record is None:
            raise AtomicSeriesActivationError(
                SELECTION_MISSING_STATUS,
                "explicit sync cancellation requires an existing exact series selection",
            )
        if not _record_selects_contract(record, contract, selected_master):
            raise AtomicSeriesActivationError(
                "atomic-series-activation-selected-contract-mismatch",
                "explicit sync cancellation cannot release another selected atomic master",
            )
        return _release_record(previous, contract, selected_master, selected_at=selected_at)


def release_terminal_atomic_series_selection_if_exact(
    contract: WorktreeContract,
    *,
    timestamp: str | None = None,
) -> AtomicSeriesActivationObservation:
    """Release terminal selection only when strict evidence proves this exact owner."""

    ACTIVATION_OWNERSHIP.check_declared_writer()
    path = activation_path(contract.coordination_root, contract)
    selected_master = series_master_ref(contract)
    selected_at = timestamp or _now_iso()
    with exclusive_access(path, ACTIVATION_OWNERSHIP):
        previous = observe_atomic_series_path(contract, path)
        record = previous.record
        if (
            previous.state == "unreadable"
            or record is None
            or not _record_selects_contract(record, contract, selected_master)
        ):
            return previous
        return _release_record(previous, contract, selected_master, selected_at=selected_at)


def _record_selects_contract(
    record: AtomicSeriesActivationRecord,
    contract: WorktreeContract,
    selected_master: TaskDocumentRef,
) -> bool:
    return bool(
        record.selectedMaster == selected_master
        and Path(record.contractPath).resolve(strict=False)
        == contract.contract_path.resolve(strict=False)
    )


def _release_record(
    previous: AtomicSeriesActivationObservation,
    contract: WorktreeContract,
    selected_master: TaskDocumentRef,
    *,
    selected_at: str,
) -> AtomicSeriesActivationObservation:
    record = previous.record
    if record is None:
        raise RuntimeError("atomic-series release requires a selected record")
    if record.state == "vacant":
        return previous
    released = AtomicSeriesActivationRecord(
        contractFingerprint=previous.contract_fingerprint,
        selectedMaster=selected_master,
        contractPath=contract.contract_path.resolve().as_posix(),
        state="vacant",
        revision=record.revision + 1,
        selectedAt=selected_at,
    )
    atomic_write_text(previous.activation_path, released.model_dump_json(indent=2) + "\n")
    return AtomicSeriesActivationObservation(
        previous.contract_path,
        previous.contract_fingerprint,
        previous.activation_path,
        "vacant",
        released,
    )


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
