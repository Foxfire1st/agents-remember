"""Deterministic races for the bridge-owned submission/setter authority."""

from __future__ import annotations

import asyncio
import sys
import unittest
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import (
    HarnessAdapterBusyError,
    HarnessAdapterDisconnectedError,
    HarnessBridgeEpochMismatchError,
    HarnessControlError,
    HarnessRequestConflictError,
)
from agents_remember.serving.harness_capabilities import CapabilitySnapshot, SetResult
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    REQUIRED_ADAPTER_CAPABILITIES,
    AdapterEvent,
    AdapterHandshake,
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationRef,
    InteractionResponse,
    LaunchSpec,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
    SubmissionReceipt,
)
from agents_remember.serving.harness_submission_authority import (
    DISPATCH_ACCEPTANCE_GRACE_SECONDS,
    BridgeSnapshotPort,
    HarnessSubmissionAuthority,
    SubmissionLimits,
)
from agents_remember.serving.harness_submission_ledger import (
    MAX_LOOKUP_REQUEST_IDS,
    OperationRecord,
    SubmissionLedger,
)

NOW = "2026-07-17T12:00:00+00:00"


def _identity() -> ControlIdentity:
    return ControlIdentity("session-1", "ar-session-1", NOW)


class _AuthorityAdapter:
    def __init__(self) -> None:
        self.current = AdapterSnapshot(
            identity=_identity(),
            control="ready",
            activity="idle",
            acceptance="immediate",
        )
        self.submit_results: deque[SubmissionReceipt | Exception] = deque()
        self.set_results: deque[SetResult | Exception] = deque()
        self.submissions: list[PromptRequest] = []
        self.set_operations: list[tuple[str, str, ControlOperationRef | None]] = []
        self.responses: list[InteractionResponse] = []
        self.submit_started = asyncio.Event()
        self.release_submit = asyncio.Event()
        self.block_submit = False
        self.set_started = asyncio.Event()
        self.release_set = asyncio.Event()
        self.block_set = False
        self.stop_modes: list[ShutdownMode] = []
        self.preflight_results: deque[Exception] = deque()
        self.reconcile_results: deque[ReconciliationResult] = deque()
        self.preflight_started = asyncio.Event()
        self.release_preflight = asyncio.Event()
        self.block_preflight = False

    async def start(self, launch: LaunchSpec) -> AdapterHandshake:
        return AdapterHandshake(
            protocol_version=CONTROL_PROTOCOL_VERSION,
            adapter_id="authority-fake",
            identity=launch.identity,
            capabilities=REQUIRED_ADAPTER_CAPABILITIES,
            snapshot=self.current,
        )

    def advertise(self) -> CapabilitySnapshot:
        return CapabilitySnapshot(models=(), selected_model_key=None, selected_effort=None)

    async def set_model(
        self, model_key: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        self.set_operations.append(("set-model", model_key, operation))
        self.set_started.set()
        if self.block_set:
            await self.release_set.wait()
        result = (
            self.set_results.popleft()
            if self.set_results
            else SetResult(
                ok=True,
                acceptance="immediate",
                requested_value=model_key,
            )
        )
        if isinstance(result, Exception):
            raise result
        return result

    async def set_effort(
        self, effort: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        self.set_operations.append(("set-effort", effort, operation))
        result = (
            self.set_results.popleft()
            if self.set_results
            else SetResult(
                ok=True,
                acceptance="immediate",
                requested_value=effort,
            )
        )
        if isinstance(result, Exception):
            raise result
        return result

    async def snapshot(self) -> AdapterSnapshot:
        return self.current

    async def preflight_operation(self, operation: ControlOperationRef) -> None:
        self.assert_operation = operation
        self.preflight_started.set()
        if self.block_preflight:
            await self.release_preflight.wait()
        if self.preflight_results:
            raise self.preflight_results.popleft()

    async def _events(self) -> AsyncIterator[AdapterEvent]:
        if False:
            yield AdapterEvent(0, "unused", _identity(), NOW)

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        return self._events()

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self.submissions.append(request)
        self.submit_started.set()
        if self.block_submit:
            await self.release_submit.wait()
        result = (
            self.submit_results.popleft()
            if self.submit_results
            else SubmissionReceipt(
                request_id=request.request_id,
                acceptance="immediate",
                submitted_at=request.submitted_at,
                accepted_at=NOW,
            )
        )
        if isinstance(result, Exception):
            raise result
        return replace(result, request_id=request.request_id, submitted_at=request.submitted_at)

    async def respond(self, response: InteractionResponse) -> None:
        self.responses.append(response)
        self.current = replace(self.current, pending_interaction=None)

    async def reconcile(self, request_id: str) -> ReconciliationResult:
        if not self.reconcile_results:
            return ReconciliationResult(request_id, "unresolved", NOW)
        return replace(self.reconcile_results.popleft(), request_id=request_id)

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)


def _authority(
    adapter: _AuthorityAdapter,
    *,
    timeline_limit: int = 64,
    ledger_limit: int = 256,
    bridge_epoch: str = "epoch-1",
    dispatch_grace_seconds: float = DISPATCH_ACCEPTANCE_GRACE_SECONDS,
) -> HarnessSubmissionAuthority:
    authority = HarnessSubmissionAuthority(
        adapter,
        BridgeSnapshotPort(
            clock=lambda: NOW,
            snapshot=lambda: adapter.current,
            set_snapshot=lambda value: setattr(adapter, "current", value),
            publish=lambda: None,
        ),
        SubmissionLimits(
            timeline=timeline_limit,
            ledger=ledger_limit,
            dispatch_grace_seconds=dispatch_grace_seconds,
        ),
        bridge_epoch=bridge_epoch,
    )
    authority.start()
    return authority


def _prompt(request_id: str, text: str = "hello", source: str = "cockpit") -> PromptRequest:
    return PromptRequest(request_id, source, text, NOW)  # type: ignore[arg-type]


async def _complete(
    authority: HarnessSubmissionAuthority,
    operation: ControlOperationRef,
    sequence: int,
) -> None:
    await authority.observe_event(
        AdapterEvent(
            sequence=sequence,
            kind="completed",
            identity=_identity(),
            created_at=NOW,
            operation=operation,
        )
    )


class HarnessSubmissionAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_active_operation_does_not_block_status_or_queued_withdrawal(self) -> None:
        adapter = _AuthorityAdapter()
        adapter.block_submit = True
        authority = _authority(adapter)
        try:
            first_task = asyncio.create_task(authority.submit(_prompt("first")))
            await asyncio.wait_for(adapter.submit_started.wait(), 1)

            second = await authority.submit(_prompt("second", "withdraw me"))
            self.assertEqual(second.acceptance, "queued")
            status = await asyncio.wait_for(
                authority.ledger.status("epoch-1", ("second",), cockpit_only=True),
                0.1,
            )
            second_status = status.submissions[0].submission
            self.assertIsNotNone(second_status)
            assert second_status is not None
            self.assertEqual(second_status.state, "queued")
            withdrawn = await asyncio.wait_for(
                authority.withdraw("epoch-1", "second", cockpit_only=True),
                0.1,
            )
            self.assertEqual((withdrawn.outcome, withdrawn.state), ("withdrawn", "withdrawn"))
            self.assertEqual([item.request_id for item in adapter.submissions], ["first"])

            adapter.release_submit.set()
            first = await asyncio.wait_for(first_task, 1)
            self.assertEqual(first.acceptance, "immediate")
            assert authority.active_operation is not None
            await _complete(authority, authority.active_operation, 1)
        finally:
            await authority.stop(forced=True)

    async def test_slow_dispatch_evidence_returns_queued_on_grace_and_still_completes(self) -> None:
        """Vendor evidence slower than the dispatch grace must not hold the submit response.

        Claude's replay echo flushes with the turn's first output (~4.25s
        measured live), so awaiting it held every dashboard submit POST for the full TTFT.
        The submit answers "queued" on the grace while the record stays live, and the late
        echo still mints the delivered upgrade through status.  Without the grace this test's
        submit never returns (the adapter stays blocked) and the wait_for below fails.
        """

        adapter = _AuthorityAdapter()
        adapter.block_submit = True
        authority = _authority(adapter, dispatch_grace_seconds=0.05)
        try:
            receipt = await asyncio.wait_for(authority.submit(_prompt("grace")), 1)
            self.assertEqual(receipt.acceptance, "queued")
            self.assertIsNone(receipt.accepted_at)
            assert receipt.detail is not None
            self.assertIn("acceptance evidence is pending", receipt.detail)
            active = authority.active_operation
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual(active.operation_id, "grace")
            # The late echo still lands: releasing the adapter mints the delivered upgrade.
            adapter.release_submit.set()
            submission = None
            for _ in range(10):
                status = await authority.ledger.status("epoch-1", ("grace",), cockpit_only=True)
                submission = status.submissions[0].submission
                assert submission is not None
                if submission.state == "delivered":
                    break
                await asyncio.sleep(0)
            assert submission is not None
            self.assertEqual(submission.state, "delivered")
            self.assertEqual(submission.accepted_at, NOW)
            self.assertFalse(submission.withdrawable)
            assert authority.active_operation is not None
            await _complete(authority, authority.active_operation, 1)
        finally:
            adapter.release_submit.set()
            await authority.stop(forced=True)

    async def test_dispatch_claim_wins_atomic_withdrawal_race(self) -> None:
        adapter = _AuthorityAdapter()
        adapter.block_submit = True
        authority = _authority(adapter)
        try:
            submit_task = asyncio.create_task(authority.submit(_prompt("dispatch-wins")))
            await asyncio.wait_for(adapter.submit_started.wait(), 1)
            result = await authority.withdraw("epoch-1", "dispatch-wins", cockpit_only=True)
            self.assertEqual((result.outcome, result.state), ("not-withdrawable", "dispatching"))
            adapter.release_submit.set()
            await submit_task
            assert authority.active_operation is not None
            await _complete(authority, authority.active_operation, 1)
        finally:
            await authority.stop(forced=True)

    async def test_withdrawal_during_preflight_wins_before_dispatch_claim(self) -> None:
        adapter = _AuthorityAdapter()
        adapter.block_preflight = True
        authority = _authority(adapter)
        try:
            submit_task = asyncio.create_task(authority.submit(_prompt("withdraw-preflight")))
            await asyncio.wait_for(adapter.preflight_started.wait(), 1)

            withdrawn = await authority.withdraw("epoch-1", "withdraw-preflight", cockpit_only=True)
            self.assertEqual((withdrawn.outcome, withdrawn.state), ("withdrawn", "withdrawn"))
            receipt = await asyncio.wait_for(submit_task, 1)
            self.assertEqual(receipt.acceptance, "rejected")

            adapter.release_preflight.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(adapter.submissions, [])
            status = await authority.ledger.status(
                "epoch-1", ("withdraw-preflight",), cockpit_only=True
            )
            submission = status.submissions[0].submission
            self.assertIsNotNone(submission)
            assert submission is not None
            self.assertEqual(submission.state, "withdrawn")
        finally:
            adapter.release_preflight.set()
            await authority.stop(forced=True)

    async def test_completion_before_receipt_is_buffered_and_releases_exact_head(self) -> None:
        adapter = _AuthorityAdapter()
        adapter.block_submit = True
        authority = _authority(adapter)
        try:
            first_task = asyncio.create_task(authority.submit(_prompt("early")))
            await asyncio.wait_for(adapter.submit_started.wait(), 1)
            first_ref = authority.active_operation
            assert first_ref is not None
            await _complete(authority, first_ref, 1)
            second = await authority.submit(_prompt("next"))
            self.assertEqual(second.acceptance, "queued")

            adapter.block_submit = False
            adapter.release_submit.set()
            await first_task
            for _ in range(5):
                if len(adapter.submissions) == 2:
                    break
                await asyncio.sleep(0)
            self.assertEqual([item.request_id for item in adapter.submissions], ["early", "next"])
            second_ref = authority.active_operation
            assert second_ref is not None and second_ref.operation_id == "next"
            with self.assertRaisesRegex(HarnessControlError, "duplicate"):
                await _complete(authority, first_ref, 2)
            self.assertEqual(authority.active_operation, second_ref)
            await _complete(authority, second_ref, 3)
        finally:
            await authority.stop(forced=True)

    async def test_buffered_completion_dominates_unknown_prompt_receipt_once(self) -> None:
        adapter = _AuthorityAdapter()
        adapter.block_submit = True
        adapter.submit_results.append(
            SubmissionReceipt("early-unknown", "unknown", NOW, detail="receipt stayed unknown")
        )
        authority = _authority(adapter)
        try:
            first_task = asyncio.create_task(authority.submit(_prompt("early-unknown")))
            await asyncio.wait_for(adapter.submit_started.wait(), 1)
            first_ref = authority.active_operation
            assert first_ref is not None
            queued = await authority.submit(_prompt("after-early-unknown"))
            self.assertEqual(queued.acceptance, "queued")

            await _complete(authority, first_ref, 1)
            adapter.block_submit = False
            adapter.release_submit.set()
            receipt = await asyncio.wait_for(first_task, 1)
            self.assertEqual(receipt.acceptance, "immediate")
            for _ in range(5):
                if len(adapter.submissions) == 2:
                    break
                await asyncio.sleep(0)
            successor = authority.active_operation
            assert successor is not None and successor.operation_id == "after-early-unknown"
            with self.assertRaisesRegex(HarnessControlError, "duplicate"):
                await _complete(authority, first_ref, 2)
            self.assertEqual(authority.active_operation, successor)
            await _complete(authority, successor, 3)
        finally:
            adapter.release_submit.set()
            await authority.stop(forced=True)

    async def test_buffered_completion_dominates_unknown_setter_result_once(self) -> None:
        adapter = _AuthorityAdapter()
        adapter.block_set = True
        adapter.set_results.append(
            SetResult(
                ok=False,
                acceptance="unknown",
                requested_value="model-b",
                detail="setter result stayed unknown",
            )
        )
        authority = _authority(adapter)
        try:
            setter_task = asyncio.create_task(authority.set_model("model-b"))
            await asyncio.wait_for(adapter.set_started.wait(), 1)
            setter_ref = authority.active_operation
            assert setter_ref is not None and setter_ref.kind == "set-model"
            queued = await authority.submit(_prompt("after-unknown-setter"))
            self.assertEqual(queued.acceptance, "queued")

            await _complete(authority, setter_ref, 1)
            adapter.block_set = False
            adapter.release_set.set()
            result = await asyncio.wait_for(setter_task, 1)
            self.assertEqual((result.ok, result.acceptance), (True, "immediate"))
            for _ in range(5):
                if adapter.submissions:
                    break
                await asyncio.sleep(0)
            successor = authority.active_operation
            assert successor is not None and successor.operation_id == "after-unknown-setter"
            with self.assertRaisesRegex(HarnessControlError, "duplicate"):
                await _complete(authority, setter_ref, 2)
            self.assertEqual(authority.active_operation, successor)
            await _complete(authority, successor, 3)
        finally:
            adapter.release_set.set()
            await authority.stop(forced=True)

    async def test_evicted_request_id_reuse_keeps_full_completion_identity(self) -> None:
        adapter = _AuthorityAdapter()
        authority = _authority(adapter, timeline_limit=2, ledger_limit=2)
        try:
            await authority.submit(_prompt("reuse"))
            first_ref = authority.active_operation
            assert first_ref is not None
            await _complete(authority, first_ref, 1)

            await authority.submit(_prompt("second"))
            second_ref = authority.active_operation
            assert second_ref is not None
            await _complete(authority, second_ref, 2)

            adapter.block_preflight = True
            adapter.preflight_started = asyncio.Event()
            adapter.release_preflight = asyncio.Event()
            evictor_task = asyncio.create_task(authority.submit(_prompt("withdrawn-evictor")))
            await asyncio.wait_for(adapter.preflight_started.wait(), 1)
            withdrawn = await authority.withdraw("epoch-1", "withdrawn-evictor", cockpit_only=True)
            self.assertEqual(withdrawn.outcome, "withdrawn")
            self.assertEqual((await evictor_task).acceptance, "rejected")
            adapter.block_preflight = False
            adapter.release_preflight.set()
            await asyncio.sleep(0)

            await authority.submit(_prompt("reuse"))
            reused_ref = authority.active_operation
            assert reused_ref is not None
            self.assertGreater(reused_ref.sequence, first_ref.sequence)
            self.assertEqual(reused_ref.operation_id, first_ref.operation_id)
            with self.assertRaisesRegex(HarnessControlError, "duplicate"):
                await _complete(authority, first_ref, 3)
            self.assertEqual(authority.active_operation, reused_ref)
            await _complete(authority, reused_ref, 4)
            self.assertIsNone(authority.active_operation)
        finally:
            adapter.release_preflight.set()
            await authority.stop(forced=True)

    async def test_prompt_setter_order_and_withdraw_while_slow_setter_owns_lane(self) -> None:
        adapter = _AuthorityAdapter()
        adapter.block_set = True
        authority = _authority(adapter)
        try:
            setter_task = asyncio.create_task(authority.set_model("model-b"))
            await asyncio.wait_for(adapter.set_started.wait(), 1)
            queued = await authority.submit(_prompt("after-set"))
            self.assertEqual(queued.acceptance, "queued")
            result = await authority.withdraw("epoch-1", "after-set", cockpit_only=True)
            self.assertEqual(result.outcome, "withdrawn")
            self.assertEqual(adapter.submissions, [])
            adapter.release_set.set()
            self.assertEqual((await setter_task).acceptance, "immediate")
            setter_operation = adapter.set_operations[0][2]
            self.assertIsNotNone(setter_operation)
            assert setter_operation is not None
            self.assertEqual(setter_operation.kind, "set-model")
        finally:
            await authority.stop(forced=True)

    async def test_same_id_is_idempotent_but_source_or_payload_change_conflicts(self) -> None:
        adapter = _AuthorityAdapter()
        authority = _authority(adapter)
        try:
            first = await authority.submit(_prompt("same", "first"))
            duplicate = await authority.submit(_prompt("same", "first"))
            self.assertEqual((first.acceptance, duplicate.acceptance), ("immediate", "immediate"))
            self.assertEqual(len(adapter.submissions), 1)
            with self.assertRaises(HarnessRequestConflictError):
                await authority.submit(_prompt("same", "different"))
            with self.assertRaises(HarnessRequestConflictError):
                await authority.submit(_prompt("same", "first", source="durable"))
            assert authority.active_operation is not None
            await _complete(authority, authority.active_operation, 1)
        finally:
            await authority.stop(forced=True)

    async def test_duplicate_projection_table_and_no_second_adapter_call(self) -> None:
        cases = (
            ("withdrawn", "rejected"),
            ("unknown", "unknown"),
            ("rejected", "rejected"),
            ("unsupported", "unsupported"),
        )
        for lifecycle, expected in cases:
            with self.subTest(lifecycle=lifecycle):
                adapter = _AuthorityAdapter()
                if lifecycle == "unknown":
                    adapter.submit_results.append(SubmissionReceipt("x", "unknown", NOW))
                elif lifecycle in {"rejected", "unsupported"}:
                    adapter.submit_results.append(
                        SubmissionReceipt("x", lifecycle, NOW)  # type: ignore[arg-type]
                    )
                authority = _authority(adapter)
                try:
                    if lifecycle == "withdrawn":
                        adapter.block_submit = True
                        active = asyncio.create_task(authority.submit(_prompt("blocker")))
                        await adapter.submit_started.wait()
                        await authority.submit(_prompt("x"))
                        await authority.withdraw("epoch-1", "x", cockpit_only=True)
                        duplicate = await authority.submit(_prompt("x"))
                        adapter.release_submit.set()
                        await active
                    else:
                        await authority.submit(_prompt("x"))
                        duplicate = await authority.submit(_prompt("x"))
                    self.assertEqual(duplicate.acceptance, expected)
                    self.assertLessEqual(
                        len([item for item in adapter.submissions if item.request_id == "x"]),
                        1,
                    )
                finally:
                    await authority.stop(forced=True)

    async def test_certified_pre_send_busy_requeues_without_vendor_queue_or_resend(self) -> None:
        adapter = _AuthorityAdapter()
        adapter.submit_results.append(HarnessAdapterBusyError("became busy before write"))
        authority = _authority(adapter)
        try:
            receipt = await authority.submit(_prompt("busy"))
            self.assertEqual(receipt.acceptance, "queued")
            status = await authority.ledger.status("epoch-1", ("busy",), cockpit_only=True)
            busy_status = status.submissions[0].submission
            self.assertIsNotNone(busy_status)
            assert busy_status is not None
            self.assertEqual(busy_status.state, "queued")
            self.assertEqual(len(adapter.submissions), 1)
        finally:
            await authority.stop(forced=True)

    async def test_preflight_busy_requeues_without_calling_adapter_submit(self) -> None:
        adapter = _AuthorityAdapter()
        adapter.preflight_results.append(HarnessAdapterBusyError("busy during fresh preflight"))
        authority = _authority(adapter)
        try:
            receipt = await authority.submit(_prompt("preflight-busy"))
            self.assertEqual(receipt.acceptance, "queued")
            self.assertEqual(adapter.submissions, [])
            status = await authority.ledger.status(
                "epoch-1", ("preflight-busy",), cockpit_only=True
            )
            submission = status.submissions[0].submission
            self.assertIsNotNone(submission)
            assert submission is not None
            self.assertEqual(submission.state, "queued")
            withdrawn = await authority.withdraw("epoch-1", "preflight-busy", cockpit_only=True)
            self.assertEqual((withdrawn.outcome, withdrawn.state), ("withdrawn", "withdrawn"))
        finally:
            await authority.stop(forced=True)

    async def test_impossible_possible_send_preflight_installs_resolvable_unknown_barrier(
        self,
    ) -> None:
        adapter = _AuthorityAdapter()
        adapter.preflight_results.append(
            HarnessAdapterDisconnectedError(
                "preflight claimed possible send",
                may_have_sent=True,
                vendor_correlation_id="impossible-preflight",
            )
        )
        authority = _authority(adapter)
        try:
            receipt = await authority.submit(_prompt("preflight-unknown"))
            self.assertEqual(receipt.acceptance, "unknown")
            self.assertEqual(receipt.vendor_correlation_id, "impossible-preflight")
            active = authority.active_operation
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual(active.operation_id, "preflight-unknown")
            self.assertEqual(adapter.submissions, [])
            self.assertEqual(
                (adapter.current.control, adapter.current.activity, adapter.current.acceptance),
                ("disconnected", "unknown", "unknown"),
            )
            await authority.resolve_operation(
                "preflight-unknown",
                "prompt",
                resolution="not-applied",
                detail="operator rejected the impossible preflight effect",
            )
            self.assertIsNone(authority.active_operation)
        finally:
            await authority.stop(forced=True)

    async def test_epoch_and_public_source_scope_fail_closed(self) -> None:
        adapter = _AuthorityAdapter()
        adapter.block_submit = True
        authority = _authority(adapter)
        try:
            terminal_task = asyncio.create_task(
                authority.submit(_prompt("terminal", source="terminal"))
            )
            await adapter.submit_started.wait()
            await authority.submit(_prompt("cockpit"))
            status = await authority.ledger.status(
                "epoch-1", ("terminal", "cockpit", "missing"), cockpit_only=True
            )
            self.assertEqual(
                [item.outcome for item in status.submissions],
                [
                    "not-found",
                    "found",
                    "not-found",
                ],
            )
            with self.assertRaises(HarnessBridgeEpochMismatchError):
                await authority.withdraw("old-epoch", "cockpit", cockpit_only=True)
            adapter.release_submit.set()
            await terminal_task
        finally:
            await authority.stop(forced=True)

    async def test_control_operation_ref_rejects_invalid_tokens(self) -> None:
        valid = {
            "bridgeEpoch": "epoch",
            "operationSequence": 1,
            "operationId": "request",
            "operationKind": "prompt",
        }
        self.assertEqual(ControlOperationRef.from_json(valid).operation_id, "request")
        for key, value in (
            ("bridgeEpoch", ""),
            ("operationSequence", 0),
            ("operationSequence", True),
            ("operationId", ""),
            ("operationKind", "response"),
        ):
            malformed = {**valid, key: value}
            with self.subTest(malformed=malformed), self.assertRaises(HarnessControlError):
                ControlOperationRef.from_json(malformed)

    async def test_a_reconciled_refusal_terminates_the_operation_and_frees_the_head(self) -> None:
        """An ambiguous send the vendor later disowns must stop blocking the timeline.

        The unknown barrier exists because the authority does not know whether the prompt
        landed. When the adapter finally answers ``rejected`` or ``unsupported`` it DOES
        know, and the record has to become terminal AND release the head -- an operation
        that is provably not in flight must not keep the next one out.
        """
        for state in ("rejected", "unsupported"):
            with self.subTest(state=state):
                adapter = _AuthorityAdapter()
                adapter.preflight_results.append(
                    HarnessAdapterDisconnectedError(
                        "preflight claimed possible send",
                        may_have_sent=True,
                        vendor_correlation_id="vendor-amb",
                    )
                )
                adapter.reconcile_results.append(
                    ReconciliationResult("amb", state, NOW, detail="the vendor disowned it")  # type: ignore[arg-type]
                )
                authority = _authority(adapter)
                try:
                    receipt = await authority.submit(_prompt("amb"))
                    self.assertEqual(receipt.acceptance, "unknown")
                    self.assertIsNotNone(authority.active_operation)

                    result = await authority.reconcile("amb")

                    self.assertEqual((result.state, result.submission_state), (state, state))
                    self.assertEqual(result.bridge_epoch, "epoch-1")
                    # The head is released: nothing is in flight any more.
                    self.assertIsNone(authority.active_operation)
                    status = await authority.ledger.status("epoch-1", ("amb",), cockpit_only=True)
                    settled = status.submissions[0].submission
                    assert settled is not None
                    self.assertEqual(
                        (settled.state, settled.detail), (state, "the vendor disowned it")
                    )
                    self.assertFalse(settled.withdrawable)
                finally:
                    await authority.stop(forced=True)

    async def test_a_setter_is_refused_outright_when_the_timeline_has_no_room(self) -> None:
        """A full timeline answers ``unknown`` immediately rather than parking the setter.

        A setter carries a caller waiting on a future. Admitting one with nowhere to run it
        would leave that caller blocked on a slot that only frees when the operation ahead
        completes -- so the refusal is the honest answer, and it must reach no adapter.
        """
        adapter = _AuthorityAdapter()
        adapter.block_submit = True
        authority = _authority(adapter, timeline_limit=1)
        held = asyncio.create_task(authority.submit(_prompt("holds-the-only-slot")))
        try:
            await asyncio.wait_for(adapter.submit_started.wait(), 1)

            refused = await authority.set_model("model-b")

            self.assertEqual((refused.ok, refused.acceptance), (False, "unknown"))
            self.assertEqual(refused.requested_value, "model-b")
            assert refused.detail is not None
            self.assertIn("ledger is full", refused.detail)
            self.assertEqual(adapter.set_operations, [])
        finally:
            adapter.release_submit.set()
            await held
            await authority.stop(forced=True)


class SubmissionLedgerTests(unittest.IsolatedAsyncioTestCase):
    """The record store on its own: what it forgets, what it refuses, and what it answers.

    Reached directly rather than through the authority because these are the ledger's own
    boundaries -- a store with no room, a page with no size, a lookup batch with no bound --
    and driving them through a live authority would mean asserting on a fake adapter's
    scheduling rather than on the store's contract.
    """

    def _ledger(self, *, limit: int = 4) -> SubmissionLedger:
        return SubmissionLedger(bridge_epoch="epoch-1", limit=limit, lock=asyncio.Lock())

    def _enrol(
        self,
        ledger: SubmissionLedger,
        kind: str,
        state: str,
        *,
        operation_id: str | None = None,
        requested_value: str | None = None,
    ) -> OperationRecord:
        record = OperationRecord(
            ref=ledger.next_ref(kind, operation_id),  # type: ignore[arg-type]
            state=state,  # type: ignore[arg-type]
            submitted_at=NOW,
            updated_at=NOW,
            source="cockpit",
            requested_value=requested_value,
        )
        ledger.enrol(record)
        return record

    def test_a_ledger_with_nothing_droppable_refuses_room_rather_than_forgetting_a_row(
        self,
    ) -> None:
        # A live row is the only evidence that a send may have landed, and a pinned row is
        # one the caller is still dispatching. Making room by dropping either would answer
        # "not-found" for an operation that is still happening.
        for label, state, pinned in (
            ("live rows", "queued", lambda key: False),
            ("pinned terminal rows", "delivered", lambda key: True),
        ):
            with self.subTest(label):
                ledger = self._ledger(limit=2)
                self._enrol(ledger, "prompt", state, operation_id="first")
                self._enrol(ledger, "prompt", state, operation_id="second")

                self.assertFalse(ledger.make_room(pinned))
                self.assertEqual(ledger.retained_record_count, 2)
                self.assertIsNotNone(ledger.by_request_id("first"))

    async def test_making_room_keeps_evicting_past_a_setter_and_says_what_it_forgot(
        self,
    ) -> None:
        """Eviction runs until one more fits, and the timeline reports the water mark.

        A setter row carries no caller-owned request id, so forgetting one touches no
        prompt index -- and that must not end the sweep early. Bounded is only honest if
        the ledger says where its retention starts, which is what
        ``evicted_before_sequence`` is for.
        """
        ledger = self._ledger(limit=2)
        first = self._enrol(ledger, "set-model", "delivered", requested_value="a")
        second = self._enrol(ledger, "set-effort", "delivered", requested_value="high")
        self._enrol(ledger, "prompt", "delivered", operation_id="survivor")

        self.assertTrue(ledger.make_room(lambda key: False))

        self.assertEqual(ledger.retained_record_count, 1)
        self.assertIsNone(ledger.by_key(first.key))
        self.assertIsNone(ledger.by_key(second.key))
        # The prompt index is intact for what survived, and only for it.
        self.assertIsNotNone(ledger.by_request_id("survivor"))
        page = await ledger.operation_timeline("epoch-1")
        self.assertEqual(page.evicted_before_sequence, second.ref.sequence)
        self.assertEqual([item.operation_id for item in page.items], ["survivor"])

    def test_a_pending_dispatch_receipt_reports_the_state_the_record_actually_reached(
        self,
    ) -> None:
        """The grace expiring is not evidence: the record answers for wherever it now is.

        Only a record still in ``dispatching`` is owed the "evidence is pending" wording. A
        record that raced into ``queued`` is plainly queued, and one that reached a terminal
        state answers through the duplicate mapping -- reporting either as "in flight" would
        tell a caller to keep reconciling something that has already settled.
        """
        ledger = self._ledger()
        expected = {
            "dispatching": ("queued", "acceptance evidence is pending"),
            "queued": ("queued", None),
            "delivered": ("immediate", None),
            "rejected": ("rejected", None),
            "withdrawn": ("rejected", "was withdrawn and will not be dispatched"),
        }
        for state, (acceptance, detail_fragment) in expected.items():
            with self.subTest(state=state):
                record = self._enrol(ledger, "prompt", state, operation_id=f"req-{state}")

                receipt = record.pending_dispatch_receipt("epoch-1")

                self.assertEqual(receipt.acceptance, acceptance)
                if detail_fragment is None:
                    self.assertIsNone(receipt.detail)
                else:
                    assert receipt.detail is not None
                    self.assertIn(detail_fragment, receipt.detail)

    async def test_a_lookup_batch_names_between_one_and_the_maximum_distinct_ids(self) -> None:
        """Unbounded or repeated ids are refused, not trimmed.

        The batch is answered under the authority's lock, so its size is the cost of every
        other operation waiting behind it; and a repeated id makes the positional answer
        ambiguous. Both are the caller's mistake and are named as such.
        """
        ledger = self._ledger()
        too_many = tuple(f"req-{index}" for index in range(MAX_LOOKUP_REQUEST_IDS + 1))
        for subject, call in (
            ("submission status", lambda ids: ledger.status("epoch-1", ids, cockpit_only=True)),
            ("submission provenance", lambda ids: ledger.provenance("epoch-1", ids)),
        ):
            for label, ids, complaint in (
                ("empty", (), f"1\\.\\.{MAX_LOOKUP_REQUEST_IDS} request ids"),
                ("over the cap", too_many, f"1\\.\\.{MAX_LOOKUP_REQUEST_IDS} request ids"),
                ("repeated", ("req-a", "req-a"), "must be unique"),
            ):
                with (
                    self.subTest(subject=subject, ids=label),
                    self.assertRaisesRegex(HarnessControlError, f"{subject}.*{complaint}"),
                ):
                    await call(ids)

    async def test_an_operation_timeline_page_must_have_a_size(self) -> None:
        # A zero limit or zero budget would answer "no items, truncated", which reads
        # exactly like a ledger that has nothing in it. Refusing keeps the two apart.
        ledger = self._ledger()
        self._enrol(ledger, "prompt", "delivered", operation_id="req-1")
        for label, kwargs in (
            ("limit", {"limit": 0}),
            ("byte budget", {"byte_budget": 0}),
        ):
            with (
                self.subTest(label),
                self.assertRaisesRegex(HarnessControlError, "positive limit and byte budget"),
            ):
                await ledger.operation_timeline("epoch-1", **kwargs)  # type: ignore[arg-type]
        # ... and a page that does have a size still answers.
        self.assertEqual(len((await ledger.operation_timeline("epoch-1")).items), 1)

    async def test_an_ambiguous_setter_with_no_recorded_value_settles_nobody(self) -> None:
        """The ambiguity barrier is installed unconditionally; a set-result is not invented.

        ``HarnessSubmissionAuthority`` never builds this shape -- every setter it admits
        carries the value the caller asked for -- but ``OperationRecord`` is a plain mutable
        dataclass whose fields are independently assignable, so the state is representable
        and the projection has to answer for it. Reporting ``requested_value=""`` to a
        waiter would be the authority telling a caller it asked for something it did not.
        """
        ledger = self._ledger()
        record = self._enrol(ledger, "set-model", "unknown")
        record.result_future = asyncio.get_running_loop().create_future()

        record.mark_unknown("adapter went away mid-set", "vendor-7", at="LATER", bridge_epoch="e")

        self.assertEqual(record.state, "unknown")
        self.assertEqual(record.detail, "adapter went away mid-set")
        self.assertEqual(record.vendor_correlation_id, "vendor-7")
        self.assertEqual(record.updated_at, "LATER")
        self.assertFalse(record.result_future.done())


if __name__ == "__main__":
    unittest.main()
