"""Durable operator inbox entries for external chat return channels."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from agents_remember.controlplane.durable_store import DurableRecord

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
    "state-signal",
]
InboxDeliveryState = Literal["queued", "no-hosted-session", "delivered", "unconfirmed"]
AdapterDeliveryState = Literal[
    "accepted", "queued", "rejected", "unknown", "completed", "unsupported"
]
OPERATOR_INBOX_FORWARD_COMPATIBLE_FIELDS = frozenset(
    {"adapterDeliveryState", "adapterDeliveryDetail"}
)


def state_signal_landed(entry: OperatorInboxEntry) -> bool:
    """Whether a state-signal row is terminal on the relay path: correlated adapter
    acceptance at a turn boundary (the N1 gate). ``acceptance=queued`` from a busy adapter is
    NOT this; only an accepted push at a boundary is a landing."""
    return (
        entry.messageKind == "state-signal"
        and entry.state == "pending"
        and entry.deliveryState == "delivered"
        and entry.adapterDeliveryState == "accepted"
    )


@dataclass(frozen=True)
class InboxAddress:
    """The mailbox an inbox row is delivered to: a lifecycle, a specific agent, a role -- at
    least one of the three, which is exactly what :func:`require_inbox_address` enforces. The
    three are one address, never independently meaningful."""

    lifecycle_id: str | None = None
    agent_id: str | None = None
    recipient_role: AgentRole | None = None


@dataclass(frozen=True)
class InboxOwner:
    """R4: the routed owner a poster derives from catalog spawn provenance BEFORE posting.

    Stamped once at creation (and re-stamped by a readdressing ladder rung) so redelivery
    never has to re-derive it from a catalog snapshot that has since moved on.
    """

    role: AgentRole | None = None
    agent_id: str | None = None
    lifecycle_id: str | None = None


@dataclass(frozen=True)
class InboxRouting:
    """Where an inbox row goes and who owns it.

    ``address`` is the mailbox this snapshot is delivered to right now; ``owner`` is the
    routed owner recorded alongside it. A readdressing ladder rung moves the address onto the
    next owner and rewrites both together, which is why they are one routing decision.
    """

    address: InboxAddress
    owner: InboxOwner = InboxOwner()


@dataclass(frozen=True)
class InboxSubject:
    """What a row is about, as opposed to who it goes to: the leaf and the seat under
    discussion and the agent being reported on. The agent-notifier coalesces re-fires and the
    ladder readdresses on exactly this triple."""

    leaf_key: str | None = None
    seat_role: str | None = None
    agent_id: str | None = None


@dataclass(frozen=True)
class InboxMessage:
    """What a row says and what it says it about: the ask, the response-channel text, the kind
    of message, the gate or artifact it points at, and its subject."""

    ask: str
    response: str
    message_kind: InboxMessageKind = "message"
    gate_id: str | None = None
    artifact_path: str | None = None
    subject: InboxSubject = InboxSubject()


@dataclass(frozen=True)
class InboxPoster:
    """Who put the row in the inbox: the recorded author and surface (``createdBy`` /
    ``createdVia``) plus the agent and role it was sent as."""

    created_by: str
    created_via: OperatorInboxVia
    sender_agent_id: str | None = None
    sender_role: AgentRole | None = None


def require_inbox_address(
    *,
    lifecycle_id: str | None,
    agent_id: str | None,
    recipient_role: AgentRole | None = None,
) -> None:
    """Require at least one mailbox key before writing or polling inbox entries."""
    if lifecycle_id is None and agent_id is None and recipient_role is None:
        raise ValueError("operator inbox requires lifecycle_id, agent_id, or recipient_role")


class OperatorInboxCompatibleRecord(DurableRecord):
    """Preserve only the named additive fields older inbox readers may not model yet.

    The one store whose ``extra`` policy differs from the contract default, and deliberately:
    it carries a named forward-compatibility allowlist that pre-dates this contract, so it
    keeps ``extra="allow"`` plus the explicit refusal below rather than the contract's blanket
    ``extra="forbid"``. The ``schemaVersion`` major/minor rule it inherits is unaffected.
    """

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def reject_unknown_extensions(self) -> Self:
        unsupported = set(self.model_extra or {}) - OPERATOR_INBOX_FORWARD_COMPATIBLE_FIELDS
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise ValueError(f"operator inbox record has unsupported fields: {names}")
        return self


class OperatorInboxEntry(OperatorInboxCompatibleRecord):
    """One append-only ``ar-operator-inbox-entry/v1`` snapshot."""

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
    # Leaf-scoped agent-notifier/completion signals carry their durable routing subject so later
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
    # Protocol delivery evidence is additive to the stable inbox delivery vocabulary. Acceptance
    # never mutates ``state``; explicit recipient consume remains the only acknowledgement (R14).
    adapterDeliveryState: AdapterDeliveryState | None = None
    adapterRequestId: str | None = None
    adapterVendorCorrelationId: str | None = None
    adapterAcceptedAt: str | None = None
    adapterCompletedAt: str | None = None
    adapterDeliveryDetail: str | None = None
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

    Delivery can finish from a stale agent-notifier snapshot after a concurrent consume. Such a
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
    message: InboxMessage,
    *,
    entry_id: str,
    now: str,
    routing: InboxRouting,
    poster: InboxPoster,
) -> OperatorInboxEntry:
    """Create a pending inbox entry. Pure: the caller mints ``entry_id`` and ``now``."""
    address = routing.address
    owner = routing.owner
    subject = message.subject
    require_inbox_address(
        lifecycle_id=address.lifecycle_id,
        agent_id=address.agent_id,
        recipient_role=address.recipient_role,
    )
    return OperatorInboxEntry(
        id=entry_id,
        ts=now,
        state="pending",
        lifecycleId=address.lifecycle_id,
        agentId=address.agent_id,
        senderAgentId=poster.sender_agent_id,
        senderRole=poster.sender_role,
        recipientRole=address.recipient_role,
        gateId=message.gate_id,
        messageKind=message.message_kind,
        artifactPath=message.artifact_path,
        leafKey=subject.leaf_key,
        seatRole=subject.seat_role,
        subjectAgentId=subject.agent_id,
        ask=message.ask,
        response=message.response,
        createdAt=now,
        createdBy=poster.created_by,
        createdVia=poster.created_via,
        ownerRole=owner.role,
        ownerAgentId=owner.agent_id,
        ownerLifecycleId=owner.lifecycle_id,
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
