"""The tool with which the agent that started a seat retires the agent in it."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.structural.agent import RetireChildRequest, StructuralRole
from agents_remember.models.task_document_ref import TaskDocumentRef

from .lazy import tools


def register_seat_retirement_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register ``retire_child``."""

    @server.tool()
    def retire_child(
        task_document_ref: TaskDocumentRef,
        role: StructuralRole,
        reason: str = "delegated seat retired",
        agent_id: str | None = None,
        interrupt_turn: bool = False,
    ) -> dict[str, Any]:
        """Retire one direct child seat (starter only). Requires interrupt_turn=true if a turn is running; repeats are inert. Docs: docs/tool-details.md."""
        return tools.retire_child_payload(
            config,
            RetireChildRequest(
                task_document_ref=task_document_ref,
                role=role,
                reason=reason,
                agent_id=agent_id,
                interrupt_turn=interrupt_turn,
            ),
        )
