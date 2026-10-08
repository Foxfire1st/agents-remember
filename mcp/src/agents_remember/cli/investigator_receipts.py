"""Task-owned Investigator requests and reads of the role's earlier recorded paths.

Selected requests are retained and reclaimed by their task's existing lifecycle.
This owner neither migrates records nor adds retention to the taskless store.
"""

from __future__ import annotations

import heapq
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from agents_remember.application.role_launch_context import (
    resolve_role_launch_context,
    selection_binding,
)
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_identity import canonical_role, role_spellings
from agents_remember.models.role_launcher import RoleDispatchRequest, RoleSelection

MAX_OPEN_INVESTIGATORS = 8
LIST_LIMIT = 50


@dataclass(frozen=True)
class InvestigatorListing:
    records: list[tuple[Path, dict[str, Any]]]
    omitted_count: int


def matches_selection(receipt: dict[str, Any], selection: RoleSelection) -> bool:
    stored = receipt.get("selection")
    return (
        isinstance(stored, dict)
        and {**stored, "role": canonical_role(str(stored.get("role", "")))}
        == selection_binding(selection)
        and canonical_role(str(receipt.get("role", selection.role))) == selection.role
    )


def _directories(config: McpRuntimeConfig, selection: RoleSelection) -> tuple[Path, ...]:
    from agents_remember.cli.role_launch_receipts import EXECUTIONS_DIRECTORY  # noqa: PLC0415

    task = resolve_role_launch_context(config, selection).effective_task
    root = (
        (task.path.parent if task else config.coordination_root)
        / "notes"
        / "reports"
        / EXECUTIONS_DIRECTORY
    )
    return tuple(root / role / "sessions" for role in role_spellings("investigator"))


def _earlier_singletons(config: McpRuntimeConfig, selection: RoleSelection) -> tuple[Path, ...]:
    from agents_remember.cli.role_launch_receipts import (  # noqa: PLC0415
        EXECUTIONS_DIRECTORY,
        digest,
    )

    if selection.sprint_document_ref is not None:
        return ()
    root = config.coordination_root / "notes" / "reports" / EXECUTIONS_DIRECTORY
    return tuple(
        root / f"{role}-{digest({**selection_binding(selection), 'role': role})[:24]}.json"
        for role in role_spellings("investigator")
    )


def investigator_receipt_path(
    config: McpRuntimeConfig, selection: RoleSelection, request_id: uuid.UUID
) -> Path:
    from agents_remember.cli.role_launch_receipts import _read_receipt  # noqa: PLC0415

    candidates = [directory / f"{request_id}.json" for directory in _directories(config, selection)]
    present = [path for path in candidates if path.exists() or path.is_symlink()]
    for path in _earlier_singletons(config, selection):
        receipt = _read_receipt(path)
        if receipt is not None and receipt.get("requestId") == str(request_id):
            present.append(path)
    if len(present) > 1:
        raise HTTPException(
            409,
            "More than one Investigator receipt has this requestId; reconcile the recorded paths without choosing one.",
        )
    return present[0] if present else candidates[0]


def _request_records(config: McpRuntimeConfig, selection: RoleSelection):
    from agents_remember.cli.role_launch_receipts import _read_receipt  # noqa: PLC0415

    for directory in _directories(config, selection):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise HTTPException(409, "The Investigator receipt store is not a safe directory.")
        for path in directory.glob("*.json"):
            receipt = _read_receipt(path)
            if receipt is None:
                continue
            _validate_address(path, receipt)
            if matches_selection(receipt, selection):
                yield path, receipt
    for path in _earlier_singletons(config, selection):
        receipt = _read_receipt(path)
        if receipt is not None and matches_selection(receipt, selection):
            _validate_address(path, receipt)
            yield path, receipt


def _validate_address(path: Path, receipt: dict[str, Any]) -> None:
    request_id = str(receipt.get("requestId"))
    try:
        valid = str(uuid.UUID(request_id)) == request_id
    except ValueError:
        valid = False
    if not valid or (path.parent.name == "sessions" and path.name != f"{request_id}.json"):
        raise HTTPException(409, "An Investigator receipt does not match its requestId address.")


def investigator_listing(config: McpRuntimeConfig, selection: RoleSelection) -> InvestigatorListing:
    total = 0

    def counted():
        nonlocal total
        for row in _request_records(config, selection):
            total += 1
            yield row

    def newest(row: tuple[Path, dict[str, Any]]) -> tuple[str, str]:
        return (str(row[1].get("createdAt", "")), str(row[1]["requestId"]))

    if selection.sprint_document_ref is None:
        # Taskless listing retains its existing ownership; its bound is a separate change.
        return InvestigatorListing(sorted(counted(), key=newest, reverse=True), 0)
    rows = heapq.nlargest(LIST_LIMIT, counted(), key=newest)
    return InvestigatorListing(rows, max(0, total - len(rows)))


def investigator_receipts(
    config: McpRuntimeConfig, selection: RoleSelection
) -> list[tuple[Path, dict[str, Any]]]:
    return investigator_listing(config, selection).records


def open_investigator_receipts(
    config: McpRuntimeConfig, selection: RoleSelection
) -> list[dict[str, Any]]:
    from agents_remember.cli.role_launch_liveness import execution_is_closed  # noqa: PLC0415

    return [
        receipt
        for _, receipt in _request_records(config, selection)
        if not execution_is_closed(receipt.get("status"))
    ]


@contextmanager
def investigator_start_capacity(
    config: McpRuntimeConfig, request: RoleDispatchRequest, *, new_turn: bool = False
):
    from agents_remember.cli.role_launch_liveness import (  # noqa: PLC0415
        _refresh_execution,
        execution_is_closed,
    )
    from agents_remember.cli.role_launch_receipts import _read_receipt  # noqa: PLC0415

    if request.role != "investigator" or request.sprint_document_ref is None:
        yield
        return
    directory = _directories(config, request)[0]
    with exclusive_file_lock(directory / ".capacity", "Investigator selection capacity"):
        existing = _read_receipt(investigator_receipt_path(config, request, request.request_id))
        current = (
            _refresh_execution(
                config, investigator_receipt_path(config, request, request.request_id), existing
            )
            if new_turn and existing
            else None
        )
        if existing is None or (current is not None and execution_is_closed(current.get("status"))):
            open_ids = []
            for path, receipt in _request_records(config, request):
                if execution_is_closed(receipt.get("status")):
                    continue
                current = _refresh_execution(config, path, receipt)
                if not execution_is_closed(current.get("status")):
                    open_ids.append(str(receipt["requestId"]))
                if len(open_ids) == MAX_OPEN_INVESTIGATORS:
                    raise HTTPException(
                        409,
                        "Eight Investigator executions are not closed for this selection: "
                        + ", ".join(open_ids)
                        + ". Close or archive one before starting another.",
                    )
        try:
            yield
        finally:
            if new_turn and existing:
                # Publish a delivered new turn before releasing the same lock Start uses.
                _refresh_execution(
                    config, investigator_receipt_path(config, request, request.request_id), existing
                )


@contextmanager
def investigator_message_capacity(config: McpRuntimeConfig, receipt: dict[str, Any]):
    if canonical_role(str(receipt.get("role"))) != "investigator":
        yield
        return
    request = RoleDispatchRequest.model_validate(
        {**receipt["selection"], "requestId": receipt["requestId"]}
    )
    with investigator_start_capacity(config, request, new_turn=True):
        yield
