"""Claude transcript-echo zipper and evicted-tail realignment."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping

from agents_remember.serving.conversation.projectors import HarnessProjector
from agents_remember.serving.conversation.projectors.common import (
    MappedUnknownVendor,
    MapperOutput,
    UnmappableShape,
)
from agents_remember.serving.harness_control_client import ControlledSession
from agents_remember.serving.harness_control_models import EvidenceFrame

from .mutation_stream import ProjectionMutationStream
from .native_ingestion import EVIDENCE_PAGE_LIMIT
from .references import ProjectionEvidenceRefs

TranscriptReader = Callable[..., tuple[Mapping[str, object], ...]]
EvidenceMapper = Callable[[EvidenceFrame, str], list[MapperOutput]]

_TURN_BODY_FRAME_TYPES = frozenset({"assistant", "user"})
_TURN_OPENING_LIFECYCLE_STATES = frozenset({"queued", "started"})


def _opens_turn_body(frame: EvidenceFrame) -> bool:
    frame_type = frame.raw.get("type")
    if frame_type in _TURN_BODY_FRAME_TYPES:
        return True
    return (
        frame_type == "command_lifecycle"
        and frame.raw.get("state") in _TURN_OPENING_LIFECYCLE_STATES
    )


class EchoIngestion:
    """Own the strict turn-order zip between Claude echoes and evidence frames."""

    def __init__(
        self,
        *,
        entry: ControlledSession,
        mapper: HarnessProjector,
        stream: ProjectionMutationStream,
        refs: ProjectionEvidenceRefs,
        transcript_reader: TranscriptReader,
        evidence_mapper: EvidenceMapper,
    ) -> None:
        self._entry = entry
        self._mapper = mapper
        self._stream = stream
        self._refs = refs
        self._read_transcript = transcript_reader
        self._map_evidence = evidence_mapper
        self.transcript_after = 0
        self.pending_frames: list[EvidenceFrame] = []
        self.turn_open = False

    def enqueue(self, frame: EvidenceFrame) -> None:
        self.pending_frames.append(frame)

    def reset(self) -> None:
        self.pending_frames.clear()
        self.turn_open = False

    def release(self) -> None:
        self.pending_frames.clear()

    async def poll(self, *, hydrated: bool, evidence_window_complete: bool) -> None:
        if not self._mapper.uses_transcript_echo:
            return
        entries = await asyncio.to_thread(
            self._read_transcript,
            self._entry,
            after_sequence=self.transcript_after,
            limit=EVIDENCE_PAGE_LIMIT,
        )
        if not hydrated:
            entries = await self._read_retained(entries)
        if not hydrated and not evidence_window_complete:
            entries = self._realign_evicted(entries)
        for entry in entries:
            if entry.get("role") != "user":
                self._advance(entry)
                continue
            if self.turn_open:
                self.turn_open = False
                while self.pending_frames:
                    frame = self.pending_frames.pop(0)
                    self._apply_frame(frame)
                    if frame.raw.get("type") == "result":
                        break
            self._apply_echo(entry)
            self.turn_open = True
            self._advance(entry)
        while self.pending_frames:
            frame = self.pending_frames.pop(0)
            self._apply_frame(frame)
            if frame.raw.get("type") == "result":
                self.turn_open = False
                break

    def _realign_evicted(
        self, entries: tuple[Mapping[str, object], ...]
    ) -> tuple[Mapping[str, object], ...]:
        echo_count = sum(1 for entry in entries if entry.get("role") == "user")
        result_indexes = [
            index
            for index, frame in enumerate(self.pending_frames)
            if frame.raw.get("type") == "result"
        ]
        trailing_start = result_indexes[-1] + 1 if result_indexes else 0
        trailing_open = any(
            _opens_turn_body(frame) for frame in self.pending_frames[trailing_start:]
        )
        body_count = len(result_indexes) + (1 if trailing_open else 0)
        if echo_count <= body_count:
            flushed = 0
            while self.pending_frames and (flushed < body_count - echo_count or echo_count == 0):
                frame = self.pending_frames.pop(0)
                self._apply_frame(frame)
                if frame.raw.get("type") == "result":
                    flushed += 1
            return entries
        orphans = echo_count - body_count
        kept: list[Mapping[str, object]] = []
        for entry in entries:
            if entry.get("role") == "user" and orphans:
                orphans -= 1
                self._apply_echo(entry)
                self._advance(entry)
                continue
            kept.append(entry)
        return tuple(kept)

    async def _read_retained(
        self, first_page: tuple[Mapping[str, object], ...]
    ) -> tuple[Mapping[str, object], ...]:
        entries = list(first_page)
        full_page = len(first_page) == EVIDENCE_PAGE_LIMIT
        while full_page:
            last = entries[-1].get("sequence")
            if not isinstance(last, int) or isinstance(last, bool):
                break
            page = await asyncio.to_thread(
                self._read_transcript,
                self._entry,
                after_sequence=last,
                limit=EVIDENCE_PAGE_LIMIT,
            )
            entries.extend(page)
            full_page = len(page) == EVIDENCE_PAGE_LIMIT
        return tuple(entries)

    def _apply_echo(self, entry: Mapping[str, object]) -> None:
        ref = self._refs.echo(entry.get("sequence"))
        outputs: list[MapperOutput]
        try:
            outputs = self._mapper.map_transcript_echo(entry, evidence_ref=ref)
        except UnmappableShape:
            outputs = [
                MappedUnknownVendor(
                    item_id=f"echo-{self._refs.epoch_prefix}-{entry.get('sequence')}",
                    vendor_type="claude:echo",
                    safe_summary="unrecognized submission echo shape",
                )
            ]
        self._stream.apply_outputs(outputs, ref)

    def _apply_frame(self, frame: EvidenceFrame) -> None:
        ref = self._refs.evidence(frame.sequence)
        self._stream.apply_outputs(self._map_evidence(frame, ref), ref)

    def _advance(self, entry: Mapping[str, object]) -> None:
        sequence = entry.get("sequence")
        if isinstance(sequence, int) and not isinstance(sequence, bool):
            self.transcript_after = sequence
