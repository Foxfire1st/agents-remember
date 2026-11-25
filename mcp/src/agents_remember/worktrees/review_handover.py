"""Who handed a leaf's memory change to its review: the leaf's registered Curator or Consolidator.

The product registers every seat it starts for a leaf in the launch receipts of the leaf's task
folder (:mod:`agents_remember.tasks.writer_stamps`). A seat's current receipt names the leaf in its
selection (``taskDocumentRef``); when a seat is replaced, its receipt moves to ``history/``; when it
is retired, its receipt reads ``status: stopped``.

The agent that handed a passed memory change to its review is the one Curator or Consolidator
registered as active on the leaf when the passed round is recorded: a current receipt whose task
document reference is the leaf's (repository and path, compared as the product reads a reference),
that carries one of those roles and whose status is neither stopped nor rejected. Receipts of one
seat (one request id) count once. The stamp is taken at that
moment and stored on the round, so a seat replaced or retired afterwards does not change it. Its
time is the time the round is recorded; the product knows no time of a hand-over. Its seat, harness,
model and effort come from the receipt.

No message decides it. Where no such seat is registered, or more than one is, or the registered
seat's receipt holds a value no stamp can hold, every agent value reads ``unknown`` with the reason;
recording the round never fails for it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from agents_remember.models.knowledge_files.writer_stamp import (
    STAMP_VALUE_FIELDS,
    UNKNOWN,
    WriterStamp,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.writer_stamps import (
    StampOwner,
    StampRequest,
    current_task_receipts,
    stamp,
)

HANDOVER_ROLES: Final = ("curator", "consolidator")
INACTIVE_STATUSES: Final = frozenset({"stopped", "rejected"})
NO_REGISTERED_HANDOVER: Final = (
    "no curator or consolidator was registered as active on the leaf when the passed memory round "
    "was recorded"
)
NO_HANDOVER_RECEIPT_VALUE: Final = "the launch receipt of the registered seat does not hold it"


def several_registered(seats: list[str]) -> str:
    """The reason for an unknown stamp when more than one seat is registered as active."""

    return (
        f"{len(seats)} curators or consolidators were registered as active on the leaf when the "
        f"passed memory round was recorded ({', '.join(seats)}), so none of them is named"
    )


def registered_handover_stamp(
    task_folder: Path, leaf: TaskDocumentRef, owner: StampOwner, at: str
) -> WriterStamp:
    """The stamp of the Curator or Consolidator registered as active on the leaf ``leaf``, whose
    receipts are in ``task_folder``, at ``at``, the time the passed round is recorded."""

    active = _active_handover_seats(task_folder, leaf)
    if not active:
        return unknown_stamp(owner, NO_REGISTERED_HANDOVER, at)
    if len(active) > 1:
        seats = [f"{one.get('role')} {one.get('requestId')}" for one in active]
        return unknown_stamp(owner, several_registered(seats), at)
    receipt = active[0]
    request_id = receipt.get("requestId")
    request = StampRequest(
        by="agent",
        role=str(receipt.get("role")),
        seat=request_id if isinstance(request_id, str) and request_id.strip() else None,
        owner=owner,
        at=at,
        unresolved=NO_HANDOVER_RECEIPT_VALUE,
    )
    try:
        return stamp(request, receipt)
    except ValidationError as error:
        first = error.errors()[0]
        place = ".".join(str(part) for part in first["loc"]) or "the stamp"
        reason = (
            f"the launch receipt of the registered {request.role} {request_id} holds a value no "
            f"stamp can hold ({place}: {first['msg']})"
        )
        return unknown_stamp(owner, reason, at)


def unknown_stamp(owner: StampOwner, reason: str, at: str = UNKNOWN) -> WriterStamp:
    """A stamp whose agent values read ``unknown`` with ``reason``; the task stays the owner's."""

    values = {name: UNKNOWN for name in STAMP_VALUE_FIELDS if name != "task"}
    task = owner.task or UNKNOWN
    reasons = {**dict.fromkeys(values, reason), **({} if owner.task else {"task": reason})}
    return WriterStamp.model_validate(
        {"by": "agent", **values, "task": task, **owner.fields(), "at": at, "unknown": reasons}
    )


def _active_handover_seats(task_folder: Path, leaf: TaskDocumentRef) -> list[Mapping[str, Any]]:
    """The distinct seats registered as active on ``leaf`` in a hand-over role."""

    seats: dict[str, Mapping[str, Any]] = {}
    for receipt in current_task_receipts(task_folder):
        status = receipt.get("status")
        active = status is None or (isinstance(status, str) and status not in INACTIVE_STATUSES)
        if receipt.get("role") in HANDOVER_ROLES and active and _names_leaf(receipt, leaf):
            seat = receipt.get("requestId")
            key = (
                seat if isinstance(seat, str) else json.dumps(receipt, sort_keys=True, default=str)
            )
            seats.setdefault(key, receipt)
    return list(seats.values())


def _names_leaf(receipt: Mapping[str, Any], leaf: TaskDocumentRef) -> bool:
    selection = receipt.get("selection")
    ref = selection.get("taskDocumentRef") if isinstance(selection, Mapping) else None
    try:
        return TaskDocumentRef.model_validate(ref) == leaf
    except ValidationError:
        return False
