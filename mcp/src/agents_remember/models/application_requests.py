"""Wire request records consumed by application operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# Transport-facing literals live in models rather than forcing MCP registration to import the
# control-plane record implementations that consume the same wire values.
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
]
NudgeReason = Literal["inactive", "missing-turn-report", "manual"]


@dataclass(frozen=True)
class LifecycleGateRequest:
    """The flat public lifecycle-gate request before domain record construction."""

    kind: str
    ask: dict[str, Any] | None = None
    lifecycle_id: str | None = None
    enclosure: str | None = None
    repo_id: str | None = None
    packet: dict[str, Any] | None = None
    required_decision: list[str] | None = None
    evidence_refs: list[dict[str, Any]] | None = None
    wait: bool = True


@dataclass(frozen=True)
class GateDecisionRequest:
    """One addressed gate verdict with its transport-owned attribution."""

    decision: str
    gate_id: str | None = None
    lifecycle_id: str | None = None
    note: str | None = None
    decided_by: str | None = None
    decided_via: str = "cli"
    deciding_role: str | None = None
    evidence_refs: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class OperatorInboxPostRequest:
    """The flat inbox post before application-owned routing and record construction."""

    ask: str
    response: str
    lifecycle_id: str | None = None
    agent_id: str | None = None
    gate_id: str | None = None
    sender_agent_id: str | None = None
    sender_role: AgentRole | None = None
    recipient_role: AgentRole | None = None
    message_kind: InboxMessageKind = "message"
    artifact_path: str | None = None
    created_by: str = "model"
    created_via: OperatorInboxVia = "cli"
    deliver_to_hosted: bool = True


@dataclass(frozen=True)
class OrchestrationNudgeRequest:
    """The flat manager-nudge request before target/subject construction."""

    reason: NudgeReason
    subject: str
    manager_agent_id: str | None = None
    manager_lifecycle_id: str | None = None
    subject_agent_id: str | None = None
    subject_lifecycle_id: str | None = None
    artifact_path: str | None = None
    rate_limit_seconds: int = 900
