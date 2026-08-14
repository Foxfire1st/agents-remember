from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace

from agents_remember.errors import HarnessControlError, HarnessRequestConflictError
from agents_remember.models.conversations.control_wire import (
    AcceptanceState,
)
from agents_remember.serving.harness_control_adapter import (
    HarnessProtocolRegistry,
    protocol_adapter_status,
)
from agents_remember.serving.harness_control_bridge import BridgeLimits, HarnessControlBridge
from agents_remember.serving.harness_control_models import (
    AdapterEvent,
    ReconciliationResult,
    ReconciliationState,
    TranscriptEntry,
)
from test_harness_control import (
    _BlockingSubmitAdapter,
    _FakeAdapter,
    _identity,
    _launch,
    _settle_events,
)


class HarnessControlConformanceTests2(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_before_and_after_send_never_blindly_resends(self) -> None:
        identity = _identity()
        before_adapter = _FakeAdapter()
        before_adapter.disconnects.append(False)
        before_bridge = HarnessControlBridge(identity, before_adapter)
        await before_bridge.start(_launch(identity))
        try:
            before = await before_bridge.submissions().submit(
                before_bridge.prompt("before", source="terminal", request_id="before")
            )
            self.assertEqual(before.acceptance, "queued")
            self.assertEqual(len(before_adapter.submissions), 1)
        finally:
            await before_bridge.stop("forced")

        after_adapter = _FakeAdapter()
        after_adapter.disconnects.append(True)
        after_bridge = HarnessControlBridge(identity, after_adapter)
        await after_bridge.start(_launch(identity))
        try:
            after = await after_bridge.submissions().submit(
                after_bridge.prompt("after", source="durable", request_id="after")
            )
            self.assertEqual(after.acceptance, "unknown")
            self.assertEqual(len(after_adapter.submissions), 1)

            after_adapter.reconciliations["after"] = ReconciliationResult(
                request_id="after",
                state="accepted",
                reconciled_at="2026-07-13T18:05:00+00:00",
                vendor_correlation_id="vendor-after",
            )
            reconciled = await after_bridge.submissions().reconcile("after")
            self.assertEqual(reconciled.state, "accepted")
            self.assertEqual(len(after_adapter.submissions), 1)
        finally:
            await after_bridge.stop("forced")

    async def test_duplicate_request_id_returns_retained_result_without_resubmission(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            first = await bridge.submissions().submit(
                bridge.prompt("only once", source="terminal", request_id="request-duplicate")
            )
            self.assertEqual(first.acceptance, "immediate")
            duplicate = await bridge.submissions().submit(
                bridge.prompt(
                    "only once",
                    source="terminal",
                    request_id="request-duplicate",
                )
            )
            self.assertEqual(duplicate, first)
            with self.assertRaises(HarnessRequestConflictError):
                await bridge.submissions().submit(
                    bridge.prompt(
                        "must not replace the first payload",
                        source="terminal",
                        request_id="request-duplicate",
                    )
                )
            self.assertEqual(
                [request.request_id for request in adapter.submissions],
                ["request-duplicate"],
            )
            self.assertEqual(adapter.submissions[0].text, "only once")
        finally:
            await bridge.stop("forced")

    async def test_dispatching_duplicate_returns_unknown_without_resubmission(self) -> None:
        identity = _identity()
        adapter = _BlockingSubmitAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            first = asyncio.create_task(
                bridge.submissions().submit(
                    bridge.prompt("first payload", source="terminal", request_id="pending-id")
                )
            )
            await asyncio.wait_for(adapter.submit_started.wait(), timeout=1.0)
            duplicate = asyncio.create_task(
                bridge.submissions().submit(
                    bridge.prompt(
                        "first payload",
                        source="terminal",
                        request_id="pending-id",
                    )
                )
            )
            await asyncio.sleep(0)
            self.assertTrue(duplicate.done())
            duplicate_receipt = await duplicate
            self.assertEqual(duplicate_receipt.acceptance, "unknown")
            adapter.release_submit.set()
            first_receipt = await first
            self.assertEqual(first_receipt.acceptance, "immediate")
            self.assertEqual(len(adapter.submissions), 1)
            self.assertEqual(adapter.submissions[0].text, "first payload")
        finally:
            adapter.release_submit.set()
            await bridge.stop("forced")

    async def test_known_receipts_reconcile_without_native_reconciliation(self) -> None:
        cases: tuple[tuple[AcceptanceState, ReconciliationState], ...] = (
            ("immediate", "accepted"),
            ("rejected", "rejected"),
            ("unsupported", "unsupported"),
        )
        for acceptance, state in cases:
            with self.subTest(acceptance=acceptance):
                identity = _identity(f"known-{acceptance}")
                adapter = _FakeAdapter()
                adapter.acceptances.append(acceptance)
                bridge = HarnessControlBridge(identity, adapter)
                await bridge.start(_launch(identity))
                try:
                    receipt = await bridge.submissions().submit(
                        bridge.prompt("payload", source="terminal", request_id=acceptance)
                    )
                    result = await bridge.submissions().reconcile(acceptance)
                    self.assertEqual((receipt.acceptance, result.state), (acceptance, state))
                    self.assertEqual(adapter.reconciliation_requests, [])
                finally:
                    await bridge.stop("forced")

        identity = _identity("known-queued")
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            await bridge.submissions().submit(
                bridge.prompt("active", source="terminal", request_id="active")
            )
            queued = await bridge.submissions().submit(
                bridge.prompt("payload", source="terminal", request_id="queued")
            )
            result = await bridge.submissions().reconcile("queued")
            self.assertEqual((queued.acceptance, result.state), ("queued", "accepted"))
            self.assertEqual(adapter.reconciliation_requests, [])
        finally:
            await bridge.stop("forced")

    async def test_operator_resolution_and_ambiguous_ledger_are_bounded(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        adapter.disconnects.extend((True, True))
        bridge = HarnessControlBridge(identity, adapter, limits=BridgeLimits(queue=2, submission=2))
        await bridge.start(_launch(identity))
        try:
            first = await bridge.submissions().submit(
                bridge.prompt("prompt", source="durable", request_id="unknown-1")
            )
            second = await bridge.submissions().submit(
                bridge.prompt("prompt", source="durable", request_id="unknown-2")
            )
            self.assertEqual((first.acceptance, second.acceptance), ("unknown", "queued"))
            refused = await bridge.submissions().submit(
                bridge.prompt("third", source="durable", request_id="unknown-3")
            )
            self.assertEqual(refused.acceptance, "rejected")
            self.assertIn("ledger", refused.detail or "")
            self.assertEqual(len(adapter.submissions), 1)

            resolution = await bridge.submissions().resolve_unknown_prompt(
                "unknown-1", state="rejected", detail="operator confirmed no visible turn"
            )
            self.assertEqual(resolution.state, "rejected")
            assert adapter.current is not None
            adapter.emit(
                AdapterEvent(
                    sequence=1,
                    kind="state",
                    identity=identity,
                    created_at="runner-ready-again",
                    snapshot=adapter.current,
                )
            )
            await _settle_events()
            self.assertEqual(len(adapter.submissions), 2)
        finally:
            await bridge.stop("forced")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_conformance_2.py:216).
    async def test_unexpected_adapter_error_preserves_unknown_and_queued_commands(
        self,
    ) -> None:  # pragma: no cover
        identity = _identity()
        adapter = _BlockingSubmitAdapter(error=RuntimeError("probe failure"))
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        first = asyncio.create_task(
            bridge.submissions().submit(
                bridge.prompt("first", source="terminal", request_id="first")
            )
        )
        try:
            await asyncio.wait_for(adapter.submit_started.wait(), timeout=1.0)
            second = asyncio.create_task(
                bridge.submissions().submit(
                    bridge.prompt("second", source="durable", request_id="second")
                )
            )
            await asyncio.sleep(0)
            adapter.release_submit.set()
            first_receipt, second_receipt = await asyncio.gather(first, second)
            self.assertEqual(
                (first_receipt.acceptance, second_receipt.acceptance),
                ("unknown", "queued"),
            )
            self.assertEqual(bridge.snapshot().control, "disconnected")
            third = await bridge.submissions().submit(
                bridge.prompt("third", source="terminal", request_id="third")
            )
            self.assertEqual(third.acceptance, "queued")
            self.assertEqual(len(adapter.submissions), 0)
        finally:
            adapter.release_submit.set()
            if not first.done():
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)
            await bridge.stop("forced")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_conformance_2.py:253).
    async def test_graceful_stop_race_rejects_new_command_without_hanging(
        self,
    ) -> None:  # pragma: no cover
        identity = _identity()
        adapter = _BlockingSubmitAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        active = asyncio.create_task(
            bridge.submissions().submit(
                bridge.prompt("active", source="terminal", request_id="active")
            )
        )
        stop = None
        try:
            await asyncio.wait_for(adapter.submit_started.wait(), timeout=1.0)
            stop = asyncio.create_task(bridge.stop("graceful"))
            await asyncio.sleep(0)
            with self.assertRaisesRegex(HarnessControlError, "control bridge is stopped"):
                await asyncio.wait_for(
                    bridge.submissions().submit(
                        bridge.prompt("racing", source="durable", request_id="racing")
                    ),
                    timeout=1.0,
                )
            adapter.release_submit.set()
            self.assertEqual((await asyncio.wait_for(active, timeout=1.0)).acceptance, "immediate")
            await asyncio.wait_for(stop, timeout=1.0)
        finally:
            adapter.release_submit.set()
            if not active.done():
                active.cancel()
                await asyncio.gather(active, return_exceptions=True)
            if stop is not None and not stop.done():
                stop.cancel()
                await asyncio.gather(stop, return_exceptions=True)
            if bridge.snapshot().control != "disconnected":
                await bridge.stop("forced")

    async def test_force_stop_cancellation_drains_active_and_queued_commands(self) -> None:
        identity = _identity()
        adapter = _BlockingSubmitAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        active = asyncio.create_task(
            bridge.submissions().submit(
                bridge.prompt("active", source="terminal", request_id="active")
            )
        )
        await asyncio.wait_for(adapter.submit_started.wait(), timeout=1.0)
        queued = asyncio.create_task(
            bridge.submissions().submit(
                bridge.prompt("queued", source="durable", request_id="queued")
            )
        )
        await asyncio.sleep(0)
        await asyncio.wait_for(bridge.stop("forced"), timeout=1.0)
        with self.assertRaisesRegex(HarnessControlError, "submission authority stopped"):
            await asyncio.wait_for(active, timeout=1.0)
        self.assertEqual((await asyncio.wait_for(queued, timeout=1.0)).acceptance, "queued")

    async def test_evicted_reconciliation_fails_loudly_and_runner_survives(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter, limits=BridgeLimits(queue=1, submission=1))
        await bridge.start(_launch(identity))
        try:
            await bridge.submissions().submit(
                bridge.prompt("old", source="terminal", request_id="old")
            )
            adapter.complete("old")
            await _settle_events()
            await bridge.submissions().submit(
                bridge.prompt("new", source="terminal", request_id="new")
            )
            adapter.complete("new")
            await _settle_events()
            with self.assertRaisesRegex(HarnessControlError, "no longer retained"):
                await asyncio.wait_for(bridge.submissions().reconcile("old"), timeout=1.0)
            survived = await asyncio.wait_for(
                bridge.submissions().submit(
                    bridge.prompt("after", source="terminal", request_id="after")
                ),
                timeout=1.0,
            )
            self.assertEqual(survived.acceptance, "immediate")
        finally:
            await bridge.stop("forced")

    async def test_unsupported_submission_ledger_is_bounded_at_two_sizes(self) -> None:
        registry = HarnessProtocolRegistry()
        for total in (8, 64):
            identity = _identity(f"unsupported-{total}")
            bridge = HarnessControlBridge(
                identity,
                registry.create("settings-harness"),
                limits=BridgeLimits(queue=4, submission=4),
            )
            await bridge.start(replace(_launch(identity), harness_id="settings-harness"))
            try:
                for index in range(total):
                    receipt = await bridge.submissions().submit(
                        bridge.prompt(
                            f"message-{index}",
                            source="durable",
                            request_id=f"request-{index}",
                        )
                    )
                    self.assertEqual(receipt.acceptance, "unsupported")
                self.assertLessEqual(bridge.submissions().ledger.retained_record_count, 4)
            finally:
                await bridge.stop("forced")

    async def test_additive_event_is_retained_and_malformed_event_fails_loudly(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            adapter.emit(
                AdapterEvent(
                    sequence=1,
                    kind="future-vendor-detail",
                    identity=identity,
                    created_at="2026-07-13T18:00:00+00:00",
                    raw={"futureField": {"nested": True}},
                )
            )
            await _settle_events()
            self.assertEqual(bridge.snapshot().raw["futureField"], {"nested": True})
            self.assertEqual(bridge.snapshot().control, "ready")

            adapter.emit(
                AdapterEvent(
                    sequence=2,
                    kind="state",
                    identity=identity,
                    created_at="2026-07-13T18:01:00+00:00",
                )
            )
            await _settle_events()
            self.assertEqual(bridge.snapshot().control, "failed")
            self.assertIn("requires a snapshot", str(bridge.snapshot().raw["bridgeError"]))
        finally:
            await bridge.stop("forced")

    async def test_capability_and_identity_mismatch_force_adapter_shutdown(self) -> None:
        identity = _identity()
        missing = _FakeAdapter(capabilities=frozenset({"state-snapshot"}))
        bridge = HarnessControlBridge(identity, missing)
        with self.assertRaisesRegex(HarnessControlError, "capability mismatch"):
            await bridge.start(_launch(identity))
        self.assertEqual(missing.stop_modes, ["forced"])

        wrong = _FakeAdapter(handshake_identity=_identity("wrong-session"))
        bridge = HarnessControlBridge(identity, wrong)
        with self.assertRaisesRegex(HarnessControlError, "identity"):
            await bridge.start(_launch(identity))
        self.assertEqual(wrong.stop_modes, ["forced"])

    async def test_transcript_reclamation_is_bounded_at_two_input_sizes(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter, limits=BridgeLimits(transcript=3))
        await bridge.start(_launch(identity))
        try:
            first = tuple(
                TranscriptEntry(i, "assistant", f"line-{i}", f"time-{i}") for i in range(1, 3)
            )
            adapter.emit(AdapterEvent(1, "transcript", identity, "time-2", transcript=first))
            await _settle_events()
            self.assertEqual(len(bridge.transcript()), 2)

            second = tuple(
                TranscriptEntry(i, "assistant", f"line-{i}", f"time-{i}") for i in range(3, 8)
            )
            adapter.emit(AdapterEvent(2, "transcript", identity, "time-7", transcript=second))
            await _settle_events()
            self.assertEqual([entry.sequence for entry in bridge.transcript()], [5, 6, 7])
        finally:
            await bridge.stop("forced")

    async def test_graceful_forced_and_unsupported_shutdown(self) -> None:
        identity = _identity()
        graceful_adapter = _FakeAdapter()
        graceful = HarnessControlBridge(identity, graceful_adapter)
        await graceful.start(_launch(identity))
        await graceful.stop("graceful")
        self.assertEqual(graceful_adapter.stop_modes, ["graceful"])

        forced_adapter = _FakeAdapter()
        forced = HarnessControlBridge(identity, forced_adapter)
        await forced.start(_launch(identity))
        await forced.stop("forced")
        self.assertEqual(forced_adapter.stop_modes, ["forced"])

        registry = HarnessProtocolRegistry()
        unsupported = HarnessControlBridge(identity, registry.create("settings-harness"))
        snapshot = await unsupported.start(
            replace(_launch(identity), harness_id="settings-harness")
        )
        self.assertEqual(snapshot.control, "unsupported")
        receipt = await unsupported.submissions().submit(
            unsupported.prompt("hello", source="terminal", request_id="unsupported-1")
        )
        self.assertEqual(receipt.acceptance, "unsupported")
        self.assertEqual(protocol_adapter_status("settings-harness"), "unsupported")
        await unsupported.stop("graceful")
