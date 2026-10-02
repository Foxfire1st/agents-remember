"""What the Paseo runtime says about one role agent, and the execution status that follows.

A refresh reads the agent once through the bridge (``agent-state``) and maps the answer through
one ordered, first-match table (:data:`STATUS_TABLE`: PNT-R07 item 1, followed by the two rows
ruled for the states that table does not name). The read has no effect on
the agent. Revive is the one call here that changes anything (``agent-resume``): it opens the
closed session of the same agent and returns the same kind of answer.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

# The result summary of an execution is the final text of the agent's last finished turn.
SUMMARY_LIMIT = 3000
_TEXT_LIMIT = 800
# The runtime's word for a live agent whose harness process is not running.
_CLOSED = "closed"
# How the last turn of an idle agent ended, in the bridge's words.
_TURN_STATES = frozenset({"none", "replied", "unreplied"})
# The receipt statuses of a launch whose turn was still open when the session closed.
_TURN_OPEN_STATUSES = frozenset({"running", "starting"})


@dataclass(frozen=True, slots=True)
class AgentReading:
    """One answer of the runtime about one agent.

    ``reachable`` is false when there is no usable answer; ``unreachable_reason`` then says why.
    ``lifecycle`` is the runtime's own status word; ``attention`` its own mark on the agent
    (``finished``, ``error``, ``permission``). ``last_turn`` is known only for an idle agent
    with an open session: ``none`` (no turn yet), ``replied`` (the turn ended with the agent's
    reply, ``final_text``) or ``unreplied`` (it ended without one, as a cancelled turn does).
    """

    reachable: bool = True
    unreachable_reason: str | None = None
    found: bool = True
    archived: bool = False
    lifecycle: str | None = None
    pending_permission: str | None = None
    turn_active: bool = False
    error: str | None = None
    attention: str | None = None
    last_turn: Literal["none", "replied", "unreplied"] | None = None
    final_text: str | None = None

    @property
    def session_closed(self) -> bool:
        return self.lifecycle == _CLOSED

    @property
    def last_turn_failed(self) -> bool:
        """An agent that is not in the error state but whose last turn failed.

        The runtime says so with its error text, or, when that text is gone (it does not outlive
        a closed session), with its error mark on an agent whose last turn left no reply.
        """

        return self.error is not None or (
            self.lifecycle == "idle" and self.attention == "error" and self.last_turn == "unreplied"
        )


@dataclass(frozen=True, slots=True)
class StatusRow:
    """What one row of the table says about the execution.

    ``None`` keeps what the receipt holds: its status, its detail, its ``canRevive`` flag. Only
    row 10 carries a result summary, and only row 1 marks the host unreachable. Rows 12 and 13
    are not in the packet's table: they keep the status and say what the runtime reports.
    """

    number: int
    status: str | None = None
    detail: str | None = None
    can_revive: bool | None = None
    summary: str | None = None
    host_unreachable: bool = False


@dataclass(frozen=True, slots=True)
class _Known:
    """What the receipt already holds and a row may depend on."""

    status: str
    # The runtime's reason, when a Revive recorded that the agent cannot be resumed.
    resume_refusal: str | None


_Matches = Callable[[AgentReading, _Known], bool]
_Outcome = Callable[[AgentReading, _Known], StatusRow]

# PNT-R07 item 1, in table order, then the two ruled rows. The first row whose condition holds
# decides; the last row holds for every reading, so one always does.
STATUS_TABLE: tuple[tuple[_Matches, _Outcome], ...] = (
    (
        lambda reading, _known: not reading.reachable,
        lambda _reading, _known: StatusRow(1, host_unreachable=True),
    ),
    (
        lambda reading, _known: not reading.found,
        lambda _reading, _known: StatusRow(2, "stopped", "the host has no such agent", False),
    ),
    (
        lambda reading, _known: reading.archived,
        lambda _reading, _known: StatusRow(3, "stopped", "archived", False),
    ),
    (
        lambda reading, known: reading.session_closed and known.status in _TURN_OPEN_STATUSES,
        lambda _reading, known: StatusRow(
            4, "interrupted", f"session closed while {known.status}", True
        ),
    ),
    (
        lambda reading, _known: reading.session_closed,
        lambda _reading, known: StatusRow(
            5,
            None,
            known.resume_refusal or "session closed",
            known.resume_refusal is None,
        ),
    ),
    (
        lambda reading, _known: reading.pending_permission is not None,
        lambda reading, _known: StatusRow(
            6, "running", f"waiting for permission: {reading.pending_permission}", False
        ),
    ),
    (
        lambda reading, _known: reading.turn_active or reading.lifecycle == "running",
        lambda _reading, _known: StatusRow(7, "running", "a turn is in progress", False),
    ),
    (
        lambda reading, _known: reading.lifecycle == "error" or reading.last_turn_failed,
        lambda reading, _known: StatusRow(8, "failed", _failure_detail(reading), False),
    ),
    (
        lambda reading, _known: reading.lifecycle == "idle" and reading.last_turn == "unreplied",
        lambda _reading, _known: StatusRow(
            9, "stopped", "last turn cancelled; the agent is idle", False
        ),
    ),
    (
        lambda reading, _known: reading.lifecycle == "idle" and reading.last_turn == "replied",
        lambda reading, _known: StatusRow(
            10,
            "completed",
            "last turn finished; the agent is idle",
            False,
            summary=(reading.final_text or "")[:SUMMARY_LIMIT],
        ),
    ),
    (
        lambda reading, _known: reading.lifecycle == "idle" and reading.last_turn == "none",
        lambda _reading, _known: StatusRow(11, "running", "started; no turn yet", False),
    ),
    (
        lambda reading, _known: reading.lifecycle == "initializing",
        lambda _reading, _known: StatusRow(12, detail="the agent is starting", can_revive=False),
    ),
    (
        lambda _reading, _known: True,
        lambda reading, _known: StatusRow(
            13, detail=f"unrecognised agent state: {reading.lifecycle}", can_revive=False
        ),
    ),
)


def _failure_detail(reading: AgentReading) -> str:
    """Row 8's detail: the runtime's message, said to be of the last turn when the agent is idle."""

    if reading.lifecycle == "error":
        return reading.error or "the agent is in an error state"
    return f"last turn failed: {reading.error}" if reading.error else "last turn failed"


def status_row(
    reading: AgentReading, previous_status: str, resume_refusal: str | None = None
) -> StatusRow:
    """The first row of the table that matches the reading."""

    known = _Known(previous_status, resume_refusal)
    return next(
        outcome(reading, known) for matches, outcome in STATUS_TABLE if matches(reading, known)
    )


def read_agent(config: McpRuntimeConfig, agent_id: str) -> AgentReading:
    """Ask the runtime about one agent: a single bridge call that changes nothing.

    A call that fails, for whatever reason, is a host that cannot be reached. The routes refuse an
    unconfigured runtime before they come here.
    """

    try:
        reply = bridge_call(config, "agent-state", {"agentId": agent_id})
    except PaseoBridgeFailure as error:
        return _no_answer(f"{error.code}: {error}")
    return _reading(reply, agent_id)


@dataclass(frozen=True, slots=True)
class ResumeOutcome:
    """What the runtime answered to a resume: the agent afterwards, and why it stayed closed."""

    reading: AgentReading
    resumed: bool
    refusal: str | None = None


def resume_agent(config: McpRuntimeConfig, agent_id: str) -> ResumeOutcome:
    """Open the closed session of the agent with this id, without a message; never create one.

    ``refusal`` is set when the runtime answered and the session is still closed: the agent
    cannot be resumed. A call without a usable answer is an unreachable reading.
    """

    try:
        reply = bridge_call(config, "agent-resume", {"agentId": agent_id})
    except PaseoBridgeFailure as error:
        return ResumeOutcome(_no_answer(f"{error.code}: {error}"), resumed=False)
    reading = _reading(reply, agent_id)
    resume = reply.get("resume")
    if not reading.reachable or not isinstance(resume, dict):
        reason = reading.unreachable_reason or "the bridge returned an unreadable resume answer"
        return ResumeOutcome(_no_answer(reason), resumed=False)
    refused = resume.get("attempted") is True and reading.session_closed and not reading.archived
    if not refused:
        return ResumeOutcome(reading, resumed=resume.get("resumed") is True)
    error_text = resume.get("error")
    return ResumeOutcome(
        reading,
        resumed=False,
        refusal=(
            error_text[:_TEXT_LIMIT]
            if isinstance(error_text, str) and error_text
            else "the host left the session closed without giving a reason"
        ),
    )


def _no_answer(reason: str) -> AgentReading:
    return AgentReading(reachable=False, unreachable_reason=reason[:_TEXT_LIMIT])


def _reading(reply: dict[str, Any], agent_id: str) -> AgentReading:
    """Turn the bridge's answer into a reading; an answer that cannot be read is no answer."""

    if "agent" not in reply:
        return _no_answer("the bridge returned an unreadable agent state")
    agent = reply["agent"]
    if agent is None:
        return AgentReading(found=False)
    if not isinstance(agent, dict) or agent.get("id") != agent_id:
        return _no_answer("the bridge returned an unreadable agent state")
    lifecycle = agent.get("status")
    if not isinstance(lifecycle, str) or not lifecycle:
        return _no_answer("the bridge returned an agent without a status")
    last_turn = agent.get("lastTurn")
    turn_state = last_turn.get("state") if isinstance(last_turn, dict) else None
    final_text = last_turn.get("text") if isinstance(last_turn, dict) else None
    if turn_state not in _TURN_STATES and lifecycle == "idle" and not agent.get("archivedAt"):
        # The bridge reads the last turn of every idle agent; without it the reply is incomplete.
        return _no_answer("the bridge returned an idle agent without its last turn")
    attention = agent.get("attentionReason")
    return AgentReading(
        archived=bool(agent.get("archivedAt")),
        lifecycle=lifecycle,
        pending_permission=_pending_permission(agent.get("pendingPermissions")),
        turn_active=agent.get("turnActive") is True,
        error=_error_message(agent.get("lastError")),
        attention=attention if isinstance(attention, str) and attention else None,
        last_turn=turn_state if turn_state in _TURN_STATES else None,
        final_text=final_text if isinstance(final_text, str) else None,
    )


def _error_message(error: Any) -> str | None:
    """The runtime's error text; of an error document with a message, that message.

    A provider's refusal arrives as a JSON document such as
    ``{"type": "error", "status": 400, "error": {"message": "…"}}``.
    """

    if not isinstance(error, str) or not error:
        return None
    try:
        document = json.loads(error)
    except ValueError:
        document = None
    if isinstance(document, dict):
        inner = document.get("error")
        for holder in (inner, document):
            message = holder.get("message") if isinstance(holder, dict) else None
            if isinstance(message, str) and message:
                return message[:_TEXT_LIMIT]
    return error[:_TEXT_LIMIT]


def _pending_permission(pending: Any) -> str | None:
    """The tool name of the first pending permission request, when there is one."""

    if not isinstance(pending, list) or not pending:
        return None
    first = pending[0]
    name = first.get("name") if isinstance(first, dict) else None
    return name if isinstance(name, str) and name else "a tool"
