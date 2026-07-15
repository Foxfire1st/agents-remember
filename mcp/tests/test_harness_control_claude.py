"""Pinned fake-protocol coverage for the Claude Code stream-json adapter."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from collections.abc import Mapping
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_claude import (
    ClaudeAdapterLimits,
    ClaudeStreamJsonAdapter,
)
from agents_remember.serving.harness_control_models import (
    ControlIdentity,
    InteractionResponse,
    LaunchSpec,
    PromptRequest,
    ShutdownMode,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "claude_stream_json" / "2.1.210"
SESSION_ID = "11111111-1111-4111-8111-111111111111"
FIRST_CORRELATION = "22222222-2222-4222-8222-222222222222"
NOW = "2026-07-14T10:00:00+00:00"


def _load_fixture(name: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in (FIXTURE_ROOT / name).read_text().splitlines()]


class _FakeClaudeTransport:
    def __init__(self, frames: list[dict[str, object]] | None = None) -> None:
        self.frames: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        for frame in frames or []:
            self.frames.put_nowait(frame)
        self.writes: list[dict[str, object]] = []
        self.argv: tuple[str, ...] | None = None
        self.cwd: Path | None = None
        self.env: dict[str, str] | None = None
        self.stop_modes: list[ShutdownMode] = []
        self.started = False
        self._returncode: int | None = None
        self._write_event = asyncio.Event()

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def start(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> None:
        self.started = True
        self.argv = argv
        self.cwd = cwd
        self.env = dict(env)

    async def read_frame(self) -> dict[str, object] | None:
        return await self.frames.get()

    async def write_frame(self, frame: Mapping[str, object]) -> None:
        self.writes.append(dict(frame))
        self._write_event.set()

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)
        self._returncode = 0
        self.frames.put_nowait(None)

    def feed(self, frame: dict[str, object]) -> None:
        self.frames.put_nowait(frame)

    def disconnect(self, *, returncode: int = 0) -> None:
        self._returncode = returncode
        self.frames.put_nowait(None)

    async def wait_for_writes(self, count: int) -> None:
        while len(self.writes) < count:
            self._write_event.clear()
            if len(self.writes) < count:
                await asyncio.wait_for(self._write_event.wait(), timeout=1.0)


def _identity() -> ControlIdentity:
    return ControlIdentity(
        ar_session_id="ar-claude-session",
        tmux_name="ar-claude-session",
        created_at=NOW,
    )


def _launch(
    *,
    argv: tuple[str, ...] = (
        "/opt/claude",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--settings",
        "/home/test/.claude/settings.json",
        "--resume",
        SESSION_ID,
    ),
) -> LaunchSpec:
    return LaunchSpec(
        identity=_identity(),
        harness_id="claude",
        cwd=Path("/workspace"),
        argv=argv,
        env={"HOME": "/home/test", "AUTH_TOKEN_FOR_TEST": "not-exposed"},
    )


def _adapter(
    transport: _FakeClaudeTransport,
    *,
    correlations: list[str] | None = None,
    limits: ClaudeAdapterLimits | None = None,
) -> ClaudeStreamJsonAdapter:
    values = iter(correlations or [FIRST_CORRELATION])

    return ClaudeStreamJsonAdapter(
        transport_factory=lambda: transport,
        clock=lambda: NOW,
        correlation_factory=lambda: next(values),
        limits=limits,
    )


def _replay(written: Mapping[str, object]) -> dict[str, object]:
    return {**written, "isReplay": True, "session_id": SESSION_ID, "timestamp": NOW}


def _result(text: str = "done") -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "result": text,
        "stop_reason": "end_turn",
        "total_cost_usd": 0,
        "usage": {},
        "modelUsage": {},
        "permission_denials": [],
        "uuid": f"result-{text}",
        "session_id": SESSION_ID,
    }


async def _settle() -> None:
    for _ in range(4):
        await asyncio.sleep(0)


class ClaudeStreamJsonAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_discover_uses_only_token_free_bootstrap_and_list_models(self) -> None:
        fixture_frames = _load_fixture("initialization.jsonl")
        transport = _FakeClaudeTransport(fixture_frames)
        adapter = _adapter(transport)

        advertised = await adapter.discover(_launch())

        self.assertEqual(advertised.selected_model_key, "sonnet")
        user_frames = [frame for frame in transport.writes if frame["type"] == "user"]
        self.assertEqual(len(user_frames), 1)
        self.assertEqual(user_frames[0]["shouldQuery"], False)
        bootstrap_result = next(frame for frame in fixture_frames if frame["type"] == "result")
        self.assertEqual(bootstrap_result["num_turns"], 0)
        self.assertEqual(bootstrap_result["total_cost_usd"], 0)
        self.assertEqual(transport.stop_modes, ["forced"])

    async def test_launch_preserves_arguments_environment_and_requires_structured_init(
        self,
    ) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport)

        handshake = await adapter.start(_launch())
        try:
            self.assertEqual(handshake.snapshot.control, "ready")
            self.assertEqual(handshake.snapshot.vendor_session_id, SESSION_ID)
            self.assertEqual(handshake.raw["claudeCodeVersion"], "2.1.210")
            assert transport.argv is not None
            self.assertEqual(
                transport.argv[:9],
                _launch().argv,
            )
            for required in (
                "-p",
                "--input-format",
                "--output-format",
                "--verbose",
                "--replay-user-messages",
                "--permission-prompt-tool",
            ):
                self.assertIn(required, transport.argv)
            self.assertEqual(transport.env, _launch().env)
            self.assertNotIn("AUTH_TOKEN_FOR_TEST", json.dumps(handshake.raw))
            self.assertEqual(
                [frame["type"] for frame in transport.writes],
                ["control_request", "user", "control_request"],
            )
            self.assertEqual(transport.writes[1]["shouldQuery"], False)
            list_request = transport.writes[2]
            self.assertEqual(list_request["request"], {"subtype": "list_models"})
            advertised = adapter.advertise()
            self.assertEqual(advertised.selected_model_key, "sonnet")
            self.assertIsNone(advertised.selected_effort)
            self.assertEqual(
                [option.key for option in advertised.models[0].effort_options],
                ["low", "medium", "high", "xhigh", "max"],
            )
            self.assertEqual(advertised.models[1].effort_options, ())
            self.assertFalse(advertised.models[2].selectable)
            self.assertEqual(len(transport.writes), 3)
        finally:
            await adapter.stop("forced")

    async def test_current_initialize_without_models_or_account_is_accepted(self) -> None:
        frames = _load_fixture("initialization.jsonl")
        response = frames[0]["response"]
        assert isinstance(response, dict)
        payload = response["response"]
        assert isinstance(payload, dict)
        self.assertNotIn("models", payload)
        self.assertNotIn("account", payload)
        transport = _FakeClaudeTransport(frames)
        adapter = _adapter(transport)

        handshake = await adapter.start(_launch())
        try:
            self.assertEqual(handshake.snapshot.control, "ready")
            self.assertEqual(adapter.advertise().models[0].key, "sonnet")
        finally:
            await adapter.stop("forced")

    async def test_malformed_or_rejected_list_models_fails_loud_without_fallback(self) -> None:
        duplicate_frames = _load_fixture("initialization.jsonl")
        response = duplicate_frames[-1]["response"]
        assert isinstance(response, dict)
        payload = response["response"]
        assert isinstance(payload, dict)
        models = payload["models"]
        assert isinstance(models, list)
        models.append(dict(models[0]))
        duplicate_transport = _FakeClaudeTransport(duplicate_frames)
        duplicate = _adapter(duplicate_transport)

        duplicate_handshake = await duplicate.start(_launch())

        self.assertEqual(duplicate_handshake.snapshot.control, "unsupported")
        self.assertIn("repeated model value", str(duplicate_handshake.raw["detail"]))
        with self.assertRaisesRegex(HarnessControlError, "repeated model value"):
            duplicate.advertise()

        rejected_frames = _load_fixture("initialization.jsonl")
        rejected_response = rejected_frames[-1]["response"]
        assert isinstance(rejected_response, dict)
        rejected_response["subtype"] = "error"
        rejected_transport = _FakeClaudeTransport(rejected_frames)
        rejected = _adapter(rejected_transport)

        rejected_handshake = await rejected.start(_launch())

        self.assertEqual(rejected_handshake.snapshot.control, "unsupported")
        self.assertIn(
            "list_models control request was rejected",
            str(rejected_handshake.raw["detail"]),
        )

    async def test_compatible_patch_version_is_accepted_after_structured_negotiation(self) -> None:
        frames = _load_fixture("initialization.jsonl")
        frames[1]["claude_code_version"] = "2.1.209"
        transport = _FakeClaudeTransport(frames)
        adapter = _adapter(transport)

        handshake = await adapter.start(_launch())
        try:
            self.assertEqual(handshake.snapshot.control, "ready")
            self.assertEqual(handshake.adapter_id, "claude-stream-json:2.1.209")
            self.assertEqual(handshake.raw["claudeCodeVersion"], "2.1.209")
            self.assertTrue(transport.started)
        finally:
            await adapter.stop("forced")

    async def test_missing_protocol_capability_fails_loudly(self) -> None:
        frames = _load_fixture("initialization.jsonl")
        response = frames[0]["response"]
        assert isinstance(response, dict)
        payload = response["response"]
        assert isinstance(payload, dict)
        payload.pop("commands")
        incompatible_transport = _FakeClaudeTransport(frames)
        incompatible = _adapter(incompatible_transport)
        handshake = await incompatible.start(_launch())
        self.assertEqual(handshake.snapshot.control, "unsupported")
        self.assertEqual(incompatible_transport.stop_modes, ["forced"])
        self.assertIn("command capabilities", str(handshake.raw["detail"]))

    async def test_correlated_acceptance_retry_activity_and_terminal_result_are_distinct(
        self,
    ) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        submission = asyncio.create_task(
            bridge.submit(bridge.prompt("first prompt", source="durable", request_id="request-1"))
        )
        try:
            await transport.wait_for_writes(4)
            turn = _load_fixture("turn.jsonl")
            transport.feed(turn[0])
            receipt = await asyncio.wait_for(submission, timeout=1.0)
            self.assertEqual(receipt.acceptance, "immediate")
            self.assertEqual(bridge.snapshot().activity, "running")
            self.assertFalse(
                any(entry.terminal_result is not None for entry in bridge.transcript())
            )

            transport.feed(turn[1])
            transport.feed(turn[2])
            await _settle()
            self.assertEqual(bridge.snapshot().activity, "settling")

            transport.feed(turn[3])
            await _settle()
            self.assertEqual(bridge.snapshot().activity, "idle")
            transcript = bridge.transcript()
            self.assertEqual([entry.role for entry in transcript], ["user", "assistant", "result"])
            self.assertEqual(transcript[0].text, "first prompt")
            self.assertEqual(transcript[-1].request_id, "request-1")
            assert transcript[-1].terminal_result is not None
            self.assertEqual(transcript[-1].terminal_result.outcome, "completed")
        finally:
            if not submission.done():
                submission.cancel()
                await asyncio.gather(submission, return_exceptions=True)
            await bridge.stop("forced")

    async def test_multiple_messages_keep_wire_order_and_queued_acceptance(self) -> None:
        correlations = [
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        ]
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport, correlations=correlations)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        try:
            first_task = asyncio.create_task(
                bridge.submit(bridge.prompt("first", source="terminal", request_id="first"))
            )
            await transport.wait_for_writes(4)
            transport.feed(_replay(transport.writes[3]))
            first = await asyncio.wait_for(first_task, timeout=1.0)

            second_task = asyncio.create_task(
                bridge.submit(bridge.prompt("second", source="durable", request_id="second"))
            )
            await transport.wait_for_writes(5)
            transport.feed(_replay(transport.writes[4]))
            second = await asyncio.wait_for(second_task, timeout=1.0)
            self.assertEqual((first.acceptance, second.acceptance), ("immediate", "queued"))
            self.assertIn("\n\nfirst", _wire_text(transport.writes[3]))
            self.assertIn("\n\nsecond", _wire_text(transport.writes[4]))

            transport.feed(_result("first done"))
            transport.feed(_result("second done"))
            await _settle()
            results = [entry for entry in bridge.transcript() if entry.role == "result"]
            self.assertEqual([entry.request_id for entry in results], ["first", "second"])
        finally:
            await bridge.stop("forced")

    async def test_permissions_and_ask_user_question_use_durable_interaction_response(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        permission, question = _load_fixture("interactions.jsonl")
        try:
            transport.feed(permission)
            await _settle()
            pending = bridge.snapshot().pending_interaction
            assert pending is not None
            self.assertEqual((pending.kind, pending.choices), ("permission", ("allow", "deny")))
            await bridge.respond(InteractionResponse("permission-1", "allow", NOW))
            permission_response = transport.writes[-1]["response"]
            assert isinstance(permission_response, dict)
            result = permission_response["response"]
            assert isinstance(result, dict)
            self.assertEqual(result["behavior"], "allow")
            self.assertEqual(bridge.snapshot().activity, "settling")

            transport.feed(question)
            await _settle()
            pending = bridge.snapshot().pending_interaction
            assert pending is not None
            self.assertEqual(pending.kind, "user-input")
            self.assertIn("Which mode", pending.prompt)
            await bridge.respond(
                InteractionResponse(
                    "question-1",
                    json.dumps({"Which mode should be used?": "Safe"}),
                    NOW,
                )
            )
            question_response = transport.writes[-1]["response"]
            assert isinstance(question_response, dict)
            result = question_response["response"]
            assert isinstance(result, dict)
            updated = result["updatedInput"]
            assert isinstance(updated, dict)
            self.assertEqual(updated["answers"], {"Which mode should be used?": "Safe"})
        finally:
            await bridge.stop("forced")

    async def test_advertised_commands_use_structured_input_and_other_commands_are_precise(
        self,
    ) -> None:
        correlations = ["command-correlation"]
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport, correlations=correlations)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        try:
            unknown = await bridge.submit(
                bridge.prompt("/not-real", source="terminal", request_id="unknown")
            )
            identity_change = await bridge.submit(
                bridge.prompt("/clear", source="terminal", request_id="clear")
            )
            self.assertEqual(
                (unknown.acceptance, identity_change.acceptance), ("unsupported", "unsupported")
            )
            self.assertIn("did not advertise", unknown.detail or "")
            self.assertIn("changes process/session identity", identity_change.detail or "")
            self.assertEqual(len(transport.writes), 3)

            compact_task = asyncio.create_task(
                bridge.submit(bridge.prompt("/compact", source="terminal", request_id="compact"))
            )
            await transport.wait_for_writes(4)
            self.assertEqual(_wire_text(transport.writes[3]), "/compact")
            transport.feed(_replay(transport.writes[3]))
            compact = await asyncio.wait_for(compact_task, timeout=1.0)
            self.assertEqual(compact.acceptance, "immediate")
        finally:
            await bridge.stop("forced")

    async def test_disconnect_reconciliation_stays_unknown_and_never_resends(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        submission = asyncio.create_task(
            bridge.submit(bridge.prompt("ambiguous", source="durable", request_id="ambiguous"))
        )
        try:
            await transport.wait_for_writes(4)
            transport.disconnect()
            receipt = await asyncio.wait_for(submission, timeout=1.0)
            self.assertEqual(receipt.acceptance, "unknown")
            await _settle()
            self.assertEqual(bridge.snapshot().control, "disconnected")
            write_count = len(transport.writes)
            reconciliation = await bridge.reconcile("ambiguous")
            self.assertEqual(reconciliation.state, "unresolved")
            self.assertIn("was not resent", reconciliation.detail or "")
            self.assertEqual(len(transport.writes), write_count)
            with self.assertRaisesRegex(HarnessControlError, "not available: disconnected"):
                await bridge.submit(
                    bridge.prompt("must not resend", source="durable", request_id="after-exit")
                )
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
            receipt = await bridge.submit(
                bridge.prompt("late replay", source="durable", request_id="late")
            )
            self.assertEqual(receipt.acceptance, "unknown")
            write_count = len(transport.writes)

            transport.feed(_replay(transport.writes[-1]))
            await _settle()
            reconciliation = await bridge.reconcile("late")
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
                bridge.submit(bridge.prompt("limited", source="terminal", request_id="limited"))
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
                    request = PromptRequest(f"request-{index}", "durable", f"message-{index}", NOW)
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


def _wire_text(frame: Mapping[str, object]) -> str:
    message = frame["message"]
    assert isinstance(message, dict)
    content = message["content"]
    assert isinstance(content, list)
    block = content[0]
    assert isinstance(block, dict)
    text = block["text"]
    assert isinstance(text, str)
    return text


async def _wait_for_activity(adapter: ClaudeStreamJsonAdapter, expected: str) -> None:
    for _ in range(20):
        if (await adapter.snapshot()).activity == expected:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"Claude adapter did not reach activity={expected}")


if __name__ == "__main__":
    unittest.main()
