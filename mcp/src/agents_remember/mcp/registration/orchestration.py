"""Structural parent/child messaging tools."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.structural.agent import (
    AgentMessageKind,
    StructuralMessageRequest,
    StructuralRole,
)
from agents_remember.models.task_document_ref import TaskDocumentRef

from ..tools.structural_agent import message_child_payload, message_parent_payload


def register_orchestration_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register durable messages whose current recipient is resolved by the plane."""

    @server.tool()
    def message_parent(
        ask: str,
        response: str,
        message_kind: AgentMessageKind = "message",
        artifact_path: str | None = None,
    ) -> dict[str, Any]:
        """Persist and deliver one whole message to this seat's current structural parent.

        Replacement is transparent: the canonical task hierarchy and role determine the current
        recipient at post and redelivery time. Runtime, lifecycle, inbox-row, and adapter ids are
        neither inputs nor outputs. Dispatch briefs and terminal state signals are plane-owned.
        """
        return message_parent_payload(
            config,
            StructuralMessageRequest(
                ask=ask,
                response=response,
                message_kind=message_kind,
                artifact_path=artifact_path,
            ),
        )

    @server.tool()
    def message_child(
        task_document_ref: TaskDocumentRef,
        role: StructuralRole,
        ask: str,
        response: str,
        message_kind: AgentMessageKind = "message",
        artifact_path: str | None = None,
    ) -> dict[str, Any]:
        """Persist and deliver one whole message to an authorized direct child seat.

        The task document and role are the stable work address. The current runtime occupant is
        selected privately and re-resolved after replacement.
        """
        return message_child_payload(
            config,
            StructuralMessageRequest(
                ask=ask,
                response=response,
                task_document_ref=task_document_ref,
                role=role,
                message_kind=message_kind,
                artifact_path=artifact_path,
            ),
        )
