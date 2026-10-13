"""Payload builder for the direct-execution-grant operation (MIK-R94 rule 2)."""

from __future__ import annotations

from typing import Any

from agents_remember.application.agent_binding import AgentBinding
from agents_remember.application.direct_execution_grants import direct_execution_grant_tool
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_document_ref import TaskDocumentRef

from .base import _tool_payload


def direct_execution_grant_payload(
    config: McpRuntimeConfig,
    ref: TaskDocumentRef,
    action: str,
    *,
    binding: AgentBinding | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "direct_execution_grant",
        direct_execution_grant_tool(config, ref, action, binding=binding),
    )
