"""Read the selected document's launch records and verify the agents still exist.

A chat answer is made of a small part of a launch record (:func:`_chat_record`). A record that
does not hold that part in the shape a launch writes it is refused with HTTP 409, like a record
that cannot be read at all: it is never answered as a document without a chat.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field

from agents_remember.application.role_launch_context import (
    TASKLESS_ROLES,
    resolve_role_launch_context,
)
from agents_remember.cli.paseo_status import read_agent
from agents_remember.cli.role_agent_override import FREE_AGENT_ROLE
from agents_remember.cli.role_answers import alias_response
from agents_remember.cli.role_launch_receipts import (
    _read_receipt,
    _receipt_address_matches,
    _receipt_path,
    _taskless_execution_receipts,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.role_launcher import RoleResultRequest, RoleSelection

# How many launch records one read visits: the host is asked once per record it is shown.
_PAGE_SIZE = 3

# The document references of a stored selection, and the label each gives its agent.
_REFERENCE_LABELS = (
    ("sprintDocumentRef", "ar.sprint-ref"),
    ("masterDocumentRef", "ar.master-ref"),
    ("taskDocumentRef", "ar.task-ref"),
)


class DocumentChatsRequest(RoleSelection):
    offset: int = Field(default=0, ge=0)
    request_id: UUID | None = Field(default=None, alias="requestId")


def register_document_chat_route(app: FastAPI, config: McpRuntimeConfig) -> None:
    def endpoint(request: DocumentChatsRequest) -> JSONResponse:
        selection = RoleSelection.model_validate(
            request.model_dump(exclude={"offset", "request_id"})
        )
        resolve_role_launch_context(config, selection)
        return alias_response(
            request,
            JSONResponse(document_chats(config, selection, request.offset, request.request_id)),
        )

    app.add_api_route("/api/role-launch/document-chats", endpoint, methods=["POST"])


def _records(
    config: McpRuntimeConfig, selection: RoleSelection, request_id: UUID | None = None
) -> list[dict[str, Any]]:
    if request_id is not None:
        scope = RoleResultRequest.model_validate(
            {**selection.model_dump(), "request_id": request_id}
        )
        path = _receipt_path(config, scope, request_id if scope.role in TASKLESS_ROLES else None)
        receipt = _undamaged_receipt(path)
        if receipt is None:
            return []
        if not _receipt_address_matches(receipt, scope):
            raise HTTPException(
                409, "The requested role receipt belongs to another document or request."
            )
        return [receipt]

    roles = _primary_roles(selection)
    # One block per role, newest first (each role's own listing is), so a page can step over the
    # older records of a role it has already answered: L188-B08's continuation follows a role
    # block rather than a global position, and no page re-lists a seat an earlier page answered.
    return [receipt for role in roles for receipt in _role_records(config, selection, role)]


def _role_records(
    config: McpRuntimeConfig, selection: RoleSelection, role: str
) -> list[dict[str, Any]]:
    """The launch records of one role of this selection, or the refusal of another document's."""

    scope = RoleResultRequest.model_validate({**selection.model_dump(), "role": role})
    if role in TASKLESS_ROLES:
        return [receipt for _path, receipt in _taskless_execution_receipts(config, scope)]
    receipt = _undamaged_receipt(_receipt_path(config, scope))
    if receipt is None:
        return []
    if not _receipt_address_matches(receipt, scope):
        raise HTTPException(
            409, "The role receipt belongs to another document; reconcile it before starting."
        )
    return [receipt]


def _undamaged_receipt(path: Path) -> dict[str, Any] | None:
    """The launch record at ``path``, or ``None`` when there is none; a damaged one is refused.

    The refusal comes before the record's address is compared, which reads its role as text.
    """

    receipt = _read_receipt(path)
    if receipt is not None:
        _readable_chat_record(receipt)
    return receipt


def _primary_roles(selection: RoleSelection) -> tuple[str, ...]:
    """The roles whose chats belong to the selected document itself.

    The selection with no document at all is the free-agent one (MIK-R196 rule 1): the architect
    and the bootstrap seat a new user starts to set a repository up, each found through its own
    receipt rather than through a task document. A selection that names the free agent is that
    role's own seats: asking for the bootstrap chat must not answer with an architect's
    (L188-B08).
    """

    if selection.task_document_ref:
        return ("worker", "reviewer", "curator")
    if selection.master_document_ref:
        return ("manager",)
    if selection.sprint_document_ref:
        return ("orchestrator",)
    if canonical_role(selection.role) == FREE_AGENT_ROLE:
        return (FREE_AGENT_ROLE,)
    return ("architect", FREE_AGENT_ROLE)


def _chat_record(receipt: dict[str, Any]) -> dict[str, Any] | None:
    """The part of a launch record a chat answer is made of; ``None`` for a damaged record.

    Kept are the role, the stored selection, the creation time and, of the execution, its kind
    and the ids of its agent and its workspace. Nothing else of the record is held, so what is
    kept does not grow with the record's launch capsule.

    A record without an execution, with one of another kind, or with one that names no agent or
    no workspace has no hosted agent; that is no damage. Damaged is a record without a role, and
    one in which a part has another shape than a launch writes: a selection or an execution
    that is no object, a document reference that is not a repository and a path, or a role, a
    creation time, a kind or an id that is no text an answer can carry.
    """

    role = receipt.get("role")
    selection = receipt.get("selection")
    created = receipt.get("createdAt")
    host = _hosted_fields(receipt.get("execution"))
    if (
        host is None
        or not _is_text(role)
        or not (created is None or _is_text(created))
        or not _is_stored_selection(selection)
    ):
        return None
    return {"role": role, "selection": selection, "createdAt": created, "execution": host}


def _hosted_fields(execution: Any) -> dict[str, Any] | None:
    """What is kept of a record's execution; ``None`` when it or one of its ids is damaged."""

    if execution is None:
        return {}
    if not isinstance(execution, dict):
        return None
    kept = {key: execution.get(key) for key in ("kind", "agentId", "workspaceId")}
    if not all(value is None or _is_text(value) for value in kept.values()):
        return None
    return kept


def _is_stored_selection(selection: Any) -> bool:
    """Whether a record's selection is an object with a role of text and whole references."""

    return (
        isinstance(selection, dict)
        and _is_text(selection.get("role", ""))
        and all(_is_reference(selection.get(name)) for name, _label in _REFERENCE_LABELS)
    )


def _is_reference(ref: Any) -> bool:
    """Whether a stored selection names no document in this place, or one by repository and path."""

    return ref is None or (
        isinstance(ref, dict) and _is_text(ref.get("repository")) and _is_text(ref.get("path"))
    )


def _is_text(value: Any) -> bool:
    """Whether a value is text that an answer can carry: a string without an unpaired surrogate."""

    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _readable_chat_record(receipt: dict[str, Any]) -> dict[str, Any]:
    """The kept part of a launch record, or the refusal of a record that is damaged."""

    record = _chat_record(receipt)
    if record is None:
        raise HTTPException(
            409, "The role execution receipt is malformed; reconcile it before launching."
        )
    return record


def _hosted_agent_id(record: dict[str, Any]) -> str | None:
    """The agent of a kept launch record that the host can show in a workspace, else ``None``."""

    host = record["execution"]
    if host.get("kind") != "paseo-agent" or not host.get("agentId") or not host.get("workspaceId"):
        return None
    return host["agentId"]


def _document_agent(record: dict[str, Any]) -> dict[str, Any]:
    host = record["execution"]
    labels = {"ar.role": record["role"]}
    for name, label in _REFERENCE_LABELS:
        ref = record["selection"].get(name)
        if ref:
            labels[label] = ref["repository"] + "/" + ref["path"]
    return {
        "agentId": host["agentId"],
        "workspaceId": host["workspaceId"],
        "archivedAt": None,
        "createdAt": record["createdAt"],
        "labels": labels,
    }


def _block_end(records: list[dict[str, Any]], index: int) -> int:
    """The first index after ``index`` whose record names another role than this one."""

    role = str(records[index].get("role"))
    end = index + 1
    while end < len(records) and str(records[end].get("role")) == role:
        end += 1
    return end


def document_chats(
    config: McpRuntimeConfig, selection: RoleSelection, offset: int, request_id: UUID | None = None
) -> dict[str, Any]:
    records = _records(config, selection, request_id)
    agents = []
    asked: list[str] = []
    primary = _primary_roles(selection)
    listed_roles: set[str] = set()
    index = offset
    # Host reads are bounded per request: a page asks about at most this many seats, however many
    # records it steps over, and old archived Projects actors are walked in pages.
    while index < len(records) and len(asked) < _PAGE_SIZE:
        # A record of the Projects listing is checked for damage here, when the read reaches it.
        record = _readable_chat_record(records[index])
        role = str(record["role"])
        if len(primary) > 1 and role in listed_roles:
            # This page already answered this role (MIK-R196 rule 1): its older records add no
            # second seat, and stepping over them costs no host read.
            index += 1
            continue
        agent_id = _hosted_agent_id(record)
        if agent_id is None:
            index += 1
            continue
        asked.append(agent_id)
        reading = read_agent(config, agent_id)
        if not reading.reachable:
            return {"agents": [], "detail": reading.unreachable_reason, "unavailable": True}
        if not reading.found or reading.archived:
            index += 1
            continue
        agents.append(_document_agent(record))
        listed_roles.add(role)
        # The rest of this role's block is older records of the seat just answered: the next page
        # starts after them, so a continuation never re-lists a seat this one already answered.
        index = _block_end(records, index)
    if asked:
        # A clicked agent goes first in a warmup pass that is still running
        # (MIK-R145 rule 4): the click names the agent before its own chat is
        # drawn, so the pass loads it next instead of after the rest. The import
        # stays here because the warmup module reads this route's record helpers.
        from agents_remember.cli.paseo_warmup_state import TRACKER  # noqa: PLC0415

        TRACKER.promote(asked)
    # L188-B08: a page that left an eligible seat unlisted is continued from the record the walk
    # stopped on, even when it listed another role's seat. Stopping as soon as anything was found
    # answered a page of architect records as the whole listing, and the bootstrap seat behind
    # them was never reached.
    return {"agents": agents, "nextOffset": index if index < len(records) else None}
