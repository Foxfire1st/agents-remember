"""Native-page and live-evidence ingestion for an active conversation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from agents_remember.errors import AgentsRememberError, HarnessControlError
from agents_remember.serving.conversation.models import ActiveConversationRef
from agents_remember.serving.conversation.projectors import HarnessProjector
from agents_remember.serving.conversation.projectors.common import (
    MappedItem,
    MappedUnknownVendor,
    MapperOutput,
    UnmappableShape,
)
from agents_remember.serving.harness_control_client import ControlledSession
from agents_remember.serving.harness_control_models import (
    EvidenceFrame,
    EvidencePage,
    NativeEvidencePage,
)

from .agent_authority import AgentAuthority
from .mutation_stream import ProjectionMutationStream
from .references import ProjectionEvidenceRefs

EvidenceReader = Callable[..., EvidencePage]
NativePageReader = Callable[..., NativeEvidencePage]

EVIDENCE_PAGE_LIMIT = 500
NATIVE_PAGE_LIMIT = 200
_CONTENT_ITEM_KINDS = frozenset({"message", "tool-call", "tool-result", "thinking", "plan"})


class EchoEvidenceBuffer(Protocol):
    def enqueue(self, frame: EvidenceFrame) -> None: ...


class ZipperEvidenceEvicted(AgentsRememberError):
    """Evidence eviction voided the echo zipper's turn-order guarantee."""


class EvidenceTimelineRegressed(AgentsRememberError):
    """The bridge evidence tip moved backward after this projector consumed it."""


class NativeEvidenceIngestion:
    """Own source watermarks, completeness, mapping, and live/native dedupe."""

    def __init__(
        self,
        *,
        identity: ActiveConversationRef,
        entry: ControlledSession,
        mapper: HarnessProjector,
        stream: ProjectionMutationStream,
        agents: AgentAuthority,
        refs: ProjectionEvidenceRefs,
        evidence_reader: EvidenceReader,
        native_page_reader: NativePageReader,
    ) -> None:
        self._identity = identity
        self._entry = entry
        self._mapper = mapper
        self._stream = stream
        self._agents = agents
        self._refs = refs
        self._read_evidence = evidence_reader
        self._read_native_page = native_page_reader
        self.evidence_after = 0
        self.evidence_window_complete = True
        self.native_cursor: str | None = None
        self.native_complete = not mapper.uses_native_pages
        self.native_dirty = False
        self.known_eviction_floor = 0
        self.live_turn_ids: dict[str | None, set[str]] = {}
        self.live_request_ids: dict[str | None, set[str]] = {}

    def reset(self) -> None:
        self.live_turn_ids.clear()
        self.live_request_ids.clear()

    def release(self) -> None:
        self.live_turn_ids.clear()
        self.live_request_ids.clear()

    async def walk_parent_history(self, *, preserve_cursor_on_failure: bool) -> None:
        prior = self.native_cursor
        self.native_cursor = None
        live_native_turns: set[str] = set()
        try:
            while True:
                page: NativeEvidencePage = await asyncio.to_thread(
                    self._read_native_page,
                    self._entry,
                    cursor=self.native_cursor,
                    limit=NATIVE_PAGE_LIMIT,
                    expected_bridge_epoch=self._identity.bridge_epoch,
                )
                for frame in page.frames:
                    ref = self._refs.native(frame.native_id)
                    outputs = [
                        self._agents.reconcile_roster(output)
                        for output in self._mapper.map_native_frame(frame, evidence_ref=ref)
                    ]
                    outputs = self.suppress_live_twins(outputs, live_native_turns, None)
                    self._stream.apply_outputs(outputs, ref)
                self.native_cursor = page.next_cursor
                if (page.next_cursor is None and not page.truncated) or not page.frames:
                    break
            self.native_complete = True
        except HarnessControlError:
            if preserve_cursor_on_failure:
                self.native_cursor = prior
            else:
                self.native_complete = False
                self.native_cursor = None

    async def refresh_native_tip(self) -> None:
        if (
            not self.native_dirty
            or not self._mapper.uses_native_pages
            or self._mapper.eager_native_continuation
        ):
            return
        self.native_dirty = False
        await self.walk_parent_history(preserve_cursor_on_failure=True)

    async def poll_evidence(self, echo_buffer: EchoEvidenceBuffer, *, hydrated: bool) -> None:
        while True:
            page: EvidencePage = await asyncio.to_thread(
                self._read_evidence,
                self._entry,
                after_sequence=self.evidence_after,
                limit=EVIDENCE_PAGE_LIMIT,
                expected_bridge_epoch=self._identity.bridge_epoch,
            )
            if hydrated and page.latest_sequence < self.evidence_after:
                raise EvidenceTimelineRegressed(
                    f"evidence tip regressed (latest {page.latest_sequence} < consumed "
                    f"{self.evidence_after}): the bridge re-baselined its timeline"
                )
            if page.evicted_before_sequence > 0:
                self.evidence_window_complete = False
            if (
                self._mapper.uses_transcript_echo
                and hydrated
                and page.evicted_before_sequence > self.known_eviction_floor
            ):
                raise ZipperEvidenceEvicted(
                    "evidence window evicted frames; the echo zipper's turn order is not provable"
                )
            self.known_eviction_floor = max(self.known_eviction_floor, page.evicted_before_sequence)
            for frame in page.frames:
                self._consume_frame(frame, echo_buffer)
                self.evidence_after = frame.sequence
            caught_up = not page.frames or self.evidence_after >= page.latest_sequence
            if (not page.truncated and caught_up) or not page.frames:
                return

    def _consume_frame(self, frame: EvidenceFrame, echo_buffer: EchoEvidenceBuffer) -> None:
        if self._mapper.uses_transcript_echo:
            echo_buffer.enqueue(frame)
            return
        ref = self._refs.evidence(frame.sequence)
        outputs = self.map_evidence_frame(frame, ref)
        if self._mapper.uses_native_pages and not self._mapper.eager_native_continuation:
            self.record_live_turn(outputs, self._agents.frame_thread(frame))
            self.native_dirty = True
        self._stream.apply_outputs(outputs, ref)

    def map_evidence_frame(self, frame: EvidenceFrame, evidence_ref: str) -> list[MapperOutput]:
        if frame.raw.get("arEvidenceTruncated") is True:
            frame_type = frame.raw.get("type")
            original_bytes = frame.raw.get("originalBytes")
            type_label = frame_type if isinstance(frame_type, str) else "native"
            size_label = (
                f" ({original_bytes} bytes)"
                if isinstance(original_bytes, int) and not isinstance(original_bytes, bool)
                else ""
            )
            outputs: list[MapperOutput] = [
                MappedUnknownVendor(
                    item_id=f"vendor-{self._identity.bridge_epoch[:12]}-{frame.sequence}",
                    vendor_type=f"{self._mapper.harness_id}:evidence-truncated",
                    safe_summary=(
                        f"oversized {type_label} frame clipped to the evidence budget{size_label}"
                    ),
                    turn_id=None,
                    created_at=frame.created_at,
                )
            ]
        else:
            try:
                outputs = self._mapper.map_evidence_frame(
                    frame,
                    evidence_ref=evidence_ref,
                    parent_thread_id=self._identity.vendor_conversation_id,
                )
            except UnmappableShape:
                outputs = [
                    MappedUnknownVendor(
                        item_id=f"vendor-{self._identity.bridge_epoch[:12]}-{frame.sequence}",
                        vendor_type=f"{self._mapper.harness_id}:malformed",
                        safe_summary="native frame failed exact schema parsing",
                        turn_id=None,
                        created_at=frame.created_at,
                    )
                ]
        agent_thread = self._agents.frame_thread(frame)
        if agent_thread is not None:
            outputs = [self._agents.bind_thread(output, agent_thread) for output in outputs]
        return [self._agents.reconcile_roster(output) for output in outputs]

    def record_live_turn(self, outputs: list[MapperOutput], bucket: str | None) -> None:
        live_turns = self.live_turn_ids.setdefault(bucket, set())
        live_requests = self.live_request_ids.setdefault(bucket, set())
        for output in outputs:
            if isinstance(output, MappedItem):
                if output.item.kind in _CONTENT_ITEM_KINDS and output.item.turn_id:
                    live_turns.add(output.item.turn_id)
                correlation = output.item.correlation
                if output.item.role == "user" and correlation and correlation.request_id:
                    live_requests.add(correlation.request_id)
            elif isinstance(output, MappedUnknownVendor) and output.turn_id:
                live_turns.add(output.turn_id)

    def suppress_live_twins(
        self, outputs: list[MapperOutput], live_native_turns: set[str], bucket: str | None
    ) -> list[MapperOutput]:
        kept: list[MapperOutput] = []
        live_turn_ids = self.live_turn_ids.get(bucket, ())
        live_request_ids = self.live_request_ids.get(bucket, ())
        for output in outputs:
            turn_id: str | None = None
            request_id: str | None = None
            is_user = False
            if isinstance(output, MappedItem):
                turn_id = output.item.turn_id
                if output.item.role == "user" and output.item.correlation is not None:
                    request_id = output.item.correlation.request_id
                    is_user = True
            elif isinstance(output, MappedUnknownVendor):
                turn_id = output.turn_id
            duplicate = (
                (turn_id is not None and turn_id in live_turn_ids)
                or (is_user and request_id is not None and request_id in live_request_ids)
                or (turn_id is not None and turn_id in live_native_turns)
            )
            if duplicate:
                if turn_id is not None:
                    live_native_turns.add(turn_id)
                continue
            kept.append(output)
        return kept

    async def poll_native_continuation(self) -> None:
        if not self._mapper.eager_native_continuation:
            return
        while True:
            page: NativeEvidencePage = await asyncio.to_thread(
                self._read_native_page,
                self._entry,
                cursor=self.native_cursor,
                limit=NATIVE_PAGE_LIMIT,
                expected_bridge_epoch=self._identity.bridge_epoch,
            )
            if not page.frames:
                self.native_complete = True
                return
            for frame in page.frames:
                ref = self._refs.native(frame.native_id)
                outputs = [
                    self._agents.reconcile_roster(output)
                    for output in self._mapper.map_native_frame(frame, evidence_ref=ref)
                ]
                self._stream.apply_outputs(outputs, ref)
            self.native_cursor = page.next_cursor
            if page.next_cursor is None:
                self.native_complete = True
                return
