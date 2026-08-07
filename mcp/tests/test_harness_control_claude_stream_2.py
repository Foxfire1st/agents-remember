from __future__ import annotations

import asyncio
import unittest

from agents_remember.errors import HarnessAdapterBusyError
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_claude import (
    ClaudeAdapterLimits,
    ClaudeStreamJsonAdapter,
)
from agents_remember.serving.harness_control_models import ControlOperationRef, PromptRequest
from test_harness_control_claude import (
    NOW,
    _adapter,
    _FakeClaudeTransport,
    _identity,
    _launch,
    _load_fixture,
    _operation,
    _replay,
    _result,
    _set_model,
    _settle,
    _wait_for_activity,
    _wire_text,
)


class ClaudeStreamJsonAdapterTests2(unittest.IsolatedAsyncioTestCase):
    async def test_advertised_commands_use_structured_input_and_other_commands_are_precise(
        self,
    ) -> None:
        correlations = ["command-correlation"]
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport, correlations=correlations)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        try:
            unknown = await bridge.submissions().submit(
                bridge.prompt("/not-real", source="terminal", request_id="unknown")
            )
            identity_change = await bridge.submissions().submit(
                bridge.prompt("/clear", source="terminal", request_id="clear")
            )
            self.assertEqual(
                (unknown.acceptance, identity_change.acceptance), ("unsupported", "unsupported")
            )
            self.assertIn("did not advertise", unknown.detail or "")
            self.assertIn("changes process/session identity", identity_change.detail or "")
            self.assertEqual(len(transport.writes), 3)

            compact_task = asyncio.create_task(
                bridge.submissions().submit(
                    bridge.prompt("/compact", source="terminal", request_id="compact")
                )
            )
            await transport.wait_for_writes(4)
            self.assertEqual(_wire_text(transport.writes[3]), "/compact")
            transport.feed(_replay(transport.writes[3]))
            compact = await asyncio.wait_for(compact_task, timeout=1.0)
            self.assertEqual(compact.acceptance, "immediate")
        finally:
            await bridge.stop("forced")

    async def test_model_and_effort_set_require_terminal_echo_and_update_model_gate(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(
            transport,
            correlations=["set-haiku", "set-sonnet", "set-low"],
        )
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        try:
            model_task = asyncio.create_task(bridge.submissions().set_model("haiku"))
            await transport.wait_for_writes(4)
            self.assertEqual(_wire_text(transport.writes[3]), "/model haiku")
            transport.feed(_replay(transport.writes[3]))
            transport.feed(_result("Set model to Haiku for this session only"))
            model = await asyncio.wait_for(model_task, timeout=1.0)
            self.assertEqual(
                (model.ok, model.acceptance, model.effective_value),
                (True, "echo-verified", "haiku"),
            )
            self.assertEqual(adapter.advertise().selected_model_key, "haiku")

            write_count = len(transport.writes)
            refused_effort = await bridge.submissions().set_effort("low")
            self.assertEqual(
                (refused_effort.ok, refused_effort.acceptance),
                (False, "unsupported"),
            )
            self.assertEqual(len(transport.writes), write_count)

            sonnet_task = asyncio.create_task(bridge.submissions().set_model("sonnet"))
            await transport.wait_for_writes(5)
            transport.feed(_replay(transport.writes[4]))
            transport.feed(_result("Set model to Sonnet for this session only"))
            self.assertEqual(
                (await asyncio.wait_for(sonnet_task, timeout=1.0)).acceptance,
                "echo-verified",
            )

            effort_task = asyncio.create_task(bridge.submissions().set_effort("low"))
            await transport.wait_for_writes(6)
            self.assertEqual(_wire_text(transport.writes[5]), "/effort low")
            transport.feed(_replay(transport.writes[5]))
            transport.feed(
                _result("Set effort level to low (this session only): Quick implementation")
            )
            effort = await asyncio.wait_for(effort_task, timeout=1.0)
            self.assertEqual(
                (effort.ok, effort.acceptance, effort.effective_value),
                (True, "echo-verified", "low"),
            )
            self.assertEqual(adapter.advertise().selected_effort, "low")
        finally:
            await bridge.stop("forced")

    async def test_terminal_refusal_or_non_echo_never_promotes_claude_capability(self) -> None:
        for result_frame, expected in (
            (
                {**_result("noninteractive_set_blocked"), "is_error": True},
                (False, "unsupported"),
            ),
            (_result("Command completed without an effective-value echo"), (True, "immediate")),
        ):
            with self.subTest(expected=expected):
                transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
                adapter = _adapter(transport, correlations=["terminal-evidence"])
                await adapter.start(_launch())
                try:
                    task = asyncio.create_task(_set_model(adapter, "haiku"))
                    await transport.wait_for_writes(4)
                    transport.feed(_replay(transport.writes[3]))
                    transport.feed(result_frame)
                    result = await asyncio.wait_for(task, timeout=1.0)
                    self.assertEqual((result.ok, result.acceptance), expected)
                    self.assertIsNone(result.effective_value)
                    self.assertEqual(adapter.advertise().selected_model_key, "sonnet")
                finally:
                    await adapter.stop("forced")

    async def test_native_noninteractive_set_blocked_refusal_maps_without_alias_guessing(
        self,
    ) -> None:
        frames = _load_fixture("initialization.jsonl")
        response = frames[-1]["response"]
        assert isinstance(response, dict)
        payload = response["response"]
        assert isinstance(payload, dict)
        models = payload["models"]
        assert isinstance(models, list)
        models.append(
            {
                "value": "regional-fable",
                "resolvedModel": "us.anthropic.claude-fable-5-20260701-v1:0",
                "displayName": "Fable 5",
                "description": "Provider-advertised model used to verify native refusal mapping",
                "supportsEffort": False,
                "supportedEffortLevels": [],
            }
        )
        transport = _FakeClaudeTransport(frames)
        adapter = _adapter(transport, correlations=["regional-fable-set"])
        await adapter.start(_launch())
        try:
            task = asyncio.create_task(_set_model(adapter, "regional-fable"))
            await transport.wait_for_writes(4)
            transport.feed(_replay(transport.writes[3]))
            transport.feed({**_result("noninteractive_set_blocked"), "is_error": True})
            result = await asyncio.wait_for(task, timeout=1.0)
            self.assertEqual((result.ok, result.acceptance), (False, "unsupported"))
            self.assertIn("native launch flag", result.detail or "")
        finally:
            await adapter.stop("forced")

        alias_frames = _load_fixture("initialization.jsonl")
        alias_response = alias_frames[-1]["response"]
        assert isinstance(alias_response, dict)
        alias_payload = alias_response["response"]
        assert isinstance(alias_payload, dict)
        alias_models = alias_payload["models"]
        assert isinstance(alias_models, list)
        alias_models.append(
            {
                "value": "fable",
                "resolvedModel": "claude-sonnet-5",
                "displayName": "Custom Sonnet Alias",
                "description": "Alias overridden to a selectable Sonnet model",
                "supportsEffort": True,
                "supportedEffortLevels": ["low"],
            }
        )
        alias_transport = _FakeClaudeTransport(alias_frames)
        alias_adapter = _adapter(alias_transport, correlations=["custom-alias-set"])
        await alias_adapter.start(_launch())
        try:
            task = asyncio.create_task(_set_model(alias_adapter, "fable"))
            await alias_transport.wait_for_writes(4)
            alias_transport.feed(_replay(alias_transport.writes[3]))
            alias_transport.feed(_result("Set model to Sonnet 5 for this session only"))
            result = await asyncio.wait_for(task, timeout=1.0)
            self.assertEqual(
                (result.ok, result.acceptance, result.effective_value),
                (True, "echo-verified", "fable"),
            )
        finally:
            await alias_adapter.stop("forced")

    async def test_model_terminal_labels_are_exact_dynamic_aliases_not_prefixes(self) -> None:
        for terminal, expected in (
            ("Set model to Sonnet 5 for this session only", "echo-verified"),
            ("Set model to Sonnet 5 impostor for this session only", "immediate"),
        ):
            with self.subTest(terminal=terminal):
                frames = _load_fixture("initialization.jsonl")
                response = frames[-1]["response"]
                assert isinstance(response, dict)
                payload = response["response"]
                assert isinstance(payload, dict)
                models = payload["models"]
                assert isinstance(models, list)
                models.append(
                    {
                        "value": "regional-sonnet",
                        "resolvedModel": "us.anthropic.claude-sonnet-5-v1:0",
                        "displayName": "Regional Sonnet",
                        "description": "Provider-resolved Sonnet alias",
                        "supportsEffort": True,
                        "supportedEffortLevels": ["low"],
                    }
                )
                transport = _FakeClaudeTransport(frames)
                adapter = _adapter(transport, correlations=["exact-label"])
                await adapter.start(_launch())
                try:
                    task = asyncio.create_task(_set_model(adapter, "regional-sonnet"))
                    await transport.wait_for_writes(4)
                    transport.feed(_replay(transport.writes[3]))
                    transport.feed(_result(terminal))
                    result = await asyncio.wait_for(task, timeout=1.0)
                    self.assertEqual(result.acceptance, expected)
                    self.assertEqual(
                        adapter.advertise().selected_model_key,
                        "regional-sonnet" if expected == "echo-verified" else "sonnet",
                    )
                finally:
                    await adapter.stop("forced")

        frames = _load_fixture("initialization.jsonl")
        response = frames[-1]["response"]
        assert isinstance(response, dict)
        payload = response["response"]
        assert isinstance(payload, dict)
        models = payload["models"]
        assert isinstance(models, list)
        models.extend(
            [
                {
                    "value": "default",
                    "resolvedModel": "claude-opus-4-8[1m]",
                    "displayName": "Default (recommended)",
                    "description": "Dynamic default",
                    "supportsEffort": True,
                    "supportedEffortLevels": ["low"],
                },
                {
                    "value": "opus[1m]",
                    "resolvedModel": "claude-opus-4-8[1m]",
                    "displayName": "Opus",
                    "description": "Resolved alias",
                    "supportsEffort": True,
                    "supportedEffortLevels": ["low"],
                },
            ]
        )
        for terminal, expected in (
            ("Set model to Opus 4.8 (1M context) (default) for this session only", True),
            ("Set model to Completely Different (default) for this session only", False),
        ):
            with self.subTest(default_terminal=terminal):
                transport = _FakeClaudeTransport(frames)
                adapter = _adapter(transport, correlations=["default-label"])
                await adapter.start(_launch())
                try:
                    task = asyncio.create_task(_set_model(adapter, "default"))
                    await transport.wait_for_writes(4)
                    transport.feed(_replay(transport.writes[3]))
                    transport.feed(_result(terminal))
                    result = await asyncio.wait_for(task, timeout=1.0)
                    self.assertEqual(result.acceptance == "echo-verified", expected)
                finally:
                    await adapter.stop("forced")

    async def test_set_timeout_neutralizes_late_replay_before_a_clean_retry(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(
            transport,
            correlations=["expired-correlation", "retry-correlation"],
            limits=ClaudeAdapterLimits(acceptance_timeout_seconds=0.005),
        )
        await adapter.start(_launch())
        try:
            expired = await _set_model(adapter, "haiku")
            self.assertEqual((expired.ok, expired.acceptance), (False, "unknown"))
            expired_frame = transport.writes[3]

            blocked = await _set_model(adapter, "haiku")
            self.assertEqual((blocked.ok, blocked.acceptance), (False, "unknown"))
            self.assertEqual(len(transport.writes), 4)

            transport.feed(_replay(expired_frame))
            transport.feed(_result("Set model to Haiku for this session only"))
            await _settle()
            self.assertEqual(adapter.advertise().selected_model_key, "sonnet")

            transport.feed(_replay(expired_frame))
            await _settle()

            retry_task = asyncio.create_task(_set_model(adapter, "haiku"))
            await transport.wait_for_writes(5)
            transport.feed(_replay(transport.writes[4]))
            transport.feed(_result("Set model to Haiku for this session only"))
            retry = await asyncio.wait_for(retry_task, timeout=1.0)
            self.assertEqual(retry.acceptance, "echo-verified")
        finally:
            await adapter.stop("forced")

    async def test_repeated_late_replay_of_an_expired_set_restores_one_turn_not_two(self) -> None:
        # A tombstoned set command can be replayed more than once (Claude re-emits its replay on a
        # resume). Each replay restores the abandoned turn so the seat does not read idle while the
        # command is still running -- but restoring it TWICE would leave a phantom turn behind that
        # the single terminal result cannot clear, and the seat would never go idle again.
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(
            transport,
            correlations=["expired-correlation"],
            limits=ClaudeAdapterLimits(acceptance_timeout_seconds=0.005),
        )
        await adapter.start(_launch())
        try:
            expired = await _set_model(adapter, "haiku")
            self.assertEqual((expired.ok, expired.acceptance), (False, "unknown"))
            expired_frame = transport.writes[3]

            operation = ControlOperationRef(
                bridge_epoch="e", sequence=1, operation_id="op-probe", kind="prompt"
            )

            transport.feed(_replay(expired_frame))
            await _settle()
            first = await adapter.snapshot()
            self.assertEqual(first.raw.get("lateClaudeReplayIgnored"), "ar-claude-set-model-1")
            self.assertIsNotNone(first.raw.get("activeTurnId"))
            # The restored turn holds the seat: no other operation may start on top of it.
            with self.assertRaises(HarnessAdapterBusyError):
                await adapter.preflight_operation(operation)

            transport.feed(_replay(expired_frame))
            await _settle()
            again = await adapter.snapshot()
            self.assertEqual(again.raw.get("activeTurnId"), first.raw.get("activeTurnId"))

            # One turn was restored, so ONE terminal result frees the seat completely. A second
            # restore would leave a turn the result never pops and the seat would stay busy.
            transport.feed(_result("Set model to Haiku for this session only"))
            await _settle()
            await adapter.preflight_operation(operation)
        finally:
            await adapter.stop("forced")

    async def test_cancelled_set_neutralizes_late_frames_before_retry(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(
            transport,
            correlations=["cancelled-correlation", "retry-correlation"],
        )
        await adapter.start(_launch())
        try:
            cancelled = asyncio.create_task(_set_model(adapter, "haiku"))
            await transport.wait_for_writes(4)
            cancelled.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await cancelled

            transport.feed(_replay(transport.writes[3]))
            transport.feed(_result("Set model to Haiku for this session only"))
            await _settle()
            self.assertEqual(adapter.advertise().selected_model_key, "sonnet")

            retry = asyncio.create_task(_set_model(adapter, "haiku"))
            await transport.wait_for_writes(5)
            transport.feed(_replay(transport.writes[4]))
            transport.feed(_result("Set model to Haiku for this session only"))
            self.assertEqual(
                (await asyncio.wait_for(retry, timeout=1.0)).acceptance,
                "echo-verified",
            )
        finally:
            await adapter.stop("forced")

    async def test_set_replay_requires_same_session_correlation_and_exact_wire_body(self) -> None:
        mutations = (
            {"uuid": "wrong-correlation"},
            {"message": {"role": "user", "content": "/model sonnet-extra"}},
            {"session_id": "different-session"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
                adapter = _adapter(transport, correlations=["strict-correlation"])
                await adapter.start(_launch())
                try:
                    task = asyncio.create_task(_set_model(adapter, "sonnet"))
                    await transport.wait_for_writes(4)
                    transport.feed({**_replay(transport.writes[3]), **mutation})
                    result = await asyncio.wait_for(task, timeout=1.0)
                    self.assertEqual((result.ok, result.acceptance), (False, "unknown"))
                    self.assertIsNone(result.effective_value)
                    self.assertEqual((await adapter.snapshot()).control, "failed")
                finally:
                    await adapter.stop("forced")

    async def test_duplicate_retained_set_correlation_is_not_written_twice(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(
            transport,
            correlations=["same-correlation", "same-correlation"],
        )
        await adapter.start(_launch())
        try:
            first_task = asyncio.create_task(_set_model(adapter, "haiku"))
            await transport.wait_for_writes(4)
            transport.feed(_replay(transport.writes[3]))
            transport.feed(_result("Set model to Haiku for this session only"))
            self.assertEqual(
                (await asyncio.wait_for(first_task, timeout=1.0)).acceptance,
                "echo-verified",
            )
            second = await _set_model(adapter, "sonnet")
            self.assertEqual((second.ok, second.acceptance), (False, "unknown"))
            self.assertIn("duplicate retained", second.detail or "")
            self.assertEqual(len(transport.writes), 4)
        finally:
            await adapter.stop("forced")

    async def test_disconnect_reconciliation_stays_unknown_and_never_resends(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        submission = asyncio.create_task(
            bridge.submissions().submit(
                bridge.prompt("ambiguous", source="durable", request_id="ambiguous")
            )
        )
        try:
            await transport.wait_for_writes(4)
            transport.disconnect()
            receipt = await asyncio.wait_for(submission, timeout=1.0)
            self.assertEqual(receipt.acceptance, "unknown")
            await _settle()
            self.assertEqual(bridge.snapshot().control, "disconnected")
            write_count = len(transport.writes)
            reconciliation = await bridge.submissions().reconcile("ambiguous")
            self.assertEqual(reconciliation.state, "unresolved")
            self.assertIn("was not resent", reconciliation.detail or "")
            self.assertEqual(len(transport.writes), write_count)
            blocked = await bridge.submissions().submit(
                bridge.prompt("must not resend", source="durable", request_id="after-exit")
            )
            self.assertEqual(blocked.acceptance, "queued")
            self.assertEqual(len(transport.writes), write_count)
        finally:
            await bridge.stop("forced")

    async def test_late_replay_reconciles_unknown_from_structured_history_without_resend(
        self,
    ) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(
            transport,
            limits=ClaudeAdapterLimits(acceptance_timeout_seconds=0.001),
        )
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        try:
            receipt = await bridge.submissions().submit(
                bridge.prompt("late replay", source="durable", request_id="late")
            )
            self.assertEqual(receipt.acceptance, "unknown")
            write_count = len(transport.writes)

            transport.feed(_replay(transport.writes[-1]))
            await _settle()
            reconciliation = await bridge.submissions().reconcile("late")
            self.assertEqual(reconciliation.state, "accepted")
            self.assertIn("replay-user-message", reconciliation.detail or "")
            self.assertEqual(len(transport.writes), write_count)
        finally:
            await bridge.stop("forced")

    async def test_nonzero_process_exit_maps_to_failed(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport)
        bridge = HarnessControlBridge(_identity(), adapter)
        await bridge.start(_launch())
        try:
            transport.disconnect(returncode=7)
            await _settle()
            self.assertEqual(bridge.snapshot().control, "failed")
            self.assertIn("status 7", str(bridge.snapshot().raw["disconnect"]))
        finally:
            await bridge.stop("forced")

    async def test_success_subtype_api_error_remains_failed_with_safe_metadata(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        try:
            submission = asyncio.create_task(
                bridge.submissions().submit(
                    bridge.prompt("limited", source="terminal", request_id="limited")
                )
            )
            await transport.wait_for_writes(4)
            transport.feed(_replay(transport.writes[3]))
            await asyncio.wait_for(submission, timeout=1.0)
            transport.feed(
                {
                    **_result("usage limit reached"),
                    "is_error": True,
                    "terminal_reason": "api_error",
                    "api_error_status": 429,
                    "stop_reason": "stop_sequence",
                }
            )
            await _settle()
            terminal = bridge.transcript()[-1].terminal_result
            assert terminal is not None
            self.assertEqual(terminal.outcome, "failed")
            self.assertEqual(
                terminal.raw,
                {
                    "subtype": "success",
                    "isError": True,
                    "terminalReason": "api_error",
                    "stopReason": "stop_sequence",
                    "apiErrorStatus": 429,
                },
            )
        finally:
            await bridge.stop("forced")

    async def test_forced_stop_reclaims_a_reader_blocked_by_full_event_queue(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(
            transport,
            limits=ClaudeAdapterLimits(event_queue_limit=1),
        )
        await adapter.start(_launch())
        transport.feed({"type": "notification", "subtype": "first"})
        transport.feed({"type": "notification", "subtype": "second"})
        await _settle()
        await asyncio.wait_for(adapter.stop("forced"), timeout=1.0)

    async def test_correlation_history_is_bounded_at_two_input_sizes(self) -> None:
        for total in (8, 64):
            transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
            adapter = _adapter(
                transport,
                correlations=[f"correlation-{index}" for index in range(total)],
                limits=ClaudeAdapterLimits(history_limit=4, event_queue_limit=8),
            )
            await adapter.start(_launch())

            async def drain(target: ClaudeStreamJsonAdapter = adapter) -> None:
                async for _ in target.subscribe():
                    pass

            drain_task = asyncio.create_task(drain())
            try:
                for index in range(total):
                    operation = _operation("prompt")
                    request = PromptRequest(
                        f"request-{index}",
                        "durable",
                        f"message-{index}",
                        NOW,
                        operation,
                    )
                    await adapter.preflight_operation(operation)
                    task = asyncio.create_task(adapter.submit(request))
                    await transport.wait_for_writes(index + 4)
                    transport.feed(_replay(transport.writes[index + 3]))
                    receipt = await asyncio.wait_for(task, timeout=1.0)
                    self.assertEqual(receipt.acceptance, "immediate")
                    transport.feed(_result(f"done-{index}"))
                    await _wait_for_activity(adapter, "idle")
                self.assertLessEqual(adapter.retained_submission_count, 4)
            finally:
                await adapter.stop("forced")
                await asyncio.gather(drain_task, return_exceptions=True)
