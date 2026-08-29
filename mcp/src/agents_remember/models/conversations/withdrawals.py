from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import Field, model_validator

from agents_remember.models.conversations.identity import AccessibleLabelProvenance
from agents_remember.models.conversations.primitives import (
    NonEmptyText,
    OperationFingerprint,
    PositiveRevision,
    WireModel,
)


class WithdrawQueueRequest(WireModel):
    operation_ref: NonEmptyText
    withdrawal_ref: NonEmptyText
    withdraw_request_id: NonEmptyText


class AttachmentRecoveryRef(WireModel):
    recovery_asset_ref: NonEmptyText
    revision: PositiveRevision
    kind: Literal["image", "file", "resource"]
    name: NonEmptyText
    mime_type: NonEmptyText
    size_bytes: int = Field(ge=0)
    sha256: NonEmptyText
    alt: NonEmptyText
    alt_provenance: AccessibleLabelProvenance
    expires_at: NonEmptyText


class WithdrawalRecovery(WireModel):
    recovery_ref: NonEmptyText
    text: str
    content_digest: NonEmptyText
    submitted_draft_revision: int | None = Field(default=None, ge=0)
    attachments: tuple[AttachmentRecoveryRef, ...]


class WithdrawnQueueResponse(WireModel):
    withdraw_request_id: NonEmptyText
    request_fingerprint: OperationFingerprint
    revision: PositiveRevision
    outcome: Literal["withdrawn"] = "withdrawn"
    operation_ref: NonEmptyText
    withdrawn_at: NonEmptyText
    recovery: WithdrawalRecovery


class FailedWithdrawalResponse(WireModel):
    withdraw_request_id: NonEmptyText
    request_fingerprint: OperationFingerprint
    revision: PositiveRevision
    outcome: Literal[
        "already-dispatching",
        "delivery-unknown",
        "epoch-mismatch",
        "not-found",
        "request-conflict",
    ]
    operation_ref: NonEmptyText
    detail: NonEmptyText


type WithdrawQueueResponse = Annotated[
    WithdrawnQueueResponse | FailedWithdrawalResponse,
    Field(discriminator="outcome"),
]


class WithdrawalOperationProjection(WireModel):
    _allowed_products: ClassVar[frozenset[tuple[str, str, str]]] = frozenset(
        {
            ("requested", "pending", "none"),
            ("linearizing", "pending", "none"),
            ("unknown", "delivery-unknown", "none"),
            ("settled", "withdrawn", "recovery-unacknowledged"),
            ("settled", "withdrawn", "acknowledged"),
            ("settled", "withdrawn", "expired"),
            ("settled", "already-dispatching", "none"),
            ("settled", "epoch-mismatch", "none"),
            ("settled", "not-found", "none"),
            ("settled", "request-conflict", "none"),
        }
    )

    withdraw_request_id: NonEmptyText
    request_fingerprint: OperationFingerprint
    operation_ref: NonEmptyText
    revision: PositiveRevision
    phase: Literal["requested", "linearizing", "settled", "unknown"]
    outcome: Literal[
        "pending",
        "withdrawn",
        "already-dispatching",
        "delivery-unknown",
        "epoch-mismatch",
        "not-found",
        "request-conflict",
    ]
    recovery_state: Literal["none", "recovery-unacknowledged", "acknowledged", "expired"]
    recovery_expires_at: str | None = None

    @model_validator(mode="after")
    def require_phase_outcome_recovery_product(self) -> WithdrawalOperationProjection:
        product = (self.phase, self.outcome, self.recovery_state)
        if product not in self._allowed_products:
            raise ValueError("withdrawal phase/outcome/recovery product is contradictory")
        return self

    @model_validator(mode="after")
    def require_coherent_recovery_expiry(self) -> WithdrawalOperationProjection:  # pragma: no cover
        if self.recovery_state == "recovery-unacknowledged" and self.recovery_expires_at is None:
            raise ValueError("unacknowledged recovery requires expiry")
        if self.recovery_state == "none" and self.recovery_expires_at is not None:
            raise ValueError("non-recovery withdrawal cannot carry recovery expiry")
        return self


class PendingWithdrawalRecoveryProjection(WireModel):
    recovery_ref: NonEmptyText
    operation_ref: NonEmptyText
    withdraw_request_id: NonEmptyText
    revision: PositiveRevision
    state: Literal["recovery-unacknowledged"] = "recovery-unacknowledged"
    recovery_expires_at: NonEmptyText


class PendingWithdrawalRecoveryList(WireModel):
    bridge_epoch: NonEmptyText
    revision: PositiveRevision
    items: tuple[PendingWithdrawalRecoveryProjection, ...]
