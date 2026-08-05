"""Cross-agent messaging tools: the operator inbox and the manager nudge."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.models.application_requests import (
    AgentRole,
    InboxMessageKind,
    NudgeReason,
    OperatorInboxPostRequest,
    OrchestrationNudgeRequest,
)

from ..config import McpRuntimeConfig
from ..tools.operator_inbox import (
    operator_inbox_consume_payload,
    operator_inbox_poll_payload,
    registered_operator_inbox_post_payload,
)
from ..tools.orchestration import (
    registered_orchestration_nudge_payload,
)


def register_orchestration_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def operator_inbox_post(
        ask: str,
        response: str,
        lifecycle_id: str | None = None,
        agent_id: str | None = None,
        gate_id: str | None = None,
        sender_agent_id: str | None = None,
        sender_role: AgentRole | None = None,
        recipient_role: AgentRole | None = None,
        message_kind: InboxMessageKind = "message",
        artifact_path: str | None = None,
        deliver_to_hosted: bool = True,
    ) -> dict[str, Any]:
        """Queue an operator response for an external chat to poll. Supply lifecycle_id
        and/or agent_id as the mailbox key. Agent-to-agent messages can include sender /
        recipient role metadata; hosted targets are push-delivered through the terminal paste seam
        while the durable inbox row remains dashboard-visible. Over MCP this route is attributed to
        the model via cli; trusted dashboard code can call the payload builder directly with
        developer/dashboard attribution."""
        return registered_operator_inbox_post_payload(
            config,
            OperatorInboxPostRequest(
                ask=ask,
                response=response,
                lifecycle_id=lifecycle_id,
                agent_id=agent_id,
                gate_id=gate_id,
                sender_agent_id=sender_agent_id,
                sender_role=sender_role,
                recipient_role=recipient_role,
                message_kind=message_kind,
                artifact_path=artifact_path,
                created_by="model",
                created_via="cli",
                deliver_to_hosted=deliver_to_hosted,
            ),
        )

    @server.tool()
    def operator_inbox_poll(
        lifecycle_id: str | None = None,
        agent_id: str | None = None,
        recipient_role: AgentRole | None = None,
    ) -> dict[str, Any]:
        """List pending external-chat inbox entries for a lifecycle_id, agent_id, and/or role
        mailbox key. Consuming an entry is explicit via operator_inbox_consume."""
        return operator_inbox_poll_payload(
            config,
            lifecycle_id=lifecycle_id,
            agent_id=agent_id,
            recipient_role=recipient_role,
        )

    @server.tool()
    def operator_inbox_consume(entry_id: str) -> dict[str, Any]:
        """Mark an external-chat inbox entry consumed. The entry remains in the append-only
        inbox log; repeated consume calls are idempotent."""
        return operator_inbox_consume_payload(
            config,
            entry_id=entry_id,
            consumed_by="model",
            consumed_via="cli",
        )

    @server.tool()
    def orchestration_nudge_manager(
        reason: NudgeReason,
        subject: str,
        manager_agent_id: str | None = None,
        manager_lifecycle_id: str | None = None,
        subject_agent_id: str | None = None,
        subject_lifecycle_id: str | None = None,
        artifact_path: str | None = None,
        rate_limit_seconds: int = 900,
    ) -> dict[str, Any]:
        """Rate-limit, log, and push a manager nudge for inactivity or a missing turn report."""
        return registered_orchestration_nudge_payload(
            config,
            OrchestrationNudgeRequest(
                reason=reason,
                subject=subject,
                manager_agent_id=manager_agent_id,
                manager_lifecycle_id=manager_lifecycle_id,
                subject_agent_id=subject_agent_id,
                subject_lifecycle_id=subject_lifecycle_id,
                artifact_path=artifact_path,
                rate_limit_seconds=rate_limit_seconds,
            ),
        )
