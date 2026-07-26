"""Active session projector engine.

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
from dataclasses import dataclass, replace
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
    ConversationAgentRef,
    ConversationAgentStatus,
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


class EvidenceTimelineRegressed(AgentsRememberError):
    """The bridge's evidence tip moved BACKWARD: the timeline was re-baselined.

    After an interrupt the runner can reset its evidence numbering; the projector's consumed
    watermark then exceeds the new tip, ``caught_up`` computes true forever, and the projection
    silently freezes on its pre-reset window while the session keeps running. A regressed tip is
    never healthy: gap out so the next page rebuilds against the re-baselined timeline.
    """


_TURN_BODY_FRAME_TYPES = frozenset({"assistant", "user"})
"""Claude wire types that occur ONLY inside a turn body: model output and tool-result carriers.

``user`` here is the non-replay tool-result carrier -- replayed user submissions are consumed by
the adapter (``_handle_replayed_user``) and never diverted to evidence, so a ``user`` evidence
frame is always in-turn.
"""

_TURN_OPENING_LIFECYCLE_STATES = frozenset({"queued", "started"})
"""The pre-result half of the 2.1.216+ slash-command lifecycle; ``completed`` closes a settled turn."""


_REGISTRY_AGENT_STATUS: dict[str, ConversationAgentStatus] = {
    "registered": "registered",
    "running": "running",
    "started": "running",
    "interacted": "running",
    "active": "running",
    "completed": "completed",
    "failed": "failed",
    "interrupted": "interrupted",
}
"""Adapter ``agentRegistry`` status vocabulary -> roster status.

Anything outside the table (``unresolved``, ``idle``) stays ``unknown``: the registry
entry exists but carries no honest lifecycle truth.
"""


def _opens_echo_turn_body(frame: EvidenceFrame) -> bool:
    """Whether a frame trailing the last result PROVES an in-flight turn body.

    An ALLOWLIST on purpose. The evicted-window realignment counts retained turn bodies to
    decide how many retained echoes are orphans, so a frame wrongly counted as an open body
    shifts the ENTIRE realigned tail one turn early -- every answer rendered above the user
    message that produced it, the newest user message orphaned answerless: exactly the
    ghost ``_realign_evicted_hydration_zip`` exists to eliminate. Frames trail the last result
    for reasons that have nothing to do with a new turn: a SETTLED slash-command turn's
    post-result ``command_lifecycle``/``completed`` close (the live 2.1.218 wire), rate-limit
    telemetry, an interrupt ``control_response`` that lost the race to natural completion and
    arrives unmatched after its turn's result (``claude_stream_state._handle_result`` pops the
    pending control entry when the turn settles, so the late answer crosses as generic
    evidence), and any frame type this mapper has never seen. A denylist re-admitted all of
    those; only the shapes the live wire proves are in-turn may count.

    Asymmetric on purpose too: over-counting shifts every retained turn, while under-counting
    (a genuinely in-flight body whose only retained frame is an unrecognized shape) misplaces
    at most the single newest turn -- the honest side to err on when the tail is ambiguous.
    """

    frame_type = frame.raw.get("type")
    if frame_type in _TURN_BODY_FRAME_TYPES:
        return True
    if frame_type == "command_lifecycle":
        return frame.raw.get("state") in _TURN_OPENING_LIFECYCLE_STATES
    return False


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


# Item kinds whose arrival proves a turn's content actually crossed live (used by
# the twin suppression; lifecycle-only kinds are deliberately absent).
_CONTENT_ITEM_KINDS = frozenset({"message", "tool-call", "tool-result", "thinking", "plan"})


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
        # Turns/requests already carried by the live window, so the lazy
        # native-tip re-walk never re-projects them as disjoint-id native-history twins.
        # Keyed per thread: ``None`` is the parent bucket (byte-identical
        # to the pre-multiplex flat sets); each agent thread gets its own so a parent native
        # re-walk can never be suppressed by an agent thread's colliding turn id.
        self._live_turn_ids: dict[str | None, set[str]] = {}
        self._live_request_ids: dict[str | None, set[str]] = {}
        # Agent threads that produced live-evidence items; only those are eligible for
        # a lazy per-thread native re-walk (``refresh_agent_native``).
        self._agent_threads_live: set[str] = set()
        # Agent threads already backfilled once by the page()-driven lazy native walk.
        self._agent_native_walked: set[str] = set()
        self._pending_agent_interaction_ids: set[str] = set()

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
            await self._backfill_agent_threads()
            # Completeness is one predicate over every channel that can still owe the store
            # content: the native history, the evidence window, AND the zipper's pending
            # frames (projected content the store cannot show until its echo arrives).
            # Routing the pend through `has_older` instead decorrelated
            # the base invariant `has_older <=> an older cursor exists` (store.py mints both
            # from one expression): the page shipped hasOlder with olderCursor absent, so the
            # browser rendered a live "Load older" button that refetches the newest page
            # forever. Pending frames are NEWER content -- the older-history channel cannot
            # carry them. Suppressing the total is the honest signal: the page stops asserting
            # "this is the whole history" without asserting an older page that does not exist.
            total_known = (
                self._native_complete
                and self._evidence_window_complete
                and not self._pending_frames
            )
            window = self._store.page(
                before_ordinal=before_ordinal, limit=limit, total_known=total_known
            )
            self.touch()
            assert self._snapshot is not None
            return PageResult(
                items=window.items,
                # Both minted by the store from one expression: hasOlder is true exactly when
                # an older cursor exists. Nothing may widen one without the other.
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
        # (the twin suppression only ever applies to turns this run has already observed live).
        self._live_turn_ids.clear()
        self._live_request_ids.clear()
        self._agent_threads_live.clear()
        self._agent_native_walked.clear()
        self._pending_agent_interaction_ids.clear()
        # Prime the snapshot BEFORE the evidence walk so multiplexed agent frames hydrate with
        # the identity the adapter's agentRegistry already bound; the fresh
        # read below still owns status/interaction authority.
        self._snapshot = await asyncio.to_thread(self._read_snapshot, self._entry)
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
        # The hydration baseline is delivered atomically with every page (page()),
        # so it is already held by any cursor holder: the first poll must not
        # re-emit it. A re-emitted envelope carries recomputed derived freshness
        # (observed_at/age_ms) at the SAME revision, which the browser reducer's
        # same-revision/different-payload guard treats as a protocol fault — a
        # deterministic re-page on every quiet fresh chat.
        self._status_revision_emitted = self._status.revision

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
                        None,
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

    async def _backfill_agent_threads(self) -> None:
        """One lazy native walk per live agent thread — agent chats get their content
        even when the live wire races or loses events.

        The vendor auto-attaches sub-agent listeners best-effort: a fast agent can
        outrun the attach, and events emitted before it never reach this connection
        (the 2026-07-26 live verification: roster and completion crossed, the
        agents' content did not). ``thread/read`` remains the authority — walk every
        live agent thread once per projector; a failed or refused walk (thread not
        yet loadable) stays unwalked so the next page retries. Idempotent through
        store dedupe + per-thread F1 buckets, and additive only: completeness
        bookkeeping stays parent-scoped exactly like the public seam.
        """

        if not self._mapper.uses_native_pages or self._mapper.eager_native_continuation:
            return
        for thread_id in sorted(self._agent_threads_live - self._agent_native_walked):
            if await self._walk_agent_native_pages(thread_id):
                self._agent_native_walked.add(thread_id)

    async def refresh_agent_native(self, thread_id: str) -> bool:
        """Lazily re-walk ONE agent thread's native history; True when the walk ran.

        Driven by ``page()``'s per-thread backfill loop (no longer a latent seam):
        agent threads are paged on demand through the ``read_native_page(thread_id=...)``
        channel once the agent produced live-evidence items. The default re-walk
        stays parent-only; completeness bookkeeping (``_native_complete``/
        ``total_known``) stays parent-scoped — an agent walk is additive and never
        widens the page's total assertion. Returns False when the thread is
        guard-refused (not yet live) or the native read failed closed, so the
        caller retries on a later page.
        """

        if (
            not self._mapper.uses_native_pages
            or self._mapper.eager_native_continuation
            or thread_id == self._identity.vendor_conversation_id
            or thread_id not in self._agent_threads_live
        ):
            return False
        await self._ensure_hydrated()
        async with self._apply_lock:
            return await self._walk_agent_native_pages(thread_id)

    async def _walk_agent_native_pages(self, thread_id: str) -> bool:
        """Full re-walk of one agent thread; idempotent through store dedupe + F1."""

        live_native_turns: set[str] = set()
        cursor: str | None = None
        try:
            while True:
                page: NativeEvidencePage = await asyncio.to_thread(
                    self._read_native_page,
                    self._entry,
                    cursor=cursor,
                    limit=NATIVE_PAGE_LIMIT,
                    expected_bridge_epoch=self._identity.bridge_epoch,
                    thread_id=thread_id,
                )
                for frame in page.frames:
                    # Agent threads can reuse the parent's positional native ids
                    # (``item-N``), so the evidence handle is thread-scoped.
                    ref = self._native_ref(f"{thread_id}:{frame.native_id}")
                    outputs = self._drop_live_settled_natives(
                        self._mapper.map_native_frame(frame, evidence_ref=ref),
                        live_native_turns,
                        thread_id,
                    )
                    # An agent walk is CONTENT backfill: roster mints from
                    # collab/subAgentActivity records belong to the live channel and
                    # the parent's native walk — nested-agent spawn records reference
                    # OTHER threads, and minting them here duplicates/misattributes
                    # roster rows (2026-07-26 live verification: parent -> 1128 ->
                    # {3be4, 440c} nesting minted a scoped duplicate for 1128).
                    outputs = [
                        output
                        for output in outputs
                        if not (
                            isinstance(output, MappedItem)
                            and output.item.item_id.startswith("codex-agent-")
                        )
                    ]
                    outputs = [self._scope_agent_native_item(output, thread_id) for output in outputs]
                    outputs = [self._bind_agent(output, thread_id) for output in outputs]
                    self._apply_outputs(outputs, ref)
                    cursor = frame.native_id
                if (page.next_cursor is None and not page.truncated) or not page.frames:
                    break
        except HarnessControlError:
            # Fail closed exactly like the parent walk: the live window remains,
            # honestly partial, and a later request can retry the walk.
            return False
        return True

    def _scope_agent_native_item(self, output: MapperOutput, thread_id: str) -> MapperOutput:
        """Scope an agent-thread native item's id to its thread.

        Positional native ids (``item-N``) are unique per thread, never across the
        multiplex: a forked agent thread replays the parent's items under the SAME
        ids, and the store's id-keyed dedupe would let the agent's copies overwrite
        the parent's items (the parent's content ended up carrying an agent's ref in
        the 2026-07-26 live verification). Thread-scoping makes every native item id
        unique by construction.
        """

        if not isinstance(output, MappedItem):
            return output
        item = output.item
        if item.item_id.startswith(f"{thread_id}:"):
            return output
        return MappedItem(item=item.model_copy(update={"item_id": f"{thread_id}:{item.item_id}"}))

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
            if self._hydrated and page.latest_sequence < self._evidence_after:
                raise EvidenceTimelineRegressed(
                    f"evidence tip regressed (latest {page.latest_sequence} < consumed "
                    f"{self._evidence_after}): the bridge re-baselined its timeline"
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
            # namespace; remember it so the native-tip re-walk cannot mint a disjoint-id twin.
            # Agent-thread frames record into their own bucket.
            self._record_live_turn(outputs, self._frame_agent_thread(frame))
            self._native_dirty = True
        self._apply_outputs(outputs, ref)

    def _frame_agent_thread(self, frame: EvidenceFrame) -> str | None:
        """The agent thread one frame belongs to; ``None`` = the parent conversation.

        The demux key is the frame's verbatim ``thread_id``; the parent is the
        projection identity's ``vendor_conversation_id``. Frames without a thread
        (or carrying the parent id) map to the parent bucket exactly as pre-multiplex.
        """

        thread_id = frame.thread_id
        if thread_id is None or thread_id == self._identity.vendor_conversation_id:
            return None
        return thread_id

    def _record_live_turn(self, outputs: list[MapperOutput], bucket: str | None) -> None:
        """Record the live turns/requests a native re-walk must not re-project as twins.

        A turn counts as live-settled only when its CONTENT crossed: lifecycle frames
        alone (turn-result, roster notices, turn outcomes) prove nothing about content
        delivery — a fast agent can emit lifecycle while its items never reach the
        connection, and the native backfill must still project those turns instead of
        suppressing them as twins (the 2026-07-26 live-verification gap).
        """

        live_turns = self._live_turn_ids.setdefault(bucket, set())
        live_requests = self._live_request_ids.setdefault(bucket, set())
        for output in outputs:
            if isinstance(output, MappedItem):
                if output.item.kind in _CONTENT_ITEM_KINDS and output.item.turn_id:
                    live_turns.add(output.item.turn_id)
                if (
                    output.item.role == "user"
                    and output.item.correlation is not None
                    and output.item.correlation.request_id
                ):
                    live_requests.add(output.item.correlation.request_id)
            elif isinstance(output, MappedUnknownVendor):
                if output.turn_id:
                    live_turns.add(output.turn_id)

    def _drop_live_settled_natives(
        self, outputs: list[MapperOutput], live_native_turns: set[str], bucket: str | None
    ) -> list[MapperOutput]:
        """Drop native-page outputs for turns already carried by the live window.

        On a hosted codex thread the live notification channel and ``thread/read`` use DISJOINT id
        namespaces for the same settled turn (live ``msg_*``/UUID item ids vs positional ``item-N``),
        so the store's id-keyed dedupe can never converge them and ``_refresh_native_tip``'s post-turn
        re-walk mints a second, native-history twin of every settled turn (deterministic
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
        live_turn_ids = self._live_turn_ids.get(bucket, ())
        live_request_ids = self._live_request_ids.get(bucket, ())
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
            live_by_turn = turn_id is not None and turn_id in live_turn_ids
            live_by_client = (
                is_user_input and request_id is not None and request_id in live_request_ids
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
        if not self._hydrated:
            # Hydration drains the retained window (live polls keep the one-page cadence):
            # the evicted-window realignment pairs echoes with bodies by count, so it must
            # see every retained submission, not just the first page.
            entries = await self._read_retained_transcript(entries)
        if not self._hydrated and not self._evidence_window_complete:
            entries = self._realign_evicted_hydration_zip(entries)
        for entry in entries:
            # Only user submission echoes are consumed here. The claude transcript also carries
            # assistant and result entries (claude_stream_state emits role="assistant"/"result"),
            # but those are the evidence/terminal path's authority — claude has no user-frame
            # evidence, so the echo is the AR-side submission record. Feeding a non-user entry to
            # the user-only echo mapper is what minted the spurious "claude:echo: unrecognized
            # submission echo shape" rows; advance past them without
            # minting or opening a turn boundary.
            if entry.get("role") != "user":
                self._advance_transcript_watermark(entry)
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
            self._apply_echo_item(entry)
            self._echo_turn_open = True
            self._advance_transcript_watermark(entry)
        # End of cycle: emit the open turn's frames, stopping after its
        # result so a future turn's frames stay pending for their own echo.
        while self._pending_frames:
            frame = self._pending_frames.pop(0)
            self._apply_evidence_frame(frame)
            if frame.raw.get("type") == "result":
                self._echo_turn_open = False
                break

    def _realign_evicted_hydration_zip(
        self, entries: tuple[Mapping[str, object], ...]
    ) -> tuple[Mapping[str, object], ...]:
        """Realign the echo/evidence tails when hydration meets an evicted evidence window.

        The echo zipper pairs each user submission with the turn body that follows it by strict
        alternation — sound only while both channels start at the same turn. The bridge's
        evidence deque (2,000 frames) prefix-evicts whole turn bodies far earlier than the
        transcript deque (1,000 entries) evicts their echoes, so a re-hydration after dormancy
        can retain echoes whose bodies are gone; the first retained echo then pairs with a
        LATER turn's body, pinning every retained answer a constant number of turns early and
        orphaning the newest user messages. Both channels
        keep suffix order, so the honest realignment is by turn count: leading echoes without
        a surviving body project as bare user items, leading bodies without a surviving echo
        project echo-less, and the paired tails zip in order. Live polling never realigns —
        eviction there gaps ordering-fault (ZipperEvidenceEvicted) instead.
        """
        echo_count = sum(1 for entry in entries if entry.get("role") == "user")
        result_indexes = [
            index
            for index, frame in enumerate(self._pending_frames)
            if frame.raw.get("type") == "result"
        ]
        trailing_start = result_indexes[-1] + 1 if result_indexes else 0
        trailing_open = any(
            _opens_echo_turn_body(frame) for frame in self._pending_frames[trailing_start:]
        )
        body_count = len(result_indexes) + (1 if trailing_open else 0)
        if echo_count <= body_count:
            # Leading bodies whose echoes aged out of the transcript first project echo-less;
            # with no retained echoes the whole retained window is echo-less, so it drains.
            flushed = 0
            while self._pending_frames and (flushed < body_count - echo_count or echo_count == 0):
                frame = self._pending_frames.pop(0)
                self._apply_evidence_frame(frame)
                if frame.raw.get("type") == "result":
                    flushed += 1
            return entries
        orphans = echo_count - body_count
        kept: list[Mapping[str, object]] = []
        for entry in entries:
            if entry.get("role") == "user" and orphans:
                orphans -= 1
                self._apply_echo_item(entry)
                self._advance_transcript_watermark(entry)
                continue
            kept.append(entry)
        return tuple(kept)

    async def _read_retained_transcript(
        self, first_page: tuple[Mapping[str, object], ...]
    ) -> tuple[Mapping[str, object], ...]:
        """Drain the retained transcript window (bounded by the bridge's 1,000-entry deque).

        Hydration only: the evicted-window realignment pairs retained echoes with retained
        turn bodies by count, so it must see EVERY retained submission echo — a single
        500-entry page of a longer retained window would undercount the echoes and misalign
        the zip it exists to fix. Pages chain off the last entry's sequence because the
        watermark advances only as entries are zipped.
        """

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

    def _apply_echo_item(self, entry: Mapping[str, object]) -> None:
        """Map one user submission echo and apply its item; turn state is the caller's."""

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

    def _advance_transcript_watermark(self, entry: Mapping[str, object]) -> None:
        sequence = entry.get("sequence")
        if isinstance(sequence, int) and not isinstance(sequence, bool):
            self._transcript_after = sequence

    def _apply_evidence_frame(self, frame: EvidenceFrame) -> None:
        ref = self._evidence_ref(frame.sequence)
        self._apply_outputs(self._map_evidence_frame(frame, ref), ref)


    def _map_evidence_frame(
        self,
        frame: EvidenceFrame,
        evidence_ref: str,
    ) -> list[MapperOutput]:
        if frame.raw.get("arEvidenceTruncated") is True:
            # The substrate's own clip envelope (an oversized frame bounded to the
            # evidence budget) is not a vendor shape — exact parsing would brand a
            # by-design truncation "malformed", so classify it honestly here for every
            # harness. The preserved `type` label names what was clipped.
            frame_type = frame.raw.get("type")
            original_bytes = frame.raw.get("originalBytes")
            type_label = frame_type if isinstance(frame_type, str) else "native"
            size_label = (
                f" ({original_bytes} bytes)"
                if isinstance(original_bytes, int) and not isinstance(original_bytes, bool)
                else ""
            )
            return [
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
        outputs: list[MapperOutput]
        try:
            outputs = self._mapper.map_evidence_frame(
                frame,
                evidence_ref=evidence_ref,
                # The multiplexed demux context: the parent thread id is
                # the projection identity's vendor conversation id.
                parent_thread_id=self._identity.vendor_conversation_id,
            )
        except UnmappableShape:
            # A malformed known shape becomes preserved unknown-vendor
            # evidence; it never kills the stream and is never guessed. The agent
            # binding below still applies: a malformed
            # AGENT-thread frame's evidence belongs to the agent's view.
            outputs = [
                MappedUnknownVendor(
                    item_id=f"vendor-{self._identity.bridge_epoch[:12]}-{frame.sequence}",
                    vendor_type=f"{self._mapper.harness_id}:malformed",
                    safe_summary="native frame failed exact schema parsing",
                    turn_id=None,
                    created_at=frame.created_at,
                )
            ]
        agent_thread = self._frame_agent_thread(frame)
        if agent_thread is not None:
            outputs = [self._bind_agent(output, agent_thread) for output in outputs]
        return outputs

    def _agent_ref(self, thread_id: str) -> ConversationAgentRef:
        """The agent identity the adapter's registry bound, or the honest bare id.

        Reads the LAST polled snapshot's ``raw['agentRegistry']``:
        ``agentPath``/status cross when the adapter bound them, and stay absent/
        ``unknown`` when it did not — a ref is never fabricated from the thread id
        alone beyond the id itself. The registry can lag the evidence stream by one
        poll cycle; roster items carry their own frame-derived status regardless.
        """

        agent_path: str | None = None
        status: ConversationAgentStatus = "unknown"
        snapshot = self._snapshot
        registry = snapshot.raw.get("agentRegistry") if snapshot is not None else None
        if isinstance(registry, Mapping):
            entry = registry.get(thread_id)
            if isinstance(entry, Mapping):
                path = entry.get("agentPath")
                if isinstance(path, str) and path:
                    agent_path = path
                entry_status = entry.get("status")
                if isinstance(entry_status, str):
                    status = _REGISTRY_AGENT_STATUS.get(entry_status, "unknown")
        return ConversationAgentRef(agent_id=thread_id, agent_path=agent_path, status=status)

    def _bind_agent(self, output: MapperOutput, thread_id: str) -> MapperOutput:
        """Attach the multiplexed agent ref to one mapped item.

        Items the mapper already tagged (roster rows, agent turn-results) keep their
        own evidence-bound ref; the registry only FILLS what that frame could not know
        (``agent_path``, a still-``unknown`` status). Preserved unknown-vendor evidence
        from the agent thread is tagged with the same ref.
        Deltas and outcomes pass through — they carry no item payload of their own.
        """

        if isinstance(output, MappedUnknownVendor):
            # A malformed AGENT-thread frame's preserved evidence belongs to the agent's
            # view too — never the parent's.
            self._agent_threads_live.add(thread_id)
            if output.agent is not None:
                return output
            return replace(output, agent=self._agent_ref(thread_id))
        if not isinstance(output, MappedItem):
            return output
        self._agent_threads_live.add(thread_id)
        item = output.item
        agent = item.agent
        if agent is None:
            return MappedItem(item=item.model_copy(update={"agent": self._agent_ref(thread_id)}))
        registry_ref = self._agent_ref(thread_id)
        updates: dict[str, object] = {}
        if agent.agent_path is None and registry_ref.agent_path is not None:
            updates["agent_path"] = registry_ref.agent_path
        if agent.status == "unknown" and registry_ref.status != "unknown":
            updates["status"] = registry_ref.status
        if not updates:
            return output
        return MappedItem(
            item=item.model_copy(update={"agent": agent.model_copy(update=updates)})
        )

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
        if pending_id != self._pending_interaction_id:
            previous_id = self._pending_interaction_id
            self._pending_interaction_id = pending_id
            if pending is not None:
                self._upsert_interaction(pending)
            if previous_id is not None:
                # The slot also ROTATES (oldest answered → next-oldest takes it):
                # the evicted id settled either way, not only when the slot empties.
                self._resolve_interaction(previous_id)
        self._apply_agent_interaction_transitions(snapshot, parent_pending_id=pending_id)

    def _apply_agent_interaction_transitions(
        self, snapshot: AdapterSnapshot, *, parent_pending_id: str | None
    ) -> None:
        """Multiplexed pending interactions.

        Every server request in ``snapshot.pending_interactions`` becomes an
        interaction-lane item: agent-thread requests carry their agent identity
        (``raw['threadId']`` + the adapter-bound ``raw['agentLabel']``), parent-thread
        requests beyond the singular slot's oldest project plainly (concurrent parent
        pendings are normal vendor traffic — the adapter keeps a per-thread pending
        map). The singular-slot entry (same interaction id) is never double-projected.
        Cleared interactions resolve per id, mirroring the singular path.
        """

        current: dict[str, tuple[PendingInteraction, str]] = {}
        for entry in snapshot.pending_interactions:
            if entry.interaction_id == parent_pending_id:
                continue
            thread_id = entry.raw.get("threadId")
            if not isinstance(thread_id, str) or not thread_id:
                continue
            current[entry.interaction_id] = (entry, thread_id)
        for interaction_id, (entry, thread_id) in current.items():
            if interaction_id in self._pending_agent_interaction_ids:
                continue
            self._pending_agent_interaction_ids.add(interaction_id)
            agent = (
                self._interaction_agent_ref(entry, thread_id)
                if thread_id != self._identity.vendor_conversation_id
                else None
            )
            self._upsert_interaction(entry, agent=agent)
        for interaction_id in sorted(
            self._pending_agent_interaction_ids - current.keys()
        ):
            self._pending_agent_interaction_ids.discard(interaction_id)
            if interaction_id == parent_pending_id:
                # Rotated OUT of the tuple INTO the singular slot: still live
                # there — the singular path owns its resolution.
                continue
            self._resolve_interaction(interaction_id)

    def _interaction_agent_ref(
        self, entry: PendingInteraction, thread_id: str
    ) -> ConversationAgentRef:
        """The agent label for one multiplexed interaction item: registry identity + adapter label."""

        registry_ref = self._agent_ref(thread_id)
        label = entry.raw.get("agentLabel")
        return ConversationAgentRef(
            agent_id=thread_id,
            agent_path=registry_ref.agent_path,
            nickname=label if isinstance(label, str) and label else None,
            status=registry_ref.status,
        )

    def _upsert_interaction(
        self, pending: PendingInteraction, *, agent: ConversationAgentRef | None = None
    ) -> None:
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
            agent=agent,
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
            # Genesis (sequence 1) chains to cursor 0, the stream origin a fresh
            # page already names: every live frame carries a real predecessor,
            # so the browser chain guard never re-pages on a healthy stream.
            previous_cursor=self._event_cursor(self._sequence - 1),
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
            previous_cursor=self._event_cursor(self._sequence - 1),
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
                    # eviction.
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
                        # Sustained loss of the native authority: one typed
                        # gap, then close; the next page reports the truth.
                        await self._gap("generation-changed")
                        return
        except asyncio.CancelledError:
            return
        finally:
            self._poll_task = None

    def _release_dormant_state(self) -> None:
        """Release the heavy per-session projection when the projector goes dormant.

        Clears the full ProjectionStore items, the live-turn/request id-sets, the retained SSE
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
        self._agent_threads_live.clear()
        self._agent_native_walked.clear()
        self._pending_agent_interaction_ids.clear()

    # -- evidence refs -------------------------------------------------------

    def _evidence_ref(self, sequence: int) -> str:
        return f"ar-ev:{self._identity.bridge_epoch[:12]}:{sequence}"

    def _native_ref(self, native_id: str) -> str:
        return f"ar-native:{self._identity.bridge_epoch[:12]}:{native_id}"

    def _echo_ref(self, sequence: object) -> str:
        return f"ar-echo:{self._identity.bridge_epoch[:12]}:{sequence}"


__all__ = ["ActiveSessionProjector", "PageResult"]
