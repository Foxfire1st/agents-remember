from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from agents_remember.models.conversations.content import ConversationItem
from agents_remember.models.conversations.cursors import ActiveEventCursor
from agents_remember.models.conversations.identity import ActiveConversationRef
from agents_remember.models.conversations.primitives import (
    NonEmptyText,
    PositiveOrdinal,
    PositiveRevision,
    WireModel,
)
from agents_remember.models.conversations.status import ConversationStatus


class AppendItemMutation(WireModel):
    op: Literal["append-item"] = "append-item"
    item: ConversationItem


class AppendBlockDeltaMutation(WireModel):
    op: Literal["append-block-delta"] = "append-block-delta"
    item_id: NonEmptyText
    block_id: NonEmptyText
    expected_revision: PositiveRevision
    next_revision: PositiveRevision
    delta: str

    @model_validator(mode="after")
    def require_next_revision(self) -> AppendBlockDeltaMutation:  # pragma: no cover
        if self.next_revision <= self.expected_revision:
            raise ValueError("nextRevision must advance expectedRevision")
        return self


class UpsertItemMutation(WireModel):
    op: Literal["upsert-item"] = "upsert-item"
    item: ConversationItem


class ReplacePageMutation(WireModel):
    op: Literal["replace-page"] = "replace-page"
    items: tuple[ConversationItem, ...]
    total_items: int | None = Field(default=None, ge=0)
    event_cursor: ActiveEventCursor
    reason: Literal["initial", "reset", "native-rehydrate"]

    @model_validator(mode="after")
    def require_honest_total(self) -> ReplacePageMutation:  # pragma: no cover
        if (
            self.total_items is not None
            and self.items
            and self.total_items < max(item.global_ordinal for item in self.items)
        ):
            raise ValueError("totalItems cannot be lower than a returned globalOrdinal")
        return self


class StatusMutation(WireModel):
    op: Literal["status"] = "status"
    status: ConversationStatus


class GapMutation(WireModel):
    op: Literal["gap"] = "gap"
    requested_after: ActiveEventCursor
    reason: Literal[
        "retention-overflow", "generation-changed", "projector-restart", "ordering-fault"
    ]
    requires_repage: Literal[True] = True
    close_after_event: Literal[True] = True


type ConversationMutation = Annotated[
    AppendItemMutation
    | AppendBlockDeltaMutation
    | UpsertItemMutation
    | ReplacePageMutation
    | StatusMutation
    | GapMutation,
    Field(discriminator="op"),
]


class ConversationEventEnvelope(WireModel):
    identity: ActiveConversationRef
    cursor: ActiveEventCursor
    # Nullable AND defaulted, because the active/control serializers dump with
    # ``exclude_none=True``: a null is DROPPED from the wire, so a required-but-nullable
    # field made this model unable to validate its own emitted body -- which the response
    # conformance suite found the moment the routes started declaring these models. The
    # wire is unchanged; the absent key already meant exactly this ``None``.
    previous_cursor: ActiveEventCursor | None = None
    sequence: PositiveOrdinal
    event_id: NonEmptyText
    emitted_at: NonEmptyText
    delivery: Literal["live", "resume-replay", "native-rehydrate"]
    mutation: ConversationMutation

    @model_validator(mode="after")
    def reject_self_predecessor(self) -> ConversationEventEnvelope:  # pragma: no cover
        if self.previous_cursor is not None and self.previous_cursor.root == self.cursor.root:
            raise ValueError("event cursor cannot equal previousCursor")
        return self
