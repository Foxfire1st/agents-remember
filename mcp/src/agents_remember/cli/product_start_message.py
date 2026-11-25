"""The product's message to a Reviewer and a Curator that start on a leaf.

When the message is made decides what it says, because it is composed over the leaf's code as it
stands at that moment:

* A seat started directly, by role_start, by the dashboard's launcher or opened at once by
  role_start_set, gets it when its start answers running. The message goes through the product's message route
  (:mod:`agents_remember.cli.product_messages`), so a recipient in its first turn gets it held.
* A seat role_start_set holds gets it when the seat opens: the message is part of the opening
  turn, under the product's line, before the messages that waited for the seat. A held seat gets
  nothing at its start.

One execution gets one start message. A repeat of a start is answered from its receipt, which
records the message under ``productStartMessage``, so it sends nothing; a replacement is a new
execution with its own receipt and gets its own. A further review round is a role message, not a
start, and brings none. Nothing here fails a start: what could not be made or sent is reported.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Final

from fastapi import HTTPException

from agents_remember.application.file_briefs import moments
from agents_remember.application.file_briefs.moments import Moment, StartRequest
from agents_remember.cli.product_messages import (
    ProductMessage,
    file_product_message,
    product_message_limit,
    product_sender_line,
    send_product_message,
)
from agents_remember.cli.role_launch_receipts import _read_receipt, _receipt_path, _write_receipt
from agents_remember.cli.role_launch_workspace import _leaf_contract_path
from agents_remember.cli.role_message_delivery import _Refused
from agents_remember.cli.role_start_answer import _started_agent_id
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleDispatchRequest, RoleSelection
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import ResolvedTaskDocument, TaskDocumentTopology
from agents_remember.worktrees.worktree_contract import load_contract

logger = logging.getLogger(__name__)

MOMENT_OF_ROLE: Final[dict[str, Moment]] = {
    "reviewer": "review-start",
    "curator": "curator-start",
}
#: The receipt field that records the one start message of an execution.
RECORD_KEY = "productStartMessage"
# What role_message's answer status means for the message.
_STATE_OF_STATUS: Final[dict[str, str]] = {
    "accepted": "delivered",
    "held": "held",
    "permission-pending": "held",
}


def direct_start_message(
    config: McpRuntimeConfig, selection: RoleSelection, answer: dict[str, Any]
) -> dict[str, Any] | None:
    """Send the start message of a seat whose start answered running; ``None`` when none is due.

    Due once per execution of a Reviewer or Curator on a leaf: not for a held seat (its opening
    carries it), and not where the receipt already records one (a repeat of the start).
    """

    agent_id = _running_agent(selection, answer)
    if agent_id is None:
        return None
    path = _receipt_path(config, selection)
    receipt = _receipt_without_message(path, agent_id)
    if receipt is None:
        return None
    report = _compose_and_send(config, path, receipt, agent_id)
    _record(path, receipt, report)
    return report


def _running_agent(selection: RoleSelection, answer: dict[str, Any]) -> str | None:
    """The started agent of a leaf's Reviewer or Curator whose start answered running, else ``None``."""

    if selection.role not in MOMENT_OF_ROLE or selection.task_document_ref is None:
        return None
    agent_id = answer.get("agentId")
    if answer.get("status") != "running" or not isinstance(agent_id, str) or not agent_id:
        return None
    return agent_id


def _receipt_without_message(path: Path, agent_id: str) -> dict[str, Any] | None:
    """The receipt of ``agent_id``'s unheld start when it records no start message yet, else ``None``."""

    try:
        receipt = _read_receipt(path)
    except HTTPException:
        return None
    if (
        receipt is None
        or _started_agent_id(receipt) != agent_id
        or receipt.get("heldSeat") is True
        or RECORD_KEY in receipt
    ):
        return None
    return receipt


def set_start_messages(
    config: McpRuntimeConfig, answer: dict[str, Any], selections: list[RoleSelection]
) -> None:
    """Give every Reviewer or Curator seat a set opened at once its start message, in its seat."""

    seats = answer.get("seats")
    if not isinstance(seats, dict):
        return
    for selection in selections:
        seat = seats.get(selection.role)
        if not isinstance(seat, dict) or seat.get("held") or not seat.get("opened"):
            continue
        report = direct_start_message(config, selection, seat)
        if report is not None:
            seat["productMessage"] = report


def held_seat_opening(
    config: McpRuntimeConfig, receipt_path: Path, receipt: dict[str, Any]
) -> str | None:
    """The start message a held Reviewer or Curator seat opens with, under the product's line.

    Composed now, when the seat opens, and filed in the leaf's messages folder as every message
    is. ``None`` when the seat is no leaf's Reviewer or Curator, or the message cannot be made; the
    seat opens all the same.
    """

    role = receipt.get("role")
    agent_id = _started_agent_id(receipt)
    if role not in MOMENT_OF_ROLE or agent_id is None or _task_ref(receipt) is None:
        return None
    composed = _compose(config, receipt, MOMENT_OF_ROLE[role])
    if isinstance(composed, str):
        logger.warning("held seat %s opens without its start message: %s", agent_id, composed)
        return None
    leaf = composed.leaf
    filed = file_product_message(
        config, ProductMessage(agent_id, receipt_path, receipt, leaf, composed.text)
    )
    if filed.get("ok") is False:
        logger.warning("the start message of seat %s was not filed: %s", agent_id, filed["detail"])
    return f"{product_sender_line(leaf)}\n{composed.text}"


def start_request(
    config: McpRuntimeConfig, receipt: dict[str, Any], moment: Moment, limit: int
) -> StartRequest | str:
    """The leaf's moment from its enclosure contract, or the sentence that says why there is none."""

    reference = _task_ref(receipt)
    if reference is None:
        return f"the seat serves no leaf; no {moment} message"
    try:
        resolved = TaskDocumentTopology(Path(config.coordination_root)).resolve(reference)
        contract = load_contract(_contract_path(receipt, resolved))
    except (ValueError, OSError) as error:
        return f"the leaf's enclosure contract cannot be read ({error}); no {moment} message"
    if contract.memory_worktree is None:
        return f"the leaf's enclosure has no memory worktree; no {moment} message"
    workspace = receipt.get("workspace")
    scratch = workspace.get("scratchRoot") if isinstance(workspace, dict) else None
    return StartRequest(
        moment=moment,
        leaf=resolved.document.id,
        memory_root=contract.memory_worktree,
        code_root=contract.code_worktree,
        base_commit=contract.code_base_commit,
        coordination_root=Path(config.coordination_root),
        limit=limit,
        scratch_root=Path(scratch) if isinstance(scratch, str) and scratch else None,
    )


def _contract_path(receipt: dict[str, Any], resolved: ResolvedTaskDocument) -> Path:
    """The contract that binds the seat's checkouts: the leaf's enclosure, or a direct leaf's series.

    A leaf with a direct-execution grant has no enclosure: its seats work in the checkouts of the
    master's branch, bound by the master's series contract, which the start recorded in the
    receipt's workspace. Its change is then the series' change since the series' base.
    """

    workspace = receipt.get("workspace")
    if isinstance(workspace, dict) and workspace.get("enclosurePreparation") == "direct":
        recorded = workspace.get("contractPath")
        if isinstance(recorded, str) and recorded:
            return Path(recorded)
    return _leaf_contract_path(resolved)


def dashboard_start_message(
    config: McpRuntimeConfig, request: RoleDispatchRequest
) -> dict[str, Any] | None:
    """The start message of a Reviewer or Curator the dashboard's launcher started.

    The launcher's dispatch answers before the seat's state is read as a tool answer, so the
    seat's state is read from the receipt the start wrote for this request. A held seat, a start
    that did not run, and a repeated request send nothing.
    """

    if request.role not in MOMENT_OF_ROLE or request.task_document_ref is None:
        return None
    try:
        receipt = _read_receipt(_receipt_path(config, request))
    except HTTPException:
        return None
    if receipt is None or receipt.get("requestId") != str(request.request_id):
        return None
    if str(receipt.get("status")) in {"rejected", "starting", "unknown", "held"}:
        return None
    agent_id = _started_agent_id(receipt)
    if not isinstance(agent_id, str) or not agent_id:
        return None
    return direct_start_message(config, request, {"status": "running", "agentId": agent_id})


def _compose(
    config: McpRuntimeConfig, receipt: dict[str, Any], moment: Moment
) -> moments.MomentMessage | moments.MomentNotice | str:
    """The moment's file and message over the leaf as it stands, or why not."""

    try:
        limit = product_message_limit(config, receipt)
    except _Refused as refused:
        return f"the message limit cannot be read ({refused.detail}); no {moment} message"
    request = start_request(config, receipt, moment, limit)
    if isinstance(request, str):
        return request
    return moments.compose_start_message(request)


def _compose_and_send(
    config: McpRuntimeConfig, path: Path, receipt: dict[str, Any], agent_id: str
) -> dict[str, Any]:
    moment = MOMENT_OF_ROLE[str(receipt.get("role"))]
    report: dict[str, Any] = {"recipientAgentId": agent_id, "moment": moment}
    composed = _compose(config, receipt, moment)
    if isinstance(composed, str):
        return {**report, "state": "not-sent", "problem": composed}
    if isinstance(composed, moments.MomentMessage):
        report["file"] = composed.file.as_posix()
    answer = send_product_message(
        config, ProductMessage(agent_id, path, receipt, composed.leaf, composed.text)
    )
    report["state"] = _STATE_OF_STATUS.get(str(answer.get("status")), "refused")
    for source, target in (("heldMessageId", "heldMessageId"), ("messageFile", "messageFile")):
        if isinstance(answer.get(source), str):
            report[target] = answer[source]
    if report["state"] == "refused":
        report["problem"] = f"{answer.get('refusal')}: {answer.get('detail')}"
    return report


def _record(path: Path, receipt: dict[str, Any], report: dict[str, Any]) -> None:
    """Record the execution's start message in its receipt, so a repeat of the start sends none."""

    try:
        current = _read_receipt(path)
        if current is None or current.get("requestId") != receipt.get("requestId"):
            return
        current[RECORD_KEY] = report
        _write_receipt(path, current)
    except (HTTPException, OSError) as error:
        logger.warning("the start message of %s was not recorded: %s", path, error)


def _task_ref(receipt: dict[str, Any]) -> TaskDocumentRef | None:
    selection = receipt.get("selection")
    value = selection.get("taskDocumentRef") if isinstance(selection, dict) else None
    return TaskDocumentRef.model_validate(value) if isinstance(value, dict) else None


__all__ = [
    "MOMENT_OF_ROLE",
    "RECORD_KEY",
    "dashboard_start_message",
    "direct_start_message",
    "held_seat_opening",
    "set_start_messages",
    "start_request",
]
