"""Resolve role-message addresses against this line's execution receipts and live agents."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from agents_remember.application.agent_binding import AgentBinding
from agents_remember.application.role_launch_context import (
    resolve_role_launch_context,
    selection_binding,
)
from agents_remember.cli.investigator_receipts import open_investigator_receipts
from agents_remember.cli.paseo_role_caller import _host_unreachable
from agents_remember.cli.paseo_status import read_agent
from agents_remember.cli.role_launch_preparation import ROLE_START_TOOL
from agents_remember.cli.role_launch_receipts import (
    _read_receipt,
    _receipt_path,
    _taskless_execution_receipts,
)
from agents_remember.cli.role_message_delivery import _Recipient, _Refused
from agents_remember.controlplane.held_store_discovery import EXECUTIONS_DIRECTORY
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_agents import RoleMessageCall
from agents_remember.models.role_identity import LEAF_ROLES, TASKLESS_ROLES
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.tasks.document_refs import TaskDocumentRefError

# More candidates are refused as ambiguous rather than selected without a complete read.
_CANDIDATE_LIMIT = 24
_NO_AGENT_STATUSES = frozenset({"stopped", "rejected"})
_UNFINISHED_START_STATUSES = frozenset({"starting", "unknown"})


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
