"""Native Orca role-session preparation for canonical AR leaf assignments."""

from __future__ import annotations

import asyncio
import uuid
from typing import Literal

from mcp.server.fastmcp import FastMCP

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.tools.orca_handover import orca_role_prepare_payload
from agents_remember.models.orca_launcher import OrcaAgentOverride, OrcaDispatchRequest
from agents_remember.models.task_document_ref import TaskDocumentRef


def register_orca_role_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register the promptless native start used before Orca creates a Task/Dispatch."""

    @server.tool()
    async def orca_role_prepare(
        role: Literal["worker", "reviewer", "curator"],
        sprint_document_ref: TaskDocumentRef,
        master_document_ref: TaskDocumentRef,
        task_document_ref: TaskDocumentRef,
        *,
        agent_override: OrcaAgentOverride | None = None,
        request_id: uuid.UUID | None = None,
    ) -> dict[str, object]:
        """Start an idle native session prepared for one canonical AR leaf role.

        Resolves the selected sprint/master/leaf and its exact paired enclosure, applies the
        configured role defaults or native override, compiles and saves the full role handover,
        then asks Orca's native launcher to start the selected agent without an initial prompt.
        No AR Task or native Run/Task/Dispatch is created here; use the returned identity and
        next-action inputs with native Orca run-create and worker-start. The handover and report
        remain task-scoped, and an uncertain native start can be replayed with the returned
        requestId. Requires AR_ORCA_RUNTIME_ROOT and ORCA_USER_DATA_PATH in this MCP process.
        """
        request = OrcaDispatchRequest.model_validate(
            {
                "role": role,
                "sprintDocumentRef": sprint_document_ref,
                "masterDocumentRef": master_document_ref,
                "taskDocumentRef": task_document_ref,
                "requestId": request_id or uuid.uuid4(),
                "agentOverride": agent_override,
            }
        )
        return await asyncio.to_thread(orca_role_prepare_payload, config, request)


__all__ = ["register_orca_role_tools"]
