"""Opt-in isolated smoke against the pinned real Pi RPC npm package."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agents_remember.errors import HarnessAdapterBusyError
from agents_remember.serving.harness_control_models import (
    ControlIdentity,
    ControlOperationRef,
    LaunchSpec,
    PromptRequest,
)
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter
from agents_remember.serving.pi_rpc_process import PiRpcSubprocess, WriteGuard
from agents_remember.serving.pi_rpc_protocol import (
    PI_RPC_PACKAGE,
    parse_pi_response,
    parse_pi_state,
)

PI_RPC_VERSION = "0.80.6"


class _RecordingPiRpcSubprocess(PiRpcSubprocess):
    """Retain commands that crossed the guarded-write seam and native queue events."""

    def __init__(self) -> None:
        super().__init__()
        self.written_commands: list[dict[str, object]] = []
        self.native_events: list[dict[str, object]] = []

    async def send(
        self,
        command: Mapping[str, object],
        *,
        before_write: WriteGuard | None = None,
    ) -> None:
        await super().send(command, before_write=before_write)
        self.written_commands.append(dict(command))

    async def _dispatch(self, frame: Mapping[str, object]) -> None:
        if frame.get("type") != "response":
            self.native_events.append(dict(frame))
        await super()._dispatch(frame)


@unittest.skipUnless(
    os.environ.get("AR_RUN_PI_RPC_SMOKE") == "1",
    "set AR_RUN_PI_RPC_SMOKE=1 to install and run the pinned Pi RPC smoke",
)
class PiRpcRealSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_pinned_isolated_install_reaches_get_state_ready(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ar-pi-rpc-smoke-") as temp:
            root = Path(temp)
            home = root / "home"
            project = root / "project"
            prefix = root / "pi"
            config = root / "pi-config"
            for path in (home, project, prefix, config):
                path.mkdir()
            (config / "models.json").write_text(
                json.dumps(
                    {
                        "providers": {
                            "smoke": {
                                "baseUrl": "http://127.0.0.1:9/v1",
                                "api": "openai-completions",
                                "apiKey": "smoke",
                                "models": [
                                    {
                                        "id": "idle",
                                        "name": "Startup smoke idle",
                                        "reasoning": False,
                                        "input": ["text"],
                                        "contextWindow": 4096,
                                        "maxTokens": 64,
                                        "cost": {
                                            "input": 0,
                                            "output": 0,
                                            "cacheRead": 0,
                                            "cacheWrite": 0,
                                        },
                                    }
                                ],
                            }
                        }
                    }
                )
            )
            install_env = {
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "npm_config_cache": str(root / "npm-cache"),
                "npm_config_audit": "false",
                "npm_config_fund": "false",
                "npm_config_loglevel": "error",
            }
            subprocess.run(
                [
                    "npm",
                    "install",
                    "--prefix",
                    str(prefix),
                    "--no-save",
                    f"{PI_RPC_PACKAGE}@{PI_RPC_VERSION}",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=install_env,
                timeout=240,
            )
            executable = prefix / "node_modules" / ".bin" / "pi"
            version = subprocess.run(
                [str(executable), "--version"],
                check=True,
                capture_output=True,
                text=True,
                env=install_env,
                timeout=30,
            ).stdout.strip()
            self.assertEqual(version, PI_RPC_VERSION)

            launch_env = {
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "PI_CODING_AGENT_DIR": str(config),
                "XDG_CONFIG_HOME": str(root / "xdg"),
                "NO_COLOR": "1",
            }
            launch = LaunchSpec(
                identity=ControlIdentity(
                    ar_session_id="real-pi-smoke",
                    tmux_name="real-pi-smoke",
                    created_at="2026-07-14T09:00:00+00:00",
                ),
                harness_id="pi",
                cwd=project,
                argv=(
                    str(executable),
                    "--model",
                    "smoke/idle",
                    "--no-session",
                    "--offline",
                    "--no-extensions",
                    "--no-skills",
                    "--no-prompt-templates",
                    "--no-themes",
                    "--no-context-files",
                ),
                env=launch_env,
            )
            adapter = PiRpcAdapter()
            handshake = await adapter.start(launch)
            try:
                state = await adapter.snapshot()
                self.assertEqual(handshake.adapter_id, "pi-rpc")
                self.assertEqual(state.control, "ready")
                self.assertEqual(state.activity, "idle")
                self.assertTrue(state.vendor_session_id)
                self.assertEqual(state.raw["vendorProtocol"], "pi-rpc/jsonl")
            finally:
                await adapter.stop("graceful")

    async def test_installed_guard_rejects_stale_idle_without_native_queueing(self) -> None:
        executable = shutil.which("pi")
        if executable is None:
            self.skipTest("installed Pi executable is unavailable")

        request_started = threading.Event()
        release_response = threading.Event()

        class BlockingCompletionHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                content_length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(content_length)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                first = {
                    "id": "chatcmpl-pi-live-smoke",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "hold",
                    "choices": [
                        {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
                    ],
                }
                self.wfile.write(f"data: {json.dumps(first)}\n\n".encode())
                self.wfile.flush()
                request_started.set()
                release_response.wait(timeout=10)
                final = {
                    "id": "chatcmpl-pi-live-smoke",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "hold",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                try:
                    self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except BrokenPipeError:
                    pass

            def log_message(self, _format: str, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), BlockingCompletionHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="ar-pi-installed-smoke-") as temp:
                root = Path(temp)
                home = root / "home"
                project = root / "project"
                config = root / "pi-config"
                for path in (home, project, config):
                    path.mkdir()
                port = int(server.server_address[1])
                (config / "models.json").write_text(
                    json.dumps(
                        {
                            "providers": {
                                "smoke": {
                                    "baseUrl": f"http://127.0.0.1:{port}/v1",
                                    "api": "openai-completions",
                                    "apiKey": "smoke",
                                    "models": [
                                        {
                                            "id": "hold",
                                            "name": "Live smoke hold",
                                            "reasoning": False,
                                            "input": ["text"],
                                            "contextWindow": 4096,
                                            "maxTokens": 64,
                                            "cost": {
                                                "input": 0,
                                                "output": 0,
                                                "cacheRead": 0,
                                                "cacheWrite": 0,
                                            },
                                        }
                                    ],
                                }
                            }
                        }
                    )
                )
                transport = _RecordingPiRpcSubprocess()
                adapter = PiRpcAdapter(transport_factory=lambda: transport)
                launch = LaunchSpec(
                    identity=ControlIdentity(
                        ar_session_id="installed-pi-stale-window",
                        tmux_name="installed-pi-stale-window",
                        created_at="2026-07-17T12:00:00+00:00",
                    ),
                    harness_id="pi",
                    cwd=project,
                    argv=(
                        executable,
                        "--model",
                        "smoke/hold",
                        "--no-session",
                        "--offline",
                        "--no-extensions",
                        "--no-skills",
                        "--no-prompt-templates",
                        "--no-themes",
                        "--no-context-files",
                        "--no-tools",
                    ),
                    env={
                        "PATH": os.environ["PATH"],
                        "HOME": str(home),
                        "PI_CODING_AGENT_DIR": str(config),
                        "PI_OFFLINE": "1",
                        "NO_COLOR": "1",
                    },
                )
                await adapter.start(launch)
                try:
                    candidate_text = "candidate must never reach Pi"
                    operation = ControlOperationRef(
                        bridge_epoch="installed-pi-epoch",
                        sequence=1,
                        operation_id="candidate-prompt",
                        kind="prompt",
                    )
                    await adapter.preflight_operation(operation)
                    idle_event_token = transport.event_token

                    seed_response = await asyncio.wait_for(
                        transport.request(
                            {
                                "id": "busy-seed",
                                "type": "prompt",
                                "message": "hold Pi in the live provider stream",
                            }
                        ),
                        timeout=5,
                    )
                    self.assertTrue(seed_response["success"])
                    self.assertTrue(await asyncio.to_thread(request_started.wait, 5))
                    for _ in range(100):
                        if transport.event_token > idle_event_token:
                            break
                        await asyncio.sleep(0.01)
                    self.assertGreater(transport.event_token, idle_event_token)

                    with self.assertRaises(HarnessAdapterBusyError):
                        await adapter.submit(
                            PromptRequest(
                                request_id="candidate-prompt",
                                source="terminal",
                                text=candidate_text,
                                submitted_at="2026-07-17T12:00:01+00:00",
                                operation=operation,
                            )
                        )

                    prompt_commands = [
                        command
                        for command in transport.written_commands
                        if command.get("type") == "prompt"
                    ]
                    self.assertEqual([command["id"] for command in prompt_commands], ["busy-seed"])
                    self.assertNotIn(candidate_text, json.dumps(prompt_commands))

                    state_request_id = "state-after-rejection"
                    state_frame = await transport.request(
                        {"id": state_request_id, "type": "get_state"}
                    )
                    state = parse_pi_state(
                        parse_pi_response(
                            state_frame,
                            request_id=state_request_id,
                            command="get_state",
                        )
                    )
                    self.assertTrue(state.is_streaming)
                    self.assertEqual(state.pending_message_count, 0)
                    queue_events = [
                        event
                        for event in transport.native_events
                        if event.get("type") == "queue_update"
                    ]
                    self.assertNotIn(candidate_text, json.dumps(queue_events))
                    for event in queue_events:
                        self.assertEqual(event.get("steering"), [])
                        self.assertEqual(event.get("followUp"), [])
                finally:
                    release_response.set()
                    await adapter.stop("forced")
        finally:
            release_response.set()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)
