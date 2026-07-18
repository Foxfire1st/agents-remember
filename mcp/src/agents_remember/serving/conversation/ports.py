"""The two conversation read ports; lifecycle/control authority remains elsewhere."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from agents_remember.serving.conversation.models import (
    ActiveConversationRef,
    ActiveEventCursor,
    ActivePageCursor,
    ConversationCapabilities,
    ConversationEventEnvelope,
    ConversationLibraryPage,
    ConversationLibraryScope,
    ConversationPage,
    ConversationStatus,
    HarnessId,
    HistoricalConversationPage,
    LibraryListCursor,
    LibraryReadCursor,
    NativeConversationRef,
    NativeResumeTarget,
)


class ActiveConversationPort(Protocol):
    """Native-hydrated reads for one already-running exact AR session."""

    async def identify(
        self,
        ar_session_id: str,
        bridge_epoch: str,
    ) -> ActiveConversationRef: ...

    async def page(
        self,
        ref: ActiveConversationRef,
        *,
        before: ActivePageCursor | None,
        limit: int,
    ) -> ConversationPage: ...

    def subscribe(
        self,
        ref: ActiveConversationRef,
        *,
        after: ActiveEventCursor,
    ) -> AsyncIterator[ConversationEventEnvelope]: ...

    async def status(self, ref: ActiveConversationRef) -> ConversationStatus: ...

    async def capabilities(
        self,
        ref: ActiveConversationRef,
    ) -> ConversationCapabilities: ...


class ConversationLibraryPort(Protocol):
    """Read-only dormant native catalog/history access for exactly one harness."""

    @property
    def harness_id(self) -> HarnessId: ...

    async def list(
        self,
        scope: ConversationLibraryScope,
        *,
        cursor: LibraryListCursor | None,
        limit: int,
    ) -> ConversationLibraryPage: ...

    async def read(
        self,
        ref: NativeConversationRef,
        *,
        before: LibraryReadCursor | None,
        limit: int,
    ) -> HistoricalConversationPage: ...

    async def resolve_resume_target(
        self,
        ref: NativeConversationRef,
    ) -> NativeResumeTarget: ...


__all__ = ["ActiveConversationPort", "ConversationLibraryPort"]
