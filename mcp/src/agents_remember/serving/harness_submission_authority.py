"""Server-authoritative ordinary-operation timeline for one hosted harness bridge.

The authority is the only prompt FIFO.  It linearizes withdrawal against dispatch, keeps setters
in the same monotonic order, and pins an accepted ordinary operation until an exact correlated
completion releases it.  Adapters are dispatch-now vendor boundaries; they never own a second
prompt queue.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Literal, cast
from uuid import uuid4

from agents_remember.errors import (
    HarnessAdapterBusyError,
    HarnessAdapterDisconnectedError,
    HarnessBridgeEpochMismatchError,
    HarnessControlError,
    HarnessRequestConflictError,
)
from agents_remember.serving.harness_capabilities import SET_ACCEPTANCE_VALUES, SetResult
from agents_remember.serving.harness_control_adapter import HarnessProtocolAdapter
from agents_remember.serving.harness_control_models import (
    AcceptanceState,
    AdapterEvent,
    AdapterSnapshot,
    ControlOperationKind,
    ControlOperationRef,
    InteractionResponse,
    PromptRequest,
    ReconciliationResult,
    ReconciliationState,
    SubmissionAuthorityDescriptor,
    SubmissionLifecycleState,
    SubmissionLookup,
    SubmissionReceipt,
    SubmissionSource,
    SubmissionStatus,
    SubmissionStatusBatch,
    WithdrawalResult,
)

Clock = Callable[[], str]
SnapshotGetter = Callable[[], AdapterSnapshot]
SnapshotSetter = Callable[[AdapterSnapshot], None]
Publisher = Callable[[], None]
OperationResolution = Literal["applied", "not-applied"]
_OperationKey = tuple[ControlOperationKind, str]


@dataclass
class _OperationRecord:
    ref: ControlOperationRef
    state: SubmissionLifecycleState
    submitted_at: str
    updated_at: str
    source: str | None = None
    payload_digest: str | None = None
    text: str | None = None
    requested_value: str | None = None
    accepted_at: str | None = None
    vendor_correlation_id: str | None = None
    detail: str | None = None
    buffered_completion_sequence: int | None = None
    result_future: asyncio.Future[object] | None = None

    @property
    def key(self) -> _OperationKey:
        return (self.ref.kind, self.ref.operation_id)

    @property
    def live(self) -> bool:
        return self.state in {"queued", "dispatching", "unknown"}


class HarnessSubmissionAuthority:
    """Own one epoch-bound, bounded prompt/setter timeline above a protocol adapter."""

    def __init__(
        self,
        adapter: HarnessProtocolAdapter,
        *,
        timeline_limit: int,
        ledger_limit: int,
        clock: Clock,
        snapshot: SnapshotGetter,
        set_snapshot: SnapshotSetter,
        publish: Publisher,
        bridge_epoch: str | None = None,
    ) -> None:
        if timeline_limit < 1 or ledger_limit < 1:
            raise HarnessControlError("submission authority limits must be positive")
        if ledger_limit < timeline_limit:
            raise HarnessControlError("submission ledger limit cannot be below timeline limit")
        self._adapter = adapter
        self._timeline_limit = timeline_limit
        self._ledger_limit = ledger_limit
        self._clock = clock
        self._snapshot = snapshot
        self._set_snapshot = set_snapshot
        self._publish = publish
        self._bridge_epoch = bridge_epoch or uuid4().hex
        self._sequence = 0
        self._records: OrderedDict[_OperationKey, _OperationRecord] = OrderedDict()
        self._prompt_ids: dict[str, _OperationKey] = {}
        self._timeline: deque[_OperationKey] = deque()
        self._active: _OperationKey | None = None
        self._lock = asyncio.Lock()
        # The ordinary lane is held through one adapter method.  Status/withdraw use only
        # ``_lock`` and therefore remain responsive while vendor evidence is slow.
        self._ordinary_lane = asyncio.Lock()
        # Responses bypass a waiting ordinary call, but adapters arbitrate the actual bytes with
        # their transport write lock.  This second lane prevents duplicate response coroutines.
        self._response_lane = asyncio.Lock()
        self._responded_interactions: set[str] = set()
        self._wake = asyncio.Event()
        self._dispatcher: asyncio.Task[None] | None = None
        self._accepting = False
        self._last_adapter_sequence = 0
        self._consumed_completions: deque[ControlOperationRef] = deque(maxlen=ledger_limit)

    @property
    def bridge_epoch(self) -> str:
        return self._bridge_epoch

    @property
    def retained_record_count(self) -> int:
        return len(self._records)

    @property
    def active_operation(self) -> ControlOperationRef | None:
        record = self._records.get(self._active) if self._active is not None else None
        return record.ref if record is not None else None

    def descriptor(self) -> SubmissionAuthorityDescriptor:
        return SubmissionAuthorityDescriptor(bridge_epoch=self._bridge_epoch)

    def start(self) -> None:
        if self._dispatcher is not None:
            raise HarnessControlError("submission authority is already started")
        self._accepting = True
        self._dispatcher = asyncio.create_task(self._run_dispatcher())

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self._require_accepting()
        self._require_epoch(request.expected_bridge_epoch)
        digest = self._payload_digest(request.text)
        wait_for_dispatch = False
        future: asyncio.Future[object] | None = None
        async with self._lock:
            prior_key = self._prompt_ids.get(request.request_id)
            if prior_key is not None:
                prior = self._records[prior_key]
                if prior.source != request.source or prior.payload_digest != digest:
                    raise HarnessRequestConflictError(
                        f"request id {request.request_id!r} already belongs to its first source/payload"
                    )
                self._records.move_to_end(prior_key)
                return self._duplicate_receipt(prior)
            if self._live_count() >= self._timeline_limit or not self._make_ledger_room():
                return SubmissionReceipt(
                    request_id=request.request_id,
                    acceptance="rejected",
                    submitted_at=request.submitted_at,
                    detail="submission ledger is full of live or unresolved operations",
                    bridge_epoch=self._bridge_epoch,
                )
            ref = self._next_ref("prompt", request.request_id)
            record = _OperationRecord(
                ref=ref,
                state="queued",
                submitted_at=request.submitted_at,
                updated_at=request.submitted_at,
                source=request.source,
                payload_digest=digest,
                text=request.text,
                detail="queued inside the authoritative control bridge",
            )
            self._records[record.key] = record
            self._prompt_ids[request.request_id] = record.key
            if self._snapshot().control == "unsupported":
                self._mark_terminal(
                    record,
                    "unsupported",
                    "hosted harness does not support native prompt control",
                    request.submitted_at,
                )
                return self._receipt(record, "unsupported")
            self._timeline.append(record.key)
            wait_for_dispatch = self._head_is_dispatchable(record.key)
            if wait_for_dispatch:
                future = asyncio.get_running_loop().create_future()
                record.result_future = future
            self._wake.set()
            if not wait_for_dispatch:
                return self._receipt(record, "queued")
        # Preserve the existing immediate receipt when an idle adapter can accept now.  A later
        # prompt returns queued immediately instead of waiting behind the active vendor operation.
        assert future is not None
        return cast(SubmissionReceipt, await asyncio.shield(future))

    async def set_model(self, model_key: str) -> SetResult:
        return await self._admit_setter("set-model", model_key)

    async def set_effort(self, effort: str) -> SetResult:
        return await self._admit_setter("set-effort", effort)

    async def respond(self, response: InteractionResponse) -> AdapterSnapshot:
        self._require_accepting()
        async with self._lock:
            pending = self._snapshot().pending_interaction
            active = self._records.get(self._active) if self._active is not None else None
            if pending is None or pending.interaction_id != response.interaction_id:
                raise HarnessControlError(
                    "interaction response does not match the pending interaction"
                )
            if active is None or active.state not in {"dispatching", "delivered", "unknown"}:
                raise HarnessControlError("interaction response has no active ordinary operation")
            if response.interaction_id in self._responded_interactions:
                raise HarnessControlError("interaction response was already submitted")
            operation = active.ref
            self._responded_interactions.add(response.interaction_id)
        try:
            async with self._response_lane:
                await self._adapter.respond(replace(response, operation=operation))
        except Exception:
            async with self._lock:
                self._responded_interactions.discard(response.interaction_id)
            raise
        snapshot = await self._adapter.snapshot()
        if snapshot.identity != self._snapshot().identity:
            raise HarnessControlError(
                "adapter snapshot identity changed after interaction response"
            )
        self._set_snapshot(snapshot)
        self._publish()
        return snapshot

    async def reconcile(
        self, request_id: str, *, expected_bridge_epoch: str | None = None
    ) -> ReconciliationResult:
        self._require_epoch(expected_bridge_epoch)
        async with self._lock:
            key = self._prompt_ids.get(request_id)
            record = self._records.get(key) if key is not None else None
            if record is None:
                raise HarnessControlError(
                    f"submission {request_id!r} is no longer retained for reconciliation"
                )
            known = self._known_reconciliation(record)
            if known is not None:
                self._records.move_to_end(record.key)
                return known
            ref = record.ref
        # Reconciliation queries only the already-active ambiguous operation.  It never admits or
        # dispatches a second ordinary operation while the unknown barrier is installed.
        result = await self._adapter.reconcile(request_id)
        if result.request_id != request_id:
            raise HarnessControlError("adapter reconciliation request id does not match")
        async with self._lock:
            current = self._records.get(ref_key(ref))
            if current is None:
                raise HarnessControlError("submission disappeared during reconciliation")
            if result.state == "accepted":
                current.state = "delivered"
                current.accepted_at = result.reconciled_at
                current.vendor_correlation_id = result.vendor_correlation_id
                current.detail = result.detail
                current.updated_at = result.reconciled_at
                # Accepted proves delivery, not turn completion; the active gate stays pinned.
            elif result.state == "rejected":
                self._mark_terminal(current, "rejected", result.detail, result.reconciled_at)
                self._release_head(current, consume_completion=False)
            elif result.state == "unsupported":
                self._mark_terminal(current, "unsupported", result.detail, result.reconciled_at)
                self._release_head(current, consume_completion=False)
            else:
                current.detail = result.detail
                current.updated_at = result.reconciled_at
            projected = replace(
                result,
                bridge_epoch=self._bridge_epoch,
                submission_state=current.state,
            )
            self._wake.set()
            return projected

    async def resolve_operation(
        self,
        operation_id: str,
        operation_kind: ControlOperationKind,
        *,
        resolution: OperationResolution,
        detail: str,
    ) -> None:
        if resolution not in {"applied", "not-applied"}:
            raise HarnessControlError("operation resolution must be applied or not-applied")
        async with self._lock:
            record = self._records.get((operation_kind, operation_id))
            if record is None or record.state != "unknown":
                raise HarnessControlError("only an unknown operation can be operator-resolved")
            at = self._clock()
            self._mark_terminal(
                record,
                "delivered" if resolution == "applied" else "rejected",
                detail,
                at,
            )
            self._release_head(record, consume_completion=False)
            self._wake.set()

    async def resolve_unknown_prompt(
        self,
        request_id: str,
        *,
        state: ReconciliationState,
        detail: str,
    ) -> ReconciliationResult:
        if state not in {"accepted", "rejected"}:
            raise HarnessControlError("operator resolution must be accepted or rejected")
        await self.resolve_operation(
            request_id,
            "prompt",
            resolution="applied" if state == "accepted" else "not-applied",
            detail=detail,
        )
        return ReconciliationResult(
            request_id=request_id,
            state=state,
            reconciled_at=self._clock(),
            detail=detail,
            bridge_epoch=self._bridge_epoch,
            submission_state="delivered" if state == "accepted" else "rejected",
        )

    async def status(
        self,
        expected_bridge_epoch: str,
        request_ids: tuple[str, ...],
        *,
        cockpit_only: bool,
    ) -> SubmissionStatusBatch:
        self._require_epoch(expected_bridge_epoch)
        if not 1 <= len(request_ids) <= 64:
            raise HarnessControlError("submission status requires 1..64 request ids")
        if len(set(request_ids)) != len(request_ids):
            raise HarnessControlError("submission status request ids must be unique")
        async with self._lock:
            lookups: list[SubmissionLookup] = []
            for request_id in request_ids:
                key = self._prompt_ids.get(request_id)
                record = self._records.get(key) if key is not None else None
                if record is None or (cockpit_only and record.source != "cockpit"):
                    lookups.append(SubmissionLookup(request_id=request_id, outcome="not-found"))
                    continue
                lookups.append(
                    SubmissionLookup(
                        request_id=request_id,
                        outcome="found",
                        submission=self._status(record),
                    )
                )
            return SubmissionStatusBatch(
                bridge_epoch=self._bridge_epoch,
                submissions=tuple(lookups),
            )

    async def withdraw(
        self,
        expected_bridge_epoch: str,
        request_id: str,
        *,
        cockpit_only: bool,
    ) -> WithdrawalResult:
        self._require_epoch(expected_bridge_epoch)
        async with self._lock:
            key = self._prompt_ids.get(request_id)
            record = self._records.get(key) if key is not None else None
            if record is None or (cockpit_only and record.source != "cockpit"):
                return WithdrawalResult(
                    request_id=request_id,
                    outcome="not-found",
                    state=None,
                    detail="submission is not retained for this cockpit authority",
                )
            if record.state == "withdrawn":
                return WithdrawalResult(
                    request_id=request_id,
                    outcome="withdrawn",
                    state="withdrawn",
                    withdrawn_at=record.updated_at,
                    detail=record.detail,
                )
            if record.state != "queued":
                return WithdrawalResult(
                    request_id=request_id,
                    outcome="not-withdrawable",
                    state=record.state,
                    detail=f"submission is already {record.state}",
                )
            at = self._clock()
            self._timeline.remove(record.key)
            self._mark_terminal(record, "withdrawn", "queued submission was withdrawn", at)
            if record.result_future is not None and not record.result_future.done():
                record.result_future.set_result(self._receipt(record, "rejected"))
            self._wake.set()
            return WithdrawalResult(
                request_id=request_id,
                outcome="withdrawn",
                state="withdrawn",
                withdrawn_at=at,
                detail=record.detail,
            )

    async def observe_event(self, event: AdapterEvent) -> None:
        """Consume direct normalized events before the bridge publishes its coalesced snapshot."""

        async with self._lock:
            if event.sequence <= self._last_adapter_sequence:
                raise HarnessControlError("adapter event sequence is stale at submission authority")
            self._last_adapter_sequence = event.sequence
            if event.kind != "completed":
                self._wake.set()
                return
            if event.operation is None:
                raise HarnessControlError("completed adapter event requires exact operation ref")
            ref = event.operation
            if ref.bridge_epoch != self._bridge_epoch:
                raise HarnessControlError("completed adapter event bridge epoch mismatch")
            if ref in self._consumed_completions:
                raise HarnessControlError("duplicate adapter completion cannot release a successor")
            active = self._records.get(self._active) if self._active is not None else None
            if active is None or active.ref != ref:
                raise HarnessControlError("adapter completion does not match the active operation")
            if active.state == "dispatching":
                if active.buffered_completion_sequence is not None:
                    raise HarnessControlError("operation already has a buffered completion")
                active.buffered_completion_sequence = event.sequence
                return
            if active.state not in {"delivered", "unknown"}:
                raise HarnessControlError("active operation is not completion-eligible")
            self._mark_terminal(active, "delivered", active.detail, event.created_at)
            self._release_head(active, consume_completion=True)
            self._wake.set()

    def notify_snapshot_updated(self) -> None:
        """Wake dispatch after the bridge installs direct readiness/completion snapshot evidence."""

        self._wake.set()

    async def stop(self, *, forced: bool) -> None:
        self._accepting = False
        if not forced:
            async with self._lock:
                active = self._records.get(self._active) if self._active is not None else None
                pending_dispatch = (
                    active.result_future
                    if active is not None
                    and active.state == "dispatching"
                    and active.result_future is not None
                    and not active.result_future.done()
                    else None
                )
            if pending_dispatch is not None:
                await asyncio.gather(asyncio.shield(pending_dispatch), return_exceptions=True)
        if self._dispatcher is not None:
            self._dispatcher.cancel()
            await asyncio.gather(self._dispatcher, return_exceptions=True)
        error = HarnessControlError("submission authority stopped")
        async with self._lock:
            for record in self._records.values():
                if record.result_future is not None and not record.result_future.done():
                    record.result_future.set_exception(HarnessControlError(str(error)))
        await self._adapter.stop("forced" if forced else "graceful")

    async def _admit_setter(
        self, kind: Literal["set-model", "set-effort"], requested_value: str
    ) -> SetResult:
        self._require_accepting()
        if self._snapshot().control == "unsupported":
            return SetResult(
                ok=False,
                acceptance="unsupported",
                requested_value=requested_value,
                detail="hosted harness does not support native capability control",
            )
        async with self._lock:
            if self._live_count() >= self._timeline_limit or not self._make_ledger_room():
                return SetResult(
                    ok=False,
                    acceptance="unknown",
                    requested_value=requested_value,
                    detail="operation ledger is full of live or unresolved operations",
                )
            next_sequence = self._sequence + 1
            ref = self._next_ref(kind, f"{self._bridge_epoch}:{next_sequence}:{kind}")
            future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
            at = self._clock()
            record = _OperationRecord(
                ref=ref,
                state="queued",
                submitted_at=at,
                updated_at=at,
                requested_value=requested_value,
                detail="queued in the authoritative ordinary-operation timeline",
                result_future=future,
            )
            self._records[record.key] = record
            self._timeline.append(record.key)
            self._wake.set()
        return cast(SetResult, await asyncio.shield(future))

    async def _run_dispatcher(self) -> None:
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                while await self._dispatch_one():
                    pass
        except asyncio.CancelledError:
            raise

    async def _dispatch_one(self) -> bool:
        async with self._lock:
            if not self._timeline or self._active is not None:
                return False
            key = self._timeline[0]
            record = self._records[key]
            if record.state != "queued" or not self._snapshot_allows_dispatch():
                return False
        async with self._ordinary_lane:
            try:
                await self._adapter_preflight(record)
            except (HarnessAdapterBusyError, HarnessAdapterDisconnectedError) as exc:
                if isinstance(exc, HarnessAdapterDisconnectedError) and exc.may_have_sent:
                    # A preflight must never send operation bytes. Treat a contrary adapter claim
                    # as unknown rather than silently certifying a requeue.
                    async with self._lock:
                        self._active = key
                        self._set_unknown_locked(
                            record,
                            str(exc),
                            exc.vendor_correlation_id,
                        )
                        snapshot = self._snapshot()
                        self._set_snapshot(
                            replace(
                                snapshot,
                                control="disconnected",
                                activity="unknown",
                                acceptance="unknown",
                                raw={**snapshot.raw, "disconnect": str(exc)},
                            )
                        )
                        self._publish()
                    return False
                async with self._lock:
                    record.detail = str(exc)
                    record.updated_at = self._clock()
                    if record.ref.kind == "prompt":
                        self._resolve_record_future(record, self._receipt(record, "queued"))
                return False
            async with self._lock:
                if (
                    not self._timeline
                    or self._timeline[0] != key
                    or self._active is not None
                    or record.state != "queued"
                    or record.ref.bridge_epoch != self._bridge_epoch
                    or not self._snapshot_allows_dispatch()
                ):
                    return True
                record.state = "dispatching"
                record.updated_at = self._clock()
                self._active = key
            try:
                if record.ref.kind == "prompt":
                    assert record.text is not None and record.source is not None
                    result: object = await self._adapter.submit(
                        PromptRequest(
                            request_id=record.ref.operation_id,
                            source=cast(SubmissionSource, record.source),
                            text=record.text,
                            submitted_at=record.submitted_at,
                            operation=record.ref,
                        )
                    )
                elif record.ref.kind == "set-model":
                    assert record.requested_value is not None
                    result = await self._adapter_setter("set-model", record)
                else:
                    assert record.requested_value is not None
                    result = await self._adapter_setter("set-effort", record)
            except HarnessAdapterBusyError as exc:
                await self._certified_pre_send_busy(record, str(exc))
                return False
            except HarnessAdapterDisconnectedError as exc:
                if not exc.may_have_sent:
                    await self._certified_pre_send_busy(record, str(exc))
                    return False
                await self._possible_send_failure(record, str(exc), exc.vendor_correlation_id)
                return False
            except Exception as exc:
                await self._possible_send_failure(record, str(exc), None)
                return False
            try:
                return await self._apply_method_result(record, result)
            except Exception as exc:
                await self._incoherent_method_result(record, exc)
                return False

    async def _adapter_preflight(self, record: _OperationRecord) -> None:
        await self._adapter.preflight_operation(record.ref)

    async def _adapter_setter(
        self, kind: ControlOperationKind, record: _OperationRecord
    ) -> SetResult:
        assert record.requested_value is not None
        method = self._adapter.set_model if kind == "set-model" else self._adapter.set_effort
        return await method(record.requested_value, operation=record.ref)

    async def _apply_method_result(self, record: _OperationRecord, result: object) -> bool:
        async with self._lock:
            current = self._records.get(record.key)
            if current is not record or self._active != record.key:
                raise HarnessControlError("ordinary operation changed while adapter method ran")
            at = self._clock()
            if record.ref.kind == "prompt":
                if not isinstance(result, SubmissionReceipt):
                    raise HarnessControlError("adapter prompt result must be a submission receipt")
                receipt = result
                if receipt.request_id != record.ref.operation_id:
                    raise HarnessControlError("adapter receipt request id does not match operation")
                if receipt.acceptance == "queued":
                    # A vendor/adaptor queue escaped the common authority after dispatch crossed.
                    self._set_unknown_locked(
                        record,
                        "adapter returned forbidden queued acceptance after dispatch",
                        receipt.vendor_correlation_id,
                    )
                    return False
                record.vendor_correlation_id = receipt.vendor_correlation_id
                record.detail = receipt.detail
                record.updated_at = at
                if (
                    receipt.acceptance == "unknown"
                    and record.buffered_completion_sequence is not None
                ):
                    record.state = "delivered"
                    record.accepted_at = receipt.accepted_at or at
                    self._resolve_record_future(record, self._receipt(record, "immediate"))
                    self._mark_terminal(record, "delivered", record.detail, at)
                    self._release_head(record, consume_completion=True)
                    self._wake.set()
                    return True
                if receipt.acceptance == "immediate":
                    record.state = "delivered"
                    record.accepted_at = receipt.accepted_at or at
                    self._resolve_record_future(record, self._receipt(record, "immediate"))
                    if record.buffered_completion_sequence is not None or bool(
                        receipt.raw.get("terminalCompletion")
                    ):
                        self._mark_terminal(record, "delivered", record.detail, at)
                        self._release_head(record, consume_completion=True)
                        self._wake.set()
                        return True
                    return False
                if receipt.acceptance in {"rejected", "unsupported"}:
                    self._mark_terminal(record, receipt.acceptance, receipt.detail, at)
                    projected = "rejected" if receipt.acceptance == "rejected" else "unsupported"
                    self._resolve_record_future(record, self._receipt(record, projected))
                    self._release_head(record, consume_completion=False)
                    self._wake.set()
                    return True
                self._set_unknown_locked(
                    record,
                    receipt.detail or "adapter could not prove prompt acceptance",
                    receipt.vendor_correlation_id,
                )
                return False
            if not isinstance(result, SetResult):
                raise HarnessControlError("adapter setter result must be SetResult")
            self._validate_set_result(result, record.requested_value or "")
            record.detail = result.detail
            record.updated_at = at
            if result.acceptance == "unknown" and record.buffered_completion_sequence is not None:
                completed = replace(result, ok=True, acceptance="immediate")
                self._resolve_record_future(record, completed)
                self._mark_terminal(record, "delivered", result.detail, at)
                self._release_head(record, consume_completion=True)
                self._wake.set()
                return True
            self._resolve_record_future(record, result)
            if result.acceptance == "unknown":
                record.state = "unknown"
                return False
            terminal_state: SubmissionLifecycleState = (
                "unsupported" if result.acceptance == "unsupported" else "delivered"
            )
            self._mark_terminal(record, terminal_state, result.detail, at)
            self._release_head(record, consume_completion=False)
            self._wake.set()
            return True

    async def _certified_pre_send_busy(self, record: _OperationRecord, detail: str) -> None:
        async with self._lock:
            if self._active != record.key or record.state != "dispatching":
                raise HarnessControlError("pre-send busy result does not match active dispatch")
            record.state = "queued"
            record.detail = detail
            record.updated_at = self._clock()
            self._active = None
            if record.ref.kind == "prompt":
                self._resolve_record_future(record, self._receipt(record, "queued"))
            # Do not hot-loop. A direct adapter readiness event wakes the dispatcher.

    async def _incoherent_method_result(self, record: _OperationRecord, error: Exception) -> None:
        """Install an ambiguity barrier when post-call adapter evidence is incoherent.

        The adapter method has already returned, so the authority cannot certify that the
        operation was not applied.  Projecting rejection or silently dispatching the next
        operation would both be unsafe; operator resolution is required instead.
        """

        async with self._lock:
            if self._active != record.key or record.state != "dispatching":
                raise HarnessControlError(
                    "incoherent adapter result does not match active dispatch"
                ) from error
            self._set_unknown_locked(
                record,
                f"incoherent adapter {type(error).__name__}: {error}",
                None,
            )

    async def _possible_send_failure(
        self, record: _OperationRecord, detail: str, vendor_correlation_id: str | None
    ) -> None:
        async with self._lock:
            self._set_unknown_locked(record, detail, vendor_correlation_id)
            snapshot = self._snapshot()
            self._set_snapshot(
                replace(
                    snapshot,
                    control="disconnected",
                    activity="unknown",
                    acceptance="unknown",
                    raw={**snapshot.raw, "disconnect": detail},
                )
            )
            self._publish()

    def _set_unknown_locked(
        self, record: _OperationRecord, detail: str, vendor_correlation_id: str | None
    ) -> None:
        record.state = "unknown"
        record.detail = detail
        record.vendor_correlation_id = vendor_correlation_id
        record.updated_at = self._clock()
        if record.ref.kind == "prompt":
            self._resolve_record_future(record, self._receipt(record, "unknown"))
        elif record.requested_value is not None:
            self._resolve_record_future(
                record,
                SetResult(
                    ok=False,
                    acceptance="unknown",
                    requested_value=record.requested_value,
                    detail=detail,
                ),
            )

    def _known_reconciliation(self, record: _OperationRecord) -> ReconciliationResult | None:
        if record.state == "unknown":
            return None
        state: ReconciliationState
        if record.state in {"queued", "dispatching", "delivered"}:
            state = "accepted"
        elif record.state in {"withdrawn", "rejected"}:
            state = "rejected"
        else:
            state = "unsupported"
        return ReconciliationResult(
            request_id=record.ref.operation_id,
            state=state,
            reconciled_at=record.accepted_at or record.updated_at,
            vendor_correlation_id=record.vendor_correlation_id,
            detail=record.detail,
            bridge_epoch=self._bridge_epoch,
            submission_state=record.state,
        )

    def _duplicate_receipt(self, record: _OperationRecord) -> SubmissionReceipt:
        acceptance = {
            "queued": "queued",
            "dispatching": "unknown",
            "delivered": "immediate",
            "withdrawn": "rejected",
            "unknown": "unknown",
            "rejected": "rejected",
            "unsupported": "unsupported",
        }[record.state]
        detail = record.detail
        if record.state == "dispatching":
            detail = "dispatch is in flight; query status/reconcile with the same request id"
        elif record.state == "withdrawn":
            detail = "the queued submission was withdrawn and will not be dispatched"
        return self._receipt(record, acceptance, detail=detail)

    def _receipt(
        self,
        record: _OperationRecord,
        acceptance: str,
        *,
        detail: str | None = None,
    ) -> SubmissionReceipt:
        return SubmissionReceipt(
            request_id=record.ref.operation_id,
            acceptance=cast(AcceptanceState, acceptance),
            submitted_at=record.submitted_at,
            vendor_correlation_id=record.vendor_correlation_id,
            accepted_at=record.accepted_at,
            detail=record.detail if detail is None else detail,
            bridge_epoch=self._bridge_epoch,
        )

    def _status(self, record: _OperationRecord) -> SubmissionStatus:
        return SubmissionStatus(
            request_id=record.ref.operation_id,
            state=record.state,
            submitted_at=record.submitted_at,
            updated_at=record.updated_at,
            accepted_at=record.accepted_at,
            withdrawable=record.state == "queued",
            detail=record.detail,
        )

    def _mark_terminal(
        self,
        record: _OperationRecord,
        state: SubmissionLifecycleState,
        detail: str | None,
        at: str,
    ) -> None:
        record.state = state
        record.detail = detail
        record.updated_at = at
        if state == "delivered" and record.accepted_at is None:
            record.accepted_at = at
        # Terminal tombstones retain the digest and lifecycle metadata, never full prompt text.
        record.text = None

    def _release_head(self, record: _OperationRecord, *, consume_completion: bool) -> None:
        if self._active == record.key:
            self._active = None
        if self._timeline and self._timeline[0] == record.key:
            self._timeline.popleft()
        elif record.key in self._timeline:
            self._timeline.remove(record.key)
        if consume_completion:
            self._consumed_completions.append(record.ref)
        record.buffered_completion_sequence = None
        self._responded_interactions.clear()
        self._records.move_to_end(record.key)

    def _head_is_dispatchable(self, key: _OperationKey) -> bool:
        return (
            len(self._timeline) == 1
            and self._timeline[0] == key
            and self._active is None
            and self._snapshot_allows_dispatch()
        )

    def _snapshot_allows_dispatch(self) -> bool:
        snapshot = self._snapshot()
        return snapshot.control == "ready" and snapshot.activity == "idle"

    def _next_ref(self, kind: ControlOperationKind, operation_id: str) -> ControlOperationRef:
        self._sequence += 1
        return ControlOperationRef(
            bridge_epoch=self._bridge_epoch,
            sequence=self._sequence,
            operation_id=operation_id,
            kind=kind,
        )

    def _live_count(self) -> int:
        return sum(
            1 for record in self._records.values() if record.live or record.key == self._active
        )

    def _make_ledger_room(self) -> bool:
        while len(self._records) >= self._ledger_limit:
            evictable = next(
                (
                    key
                    for key, record in self._records.items()
                    if key != self._active
                    and key not in self._timeline
                    and record.state in {"delivered", "withdrawn", "rejected", "unsupported"}
                ),
                None,
            )
            if evictable is None:
                return False
            record = self._records.pop(evictable)
            if record.ref.kind == "prompt":
                self._prompt_ids.pop(record.ref.operation_id, None)
        return True

    def _require_epoch(self, expected: str | None) -> None:
        if expected is not None and expected != self._bridge_epoch:
            raise HarnessBridgeEpochMismatchError(expected, self._bridge_epoch)

    def _require_accepting(self) -> None:
        if not self._accepting:
            raise HarnessControlError("submission authority is stopped")

    @staticmethod
    def _payload_digest(text: str) -> str:
        return sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _resolve_record_future(record: _OperationRecord, value: object) -> None:
        future = record.result_future
        if future is not None and not future.done():
            future.set_result(value)

    @staticmethod
    def _validate_set_result(result: SetResult, requested_value: str) -> None:
        if result.requested_value != requested_value:
            raise HarnessControlError("adapter set result does not match requested value")
        if result.acceptance not in SET_ACCEPTANCE_VALUES:
            raise HarnessControlError("adapter set result has invalid acceptance")
        if result.acceptance == "echo-verified":
            if not result.ok or result.effective_value is None:
                raise HarnessControlError("echo-verified setter requires effective evidence")
        elif result.acceptance in {"immediate", "queued"}:
            if not result.ok or result.effective_value is not None:
                raise HarnessControlError("accepted setter result has incoherent evidence")
        elif result.ok or result.effective_value is not None:
            raise HarnessControlError("failed setter result cannot claim effect")


def ref_key(ref: ControlOperationRef) -> _OperationKey:
    return (ref.kind, ref.operation_id)
