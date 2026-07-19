"""One ordered, protocol-backed control bridge for one exact hosted harness session."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from agents_remember.errors import HarnessAdapterDisconnectedError, HarnessControlError
from agents_remember.serving.harness_capabilities import CapabilitySnapshot, SetResult
from agents_remember.serving.harness_control_adapter import (
    HarnessProtocolAdapter,
    reduce_adapter_event,
)
from agents_remember.serving.harness_control_models import (
    AR_EVIDENCE_KEY,
    CONTROL_PROTOCOL_VERSION,
    EVIDENCE_PAGE_BYTE_BUDGET,
    MAX_NATIVE_EVIDENCE_PAGE,
    REQUIRED_ADAPTER_CAPABILITIES,
    AdapterEvent,
    AdapterHandshake,
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationKind,
    EvidenceFrame,
    EvidencePage,
    InteractionResponse,
    LaunchSpec,
    NativeEvidencePage,
    NativePageReader,
    PromptRequest,
    ReconciliationResult,
    ReconciliationState,
    ShutdownMode,
    SubmissionAuthorityDescriptor,
    SubmissionProvenanceBatch,
    SubmissionReceipt,
    SubmissionSource,
    SubmissionStatusBatch,
    TranscriptEntry,
    WithdrawalResult,
    clip_evidence_payload,
    evidence_frame_wire_bytes,
)
from agents_remember.serving.harness_control_queue import HarnessControlQueue
from agents_remember.serving.harness_submission_authority import OperationResolution

Clock = Callable[[], str]


class HarnessControlBridge:
    """Own one adapter and serialize terminal and durable input through one bounded queue."""

    def __init__(
        self,
        identity: ControlIdentity,
        adapter: HarnessProtocolAdapter,
        *,
        queue_limit: int = 64,
        transcript_limit: int = 1000,
        submission_limit: int = 256,
        subscriber_queue_limit: int = 16,
        evidence_limit: int = 2000,
        evidence_frame_bytes: int = 32 * 1024,
        clock: Clock = lambda: datetime.now(UTC).isoformat(),
    ) -> None:
        for name, value in (
            ("queue_limit", queue_limit),
            ("transcript_limit", transcript_limit),
            ("submission_limit", submission_limit),
            ("subscriber_queue_limit", subscriber_queue_limit),
            ("evidence_limit", evidence_limit),
            ("evidence_frame_bytes", evidence_frame_bytes),
        ):
            if value < 1:
                raise HarnessControlError(f"{name} must be positive")
        self.identity = identity
        self._adapter = adapter
        self._clock = clock
        self._transcript: deque[TranscriptEntry] = deque(maxlen=transcript_limit)
        self._evidence: deque[EvidenceFrame] = deque(maxlen=evidence_limit)
        self._evidence_limit = evidence_limit
        self._evidence_frame_bytes = evidence_frame_bytes
        self._evicted_before_sequence = 0
        self._subscriber_queue_limit = subscriber_queue_limit
        self._subscribers: set[asyncio.Queue[AdapterSnapshot]] = set()
        self._snapshot = AdapterSnapshot(
            identity=identity,
            control="starting",
            activity="unknown",
            acceptance="unknown",
        )
        self._started = False
        self._stopped = False
        self._event_task: asyncio.Task[None] | None = None
        self._command_queue = HarnessControlQueue(
            adapter,
            queue_limit=queue_limit,
            submission_limit=submission_limit,
            clock=clock,
            snapshot=self.snapshot,
            set_snapshot=self._set_snapshot,
            publish=self._publish,
        )

    async def start(self, launch: LaunchSpec) -> AdapterSnapshot:
        if self._started:
            raise HarnessControlError("control bridge is already started")
        if launch.identity != self.identity:
            raise HarnessControlError("launch identity does not match the control bridge")
        try:
            handshake = await self._adapter.start(launch)
            self._validate_handshake(handshake)
        except Exception:
            # Adapter startup can fail after it has acquired a subprocess, before it returns a
            # handshake. Forced cleanup therefore covers both startup and handshake refusal.
            await self._adapter.stop("forced")
            raise
        self._snapshot = handshake.snapshot
        self._started = True
        self._command_queue.start()
        if self._snapshot.control != "unsupported":
            self._event_task = asyncio.create_task(self._run_events())
        self._publish()
        return self._snapshot

    def snapshot(self) -> AdapterSnapshot:
        return self._snapshot

    def mark_failed(self, detail: str) -> AdapterSnapshot:
        """Expose a startup failure through IPC after the adapter cleaned up its partial launch."""

        if self._started:
            raise HarnessControlError("cannot replace a started control bridge with a failure")
        self._started = True
        self._snapshot = replace(
            self._snapshot,
            control="failed",
            activity="unknown",
            acceptance="rejected",
            raw={**self._snapshot.raw, "bridgeError": detail},
        )
        self._publish()
        return self._snapshot

    async def subscribe(self) -> AsyncGenerator[AdapterSnapshot]:
        queue: asyncio.Queue[AdapterSnapshot] = asyncio.Queue(maxsize=self._subscriber_queue_limit)
        self._subscribers.add(queue)
        queue.put_nowait(self._snapshot)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    def transcript(
        self, *, after_sequence: int = 0, limit: int = 500
    ) -> tuple[TranscriptEntry, ...]:
        bounded = min(500, max(1, limit))
        return tuple(entry for entry in self._transcript if entry.sequence > after_sequence)[
            :bounded
        ]

    def evidence(
        self,
        *,
        after_sequence: int = 0,
        limit: int = 500,
        byte_budget: int = EVIDENCE_PAGE_BYTE_BUDGET,
    ) -> EvidencePage:
        """Page the bounded evidence buffer (a hot window, never history authority)."""

        if byte_budget < 1:
            raise HarnessControlError("evidence page byte budget must be positive")
        bounded = min(500, max(1, limit))
        frames: list[EvidenceFrame] = []
        used = 0
        truncated = False
        for frame in self._evidence:
            if frame.sequence <= after_sequence:
                continue
            size = evidence_frame_wire_bytes(frame)
            if len(frames) >= bounded or (frames and used + size > byte_budget):
                truncated = True
                break
            frames.append(frame)
            used += size
        return EvidencePage(
            frames=tuple(frames),
            latest_sequence=self._evidence[-1].sequence if self._evidence else 0,
            evicted_before_sequence=self._evicted_before_sequence,
            truncated=truncated,
            bridge_epoch=self._command_queue.bridge_epoch,
        )

    async def native_page(
        self,
        *,
        cursor: str | None,
        limit: int,
        byte_budget: int = EVIDENCE_PAGE_BYTE_BUDGET,
    ) -> NativeEvidencePage:
        """Page harness-native history through the adapter's own read seam; bridge-stamped epoch."""

        self._require_running()
        adapter = self._adapter
        if not isinstance(adapter, NativePageReader):
            raise HarnessControlError(
                f"{type(adapter).__name__} does not support native evidence pages; "
                "only the live stream/replay evidence buffer is available for this session"
            )
        page = await adapter.read_native_page(
            cursor=cursor,
            limit=max(1, min(MAX_NATIVE_EVIDENCE_PAGE, limit)),
            byte_budget=byte_budget,
        )
        if page.bridge_epoch:
            raise HarnessControlError("native evidence adapter must not mint the bridge epoch")
        return replace(page, bridge_epoch=self._command_queue.bridge_epoch)

    async def submission_provenance(
        self,
        expected_bridge_epoch: str,
        request_ids: tuple[str, ...],
    ) -> SubmissionProvenanceBatch:
        self._require_running()
        return await self._command_queue.provenance(expected_bridge_epoch, request_ids)

    def prompt(
        self,
        text: str,
        *,
        source: SubmissionSource,
        request_id: str | None = None,
        expected_bridge_epoch: str | None = None,
    ) -> PromptRequest:
        return PromptRequest(
            request_id=request_id or uuid4().hex,
            source=source,
            text=text,
            submitted_at=self._clock(),
            expected_bridge_epoch=expected_bridge_epoch,
        )

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self._require_running()
        return await self._command_queue.submit(request)

    def submission_authority(self) -> SubmissionAuthorityDescriptor:
        self._require_running()
        return self._command_queue.descriptor()

    async def submission_status(
        self,
        expected_bridge_epoch: str,
        request_ids: tuple[str, ...],
        *,
        cockpit_only: bool = False,
    ) -> SubmissionStatusBatch:
        self._require_running()
        return await self._command_queue.status(
            expected_bridge_epoch,
            request_ids,
            cockpit_only=cockpit_only,
        )

    async def withdraw_submission(
        self,
        expected_bridge_epoch: str,
        request_id: str,
        *,
        cockpit_only: bool = False,
    ) -> WithdrawalResult:
        self._require_running()
        return await self._command_queue.withdraw(
            expected_bridge_epoch,
            request_id,
            cockpit_only=cockpit_only,
        )

    @property
    def retained_submission_count(self) -> int:
        """Current bounded receipt/reconciliation ledger size for diagnostics and scaling proof."""

        return self._command_queue.retained_submission_count

    async def respond(self, response: InteractionResponse) -> AdapterSnapshot:
        self._require_running()
        return await self._command_queue.respond(response)

    async def reconcile(
        self, request_id: str, *, expected_bridge_epoch: str | None = None
    ) -> ReconciliationResult:
        self._require_running()
        return await self._command_queue.reconcile(
            request_id,
            expected_bridge_epoch=expected_bridge_epoch,
        )

    async def resolve_unknown(
        self,
        request_id: str,
        *,
        state: ReconciliationState,
        detail: str,
    ) -> ReconciliationResult:
        self._require_running()
        return await self._command_queue.resolve_unknown(request_id, state=state, detail=detail)

    async def resolve_operation(
        self,
        operation_id: str,
        operation_kind: ControlOperationKind,
        *,
        resolution: OperationResolution,
        detail: str,
    ) -> None:
        self._require_running()
        if resolution not in {"applied", "not-applied"}:
            raise HarnessControlError("operation resolution must be applied or not-applied")
        await self._command_queue.resolve_operation(
            operation_id,
            operation_kind,
            resolution=resolution,
            detail=detail,
        )

    def advertise(self) -> CapabilitySnapshot:
        self._require_running()
        return self._adapter.advertise()

    async def set_model(self, model_key: str) -> SetResult:
        self._require_running()
        return await self._command_queue.set_model(model_key)

    async def set_effort(self, effort: str) -> SetResult:
        self._require_running()
        return await self._command_queue.set_effort(effort)

    async def stop(self, mode: ShutdownMode = "graceful") -> None:
        if self._stopped:
            return
        if mode == "forced":
            self._stopped = True
            await self._cancel_event_task()
            await self._command_queue.force_stop()
            return
        self._require_started()
        self._stopped = True
        try:
            await self._command_queue.graceful_stop()
        finally:
            await self._cancel_event_task()

    def _validate_handshake(self, handshake: AdapterHandshake) -> None:
        if handshake.protocol_version != CONTROL_PROTOCOL_VERSION:
            raise HarnessControlError("adapter handshake protocol version mismatch")
        if handshake.identity != self.identity or handshake.snapshot.identity != self.identity:
            raise HarnessControlError("adapter handshake identity does not match the bridge")
        if handshake.snapshot.control not in {"ready", "unsupported"}:
            raise HarnessControlError("adapter handshake did not establish explicit readiness")
        if handshake.snapshot.control == "unsupported":
            return
        missing = REQUIRED_ADAPTER_CAPABILITIES - handshake.capabilities
        if missing:
            raise HarnessControlError(
                "adapter capability mismatch: missing " + ", ".join(sorted(missing))
            )

    def _require_started(self) -> None:
        if not self._started:
            raise HarnessControlError("control bridge is not started")

    def _require_running(self) -> None:
        self._require_started()
        if self._stopped:
            raise HarnessControlError("control bridge is stopped")
        if self._snapshot.control == "failed":
            raise HarnessControlError(
                f"control bridge failed: {self._snapshot.raw.get('bridgeError', 'unknown error')}"
            )

    async def _run_events(self) -> None:
        try:
            async for event in self._adapter.subscribe():
                try:
                    payload = event.raw.get(AR_EVIDENCE_KEY)
                    reduced_event = (
                        self._divert_evidence(event, payload) if payload is not None else event
                    )
                    updated = reduce_adapter_event(self._snapshot, reduced_event)
                    # The authority consumes the direct event before public subscribers see the
                    # coalesced snapshot. Draining from subscribe() would lose intermediate
                    # completion identity under subscriber backpressure.
                    await self._command_queue.observe_event(reduced_event)
                    self._append_transcript(reduced_event.transcript)
                except HarnessControlError as exc:
                    self._snapshot = replace(
                        self._snapshot,
                        control="failed",
                        activity="unknown",
                        acceptance="rejected",
                        raw={**self._snapshot.raw, "bridgeError": str(exc)},
                    )
                    self._publish()
                    return
                self._snapshot = updated
                self._command_queue.notify_snapshot_updated()
                self._publish()
            if not self._stopped and self._snapshot.control == "ready":
                self._snapshot = replace(
                    self._snapshot,
                    control="disconnected",
                    activity="unknown",
                    acceptance="unknown",
                )
                self._publish()
        except HarnessAdapterDisconnectedError as exc:
            self._snapshot = replace(
                self._snapshot,
                control="disconnected",
                activity="unknown",
                acceptance="unknown" if exc.may_have_sent else "rejected",
                raw={**self._snapshot.raw, "disconnect": str(exc)},
            )
            self._publish()

    def _append_transcript(self, entries: tuple[TranscriptEntry, ...]) -> None:
        previous = self._transcript[-1].sequence if self._transcript else 0
        for entry in entries:
            if entry.sequence <= previous:
                raise HarnessControlError("transcript sequence must increase monotonically")
            self._transcript.append(entry)
            previous = entry.sequence

    def _divert_evidence(self, event: AdapterEvent, payload: object) -> AdapterEvent:
        """Append one reserved-key payload to the bounded buffer; return the redacted event.

        The redacted event is what reduction, the command authority, the transcript, and every
        subscriber see, so ``snapshot.raw`` and all of its projections stay byte-identical.
        """

        if not isinstance(payload, Mapping):
            raise HarnessControlError("adapter evidence payload must be an object")
        self._append_evidence(event.sequence, event.kind, event.created_at, payload)
        return replace(
            event,
            raw={key: value for key, value in event.raw.items() if key != AR_EVIDENCE_KEY},
        )

    def _append_evidence(
        self, sequence: int, kind: str, created_at: str, payload: Mapping[str, object]
    ) -> None:
        previous = self._evidence[-1].sequence if self._evidence else 0
        if sequence <= previous:
            raise HarnessControlError("evidence sequence must increase monotonically")
        if len(self._evidence) == self._evidence_limit:
            self._evicted_before_sequence = self._evidence[0].sequence
        self._evidence.append(
            EvidenceFrame(
                sequence=sequence,
                kind=kind,
                created_at=created_at,
                raw=clip_evidence_payload(payload, max_bytes=self._evidence_frame_bytes),
            )
        )

    def _publish(self) -> None:
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(self._snapshot)

    def _set_snapshot(self, snapshot: AdapterSnapshot) -> None:
        self._snapshot = snapshot

    async def _cancel_event_task(self) -> None:
        if self._event_task is None:
            return
        self._event_task.cancel()
        await asyncio.gather(self._event_task, return_exceptions=True)
