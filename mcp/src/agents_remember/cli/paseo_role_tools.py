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

import logging
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from agents_remember.application.agent_binding import (
    AgentBinding,
)
from agents_remember.application.role_launch_context import (
    resolve_role_launch_context,
)
from agents_remember.cli.investigator_receipts import (
    investigator_message_capacity,
)
from agents_remember.cli.paseo_bridge import (
    BRIDGE_REFUSED,
    BRIDGE_TIMEOUT,
    SEND_OUTCOME_UNKNOWN,
    PaseoBridgeFailure,
    bridge_call,
)
from agents_remember.cli.paseo_catalog import runnable_harnesses
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.cli.paseo_role_caller import (
    EXPLAINER_SENDER,
    _host_unreachable,
    _message_caller,
    role_message_limit,
    sender_line,
    starting_agent,
)
from agents_remember.cli.paseo_role_recipients import (
    _archived,
    _line_receipts,
    _not_found,
    _resolve_recipient,
)
from agents_remember.cli.paseo_role_start import (
    MAY_START,
    _started,
    start_leaf_set,
    start_role,
    start_rule_violation,
)
from agents_remember.cli.paseo_role_wait import SentMessage, wait_for_turn
from agents_remember.cli.provider_limit_hold import PROVIDER_LIMIT_REASON, provider_limit_hold
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
    note_held_receipt,
    sender_terminal_lines,
    waiting_total,
    withdraw_message,
)
from agents_remember.cli.role_launch_preparation import (
    ROLE_START_TOOL,
    _verify_leaf_revival_scope,
)
from agents_remember.cli.role_launch_receipts import (
    _read_receipt,
    _receipt_path,
    _taskless_execution_receipts,
    _write_receipt,
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
from agents_remember.cli.role_start_answer import second_copy_loaded
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_agents import (
    MAX_WAIT_SECONDS,
    RoleMessageCall,
)
from agents_remember.models.role_identity import (
    LEAF_ROLES,
    ROLE_ALIAS_NOTICE,
    TASKLESS_ROLES,
)
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.observer.tool_events import call_target_role
from agents_remember.serving.projections.host_activity import agent_in_turn
from agents_remember.tasks.document_refs import TaskDocumentRefError
from agents_remember.worktrees.role_reports import (
    RoleReportCollectionError,
    collect_execution_reports,
)

# Existing role-tool imports remain a facade; each operation has one implementation.
__all__ = [
    "MAY_START",
    "_started",
    "role_message_limit",
    "runnable_harnesses",
    "second_copy_loaded",
    "send_role_message",
    "sender_line",
    "start_leaf_set",
    "start_role",
    "start_rule_violation",
    "starting_agent",
]

logger = logging.getLogger(__name__)


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
        binding = _message_caller(config, environment)
        limit = role_message_limit(config, binding)
        _refuse_over_limit(call, limit)
        own_path, own_receipt = _own_receipt(config, binding)
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


def _own_receipt(
    config: McpRuntimeConfig, binding: AgentBinding
) -> tuple[Path | None, dict[str, Any] | None]:
    """The sender's own execution receipt and its path; a leaf explainer has neither (D607)."""

    if binding.role == EXPLAINER_SENDER:
        return None, None
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
    return own_path, _read_receipt(own_path)


def _collect_own_reports(
    config: McpRuntimeConfig,
    binding: AgentBinding,
    own_path: Path | None,
    own_receipt: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Collect the sender's execution reports; failures ride along, never abort."""

    _ = config
    if own_path is None or not own_receipt or own_receipt.get("agentId") != binding.agent_id:
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
    # MIK-R190 rule 9: the one check, before anything reaches the host. A limited
    # recipient's message is held with the reason and the reset time, idle or not;
    # rule 10 delivers it after that moment. The presenter's own send is the one
    # path outside this (it holds no role seat).
    limit = provider_limit_hold(send.config, send.recipient.agent_id)
    if limit is not None:
        return _hold_for_seat(
            send,
            reason=PROVIDER_LIMIT_REASON,
            limited_until=str(limit["limitedUntil"]),
        )
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
            attempted = undelivered.fields.get("attemptedMessageId")
            return _hold_for_seat(
                send,
                reason=str(undelivered.fields["holdReason"]),
                permission=permission if isinstance(permission, str) else None,
                attempted_message_id=attempted if isinstance(attempted, str) else None,
            )
        if undelivered.refusal == "host-unreachable" and located is not None:
            # A host that did not answer delays the message, it does not lose it
            # (MIK-R171 rule 3): the waiting copy keeps the id of the attempt, so the
            # delivery under rule 1 goes out under an id the host may already record.
            return _hold_for_seat(
                send,
                reason="host-unreachable",
                detail=undelivered.detail,
                attempted_message_id=_filed_message_id(send),
            )
        raise


def _name_replacement(result: dict[str, Any], taken: str | None) -> None:
    """Name a replaced running turn plainly, the gap v2 rule 2 states in its text.

    A provider known to steer may still have its turn replaced at that moment, and a turn may
    begin between the read and the send. The delivery is real either way, and the answer says
    so rather than reporting a delivery that hides what happened to the turn.
    """

    if taken == "replaced":
        result["taken"] = "replaced"
        result["warning"] = (
            "The Paseo runtime replaced the recipient's running turn to take the message: the "
            "message is delivered, and that turn did not continue because it was cancelled."
        )


def _host_reports_a_turn(config: McpRuntimeConfig, agent_id: str) -> bool:
    """Whether the host's activity publication says this agent is in a turn (rule 2)."""

    return agent_in_turn(config, agent_id)


def _filed_message_id(send: _MessageSend) -> str | None:
    """The id this send used at the host, when the message was filed before it went out."""

    filed = send.filed
    value = filed.get("messageId") if isinstance(filed, dict) else None
    return value if isinstance(value, str) and value else None


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


def _hold_for_seat(  # noqa: PLR0913 -- each fact of the wait rides the take, per reason
    send: _MessageSend,
    *,
    reason: str,
    permission: str | None = None,
    detail: str = "",
    attempted_message_id: str | None = None,
    limited_until: str | None = None,
) -> dict[str, Any]:
    """Queue one message for its recipient and drain what waits (MIK-R100 rules 4/4a/4b).

    ``attempted_message_id`` names the host message id of a send that reached the host
    and was not answered for this message (a lost connection, a timeout, a turn the
    runtime replaced): the waiting copy keeps it, so a later delivery goes out under an
    id the host may already record and is delivered at most once (MIK-R171 rule 3).

    ``limited_until`` is rule 9's reset time when a provider limit is why the message
    waits; it is written on the waiting row itself, so the reason and the moment it
    names survive a restart together.
    """

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
                attempted_message_id=attempted_message_id,
                limited_until=limited_until,
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
    note_held_receipt(send.config, path)
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
        detail=str(drain.get("detail") or "") or detail,
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
        }
        _name_replacement(result, taken_word)
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
        "host-unreachable": "the Paseo runtime did not answer; the message is held, not lost",
        PROVIDER_LIMIT_REASON: "the recipient's provider usage limit holds it",
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
    if reason == PROVIDER_LIMIT_REASON:
        # MIK-R190 rule 9: the answer repeats the reset time the waiting row carries --
        # the row was written by this take, so the answer and the store agree. A later
        # limit-file read only adds the sentence explaining it.
        limit = provider_limit_hold(send.config, send.recipient.agent_id)
        result["limitedUntil"] = taken.get("limitedUntil") or (
            limit["limitedUntil"] if limit is not None else None
        )
        if limit is not None:
            result["detail"] = f"{result['detail']} {limit['detail']}"
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
    # MIK-R171 rule 2, second view: the host's own activity record is read before the
    # send, because the agent snapshot can lag a turn the host has already published.
    # When either view says a turn runs, nothing is sent and the message is held.
    if _host_reports_a_turn(config, recipient.agent_id):
        raise _Refused(
            "recipient-busy",
            f"Agent {recipient.agent_id} is in a turn the host's own activity record reports. "
            "The message was not sent; the product holds it and delivers it when that turn "
            "ends.",
            "Nothing has to be sent again. The queue delivers it after the turn ends.",
            recipientAgentId=recipient.agent_id,
            holdReason="recipient-in-turn",
        )
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
            "The recipient's running turn took the message up; nothing was replaced."
            if taken == "steered"
            else "The recipient started a turn with the message."
        ),
        "recipientAgentId": recipient.agent_id,
        "senderLine": line,
        "taken": "steered" if taken == "steered" else "started",
        **({"resumed": True} if resumed else {}),
    }
    _name_replacement(result, taken)
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
