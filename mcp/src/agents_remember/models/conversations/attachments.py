from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.conversations.identity import AccessibleLabelProvenance
from agents_remember.models.conversations.primitives import (
    NonEmptyText,
    OperationFingerprint,
    PositiveRevision,
    WireModel,
)


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
