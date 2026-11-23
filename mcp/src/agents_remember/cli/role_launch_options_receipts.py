"""Read launcher receipts without holding the dispatch lock during host access."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from agents_remember.cli.investigator_receipts import investigator_listing
from agents_remember.cli.role_launch_liveness import apply_execution_reading, read_execution_agent
from agents_remember.cli.role_launch_receipts import (
    _move_taskless_legacy_receipts,
    _plan_taskless_legacy_receipt_moves,
    _public_execution,
    _read_receipt,
    _receipt_address_matches,
    _receipt_path,
    _taskless_execution_receipts,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_identity import TASKLESS_ROLES
from agents_remember.models.role_launcher import (
    RoleLauncherOptionsRequest,
    RoleResultRequest,
    RoleSelection,
)

type ReceiptLock = Callable[[], AbstractContextManager[None]]


def launcher_receipts(
    config: McpRuntimeConfig, request: RoleLauncherOptionsRequest, lock: ReceiptLock
) -> dict[str, Any]:
    if request.role in TASKLESS_ROLES:
        _migrate_legacy_receipts(config, request, lock)
        return _launcher_listing(config, request)
    path = _receipt_path(config, request)
    receipt = _read_receipt(path)
    return {"execution": _read_current_execution(config, path, receipt, lock) if receipt else None}


def _launcher_listing(
    config: McpRuntimeConfig, request: RoleLauncherOptionsRequest
) -> dict[str, Any]:
    if request.role == "investigator":
        listing = investigator_listing(config, request)
        return {
            "executions": [_public_execution(receipt) for _, receipt in listing.records],
            "omittedCount": listing.omitted_count,
        }
    return {
        "executions": [
            _public_execution(receipt)
            for _, receipt in _taskless_execution_receipts(config, request)
        ]
    }


def result_execution(
    config: McpRuntimeConfig, request: RoleResultRequest, lock: ReceiptLock
) -> dict[str, Any]:
    path = _result_path(config, request, lock)
    receipt = _require_result_receipt(_read_receipt(path), request)
    result = _read_current_execution(config, path, receipt, lock, request)
    assert result is not None
    return result


def _result_path(config: McpRuntimeConfig, request: RoleResultRequest, lock: ReceiptLock) -> Path:
    if request.role not in TASKLESS_ROLES:
        return _receipt_path(config, request)
    if request.request_id is None:
        raise HTTPException(
            status_code=400, detail="Taskless role results require the exact saved requestId."
        )
    _migrate_legacy_receipts(config, request, lock)
    return _receipt_path(config, request, request.request_id)


def _migrate_legacy_receipts(
    config: McpRuntimeConfig, request: RoleSelection, lock: ReceiptLock
) -> None:
    moves = _plan_taskless_legacy_receipt_moves(config, request)
    if moves:
        with lock():
            # Start or another migration may have moved a source during discovery.
            current = [move for move in moves if _read_receipt(move.source) == move.receipt]
            _move_taskless_legacy_receipts(current)


def _require_result_receipt(
    receipt: dict[str, Any] | None, request: RoleResultRequest
) -> dict[str, Any]:
    if receipt is None:
        raise HTTPException(
            status_code=404, detail="No role execution is recorded for this AR selection."
        )
    if not _receipt_address_matches(receipt, request):
        raise HTTPException(
            status_code=409, detail="The recorded role execution belongs to another AR selection."
        )
    return receipt


def _read_current_execution(
    config: McpRuntimeConfig,
    path: Path,
    receipt: dict[str, Any],
    lock: ReceiptLock,
    request: RoleResultRequest | None = None,
) -> dict[str, Any] | None:
    reading = read_execution_agent(config, receipt)
    with lock():
        current = _read_receipt(path)
        if request is not None:
            current = _require_result_receipt(current, request)
        if current is None:
            return None
        # Start or another read may have replaced or advanced the saved execution meanwhile.
        return apply_execution_reading(path, current, reading if current == receipt else None)
