"""Active session projector engine (260718-CHATS-L1, R2/R4).

One projector per running session: it hydrates the projection from native
authority (native pages where the harness has them, the live evidence window
otherwise), keeps it current through bounded IPC polls, mints the totally
ordered event stream with bounded retention, and closes established streams
with one typed ``gap`` on epoch/generation loss. The 1,000-entry
TranscriptEntry deque is never paged as history authority; Claude user
submissions arrive only through the adapter's exact submission echo, zipped
into the frame order by turn without ever consulting timestamps.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError

from agents_remember.errors import (
    AgentsRememberError,
    HarnessBridgeEpochMismatchError,
    HarnessControlError,
)
from agents_remember.serving.conversation.active.cursor import (
    mint_event_cursor,
)
from agents_remember.serving.conversation.active.status import (
    ConversationStatusService,
    TurnTerminalEvidence,
)
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
    ChoiceOption,
    ChoicesBlock,
    ConversationCorrelation,
    ConversationEventEnvelope,
    ConversationItem,
    ConversationMutation,
    ConversationStatus,
    GapMutation,
    ProvenanceEvidence,
    StatusMutation,
    TextBlock,
    UpsertItemMutation,
)
from agents_remember.serving.conversation.projectors import HarnessProjector
from agents_remember.serving.conversation.projectors.common import (
    MappedBlockDelta,
    MappedItem,
    MappedTurnOutcome,
    MappedUnknownVendor,
    MapperOutput,
    UnmappableShape,
)
from agents_remember.serving.harness_control_client import (
    ControlledSession,
    read_control_evidence,
    read_control_native_page,
    read_control_snapshot,
    read_control_transcript,
    read_submission_provenance,
)
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    EvidenceFrame,
    EvidencePage,
    NativeEvidencePage,
    PendingInteraction,
    SubmissionProvenanceBatch,
)

Clock = Callable[[], str]
GapReason = Literal[
    "retention-overflow", "generation-changed", "projector-restart", "ordering-fault"
]
EvidenceReader = Callable[..., EvidencePage]
NativePageReaderFn = Callable[..., NativeEvidencePage]
TranscriptReader = Callable[..., tuple[Mapping[str, object], ...]]
ProvenanceReader = Callable[..., SubmissionProvenanceBatch]
SnapshotReader = Callable[..., AdapterSnapshot]

POLL_INTERVAL_SECONDS = 1.0
EVIDENCE_PAGE_LIMIT = 500
NATIVE_PAGE_LIMIT = 200
RETENTION_LIMIT = 1000
SUBSCRIBER_QUEUE_LIMIT = 256
CONSUMER_TTL_SECONDS = 30.0
MAX_CONSECUTIVE_READ_FAILURES = 5
MAX_PROVENANCE_BATCH = 64

CLOSE_SENTINEL = object()
"""Queue terminal marker: the subscription ends after this, never an item."""


class ZipperEvidenceEvicted(AgentsRememberError):
    """Evidence eviction voided the echo zipper's turn-order guarantee.

    Claude submissions merge two channels by strict turn order; once the
    evidence window evicts a frame, a pending result can be lost and the merge
    would silently invert items. This is a hard stop: the established stream
    receives one ``ordering-fault`` gap and the next page rehydrates from the
    remaining window with honest completeness.
    """


@dataclass(frozen=True)
class PageResult:
    """The atomic page assembly inputs a route serializes."""

    items: tuple[ConversationItem, ...]
    older_ordinal: int | None
    has_older: bool
    total_items: int | None
    event_cursor: ActiveEventCursor
    hydration_id: str
    status: ConversationStatus
    snapshot: AdapterSnapshot


class ActiveSessionProjector:
    """The live projector for one exact running session/epoch."""

    def __init__(
        self,
        *,
        identity: ActiveConversationRef,
        authorization: AuthorizationBinding,
        entry: ControlledSession,
        mapper: HarnessProjector,
        secret: bytes,
        clock: Clock,
        evidence_reader: EvidenceReader = read_control_evidence,
        native_page_reader: NativePageReaderFn = read_control_native_page,
        transcript_reader: TranscriptReader = read_control_transcript,
        provenance_reader: ProvenanceReader = read_submission_provenance,
        snapshot_reader: SnapshotReader = read_control_snapshot,
    ) -> None:
        self._identity = identity
        self._authorization = authorization
        self._entry = entry
        self._mapper = mapper
        self._secret = secret
        self._clock = clock
        self._read_evidence = evidence_reader
        self._read_native_page = native_page_reader
        self._read_transcript = transcript_reader
        self._read_provenance = provenance_reader
        self._read_snapshot = snapshot_reader
        self._status = ConversationStatusService(identity, clock=clock)
        self._generation = uuid4().hex
        self._store = ProjectionStore()
        self._sequence = 0
        self._retention: list[ConversationEventEnvelope] = []
        self._retention_floor = 0
        self._subscribers: set[asyncio.Queue[object]] = set()
        self._hydrated = False
        self._hydration_lock = asyncio.Lock()
        self._apply_lock = asyncio.Lock()
        self._poll_task: asyncio.Task[None] | None = None
        self._consumer_until = 0.0
        self._closed = False
        self._evidence_after = 0
        self._transcript_after = 0
        self._evidence_window_complete = True
        self._native_cursor: str | None = None
        self._native_complete = not mapper.uses_native_pages
        self._native_dirty = False
        self._pending_terminal: TurnTerminalEvidence | None = None
        self._snapshot: AdapterSnapshot | None = None
        self._pending_interaction_id: str | None = None
        self._status_revision_emitted = 0
        self._consecutive_failures = 0
        self._pending_frames: list[EvidenceFrame] = []
        self._echo_turn_open = False
        self._known_eviction_floor = 0
        # 260718-CHATS-L5 F1: turns/requests already carried by the live window, so the lazy
        # native-tip re-walk never re-projects them as disjoint-id native-history twins.
        self._live_turn_ids: set[str] = set()
        self._live_request_ids: set[str] = set()

    @property
    def generation(self) -> str:
        return self._generation

    def matches(self, identity: ActiveConversationRef) -> bool:
        """One projector per exact session/epoch/native conversation."""

        return (
            self._identity.ar_session_id == identity.ar_session_id
            and self._identity.bridge_epoch == identity.bridge_epoch
            and self._identity.vendor_conversation_id == identity.vendor_conversation_id
            and not self._closed
        )

    @property
    def retention_floor(self) -> int:
        return self._retention_floor

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def page(self, *, before_ordinal: int | None, limit: int) -> PageResult:
        """Assemble one atomic page + event cursor; never from event retention."""

        await self._ensure_hydrated()
        async with self._apply_lock:
            await self._refresh_native_tip()
            total_known = self._native_complete and self._evidence_window_complete
            window = self._store.page(
                before_ordinal=before_ordinal, limit=limit, total_known=total_known
            )
            self.touch()
            assert self._snapshot is not None
            return PageResult(
                items=window.items,
                older_ordinal=window.older_ordinal,
                has_older=window.has_older,
                total_items=window.total_items,
                event_cursor=self._event_cursor(self._sequence),
                hydration_id=f"hydration-{uuid4().hex}",
                status=self._status.current(),
                snapshot=self._snapshot,
            )

    def subscribe(self) -> asyncio.Queue[object]:
        """Attach one live subscriber; resume pre-checks happen at the route."""

        self.touch()
        queue: asyncio.Queue[object] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_LIMIT)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[object]) -> None:
        self._subscribers.discard(queue)

    def retained_after(self, sequence: int) -> tuple[ConversationEventEnvelope, ...]:
        return tuple(envelope for envelope in self._retention if envelope.sequence > sequence)

    def touch(self) -> None:
        self._consumer_until = asyncio.get_running_loop().time() + CONSUMER_TTL_SECONDS
        self._ensure_polling()

    async def close(self) -> None:
        self._closed = True
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        for queue in tuple(self._subscribers):
            self._offer(queue, CLOSE_SENTINEL)
        self._subscribers.clear()

    async def poll_once(self) -> None:
        """One bounded observation cycle over the production IPC seam."""

        await self._ensure_hydrated()
        async with self._apply_lock:
            await self._poll_evidence()
            await self._poll_transcript_echo()
            await self._poll_native_continuation()
            await self._resolve_provenance()
            snapshot = await asyncio.to_thread(self._read_snapshot, self._entry)
            self._apply_interaction_transitions(snapshot)
            status = self._status.observe(
                snapshot,
                self._mapper.harness_id,
                terminal=self._pending_terminal,
            )
            self._pending_terminal = None
            self._snapshot = snapshot
            if status.revision > self._status_revision_emitted:
                self._status_revision_emitted = status.revision
                self._emit(StoreMutation(kind="status"), status=status)
            self._consecutive_failures = 0

    # -- hydration -------------------------------------------------------

    async def _ensure_hydrated(self) -> None:
        if self._hydrated:
            return
        async with self._hydration_lock:
            if self._hydrated:
                return
            await self._rebuild()
            self._hydrated = True

    async def _rebuild(self) -> None:
        """Re-derive the whole projection from native authority; never invent."""

        self._store = ProjectionStore()
        self._sequence = 0
        self._retention.clear()
        self._retention_floor = 0
        self._pending_frames.clear()
        self._echo_turn_open = False
        # The rebuild re-derives from native authority first, then the live window; the live-turn
        # sets must start empty so the hydration walk projects prior-session native history in full
        # (F1 suppression only ever applies to turns this run has already observed live).
        self._live_turn_ids.clear()
        self._live_request_ids.clear()
        if self._mapper.uses_native_pages:
            await self._walk_native_pages(preserve_cursor_on_failure=False)
        await self._poll_evidence()
        await self._poll_transcript_echo()
        await self._poll_native_continuation()
        await self._resolve_provenance()
        snapshot = await asyncio.to_thread(self._read_snapshot, self._entry)
        self._apply_interaction_transitions(snapshot)
        self._snapshot = snapshot
        self._status.observe(snapshot, self._mapper.harness_id)

    async def _walk_native_pages(self, *, preserve_cursor_on_failure: bool) -> None:
        prior = self._native_cursor
        self._native_cursor = None
        live_native_turns: set[str] = set()
        try:
            while True:
                page: NativeEvidencePage = await asyncio.to_thread(
                    self._read_native_page,
                    self._entry,
                    cursor=self._native_cursor,
                    limit=NATIVE_PAGE_LIMIT,
                    expected_bridge_epoch=self._identity.bridge_epoch,
                )
                for frame in page.frames:
                    ref = self._native_ref(frame.native_id)
                    outputs = self._drop_live_settled_natives(
                        self._mapper.map_native_frame(frame, evidence_ref=ref),
                        live_native_turns,
                    )
                    self._apply_outputs(outputs, ref)
                    self._native_cursor = frame.native_id
                if (page.next_cursor is None and not page.truncated) or not page.frames:
                    break
            self._native_complete = True
        except HarnessControlError:
            # Native reads fail closed (ephemeral codex threads, unsupported
            # adapters): the live window remains, honestly partial.
            if preserve_cursor_on_failure:
                self._native_cursor = prior
            else:
                self._native_complete = False
                self._native_cursor = None

    async def _refresh_native_tip(self) -> None:
        if not self._native_dirty or not self._mapper.uses_native_pages:
            return
        if self._mapper.eager_native_continuation:
            return
        self._native_dirty = False
        await self._walk_native_pages(preserve_cursor_on_failure=True)

    # -- poll channels ----------------------------------------------------

    async def _poll_evidence(self) -> None:
        while True:
            page: EvidencePage = await asyncio.to_thread(
                self._read_evidence,
                self._entry,
                after_sequence=self._evidence_after,
                limit=EVIDENCE_PAGE_LIMIT,
                expected_bridge_epoch=self._identity.bridge_epoch,
            )
            if page.evicted_before_sequence > 0:
                self._evidence_window_complete = False
            if (
                self._mapper.uses_transcript_echo
                and self._hydrated
                and page.evicted_before_sequence > self._known_eviction_floor
            ):
                raise ZipperEvidenceEvicted(
                    "evidence window evicted frames; the echo zipper's turn "
                    "order is no longer provable"
                )
            self._known_eviction_floor = max(self._known_eviction_floor, page.evicted_before_sequence)
            for frame in page.frames:
                self._consume_evidence_frame(frame)
                self._evidence_after = frame.sequence
            caught_up = not page.frames or self._evidence_after >= page.latest_sequence
            if (not page.truncated and caught_up) or not page.frames:
                return

    def _consume_evidence_frame(self, frame: EvidenceFrame) -> None:
        if self._mapper.uses_transcript_echo:
            # Claude frames are zipped with the submission-echo channel in
            # turn order; they apply when the zipper reaches them.
            self._pending_frames.append(frame)
            return
        ref = self._evidence_ref(frame.sequence)
        outputs = self._map_evidence_frame(frame, ref)
        if self._mapper.uses_native_pages and not self._mapper.eager_native_continuation:
            # Lazy-native harness (codex): the live frame settles this turn under its live-id
            # namespace; remember it so the native-tip re-walk cannot mint a disjoint-id twin (F1).
            self._record_live_turn(outputs)
            self._native_dirty = True
        self._apply_outputs(outputs, ref)

    def _record_live_turn(self, outputs: list[MapperOutput]) -> None:
        """Record the live turns/requests a native re-walk must not re-project as twins (F1)."""

        for output in outputs:
            if isinstance(output, MappedItem):
                if output.item.turn_id:
                    self._live_turn_ids.add(output.item.turn_id)
                if (
                    output.item.role == "user"
                    and output.item.correlation is not None
                    and output.item.correlation.request_id
                ):
                    self._live_request_ids.add(output.item.correlation.request_id)
            elif isinstance(output, MappedTurnOutcome | MappedUnknownVendor):
                if output.turn_id:
                    self._live_turn_ids.add(output.turn_id)

    def _drop_live_settled_natives(
        self, outputs: list[MapperOutput], live_native_turns: set[str]
    ) -> list[MapperOutput]:
        """Drop native-page outputs for turns already carried by the live window (F1).

        On a hosted codex thread the live notification channel and ``thread/read`` use DISJOINT id
        namespaces for the same settled turn (live ``msg_*``/UUID item ids vs positional ``item-N``),
        so the store's id-keyed dedupe can never converge them and ``_refresh_native_tip``'s post-turn
        re-walk mints a second, native-history twin of every settled turn (L4 verdict F1, deterministic
        2/2 turns on the wire). The live window already carries each settled turn's full item payloads
        (see ``_CodexProjector``), so a native frame whose turn is already live is a pure duplicate.

        A native turn is "already live" when its parent turn id was observed on the live channel
        (``_live_turn_ids``) OR when its native user frame carries a ``clientId`` we submitted live
        (``_live_request_ids``); the latter holds even if the hosted thread renumbers turn ids, because
        ``clientId`` is our own request id and survives verbatim into ``thread/read``
        (``find_request_turn``). Once any frame identifies a native turn as live, its siblings are
        dropped through ``live_native_turns`` -- codex stores a turn's items user-first, so the user
        frame anchors the turn before its harness siblings. Genuine prior-session native history (never
        seen live) is untouched, and at hydration both live sets are empty (the native walk runs before
        the first evidence poll), so a resumed thread hydrates its full native history.
        """

        kept: list[MapperOutput] = []
        for output in outputs:
            turn_id: str | None = None
            request_id: str | None = None
            is_user_input = False
            if isinstance(output, MappedItem):
                turn_id = output.item.turn_id
                if output.item.role == "user" and output.item.correlation is not None:
                    request_id = output.item.correlation.request_id
                    is_user_input = True
            elif isinstance(output, MappedUnknownVendor):
                turn_id = output.turn_id
            live_by_turn = turn_id is not None and turn_id in self._live_turn_ids
            live_by_client = (
                is_user_input and request_id is not None and request_id in self._live_request_ids
            )
            live_by_sibling = turn_id is not None and turn_id in live_native_turns
            if live_by_turn or live_by_client or live_by_sibling:
                if turn_id is not None:
                    live_native_turns.add(turn_id)
                continue
            kept.append(output)
        return kept

    async def _poll_transcript_echo(self) -> None:
        if not self._mapper.uses_transcript_echo:
            return
        entries = await asyncio.to_thread(
            self._read_transcript,
            self._entry,
            after_sequence=self._transcript_after,
            limit=EVIDENCE_PAGE_LIMIT,
        )
        for entry in entries:
            # Only user submission echoes are consumed here. The claude transcript also carries
            # assistant and result entries (claude_stream_state emits role="assistant"/"result"),
            # but those are the evidence/terminal path's authority — claude has no user-frame
            # evidence, so the echo is the AR-side submission record. Feeding a non-user entry to
            # the user-only echo mapper is what minted the spurious "claude:echo: unrecognized
            # submission echo shape" rows in the developer's image3; advance past them without
            # minting or opening a turn boundary. (260718-CHATS-L5F R3)
            if entry.get("role") != "user":
                sequence = entry.get("sequence")
                if isinstance(sequence, int) and not isinstance(sequence, bool):
                    self._transcript_after = sequence
                continue
            # The adapter accepts one turn at a time, so an echo always
            # follows its turn's frames and the next echo always follows the
            # previous turn's result. While a turn is open, flush pending
            # frames through its result before emitting the next user.
            if self._echo_turn_open:
                self._echo_turn_open = False
                while self._pending_frames:
                    frame = self._pending_frames.pop(0)
                    self._apply_evidence_frame(frame)
                    if frame.raw.get("type") == "result":
                        break
                else:
                    # The previous turn's result never crossed (evidence
                    # eviction); the turn closes honestly without one.
                    pass
            ref = self._echo_ref(entry.get("sequence"))
            outputs: list[MapperOutput]
            try:
                outputs = self._mapper.map_transcript_echo(entry, evidence_ref=ref)
            except UnmappableShape:
                outputs = [
                    MappedUnknownVendor(
                        item_id=f"echo-{self._identity.bridge_epoch[:12]}-{entry.get('sequence')}",
                        vendor_type="claude:echo",
                        safe_summary="unrecognized submission echo shape",
                    )
                ]
            self._apply_outputs(outputs, ref)
            self._echo_turn_open = True
            sequence = entry.get("sequence")
            if isinstance(sequence, int) and not isinstance(sequence, bool):
                self._transcript_after = sequence
        # End of cycle: emit the open turn's frames, stopping after its
        # result so a future turn's frames stay pending for their own echo.
        while self._pending_frames:
            frame = self._pending_frames.pop(0)
            self._apply_evidence_frame(frame)
            if frame.raw.get("type") == "result":
                self._echo_turn_open = False
                break

    def _apply_evidence_frame(self, frame: EvidenceFrame) -> None:
        ref = self._evidence_ref(frame.sequence)
        self._apply_outputs(self._map_evidence_frame(frame, ref), ref)

    def _map_evidence_frame(
        self,
        frame: EvidenceFrame,
        evidence_ref: str,
    ) -> list[MapperOutput]:
        try:
            return self._mapper.map_evidence_frame(frame, evidence_ref=evidence_ref)
        except UnmappableShape:
            # A malformed known shape becomes preserved unknown-vendor
            # evidence; it never kills the stream and is never guessed.
            fallback: list[MapperOutput] = [
                MappedUnknownVendor(
                    item_id=f"vendor-{self._identity.bridge_epoch[:12]}-{frame.sequence}",
                    vendor_type=f"{self._mapper.harness_id}:malformed",
                    safe_summary="native frame failed exact schema parsing",
                    turn_id=None,
                    created_at=frame.created_at,
                )
            ]
            return fallback

    async def _poll_native_continuation(self) -> None:
        if not self._mapper.eager_native_continuation:
            return
        while True:
            page: NativeEvidencePage = await asyncio.to_thread(
                self._read_native_page,
                self._entry,
                cursor=self._native_cursor,
                limit=NATIVE_PAGE_LIMIT,
                expected_bridge_epoch=self._identity.bridge_epoch,
            )
            if not page.frames:
                self._native_complete = True
                return
            for frame in page.frames:
                ref = self._native_ref(frame.native_id)
                self._apply_outputs(
                    self._mapper.map_native_frame(frame, evidence_ref=ref), ref
                )
                self._native_cursor = frame.native_id
            if page.next_cursor is None:
                self._native_complete = True
                return

    async def _resolve_provenance(self) -> None:
        pending = self._store.pending_request_ids()[:MAX_PROVENANCE_BATCH]
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
            for mutation in self._store.apply_provenance(record.request_id, source):
                self._emit(mutation)

    # -- mutation application --------------------------------------------

    def _apply_outputs(self, outputs: list[MapperOutput], evidence_ref: str) -> None:
        for output in outputs:
            if isinstance(output, MappedItem):
                for mutation in self._store.apply_item(output):
                    self._emit(mutation)
            elif isinstance(output, MappedBlockDelta):
                for mutation in self._store.apply_delta(output):
                    self._emit(mutation)
            elif isinstance(output, MappedUnknownVendor):
                for mutation in self._store.apply_item(
                    unknown_vendor_item(output, evidence_ref=evidence_ref)
                ):
                    self._emit(mutation)
            elif isinstance(output, MappedTurnOutcome):
                self._pending_terminal = TurnTerminalEvidence(
                    outcome=output.outcome,
                    turn_id=output.turn_id,
                    stop_reason=output.stop_reason,
                )

    def _apply_interaction_transitions(self, snapshot: AdapterSnapshot) -> None:
        pending = snapshot.pending_interaction
        pending_id = pending.interaction_id if pending is not None else None
        if pending_id == self._pending_interaction_id:
            return
        if pending is not None:
            self._pending_interaction_id = pending_id
            self._upsert_interaction(pending)
            return
        if self._pending_interaction_id is not None:
            interaction_id = self._pending_interaction_id
            self._pending_interaction_id = None
            self._resolve_interaction(interaction_id)

    def _upsert_interaction(self, pending: PendingInteraction) -> None:
        blocks: list = [TextBlock(block_id="prompt", text=pending.prompt)]
        if pending.choices:
            blocks.append(
                ChoicesBlock(
                    block_id="choices",
                    interaction_id=pending.interaction_id,
                    options=tuple(
                        ChoiceOption(option_id=choice, label=choice)
                        for choice in pending.choices
                    ),
                )
            )
        item = ConversationItem(
            item_id=pending.interaction_id,
            revision=1,
            global_ordinal=1,
            lane="interaction",
            source="harness-live",
            provenance=ProvenanceEvidence(
                strength="exact",
                origin="adapter pending-interaction authority",
                producer="harness",
                observed_at=pending.created_at,
            ),
            role="system",
            kind="interaction",
            phase="waiting",
            blocks=tuple(blocks),
            correlation=ConversationCorrelation(interaction_id=pending.interaction_id),
            created_at=pending.created_at,
        )
        for mutation in self._store.apply_item(MappedItem(item=item)):
            self._emit(mutation)

    def _resolve_interaction(self, interaction_id: str) -> None:
        current = next(
            (item for item in self._store.items() if item.item_id == interaction_id),
            None,
        )
        if current is None:
            return
        resolved = current.model_copy(
            update={
                "phase": "unknown",
                "provenance": current.provenance.model_copy(
                    update={"reason": "interaction cleared without observable outcome"}
                ),
            }
        )
        for mutation in self._store.apply_item(MappedItem(item=resolved)):
            self._emit(mutation)

    # -- envelope minting, retention, fan-out ------------------------------

    def _emit(self, mutation: StoreMutation, *, status: ConversationStatus | None = None) -> None:
        conversation_mutation = self._conversation_mutation(mutation, status=status)
        if conversation_mutation is None:
            return
        envelope = self._mint_envelope(conversation_mutation)
        self._publish(envelope)

    def _mint_envelope(self, mutation: ConversationMutation) -> ConversationEventEnvelope:
        self._sequence += 1
        return ConversationEventEnvelope(
            identity=self._identity,
            cursor=self._event_cursor(self._sequence),
            previous_cursor=self._event_cursor(self._sequence - 1)
            if self._sequence > 1
            else None,
            sequence=self._sequence,
            event_id=f"{self._generation}:{self._sequence}",
            emitted_at=self._clock(),
            delivery="live",
            mutation=mutation,
        )

    def _conversation_mutation(
        self,
        mutation: StoreMutation,
        *,
        status: ConversationStatus | None,
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
        if mutation.kind == "append-block-delta":
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
        return None

    def _publish(self, envelope: ConversationEventEnvelope) -> None:
        self._retain(envelope)
        overflow_gap: ConversationEventEnvelope | None = None
        for queue in tuple(self._subscribers):
            if queue.full():
                self._subscribers.discard(queue)
                if overflow_gap is None:
                    # One gap per overflow sweep, retained like every stream
                    # event: the sequence chain stays hole-free for all other
                    # consumers, and resumers replaying it reset honestly.
                    overflow_gap = self._gap_envelope("retention-overflow")
                    self._retain(overflow_gap)
                # Evict the oldest queued item so exactly one typed gap and
                # the close sentinel are actually delivered to this consumer.
                self._offer_evicting(queue, overflow_gap)
                self._offer_evicting(queue, CLOSE_SENTINEL)
                continue
            self._offer(queue, envelope)

    def _retain(self, envelope: ConversationEventEnvelope) -> None:
        self._retention.append(envelope)
        if len(self._retention) > RETENTION_LIMIT:
            self._retention.pop(0)
            self._retention_floor = self._retention[0].sequence - 1 if self._retention else 0

    def _gap_envelope(self, reason: GapReason) -> ConversationEventEnvelope:
        self._sequence += 1
        return ConversationEventEnvelope(
            identity=self._identity,
            cursor=self._event_cursor(self._sequence),
            previous_cursor=self._event_cursor(self._sequence - 1)
            if self._sequence > 1
            else None,
            sequence=self._sequence,
            event_id=f"{self._generation}:{self._sequence}",
            emitted_at=self._clock(),
            delivery="live",
            mutation=GapMutation(
                requested_after=self._event_cursor(self._sequence - 1)
                if self._sequence > 1
                else self._event_cursor(0),
                reason=reason,
            ),
        )

    async def _gap(self, reason: GapReason) -> None:
        envelope = self._gap_envelope(reason)
        self._retain(envelope)
        for queue in tuple(self._subscribers):
            self._offer(queue, envelope)
            self._offer(queue, CLOSE_SENTINEL)
        self._subscribers.clear()
        self._closed = True

    def _offer(self, queue: asyncio.Queue[object], item: object) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(item)

    def _offer_evicting(self, queue: asyncio.Queue[object], item: object) -> None:
        """Deliver even to a full queue: evict the oldest item, never the signal."""

        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(item)

    def _event_cursor(self, sequence: int) -> ActiveEventCursor:
        return mint_event_cursor(
            self._secret,
            self._authorization,
            self._identity,
            generation=self._generation,
            sequence=max(0, sequence),
        )

    # -- poll loop ----------------------------------------------------------

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
                    not self._subscribers
                    and asyncio.get_running_loop().time() > self._consumer_until
                ):
                    # Dormant: no subscribers past the consumer TTL. Release the heavy per-session
                    # projection now instead of lingering as a registered tombstone until 32-LRU
                    # eviction (260718-CHATS-L5F R5).
                    self._release_dormant_state()
                    break
                try:
                    await self.poll_once()
                except HarnessBridgeEpochMismatchError:
                    await self._gap("generation-changed")
                    return
                except ZipperEvidenceEvicted:
                    await self._gap("ordering-fault")
                    return
                except (ValidationError, UnmappableShape):
                    await self._gap("ordering-fault")
                    return
                except HarnessControlError:
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= MAX_CONSECUTIVE_READ_FAILURES:
                        # Sustained loss of the native authority: one typed
                        # gap, then close; the next page reports the truth.
                        await self._gap("generation-changed")
                        return
        except asyncio.CancelledError:
            return
        finally:
            self._poll_task = None

    def _release_dormant_state(self) -> None:
        """Release the heavy per-session projection when the projector goes dormant (L5F R5).

        Clears the full ProjectionStore items, the L5 live-turn/request id-sets, the retained SSE
        envelopes, and any pending frames, and retires the shell (``_closed``) so the next access
        re-creates a fresh projector (``matches`` returns False while closed). The memory is freed
        immediately rather than held resident in a dead-session tombstone until LRU eviction.
        """

        self._closed = True
        self._store = ProjectionStore()
        self._retention.clear()
        self._retention_floor = 0
        self._pending_frames.clear()
        self._live_turn_ids.clear()
        self._live_request_ids.clear()

    # -- evidence refs -------------------------------------------------------

    def _evidence_ref(self, sequence: int) -> str:
        return f"ar-ev:{self._identity.bridge_epoch[:12]}:{sequence}"

    def _native_ref(self, native_id: str) -> str:
        return f"ar-native:{self._identity.bridge_epoch[:12]}:{native_id}"

    def _echo_ref(self, sequence: object) -> str:
        return f"ar-echo:{self._identity.bridge_epoch[:12]}:{sequence}"


__all__ = ["ActiveSessionProjector", "PageResult"]
