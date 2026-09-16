"""Native eve protocol adapter: AR's session contract over eve's durable session API.

The adapter owns exactly one AR-owned eve application process and one durable eve session per
bridge epoch. It speaks eve's documented HTTP session routes and reads the durable NDJSON event
stream from an absolute event index, so readiness, acceptance and terminal state are all derived
from the protocol rather than from a pane, a log line or a successful process spawn.

Three properties are load-bearing and are enforced in code below:

* **Cursor, not event id, is the resume position.** eve's stable ``meta.id`` recognises an event a
  reconnect already delivered; only the absolute stream index can resume without a gap, because
  ids minted by different durable steps in the same millisecond do not sort.
* **Acceptance is not completion.** A create or follow-up response proves eve durably queued the
  message; the turn's boundary event proves it finished. The receipt reports the first. When the
  response itself is lost the outcome is ambiguous, and ``reconcile`` reads the durable record to
  answer with evidence instead of repeating a possibly accepted write.
* **Ordinary deliveries queue.** eve's default for a follow-up is cancellation-backed ``steer``.
  This adapter always spells ``queue``, so a second ordinary delivery never cancels active work;
  a deliberate interrupt is a separate, turn-addressed operation.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from agents_remember.errors import (
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
from agents_remember.serving.eve_events import EveEventMapper, EveSnapshotInputs
from agents_remember.serving.eve_protocol import (
    TURN_BOUNDARY_EVENT_TYPES,
    EveEventDeduplicator,
    EveStreamEvent,
)
from agents_remember.serving.eve_runtime_client import (
    EveRuntimeProcess,
    EveRuntimeTransport,
)
from agents_remember.serving.eve_runtime_launch import (
    EveLaunchSelection,
    EveRuntimeSpec,
    launch_spec_binding,
    launch_spec_selection,
    launch_spec_state_root,
    resolve_runtime_spec,
)
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    ModelCapability,
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
)

Clock = Callable[[], str]
RuntimeFactory = Callable[[EveRuntimeSpec], EveRuntimeTransport]

EVE_ADAPTER_ID = "eve-session"

DEFAULT_DISCOVERY_HEALTH_TIMEOUT_SECONDS = 120.0
"""How long a cold eve application may take to answer its own health route.

eve compiles and boots its host before it serves anything, which is seconds on a warm install and
longer on a cold one; the health route is still the readiness proof, this is only its budget.
"""

REASONING_EFFORTS = (
    "provider-default",
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
)
"""The reasoning levels eve documents for ``agent.ts``, used ONLY to validate a LAUNCH selection.

This is not a catalog. The pinned application reads no effort value, so nothing here is advertised
as a selectable option: the set exists so that a settings-owned selection naming an undocumented
level is refused at launch instead of being passed through as if it meant something. A launch that
names a documented level still runs identically, which is why the capability catalog publishes no
effort options at all.
"""

EVE_MODEL_UNKNOWN_DETAIL = (
    "eve's model is compiled into the pinned runtime application, so this adapter cannot enumerate "
    "the catalog the running application resolved. The model reported here is the one the launch "
    "selection fixed, and it is verified from the runtime's own environment."
)


@dataclass(frozen=True)
class EveAdapterLimits:
    """How much one eve adapter may retain and how long one native exchange may take."""

    submission: int = 256
    interaction: int = 64
    health_timeout_seconds: float = DEFAULT_DISCOVERY_HEALTH_TIMEOUT_SECONDS


DEFAULT_EVE_ADAPTER_LIMITS = EveAdapterLimits()


@dataclass(frozen=True)
class _SubmissionEvidence:
    """What one admitted request knows about itself while its native outcome is still open.

    ``detail`` is the outcome's own words -- a refusal's reason, or why a write is ambiguous -- so a
    reconciliation can report what actually happened instead of only which state it reached.
    """

    request_id: str
    text: str
    cursor_before: int
    state: Literal["pending", "accepted", "rejected", "unknown"]
    delivery_id: str | None = None
    session_id: str | None = None
    detail: str | None = None


class EveSessionAdapter:
    """Own one AR eve runtime and translate only structurally validated protocol semantics."""

    def __init__(
        self,
        *,
        runtime_factory: RuntimeFactory | None = None,
        limits: EveAdapterLimits = DEFAULT_EVE_ADAPTER_LIMITS,
        clock: Clock = lambda: datetime.now(UTC).isoformat(),
        expected_launch: EveLaunchSelection | None = None,
    ) -> None:
        if limits.submission < 1 or limits.interaction < 1:
            raise HarnessControlError("eve adapter limits must be positive")
        self._runtime_factory = runtime_factory or self._default_runtime
        self._submission_limit = limits.submission
        self._interaction_limit = limits.interaction
        self._health_timeout = limits.health_timeout_seconds
        self._clock = clock
        self._expected_launch = expected_launch
        self._runtime: EveRuntimeTransport | None = None
        self._launch: LaunchSpec | None = None
        self._spec: EveRuntimeSpec | None = None
        self._events: EveEventMapper | None = None
        self._capabilities: CapabilitySnapshot | None = None
        self._cursor = 0
        self._high_water = 0
        self._replay = EveEventDeduplicator()
        self._submissions: dict[str, _SubmissionEvidence] = {}
        self._request_sequence = 0
        self._transport_generation = 0
        self._activity_token = 0
        self._prepared_operation: tuple[ControlOperationRef, int, int] | None = None
        self._active_operation: ControlOperationRef | None = None
        self._observed_turn_id: str | None = None
        self._transport_changed = asyncio.Event()
        self._stopped = False
        self._last_interrupt: tuple[tuple[str | None, str], InterruptResult] | None = None

    # -- lifecycle ------------------------------------------------------------------------

    async def start(self, launch: LaunchSpec) -> AdapterHandshake:
        if self._runtime is not None:
            raise HarnessControlError("eve adapter is already started")
        spec = self._resolve_spec(launch)
        runtime = self._runtime_factory(spec)
        started = False
        ready = False
        try:
            await runtime.start()
            started = True
            self._runtime = runtime
            self._launch = launch
            self._spec = spec
            self._events = EveEventMapper(
                launch.identity,
                interaction_limit=self._interaction_limit,
                clock=self._clock,
            )
            health = await runtime.health()
            selection = self._selection_for(launch)
            self._capabilities = self._capability_snapshot(selection)
            if self._expected_launch is not None:
                _verify_effective_selection(self._expected_launch, selection)
            snapshot = self._require_mapper().publish(
                EveSnapshotInputs(
                    extras={
                        "runtimeEndpoint": runtime.endpoint,
                        "runtimeRoot": str(spec.root),
                        "eveHealth": dict(health),
                        "selectedModel": selection.model_key,
                        "selectedEffort": selection.effort,
                    },
                )
            )
            ready = True
            return AdapterHandshake(
                protocol_version=CONTROL_PROTOCOL_VERSION,
                adapter_id=EVE_ADAPTER_ID,
                identity=launch.identity,
                capabilities=REQUIRED_ADAPTER_CAPABILITIES,
                snapshot=snapshot,
                raw={
                    "vendorProtocol": "eve-http-session/v1",
                    "runtimeRoot": str(spec.root),
                    "runtimeEndpoint": runtime.endpoint,
                    "nodeExecutable": spec.node_executable,
                    "streamCursor": self._cursor,
                    "sessionId": None,
                },
            )
        finally:
            if not ready:
                if started:
                    await runtime.stop("forced")
                self._runtime = None
                self._launch = None
                self._spec = None
                self._events = None
                self._capabilities = None

    async def stop(self, mode: ShutdownMode) -> None:
        """Stop the runtime this adapter started; a durable eve session is never deleted here."""

        if self._stopped:
            return
        self._stopped = True
        self._transport_changed.set()
        runtime = self._runtime
        if runtime is not None:
            await runtime.stop(mode)

    # -- discovery and capabilities --------------------------------------------------------

    async def discover(self, launch: LaunchSpec) -> CapabilitySnapshot:
        """Read the token-free catalog a launch would produce, without keeping a session."""

        spec = self._resolve_spec(launch)
        runtime = self._runtime_factory(spec)
        started = False
        try:
            await runtime.start()
            started = True
            await runtime.health()
            return self._capability_snapshot(self._selection_for(launch))
        finally:
            if started:
                await runtime.stop("forced")

    def advertise(self) -> CapabilitySnapshot:
        self._require_started()
        capabilities = self._capabilities
        if capabilities is None:
            raise HarnessControlError("eve adapter has no advertised catalog")
        return capabilities

    async def set_model(
        self, model_key: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        """Refuse a live model change: eve's model is a compiled application value.

        Accepting a different key here would silently keep serving the old model, so the adapter
        reports ``unsupported`` and names the one path that really changes it -- a rotated runtime.
        """

        del operation
        self._require_started()
        return SetResult(
            ok=False,
            acceptance="unsupported",
            requested_value=model_key,
            effective_value=self.advertise().selected_model_key,
            detail=(
                "eve's model is compiled into the pinned runtime application; changing it requires "
                "a new runtime launch, not a live session mutation"
            ),
        )

    async def set_effort(
        self, effort: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del operation
        self._require_started()
        catalog = self.advertise()
        detail = (
            "eve reasoning effort must be one of " + ", ".join(REASONING_EFFORTS)
            if effort not in REASONING_EFFORTS
            else (
                "eve's reasoning effort is compiled into the pinned runtime application; changing "
                "it requires a new runtime launch, not a live session mutation"
            )
        )
        return SetResult(
            ok=False,
            acceptance="unsupported",
            requested_value=effort,
            # No effort became effective, so the model the session is actually running stays the
            # reported effective value rather than the refused request being echoed back.
            effective_value=catalog.selected_model_key,
            detail=detail,
        )

    # -- state ----------------------------------------------------------------------------

    async def snapshot(self) -> AdapterSnapshot:
        self._require_started()
        snapshot = self._require_mapper().snapshot
        return replace(
            snapshot,
            raw={
                **dict(snapshot.raw),
                "streamCursor": self._cursor,
                "observedTurnId": self._observed_turn_id,
            },
        )

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        return self._event_stream()

    async def preflight_operation(self, operation: ControlOperationRef) -> None:
        """Capture fresh idle protocol evidence while the authority still owns the queue row."""

        self._require_started()
        mapper = self._require_mapper()
        if self._active_operation is not None:
            raise HarnessAdapterBusyError("eve already has an active ordinary operation")
        if mapper.interactions:
            raise HarnessAdapterBusyError("eve has a pending input request")
        if mapper.session_terminal is not None:
            raise HarnessAdapterBusyError(
                "eve session is terminal; no further delivery is possible"
            )
        if self._observed_turn_id is not None:
            raise HarnessAdapterBusyError("eve still has an observed open turn")
        await self._require_runtime().health()
        self._activity_token += 1
        self._prepared_operation = (
            operation,
            self._transport_generation,
            self._activity_token,
        )

    def attach_durable_session(self, session_id: str) -> AdapterSnapshot:
        """Attach this bridge epoch to an existing durable eve session.

        A restarted bridge resumes the session it already proved instead of creating a replacement;
        what it carries forward is the identity and the persisted absolute cursor, which is the
        whole of what eve's contract makes resumable.
        """

        self._require_started()
        return self._require_mapper().bind_session(session_id)

    # -- delivery -------------------------------------------------------------------------

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self._require_started()
        operation = self._require_operation(request.operation, "prompt")
        if operation.operation_id != request.request_id:
            raise HarnessControlError("eve prompt operation id does not match request id")
        if request.request_id in self._submissions:
            raise HarnessControlError(f"duplicate eve request id: {request.request_id}")
        if request.assets:
            raise HarnessControlError(
                "eve asset submission is not implemented; this adapter refuses the payload rather "
                "than dropping the assets"
            )
        self._remember_admission(request)
        guard = self._claim_prepared_operation(operation)
        mapper = self._require_mapper()
        session_id = mapper.session_id
        cursor_before = self._cursor
        try:
            guard()
            if session_id is None:
                new_session, delivery_id = await self._require_runtime().create_session(
                    request.text
                )
                mapper.bind_session(new_session)
                session_id = new_session
            else:
                _, delivery_id = await self._require_runtime().send_message(
                    session_id, request.text
                )
        except HarnessAdapterBusyError:
            self._submissions.pop(request.request_id, None)
            self._clear_operation(operation)
            raise
        except HarnessAdapterDisconnectedError as exc:
            self._mark_unknown(
                request.request_id, str(exc), delivery_id=None, cursor_before=cursor_before
            )
            raise HarnessAdapterDisconnectedError(
                str(exc),
                may_have_sent=exc.may_have_sent,
                vendor_correlation_id=request.request_id,
            ) from exc
        except HarnessControlError as exc:
            self._mark_rejected(request.request_id, str(exc))
            self._clear_operation(operation)
            raise
        self._mark_accepted(request.request_id, delivery_id=delivery_id, session_id=session_id)
        mapper.publish_dispatching()
        accepted_at = self._clock()
        return SubmissionReceipt(
            request_id=request.request_id,
            acceptance="immediate",
            submitted_at=request.submitted_at,
            vendor_correlation_id=delivery_id or request.request_id,
            accepted_at=accepted_at,
            detail="eve durably accepted the message; turn completion is reported on the stream",
            raw={"sessionId": session_id, "turnPolicy": "queue"},
        )

    async def respond(self, response: InteractionResponse) -> None:
        self._require_started()
        mapper = self._require_mapper()
        session_id = self._require_session_id()
        if response.operation is not None and response.operation != self._active_operation:
            raise HarnessControlError(
                "eve interaction response does not match the active operation"
            )
        payload = mapper.response_payload(response.interaction_id, response.response)
        try:
            await self._require_runtime().send_input_responses(session_id, (payload,))
        except HarnessAdapterDisconnectedError as exc:
            raise HarnessAdapterDisconnectedError(
                str(exc),
                may_have_sent=exc.may_have_sent,
                vendor_correlation_id=response.interaction_id,
            ) from exc
        mapper.complete_response(response.interaction_id)
        self._activity_token += 1

    async def interrupt(
        self,
        *,
        turn_id: str | None,
        expected_operation_id: str | None,
    ) -> InterruptResult:
        """One turn-addressed native cancel, replayed once for the same observed turn.

        eve's cancel route is cooperative: ``accepted`` means the live session durably queued the
        request, and the turn's own ``turn.cancelled``/``session.waiting`` boundary is what proves
        it settled. The acknowledgement therefore reports acceptance only.
        """

        self._require_started()
        # Guard order is fixed and the refusal names both inputs: the caller's turn identity is
        # checked first because it addresses native work, then the AR operation identity.
        observed = self._observed_turn_id
        if turn_id is not None and observed is not None and turn_id != observed:
            raise HarnessControlError(
                "interrupt turn id does not match the observed eve turn: "
                f"requested {turn_id!r}, observed {observed!r}"
            )
        active = self._active_operation
        if active is None:
            raise HarnessControlError("no active eve operation to interrupt")
        if expected_operation_id is not None and expected_operation_id != active.operation_id:
            raise HarnessControlError(
                "interrupt operation id does not match the active eve operation: "
                f"requested {expected_operation_id!r}, active {active.operation_id!r}"
            )
        target = turn_id or observed
        pair = (target, active.operation_id)
        if self._last_interrupt is not None and self._last_interrupt[0] == pair:
            return self._last_interrupt[1]
        try:
            body = await self._require_runtime().cancel_turn(
                self._require_session_id(), turn_id=target
            )
        except HarnessControlError as exc:
            result = InterruptResult(
                acknowledgement="rejected",
                bridge_epoch="",
                operation=active,
                vendor_correlation_id=target,
                detail=str(exc),
                raw={"eveRoute": "cancel"},
            )
        else:
            status = body.get("status")
            accepted = status == "accepted"
            result = InterruptResult(
                acknowledgement="accepted" if accepted else "unknown",
                bridge_epoch="",
                operation=active,
                vendor_correlation_id=target,
                detail=(
                    "native cancel durably queued for the observed eve turn; settlement is reported "
                    "by the turn boundary on the stream"
                    if accepted
                    else f"native cancel answered {status!r} (no active turn on that id)"
                ),
                raw={"eveRoute": "cancel", "eveStatus": status, "turnId": target},
            )
        self._last_interrupt = (pair, result)
        return result

    # -- reconciliation -------------------------------------------------------------------

    async def reconcile(self, request_id: str) -> ReconciliationResult:
        """Answer from durable native evidence; never repeat a possibly accepted write."""

        self._require_started()
        evidence = self._submissions.get(request_id)
        if evidence is None:
            raise HarnessControlError(f"unknown eve request id: {request_id}")
        if evidence.state == "accepted":
            return self._reconciled(
                request_id,
                "accepted",
                evidence,
                "eve's acceptance response for this request was already observed",
            )
        if evidence.state == "rejected":
            return self._reconciled(
                request_id,
                "rejected",
                evidence,
                "eve refused this request before any durable write",
            )
        session_id = evidence.session_id or self._require_mapper().session_id
        if session_id is None:
            return self._reconciled(
                request_id,
                "unresolved",
                evidence,
                "no durable session id was ever proved for this request",
            )
        try:
            observed = await self._read_from_cursor(session_id, evidence.cursor_before)
        except HarnessControlError as exc:
            return self._reconciled(
                request_id, "unresolved", evidence, f"durable read failed: {exc}"
            )
        if any(_proves_delivery(event, evidence) for event in observed):
            self._mark_accepted(request_id, delivery_id=evidence.delivery_id, session_id=session_id)
            return self._reconciled(
                request_id,
                "accepted",
                evidence,
                "the durable record holds this exact message verbatim past this request's cursor; "
                "no resend performed",
            )
        return self._reconciled(
            request_id,
            "unresolved",
            evidence,
            "the durable record past this request's cursor does not prove acceptance; "
            "the write may or may not have landed, so it is not repeated",
        )

    # -- streaming ------------------------------------------------------------------------

    async def _event_stream(self) -> AsyncIterator[AdapterEvent]:
        self._require_started()
        mapper = self._require_mapper()
        while not self._stopped:
            runtime = self._require_runtime()
            generation = self._transport_generation
            session_id = mapper.session_id
            if session_id is None:
                # No durable session exists yet: nothing to read, so wait for one to be admitted
                # rather than opening a stream against an id this epoch has not proved.
                self._transport_changed.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._transport_changed.wait(), timeout=1.0)
                continue
            try:
                async for event in runtime.stream(session_id, start_index=self._cursor):
                    self._activity_token += 1
                    translated = self._translate(event)
                    if translated is None:
                        continue
                    if translated.kind in {"completed", "failed", "cancelled"}:
                        self._finish_operation()
                    yield translated
            except HarnessAdapterDisconnectedError as exc:
                if generation == self._transport_generation:
                    yield mapper.disconnected(str(exc))
            except HarnessControlError as exc:
                yield mapper.failed(str(exc))
                return
            if self._stopped:
                return
            # A closed reader is not a dead session: reconnect from the persisted absolute index.
            # Reconnecting is also what tells a live-but-parked session apart from a finished one,
            # so no state is invented here.
            await asyncio.sleep(0.05)

    def _translate(self, event: EveStreamEvent) -> AdapterEvent | None:
        """Advance the cursor, drop a replayed duplicate, and translate one native event."""

        mapper = self._require_mapper()
        if self._is_replay(event):
            self._cursor = max(self._cursor, event.index + 1)
            return None
        self._cursor = max(self._cursor, event.index + 1)
        self._high_water = max(self._high_water, self._cursor)
        if event.type == "turn.started":
            self._observed_turn_id = event.turn_id
        elif event.type == "turn.completed" or event.type in {
            "turn.failed",
            "turn.cancelled",
            "session.waiting",
            "session.completed",
            "session.failed",
        }:
            self._observed_turn_id = None
        return mapper.translate(event, cursor=self._cursor)

    def _is_replay(self, event: EveStreamEvent) -> bool:
        """One replay verdict per event, from the two proofs eve's record actually offers.

        An identified event is answered by the module's single bounded id window; a record written
        before stream version 20 carries no id at all, so the contiguous high-water mark is the only
        replay proof left for it.
        """

        if event.event_id is not None:
            return self._replay.is_replay(event)
        return event.index < self._high_water

    async def _read_from_cursor(self, session_id: str, cursor: int) -> tuple[EveStreamEvent, ...]:
        """Read the durable record from one absolute index up to the tail, then stop."""

        events: list[EveStreamEvent] = []
        runtime = self._require_runtime()
        try:
            async for event in runtime.stream(session_id, start_index=cursor):
                events.append(event)
                if len(events) >= _RECONCILE_READ_LIMIT:
                    break
        except HarnessAdapterDisconnectedError:
            # A bounded read that ended early still carries everything it read; the caller decides
            # whether that is enough to answer.
            pass
        return tuple(events)

    # -- internals ------------------------------------------------------------------------

    def _default_runtime(self, spec: EveRuntimeSpec) -> EveRuntimeTransport:
        return EveRuntimeProcess(spec.launch, health_timeout_seconds=self._health_timeout)

    def _resolve_spec(self, launch: LaunchSpec) -> EveRuntimeSpec:
        """Resolve the launch from what the spec asked for, including its own overrides.

        Resolution reads the launch environment as given, because ``AR_EVE_RUNTIME_ROOT`` and
        ``AR_EVE_NODE`` are the documented ways a caller names an application and an interpreter.
        The *child* environment is composed separately: the adapter owns those two values for the
        process it starts, and a stale ambient one must not reappear inside it.
        """

        return resolve_runtime_spec(
            selection=self._selection_for(launch),
            binding=launch_spec_binding(launch),
            env={**os.environ, **dict(launch.env)},
            state_root=launch_spec_state_root(launch),
        )

    def _selection_for(self, launch: LaunchSpec) -> EveLaunchSelection:
        return launch_spec_selection(launch)

    def _capability_snapshot(self, selection: EveLaunchSelection) -> CapabilitySnapshot:
        """The catalog this runtime can actually back.

        The model is real: it is the value the runtime compiles into its provider handle. The effort
        axis is not. The pinned application reads no effort value at all -- no ``AR_EVE_EFFORT``
        consumer exists under ``eve_runtime/agent``, and the adapter's launch vocabulary carries the
        selection as environment provenance rather than as a knob the runtime honours -- so an
        effort menu would advertise a control whose every value produces the same run. The catalog
        therefore offers no effort options and does not claim effort support; the launch selection
        is still REPORTED (``selected_effort``) because it is the configuration the runtime was
        started under, which is a fact rather than a menu.
        """

        return CapabilitySnapshot(
            models=(
                ModelCapability(
                    key=selection.model_key,
                    display_name=selection.model_key,
                    description=EVE_MODEL_UNKNOWN_DETAIL,
                    resolved_model=selection.model_key,
                    supports_effort=False,
                    effort_options=(),
                    default_effort=None,
                    is_default=True,
                    selectable=True,
                    provider=selection.provider_name,
                ),
            ),
            selected_model_key=selection.model_key,
            selected_effort=selection.effort,
        )

    def _remember_admission(self, request: PromptRequest) -> None:
        if len(self._submissions) >= self._submission_limit:
            self._evict_submission()
        self._submissions[request.request_id] = _SubmissionEvidence(
            request_id=request.request_id,
            text=request.text,
            cursor_before=self._cursor,
            state="pending",
        )

    def _evict_submission(self) -> None:
        """Drop the oldest settled row; a live row is never dropped to make room."""

        for request_id, evidence in self._submissions.items():
            if evidence.state != "pending":
                del self._submissions[request_id]
                return
        raise HarnessAdapterBusyError("eve submission evidence is full of unresolved requests")

    def _mark_accepted(
        self,
        request_id: str,
        *,
        delivery_id: str | None,
        session_id: str | None,
    ) -> None:
        evidence = self._submissions.get(request_id)
        if evidence is None:
            return
        self._submissions[request_id] = replace(
            evidence,
            state="accepted",
            delivery_id=delivery_id or evidence.delivery_id,
            session_id=session_id or evidence.session_id,
        )

    def _mark_rejected(self, request_id: str, detail: str) -> None:
        evidence = self._submissions.get(request_id)
        if evidence is None:
            return
        self._submissions[request_id] = replace(evidence, state="rejected", detail=detail)

    def _mark_unknown(
        self,
        request_id: str,
        detail: str,
        *,
        delivery_id: str | None,
        cursor_before: int | None = None,
    ) -> None:
        evidence = self._submissions.get(request_id)
        if evidence is None:
            return
        self._submissions[request_id] = replace(
            evidence,
            state="unknown",
            delivery_id=delivery_id or evidence.delivery_id,
            session_id=evidence.session_id or self._require_mapper().session_id,
            cursor_before=cursor_before if cursor_before is not None else evidence.cursor_before,
            detail=detail,
        )

    def _reconciled(
        self,
        request_id: str,
        state: Literal["accepted", "rejected", "unresolved"],
        evidence: _SubmissionEvidence,
        detail: str,
    ) -> ReconciliationResult:
        return ReconciliationResult(
            request_id=request_id,
            state=state,
            reconciled_at=self._clock(),
            vendor_correlation_id=evidence.delivery_id or request_id,
            detail=detail,
            raw={
                "sessionId": evidence.session_id or self._require_mapper().session_id,
                "cursorBefore": evidence.cursor_before,
                "streamCursor": self._cursor,
            },
        )

    def _finish_operation(self) -> None:
        """Release the active ordinary operation once its turn reached a native boundary."""

        operation = self._active_operation
        if operation is None:
            return
        self._clear_operation(operation)
        self._activity_token += 1

    def _claim_prepared_operation(self, operation: ControlOperationRef) -> Callable[[], None]:
        prepared = self._prepared_operation
        if prepared is None or prepared[0] != operation:
            raise HarnessAdapterBusyError("eve operation lacks matching fresh idle preflight")
        if self._active_operation is not None:
            raise HarnessAdapterBusyError("eve already has an active ordinary operation")
        self._active_operation = operation
        generation, activity_token = prepared[1], prepared[2]

        def guard() -> None:
            if self._active_operation != operation or self._prepared_operation != prepared:
                raise HarnessAdapterBusyError("eve operation changed before its guarded write")
            if self._transport_generation != generation:
                raise HarnessAdapterBusyError("eve runtime generation changed before the write")
            if self._activity_token != activity_token:
                raise HarnessAdapterBusyError("eve activity changed after fresh idle preflight")

        return guard

    def _clear_operation(self, operation: ControlOperationRef) -> None:
        if self._active_operation == operation:
            self._active_operation = None
        if self._prepared_operation is not None and self._prepared_operation[0] == operation:
            self._prepared_operation = None

    def _require_session_id(self) -> str:
        session_id = self._require_mapper().session_id
        if session_id is None:
            raise HarnessControlError("eve adapter has no durable session for this bridge epoch")
        return session_id

    def _require_started(self) -> None:
        if self._events is None or self._launch is None or self._runtime is None:
            raise HarnessControlError("eve adapter is not started")
        if self._stopped:
            raise HarnessControlError("eve adapter is stopped")

    def _require_runtime(self) -> EveRuntimeTransport:
        if self._runtime is None:
            raise HarnessControlError("eve adapter has no runtime")
        return self._runtime

    def _require_mapper(self) -> EveEventMapper:
        if self._events is None:
            raise HarnessControlError("eve adapter has no event mapper")
        return self._events

    @staticmethod
    def _require_operation(
        operation: ControlOperationRef | None,
        kind: Literal["prompt", "set-model", "set-effort"],
    ) -> ControlOperationRef:
        if operation is None or operation.kind != kind:
            raise HarnessControlError(f"eve {kind} requires its exact operation ref")
        return operation


_RECONCILE_READ_LIMIT = 4096
"""How many durable events one reconciliation read may scan.

A reconciliation answers a question about one delivery, which sits near the tail of the record it
was written to; the bound keeps a pathological record from turning one question into an unbounded
read, and reaching it reports ``unresolved`` rather than guessing.
"""


def _proves_delivery(event: EveStreamEvent, evidence: _SubmissionEvidence) -> bool:
    """Whether one durable event proves this exact submission was accepted.

    Only one proof is available to a request that needs reconciling, and it is this one. A delivery
    id is the stronger proof, but it reaches the adapter on the acceptance response -- so a request
    whose response was *lost* never learned one, and the delivery ids the durable frames carry
    cannot be tied back to it. What remains is the caller's own record of what it sent: the durable
    record holding the accepted user message verbatim past the cursor the request was written at.
    Its absence is what leaves the outcome genuinely unresolved rather than assumed in either
    direction.
    """

    return event.type == "message.received" and event.data.get("message") == evidence.text


def _verify_effective_selection(expected: EveLaunchSelection, actual: EveLaunchSelection) -> None:
    """Refuse a launch whose resolved selection is not the one the settings authority fixed."""

    if expected.model_key != actual.model_key:
        raise HarnessControlError(
            f"eve launch selected model {expected.model_key!r}, but the running runtime resolved "
            f"{actual.model_key!r}"
        )
    if expected.effort != actual.effort:
        raise HarnessControlError(
            f"eve launch selected effort {expected.effort!r}, but the running runtime resolved "
            f"{actual.effort!r}"
        )


__all__ = [
    "DEFAULT_EVE_ADAPTER_LIMITS",
    "EVE_ADAPTER_ID",
    "REASONING_EFFORTS",
    "TURN_BOUNDARY_EVENT_TYPES",
    "EveAdapterLimits",
    "EveEventMapper",
    "EveSessionAdapter",
    "EveSnapshotInputs",
    "EveStreamEvent",
]
