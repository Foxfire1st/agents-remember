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

Every message holds at most the settings' limit of characters (``text`` counts; the sender
line and the file line do not). The tool writes one file per message before handing it to the
host (see :mod:`agents_remember.cli.role_message_files`): the message and, where given, the
full text from ``body`` and the records or copies of ``attach``. A refused delivery keeps its
file under a name ending in ``.undelivered.md``.

Every answer is a plain mapping in the shape of the tools' response models. A call that is not
carried out is a refusal that names its one reason.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from agents_remember.application.agent_binding import (
    AgentBinding,
    exclusion_keeps_copy_out,
    read_agent_binding,
)
from agents_remember.application.role_launch_context import (
    LEAF_ROLES,
    TASKLESS_ROLES,
    resolve_role_launch_context,
    selection_binding,
)
from agents_remember.cli.investigator_receipts import (
    investigator_message_capacity,
    open_investigator_receipts,
)
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
from agents_remember.cli.paseo_role_wait import SentMessage, wait_for_turn
from agents_remember.cli.paseo_status import read_agent
from agents_remember.cli.role_activity import resume_role_agent
from agents_remember.cli.role_held_messages import (
    MAX_HELD_PER_RECIPIENT,
    HeldMessage,
    HeldMessageCommitted,
    HeldStoreFull,
    _read_store_counts,
    apply_drain_to_receipt,
    drain_held,
    hold_message,
    sender_terminal_lines,
    waiting_total,
    withdraw_message,
)
from agents_remember.cli.role_instructions import launch_choice_metadata
from agents_remember.cli.role_launch_preparation import (
    ROLE_MESSAGE_TOOL,
    ROLE_START_TOOL,
    _verify_leaf_revival_scope,
)
from agents_remember.cli.role_launch_receipts import (
    EXECUTIONS_DIRECTORY,
    _read_receipt,
    _receipt_path,
    _taskless_execution_receipts,
    _write_receipt,
)
from agents_remember.cli.role_launch_routes import (
    SET_SEAT_ROLES,
    LaunchLockBusy,
    _role_launch_dispatch_endpoint,
    dispatch_leaf_set,
)
from agents_remember.cli.role_message_delivery import (
    _archived_with_successor,
    _ensure_recipient_link,
    _ensure_sender_link,
    _file_long_reply,
    _FiledMessage,
    _keep_refused_file,
    _message_scope,
    _MessageDraft,
    _MessageParties,
    _prepare_attachments,
    _Recipient,
    _recipient_role,
    _recipient_subject,
    _refuse_over_limit,
    _Refused,
    _store_message,
)
from agents_remember.cli.role_message_files import file_line, utc_stem
from agents_remember.errors import RolePreparationError
from agents_remember.kernel.agentic_settings import (
    AgenticSettingsError,
    load_agentic_settings,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_agents import (
    MAX_WAIT_SECONDS,
    RoleMessageCall,
    RoleStartCall,
    RoleStartSetCall,
)
from agents_remember.models.role_identity import ROLE_ALIAS_NOTICE, canonical_role
from agents_remember.models.role_launcher import RoleDispatchRequest, RoleSelection
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.tool_events import call_target_role
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.role_reports import (
    RoleReportCollectionError,
    collect_execution_reports,
)

# How long a start waits for another start of the same tool server to end: one agent issues
# its starts side by side, and the launch path runs them one at a time.
LOCK_WAIT_SECONDS = 60
# The bridge's code for a connection lost while a message was being sent.
SEND_OUTCOME_UNKNOWN = "paseo_send_outcome_unknown"
# How many executions of a selection are asked about when a role is addressed. More than that
# are never chosen among: the address is refused as ambiguous.
_CANDIDATE_LIMIT = 24
# Receipt statuses of an execution that has no live agent to ask about.
_NO_AGENT_STATUSES = frozenset({"stopped", "rejected"})
# The receipt statuses of a start that has not answered yet, or answered without a result.
_UNFINISHED_START_STATUSES = frozenset({"starting", "unknown"})
_PROJECTS = "Projects"

# Who may start what (PNT-R06 item 4); a role that is not listed may start none.
MAY_START: dict[str, tuple[str, ...]] = {
    "architect": ("investigator", "orchestrator", "manager", "worker", "reviewer", "curator"),
    "orchestrator": ("manager", "worker", "reviewer", "curator", "investigator"),
    "manager": ("worker", "reviewer", "curator", "investigator"),
}


# _Refused and _Recipient are imported from role_message_delivery above: the two
# definitions were identical, and refusals raised while filing must be the same
# class the send below catches.


@dataclass(frozen=True, slots=True)
class _MessageSend:
    """One message send: who sends what to whom, and what the sender takes along.

    ``located`` is the recipient's receipt path, receipt and working folder, or None
    when the recipient has no receipt with a store (then nothing can be held for it).
    ``filed`` is the filed message's record (path, SHA-256, message id, folder, the
    recipient's role and task, the text limit), or None when the message was held
    before it could be filed.
    """

    config: McpRuntimeConfig
    call: RoleMessageCall
    sender: StartingAgent
    recipient: _Recipient
    located: tuple[Path, dict[str, Any], str] | None
    withdrawn: str | None
    collection: dict[str, Any] | None
    filed: dict[str, Any] | None = None


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
        f"A chat without a role cannot use {ROLE_MESSAGE_TOOL}: the developer speaks with the "
        f"agent in the dashboard. Such a chat starts a role with {ROLE_START_TOOL}.",
    )


def _binding_absent(environment: Mapping[str, str] | None) -> bool:
    """The caller carries no binding at all, rather than a malformed one.

    Only a genuinely absent binding is a chat without a role (MIK-R113 rule 6);
    a binding that names an agent but does not fit its role's class stays refused.
    """

    try:
        return read_agent_binding(environment) is None
    except ValueError:
        return False


def starting_agent(config: McpRuntimeConfig, binding: AgentBinding) -> StartingAgent:
    """The caller as the runtime and a recipient see it: agent id, role, and the work it serves.

    The work is named by the id of the most specific task document of the binding, or
    ``Projects`` for a taskless role.
    """

    reference = binding.task_ref or binding.master_ref or binding.sprint_ref
    if reference is None:
        return StartingAgent(
            binding.agent_id,
            binding.role,
            _PROJECTS,
            binding.sprint_ref,
            binding.master_ref,
            binding.task_ref,
        )
    try:
        subject = TaskDocumentTopology(config.coordination_root).resolve(reference).document.id
    except TaskDocumentRefError:
        # The document is gone or unreadable; its reference still says which work is meant.
        subject = reference.key
    return StartingAgent(
        binding.agent_id,
        binding.role,
        subject,
        binding.sprint_ref,
        binding.master_ref,
        binding.task_ref,
    )


def sender_line(sender: StartingAgent) -> str:
    """The line that opens every delivered message."""

    return f"From {sender.role} · {sender.subject} · agent {sender.agent_id}"


def role_message_limit(config: McpRuntimeConfig, binding: AgentBinding) -> int:
    """The bound on the characters of one message text, read per call (MIK-R117).

    The number in force is the one the settings file names at the call; where
    the settings name none it is :data:`DEFAULT_ROLE_MESSAGE_LIMIT`. A changed
    number holds for the next call without a restart of a tool server.
    """

    reference = binding.task_ref or binding.master_ref or binding.sprint_ref
    repo_root = None
    if reference is not None:
        scope = config.repositories.get(reference.repository)
        if scope is not None:
            repo_root = scope.path
    try:
        settings = load_agentic_settings(config.coordination_root, repo_root=repo_root)
    except AgenticSettingsError as error:
        raise _Refused(
            "settings-invalid",
            f"The settings cannot be loaded: {error}",
            "Fix the named field in the named settings file; the next call reads it again.",
        ) from error
    return settings.role_message_limit


def start_rule_violation(binding: AgentBinding, selection: RoleSelection) -> tuple[str, str] | None:
    """The rule a start would violate, as (refusal, the rule in words); ``None`` when allowed."""

    allowed = MAY_START.get(canonical_role(binding.role), ())
    if selection.role not in allowed:
        offered = ", ".join(allowed) if allowed else "no role"
        return (
            "role-may-not-start-role",
            f"A {binding.role} may start {offered}; it may not start a {selection.role}. "
            "(An architect may start every role except architect; an orchestrator may start "
            "manager, worker, reviewer, curator and investigator; a manager may start worker, reviewer, "
            "curator and investigator; worker, reviewer, curator and investigator may start none.)",
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


def start_leaf_set(
    config: McpRuntimeConfig,
    call: RoleStartSetCall,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Start a leaf's Worker, Reviewer and Curator in one call (MIK-R100 rule 1)."""

    try:
        binding = _caller(config, environment)
        if binding.role != "manager":
            # Rule 1: the set is the owning Manager's transaction. Single-role
            # permission is unchanged; only the set is restricted.
            raise _Refused(
                "role-may-not-start-role",
                f"Only the leaf's owning Manager starts Worker, Reviewer and Curator in one "
                f"call (caller role: {binding.role}).",
                "Start single roles with role_start, or have the owning Manager start the set.",
            )
        selections = _set_selections(call)
        for selection in selections:
            violated = start_rule_violation(binding, selection)
            if violated is not None:
                raise _Refused(
                    violated[0],
                    violated[1],
                    "Start only roles this rule allows; anything else is the developer's or the "
                    "parent's to start.",
                )
        sender = starting_agent(config, binding)
        reviewer_opening = (
            f"{sender_line(sender)}\n{call.reviewer_message}" if call.reviewer_message else None
        )
        answer = dispatch_leaf_set(
            config,
            call,
            sender,
            reviewer_opening=reviewer_opening,
            lock_wait_seconds=LOCK_WAIT_SECONDS,
        )
        return _with_second_copies(config, answer, selections)
    except _Refused as refused:
        return {**refused.payload(), "requestId": str(call.request_id)}
    except LaunchLockBusy:
        return {
            "ok": False,
            "status": "refused",
            "refusal": "launch-refused",
            "detail": (
                "Another start of this tool server was still running after "
                f"{LOCK_WAIT_SECONDS} seconds, so this set was not begun. Nothing was recorded "
                "for this request."
            ),
            "nextAction": "Call role_start_set again with the same arguments; sets run one at a time.",
            "requestId": str(call.request_id),
        }


def _with_second_copies(
    config: McpRuntimeConfig, answer: dict[str, Any], selections: list[RoleSelection]
) -> dict[str, Any]:
    """Say for every seat that has an agent whether it carries the product's tools twice.

    Read from the seat's receipt, as a single start reads it (MIK-R132 rule 5). A seat whose
    receipt cannot be read, or names another agent, reads as loaded, like a receipt without a
    record of what the launch handed the host.
    """

    seats = answer.get("seats")
    if not isinstance(seats, dict):
        return answer
    for selection in selections:
        seat = seats.get(selection.role)
        if not isinstance(seat, dict) or not seat.get("agentId"):
            continue
        try:
            receipt = _read_receipt(_receipt_path(config, selection))
        except HTTPException:
            receipt = None
        seat["secondCopyLoaded"] = (
            receipt is None
            or _started_agent_id(receipt) != seat["agentId"]
            or second_copy_loaded(receipt)
        )
    return answer


def _set_selections(call: RoleStartSetCall) -> list[RoleSelection]:
    """The three seat selections of a set, validated before anything is prepared for them."""

    unknown = sorted(set(call.replaces or ()) - set(SET_SEAT_ROLES))
    if unknown:
        raise _launch_refused(
            f"The set names replacements for no seat of a leaf: {', '.join(unknown)}. "
            "A set replaces only worker, reviewer or curator."
        )
    try:
        return [
            RoleSelection.model_validate(
                {
                    "role": role,
                    "sprintDocumentRef": call.sprint_document_ref,
                    "masterDocumentRef": call.master_document_ref,
                    "taskDocumentRef": call.task_document_ref,
                }
            )
            for role in SET_SEAT_ROLES
        ]
    except (ValidationError, ValueError) as error:
        raise _launch_refused(f"The set selection is not valid: {error}") from error


def start_role(
    config: McpRuntimeConfig,
    call: RoleStartCall,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Start one role agent for the selection, as the launcher's Start does.

    A role agent's call carries that agent as its parent. A call from a tool
    server no role agent was started for -- a chat without a role -- starts
    through the same table as the dashboard's start (MIK-R113 rule 6): every
    role with its canonical selection, no replacement, no parent. The call
    chooses nothing a role agent's call cannot choose, and no permission mode.
    """

    try:
        binding: AgentBinding | None = _caller(config, environment)
    except _Refused as refused:
        if refused.refusal != "caller-has-no-binding" or not _binding_absent(environment):
            return {
                **refused.payload(),
                "requestId": str(call.request_id),
                "role": canonical_role(call.role),
            }
        binding = None
    try:
        result = _start_role(config, call, binding)
    except _Refused as refused:
        result = {
            **refused.payload(),
            "requestId": str(call.request_id),
            "role": canonical_role(call.role),
        }
    if call.role == "system-specialist":
        result["detail"] = str(result.get("detail", "")) + " " + ROLE_ALIAS_NOTICE
    return result


def _start_role(
    config: McpRuntimeConfig, call: RoleStartCall, binding: AgentBinding | None
) -> dict[str, Any]:
    try:
        request = RoleDispatchRequest.model_validate(
            {
                "role": call.role,
                "sprintDocumentRef": call.sprint_document_ref,
                "masterDocumentRef": call.master_document_ref,
                "taskDocumentRef": call.task_document_ref,
                "requestId": call.request_id,
                "agentOverride": _agent_override(call),
                "replacesAgentId": call.replaces_agent_id,
                "replacesInterruptTurn": call.replaces_interrupt_turn,
                "chatStarted": binding is None,
            }
        )
    except (ValidationError, ValueError) as error:
        raise _launch_refused(f"The start request is not valid: {error}") from error
    if binding is None:
        # MIK-R113 rule 6: no parent, so no replacement: only the agent that
        # started a seat, or the launcher, may replace its holder. The
        # parentless table itself is checked by the dispatch endpoint, through
        # the same table as the dashboard's start.
        if call.replaces_agent_id is not None:
            raise _Refused(
                "role-may-not-start-role",
                "A chat without a role may start an agent but may not replace the holder "
                "of a seat; only the agent that started the seat, or the launcher, may.",
                "Start again without a replacement, or replace the holder from the dashboard.",
            )
        starter = None
    else:
        violated = start_rule_violation(binding, request)
        if violated is not None:
            raise _Refused(
                violated[0],
                violated[1],
                "Start only roles this rule allows; anything else is the developer's or the "
                "parent's to start.",
            )
        starter = starting_agent(config, binding)
    # Agent, model and effort are checked against the catalog as the runtime reports it at the
    # time of this call; this process keeps no catalog between calls.
    forget_launcher_catalogs()
    try:
        response = _role_launch_dispatch_endpoint(
            config,
            request,
            started_by=starter,
            lock_wait_seconds=LOCK_WAIT_SECONDS,
        )
    except LaunchLockBusy as busy:
        raise _Refused(
            "launch-refused",
            f"Another start of this tool server was still running after {LOCK_WAIT_SECONDS} "
            "seconds, so this one was not begun. Nothing was recorded for this request.",
            f"Call {ROLE_START_TOOL} again with the same arguments; starts run one at a time.",
        ) from busy
    except HTTPException as error:
        raise _start_refusal(error) from error
    finally:
        forget_launcher_catalogs()
    unread = _host_not_read(response)
    if unread is not None:
        raise _host_unreachable(f"{unread} Nothing was recorded for this request.")
    return _started(config, request)


def _host_not_read(response: JSONResponse) -> str | None:
    """The start path's answer that the selection's agent could not be read, when it is that.

    A new start on a selection that has an execution needs that execution's current state; while
    the host gives no answer the start path refuses, marks the answer and records nothing.
    """

    try:
        answer = json.loads(bytes(response.body))
    except ValueError:
        return None
    if (
        isinstance(answer, dict)
        and answer.get("hostUnreachable") is True
        and "status" not in answer
    ):
        return str(answer.get("detail") or "")
    return None


def _agent_override(call: RoleStartCall) -> dict[str, Any] | None:
    if call.agent is None:
        if call.model is not None or call.effort is not None:
            raise ValueError("a model or an effort is given without the agent it belongs to")
        if not call.feature_values:
            return None
    return {
        **({"featureValues": call.feature_values} if call.feature_values else {}),
        **{
            key: value
            for key, value in (
                ("agentId", call.agent),
                ("modelId", call.model),
                ("effortId", call.effort),
            )
            if value is not None
        },
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
    if isinstance(cause, RolePreparationError):
        return _Refused(
            "launch-refused", str(cause), cause.next_action, preparationStatus=cause.status
        )
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


def _projects_preparation_note(receipt: dict[str, Any]) -> str:
    preparation = receipt.get("preparation")
    if isinstance(preparation, dict) and preparation.get("workspace") == "opened":
        return " Projects workspace was opened; the host does not report whether it was created."
    return ""


def _started_agent_id(receipt: dict[str, Any]) -> str | None:
    """The agent a saved receipt's execution names, as _public_execution reports it."""

    reference = receipt.get("execution")
    if isinstance(reference, dict):
        agent_id = reference.get("agentId")
        if isinstance(agent_id, str) and agent_id:
            return agent_id
    agent_id = receipt.get("agentId")
    return agent_id if isinstance(agent_id, str) and agent_id else None


def second_copy_loaded(receipt: dict[str, Any]) -> bool:
    """Whether the started agent carries the product's tools a second time.

    It follows from what the launch handed the host (MIK-R132 rule 5), never from the
    harness's name: the copy is kept out only by a record of the mode the host reads. A
    receipt without a record, and one with a record of an earlier build, which handed the
    host nothing, read as loaded. An agent that was given no task server carries no second
    copy. The record is read from the receipt, so a repeat answered from its receipt makes
    no new call.
    """

    replay = receipt.get("replayRequest")
    applied = replay.get("serverExclusion") if isinstance(replay, dict) else None
    if applied is None:
        # The receipt drops replayRequest after the launch; the record survives at its top.
        applied = receipt.get("serverExclusion")
    tool_server = receipt.get("toolServer")
    carries_task_server = not isinstance(tool_server, dict) or bool(tool_server.get("applied"))
    return carries_task_server and not exclusion_keeps_copy_out(applied)


def _started(config: McpRuntimeConfig, request: RoleDispatchRequest) -> dict[str, Any]:
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
    agent_id = _started_agent_id(receipt)
    launch = (
        "rejected"
        if execution == "rejected"
        else "unknown"
        if execution in {"starting", "unknown"}
        else "held"
        if execution == "held"
        else "running"
    )
    if execution == "stopped" and isinstance(agent_id, str):
        _refuse_agent_that_is_gone(config, agent_id, execution)
    report = receipt.get("report")
    artifact = receipt.get("handoverArtifact")
    next_action = {
        "running": None,
        "held": "The seat is held and takes its first turn with the first message addressed "
        "to it; no new start is needed.",
        "rejected": "The launch is closed and agentId names no agent that can be used. Resolve "
        "the reason in detail, then start again with a new request id.",
        "unknown": f"Call {ROLE_START_TOOL} again with the same request id and selection: it "
        "reconciles the same agent and never creates a second.",
    }[launch]
    result = {
        "ok": launch == "running",
        "status": launch,
        "detail": str(receipt.get("detail") or "") + _projects_preparation_note(receipt),
        "requestId": str(request.request_id),
        "role": request.role,
        "agentId": agent_id,
        "secondCopyLoaded": second_copy_loaded(receipt),
        "parentAgentId": receipt.get("parentAgentId"),
        "reportPath": report.get("path") if isinstance(report, dict) else None,
        "handoverArtifactPath": artifact.get("path") if isinstance(artifact, dict) else None,
        "executionStatus": execution,
        **({"preparation": receipt["preparation"]} if "preparation" in receipt else {}),
        "choices": launch_choice_metadata(receipt.get("choices", {})),
        **(
            {"taskDocumentFiles": receipt["taskDocumentFiles"]}
            if receipt.get("taskDocumentFiles")
            else {}
        ),
        **({"warning": receipt["warning"]} if receipt.get("warning") else {}),
        **({"nextAction": next_action} if next_action else {}),
    }
    return result


def _refuse_agent_that_is_gone(config: McpRuntimeConfig, agent_id: str, execution: str) -> None:
    """Refuse the repeat of a request whose agent is archived or unknown to the host.

    A ``stopped`` execution can also be one whose last turn was cancelled; that agent is live and
    the repeat answers ``running`` for it. Which of the two it is, the host says: one read.
    """

    reading = read_agent(config, agent_id)
    if not reading.reachable:
        raise _host_unreachable(
            f"{reading.unreachable_reason} Whether agent {agent_id} of this request still "
            "exists could not be read."
        )
    if reading.found and not reading.archived:
        return
    state = "is archived" if reading.found else "is unknown to the host"
    raise _Refused(
        "launch-refused",
        f"Agent {agent_id} of this request {state}; a repeat of the request id does not bring "
        "it back.",
        "Start again with a new request id.",
        agentId=agent_id,
        executionStatus=execution,
    )


# ---------------------------------------------------------------------------------------------
# Role message
# ---------------------------------------------------------------------------------------------


def _file_message(
    config: McpRuntimeConfig, call: RoleMessageCall, parties: _MessageParties, limit: int
) -> dict[str, Any]:
    """File one message before it reaches the host (MIK-R117 rules 2 to 4).

    The filing is the all-or-nothing transaction of role_message_delivery: the scope,
    the attachments, the workspace links and the staged write either all happen or a
    preparation failure refuses the send as message-file-failed before anything is
    delivered. The record carries the filed path, its SHA-256, the message id, the
    folder, the recipient's role and task, and the text limit.
    """

    scope = _message_scope(config, parties)
    sender = parties.sender
    recipient = parties.recipient
    when = datetime.now(UTC)
    message_id = str(uuid.uuid4())
    recipient_role = _recipient_role(recipient, call)
    recipient_task = _recipient_subject(config, recipient)
    stem = utc_stem(sender.role, sender.agent_id, recipient_role, recipient.agent_id, when=when)
    attachments = _prepare_attachments(call, parties, scope, stem)
    sender_warning = _ensure_sender_link(config, parties)
    recipient_warning = _ensure_recipient_link(config, parties)
    link_warning = recipient_warning
    if sender_warning:
        link_warning = (
            f"{sender_warning} {recipient_warning}".strip() if recipient_warning else sender_warning
        )
    path, digest, _payload = _store_message(
        call,
        parties,
        _MessageDraft(
            scope.folder,
            sender_line(sender),
            stem,
            when,
            message_id,
            recipient_role,
            recipient_task,
        ),
        attachments,
    )
    return {
        "path": path.as_posix(),
        "sha256": digest,
        "messageId": message_id,
        "folder": scope.folder.as_posix(),
        "recipientRole": recipient_role,
        "recipientTask": recipient_task,
        "limit": limit,
        "linkWarning": link_warning or "",
    }


def send_role_message(
    config: McpRuntimeConfig,
    call: RoleMessageCall,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Send one message to one role agent and, with ``wait``, return the turn's outcome."""

    sender: StartingAgent | None = None
    recipient: _Recipient | None = None
    filed: dict[str, Any] | None = None
    try:
        binding = _caller(config, environment)
        limit = role_message_limit(config, binding)
        _refuse_over_limit(call, limit)
        selection = RoleSelection.model_validate(
            dict(
                role=binding.role,
                sprintDocumentRef=binding.sprint_ref,
                masterDocumentRef=binding.master_ref,
                taskDocumentRef=binding.task_ref,
            )
        )
        own_path = _receipt_path(
            config,
            selection,
            uuid.UUID(binding.request_id) if binding.role in TASKLESS_ROLES else None,
        )
        own_receipt = _read_receipt(own_path)
        sender = starting_agent(config, binding)
        try:
            recipient = _resolve_recipient(config, call, binding)
        except _Refused as unresolved:
            if unresolved.refusal == "recipient-busy" and unresolved.fields.get("holdReason"):
                return _hold_for_unresolved(config, call, sender, unresolved)
            raise
        _carry_resolved_target_role(call, recipient)
        collection = _collect_own_reports(config, binding, own_path, own_receipt)
        located = _locate_recipient_store(config, call, recipient.agent_id)
        withdrawn, withdrawal_note = _withdraw_requested(config, call, sender, recipient, located)
        parties = _MessageParties(binding, sender, recipient, own_receipt)
        filed = _file_message(config, call, parties, limit)
        result = _deliver_or_hold(
            _MessageSend(config, call, sender, recipient, located, withdrawn, collection, filed)
        )
        if collection is not None:
            result["reportCollection"] = collection
        if withdrawn is not None:
            result["withdrawnHeldMessageId"] = withdrawn
        if withdrawal_note is not None and isinstance(result.get("detail"), str):
            result["detail"] = f"{result['detail']} {withdrawal_note}"
        result.update(_held_readback(located[0] if located else None, sender.agent_id))
    except _Refused as refused:
        if refused.refusal == "recipient-archived" and recipient is not None and sender is not None:
            parties = _MessageParties(binding, sender, recipient, own_receipt)
            refused = _archived_with_successor(config, parties)
        if filed is not None:
            _keep_refused_file(Path(filed["path"]), refused)
        result = _refusal_answer(config, call, sender, refused)
    if call.role == "system-specialist":
        prior = str(result.get("warning", ""))
        result["warning"] = f"{prior} {ROLE_ALIAS_NOTICE}".strip()
    return result


def _collect_own_reports(
    config: McpRuntimeConfig,
    binding: AgentBinding,
    own_path: Path,
    own_receipt: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Collect the sender's execution reports; failures ride along, never abort."""

    _ = config
    if not own_receipt or own_receipt.get("agentId") != binding.agent_id:
        return None
    try:
        collection = collect_execution_reports(own_receipt)
    except RoleReportCollectionError as error:
        collection = {"state": "failed", "detail": str(error), "sourcesPreserved": True}
        own_receipt["reportCollection"] = collection
    try:
        _write_receipt(own_path, own_receipt)
    except OSError as error:
        collection = {**collection, "recordError": str(error)}
    return collection


def _carry_resolved_target_role(call: RoleMessageCall, recipient: _Recipient) -> None:
    """Name the addressed role for the river from the resolved recipient.

    The payload builder only sees the call, whose ``role`` is absent when the
    recipient is addressed by agent id. The durable receipt that resolved the
    recipient already records its role, so the completed event carries that
    actual role for both addressing forms. Nothing is guessed: when the
    receipt names no role and the call names none, the event stays literal
    (no ``targetRole``). Refused calls never reach this point.
    """
    role = recipient.receipt.get("role")
    if not isinstance(role, str) or not role:
        role = call.role
    if isinstance(role, str) and role:
        call_target_role.set(role)


def _locate_recipient_store(
    config: McpRuntimeConfig, call: RoleMessageCall, agent_id: str
) -> tuple[Path, dict[str, Any], str] | None:
    """The current receipt and working folder of one recipient, when both name it.

    History receipts never locate a store: a replaced agent's message is refused as
    archived by the delivery, not held behind its successor.
    """

    candidates: list[tuple[Path, dict[str, Any]]] = []
    if call.role is not None:
        try:
            selection = RoleSelection.model_validate(
                {
                    "role": call.role,
                    "sprintDocumentRef": call.sprint_document_ref,
                    "masterDocumentRef": call.master_document_ref,
                    "taskDocumentRef": call.task_document_ref,
                }
            )
        except (ValidationError, ValueError):
            return None
        if selection.role in TASKLESS_ROLES:
            try:
                candidates = [
                    (path, receipt)
                    for path, receipt in _taskless_execution_receipts(config, selection)
                ]
            except (ValueError, TaskDocumentRefError, HTTPException):
                return None
        else:
            try:
                path = _receipt_path(config, selection)
                receipt = _read_receipt(path)
            except HTTPException:
                return None
            candidates = [(path, receipt)] if receipt is not None else []
    else:
        candidates = [
            (path, receipt)
            for path, receipt in _line_receipts(config)
            if path.parent.name != "history" and receipt.get("agentId") == agent_id
        ]
        if not candidates:
            return None
        # The current receipt first, as the recipient lookup prefers it.
        candidates.sort(key=lambda item: item[0].parent.name == "history")
    for path, receipt in candidates:
        if receipt.get("agentId") != agent_id:
            continue
        workspace = receipt.get("workspace")
        folder = workspace.get("path") if isinstance(workspace, dict) else None
        if isinstance(folder, str) and folder:
            return path, receipt, folder
    return None


def _withdraw_requested(
    config: McpRuntimeConfig,
    call: RoleMessageCall,
    sender: StartingAgent,
    recipient: _Recipient,
    located: tuple[Path, dict[str, Any], str] | None,
) -> tuple[str | None, str | None]:
    """End the sender's named waiting message as withdrawn, before the text is handled.

    Returns the withdrawn id with no note, or no id with the note when the message
    is already committed to delivery: the new text is still held behind it.
    """

    _ = config
    message_id = call.withdraw_held_message_id
    if not message_id:
        return None, None
    if located is None:
        raise _not_found(
            f"Agent {recipient.agent_id} keeps no held-message store to withdraw from.",
            recipientAgentId=recipient.agent_id,
        )
    path, _receipt, folder = located
    try:
        ended = withdraw_message(folder, recipient.agent_id, message_id, sender.agent_id)
    except ValueError as error:
        raise _not_found(str(error), recipientAgentId=recipient.agent_id) from error
    except HeldMessageCommitted as committed:
        return None, (
            f"The withdrawal of waiting message {committed.message_id} was refused: it is "
            "already committed to delivery and will be delivered once."
        )
    if ended is None:
        raise _not_found(
            f"No waiting message {message_id} of this sender for agent {recipient.agent_id}.",
            recipientAgentId=recipient.agent_id,
        )
    _record_held_lines(path, [ended], "withdrawn")
    return message_id, None


def _record_held_lines(path: Path, ended: list[dict[str, Any]], how: str) -> None:
    """Write terminal lines for messages ended outside a drain (withdrawal)."""

    from agents_remember.cli.role_held_messages import (  # noqa: PLC0415
        record_lines,
        recount_waiting_for,
    )

    try:
        current = _read_receipt(path)
    except HTTPException:
        return
    if current is None:
        return
    record_lines(current, ended, how)
    recount_waiting_for(current)
    with suppress(OSError):
        _write_receipt(path, current)


def _deliver_or_hold(send: _MessageSend) -> dict[str, Any]:
    """Deliver one message, or hold it behind what waits (MIK-R100 rules 4/4a/4b)."""

    located = send.located
    if located is not None and located[1].get("status") == "held":
        return _hold_for_seat(send, reason="held-seat-unopened")
    if located is not None and waiting_total(located[2], send.recipient.agent_id) > 0:
        # Nothing overtakes (MIK-R100 rule 4b): while anything is held for the
        # recipient, a further message is held behind it, even when the host
        # could take it at that moment.
        return _hold_for_seat(send, reason="held-behind-waiting")
    try:
        return _deliver(send.config, send.call, send.sender, send.recipient, send.filed)
    except _Refused as undelivered:
        if undelivered.refusal == "recipient-busy" and undelivered.fields.get("holdReason"):
            if located is None:
                raise
            permission = undelivered.fields.get("permission")
            return _hold_for_seat(
                send,
                reason=str(undelivered.fields["holdReason"]),
                permission=permission if isinstance(permission, str) else None,
            )
        raise


def _hold_for_unresolved(
    config: McpRuntimeConfig,
    call: RoleMessageCall,
    sender: StartingAgent,
    unresolved: _Refused,
) -> dict[str, Any]:
    """Hold a message whose recipient's start has not finished (MIK-R100 rule 4b)."""

    agent_id = str(unresolved.fields.get("recipientAgentId") or "")
    located = _locate_recipient_store(config, call, agent_id)
    if located is None:
        raise unresolved
    path, _receipt, _folder = located
    _ = path
    recipient = _Recipient(agent_id, _receipt)
    withdrawn, _withdrawal_note = _withdraw_requested(config, call, sender, recipient, located)
    return _hold_for_seat(
        _MessageSend(config, call, sender, recipient, located, withdrawn, None),
        reason="start-unfinished",
    )


def _hold_for_seat(
    send: _MessageSend, *, reason: str, permission: str | None = None
) -> dict[str, Any]:
    """Queue one message for its recipient and drain what waits (MIK-R100 rules 4/4a/4b)."""

    located = send.located
    assert located is not None, "held for a recipient without a store"
    path, _receipt, folder = located
    line = sender_line(send.sender)
    text = send.call.text
    if send.filed is not None and (send.call.body is not None or send.call.attach):
        # The whole text from body and the records or copies of attach ride in the file;
        # the held text names it so the recipient can read it on delivery (MIK-R117 rule 3).
        text += f"\n{file_line(Path(send.filed['path']), str(send.filed['sha256']))}"
    try:
        taken = hold_message(
            folder,
            send.recipient.agent_id,
            HeldMessage(
                sender_line=line,
                sender_agent_id=send.sender.agent_id,
                text=text,
                reason=reason,
                permission=permission,
            ),
        )
    except HeldStoreFull as full:
        raise _Refused(
            "recipient-busy",
            f"Agent {send.recipient.agent_id} already holds {MAX_HELD_PER_RECIPIENT} messages: "
            f"{full}",
            "Wait for a delivery or withdraw a waiting message; none was dropped.",
            recipientAgentId=send.recipient.agent_id,
        ) from full
    except ValueError as error:
        raise _not_found(str(error), recipientAgentId=send.recipient.agent_id) from error
    _note_waiting(path, taken["waitingTotal"])
    try:
        drain = drain_held(send.config, path, _receipt)
    except Exception as error:
        drain = {"outcome": "drain-failed", "detail": str(error)}
    outcome = str(drain.get("outcome") or "")
    if outcome in {"delivered", "opened"}:
        apply_drain_to_receipt(path, drain)
        return _held_then_delivered(send, taken, drain)
    if outcome == "partially-delivered":
        apply_drain_to_receipt(path, drain)
        return _held_partial_progress(send, taken, drain)
    if outcome == "recipient-gone":
        apply_drain_to_receipt(path, drain)
        raise _archived(send.recipient.agent_id)
    if outcome == "permission-pending" and send.call.wait:
        apply_drain_to_receipt(path, drain)
        return _held_wait_permission(send, drain)
    apply_drain_to_receipt(path, drain)
    drain_permission = drain.get("permission")
    final_reason = (
        reason if outcome in {"still-held", "drain-failed", "nothing-waiting"} else outcome
    )
    if final_reason == "permission-pending" and reason == "permission-pending":
        # Rule 4a names the permission as the refused send named it: the send met the
        # prompt first, so its naming (or "not supplied") stands over the drain's.
        final_permission = permission
    else:
        final_permission = drain_permission if isinstance(drain_permission, str) else permission
    return _held_answer(
        send,
        taken,
        reason=final_reason,
        permission=final_permission,
        detail=str(drain.get("detail") or ""),
    )


def _trigger_delivery(drain: dict[str, Any], taken: dict[str, Any]) -> dict[str, Any]:
    """The split part carrying the triggering message; the last part as fallback."""

    deliveries = [
        delivery for delivery in drain.get("deliveries", []) if isinstance(delivery, dict)
    ]
    mine = taken.get("heldMessageId")
    for delivery in deliveries:
        if mine in (delivery.get("ids") or []):
            return delivery
    return deliveries[-1] if deliveries else {}


def _held_partial_progress(
    send: _MessageSend, taken: dict[str, Any], drain: dict[str, Any]
) -> dict[str, Any]:
    """The held answer when only some waiting messages fit the host's admission.

    The delivered parts are recorded; the rest stay held with fresh counts, and
    nothing has to be sent again.
    """

    located = send.located
    assert located is not None, "held for a recipient without a store"
    mine, total = _read_store_counts(located[2], send.recipient.agent_id, send.sender.agent_id)
    fresh = {**taken, "waitingMine": mine, "waitingTotal": total}
    permission = drain.get("permission")
    return _held_answer(
        send,
        fresh,
        reason="still-held",
        permission=permission if isinstance(permission, str) else None,
        detail=str(drain.get("detail") or ""),
    )


def _held_then_delivered(
    send: _MessageSend, taken: dict[str, Any], drain: dict[str, Any]
) -> dict[str, Any]:
    """The answer when a drain delivered everything held, this message with it."""

    line = sender_line(send.sender)
    if drain.get("outcome") == "opened":
        result: dict[str, Any] = {
            "ok": True,
            "status": "accepted",
            "detail": str(drain.get("detail") or "The held seat opened."),
            "recipientAgentId": send.recipient.agent_id,
            "senderLine": line,
            "taken": "started",
            "openedHeldSeat": True,
        }
    else:
        delivery = _trigger_delivery(drain, taken)
        taken_word = delivery.get("taken")
        turn_id = delivery.get("turnId")
        message_id = delivery.get("messageId")
        result = {
            "ok": True,
            "status": "accepted",
            "detail": str(
                drain.get("detail") or "The held messages were delivered in one delivery."
            ),
            "recipientAgentId": send.recipient.agent_id,
            "senderLine": line,
            "taken": "steered" if taken_word == "steered" else "started",
            **({"turnId": turn_id} if isinstance(turn_id, str) else {}),
        }
        if taken_word == "replaced":
            result["warning"] = (
                "The Paseo runtime cancelled the recipient's running turn to deliver "
                "this message although it was asked to hand the message to that turn."
            )
    if send.collection is not None:
        result["reportCollection"] = send.collection
    if send.withdrawn is not None:
        result["withdrawnHeldMessageId"] = send.withdrawn
    if send.call.wait and drain.get("outcome") == "delivered" and isinstance(message_id, str):
        result.update(
            wait_for_turn(
                send.config,
                SentMessage(
                    send.recipient.agent_id,
                    message_id,
                    turn_id if isinstance(turn_id, str) else None,
                    steered=taken_word == "steered",
                ),
                min(max(1, send.call.timeout_seconds), MAX_WAIT_SECONDS),
            )
        )
    return result


def _held_wait_permission(send: _MessageSend, drain: dict[str, Any]) -> dict[str, Any]:
    """A wait on a held message answers permission-pending as today (rule 4a)."""

    name = drain.get("permission")
    permission = name if isinstance(name, str) and name else "a tool"
    result: dict[str, Any] = {
        "ok": True,
        "status": "permission-pending",
        "detail": (
            f"Agent {send.recipient.agent_id} waits for a permission decision: {permission}. "
            "The message stays held and is delivered when the prompt is answered; "
            "the reply is read later."
        ),
        "recipientAgentId": send.recipient.agent_id,
        "senderLine": sender_line(send.sender),
        "permission": permission,
    }
    if send.collection is not None:
        result["reportCollection"] = send.collection
    if send.withdrawn is not None:
        result["withdrawnHeldMessageId"] = send.withdrawn
    located = send.located
    assert located is not None, "held for a recipient without a store"
    result.update(_held_readback(located[0], send.sender.agent_id))
    return result


def _held_answer(
    send: _MessageSend,
    taken: dict[str, Any],
    *,
    reason: str,
    permission: str | None,
    detail: str,
) -> dict[str, Any]:
    """The held answer: what waits, why, and what the sender has waiting (rules 4a/4b)."""

    why = {
        "held-seat-unopened": "the seat is held and takes its first turn with its first message",
        "held-behind-waiting": "earlier messages still wait for the recipient",
        "start-unfinished": "the recipient's start has not finished",
        "permission-pending": "the recipient waits for a permission decision",
        "recipient-in-turn": "the recipient is in a turn the host cannot write into",
    }.get(reason, detail or reason)
    if reason == "permission-pending":
        # Rule 4a names the permission as the host names it, or "not supplied":
        # the reading's fallback word for an unnamed prompt counts as unnamed.
        if permission and permission != "a tool":
            why = f"the recipient waits for a permission decision ({permission})"
        else:
            why = "the recipient waits for a permission decision (not supplied)"
            permission = None
    result: dict[str, Any] = {
        "ok": True,
        "status": "held",
        "detail": (
            f"The message is held for agent {send.recipient.agent_id}: {why}. It is delivered "
            "when the recipient can be written to, in the order it was taken; nothing has "
            "to be sent again." + (f" The last delivery attempt said: {detail}" if detail else "")
        ),
        "recipientAgentId": send.recipient.agent_id,
        "senderLine": sender_line(send.sender),
        "heldReason": reason,
        "heldMessageId": taken.get("heldMessageId"),
        "waitingMine": taken.get("waitingMine"),
        "waitingTotal": taken.get("waitingTotal"),
    }
    if send.filed is not None:
        result["messageFile"] = send.filed["path"]
        result["messageFileSha256"] = send.filed["sha256"]
    if permission:
        result["permission"] = permission
    else:
        result["permission"] = None
    if send.withdrawn is not None:
        result["withdrawnHeldMessageId"] = send.withdrawn
    if send.collection is not None:
        result["reportCollection"] = send.collection
    located = send.located
    assert located is not None, "held for a recipient without a store"
    result.update(_held_readback(located[0], send.sender.agent_id))
    # waitingMine/Total from the take stay authoritative over a recount race.
    result["waitingMine"] = taken.get("waitingMine")
    result["waitingTotal"] = taken.get("waitingTotal")
    return result


def _refusal_answer(
    config: McpRuntimeConfig,
    call: RoleMessageCall,
    sender: StartingAgent | None,
    refused: _Refused,
) -> dict[str, Any]:
    """The refusal answer, with the sender's terminal lines when its recipient is gone."""

    result = refused.payload()
    if sender is not None and refused.refusal in {"recipient-archived", "recipient-not-found"}:
        result.update(_held_readback(_refusal_receipt_path(config, call, refused), sender.agent_id))
    return result


def _refusal_receipt_path(
    config: McpRuntimeConfig, call: RoleMessageCall, refused: _Refused
) -> Path | None:
    """The receipt holding a refused recipient's ends, for the sender's readback.

    Best-effort and read-only: when the recipient is archived or gone, the lines
    its receipt recorded are still the sender's terminal history with it. Nothing
    is sent and the recipient is not reopened.
    """

    agent_id = refused.fields.get("recipientAgentId")
    if isinstance(agent_id, str) and agent_id:
        fallback = None
        for path, receipt in _line_receipts(config):
            if receipt.get("agentId") == agent_id:
                if path.parent.name != "history":
                    return path
                fallback = fallback or path
        return fallback
    if call.role is None:
        return None
    try:
        selection = RoleSelection.model_validate(
            {
                "role": call.role,
                "sprintDocumentRef": call.sprint_document_ref,
                "masterDocumentRef": call.master_document_ref,
                "taskDocumentRef": call.task_document_ref,
            }
        )
        path = _receipt_path(config, selection)
    except (ValidationError, ValueError, HTTPException):
        return None
    return path if path.is_file() else None


def _held_readback(path: Path | None, sender_agent_id: str) -> dict[str, Any]:
    """What a sender reads about its held messages: its waiting ends and terminal lines.

    Empty readback adds no key: an ordinary delivery answers exactly as it did.
    """

    if path is None:
        return {}
    try:
        current = _read_receipt(path)
    except HTTPException:
        return {}
    if current is None:
        return {}
    recent = sender_terminal_lines(current, sender_agent_id)
    return {"recentTerminal": recent} if recent else {}


def _note_waiting(path: Path, total: int) -> None:
    """Recount the recipient's waiting messages after one was taken."""

    try:
        current = _read_receipt(path)
    except HTTPException:
        return
    if current is None:
        return
    current["heldWaiting"] = total
    with suppress(OSError):
        _write_receipt(path, current)


def _resolve_recipient(
    config: McpRuntimeConfig, call: RoleMessageCall, binding: AgentBinding
) -> _Recipient:
    by_selection = call.role is not None
    if (call.agent_id is not None) == by_selection:
        raise _not_found(
            "Name the recipient either with agent_id, or with role plus the task references its "
            "class requires (sprint_document_ref, master_document_ref, task_document_ref); not "
            "both and not neither. No other argument names a recipient."
        )
    recipient = (
        _recipient_by_selection(config, call, binding.agent_id)
        if by_selection
        else _recipient_by_id(config, call)
    )
    if recipient.agent_id == binding.agent_id:
        raise _not_found("The recipient this resolves to is the calling agent itself.")
    if recipient.receipt.get("status") in _UNFINISHED_START_STATUSES:
        # Until its start has answered, an agent may not have its first message; the
        # product holds the message and delivers it when the start has answered
        # running (MIK-R100 rule 4b), instead of refusing it as busy.
        raise _Refused(
            "recipient-busy",
            f"Agent {recipient.agent_id} is not ready for a message: its start has not finished "
            f"(execution status {recipient.receipt.get('status')}).",
            "The message is held and delivered once the start of that agent has answered "
            "running; nothing has to be sent again.",
            recipientAgentId=recipient.agent_id,
            holdReason="start-unfinished",
        )
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
        "parent with role_message on agents-remember-task; without a parent, ask the "
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


def _recipient_by_selection(
    config: McpRuntimeConfig, call: RoleMessageCall, caller_id: str
) -> _Recipient:
    """The live agent of the selection's executions; several live agents are never chosen among.

    A task-bound selection has one current execution; its agent is the recipient when it is live,
    and otherwise the most recent earlier execution whose agent is live -- except on a leaf's
    seat, where two live agents refuse with both ids instead (MIK-R100 rule 9: there is one
    resolver). A taskless role can have several live agents; then the recipient is ambiguous.
    Of a taskless role every execution that is not already stopped or rejected is asked about;
    when there are more of those than are asked about, the address is ambiguous as well. The
    caller's own execution is no candidate: a role address means another agent of that role.
    """

    try:
        selection = RoleSelection.model_validate(
            {
                "role": call.role,
                "sprintDocumentRef": call.sprint_document_ref,
                "masterDocumentRef": call.master_document_ref,
                "taskDocumentRef": call.task_document_ref,
            }
        )
        resolve_role_launch_context(config, selection)
        receipts = _selection_receipts(config, selection)
    except (ValidationError, ValueError, TaskDocumentRefError, HTTPException) as error:
        detail = error.detail if isinstance(error, HTTPException) else error
        raise _not_found(f"The recipient selection cannot be resolved: {detail}") from error
    taskless = selection.role in TASKLESS_ROLES
    own = [r for r in receipts if r.get("agentId") == caller_id]
    receipts = [r for r in receipts if r.get("agentId") != caller_id]
    if taskless:
        # An execution already stopped or rejected has no live agent to ask about.
        receipts = [r for r in receipts if r.get("status") not in _NO_AGENT_STATUSES]
    leaf_seat = not taskless and selection.role in LEAF_ROLES
    live, archived = _live_agents(
        config, receipts[:_CANDIDATE_LIMIT], first_only=not taskless and not leaf_seat
    )
    cut = taskless and len(receipts) > _CANDIDATE_LIMIT
    if len(live) > 1 or cut:
        ids = [recipient.agent_id for recipient in live]
        listed = f"{len(ids)} live {selection.role} agents match: {', '.join(ids)}."
        if cut:
            listed = (
                f"{len(receipts)} {selection.role} executions are not known to be stopped; only "
                f"the {_CANDIDATE_LIMIT} most recent were asked about, so the list was cut. Of "
                f"those, {len(ids)} are live{': ' + ', '.join(ids) if ids else ''}."
            )
        raise _Refused(
            "recipient-ambiguous",
            f"{listed} The tool never picks one.",
            "Send the message again addressed to the recipient by agent_id.",
            candidateAgentIds=ids,
        )
    if live:
        return live[0]
    if archived is not None:
        raise _archived(archived)
    other = " other than the calling agent" if own else ""
    raise _not_found(f"No live {selection.role} agent{other} is recorded for this selection.")


def _live_agents(
    config: McpRuntimeConfig, receipts: list[dict[str, Any]], *, first_only: bool
) -> tuple[list[_Recipient], str | None]:
    """The executions whose agent is live, and the first archived agent met on the way.

    Each agent is read once from the host; the read changes nothing. ``first_only`` stops at the
    first live agent, which for a task-bound selection is its open or most recent execution.
    """

    live: list[_Recipient] = []
    archived: str | None = None
    for receipt in receipts:
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


def _selection_receipts(config: McpRuntimeConfig, selection: RoleSelection) -> list[dict[str, Any]]:
    """The selection's executions, the current one first, then earlier ones newest first."""

    if selection.role == "investigator" and selection.sprint_document_ref is not None:
        return open_investigator_receipts(config, selection)
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
    filed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    line = sender_line(sender)
    # One id for the message whatever the number of attempts: the runtime records it with the
    # message, and the wait finds the message by it. A filed message already has its id from
    # the filing, so the file, the payload and the wait name the same message.
    message_id = str(filed["messageId"]) if filed is not None else str(uuid.uuid4())
    payload = {
        "agentId": recipient.agent_id,
        "text": f"{line}\n{call.text}",
        "messageId": message_id,
    }
    if filed is not None and (call.body is not None or call.attach):
        # The whole text from body and the records or copies of attach ride in the file;
        # the delivered text names it so the recipient can read it (MIK-R117 rule 3).
        payload["text"] += f"\n{file_line(Path(filed['path']), str(filed['sha256']))}"
    resumed = False
    try:
        with investigator_message_capacity(config, recipient.receipt):
            delivery = _send(config, payload)
            if delivery.get("refused") == "closed":
                _resume(config, recipient)
                resumed = True
                # A resumed session waits for its tool servers, as its first launch does.
                tool_server = recipient.receipt.get("toolServer")
                waits = isinstance(tool_server, dict) and tool_server.get("applied") is True
                delivery = _send(config, {**payload, "afterResume": True} if waits else payload)
    except HTTPException as error:
        raise _Refused(
            "investigator-capacity",
            str(error.detail),
            "Close or archive one Investigator execution on this selection before starting another turn.",
            recipientAgentId=recipient.agent_id,
        ) from error
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
    if filed is not None:
        result["messageFile"] = filed["path"]
        result["messageFileSha256"] = filed["sha256"]
        if filed.get("linkWarning"):
            prior = str(result.get("warning", ""))
            result["warning"] = f"{prior} {filed['linkWarning']}".strip()
    if not call.wait:
        return result
    waited = wait_for_turn(
        config,
        SentMessage(
            recipient.agent_id,
            message_id,
            turn_id if isinstance(turn_id, str) and turn_id else None,
            steered=taken == "steered",
        ),
        min(max(1, call.timeout_seconds), MAX_WAIT_SECONDS),
    )
    if filed is not None:
        # A wait reply longer than the limit is filed whole and cut in the answer (rule 6).
        _file_long_reply(
            _FiledMessage(
                Path(filed["folder"]),
                sender,
                str(filed["recipientRole"]),
                recipient.agent_id,
                str(filed["recipientTask"]),
                str(filed["messageId"]),
            ),
            int(filed["limit"]),
            waited,
            result,
        )
    return {**result, **waited}


def _send(config: McpRuntimeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    """One delivery attempt; a turn that changed under the send is tried once more."""

    for attempt in (1, 2):
        try:
            reply = bridge_call(config, "agent-send", payload)
        except PaseoBridgeFailure as error:
            if error.code == BRIDGE_REFUSED:
                if attempt == 1:
                    continue
                return {"delivered": False, "refused": "busy", "detail": str(error)}
            # A call that ran out of time, or lost its connection while the message was being
            # sent, may have delivered it.
            uncertain = (
                " It is not known whether the message was delivered."
                if error.code in {BRIDGE_TIMEOUT, SEND_OUTCOME_UNKNOWN}
                and "not known whether" not in str(error)
                else ""
            )
            raise _host_unreachable(f"{error.code}: {error}{uncertain}") from error
        delivery = reply.get("delivery")
        if not isinstance(delivery, dict):
            raise _host_unreachable("The Paseo bridge returned an unreadable delivery answer.")
        return delivery
    raise AssertionError("unreachable")


def _host_unreachable(detail: str) -> _Refused:
    return _Refused(
        "host-unreachable",
        f"The Paseo runtime cannot be reached. {detail}",
        "Tell the developer in your own chat that the Paseo runtime cannot be reached; repeat "
        "the call after runtime_install (if not installed) and agents-remember dashboard --daemon.",
    )


def _resume(config: McpRuntimeConfig, recipient: _Recipient) -> None:
    """Resume the recipient's closed session, under the scope check a Revive applies."""

    receipt = recipient.receipt
    if receipt.get("role") in LEAF_ROLES:
        try:
            selection = RoleSelection.model_validate(receipt.get("selection"))
            _verify_leaf_revival_scope(
                config, resolve_role_launch_context(config, selection), receipt
            )
        except (ValidationError, ValueError, TaskDocumentRefError) as error:
            raise _Refused(
                "scope-check-failed",
                str(error),
                f"The recipient's task scope changed since its launch. Start the role again "
                f"with {ROLE_START_TOOL} for the current scope, or ask your parent with "
                "role_message on agents-remember-task; without a parent, ask the developer "
                "in your own chat.",
                recipientAgentId=recipient.agent_id,
            ) from error
    outcome = resume_role_agent(config, receipt)
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
            "Nothing was relaunched. Tell your parent with role_message on agents-remember-task; "
            "without a parent, tell the developer in your own chat. A new agent comes "
            f"only from a new {ROLE_START_TOOL}.",
            recipientAgentId=recipient.agent_id,
        )


def _undelivered(agent_id: str, delivery: dict[str, Any]) -> _Refused:
    reason = delivery.get("refused")
    detail = str(delivery.get("detail") or "")
    if reason == "busy" and delivery.get("permissionPending") is True:
        # The host answers a pending permission with a denial when it delivers a message.
        # The product holds the message and delivers it when the prompt is answered
        # (MIK-R100 rule 4a); the guard that nothing is sent stays.
        name = delivery.get("permission")
        named = f" ({name})" if isinstance(name, str) and name else ""
        return _Refused(
            "recipient-busy",
            f"Agent {agent_id} waits for a permission decision{named}. The message was not "
            "delivered to the host; the product holds it and delivers it when the prompt "
            "is answered.",
            "Nothing has to be sent again. Only the developer can answer the prompt, in the "
            "recipient's chat.",
            recipientAgentId=agent_id,
            holdReason="permission-pending",
            **({"permission": name} if named else {}),
        )
    if reason == "busy":
        return _Refused(
            "recipient-busy",
            f"Agent {agent_id} is busy and the message was not delivered: {detail}",
            "The message is held and delivered when the recipient's turn ends; nothing has "
            "to be sent again.",
            recipientAgentId=agent_id,
            holdReason="recipient-in-turn",
        )
    if reason == "archived":
        return _archived(agent_id)
    if reason == "closed":
        return _Refused(
            "recipient-cannot-be-resumed",
            f"The session of agent {agent_id} closed again before the message was delivered.",
            "Send the message again; if it repeats, tell your parent with role_message on "
            "agents-remember-task; without a parent, tell the developer in your own chat.",
            recipientAgentId=agent_id,
        )
    return _not_found(f"The host has no agent {agent_id}.", recipientAgentId=agent_id)
