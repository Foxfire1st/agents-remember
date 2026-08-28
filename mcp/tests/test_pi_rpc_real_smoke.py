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

import pytest
from _pi_rpc_capabilities import (
    CAPABILITY_SCHEMA,
    observe_capabilities,
    observe_version,
)
from agents_remember.errors import HarnessAdapterBusyError
from agents_remember.models.conversations.control_wire import (
    ControlIdentity,
    ControlOperationRef,
    LaunchSpec,
)
from agents_remember.serving.harness_control_models import (
    PromptRequest,
)
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter
from agents_remember.serving.pi_rpc_process import PiRpcSubprocess, WriteGuard
from agents_remember.serving.pi_rpc_protocol import (
    PI_RPC_PACKAGE,
    parse_pi_response,
    parse_pi_state,
    pi_rpc_launch,
)

# Must match the version the product pins: mcp/native_helpers/conversation_library/package.json
# and the native flag contract in serving/pi_rpc_adapter.py. A smoke test that installs a
# different build proves the adapter works against a runtime nobody ships.
PI_RPC_VERSION = "0.80.7"

# The literal path keeps the lifecycle catalog's consumer relationship source-observable. The
# capability test separately requires the recording's embedded version to equal PI_RPC_VERSION,
# so bumping the runtime pin without re-recording still fails loudly.
CAPABILITY_FIXTURE = Path(__file__).parent / "fixtures/pi_rpc/0.80.7-capabilities.json"


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_real_smoke.py:52).
def install_pinned_pi(root: Path, *, home: Path) -> Path:  # pragma: no cover
    """Install exactly ``PI_RPC_VERSION`` into ``root`` and return its executable.

    One install path for the whole module: a second one could drift to a different build
    and quietly re-validate the wrong runtime, which is the failure this file exists to
    prevent.
    """
    prefix = root / "pi"
    prefix.mkdir(parents=True, exist_ok=True)
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
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "npm_config_cache": str(root / "npm-cache"),
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "npm_config_loglevel": "error",
        },
        timeout=600,
    )
    return prefix / "node_modules" / ".bin" / "pi"


class _RecordingPiRpcSubprocess(PiRpcSubprocess):
    """Retain commands that crossed the guarded-write seam and native queue events."""

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_real_smoke.py:89).
    def __init__(self) -> None:  # pragma: no cover
        super().__init__()
        self.written_commands: list[dict[str, object]] = []
        self.native_events: list[dict[str, object]] = []

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_real_smoke.py:94).
    async def send(  # pragma: no cover
        self,
        command: Mapping[str, object],
        *,
        before_write: WriteGuard | None = None,
    ) -> None:
        await super().send(command, before_write=before_write)
        self.written_commands.append(dict(command))

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_real_smoke.py:103).
    async def _dispatch(self, frame: Mapping[str, object]) -> None:  # pragma: no cover
        if frame.get("type") != "response":
            self.native_events.append(dict(frame))
        await super()._dispatch(frame)


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_real_smoke.py:109).
def _holding_completion_handler(  # pragma: no cover
    request_started: threading.Event, release_response: threading.Event
) -> type[BaseHTTPRequestHandler]:
    """An OpenAI-completions endpoint that holds a real provider stream open on demand.

    It answers with the opening chunk, signals ``request_started``, and then blocks until
    ``release_response`` — which is what keeps the installed Pi genuinely streaming while the
    test proves a second prompt cannot slip past the busy guard.
    """

    class BlockingCompletionHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_real_smoke.py:122).
        def do_POST(self) -> None:  # pragma: no cover
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
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
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

        # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_real_smoke.py:154).
        def log_message(self, _format: str, *args: object) -> None:  # pragma: no cover
            pass

    return BlockingCompletionHandler


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_real_smoke.py:160).
def _smoke_workspace(root: Path) -> tuple[Path, Path, Path]:  # pragma: no cover
    """The home / project / Pi-config triple an isolated installed run needs, created."""
    home, project, config = root / "home", root / "project", root / "pi-config"
    for path in (home, project, config):
        path.mkdir()
    return home, project, config


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_real_smoke.py:168).
def _stale_window_launch(
    executable: str, *, home: Path, project: Path, config: Path
) -> LaunchSpec:  # pragma: no cover
    """Launch the installed Pi with every ambient source of state switched off.

    Sessions, extensions, skills, prompt templates, themes, context files and tools are all
    disabled so the only thing that can move Pi's state is the prompt this test sends.
    """
    return LaunchSpec(
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


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_real_smoke.py:205).
def _smoke_provider_models(port: int) -> str:  # pragma: no cover
    """A single non-reasoning model pointed at the local holding endpoint."""
    return json.dumps(
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


@pytest.mark.ar_run_pi_rpc_smoke
@unittest.skipUnless(
    os.environ.get("AR_RUN_PI_RPC_SMOKE") == "1",
    "set AR_RUN_PI_RPC_SMOKE=1 to install and run the pinned Pi RPC smoke",
)
class PiRpcRealSmokeTests(unittest.IsolatedAsyncioTestCase):
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_real_smoke.py:242).
    async def test_pinned_isolated_install_reaches_get_state_ready(
        self,
    ) -> None:  # pragma: no cover
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
            executable = install_pinned_pi(root, home=home)
            self.assertEqual(observe_version(str(executable)), PI_RPC_VERSION)

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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_real_smoke.py:324).
    async def test_committed_capability_fixture_still_describes_the_installed_runtime(  # pragma: no cover
        self,
    ) -> None:
        """Re-verify the recording against the build it claims to describe.

        The fixture is evidence about a runtime, so leaving it to be maintained by hand
        means it can go on asserting a surface no shipped Pi has. Everything checked here
        is read off a Pi this test installed and drove: the framing off the bytes, the
        state fields off a live ``get_state``, the events off a real offline turn, and the
        dialog/fire-and-forget split off an extension that calls all nine UI methods and
        reports which ones waited for a reply.
        """
        fixture = json.loads(CAPABILITY_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(fixture["schema"], CAPABILITY_SCHEMA)
        self.assertEqual(fixture["package"], PI_RPC_PACKAGE)
        # The pin and the recording are one fact. A bump that forgets the fixture lands
        # here rather than passing against a build nobody ships.
        self.assertEqual(fixture["version"], PI_RPC_VERSION)
        self.assertEqual(
            list(self._rpc_launch_argv()[1:]),
            fixture["launch"],
            "the adapter no longer launches Pi with the argv the recording claims",
        )

        with tempfile.TemporaryDirectory(prefix="ar-pi-capabilities-") as temp:
            root = Path(temp)
            home = root / "home"
            home.mkdir()
            executable = install_pinned_pi(root, home=home)
            observed = observe_capabilities(
                str(executable), workspace=root / "probe", commands=fixture["commands"]
            )

        self.assertEqual(observed.version, fixture["version"])
        self.assertTrue(
            observed.unknown_command_rejected,
            "the runtime did not reject the probe's unknown-command control, so its "
            "evidence that the recorded commands exist proves nothing",
        )
        self.assertEqual(observed.framing, fixture["framing"])
        self.assertEqual(observed.dispatched_commands, frozenset(fixture["commands"]))
        self.assertEqual(observed.dialog_methods, frozenset(fixture["dialogMethods"]))
        self.assertEqual(
            observed.fire_and_forget_methods, frozenset(fixture["fireAndForgetMethods"])
        )
        # Pi may report more state and fire more events than the adapter reads; it may not
        # stop reporting something the adapter depends on.
        self.assertLessEqual(frozenset(fixture["stateFields"]), observed.state_fields)
        self.assertLessEqual(frozenset(fixture["events"]), observed.events)

    @staticmethod
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_real_smoke.py:375).
    def _rpc_launch_argv() -> tuple[str, ...]:  # pragma: no cover
        """The argv the adapter builds for RPC mode, with the vendor path factored out."""
        return pi_rpc_launch(
            LaunchSpec(
                identity=ControlIdentity(
                    ar_session_id="capability-recording",
                    tmux_name="capability-recording",
                    created_at="2026-07-31T00:00:00+00:00",
                ),
                harness_id="pi",
                cwd=Path.cwd(),
                argv=("pi",),
            )
        ).argv

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_real_smoke.py:390).
    async def test_installed_guard_rejects_stale_idle_without_native_queueing(
        self,
    ) -> None:  # pragma: no cover
        executable = shutil.which("pi")
        if executable is None:
            self.skipTest("installed Pi executable is unavailable")

        request_started = threading.Event()
        release_response = threading.Event()
        handler = _holding_completion_handler(request_started, release_response)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="ar-pi-installed-smoke-") as temp:
                home, project, config = _smoke_workspace(Path(temp))
                port = int(server.server_address[1])
                (config / "models.json").write_text(_smoke_provider_models(port))
                transport = _RecordingPiRpcSubprocess()
                adapter = PiRpcAdapter(transport_factory=lambda: transport)
                await adapter.start(
                    _stale_window_launch(executable, home=home, project=project, config=config)
                )
                try:
                    candidate_text = "candidate must never reach Pi"
                    operation = ControlOperationRef(
                        bridge_epoch="installed-pi-epoch",
                        sequence=1,
                        operation_id="candidate-prompt",
                        kind="prompt",
                    )
                    await adapter.preflight_operation(operation)
                    await self._hold_pi_in_a_live_stream(transport, request_started)

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

                    await self._assert_candidate_never_reached_pi(transport, candidate_text)
                finally:
                    release_response.set()
                    await adapter.stop("forced")
        finally:
            release_response.set()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_pi_rpc_real_smoke.py:443).
    async def _hold_pi_in_a_live_stream(  # pragma: no cover
        self, transport: _RecordingPiRpcSubprocess, request_started: threading.Event
    ) -> None:
        """Seed a prompt that the holding endpoint keeps open, and wait until Pi says so.

        The guard under test rejects on observed state, so the test must not proceed on the
        seed's acknowledgement alone: it waits for the provider request to land and for Pi to
        emit a state event past the idle token.
        """
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

    # 260731-EFA-L7 R10: AR_RUN_PI_RPC_SMOKE-gated helper; needs the installed Pi RPC build.
    async def _assert_candidate_never_reached_pi(  # pragma: no cover
        self, transport: _RecordingPiRpcSubprocess, candidate_text: str
    ) -> None:
        """The rejected prompt left no trace: not on the wire, not in Pi's own queue.

        Pi has a native queue, so "rejected" is only true if the candidate never crossed the
        write seam AND never appears in a queue_update — the guard must not have quietly
        handed the body to Pi to hold.
        """
        prompt_commands = [
            command for command in transport.written_commands if command.get("type") == "prompt"
        ]
        self.assertEqual([command["id"] for command in prompt_commands], ["busy-seed"])
        self.assertNotIn(candidate_text, json.dumps(prompt_commands))

        state_request_id = "state-after-rejection"
        state_frame = await transport.request({"id": state_request_id, "type": "get_state"})
        state = parse_pi_state(
            parse_pi_response(state_frame, request_id=state_request_id, command="get_state")
        )
        self.assertTrue(state.is_streaming)
        self.assertEqual(state.pending_message_count, 0)
        queue_events = [
            event for event in transport.native_events if event.get("type") == "queue_update"
        ]
        self.assertNotIn(candidate_text, json.dumps(queue_events))
        for event in queue_events:
            self.assertEqual(event.get("steering"), [])
            self.assertEqual(event.get("followUp"), [])
