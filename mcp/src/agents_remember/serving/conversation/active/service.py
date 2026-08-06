"""App-scoped active conversation authority and bounded projector registry."""

from __future__ import annotations

import asyncio
import os
import weakref
from collections.abc import Callable
from datetime import UTC, datetime

from agents_remember.errors import HarnessBridgeEpochMismatchError
from agents_remember.serving.conversation.active.agent_history import AgentHistoryHydration
from agents_remember.serving.conversation.active.capabilities import capabilities_for
from agents_remember.serving.conversation.active.cursor import (
    CursorResetRequiredError,
    decode_event_cursor,
    decode_page_cursor,
    mint_page_cursor,
    require_same_generation,
)
from agents_remember.serving.conversation.active.factories import (
    build_identity,
    current_bridge_epoch,
    live_snapshot,
    resolve_running_entry,
)
from agents_remember.serving.conversation.active.projector import (
    ActiveSessionProjector,
    PageResult,
)
from agents_remember.serving.conversation.active.projector.facade import ProjectedSession
from agents_remember.serving.conversation.models import (
    ActiveConversationRef,
    ActiveEventCursor,
    ActivePageCursor,
    AuthorizationBinding,
    ConversationCapabilities,
    ConversationEventEnvelope,
    ConversationPage,
    ConversationPageWindow,
)
from agents_remember.serving.conversation.runtime import ConversationRuntime

Clock = Callable[[], str]

MAX_PROJECTORS_PER_APP = 32
"""Bounded live-projector set per app; eviction rehydrates on demand (D2)."""

DEFAULT_PAGE_LIMIT = 200
MAX_PAGE_LIMIT = 500


def utc_clock() -> str:
    return datetime.now(UTC).isoformat()


class ActiveConversationService:
    """The per-app active conversation authority."""

    def __init__(self, runtime: ConversationRuntime, *, clock: Clock = utc_clock) -> None:
        self._runtime = runtime
        self._clock = clock
        self._secret = os.urandom(32)
        self._projectors: dict[str, ActiveSessionProjector] = {}
        self._projector_lru: list[str] = []
        self._session_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    @property
    def secret(self) -> bytes:
        return self._secret

    @property
    def projector_count(self) -> int:
        return len(self._projectors)

    async def page(
        self,
        authorization: AuthorizationBinding,
        ar_session_id: str,
        *,
        expected_bridge_epoch: str,
        before: ActivePageCursor | None,
        limit: int,
    ) -> ConversationPage:
        """One authorized, epoch-checked atomic page + event cursor."""

        projector, identity = await self._projector_for(
            authorization, ar_session_id, expected_bridge_epoch=expected_bridge_epoch
        )
        before_ordinal: int | None = None
        if before is not None:
            before_ordinal = decode_page_cursor(
                self._secret, before, authorization, identity
            ).ordinal
        bounded_limit = max(1, min(MAX_PAGE_LIMIT, limit))
        result = await projector.page(before_ordinal=before_ordinal, limit=bounded_limit)
        return self._assemble_page(authorization, identity, result)

    async def subscribe(
        self,
        authorization: AuthorizationBinding,
        ar_session_id: str,
        *,
        expected_bridge_epoch: str,
        after: ActiveEventCursor,
    ) -> ActiveSubscription:
        """All pre-stream checks; returns only when the stream may start."""

        projector, identity = await self._projector_for(
            authorization, ar_session_id, expected_bridge_epoch=expected_bridge_epoch
        )
        decoded = decode_event_cursor(self._secret, after, authorization, identity)
        require_same_generation(decoded, projector.generation)
        if decoded.sequence < projector.retention_floor:
            raise CursorResetRequiredError(
                "event cursor predates the retained event window",
                reason="retention-overflow",
            )
        queue = projector.subscribe()
        replay = projector.retained_after(decoded.sequence)
        return ActiveSubscription(
            projector=projector,
            queue=queue,
            replay=replay,
        )

    async def hydrate_agent_history(
        self,
        authorization: AuthorizationBinding,
        ar_session_id: str,
        *,
        expected_bridge_epoch: str,
        agent_id: str,
    ) -> AgentHistoryHydration:
        """Hydrate only the operator-selected child; parent paging stays metadata-first."""

        projector, _identity = await self._projector_for(
            authorization,
            ar_session_id,
            expected_bridge_epoch=expected_bridge_epoch,
        )
        return await projector.refresh_agent_native(agent_id)

    async def close(self) -> None:
        for projector in tuple(self._projectors.values()):
            await projector.close()
        self._projectors.clear()
        self._projector_lru.clear()

    async def release_session(self, ar_session_id: str) -> None:
        """De-register and close one ended session's projector."""

        projector = self._projectors.pop(ar_session_id, None)
        self._lru_drop(ar_session_id)
        if projector is not None:
            await projector.close()

    async def _projector_for(
        self,
        authorization: AuthorizationBinding,
        ar_session_id: str,
        *,
        expected_bridge_epoch: str,
    ) -> tuple[ActiveSessionProjector, ActiveConversationRef]:
        lock = self._session_locks.get(ar_session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[ar_session_id] = lock
        async with lock:
            return await self._projector_for_locked(
                authorization,
                ar_session_id,
                expected_bridge_epoch=expected_bridge_epoch,
            )

    async def _projector_for_locked(
        self,
        authorization: AuthorizationBinding,
        ar_session_id: str,
        *,
        expected_bridge_epoch: str,
    ) -> tuple[ActiveSessionProjector, ActiveConversationRef]:
        """Resolve/create one session projector without duplicate replacement races."""

        # The catalog resolution takes the TerminalCatalog RLock + file read; offload it off
        # the event loop like the epoch/snapshot reads below.
        entry = await asyncio.to_thread(resolve_running_entry, self._runtime, ar_session_id)
        actual_epoch = await asyncio.to_thread(current_bridge_epoch, entry)
        if actual_epoch != expected_bridge_epoch:
            raise HarnessBridgeEpochMismatchError(expected_bridge_epoch, actual_epoch)
        snapshot = await asyncio.to_thread(live_snapshot, entry)
        identity, mapper = build_identity(
            entry, secret=self._secret, bridge_epoch=actual_epoch, snapshot=snapshot
        )
        projector = self._projectors.get(ar_session_id)
        if projector is not None and projector.matches(identity):
            self._lru_touch(ar_session_id)
            return projector, identity
        if projector is not None:
            await projector.close()
            self._projectors.pop(ar_session_id, None)
            self._lru_drop(ar_session_id)
        await self._evict_if_needed()
        projector = ActiveSessionProjector(
            ProjectedSession(
                identity=identity,
                authorization=authorization,
                entry=entry,
                mapper=mapper,
                secret=self._secret,
            ),
            clock=self._clock,
        )
        self._projectors[ar_session_id] = projector
        self._lru_touch(ar_session_id)
        return projector, identity

    async def _evict_if_needed(self) -> None:
        while len(self._projectors) >= MAX_PROJECTORS_PER_APP and self._projector_lru:
            oldest = self._projector_lru.pop(0)
            projector = self._projectors.pop(oldest, None)
            if projector is not None:
                await projector.close()

    def _lru_touch(self, ar_session_id: str) -> None:
        self._lru_drop(ar_session_id)
        self._projector_lru.append(ar_session_id)

    def _lru_drop(self, ar_session_id: str) -> None:
        if ar_session_id in self._projector_lru:
            self._projector_lru.remove(ar_session_id)

    def _assemble_page(
        self,
        authorization: AuthorizationBinding,
        identity: ActiveConversationRef,
        result: PageResult,
    ) -> ConversationPage:
        older_cursor = (
            mint_page_cursor(self._secret, authorization, identity, ordinal=result.older_ordinal)
            if result.older_ordinal is not None
            else None
        )
        capabilities: ConversationCapabilities = capabilities_for(
            identity.harness_id, result.snapshot
        )
        return ConversationPage(
            identity=identity,
            items=result.items,
            page=ConversationPageWindow(
                older_cursor=older_cursor,
                has_older=result.has_older,
                total_items=result.total_items,
            ),
            event_cursor=result.event_cursor,
            hydration_id=result.hydration_id,
            status=result.status,
            capabilities=capabilities,
        )


class ActiveSubscription:
    """A validated live subscription: replay first, then the live queue."""

    def __init__(
        self,
        *,
        projector: ActiveSessionProjector,
        queue: asyncio.Queue[object],
        replay: tuple[ConversationEventEnvelope, ...],
    ) -> None:
        self.projector = projector
        self.queue = queue
        self.replay = replay

    def detach(self) -> None:
        self.projector.unsubscribe(self.queue)


_SERVICES: weakref.WeakKeyDictionary[ConversationRuntime, ActiveConversationService] = (
    weakref.WeakKeyDictionary()
)


def active_conversation_service(runtime: ConversationRuntime) -> ActiveConversationService:
    """Return the one active service per installed runtime authority."""

    service = _SERVICES.get(runtime)
    if service is None:
        service = ActiveConversationService(runtime)
        _SERVICES[runtime] = service
    return service


__all__ = ["ActiveConversationService", "ActiveSubscription", "active_conversation_service"]
