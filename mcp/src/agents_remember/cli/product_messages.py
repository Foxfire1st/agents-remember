"""Messages the product itself sends, through role_message's own route.

The product is no agent: it holds no binding, no receipt and no start authority. Its sender is a
fixed identity built here: the word ``agents-remember`` as its role and agent, the recipient's own
task references, and the leaf it speaks of as its subject. Its line reads
"From Agents Remember (product) · <leaf>". role_message takes its sender only from a launch
binding, whose role is an AR role, or from an explainer's binding; neither can name this word, so
no agent sends as the product.

A product message runs role_message's own steps: the limit of the recipient's task, the message
file in the recipient task's messages folder whose head names the product, and deliver-or-hold. A
busy recipient gets it held and delivered later as every message is, and a recipient whose start
has not finished gets it held as start-unfinished.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.agent_binding import AgentBinding
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.cli.paseo_role_caller import (
    PRODUCT_SENDER,
    PRODUCT_SENDER_NAME,
    role_message_limit,
    sender_line,
)
from agents_remember.cli.paseo_role_tools import (
    _deliver_or_hold,
    _file_message,
    _held_refusal,
    _hold_for_seat,
    _message_seats,
    _MessageSend,
)
from agents_remember.cli.role_held_store import HeldSeatClosed
from agents_remember.cli.role_message_delivery import (
    _keep_refused_file,
    _MessageParties,
    _Recipient,
    _refuse_over_limit,
    _Refused,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_agents import RoleMessageCall
from agents_remember.models.task_document_ref import TaskDocumentRef

# The receipt statuses of a start that has not answered yet, as role_message holds them.
_UNFINISHED_START_STATUSES = frozenset({"starting", "unknown"})


@dataclass(frozen=True, slots=True)
class ProductMessage:
    """One message of the product to one started agent, about one leaf.

    ``receipt`` is the recipient's execution receipt as read at ``receipt_path``: it names the
    recipient's task references, its working folder (where a held message waits) and its status.
    """

    recipient_agent_id: str
    receipt_path: Path
    receipt: dict[str, Any]
    leaf: str
    text: str


def product_sender(receipt: dict[str, Any], leaf: str) -> tuple[AgentBinding, StartingAgent]:
    """The product as a sender: the recipient's task references, the leaf as its subject."""

    sprint, master, task = _selection_refs(receipt)
    binding = AgentBinding(
        agent_id=PRODUCT_SENDER,
        role=PRODUCT_SENDER,
        request_id=PRODUCT_SENDER,
        report_path="",
        sprint_ref=sprint,
        master_ref=master,
        task_ref=task,
    )
    sender = StartingAgent(PRODUCT_SENDER, PRODUCT_SENDER, leaf, sprint, master, task)
    return binding, sender


def product_sender_line(leaf: str) -> str:
    """The line that opens a product message about ``leaf``."""

    return sender_line(StartingAgent(PRODUCT_SENDER, PRODUCT_SENDER, leaf))


def product_message_limit(config: McpRuntimeConfig, receipt: dict[str, Any]) -> int:
    """The route's limit for a message to the recipient's task, read per call as role_message reads it."""

    binding, _sender = product_sender(receipt, "")
    return role_message_limit(config, binding)


def file_product_message(config: McpRuntimeConfig, message: ProductMessage) -> dict[str, Any]:
    """File one product message without delivering it: the record of a message a seat opens with.

    Answers the filed record (path, SHA-256, message id, folder), or the refusal's payload.
    """

    binding, sender = product_sender(message.receipt, message.leaf)
    call = RoleMessageCall(text=message.text, agent_id=message.recipient_agent_id)
    parties = _MessageParties(
        binding, sender, _Recipient(message.recipient_agent_id, message.receipt), None
    )
    located = _located(message)
    try:
        limit = role_message_limit(config, binding)
        _refuse_over_limit(call, limit)
        return _file_message(config, call, parties, limit, _message_seats(None, located, parties))
    except (_Refused, HeldSeatClosed) as refusal:
        refused = _held_refusal(refusal) if isinstance(refusal, HeldSeatClosed) else refusal
        return refused.payload()


def send_product_message(config: McpRuntimeConfig, message: ProductMessage) -> dict[str, Any]:
    """Send one product message through role_message's steps; answers as role_message answers.

    The answer's ``status`` is ``accepted`` (delivered), ``held`` or ``permission-pending``
    (waiting in the recipient's store), or ``refused`` with the one reason.
    """

    binding, sender = product_sender(message.receipt, message.leaf)
    recipient = _Recipient(message.recipient_agent_id, message.receipt)
    call = RoleMessageCall(text=message.text, agent_id=message.recipient_agent_id)
    parties = _MessageParties(binding, sender, recipient, None)
    located = _located(message)
    filed: dict[str, Any] | None = None
    try:
        limit = role_message_limit(config, binding)
        _refuse_over_limit(call, limit)
        filed = _file_message(config, call, parties, limit, _message_seats(None, located, parties))
        send = _MessageSend(config, call, sender, recipient, located, None, None, filed)
        if located is not None and message.receipt.get("status") in _UNFINISHED_START_STATUSES:
            return _hold_for_seat(send, reason="start-unfinished")
        return _deliver_or_hold(send)
    except (_Refused, HeldSeatClosed) as refusal:
        refused = _held_refusal(refusal) if isinstance(refusal, HeldSeatClosed) else refusal
        if filed is not None:
            _keep_refused_file(Path(filed["path"]), refused)
        return refused.payload()


def _located(message: ProductMessage) -> tuple[Path, dict[str, Any], str] | None:
    """The recipient's receipt and working folder, where a held message waits."""

    workspace = message.receipt.get("workspace")
    folder = workspace.get("path") if isinstance(workspace, dict) else None
    if isinstance(folder, str) and folder:
        return message.receipt_path, message.receipt, folder
    return None


def _selection_refs(
    receipt: dict[str, Any],
) -> tuple[TaskDocumentRef | None, TaskDocumentRef | None, TaskDocumentRef | None]:
    selection = receipt.get("selection")
    selected = selection if isinstance(selection, dict) else {}
    return (
        _ref(selected.get("sprintDocumentRef")),
        _ref(selected.get("masterDocumentRef")),
        _ref(selected.get("taskDocumentRef")),
    )


def _ref(value: object) -> TaskDocumentRef | None:
    return TaskDocumentRef.model_validate(value) if isinstance(value, dict) else None


__all__ = [
    "PRODUCT_SENDER",
    "PRODUCT_SENDER_NAME",
    "ProductMessage",
    "file_product_message",
    "product_message_limit",
    "product_sender",
    "product_sender_line",
    "send_product_message",
]
