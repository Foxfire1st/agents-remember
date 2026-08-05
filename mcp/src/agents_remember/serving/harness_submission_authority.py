"""Server-authoritative ordinary-operation timeline for one hosted harness bridge.

The authority is the only prompt FIFO.  It linearizes withdrawal against dispatch, keeps setters
in the same monotonic order, and pins an accepted ordinary operation until an exact correlated
completion releases it.  Adapters are dispatch-now vendor boundaries; they never own a second
prompt queue.

It owns the ORDER -- the dispatch timeline, the pinned active operation, the adapter conversation.
It does not own the rows: the retained records and every read that answers for a request id live on
``self.ledger`` (``harness_submission_ledger``), which shares this class's lock so a status read and
the dispatcher never see different rows.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
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
    HarnessInteractionNotPendingError,
    HarnessRequestConflictError,
)
from agents_remember.serving.harness_capabilities import SET_ACCEPTANCE_VALUES, SetResult
from agents_remember.serving.harness_control_adapter import (
    AssetSubmitCapable,
    HarnessProtocolAdapter,
)
from agents_remember.serving.harness_control_models import (
    AdapterEvent,
    AdapterSnapshot,
    AssetReference,
    ControlOperationKind,
    ControlOperationRef,
    InteractionResponse,
    PromptRequest,
    ReconciliationResult,
    ReconciliationState,
    SubmissionLifecycleState,
    SubmissionReceipt,
    SubmissionSource,
    WithdrawalRecovery,
    WithdrawalResult,
)
from agents_remember.serving.harness_submission_ledger import (
    OperationKey,
    OperationRecord,
    SubmissionLedger,
    ref_key,
)

Clock = Callable[[], str]
SnapshotGetter = Callable[[], AdapterSnapshot]
SnapshotSetter = Callable[[AdapterSnapshot], None]
Publisher = Callable[[], None]
OperationResolution = Literal["applied", "not-applied"]

DISPATCH_ACCEPTANCE_GRACE_SECONDS = 0.5
"""Bound the synchronous wait for vendor acceptance evidence on a dispatchable submit head.

Claude's replay echo (the proof behind acceptance="immediate") is emitted with
the turn's first output, so it ARRIVES at vendor TTFT -- measured ~4.25s live (FIONREAD on the
adapter pipe: claude's stdout stays empty from prompt dispatch until first-token time) -- while its
frame timestamp suggests ~22ms. Waiting for it held every dashboard submit POST for the full TTFT.
Inside the grace a fast adapter keeps its synchronous immediate receipt and the instant busy /
synchronous-rejection paths are untouched; past it the submit answers "queued" honestly, the record
stays live, and status/reconcile project acceptance when the echo lands. "immediate" is still only
minted on verified acceptance evidence."""


def _consume_future_exception(future: asyncio.Future[object]) -> None:
    """Retrieve a late exception on a future whose bounded waiter already returned."""

    if not future.cancelled():
        future.exception()


@dataclass(frozen=True)
class BridgeSnapshotPort:
    """How a bridge sub-component reads, replaces, publishes and timestamps the ONE snapshot.

    Every component under a bridge shares a single snapshot: reading it through one accessor while
    replacing it through another component's setter would publish transitions nobody else sees.
    The clock rides along because every published transition is stamped by the same one.
    """

    clock: Clock
    snapshot: SnapshotGetter
    set_snapshot: SnapshotSetter
    publish: Publisher


@dataclass(frozen=True)
class SubmissionLimits:
    """The bounds of one submission authority: how long its timeline is, how long its ledger is.

    They are one bound, not two: the ledger retains settled records the timeline has already
    dropped, so a ledger shorter than its timeline cannot answer for the operations still in it --
    which is why the constructor refuses that combination rather than accepting two free numbers.
    """

    timeline: int
    ledger: int
    dispatch_grace_seconds: float = DISPATCH_ACCEPTANCE_GRACE_SECONDS


class HarnessSubmissionAuthority:
    """Own one epoch-bound, bounded prompt/setter timeline above a protocol adapter."""

    def __init__(
        self,
        adapter: HarnessProtocolAdapter,
        port: BridgeSnapshotPort,
        limits: SubmissionLimits,
        *,
        bridge_epoch: str | None = None,
    ) -> None:
        timeline_limit, ledger_limit = limits.timeline, limits.ledger
        dispatch_grace_seconds = limits.dispatch_grace_seconds
        if timeline_limit < 1 or ledger_limit < 1:
            raise HarnessControlError("submission authority limits must be positive")
        if ledger_limit < timeline_limit:
            raise HarnessControlError("submission ledger limit cannot be below timeline limit")
        if dispatch_grace_seconds <= 0:
            raise HarnessControlError("submission authority dispatch grace must be positive")
        self._adapter = adapter
        self._timeline_limit = timeline_limit
        self._dispatch_grace_seconds = dispatch_grace_seconds
        self._clock = port.clock
        self._snapshot = port.snapshot
        self._set_snapshot = port.set_snapshot
        self._publish = port.publish
        self._bridge_epoch = bridge_epoch or uuid4().hex
        self._lock = asyncio.Lock()
        self.ledger = SubmissionLedger(
            bridge_epoch=self._bridge_epoch, limit=ledger_limit, lock=self._lock
        )
        self._timeline: deque[OperationKey] = deque()
        self._active: OperationKey | None = None
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
    def active_operation(self) -> ControlOperationRef | None:
        record = self.ledger.by_key(self._active) if self._active is not None else None
        return record.ref if record is not None else None

    def start(self) -> None:
        if self._dispatcher is not None:
            raise HarnessControlError("submission authority is already started")
        self._accepting = True
        self._dispatcher = asyncio.create_task(self._run_dispatcher())

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self._require_accepting()
        self._require_epoch(request.expected_bridge_epoch)
        digest = self._payload_digest(request.text, request.assets)
        future: asyncio.Future[object] | None = None
        async with self._lock:
            settled = self._pre_admission_receipt_locked(request, digest)
            if settled is not None:
                return settled
            record = self._enrol_prompt_locked(request, digest)
            unsupported = self._unsupported_prompt_locked(record, request)
            if unsupported is not None:
                return unsupported
            self._timeline.append(record.key)
            if not self._head_is_dispatchable(record.key):
                self._wake.set()
                return record.receipt("queued", bridge_epoch=self._bridge_epoch)
            future = asyncio.get_running_loop().create_future()
            record.result_future = future
            self._wake.set()
        # An idle adapter keeps its synchronous immediate receipt when acceptance evidence lands
        # inside the dispatch grace; a later prompt returns queued immediately instead of waiting
        # behind the active vendor operation.  Past the grace the honest synchronous answer is
        # "queued" -- slow vendor evidence (Claude's echo flushes at turn TTFT) must not hold the
        # submit response.  The record stays live and status/reconcile project the outcome.
        assert future is not None
        try:
            return cast(
                SubmissionReceipt,
                await asyncio.wait_for(
                    asyncio.shield(future), timeout=self._dispatch_grace_seconds
                ),
            )
        except TimeoutError:
            # Nobody waits on the record's future now; consume a late exception (authority stop
            # during dispatch) instead of leaking an unretrieved-future warning.
            future.add_done_callback(_consume_future_exception)
            async with self._lock:
                return record.pending_dispatch_receipt(self._bridge_epoch)

    def _pre_admission_receipt_locked(
        self, request: PromptRequest, digest: str
    ) -> SubmissionReceipt | None:
        """The receipt this request is already owed before any record exists, if any.

        Re-submitting a request id is idempotent only when the source and payload are the first
        submission's; a different one under the same id is a conflict, not a second operation. A
        ledger with no room for a live operation rejects rather than dropping something live.
        """

        prior = self.ledger.by_request_id(request.request_id)
        if prior is not None:
            if prior.source != request.source or prior.payload_digest != digest:
                raise HarnessRequestConflictError(
                    f"request id {request.request_id!r} already belongs to its first source/payload"
                )
            self.ledger.touch(prior)
            return prior.duplicate_receipt(self._bridge_epoch)
        if not self._has_admission_room():
            return SubmissionReceipt(
                request_id=request.request_id,
                acceptance="rejected",
                submitted_at=request.submitted_at,
                detail="submission ledger is full of live or unresolved operations",
                bridge_epoch=self._bridge_epoch,
            )
        return None

    def _enrol_prompt_locked(self, request: PromptRequest, digest: str) -> OperationRecord:
        """Register one admitted prompt as a queued record, before it reaches the timeline."""

        record = OperationRecord(
            ref=self.ledger.next_ref("prompt", request.request_id),
            state="queued",
            submitted_at=request.submitted_at,
            updated_at=request.submitted_at,
            source=request.source,
            payload_digest=digest,
            text=request.text,
            detail="queued inside the authoritative control bridge",
            assets=request.assets,
        )
        self.ledger.enrol(record)
        return record

    def _unsupported_prompt_locked(
        self, record: OperationRecord, request: PromptRequest
    ) -> SubmissionReceipt | None:
        """Retire a prompt this hosted harness cannot carry at all, before it is ever queued.

        The record is still created and marked terminal so the ledger can answer for the request
        id; what it never does is join the dispatch timeline.

        This is the SINGLE place capability is decided. It runs under the same lock that enrols the
        record, against the one adapter the authority was constructed with, so the answer it gives
        is the answer that still holds at dispatch (:meth:`_invoke_adapter` only asserts it). A
        refusal here is clean and terminal -- an ``unsupported`` receipt, the session untouched --
        whereas refusing at dispatch could only produce an ``unknown`` ambiguity barrier, because
        by then the authority can no longer say whether bytes crossed the wire.
        """

        if self._snapshot().control == "unsupported":
            record.mark_terminal(
                "unsupported",
                "hosted harness does not support native prompt control",
                request.submitted_at,
            )
            return record.receipt("unsupported", bridge_epoch=self._bridge_epoch)
        if request.assets and not isinstance(self._adapter, AssetSubmitCapable):
            record.mark_terminal(
                "unsupported",
                "hosted harness does not support asset submissions",
                request.submitted_at,
            )
            return record.receipt("unsupported", bridge_epoch=self._bridge_epoch)
        return None

    async def set_model(self, model_key: str) -> SetResult:
        return await self._admit_setter("set-model", model_key)

    async def set_effort(self, effort: str) -> SetResult:
        return await self._admit_setter("set-effort", effort)

    async def respond(self, response: InteractionResponse) -> AdapterSnapshot:
        self._require_accepting()
        async with self._lock:
            snapshot_now = self._snapshot()
            pending = snapshot_now.pending_interaction
            active = self.ledger.by_key(self._active) if self._active is not None else None
            # Parent-ness is decided by the entry's own thread, not by which slot
            # carries it: concurrent parent pendings beyond the singular slot's
            # oldest ride the plural tuple (the adapter keeps a per-thread pending
            # map), so a tuple entry with the parent's threadId gets the
            # active-operation guard exactly like the singular slot.
            matched_entry = next(
                (
                    entry
                    for entry in snapshot_now.pending_interactions
                    if entry.interaction_id == response.interaction_id
                ),
                None,
            )
            singular_match = (
                pending is not None and pending.interaction_id == response.interaction_id
            )
            is_parent = singular_match or (
                matched_entry is not None
                and matched_entry.raw.get("threadId") == snapshot_now.vendor_session_id
            )
            if not singular_match and matched_entry is None:
                raise HarnessInteractionNotPendingError(
                    "interaction response does not match the pending interaction"
                )
            operation: ControlOperationRef | None = None
            if is_parent:
                if active is None or active.state not in {"dispatching", "delivered", "unknown"}:
                    raise HarnessInteractionNotPendingError(
                        "interaction response has no active ordinary operation"
                    )
                operation = active.ref
            if response.interaction_id in self._responded_interactions:
                raise HarnessInteractionNotPendingError(
                    "interaction response was already submitted"
                )
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
            record = self.ledger.by_request_id(request_id)
            if record is None:
                raise HarnessControlError(
                    f"submission {request_id!r} is no longer retained for reconciliation"
                )
            known = record.reconciliation(self._bridge_epoch)
            if known is not None:
                self.ledger.touch(record)
                return known
            ref = record.ref
        # Reconciliation queries only the already-active ambiguous operation.  It never admits or
        # dispatches a second ordinary operation while the unknown barrier is installed.
        result = await self._adapter.reconcile(request_id)
        if result.request_id != request_id:
            raise HarnessControlError("adapter reconciliation request id does not match")
        async with self._lock:
            current = self.ledger.by_key(ref_key(ref))
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
                current.mark_terminal("rejected", result.detail, result.reconciled_at)
                self._release_head(current, consume_completion=False)
            elif result.state == "unsupported":
                current.mark_terminal("unsupported", result.detail, result.reconciled_at)
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
            record = self.ledger.by_key((operation_kind, operation_id))
            if record is None or record.state != "unknown":
                raise HarnessControlError("only an unknown operation can be operator-resolved")
            record.mark_terminal(
                "delivered" if resolution == "applied" else "rejected",
                detail,
                self._clock(),
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

    async def withdraw(
        self,
        expected_bridge_epoch: str,
        request_id: str,
        *,
        cockpit_only: bool,
    ) -> WithdrawalResult:
        self._require_epoch(expected_bridge_epoch)
        async with self._lock:
            record = self.ledger.by_request_id(request_id)
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
            # The exact body crosses once, inside this response, before the tombstone fires.
            recovery = WithdrawalRecovery(text=record.text, assets=record.assets)
            record.mark_terminal("withdrawn", "queued submission was withdrawn", at)
            record.resolve(record.receipt("rejected", bridge_epoch=self._bridge_epoch))
            self._wake.set()
            return WithdrawalResult(
                request_id=request_id,
                outcome="withdrawn",
                state="withdrawn",
                withdrawn_at=at,
                detail=record.detail,
                recovery=recovery,
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
            active = self.ledger.by_key(self._active) if self._active is not None else None
            if active is None or active.ref != ref:
                raise HarnessControlError("adapter completion does not match the active operation")
            if active.state == "dispatching":
                if active.buffered_completion_sequence is not None:
                    raise HarnessControlError("operation already has a buffered completion")
                active.buffered_completion_sequence = event.sequence
                return
            if active.state not in {"delivered", "unknown"}:
                raise HarnessControlError("active operation is not completion-eligible")
            active.mark_terminal("delivered", active.detail, event.created_at)
            self._release_head(active, consume_completion=True)
            self._wake.set()

    def notify_snapshot_updated(self) -> None:
        """Wake dispatch after the bridge installs direct readiness/completion snapshot evidence."""

        self._wake.set()

    async def stop(self, *, forced: bool) -> None:
        self._accepting = False
        if not forced:
            async with self._lock:
                active = self.ledger.by_key(self._active) if self._active is not None else None
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
            for record in self.ledger.records():
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
            if not self._has_admission_room():
                return SetResult(
                    ok=False,
                    acceptance="unknown",
                    requested_value=requested_value,
                    detail="operation ledger is full of live or unresolved operations",
                )
            future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
            at = self._clock()
            record = OperationRecord(
                ref=self.ledger.next_ref(kind),
                state="queued",
                submitted_at=at,
                updated_at=at,
                requested_value=requested_value,
                detail="queued in the authoritative ordinary-operation timeline",
                result_future=future,
            )
            self.ledger.enrol(record)
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
        """Advance the dispatch timeline by at most one operation.

        Returns whether the dispatcher should look at the head again right away rather than wait
        for a wake: either the head was released, or the claim went stale and the new head has not
        been examined yet.
        """

        async with self._lock:
            record = self._dispatchable_head_locked()
        if record is None:
            return False
        async with self._ordinary_lane:
            if await self._preflight_declined(record):
                return False
            async with self._lock:
                if not self._claim_head_locked(record):
                    return True
            return await self._send_and_settle(record)

    def _dispatchable_head_locked(self) -> OperationRecord | None:
        """The queued head this step may dispatch, or ``None`` when there is nothing to do."""

        if not self._timeline or self._active is not None:
            return None
        record = self.ledger.by_key(self._timeline[0])
        assert record is not None
        if record.state != "queued" or not self._snapshot_allows_dispatch():
            return None
        return record

    async def _preflight_declined(self, record: OperationRecord) -> bool:
        """Ask the adapter whether it can take the operation; ``True`` means stand down.

        A preflight sends no operation bytes, so a busy or not-yet-connected adapter simply leaves
        the record queued for a later step. An adapter that nonetheless claims it may have sent
        something is treated as unknown -- certifying a requeue on that claim could deliver twice.
        """

        try:
            await self._adapter_preflight(record)
        except (HarnessAdapterBusyError, HarnessAdapterDisconnectedError) as exc:
            if isinstance(exc, HarnessAdapterDisconnectedError) and exc.may_have_sent:
                await self._unknown_after_preflight_claim(record, exc)
                return True
            async with self._lock:
                record.detail = str(exc)
                record.updated_at = self._clock()
                if record.ref.kind == "prompt":
                    record.resolve(record.receipt("queued", bridge_epoch=self._bridge_epoch))
            return True
        return False

    async def _unknown_after_preflight_claim(
        self, record: OperationRecord, exc: HarnessAdapterDisconnectedError
    ) -> None:
        """Install the ambiguity barrier for a preflight that claims it may have sent bytes."""

        async with self._lock:
            self._active = record.key
            self._mark_unknown_locked(record, str(exc), exc.vendor_correlation_id)
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

    def _claim_head_locked(self, record: OperationRecord) -> bool:
        """Re-verify the head under the lock and mark it dispatching; ``False`` = the world moved.

        The preflight ran outside the lock, so the timeline, the bridge epoch and the snapshot may
        all have changed since this record was chosen. Anything that moved voids the claim.
        """

        if (
            not self._timeline
            or self._timeline[0] != record.key
            or self._active is not None
            or record.state != "queued"
            or record.ref.bridge_epoch != self._bridge_epoch
            or not self._snapshot_allows_dispatch()
        ):
            return False
        record.state = "dispatching"
        record.updated_at = self._clock()
        self._active = record.key
        return True

    async def _send_and_settle(self, record: OperationRecord) -> bool:
        """Issue the claimed operation and apply whatever came back.

        Every failure mode here turns on what the adapter can certify. A busy adapter, or a
        disconnect it proves happened before the write, is safely requeued. A disconnect that may
        have sent, any other exception, and a result the authority cannot read coherently all
        install the ambiguity barrier instead of guessing which side of the wire the bytes are on.
        """

        try:
            result = await self._invoke_adapter(record)
        except HarnessAdapterBusyError as exc:
            await self._certified_pre_send_busy(record, str(exc))
            return False
        except HarnessAdapterDisconnectedError as exc:
            if exc.may_have_sent:
                await self._possible_send_failure(record, str(exc), exc.vendor_correlation_id)
            else:
                await self._certified_pre_send_busy(record, str(exc))
            return False
        except Exception as exc:
            await self._possible_send_failure(record, str(exc), None)
            return False
        try:
            return await self._apply_method_result(record, result)
        except Exception as exc:
            await self._incoherent_method_result(record, exc)
            return False

    async def _invoke_adapter(self, record: OperationRecord) -> object:
        """Call the adapter method this operation names, taking the asset path when it carries any.

        Whether this hosted harness can carry assets at all is decided ONCE, at admission, by
        :meth:`_unsupported_prompt_locked`: a prompt carrying assets against an adapter that is not
        :class:`AssetSubmitCapable` is retired ``unsupported`` under the same lock that enrols it,
        and never joins the dispatch timeline. ``self._adapter`` is the object handed to
        ``__init__`` and is never rebound -- reconnect (Codex, pi) replaces the transport under a
        fixed adapter, and no adapter in this tree binds or drops ``submit_with_assets`` at
        runtime -- so an asset dispatch arriving here is one admission already approved.
        """

        if record.ref.kind != "prompt":
            assert record.requested_value is not None
            return await self._adapter_setter(record.ref.kind, record)
        assert record.text is not None and record.source is not None
        request = PromptRequest(
            request_id=record.ref.operation_id,
            source=cast(SubmissionSource, record.source),
            text=record.text,
            submitted_at=record.submitted_at,
            operation=record.ref,
            assets=record.assets,
        )
        if not record.assets:
            return await self._adapter.submit(request)
        capable = self._adapter
        assert isinstance(capable, AssetSubmitCapable)
        return await capable.submit_with_assets(request)

    async def _adapter_preflight(self, record: OperationRecord) -> None:
        await self._adapter.preflight_operation(record.ref)

    async def _adapter_setter(
        self, kind: ControlOperationKind, record: OperationRecord
    ) -> SetResult:
        assert record.requested_value is not None
        method = self._adapter.set_model if kind == "set-model" else self._adapter.set_effort
        return await method(record.requested_value, operation=record.ref)

    async def _apply_method_result(self, record: OperationRecord, result: object) -> bool:
        async with self._lock:
            current = self.ledger.by_key(record.key)
            if current is not record or self._active != record.key:
                raise HarnessControlError("ordinary operation changed while adapter method ran")
            at = self._clock()
            if record.ref.kind == "prompt":
                return self._apply_prompt_receipt_locked(record, result, at=at)
            return self._apply_set_result_locked(record, result, at=at)

    def _verified_prompt_receipt(
        self, record: OperationRecord, result: object
    ) -> SubmissionReceipt:
        """The adapter's result read as a receipt for exactly this operation, or a control error."""

        if not isinstance(result, SubmissionReceipt):
            raise HarnessControlError("adapter prompt result must be a submission receipt")
        if result.request_id != record.ref.operation_id:
            raise HarnessControlError("adapter receipt request id does not match operation")
        return result

    def _accept_prompt_locked(
        self, record: OperationRecord, receipt: SubmissionReceipt, *, at: str
    ) -> None:
        """Record the acceptance the submitter is owed, without deciding the turn is over."""

        record.state = "delivered"
        record.accepted_at = receipt.accepted_at or at
        record.resolve(record.receipt("immediate", bridge_epoch=self._bridge_epoch))

    def _complete_delivered_locked(self, record: OperationRecord, *, at: str) -> None:
        """Retire a delivered prompt whose turn is also over and release the head."""

        record.mark_terminal("delivered", record.detail, at)
        self._release_head(record, consume_completion=True)
        self._wake.set()

    def _apply_prompt_receipt_locked(
        self, record: OperationRecord, result: object, *, at: str
    ) -> bool:
        """Apply one adapter submission receipt to the head. Returns whether the head was released.

        ``immediate`` settles the submitter's receipt but only releases the head once the turn is
        also known to be over -- a buffered completion, or the adapter's own ``terminalCompletion``
        stamp. ``unknown`` with a buffered completion is that same evidence arriving in the other
        order and settles identically; ``unknown`` without one cannot be certified in either
        direction and installs the ambiguity barrier.
        """

        receipt = self._verified_prompt_receipt(record, result)
        if receipt.acceptance == "queued":
            # A vendor/adaptor queue escaped the common authority after dispatch crossed.
            self._mark_unknown_locked(
                record,
                "adapter returned forbidden queued acceptance after dispatch",
                receipt.vendor_correlation_id,
            )
            return False
        record.vendor_correlation_id = receipt.vendor_correlation_id
        record.detail = receipt.detail
        record.updated_at = at
        if receipt.acceptance == "unknown" and record.buffered_completion_sequence is not None:
            self._accept_prompt_locked(record, receipt, at=at)
            self._complete_delivered_locked(record, at=at)
            return True
        if receipt.acceptance == "immediate":
            self._accept_prompt_locked(record, receipt, at=at)
            if record.buffered_completion_sequence is None and not receipt.raw.get(
                "terminalCompletion"
            ):
                return False
            self._complete_delivered_locked(record, at=at)
            return True
        if receipt.acceptance in {"rejected", "unsupported"}:
            record.mark_terminal(receipt.acceptance, receipt.detail, at)
            projected = "rejected" if receipt.acceptance == "rejected" else "unsupported"
            record.resolve(record.receipt(projected, bridge_epoch=self._bridge_epoch))
            self._release_head(record, consume_completion=False)
            self._wake.set()
            return True
        self._mark_unknown_locked(
            record,
            receipt.detail or "adapter could not prove prompt acceptance",
            receipt.vendor_correlation_id,
        )
        return False

    def _apply_set_result_locked(self, record: OperationRecord, result: object, *, at: str) -> bool:
        """Apply one adapter setter result to the head. Returns whether the head was released.

        A setter has no later acceptance echo to wait for, so it is terminal the moment it returns
        -- unless the adapter reports ``unknown`` with no buffered completion to disambiguate it.
        """

        if not isinstance(result, SetResult):
            raise HarnessControlError("adapter setter result must be SetResult")
        self._validate_set_result(result, record.requested_value or "")
        record.detail = result.detail
        record.updated_at = at
        if result.acceptance == "unknown" and record.buffered_completion_sequence is not None:
            record.resolve(replace(result, ok=True, acceptance="immediate"))
            self._complete_delivered_locked(record, at=at)
            return True
        record.resolve(result)
        if result.acceptance == "unknown":
            record.state = "unknown"
            return False
        terminal_state: SubmissionLifecycleState = (
            "unsupported" if result.acceptance == "unsupported" else "delivered"
        )
        record.mark_terminal(terminal_state, result.detail, at)
        self._release_head(record, consume_completion=False)
        self._wake.set()
        return True

    async def _certified_pre_send_busy(self, record: OperationRecord, detail: str) -> None:
        async with self._lock:
            if self._active != record.key or record.state != "dispatching":
                raise HarnessControlError("pre-send busy result does not match active dispatch")
            record.state = "queued"
            record.detail = detail
            record.updated_at = self._clock()
            self._active = None
            if record.ref.kind == "prompt":
                record.resolve(record.receipt("queued", bridge_epoch=self._bridge_epoch))
            # Do not hot-loop. A direct adapter readiness event wakes the dispatcher.

    async def _incoherent_method_result(self, record: OperationRecord, error: Exception) -> None:
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
            self._mark_unknown_locked(
                record,
                f"incoherent adapter {type(error).__name__}: {error}",
                None,
            )

    async def _possible_send_failure(
        self, record: OperationRecord, detail: str, vendor_correlation_id: str | None
    ) -> None:
        async with self._lock:
            self._mark_unknown_locked(record, detail, vendor_correlation_id)
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

    def _mark_unknown_locked(
        self, record: OperationRecord, detail: str, vendor_correlation_id: str | None
    ) -> None:
        record.mark_unknown(
            detail,
            vendor_correlation_id,
            at=self._clock(),
            bridge_epoch=self._bridge_epoch,
        )

    def _release_head(self, record: OperationRecord, *, consume_completion: bool) -> None:
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
        self.ledger.touch(record)

    def _head_is_dispatchable(self, key: OperationKey) -> bool:
        return (
            len(self._timeline) == 1
            and self._timeline[0] == key
            and self._active is None
            and self._snapshot_allows_dispatch()
        )

    def _snapshot_allows_dispatch(self) -> bool:
        snapshot = self._snapshot()
        return snapshot.control == "ready" and snapshot.activity == "idle"

    def _has_admission_room(self) -> bool:
        """Whether one more operation fits: a free timeline slot AND a ledger row to spend."""

        return self.ledger.live_count(
            self._active
        ) < self._timeline_limit and self.ledger.make_room(self._pinned)

    def _pinned(self, key: OperationKey) -> bool:
        """Whether dispatch still needs this row, which is what eviction must never take."""

        return key == self._active or key in self._timeline

    def _require_epoch(self, expected: str | None) -> None:
        if expected is not None and expected != self._bridge_epoch:
            raise HarnessBridgeEpochMismatchError(expected, self._bridge_epoch)

    def _require_accepting(self) -> None:
        if not self._accepting:
            raise HarnessControlError("submission authority is stopped")

    @staticmethod
    def _payload_digest(text: str, assets: tuple[AssetReference, ...] = ()) -> str:
        """Idempotence identity: text only when no assets ride (byte-identical to before)."""

        if not assets:
            return sha256(text.encode("utf-8")).hexdigest()
        canonical = json.dumps(
            {
                "text": text,
                "assets": [
                    {
                        "assetId": asset.asset_id,
                        "mimeType": asset.mime_type,
                        "byteSize": asset.byte_size,
                        "sha256": asset.sha256,
                    }
                    for asset in sorted(assets, key=lambda item: item.asset_id)
                ],
            },
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

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
