from __future__ import annotations

import asyncio
import io
import unittest
from collections.abc import AsyncIterator
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    AcceptanceState,
    AdapterSnapshot,
    ControlIdentity,
    LaunchSpec,
    SubmissionReceipt,
)
from agents_remember.serving.codex_app_server_adapter import CodexAppServerAdapter
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    EffortOption,
    ModelCapability,
)
from agents_remember.serving.harness_control_adapter import UnsupportedHarnessProtocolAdapter
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_claude import ClaudeStreamJsonAdapter
from agents_remember.serving.harness_control_factories import (
    create_harness_protocol_adapter,
    harness_launch_knobs,
)
from agents_remember.serving.harness_control_models import (
    PromptRequest,
    TerminalResult,
    TranscriptEntry,
)
from agents_remember.serving.harness_control_runner import (
    RunnerConfig,
    _prepare_controlled_launch,
    _read_terminal_input,
    _render_updates,
    _submit_session_commands,
    adapter_argv,
    control_runner_command,
    parse_runner_config,
    run_controlled_session,
)
from agents_remember.serving.harness_launch import ResolvedLaunch
from agents_remember.serving.harness_terminal_surface import HarnessTerminalSurface
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter

NOW = "2026-07-14T10:00:00+00:00"


def _identity() -> ControlIdentity:
    return ControlIdentity("session-1", "ar-session-1", NOW)


class RunnerConfigTests(unittest.TestCase):
    def test_command_round_trip_and_malformed_payloads(self) -> None:
        config = RunnerConfig(
            identity=_identity(),
            harness_id="claude",
            cwd=Path("/workspace"),
            argv=("claude",),
            endpoint_root=Path("/runtime/control"),
            session_commands=("/color blue",),
            resolved_launch=ResolvedLaunch("claude", "claude-fable-5", "max", Path("/workspace")),
        )
        command = control_runner_command(config)
        self.assertEqual(command[1:3], ("-m", "agents_remember.serving.harness_control_runner"))
        self.assertEqual(parse_runner_config(command[3]), config)
        for malformed in ("not-base64", "W10=", "e30="):
            with self.subTest(malformed=malformed), self.assertRaises(HarnessControlError):
                parse_runner_config(malformed)

    def test_codex_runner_uses_app_server_and_preserves_arguments_for_conflict_validation(
        self,
    ) -> None:
        config = RunnerConfig(
            identity=_identity(),
            harness_id="codex",
            cwd=Path("/workspace"),
            argv=(
                "codex",
                "--model",
                "gpt-5.6-sol",
                "--config",
                "model_reasoning_effort=xhigh",
                "--sandbox",
                "workspace-write",
                "--model",
            ),
            endpoint_root=Path("/runtime/control"),
        )
        self.assertEqual(
            adapter_argv(config.harness_id, config.argv),
            (
                "codex",
                "app-server",
                "--model",
                "gpt-5.6-sol",
                "--config",
                "model_reasoning_effort=xhigh",
                "--sandbox",
                "workspace-write",
                "--model",
            ),
        )
        self.assertEqual(
            adapter_argv("claude", ("claude", "--verbose")),
            ("claude", "--verbose"),
        )

    def test_factory_maps_all_builtins_and_keeps_custom_unsupported(self) -> None:
        self.assertIsInstance(
            create_harness_protocol_adapter("claude", env={}), ClaudeStreamJsonAdapter
        )
        self.assertIsInstance(
            create_harness_protocol_adapter("codex", env={}),
            CodexAppServerAdapter,
        )
        self.assertIsInstance(create_harness_protocol_adapter("pi", env={}), PiRpcAdapter)
        self.assertIsInstance(
            create_harness_protocol_adapter("custom", env={}),
            UnsupportedHarnessProtocolAdapter,
        )

    def test_native_adapters_own_their_exact_launch_channels(self) -> None:
        self.assertEqual(
            harness_launch_knobs("claude", model_key="claude-fable-5", effort="max").argv,
            ("--model", "claude-fable-5", "--effort", "max"),
        )
        self.assertEqual(
            harness_launch_knobs("codex", model_key="gpt-test", effort="high").session_config,
            {"model": "gpt-test", "model_reasoning_effort": "high"},
        )
        self.assertEqual(
            harness_launch_knobs("pi", model_key="provider/model", effort="xhigh").argv,
            ("--model", "provider/model", "--thinking", "xhigh"),
        )
        with self.assertRaisesRegex(HarnessControlError, "no protocol adapter is registered"):
            harness_launch_knobs("custom", model_key="provider/model", effort="high")


class _LaunchAdapter:
    def __init__(self) -> None:
        self.discovered_argv: tuple[str, ...] | None = None

    async def discover(self, launch: LaunchSpec) -> CapabilitySnapshot:
        self.discovered_argv = launch.argv
        return CapabilitySnapshot(
            models=(
                ModelCapability(
                    key="provider/model",
                    display_name="Model",
                    supports_effort=True,
                    effort_options=(EffortOption("high", "high"),),
                    default_effort="high",
                ),
            ),
            selected_model_key=None,
            selected_effort=None,
        )


class LaunchPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_roleless_codex_ignores_ambient_spawn_knobs_and_keeps_dynamic_defaults(
        self,
    ) -> None:
        config = RunnerConfig(
            identity=_identity(),
            harness_id="codex",
            cwd=Path("/workspace"),
            argv=("codex", "--sandbox", "workspace-write"),
            endpoint_root=Path("/runtime/control"),
        )

        adapter, launch = await _prepare_controlled_launch(
            config,
            env={"AR_SPAWN_MODEL": "ambient-model", "AR_SPAWN_EFFORT": "ambient-effort"},
        )

        self.assertIsInstance(adapter, CodexAppServerAdapter)
        codex = cast(CodexAppServerAdapter, adapter)
        self.assertIsNone(codex._settings.model)
        self.assertIsNone(codex._settings.reasoning_effort)
        self.assertEqual(launch.argv, ("codex", "app-server", "--sandbox", "workspace-write"))

    async def test_dynamic_discovery_precedes_native_launch_knob_application(self) -> None:
        discoverer = _LaunchAdapter()
        runtime = _LaunchAdapter()
        config = RunnerConfig(
            identity=_identity(),
            harness_id="pi",
            cwd=Path("/workspace"),
            argv=("pi", "--no-session"),
            endpoint_root=Path("/runtime/control"),
            resolved_launch=ResolvedLaunch("pi", "provider/model", "high", Path("/workspace")),
        )
        with mock.patch(
            "agents_remember.serving.harness_control_runner.create_harness_protocol_adapter",
            side_effect=[discoverer, runtime],
        ):
            adapter, launch = await _prepare_controlled_launch(config, env={"KEEP": "yes"})

        self.assertIs(adapter, runtime)
        self.assertEqual(discoverer.discovered_argv, ("pi", "--no-session"))
        self.assertEqual(
            launch.argv,
            (
                "pi",
                "--model",
                "provider/model",
                "--thinking",
                "high",
                "--no-session",
            ),
        )
        self.assertEqual(launch.env["KEEP"], "yes")

    async def test_dynamic_refusal_stops_before_the_fresh_runtime_adapter_is_created(self) -> None:
        discoverer = _LaunchAdapter()
        config = RunnerConfig(
            identity=_identity(),
            harness_id="pi",
            cwd=Path("/workspace"),
            argv=("pi",),
            endpoint_root=Path("/runtime/control"),
            resolved_launch=ResolvedLaunch("pi", "provider/model", "ultracode", Path("/workspace")),
        )
        factory = mock.Mock(return_value=discoverer)
        with (
            mock.patch(
                "agents_remember.serving.harness_control_runner.create_harness_protocol_adapter",
                factory,
            ),
            self.assertRaisesRegex(
                HarnessControlError,
                r"launch effort 'ultracode'.*advertised launch efforts: \[high\]",
            ),
        ):
            await _prepare_controlled_launch(config, env={})

        factory.assert_called_once()
        self.assertEqual(discoverer.discovered_argv, ("pi",))

    async def test_launch_args_cannot_duplicate_an_adapter_owned_selector(self) -> None:
        discoverer = _LaunchAdapter()
        config = RunnerConfig(
            identity=_identity(),
            harness_id="pi",
            cwd=Path("/workspace"),
            argv=("pi", "--model", "other/provider-model"),
            endpoint_root=Path("/runtime/control"),
            resolved_launch=ResolvedLaunch("pi", "provider/model", "high", Path("/workspace")),
        )
        factory = mock.Mock(return_value=discoverer)
        with (
            mock.patch(
                "agents_remember.serving.harness_control_runner.create_harness_protocol_adapter",
                factory,
            ),
            self.assertRaisesRegex(HarnessControlError, r"adapter-owned option.*--model"),
        ):
            await _prepare_controlled_launch(config, env={})

        factory.assert_called_once()
        self.assertIsNone(discoverer.discovered_argv)

    async def test_codex_launch_args_cannot_duplicate_adapter_owned_config(self) -> None:
        discoverer = _LaunchAdapter()
        config = RunnerConfig(
            identity=_identity(),
            harness_id="codex",
            cwd=Path("/workspace"),
            argv=("codex", "--config", "model_reasoning_effort=low"),
            endpoint_root=Path("/runtime/control"),
            resolved_launch=ResolvedLaunch("codex", "provider/model", "high", Path("/workspace")),
        )
        factory = mock.Mock(return_value=discoverer)
        with (
            mock.patch(
                "agents_remember.serving.harness_control_runner.create_harness_protocol_adapter",
                factory,
            ),
            self.assertRaisesRegex(
                HarnessControlError,
                r"adapter-owned config key.*model_reasoning_effort",
            ),
        ):
            await _prepare_controlled_launch(config, env={})

        factory.assert_called_once()
        self.assertIsNone(discoverer.discovered_argv)

    async def test_codex_attached_config_conflict_preflights_the_production_adapter(self) -> None:
        config = RunnerConfig(
            identity=_identity(),
            harness_id="codex",
            cwd=Path("/workspace"),
            argv=("codex", "-cmodel_reasoning_effort=low"),
            endpoint_root=Path("/runtime/control"),
            resolved_launch=ResolvedLaunch("codex", "gpt-5.6-sol", "xhigh", Path("/workspace")),
        )
        discover = mock.AsyncMock()
        with (
            mock.patch.object(CodexAppServerAdapter, "discover", discover),
            self.assertRaisesRegex(
                HarnessControlError,
                r"adapter-owned config key.*model_reasoning_effort",
            ),
        ):
            await _prepare_controlled_launch(config, env={})

        discover.assert_not_awaited()


class _CaptureServer:
    latest: _CaptureServer | None = None

    def __init__(self, _endpoint: object, bridge: HarnessControlBridge) -> None:
        self.bridge = bridge
        self.started = False
        self.closed = False
        self.snapshot_on_close: AdapterSnapshot | None = None
        type(self).latest = self

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.snapshot_on_close = self.bridge.snapshot()
        self.closed = True


class _StartFailureAdapter(_LaunchAdapter):
    def __init__(self, detail: str) -> None:
        super().__init__()
        self.detail = detail
        self.stop_modes: list[str] = []

    async def start(self, _launch: LaunchSpec) -> object:
        raise HarnessControlError(self.detail)

    async def stop(self, mode: str) -> None:
        self.stop_modes.append(mode)


async def _completed_surface_task(_surface: object) -> None:
    return None


class RunnerStartupFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_preparation_failure_is_exposed_as_exact_runner_control_state(self) -> None:
        config = RunnerConfig(
            identity=_identity(),
            harness_id="pi",
            cwd=Path("/workspace"),
            argv=("pi",),
            endpoint_root=Path("/runtime/control"),
            resolved_launch=ResolvedLaunch("pi", "provider/missing", "high", Path("/workspace")),
        )
        detail = (
            "pi launch model 'provider/missing' is absent from the dynamic catalog; "
            "advertised models: [provider/model]"
        )
        _CaptureServer.latest = None
        stdout = io.StringIO()
        with (
            mock.patch(
                "agents_remember.serving.harness_control_runner._prepare_controlled_launch",
                side_effect=HarnessControlError(detail),
            ),
            mock.patch(
                "agents_remember.serving.harness_control_runner.HarnessControlServer",
                _CaptureServer,
            ),
            mock.patch(
                "agents_remember.serving.harness_control_runner._render_updates",
                _completed_surface_task,
            ),
            mock.patch(
                "agents_remember.serving.harness_control_runner._read_terminal_input",
                _completed_surface_task,
            ),
            redirect_stdout(stdout),
        ):
            await run_controlled_session(config)

        server = _CaptureServer.latest
        assert server is not None
        snapshot = server.snapshot_on_close
        assert snapshot is not None
        self.assertTrue(server.started)
        self.assertTrue(server.closed)
        self.assertEqual(snapshot.control, "failed")
        self.assertEqual(snapshot.acceptance, "rejected")
        self.assertEqual(snapshot.raw["bridgeError"], detail)
        self.assertIn(f"[control] launch failed: {detail}", stdout.getvalue())

    async def test_adapter_start_mismatch_is_persistent_failed_rejected_evidence(self) -> None:
        config = RunnerConfig(
            identity=_identity(),
            harness_id="claude",
            cwd=Path("/workspace"),
            argv=("claude",),
            endpoint_root=Path("/runtime/control"),
            resolved_launch=ResolvedLaunch("claude", "sonnet", "high", Path("/workspace")),
        )
        detail = "claude launch selected model 'sonnet', but the running harness reported 'haiku'"
        adapter = _StartFailureAdapter(detail)
        launch = LaunchSpec(
            identity=config.identity,
            harness_id="claude",
            cwd=config.cwd,
            argv=("claude", "--model", "sonnet", "--effort", "high"),
        )
        _CaptureServer.latest = None
        stdout = io.StringIO()
        with (
            mock.patch(
                "agents_remember.serving.harness_control_runner._prepare_controlled_launch",
                return_value=(adapter, launch),
            ),
            mock.patch(
                "agents_remember.serving.harness_control_runner.HarnessControlServer",
                _CaptureServer,
            ),
            mock.patch(
                "agents_remember.serving.harness_control_runner._render_updates",
                _completed_surface_task,
            ),
            mock.patch(
                "agents_remember.serving.harness_control_runner._read_terminal_input",
                _completed_surface_task,
            ),
            redirect_stdout(stdout),
        ):
            await run_controlled_session(config)

        server = _CaptureServer.latest
        assert server is not None
        snapshot = server.snapshot_on_close
        assert snapshot is not None
        self.assertEqual(snapshot.control, "failed")
        self.assertEqual(snapshot.acceptance, "rejected")
        self.assertEqual(snapshot.raw["bridgeError"], detail)
        self.assertIn(f"[control] launch failed: {detail}", stdout.getvalue())
        self.assertEqual(adapter.stop_modes, ["forced"])


class _InputSurface:
    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject
        self.submissions: list[str] = []

    async def submit_terminal(self, text: str) -> SubmissionReceipt:
        self.submissions.append(text)
        if self.reject:
            raise HarnessControlError("adapter unavailable")
        return SubmissionReceipt(
            request_id=f"request-{len(self.submissions)}",
            acceptance="immediate",
            submitted_at=NOW,
            detail="accepted",
        )


class _RenderBridge:
    def __init__(self) -> None:
        self.identity = _identity()
        self.calls: list[int] = []

    async def subscribe(self) -> AsyncIterator[AdapterSnapshot]:
        ready = AdapterSnapshot(
            identity=self.identity,
            control="ready",
            activity="running",
            acceptance="immediate",
        )
        yield ready
        yield replace(ready, control="failed", activity="unknown", acceptance="unknown")

    def transcript(self, *, after_sequence: int = 0) -> tuple[TranscriptEntry, ...]:
        self.calls.append(after_sequence)
        if after_sequence:
            return ()
        return (
            TranscriptEntry(
                sequence=1,
                role="assistant",
                text="hello\x00 world",
                created_at=NOW,
                request_id="request-1",
                terminal_result=TerminalResult(outcome="completed", completed_at=NOW),
            ),
        )


class _RenderSurface:
    def __init__(self) -> None:
        self.bridge = _RenderBridge()


class RunnerLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_input_submits_whole_nonempty_lines_and_reports_rejection(self) -> None:
        accepted = _InputSurface()
        stdout = io.StringIO()
        with (
            mock.patch.object(
                __import__("sys").stdin,
                "readline",
                side_effect=["\x1b[200~hello\x1b[201~\n", "\n", ""],
            ),
            redirect_stdout(stdout),
        ):
            await _read_terminal_input(cast(HarnessTerminalSurface, accepted))
        self.assertEqual(accepted.submissions, ["hello"])
        self.assertIn("request-1 immediate: accepted", stdout.getvalue())

        rejected = _InputSurface(reject=True)
        stdout = io.StringIO()
        with (
            mock.patch.object(__import__("sys").stdin, "readline", side_effect=["blocked\n", ""]),
            redirect_stdout(stdout),
        ):
            await _read_terminal_input(cast(HarnessTerminalSurface, rejected))
        self.assertEqual(rejected.submissions, ["blocked"])
        self.assertIn("rejected: adapter unavailable", stdout.getvalue())

    async def test_render_updates_prints_state_and_structured_terminal_result(self) -> None:
        surface = _RenderSurface()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            await asyncio.wait_for(
                _render_updates(cast(HarnessTerminalSurface, surface)), timeout=1.0
            )
        output = stdout.getvalue()
        self.assertIn("[control] ready activity=running acceptance=immediate", output)
        self.assertIn("request=request-1 result=completed", output)
        self.assertIn("hello world", output)
        self.assertIn("[control] failed activity=unknown acceptance=unknown", output)
        self.assertEqual(surface.bridge.calls, [0, 1])


class _SessionCommandAuthority:
    def __init__(self, acceptances: tuple[str, ...]) -> None:
        self.acceptances = list(acceptances)
        self.requests: list[PromptRequest] = []

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self.requests.append(request)
        acceptance = self.acceptances[len(self.requests) - 1]
        return SubmissionReceipt(
            request_id=request.request_id,
            acceptance=cast(AcceptanceState, acceptance),
            submitted_at=request.submitted_at,
            detail=None if acceptance in {"immediate", "queued"} else "harness said no",
        )


class _SessionCommandBridge:
    def __init__(self, acceptances: tuple[str, ...]) -> None:
        self.identity = _identity()
        self.authority = _SessionCommandAuthority(acceptances)

    def submissions(self) -> _SessionCommandAuthority:
        return self.authority


class RunnerSessionCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_commands_carry_a_deterministic_durable_identity(self) -> None:
        """The request id is the authority's duplicate key, so it must be derivable, not fresh.

        `run_controlled_session` replays `sessionCommands` every time it brings a session
        up, and a hosted session is re-launched on reconnect. A per-run id would make each
        replay a NEW prompt: the same `/model` line submitted again, into a live thread.
        Deriving it from the session id and the command's position is what makes the second
        run a duplicate the authority recognises and answers from the ledger.

        They are `durable`, not `terminal`: nobody typed them, and the source is what the
        cockpit uses to tell an operator's message from the runner's own setup.
        """
        bridge = _SessionCommandBridge(("immediate", "unsupported"))
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            await _submit_session_commands(
                cast(HarnessControlBridge, bridge), ("/model fable", "/compact")
            )

        self.assertEqual(
            [request.request_id for request in bridge.authority.requests],
            ["session-1:session-command:1", "session-1:session-command:2"],
        )
        self.assertEqual(
            [request.text for request in bridge.authority.requests],
            ["/model fable", "/compact"],
        )
        self.assertEqual({request.source for request in bridge.authority.requests}, {"durable"})
        # Only the one the harness would not take is reported; an accepted command is silent.
        output = stdout.getvalue()
        self.assertNotIn("session command 1", output)
        self.assertIn("session command 2 unsupported: harness said no", output)


if __name__ == "__main__":
    unittest.main()
