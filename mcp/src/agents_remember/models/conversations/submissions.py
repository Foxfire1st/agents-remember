from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from agents_remember.models.conversations.identity import AccessibleLabelProvenance
from agents_remember.models.conversations.primitives import (
    NonEmptyText,
    PositiveOrdinal,
    PositiveRevision,
    WireModel,
)


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


type ComposerSubmitBlock = Annotated[
    TextSubmitBlock | AssetSubmitBlock, Field(discriminator="type")
]


class ConversationSubmitRequest(WireModel):
    expected_bridge_epoch: NonEmptyText
    request_id: NonEmptyText
    disposition: Literal["next"] = "next"
    content: tuple[ComposerSubmitBlock, ...] = Field(min_length=1)
    draft_revision: int = Field(ge=0)
