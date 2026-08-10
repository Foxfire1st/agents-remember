from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.conversations.cursors import ActiveEventCursor
from agents_remember.models.conversations.identity import ActiveConversationRef, ProvenanceEvidence
from agents_remember.models.conversations.primitives import (
    NonEmptyText,
    PositiveRevision,
    WireModel,
)

ConversationProcessState = Literal["starting", "connected", "disconnected", "exited", "failed"]

ConversationTurnState = Literal[
    "ready",
    "working",
    "waiting",
    "needs-input",
    "settling",
    "retrying",
    "compacting",
    "interrupted",
    "failed",
]

CanonicalStatusEvidence = Literal[
    "settled-dispatchable",
    "active-native-turn",
    "declared-external-wait",
    "pending-interaction",
    "native-end-reconciling",
    "native-retry",
    "native-compaction",
    "interrupt-settled",
    "turn-failed",
]

CANONICAL_TURN_STATE_BY_EVIDENCE: Mapping[CanonicalStatusEvidence, ConversationTurnState] = {
    "settled-dispatchable": "ready",
    "active-native-turn": "working",
    "declared-external-wait": "waiting",
    "pending-interaction": "needs-input",
    "native-end-reconciling": "settling",
    "native-retry": "retrying",
    "native-compaction": "compacting",
    "interrupt-settled": "interrupted",
    "turn-failed": "failed",
}


class StatusFreshness(WireModel):
    state: Literal["fresh", "stale", "unknown"]
    # Nullable AND defaulted, because the active/control serializers dump with
    # ``exclude_none=True``: a null is DROPPED from the wire, so a required-but-nullable
    # field made this model unable to validate its own emitted body -- which the response
    # conformance suite found the moment the routes started declaring these models. The
    # wire is unchanged; the absent key already meant exactly this ``None``.
    last_evidence_at: str | None = None
    age_ms: int | None = Field(default=None, ge=0)
    stale_after_ms: int = Field(gt=0)
    observation_bound: NonEmptyText


class ConversationProcessStatus(WireModel):
    state: ConversationProcessState
    generation: NonEmptyText
    terminal_outcome: Literal["clean-exit", "failed-exit", "signal", "unknown"] | None = None
    detail: str | None = None


class ConversationTurnWaiting(WireModel):
    reason: NonEmptyText
    interaction_id: str | None = None
    operation_ref: str | None = None


class ConversationTurnOutcome(WireModel):
    state: Literal["completed", "interrupted", "failed", "unknown"]
    stop_reason: str | None = None
    operation_ref: str | None = None


class ConversationTurnStatus(WireModel):
    state: ConversationTurnState
    # Nullable AND defaulted, because the active/control serializers dump with
    # ``exclude_none=True``: a null is DROPPED from the wire, so a required-but-nullable
    # field made this model unable to validate its own emitted body -- which the response
    # conformance suite found the moment the routes started declaring these models. The
    # wire is unchanged; the absent key already meant exactly this ``None``.
    turn_id: str | None = None
    state_since: str | None = None
    waiting: ConversationTurnWaiting | None = None
    terminal_outcome: ConversationTurnOutcome | None = None

    @model_validator(mode="after")
    def require_waiting_evidence(self) -> ConversationTurnStatus:
        if self.state == "waiting":
            if self.waiting is None:
                raise ValueError("waiting requires a reason record")
            if self.waiting.interaction_id is not None:
                raise ValueError("waiting cannot carry an interactionId")
        elif self.state == "needs-input":
            if self.waiting is None or not self.waiting.interaction_id:
                raise ValueError("needs-input requires an exact interactionId")
        elif self.waiting is not None:
            raise ValueError(f"{self.state} cannot carry waiting evidence")
        return self

    @model_validator(mode="after")
    def require_terminal_evidence(self) -> ConversationTurnStatus:
        allowed_outcomes: Mapping[ConversationTurnState, frozenset[str | None]] = {
            "ready": frozenset({None, "completed"}),
            "working": frozenset({None}),
            "waiting": frozenset({None}),
            "needs-input": frozenset({None}),
            "settling": frozenset({None, "completed", "unknown"}),
            "retrying": frozenset({None}),
            "compacting": frozenset({None}),
            "interrupted": frozenset({"interrupted"}),
            "failed": frozenset({"failed"}),
        }
        outcome = self.terminal_outcome.state if self.terminal_outcome is not None else None
        if outcome not in allowed_outcomes[self.state]:
            raise ValueError(f"{self.state} has contradictory terminal outcome evidence")
        return self


class ConversationStatusEvidence(ProvenanceEvidence):
    adapter_revision: int | None = Field(default=None, ge=0)
    native_event_cursor: ActiveEventCursor | None = None


class ConversationStatus(WireModel):
    identity: ActiveConversationRef
    revision: PositiveRevision
    observed_at: NonEmptyText
    freshness: StatusFreshness
    process: ConversationProcessStatus
    turn: ConversationTurnStatus
    evidence: ConversationStatusEvidence

    @model_validator(mode="after")
    def reject_false_ready(self) -> ConversationStatus:
        if self.turn.state == "ready" and self.evidence.strength == "unknown":
            raise ValueError("unknown evidence cannot establish ready")
        return self
