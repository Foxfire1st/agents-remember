"""Response models for the ``operator_inbox_*`` external-chat return channel."""

from __future__ import annotations

from typing import Any, Literal

from agents_remember.models.base import ToolResponse

# The operator-inbox wire vocabulary (moved from controlplane.operator_inbox_records).
OperatorInboxState = Literal[
    "pending",
    "landed",
    "superseded",
    "unresolved",
    "expired",
    "consumed",
    "ladder-resolved",
]
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
    "system-specialist",
    "architect",
    "curator",
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
    "degradation-alert",
    "decision-item",
    "decision-ruling",
    "dispatch-brief",
    "state-signal",
]
InboxDeliveryState = Literal["queued", "no-hosted-session", "delivered", "unconfirmed"]
AdapterDeliveryState = Literal[
    "accepted", "queued", "rejected", "unknown", "completed", "unsupported"
]


class OperatorInboxPostResponse(ToolResponse):
    """``operator_inbox_post``: a newly queued operator response."""

    entryId: str
    state: OperatorInboxState
    lifecycleId: str | None = None
    agentId: str | None = None
    senderAgentId: str | None = None
    senderRole: AgentRole | None = None
    recipientRole: AgentRole | None = None
    ownerRole: AgentRole | None = None
    ownerAgentId: str | None = None
    ownerLifecycleId: str | None = None
    gateId: str | None = None
    messageKind: InboxMessageKind
    artifactPath: str | None = None
    deliveryState: InboxDeliveryState
    deliveredAt: str | None = None
    deliveredToSession: str | None = None
    deliveryDetail: str | None = None
    adapterDeliveryState: AdapterDeliveryState | None = None
    adapterRequestId: str | None = None
    adapterVendorCorrelationId: str | None = None
    adapterAcceptedAt: str | None = None
    adapterCompletedAt: str | None = None
    adapterDeliveryDetail: str | None = None


class OperatorInboxPollResponse(ToolResponse):
    """``operator_inbox_poll``: pending entries for one mailbox key."""

    lifecycleId: str | None = None
    agentId: str | None = None
    recipientRole: AgentRole | None = None
    entryCount: int
    entries: list[dict[str, Any]]


class OperatorInboxConsumeResponse(ToolResponse):
    """``operator_inbox_consume``: the entry state after acknowledgement."""

    entryId: str
    state: OperatorInboxState
    consumedNow: bool
    consumedAt: str | None = None


class OperatorInboxSupersedeResponse(ToolResponse):
    """``operator_inbox_supersede``: the terminal marker after an explicit supersession."""

    entryId: str
    state: OperatorInboxState
    supersededNow: bool
    terminalAt: str | None = None
    terminalReason: str | None = None
    supersededBy: str | None = None
