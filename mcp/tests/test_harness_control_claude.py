"""Pinned fake-protocol coverage for the Claude Code stream-json adapter."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from collections.abc import Callable, Mapping
from itertools import count
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_capabilities import SetResult
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_claude import (
    ClaudeAdapterLimits,
    ClaudeStreamJsonAdapter,
)
from agents_remember.serving.harness_control_models import (
    ControlIdentity,
    ControlOperationKind,
    ControlOperationRef,
    InteractionResponse,
    LaunchSpec,
    PromptRequest,
    ShutdownMode,
)
from agents_remember.serving.harness_launch import ResolvedLaunch

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "claude_stream_json" / "2.1.210"
SESSION_ID = "11111111-1111-4111-8111-111111111111"
FIRST_CORRELATION = "22222222-2222-4222-8222-222222222222"
NOW = "2026-07-14T10:00:00+00:00"
_OPERATION_SEQUENCE = count(1)


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

    async def write_frame(
        self,
        frame: Mapping[str, object],
        *,
        before_write: Callable[[], None] | None = None,
    ) -> None:
        if before_write is not None:
            before_write()
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
    expected_launch: ResolvedLaunch | None = None,
) -> ClaudeStreamJsonAdapter:
    values = iter(correlations or [FIRST_CORRELATION])

    return ClaudeStreamJsonAdapter(
        transport_factory=lambda: transport,
        clock=lambda: NOW,
        correlation_factory=lambda: next(values),
        limits=limits,
        expected_launch=expected_launch,
    )


def _replay(written: Mapping[str, object]) -> dict[str, object]:
    replay = {**written, "isReplay": True, "session_id": SESSION_ID, "timestamp": NOW}
    text = _wire_text(written)
    stripped = text.lstrip()
    if stripped.startswith("/"):
        command_text = stripped[1:]
        command, separator, arguments = command_text.partition(" ")
        if not separator:
            arguments = ""
        replay["message"] = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"<command-name>/{command}</command-name>\n"
                        f"            <command-message>{command}</command-message>\n"
                        f"            <command-args>{arguments}</command-args>"
                    ),
                }
            ],
        }
    return replay


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


def _operation(kind: ControlOperationKind) -> ControlOperationRef:
    sequence = next(_OPERATION_SEQUENCE)
    return ControlOperationRef(
        bridge_epoch="claude-test-epoch",
        sequence=sequence,
        operation_id=f"claude-test-{kind}-{sequence}",
        kind=kind,
    )


async def _set_model(adapter: ClaudeStreamJsonAdapter, model_key: str) -> SetResult:
    operation = _operation("set-model")
    await adapter.preflight_operation(operation)
    return await adapter.set_model(model_key, operation=operation)


async def _set_effort(adapter: ClaudeStreamJsonAdapter, effort: str) -> SetResult:
    operation = _operation("set-effort")
    await adapter.preflight_operation(operation)
    return await adapter.set_effort(effort, operation=operation)


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
        assert transport.argv is not None
        self.assertEqual(transport.argv[:9], _launch().argv)
        self.assertEqual(
            transport.argv[9:12],
            ("--mcp-config", '{"mcpServers":{}}', "--strict-mcp-config"),
        )
        self.assertEqual(transport.stop_modes, ["forced"])

    async def test_discover_replaces_all_installed_mcp_selector_spellings(self) -> None:
        executable = "/opt/claude"
        empty = ("--mcp-config", '{"mcpServers":{}}', "--strict-mcp-config")
        # Live 2.1.210 startup accepts the exact strict flag below, but rejects
        # --no-strict-mcp-config and the =true/=false boolean forms; those are not its grammar.
        cases = (
            (
                "separate-single",
                (
                    executable,
                    "--model",
                    "sonnet",
                    "--mcp-config",
                    '{"mcpServers":{"touch":{}}}',
                    "--effort",
                    "high",
                ),
                (executable, "--model", "sonnet", "--effort", "high", *empty),
            ),
            (
                "separate-multiple-and-repeated",
                (
                    executable,
                    "--mcp-config",
                    "/tmp/one.json",
                    '{"mcpServers":{"two":{}}}',
                    "/tmp/three.json",
                    "--settings",
                    "/tmp/settings.json",
                    "--mcp-config",
                    "/tmp/four.json",
                    "/tmp/five.json",
                    "--resume",
                    SESSION_ID,
                ),
                (
                    executable,
                    "--settings",
                    "/tmp/settings.json",
                    "--resume",
                    SESSION_ID,
                    *empty,
                ),
            ),
            (
                "equals-attached-preserves-following-positionals",
                (
                    executable,
                    "--append-system-prompt",
                    "keep-before",
                    "--mcp-config=/tmp/attached.json",
                    "literal-positional",
                    "--strict-mcp-config",
                    '--mcp-config={"mcpServers":{"other":{}}}',
                    "--append-system-prompt",
                    "keep-after",
                ),
                (
                    executable,
                    "--append-system-prompt",
                    "keep-before",
                    "literal-positional",
                    "--append-system-prompt",
                    "keep-after",
                    *empty,
                ),
            ),
            (
                "end-of-options-preserves-positional-suffix",
                (
                    executable,
                    "--model",
                    "sonnet",
                    "--mcp-config=/tmp/attached.json",
                    "--",
                    "--mcp-config",
                    "literal-positional",
                    "--strict-mcp-config",
                ),
                (
                    executable,
                    "--model",
                    "sonnet",
                    *empty,
                    "--",
                    "--mcp-config",
                    "literal-positional",
                    "--strict-mcp-config",
                ),
            ),
        )
        for label, argv, expected_prefix in cases:
            with self.subTest(label=label):
                transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
                adapter = _adapter(transport)

                await adapter.discover(_launch(argv=argv))

                assert transport.argv is not None
                self.assertEqual(transport.argv[: len(expected_prefix)], expected_prefix)
                discovery_prefix = transport.argv[: len(expected_prefix)]
                separator = (
                    discovery_prefix.index("--")
                    if "--" in discovery_prefix
                    else len(discovery_prefix)
                )
                parsed_options = discovery_prefix[:separator]
                self.assertEqual(parsed_options.count("--mcp-config"), 1)
                self.assertEqual(parsed_options.count("--strict-mcp-config"), 1)
                self.assertFalse(
                    any(argument.startswith("--mcp-config=") for argument in parsed_options)
                )
                self.assertEqual(transport.stop_modes, ["forced"])

    async def test_normal_start_preserves_existing_mcp_selectors_byte_for_byte(self) -> None:
        argv = (
            "/opt/claude",
            "--model",
            "sonnet",
            "--mcp-config",
            "/tmp/one.json",
            "/tmp/two.json",
            "--strict-mcp-config",
            "--settings",
            "/tmp/settings.json",
        )
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport)

        await adapter.start(_launch(argv=argv))
        try:
            assert transport.argv is not None
            self.assertEqual(transport.argv[: len(argv)], argv)
        finally:
            await adapter.stop("forced")

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
            self.assertNotIn("--mcp-config", transport.argv)
            self.assertNotIn("--strict-mcp-config", transport.argv)
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

    async def test_expected_launch_model_mismatch_closes_and_propagates_as_failure(self) -> None:
        frames = _load_fixture("initialization.jsonl")
        system_init = frames[1]
        system_init["model"] = "haiku"
        transport = _FakeClaudeTransport(frames)
        adapter = _adapter(
            transport,
            expected_launch=ResolvedLaunch("claude", "sonnet", "high", Path("/workspace")),
        )

        with self.assertRaisesRegex(
            HarnessControlError,
            "selected model 'sonnet'.*running harness reported 'haiku'",
        ):
            await adapter.start(_launch())

        self.assertEqual(transport.stop_modes, ["forced"])

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

    async def test_multiple_messages_use_only_the_authoritative_bridge_queue(self) -> None:
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

            second = await bridge.submit(
                bridge.prompt("second", source="durable", request_id="second")
            )
            self.assertEqual((first.acceptance, second.acceptance), ("immediate", "queued"))
            self.assertEqual(len(transport.writes), 4)
            self.assertIn("\n\nfirst", _wire_text(transport.writes[3]))

            transport.feed(_result("first done"))
            await transport.wait_for_writes(5)
            self.assertIn("\n\nsecond", _wire_text(transport.writes[4]))
            transport.feed(_replay(transport.writes[4]))
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
            active = asyncio.create_task(
                bridge.submit(
                    bridge.prompt("interaction turn", source="terminal", request_id="interaction")
                )
            )
            await transport.wait_for_writes(4)
            transport.feed(_replay(transport.writes[3]))
            self.assertEqual((await active).acceptance, "immediate")
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
            transport.feed(_result("interaction done"))
            await _wait_for_activity(adapter, "idle")
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

    async def test_model_and_effort_set_require_terminal_echo_and_update_model_gate(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(
            transport,
            correlations=["set-haiku", "set-sonnet", "set-low"],
        )
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        try:
            model_task = asyncio.create_task(bridge.set_model("haiku"))
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
            refused_effort = await bridge.set_effort("low")
            self.assertEqual(
                (refused_effort.ok, refused_effort.acceptance),
                (False, "unsupported"),
            )
            self.assertEqual(len(transport.writes), write_count)

            sonnet_task = asyncio.create_task(bridge.set_model("sonnet"))
            await transport.wait_for_writes(5)
            transport.feed(_replay(transport.writes[4]))
            transport.feed(_result("Set model to Sonnet for this session only"))
            self.assertEqual(
                (await asyncio.wait_for(sonnet_task, timeout=1.0)).acceptance,
                "echo-verified",
            )

            effort_task = asyncio.create_task(bridge.set_effort("low"))
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
            blocked = await bridge.submit(
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
