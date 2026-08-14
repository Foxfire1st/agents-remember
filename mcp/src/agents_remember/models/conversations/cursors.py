from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from agents_remember.models.conversations.identity import (
    ActiveConversationRef,
    AuthorizationBinding,
    ConversationLibraryScope,
)
from agents_remember.models.conversations.primitives import (
    NonEmptyText,
    PositiveRevision,
    WireModel,
    _OpaqueToken,
)


class ActivePageCursor(_OpaqueToken):
    token_prefix = "ar-apc1."


class ActiveEventCursor(_OpaqueToken):
    token_prefix = "ar-aec1."


class LibraryListCursor(_OpaqueToken):
    token_prefix = "ar-llc1."


class LibraryReadCursor(_OpaqueToken):
    token_prefix = "ar-lrc1."


class LibraryConversationKey(_OpaqueToken):
    token_prefix = "ar-lck1."


class NativeResumeTarget(_OpaqueToken):
    """Server-private exact native resume target; never a public authorization grant."""

    token_prefix = "ar-nrt1."


class ActiveCursorBinding(WireModel):
    authorization: AuthorizationBinding
    purpose: Literal["active-page", "active-event"]
    identity: ActiveConversationRef
    projector_generation: NonEmptyText
    schema_version: PositiveRevision = 1


class LibraryCursorBinding(WireModel):
    scope: ConversationLibraryScope
    purpose: Literal["library-list", "library-read"]
    catalog_generation: PositiveRevision
    schema_version: PositiveRevision = 1


class LibraryKeyBinding(WireModel):
    scope: ConversationLibraryScope
    identity_digest: NonEmptyText
    catalog_generation: PositiveRevision
    schema_version: PositiveRevision = 1


class ActiveEventResume(WireModel):
    """The two SSE resume sources must name one identical event cursor."""

    after: ActiveEventCursor | None = None
    last_event_id: ActiveEventCursor | None = None

    @model_validator(mode="after")
    def require_one_unambiguous_cursor(self) -> ActiveEventResume:
        if self.after is None and self.last_event_id is None:
            raise ValueError("an active event resume cursor is required")
        if (
            self.after is not None
            and self.last_event_id is not None
            and self.after.root != self.last_event_id.root
        ):
            raise ValueError("cursor-conflict: after and Last-Event-ID differ")
        return self

    @property
    def cursor(self) -> ActiveEventCursor:
        cursor = self.after or self.last_event_id
        assert cursor is not None
        return cursor
