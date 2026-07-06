"""Durable operator inbox entries for external chat return channels."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OPERATOR_INBOX_RECORD_SCHEMA = "ar-operator-inbox-entry/v1"

OperatorInboxState = Literal["pending", "consumed"]
OperatorInboxVia = Literal["chat", "dashboard", "cli"]
AgentRole = Literal[
    "developer",
    "operator",
    "designer",
    "strategist",
    "orchestrator",
    "manager",
    "worker",
    "reviewer",
    "agent",
    "system",
]
InboxMessageKind = Literal[
    "message",
    "gate-response",
    "turn-report",
    "master-handover",
    "nudge",
    "escalation",
]
InboxDeliveryState = Literal["queued", "no-hosted-session", "delivered", "unconfirmed"]


def require_inbox_address(
    *,
    lifecycle_id: str | None,
    agent_id: str | None,
    recipient_role: AgentRole | None = None,
) -> None:
    """Require at least one mailbox key before writing or polling inbox entries."""
    if lifecycle_id is None and agent_id is None and recipient_role is None:
        raise ValueError("operator inbox requires lifecycle_id, agent_id, or recipient_role")


class OperatorInboxEntry(BaseModel):
    """One append-only ``ar-operator-inbox-entry/v1`` snapshot."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=OPERATOR_INBOX_RECORD_SCHEMA, alias="schema")
    id: str
    ts: str
    state: OperatorInboxState
    lifecycleId: str | None = None
    agentId: str | None = None
    senderAgentId: str | None = None
    senderRole: AgentRole | None = None
    recipientRole: AgentRole | None = None
    gateId: str | None = None
    messageKind: InboxMessageKind = "message"
    artifactPath: str | None = None
    ask: str
    response: str
    createdAt: str
    createdBy: str
    createdVia: OperatorInboxVia
    deliveryState: InboxDeliveryState = "queued"
    deliveredAt: str | None = None
    deliveredToSession: str | None = None
    deliveryDetail: str | None = None
    consumedAt: str | None = None
    consumedBy: str | None = None
    consumedVia: OperatorInboxVia | None = None


def create_operator_inbox_entry(
    *,
    entry_id: str,
    now: str,
    lifecycle_id: str | None,
    agent_id: str | None,
    ask: str,
    response: str,
    created_by: str,
    created_via: OperatorInboxVia,
    gate_id: str | None = None,
    sender_agent_id: str | None = None,
    sender_role: AgentRole | None = None,
    recipient_role: AgentRole | None = None,
    message_kind: InboxMessageKind = "message",
    artifact_path: str | None = None,
) -> OperatorInboxEntry:
    """Create a pending inbox entry. Pure: the caller mints ``entry_id`` and ``now``."""
    require_inbox_address(
        lifecycle_id=lifecycle_id,
        agent_id=agent_id,
        recipient_role=recipient_role,
    )
    return OperatorInboxEntry(
        id=entry_id,
        ts=now,
        state="pending",
        lifecycleId=lifecycle_id,
        agentId=agent_id,
        senderAgentId=sender_agent_id,
        senderRole=sender_role,
        recipientRole=recipient_role,
        gateId=gate_id,
        messageKind=message_kind,
        artifactPath=artifact_path,
        ask=ask,
        response=response,
        createdAt=now,
        createdBy=created_by,
        createdVia=created_via,
    )


def consume_operator_inbox_entry(
    entry: OperatorInboxEntry,
    *,
    now: str,
    consumed_by: str,
    consumed_via: OperatorInboxVia,
) -> OperatorInboxEntry:
    """Return a consumed snapshot, preserving the original post attribution."""
    if entry.state == "consumed":
        return entry
    return entry.model_copy(
        update={
            "ts": now,
            "state": "consumed",
            "consumedAt": now,
            "consumedBy": consumed_by,
            "consumedVia": consumed_via,
        }
    )
