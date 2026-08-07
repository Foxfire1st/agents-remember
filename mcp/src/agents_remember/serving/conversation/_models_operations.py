from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, ClassVar, Literal, TypeAlias

from pydantic import Field, model_validator

from agents_remember.serving.conversation._models_wire import (
    AccessibleLabelProvenance,
    ActiveConversationRef,
    NonEmptyText,
    OperationFingerprint,
    PositiveOrdinal,
    PositiveRevision,
    WireModel,
)


class OpenConversationOperation(WireModel):
    _phases_by_outcome: ClassVar[Mapping[str, frozenset[str]]] = {
        "pending": frozenset({"requested", "launching", "catalog-wait"}),
        "opened": frozenset({"opened"}),
        "unsupported": frozenset({"failed"}),
        "stale-identity": frozenset({"failed"}),
        "launch-failed": frozenset({"retiring", "failed"}),
        "identity-mismatch": frozenset({"retiring", "failed"}),
        "timeout-unknown": frozenset({"launching", "catalog-wait", "unknown"}),
        "request-conflict": frozenset({"failed"}),
    }
    _failure_rollbacks: ClassVar[Mapping[tuple[str, str, bool], frozenset[str]]] = {
        ("launch-failed", "failed", False): frozenset({"not-needed"}),
        ("launch-failed", "retiring", True): frozenset({"retire-pending"}),
        ("launch-failed", "failed", True): frozenset({"retired", "retire-failed"}),
        ("identity-mismatch", "retiring", True): frozenset({"retire-pending"}),
        ("identity-mismatch", "failed", True): frozenset({"retired", "retire-failed"}),
    }

    request_id: NonEmptyText
    request_fingerprint: OperationFingerprint
    revision: PositiveRevision
    phase: Literal[
        "requested", "launching", "catalog-wait", "opened", "retiring", "failed", "unknown"
    ]
    outcome: Literal[
        "pending",
        "opened",
        "unsupported",
        "stale-identity",
        "launch-failed",
        "identity-mismatch",
        "timeout-unknown",
        "request-conflict",
    ]
    ar_session_id: str | None = None
    bridge_epoch: str | None = None
    identity: ActiveConversationRef | None = None
    catalog_generation: int | None = Field(default=None, ge=1)
    rollback: Literal["not-needed", "retired", "retire-pending", "retire-failed"]
    detail: str | None = None

    @model_validator(mode="after")
    def require_phase_outcome_product(self) -> OpenConversationOperation:
        if self.phase not in self._phases_by_outcome[self.outcome]:
            raise ValueError("open phase/outcome product is contradictory")
        return self

    @model_validator(mode="after")
    def require_complete_identity_tuple(self) -> OpenConversationOperation:
        identity_parts = (self.ar_session_id, self.bridge_epoch, self.identity)
        if any(part is not None for part in identity_parts) and not all(
            part is not None for part in identity_parts
        ):
            raise ValueError("open identity tuple must be complete")
        return self

    @model_validator(mode="after")
    def require_matching_identity_tuple(self) -> OpenConversationOperation:
        if self.identity is not None and (
            self.ar_session_id != self.identity.ar_session_id
            or self.bridge_epoch != self.identity.bridge_epoch
        ):
            raise ValueError("open identity tuple must agree exactly")
        return self

    @model_validator(mode="after")
    def require_identity_for_catalog_generation(self) -> OpenConversationOperation:
        if self.catalog_generation is not None and self.identity is None:
            raise ValueError("catalog generation requires an exact open identity")
        return self

    @model_validator(mode="after")
    def forbid_identity_for_no_launch_outcome(self) -> OpenConversationOperation:
        if self.outcome in {"unsupported", "stale-identity"} and self.identity is not None:
            raise ValueError(f"{self.outcome} cannot carry a spawned identity")
        return self

    @model_validator(mode="after")
    def require_opened_catalog_proof(self) -> OpenConversationOperation:
        if self.outcome == "opened" and (
            self.identity is None
            or self.catalog_generation is None
            or self.rollback != "not-needed"
        ):
            raise ValueError("opened requires exact identity, catalog proof, and no rollback")
        return self

    @model_validator(mode="after")
    # 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/_models_operations.py:108).
    def require_coherent_rollback(self) -> OpenConversationOperation:  # pragma: no cover
        if self.outcome not in {"launch-failed", "identity-mismatch"}:
            if self.rollback != "not-needed":
                raise ValueError("rollback requires an exact failed spawned identity")
            return self

        product = (self.outcome, self.phase, self.identity is not None)
        allowed_rollbacks = self._failure_rollbacks.get(product, frozenset())
        if self.rollback not in allowed_rollbacks:
            raise ValueError("rollback state must agree with failure identity and phase")
        if self.outcome == "identity-mismatch" and self.catalog_generation is None:
            raise ValueError("identity mismatch requires exact catalog generation")
        return self


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


class CockpitQueueIdentity(WireModel):
    withdrawal_ref: NonEmptyText
    redacted_preview: str = Field(max_length=384)
    preview_truncated: bool
    content_digest: NonEmptyText


class OperationQueueItem(WireModel):
    operation_ref: NonEmptyText
    revision: PositiveRevision
    sequence: PositiveOrdinal
    kind: Literal["prompt", "set-model", "set-effort"]
    source: Literal["cockpit", "terminal", "durable"]
    phase: Literal["queued", "dispatching", "unknown"]
    withdrawable: bool
    safe_label: NonEmptyText
    cockpit: CockpitQueueIdentity | None = None

    @model_validator(mode="after")
    def protect_queue_source_privacy(self) -> OperationQueueItem:
        authorized = self.source == "cockpit" and self.phase == "queued"
        if self.withdrawable != authorized:
            raise ValueError("only a queued cockpit operation can be withdrawable")
        if (self.cockpit is not None) != authorized:
            raise ValueError("cockpit withdrawal identity is private to a withdrawable cockpit row")
        return self


class OperationQueueProjection(WireModel):
    bridge_epoch: NonEmptyText
    revision: PositiveRevision
    items: tuple[OperationQueueItem, ...]


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


WithdrawQueueResponse: TypeAlias = Annotated[
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
    # 260731-EFA-L7 R10: verbatim L7 split (L7-OQ1 Option A serving scope); unchanged edge branch, out of this leaf's behavior scope (mcp/src/agents_remember/serving/conversation/_models_operations.py:284).
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


class AttachmentReceipt(WireModel):
    asset_id: NonEmptyText
    request_id: NonEmptyText
    request_fingerprint: OperationFingerprint
    revision: PositiveRevision
    ar_session_id: NonEmptyText
    bridge_epoch: NonEmptyText
    kind: Literal["image", "file", "resource"]
    name: NonEmptyText
    mime_type: NonEmptyText
    size_bytes: int = Field(ge=0)
    sha256: NonEmptyText
    alt: NonEmptyText
    alt_provenance: AccessibleLabelProvenance
    expires_at: NonEmptyText


class TextSubmitBlock(WireModel):
    type: Literal["text"] = "text"
    text: str


class AssetSubmitBlock(WireModel):
    type: Literal["asset-ref"] = "asset-ref"
    asset_id: NonEmptyText
    kind: Literal["image", "file", "resource"]
    name: NonEmptyText
    mime_type: NonEmptyText
    alt: NonEmptyText
    alt_provenance: AccessibleLabelProvenance
    sha256: NonEmptyText


ComposerSubmitBlock: TypeAlias = Annotated[
    TextSubmitBlock | AssetSubmitBlock, Field(discriminator="type")
]


class ConversationSubmitRequest(WireModel):
    expected_bridge_epoch: NonEmptyText
    request_id: NonEmptyText
    disposition: Literal["next"] = "next"
    content: tuple[ComposerSubmitBlock, ...] = Field(min_length=1)
    draft_revision: int = Field(ge=0)


class AttachmentOperationProjection(WireModel):
    request_id: NonEmptyText
    request_fingerprint: OperationFingerprint
    operation_ref: str | None = None
    revision: PositiveRevision
    phase: Literal[
        "staging",
        "staged",
        "queued",
        "dispatching",
        "accepted",
        "recoverable",
        "failed",
        "expired",
        "unknown",
    ]
    outcome: Literal["pending", "accepted", "rejected", "withdrawn", "failed", "expired", "unknown"]
    asset_ids: tuple[str, ...]
    recovery_expires_at: str | None = None

    @model_validator(mode="after")
    def require_coherent_attachment_state(self) -> AttachmentOperationProjection:
        allowed_outcomes: Mapping[str, frozenset[str]] = {
            "staging": frozenset({"pending"}),
            "staged": frozenset({"pending"}),
            "queued": frozenset({"pending"}),
            "dispatching": frozenset({"pending"}),
            "accepted": frozenset({"accepted"}),
            "recoverable": frozenset({"withdrawn"}),
            "failed": frozenset({"rejected", "failed"}),
            "expired": frozenset({"expired"}),
            "unknown": frozenset({"unknown"}),
        }
        if self.outcome not in allowed_outcomes[self.phase]:
            raise ValueError("attachment phase/outcome product is contradictory")
        if (self.recovery_expires_at is not None) != (self.phase == "recoverable"):
            raise ValueError("attachment recovery expiry requires recoverable phase")
        return self
