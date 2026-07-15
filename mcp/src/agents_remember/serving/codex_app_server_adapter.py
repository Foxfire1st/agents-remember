"""Capability-negotiated Codex app-server adapter for the L1 harness contract."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal

from agents_remember.errors import (
    CodexAppServerError,
    CodexAppServerRpcError,
    HarnessAdapterDisconnectedError,
    HarnessControlError,
)
from agents_remember.serving.codex_app_server_protocol import (
    CODEX_APP_SERVER_PROTOCOL,
    CodexAppServerTransport,
    CodexStdioTransport,
    JsonObject,
)
from agents_remember.serving.codex_app_server_session import (
    CodexAppServerSession,
    CodexAppServerSettings,
    TransportFactory,
)
from agents_remember.serving.codex_app_server_state import (
    CodexServerInteraction,
    CodexSubmissionLedger,
    SubmissionEvidence,
    activity_from_thread_status,
    find_request_turn,
    interaction_result,
    iso_from_epoch,
    iso_from_millis,
    parse_server_interaction,
    parse_turn,
    required_object,
    required_text,
    terminal_result,
    transcript_from_item,
)
from agents_remember.serving.harness_capabilities import CapabilitySnapshot
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    REQUIRED_ADAPTER_CAPABILITIES,
    AdapterEvent,
    AdapterHandshake,
    AdapterSnapshot,
    InteractionResponse,
    LaunchSpec,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
    SubmissionReceipt,
    TranscriptEntry,
)

Clock = Callable[[], str]


class CodexAppServerAdapter:
    """One stable app-server connection/thread with exact request and turn correlation."""

    def __init__(
        self,
        settings: CodexAppServerSettings,
        *,
        transport_factory: TransportFactory = CodexStdioTransport,
        clock: Clock = lambda: datetime.now(UTC).isoformat(),
    ) -> None:
        self._settings = settings
        self._session = CodexAppServerSession(settings, transport_factory=transport_factory)
        self._clock = clock
        self._snapshot: AdapterSnapshot | None = None
        self._events: asyncio.Queue[AdapterEvent | None] = asyncio.Queue(maxsize=256)
        self._processor: asyncio.Task[None] | None = None
        self._stopped = False
        self._active_turn_id: str | None = None
        self._pending_interaction: CodexServerInteraction | None = None
        self._submissions = CodexSubmissionLedger(settings.submission_limit)
        self._busy_queue: deque[SubmissionEvidence] = deque()
        self._event_sequence = 0
        self._transcript_sequence = 0

    async def start(self, launch: LaunchSpec) -> AdapterHandshake:
        if self._session.transport is not None:
            raise CodexAppServerError("Codex adapter is already started")
        if launch.harness_id != "codex":
            raise CodexAppServerError("Codex adapter requires harness_id='codex'")
        snapshot = await self._session.connect(
            launch,
            resume_thread_id=self._settings.resume_thread_id,
        )
        self._snapshot = replace(
            snapshot,
            acceptance=self._busy_acceptance(snapshot.activity, snapshot.acceptance),
        )
        transport = self._require_transport()
        self._processor = asyncio.create_task(self._run_messages(transport))
        cli_version = self._session.cli_version
        assert cli_version is not None
        return AdapterHandshake(
            protocol_version=CONTROL_PROTOCOL_VERSION,
            adapter_id=f"{CODEX_APP_SERVER_PROTOCOL}:{cli_version}",
            identity=launch.identity,
            capabilities=REQUIRED_ADAPTER_CAPABILITIES,
            snapshot=self._snapshot,
            raw=self._session.capability_snapshot(),
        )

    async def snapshot(self) -> AdapterSnapshot:
        if self._snapshot is None:
            raise CodexAppServerError("Codex adapter is not started")
        return self._snapshot

    async def discover(self, launch: LaunchSpec) -> CapabilitySnapshot:
        if launch.harness_id != "codex":
            raise CodexAppServerError("Codex adapter requires harness_id='codex'")
        return await self._session.discover(launch)

    def advertise(self) -> CapabilitySnapshot:
        self._require_ready()
        return self._session.advertise()

    async def _event_stream(self) -> AsyncIterator[AdapterEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        return self._event_stream()

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self._require_ready()
        evidence = self._submissions.reserve(request)
        if evidence is None:
            return SubmissionReceipt(
                request_id=request.request_id,
                acceptance="rejected",
                submitted_at=request.submitted_at,
                detail="Codex adapter correlation ledger is full",
            )
        if self._active_turn_id is None:
            return await self._start_turn(evidence)
        if self._settings.busy_policy == "steer":
            return await self._steer_turn(evidence)
        if len(self._busy_queue) >= self._settings.busy_queue_limit:
            evidence.state = "rejected"
            return SubmissionReceipt(
                request_id=request.request_id,
                acceptance="rejected",
                submitted_at=request.submitted_at,
                detail="Codex busy queue is full",
            )
        evidence.state = "queued"
        self._busy_queue.append(evidence)
        return SubmissionReceipt(
            request_id=request.request_id,
            acceptance="queued",
            submitted_at=request.submitted_at,
            detail="queued until the active Codex turn completes",
            raw={"busyPolicy": "queue", "queuePosition": len(self._busy_queue)},
        )

    async def respond(self, response: InteractionResponse) -> None:
        pending = self._pending_interaction
        if pending is None or response.interaction_id != pending.pending.interaction_id:
            raise CodexAppServerError(
                "Codex interaction response does not match the pending request"
            )
        transport = self._require_transport()
        result = interaction_result(pending.method, response.response)
        await transport.respond(pending.rpc_id, result)
        self._pending_interaction = None
        self._snapshot = replace(
            await self.snapshot(),
            activity="settling",
            acceptance="queued",
            pending_interaction=None,
            raw={**(await self.snapshot()).raw, "resolvedServerRequest": pending.method},
        )

    async def reconcile(self, request_id: str) -> ReconciliationResult:
        evidence = self._submissions.get(request_id)
        if evidence is None or evidence.state != "unknown":
            raise CodexAppServerError("only an unknown Codex submission can be reconciled")
        if (await self.snapshot()).control == "disconnected":
            await self._reconnect()
        transport = self._require_transport()
        result = await transport.request(
            "thread/read",
            {"threadId": self._require_thread_id(), "includeTurns": True},
        )
        thread = required_object(result.get("thread"), context="thread/read response.thread")
        thread_id = required_text(thread, "id", context="thread/read response.thread")
        if thread_id != self._require_thread_id():
            raise CodexAppServerError("thread/read returned a different Codex thread id")
        turn_id = find_request_turn(
            thread,
            request_id=request_id,
            request_text=evidence.request.text,
            known_turn_id=evidence.turn_id,
        )
        if turn_id is None:
            return ReconciliationResult(
                request_id=request_id,
                state="unresolved",
                reconciled_at=self._clock(),
                detail="no exact Codex clientUserMessageId/turn evidence; request was not resent",
                raw={"threadId": thread_id, "resend": False},
            )
        evidence.state = "accepted"
        evidence.turn_id = turn_id
        return ReconciliationResult(
            request_id=request_id,
            state="accepted",
            reconciled_at=self._clock(),
            vendor_correlation_id=turn_id,
            detail="matched exact Codex thread/turn/user-item evidence",
            raw={"threadId": thread_id, "resend": False},
        )

    async def stop(self, mode: ShutdownMode) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._processor is not None:
            self._processor.cancel()
            await asyncio.gather(self._processor, return_exceptions=True)
        if self._session.transport is not None:
            await self._session.transport.stop(mode)
        self._offer_event(None)

    async def _start_turn(self, evidence: SubmissionEvidence) -> SubmissionReceipt:
        launch = self._session.launch
        model = self._session.model
        assert launch is not None and model is not None
        params: JsonObject = {
            "threadId": self._require_thread_id(),
            "input": [{"type": "text", "text": evidence.request.text}],
            "clientUserMessageId": evidence.request.request_id,
            "model": model.model,
            "cwd": str(launch.cwd),
            "effort": self._settings.reasoning_effort,
        }
        for key, value in (
            ("approvalPolicy", self._settings.approval_policy),
            ("approvalsReviewer", self._settings.approvals_reviewer),
            ("sandboxPolicy", self._settings.turn_sandbox_policy),
        ):
            if value is not None:
                params[key] = dict(value) if isinstance(value, Mapping) else value
        try:
            result = await self._require_transport().request("turn/start", params)
        except CodexAppServerRpcError as exc:
            evidence.state = "rejected"
            return SubmissionReceipt(
                request_id=evidence.request.request_id,
                acceptance="rejected",
                submitted_at=evidence.request.submitted_at,
                detail=str(exc),
            )
        except HarnessAdapterDisconnectedError as exc:
            evidence.state = "unknown"
            await self._mark_disconnected(str(exc))
            raise HarnessAdapterDisconnectedError(
                str(exc),
                may_have_sent=exc.may_have_sent,
                vendor_correlation_id=evidence.turn_id,
            ) from exc
        turn = parse_turn(result, context="turn/start response")
        turn_id = required_text(turn, "id", context="turn/start response.turn")
        evidence.state = "accepted"
        evidence.turn_id = turn_id
        if turn.get("status") == "inProgress":
            self._active_turn_id = turn_id
            await self._set_activity("running", turn_id=turn_id)
        return SubmissionReceipt(
            request_id=evidence.request.request_id,
            acceptance="immediate",
            submitted_at=evidence.request.submitted_at,
            vendor_correlation_id=turn_id,
            accepted_at=self._clock(),
            raw={"method": "turn/start", "clientUserMessageId": evidence.request.request_id},
        )

    async def _steer_turn(self, evidence: SubmissionEvidence) -> SubmissionReceipt:
        turn_id = self._active_turn_id
        assert turn_id is not None
        try:
            result = await self._require_transport().request(
                "turn/steer",
                {
                    "threadId": self._require_thread_id(),
                    "expectedTurnId": turn_id,
                    "input": [{"type": "text", "text": evidence.request.text}],
                    "clientUserMessageId": evidence.request.request_id,
                },
            )
        except CodexAppServerRpcError as exc:
            evidence.state = "rejected"
            return SubmissionReceipt(
                request_id=evidence.request.request_id,
                acceptance="rejected",
                submitted_at=evidence.request.submitted_at,
                detail=str(exc),
            )
        echoed_turn_id = required_text(result, "turnId", context="turn/steer response")
        if echoed_turn_id != turn_id:
            raise CodexAppServerError("turn/steer response changed the active Codex turn id")
        evidence.state = "accepted"
        evidence.turn_id = turn_id
        return SubmissionReceipt(
            request_id=evidence.request.request_id,
            acceptance="immediate",
            submitted_at=evidence.request.submitted_at,
            vendor_correlation_id=turn_id,
            accepted_at=self._clock(),
            raw={"method": "turn/steer", "clientUserMessageId": evidence.request.request_id},
        )

    async def _run_messages(self, transport: CodexAppServerTransport) -> None:
        try:
            async for message in transport.messages():
                await self._handle_message(message)
        except asyncio.CancelledError:
            raise
        except HarnessAdapterDisconnectedError as exc:
            await self._mark_disconnected(str(exc))
        except HarnessControlError as exc:
            await self._mark_failed(str(exc))

    async def _handle_message(self, message: JsonObject) -> None:
        method = required_text(message, "method", context="Codex app-server message")
        if "id" in message:
            await self._handle_server_request(message)
            return
        params = required_object(message.get("params"), context=f"{method} params")
        if method == "thread/status/changed":
            self._validate_thread(params)
            activity, acceptance = activity_from_thread_status(
                required_object(params.get("status"), context="thread/status/changed status")
            )
            self._snapshot = replace(
                await self.snapshot(),
                activity=activity,
                acceptance=self._busy_acceptance(activity, acceptance),
            )
            await self._emit("state", raw={"codexMethod": method})
            return
        if method == "turn/started":
            self._validate_thread(params)
            turn = parse_turn(params, context="turn/started params")
            self._active_turn_id = required_text(turn, "id", context="turn/started turn")
            await self._set_activity("running", turn_id=self._active_turn_id)
            return
        if method == "turn/completed":
            await self._handle_turn_completed(params)
            return
        if method == "item/completed":
            await self._handle_item_completed(params)
            return
        if method == "serverRequest/resolved":
            await self._handle_server_request_resolved(params)
            return
        if method == "thread/settings/updated":
            self._handle_settings_updated(params)
            await self._emit("state", raw={"codexMethod": method})
            return
        await self._emit("codex-notification", raw={"codexMethod": method})

    async def _handle_turn_completed(self, params: JsonObject) -> None:
        self._validate_thread(params)
        turn = parse_turn(params, context="turn/completed params")
        turn_id = required_text(turn, "id", context="turn/completed turn")
        if self._active_turn_id is not None and turn_id != self._active_turn_id:
            raise CodexAppServerError("turn/completed does not match the active Codex turn")
        completed_at = iso_from_epoch(turn.get("completedAt"), fallback=self._clock())
        self._transcript_sequence += 1
        transcript = TranscriptEntry(
            sequence=self._transcript_sequence,
            role="result",
            text=f"Codex turn {turn.get('status')}",
            created_at=completed_at,
            vendor_correlation_id=turn_id,
            terminal_result=terminal_result(turn, completed_at=completed_at),
            raw={"codexTurnId": turn_id},
        )
        for evidence in self._submissions.values():
            if evidence.turn_id == turn_id:
                evidence.state = "completed"
        self._active_turn_id = None
        self._snapshot = replace(
            await self.snapshot(),
            activity="settling" if self._busy_queue else "idle",
            acceptance="queued" if self._busy_queue else "immediate",
        )
        await self._emit(
            "completed",
            transcript=(transcript,),
            raw={"codexMethod": "turn/completed", "turnId": turn_id},
        )
        await self._dispatch_queued()

    async def _handle_item_completed(self, params: JsonObject) -> None:
        self._validate_thread(params)
        turn_id = required_text(params, "turnId", context="item/completed params")
        item = required_object(params.get("item"), context="item/completed params.item")
        self._transcript_sequence += 1
        transcript = transcript_from_item(
            item,
            sequence=self._transcript_sequence,
            created_at=iso_from_millis(params.get("completedAtMs"), fallback=self._clock()),
            turn_id=turn_id,
        )
        if transcript is None:
            self._transcript_sequence -= 1
            await self._emit("codex-notification", raw={"codexMethod": "item/completed"})
            return
        await self._emit(
            "transcript",
            transcript=(transcript,),
            raw={"codexMethod": "item/completed", "turnId": turn_id},
        )

    async def _handle_server_request(self, message: JsonObject) -> None:
        request_id = message.get("id")
        try:
            interaction = parse_server_interaction(message, created_at=self._clock())
            self._validate_thread(required_object(message.get("params"), context="server request"))
            if self._pending_interaction is not None:
                raise CodexAppServerError("Codex emitted multiple unresolved server requests")
        except CodexAppServerError as exc:
            if isinstance(request_id, (str, int)) and not isinstance(request_id, bool):
                await self._require_transport().respond_error(
                    request_id,
                    code=-32601,
                    message=str(exc),
                )
            raise
        self._pending_interaction = interaction
        self._snapshot = replace(
            await self.snapshot(),
            activity="blocked",
            acceptance="queued",
            pending_interaction=interaction.pending,
        )
        await self._emit("state", raw={"codexMethod": interaction.method})

    async def _handle_server_request_resolved(self, params: JsonObject) -> None:
        self._validate_thread(params)
        request_id = params.get("requestId")
        pending = self._pending_interaction
        if pending is not None and request_id == pending.rpc_id:
            self._pending_interaction = None
            self._snapshot = replace(
                await self.snapshot(),
                activity="settling",
                acceptance="queued",
                pending_interaction=None,
            )
        await self._emit("state", raw={"codexMethod": "serverRequest/resolved"})

    def _handle_settings_updated(self, params: JsonObject) -> None:
        self._validate_thread(params)
        settings = required_object(
            params.get("threadSettings"), context="thread/settings/updated threadSettings"
        )
        effort = required_text(settings, "effort", context="thread/settings/updated")
        model = required_text(settings, "model", context="thread/settings/updated")
        selected = self._session.model
        assert selected is not None
        if effort != self._settings.reasoning_effort or model != selected.model:
            raise CodexAppServerError(
                "Codex thread/settings/updated changed the configured model or reasoning effort"
            )
        self._session.record_effective_effort(effort)
        assert self._snapshot is not None
        self._snapshot = replace(
            self._snapshot,
            raw={**self._snapshot.raw, "effectiveReasoningEffort": effort},
        )

    async def _dispatch_queued(self) -> None:
        if not self._busy_queue or self._active_turn_id is not None:
            return
        evidence = self._busy_queue.popleft()
        receipt = await self._start_turn(evidence)
        if receipt.acceptance == "rejected":
            assert self._snapshot is not None
            self._snapshot = replace(
                self._snapshot,
                acceptance="rejected",
                raw={
                    **self._snapshot.raw,
                    "queuedDispatchRejected": evidence.request.request_id,
                },
            )
            await self._emit("state", raw={"codexMethod": "turn/start"})

    async def _reconnect(self) -> None:
        thread_id = self._require_thread_id()
        if self._processor is not None:
            self._processor.cancel()
            await asyncio.gather(self._processor, return_exceptions=True)
        if self._session.transport is not None:
            await self._session.transport.stop("forced")
        launch = self._session.launch
        assert launch is not None
        snapshot = await self._session.connect(launch, resume_thread_id=thread_id)
        self._snapshot = replace(
            snapshot,
            acceptance=self._busy_acceptance(snapshot.activity, snapshot.acceptance),
        )
        self._processor = asyncio.create_task(self._run_messages(self._require_transport()))
        await self._emit("state", raw={"codexMethod": "thread/resume", "reconnected": True})

    async def _set_activity(self, activity: Literal["running"], *, turn_id: str) -> None:
        assert self._snapshot is not None
        self._snapshot = replace(
            self._snapshot,
            activity=activity,
            acceptance="immediate" if self._settings.busy_policy == "steer" else "queued",
            raw={**self._snapshot.raw, "activeTurnId": turn_id},
        )
        await self._emit("state", raw={"codexMethod": "turn/started", "turnId": turn_id})

    async def _mark_disconnected(self, detail: str) -> None:
        if self._snapshot is None or self._snapshot.control == "disconnected":
            return
        self._snapshot = replace(
            self._snapshot,
            control="disconnected",
            activity="unknown",
            acceptance="unknown",
            raw={**self._snapshot.raw, "disconnect": detail},
        )
        await self._emit("disconnected", raw={"codexDisconnect": True})

    async def _mark_failed(self, detail: str) -> None:
        if self._snapshot is None:
            return
        self._snapshot = replace(
            self._snapshot,
            control="failed",
            activity="unknown",
            acceptance="rejected",
            raw={**self._snapshot.raw, "protocolError": detail},
        )
        await self._emit("failed", raw={"codexProtocolFailure": True})

    async def _emit(
        self,
        kind: str,
        *,
        transcript: tuple[TranscriptEntry, ...] = (),
        raw: Mapping[str, object] | None = None,
    ) -> None:
        launch = self._session.launch
        assert self._snapshot is not None and launch is not None
        self._event_sequence += 1
        self._snapshot = replace(self._snapshot, last_event_sequence=self._event_sequence)
        event = AdapterEvent(
            sequence=self._event_sequence,
            kind=kind,
            identity=launch.identity,
            created_at=self._clock(),
            snapshot=self._snapshot
            if kind in {"state", "completed", "disconnected", "failed"}
            else None,
            transcript=transcript,
            raw=dict(raw or {}),
        )
        if self._events.full():
            raise CodexAppServerError("Codex adapter event queue is full")
        self._events.put_nowait(event)

    def _busy_acceptance(self, activity: str, fallback: str) -> str:
        if activity == "running":
            return "immediate" if self._settings.busy_policy == "steer" else "queued"
        return fallback

    def _validate_thread(self, params: Mapping[str, object]) -> None:
        thread_id = required_text(params, "threadId", context="Codex notification")
        if thread_id != self._session.thread_id:
            raise CodexAppServerError("Codex message belongs to a different thread")

    def _require_ready(self) -> None:
        if self._stopped:
            raise CodexAppServerError("Codex adapter is stopped")
        snapshot = self._snapshot
        if snapshot is None or snapshot.control != "ready":
            raise HarnessAdapterDisconnectedError(
                "Codex adapter is not protocol-ready",
                may_have_sent=False,
            )

    def _require_transport(self) -> CodexAppServerTransport:
        if self._session.transport is None:
            raise CodexAppServerError("Codex transport is not started")
        return self._session.transport

    def _require_thread_id(self) -> str:
        if self._session.thread_id is None:
            raise CodexAppServerError("Codex thread identity is not established")
        return self._session.thread_id

    def _offer_event(self, event: AdapterEvent | None) -> None:
        if not self._events.full():
            self._events.put_nowait(event)
