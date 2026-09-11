from __future__ import annotations

import asyncio
import unittest
from collections import deque
from collections.abc import Mapping

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.pi_rpc_adapter import PiAdapterLimits, PiRpcAdapter
from test_pi_rpc_adapter import (
    _direct_set_effort,
    _direct_set_model,
    _direct_submit,
    _FakePiTransport,
    _identity,
    _launch,
    _operation,
    _TransportSequence,
)


class PiRpcAdapterTests1(unittest.IsolatedAsyncioTestCase):
    async def test_discover_is_token_free_and_stops_the_transient_rpc_process(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=_TransportSequence(transport))

        advertised = await adapter.discover(_launch())

        self.assertEqual(advertised.selected_model_key, "anthropic/claude-test")
        self.assertEqual(
            [command["type"] for command in transport.commands],
            ["get_state", "get_available_models"],
        )
        self.assertFalse(any(command["type"] == "prompt" for command in transport.commands))
        self.assertEqual(transport.stop_modes, ["forced"])

    async def test_catalog_failure_stops_and_resets_start_and_discover_processes(self) -> None:
        start_transport = _FakePiTransport()
        start_transport.models = []
        retry_transport = _FakePiTransport()
        adapter = PiRpcAdapter(
            transport_factory=_TransportSequence(start_transport, retry_transport)
        )

        with self.assertRaisesRegex(HarnessControlError, "absent from get_available_models"):
            await adapter.start(_launch())
        self.assertEqual(start_transport.stop_modes, ["forced"])

        handshake = await adapter.start(_launch())
        self.assertEqual(handshake.snapshot.control, "ready")
        await adapter.stop("forced")

        discover_transport = _FakePiTransport()
        discover_transport.models = []
        discover = PiRpcAdapter(transport_factory=_TransportSequence(discover_transport))
        with self.assertRaisesRegex(HarnessControlError, "absent from get_available_models"):
            await discover.discover(_launch())
        self.assertEqual(discover_transport.stop_modes, ["forced"])

    async def test_handshake_and_prompt_ack_preserve_launch_and_correlation(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(
            transport_factory=_TransportSequence(transport),
            clock=lambda: "2026-07-14T09:02:00+00:00",
        )
        handshake = await adapter.start(_launch())
        try:
            receipt = await _direct_submit(adapter, "request-1")
            self.assertEqual(handshake.snapshot.control, "ready")
            self.assertEqual(handshake.snapshot.vendor_session_id, "pi-session-1")
            self.assertEqual(handshake.adapter_id, "pi-rpc")
            self.assertEqual(handshake.raw["vendorProtocol"], "pi-rpc/jsonl")
            self.assertNotIn("piVersion", handshake.raw)
            self.assertEqual(receipt.acceptance, "immediate")
            self.assertEqual(receipt.vendor_correlation_id, "request-1")
            advertised = adapter.advertise()
            self.assertEqual(advertised.selected_model_key, "anthropic/claude-test")
            self.assertEqual(advertised.selected_effort, "high")
            self.assertEqual(
                [option.key for option in advertised.models[0].effort_options],
                ["off", "minimal", "low", "medium", "high", "max"],
            )
            self.assertEqual(
                [option.key for option in advertised.models[1].effort_options],
                ["off"],
            )
            safe_model = handshake.snapshot.raw["model"]
            assert isinstance(safe_model, dict)
            self.assertNotIn("headers", safe_model)
            self.assertEqual(
                transport.launches[0].argv, ("pi", "--mode", "rpc", *_launch().argv[1:])
            )
            prompt = next(command for command in transport.commands if command["type"] == "prompt")
            self.assertEqual(prompt["id"], "request-1")
            self.assertNotIn("streamingBehavior", prompt)
            self.assertEqual(
                [
                    command["type"]
                    for command in transport.commands
                    if command["type"] == "get_available_models"
                ],
                ["get_available_models"],
            )
        finally:
            await adapter.stop("forced")

    async def test_set_model_uses_exact_provider_id_and_vendor_error_readback(self) -> None:
        transport = _FakePiTransport()
        transport.models.append(
            {
                "id": "nested/model-id",
                "name": "Nested Test",
                "api": "test",
                "provider": "provider-x",
                "reasoning": True,
                "thinkingLevelMap": {"high": "high"},
            }
        )
        adapter = PiRpcAdapter(transport_factory=_TransportSequence(transport))
        await adapter.start(_launch())
        try:
            changed = await _direct_set_model(adapter, "provider-x/nested/model-id")
            self.assertEqual(
                (changed.ok, changed.acceptance, changed.effective_value),
                (True, "echo-verified", "provider-x/nested/model-id"),
            )
            set_command = next(
                command for command in transport.commands if command["type"] == "set_model"
            )
            self.assertEqual(set_command["provider"], "provider-x")
            self.assertEqual(set_command["modelId"], "nested/model-id")
            self.assertEqual(adapter.advertise().selected_model_key, "provider-x/nested/model-id")

            unknown = await _direct_set_model(
                adapter,
                "provider-x/not-authorized",
                sequence=2,
            )
            self.assertEqual((unknown.ok, unknown.acceptance), (False, "unsupported"))
            self.assertEqual(unknown.detail, "Model not found: provider-x/not-authorized")

            write_count = len([item for item in transport.commands if item["type"] == "set_model"])
            malformed = await _direct_set_model(
                adapter,
                "missing-provider-qualification",
                sequence=3,
            )
            self.assertEqual((malformed.ok, malformed.acceptance), (False, "unsupported"))
            self.assertEqual(
                len([item for item in transport.commands if item["type"] == "set_model"]),
                write_count,
            )
        finally:
            await adapter.stop("forced")

    async def test_set_thinking_reports_exact_and_clamped_readback_without_notification(
        self,
    ) -> None:
        transport = _FakePiTransport()
        thinking_map = transport.models[0]["thinkingLevelMap"]
        assert isinstance(thinking_map, dict)
        thinking_map["xhigh"] = "xhigh"
        transport.thinking_clamps["xhigh"] = "high"
        adapter = PiRpcAdapter(transport_factory=_TransportSequence(transport))
        await adapter.start(_launch())
        try:
            exact = await _direct_set_effort(adapter, "low")
            self.assertEqual(
                (exact.ok, exact.acceptance, exact.requested_value, exact.effective_value),
                (True, "echo-verified", "low", "low"),
            )

            transport.session["thinkingLevel"] = "high"
            clamped = await _direct_set_effort(adapter, "xhigh", sequence=2)
            self.assertEqual(
                (
                    clamped.ok,
                    clamped.acceptance,
                    clamped.requested_value,
                    clamped.effective_value,
                ),
                (True, "echo-verified", "xhigh", "high"),
            )
            self.assertIn("clamped", clamped.detail or "")
            self.assertTrue(transport.event_queue.empty())
            self.assertEqual(
                [command["type"] for command in transport.commands[-3:]],
                ["set_thinking_level", "get_state", "get_available_models"],
            )

            write_count = len(
                [item for item in transport.commands if item["type"] == "set_thinking_level"]
            )
            arbitrary = await _direct_set_effort(
                adapter,
                "vendor-invented-token",
                sequence=3,
            )
            self.assertEqual((arbitrary.ok, arbitrary.acceptance), (False, "unsupported"))
            self.assertEqual(
                len([item for item in transport.commands if item["type"] == "set_thinking_level"]),
                write_count,
            )
        finally:
            await adapter.stop("forced")

    async def test_model_then_effort_uses_new_model_gate(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=_TransportSequence(transport))
        await adapter.start(_launch())
        try:
            model = await _direct_set_model(adapter, "local/chat-test")
            self.assertEqual(model.acceptance, "echo-verified")
            self.assertEqual(adapter.advertise().selected_model_key, "local/chat-test")
            self.assertEqual(adapter.advertise().selected_effort, "off")

            effort = await _direct_set_effort(adapter, "high", sequence=2)
            self.assertEqual(
                (effort.ok, effort.acceptance, effort.effective_value),
                (False, "unsupported", None),
            )
            self.assertEqual(adapter.advertise().selected_effort, "off")
        finally:
            await adapter.stop("forced")

    async def test_set_timeout_never_claims_effect_without_readback(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=_TransportSequence(transport))
        await adapter.start(_launch())
        try:
            operation = _operation("set-model-timeout", "set-model")
            await adapter.preflight_operation(operation)
            transport.command_failures["get_state"] = deque([TimeoutError()])
            result = await adapter.set_model("local/chat-test", operation=operation)
            self.assertEqual((result.ok, result.acceptance), (False, "unknown"))
            self.assertIsNone(result.effective_value)
        finally:
            await adapter.stop("forced")

    async def test_mutation_timeouts_hold_unknown_blocker_until_explicit_resolution(self) -> None:
        for stalled_command in ("set_model", "get_state", "get_available_models"):
            with self.subTest(stalled_command=stalled_command):
                transport = _FakePiTransport()
                adapter = PiRpcAdapter(
                    transport_factory=_TransportSequence(transport),
                    limits=PiAdapterLimits(configuration_timeout_seconds=0.01),
                )
                bridge = HarnessControlBridge(_identity(), adapter)
                await bridge.start(_launch())
                try:
                    if stalled_command == "get_state":

                        def stall_readback(
                            command: Mapping[str, object],
                            target: _FakePiTransport = transport,
                        ) -> None:
                            if command.get("type") == "set_model":
                                target.command_hangs["get_state"] = 1
                                target.before_write_hook = None

                        transport.before_write_hook = stall_readback
                    else:
                        transport.command_hangs[stalled_command] = 1
                    timed_out = await asyncio.wait_for(
                        bridge.submissions().set_model("local/chat-test"),
                        timeout=1.0,
                    )
                    self.assertEqual(
                        (timed_out.ok, timed_out.acceptance, timed_out.effective_value),
                        (False, "unknown", None),
                    )
                    active = bridge._authority.active_operation
                    assert active is not None
                    self.assertEqual(active.kind, "set-model")
                    later_task = asyncio.create_task(
                        bridge.submissions().set_model("anthropic/claude-test")
                    )
                    await asyncio.sleep(0.02)
                    self.assertFalse(later_task.done())
                    await bridge.submissions().resolve_operation(
                        active.operation_id,
                        active.kind,
                        resolution="not-applied",
                        detail="test operator cleared the unknown mutation blocker",
                    )
                    later = await asyncio.wait_for(later_task, timeout=1.0)
                    self.assertEqual((later.ok, later.acceptance), (True, "echo-verified"))
                    self.assertEqual(
                        adapter.advertise().selected_model_key,
                        "anthropic/claude-test",
                    )
                finally:
                    await bridge.stop("forced")

    async def test_incoherent_pi_readback_never_promotes_or_breaks_advertise(self) -> None:
        clamp_transport = _FakePiTransport()
        thinking_map = clamp_transport.models[0]["thinkingLevelMap"]
        assert isinstance(thinking_map, dict)
        thinking_map["xhigh"] = "xhigh"
        clamp_transport.thinking_clamps["xhigh"] = "vendor-weird"
        clamp_adapter = PiRpcAdapter(transport_factory=_TransportSequence(clamp_transport))
        await clamp_adapter.start(_launch())
        try:
            invalid_clamp = await _direct_set_effort(clamp_adapter, "xhigh")
            self.assertEqual(
                (invalid_clamp.ok, invalid_clamp.acceptance, invalid_clamp.effective_value),
                (False, "unknown", None),
            )
            self.assertEqual(clamp_adapter.advertise().selected_effort, "high")
        finally:
            await clamp_adapter.stop("forced")

        model_transport = _FakePiTransport()
        model_transport.models.append(
            {
                "id": "ephemeral-model",
                "name": "Ephemeral Model",
                "api": "test",
                "provider": "provider-x",
                "reasoning": True,
                "thinkingLevelMap": {"high": "high"},
            }
        )
        model_transport.hide_selected_model_after_set = True
        model_adapter = PiRpcAdapter(transport_factory=_TransportSequence(model_transport))
        await model_adapter.start(_launch())
        try:
            invalid_model = await _direct_set_model(
                model_adapter,
                "provider-x/ephemeral-model",
            )
            self.assertEqual(
                (invalid_model.ok, invalid_model.acceptance, invalid_model.effective_value),
                (False, "unknown", None),
            )
            self.assertEqual(
                model_adapter.advertise().selected_model_key,
                "anthropic/claude-test",
            )
        finally:
            await model_adapter.stop("forced")
