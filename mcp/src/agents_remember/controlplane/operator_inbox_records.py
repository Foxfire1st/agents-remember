"""Durable operator inbox entries for external chat return channels."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OPERATOR_INBOX_RECORD_SCHEMA = "ar-operator-inbox-entry/v1"

OperatorInboxState = Literal["pending", "consumed", "ladder-resolved"]
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
    # Leaf-scoped supervisor/completion signals carry their durable routing subject so later
    # redelivery/escalation can re-check the live leaf chain instead of trusting a stale address.
    leafKey: str | None = None
    seatRole: str | None = None
    subjectAgentId: str | None = None
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
    ladderResolvedAt: str | None = None
    ladderResolvedReason: str | None = None
    # R1 (260707-HFX2-L1): ack semantics -- consume=ack is the ONLY terminal outcome. 'delivered'
    # is never terminal (F-A/F-V proved pasted != perceived), so every delivery attempt -- including
    # a confirmed paste -- stamps a redelivery schedule until the row is actually consumed.
    attemptCount: int = 0
    lastAttemptAt: str | None = None
    nextAttemptAt: str | None = None
    # Set only when the ladder (HFX2-L4) escalates an unacked row past redelivery; this leaf only
    # reserves the field so the row stays escalatable -- it never sets it itself.
    escalatedAt: str | None = None
    # Independent safety-floor stamp written only by advance_rung. General row ``ts`` changes on
    # delivery/renewal and therefore cannot prove when the last rung transition occurred.
    rungTransitionAt: str | None = None
    # P-15 tier 3 (260707-HFX2-L4): the ladder's own rung marker. 0 = not yet escalated;
    # 1 = renudged to the original addressee; 2 = skip-level re-addressed to the owner's owner;
    # 3 = surfaced to the developer attention queue. ``escalatedAt`` is re-stamped on every rung
    # transition (the anchor the next SLA check reads from), so it always names "since when has
    # this row sat at its CURRENT rung", not merely "was this row ever escalated".
    rung: int = 0
    # R4 hierarchical routing: the owner address derived from catalog spawn provenance
    # (spawned_by_session chain) at post time, so redelivery/escalation never has to
    # re-derive it later. ``ownerRole`` mirrors ``recipientRole`` semantics but is the
    # ROUTED address (worker -> its manager, manager -> its orchestrator, decision-item ->
    # architect) rather than the caller-supplied one; ``None`` when routing had nothing to derive
    # (e.g. a role-only mailbox with no catalog provenance).
    ownerRole: AgentRole | None = None
    ownerAgentId: str | None = None
    ownerLifecycleId: str | None = None


def fold_operator_inbox_entries(
    entries: Iterable[OperatorInboxEntry],
) -> dict[str, OperatorInboxEntry]:
    """Fold snapshots by id while preserving the first observed terminal transition.

    Delivery can finish from a stale supervisor snapshot after a concurrent consume. Such a
    pending snapshot is physically later in the append-only log, but it cannot reverse an
    already-recorded terminal state. Terminal snapshots otherwise remain last-wins so repeated
    consumes and ladder resolution keep their existing idempotent behavior.
    """
    current: dict[str, OperatorInboxEntry] = {}
    for entry in entries:
        previous = current.get(entry.id)
        if previous is not None and previous.state != "pending" and entry.state == "pending":
            continue
        current[entry.id] = entry
    return current


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
    leaf_key: str | None = None,
    seat_role: str | None = None,
    subject_agent_id: str | None = None,
    owner_role: AgentRole | None = None,
    owner_agent_id: str | None = None,
    owner_lifecycle_id: str | None = None,
) -> OperatorInboxEntry:
    """Create a pending inbox entry. Pure: the caller mints ``entry_id`` and ``now``.

    ``owner_*`` (R4) is the routed address the caller derived from catalog spawn provenance
    (or a reserved role like ``architect`` for a ``decision-item``) BEFORE posting -- stamped once,
    at creation, so redelivery never has to re-derive it from a catalog snapshot that may have
    since moved on.
    """
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
        leafKey=leaf_key,
        seatRole=seat_role,
        subjectAgentId=subject_agent_id,
        ask=ask,
        response=response,
        createdAt=now,
        createdBy=created_by,
        createdVia=created_via,
        ownerRole=owner_role,
        ownerAgentId=owner_agent_id,
        ownerLifecycleId=owner_lifecycle_id,
    )


def consume_operator_inbox_entry(
    entry: OperatorInboxEntry,
    *,
    now: str,
    consumed_by: str,
    consumed_via: OperatorInboxVia,
) -> OperatorInboxEntry:
    """Return a consumed snapshot, preserving the original post attribution."""
    if entry.state != "pending":
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
