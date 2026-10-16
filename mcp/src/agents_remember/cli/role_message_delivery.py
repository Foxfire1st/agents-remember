"""Deliver one role message: resolve, file, hand over, and optionally wait (MIK-R117).

This module owns message preparation, filing/reply and successor
responsibilities behind the stable ``send_role_message`` facade in
:mod:`agents_remember.cli.paseo_role_tools` (L117-C1-F04). It imports nothing
from that facade module: shared names live here and the facade re-exports
the ones its callers need.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from pydantic import ValidationError

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
    PaseoBridgeFailure,
    bridge_call,
)
from agents_remember.cli.paseo_role_wait import SentMessage, wait_for_turn
from agents_remember.cli.paseo_status import read_agent
from agents_remember.cli.role_activity import resume_role_agent
from agents_remember.cli.role_launch_preparation import (
    ROLE_START_TOOL,
    _verify_leaf_revival_scope,
)
from agents_remember.cli.role_launch_receipts import (
    EXECUTIONS_DIRECTORY,
    _read_receipt,
    _receipt_path,
    _taskless_execution_receipts,
)
from agents_remember.cli.role_message_files import (
    MessageContent,
    MessageFileError,
    ensure_messages_link,
    file_line,
    leaf_messages_folder,
    mark_undelivered,
    narrowed_scope,
    record_attachments,
    render_message_file,
    sha256_file,
    store_message_file,
    utc_stem,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_agents import MAX_WAIT_SECONDS, RoleMessageCall
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.tool_events import call_target_role
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology

if TYPE_CHECKING:
    from agents_remember.application.agent_binding import AgentBinding
    from agents_remember.cli.paseo_launch import StartingAgent


SEND_OUTCOME_UNKNOWN = "paseo_send_outcome_unknown"
# How many executions of a selection are asked about when a role is addressed. More than that
# are never chosen among: the address is refused as ambiguous.
_CANDIDATE_LIMIT = 24
# Receipt statuses of an execution that has no live agent to ask about.
_NO_AGENT_STATUSES = frozenset({"stopped", "rejected"})
# The receipt statuses of a start that has not answered yet, or answered without a result.
_UNFINISHED_START_STATUSES = frozenset({"starting", "unknown"})
MAY_START: dict[str, tuple[str, ...]] = {
    "architect": ("investigator", "orchestrator", "manager", "worker", "reviewer", "curator"),
    "orchestrator": ("manager", "worker", "reviewer", "curator", "investigator"),
    "manager": ("worker", "reviewer", "curator", "investigator"),
}


@dataclass(frozen=True, slots=True)
class _Recipient:
    agent_id: str
    receipt: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _MessageParties:
    """The sender, the resolved recipient, and the sender's own receipt."""

    binding: AgentBinding
    sender: StartingAgent
    recipient: _Recipient
    own_receipt: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class _FiledMessage:
    """A delivered message's file: where it lies and whom it went between."""

    folder: Path
    sender: StartingAgent
    recipient_role: str
    recipient_id: str
    recipient_task: str
    message_id: str


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


def sender_line(sender: StartingAgent) -> str:
    """The line that opens every delivered message."""

    return f"From {sender.role} · {sender.subject} · agent {sender.agent_id}"


def _ref_key(reference: Any) -> str | None:
    """The comparison key of one task reference, object or mapping, if it has one."""
    if reference is None:
        return None
    if isinstance(reference, dict):
        repository, path = reference.get("repository"), reference.get("path")
    else:
        repository, path = getattr(reference, "repository", None), getattr(reference, "path", None)
    if not isinstance(repository, str) or not isinstance(path, str):
        return None
    return f"{repository}/{path}"


def _binding_refs(binding: AgentBinding) -> dict[str, str | None]:
    return {
        "leaf": _ref_key(binding.task_ref),
        "master": _ref_key(binding.master_ref),
        "sprint": _ref_key(binding.sprint_ref),
    }


def _receipt_refs(receipt: Mapping[str, Any]) -> dict[str, str | None]:
    selection = receipt.get("selection")
    if not isinstance(selection, Mapping):
        return {"leaf": None, "master": None, "sprint": None}
    refs = (
        selection.get("taskDocumentRef"),
        selection.get("masterDocumentRef"),
        selection.get("sprintDocumentRef"),
    )
    names = ("leaf", "master", "sprint")
    return {name: _ref_key(ref) for name, ref in zip(names, refs, strict=True)}


def _task_dir_of(config: McpRuntimeConfig, key: str) -> Path:
    """The task directory that holds the document named by one reference key."""
    repository, _, path = key.partition("/")
    reference = TaskDocumentRef(repository=repository, path=path)
    try:
        return TaskDocumentTopology(config.coordination_root).resolve(reference).path.parent
    except TaskDocumentRefError as error:
        raise MessageFileError(
            f"The message folder cannot be resolved: the task document {key} "
            f"cannot be read ({error}). Nothing was written and nothing was delivered."
        ) from error


def _message_scope(config: McpRuntimeConfig, parties: _MessageParties) -> Any:
    """The narrowed folder decision for one message (MIK-R117 rule 5)."""
    try:
        return narrowed_scope(
            coordination_root=config.coordination_root,
            sender_refs=_binding_refs(parties.binding),
            recipient_refs=_receipt_refs(parties.recipient.receipt),
            sender_role=parties.binding.role,
            task_dir_of=lambda key: _task_dir_of(config, key),
        )
    except MessageFileError as error:
        raise _Refused(
            "message-file-failed",
            str(error),
            "Restore the unreadable task document, then send the message again.",
        ) from error


def _recipient_subject(config: McpRuntimeConfig, recipient: _Recipient) -> str:
    """The task the recipient serves, as the message file records it."""
    refs = _receipt_refs(recipient.receipt)
    key = refs.get("leaf") or refs.get("master") or refs.get("sprint")
    if key is None:
        return "Projects"
    try:
        repository, _, path = key.partition("/")
        document = (
            TaskDocumentTopology(config.coordination_root)
            .resolve(TaskDocumentRef(repository=repository, path=path))
            .document
        )
        return document.id
    except TaskDocumentRefError:
        return key


def _may_start_role(caller_role: str, target_role: str) -> bool:
    """Whether a caller of one role may start another (PNT-R06 item 4)."""
    return target_role in MAY_START.get(canonical_role(caller_role), ())


def _start_again_next_action(caller_role: str, target_role: str, work: str) -> str:
    """The next action where a new agent is needed, naming only allowed starts."""
    if _may_start_role(caller_role, target_role):
        return (
            f"Start the role again with {ROLE_START_TOOL} {work}, or ask the parent "
            "with role_message on agents-remember-task; without a parent, ask the "
            "developer in your own chat."
        )
    return (
        "Ask the parent with role_message on agents-remember-task to start the role "
        f"again {work}; without a parent, ask the developer in your own chat."
    )


# ---------------------------------------------------------------------------------------------
# Role start
# ---------------------------------------------------------------------------------------------


def _host_unreachable(detail: str) -> _Refused:
    return _Refused(
        "host-unreachable",
        f"The Paseo runtime cannot be reached. {detail}",
        "Tell the developer in your own chat that the Paseo runtime cannot be reached; repeat "
        "the call after runtime_install (if not installed) and agents-remember dashboard --daemon.",
    )


def _refuse_over_limit(call: RoleMessageCall, limit: int) -> None:
    """Refuse a text over the settings' limit (MIK-R117 rule 1).

    Only ``text`` counts: neither the sender line nor the file line. The
    refusal names the count, the limit, and the two inputs that carry the
    full text instead (``body`` and ``attach``).
    """

    count = len(call.text)
    if count <= limit:
        return
    raise _Refused(
        "message-too-long",
        f"The message holds {count} characters, over the limit of {limit}. "
        "Put the full text in body (any length, filed with the message) or attach "
        "its files with attach, and keep text to what wakes the recipient.",
        "Send the message again with a shorter text and the full text in body or attach.",
        messageLength=count,
        messageLimit=limit,
    )


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
        _recipient_by_selection(config, call, binding)
        if by_selection
        else _recipient_by_id(config, call)
    )
    if recipient.agent_id == binding.agent_id:
        raise _not_found("The recipient this resolves to is the calling agent itself.")
    if recipient.receipt.get("status") in _UNFINISHED_START_STATUSES:
        # Until its start has answered, an agent may not have its first message; a message
        # that reaches it first would take that message's place.
        raise _Refused(
            "recipient-busy",
            f"Agent {recipient.agent_id} is not ready for a message: its start has not finished "
            f"(execution status {recipient.receipt.get('status')}).",
            "Send the message again once the start of that agent has answered running. A start "
            f"that answered unknown is repeated with {ROLE_START_TOOL} by the agent that made it.",
            recipientAgentId=recipient.agent_id,
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


def _archived_for(caller_role: str, recipient_role: str, agent_id: str) -> _Refused:
    """The archived refusal, naming a start only its caller may make (rule 9)."""
    return _Refused(
        "recipient-archived",
        f"Agent {agent_id} is archived. A message never un-archives an agent.",
        _start_again_next_action(caller_role, recipient_role, "if the work continues"),
        recipientAgentId=agent_id,
    )


def _live_successor(config: McpRuntimeConfig, recipient: _Recipient) -> str | None:
    """The live agent of a later execution of the archived recipient's selection.

    Where the recipient named by id is archived and a later execution of the
    same role on the same selection has a live agent, its id (rule 9).
    """
    selection_data = recipient.receipt.get("selection")
    if not isinstance(selection_data, dict):
        return None
    try:
        selection = RoleSelection.model_validate(selection_data)
    except ValidationError:
        return None
    try:
        receipts = _selection_receipts(config, selection)
    except (ValidationError, ValueError, TaskDocumentRefError, HTTPException):
        return None
    candidates = [r for r in receipts if r.get("agentId") != recipient.agent_id]
    live, _archived = _live_agents(config, candidates[:_CANDIDATE_LIMIT], first_only=True)
    return live[0].agent_id if live else None


def _archived_with_successor(config: McpRuntimeConfig, parties: _MessageParties) -> _Refused:
    """The archived refusal: the successor when one is live, else the gated one."""
    recipient = parties.recipient
    binding = parties.binding
    successor = _live_successor(config, recipient)
    if successor is not None:
        return _Refused(
            "recipient-archived",
            f"Agent {recipient.agent_id} is archived. The same role has a live "
            f"successor: agent {successor}. A message never un-archives an agent.",
            f"Send the message again to agent {successor}.",
            recipientAgentId=recipient.agent_id,
            successorAgentId=successor,
        )
    return _archived_for(binding.role, _recipient_role(recipient), recipient.agent_id)


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
    config: McpRuntimeConfig, call: RoleMessageCall, binding: AgentBinding
) -> _Recipient:
    """The live agent of the selection's executions; several live agents are never chosen among.

    A task-bound selection has one current execution; its agent is the recipient when it is live,
    and otherwise the most recent earlier execution whose agent is live. A taskless role can have
    several live agents; then the recipient is ambiguous. Of a taskless role every execution that
    is not already stopped or rejected is asked about; when there are more of those than are
    asked about, the address is ambiguous as well. The caller's own execution is no candidate:
    a role address means another agent of that role.
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
    own = [r for r in receipts if r.get("agentId") == binding.agent_id]
    receipts = [r for r in receipts if r.get("agentId") != binding.agent_id]
    if taskless:
        # An execution already stopped or rejected has no live agent to ask about.
        receipts = [r for r in receipts if r.get("status") not in _NO_AGENT_STATUSES]
    live, archived = _live_agents(config, receipts[:_CANDIDATE_LIMIT], first_only=not taskless)
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
        # By role there is no live agent to name instead: no live agent of the
        # selection exists, so the gated refusal stands (rule 9 names the
        # successor only where one is live).
        raise _archived_for(binding.role, selection.role, archived)
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


def _recipient_role(recipient: _Recipient, call: RoleMessageCall | None = None) -> str:
    """The recipient's role, from its durable receipt or the call that named it."""
    role = recipient.receipt.get("role")
    if isinstance(role, str) and role:
        return role
    if call is not None and isinstance(call.role, str) and call.role:
        return call.role
    return "agent"


def _workspace_path(receipt: Mapping[str, Any] | None) -> Path | None:
    if not isinstance(receipt, dict):
        return None
    workspace = receipt.get("workspace")
    if not isinstance(workspace, dict):
        return None
    path = workspace.get("path")
    if not isinstance(path, str) or not path:
        return None
    candidate = Path(path)
    return candidate if candidate.is_dir() else None


def _prepare_attachments(
    call: RoleMessageCall,
    parties: _MessageParties,
    scope: Any,
    stem: str,
) -> list[Any]:
    """The attachment records of one message, copied where the rule says (rule 3)."""
    attach = call.attach
    if attach is None:
        return []
    if isinstance(attach, str) or not isinstance(attach, (list, tuple)):
        raise _Refused(
            "message-file-failed",
            f"attach must be the paths of files that exist, not {attach!r}. "
            "Nothing was written and nothing was delivered.",
            "Send the message again with attach as a list of existing file paths.",
        )
    paths = list(attach)
    for entry in paths:
        if not isinstance(entry, str) or not entry:
            raise _Refused(
                "message-file-failed",
                f"attach must be the paths of files that exist, not {entry!r}. "
                "Nothing was written and nothing was delivered.",
                "Send the message again with attach as a list of existing file paths.",
            )
    roots = [scope.record_root]
    for receipt in (parties.own_receipt, parties.recipient.receipt):
        workspace = _workspace_path(receipt)
        if workspace is not None:
            roots.append(workspace.resolve())
    try:
        return record_attachments(scope.folder, stem, paths, record_roots=roots)
    except MessageFileError as error:
        raise _Refused(
            "message-file-failed",
            str(error),
            "Attach only paths of files that exist and can be read; a file outside "
            "the task's record and the leaf workspaces is copied into the folder.",
        ) from error


def _ensure_sender_link(config: McpRuntimeConfig, parties: _MessageParties) -> str | None:
    """Carry a leaf workspace's ``messages`` link to its leaf folder (rule 5)."""
    if parties.binding.task_ref is None:
        return None
    group = _workspace_path(parties.own_receipt)
    if group is None:
        return None
    try:
        refs = _binding_refs(parties.binding)
        folder = narrowed_scope(
            coordination_root=config.coordination_root,
            sender_refs=refs,
            recipient_refs=refs,
            sender_role=parties.binding.role,
            task_dir_of=lambda key: _task_dir_of(config, key),
        ).folder
    except MessageFileError as error:
        return f"The workspace messages link cannot be resolved ({error})."
    return ensure_messages_link(group, folder)


def _ensure_recipient_link(config: McpRuntimeConfig, parties: _MessageParties) -> str | None:
    """Link the receiving leaf workspace to its own leaf folder (F02).

    An old workspace that predates links gets it at its first inbound
    message. The link always names the recipient's own leaf folder — never
    the folder of one message — so a cross-level message neither steals nor
    warns about the leaf's link. Occupied non-links and divergent links are
    preserved with a warning, never replaced.
    """
    refs = _receipt_refs(parties.recipient.receipt)
    leaf = refs.get("leaf")
    if leaf is None:
        return None
    group = _workspace_path(parties.recipient.receipt)
    if group is None:
        return None
    try:
        folder = leaf_messages_folder(Path(_task_dir_of(config, leaf)), leaf)
    except MessageFileError as error:
        return f"The workspace messages link cannot be resolved ({error})."
    return ensure_messages_link(group, folder)


def _discard_copies(attachments: list[Any]) -> None:
    """Remove the attachment copies of one message that will not be delivered (F01)."""
    for record in attachments:
        copy = getattr(record, "copy", None)
        if copy:
            try:
                Path(copy).unlink(missing_ok=True)
            except OSError:
                continue


def _discard_message_files(path: Path, attachments: list[Any]) -> None:
    """Remove what one message wrote after a refusal before any delivery attempt."""
    for target in [path, *(Path(record.copy) for record in attachments if record.copy)]:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            continue


def _keep_refused_file(path: Path, refused: _Refused) -> None:
    """Keep a refused delivery's file and name it in the refusal (rule 2)."""
    final = path
    try:
        final = mark_undelivered(path, refusal=refused.refusal, refusal_detail=refused.detail)
    except OSError:
        final = path
    digest = ""
    with contextlib.suppress(OSError):
        digest = sha256_file(final)
    refused.detail = f"{refused.detail} The refused message is kept in {final.as_posix()}" + (
        f" (SHA-256 {digest})." if digest else "."
    )
    refused.fields["messageFile"] = final.as_posix()
    if digest:
        refused.fields["messageFileSha256"] = digest


@dataclass(frozen=True, slots=True)
class _MessageDraft:
    """Everything _store_message needs beyond the call, the parties, and the files."""

    folder: Path
    line: str
    stem: str
    when: datetime
    message_id: str
    recipient_role: str
    recipient_task: str


def _store_message(
    call: RoleMessageCall,
    parties: _MessageParties,
    draft: _MessageDraft,
    attachments: list[Any],
) -> tuple[Path, str, dict[str, Any]]:
    """File the message whole, then build the wake-up payload naming the file."""
    sender = parties.sender
    recipient = parties.recipient
    try:
        path = store_message_file(
            draft.folder,
            draft.stem,
            render_message_file(
                MessageContent(
                    when=draft.when,
                    sender_line=draft.line,
                    sender_task=sender.subject,
                    recipient_role=draft.recipient_role,
                    recipient_id=recipient.agent_id,
                    recipient_task=draft.recipient_task,
                    message_id=draft.message_id,
                    text=call.text,
                    body=call.body,
                    attachments=tuple(attachments),
                )
            ),
        )
    except MessageFileError as error:
        _discard_copies(attachments)
        raise _Refused(
            "message-file-failed",
            f"{error} Nothing was delivered.",
            "Resolve the reason named in detail, then send the message again.",
        ) from error
    digest = sha256_file(path)
    # One id for the message whatever the number of attempts: the runtime records it with the
    # message, and the wait finds the message by it.
    delivered = f"{draft.line}\n{call.text}"
    if call.body is not None or attachments:
        delivered += f"\n{file_line(path, digest)}"
    return (
        path,
        digest,
        {
            "agentId": recipient.agent_id,
            "text": delivered,
            "messageId": draft.message_id,
        },
    )


def _deliver(
    config: McpRuntimeConfig,
    call: RoleMessageCall,
    parties: _MessageParties,
    *,
    limit: int,
) -> dict[str, Any]:
    sender = parties.sender
    recipient = parties.recipient
    line = sender_line(sender)
    message_id = str(uuid.uuid4())
    recipient_role = _recipient_role(recipient, call)
    recipient_task = _recipient_subject(config, recipient)
    scope = _message_scope(config, parties)
    when = datetime.now(UTC)
    stem = utc_stem(sender.role, sender.agent_id, recipient_role, recipient.agent_id, when=when)
    attachments = _prepare_attachments(call, parties, scope, stem)
    link_warning = _ensure_sender_link(config, parties)
    recipient_warning = _ensure_recipient_link(config, parties)
    if recipient_warning:
        link_warning = (
            f"{link_warning} {recipient_warning}".strip() if link_warning else recipient_warning
        )
    path, digest, payload = _store_message(
        call,
        parties,
        _MessageDraft(scope.folder, line, stem, when, message_id, recipient_role, recipient_task),
        attachments,
    )
    resumed = False
    attempted = False
    try:
        with investigator_message_capacity(config, recipient.receipt):
            attempted = True
            delivery = _send(config, payload)
            if delivery.get("refused") == "closed":
                _resume(config, parties)
                resumed = True
                # A resumed session waits for its tool servers, as its first launch does.
                tool_server = recipient.receipt.get("toolServer")
                waits = isinstance(tool_server, dict) and tool_server.get("applied") is True
                delivery = _send(config, {**payload, "afterResume": True} if waits else payload)
    except HTTPException as error:
        _discard_message_files(path, attachments)
        raise _Refused(
            "investigator-capacity",
            str(error.detail),
            "Close or archive one Investigator execution on this selection before starting another turn.",
            recipientAgentId=recipient.agent_id,
        ) from error
    except _Refused as refused:
        if not attempted:
            _discard_message_files(path, attachments)
            raise
        _keep_refused_file(path, refused)
        raise
    if delivery.get("delivered") is not True:
        refusal = _delivery_refusal(config, parties, delivery)
        _keep_refused_file(path, refusal)
        raise refusal
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
        "messageFile": path.as_posix(),
        "messageFileSha256": digest,
        **({"resumed": True} if resumed else {}),
        **(
            {
                "warning": "The Paseo runtime cancelled the recipient's running turn to deliver "
                "this message although it was asked to hand the message to that turn."
            }
            if taken == "replaced"
            else {}
        ),
        **({"warning": link_warning} if link_warning else {}),
    }
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
    _file_long_reply(
        _FiledMessage(
            scope.folder,
            sender,
            recipient_role,
            recipient.agent_id,
            recipient_task,
            message_id,
        ),
        limit,
        waited,
        result,
    )
    return {**result, **waited}


def _file_long_reply(
    filed: _FiledMessage,
    limit: int,
    waited: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """File a wait reply longer than the limit whole (rule 6).

    The whole result keeps the full reply so the call's detail file holds
    it; the answer pipeline cuts it to its beginning with the location
    (MIK-R111 rule 5).
    """
    reply = waited.get("text")
    if not isinstance(reply, str) or len(reply) <= limit:
        return
    when = datetime.now(UTC)
    stem = utc_stem(
        filed.recipient_role,
        filed.recipient_id,
        filed.sender.role,
        filed.sender.agent_id,
        when=when,
    )
    content = render_message_file(
        MessageContent(
            when=when,
            sender_line=(
                f"From {filed.recipient_role} · {filed.recipient_task} · agent {filed.recipient_id}"
            ),
            sender_task=filed.recipient_task,
            recipient_role=filed.sender.role,
            recipient_id=filed.sender.agent_id,
            recipient_task=filed.sender.subject,
            message_id=str(uuid.uuid4()),
            text=(
                f"Reply to message {filed.message_id}; the whole reply is filed "
                f"here, the delivered answer carries its beginning."
            ),
            body=reply,
        )
    )
    try:
        path = store_message_file(filed.folder, stem, content)
        digest = sha256_file(path)
    except (MessageFileError, OSError) as error:
        waited["text"] = reply
        waited["textTruncated"] = True
        waited["detail"] = (
            f"{waited.get('detail', '')} The whole reply could not be filed ({error}); "
            f"it rides the call's detail file whole."
        )
        result["warning"] = (
            f"{result.get('warning', '')} The whole wait reply could not be filed "
            f"({error}).".strip()
        )
        return
    waited["text"] = reply
    waited["textTruncated"] = True
    waited["detail"] = (
        f"{waited.get('detail', '')} The whole reply is filed at {path.as_posix()} "
        f"(SHA-256 {digest})."
    )
    waited["messageFile"] = path.as_posix()
    waited["messageFileSha256"] = digest
    result["messageFile"] = path.as_posix()
    result["messageFileSha256"] = digest


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


def _resume(config: McpRuntimeConfig, parties: _MessageParties) -> None:
    """Resume the recipient's closed session, under the scope check a Revive applies."""

    recipient = parties.recipient
    binding = parties.binding
    receipt = recipient.receipt
    recipient_role = _recipient_role(recipient)
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
                "The recipient's task scope changed since its launch. "
                + _start_again_next_action(binding.role, recipient_role, "for the current scope"),
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
        raise _archived_with_successor(config, parties)
    if outcome.refusal is not None or reading.session_closed:
        if _may_start_role(binding.role, recipient_role):
            relaunch = f"A new agent comes only from a new {ROLE_START_TOOL}."
        else:
            relaunch = (
                "A new agent comes only from a new start, which your role may not make: "
                "ask the parent to start it."
            )
        raise _Refused(
            "recipient-cannot-be-resumed",
            outcome.refusal or "The host left the recipient's session closed.",
            "Nothing was relaunched. Tell your parent with role_message on agents-remember-task; "
            "without a parent, tell the developer in your own chat. " + relaunch,
            recipientAgentId=recipient.agent_id,
        )


def _delivery_refusal(
    config: McpRuntimeConfig,
    parties: _MessageParties,
    delivery: dict[str, Any],
) -> _Refused:
    recipient = parties.recipient
    agent_id = recipient.agent_id
    reason = delivery.get("refused")
    detail = str(delivery.get("detail") or "")
    if reason == "busy" and delivery.get("permissionPending") is True:
        # The host answers a pending permission with a denial when it delivers a message.
        name = delivery.get("permission")
        named = f" ({name})" if isinstance(name, str) and name else ""
        return _Refused(
            "recipient-busy",
            f"Agent {agent_id} waits for a permission decision{named}. A message would answer "
            "it with a denial, so the message was not delivered.",
            "The developer answers the permission in the recipient's chat; send the message "
            "again afterwards.",
            recipientAgentId=agent_id,
            **({"permission": name} if named else {}),
        )
    if reason == "busy":
        return _Refused(
            "recipient-busy",
            f"Agent {agent_id} is busy and the message was not delivered: {detail}",
            "Send the message again later; the recipient's turn was left running.",
            recipientAgentId=agent_id,
        )
    if reason == "archived":
        return _archived_with_successor(config, parties)
    if reason == "closed":
        return _Refused(
            "recipient-cannot-be-resumed",
            f"The session of agent {agent_id} closed again before the message was delivered.",
            "Send the message again; if it repeats, tell your parent with role_message on "
            "agents-remember-task; without a parent, tell the developer in your own chat.",
            recipientAgentId=agent_id,
        )
    return _not_found(f"The host has no agent {agent_id}.", recipientAgentId=agent_id)
