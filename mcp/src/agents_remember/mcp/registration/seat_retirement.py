"""The scope-authorized role seat retirement tool."""

from typing import Any

from mcp.server.mcpserver import MCPServer

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import LauncherRole
from agents_remember.models.structural.agent import RetireChildRequest
from agents_remember.models.task_document_ref import TaskDocumentRef

from .lazy import tools


def register_seat_retirement_tools(server: MCPServer[Any], config: McpRuntimeConfig) -> None:
    """Register ``retire_child``."""

    @server.tool()
    def retire_child(
        agent_id: str,
        role: LauncherRole,
        *,
        reason: str = "delegated seat retired",
        interrupt_turn: bool = False,
        sprint_document_ref: TaskDocumentRef | None = None,
        master_document_ref: TaskDocumentRef | None = None,
        task_document_ref: TaskDocumentRef | None = None,
    ) -> dict[str, Any]:
        """Retire one role agent within scope. Pass agent_id and role. Scope uses the caller binding and the selection recorded for that ID. Task references are optional. Open child seats block retirement. Active turns or subagents require interrupt_turn. More: docs/tool-details.md."""
        return tools.retire_child_payload(
            config,
            RetireChildRequest(
                agent_id=agent_id,
                role=role,
                reason=reason,
                interrupt_turn=interrupt_turn,
                sprint_document_ref=sprint_document_ref,
                master_document_ref=master_document_ref,
                task_document_ref=task_document_ref,
            ),
        )
