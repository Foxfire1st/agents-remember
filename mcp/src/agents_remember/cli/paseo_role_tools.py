"""Role start and role messaging by role agents.

Both operations run in the tool server a launch started for one agent. The caller is that agent:
the binding of its tool server says who it is, and a tool server without a binding serves neither.

:func:`start_role` starts one role agent for a canonical selection through the launcher's own
start path, with the caller recorded as the new agent's parent. :func:`send_role_message` sends
one text message to one role agent of this line's receipts: the delivered text opens with a line
that names the sender, a recipient's running turn is never cancelled, a closed session is resumed
first under the scope check of Revive, and an archived or missing recipient is refused. With
``wait`` the call returns the outcome of the turn that consumed the message. The wait is a
sequence of bridge calls inside this one tool call; nothing here polls outside a call and no
message is stored.

Every answer is a plain mapping in the shape of the tools' response models. A call that is not
carried out is a refusal that names its one reason.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from agents_remember.application.agent_binding import AgentBinding, read_agent_binding
from agents_remember.application.orca_task_context import (
    LEAF_ROLES,
    TASKLESS_ROLES,
    resolve_orca_role_context,
    selection_binding,
)
from agents_remember.cli.orca_task_preparation import (
    ROLE_MESSAGE_TOOL,
    ROLE_START_TOOL,
    _verify_leaf_revival_scope,
)
from agents_remember.cli.orca_task_receipts import (
    EXECUTIONS_DIRECTORY,
    _read_receipt,
    _receipt_path,
    _taskless_execution_receipts,
)
from agents_remember.cli.orca_task_routes import _orca_dispatch_endpoint
from agents_remember.cli.paseo_bridge import (
    BRIDGE_REFUSED,
    BRIDGE_TIMEOUT,
    RUNTIME_NOT_CONFIGURED,
    PaseoBridgeFailure,
    bridge_call,
    require_bridge_runtime,
)
from agents_remember.cli.paseo_catalog import forget_launcher_catalogs
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.cli.paseo_status import read_agent, resume_agent
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.orca_launcher import OrcaDispatchRequest, OrcaSelection
from agents_remember.models.role_agents import (
    MAX_WAIT_SECONDS,
    RoleMessageCall,
    RoleStartCall,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology

# One bridge call of a wait: well inside the limit every bridge call has to end within.
WAIT_SLICE_SECONDS = 40
# How many of a selection's most recent executions are asked about when a role is addressed.
_CANDIDATE_LIMIT = 12
_PROJECTS = "Projects"

# Who may start what (PNT-R06 item 4); a role that is not listed may start none.
MAY_START: dict[str, tuple[str, ...]] = {
    "architect": ("system-specialist", "orchestrator", "manager", "worker", "reviewer", "curator"),
    "orchestrator": ("manager", "worker", "reviewer", "curator"),
    "manager": ("worker", "reviewer", "curator"),
}

_monotonic = time.monotonic


class _Refused(Exception):
    """One named reason for not carrying out a call."""

    def __init__(self, refusal: str, detail: str, next_action: str, **fields: Any) -> None:
        super().__init__(detail)
        self.refusal = refusal
        self.detail = detail
        self.next_action = next_action
        self.fields = fields

    def payload(self) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "refused",
            "refusal": self.refusal,
            "detail": self.detail,
            "nextAction": self.next_action,
            **self.fields,
        }


@dataclass(frozen=True, slots=True)
class _Recipient:
    agent_id: str
    receipt: dict[str, Any]


# ---------------------------------------------------------------------------------------------
# The caller
# ---------------------------------------------------------------------------------------------


def _caller(config: McpRuntimeConfig, environment: Mapping[str, str] | None) -> AgentBinding:
    """The binding of the calling agent and a configured runtime, or the refusal that names it."""

    try:
        binding = read_agent_binding(environment)
    except ValueError as error:
        raise _no_binding(f" {error}") from error
    if binding is None:
        raise _no_binding("")
    try:
        require_bridge_runtime(config)
    except PaseoBridgeFailure as error:
        raise _Refused(
            "no-paseo-runtime-configured",
            str(error),
            "Tell the developer in your own chat that the AR settings name no Paseo runtime.",
        ) from error
    return binding


def _no_binding(reason: str) -> _Refused:
    return _Refused(
        "caller-has-no-binding",
        "This tool server was not started by an AR role launch, so the caller has no binding "
        f"(agent id, role, task references).{reason}",
        f"Only a role agent that AR launched can use {ROLE_START_TOOL} and {ROLE_MESSAGE_TOOL}; "
        "start the role from the dashboard launcher.",
    )


def starting_agent(config: McpRuntimeConfig, binding: AgentBinding) -> StartingAgent:
    """The caller as the runtime and a recipient see it: agent id, role, and the work it serves.

    The work is named by the id of the most specific task document of the binding, or
    ``Projects`` for a taskless role.
    """

    reference = binding.task_ref or binding.master_ref or binding.sprint_ref
    if reference is None:
        return StartingAgent(binding.agent_id, binding.role, _PROJECTS)
    try:
        subject = TaskDocumentTopology(config.coordination_root).resolve(reference).document.id
    except TaskDocumentRefError:
        # The document is gone or unreadable; its reference still says which work is meant.
        subject = reference.key
    return StartingAgent(binding.agent_id, binding.role, subject)


def sender_line(sender: StartingAgent) -> str:
    """The line that opens every delivered message."""

    return f"From {sender.role} · {sender.subject} · agent {sender.agent_id}"


# ---------------------------------------------------------------------------------------------
# Role start
# ---------------------------------------------------------------------------------------------


def start_rule_violation(binding: AgentBinding, selection: OrcaSelection) -> tuple[str, str] | None:
    """The rule a start would violate, as (refusal, the rule in words); ``None`` when allowed."""

    allowed = MAY_START.get(binding.role, ())
    if selection.role not in allowed:
        offered = ", ".join(allowed) if allowed else "no role"
        return (
            "role-may-not-start-role",
            f"A {binding.role} may start {offered}; it may not start a {selection.role}. "
            "(An architect may start every role except architect; an orchestrator may start "
            "manager, worker, reviewer and curator; a manager may start worker, reviewer and "
            "curator; worker, reviewer, curator and system-specialist may start none.)",
        )
    if binding.role == "orchestrator" and selection.sprint_document_ref != binding.sprint_ref:
        return (
            "selection-outside-callers-scope",
            "An orchestrator may start roles only on selections under its own sprint "
            f"({_key(binding.sprint_ref)}); this selection names sprint "
            f"{_key(selection.sprint_document_ref)}.",
        )
    if binding.role == "manager" and (
        selection.master_document_ref != binding.master_ref
        or selection.sprint_document_ref != binding.sprint_ref
    ):
        return (
            "selection-outside-callers-scope",
            "A manager may start roles only on selections under its own master "
            f"({_key(binding.master_ref)}); this selection names master "
            f"{_key(selection.master_document_ref)} under sprint "
            f"{_key(selection.sprint_document_ref)}.",
        )
    return None


def _key(reference: TaskDocumentRef | None) -> str:
    return reference.key if reference is not None else "none"


def start_role(
    config: McpRuntimeConfig,
    call: RoleStartCall,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Start one role agent for the selection, as the launcher's Start does, with a parent."""

    try:
        return _start_role(config, call, _caller(config, environment))
    except _Refused as refused:
        return {**refused.payload(), "requestId": str(call.request_id), "role": call.role}


def _start_role(
    config: McpRuntimeConfig, call: RoleStartCall, binding: AgentBinding
) -> dict[str, Any]:
    try:
        request = OrcaDispatchRequest.model_validate(
            {
                "role": call.role,
                "sprintDocumentRef": call.sprint_document_ref,
                "masterDocumentRef": call.master_document_ref,
                "taskDocumentRef": call.task_document_ref,
                "requestId": call.request_id,
                "agentOverride": _agent_override(call),
            }
        )
    except (ValidationError, ValueError) as error:
        raise _launch_refused(f"The start request is not valid: {error}") from error
    violated = start_rule_violation(binding, request)
    if violated is not None:
        raise _Refused(
            violated[0],
            violated[1],
            "Start only roles this rule allows; anything else is the developer's or the "
            "parent's to start.",
        )
    # Agent, model and effort are checked against the catalog as the runtime reports it at the
    # time of this call; this process keeps no catalog between calls.
    forget_launcher_catalogs()
    try:
        _orca_dispatch_endpoint(config, request, started_by=starting_agent(config, binding))
    except HTTPException as error:
        raise _start_refusal(error) from error
    finally:
        forget_launcher_catalogs()
    return _started(config, request, binding)


def _agent_override(call: RoleStartCall) -> dict[str, str] | None:
    if call.agent is None:
        if call.model is not None or call.effort is not None:
            raise ValueError("a model or an effort is given without the agent it belongs to")
        return None
    return {
        key: value
        for key, value in (
            ("agentId", call.agent),
            ("modelId", call.model),
            ("effortId", call.effort),
        )
        if value is not None
    }


def _launch_refused(detail: str) -> _Refused:
    return _Refused(
        "launch-refused",
        detail,
        "Resolve the reason named in detail; repeat the same request id only for the same "
        "selection and override.",
    )


def _start_refusal(error: HTTPException) -> _Refused:
    """Why the start path refused before it recorded anything for this request."""

    detail = str(error.detail)
    cause = error.__cause__
    if isinstance(cause, PaseoBridgeFailure):
        if cause.code == RUNTIME_NOT_CONFIGURED:
            return _Refused(
                "no-paseo-runtime-configured",
                detail,
                "Tell the developer in your own chat that the AR settings name no Paseo runtime.",
            )
        if cause.code != BRIDGE_REFUSED:
            return _host_unreachable(
                f"{cause.code}: {detail} Nothing was recorded for this request."
            )
    return _launch_refused(detail)


def _host_unreachable(detail: str) -> _Refused:
    return _Refused(
        "host-unreachable",
        f"The Paseo runtime cannot be reached. {detail}",
        "Tell the developer in your own chat that the Paseo runtime cannot be reached; repeat "
        "the call once it runs again.",
    )


def _started(
    config: McpRuntimeConfig, request: OrcaDispatchRequest, binding: AgentBinding
) -> dict[str, Any]:
    """The tool's answer, read from the receipt the start path wrote for this request."""

    try:
        receipt = _read_receipt(
            _receipt_path(
                config, request, request.request_id if request.role in TASKLESS_ROLES else None
            )
        )
    except HTTPException as error:
        raise _launch_refused(str(error.detail)) from error
    if receipt is None or receipt.get("requestId") != str(request.request_id):
        raise _launch_refused("The start was answered but its receipt cannot be read back.")
    execution = str(receipt.get("status"))
    launch = (
        "rejected"
        if execution == "rejected"
        else "unknown"
        if execution in {"starting", "unknown"}
        else "running"
    )
    report = receipt.get("report")
    artifact = receipt.get("handoverArtifact")
    next_action = {
        "running": None,
        "rejected": "No agent exists under agentId. Resolve the reason in detail, then start "
        "again with a new request id.",
        "unknown": f"Call {ROLE_START_TOOL} again with the same request id and selection: it "
        "reconciles the same agent and never creates a second.",
    }[launch]
    return {
        "ok": launch == "running",
        "status": launch,
        "detail": str(receipt.get("detail") or ""),
        "requestId": str(request.request_id),
        "role": request.role,
        "agentId": receipt.get("agentId"),
        "parentAgentId": receipt.get("parentAgentId") or binding.agent_id,
        "reportPath": report.get("path") if isinstance(report, dict) else None,
        "handoverArtifactPath": artifact.get("path") if isinstance(artifact, dict) else None,
        "executionStatus": execution,
        **({"nextAction": next_action} if next_action else {}),
    }


# ---------------------------------------------------------------------------------------------
# Role message
# ---------------------------------------------------------------------------------------------


def send_role_message(
    config: McpRuntimeConfig,
    call: RoleMessageCall,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Send one message to one role agent and, with ``wait``, return the turn's outcome."""

    try:
        binding = _caller(config, environment)
        sender = starting_agent(config, binding)
        recipient = _resolve_recipient(config, call, binding)
        return _deliver(config, call, sender, recipient)
    except _Refused as refused:
        return refused.payload()


def _resolve_recipient(
    config: McpRuntimeConfig, call: RoleMessageCall, binding: AgentBinding
) -> _Recipient:
    by_selection = call.role is not None
    if (call.agent_id is not None) == by_selection:
        raise _not_found(
            "Name the recipient either by agent_id, or by role plus the task references its "
            "class requires; not both and not neither."
        )
    recipient = (
        _recipient_by_selection(config, call) if by_selection else _recipient_by_id(config, call)
    )
    if recipient.agent_id == binding.agent_id:
        raise _not_found("The recipient this resolves to is the calling agent itself.")
    return recipient


def _not_found(detail: str, **fields: Any) -> _Refused:
    return _Refused(
        "recipient-not-found",
        detail,
        "Address a role agent that this AR line launched: by the agent id a start returned, or "
        "by its role and task references.",
        **fields,
    )


def _archived(agent_id: str) -> _Refused:
    return _Refused(
        "recipient-archived",
        f"Agent {agent_id} is archived. A message never un-archives an agent.",
        f"Start the role again with {ROLE_START_TOOL} if the work continues, or ask the "
        "developer in your own chat.",
        recipientAgentId=agent_id,
    )


def _recipient_by_id(config: McpRuntimeConfig, call: RoleMessageCall) -> _Recipient:
    """The execution of this line whose receipt records exactly this agent id."""

    agent_id = call.agent_id or ""
    if not _is_minted_id(agent_id):
        # The runtime resolves id prefixes and titles; only an id AR minted is passed to it.
        raise _not_found(
            f"{agent_id!r} is not an agent id: AR agent ids are UUIDs. A role name or a title is "
            "never an id; address a role with role and task references instead."
        )
    found: dict[str, Any] | None = None
    for path, receipt in _line_receipts(config):
        if receipt.get("agentId") == agent_id:
            found = receipt
            if path.parent.name != "history":
                break
    if found is None:
        raise _not_found(
            f"No role execution of this AR line records agent {agent_id}.",
            recipientAgentId=agent_id,
        )
    return _Recipient(agent_id, found)


def _is_minted_id(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _line_receipts(config: McpRuntimeConfig) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Every readable execution receipt of this line, current ones and archived ones."""

    coordination = config.coordination_root
    roots = [coordination / "notes" / "reports" / EXECUTIONS_DIRECTORY]
    roots.extend(sorted((coordination / "tasks").glob(f"**/notes/reports/{EXECUTIONS_DIRECTORY}")))
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.json")):
            if "message-bindings" in path.relative_to(root).parts:
                continue
            receipt = _readable_receipt(path)
            if receipt is not None:
                yield path, receipt


def _readable_receipt(path: Path) -> dict[str, Any] | None:
    try:
        return _read_receipt(path)
    except HTTPException:
        return None


def _recipient_by_selection(config: McpRuntimeConfig, call: RoleMessageCall) -> _Recipient:
    """The live agent of the selection's executions; several live agents are never chosen among.

    A task-bound selection has one current execution; its agent is the recipient when it is live,
    and otherwise the most recent earlier execution whose agent is live. A taskless role can have
    several live agents; then the recipient is ambiguous.
    """

    try:
        selection = OrcaSelection.model_validate(
            {
                "role": call.role,
                "sprintDocumentRef": call.sprint_document_ref,
                "masterDocumentRef": call.master_document_ref,
                "taskDocumentRef": call.task_document_ref,
            }
        )
        resolve_orca_role_context(config, selection)
        receipts = _selection_receipts(config, selection)
    except (ValidationError, ValueError, TaskDocumentRefError, HTTPException) as error:
        detail = error.detail if isinstance(error, HTTPException) else error
        raise _not_found(f"The recipient selection cannot be resolved: {detail}") from error
    live, archived = _live_agents(config, receipts, first_only=selection.role not in TASKLESS_ROLES)
    if len(live) > 1:
        ids = [recipient.agent_id for recipient in live]
        raise _Refused(
            "recipient-ambiguous",
            f"{len(ids)} live {selection.role} agents match: {', '.join(ids)}. The tool never "
            "picks one.",
            "Send the message again addressed to one of candidateAgentIds by agent_id.",
            candidateAgentIds=ids,
        )
    if live:
        return live[0]
    if archived is not None:
        raise _archived(archived)
    raise _not_found(f"No live {selection.role} agent is recorded for this selection.")


def _live_agents(
    config: McpRuntimeConfig, receipts: list[dict[str, Any]], *, first_only: bool
) -> tuple[list[_Recipient], str | None]:
    """The executions whose agent is live, and the first archived agent met on the way.

    Each agent is read once from the host; the read changes nothing. ``first_only`` stops at the
    first live agent, which for a task-bound selection is its open or most recent execution.
    """

    live: list[_Recipient] = []
    archived: str | None = None
    for receipt in receipts[:_CANDIDATE_LIMIT]:
        agent_id = receipt.get("agentId")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        reading = read_agent(config, agent_id)
        if not reading.reachable:
            raise _host_unreachable(str(reading.unreachable_reason))
        if reading.found and reading.archived:
            archived = archived or agent_id
        elif reading.found:
            live.append(_Recipient(agent_id, receipt))
            if first_only:
                break
    return live, archived


def _selection_receipts(config: McpRuntimeConfig, selection: OrcaSelection) -> list[dict[str, Any]]:
    """The selection's executions, the current one first, then earlier ones newest first."""

    if selection.role in TASKLESS_ROLES:
        return [receipt for _path, receipt in _taskless_execution_receipts(config, selection)]
    path = _receipt_path(config, selection)
    current = _read_receipt(path)
    expected = selection_binding(selection)
    earlier = [
        receipt
        for receipt in map(_readable_receipt, (path.parent / "history").glob("*.json"))
        if receipt is not None and receipt.get("selection") == expected
    ]
    earlier.sort(key=lambda receipt: str(receipt.get("createdAt", "")), reverse=True)
    return [*([current] if current is not None else []), *earlier]


def _deliver(
    config: McpRuntimeConfig,
    call: RoleMessageCall,
    sender: StartingAgent,
    recipient: _Recipient,
) -> dict[str, Any]:
    line = sender_line(sender)
    payload = {
        "agentId": recipient.agent_id,
        "text": f"{line}\n{call.text}",
        "messageId": str(uuid.uuid4()),
    }
    delivery = _send(config, payload)
    resumed = False
    if delivery.get("refused") == "closed":
        _resume(config, recipient)
        resumed = True
        delivery = _send(config, payload)
    if delivery.get("delivered") is not True:
        raise _undelivered(recipient.agent_id, delivery)
    taken = delivery.get("taken")
    turn_id = delivery.get("turnId")
    result: dict[str, Any] = {
        "ok": True,
        "status": "accepted",
        "detail": (
            "The recipient's running turn took the message up; nothing was cancelled."
            if taken == "steered"
            else "The recipient started a turn with the message."
        ),
        "recipientAgentId": recipient.agent_id,
        "senderLine": line,
        "taken": "steered" if taken == "steered" else "started",
        **({"resumed": True} if resumed else {}),
        **(
            {
                "warning": "The Paseo runtime cancelled the recipient's running turn to deliver "
                "this message although it was asked to hand the message to that turn."
            }
            if taken == "replaced"
            else {}
        ),
    }
    if not call.wait:
        return result
    return {
        **result,
        **_wait(
            config,
            recipient.agent_id,
            turn_id if isinstance(turn_id, str) and turn_id else None,
            min(max(1, call.timeout_seconds), MAX_WAIT_SECONDS),
        ),
    }


def _send(config: McpRuntimeConfig, payload: dict[str, str]) -> dict[str, Any]:
    """One delivery attempt; a turn that changed under the send is tried once more."""

    for attempt in (1, 2):
        try:
            reply = bridge_call(config, "agent-send", payload)
        except PaseoBridgeFailure as error:
            if error.code == BRIDGE_REFUSED:
                if attempt == 1:
                    continue
                return {"delivered": False, "refused": "busy", "detail": str(error)}
            uncertain = (
                " It is not known whether the message was delivered."
                if error.code == BRIDGE_TIMEOUT
                else ""
            )
            raise _host_unreachable(f"{error.code}: {error}{uncertain}") from error
        delivery = reply.get("delivery")
        if not isinstance(delivery, dict):
            raise _host_unreachable("The Paseo bridge returned an unreadable delivery answer.")
        return delivery
    raise AssertionError("unreachable")


def _resume(config: McpRuntimeConfig, recipient: _Recipient) -> None:
    """Resume the recipient's closed session, under the scope check a Revive applies."""

    receipt = recipient.receipt
    if receipt.get("role") in LEAF_ROLES:
        try:
            selection = OrcaSelection.model_validate(receipt.get("selection"))
            _verify_leaf_revival_scope(resolve_orca_role_context(config, selection), receipt)
        except (ValidationError, ValueError, TaskDocumentRefError) as error:
            raise _Refused(
                "scope-check-failed",
                str(error),
                f"The recipient's task scope changed since its launch. Start the role again "
                f"with {ROLE_START_TOOL} for the current scope, or ask the developer.",
                recipientAgentId=recipient.agent_id,
            ) from error
    outcome = resume_agent(config, recipient.agent_id)
    reading = outcome.reading
    if not reading.reachable:
        raise _host_unreachable(str(reading.unreachable_reason))
    if not reading.found:
        raise _not_found(
            f"The host has no agent {recipient.agent_id}.", recipientAgentId=recipient.agent_id
        )
    if reading.archived:
        raise _archived(recipient.agent_id)
    if outcome.refusal is not None or reading.session_closed:
        raise _Refused(
            "recipient-cannot-be-resumed",
            outcome.refusal or "The host left the recipient's session closed.",
            "Nothing was relaunched. Tell the developer in your own chat; a new agent comes "
            f"only from a new {ROLE_START_TOOL}.",
            recipientAgentId=recipient.agent_id,
        )


def _undelivered(agent_id: str, delivery: dict[str, Any]) -> _Refused:
    reason = delivery.get("refused")
    detail = str(delivery.get("detail") or "")
    if reason == "busy":
        return _Refused(
            "recipient-busy",
            f"Agent {agent_id} is busy and the message was not delivered: {detail}",
            "Send the message again later; the recipient's turn was left running.",
            recipientAgentId=agent_id,
        )
    if reason == "archived":
        return _archived(agent_id)
    if reason == "closed":
        return _Refused(
            "recipient-cannot-be-resumed",
            f"The session of agent {agent_id} closed again before the message was delivered.",
            "Send the message again; if it repeats, tell the developer in your own chat.",
            recipientAgentId=agent_id,
        )
    return _not_found(f"The host has no agent {agent_id}.", recipientAgentId=agent_id)


def _wait(
    config: McpRuntimeConfig,
    agent_id: str,
    turn_id: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Wait for the turn that consumed the message, as a sequence of bounded bridge calls."""

    started = _monotonic()
    deadline = started + timeout_seconds

    def waited() -> int:
        return int(_monotonic() - started)

    def later(reason: str) -> dict[str, Any]:
        return {
            "status": "timeout",
            "detail": f"{reason} The message stays delivered; the reply must be read later: the "
            f"recipient can answer with {ROLE_MESSAGE_TOOL}, or ask it again.",
            "waitedSeconds": waited(),
        }

    while True:
        remaining = deadline - _monotonic()
        if remaining <= 0:
            return later(f"The recipient's turn did not end within {timeout_seconds} seconds.")
        try:
            reply = bridge_call(
                config,
                "agent-wait",
                {
                    "agentId": agent_id,
                    **({"turnId": turn_id} if turn_id else {}),
                    "waitMs": max(1, int(min(remaining, WAIT_SLICE_SECONDS) * 1000)),
                },
            )
        except PaseoBridgeFailure as error:
            return later(f"The wait ended early because the host gave no answer ({error.code}).")
        answer = reply.get("wait")
        state = answer.get("state") if isinstance(answer, dict) else None
        if state == "running":
            continue
        if not isinstance(answer, dict) or state not in {"permission", "ended", "unavailable"}:
            return later("The wait ended early because the host's answer could not be read.")
        return {**_turn_result(answer), "waitedSeconds": waited()}


def _turn_result(answer: dict[str, Any]) -> dict[str, Any]:
    """The result of a wait that ended: the turn's outcome, or the pending permission."""

    state = answer["state"]
    if state == "permission":
        name = str(answer.get("permission") or "a tool")
        return {
            "status": "permission-pending",
            "detail": f"The recipient waits for a permission decision: {name}. The developer "
            "answers it in the recipient's chat.",
            "permission": name,
        }
    if state == "unavailable":
        return {
            "status": "turn-cancelled",
            "detail": "The recipient's turn did not finish: during the wait the agent became "
            f"{_UNAVAILABLE.get(str(answer.get('reason')), 'unavailable')}.",
        }
    text = answer.get("text")
    reply = {
        "text": text if isinstance(text, str) else None,
        "textTruncated": answer.get("textTruncated") is True,
    }
    outcome = answer.get("outcome")
    if outcome == "finished":
        return {
            "status": "turn-finished",
            "detail": "The recipient's turn finished; text is its final text. A finished turn "
            "is not AR acceptance of any requirement.",
            **reply,
        }
    if outcome == "failed":
        return {
            "status": "turn-failed",
            "detail": f"The recipient's turn failed: {answer.get('error') or 'no reason given'}",
            **reply,
        }
    return {
        "status": "turn-cancelled",
        "detail": "The recipient's turn was cancelled before it gave a final text.",
        **reply,
    }


_UNAVAILABLE = {
    "not-found": "unknown to the host",
    "archived": "archived",
    "closed": "a closed session",
}
