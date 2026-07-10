"""Response models for the ``operator_inbox_*`` external-chat return channel."""

from __future__ import annotations

from typing import Any

from agents_remember.controlplane.operator_inbox_records import (
    AgentRole,
    InboxDeliveryState,
    InboxMessageKind,
    OperatorInboxState,
)
from agents_remember.models.base import ToolResponse


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
