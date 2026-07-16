"""Bounded ordered commands and ambiguous-submission ledger for a harness bridge."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TypeVar

from agents_remember.errors import HarnessAdapterDisconnectedError, HarnessControlError
from agents_remember.serving.harness_capabilities import SET_ACCEPTANCE_VALUES, SetResult
from agents_remember.serving.harness_control_adapter import HarnessProtocolAdapter
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    InteractionResponse,
    PromptRequest,
    ReconciliationResult,
    ReconciliationState,
    SubmissionReceipt,
)

Clock = Callable[[], str]
SnapshotGetter = Callable[[], AdapterSnapshot]
SnapshotSetter = Callable[[AdapterSnapshot], None]
Publisher = Callable[[], None]
T = TypeVar("T")


@dataclass
class _SubmitCommand:
    request: PromptRequest
    future: asyncio.Future[SubmissionReceipt]


@dataclass
class _RespondCommand:
    response: InteractionResponse
    future: asyncio.Future[AdapterSnapshot]


@dataclass
class _ReconcileCommand:
    request_id: str
    future: asyncio.Future[ReconciliationResult]


@dataclass
class _ResolveCommand:
    request_id: str
    state: ReconciliationState
    detail: str
    future: asyncio.Future[ReconciliationResult]


@dataclass
class _SetModelCommand:
    model_key: str
    future: asyncio.Future[SetResult]


@dataclass
class _SetEffortCommand:
    effort: str
    future: asyncio.Future[SetResult]


@dataclass
class _StopCommand:
    future: asyncio.Future[None]


class HarnessControlQueue:
    """Serialize every control mutation and retain a bounded ambiguous-send ledger."""

    def __init__(
        self,
        adapter: HarnessProtocolAdapter,
        *,
        queue_limit: int,
        submission_limit: int,
        clock: Clock,
        snapshot: SnapshotGetter,
        set_snapshot: SnapshotSetter,
        publish: Publisher,
    ) -> None:
        self._adapter = adapter
        self._commands: asyncio.Queue[
            _SubmitCommand
            | _RespondCommand
            | _ReconcileCommand
            | _ResolveCommand
            | _SetModelCommand
            | _SetEffortCommand
            | _StopCommand
        ] = asyncio.Queue(maxsize=queue_limit)
        self._submissions: OrderedDict[str, SubmissionReceipt] = OrderedDict()
        self._pending_submissions: set[str] = set()
        self._submission_futures: dict[str, asyncio.Future[SubmissionReceipt]] = {}
        self._submission_limit = submission_limit
        self._clock = clock
        self._snapshot = snapshot
        self._set_snapshot = set_snapshot
        self._publish = publish
        self._task: asyncio.Task[None] | None = None
        self._accepting = False

    def start(self) -> None:
        if self._task is not None:
            raise HarnessControlError("control command queue is already started")
        self._accepting = True
        self._task = asyncio.create_task(self._run())

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self._require_accepting()
        retained = self._submissions.get(request.request_id)
        if retained is not None:
            pending = self._submission_futures.get(request.request_id)
            if pending is not None:
                # requestId is the idempotency key. A retry waits for the first command's result;
                # it never changes or resubmits the original whole-message payload.
                return await asyncio.shield(pending)
            return retained
        if not self._reserve(request):
            return SubmissionReceipt(
                request_id=request.request_id,
                acceptance="rejected",
                submitted_at=request.submitted_at,
                detail="submission ledger is full of unresolved ambiguous sends",
            )
        future = asyncio.get_running_loop().create_future()
        self._submission_futures[request.request_id] = future
        try:
            self._commands.put_nowait(_SubmitCommand(request=request, future=future))
        except asyncio.QueueFull:
            self._submissions.pop(request.request_id, None)
            self._pending_submissions.discard(request.request_id)
            self._submission_futures.pop(request.request_id, None)
            return SubmissionReceipt(
                request_id=request.request_id,
                acceptance="rejected",
                submitted_at=request.submitted_at,
                detail="control bridge input queue is full",
            )
        return await asyncio.shield(future)

    async def respond(self, response: InteractionResponse) -> AdapterSnapshot:
        pending = self._snapshot().pending_interaction
        if pending is None or pending.interaction_id != response.interaction_id:
            raise HarnessControlError("interaction response does not match the pending interaction")
        future = asyncio.get_running_loop().create_future()
        self._put(_RespondCommand(response=response, future=future))
        return await future

    async def reconcile(self, request_id: str) -> ReconciliationResult:
        future = asyncio.get_running_loop().create_future()
        self._put(_ReconcileCommand(request_id=request_id, future=future))
        return await future

    async def resolve_unknown(
        self,
        request_id: str,
        *,
        state: ReconciliationState,
        detail: str,
    ) -> ReconciliationResult:
        if state not in {"accepted", "rejected"}:
            raise HarnessControlError("operator resolution must be accepted or rejected")
        future = asyncio.get_running_loop().create_future()
        self._put(_ResolveCommand(request_id=request_id, state=state, detail=detail, future=future))
        return await future

    async def set_model(self, model_key: str) -> SetResult:
        future = asyncio.get_running_loop().create_future()
        self._put(_SetModelCommand(model_key=model_key, future=future))
        return await future

    async def set_effort(self, effort: str) -> SetResult:
        future = asyncio.get_running_loop().create_future()
        self._put(_SetEffortCommand(effort=effort, future=future))
        return await future

    async def graceful_stop(self) -> None:
        self._require_accepting()
        self._accepting = False
        future = asyncio.get_running_loop().create_future()
        await self._commands.put(_StopCommand(future=future))
        await future
        if self._task is not None:
            await self._task

    async def force_stop(self) -> None:
        self._accepting = False
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        error = HarnessControlError("control bridge was force-stopped")
        try:
            await self._adapter.stop("forced")
        except Exception as exc:
            error = self._unexpected_adapter_error(exc)
            self._mark_failed(error)
            self._drain(error)
            raise error from exc
        else:
            self._set_snapshot(
                replace(
                    self._snapshot(),
                    control="disconnected",
                    activity="unknown",
                    acceptance="rejected",
                )
            )
            self._publish()
            self._drain(error)

    @property
    def retained_submission_count(self) -> int:
        return len(self._submissions)

    def _put(
        self,
        command: (
            _RespondCommand
            | _ReconcileCommand
            | _ResolveCommand
            | _SetModelCommand
            | _SetEffortCommand
        ),
    ) -> None:
        self._require_accepting()
        try:
            self._commands.put_nowait(command)
        except asyncio.QueueFull as exc:
            raise HarnessControlError("control bridge input queue is full") from exc

    def _require_unknown(self, request_id: str, *, action: str) -> None:
        receipt = self._submissions.get(request_id)
        if receipt is None or receipt.acceptance != "unknown":
            raise HarnessControlError(f"only an unknown submission can be {action}")

    def _require_accepting(self) -> None:
        if not self._accepting:
            raise HarnessControlError("control command queue is stopped")

    def _reserve(self, request: PromptRequest) -> bool:
        if len(self._submissions) >= self._submission_limit:
            evictable = next(
                (
                    request_id
                    for request_id, receipt in self._submissions.items()
                    if request_id not in self._pending_submissions
                    and receipt.acceptance != "unknown"
                ),
                None,
            )
            if evictable is None:
                return False
            self._submissions.pop(evictable)
        self._submissions[request.request_id] = SubmissionReceipt(
            request_id=request.request_id,
            acceptance="queued",
            submitted_at=request.submitted_at,
            detail="queued inside the local control bridge",
        )
        self._pending_submissions.add(request.request_id)
        return True

    def _remember(self, receipt: SubmissionReceipt) -> None:
        self._submissions[receipt.request_id] = receipt
        self._submissions.move_to_end(receipt.request_id)

    async def _run(self) -> None:
        exit_error = HarnessControlError("control command queue stopped")
        try:
            while True:
                command = await self._commands.get()
                try:
                    if await self._execute_command(command):
                        return
                except asyncio.CancelledError:
                    exit_error = HarnessControlError("control command queue was cancelled")
                    self._fail_command(command, exit_error, ambiguous=True)
                    raise
                except HarnessControlError as exc:
                    self._fail_command(command, exc)
                    if isinstance(command, _StopCommand):
                        exit_error = exc
                        self._mark_failed(exc)
                        return
                except Exception as exc:
                    exit_error = self._unexpected_adapter_error(exc)
                    self._fail_command(command, exit_error, ambiguous=True)
                    self._mark_failed(exit_error)
                    return
        finally:
            self._accepting = False
            self._drain(exit_error)

    async def _execute_command(
        self,
        command: _SubmitCommand
        | _RespondCommand
        | _ReconcileCommand
        | _ResolveCommand
        | _SetModelCommand
        | _SetEffortCommand
        | _StopCommand,
    ) -> bool:
        if isinstance(command, _SubmitCommand):
            await self._execute_submit(command)
        elif isinstance(command, _RespondCommand):
            await self._execute_response(command)
        elif isinstance(command, _ReconcileCommand):
            await self._execute_reconcile(command)
        elif isinstance(command, _ResolveCommand):
            self._execute_resolution(command)
        elif isinstance(command, _SetModelCommand):
            await self._execute_set_model(command)
        elif isinstance(command, _SetEffortCommand):
            await self._execute_set_effort(command)
        else:
            await self._execute_stop(command)
            return True
        return False

    async def _execute_stop(self, command: _StopCommand) -> None:
        await self._adapter.stop("graceful")
        self._set_snapshot(
            replace(
                self._snapshot(),
                control="disconnected",
                activity="idle",
                acceptance="rejected",
            )
        )
        self._publish()
        self._complete_future(command.future, None)

    async def _execute_submit(self, command: _SubmitCommand) -> None:
        try:
            receipt = await self._adapter.submit(command.request)
        except HarnessAdapterDisconnectedError as exc:
            receipt = SubmissionReceipt(
                request_id=command.request.request_id,
                acceptance="unknown" if exc.may_have_sent else "rejected",
                submitted_at=command.request.submitted_at,
                vendor_correlation_id=exc.vendor_correlation_id,
                detail=str(exc),
            )
            self._set_snapshot(
                replace(
                    self._snapshot(),
                    control="disconnected",
                    activity="unknown",
                    acceptance="unknown" if exc.may_have_sent else "rejected",
                )
            )
            self._publish()
        if receipt.request_id != command.request.request_id:
            raise HarnessControlError("adapter receipt request id does not match the submission")
        self._remember(receipt)
        self._pending_submissions.discard(command.request.request_id)
        self._submission_futures.pop(command.request.request_id, None)
        self._complete_future(command.future, receipt)

    async def _execute_response(self, command: _RespondCommand) -> None:
        await self._adapter.respond(command.response)
        snapshot = await self._adapter.snapshot()
        if snapshot.identity != self._snapshot().identity:
            raise HarnessControlError(
                "adapter snapshot identity changed after interaction response"
            )
        self._set_snapshot(snapshot)
        self._publish()
        self._complete_future(command.future, snapshot)

    async def _execute_reconcile(self, command: _ReconcileCommand) -> None:
        receipt = self._submissions.get(command.request_id)
        if receipt is None:
            raise HarnessControlError(
                f"submission {command.request_id!r} is no longer retained for reconciliation"
            )
        result = self._known_reconciliation(receipt)
        if result is None:
            result = await self._adapter.reconcile(command.request_id)
            if result.request_id != command.request_id:
                raise HarnessControlError("adapter reconciliation request id does not match")
            self._apply_reconciliation(result)
        else:
            self._submissions.move_to_end(command.request_id)
        self._complete_future(command.future, result)

    def _known_reconciliation(self, receipt: SubmissionReceipt) -> ReconciliationResult | None:
        if receipt.acceptance == "unknown":
            return None
        state: ReconciliationState
        if receipt.acceptance in {"immediate", "queued"}:
            state = "accepted"
        elif receipt.acceptance == "rejected":
            state = "rejected"
        else:
            state = "unsupported"
        return ReconciliationResult(
            request_id=receipt.request_id,
            state=state,
            reconciled_at=receipt.accepted_at or self._clock(),
            vendor_correlation_id=receipt.vendor_correlation_id,
            detail=receipt.detail,
            raw=receipt.raw,
        )

    async def _execute_set_model(self, command: _SetModelCommand) -> None:
        result = await self._adapter.set_model(command.model_key)
        self._validate_set_result(result, command.model_key)
        if result.acceptance in {"immediate", "queued"}:
            await self._refresh_snapshot_after_set()
        self._complete_future(command.future, result)

    async def _execute_set_effort(self, command: _SetEffortCommand) -> None:
        result = await self._adapter.set_effort(command.effort)
        self._validate_set_result(result, command.effort)
        if result.acceptance in {"immediate", "queued"}:
            await self._refresh_snapshot_after_set()
        self._complete_future(command.future, result)

    def _execute_resolution(self, command: _ResolveCommand) -> None:
        self._require_unknown(command.request_id, action="operator-resolved")
        result = ReconciliationResult(
            request_id=command.request_id,
            state=command.state,
            reconciled_at=self._clock(),
            detail=command.detail,
        )
        self._apply_reconciliation(result)
        self._complete_future(command.future, result)

    async def _refresh_snapshot_after_set(self) -> None:
        snapshot = await self._adapter.snapshot()
        if snapshot.identity != self._snapshot().identity:
            raise HarnessControlError("adapter snapshot identity changed after capability mutation")
        self._set_snapshot(snapshot)
        self._publish()

    def _apply_reconciliation(self, result: ReconciliationResult) -> None:
        prior = self._submissions.get(result.request_id)
        if prior is None:
            raise HarnessControlError(
                f"submission {result.request_id!r} is no longer retained for reconciliation"
            )
        acceptance = {
            "accepted": "immediate",
            "rejected": "rejected",
            "unresolved": "unknown",
            "unsupported": "unsupported",
        }[result.state]
        self._remember(
            replace(
                prior,
                acceptance=acceptance,
                vendor_correlation_id=(result.vendor_correlation_id or prior.vendor_correlation_id),
                accepted_at=result.reconciled_at if result.state == "accepted" else None,
                detail=result.detail,
                raw=result.raw,
            )
        )

    def _fail_command(
        self,
        command: _SubmitCommand
        | _RespondCommand
        | _ReconcileCommand
        | _ResolveCommand
        | _SetModelCommand
        | _SetEffortCommand
        | _StopCommand,
        error: HarnessControlError,
        *,
        ambiguous: bool = False,
    ) -> None:
        if isinstance(command, _SubmitCommand):
            request_id = command.request.request_id
            self._pending_submissions.discard(request_id)
            self._submission_futures.pop(request_id, None)
            prior = self._submissions.get(request_id)
            if ambiguous and prior is not None:
                self._remember(replace(prior, acceptance="unknown", detail=str(error)))
            else:
                self._submissions.pop(request_id, None)
        if not command.future.done():
            # Never attach the exception currently handled by the runner to a caller future. Its
            # traceback owns the live runner frame; assertion/debug tooling may clear that frame
            # when consuming the future and terminate the runner with a coroutine-reuse error.
            command.future.set_exception(HarnessControlError(str(error)))

    def _drain(self, error: HarnessControlError) -> None:
        while not self._commands.empty():
            self._fail_command(self._commands.get_nowait(), error)

    def _mark_failed(self, error: HarnessControlError) -> None:
        snapshot = self._snapshot()
        self._set_snapshot(
            replace(
                snapshot,
                control="failed",
                activity="unknown",
                acceptance="rejected",
                raw={**snapshot.raw, "bridgeError": str(error)},
            )
        )
        self._publish()

    @staticmethod
    def _validate_set_result(result: SetResult, requested_value: str) -> None:
        if result.requested_value != requested_value:
            raise HarnessControlError("adapter set result does not match the requested value")
        if result.acceptance not in SET_ACCEPTANCE_VALUES:
            raise HarnessControlError(
                f"adapter set result has unsupported acceptance {result.acceptance!r}"
            )
        if result.acceptance == "echo-verified":
            if not result.ok or result.effective_value is None:
                raise HarnessControlError(
                    "echo-verified adapter set result requires an evidenced effective value"
                )
            return
        if result.acceptance in {"immediate", "queued"}:
            if not result.ok:
                raise HarnessControlError(
                    f"{result.acceptance} adapter set result must be accepted"
                )
            if result.effective_value is not None:
                raise HarnessControlError(
                    f"{result.acceptance} adapter set result cannot claim an effective value"
                )
            return
        if result.ok or result.effective_value is not None:
            raise HarnessControlError(
                f"{result.acceptance} adapter set result cannot claim acceptance or effect"
            )

    @staticmethod
    def _complete_future(future: asyncio.Future[T], result: T) -> None:
        """Leave a caller-cancelled command unobserved without terminating the queue."""

        if not future.done():
            future.set_result(result)

    @staticmethod
    def _unexpected_adapter_error(error: Exception) -> HarnessControlError:
        return HarnessControlError(
            f"unexpected adapter {type(error).__name__} during control command: {error}"
        )
