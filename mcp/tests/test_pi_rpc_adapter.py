"""Fake-transport conformance for the strict Pi RPC adapter."""

from __future__ import annotations

import asyncio
import json
import unittest
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import cast

from agents_remember.errors import (
    HarnessAdapterBusyError,
    HarnessAdapterDisconnectedError,
    HarnessControlError,
)
from agents_remember.serving.harness_capabilities import SetResult
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_models import (
    AdapterEvent,
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationKind,
    ControlOperationRef,
    InteractionResponse,
    LaunchSpec,
    PromptRequest,
    ShutdownMode,
    SubmissionReceipt,
    SubmissionSource,
)
from agents_remember.serving.pi_rpc_adapter import PiAdapterLimits, PiRpcAdapter
from agents_remember.serving.pi_rpc_protocol import (
    PI_RPC_DIALOG_METHODS,
    PI_RPC_FIRE_AND_FORGET_METHODS,
    PI_RPC_PACKAGE,
    PiRpcJsonlDecoder,
    encode_pi_rpc_frame,
    parse_pi_models,
    pi_rpc_launch,
)
from agents_remember.serving.pi_rpc_submissions import (
    PiSubmissionEvidence,
    PiSubmissionLedger,
)
from test_pi_rpc_real_smoke import PI_RPC_VERSION

FIXTURES = Path(__file__).parent / "fixtures" / "pi_rpc"


class _FakePiTransport:
    def __init__(
        self,
        *,
        session_id: str = "pi-session-1",
        session_file: str | None = "/sessions/pi-session-1.jsonl",
        entries: list[dict[str, object]] | None = None,
        leaf_id: str | None = "entry-0",
    ) -> None:
        self.models = [
            {
                "id": "claude-test",
                "name": "Claude Test",
                "api": "anthropic-messages",
                "provider": "anthropic",
                "baseUrl": "https://api.anthropic.test",
                "reasoning": True,
                "input": ["text"],
                "contextWindow": 200000,
                "maxTokens": 32000,
                "cost": {"input": 1, "output": 2},
                "thinkingLevelMap": {
                    "off": "off",
                    "minimal": "minimal",
                    "low": "low",
                    "medium": "medium",
                    "high": "high",
                    "max": "max",
                },
            },
            {
                "id": "chat-test",
                "name": "Chat Test",
                "api": "openai-completions",
                "provider": "local",
                "baseUrl": "http://localhost:8080",
                "reasoning": False,
                "input": ["text"],
                "contextWindow": 32000,
                "maxTokens": 8000,
                "cost": {"input": 0, "output": 0},
            },
        ]
        self.session = {
            "model": {**self.models[0], "headers": {"Authorization": "secret-test-value"}},
            "thinkingLevel": "high",
            "isStreaming": False,
            "isCompacting": False,
            "steeringMode": "all",
            "followUpMode": "one-at-a-time",
            "sessionFile": session_file,
            "sessionId": session_id,
            "autoCompactionEnabled": True,
            "messageCount": 0,
            "pendingMessageCount": 0,
        }
        if session_file is None:
            self.session.pop("sessionFile")
        self.entries = entries or []
        self.leaf_id = leaf_id
        self.launches: list[LaunchSpec] = []
        self.commands: list[dict[str, object]] = []
        self.stop_modes: list[ShutdownMode] = []
        self.prompt_failures: deque[HarnessAdapterDisconnectedError] = deque()
        self.prompt_refusals: deque[str] = deque()
        self.thinking_clamps: dict[str, str] = {}
        self.command_failures: dict[str, deque[Exception]] = {}
        self.command_hangs: dict[str, int] = {}
        self.hide_selected_model_after_set = False
        self.before_write_hook: Callable[[Mapping[str, object]], None] | None = None
        self._event_token = 0
        self.event_queue: asyncio.Queue[
            Mapping[str, object] | HarnessControlError | HarnessAdapterDisconnectedError | None
        ] = asyncio.Queue()

    async def start(self, launch: LaunchSpec) -> None:
        self.launches.append(launch)

    @property
    def event_token(self) -> int:
        return self._event_token

    async def request(
        self,
        command: Mapping[str, object],
        *,
        before_write: Callable[[], None] | None = None,
    ) -> Mapping[str, object]:
        """Record the command, apply whatever the test armed, then answer it.

        The two halves are separate on purpose: the arming hooks (write hooks, injected
        failures, hangs) are per-transport and apply to every command, while the answers
        below are per-command-type and are what each Pi RPC verb actually returns.
        """
        command_type = cast(str, command["type"])
        await self._record_and_apply_arming(command, command_type, before_write)
        reply = self._replies().get(command_type)
        if reply is None:
            raise AssertionError(f"unexpected fake command: {command_type}")
        return reply(cast(str, command["id"]), command)

    async def _record_and_apply_arming(
        self,
        command: Mapping[str, object],
        command_type: str,
        before_write: Callable[[], None] | None,
    ) -> None:
        """Everything a test can arm ahead of a command, in the order Pi would hit it.

        A prompt failure that could not have reached Pi is raised *before* the command is
        recorded, because such a command never went out; every other arming applies after.
        """
        copied = dict(command)
        if self.before_write_hook is not None:
            self.before_write_hook(copied)
        if before_write is not None:
            before_write()
        if command_type == "prompt" and self.prompt_failures:
            failure = self.prompt_failures[0]
            if not failure.may_have_sent:
                raise self.prompt_failures.popleft()
        self.commands.append(copied)
        remaining_hangs = self.command_hangs.get(command_type, 0)
        if remaining_hangs:
            self.command_hangs[command_type] = remaining_hangs - 1
            await asyncio.Future()
        failures = self.command_failures.get(command_type)
        if failures:
            raise failures.popleft()

    def _reply_get_state(
        self, request_id: str, _command: Mapping[str, object]
    ) -> Mapping[str, object]:
        return _success(request_id, "get_state", dict(self.session))

    def _reply_get_entries(
        self, request_id: str, command: Mapping[str, object]
    ) -> Mapping[str, object]:
        entries = self.entries
        since = command.get("since")
        if since is not None:
            index = next(
                (position for position, entry in enumerate(entries) if entry.get("id") == since),
                None,
            )
            if index is None:
                return _failure(request_id, "get_entries", f"Entry not found: {since}")
            entries = entries[index + 1 :]
        return _success(
            request_id,
            "get_entries",
            {"entries": entries, "leafId": self.leaf_id},
        )

    def _reply_get_available_models(
        self, request_id: str, _command: Mapping[str, object]
    ) -> Mapping[str, object]:
        return _success(request_id, "get_available_models", {"models": self.models})

    def _reply_set_model(
        self, request_id: str, command: Mapping[str, object]
    ) -> Mapping[str, object]:
        key = f"{command.get('provider')}/{command.get('modelId')}"
        selected = next(
            (model for model in self.models if f"{model['provider']}/{model['id']}" == key),
            None,
        )
        if selected is None:
            return _failure(request_id, "set_model", f"Model not found: {key}")
        self.session["model"] = dict(selected)
        if selected.get("reasoning") is False:
            self.session["thinkingLevel"] = "off"
        if self.hide_selected_model_after_set:
            self.models.remove(selected)
        return _acknowledgement(request_id, "set_model")

    def _reply_set_thinking_level(
        self, request_id: str, command: Mapping[str, object]
    ) -> Mapping[str, object]:
        requested = cast(str, command["level"])
        self.session["thinkingLevel"] = self.thinking_clamps.get(requested, requested)
        return _acknowledgement(request_id, "set_thinking_level")

    def _reply_prompt(
        self, request_id: str, _command: Mapping[str, object]
    ) -> Mapping[str, object]:
        if self.prompt_failures:
            raise self.prompt_failures.popleft()
        if self.prompt_refusals:
            # Pi answered the write; the answer is a refusal. Distinct from
            # `prompt_failures`, which are transport-level and never produce a frame.
            return _failure(request_id, "prompt", self.prompt_refusals.popleft())
        return _acknowledgement(request_id, "prompt")

    def _replies(self) -> Mapping[str, Callable[[str, Mapping[str, object]], Mapping[str, object]]]:
        """The verbs this fake answers. A command type absent here is a test asking for
        something Pi's RPC surface does not have, which ``request`` reports as such."""
        return {
            "get_state": self._reply_get_state,
            "get_entries": self._reply_get_entries,
            "get_available_models": self._reply_get_available_models,
            "set_model": self._reply_set_model,
            "set_thinking_level": self._reply_set_thinking_level,
            "prompt": self._reply_prompt,
        }

    async def send(
        self,
        command: Mapping[str, object],
        *,
        before_write: Callable[[], None] | None = None,
    ) -> None:
        if self.before_write_hook is not None:
            self.before_write_hook(command)
        if before_write is not None:
            before_write()
        self.commands.append(dict(command))

    async def _events(self) -> AsyncIterator[Mapping[str, object]]:
        while True:
            item = await self.event_queue.get()
            if item is None:
                raise HarnessAdapterDisconnectedError(
                    "fake Pi transport stopped", may_have_sent=False
                )
            if isinstance(item, HarnessControlError):
                raise item
            yield item

    def events(self) -> AsyncIterator[Mapping[str, object]]:
        return self._events()

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)
        self.event_queue.put_nowait(None)

    def emit(self, frame: Mapping[str, object]) -> None:
        self._event_token += 1
        self.event_queue.put_nowait(frame)

    def fail_events(self, error: HarnessControlError) -> None:
        self.event_queue.put_nowait(error)


class _TransportSequence:
    def __init__(self, *transports: _FakePiTransport) -> None:
        self.transports = deque(transports)

    def __call__(self) -> _FakePiTransport:
        return self.transports.popleft()


def _success(request_id: str, command: str, data: object) -> dict[str, object]:
    return {
        "id": request_id,
        "type": "response",
        "command": command,
        "success": True,
        "data": data,
    }


def _acknowledgement(request_id: str, command: str) -> dict[str, object]:
    """A success frame for a verb Pi acknowledges without a data payload."""
    return {
        "id": request_id,
        "type": "response",
        "command": command,
        "success": True,
    }


def _failure(request_id: str, command: str, error: str) -> dict[str, object]:
    return {
        "id": request_id,
        "type": "response",
        "command": command,
        "success": False,
        "error": error,
    }


def _identity() -> ControlIdentity:
    return ControlIdentity(
        ar_session_id="ar-session-pi",
        tmux_name="ar-pi",
        created_at="2026-07-14T09:00:00+00:00",
    )


def _launch(*, persistent: bool = True) -> LaunchSpec:
    session_args = ("--session-dir", "/sessions") if persistent else ("--no-session",)
    return LaunchSpec(
        identity=_identity(),
        harness_id="pi",
        cwd=Path("/workspace/project"),
        argv=(
            "pi",
            "--provider",
            "anthropic",
            "--model",
            "anthropic/claude-test",
            "--thinking",
            "high",
            *session_args,
            "--no-extensions",
        ),
        env={"PATH": "/tools", "PI_CONFIG": "/settings/pi.json"},
    )


def _operation(
    operation_id: str,
    kind: ControlOperationKind = "prompt",
    *,
    sequence: int = 1,
) -> ControlOperationRef:
    return ControlOperationRef(
        bridge_epoch="pi-test-epoch",
        sequence=sequence,
        operation_id=operation_id,
        kind=kind,
    )


def _prompt(
    request_id: str,
    *,
    source: SubmissionSource = "durable",
    operation: ControlOperationRef | None = None,
) -> PromptRequest:
    return PromptRequest(
        request_id=request_id,
        source=source,
        text=f"prompt {request_id}",
        submitted_at="2026-07-14T09:01:00+00:00",
        operation=operation,
    )


async def _direct_submit(
    adapter: PiRpcAdapter,
    request_id: str,
    *,
    source: SubmissionSource = "durable",
) -> SubmissionReceipt:
    operation = _operation(request_id)
    await adapter.preflight_operation(operation)
    return await adapter.submit(_prompt(request_id, source=source, operation=operation))


async def _direct_set_model(
    adapter: PiRpcAdapter,
    model_key: str,
    *,
    sequence: int = 1,
) -> SetResult:
    operation = _operation(f"set-model-{sequence}", "set-model", sequence=sequence)
    await adapter.preflight_operation(operation)
    return await adapter.set_model(model_key, operation=operation)


async def _direct_set_effort(
    adapter: PiRpcAdapter,
    effort: str,
    *,
    sequence: int = 1,
) -> SetResult:
    operation = _operation(f"set-effort-{sequence}", "set-effort", sequence=sequence)
    await adapter.preflight_operation(operation)
    return await adapter.set_effort(effort, operation=operation)


class PiRpcProtocolTests(unittest.TestCase):
    def test_lf_only_decoder_preserves_unicode_separators_and_accepts_crlf(self) -> None:
        decoder = PiRpcJsonlDecoder()
        first = '{"type":"event","text":"left\u2028right\u2029done"}'.encode()
        frames = decoder.feed(first[:13]) + decoder.feed(first[13:] + b"\r\n")
        self.assertEqual(frames[0]["text"], "left\u2028right\u2029done")
        self.assertEqual(decoder.finish(), ())

    def test_malformed_and_overlong_frames_refuse_loudly(self) -> None:
        with self.assertRaisesRegex(HarnessControlError, "malformed Pi RPC JSONL frame"):
            PiRpcJsonlDecoder().feed(b'{"type":]\n')
        with self.assertRaisesRegex(HarnessControlError, "exceeds 4 bytes"):
            PiRpcJsonlDecoder(max_frame_bytes=4).feed(b"12345")
        with self.assertRaisesRegex(HarnessControlError, "exceeds 4 bytes"):
            PiRpcJsonlDecoder(max_frame_bytes=4).feed(b"12345\n")

    def test_encoder_emits_one_lf_and_rejects_non_standard_numbers(self) -> None:
        self.assertEqual(encode_pi_rpc_frame({"text": "a\u2028b"}).count(b"\n"), 1)
        with self.assertRaisesRegex(HarnessControlError, "not JSON-serializable"):
            encode_pi_rpc_frame({"value": float("nan")})

    def test_launch_preserves_every_configuration_field(self) -> None:
        launch = _launch()
        rpc = pi_rpc_launch(launch)
        self.assertEqual(rpc.argv, ("pi", "--mode", "rpc", *launch.argv[1:]))
        self.assertEqual(rpc.cwd, launch.cwd)
        self.assertEqual(rpc.env, launch.env)
        self.assertEqual(rpc.identity, launch.identity)
        with self.assertRaisesRegex(HarnessControlError, "non-RPC"):
            pi_rpc_launch(
                LaunchSpec(
                    identity=launch.identity,
                    harness_id="pi",
                    cwd=launch.cwd,
                    argv=("pi", "--mode", "text"),
                )
            )
        with self.assertRaisesRegex(HarnessControlError, "harness_id='pi'"):
            pi_rpc_launch(
                LaunchSpec(
                    identity=launch.identity,
                    harness_id="pi-near-miss",
                    cwd=launch.cwd,
                    argv=launch.argv,
                )
            )

    def test_capability_fixture_documents_the_smoke_baseline(self) -> None:
        """The recording describes the pinned build, and the adapter agrees with it.

        The fixture is named after ``PI_RPC_VERSION``, so this reads the file the smoke
        test re-records rather than a second, older baseline: there is exactly one
        recording in the tree and it belongs to the version the product ships.
        """
        fixture = json.loads(
            (FIXTURES / f"{PI_RPC_VERSION}-capabilities.json").read_text(encoding="utf-8")
        )
        self.assertEqual(fixture["package"], PI_RPC_PACKAGE)
        self.assertEqual(fixture["version"], PI_RPC_VERSION)
        self.assertEqual(set(fixture["dialogMethods"]), PI_RPC_DIALOG_METHODS)
        self.assertEqual(set(fixture["fireAndForgetMethods"]), PI_RPC_FIRE_AND_FORGET_METHODS)
        self.assertEqual(
            sorted(path.name for path in FIXTURES.glob("*-capabilities.json")),
            [f"{PI_RPC_VERSION}-capabilities.json"],
            "a second capability recording leaves no rule about which one is authoritative",
        )

    def test_available_models_preserve_provider_identity_and_model_gated_thinking(self) -> None:
        models = parse_pi_models(
            {
                "data": {
                    "models": [
                        {
                            "provider": "provider-a",
                            "id": "shared/id",
                            "name": "Reasoning A",
                            "reasoning": True,
                            "thinkingLevelMap": {
                                "low": None,
                                "xhigh": None,
                                "max": "provider-max",
                            },
                        },
                        {
                            "provider": "provider-b",
                            "id": "shared/id",
                            "name": "Chat B",
                            "reasoning": False,
                        },
                    ]
                }
            }
        )

        self.assertEqual(
            [model.key for model in models],
            ["provider-a/shared/id", "provider-b/shared/id"],
        )
        self.assertEqual(
            [option.key for option in models[0].effort_options],
            ["off", "minimal", "medium", "high", "max"],
        )
        self.assertFalse(models[1].supports_effort)
        self.assertEqual([option.key for option in models[1].effort_options], ["off"])

    def test_available_models_accept_empty_auth_catalog_and_reject_bad_maps(self) -> None:
        self.assertEqual(parse_pi_models({"data": {"models": []}}), ())
        with self.assertRaisesRegex(HarnessControlError, "thinkingLevelMap.low"):
            parse_pi_models(
                {
                    "data": {
                        "models": [
                            {
                                "provider": "provider",
                                "id": "model",
                                "name": "Model",
                                "reasoning": True,
                                "thinkingLevelMap": {"low": 7},
                            }
                        ]
                    }
                }
            )


class PiRpcAdapterTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_mutation_timeouts_hold_unknown_barrier_until_explicit_resolution(self) -> None:
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
                        detail="test operator cleared the unknown mutation barrier",
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
        for line in (FIXTURES / "activity.jsonl").read_bytes().splitlines(keepends=True):
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

    async def test_disconnect_before_response_reconnects_by_session_and_entries_without_resend(
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


class PiSubmissionLedgerTests(unittest.IsolatedAsyncioTestCase):
    """What the adapter's request-id ledger retains, refuses and forgets.

    The ledger is the whole of Pi's reconciliation story: an ambiguous send leaves a row
    behind, and that row is the only evidence the write may have landed. Its eviction rule
    is therefore a correctness rule, not a memory-management one.
    """

    async def test_a_repeated_request_id_is_refused_before_anything_reaches_pi(self) -> None:
        # The request id is the vendor correlation id, so reusing one would make two
        # different prompts indistinguishable in reconciliation. The refusal has to land
        # before the write, or the ambiguity it prevents has already happened.
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=_TransportSequence(transport))
        await adapter.start(_launch())
        try:
            operation = _operation("request-1")
            await adapter.preflight_operation(operation)
            await adapter.submit(_prompt("request-1", operation=operation))
            prompts_before = [c for c in transport.commands if c["type"] == "prompt"]

            # A retry of the operation that is still prepared: the write already went out,
            # so the ledger -- not the operation gate -- is what has to refuse it.
            with self.assertRaisesRegex(HarnessControlError, "duplicate Pi RPC request id"):
                await adapter.submit(_prompt("request-1", operation=operation))

            self.assertEqual(
                [c for c in transport.commands if c["type"] == "prompt"], prompts_before
            )
        finally:
            await adapter.stop("forced")

    async def test_a_refused_prompt_settles_its_row_so_the_ledger_can_move_on(self) -> None:
        """A rejection is evidence, and evidence makes the row droppable.

        Only ``accepted``/``rejected`` rows may be evicted. A refusal that left its row
        ``pending`` would be indistinguishable from an ambiguous send, and a ledger of
        those refuses the next submission outright -- so a run of refused prompts would
        wedge the seat.
        """
        transport = _FakePiTransport()
        transport.prompt_refusals.append("Pi refused the prompt")
        adapter = PiRpcAdapter(
            transport_factory=_TransportSequence(transport),
            limits=PiAdapterLimits(submission=1),
        )
        await adapter.start(_launch())
        try:
            refused = await _direct_submit(adapter, "refused-1")

            self.assertEqual(refused.acceptance, "rejected")
            assert refused.detail is not None
            self.assertIn("Pi refused the prompt", refused.detail)

            # The proof that the row settled: the one-slot ledger admits the next send.
            accepted = await _direct_submit(adapter, "after-refusal")
            self.assertEqual(accepted.acceptance, "immediate")
        finally:
            await adapter.stop("forced")

    def test_a_ledger_of_ambiguous_sends_refuses_the_next_row(self) -> None:
        """An unknown row is never evicted, even when that costs the next submission.

        Forgetting one would destroy the only record that a write may have reached Pi, and
        with it the operator's ability to ask. Refusing is the honest failure: the seat
        stops accepting prompts until somebody resolves the ambiguity, rather than
        quietly losing the evidence that there was one.
        """
        ledger = PiSubmissionLedger(2)
        for request_id, state in (("settled", "accepted"), ("ambiguous", "unknown")):
            ledger.remember(request_id, self._evidence(request_id, state))

        # One settled row is droppable, so the third submission fits.
        ledger.remember("third", self._evidence("third", "pending"))
        self.assertFalse(ledger.knows("settled"))
        self.assertTrue(ledger.knows("ambiguous"))

        # Now nothing is settled, and the ledger says so instead of forgetting one.
        with self.assertRaisesRegex(HarnessControlError, "ledger is full of ambiguous sends"):
            ledger.remember("fourth", self._evidence("fourth", "pending"))
        self.assertTrue(ledger.knows("ambiguous"))
        self.assertTrue(ledger.knows("third"))

    def test_a_settled_row_is_evicted_oldest_first_and_its_evidence_is_gone(self) -> None:
        # Eviction order is what decides which reconciliation input survives, so it is
        # asserted rather than left to the mapping's iteration order by accident.
        ledger = PiSubmissionLedger(2)
        ledger.remember("oldest", self._evidence("oldest", "accepted"))
        ledger.remember("newer", self._evidence("newer", "rejected"))

        ledger.remember("newest", self._evidence("newest", "pending"))

        self.assertIsNone(ledger.get("oldest"))
        self.assertTrue(ledger.knows("newer"))
        held = ledger.get("newest")
        assert held is not None
        self.assertEqual(held.state, "pending")

    def _evidence(self, request_id: str, state: str) -> PiSubmissionEvidence:
        return PiSubmissionEvidence(
            request=_prompt(request_id),
            cursor_before="entry-0",
            state=state,  # type: ignore[arg-type]
        )
