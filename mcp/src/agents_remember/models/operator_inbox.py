"""Response models for the ``operator_inbox_*`` external-chat return channel."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

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
# The post's own outcome: whether an entry was queued, or whether the post was refused
# before it could be. ``sprint-owner-required`` is the decision-item refusal that fires when
# no sprint owner could be routed for the message
# (serving/operator_inbox_posts.py:304-310) -- it is a typed outcome of this operation, so it
# belongs on this operation's own response rather than surfacing as a tool error.
OperatorInboxPostStatus = Literal["queued", "sprint-owner-required"]


class OperatorInboxPostResponse(ToolResponse):
    """``operator_inbox_post``: a newly queued operator response, or a typed refusal.

    ``status`` names which of the two this is. The queued projection (``entryId``,
    ``state``, ``messageKind``, ``deliveryState``) is required exactly when ``ok`` is
    true and stays absent on a refusal, where no entry exists to describe: the
    ``sprint-owner-required`` refusal fires *before* the first write, so it has no entry
    id to report and must not invent one. The requirement is conditional, so the JSON
    schema cannot carry it (``required`` is only ever the unconditional fields) -- the
    validator below is the enforcement, and a caller reading the schema alone must read
    this docstring with it.
    """

    status: OperatorInboxPostStatus
    entryId: str | None = None
    state: OperatorInboxState | None = None
    lifecycleId: str | None = None
    agentId: str | None = None
    senderAgentId: str | None = None
    senderRole: AgentRole | None = None
    recipientRole: AgentRole | None = None
    ownerRole: AgentRole | None = None
    ownerAgentId: str | None = None
    ownerLifecycleId: str | None = None
    gateId: str | None = None
    messageKind: InboxMessageKind | None = None
    artifactPath: str | None = None
    deliveryState: InboxDeliveryState | None = None
    deliveredAt: str | None = None
    deliveredToSession: str | None = None
    deliveryDetail: str | None = None
    adapterDeliveryState: AdapterDeliveryState | None = None
    adapterRequestId: str | None = None
    adapterVendorCorrelationId: str | None = None
    adapterAcceptedAt: str | None = None
    adapterCompletedAt: str | None = None
    adapterDeliveryDetail: str | None = None
    # The refusal's own prose, as on ``SessionRetireResponse``: a typed refusal a seat can act
    # on, instead of a bare tool error. Absent on a queued post.
    detail: str | None = Field(default=None, max_length=8192)

    @model_validator(mode="after")
    def _require_the_queued_projection_when_ok(self) -> OperatorInboxPostResponse:
        """A post that reports success must name the entry it queued and how it landed."""

        if not self.ok:
            return self
        missing = [
            name
            for name in ("entryId", "state", "messageKind", "deliveryState")
            if getattr(self, name) is None
        ]
        if missing:
            raise ValueError("a queued operator post must report " + ", ".join(missing))
        return self


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
