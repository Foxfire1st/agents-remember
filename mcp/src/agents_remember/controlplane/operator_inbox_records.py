"""Durable operator inbox entries for external chat return channels."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from agents_remember.controlplane.durable_store import DurableRecord
from agents_remember.models.operator_inbox import (
    AdapterDeliveryState,
    AgentRole,
    InboxDeliveryState,
    InboxMessageKind,
    OperatorInboxState,
    OperatorInboxVia,
)
from agents_remember.models.task_document_ref import TaskDocumentRef

OPERATOR_INBOX_RECORD_SCHEMA = "ar-operator-inbox-entry/v2"

OPERATOR_INBOX_FORWARD_COMPATIBLE_FIELDS = frozenset(
    {"adapterDeliveryState", "adapterDeliveryDetail"}
)


def state_signal_landed(entry: OperatorInboxEntry) -> bool:
    """Whether an inbox row is terminal by landing (N16): the formal ``landed`` state.

    The by-rule predicate that derived landing from ``state-signal`` + ``delivered`` +
    ``adapterDeliveryState=accepted`` folded into the schema: :func:`record_delivery` now
    writes the ``landed`` snapshot itself when a correlated acceptance happened at a turn
    boundary. ``acceptance=queued`` from a busy adapter is never this.
    """
    return entry.state == "landed"


@dataclass(frozen=True)
class InboxAddress:
    """One structural mailbox plus its current private delivery correlations."""

    task_document_ref: TaskDocumentRef | None = None
    lifecycle_id: str | None = None
    agent_id: str | None = None
    recipient_role: AgentRole | None = None


@dataclass(frozen=True)
class InboxOwner:
    """The routed owner a poster derives from catalog provenance BEFORE posting.

    Stamped at creation and re-stamped by post-time re-resolution and sweep-time rebinding so
    redelivery never has to re-derive it from a catalog snapshot that has since moved on.
    """

    role: AgentRole | None = None
    task_document_ref: TaskDocumentRef | None = None
    agent_id: str | None = None
    lifecycle_id: str | None = None


@dataclass(frozen=True)
class InboxRouting:
    """Where an inbox row goes and who owns it.

    ``address`` is the mailbox this snapshot is delivered to right now; ``owner`` is the
    routed owner recorded alongside it. Sweep-time rebinding moves the address onto the current
    qualified owner and rewrites both together, which is why they are one routing decision.
    """

    address: InboxAddress
    owner: InboxOwner = InboxOwner()


@dataclass(frozen=True)
class InboxSubject:
    """The document-owned seat a row concerns, plus private occupant correlation."""

    task_document_ref: TaskDocumentRef | None = None
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
    task_document_ref: TaskDocumentRef | None = None,
    lifecycle_id: str | None,
    agent_id: str | None,
    recipient_role: AgentRole | None = None,
) -> None:
    """Require at least one mailbox key before writing or polling inbox entries."""
    if (
        task_document_ref is None
        and lifecycle_id is None
        and agent_id is None
        and recipient_role is None
    ):
        raise ValueError(
            "operator inbox requires task_document_ref, lifecycle_id, agent_id, or recipient_role"
        )


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
    """One append-only ``ar-operator-inbox-entry/v2`` snapshot."""

    schema_version: str = Field(default=OPERATOR_INBOX_RECORD_SCHEMA, alias="schema")
    id: str
    ts: str
    state: OperatorInboxState
    lifecycleId: str | None = None
    agentId: str | None = None
    taskDocumentRef: TaskDocumentRef | None = None
    senderAgentId: str | None = None
    senderRole: AgentRole | None = None
    recipientRole: AgentRole | None = None
    gateId: str | None = None
    messageKind: InboxMessageKind = "message"
    artifactPath: str | None = None
    # The durable structural subject survives occupant replacement. Exact ids are correlation only.
    subjectTaskDocumentRef: TaskDocumentRef | None = None
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
    # Protocol delivery evidence is additive to the stable inbox delivery vocabulary.
    # Correlated adapter acceptance AT A TURN BOUNDARY writes the formal ``landed`` terminal
    # state (N16); acceptance at any other time is delivery evidence, never terminality.
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
    # N16 (260713-TES): the system acks -- a row lands when a correlated adapter acceptance
    # reaches it at a turn boundary. Every other delivery attempt stamps a redelivery schedule;
    # ``consumed`` is an optional attribution marker with nothing mechanical attached.
    attemptCount: int = 0
    lastAttemptAt: str | None = None
    nextAttemptAt: str | None = None
    # Formal terminal stamps for the post-N16 vocabulary: every non-pending transition writes
    # ``terminalAt`` (when the row became terminal) and ``terminalReason`` (why). Terminal
    # markers stay inspectable for the marker-retention window, then are physically evicted.
    terminalAt: str | None = None
    terminalReason: str | None = None
    supersededBy: str | None = None
    # Legacy field from the retired timed escalation ladder; retained for parse compatibility
    # only -- no transition writes it anymore.
    escalatedAt: str | None = None
    # Legacy field from the retired timed escalation ladder; retained for parse compatibility
    # only -- no transition writes it anymore.
    rungTransitionAt: str | None = None
    # Legacy field from the retired timed escalation ladder; retained for parse compatibility
    # only (0 = never escalated; no transition advances it anymore).
    rung: int = 0
    # R4 hierarchical routing: the owner address derived from catalog spawn provenance
    # (spawned_by_session chain) at post time, so redelivery/escalation never has to
    # re-derive it later. ``ownerRole`` mirrors ``recipientRole`` semantics but is the
    # ROUTED address (worker -> its manager, manager -> its orchestrator, decision-item ->
    # architect) rather than the caller-supplied one; ``None`` when routing had nothing to derive
    # (e.g. a role-only mailbox with no catalog provenance).
    ownerRole: AgentRole | None = None
    ownerTaskDocumentRef: TaskDocumentRef | None = None
    ownerAgentId: str | None = None
    ownerLifecycleId: str | None = None


def fold_operator_inbox_entries(
    entries: Iterable[OperatorInboxEntry],
) -> dict[str, OperatorInboxEntry]:
    """Fold snapshots by id while preserving the first observed terminal transition.

    Delivery can finish from a stale agent-notifier snapshot after a concurrent consume. Such a
    pending snapshot is physically later in the append-only log, but it cannot reverse an
    already-recorded terminal state. Terminal snapshots otherwise remain last-wins so repeated
    attribution marks keep their existing idempotent behavior.
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
        task_document_ref=address.task_document_ref,
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
        taskDocumentRef=address.task_document_ref,
        senderAgentId=poster.sender_agent_id,
        senderRole=poster.sender_role,
        recipientRole=address.recipient_role,
        gateId=message.gate_id,
        messageKind=message.message_kind,
        artifactPath=message.artifact_path,
        subjectTaskDocumentRef=subject.task_document_ref,
        seatRole=subject.seat_role,
        subjectAgentId=subject.agent_id,
        ask=message.ask,
        response=message.response,
        createdAt=now,
        createdBy=poster.created_by,
        createdVia=poster.created_via,
        ownerRole=owner.role,
        ownerTaskDocumentRef=owner.task_document_ref,
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
    """Return an attribution-marked snapshot, preserving the row's state (N16).

    ``operator_inbox_consume`` is demoted to an optional attribution marker: it stamps
    ``consumedAt``/``consumedBy``/``consumedVia`` once and nothing else. No mechanical
    behavior -- retry, expectation, escalation, or terminality -- hangs off it, so ``state`` is
    untouched and a landed row stays ``landed`` even when a model also marks it consumed.
    """
    if entry.consumedAt is not None:
        return entry
    return entry.model_copy(
        update={
            "ts": now,
            "consumedAt": now,
            "consumedBy": consumed_by,
            "consumedVia": consumed_via,
        }
    )
