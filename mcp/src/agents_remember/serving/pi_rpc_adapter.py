"""Protocol-backed Pi RPC adapter atop the normalized L1 harness bridge."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from agents_remember.errors import (
    HarnessAdapterBusyError,
    HarnessAdapterDisconnectedError,
    HarnessControlError,
)
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    LaunchKnobs,
    ModelCapability,
    SetResult,
)
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    REQUIRED_ADAPTER_CAPABILITIES,
    AdapterEvent,
    AdapterHandshake,
    AdapterSnapshot,
    ControlOperationRef,
    InteractionResponse,
    LaunchSpec,
    NativeEvidenceFrame,
    NativeEvidencePage,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
    SubmissionReceipt,
    window_native_evidence_page,
)
from agents_remember.serving.harness_launch import ResolvedLaunch, verify_effective_launch
from agents_remember.serving.pi_rpc_configuration import (
    DEFAULT_PI_MUTATION_TIMEOUT_SECONDS,
    PiRpcConfiguration,
)
from agents_remember.serving.pi_rpc_events import PiRpcEventMapper
from agents_remember.serving.pi_rpc_process import PiRpcSubprocess, PiRpcTransport
from agents_remember.serving.pi_rpc_protocol import (
    PI_RPC_PROTOCOL,
    PiEntries,
    PiSessionState,
    has_no_session,
    parse_pi_entries,
    parse_pi_models,
    parse_pi_response,
    parse_pi_state,
    pi_entry_created_at,
    pi_entry_identity,
    pi_entry_user_text,
    pi_response_error,
    pi_rpc_launch,
    pi_rpc_resume_launch,
    require_pi_success,
)

Clock = Callable[[], str]
TransportFactory = Callable[[], PiRpcTransport]
SubmissionKnowledge = Literal["pending", "accepted", "rejected", "unknown"]


@dataclass(frozen=True)
class _SubmissionEvidence:
    request: PromptRequest
    cursor_before: str | None
    state: SubmissionKnowledge


class PiRpcAdapter:
    """Own a Pi RPC subprocess and translate only structurally validated protocol semantics."""

    def __init__(
        self,
        *,
        transport_factory: TransportFactory = PiRpcSubprocess,
        submission_limit: int = 256,
        interaction_limit: int = 64,
        configuration_timeout_seconds: float = DEFAULT_PI_MUTATION_TIMEOUT_SECONDS,
        clock: Clock = lambda: datetime.now(UTC).isoformat(),
        expected_launch: ResolvedLaunch | None = None,
    ) -> None:
        if submission_limit < 1 or interaction_limit < 1:
            raise HarnessControlError("Pi RPC adapter limits must be positive")
        self._transport_factory = transport_factory
        self._submission_limit = submission_limit
        self._interaction_limit = interaction_limit
        self._clock = clock
        self._expected_launch = expected_launch
        self._transport: PiRpcTransport | None = None
        self._launch: LaunchSpec | None = None
        self._state: PiSessionState | None = None
        self._capabilities: CapabilitySnapshot | None = None
        self._events: PiRpcEventMapper | None = None
        self._cursor: str | None = None
        self._submissions: OrderedDict[str, _SubmissionEvidence] = OrderedDict()
        self._request_sequence = 0
        self._transport_generation = 0
        self._activity_token = 0
        self._prepared_operation: tuple[ControlOperationRef, int, int, int] | None = None
        self._active_operation: ControlOperationRef | None = None
        self._transport_changed = asyncio.Event()
        self._stopped = False
        self._configuration = PiRpcConfiguration(
            transport=self._require_transport,
            read_state=self._read_configuration_state,
            read_capabilities=self._read_available_models,
            capabilities=self.advertise,
            commit=self._commit_configuration,
            request_id=self._internal_id,
            timeout_seconds=configuration_timeout_seconds,
        )

    async def start(self, launch: LaunchSpec) -> AdapterHandshake:
        if self._transport is not None:
            raise HarnessControlError("Pi RPC adapter is already started")
        rpc_launch = pi_rpc_launch(launch)
        transport = self._transport_factory()
        transport_started = False
        ready = False
        try:
            await transport.start(rpc_launch)
            transport_started = True
            self._transport = transport
            self._launch = rpc_launch
            self._events = PiRpcEventMapper(
                launch.identity,
                interaction_limit=self._interaction_limit,
                clock=self._clock,
            )
            state = await self._read_state()
            self._capabilities = await self._read_available_models(state)
            if self._expected_launch is not None:
                verify_effective_launch(
                    self._expected_launch,
                    self._capabilities,
                    require_effort_echo=True,
                )
            entries = await self._read_entries()
            self._cursor = entries.leaf_id
            snapshot = self._events.apply_state(state, cursor=self._cursor)
            if self._expected_launch is not None:
                snapshot = replace(
                    snapshot,
                    raw={
                        **snapshot.raw,
                        "requestedLaunchModel": self._expected_launch.model_key,
                        "requestedLaunchEffort": self._expected_launch.effort,
                        "launchAcceptance": "echo-verified",
                    },
                )
            ready = True
            return AdapterHandshake(
                protocol_version=CONTROL_PROTOCOL_VERSION,
                adapter_id="pi-rpc",
                identity=launch.identity,
                capabilities=REQUIRED_ADAPTER_CAPABILITIES,
                snapshot=snapshot,
                raw={
                    "vendorProtocol": PI_RPC_PROTOCOL,
                    "entryCursor": self._cursor,
                },
            )
        finally:
            if not ready:
                if transport_started:
                    await transport.stop("forced")
                self._transport = None
                self._launch = None
                self._state = None
                self._capabilities = None
                self._events = None
                self._cursor = None

    async def snapshot(self) -> AdapterSnapshot:
        self._require_started()
        state = await self._read_state()
        return self._require_event_mapper().apply_state(state, cursor=self._cursor)

    async def discover(self, launch: LaunchSpec) -> CapabilitySnapshot:
        rpc_launch = pi_rpc_launch(launch)
        transport = self._transport_factory()
        transport_started = False
        try:
            await transport.start(rpc_launch)
            transport_started = True
            state = await self._request_state(transport)
            return await self._request_available_models(transport, state)
        finally:
            if transport_started:
                await transport.stop("forced")

    def advertise(self) -> CapabilitySnapshot:
        self._require_started()
        capabilities = self._capabilities
        state = self._state
        if capabilities is None or state is None:
            raise HarnessControlError("Pi RPC model catalog is not available")
        return self._current_capabilities(capabilities.models, state)

    def launch_knobs(self, *, model_key: str, effort: str | None) -> LaunchKnobs:
        """Use Pi 0.80.7's exact native model/thinking flags; RPC mode stays protocol-owned."""

        if not model_key or model_key != model_key.strip():
            raise HarnessControlError("Pi launch model must be non-empty with no outer whitespace")
        if effort is None or not effort or effort != effort.strip():
            raise HarnessControlError("Pi launch effort must be non-empty with no outer whitespace")
        return LaunchKnobs(
            argv=("--model", model_key, "--thinking", effort),
            owned_argv_options=("--model", "--thinking"),
        )

    async def set_model(
        self, model_key: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        self._require_started()
        operation = self._require_operation(operation, "set-model")
        guard = self._claim_prepared_operation(operation)
        try:
            return await self._configuration.set_model(model_key, before_write=guard)
        finally:
            self._clear_operation(operation)

    async def set_effort(
        self, effort: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        self._require_started()
        operation = self._require_operation(operation, "set-effort")
        guard = self._claim_prepared_operation(operation)
        try:
            return await self._configuration.set_effort(effort, before_write=guard)
        finally:
            self._clear_operation(operation)

    async def preflight_operation(self, operation: ControlOperationRef) -> None:
        """Capture fresh idle evidence while the authority still owns a withdrawable queue row."""

        self._require_started()
        if self._active_operation is not None:
            raise HarnessAdapterBusyError("Pi already has an active ordinary operation")
        state = await self._read_state()
        mapper = self._require_event_mapper()
        mapper.apply_state(state, cursor=self._cursor)
        self._require_idle_state(state)
        if mapper.snapshot.pending_interaction is not None:
            raise HarnessAdapterBusyError("Pi has a pending extension interaction")
        self._prepared_operation = (
            operation,
            self._transport_generation,
            self._activity_token,
            self._require_transport().event_token,
        )

    async def _event_stream(self) -> AsyncIterator[AdapterEvent]:
        self._require_started()
        mapper = self._require_event_mapper()
        while not self._stopped:
            transport = self._require_transport()
            generation = self._transport_generation
            try:
                async for frame in transport.events():
                    self._activity_token += 1
                    completion_operation = (
                        self._active_operation if frame.get("type") == "agent_settled" else None
                    )
                    settled_state = (
                        await self._read_state() if frame.get("type") == "agent_settled" else None
                    )
                    event = mapper.translate(
                        frame,
                        settled_state=settled_state,
                        cursor=self._cursor,
                        operation=completion_operation,
                    )
                    if event.kind == "completed" and completion_operation is not None:
                        self._clear_operation(completion_operation)
                    yield event
            except HarnessAdapterDisconnectedError as exc:
                if generation == self._transport_generation:
                    yield mapper.disconnected(exc)
            except HarnessControlError as exc:
                yield mapper.failed(exc)
                return
            if self._stopped:
                return
            if generation == self._transport_generation:
                self._transport_changed.clear()
                await self._transport_changed.wait()
            if generation != self._transport_generation:
                await self._transport_changed.wait()
                yield mapper.reconnected()

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        return self._event_stream()

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self._require_started()
        operation = self._require_operation(request.operation, "prompt")
        if operation.operation_id != request.request_id:
            raise HarnessControlError("Pi prompt operation id does not match request id")
        if request.request_id in self._submissions:
            raise HarnessControlError(f"duplicate Pi RPC request id: {request.request_id}")
        try:
            entries = await self._read_entries()
        except HarnessAdapterDisconnectedError as exc:
            raise HarnessAdapterDisconnectedError(
                "Pi RPC disconnected before prompt submission",
                may_have_sent=False,
                vendor_correlation_id=request.request_id,
            ) from exc
        self._cursor = entries.leaf_id
        self._remember_submission(
            request.request_id,
            _SubmissionEvidence(request=request, cursor_before=self._cursor, state="pending"),
        )
        command: dict[str, object] = {
            "id": request.request_id,
            "type": "prompt",
            "message": request.text,
        }
        guard = self._claim_prepared_operation(operation)
        try:
            frame = await self._require_transport().request(command, before_write=guard)
        except HarnessAdapterBusyError:
            self._submissions.pop(request.request_id, None)
            self._clear_operation(operation)
            raise
        except HarnessAdapterDisconnectedError as exc:
            knowledge: SubmissionKnowledge = "unknown" if exc.may_have_sent else "rejected"
            self._submissions[request.request_id] = replace(
                self._submissions[request.request_id], state=knowledge
            )
            if not exc.may_have_sent:
                self._submissions.pop(request.request_id, None)
                self._clear_operation(operation)
            raise HarnessAdapterDisconnectedError(
                str(exc),
                may_have_sent=exc.may_have_sent,
                vendor_correlation_id=request.request_id,
            ) from exc
        response = parse_pi_response(frame, request_id=request.request_id, command="prompt")
        if response["success"] is False:
            self._submissions[request.request_id] = replace(
                self._submissions[request.request_id], state="rejected"
            )
            self._clear_operation(operation)
            return SubmissionReceipt(
                request_id=request.request_id,
                acceptance="rejected",
                submitted_at=request.submitted_at,
                vendor_correlation_id=request.request_id,
                detail=pi_response_error(response),
                raw=dict(response),
            )
        self._submissions[request.request_id] = replace(
            self._submissions[request.request_id], state="accepted"
        )
        self._require_event_mapper().mark_prompt_accepted()
        return SubmissionReceipt(
            request_id=request.request_id,
            acceptance="immediate",
            submitted_at=request.submitted_at,
            vendor_correlation_id=request.request_id,
            accepted_at=self._clock(),
            raw=dict(response),
        )

    async def respond(self, response: InteractionResponse) -> None:
        if response.operation is None or response.operation != self._active_operation:
            raise HarnessControlError("Pi interaction response does not match the active operation")
        mapper = self._require_event_mapper()
        payload = mapper.response_payload(response)
        await self._require_transport().send(payload)
        mapper.complete_response(response.interaction_id)

    async def reconcile(self, request_id: str) -> ReconciliationResult:
        evidence = self._submissions.get(request_id)
        if evidence is None:
            raise HarnessControlError(f"unknown Pi RPC request id: {request_id}")
        if evidence.state in {"accepted", "rejected"}:
            reconciled_state: Literal["accepted", "rejected"] = (
                "accepted" if evidence.state == "accepted" else "rejected"
            )
            return ReconciliationResult(
                request_id=request_id,
                state=reconciled_state,
                reconciled_at=self._clock(),
                vendor_correlation_id=request_id,
                detail="correlated Pi prompt response was already observed",
            )
        if evidence.state != "unknown":
            raise HarnessControlError("Pi RPC request is still in flight")
        if not await self._reconnect():
            return self._unresolved(request_id, "Pi session persistence is unavailable")
        try:
            entries = await self._read_entries(since=evidence.cursor_before)
        except HarnessControlError as exc:
            return self._unresolved(request_id, f"Pi entry reconciliation failed: {exc}")
        self._cursor = entries.leaf_id
        accepted = any(
            pi_entry_user_text(entry) == evidence.request.text for entry in entries.entries
        )
        if not accepted:
            return self._unresolved(
                request_id,
                "no exact post-cursor user entry proves whether Pi accepted the prompt",
                entries=entries,
            )
        self._submissions[request_id] = replace(evidence, state="accepted")
        return ReconciliationResult(
            request_id=request_id,
            state="accepted",
            reconciled_at=self._clock(),
            vendor_correlation_id=request_id,
            detail="exact prompt found after the durable Pi entry cursor; no resend performed",
            raw={"entryCursor": entries.leaf_id},
        )

    async def stop(self, mode: ShutdownMode) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._transport_changed.set()
        transport = self._transport
        if transport is not None:
            await transport.stop(mode)
        if self._events is not None:
            self._events.clear()

    @property
    def retained_interaction_count(self) -> int:
        """Bounded pending-dialog count exposed for D2/D3 scaling proof."""

        return self._require_event_mapper().retained_interaction_count

    @property
    def active_operation(self) -> ControlOperationRef | None:
        return self._active_operation

    async def _read_state(self) -> PiSessionState:
        state = await self._request_state(self._require_transport())
        self._state = state
        self._activity_token += 1
        return state

    async def _read_configuration_state(self) -> PiSessionState:
        """Read a candidate state without publishing it before catalog validation."""

        return await self._request_state(self._require_transport())

    def _commit_configuration(
        self,
        state: PiSessionState,
        capabilities: CapabilitySnapshot,
    ) -> None:
        self._state = state
        self._capabilities = capabilities

    async def _request_state(self, transport: PiRpcTransport) -> PiSessionState:
        request_id = self._internal_id("state")
        frame = await transport.request({"id": request_id, "type": "get_state"})
        response = parse_pi_response(frame, request_id=request_id, command="get_state")
        require_pi_success(response, "get_state")
        return parse_pi_state(response)

    async def _read_entries(self, *, since: str | None = None) -> PiEntries:
        request_id = self._internal_id("entries")
        command: dict[str, object] = {"id": request_id, "type": "get_entries"}
        if since is not None:
            command["since"] = since
        frame = await self._require_transport().request(command)
        response = parse_pi_response(frame, request_id=request_id, command="get_entries")
        require_pi_success(response, "get_entries")
        return parse_pi_entries(response)

    async def read_native_page(
        self,
        *,
        cursor: str | None,
        limit: int,
        byte_budget: int,
    ) -> NativeEvidencePage:
        """Page durable entries through ``get_entries``; the native branch stays the authority."""

        self._require_started()
        entries = await self._read_entries(since=cursor)
        frames: list[NativeEvidenceFrame] = []
        seen: set[str] = set()
        for entry in entries.entries:
            entry_id, parent_id, entry_type = pi_entry_identity(entry)
            if entry_id in seen:
                raise HarnessControlError(
                    f"Pi RPC get_entries repeated entry id {entry_id!r}; "
                    "native paging cannot continue without exact identity"
                )
            seen.add(entry_id)
            frames.append(
                NativeEvidenceFrame(
                    native_id=entry_id,
                    native_parent_id=parent_id,
                    native_type=entry_type,
                    created_at=pi_entry_created_at(entry),
                    raw=entry,
                )
            )
        return window_native_evidence_page(
            tuple(frames),
            cursor=None,
            limit=limit,
            byte_budget=byte_budget,
        )

    async def _read_available_models(self, state: PiSessionState) -> CapabilitySnapshot:
        return await self._request_available_models(self._require_transport(), state)

    async def _request_available_models(
        self,
        transport: PiRpcTransport,
        state: PiSessionState,
    ) -> CapabilitySnapshot:
        request_id = self._internal_id("models")
        frame = await transport.request({"id": request_id, "type": "get_available_models"})
        response = parse_pi_response(
            frame,
            request_id=request_id,
            command="get_available_models",
        )
        require_pi_success(response, "get_available_models")
        return self._current_capabilities(parse_pi_models(response), state)

    def _current_capabilities(
        self,
        models: tuple[ModelCapability, ...],
        state: PiSessionState,
    ) -> CapabilitySnapshot:
        if state.model_key is None:
            return CapabilitySnapshot(
                models=models,
                selected_model_key=None,
                selected_effort=None,
            )
        selected = next((model for model in models if model.key == state.model_key), None)
        if selected is None:
            raise HarnessControlError(
                f"Pi current model {state.model_key!r} is absent from get_available_models"
            )
        supported = {option.key for option in selected.effort_options}
        if state.thinking_level not in supported:
            raise HarnessControlError(
                f"Pi current thinking level {state.thinking_level!r} is not advertised for "
                f"{state.model_key!r}"
            )
        return CapabilitySnapshot(
            models=models,
            selected_model_key=state.model_key,
            selected_effort=state.thinking_level,
        )

    async def _reconnect(self) -> bool:
        launch = self._require_launch()
        state = self._state
        if state is None or state.session_file is None or has_no_session(launch.argv):
            return False
        previous_session_id = state.session_id
        old_transport = self._require_transport()
        self._transport_generation += 1
        self._prepared_operation = None
        self._transport_changed.clear()
        await old_transport.stop("forced")
        transport = self._transport_factory()
        resume_launch = pi_rpc_resume_launch(launch, state.session_file)
        try:
            await transport.start(resume_launch)
            self._transport = transport
            self._launch = resume_launch
            resumed = await self._read_state()
            if resumed.session_id != previous_session_id:
                await transport.stop("forced")
                raise HarnessControlError("Pi reconnect resumed a different session identity")
            self._require_event_mapper().apply_state(resumed, cursor=self._cursor)
        finally:
            self._transport_changed.set()
        return True

    def _remember_submission(self, request_id: str, evidence: _SubmissionEvidence) -> None:
        if len(self._submissions) >= self._submission_limit:
            evictable = next(
                (
                    key
                    for key, value in self._submissions.items()
                    if value.state in {"accepted", "rejected"}
                ),
                None,
            )
            if evictable is None:
                raise HarnessControlError("Pi reconciliation ledger is full of ambiguous sends")
            self._submissions.pop(evictable)
        self._submissions[request_id] = evidence

    def _unresolved(
        self, request_id: str, detail: str, *, entries: PiEntries | None = None
    ) -> ReconciliationResult:
        return ReconciliationResult(
            request_id=request_id,
            state="unresolved",
            reconciled_at=self._clock(),
            vendor_correlation_id=request_id,
            detail=detail,
            raw={"entryCursor": entries.leaf_id if entries is not None else self._cursor},
        )

    def _internal_id(self, purpose: str) -> str:
        self._request_sequence += 1
        return f"ar-pi-{purpose}-{self._request_sequence}"

    def _claim_prepared_operation(self, operation: ControlOperationRef) -> Callable[[], None]:
        prepared = self._prepared_operation
        if prepared is None or prepared[0] != operation:
            raise HarnessAdapterBusyError("Pi operation lacks matching fresh idle preflight")
        if self._active_operation is not None:
            raise HarnessAdapterBusyError("Pi already has an active ordinary operation")
        self._active_operation = operation
        generation, activity_token, event_token = prepared[1], prepared[2], prepared[3]

        def guard() -> None:
            if self._active_operation != operation or self._prepared_operation != prepared:
                raise HarnessAdapterBusyError("Pi operation changed before its guarded write")
            if self._transport_generation != generation:
                raise HarnessAdapterBusyError("Pi transport generation changed before prompt write")
            if self._activity_token != activity_token:
                raise HarnessAdapterBusyError("Pi activity changed after fresh idle preflight")
            if self._require_transport().event_token != event_token:
                raise HarnessAdapterBusyError("Pi received an event after fresh idle preflight")
            state = self._state
            if state is None:
                raise HarnessAdapterBusyError("Pi has no cached state at guarded write")
            self._require_idle_state(state)
            snapshot = self._require_event_mapper().snapshot
            if snapshot.pending_interaction is not None or snapshot.activity != "idle":
                raise HarnessAdapterBusyError("Pi became busy before the guarded write")

        return guard

    def _clear_operation(self, operation: ControlOperationRef) -> None:
        if self._active_operation == operation:
            self._active_operation = None
        if self._prepared_operation is not None and self._prepared_operation[0] == operation:
            self._prepared_operation = None

    @staticmethod
    def _require_operation(
        operation: ControlOperationRef | None,
        kind: Literal["prompt", "set-model", "set-effort"],
    ) -> ControlOperationRef:
        if operation is None or operation.kind != kind:
            raise HarnessControlError(f"Pi {kind} requires its exact operation ref")
        return operation

    @staticmethod
    def _require_idle_state(state: PiSessionState) -> None:
        if state.is_streaming or state.is_compacting or state.pending_message_count:
            raise HarnessAdapterBusyError("Pi fresh state is not idle")

    def _require_started(self) -> None:
        if self._events is None or self._launch is None or self._transport is None:
            raise HarnessControlError("Pi RPC adapter is not started")
        if self._stopped:
            raise HarnessControlError("Pi RPC adapter is stopped")

    def _require_transport(self) -> PiRpcTransport:
        if self._transport is None:
            raise HarnessControlError("Pi RPC adapter has no transport")
        return self._transport

    def _require_event_mapper(self) -> PiRpcEventMapper:
        if self._events is None:
            raise HarnessControlError("Pi RPC adapter has no event mapper")
        return self._events

    def _require_launch(self) -> LaunchSpec:
        if self._launch is None:
            raise HarnessControlError("Pi RPC adapter has no launch specification")
        return self._launch
