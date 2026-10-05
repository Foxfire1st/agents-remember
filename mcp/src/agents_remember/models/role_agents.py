"""Wire models of the two tools through which role agents start and message role agents.

Both tools answer in one envelope. ``status`` names the result; a call the tool did not carry
out has ``ok: false``, ``status: "refused"``, the one ``refusal`` that applies, the reason in
``detail`` and what the caller can do in ``nextAction``. The refusals and results of
``role_message`` are closed sets: a caller can act on each by name.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from agents_remember.models.base import ToolResponse
from agents_remember.models.task_document_ref import TaskDocumentRef

DEFAULT_WAIT_SECONDS = 300
MAX_WAIT_SECONDS = 1800


@dataclass(frozen=True, slots=True)
class RoleStartCall:
    """The inputs of one role start: the selection, the caller's request id, the override."""

    role: str
    request_id: uuid.UUID
    sprint_document_ref: TaskDocumentRef | None = None
    master_document_ref: TaskDocumentRef | None = None
    task_document_ref: TaskDocumentRef | None = None
    agent: str | None = None
    model: str | None = None
    effort: str | None = None


@dataclass(frozen=True, slots=True)
class RoleMessageCall:
    """The inputs of one message: the text, and the recipient by agent id or by selection."""

    text: str
    agent_id: str | None = None
    role: str | None = None
    sprint_document_ref: TaskDocumentRef | None = None
    master_document_ref: TaskDocumentRef | None = None
    task_document_ref: TaskDocumentRef | None = None
    wait: bool = False
    timeout_seconds: int = DEFAULT_WAIT_SECONDS


RoleStartStatus = Literal["running", "rejected", "unknown", "refused"]
RoleStartRefusal = Literal[
    "caller-has-no-binding",
    "no-paseo-runtime-configured",
    "host-unreachable",
    "role-may-not-start-role",
    "selection-outside-callers-scope",
    "launch-refused",
]

RoleMessageStatus = Literal[
    "accepted",
    "turn-finished",
    "turn-failed",
    "turn-cancelled",
    "permission-pending",
    "timeout",
    "refused",
]
RoleMessageRefusal = Literal[
    "recipient-busy",
    "recipient-not-found",
    "recipient-archived",
    "recipient-ambiguous",
    "recipient-cannot-be-resumed",
    "scope-check-failed",
    "caller-has-no-binding",
    "no-paseo-runtime-configured",
    "host-unreachable",
]


class RoleStartResponse(ToolResponse):
    """One role agent started for a canonical selection, or why it was not.

    ``status`` is the launch status: ``running`` (the host has the agent), ``rejected`` (the
    launch is closed; ``agentId`` names no usable agent) or ``unknown`` (no usable answer; the same
    ``requestId`` reconciles the same agent). ``executionStatus`` is what the execution's receipt
    says now, which differs from the launch status once the agent has finished a turn.
    """

    operation: Literal["role_start"] = "role_start"
    status: RoleStartStatus
    detail: str
    refusal: RoleStartRefusal | None = None
    nextAction: str | None = None
    requestId: str | None = None
    role: str | None = None
    agentId: str | None = None
    parentAgentId: str | None = None
    reportPath: str | None = None
    handoverArtifactPath: str | None = None
    executionStatus: str | None = None


class RoleMessageResponse(ToolResponse):
    """One message to one role agent: what the host did with it, or why it was not sent.

    ``senderLine`` is the first line of the delivered text. ``taken`` says how the recipient took
    the message: it ``started`` a turn with it, or its running turn took it up (``steered``).
    ``text`` is the recipient's final text of the turn that consumed the message.
    """

    operation: Literal["role_message"] = "role_message"
    status: RoleMessageStatus
    detail: str
    refusal: RoleMessageRefusal | None = None
    nextAction: str | None = None
    recipientAgentId: str | None = None
    candidateAgentIds: list[str] | None = None
    senderLine: str | None = None
    taken: Literal["started", "steered"] | None = None
    resumed: bool | None = None
    text: str | None = None
    textTruncated: bool | None = None
    permission: str | None = None
    waitedSeconds: int | None = None
    warning: str | None = None
