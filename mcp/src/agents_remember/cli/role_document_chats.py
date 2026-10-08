"""Read the selected document's launch records and verify the agents still exist."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import Field

from agents_remember.application.role_launch_context import resolve_role_launch_context
from agents_remember.cli.paseo_status import read_agent
from agents_remember.cli.role_launch_receipts import (
    _read_receipt,
    _receipt_address_matches,
    _receipt_path,
    _taskless_execution_receipts,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleResultRequest, RoleSelection


class DocumentChatsRequest(RoleSelection):
    offset: int = Field(default=0, ge=0)
    request_id: UUID | None = Field(default=None, alias="requestId")


def register_document_chat_route(app: FastAPI, config: McpRuntimeConfig) -> None:
    def endpoint(request: DocumentChatsRequest) -> dict[str, Any]:
        selection = RoleSelection.model_validate(
            request.model_dump(exclude={"offset", "request_id"})
        )
        resolve_role_launch_context(config, selection)
        return document_chats(config, selection, request.offset, request.request_id)

    app.add_api_route("/api/role-launch/document-chats", endpoint, methods=["POST"])


def _records(
    config: McpRuntimeConfig, selection: RoleSelection, request_id: UUID | None = None
) -> list[dict[str, Any]]:
    if request_id is not None:
        scope = RoleResultRequest.model_validate(
            {**selection.model_dump(), "request_id": request_id}
        )
        path = _receipt_path(
            config, scope, request_id if scope.role in {"architect", "system-specialist"} else None
        )
        receipt = _read_receipt(path)
        if receipt is None:
            return []
        if not _receipt_address_matches(receipt, scope):
            raise HTTPException(
                409, "The requested role receipt belongs to another document or request."
            )
        return [receipt]

    roles = (
        ("worker", "reviewer", "curator")
        if selection.task_document_ref
        else ("manager",)
        if selection.master_document_ref
        else ("orchestrator",)
        if selection.sprint_document_ref
        else ("architect",)
    )
    records = []
    for role in roles:
        scope = RoleResultRequest.model_validate({**selection.model_dump(), "role": role})
        if role == "architect":
            records.extend(receipt for _, receipt in _taskless_execution_receipts(config, scope))
            continue
        receipt = _read_receipt(_receipt_path(config, scope))
        if receipt is None:
            continue
        if not _receipt_address_matches(receipt, scope):
            raise HTTPException(
                409, "The role receipt belongs to another document; reconcile it before starting."
            )
        records.append(receipt)
    return records


def _document_agent(receipt: dict[str, Any]) -> dict[str, Any]:
    host = receipt["execution"]
    labels = {"ar.role": receipt["role"]}
    for name, label in (
        ("sprintDocumentRef", "ar.sprint-ref"),
        ("masterDocumentRef", "ar.master-ref"),
        ("taskDocumentRef", "ar.task-ref"),
    ):
        ref = receipt["selection"].get(name)
        if ref:
            labels[label] = ref["repository"] + "/" + ref["path"]
    return {
        "agentId": host["agentId"],
        "workspaceId": host["workspaceId"],
        "archivedAt": None,
        "createdAt": receipt.get("createdAt"),
        "labels": labels,
    }


def document_chats(
    config: McpRuntimeConfig, selection: RoleSelection, offset: int, request_id: UUID | None = None
) -> dict[str, Any]:
    records = _records(config, selection, request_id)
    agents = []
    # Host reads are bounded per request; old archived Projects actors are walked in pages.
    for receipt in records[offset : offset + 3]:
        host = receipt.get("execution") or {}
        if (
            host.get("kind") != "paseo-agent"
            or not host.get("agentId")
            or not host.get("workspaceId")
        ):
            continue
        reading = read_agent(config, host["agentId"])
        if not reading.reachable:
            return {"agents": [], "detail": reading.unreachable_reason, "unavailable": True}
        if not reading.found or reading.archived:
            continue
        agents.append(_document_agent(receipt))
        if not selection.sprint_document_ref:
            break
    next_offset = offset + 3 if not agents and offset + 3 < len(records) else None
    return {"agents": agents, "nextOffset": next_offset}
