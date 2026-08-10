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
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlOperationRef,
    InterruptResult,
    LaunchSpec,
    SubmissionReceipt,
)
from agents_remember.models.conversations.evidence import (
    AR_EVIDENCE_KEY,
    AR_EVIDENCE_METHOD_KEY,
    NativeEvidencePage,
)
from agents_remember.serving.codex_agent_lifecycle import (
    completed_turn_status,
    merge_agent_status,
)
from agents_remember.serving.codex_app_server_events import CodexEventQueue
from agents_remember.serving.codex_app_server_history import CodexNativeHistoryReader
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
    STABLE_SERVER_REQUESTS,
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
from agents_remember.serving.codex_app_server_threads import (
    PENDING_INTERACTIONS_PER_THREAD,
    CodexThreadRegistry,
    CodexThreadState,
)
from agents_remember.serving.codex_app_server_turns import (
    StartedTurn,
    accepted_turn_receipt,
    rejected_turn_receipt,
    turn_start_params,
    verified_asset_path,
)
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    SetResult,
)
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    REQUIRED_ADAPTER_CAPABILITIES,
    AdapterEvent,
    AdapterHandshake,
    InteractionResponse,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
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
        self._native_history = CodexNativeHistoryReader()
        self._clock = clock
        self._snapshot: AdapterSnapshot | None = None
        self._events = CodexEventQueue(notice=self._load_shed_notice)
        self._processor: asyncio.Task[None] | None = None
        self._stopped = False
        self._threads = CodexThreadRegistry(
            session_thread_id=lambda: self._session.thread_id,
            completed_turn_limit=settings.submission_limit,
            on_register=self._publish_agent_registry,
        )
        self._submissions = CodexSubmissionLedger(settings.submission_limit)
        self._fresh_turn_required = False
        self._pending_operation: ControlOperationRef | None = None
        self._active_operation: ControlOperationRef | None = None
        self._event_sequence = 0
        self._transcript_sequence = 0
        self._last_interrupt: tuple[tuple[str, str], InterruptResult] | None = None

    async def start(self, launch: LaunchSpec) -> AdapterHandshake:
        if self._session.transport is not None:
            raise CodexAppServerError("Codex adapter is already started")
        if launch.harness_id != "codex":
            raise CodexAppServerError("Codex adapter requires harness_id='codex'")
        snapshot = await self._session.connect(
            launch,
            resume_thread_id=self._settings.resume_thread_id,
        )
        self._threads.parent()
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

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        return self._events.stream()

    async def preflight_operation(self, operation: ControlOperationRef) -> None:
        self._require_ready()
        parent = self._threads.parent()
        if (
            parent.active_turn_id is not None
            or self._pending_operation is not None
            or parent.pending_interaction is not None
        ):
            raise HarnessAdapterBusyError(f"Codex is not idle at {operation.kind} preflight")

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self._require_ready()
        operation = request.operation
        if operation is None or operation.kind != "prompt":
            raise CodexAppServerError("Codex submit requires an exact prompt operation ref")
        parent = self._threads.parent()
        if parent.active_turn_id is not None or self._pending_operation is not None:
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

    async def submit_with_assets(self, request: PromptRequest) -> SubmissionReceipt:
        """Asset-capable submit: pre-verify staged bytes before any native write."""

        try:
            for asset in request.assets:
                verified_asset_path(asset)
        except CodexAppServerError as exc:
            return SubmissionReceipt(
                request_id=request.request_id,
                acceptance="rejected",
                submitted_at=request.submitted_at,
                detail=str(exc),
            )
        return await self.submit(request)

    async def interrupt(
        self,
        *,
        turn_id: str | None,
        expected_operation_id: str | None,
    ) -> InterruptResult:
        """One native ``turn/interrupt`` against the exact active turn, replaying once.

        ``expected_operation_id`` is evidence on the result, never a guard input: the codex
        guard is the native turn identity. A repeat naming the same active turn replays the
        first acknowledgement with no second native write.
        """

        del expected_operation_id
        active = self._threads.parent().active_turn_id
        if active is None:
            raise CodexAppServerError("no active Codex turn to interrupt")
        if turn_id is not None and turn_id != active:
            raise CodexAppServerError("interrupt turn id does not match the active Codex turn")
        pair = (turn_id or active, active)
        if self._last_interrupt is not None and self._last_interrupt[0] == pair:
            return self._last_interrupt[1]
        operation = self._active_operation
        try:
            await self._require_transport().request(
                "turn/interrupt",
                {"threadId": self._require_thread_id(), "turnId": active},
            )
        except CodexAppServerRpcError as exc:
            result = InterruptResult(
                acknowledgement="rejected",
                bridge_epoch="",
                operation=operation,
                vendor_correlation_id=active,
                detail=str(exc),
                raw={"codexMethod": "turn/interrupt", "turnId": active},
            )
        else:
            result = InterruptResult(
                acknowledgement="accepted",
                bridge_epoch="",
                operation=operation,
                vendor_correlation_id=active,
                detail="native interrupt acknowledged for the exact active Codex turn",
                raw={"codexMethod": "turn/interrupt", "turnId": active},
            )
        self._last_interrupt = (pair, result)
        return result

    async def respond(self, response: InteractionResponse) -> None:
        found = self._threads.interaction_thread(response.interaction_id)
        if found is None:
            raise CodexAppServerError(
                "Codex interaction response does not match the pending request"
            )
        state, rpc_id = found
        pending = state.pending_interactions[rpc_id]
        if state.is_parent and (
            response.operation is None or response.operation != self._active_operation
        ):
            raise CodexAppServerError("Codex response does not match the active operation")
        transport = self._require_transport()
        result = interaction_result(
            pending.method,
            response.response,
            params=pending.params,
        )
        await transport.respond(pending.rpc_id, result)
        del state.pending_interactions[rpc_id]
        if state.is_parent:
            self._snapshot = replace(
                await self.snapshot(),
                activity="settling",
                acceptance="queued",
                raw={**(await self.snapshot()).raw, "resolvedServerRequest": pending.method},
            )
        self._sync_pending_snapshot()

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
        thread_id: str | None = None,
    ) -> NativeEvidencePage:
        """Page native history through the probed bounded contract or explicit legacy path.

        ``thread_id`` selects the thread to read: ``None`` reads the
        parent/session thread exactly as before, an explicit id reads that (sub-agent)
        thread. The dedicated history reader owns source paging, opaque continuation,
        item/byte limits, and method-unavailable-only legacy compatibility.
        """

        if (await self.snapshot()).control == "disconnected":
            await self._reconnect()
        requested_thread_id = thread_id if thread_id is not None else self._require_thread_id()
        transport = self._require_transport()
        return await self._native_history.read_page(
            transport,
            thread_id=requested_thread_id,
            cursor=cursor,
            limit=limit,
            byte_budget=byte_budget,
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
        self._events.offer(None)

    async def _start_turn(
        self, evidence: SubmissionEvidence, operation: ControlOperationRef
    ) -> SubmissionReceipt:
        launch = self._session.launch
        assert launch is not None
        params = turn_start_params(
            evidence,
            thread_id=self._require_thread_id(),
            cwd=launch.cwd,
            settings=self._settings,
        )
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
        parent = self._threads.parent()
        buffered = self._bind_started_turn(evidence, parent, turn_id=turn_id, operation=operation)
        status = required_text(turn, "status", context="turn/start response.turn")
        if status in {"failed", "interrupted"}:
            return self._rejected_turn_receipt(
                evidence, parent, turn_id=turn_id, status=status, operation=operation
            )
        return await self._accept_started_turn(
            evidence,
            parent,
            StartedTurn(turn_id=turn_id, status=status, operation=operation, buffered=buffered),
        )

    def _bind_started_turn(
        self,
        evidence: SubmissionEvidence,
        parent: CodexThreadState,
        *,
        turn_id: str,
        operation: ControlOperationRef,
    ) -> JsonObject | None:
        """Bind the started turn to this submission; hand back any completion buffered before it.

        ``turn/completed`` can win the race against the ``turn/start`` response it belongs to, and
        is buffered unbound until the id is known. A buffered frame naming a different id means
        the stream is describing some other turn, and a retained terminal id can never start
        again -- both fail the start rather than being reconciled into it.
        """

        buffered: JsonObject | None = None
        if parent.unbound_completions:
            buffered_turn_id, buffered = next(iter(parent.unbound_completions.items()))
            parent.unbound_completions.clear()
            if buffered_turn_id != turn_id:
                raise CodexAppServerError(
                    "buffered turn/completed id does not match the turn/start response"
                )
        if turn_id in parent.completed_turns:
            raise CodexAppServerError("turn/start reused a retained terminal turn id")
        evidence.turn_id = turn_id
        parent.turn_operations[turn_id] = operation
        return buffered

    def _rejected_turn_receipt(
        self,
        evidence: SubmissionEvidence,
        parent: CodexThreadState,
        *,
        turn_id: str,
        status: str,
        operation: ControlOperationRef,
    ) -> SubmissionReceipt:
        """Retire a turn ``turn/start`` already reported terminal, without accepting its selection.

        The pending model/effort selection stays pending precisely because the turn it would have
        been proven on never ran.
        """

        evidence.state = "rejected"
        self._threads.retire_turn(parent, turn_id, operation)
        self._fresh_turn_required = self._session.has_pending_settings
        self._refresh_capability_snapshot()
        return rejected_turn_receipt(evidence, turn_id=turn_id, status=status)

    async def _accept_started_turn(
        self,
        evidence: SubmissionEvidence,
        parent: CodexThreadState,
        started: StartedTurn,
    ) -> SubmissionReceipt:
        """Commit the accepted selection and settle a turn that started -- or already finished.

        Anything other than ``inProgress`` completed inside the start call, so it is retired here
        instead of waiting for a ``turn/completed`` that already arrived or will never come. A
        buffered completion is replayed first so it is not counted as a second terminal event.
        """

        turn_id, status = started.turn_id, started.status
        operation, buffered = started.operation, started.buffered
        evidence.state = "accepted" if status == "inProgress" else "completed"
        self._session.accept_settings_selection(model=evidence.model, effort=evidence.effort)
        self._fresh_turn_required = self._session.has_pending_settings
        self._refresh_capability_snapshot()
        if status == "inProgress":
            parent.active_turn_id = turn_id
            self._active_operation = operation
            await self._set_activity("running", turn_id=turn_id)
        completion_emitted = False
        if buffered is not None:
            await self._handle_turn_completed(buffered)
            completion_emitted = True
        terminal_completion = status != "inProgress" and not completion_emitted
        if terminal_completion:
            self._threads.retire_turn(parent, turn_id, operation)
        return accepted_turn_receipt(
            evidence,
            turn_id=turn_id,
            accepted_at=self._clock(),
            terminal_completion=terminal_completion,
        )

    async def _run_messages(self, transport: CodexAppServerTransport) -> None:
        try:
            async for message in transport.messages():
                try:
                    await self._handle_message(message)
                except CodexAppServerError as exc:
                    # Parent-thread shape errors keep failing the bridge (that contract is
                    # load-bearing); a malformed SUB-AGENT frame degrades to raw evidence
                    # instead of killing it.
                    if not await self._degrade_agent_frame(message, exc):
                        raise
        except asyncio.CancelledError:
            raise
        except HarnessAdapterDisconnectedError as exc:
            await self._mark_disconnected(str(exc))
        except HarnessControlError as exc:
            await self._mark_failed(str(exc))

    async def _degrade_agent_frame(
        self, message: JsonObject, exc: CodexAppServerError, *, force: bool = False
    ) -> bool:
        """Degrade one malformed non-parent frame to preserved raw evidence.

        Mirrors the projector's UnmappableShape discipline: the bridge stays ready and the
        frame crosses unmodified with its failure noted, rather than failing the whole
        bridge. Only a well-formed FOREIGN threadId qualifies — a missing/parent threadId
        re-raises exactly as before, unless ``force`` is set (unknown server-request
        METHODS degrade on any thread; a new vendor request type is traffic, not a
        protocol violation).
        """

        params = message.get("params")
        if not isinstance(params, Mapping):
            return False
        if not force:
            thread_id = params.get("threadId")
            if (
                not isinstance(thread_id, str)
                or not thread_id
                or thread_id == self._session.thread_id
            ):
                return False
        method = message.get("method")
        method_text = method if isinstance(method, str) else "unknown"
        await self._emit(
            "codex-notification",
            raw={
                "codexMethod": method_text,
                AR_EVIDENCE_METHOD_KEY: method_text,
                AR_EVIDENCE_KEY: dict(params),
                "degraded": str(exc),
            },
        )
        return True

    async def _handle_message(self, message: JsonObject) -> None:
        method = required_text(message, "method", context="Codex app-server message")
        if "id" in message:
            await self._handle_server_request(message)
            return
        params = required_object(message.get("params"), context=f"{method} params")
        if method == "thread/status/changed":
            await self._handle_status_changed(method, params)
        elif method == "turn/started":
            await self._handle_turn_started(method, params)
        elif method == "turn/completed":
            await self._handle_turn_completed(params)
        elif method == "item/completed":
            await self._handle_item_completed(params)
        elif method == "serverRequest/resolved":
            await self._handle_server_request_resolved(params)
        elif method == "thread/settings/updated":
            await self._handle_settings_notification(method, params)
        else:
            await self._handle_foreign_notification(method, params)

    async def _handle_status_changed(self, method: str, params: JsonObject) -> None:
        """Apply one ``thread/status/changed``.

        Only the parent thread's status is the session's status. A sub-agent's merges into the
        agent registry and crosses as raw evidence, so a busy sub-agent never makes the seat read
        as busy -- but its status shape is still validated, in either case, before anything moves.
        """

        state = self._threads.resolve(params, context="thread/status/changed params")
        activity, acceptance = activity_from_thread_status(
            required_object(params.get("status"), context="thread/status/changed status")
        )
        if state.is_parent:
            self._snapshot = replace(
                await self.snapshot(),
                activity=activity,
                acceptance=self._busy_acceptance(activity, acceptance),
            )
            await self._emit("state", raw={"codexMethod": method, AR_EVIDENCE_KEY: params})
            return
        candidate_status = required_text(
            required_object(params.get("status"), context="thread/status/changed status"),
            "type",
            context="thread/status/changed status",
        )
        state.status = merge_agent_status(state.status, candidate_status)
        self._publish_agent_registry()
        await self._emit_notification(method, params)

    async def _handle_turn_started(self, method: str, params: JsonObject) -> None:
        """Record the thread's new active turn id; only the parent's start moves session activity."""

        state = self._threads.resolve(params, context="turn/started params")
        turn = parse_turn(params, context="turn/started params")
        turn_id = required_text(turn, "id", context="turn/started turn")
        state.active_turn_id = turn_id
        if state.is_parent:
            await self._set_activity("running", turn_id=turn_id)
            return
        state.status = merge_agent_status(state.status, "running", explicit_turn_start=True)
        self._publish_agent_registry()
        await self._emit_notification(method, params)

    async def _handle_settings_notification(self, method: str, params: JsonObject) -> None:
        """Apply a parent-thread settings update; a sub-agent's own settings are evidence only."""

        state = self._threads.resolve(params, context="thread/settings/updated params")
        if not state.is_parent:
            await self._emit_notification(method, params)
            return
        self._handle_settings_updated(params)
        await self._emit("state", raw={"codexMethod": method, AR_EVIDENCE_KEY: params})

    async def _handle_foreign_notification(self, method: str, params: JsonObject) -> None:
        """Cross any other notification as raw vendor evidence, registering well-formed threads.

        A malformed ``threadId`` is never an error on this path -- unrecognized shapes crossed as
        raw evidence before the demux existed and must keep doing so.
        """

        thread_id = params.get("threadId")
        if isinstance(thread_id, str) and thread_id:
            self._threads.resolve(params, context=f"{method} params")
        self._threads.learn_item_thread(params)
        await self._emit_notification(method, params)

    async def _emit_notification(self, method: str, params: JsonObject) -> None:
        """Emit raw vendor evidence for one notification, routing deltas by item.

        Delta frames (``item/.../delta``, ``patchUpdated``) can lack a usable ``threadId``;
        the item→thread index learned from item/started and item/completed supplies the
        demux key. A delta for an unknown item crosses unmodified — the bridge degrades a
        missing threadId to the parent/None path, and no thread is ever invented here.
        """

        await self._emit(
            "codex-notification",
            raw={
                "codexMethod": method,
                AR_EVIDENCE_METHOD_KEY: method,
                AR_EVIDENCE_KEY: self._threads.route_delta_params(method, params),
            },
        )

    async def _handle_turn_completed(self, params: JsonObject) -> None:
        state = self._threads.resolve(params, context="turn/completed params")
        turn = parse_turn(params, context="turn/completed params")
        turn_id = required_text(turn, "id", context="turn/completed turn")
        if turn_id in state.completed_turns:
            return
        if not state.is_parent:
            # Agent turns carry no operation (turn/start writes are parent-only); the
            # completion is per-thread bookkeeping plus raw evidence, never settlement.
            if state.active_turn_id == turn_id:
                state.active_turn_id = None
            state.status = completed_turn_status(turn.get("status"))
            self._threads.retire_turn(state, turn_id, None)
            self._publish_agent_registry()
            await self._emit_notification("turn/completed", params)
            return
        operation = state.turn_operations.get(turn_id)
        if operation is None:
            # Codex can deliver the notification before the turn/start response binds its turn id
            # to the pending operation. Keep only that one exact raw completion until binding.
            if self._pending_operation is None or state.unbound_completions:
                raise CodexAppServerError("turn/completed has no bindable pending operation")
            state.unbound_completions[turn_id] = params
            return
        if state.active_turn_id is not None and turn_id != state.active_turn_id:
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
        state.active_turn_id = None
        self._active_operation = None
        self._threads.retire_turn(state, turn_id, operation)
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
        state = self._threads.resolve(params, context="item/completed params")
        turn_id = required_text(params, "turnId", context="item/completed params")
        item = required_object(params.get("item"), context="item/completed params.item")
        self._threads.learn_item_thread(params)
        if self._threads.learn_collab_identity(item):
            self._publish_agent_registry()
        self._transcript_sequence += 1
        transcript = transcript_from_item(
            item,
            sequence=self._transcript_sequence,
            created_at=iso_from_millis(params.get("completedAtMs"), fallback=self._clock()),
            turn_id=turn_id,
        )
        if transcript is None:
            self._transcript_sequence -= 1
            await self._emit_notification("item/completed", params)
            return
        if not state.is_parent:
            # Downstream conversation demux keys on the native thread id.
            # Deliberate asymmetry: AGENT entries carry
            # ``threadId`` here; PARENT entries deliberately carry none — the pre-multiplex
            # parent transcript shape stays byte-identical, and the demux reads a
            # missing threadId as the parent.
            transcript = replace(transcript, raw={**transcript.raw, "threadId": state.thread_id})
        await self._emit(
            "transcript",
            transcript=(transcript,),
            raw={"codexMethod": "item/completed", "turnId": turn_id, AR_EVIDENCE_KEY: params},
        )

    async def _handle_server_request(self, message: JsonObject) -> None:
        request_id = message.get("id")
        try:
            interaction = parse_server_interaction(message, created_at=self._clock())
        except CodexAppServerError as exc:
            # Decide by METHOD first. An unknown/experimental server request METHOD is
            # vendor traffic, never a bridge failure: answer it with decline semantics
            # (the vendor maps an error response to decline) and cross it as degraded
            # evidence, on ANY thread. A KNOWN stable method's malformed shape
            # (rpc-id type, non-object params) is a protocol violation, not traffic:
            # no decline-and-degrade — the loop's agent-degrade/parent-fail split
            # applies unchanged.
            method = message.get("method")
            if isinstance(method, str) and method in STABLE_SERVER_REQUESTS:
                raise
            if isinstance(request_id, (str, int)) and not isinstance(request_id, bool):
                await self._require_transport().respond_error(
                    request_id,
                    code=-32601,
                    message=str(exc),
                )
            await self._degrade_agent_frame(message, exc, force=True)
            return
        try:
            state = self._threads.resolve(
                required_object(message.get("params"), context="server request"),
                context="Codex server request",
            )
        except CodexAppServerError as exc:
            # Malformed thread identity: decline the request when it is answerable,
            # then the loop's normal agent-degrade/parent-fail split applies.
            if isinstance(request_id, (str, int)) and not isinstance(request_id, bool):
                await self._require_transport().respond_error(
                    request_id,
                    code=-32601,
                    message=str(exc),
                )
            raise
        if len(state.pending_interactions) >= PENDING_INTERACTIONS_PER_THREAD:
            # Bounded map: the NEW request is declined + degraded, never a bridge
            # failure and never a silent loss of an older unanswered one.
            await self._require_transport().respond_error(
                interaction.rpc_id,
                code=-32601,
                message="pending interaction map is full",
            )
            await self._emit(
                "codex-notification",
                raw={
                    "codexMethod": interaction.method,
                    AR_EVIDENCE_METHOD_KEY: interaction.method,
                    AR_EVIDENCE_KEY: required_object(
                        message.get("params"), context="server request"
                    ),
                    "degraded": "pending interaction map is full; the request was declined",
                },
            )
            return
        # Concurrent pendings are normal vendor traffic (the codex TUI keeps one
        # app-global pending map keyed by approval id) — register, never raise.
        # Vendor rpc-id REUSE overwrites the older pending, which then becomes
        # honestly unanswerable later — a JSON-RPC violation the vendor owns.
        state.pending_interactions[interaction.rpc_id] = interaction
        if state.is_parent:
            self._snapshot = replace(
                await self.snapshot(),
                activity="blocked",
                acceptance="queued",
            )
        # A sub-agent approval never moves the parent-scoped activity; it lands in
        # the multiplexed pending_interactions tuple only.
        self._sync_pending_snapshot()
        await self._emit("state", raw={"codexMethod": interaction.method})

    async def _handle_server_request_resolved(self, params: JsonObject) -> None:
        state = self._threads.resolve(params, context="serverRequest/resolved params")
        request_id = params.get("requestId")
        cleared = (
            state.pending_interactions.pop(request_id, None)
            if isinstance(request_id, (str, int)) and not isinstance(request_id, bool)
            else None
        )
        if cleared is not None and state.is_parent:
            self._snapshot = replace(
                await self.snapshot(),
                activity="settling",
                acceptance="queued",
            )
        if cleared is not None:
            self._sync_pending_snapshot()
        await self._emit("state", raw={"codexMethod": "serverRequest/resolved"})

    def _sync_pending_snapshot(self) -> None:
        """Rebuild the multiplexed pending tuple; the singular slot stays the parent's oldest."""

        if self._snapshot is None:
            return
        parent = self._threads.parent()
        self._snapshot = replace(
            self._snapshot,
            pending_interaction=parent.pending_interaction.pending
            if parent.pending_interaction is not None
            else None,
            pending_interactions=self._threads.pending_interactions(),
        )

    def _handle_settings_updated(self, params: JsonObject) -> None:
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
        self._native_history.reset_probe()
        self._snapshot = replace(
            snapshot,
            acceptance=self._busy_acceptance(snapshot.activity, snapshot.acceptance),
        )
        self._publish_agent_registry()
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
        self._events.offer(event)

    def _load_shed_notice(self, count: int) -> AdapterEvent | None:
        """Mint the one notice accounting for ``count`` shed events, sequenced like any other.

        Answers ``None`` before the first snapshot exists: there is nothing to sequence the
        notice against yet, and the queue keeps owing the count until there is.
        """

        if self._snapshot is None:
            return None
        launch = self._session.launch
        assert launch is not None
        self._event_sequence += 1
        self._snapshot = replace(self._snapshot, last_event_sequence=self._event_sequence)
        return AdapterEvent(
            sequence=self._event_sequence,
            kind="codex-notification",
            identity=launch.identity,
            created_at=self._clock(),
            raw={
                "codexMethod": "ar/load-shed",
                AR_EVIDENCE_METHOD_KEY: "ar/load-shed",
                AR_EVIDENCE_KEY: {
                    "droppedEvents": count,
                    "reason": "the consumer fell behind; the oldest delta events were shed to keep the bridge live",
                },
                "degraded": f"{count} evidence events shed under load",
            },
        )

    def _busy_acceptance(self, activity: str, fallback: str) -> str:
        if activity == "running":
            return "immediate"
        return fallback

    def _guard_turn_start(self, operation: ControlOperationRef) -> None:
        """Final synchronous guard executed under the transport write lock."""

        parent = self._threads.parent()
        if (
            self._pending_operation != operation
            or parent.active_turn_id is not None
            or self._active_operation is not None
            or parent.pending_interaction is not None
        ):
            raise HarnessAdapterBusyError("Codex became busy before the guarded turn/start write")

    # Parent-thread views kept for the white-box correlation tests.
    @property
    def _active_turn_id(self) -> str | None:
        return self._threads.parent().active_turn_id

    @property
    def _turn_operations(self) -> dict[str, ControlOperationRef]:
        return self._threads.parent().turn_operations

    @property
    def _unbound_completions(self) -> dict[str, JsonObject]:
        return self._threads.parent().unbound_completions

    @property
    def _completed_turns(self) -> OrderedDict[str, ControlOperationRef | None]:
        return self._threads.parent().completed_turns

    def _publish_agent_registry(self) -> None:
        """Publish the bounded agent registry into snapshot.raw for the serving projector."""

        if self._snapshot is None:
            return
        self._snapshot = replace(
            self._snapshot,
            raw={**self._snapshot.raw, "agentRegistry": self._threads.agent_registry()},
        )

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
