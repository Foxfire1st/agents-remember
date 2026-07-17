"""One ordered, protocol-backed control bridge for one exact hosted harness session."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator, Callable
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
    CONTROL_PROTOCOL_VERSION,
    REQUIRED_ADAPTER_CAPABILITIES,
    AdapterHandshake,
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationKind,
    InteractionResponse,
    LaunchSpec,
    PromptRequest,
    ReconciliationResult,
    ReconciliationState,
    ShutdownMode,
    SubmissionAuthorityDescriptor,
    SubmissionReceipt,
    SubmissionSource,
    SubmissionStatusBatch,
    TranscriptEntry,
    WithdrawalResult,
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
        clock: Clock = lambda: datetime.now(UTC).isoformat(),
    ) -> None:
        for name, value in (
            ("queue_limit", queue_limit),
            ("transcript_limit", transcript_limit),
            ("submission_limit", submission_limit),
            ("subscriber_queue_limit", subscriber_queue_limit),
        ):
            if value < 1:
                raise HarnessControlError(f"{name} must be positive")
        self.identity = identity
        self._adapter = adapter
        self._clock = clock
        self._transcript: deque[TranscriptEntry] = deque(maxlen=transcript_limit)
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
                    updated = reduce_adapter_event(self._snapshot, event)
                    # The authority consumes the direct event before public subscribers see the
                    # coalesced snapshot. Draining from subscribe() would lose intermediate
                    # completion identity under subscriber backpressure.
                    await self._command_queue.observe_event(event)
                    self._append_transcript(event.transcript)
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
