"""Capability-negotiated Codex app-server adapter for the L1 harness contract."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field, replace
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
    RequestId,
)
from agents_remember.serving.codex_app_server_session import (
    CodexAppServerSession,
    CodexAppServerSettings,
    TransportFactory,
)
from agents_remember.serving.codex_app_server_state import (
    STABLE_SERVER_REQUESTS,
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
    AR_EVIDENCE_METHOD_KEY,
    CONTROL_PROTOCOL_VERSION,
    REQUIRED_ADAPTER_CAPABILITIES,
    AdapterEvent,
    AdapterHandshake,
    AdapterSnapshot,
    AssetReference,
    ControlOperationRef,
    InteractionResponse,
    InterruptResult,
    LaunchSpec,
    NativeEvidencePage,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
    SubmissionReceipt,
    TranscriptEntry,
    read_asset_bytes,
    window_native_evidence_page,
)

Clock = Callable[[], str]

THREAD_REGISTRY_LIMIT = 64
ITEM_THREAD_INDEX_LIMIT = 1024
PENDING_INTERACTIONS_PER_THREAD = 16
ADAPTER_EVENT_QUEUE_LIMIT = 1024
_LOAD_SHED_DELTA_METHODS = frozenset(
    {
        "item/agentMessage/delta",
        "item/plan/delta",
        "item/reasoning/summaryTextDelta",
        "item/reasoning/summaryPartAdded",
        "item/reasoning/textDelta",
        "item/commandExecution/outputDelta",
        "item/fileChange/patchUpdated",
    }
)


@dataclass
class _ThreadState:
    """Per-thread demux state on one multiplexed app-server connection.

    The codex app-server auto-attaches sub-agent thread listeners to the seat's
    connection, so turn/item/approval traffic for many threads arrives interleaved.
    The parent thread is the session thread (turn writes stay parent-only); any other
    threadId is auto-registered from traffic with status ``unresolved`` until
    parent-thread collab evidence (collabAgentToolCall/subAgentActivity) binds its
    identity. Agent turns never carry a ControlOperationRef: turn/start writes are
    parent-only, so agent completions record ``None`` as the operation.
    """

    thread_id: str
    is_parent: bool
    status: str
    agent_path: str | None = None
    active_turn_id: str | None = None
    turn_operations: dict[str, ControlOperationRef] = field(default_factory=dict)
    completed_turns: OrderedDict[str, ControlOperationRef | None] = field(
        default_factory=OrderedDict
    )
    unbound_completions: dict[str, JsonObject] = field(default_factory=dict)
    pending_interactions: OrderedDict[RequestId, CodexServerInteraction] = field(
        default_factory=OrderedDict
    )
    """Concurrent server->client requests keyed by rpc id, exactly how the vendor
    tracks them (the codex TUI keeps one app-global pending map keyed by approval
    id): a second request while another is pending is normal traffic, never an
    error."""

    @property
    def pending_interaction(self) -> CodexServerInteraction | None:
        """The thread's oldest pending interaction (the pre-multiplex singular view)."""

        return next(iter(self.pending_interactions.values()), None)


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
        self._events: asyncio.Queue[AdapterEvent | None] = asyncio.Queue(
            maxsize=ADAPTER_EVENT_QUEUE_LIMIT
        )
        self._dropped_events = 0
        self._processor: asyncio.Task[None] | None = None
        self._stopped = False
        self._threads: OrderedDict[str, _ThreadState] = OrderedDict()
        self._item_threads: OrderedDict[str, str] = OrderedDict()
        self._submissions = CodexSubmissionLedger(settings.submission_limit)
        self._fresh_turn_required = False
        self._pending_operation: ControlOperationRef | None = None
        self._active_operation: ControlOperationRef | None = None
        self._completed_turn_limit = settings.submission_limit
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
        self._parent_state()
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
            # Consumer-side catch-up: after a flood the producer may go silent,
            # so the shed accounting cannot wait for the next put — mint the notice
            # as soon as the drained queue has room. Same monotonic sequence path,
            # and dropped==0 makes it a no-op, so the notice itself never recurses.
            self._emit_load_shed_notice_if_caught_up()

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        return self._event_stream()

    async def preflight_operation(self, operation: ControlOperationRef) -> None:
        self._require_ready()
        parent = self._parent_state()
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
        parent = self._parent_state()
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
                self._verified_asset_path(asset)
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
        active = self._parent_state().active_turn_id
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
        found = self._interaction_thread(response.interaction_id)
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
        result = interaction_result(pending.method, response.response)
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
        """Page the live thread through ``thread/read``; the native read stays the authority.

        ``thread_id`` selects the thread to read: ``None`` reads the
        parent/session thread exactly as before, an explicit id reads that (sub-agent)
        thread — the app-server serves ``thread/read`` for every multiplexed thread.
        """

        if (await self.snapshot()).control == "disconnected":
            await self._reconnect()
        requested_thread_id = thread_id if thread_id is not None else self._require_thread_id()
        transport = self._require_transport()
        result = await transport.request(
            "thread/read",
            {"threadId": requested_thread_id, "includeTurns": True},
        )
        thread = required_object(result.get("thread"), context="thread/read response.thread")
        returned_thread_id = required_text(thread, "id", context="thread/read response.thread")
        if returned_thread_id != requested_thread_id:
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
            "input": self._turn_input(evidence.request),
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
        parent = self._parent_state()
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
        status = required_text(turn, "status", context="turn/start response.turn")
        if status in {"failed", "interrupted"}:
            evidence.state = "rejected"
            self._remember_completed_turn(parent, turn_id, operation)
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
            parent.active_turn_id = turn_id
            self._active_operation = operation
            await self._set_activity("running", turn_id=turn_id)
        completion_emitted = False
        if buffered is not None:
            await self._handle_turn_completed(buffered)
            completion_emitted = True
        terminal_completion = status != "inProgress" and not completion_emitted
        if terminal_completion:
            self._remember_completed_turn(parent, turn_id, operation)
        raw: JsonObject = {
            "method": "turn/start",
            "clientUserMessageId": evidence.request.request_id,
            "terminalCompletion": terminal_completion,
        }
        if evidence.request.assets:
            raw["assetIds"] = [asset.asset_id for asset in evidence.request.assets]
        return SubmissionReceipt(
            request_id=evidence.request.request_id,
            acceptance="immediate",
            submitted_at=evidence.request.submitted_at,
            vendor_correlation_id=turn_id,
            accepted_at=self._clock(),
            raw=raw,
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
            state = self._thread_for(params, context="thread/status/changed params")
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
            # A sub-agent thread's status never moves the parent-scoped activity;
            # it is registry + raw evidence only.
            state.status = required_text(
                required_object(params.get("status"), context="thread/status/changed status"),
                "type",
                context="thread/status/changed status",
            )
            await self._emit_notification(method, params)
            return
        if method == "turn/started":
            state = self._thread_for(params, context="turn/started params")
            turn = parse_turn(params, context="turn/started params")
            turn_id = required_text(turn, "id", context="turn/started turn")
            state.active_turn_id = turn_id
            if state.is_parent:
                await self._set_activity("running", turn_id=turn_id)
                return
            await self._emit_notification(method, params)
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
            state = self._thread_for(params, context="thread/settings/updated params")
            if state.is_parent:
                self._handle_settings_updated(params)
                await self._emit("state", raw={"codexMethod": method, AR_EVIDENCE_KEY: params})
                return
            await self._emit_notification(method, params)
            return
        # Generic vendor evidence: auto-register a well-formed foreign thread without
        # ever failing on malformed threadIds — those cross as raw evidence exactly as
        # they did before the demux existed.
        thread_id = params.get("threadId")
        if isinstance(thread_id, str) and thread_id:
            self._thread_for(params, context=f"{method} params")
        self._learn_item_thread(params)
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
                AR_EVIDENCE_KEY: self._route_delta_params(method, params),
            },
        )

    async def _handle_turn_completed(self, params: JsonObject) -> None:
        state = self._thread_for(params, context="turn/completed params")
        turn = parse_turn(params, context="turn/completed params")
        turn_id = required_text(turn, "id", context="turn/completed turn")
        if turn_id in state.completed_turns:
            return
        if not state.is_parent:
            # Agent turns carry no operation (turn/start writes are parent-only); the
            # completion is per-thread bookkeeping plus raw evidence, never settlement.
            if state.active_turn_id == turn_id:
                state.active_turn_id = None
            self._remember_completed_turn(state, turn_id, None)
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
        self._remember_completed_turn(state, turn_id, operation)
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
        state = self._thread_for(params, context="item/completed params")
        turn_id = required_text(params, "turnId", context="item/completed params")
        item = required_object(params.get("item"), context="item/completed params.item")
        self._learn_item_thread(params)
        self._learn_collab_identity(item)
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
            state = self._thread_for(
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
        state = self._thread_for(params, context="serverRequest/resolved params")
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

    def _interaction_thread(self, interaction_id: str) -> tuple[_ThreadState, RequestId] | None:
        """The (thread, rpc id) owning ``interaction_id`` (answers route by request id)."""

        for state in self._threads.values():
            for rpc_id, pending in state.pending_interactions.items():
                if pending.pending.interaction_id == interaction_id:
                    return state, rpc_id
        return None

    def _sync_pending_snapshot(self) -> None:
        """Rebuild the multiplexed pending tuple; the singular slot stays the parent's oldest."""

        if self._snapshot is None:
            return
        parent = self._parent_state()
        pendings = []
        for state in self._threads.values():
            for pending in state.pending_interactions.values():
                raw = dict(pending.pending.raw)
                if not state.is_parent:
                    raw["agentLabel"] = self._agent_label(state)
                pendings.append(replace(pending.pending, raw=raw))
        self._snapshot = replace(
            self._snapshot,
            pending_interaction=parent.pending_interaction.pending
            if parent.pending_interaction is not None
            else None,
            pending_interactions=tuple(pendings),
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
        self._enqueue(event)

    def _enqueue(self, event: AdapterEvent | None) -> None:
        """Bounded queue with honest load-shedding: never raises, never silently drops.

        A multiplexed seat's delta floods can outpace the consumer (the 2026-07-26
        seat death: the queue-full raise killed the bridge, and content vanished
        before it). On a full queue the oldest HIGH-VOLUME delta event sheds first
        (structural events — turns, completions, interactions, failures, the close
        sentinel — shed only when nothing else remains), every shed is counted, and
        one load-shed notice crosses when the consumer catches up, so the projection
        surfaces the loss instead of hiding it.
        """

        if event is None:
            # The close sentinel terminates subscribers: the shed accounting must
            # cross BEFORE it, never behind it — make room for notice + sentinel,
            # mint, then offer. Every eviction lands in the notice's count.
            while self._events.qsize() > self._events.maxsize - 2:
                self._evict_for_space()
            self._emit_load_shed_notice_if_caught_up()
        while self._events.full():
            self._evict_for_space()
        self._events.put_nowait(event)
        self._emit_load_shed_notice_if_caught_up()

    def _evict_for_space(self) -> None:
        held: list[AdapterEvent | None] = []
        while not self._events.empty():
            held.append(self._events.get_nowait())
        index = next(
            (
                position
                for position, candidate in enumerate(held)
                if candidate is not None
                and candidate.raw.get("codexMethod") in _LOAD_SHED_DELTA_METHODS
            ),
            0,  # nothing sheddable: the oldest event overall
        )
        evicted = held.pop(index)
        for candidate in held:
            self._events.put_nowait(candidate)
        if evicted is not None:
            self._dropped_events += 1

    def _emit_load_shed_notice_if_caught_up(self) -> None:
        if self._dropped_events == 0 or self._events.full() or self._snapshot is None:
            return
        count, self._dropped_events = self._dropped_events, 0
        launch = self._session.launch
        assert launch is not None
        self._event_sequence += 1
        self._snapshot = replace(self._snapshot, last_event_sequence=self._event_sequence)
        self._events.put_nowait(
            AdapterEvent(
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
        )

    def _turn_input(self, request: PromptRequest) -> list[JsonObject]:
        """Build the turn input blocks; verified local images ride as native paths."""

        blocks: list[JsonObject] = [{"type": "text", "text": request.text}]
        for asset in request.assets:
            blocks.append({"type": "localImage", "path": self._verified_asset_path(asset)})
        return blocks

    def _verified_asset_path(self, asset: AssetReference) -> str:
        """Re-verify the staged file at construction before the native process sees its path."""

        if asset.spool_path is None:
            raise CodexAppServerError("Codex asset submission requires a verified spool path")
        digest, size, _data = read_asset_bytes(asset.spool_path)
        if size != asset.byte_size or digest != asset.sha256:
            raise CodexAppServerError(
                f"Codex asset {asset.asset_id!r} failed verification at construction"
            )
        return str(asset.spool_path)

    def _busy_acceptance(self, activity: str, fallback: str) -> str:
        if activity == "running":
            return "immediate"
        return fallback

    def _guard_turn_start(self, operation: ControlOperationRef) -> None:
        """Final synchronous guard executed under the transport write lock."""

        parent = self._parent_state()
        if (
            self._pending_operation != operation
            or parent.active_turn_id is not None
            or self._active_operation is not None
            or parent.pending_interaction is not None
        ):
            raise HarnessAdapterBusyError("Codex became busy before the guarded turn/start write")

    def _remember_completed_turn(
        self,
        state: _ThreadState,
        turn_id: str,
        operation: ControlOperationRef | None,
    ) -> None:
        """Release live correlation and retain only the bounded terminal duplicate window."""

        state.turn_operations.pop(turn_id, None)
        state.completed_turns[turn_id] = operation
        state.completed_turns.move_to_end(turn_id)
        while len(state.completed_turns) > self._completed_turn_limit:
            state.completed_turns.popitem(last=False)

    def _parent_state(self) -> _ThreadState:
        """The session/parent thread's demux state, registered on first use."""

        thread_id = self._require_thread_id()
        state = self._threads.get(thread_id)
        if state is None:
            state = _ThreadState(thread_id=thread_id, is_parent=True, status="active")
            self._threads[thread_id] = state
        elif not state.is_parent:
            state.is_parent = True
        return state

    # Parent-thread views kept for the white-box correlation tests.
    @property
    def _active_turn_id(self) -> str | None:
        return self._parent_state().active_turn_id

    @property
    def _turn_operations(self) -> dict[str, ControlOperationRef]:
        return self._parent_state().turn_operations

    @property
    def _unbound_completions(self) -> dict[str, JsonObject]:
        return self._parent_state().unbound_completions

    @property
    def _completed_turns(self) -> OrderedDict[str, ControlOperationRef | None]:
        return self._parent_state().completed_turns

    def _thread_for(self, params: Mapping[str, object], *, context: str) -> _ThreadState:
        """Demux one notification to its thread state.

        A missing/non-text ``threadId`` is a shape error and fails closed exactly as the
        old ``_validate_thread`` did; a well-formed foreign threadId is never an error —
        it auto-registers as an ``unresolved`` agent thread until collab evidence binds
        its identity.
        """

        thread_id = required_text(params, "threadId", context=context)
        state = self._threads.get(thread_id)
        if state is not None:
            self._threads.move_to_end(thread_id)
            return state
        if thread_id == self._session.thread_id:
            return self._parent_state()
        if len(self._threads) >= THREAD_REGISTRY_LIMIT:
            # Evict the oldest settled/unresolved agent thread first: an actively-turning agent or one holding a pending approval is
            # never evicted. When nothing is evictable this raises, and the message loop
            # degrades the frame to raw evidence instead of failing the bridge.
            evictable = next(
                (
                    key
                    for key, entry in self._threads.items()
                    if not entry.is_parent
                    and entry.pending_interaction is None
                    and entry.active_turn_id is None
                ),
                None,
            )
            if evictable is None:
                raise CodexAppServerError("Codex thread registry is full")
            del self._threads[evictable]
        state = _ThreadState(thread_id=thread_id, is_parent=False, status="unresolved")
        self._threads[thread_id] = state
        self._publish_agent_registry()
        return state

    def _agent_label(self, state: _ThreadState) -> str:
        """The bound identity evidence for one agent thread, or the fallback label."""

        if state.agent_path is not None:
            return state.agent_path
        return f"agent {state.thread_id[:8]}"

    def _learn_item_thread(self, params: Mapping[str, object]) -> None:
        """Learn the item→thread demux index from item traffic; malformed shapes are skipped."""

        thread_id = params.get("threadId")
        item = params.get("item")
        if not isinstance(thread_id, str) or not thread_id or not isinstance(item, Mapping):
            return
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            return
        self._item_threads[item_id] = thread_id
        self._item_threads.move_to_end(item_id)
        while len(self._item_threads) > ITEM_THREAD_INDEX_LIMIT:
            self._item_threads.popitem(last=False)

    def _route_delta_params(self, method: str, params: JsonObject) -> JsonObject:
        """Bind a delta frame's thread from the item index when the frame itself lacks it."""

        thread_id = params.get("threadId")
        if isinstance(thread_id, str) and thread_id:
            return params
        if "/delta" not in method and not method.endswith("patchUpdated"):
            return params
        item_id = params.get("itemId")
        if not isinstance(item_id, str):
            return params
        bound = self._item_threads.get(item_id)
        if bound is None:
            return params
        return {**params, "threadId": bound}

    def _learn_collab_identity(self, item: Mapping[str, object]) -> None:
        """Bind agent identity from collab items.

        Parent-thread items model the collaboration itself: ``collabAgentToolCall``
        carries ``receiverThreadIds``/``agentsStates`` and ``subAgentActivity`` carries
        ``agentThreadId`` + ``agentPath``. Well-formed entries bind the registry;
        anything else is left as raw evidence for the projector.
        """

        item_type = item.get("type")
        learned = False
        if item_type == "subAgentActivity":
            agent_thread_id = item.get("agentThreadId")
            if isinstance(agent_thread_id, str) and agent_thread_id:
                state = self._threads.get(agent_thread_id) or self._thread_for(
                    {"threadId": agent_thread_id}, context="subAgentActivity item"
                )
                agent_path = item.get("agentPath")
                if isinstance(agent_path, str) and agent_path:
                    state.agent_path = agent_path
                kind = item.get("kind")
                if isinstance(kind, str) and kind:
                    state.status = kind
                learned = True
        elif item_type == "collabAgentToolCall":
            receivers = item.get("receiverThreadIds")
            if isinstance(receivers, list):
                for receiver in receivers:
                    if isinstance(receiver, str) and receiver:
                        self._thread_for({"threadId": receiver}, context="collabAgentToolCall item")
                        learned = True
            agents_states = item.get("agentsStates")
            if isinstance(agents_states, Mapping):
                for agent_thread_id, agent_state in agents_states.items():
                    state = self._threads.get(agent_thread_id)
                    if state is None or not isinstance(agent_state, Mapping):
                        continue
                    status = agent_state.get("status")
                    if isinstance(status, str) and status:
                        state.status = status
                        learned = True
        if learned:
            self._publish_agent_registry()

    def _publish_agent_registry(self) -> None:
        """Publish the bounded agent registry into snapshot.raw for the serving projector."""

        if self._snapshot is None:
            return
        registry: JsonObject = {}
        for thread_id, state in self._threads.items():
            if state.is_parent:
                continue
            entry: JsonObject = {"status": state.status}
            if state.agent_path is not None:
                entry["agentPath"] = state.agent_path
            registry[thread_id] = entry
        self._snapshot = replace(
            self._snapshot,
            raw={**self._snapshot.raw, "agentRegistry": registry},
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

    def _offer_event(self, event: AdapterEvent | None) -> None:
        self._enqueue(event)
