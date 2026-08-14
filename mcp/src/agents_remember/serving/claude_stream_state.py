"""Correlation ledger and normalized event state for one Claude stream-json process."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, replace

from agents_remember.errors import (
    HarnessAdapterBusyError,
    HarnessAdapterDisconnectedError,
    HarnessControlError,
)
from agents_remember.models.conversations.control_wire import (
    AcceptanceState,
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationRef,
    InterruptResult,
    SubmissionReceipt,
)
from agents_remember.models.conversations.evidence import (
    AR_EVIDENCE_KEY,
    AR_TERMINAL_OUTCOME_KEY,
)
from agents_remember.serving.claude_stream_limits import ClaudeAdapterLimits
from agents_remember.serving.claude_stream_protocol import (
    clip_transcript_text,
    command_unsupported_detail,
    correlation_envelope,
    interaction_response_frame,
    interrupt_frame,
    interrupt_response_outcome,
    message_text,
    optional_text,
    pending_interaction,
    prompt_frame,
    raw_message_text,
    result_detail,
    session_command,
    session_command_replay_text,
    terminal_metadata,
    terminal_outcome,
)
from agents_remember.serving.claude_stream_submission import (
    ClaudeSubmission,
    consume_future_exception,
)
from agents_remember.serving.claude_stream_transport import ClaudeStreamTransport
from agents_remember.serving.harness_control_models import (
    AdapterEvent,
    InteractionResponse,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
    TerminalResult,
    TranscriptEntry,
    TranscriptRole,
)

Clock = Callable[[], str]
CorrelationFactory = Callable[[], str]

_ACTIVE_TURN_ID_KEY = "activeTurnId"
"""Snapshot-raw key carrying the wire turn identity of the one active Claude turn.

Claude has no native turn identity, so the wire projects the accepted operation's id for the
turn's whole lifetime — the exact identity the exact-turn interrupt contract guards pre-write
(the control authority maps a caller's ``turnId`` onto ``expectedOperationId`` for claude).
The key is shared with the codex adapter so the canonical status authority reads one name.
It must ride the acceptance/result EVENT raw: the bridge accumulates ``snapshot.raw`` from
event raw keys only, so a snapshot-only stamp never crosses the IPC seam. The result event
carries a null tombstone so a settled turn's identity can never leak into a later frame's
``running`` activity as a stale id.
"""


@dataclass
class _PendingControl:
    """One in-flight adapter control request awaiting its correlated control_response."""

    future: asyncio.Future[tuple[bool, str | None, dict[str, object]]]
    target_request_id: str


@dataclass(frozen=True)
class ClaudeStreamSession:
    """WHICH Claude stream this state reduces: its identity, its snapshot, its wire, its verbs.

    The four are settled together at handshake and never independently: the supported command set
    is what THIS transport advertised for THIS identity, and the snapshot is the state that pairing
    starts from.
    """

    identity: ControlIdentity
    snapshot: AdapterSnapshot
    transport: ClaudeStreamTransport
    supported_commands: frozenset[str]


@dataclass(frozen=True)
class TranscriptCorrelation:
    """What ties one transcript entry back to the submission that produced it, and when.

    The AR request id and the vendor's own correlation id name the same submission from the two
    sides of the bridge; the timestamp is the moment they were observed together. An entry stamped
    with one submission's ids and another's time is unusable as evidence.
    """

    request_id: str | None
    vendor_correlation_id: str | None
    created_at: str


class ClaudeStreamState:
    """Own bounded submission evidence and reduce Claude frames into L1 adapter events."""

    def __init__(
        self,
        session: ClaudeStreamSession,
        *,
        clock: Clock,
        correlation_factory: CorrelationFactory,
        limits: ClaudeAdapterLimits,
        pending_interaction_frame: dict[str, object] | None = None,
    ) -> None:
        self._identity = session.identity
        self._snapshot = session.snapshot
        self._transport = session.transport
        self._supported_commands = session.supported_commands
        self._clock = clock
        self._correlation_factory = correlation_factory
        self._limits = limits
        self._events: asyncio.Queue[AdapterEvent | None] = asyncio.Queue(
            maxsize=limits.event_queue_limit
        )
        self._history: OrderedDict[str, ClaudeSubmission] = OrderedDict()
        self._pending_by_text: dict[str, str] = {}
        self._pending_by_correlation: dict[str, str] = {}
        self._accepted_order: deque[str] = deque()
        self._pending_interaction_frame = pending_interaction_frame
        self._reader_task: asyncio.Task[None] | None = None
        self._event_sequence = 0
        self._transcript_sequence = 0
        self._control_sequence = 0
        self._pending_control: dict[str, _PendingControl] = {}
        self._interrupt_accepted: set[str] = set()
        self._last_interrupt: tuple[tuple[str, str], InterruptResult] | None = None

    @property
    def retained_submission_count(self) -> int:
        return len(self._history)

    @property
    def snapshot(self) -> AdapterSnapshot:
        return self._snapshot

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        return self._event_stream()

    def start_reader(self) -> None:
        if self._reader_task is not None:
            raise HarnessControlError("Claude event reader is already started")
        self._reader_task = asyncio.create_task(self._run_reader())

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self._require_available()
        if request.operation is None:
            raise HarnessControlError("Claude submission requires an exact operation ref")
        if any(record.abandoned and not record.completed for record in self._history.values()):
            return SubmissionReceipt(
                request_id=request.request_id,
                acceptance="rejected",
                submitted_at=request.submitted_at,
                detail=(
                    "Claude has not terminated an earlier abandoned session command; "
                    "a later command was not sent"
                ),
            )
        command = session_command(request.text)
        if command is not None:
            unsupported = command_unsupported_detail(command, self._supported_commands)
            if unsupported is not None:
                return SubmissionReceipt(
                    request_id=request.request_id,
                    acceptance="unsupported",
                    submitted_at=request.submitted_at,
                    detail=unsupported,
                )
        correlation_id = self._correlation_factory()
        wire_text = (
            request.text
            if command is not None
            else correlation_envelope(request.request_id, correlation_id, request.text)
        )
        replay_text = (
            session_command_replay_text(request.text) if command is not None else wire_text
        )
        record = self._reserve_submission(
            request,
            correlation_id,
            wire_text,
            replay_text,
            request.operation,
        )
        if record is None:
            return SubmissionReceipt(
                request_id=request.request_id,
                acceptance="rejected",
                submitted_at=request.submitted_at,
                detail="Claude correlation history is full of unresolved or active submissions",
            )
        await self._write_submission(record)
        return await self._await_acceptance(record)

    def preflight_operation(self, operation: ControlOperationRef) -> None:
        self._require_available()
        if (
            self._accepted_order
            or self._pending_by_text
            or self._pending_interaction_frame is not None
            or self._snapshot.activity != "idle"
        ):
            raise HarnessAdapterBusyError(
                f"Claude is not idle for {operation.kind} operation preflight"
            )

    async def wait_terminal(self, request_id: str) -> TerminalResult | None:
        """Wait for the terminal result that follows one accepted structured command."""

        record = self._history.get(request_id)
        if record is None or record.acceptance not in {"immediate", "queued"}:
            raise HarnessControlError(
                "Claude terminal evidence requires a retained accepted submission"
            )
        try:
            return await asyncio.wait_for(
                asyncio.shield(record.terminal_future),
                timeout=self._limits.acceptance_timeout_seconds,
            )
        except TimeoutError:
            record.terminal_future.add_done_callback(consume_future_exception)
            self.abandon_submission(request_id)
            return None

    def abandon_submission(self, request_id: str) -> None:
        """Neutralize a cancelled/expired setter while retaining its late-frame tombstone."""

        record = self._history.get(request_id)
        if record is None or record.completed:
            return
        record.abandoned = True
        record.acceptance = "unknown"
        self._remove_pending(record)

    async def respond(self, response: InteractionResponse) -> None:
        self._require_available()
        pending = self._snapshot.pending_interaction
        vendor_frame = self._pending_interaction_frame
        if (
            pending is None
            or vendor_frame is None
            or pending.interaction_id != response.interaction_id
        ):
            raise HarnessControlError(
                "Claude interaction response does not match the pending request"
            )
        active = self.active_operation()
        if response.operation is None or response.operation != active:
            raise HarnessControlError("Claude interaction response changed operation identity")
        await self._transport.write_frame(
            interaction_response_frame(vendor_frame, response.response)
        )
        self._pending_interaction_frame = None
        self._snapshot = replace(
            self._snapshot,
            activity="settling",
            acceptance="queued",
            pending_interaction=None,
            raw={**self._snapshot.raw, "lastInteractionResponseAt": response.responded_at},
        )

    async def interrupt(
        self,
        *,
        turn_id: str | None,
        expected_operation_id: str | None,
    ) -> InterruptResult:
        """One native interrupt control request against the exact active turn, replaying once.

        Claude has no native turn identity: the wire projects the accepted operation's id as
        the turn identity (``activeTurnId``), and the control authority hands it back here as
        the caller's expected active-operation id. Like Pi, that id must match the current
        active operation before any native bytes are written, so a stale reconcile can never
        interrupt a successor. The native control_response answer is awaited on the reader's
        request-id correlation; an accepted interrupt is recorded so the turn's error-shaped
        result settles ``cancelled`` (interrupted), never ``failed``. A repeat naming the
        same (expected, active) pair replays the first acknowledgement with no second write.
        """

        self._require_available()
        if turn_id is not None:
            raise HarnessControlError(
                "Claude interrupt does not accept turn identity; use expectedOperationId"
            )
        if not self._accepted_order:
            raise HarnessControlError("no active Claude turn to interrupt")
        record = self._history[self._accepted_order[0]]
        active = record.operation
        if expected_operation_id is not None and expected_operation_id != active.operation_id:
            raise HarnessControlError(
                "interrupt operation id does not match the active Claude operation"
            )
        pair = (expected_operation_id or active.operation_id, active.operation_id)
        if self._last_interrupt is not None and self._last_interrupt[0] == pair:
            return self._last_interrupt[1]
        self._control_sequence += 1
        control_id = f"ar-claude-interrupt-{self._control_sequence}"
        future: asyncio.Future[tuple[bool, str | None, dict[str, object]]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending_control[control_id] = _PendingControl(
            future=future, target_request_id=record.request.request_id
        )
        try:
            await self._transport.write_frame(interrupt_frame(control_id))
            accepted, detail, response_frame = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=self._limits.acceptance_timeout_seconds,
            )
        except TimeoutError:
            # The request bytes were sent but the answer is genuinely lost. Keep the
            # correlation entry so a late success still records the accepted interrupt
            # before the turn settles; the acknowledgement stays honestly unknown.
            future.add_done_callback(consume_future_exception)
            result = InterruptResult(
                acknowledgement="unknown",
                bridge_epoch="",
                operation=active,
                vendor_correlation_id=control_id,
                detail=(
                    "Claude did not answer the interrupt control request before the "
                    "acknowledgement bound"
                ),
            )
        except BaseException:
            self._pending_control.pop(control_id, None)
            raise
        else:
            self._pending_control.pop(control_id, None)
            if accepted:
                result = InterruptResult(
                    acknowledgement="accepted",
                    bridge_epoch="",
                    operation=active,
                    vendor_correlation_id=control_id,
                    detail="native interrupt acknowledged for the exact active Claude turn",
                    raw={"claudeEvent": response_frame},
                )
            else:
                result = InterruptResult(
                    acknowledgement="rejected",
                    bridge_epoch="",
                    operation=active,
                    vendor_correlation_id=control_id,
                    detail=detail,
                    raw={"claudeEvent": response_frame},
                )
        self._last_interrupt = (pair, result)
        return result

    def reconcile(self, request_id: str) -> ReconciliationResult:
        record = self._history.get(request_id)
        if record is None:
            return ReconciliationResult(
                request_id=request_id,
                state="unresolved",
                reconciled_at=self._clock(),
                detail="no retained Claude correlation history exists for this request",
            )
        if record.acceptance in {"immediate", "queued"}:
            return self._accepted_reconciliation(record)
        if record.acceptance == "rejected":
            return ReconciliationResult(
                request_id=request_id,
                state="rejected",
                reconciled_at=self._clock(),
                vendor_correlation_id=record.correlation_id,
                detail="Claude rejected the submission before correlated acceptance",
            )
        return ReconciliationResult(
            request_id=request_id,
            state="unresolved",
            reconciled_at=self._clock(),
            vendor_correlation_id=record.correlation_id,
            detail=(
                "the established Claude session has no replay-user-message evidence for this "
                "submission; it remains unknown and was not resent"
            ),
            raw={"vendorSessionId": self._snapshot.vendor_session_id},
        )

    async def finish_reader(self, mode: ShutdownMode) -> None:
        if self._reader_task is not None:
            if not self._reader_task.done():
                self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        self._snapshot = replace(
            self._snapshot,
            control="disconnected",
            activity="idle" if mode == "graceful" else "unknown",
            acceptance="rejected",
        )

    async def _write_submission(self, record: ClaudeSubmission) -> None:
        try:
            await self._transport.write_frame(
                prompt_frame(
                    session_id=self._snapshot.vendor_session_id or "",
                    correlation_id=record.correlation_id,
                    text=record.wire_text,
                ),
                before_write=lambda: self._guard_submission_write(record),
            )
        except HarnessAdapterDisconnectedError:
            self._remove_pending(record)
            record.acceptance_future.cancel()
            raise
        except HarnessControlError:
            record.acceptance = "rejected"
            self._remove_pending(record)
            record.acceptance_future.cancel()
            self._history.pop(record.request.request_id, None)
            raise

    async def _await_acceptance(self, record: ClaudeSubmission) -> SubmissionReceipt:
        try:
            return await asyncio.wait_for(
                asyncio.shield(record.acceptance_future),
                timeout=self._limits.acceptance_timeout_seconds,
            )
        except TimeoutError:
            record.acceptance = "unknown"
            record.acceptance_future.add_done_callback(consume_future_exception)
            self._snapshot = replace(self._snapshot, acceptance="unknown")
            return SubmissionReceipt(
                request_id=record.request.request_id,
                acceptance="unknown",
                submitted_at=record.request.submitted_at,
                vendor_correlation_id=record.correlation_id,
                detail="Claude did not replay the correlated user message before the acknowledgement bound",
            )

    def _accepted_reconciliation(self, record: ClaudeSubmission) -> ReconciliationResult:
        return ReconciliationResult(
            request_id=record.request.request_id,
            state="accepted",
            reconciled_at=self._clock(),
            vendor_correlation_id=record.correlation_id,
            detail="correlated replay-user-message acceptance is retained for this Claude session",
            raw={"vendorSessionId": self._snapshot.vendor_session_id},
        )

    def _reserve_submission(
        self,
        request: PromptRequest,
        correlation_id: str,
        wire_text: str,
        replay_text: str,
        operation: ControlOperationRef,
    ) -> ClaudeSubmission | None:
        if request.request_id in self._history:
            raise HarnessControlError(f"duplicate Claude request id: {request.request_id}")
        if any(record.correlation_id == correlation_id for record in self._history.values()):
            raise HarnessControlError(f"duplicate retained Claude correlation id: {correlation_id}")
        if wire_text in self._pending_by_text:
            raise HarnessControlError("an identical Claude message is still awaiting replay")
        if len(self._history) >= self._limits.history_limit and not self._evict_submission():
            return None
        record = ClaudeSubmission(
            request=request,
            correlation_id=correlation_id,
            wire_text=wire_text,
            replay_text=replay_text,
            operation=operation,
            acceptance_future=asyncio.get_running_loop().create_future(),
            terminal_future=asyncio.get_running_loop().create_future(),
        )
        record.terminal_future.add_done_callback(consume_future_exception)
        self._history[request.request_id] = record
        self._pending_by_text[wire_text] = request.request_id
        self._pending_by_correlation[correlation_id] = request.request_id
        return record

    def _evict_submission(self) -> bool:
        active = set(self._accepted_order) | set(self._pending_by_text.values())
        evictable = next(
            (
                request_id
                for request_id, record in self._history.items()
                if request_id not in active
                and (record.acceptance != "unknown" or (record.abandoned and record.completed))
            ),
            None,
        )
        if evictable is None:
            return False
        self._history.pop(evictable)
        return True

    async def _event_stream(self) -> AsyncIterator[AdapterEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def _run_reader(self) -> None:
        try:
            await self._read_frames()
        except asyncio.CancelledError:
            raise
        except HarnessControlError as exc:
            await self._fail_reader(str(exc))
        except Exception as exc:
            # This task is the sole stdout reader. Publishing a type-only terminal failure is
            # necessary so an unexpected parser defect cannot strand bridge callers or leak data.
            await self._fail_reader(
                f"unexpected Claude adapter reader failure: {type(exc).__name__}"
            )
        finally:
            self._finish_events()

    async def _read_frames(self) -> None:
        while True:
            frame = await self._transport.read_frame()
            if frame is None:
                await self._handle_eof()
                return
            await self._handle_frame(frame)

    async def _handle_frame(self, frame: Mapping[str, object]) -> None:
        frame_type, subtype = frame.get("type"), frame.get("subtype")
        handler = self._frame_handler(frame_type, subtype, frame)
        if handler is not None:
            await handler(frame)
            return
        if frame_type == "control_request":
            await self._handle_interaction(frame)
            return
        await self._emit(
            "state",
            raw={
                "claudeEventType": str(frame_type),
                "claudeEventSubtype": str(subtype),
                AR_EVIDENCE_KEY: dict(frame),
            },
        )

    def _frame_handler(self, frame_type: object, subtype: object, frame: Mapping[str, object]):
        if frame_type == "user" and frame.get("isReplay") is True:
            return self._handle_replayed_user
        handlers = {
            ("assistant", None): self._handle_assistant,
            ("result", None): self._handle_result,
            ("system", "api_retry"): self._handle_retry,
            ("system", "status"): self._handle_status,
            ("control_response", None): self._handle_control_response,
            ("control_cancel_request", None): self._handle_interaction_cancel,
        }
        direct = handlers.get((frame_type, subtype))
        if direct is not None:
            return direct
        return handlers.get((frame_type, None))

    async def _handle_interaction(self, frame: Mapping[str, object]) -> None:
        interaction = pending_interaction(frame, created_at=self._created_at(frame))
        if interaction is None:
            request = frame.get("request")
            subtype = request.get("subtype") if isinstance(request, Mapping) else None
            raise HarnessControlError(f"unsupported Claude control request subtype: {subtype}")
        if self._pending_interaction_frame is not None:
            raise HarnessControlError("Claude emitted concurrent blocking interactions")
        self._pending_interaction_frame = dict(frame)
        self._snapshot = replace(
            self._snapshot,
            activity="blocked",
            pending_interaction=interaction,
        )
        await self._emit("state", raw={"claudeEventType": "control_request"})

    async def _handle_control_response(self, frame: Mapping[str, object]) -> None:
        """Resolve one adapter control request (the interrupt) by its correlated request id.

        A control_response naming no pending adapter request keeps the pre-interrupt behavior:
        preserved as generic evidence, never consumed. A matched success for the interrupt
        records the accepted-interrupt correlation while its target turn is still active, so
        the turn's error-shaped result settles interrupted; a late answer after the turn
        settled records nothing (the result already kept its native meaning).
        """

        response = frame.get("response")
        request_id = response.get("request_id") if isinstance(response, Mapping) else None
        pending = self._pending_control.get(request_id) if isinstance(request_id, str) else None
        if not isinstance(request_id, str) or pending is None:
            await self._emit(
                "state",
                raw={
                    "claudeEventType": "control_response",
                    AR_EVIDENCE_KEY: dict(frame),
                },
            )
            return
        outcome = interrupt_response_outcome(frame, request_id)
        if outcome is None:
            return
        accepted, detail = outcome
        if (
            accepted
            and self._accepted_order
            and self._accepted_order[0] == pending.target_request_id
        ):
            self._interrupt_accepted.add(pending.target_request_id)
        if not pending.future.done():
            pending.future.set_result((accepted, detail, dict(frame)))

    async def _handle_replayed_user(self, frame: Mapping[str, object]) -> None:
        wire_text = raw_message_text(frame)
        correlation = frame.get("uuid")
        request_id = (
            self._pending_by_correlation.get(correlation) if isinstance(correlation, str) else None
        )
        if request_id is None:
            await self._handle_abandoned_replay(frame, correlation=correlation, wire_text=wire_text)
            return
        record = self._history[request_id]
        self._require_faithful_replay(frame, retained_text=record.replay_text, wire_text=wire_text)
        accepted_at = self._created_at(frame)
        if self._accepted_order:
            raise HarnessControlError("Claude accepted a second ordinary prompt")
        acceptance: AcceptanceState = "immediate"
        record.acceptance = acceptance
        record.accepted_at = accepted_at
        self._accepted_order.append(request_id)
        self._remove_pending(record)
        receipt = SubmissionReceipt(
            request_id=request_id,
            acceptance=acceptance,
            submitted_at=record.request.submitted_at,
            vendor_correlation_id=record.correlation_id,
            accepted_at=accepted_at,
            detail="accepted by correlated replay-user-message",
        )
        if not record.acceptance_future.done():
            record.acceptance_future.set_result(receipt)
        self._snapshot = replace(self._snapshot, activity="running", acceptance=acceptance)
        transcript = self._transcript_entry(
            TranscriptCorrelation(
                request_id=request_id,
                vendor_correlation_id=record.correlation_id,
                created_at=accepted_at,
            ),
            role="user",
            text=record.request.text,
        )
        await self._emit(
            "state",
            transcript=(transcript,),
            raw={
                "acceptanceEvidence": "replay",
                _ACTIVE_TURN_ID_KEY: record.operation.operation_id,
            },
        )

    async def _handle_abandoned_replay(
        self,
        frame: Mapping[str, object],
        *,
        correlation: object,
        wire_text: str,
    ) -> None:
        """Absorb a replay whose request is no longer pending, using the retained record.

        A correlation with no retained record is unattributable and fails the stream. A retained
        one is still verified verbatim, then ignored for acceptance -- the turn it named already
        completed, or it landed late -- while the late case still restores the accepted order and
        the active turn id so the seat does not read as idle.
        """

        abandoned = next(
            (
                record
                for record in self._history.values()
                if isinstance(correlation, str)
                and record.correlation_id == correlation
                and record.abandoned
            ),
            None,
        )
        if abandoned is None:
            raise HarnessControlError(
                "Claude replayed a user message without a retained correlation"
            )
        self._require_faithful_replay(
            frame, retained_text=abandoned.replay_text, wire_text=wire_text
        )
        if abandoned.completed:
            await self._emit(
                "state",
                raw={"completedClaudeReplayIgnored": abandoned.request.request_id},
            )
            return
        if abandoned.request.request_id not in self._accepted_order:
            self._accepted_order.append(abandoned.request.request_id)
        await self._emit(
            "state",
            raw={
                "lateClaudeReplayIgnored": abandoned.request.request_id,
                _ACTIVE_TURN_ID_KEY: abandoned.operation.operation_id,
            },
        )

    def _require_faithful_replay(
        self,
        frame: Mapping[str, object],
        *,
        retained_text: str,
        wire_text: str,
    ) -> None:
        """Refuse a replay that is not the retained submission, verbatim, on the same session.

        A changed session identity or a changed body means the echo is not evidence about the
        request it correlates to, so nothing may be concluded from it in either direction.
        """

        if frame.get("session_id") != self._snapshot.vendor_session_id:
            raise HarnessControlError("Claude replay-user-message changed session identity")
        if retained_text != wire_text:
            raise HarnessControlError(
                "Claude replay-user-message body changed for its retained correlation"
            )

    async def _handle_assistant(self, frame: Mapping[str, object]) -> None:
        self._snapshot = replace(self._snapshot, activity="running")
        text = message_text(frame)
        transcript: tuple[TranscriptEntry, ...] = ()
        if text:
            transcript = (
                self._transcript_entry(
                    TranscriptCorrelation(
                        request_id=self._current_request_id(),
                        vendor_correlation_id=optional_text(frame.get("uuid")),
                        created_at=self._created_at(frame),
                    ),
                    role="assistant",
                    text=text,
                ),
            )
        await self._emit(
            "state",
            transcript=transcript,
            raw={"claudeEventType": "assistant", AR_EVIDENCE_KEY: dict(frame)},
        )

    async def _handle_retry(self, frame: Mapping[str, object]) -> None:
        self._snapshot = replace(self._snapshot, activity="settling")
        await self._emit(
            "state",
            raw={
                "claudeEventType": "api_retry",
                "retryAttempt": frame.get("attempt"),
                "retryMax": frame.get("max_retries"),
                "retryDelayMs": frame.get("retry_delay_ms"),
                "retryStatus": frame.get("error_status"),
            },
        )

    async def _handle_status(self, frame: Mapping[str, object]) -> None:
        status = frame.get("status")
        activity = self._status_activity(status)
        self._snapshot = replace(self._snapshot, activity=activity)
        await self._emit("state", raw={"claudeStatus": status})

    def _status_activity(self, status: object) -> str:
        if status == "compacting":
            return "settling"
        if status == "requesting":
            return "running"
        if self._snapshot.pending_interaction is not None:
            return "blocked"
        return "running" if self._accepted_order else "idle"

    async def _handle_result(self, frame: Mapping[str, object]) -> None:
        if frame.get("session_id") != self._snapshot.vendor_session_id:
            raise HarnessControlError("Claude result changed session identity")
        request_id = self._accepted_order.popleft() if self._accepted_order else None
        record: ClaudeSubmission | None = None
        # The accepted-interrupt correlation is the ONLY distinction between an interrupted
        # turn and a real failure: natively both arrive as error_during_execution/is_error.
        interrupt_accepted = request_id is not None and request_id in self._interrupt_accepted
        if request_id is not None:
            self._interrupt_accepted.discard(request_id)
            for control_id, pending in tuple(self._pending_control.items()):
                if pending.target_request_id == request_id:
                    self._pending_control.pop(control_id, None)
        outcome = terminal_outcome(frame, interrupt_accepted=interrupt_accepted)
        completed_at = self._created_at(frame)
        detail = result_detail(frame)
        terminal = TerminalResult(
            outcome=outcome,
            completed_at=completed_at,
            detail=detail or None,
            raw=terminal_metadata(frame),
        )
        if request_id is not None:
            record = self._history[request_id]
            record.completed = True
            self._history.move_to_end(request_id)
        transcript = self._transcript_entry(
            TranscriptCorrelation(
                request_id=request_id,
                vendor_correlation_id=optional_text(frame.get("uuid")),
                created_at=completed_at,
            ),
            role="result",
            text=detail or outcome,
            terminal_result=terminal,
        )
        activity = (
            "blocked"
            if self._pending_interaction_frame is not None
            else ("running" if self._accepted_order else "idle")
        )
        self._snapshot = replace(self._snapshot, activity=activity)
        if record is None:
            raise HarnessControlError("Claude result has no exact active operation")
        # The correlated classification rides the diverted payload copy as adapter-attributed
        # evidence so the projector and the interrupt settlement ledger read the same truth;
        # the native frame keys stay byte-intact beside the reserved ar* stamp.
        payload = {**frame, AR_TERMINAL_OUTCOME_KEY: outcome}
        await self._emit(
            "completed",
            transcript=(transcript,),
            raw={
                "terminalOutcome": outcome,
                _ACTIVE_TURN_ID_KEY: None,
                AR_EVIDENCE_KEY: payload,
            },
            operation=record.operation,
        )
        if record is not None and not record.terminal_future.done():
            record.terminal_future.set_result(terminal)

    async def _handle_interaction_cancel(self, frame: Mapping[str, object]) -> None:
        pending = self._snapshot.pending_interaction
        if pending is None or frame.get("request_id") != pending.interaction_id:
            return
        self._pending_interaction_frame = None
        self._snapshot = replace(
            self._snapshot,
            pending_interaction=None,
            activity="running" if self._accepted_order else "idle",
        )
        await self._emit("state", raw={"interactionCancelled": pending.interaction_id})

    async def _handle_eof(self) -> None:
        failed = self._transport.returncode not in {None, 0}
        detail = (
            f"Claude Code exited with status {self._transport.returncode}"
            if failed
            else "Claude Code stream disconnected"
        )
        had_pending = bool(self._pending_by_text)
        prior_acceptance = self._snapshot.acceptance
        self._complete_pending_on_disconnect(detail)
        self._snapshot = replace(
            self._snapshot,
            control="failed" if failed else "disconnected",
            activity="unknown",
            acceptance="unknown" if had_pending else prior_acceptance,
            raw={**self._snapshot.raw, "disconnect": detail},
        )
        await self._emit("failed" if failed else "disconnected", raw={"disconnect": detail})

    async def _fail_reader(self, detail: str) -> None:
        self._complete_pending_on_disconnect(detail, control_error=True)
        self._snapshot = replace(
            self._snapshot,
            control="failed",
            activity="unknown",
            acceptance="rejected",
            raw={**self._snapshot.raw, "streamFailure": detail},
        )
        await self._emit("failed", raw={"streamFailure": detail})

    async def _emit(
        self,
        kind: str,
        *,
        transcript: tuple[TranscriptEntry, ...] = (),
        raw: Mapping[str, object] | None = None,
        operation: ControlOperationRef | None = None,
    ) -> None:
        self._event_sequence += 1
        event_raw = dict(raw or {})
        # The reserved evidence key rides the event only; the adapter's own snapshot merge must
        # stay byte-identical for every pre-existing key so bridge-side redaction has the final
        # say over what any projection can see.
        snapshot_raw = {key: value for key, value in event_raw.items() if key != AR_EVIDENCE_KEY}
        self._snapshot = replace(
            self._snapshot,
            last_event_sequence=self._event_sequence,
            raw={**self._snapshot.raw, **snapshot_raw},
        )
        await self._events.put(
            AdapterEvent(
                sequence=self._event_sequence,
                kind=kind,
                identity=self._identity,
                created_at=self._clock(),
                snapshot=self._snapshot,
                transcript=transcript,
                raw=event_raw,
                operation=operation,
            )
        )

    def _transcript_entry(
        self,
        correlation: TranscriptCorrelation,
        *,
        role: TranscriptRole,
        text: str,
        terminal_result: TerminalResult | None = None,
    ) -> TranscriptEntry:
        request_id = correlation.request_id
        vendor_correlation_id = correlation.vendor_correlation_id
        created_at = correlation.created_at
        self._transcript_sequence += 1
        return TranscriptEntry(
            sequence=self._transcript_sequence,
            role=role,
            text=clip_transcript_text(text),
            created_at=created_at,
            request_id=request_id,
            vendor_correlation_id=vendor_correlation_id,
            terminal_result=terminal_result,
        )

    def _remove_pending(self, record: ClaudeSubmission) -> None:
        self._pending_by_text.pop(record.wire_text, None)
        self._pending_by_correlation.pop(record.correlation_id, None)

    def _complete_pending_on_disconnect(self, detail: str, *, control_error: bool = False) -> None:
        for control_id, pending in tuple(self._pending_control.items()):
            if not pending.future.done():
                control_response_error: HarnessControlError
                if control_error:
                    control_response_error = HarnessControlError(detail)
                else:
                    control_response_error = HarnessAdapterDisconnectedError(
                        detail,
                        may_have_sent=True,
                        vendor_correlation_id=control_id,
                    )
                pending.future.set_exception(control_response_error)
        self._pending_control.clear()
        for request_id in tuple(self._pending_by_text.values()):
            record = self._history[request_id]
            record.acceptance = "unknown"
            if not record.acceptance_future.done():
                error: HarnessControlError
                if control_error:
                    error = HarnessControlError(detail)
                else:
                    error = HarnessAdapterDisconnectedError(
                        detail,
                        may_have_sent=True,
                        vendor_correlation_id=record.correlation_id,
                    )
                record.acceptance_future.set_exception(error)
            self._remove_pending(record)
        for request_id in tuple(self._accepted_order):
            record = self._history[request_id]
            if not record.terminal_future.done():
                error = (
                    HarnessControlError(detail)
                    if control_error
                    else HarnessAdapterDisconnectedError(
                        detail,
                        may_have_sent=True,
                        vendor_correlation_id=record.correlation_id,
                    )
                )
                record.terminal_future.set_exception(error)
        self._accepted_order.clear()

    def _current_request_id(self) -> str | None:
        return self._accepted_order[0] if self._accepted_order else None

    def active_operation(self) -> ControlOperationRef | None:
        if self._accepted_order:
            return self._history[self._accepted_order[0]].operation
        if len(self._pending_by_text) == 1:
            request_id = next(iter(self._pending_by_text.values()))
            return self._history[request_id].operation
        return None

    def _guard_submission_write(self, record: ClaudeSubmission) -> None:
        """Final no-await guard under the shared Claude transport write lock."""

        if (
            self._accepted_order
            or self._pending_interaction_frame is not None
            or self._pending_by_text.get(record.wire_text) != record.request.request_id
            or self.active_operation() != record.operation
        ):
            raise HarnessAdapterBusyError(
                "Claude became busy before the guarded prompt/setter frame write"
            )

    def _created_at(self, frame: Mapping[str, object]) -> str:
        timestamp = frame.get("timestamp")
        return timestamp if isinstance(timestamp, str) and timestamp else self._clock()

    def _require_available(self) -> None:
        if self._snapshot.control != "ready":
            raise HarnessControlError(f"Claude adapter is not available: {self._snapshot.control}")

    def _finish_events(self) -> None:
        if self._events.full():
            self._events.get_nowait()
        self._events.put_nowait(None)
