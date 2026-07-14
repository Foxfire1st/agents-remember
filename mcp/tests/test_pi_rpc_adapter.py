"""Fake-transport conformance for the strict Pi RPC adapter."""

from __future__ import annotations

import asyncio
import json
import unittest
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from pathlib import Path
from typing import cast

from agents_remember.errors import HarnessAdapterDisconnectedError, HarnessControlError
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_models import (
    AdapterEvent,
    AdapterSnapshot,
    ControlIdentity,
    InteractionResponse,
    LaunchSpec,
    PromptRequest,
    ShutdownMode,
    SubmissionSource,
)
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter
from agents_remember.serving.pi_rpc_protocol import (
    PI_RPC_DIALOG_METHODS,
    PI_RPC_FIRE_AND_FORGET_METHODS,
    PI_RPC_PACKAGE,
    SUPPORTED_PI_RPC_VERSIONS,
    PiRpcJsonlDecoder,
    encode_pi_rpc_frame,
    pi_rpc_launch,
    require_pi_rpc_version,
)

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
        self.session = {
            "model": None,
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
        self.event_queue: asyncio.Queue[
            Mapping[str, object] | HarnessControlError | HarnessAdapterDisconnectedError | None
        ] = asyncio.Queue()

    async def start(self, launch: LaunchSpec) -> None:
        self.launches.append(launch)

    async def request(self, command: Mapping[str, object]) -> Mapping[str, object]:
        copied = dict(command)
        self.commands.append(copied)
        request_id = cast(str, command["id"])
        command_type = cast(str, command["type"])
        if command_type == "get_state":
            return _success(request_id, "get_state", dict(self.session))
        if command_type == "get_entries":
            entries = self.entries
            since = command.get("since")
            if since is not None:
                index = next(
                    (
                        position
                        for position, entry in enumerate(entries)
                        if entry.get("id") == since
                    ),
                    None,
                )
                if index is None:
                    return {
                        "id": request_id,
                        "type": "response",
                        "command": "get_entries",
                        "success": False,
                        "error": f"Entry not found: {since}",
                    }
                entries = entries[index + 1 :]
            return _success(
                request_id,
                "get_entries",
                {"entries": entries, "leafId": self.leaf_id},
            )
        if command_type == "prompt":
            if self.prompt_failures:
                raise self.prompt_failures.popleft()
            return {
                "id": request_id,
                "type": "response",
                "command": "prompt",
                "success": True,
            }
        raise AssertionError(f"unexpected fake command: {command_type}")

    async def send(self, command: Mapping[str, object]) -> None:
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


def _prompt(request_id: str, *, source: SubmissionSource = "durable") -> PromptRequest:
    return PromptRequest(
        request_id=request_id,
        source=source,
        text=f"prompt {request_id}",
        submitted_at="2026-07-14T09:01:00+00:00",
    )


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

    def test_capability_fixture_is_the_narrow_code_policy(self) -> None:
        fixture = json.loads((FIXTURES / "0.80.6-capabilities.json").read_text())
        self.assertEqual(fixture["package"], PI_RPC_PACKAGE)
        self.assertEqual({fixture["version"]}, SUPPORTED_PI_RPC_VERSIONS)
        self.assertEqual(set(fixture["dialogMethods"]), PI_RPC_DIALOG_METHODS)
        self.assertEqual(set(fixture["fireAndForgetMethods"]), PI_RPC_FIRE_AND_FORGET_METHODS)
        require_pi_rpc_version("0.80.6")
        with self.assertRaisesRegex(HarnessControlError, "unsupported Pi RPC version"):
            require_pi_rpc_version("0.80.5")


class PiRpcAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_and_prompt_ack_preserve_launch_and_correlation(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(
            version="0.80.6",
            transport_factory=_TransportSequence(transport),
            clock=lambda: "2026-07-14T09:02:00+00:00",
        )
        handshake = await adapter.start(_launch())
        try:
            receipt = await adapter.submit(_prompt("request-1"))
            self.assertEqual(handshake.snapshot.control, "ready")
            self.assertEqual(handshake.snapshot.vendor_session_id, "pi-session-1")
            self.assertEqual(receipt.acceptance, "immediate")
            self.assertEqual(receipt.vendor_correlation_id, "request-1")
            self.assertEqual(
                transport.launches[0].argv, ("pi", "--mode", "rpc", *_launch().argv[1:])
            )
            prompt = next(command for command in transport.commands if command["type"] == "prompt")
            self.assertEqual(prompt["id"], "request-1")
            self.assertNotIn("streamingBehavior", prompt)
        finally:
            await adapter.stop("forced")

    async def test_streaming_prompts_use_source_specific_queue_behavior(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(version="0.80.6", transport_factory=_TransportSequence(transport))
        await adapter.start(_launch())
        stream = cast(AsyncGenerator[AdapterEvent], adapter.subscribe())
        try:
            transport.emit({"type": "agent_start"})
            running = await asyncio.wait_for(anext(stream), timeout=1.0)
            running_snapshot = running.snapshot
            assert running_snapshot is not None
            self.assertEqual(running_snapshot.activity, "running")
            terminal = await adapter.submit(_prompt("terminal-1", source="terminal"))
            durable = await adapter.submit(_prompt("durable-1", source="durable"))
            self.assertEqual((terminal.acceptance, durable.acceptance), ("queued", "queued"))
            prompts = [item for item in transport.commands if item["type"] == "prompt"]
            self.assertEqual([item["streamingBehavior"] for item in prompts], ["steer", "followUp"])
        finally:
            await adapter.stop("forced")
            await stream.aclose()

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
                adapter = PiRpcAdapter(
                    version="0.80.6", transport_factory=_TransportSequence(transport)
                )
                handshake = await adapter.start(_launch())
                try:
                    self.assertEqual(handshake.snapshot.activity, expected)
                    self.assertEqual(handshake.snapshot.acceptance, "queued")
                finally:
                    await adapter.stop("forced")

    async def test_retry_compaction_and_agent_settled_are_not_early_idle(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(version="0.80.6", transport_factory=_TransportSequence(transport))
        await adapter.start(_launch())
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
                    version="0.80.6",
                    transport_factory=_TransportSequence(transport),
                    interaction_limit=4,
                )
                await adapter.start(_launch())
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
                            )
                        )
                        self.assertLessEqual(adapter.retained_interaction_count, 1)
                    responses = [
                        item
                        for item in transport.commands
                        if item["type"] == "extension_ui_response"
                    ]
                    self.assertEqual(len(responses), size)
                    self.assertTrue(all(item["confirmed"] is True for item in responses))
                    self.assertEqual(adapter.retained_interaction_count, 0)
                finally:
                    await adapter.stop("forced")
                    await stream.aclose()

    async def test_disconnect_before_ack_rejects_without_resend(self) -> None:
        transport = _FakePiTransport()
        transport.prompt_failures.append(
            HarnessAdapterDisconnectedError("closed before write", may_have_sent=False)
        )
        adapter = PiRpcAdapter(version="0.80.6", transport_factory=_TransportSequence(transport))
        bridge = HarnessControlBridge(_identity(), adapter)
        await bridge.start(_launch())
        try:
            receipt = await bridge.submit(
                bridge.prompt("before", source="durable", request_id="before")
            )
            self.assertEqual(receipt.acceptance, "rejected")
            self.assertEqual(
                len([item for item in transport.commands if item["type"] == "prompt"]), 1
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
        adapter = PiRpcAdapter(
            version="0.80.6", transport_factory=_TransportSequence(first, second)
        )
        bridge = HarnessControlBridge(_identity(), adapter)
        await bridge.start(_launch())
        try:
            receipt = await bridge.submit(
                bridge.prompt("ambiguous", source="durable", request_id="ambiguous-1")
            )
            self.assertEqual(receipt.acceptance, "unknown")
            reconciled = await bridge.reconcile("ambiguous-1")
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
        adapter = PiRpcAdapter(version="0.80.6", transport_factory=_TransportSequence(transport))
        bridge = HarnessControlBridge(_identity(), adapter)
        await bridge.start(_launch())
        try:
            receipt = await bridge.submit(
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
        adapter = PiRpcAdapter(version="0.80.6", transport_factory=_TransportSequence(transport))
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
