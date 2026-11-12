"""Single-role and leaf-set starts through the shared launcher path."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from agents_remember.application.agent_binding import AgentBinding
from agents_remember.cli import role_agent_override as launch_overrides
from agents_remember.cli.paseo_bridge import (
    BRIDGE_REFUSED,
    RUNTIME_NOT_CONFIGURED,
    PaseoBridgeFailure,
)
from agents_remember.cli.paseo_catalog import launcher_catalog_question
from agents_remember.cli.paseo_role_caller import (
    _binding_absent,
    _caller,
    _host_unreachable,
    sender_line,
    starting_agent,
)
from agents_remember.cli.role_launch_preparation import ROLE_START_TOOL
from agents_remember.cli.role_launch_receipts import _read_receipt, _receipt_path
from agents_remember.cli.role_launch_request_rules import with_caller_harness
from agents_remember.cli.role_launch_routes import (
    SET_SEAT_ROLES,
    LaunchLockBusy,
    _role_launch_dispatch_endpoint,
    dispatch_leaf_set,
)
from agents_remember.cli.role_message_delivery import _Refused
from agents_remember.cli.role_start_answer import (
    _launch_refused,
    _started_agent_id,
    second_copy_loaded,
    started_answer,
)
from agents_remember.errors import RolePreparationError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_agents import RoleStartCall, RoleStartSetCall
from agents_remember.models.role_identity import ROLE_ALIAS_NOTICE, canonical_role
from agents_remember.models.role_launcher import RoleDispatchRequest, RoleSelection
from agents_remember.models.task_document_ref import TaskDocumentRef

# Concurrent starts from one tool server share the launcher's transaction lock.
LOCK_WAIT_SECONDS = 60

# Only supervising roles may start another role.
MAY_START: dict[str, tuple[str, ...]] = {
    "architect": ("investigator", "orchestrator", "manager", "worker", "reviewer", "curator"),
    "orchestrator": ("manager", "worker", "reviewer", "curator", "investigator"),
    "manager": ("worker", "reviewer", "curator", "investigator"),
}


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


@launcher_catalog_question()
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


@launcher_catalog_question()
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
                "callerHarness": call.caller_harness,
                "stepUp": call.step_up,
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
    # One fresh Paseo question is shared only within this start operation.
    # MIK-R196 rule 1: a free-agent model-only choice is bound to the caller's harness and
    # validated with the selection owner here, so an unbindable or unoffered model is refused
    # before the dispatch path prepares anything.
    request, not_offered = launch_overrides.bound_to_caller_harness(
        config, request, with_caller_harness
    )
    if not_offered is not None:
        raise _launch_refused(not_offered)
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
    # MIK-R196 rule 1: the free agent's model-only choice binds the caller's harness, so the
    # override rule lives with the binding it depends on.
    return launch_overrides.agent_override(call)


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


def _started(config: McpRuntimeConfig, request: RoleDispatchRequest) -> dict[str, Any]:
    return started_answer(config, request)
