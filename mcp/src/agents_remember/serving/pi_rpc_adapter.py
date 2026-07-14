"""Protocol-backed Pi RPC adapter atop the normalized L1 harness bridge."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from agents_remember.errors import HarnessAdapterDisconnectedError, HarnessControlError
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
)
from agents_remember.serving.pi_rpc_events import PiRpcEventMapper
from agents_remember.serving.pi_rpc_process import PiRpcSubprocess, PiRpcTransport
from agents_remember.serving.pi_rpc_protocol import (
    PI_RPC_PROTOCOL,
    PiEntries,
    PiSessionState,
    has_no_session,
    non_negative_int,
    parse_pi_entries,
    parse_pi_response,
    parse_pi_state,
    pi_entry_user_text,
    pi_response_error,
    pi_rpc_launch,
    pi_rpc_resume_launch,
    require_pi_rpc_version,
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
    """Own a Pi RPC subprocess and translate only documented 0.80.6 protocol semantics."""

    def __init__(
        self,
        *,
        version: str,
        transport_factory: TransportFactory = PiRpcSubprocess,
        submission_limit: int = 256,
        interaction_limit: int = 64,
        clock: Clock = lambda: datetime.now(UTC).isoformat(),
    ) -> None:
        if submission_limit < 1 or interaction_limit < 1:
            raise HarnessControlError("Pi RPC adapter limits must be positive")
        self._version = version
        self._transport_factory = transport_factory
        self._submission_limit = submission_limit
        self._interaction_limit = interaction_limit
        self._clock = clock
        self._transport: PiRpcTransport | None = None
        self._launch: LaunchSpec | None = None
        self._state: PiSessionState | None = None
        self._events: PiRpcEventMapper | None = None
        self._cursor: str | None = None
        self._submissions: OrderedDict[str, _SubmissionEvidence] = OrderedDict()
        self._request_sequence = 0
        self._transport_generation = 0
        self._transport_changed = asyncio.Event()
        self._stopped = False

    async def start(self, launch: LaunchSpec) -> AdapterHandshake:
        if self._transport is not None:
            raise HarnessControlError("Pi RPC adapter is already started")
        require_pi_rpc_version(self._version)
        rpc_launch = pi_rpc_launch(launch)
        transport = self._transport_factory()
        await transport.start(rpc_launch)
        self._transport = transport
        self._launch = rpc_launch
        self._events = PiRpcEventMapper(
            launch.identity,
            version=self._version,
            interaction_limit=self._interaction_limit,
            clock=self._clock,
        )
        state = await self._read_state()
        entries = await self._read_entries()
        self._cursor = entries.leaf_id
        snapshot = self._events.apply_state(state, cursor=self._cursor)
        return AdapterHandshake(
            protocol_version=CONTROL_PROTOCOL_VERSION,
            adapter_id=f"pi-rpc:{self._version}",
            identity=launch.identity,
            capabilities=REQUIRED_ADAPTER_CAPABILITIES,
            snapshot=snapshot,
            raw={
                "vendorProtocol": PI_RPC_PROTOCOL,
                "piVersion": self._version,
                "entryCursor": self._cursor,
            },
        )

    async def snapshot(self) -> AdapterSnapshot:
        self._require_started()
        state = await self._read_state()
        return self._require_event_mapper().apply_state(state, cursor=self._cursor)

    async def _event_stream(self) -> AsyncIterator[AdapterEvent]:
        self._require_started()
        mapper = self._require_event_mapper()
        while not self._stopped:
            transport = self._require_transport()
            generation = self._transport_generation
            try:
                async for frame in transport.events():
                    settled_state = (
                        await self._read_state() if frame.get("type") == "agent_settled" else None
                    )
                    yield mapper.translate(frame, settled_state=settled_state, cursor=self._cursor)
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
        behavior = self._streaming_behavior(request)
        command: dict[str, object] = {
            "id": request.request_id,
            "type": "prompt",
            "message": request.text,
        }
        if behavior is not None:
            command["streamingBehavior"] = behavior
        try:
            frame = await self._require_transport().request(command)
        except HarnessAdapterDisconnectedError as exc:
            knowledge: SubmissionKnowledge = "unknown" if exc.may_have_sent else "rejected"
            self._submissions[request.request_id] = replace(
                self._submissions[request.request_id], state=knowledge
            )
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
            return SubmissionReceipt(
                request_id=request.request_id,
                acceptance="rejected",
                submitted_at=request.submitted_at,
                vendor_correlation_id=request.request_id,
                detail=pi_response_error(response),
                raw=dict(response),
            )
        acceptance = "queued" if behavior is not None else "immediate"
        self._submissions[request.request_id] = replace(
            self._submissions[request.request_id], state="accepted"
        )
        self._require_event_mapper().set_acceptance(acceptance)
        return SubmissionReceipt(
            request_id=request.request_id,
            acceptance=acceptance,
            submitted_at=request.submitted_at,
            vendor_correlation_id=request.request_id,
            accepted_at=self._clock(),
            raw={"streamingBehavior": behavior, **dict(response)},
        )

    async def respond(self, response: InteractionResponse) -> None:
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

    async def _read_state(self) -> PiSessionState:
        request_id = self._internal_id("state")
        frame = await self._require_transport().request({"id": request_id, "type": "get_state"})
        response = parse_pi_response(frame, request_id=request_id, command="get_state")
        require_pi_success(response, "get_state")
        state = parse_pi_state(response)
        self._state = state
        return state

    async def _read_entries(self, *, since: str | None = None) -> PiEntries:
        request_id = self._internal_id("entries")
        command: dict[str, object] = {"id": request_id, "type": "get_entries"}
        if since is not None:
            command["since"] = since
        frame = await self._require_transport().request(command)
        response = parse_pi_response(frame, request_id=request_id, command="get_entries")
        require_pi_success(response, "get_entries")
        return parse_pi_entries(response)

    async def _reconnect(self) -> bool:
        launch = self._require_launch()
        state = self._state
        if state is None or state.session_file is None or has_no_session(launch.argv):
            return False
        previous_session_id = state.session_id
        old_transport = self._require_transport()
        self._transport_generation += 1
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

    def _streaming_behavior(self, request: PromptRequest) -> str | None:
        snapshot = self._require_event_mapper().snapshot
        raw = snapshot.raw
        busy = (
            snapshot.activity in {"running", "blocked", "settling"}
            or raw.get("isStreaming") is True
            or raw.get("isCompacting") is True
            or non_negative_int(raw.get("pendingMessageCount")) > 0
        )
        if not busy:
            return None
        return "steer" if request.source == "terminal" else "followUp"

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
