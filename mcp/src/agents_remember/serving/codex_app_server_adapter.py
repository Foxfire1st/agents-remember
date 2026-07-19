"""Capability-negotiated Codex app-server adapter for the L1 harness contract."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal

from agents_remember.errors import (
    CodexAppServerError,
    CodexAppServerRpcError,
    HarnessAdapterBusyError,
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
    native_evidence_frames_from_thread,
    parse_server_interaction,
    parse_turn,
    required_object,
    required_text,
    terminal_result,
    transcript_from_item,
)
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    LaunchKnobs,
    SetResult,
)
from agents_remember.serving.harness_control_models import (
    AR_EVIDENCE_KEY,
    CONTROL_PROTOCOL_VERSION,
    REQUIRED_ADAPTER_CAPABILITIES,
    AdapterEvent,
    AdapterHandshake,
    AdapterSnapshot,
    ControlOperationRef,
    InteractionResponse,
    LaunchSpec,
    NativeEvidencePage,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
    SubmissionReceipt,
    TranscriptEntry,
    window_native_evidence_page,
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
        self._fresh_turn_required = False
        self._pending_operation: ControlOperationRef | None = None
        self._active_operation: ControlOperationRef | None = None
        self._turn_operations: dict[str, ControlOperationRef] = {}
        self._unbound_completions: dict[str, JsonObject] = {}
        self._completed_turns: OrderedDict[str, ControlOperationRef] = OrderedDict()
        self._completed_turn_limit = settings.submission_limit
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

    def launch_knobs(self, *, model_key: str, effort: str | None) -> LaunchKnobs:
        """Carry native Codex launch state through thread/start, never CODEX_CONFIG."""

        if not model_key or model_key != model_key.strip():
            raise CodexAppServerError(
                "Codex launch model must be non-empty with no outer whitespace"
            )
        if effort is None or not effort or effort != effort.strip():
            raise CodexAppServerError(
                "Codex launch effort must be non-empty with no outer whitespace"
            )
        return LaunchKnobs(
            session_config={
                "model": model_key,
                "model_reasoning_effort": effort,
            },
            owned_argv_options=("--model", "-m"),
            owned_config_keys=("model", "model_reasoning_effort"),
        )

    async def set_model(
        self, model_key: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del operation
        self._require_ready()
        try:
            rebase_detail = self._session.set_desired_model(model_key)
        except CodexAppServerError as exc:
            return SetResult(
                ok=False,
                acceptance="unsupported",
                requested_value=model_key,
                detail=str(exc),
            )
        self._fresh_turn_required = self._session.has_pending_settings
        self._refresh_capability_snapshot()
        if not self._session.has_pending_settings:
            return SetResult(
                ok=True,
                acceptance="immediate",
                requested_value=model_key,
                detail="Codex already has this model as its effective thread setting",
            )
        detail = "queued for the next fresh Codex turn on the existing thread"
        if rebase_detail is not None:
            detail = f"{detail}; {rebase_detail}"
        return SetResult(
            ok=True,
            acceptance="queued",
            requested_value=model_key,
            detail=detail,
        )

    async def set_effort(
        self, effort: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del operation
        self._require_ready()
        try:
            self._session.set_desired_effort(effort)
        except CodexAppServerError as exc:
            return SetResult(
                ok=False,
                acceptance="unsupported",
                requested_value=effort,
                detail=str(exc),
            )
        self._fresh_turn_required = self._session.has_pending_settings
        self._refresh_capability_snapshot()
        if not self._session.has_pending_settings:
            return SetResult(
                ok=True,
                acceptance="immediate",
                requested_value=effort,
                detail="Codex already has this effort as its effective thread setting",
            )
        return SetResult(
            ok=True,
            acceptance="queued",
            requested_value=effort,
            detail="queued for the next fresh Codex turn on the existing thread",
        )

    async def _event_stream(self) -> AsyncIterator[AdapterEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        return self._event_stream()

    async def preflight_operation(self, operation: ControlOperationRef) -> None:
        self._require_ready()
        if (
            self._active_turn_id is not None
            or self._pending_operation is not None
            or self._pending_interaction is not None
        ):
            raise HarnessAdapterBusyError(
                f"Codex is not idle at {operation.kind} preflight"
            )

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self._require_ready()
        operation = request.operation
        if operation is None or operation.kind != "prompt":
            raise CodexAppServerError("Codex submit requires an exact prompt operation ref")
        if self._active_turn_id is not None or self._pending_operation is not None:
            raise HarnessAdapterBusyError("Codex already has an active ordinary operation")
        evidence = self._submissions.reserve(
            request,
            model=self._session.require_desired_model(),
            effort=self._session.require_desired_effort(),
        )
        if evidence is None:
            return SubmissionReceipt(
                request_id=request.request_id,
                acceptance="rejected",
                submitted_at=request.submitted_at,
                detail="Codex adapter correlation ledger is full",
            )
        self._pending_operation = operation
        try:
            return await self._start_turn(evidence, operation)
        finally:
            if self._pending_operation == operation:
                self._pending_operation = None

    async def respond(self, response: InteractionResponse) -> None:
        pending = self._pending_interaction
        if pending is None or response.interaction_id != pending.pending.interaction_id:
            raise CodexAppServerError(
                "Codex interaction response does not match the pending request"
            )
        if response.operation is None or response.operation != self._active_operation:
            raise CodexAppServerError("Codex response does not match the active operation")
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

    async def read_native_page(
        self,
        *,
        cursor: str | None,
        limit: int,
        byte_budget: int,
    ) -> NativeEvidencePage:
        """Page the live thread through ``thread/read``; the native read stays the authority."""

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
        frames = native_evidence_frames_from_thread(thread)
        try:
            return window_native_evidence_page(
                frames,
                cursor=cursor,
                limit=limit,
                byte_budget=byte_budget,
            )
        except HarnessControlError as exc:
            raise CodexAppServerError(f"Codex native evidence page failed: {exc}") from exc

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

    async def _start_turn(
        self, evidence: SubmissionEvidence, operation: ControlOperationRef
    ) -> SubmissionReceipt:
        launch = self._session.launch
        model = evidence.model
        assert launch is not None
        params: JsonObject = {
            "threadId": self._require_thread_id(),
            "input": [{"type": "text", "text": evidence.request.text}],
            "clientUserMessageId": evidence.request.request_id,
            "model": model.model,
            "cwd": str(launch.cwd),
            "effort": evidence.effort,
        }
        for key, value in (
            ("approvalPolicy", self._settings.approval_policy),
            ("approvalsReviewer", self._settings.approvals_reviewer),
            ("sandboxPolicy", self._settings.turn_sandbox_policy),
        ):
            if value is not None:
                params[key] = dict(value) if isinstance(value, Mapping) else value
        try:
            result = await self._require_transport().request(
                "turn/start",
                params,
                before_write=lambda: self._guard_turn_start(operation),
            )
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
        buffered: JsonObject | None = None
        if self._unbound_completions:
            buffered_turn_id, buffered = next(iter(self._unbound_completions.items()))
            self._unbound_completions.clear()
            if buffered_turn_id != turn_id:
                raise CodexAppServerError(
                    "buffered turn/completed id does not match the turn/start response"
                )
        if turn_id in self._completed_turns:
            raise CodexAppServerError("turn/start reused a retained terminal turn id")
        evidence.turn_id = turn_id
        self._turn_operations[turn_id] = operation
        status = required_text(turn, "status", context="turn/start response.turn")
        if status in {"failed", "interrupted"}:
            evidence.state = "rejected"
            self._remember_completed_turn(turn_id, operation)
            self._fresh_turn_required = self._session.has_pending_settings
            self._refresh_capability_snapshot()
            return SubmissionReceipt(
                request_id=evidence.request.request_id,
                acceptance="rejected",
                submitted_at=evidence.request.submitted_at,
                vendor_correlation_id=turn_id,
                detail=f"Codex turn/start returned terminal status {status!r}",
                raw={
                    "method": "turn/start",
                    "clientUserMessageId": evidence.request.request_id,
                    "turnStatus": status,
                },
            )
        evidence.state = "accepted" if status == "inProgress" else "completed"
        self._session.accept_settings_selection(model=evidence.model, effort=evidence.effort)
        self._fresh_turn_required = self._session.has_pending_settings
        self._refresh_capability_snapshot()
        if status == "inProgress":
            self._active_turn_id = turn_id
            self._active_operation = operation
            await self._set_activity("running", turn_id=turn_id)
        completion_emitted = False
        if buffered is not None:
            await self._handle_turn_completed(buffered)
            completion_emitted = True
        terminal_completion = status != "inProgress" and not completion_emitted
        if terminal_completion:
            self._remember_completed_turn(turn_id, operation)
        return SubmissionReceipt(
            request_id=evidence.request.request_id,
            acceptance="immediate",
            submitted_at=evidence.request.submitted_at,
            vendor_correlation_id=turn_id,
            accepted_at=self._clock(),
            raw={
                "method": "turn/start",
                "clientUserMessageId": evidence.request.request_id,
                "terminalCompletion": terminal_completion,
            },
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
            await self._emit("state", raw={"codexMethod": method, AR_EVIDENCE_KEY: params})
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
            await self._emit("state", raw={"codexMethod": method, AR_EVIDENCE_KEY: params})
            return
        await self._emit(
            "codex-notification", raw={"codexMethod": method, AR_EVIDENCE_KEY: params}
        )

    async def _handle_turn_completed(self, params: JsonObject) -> None:
        self._validate_thread(params)
        turn = parse_turn(params, context="turn/completed params")
        turn_id = required_text(turn, "id", context="turn/completed turn")
        if turn_id in self._completed_turns:
            return
        operation = self._turn_operations.get(turn_id)
        if operation is None:
            # Codex can deliver the notification before the turn/start response binds its turn id
            # to the pending operation. Keep only that one exact raw completion until binding.
            if self._pending_operation is None or self._unbound_completions:
                raise CodexAppServerError("turn/completed has no bindable pending operation")
            self._unbound_completions[turn_id] = params
            return
        if self._active_turn_id is not None and turn_id != self._active_turn_id:
            raise CodexAppServerError("turn/completed does not match the active Codex turn")
        if self._active_operation is not None and operation != self._active_operation:
            raise CodexAppServerError("turn/completed does not match the active operation")
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
        self._active_operation = None
        self._remember_completed_turn(turn_id, operation)
        self._snapshot = replace(
            await self.snapshot(),
            activity="idle",
            acceptance="immediate",
        )
        await self._emit(
            "completed",
            transcript=(transcript,),
            raw={"codexMethod": "turn/completed", "turnId": turn_id, AR_EVIDENCE_KEY: params},
            operation=operation,
        )

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
            await self._emit(
                "codex-notification",
                raw={"codexMethod": "item/completed", AR_EVIDENCE_KEY: params},
            )
            return
        await self._emit(
            "transcript",
            transcript=(transcript,),
            raw={"codexMethod": "item/completed", "turnId": turn_id, AR_EVIDENCE_KEY: params},
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
        self._session.accept_settings_update(model=model, effort=effort)
        self._refresh_capability_snapshot()

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
            acceptance="immediate",
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
        operation: ControlOperationRef | None = None,
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
            operation=operation,
        )
        if self._events.full():
            raise CodexAppServerError("Codex adapter event queue is full")
        self._events.put_nowait(event)

    def _busy_acceptance(self, activity: str, fallback: str) -> str:
        if activity == "running":
            return "immediate"
        return fallback

    def _guard_turn_start(self, operation: ControlOperationRef) -> None:
        """Final synchronous guard executed under the transport write lock."""

        if (
            self._pending_operation != operation
            or self._active_turn_id is not None
            or self._active_operation is not None
            or self._pending_interaction is not None
        ):
            raise HarnessAdapterBusyError(
                "Codex became busy before the guarded turn/start write"
            )

    def _remember_completed_turn(
        self, turn_id: str, operation: ControlOperationRef
    ) -> None:
        """Release live correlation and retain only the bounded terminal duplicate window."""

        self._turn_operations.pop(turn_id, None)
        self._completed_turns[turn_id] = operation
        self._completed_turns.move_to_end(turn_id)
        while len(self._completed_turns) > self._completed_turn_limit:
            self._completed_turns.popitem(last=False)

    def _validate_thread(self, params: Mapping[str, object]) -> None:
        thread_id = required_text(params, "threadId", context="Codex notification")
        if thread_id != self._session.thread_id:
            raise CodexAppServerError("Codex message belongs to a different thread")

    def _refresh_capability_snapshot(self) -> None:
        if self._snapshot is None:
            return
        self._snapshot = replace(
            self._snapshot,
            raw={
                **self._snapshot.raw,
                **self._session.capability_snapshot(),
                "freshTurnRequired": self._fresh_turn_required,
            },
        )

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
