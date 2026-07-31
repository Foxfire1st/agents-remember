"""Thin execution facade over the bridge-owned submission authority.

The former command actor was a second order and blocked interaction responses/status behind slow
adapter I/O.  ``HarnessSubmissionAuthority`` now owns the only prompt/setter timeline; this class
preserves the bridge-facing API and stop/snapshot behavior without retaining another queue.
"""

from __future__ import annotations

from dataclasses import replace

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_capabilities import SetResult
from agents_remember.serving.harness_control_adapter import HarnessProtocolAdapter
from agents_remember.serving.harness_control_models import (
    EVIDENCE_PAGE_BYTE_BUDGET,
    MAX_OPERATION_TIMELINE_PAGE,
    AdapterEvent,
    AdapterSnapshot,
    ControlOperationKind,
    ControlOperationRef,
    InteractionResponse,
    OperationTimeline,
    PromptRequest,
    ReconciliationResult,
    ReconciliationState,
    SubmissionAuthorityDescriptor,
    SubmissionProvenanceBatch,
    SubmissionReceipt,
    SubmissionStatusBatch,
    WithdrawalResult,
)
from agents_remember.serving.harness_submission_authority import (
    BridgeSnapshotPort,
    HarnessSubmissionAuthority,
    OperationResolution,
    SubmissionLimits,
)


class HarnessControlQueue:
    """Compatibility facade; all ordering and lifecycle truth lives in ``authority``."""

    def __init__(
        self,
        adapter: HarnessProtocolAdapter,
        port: BridgeSnapshotPort,
        *,
        queue_limit: int,
        submission_limit: int,
    ) -> None:
        self._adapter = adapter
        self._snapshot = port.snapshot
        self._set_snapshot = port.set_snapshot
        self._publish = port.publish
        self._authority = HarnessSubmissionAuthority(
            adapter,
            port,
            SubmissionLimits(timeline=queue_limit, ledger=submission_limit),
        )
        self._started = False

    @property
    def bridge_epoch(self) -> str:
        return self._authority.bridge_epoch

    @property
    def active_operation(self) -> ControlOperationRef | None:
        return self._authority.active_operation

    @property
    def retained_submission_count(self) -> int:
        return self._authority.retained_record_count

    def start(self) -> None:
        if self._started:
            raise HarnessControlError("control command queue is already started")
        self._started = True
        self._authority.start()

    def descriptor(self) -> SubmissionAuthorityDescriptor:
        return self._authority.descriptor()

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        return await self._authority.submit(request)

    async def respond(self, response: InteractionResponse) -> AdapterSnapshot:
        return await self._authority.respond(response)

    async def reconcile(
        self, request_id: str, *, expected_bridge_epoch: str | None = None
    ) -> ReconciliationResult:
        return await self._authority.reconcile(
            request_id,
            expected_bridge_epoch=expected_bridge_epoch,
        )

    async def resolve_unknown(
        self,
        request_id: str,
        *,
        state: ReconciliationState,
        detail: str,
    ) -> ReconciliationResult:
        return await self._authority.resolve_unknown_prompt(
            request_id,
            state=state,
            detail=detail,
        )

    async def resolve_operation(
        self,
        operation_id: str,
        operation_kind: ControlOperationKind,
        *,
        resolution: OperationResolution,
        detail: str,
    ) -> None:
        await self._authority.resolve_operation(
            operation_id,
            operation_kind,
            resolution=resolution,
            detail=detail,
        )

    async def set_model(self, model_key: str) -> SetResult:
        return await self._authority.set_model(model_key)

    async def set_effort(self, effort: str) -> SetResult:
        return await self._authority.set_effort(effort)

    async def status(
        self,
        expected_bridge_epoch: str,
        request_ids: tuple[str, ...],
        *,
        cockpit_only: bool,
    ) -> SubmissionStatusBatch:
        return await self._authority.status(
            expected_bridge_epoch,
            request_ids,
            cockpit_only=cockpit_only,
        )

    async def provenance(
        self,
        expected_bridge_epoch: str,
        request_ids: tuple[str, ...],
    ) -> SubmissionProvenanceBatch:
        """Read-only provenance batch for every source; the sole bridge->authority path."""

        return await self._authority.provenance(expected_bridge_epoch, request_ids)

    async def operation_timeline(
        self,
        expected_bridge_epoch: str,
        *,
        after_sequence: int = 0,
        limit: int = MAX_OPERATION_TIMELINE_PAGE,
        byte_budget: int = EVIDENCE_PAGE_BYTE_BUDGET,
    ) -> OperationTimeline:
        """Read-only paged never-bodies ledger enumeration; the sole bridge->authority path."""

        return await self._authority.operation_timeline(
            expected_bridge_epoch,
            after_sequence=after_sequence,
            limit=limit,
            byte_budget=byte_budget,
        )

    async def withdraw(
        self,
        expected_bridge_epoch: str,
        request_id: str,
        *,
        cockpit_only: bool,
    ) -> WithdrawalResult:
        return await self._authority.withdraw(
            expected_bridge_epoch,
            request_id,
            cockpit_only=cockpit_only,
        )

    async def observe_event(self, event: AdapterEvent) -> None:
        await self._authority.observe_event(event)

    def notify_snapshot_updated(self) -> None:
        self._authority.notify_snapshot_updated()

    async def graceful_stop(self) -> None:
        await self._stop(forced=False)

    async def force_stop(self) -> None:
        await self._stop(forced=True)

    async def _stop(self, *, forced: bool) -> None:
        if not self._started:
            return
        self._started = False
        try:
            await self._authority.stop(forced=forced)
        except Exception as exc:
            error = HarnessControlError(
                f"unexpected adapter {type(exc).__name__} during control stop: {exc}"
            )
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
            raise error from exc
        else:
            self._set_snapshot(
                replace(
                    self._snapshot(),
                    control="disconnected",
                    activity="unknown" if forced else "idle",
                    acceptance="rejected",
                )
            )
            self._publish()
