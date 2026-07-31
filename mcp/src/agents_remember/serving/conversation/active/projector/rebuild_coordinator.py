"""Hydration, snapshot, status, and poll-cycle coordination."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from agents_remember.serving.conversation.active.status import ConversationStatusService
from agents_remember.serving.conversation.models import (
    ActiveEventCursor,
    ConversationItem,
    ConversationStatus,
)
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    SubmissionProvenanceBatch,
)

from .child_history import ChildHistoryProjection
from .echo_ingestion import EchoIngestion
from .interaction_projection import InteractionProjection
from .native_ingestion import NativeEvidenceIngestion
from .wiring import BridgeReaders, SessionProjectionSpine

Clock = Callable[[], str]
SnapshotReader = Callable[..., AdapterSnapshot]
ProvenanceReader = Callable[..., SubmissionProvenanceBatch]
MAX_PROVENANCE_BATCH = 64


@dataclass(frozen=True)
class PageResult:
    """Atomic page assembly returned by the public facade."""

    items: tuple[ConversationItem, ...]
    older_ordinal: int | None
    has_older: bool
    total_items: int | None
    event_cursor: ActiveEventCursor
    hydration_id: str
    status: ConversationStatus
    snapshot: AdapterSnapshot


@dataclass(frozen=True)
class IngestionComponents:
    """The four ingestion components one rebuild drives, in the order it must drive them.

    A rebuild is not four independent refreshes: native evidence establishes the timeline, echo
    ingestion fills the transcript gaps in it, child history hangs off the agents that appeared,
    and the interaction projection reads what all three produced. Passing them as one set is what
    keeps a coordinator from being wired to three components of this session and one of another.
    """

    native: NativeEvidenceIngestion
    echo: EchoIngestion
    child_history: ChildHistoryProjection
    interactions: InteractionProjection


class RebuildCoordinator:
    """Order native hydration and incremental polls over the component graph."""

    def __init__(
        self,
        spine: SessionProjectionSpine,
        readers: BridgeReaders,
        components: IngestionComponents,
    ) -> None:
        self._identity = spine.identity
        self._entry = spine.entry
        self._mapper = spine.mapper
        self._stream = spine.stream
        self._native = components.native
        self._echo = components.echo
        self._agents = spine.agents
        self._child_history = components.child_history
        self._interactions = components.interactions
        self._apply_lock = spine.apply_lock
        self._read_snapshot = readers.snapshot
        self._read_provenance = readers.provenance
        self._status = ConversationStatusService(spine.identity, clock=spine.clock)
        self._hydrated = False
        self._hydration_lock = asyncio.Lock()
        self.snapshot: AdapterSnapshot | None = None
        self.status_revision_emitted = 0

    @property
    def hydrated(self) -> bool:
        return self._hydrated

    async def ensure_hydrated(self) -> None:
        if self._hydrated:
            return
        async with self._hydration_lock:
            if self._hydrated:
                return
            await self._rebuild()
            self._hydrated = True

    async def page(self, *, before_ordinal: int | None, limit: int) -> PageResult:
        await self.ensure_hydrated()
        async with self._apply_lock:
            await self._native.refresh_native_tip()
            total_known = (
                self._native.native_complete
                and self._native.evidence_window_complete
                and not self._echo.pending_frames
            )
            window = self._stream.store.page(
                before_ordinal=before_ordinal,
                limit=limit,
                total_known=total_known,
            )
            assert self.snapshot is not None
            return PageResult(
                items=window.items,
                older_ordinal=window.older_ordinal,
                has_older=window.has_older,
                total_items=window.total_items,
                event_cursor=self._stream.event_cursor(),
                hydration_id=f"hydration-{uuid4().hex}",
                status=self._status.current(),
                snapshot=self.snapshot,
            )

    async def poll_once(self) -> None:
        await self.ensure_hydrated()
        async with self._apply_lock:
            await self._poll_channels(hydrated=True)
            snapshot = await asyncio.to_thread(self._read_snapshot, self._entry)
            self._interactions.apply(snapshot)
            status = self._status.observe(
                snapshot,
                self._mapper.harness_id,
                terminal=self._stream.consume_terminal(),
            )
            self.snapshot = snapshot
            self._agents.set_snapshot(snapshot)
            if status.revision > self.status_revision_emitted:
                self.status_revision_emitted = status.revision
                self._stream.emit_status(status)

    async def refresh_child(self, thread_id: str):
        await self.ensure_hydrated()
        return await self._child_history.refresh(thread_id)

    async def _rebuild(self) -> None:
        self._stream.reset_projection()
        self._echo.reset()
        self._native.reset()
        self._agents.reset()
        self._child_history.reset()
        self._interactions.reset()
        snapshot = await asyncio.to_thread(self._read_snapshot, self._entry)
        self.snapshot = snapshot
        self._agents.set_snapshot(snapshot)
        if self._mapper.uses_native_pages:
            await self._native.walk_parent_history(preserve_cursor_on_failure=False)
        await self._poll_channels(hydrated=False)
        snapshot = await asyncio.to_thread(self._read_snapshot, self._entry)
        self._interactions.apply(snapshot)
        self.snapshot = snapshot
        self._agents.set_snapshot(snapshot)
        self._status.observe(snapshot, self._mapper.harness_id)
        self.status_revision_emitted = self._status.revision

    async def _poll_channels(self, *, hydrated: bool) -> None:
        await self._native.poll_evidence(self._echo, hydrated=hydrated)
        await self._echo.poll(
            hydrated=hydrated,
            evidence_window_complete=self._native.evidence_window_complete,
        )
        await self._native.poll_native_continuation()
        await self._resolve_provenance()

    async def _resolve_provenance(self) -> None:
        pending = self._stream.store.pending_request_ids()[:MAX_PROVENANCE_BATCH]
        if not pending:
            return
        batch = await asyncio.to_thread(
            self._read_provenance,
            self._entry,
            expected_bridge_epoch=self._identity.bridge_epoch,
            request_ids=tuple(pending),
        )
        for record in batch.provenance:
            source = record.source if record.outcome == "found" else None
            for mutation in self._stream.store.apply_provenance(record.request_id, source):
                self._stream.emit(mutation)
