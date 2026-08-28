from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncGenerator, Mapping
from pathlib import Path
from typing import cast

from agents_remember.errors import (
    HarnessAdapterBusyError,
    HarnessAdapterDisconnectedError,
    HarnessControlError,
)
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
)
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_models import (
    AdapterEvent,
    InteractionResponse,
)
from agents_remember.serving.pi_rpc_adapter import PiAdapterLimits, PiRpcAdapter
from agents_remember.serving.pi_rpc_protocol import PiRpcJsonlDecoder
from test_pi_rpc_adapter import (
    _direct_submit,
    _FakePiTransport,
    _identity,
    _launch,
    _operation,
    _prompt,
    _TransportSequence,
)

ACTIVITY_FIXTURE = Path(__file__).parent / "fixtures/pi_rpc/activity.jsonl"


class PiRpcAdapterTests2(unittest.IsolatedAsyncioTestCase):
    async def test_stale_idle_window_rejects_without_native_queue_or_prompt_bytes(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=_TransportSequence(transport))
        await adapter.start(_launch())
        try:
            operation = _operation("stale-window")
            await adapter.preflight_operation(operation)

            def become_busy(command: Mapping[str, object]) -> None:
                if command.get("type") == "prompt":
                    transport.emit({"type": "agent_start"})

            transport.before_write_hook = become_busy
            with self.assertRaisesRegex(HarnessAdapterBusyError, "received an event"):
                await adapter.submit(
                    _prompt(
                        "stale-window",
                        source="terminal",
                        operation=operation,
                    )
                )
            prompts = [item for item in transport.commands if item["type"] == "prompt"]
            self.assertEqual(prompts, [])
            self.assertIsNone(adapter._active_operation)
        finally:
            await adapter.stop("forced")

    async def test_get_state_drives_stream_compaction_and_pending_activity(self) -> None:
        cases = (
            ({"isStreaming": True, "pendingMessageCount": 2}, "running"),
            ({"isCompacting": True}, "settling"),
            ({"pendingMessageCount": 2}, "settling"),
        )
        for updates, expected in cases:
            with self.subTest(updates=updates):
                transport = _FakePiTransport()
                transport.session.update(updates)
                adapter = PiRpcAdapter(transport_factory=_TransportSequence(transport))
                handshake = await adapter.start(_launch())
                try:
                    self.assertEqual(handshake.snapshot.activity, expected)
                    self.assertEqual(handshake.snapshot.acceptance, "queued")
                finally:
                    await adapter.stop("forced")

    async def test_retry_compaction_and_agent_settled_are_not_early_idle(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=_TransportSequence(transport))
        await adapter.start(_launch())
        receipt = await _direct_submit(adapter, "activity-fixture")
        self.assertEqual(receipt.acceptance, "immediate")
        stream = cast(AsyncGenerator[AdapterEvent], adapter.subscribe())
        decoder = PiRpcJsonlDecoder()
        frames: list[Mapping[str, object]] = []
        for line in ACTIVITY_FIXTURE.read_bytes().splitlines(keepends=True):
            frames.extend(decoder.feed(line))
        try:
            events = []
            for frame in frames:
                transport.emit(frame)
                events.append(await asyncio.wait_for(anext(stream), timeout=1.0))
            snapshots = [event.snapshot for event in events]
            assert all(snapshot is not None for snapshot in snapshots)
            typed_snapshots = cast(list[AdapterSnapshot], snapshots)
            self.assertEqual(typed_snapshots[0].activity, "running")
            self.assertTrue(
                all(snapshot.activity == "settling" for snapshot in typed_snapshots[1:5])
            )
            self.assertEqual(events[-1].kind, "completed")
            self.assertEqual(events[-1].operation, _operation("activity-fixture"))
            final_snapshot = events[-1].snapshot
            assert final_snapshot is not None
            self.assertEqual(final_snapshot.activity, "idle")
            self.assertEqual(final_snapshot.raw["pendingMessageCount"], 0)
        finally:
            await adapter.stop("forced")
            await stream.aclose()

    async def test_extension_ui_round_trip_and_reclamation_scale(self) -> None:
        for size in (8, 64):
            with self.subTest(size=size):
                transport = _FakePiTransport()
                adapter = PiRpcAdapter(
                    transport_factory=_TransportSequence(transport),
                    limits=PiAdapterLimits(interaction=4),
                )
                await adapter.start(_launch())
                operation = _operation(f"interaction-{size}")
                await adapter.preflight_operation(operation)
                await adapter.submit(
                    _prompt(
                        f"interaction-{size}",
                        operation=operation,
                    )
                )
                stream = cast(AsyncGenerator[AdapterEvent], adapter.subscribe())
                try:
                    for index in range(size):
                        interaction_id = f"confirm-{index}"
                        transport.emit(
                            {
                                "type": "extension_ui_request",
                                "id": interaction_id,
                                "method": "confirm",
                                "title": "Continue?",
                                "message": "Confirm action",
                            }
                        )
                        blocked = await asyncio.wait_for(anext(stream), timeout=1.0)
                        blocked_snapshot = blocked.snapshot
                        assert blocked_snapshot is not None
                        pending = blocked_snapshot.pending_interaction
                        assert pending is not None
                        self.assertEqual(pending.interaction_id, interaction_id)
                        await adapter.respond(
                            InteractionResponse(
                                interaction_id=interaction_id,
                                response="true",
                                responded_at="2026-07-14T09:03:00+00:00",
                                operation=operation,
                            )
                        )
                        self.assertLessEqual(adapter._retained_interaction_count, 1)
                    responses = [
                        item
                        for item in transport.commands
                        if item["type"] == "extension_ui_response"
                    ]
                    self.assertEqual(len(responses), size)
                    self.assertTrue(all(item["confirmed"] is True for item in responses))
                    self.assertEqual(adapter._retained_interaction_count, 0)
                finally:
                    await adapter.stop("forced")
                    await stream.aclose()

    async def test_certified_pre_write_disconnect_stays_authoritatively_queued(self) -> None:
        transport = _FakePiTransport()
        transport.prompt_failures.append(
            HarnessAdapterDisconnectedError("closed before write", may_have_sent=False)
        )
        adapter = PiRpcAdapter(transport_factory=_TransportSequence(transport))
        bridge = HarnessControlBridge(_identity(), adapter)
        await bridge.start(_launch())
        try:
            receipt = await bridge.submissions().submit(
                bridge.prompt("before", source="durable", request_id="before")
            )
            self.assertEqual(receipt.acceptance, "queued")
            self.assertEqual(
                len([item for item in transport.commands if item["type"] == "prompt"]), 0
            )
        finally:
            await bridge.stop("forced")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_adapter_ops_2.py:188).
    async def test_disconnect_before_response_reconnects_by_session_and_entries_without_resend(  # pragma: no cover
        self,
    ) -> None:
        base_entry = {
            "type": "message",
            "id": "entry-0",
            "parentId": None,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "base"}]},
        }
        accepted_entry = {
            "type": "message",
            "id": "entry-1",
            "parentId": "entry-0",
            "message": {"role": "user", "content": [{"type": "text", "text": "ambiguous"}]},
        }
        first = _FakePiTransport(entries=[base_entry], leaf_id="entry-0")
        first.prompt_failures.append(
            HarnessAdapterDisconnectedError("closed after write", may_have_sent=True)
        )
        second = _FakePiTransport(entries=[base_entry, accepted_entry], leaf_id="entry-1")
        adapter = PiRpcAdapter(transport_factory=_TransportSequence(first, second))
        bridge = HarnessControlBridge(_identity(), adapter)
        await bridge.start(_launch())
        try:
            receipt = await bridge.submissions().submit(
                bridge.prompt("ambiguous", source="durable", request_id="ambiguous-1")
            )
            self.assertEqual(receipt.acceptance, "unknown")
            reconciled = await bridge.submissions().reconcile("ambiguous-1")
            self.assertEqual(reconciled.state, "accepted")
            detail = reconciled.detail
            assert detail is not None
            self.assertIn("no resend", detail)
            self.assertIn("--session", second.launches[0].argv)
            self.assertIn("/sessions/pi-session-1.jsonl", second.launches[0].argv)
            self.assertEqual(second.launches[0].cwd, _launch().cwd)
            self.assertEqual(second.launches[0].env, _launch().env)
            for preserved in (
                "--provider",
                "anthropic",
                "--model",
                "anthropic/claude-test",
                "--thinking",
                "high",
                "--no-extensions",
            ):
                self.assertIn(preserved, second.launches[0].argv)
            self.assertFalse(any(item["type"] == "prompt" for item in second.commands))
            self.assertEqual(len([item for item in first.commands if item["type"] == "prompt"]), 1)
            for _ in range(10):
                if bridge.snapshot().control == "ready":
                    break
                await asyncio.sleep(0)
            self.assertEqual(bridge.snapshot().control, "ready")
            second.emit({"type": "agent_start"})
            for _ in range(10):
                if bridge.snapshot().activity == "running":
                    break
                await asyncio.sleep(0)
            self.assertEqual(bridge.snapshot().activity, "running")
        finally:
            await bridge.stop("forced")

    async def test_disconnect_after_ack_keeps_correlated_acceptance_without_resend(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=_TransportSequence(transport))
        bridge = HarnessControlBridge(_identity(), adapter)
        await bridge.start(_launch())
        try:
            receipt = await bridge.submissions().submit(
                bridge.prompt("acked", source="durable", request_id="acked-1")
            )
            self.assertEqual(receipt.acceptance, "immediate")
            transport.fail_events(
                HarnessAdapterDisconnectedError("closed after ack", may_have_sent=False)
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(bridge.snapshot().control, "disconnected")
            self.assertEqual(
                len([item for item in transport.commands if item["type"] == "prompt"]), 1
            )
        finally:
            await bridge.stop("forced")

    async def test_malformed_transport_frame_fails_adapter_loudly(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=_TransportSequence(transport))
        await adapter.start(_launch())
        stream = cast(AsyncGenerator[AdapterEvent], adapter.subscribe())
        try:
            transport.fail_events(HarnessControlError("malformed Pi RPC JSONL frame"))
            failed = await asyncio.wait_for(anext(stream), timeout=1.0)
            self.assertEqual(failed.kind, "failed")
            failed_snapshot = failed.snapshot
            assert failed_snapshot is not None
            self.assertEqual(failed_snapshot.control, "failed")
        finally:
            await adapter.stop("forced")
            await stream.aclose()
