"""The facts of a writer's stamp read from a seat's launch receipt.

A launched seat's receipt (``ar-role-execution/v1``) lies in the receipt holder of the folder that
holds its task document, ``<folder>/notes/reports/paseo-native-executions/``; a superseded receipt
moves to ``history/<requestId>.json`` there. A taskless seat's receipt lies in its role's session
folder of the coordination root's holder instead. The receipt names the harness (``agent.id``), the
model and the effort; the host's observed value wins over the requested one where the receipt holds
both. Nothing here reads a tool's input: the caller is named by its launch binding, the receipt by
the binding's request id or by the leaf's registered role.

A value the receipt does not hold reads ``unknown`` with the reason, and a receipt that cannot be
found makes every receipt value unknown; a stamp is never refused for that.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from agents_remember.controlplane.held_store_discovery import (
    receipt_holder,
    taskless_session_directory,
)
from agents_remember.models.knowledge_files.writer_stamp import UNKNOWN, StampSource, WriterStamp
from agents_remember.models.role_identity import role_spellings
from agents_remember.models.task_document_ref import TaskDocumentRef

RECEIPT_SCHEMA: Final = "ar-role-execution/v1"
HISTORY_DIRECTORY: Final = "history"
# The receipt values of a stamp, with the receipt's flat key and the choices key that may hold the
# host's observed value.
_RECEIPT_VALUES: Final = (
    ("harness", "id", "agent"),
    ("model", "model", "model"),
    ("effort", "effort", "effort"),
)


@dataclass(frozen=True)
class StampOwner:
    """The task and the owner (leaf, wave or crossing) a stamped write belongs to."""

    task: str
    kind: str | None = None
    owner_id: str | None = None

    def fields(self) -> dict[str, str]:
        return {self.kind: self.owner_id} if self.kind and self.owner_id else {}


@dataclass(frozen=True)
class StampRequest:
    """What a stamp is made of besides the receipt: who called, for what, and when."""

    by: StampSource
    role: str | None
    seat: str | None
    owner: StampOwner
    at: str
    operation: str | None = None
    unresolved: str | None = None
    """Why the caller could not be resolved, when it could not."""


def task_receipts(task_folder: Path) -> Iterator[dict[str, Any]]:
    """Every readable receipt in a task folder's holder, current ones before superseded ones."""

    holder = receipt_holder(task_folder)
    yield from _receipts_in(holder)
    yield from _receipts_in(holder / HISTORY_DIRECTORY)


def current_task_receipts(task_folder: Path) -> Iterator[dict[str, Any]]:
    """The readable receipts of the seats a task folder registers now; superseded ones excluded."""

    return _receipts_in(receipt_holder(task_folder))


def _receipts_in(directory: Path) -> Iterator[dict[str, Any]]:
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        receipt = _read_receipt(path)
        if receipt is not None:
            yield receipt


def receipt_of_request(task_folder: Path, request_id: str) -> dict[str, Any] | None:
    """The receipt of the seat started by ``request_id``, current or superseded."""

    return next(
        (one for one in task_receipts(task_folder) if one.get("requestId") == request_id), None
    )


def taskless_receipt(coordination_root: Path, role: str, request_id: str) -> dict[str, Any] | None:
    """The receipt of the taskless seat (Architect, Investigator, Bootstrap) ``request_id`` started.

    Such a seat names no task document, so its launch keeps one receipt per request in the role's
    session folder of the coordination root's holder, ``<holder>/<role>/sessions/<requestId>.json``
    (:func:`taskless_session_directory`, shared with the launch that writes it); an Investigator's may lie under its older name.
    """

    for spelling in role_spellings(role):
        sessions = taskless_session_directory(coordination_root, spelling)
        receipt = _read_receipt(sessions / f"{request_id}.json")
        if receipt is not None and receipt.get("requestId") == request_id:
            return receipt
    return None


def leaf_role_receipt(task_folder: Path, leaf: TaskDocumentRef, role: str) -> dict[str, Any] | None:
    """The current receipt of the seat in ``role`` registered on the leaf ``leaf``."""

    wanted = leaf.model_dump(mode="json")
    for receipt in task_receipts(task_folder):
        selection = receipt.get("selection")
        if (
            receipt.get("role") == role
            and isinstance(selection, Mapping)
            and selection.get("taskDocumentRef") == wanted
        ):
            return receipt
    return None


def stamp(request: StampRequest, receipt: Mapping[str, Any] | None) -> WriterStamp:
    """The stamp of ``request``, its harness, model and effort read from ``receipt``."""

    reasons: dict[str, str] = {}
    values: dict[str, str] = {}
    caller_reason = request.unresolved or "the caller's launch binding does not name it"
    for name, value in (("role", request.role), ("seat", request.seat)):
        values[name] = value or UNKNOWN
        if not value:
            reasons[name] = caller_reason
    for name, flat, choice in _RECEIPT_VALUES:
        value = _receipt_value(receipt, flat, choice)
        values[name] = value or UNKNOWN
        if not value:
            reasons[name] = (
                "no launch receipt of the caller was found"
                if receipt is None
                else f"the launch receipt does not hold the {name}"
            )
    values["task"] = request.owner.task or UNKNOWN
    if not request.owner.task:
        reasons["task"] = caller_reason
    document: dict[str, Any] = {
        "by": request.by,
        **values,
        **request.owner.fields(),
        "at": request.at,
    }
    if request.operation is not None:
        document["operation"] = request.operation
    if reasons:
        document["unknown"] = reasons
    return WriterStamp.model_validate(document)


def _receipt_value(receipt: Mapping[str, Any] | None, flat: str, choice: str) -> str | None:
    if receipt is None:
        return None
    choices = receipt.get("choices")
    chosen = choices.get(choice) if isinstance(choices, Mapping) else None
    if isinstance(chosen, Mapping):
        observed = chosen.get("observedValue")
        if isinstance(observed, str) and observed.strip():
            return observed
    agent = receipt.get("agent")
    value = agent.get(flat) if isinstance(agent, Mapping) else None
    return value if isinstance(value, str) and value.strip() else None


def _read_receipt(path: Path) -> dict[str, Any] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict) or document.get("schema") != RECEIPT_SCHEMA:
        return None
    return document
