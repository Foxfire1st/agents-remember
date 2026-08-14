from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from agents_remember.errors import HarnessBridgeEpochMismatchError, HarnessControlError
from agents_remember.models.conversations.control_wire import (
    ControlIdentity,
    operation_timeline_item_json,
    operation_timeline_item_wire_bytes,
)
from agents_remember.models.conversations.evidence import (
    AR_EVIDENCE_KEY,
    EVIDENCE_PAGE_BYTE_BUDGET,
)
from agents_remember.serving.harness_control_bridge import BridgeLimits, HarnessControlBridge
from agents_remember.serving.harness_control_claude import ClaudeStreamJsonAdapter
from agents_remember.serving.harness_control_client import (
    ControlSubmission,
    interrupt_control,
    read_operation_timeline,
    read_submission_authority,
    set_control_effort,
    set_control_model,
    submit_control_prompt,
)
from agents_remember.serving.harness_control_ipc import HarnessControlServer, LocalControlEndpoint
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter
from test_harness_control_plane import (
    NOW,
    _claude_fixture,
    _claude_replay,
    _ControlledEntry,
    _drive_completions,
    _FakeClaudeTransport,
    _FakePiTransport,
    _identity,
    _launch,
    _obj,
    _pi_active_operation,
    _SetterAdapter,
    _settle,
)


class PiInterruptTests(unittest.IsolatedAsyncioTestCase):
    async def test_abort_write_guard_replay_and_successor_refusal(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=lambda: transport, clock=lambda: NOW)
        await adapter.start(_launch(_identity("pi-int"), harness_id="pi"))
        try:
            operation = await _pi_active_operation(adapter, "op-a")
            result = await adapter.interrupt(turn_id=None, expected_operation_id="op-a")
            self.assertEqual(result.acknowledgement, "accepted")
            self.assertEqual(result.operation, operation)
            aborts = [command for command in transport.commands if command["type"] == "abort"]
            self.assertEqual(len(aborts), 1)
            self.assertEqual(result.vendor_correlation_id, aborts[0]["id"])
            # Matching replay: same pair, first acknowledgement, zero additional writes.
            replay = await adapter.interrupt(turn_id=None, expected_operation_id="op-a")
            self.assertEqual(replay, result)
            self.assertEqual(
                len([command for command in transport.commands if command["type"] == "abort"]), 1
            )
            # Mismatch guard: typed before any write.
            with self.assertRaises(HarnessControlError):
                await adapter.interrupt(turn_id=None, expected_operation_id="op-other")
            with self.assertRaises(HarnessControlError):
                await adapter.interrupt(turn_id="turn-1", expected_operation_id=None)
            self.assertEqual(
                len([command for command in transport.commands if command["type"] == "abort"]), 1
            )
            # Operation A settles; successor B starts. A stale reconcile for A must fail
            # typed with ZERO additional abort writes (the successor-operation rule).
            events = adapter.subscribe()
            transport.emit({"type": "agent_settled"})
            settled = await asyncio.wait_for(anext(events), timeout=1.0)
            self.assertEqual(settled.kind, "completed")
            self.assertIsNone(adapter._active_operation)
            await _pi_active_operation(adapter, "op-b")
            with self.assertRaises(HarnessControlError):
                await adapter.interrupt(turn_id=None, expected_operation_id="op-a")
            self.assertEqual(
                len([command for command in transport.commands if command["type"] == "abort"]), 1
            )
            # A matching reconcile against the still-active successor writes exactly once.
            second = await adapter.interrupt(turn_id=None, expected_operation_id="op-b")
            self.assertEqual(second.acknowledgement, "accepted")
            self.assertEqual(
                len([command for command in transport.commands if command["type"] == "abort"]), 2
            )
            replay_b = await adapter.interrupt(turn_id=None, expected_operation_id="op-b")
            self.assertEqual(replay_b, second)
            self.assertEqual(
                len([command for command in transport.commands if command["type"] == "abort"]), 2
            )
        finally:
            await adapter.stop("forced")

    async def test_abort_no_active_operation_typed(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=lambda: transport, clock=lambda: NOW)
        await adapter.start(_launch(_identity("pi-int-idle"), harness_id="pi"))
        try:
            with self.assertRaises(HarnessControlError):
                await adapter.interrupt(turn_id=None, expected_operation_id=None)
            self.assertEqual(
                [command for command in transport.commands if command["type"] == "abort"], []
            )
        finally:
            await adapter.stop("forced")

    async def test_contentless_message_end_crosses_as_evidence_without_failing(self) -> None:
        """An interrupted turn's textless message_end mints no entry and no failure."""

        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=lambda: transport, clock=lambda: NOW)
        await adapter.start(_launch(_identity("pi-aborted-msg"), harness_id="pi"))
        events = adapter.subscribe()
        try:
            transport.emit(
                {
                    "type": "message_end",
                    "message": {"role": "assistant", "content": []},
                }
            )
            event = await asyncio.wait_for(anext(events), timeout=1.0)
            self.assertEqual(event.kind, "pi:message_end")
            self.assertEqual(event.transcript, ())
            self.assertEqual(_obj(event.raw[AR_EVIDENCE_KEY])["type"], "message_end")
            self.assertEqual(_obj(event.raw["piEvent"])["type"], "message_end")
            self.assertEqual((await adapter.snapshot()).control, "ready")
            transport.emit({"type": "message_end", "message": {"role": "system", "content": []}})
            failure = await asyncio.wait_for(anext(events), timeout=1.0)
            self.assertEqual(failure.kind, "failed")
            assert failure.snapshot is not None
            self.assertIn("user or assistant role", str(failure.snapshot.raw.get("adapterError")))
        finally:
            await adapter.stop("forced")

    async def test_abort_native_failure_is_rejected_acknowledgement(self) -> None:
        transport = _FakePiTransport()
        transport.abort_results.append(False)
        adapter = PiRpcAdapter(transport_factory=lambda: transport, clock=lambda: NOW)
        await adapter.start(_launch(_identity("pi-int-rpc"), harness_id="pi"))
        try:
            await _pi_active_operation(adapter, "op-c")
            result = await adapter.interrupt(turn_id=None, expected_operation_id="op-c")
            self.assertEqual(result.acknowledgement, "rejected")
            self.assertIn("nothing to abort", result.detail or "")
        finally:
            await adapter.stop("forced")


class ClaudeInterruptTests(unittest.IsolatedAsyncioTestCase):
    """The REAL claude adapter behind the bridge + IPC interrupt route (probe-locked shape)."""

    async def _serve(self, adapter, identity: ControlIdentity, tmp: str):
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
        await bridge.start(_launch(identity, harness_id="claude"))
        endpoint = LocalControlEndpoint.for_session(Path(tmp), identity)
        server = HarnessControlServer(endpoint, bridge)
        await server.start()
        return bridge, server, _ControlledEntry(identity, endpoint.path)

    async def _active_turn(self, transport: _FakeClaudeTransport, bridge) -> None:
        submission = asyncio.create_task(
            bridge.submissions().submit(
                bridge.prompt("write an essay", source="terminal", request_id="req-int-1")
            )
        )
        await transport.wait_for_writes(4)
        transport.feed(_claude_replay(transport.writes[3]))
        receipt = await asyncio.wait_for(submission, timeout=1.0)
        assert receipt.acceptance == "immediate"

    async def test_interrupt_routes_through_bridge_and_ipc_and_settles_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("claude-int-ipc")
            transport = _FakeClaudeTransport(_claude_fixture("2.1.210", "initialization.jsonl"))
            adapter = ClaudeStreamJsonAdapter(
                transport_factory=lambda: transport, clock=lambda: NOW
            )
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                await self._active_turn(transport, bridge)
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                interrupt_task = asyncio.create_task(
                    asyncio.to_thread(
                        interrupt_control,
                        entry,
                        expected_bridge_epoch=descriptor.bridge_epoch,
                    )
                )
                await transport.wait_for_writes(5)
                self.assertEqual(
                    transport.writes[4],
                    {
                        "type": "control_request",
                        "request_id": "ar-claude-interrupt-1",
                        "request": {"subtype": "interrupt"},
                    },
                )
                control_response, aborted, marker, result = _claude_fixture(
                    "2.1.217", "interrupt.jsonl"
                )
                transport.feed(control_response)
                acknowledgement = await asyncio.wait_for(interrupt_task, timeout=5.0)
                self.assertEqual(acknowledgement.acknowledgement, "accepted")
                self.assertEqual(acknowledgement.bridge_epoch, descriptor.bridge_epoch)
                self.assertEqual(acknowledgement.vendor_correlation_id, "ar-claude-interrupt-1")
                assert acknowledgement.operation is not None
                self.assertEqual(acknowledgement.operation.kind, "prompt")

                transport.feed(aborted)
                transport.feed(marker)
                transport.feed(result)
                await _settle()
                results = [entry for entry in bridge.transcript() if entry.role == "result"]
                self.assertEqual(len(results), 1)
                assert results[0].terminal_result is not None
                self.assertEqual(results[0].terminal_result.outcome, "cancelled")
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_interrupt_without_an_active_turn_fails_typed_over_ipc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("claude-int-idle")
            transport = _FakeClaudeTransport(_claude_fixture("2.1.210", "initialization.jsonl"))
            adapter = ClaudeStreamJsonAdapter(
                transport_factory=lambda: transport, clock=lambda: NOW
            )
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                with self.assertRaisesRegex(HarnessControlError, "no active Claude turn"):
                    await asyncio.to_thread(
                        interrupt_control,
                        entry,
                        expected_bridge_epoch=descriptor.bridge_epoch,
                    )
                self.assertEqual(len(transport.writes), 3)
            finally:
                await server.close()
                await bridge.stop("forced")


class OperationTimelineTests(unittest.IsolatedAsyncioTestCase):
    async def _serve(self, adapter, identity: ControlIdentity, tmp: str, **bridge_kwargs):
        bridge = HarnessControlBridge(identity, adapter, **bridge_kwargs, clock=lambda: NOW)
        await bridge.start(_launch(identity))
        endpoint = LocalControlEndpoint.for_session(Path(tmp), identity)
        server = HarnessControlServer(endpoint, bridge)
        await server.start()
        return bridge, server, _ControlledEntry(identity, endpoint.path)

    async def test_all_sources_and_kinds_enumerate_never_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("timeline-all")
            adapter = _SetterAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "cockpit body",
                    ControlSubmission(
                        source="cockpit", request_id="tl-cockpit", expected_bridge_epoch=epoch
                    ),
                )
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "terminal body",
                    ControlSubmission(source="terminal", request_id="tl-terminal"),
                )
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "durable body",
                    ControlSubmission(source="durable", request_id="tl-durable"),
                )
                await _drive_completions(adapter, ["tl-cockpit", "tl-terminal", "tl-durable"])
                await asyncio.to_thread(set_control_model, entry, "model-b")
                await asyncio.to_thread(set_control_effort, entry, "max")
                page = await asyncio.to_thread(
                    read_operation_timeline, entry, expected_bridge_epoch=epoch
                )
                self.assertEqual(page.bridge_epoch, epoch)
                self.assertEqual(len(page.items), 5)
                kinds = {item.operation_id: item.kind for item in page.items}
                self.assertEqual(kinds["tl-cockpit"], "prompt")
                setter_kinds = {item.kind for item in page.items if item.kind != "prompt"}
                self.assertEqual(setter_kinds, {"set-model", "set-effort"})
                sources = {item.operation_id: item.source for item in page.items}
                self.assertEqual(sources["tl-cockpit"], "cockpit")
                self.assertEqual(sources["tl-terminal"], "terminal")
                self.assertEqual(sources["tl-durable"], "durable")
                setter_sources = {item.source for item in page.items if item.kind != "prompt"}
                self.assertEqual(setter_sources, {None})
                sequences = [item.sequence for item in page.items]
                self.assertEqual(sequences, sorted(sequences))
                self.assertEqual(page.latest_sequence, max(sequences))
                # Never bodies: exact ten-key shape, no text, no setter values.
                for item in page.items:
                    serialized = operation_timeline_item_json(item)
                    self.assertEqual(
                        set(serialized),
                        {
                            "operationId",
                            "kind",
                            "source",
                            "state",
                            "sequence",
                            "submittedAt",
                            "updatedAt",
                            "acceptedAt",
                            "payloadDigestPresent",
                            "vendorCorrelationId",
                        },
                    )
                prompt_states = {item.operation_id: item.state for item in page.items[:3]}
                self.assertTrue(
                    all(
                        state in {"delivered", "queued", "dispatching"}
                        for state in prompt_states.values()
                    )
                )
                self.assertTrue(all(item.payload_digest_present for item in page.items[:3]))
                self.assertFalse(any(item.payload_digest_present is None for item in page.items))
            finally:
                await server.close()
                await bridge.stop("forced")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane_assets.py:338).
    async def test_paged_union_no_overlap_gap_tolerant_and_epoch_flip_typed(
        self,
    ) -> None:  # pragma: no cover
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("timeline-page")
            adapter = _SetterAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                for index in range(12):
                    await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        f"body-{index}-{'y' * 200}",
                        ControlSubmission(source="durable", request_id=f"tl-p-{index}"),
                    )
                chained: list[int] = []
                after = 0
                truncated_pages = 0
                for _ in range(20):
                    page = await asyncio.to_thread(
                        read_operation_timeline,
                        entry,
                        expected_bridge_epoch=epoch,
                        after_sequence=after,
                        limit=3,
                    )
                    chained.extend(item.sequence for item in page.items)
                    if not page.truncated:
                        break
                    truncated_pages += 1
                    after = page.items[-1].sequence
                else:
                    self.fail("paged enumeration never terminated")
                self.assertEqual(truncated_pages, 3)
                self.assertEqual(len(chained), len(set(chained)))
                self.assertEqual(chained, list(range(1, 13)))
                with self.assertRaises(HarnessBridgeEpochMismatchError):
                    await asyncio.to_thread(
                        read_operation_timeline,
                        entry,
                        expected_bridge_epoch="not-the-epoch",
                    )
                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(
                        read_operation_timeline,
                        entry,
                        expected_bridge_epoch=epoch,
                        after_sequence="opaque-cursor",  # type: ignore[arg-type]
                    )
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_eviction_floor_disclosed_and_rereads_converge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("timeline-evict")
            adapter = _SetterAdapter()
            bridge, server, entry = await self._serve(
                adapter, identity, tmp, limits=BridgeLimits(submission=4, queue=4)
            )
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                ids = [f"tl-e-{index}" for index in range(9)]
                for request_id in ids:
                    await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        f"evict-{request_id}",
                        ControlSubmission(source="durable", request_id=request_id),
                    )
                    await _drive_completions(adapter, [request_id])
                page = await asyncio.to_thread(
                    read_operation_timeline, entry, expected_bridge_epoch=epoch
                )
                self.assertGreater(page.evicted_before_sequence, 0)
                self.assertGreaterEqual(page.latest_sequence, 9)
                retained = [item.sequence for item in page.items]
                self.assertTrue(
                    all(sequence > page.evicted_before_sequence for sequence in retained)
                )
                # A re-read from zero converges to the current retained union; the floor
                # honestly discloses the jump instead of pretending completeness.
                reread = await asyncio.to_thread(
                    read_operation_timeline,
                    entry,
                    expected_bridge_epoch=epoch,
                    after_sequence=0,
                )
                self.assertEqual([item.sequence for item in reread.items], retained)
            finally:
                await server.close()
                await bridge.stop("forced")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane_assets.py:432).
    async def test_full_ledger_budget_edge_pages_within_measured_budget(
        self,
    ) -> None:  # pragma: no cover
        with tempfile.TemporaryDirectory():
            identity = _identity("timeline-budget")
            adapter = _SetterAdapter()
            bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
            await bridge.start(_launch(identity))
            try:
                epoch = bridge.submissions().bridge_epoch
                ids = [
                    f"caller-minted-operation-id-{index:03d}-" + "x" * 64 for index in range(256)
                ]
                for request_id in ids:
                    await bridge.submissions().submit(
                        bridge.prompt(
                            f"payload-{request_id[:12]}-{'z' * 24}",
                            source="durable",
                            request_id=request_id,
                        )
                    )
                    await _drive_completions(adapter, [request_id])
                chained: list[int] = []
                after = 0
                pages = 0
                worst_page_bytes = 0
                while True:
                    page = await bridge.submissions().ledger.operation_timeline(
                        epoch,
                        after_sequence=after,
                        byte_budget=EVIDENCE_PAGE_BYTE_BUDGET,
                    )
                    pages += 1
                    page_bytes = sum(
                        operation_timeline_item_wire_bytes(item) for item in page.items
                    )
                    worst_page_bytes = max(worst_page_bytes, page_bytes)
                    chained.extend(item.sequence for item in page.items)
                    if not page.truncated:
                        break
                    after = page.items[-1].sequence
                    if pages > 64:
                        self.fail("budget-paged enumeration never terminated")
                self.assertEqual(chained, list(range(1, 257)))
                self.assertGreater(pages, 1)
                self.assertLessEqual(worst_page_bytes, EVIDENCE_PAGE_BYTE_BUDGET)
            finally:
                await bridge.stop("forced")
