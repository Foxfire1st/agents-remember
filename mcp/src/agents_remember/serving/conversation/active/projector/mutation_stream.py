"""Canonical store mutation, cursor, retention, and subscriber fan-out."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Literal
from uuid import uuid4

from agents_remember.serving.conversation.active.cursor import mint_event_cursor
from agents_remember.serving.conversation.active.status import TurnTerminalEvidence
from agents_remember.serving.conversation.active.store import (
    ProjectionStore,
    StoreMutation,
    unknown_vendor_item,
)
from agents_remember.serving.conversation.models import (
    ActiveConversationRef,
    ActiveEventCursor,
    AppendBlockDeltaMutation,
    AppendItemMutation,
    AuthorizationBinding,
    ConversationEventEnvelope,
    ConversationMutation,
    ConversationStatus,
    GapMutation,
    StatusMutation,
    UpsertItemMutation,
)
from agents_remember.serving.conversation.projectors.common import (
    MappedBlockDelta,
    MappedItem,
    MappedTurnOutcome,
    MappedUnknownVendor,
    MapperOutput,
)

Clock = Callable[[], str]
GapReason = Literal[
    "retention-overflow", "generation-changed", "projector-restart", "ordering-fault"
]

RETENTION_LIMIT = 1000
SUBSCRIBER_QUEUE_LIMIT = 256
CLOSE_SENTINEL = object()


class ProjectionMutationStream:
    """Own the canonical projection and its totally ordered live mutation stream."""

    def __init__(
        self,
        *,
        identity: ActiveConversationRef,
        authorization: AuthorizationBinding,
        secret: bytes,
        clock: Clock,
    ) -> None:
        self._identity = identity
        self._authorization = authorization
        self._secret = secret
        self._clock = clock
        self.generation = uuid4().hex
        self.store = ProjectionStore()
        self.sequence = 0
        self.retention: list[ConversationEventEnvelope] = []
        self.retention_floor = 0
        self.subscribers: set[asyncio.Queue[object]] = set()
        self.pending_terminal: TurnTerminalEvidence | None = None

    def reset_projection(self) -> None:
        self.store = ProjectionStore()
        self.sequence = 0
        self.retention.clear()
        self.retention_floor = 0
        self.pending_terminal = None

    def release_projection(self) -> None:
        self.store = ProjectionStore()
        self.retention.clear()
        self.retention_floor = 0
        self.pending_terminal = None

    def apply_outputs(self, outputs: list[MapperOutput], evidence_ref: str) -> None:
        for output in outputs:
            if isinstance(output, MappedItem):
                self._emit_store_mutations(self.store.apply_item(output))
            elif isinstance(output, MappedBlockDelta):
                self._emit_store_mutations(self.store.apply_delta(output))
            elif isinstance(output, MappedUnknownVendor):
                self._emit_store_mutations(
                    self.store.apply_item(unknown_vendor_item(output, evidence_ref=evidence_ref))
                )
            elif isinstance(output, MappedTurnOutcome):
                self.pending_terminal = TurnTerminalEvidence(
                    outcome=output.outcome,
                    turn_id=output.turn_id,
                    stop_reason=output.stop_reason,
                )

    def consume_terminal(self) -> TurnTerminalEvidence | None:
        terminal = self.pending_terminal
        self.pending_terminal = None
        return terminal

    def emit_status(self, status: ConversationStatus) -> None:
        self.emit(StoreMutation(kind="status"), status=status)

    def emit(
        self, mutation: StoreMutation, *, status: ConversationStatus | None = None
    ) -> None:
        public = _conversation_mutation(mutation, status=status)
        if public is not None:
            self._publish(self._mint_envelope(public))

    def _emit_store_mutations(self, mutations: list[StoreMutation]) -> None:
        for mutation in mutations:
            self.emit(mutation)

    def subscribe(self) -> asyncio.Queue[object]:
        queue: asyncio.Queue[object] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_LIMIT)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[object]) -> None:
        self.subscribers.discard(queue)

    def retained_after(self, sequence: int) -> tuple[ConversationEventEnvelope, ...]:
        return tuple(envelope for envelope in self.retention if envelope.sequence > sequence)

    def event_cursor(self, sequence: int | None = None) -> ActiveEventCursor:
        return mint_event_cursor(
            self._secret,
            self._authorization,
            self._identity,
            generation=self.generation,
            sequence=max(0, self.sequence if sequence is None else sequence),
        )

    async def gap(self, reason: GapReason) -> None:
        envelope = self._gap_envelope(reason)
        self._retain(envelope)
        for queue in tuple(self.subscribers):
            _offer(queue, envelope)
            _offer(queue, CLOSE_SENTINEL)
        self.subscribers.clear()

    def close_subscribers(self) -> None:
        for queue in tuple(self.subscribers):
            _offer(queue, CLOSE_SENTINEL)
        self.subscribers.clear()

    def _mint_envelope(self, mutation: ConversationMutation) -> ConversationEventEnvelope:
        self.sequence += 1
        return ConversationEventEnvelope(
            identity=self._identity,
            cursor=self.event_cursor(),
            previous_cursor=self.event_cursor(self.sequence - 1),
            sequence=self.sequence,
            event_id=f"{self.generation}:{self.sequence}",
            emitted_at=self._clock(),
            delivery="live",
            mutation=mutation,
        )

    def _publish(self, envelope: ConversationEventEnvelope) -> None:
        self._retain(envelope)
        overflow_gap: ConversationEventEnvelope | None = None
        for queue in tuple(self.subscribers):
            if queue.full():
                self.subscribers.discard(queue)
                if overflow_gap is None:
                    overflow_gap = self._gap_envelope("retention-overflow")
                    self._retain(overflow_gap)
                _offer_evicting(queue, overflow_gap)
                _offer_evicting(queue, CLOSE_SENTINEL)
                continue
            _offer(queue, envelope)

    def _retain(self, envelope: ConversationEventEnvelope) -> None:
        self.retention.append(envelope)
        if len(self.retention) > RETENTION_LIMIT:
            self.retention.pop(0)
            self.retention_floor = self.retention[0].sequence - 1 if self.retention else 0

    def _gap_envelope(self, reason: GapReason) -> ConversationEventEnvelope:
        self.sequence += 1
        predecessor = self.event_cursor(self.sequence - 1)
        return ConversationEventEnvelope(
            identity=self._identity,
            cursor=self.event_cursor(),
            previous_cursor=predecessor,
            sequence=self.sequence,
            event_id=f"{self.generation}:{self.sequence}",
            emitted_at=self._clock(),
            delivery="live",
            mutation=GapMutation(requested_after=predecessor, reason=reason),
        )


def _conversation_mutation(
    mutation: StoreMutation, *, status: ConversationStatus | None
) -> ConversationMutation | None:
    if mutation.kind == "status":
        assert status is not None
        return StatusMutation(status=status)
    if mutation.kind == "append-item":
        assert mutation.item is not None
        return AppendItemMutation(item=mutation.item)
    if mutation.kind == "upsert-item":
        assert mutation.item is not None
        return UpsertItemMutation(item=mutation.item)
    if mutation.kind != "append-block-delta":
        return None
    assert (
        mutation.item_id is not None
        and mutation.block_id is not None
        and mutation.expected_revision is not None
        and mutation.next_revision is not None
        and mutation.delta is not None
    )
    return AppendBlockDeltaMutation(
        item_id=mutation.item_id,
        block_id=mutation.block_id,
        expected_revision=mutation.expected_revision,
        next_revision=mutation.next_revision,
        delta=mutation.delta,
    )


def _offer(queue: asyncio.Queue[object], item: object) -> None:
    with contextlib.suppress(asyncio.QueueFull):
        queue.put_nowait(item)


def _offer_evicting(queue: asyncio.Queue[object], item: object) -> None:
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(item)
