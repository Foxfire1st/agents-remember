"""Plane-resolved agent dispatch and structural seat-management tools."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.structural.agent import (
    DispatchAgentRequest,
    RenameChildRequest,
    RetireChildRequest,
    StructuralRole,
)
from agents_remember.models.task_document_ref import TaskDocumentRef

from ..tools.structural_agent import (
    dispatch_agent_payload,
    rename_child_payload,
    rename_self_payload,
    retire_child_payload,
)


def register_session_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register operations that never ask a model for a runtime occupant id."""

    @server.tool()
    def dispatch_agent(
        task_document_ref: TaskDocumentRef,
        role: StructuralRole,
        brief: str,
        label: str | None = None,
    ) -> dict[str, Any]:
        """Create and durably brief one authorized direct child seat.

        The caller is proven from plane-injected process identity. The target is the canonical
        sprint/master/leaf task document plus role. Harness selection, runtime identity,
        readiness, exact initial brief pinning, delivery correlation, and retry remain private
        control-plane work. A queued brief is already durable and needs no model-held retry id.
        """
        return dispatch_agent_payload(
            config,
            DispatchAgentRequest(
                task_document_ref=task_document_ref,
                role=role,
                brief=brief,
                label=label,
            ),
        )

    @server.tool()
    def retire_child(
        task_document_ref: TaskDocumentRef,
        role: StructuralRole,
        reason: str = "delegated seat retired",
    ) -> dict[str, Any]:
        """Retire the current occupant of one authorized direct child seat."""
        return retire_child_payload(
            config,
            RetireChildRequest(
                task_document_ref=task_document_ref,
                role=role,
                reason=reason,
            ),
        )

    @server.tool()
    def rename_child(
        task_document_ref: TaskDocumentRef,
        role: StructuralRole,
        label: str,
    ) -> dict[str, Any]:
        """Change the display label of the current occupant of a direct child seat."""
        return rename_child_payload(
            config,
            RenameChildRequest(
                task_document_ref=task_document_ref,
                role=role,
                label=label,
            ),
        )

    @server.tool()
    def rename_self(label: str) -> dict[str, Any]:
        """Change this hosted seat's display label without supplying its runtime id."""
        return rename_self_payload(config, label=label)
