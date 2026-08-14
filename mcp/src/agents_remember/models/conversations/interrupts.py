from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import model_validator

from agents_remember.models.conversations.identity import NonEmptyText, WireModel
from agents_remember.models.conversations.primitives import OperationFingerprint, PositiveRevision


class InterruptOperation(WireModel):
    request_id: NonEmptyText
    request_fingerprint: OperationFingerprint
    revision: PositiveRevision
    bridge_epoch: NonEmptyText
    turn_id: NonEmptyText
    acknowledgement: Literal["requested", "accepted", "unknown", "rejected"]
    settlement: Literal["pending", "interrupted", "already-settled", "failed"]
    native_correlation_id: str | None = None
    requested_at: NonEmptyText
    settled_at: str | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def require_coherent_acknowledgement_and_settlement(self) -> InterruptOperation:
        allowed_settlements: Mapping[str, frozenset[str]] = {
            "requested": frozenset({"pending"}),
            "accepted": frozenset({"pending", "interrupted", "already-settled", "failed"}),
            "unknown": frozenset({"pending", "interrupted", "already-settled", "failed"}),
            "rejected": frozenset({"failed"}),
        }
        if self.settlement not in allowed_settlements[self.acknowledgement]:
            raise ValueError("interrupt acknowledgement/settlement product is contradictory")
        if (self.settled_at is not None) != (self.settlement != "pending"):
            raise ValueError("interrupt settledAt must match terminal settlement")
        return self
