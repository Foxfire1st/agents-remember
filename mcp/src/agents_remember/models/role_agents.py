"""Wire models of the two tools through which role agents start and message role agents.

Both tools answer in one envelope. ``status`` names the result; a call the tool did not carry
out has ``ok: false``, ``status: "refused"``, the one ``refusal`` that applies, the reason in
``detail`` and what the caller can do in ``nextAction``. The refusals and results of
``role_message`` are closed sets: a caller can act on each by name.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

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
    feature_values: dict[str, bool | str] = field(default_factory=dict)
    # The holder this start replaces on its seat (MIK-R100 rule 9): the start retires
    # that agent first, in the same call, by rule 8. Absent for an ordinary start.
    replaces_agent_id: str | None = None
    # The word that the holder's turn, if it is in one, may be interrupted by the
    # replacement (rule 8). Without it a running holder is refused, as in retirement.
    replaces_interrupt_turn: bool = False
    step_up: str | None = None


@dataclass(frozen=True, slots=True)
class SeatLaunchOverride:
    """The launch choices of one seat of a set start, as any start of its role takes them."""

    agent: str | None = None
    model: str | None = None
    effort: str | None = None
    feature_values: dict[str, bool | str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RoleStartSetCall:
    """The inputs of one set start: the leaf, the set request id, and per-seat choices.

    The Manager names a leaf and the product starts its Worker, Reviewer and Curator
    in this one call (MIK-R100 rule 1). Per-seat request ids derive deterministically
    from ``request_id``, so a repeat with the same set id reconciles the same three
    agents and creates no fourth. ``reviewer_message`` is the Reviewer's first work
    (rule 3): carried, the Reviewer opens at once; absent, Reviewer and Curator are
    held (rule 2). ``replaces`` names, per seat role, the holder a replacing start
    retires first (rule 9).
    """

    request_id: uuid.UUID
    sprint_document_ref: TaskDocumentRef | None = None
    master_document_ref: TaskDocumentRef | None = None
    task_document_ref: TaskDocumentRef | None = None
    reviewer_message: str | None = None
    worker: SeatLaunchOverride = field(default_factory=SeatLaunchOverride)
    reviewer: SeatLaunchOverride = field(default_factory=SeatLaunchOverride)
    curator: SeatLaunchOverride = field(default_factory=SeatLaunchOverride)
    replaces: dict[str, str] = field(default_factory=dict)
    # The word, for the whole set, that a replaced holder's turn may be
    # interrupted (rule 8). Without it a running holder is refused, as in retirement.
    replaces_interrupt_turn: bool = False


@dataclass(frozen=True, slots=True)
class RoleMessageCall:
    """The inputs of one message: the text, and the recipient by agent id or by selection.

    ``text`` is the wake-up: at most the settings' limit of characters. ``body``
    takes the full text of any length for the message file, and ``attach`` the
    paths of files the message file records (MIK-R117).
    """

    text: str
    agent_id: str | None = None
    role: str | None = None
    sprint_document_ref: TaskDocumentRef | None = None
    master_document_ref: TaskDocumentRef | None = None
    task_document_ref: TaskDocumentRef | None = None
    body: str | None = None
    attach: list[str] | None = None
    wait: bool = False
    timeout_seconds: int = DEFAULT_WAIT_SECONDS
    # One held message of this sender for this recipient to end as withdrawn
    # (MIK-R100 rules 4a/4b): the id a held answer named in heldMessageId. The text
    # is still processed normally, so withdrawing with a new text replaces the
    # earlier one in one call.
    withdraw_held_message_id: str | None = None


RoleStartStatus = Literal["running", "held", "rejected", "unknown", "refused"]
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
    "held",
    "turn-finished",
    "turn-failed",
    "turn-cancelled",
    "permission-pending",
    "timeout",
    "refused",
]
RoleMessageRefusal = Literal[
    "investigator-capacity",
    "recipient-busy",
    "recipient-not-found",
    "recipient-archived",
    "recipient-ambiguous",
    "recipient-cannot-be-resumed",
    "scope-check-failed",
    "caller-has-no-binding",
    "no-paseo-runtime-configured",
    "host-unreachable",
    "message-too-long",
    "message-file-failed",
    "settings-invalid",
]


class RoleEnvironmentPreparation(BaseModel):
    """What the shared start established; opened names no host-reported creation outcome."""

    model_config = ConfigDict(extra="forbid")
    enclosure: Literal["created", "found", "not-applicable"]
    workspace: Literal["created", "found", "opened"] | None = None


class RoleStartResponse(ToolResponse):
    """One role agent started for a canonical selection, or why it was not.

    ``status`` is the launch status: ``running`` (the host has the agent), ``held`` (a held
    seat: the agent exists and has taken no turn), ``rejected`` (the
    launch is closed; ``agentId`` names no usable agent) or ``unknown`` (no usable answer; the same
    ``requestId`` reconciles the same agent). ``executionStatus`` is what the execution's receipt
    says now, which differs from the launch status once the agent has finished a turn.
    """

    operation: Literal["role_start"] = "role_start"
    stepUp: dict[str, Any] | None = None
    status: RoleStartStatus
    detail: str
    refusal: RoleStartRefusal | None = None
    nextAction: str | None = None
    requestId: str | None = None
    role: str | None = None
    agentId: str | None = None
    # Rule 6 (MIK-R112): whether this agent also carries the product's other server. True on
    # harnesses with no one-launch exclusion; the brief keeps the server-choice sentence
    # for those.
    secondCopyLoaded: bool = False
    parentAgentId: str | None = None
    reportPath: str | None = None
    handoverArtifactPath: str | None = None
    executionStatus: str | None = None
    preparation: RoleEnvironmentPreparation | None = None
    preparationStatus: str | None = None
    choices: dict[str, Any] | None = None
    warning: str | None = None
    taskDocumentFiles: dict[str, Any] | None = None


class RoleMessageResponse(ToolResponse):
    """One message to one role agent: what the host did with it, or why it was not sent.

    ``senderLine`` is the first line of the delivered text. ``taken`` says how the recipient took
    the message: it ``started`` a turn with it, or its running turn took it up (``steered``).
    ``text`` is the recipient's final text of the turn that consumed the message. A message
    the product holds (MIK-R100 rules 4a/4b) answers ``held`` instead of ``accepted``:
    ``heldReason`` says why, ``heldMessageId`` names the waiting message, and
    ``waitingMine``/``waitingTotal`` say how many messages of this sender, and in all,
    wait for the recipient.
    ``messageFile``/``messageFileSha256`` name the message file (MIK-R117): the delivered
    message's file, a refused delivery's ``.undelivered.md`` file, or the file of a wait
    reply cut at the message limit.
    """

    operation: Literal["role_message"] = "role_message"
    status: RoleMessageStatus
    detail: str
    refusal: RoleMessageRefusal | None = None
    nextAction: str | None = None
    recipientAgentId: str | None = None
    candidateAgentIds: list[str] | None = None
    senderLine: str | None = None
    taken: Literal["started", "steered", "replaced"] | None = None
    resumed: bool | None = None
    text: str | None = None
    textTruncated: bool | None = None
    # MIK-R117: the message file of a delivered message (rule 4), of a refused
    # delivery (rule 2, ending in .undelivered.md), or of a cut wait reply
    # (rule 6). The delivered/wait text names the same file and hash in words.
    messageFile: str | None = None
    messageFileSha256: str | None = None
    # MIK-R117: every producer-owned refusal field the tool can return. The
    # strict contract must name them all, or the registered path throws
    # instead of refusing (L117-C1-F07).
    messageLength: int | None = None
    messageLimit: int | None = None
    successorAgentId: str | None = None
    permission: str | None = None
    waitedSeconds: int | None = None
    warning: str | None = None
    reportCollection: dict[str, Any] | None = None
    heldReason: str | None = None
    heldMessageId: str | None = None
    waitingMine: int | None = None
    waitingTotal: int | None = None
    recentTerminal: list[dict[str, Any]] | None = None
    openedHeldSeat: bool | None = None
    withdrawnHeldMessageId: str | None = None


class RoleStartSetResponse(ToolResponse):
    """Three seats of a leaf from one call (MIK-R100 rule 1), or why the set is partial.

    ``seats`` carries worker, reviewer and curator in that order: each names its agent,
    its seat request id, whether the seat is held (no turn yet) or opened at once, and
    whether the host records a turn for it. A seat that has an agent also says whether the
    agent carries the product's other server (``secondCopyLoaded``, as ``RoleStartResponse``
    says it of a single start). ``ok`` is true only when all three seats
    have an agent; a partial set names the failed seat, leaves the others as they are,
    and a repeat with the same set request id completes it.
    """

    operation: Literal["role_start_set"] = "role_start_set"
    status: str
    detail: str
    refusal: RoleStartRefusal | None = None
    nextAction: str | None = None
    requestId: str | None = None
    seats: dict[str, Any] | None = None
