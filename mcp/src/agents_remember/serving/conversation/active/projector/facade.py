"""Small public facade for one active-session projection component graph."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic import ValidationError

from agents_remember.errors import HarnessBridgeEpochMismatchError, HarnessControlError
from agents_remember.models.conversations.identity import (
    ActiveConversationRef,
    AuthorizationBinding,
)
from agents_remember.models.conversations.stream_events import (
    ConversationEventEnvelope,
)
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.conversation.active.agent_history import AgentHistoryHydration
from agents_remember.serving.conversation.projectors import HarnessProjector
from agents_remember.serving.conversation.projectors.common import UnmappableShape

from .agent_authority import AgentAuthority
from .child_history import ChildHistoryProjection
from .echo_ingestion import EchoIngestion
from .interaction_projection import InteractionProjection
from .mutation_stream import CLOSE_SENTINEL, GapReason, ProjectionMutationStream
from .native_ingestion import (
    EvidenceTimelineRegressed,
    NativeEvidenceIngestion,
    ZipperEvidenceEvicted,
)
from .rebuild_coordinator import IngestionComponents, PageResult, RebuildCoordinator
from .references import ProjectionEvidenceRefs
from .wiring import BridgeReaders, SessionProjectionSpine

POLL_INTERVAL_SECONDS = 1.0
CONSUMER_TTL_SECONDS = 30.0
MAX_CONSECUTIVE_READ_FAILURES = 5


@dataclass(frozen=True)
class ProjectedSession:
    """WHICH conversation is being projected, and the authority to mint references for it.

    The identity names the exact session and bridge epoch; the controlled session is the row those
    reads go through; the mapper is that harness's shape reader; the authorization and the signing
    secret are what every emitted reference is bound to. A projector built from a mixed set would
    publish one session's events under another's references, so the five arrive as one value.
    """

    identity: ActiveConversationRef
    authorization: AuthorizationBinding
    entry: TerminalCatalogEntry
    mapper: HarnessProjector
    secret: bytes


class ActiveSessionProjector:
    """Orchestrate bounded components for one exact session/epoch."""

    def __init__(
        self,
        session: ProjectedSession,
        *,
        clock,
        readers: BridgeReaders,
    ) -> None:
        identity = session.identity
        entry = session.entry
        mapper = session.mapper
        self._identity = identity
        self._entry = entry
        self._mapper = mapper
        self._apply_lock = asyncio.Lock()
        self._stream = ProjectionMutationStream(
            identity=identity,
            authorization=session.authorization,
            secret=session.secret,
            clock=clock,
        )
        self._agents = AgentAuthority(identity.vendor_conversation_id)
        spine = SessionProjectionSpine(
            identity=identity,
            entry=entry,
            mapper=mapper,
            stream=self._stream,
            agents=self._agents,
            refs=ProjectionEvidenceRefs.from_bridge_epoch(identity.bridge_epoch),
            apply_lock=self._apply_lock,
            clock=clock,
        )
        self._native = NativeEvidenceIngestion(spine, readers)
        self._echo = EchoIngestion(spine, readers, self._native.map_evidence_frame)
        self._child_history = ChildHistoryProjection(spine, readers, self._native)
        self._interactions = InteractionProjection(
            stream=self._stream,
            parent_thread_id=identity.vendor_conversation_id,
            agent_ref=self._agents.ref,
        )
        self._coordinator = RebuildCoordinator(
            spine,
            readers,
            IngestionComponents(
                native=self._native,
                echo=self._echo,
                child_history=self._child_history,
                interactions=self._interactions,
            ),
        )
        self._poll_task: asyncio.Task[None] | None = None
        self._consumer_until = 0.0
        self._consecutive_failures = 0
        self._closed = False

    @property
    def generation(self) -> str:
        return self._stream.generation

    @property
    def retention_floor(self) -> int:
        return self._stream.retention_floor

    @property
    def subscriber_count(self) -> int:
        return len(self._stream.subscribers)

    def matches(self, identity: ActiveConversationRef) -> bool:
        return (
            self._identity.ar_session_id == identity.ar_session_id
            and self._identity.bridge_epoch == identity.bridge_epoch
            and self._identity.vendor_conversation_id == identity.vendor_conversation_id
            and not self._closed
        )

    async def page(self, *, before_ordinal: int | None, limit: int) -> PageResult:
        result = await self._coordinator.page(before_ordinal=before_ordinal, limit=limit)
        self.touch()
        return result

    def subscribe(self) -> asyncio.Queue[object]:
        self.touch()
        return self._stream.subscribe()

    def unsubscribe(self, queue: asyncio.Queue[object]) -> None:
        self._stream.unsubscribe(queue)

    def retained_after(self, sequence: int) -> tuple[ConversationEventEnvelope, ...]:
        return self._stream.retained_after(sequence)

    def touch(self) -> None:
        self._consumer_until = asyncio.get_running_loop().time() + CONSUMER_TTL_SECONDS
        self._ensure_polling()

    async def poll_once(self) -> None:
        await self._coordinator.poll_once()
        self._consecutive_failures = 0

    async def refresh_agent_native(self, thread_id: str) -> AgentHistoryHydration:
        return await self._coordinator.refresh_child(thread_id)

    async def close(self) -> None:
        self._closed = True
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        await self._child_history.close()
        self._stream.close_subscribers()

    def _ensure_polling(self) -> None:
        if self._closed or self._poll_task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._poll_task = loop.create_task(self._run())

    async def _run(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                if (
                    not self._stream.subscribers
                    and asyncio.get_running_loop().time() > self._consumer_until
                ):
                    self._release_dormant_state()
                    break
                try:
                    await self.poll_once()
                except HarnessBridgeEpochMismatchError:
                    await self._gap("generation-changed")
                    return
                except (ZipperEvidenceEvicted, EvidenceTimelineRegressed):
                    await self._gap("ordering-fault")
                    return
                except (ValidationError, UnmappableShape):
                    await self._gap("ordering-fault")
                    return
                except HarnessControlError:
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= MAX_CONSECUTIVE_READ_FAILURES:
                        await self._gap("generation-changed")
                        return
        except asyncio.CancelledError:
            return
        finally:
            self._poll_task = None

    async def _gap(self, reason: GapReason) -> None:
        await self._stream.gap(reason)
        self._closed = True

    def _release_dormant_state(self) -> None:
        self._closed = True
        self._stream.release_projection()
        self._echo.release()
        self._native.release()
        self._agents.reset()
        self._child_history.release()
        self._interactions.reset()


__all__ = ["CLOSE_SENTINEL", "ActiveSessionProjector", "PageResult"]
