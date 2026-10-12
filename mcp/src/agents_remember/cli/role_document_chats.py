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

from agents_remember.application.role_launch_context import resolve_role_launch_context
from agents_remember.cli.paseo_status import read_agent
from agents_remember.cli.role_answers import alias_response
from agents_remember.cli.role_launch_receipts import (
    _read_receipt,
    _receipt_address_matches,
    _receipt_path,
    _taskless_execution_receipts,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleResultRequest, RoleSelection

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
        path = _receipt_path(
            config, scope, request_id if scope.role in {"architect", "investigator"} else None
        )
        receipt = _undamaged_receipt(path)
        if receipt is None:
            return []
        if not _receipt_address_matches(receipt, scope):
            raise HTTPException(
                409, "The requested role receipt belongs to another document or request."
            )
        return [receipt]

    records = []
    for role in _primary_roles(selection):
        scope = RoleResultRequest.model_validate({**selection.model_dump(), "role": role})
        if role == "architect":
            records.extend(receipt for _, receipt in _taskless_execution_receipts(config, scope))
            continue
        receipt = _undamaged_receipt(_receipt_path(config, scope))
        if receipt is None:
            continue
        if not _receipt_address_matches(receipt, scope):
            raise HTTPException(
                409, "The role receipt belongs to another document; reconcile it before starting."
            )
        records.append(receipt)
    return records


def _undamaged_receipt(path: Path) -> dict[str, Any] | None:
    """The launch record at ``path``, or ``None`` when there is none; a damaged one is refused.

    The refusal comes before the record's address is compared, which reads its role as text.
    """

    receipt = _read_receipt(path)
    if receipt is not None:
        _readable_chat_record(receipt)
    return receipt


def _primary_roles(selection: RoleSelection) -> tuple[str, ...]:
    """The roles whose chats belong to the selected document itself."""

    if selection.task_document_ref:
        return ("worker", "reviewer", "curator")
    if selection.master_document_ref:
        return ("manager",)
    if selection.sprint_document_ref:
        return ("orchestrator",)
    return ("architect",)


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


def document_chats(
    config: McpRuntimeConfig, selection: RoleSelection, offset: int, request_id: UUID | None = None
) -> dict[str, Any]:
    records = _records(config, selection, request_id)
    agents = []
    # Host reads are bounded per request; old archived Projects actors are walked in pages.
    for receipt in records[offset : offset + 3]:
        # A record of the Projects listing is checked for damage here, when the read reaches it.
        record = _readable_chat_record(receipt)
        agent_id = _hosted_agent_id(record)
        if agent_id is None:
            continue
        reading = read_agent(config, agent_id)
        if not reading.reachable:
            return {"agents": [], "detail": reading.unreachable_reason, "unavailable": True}
        if not reading.found or reading.archived:
            continue
        agents.append(_document_agent(record))
        if not selection.sprint_document_ref:
            break
    next_offset = offset + 3 if not agents and offset + 3 < len(records) else None
    return {"agents": agents, "nextOffset": next_offset}
