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
from agents_remember.serving.codex_app_server_adapter import CodexAppServerAdapter
from agents_remember.serving.harness_control_adapter import UnsupportedHarnessProtocolAdapter
from agents_remember.serving.harness_control_claude import ClaudeStreamJsonAdapter
from agents_remember.serving.harness_control_factories import create_harness_protocol_adapter
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    ControlIdentity,
    SubmissionReceipt,
    TerminalResult,
    TranscriptEntry,
)
from agents_remember.serving.harness_control_runner import (
    RunnerConfig,
    _adapter_argv,
    _read_terminal_input,
    _render_updates,
    control_runner_command,
    parse_runner_config,
)
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
            argv=("claude", "--model", "opus"),
            endpoint_root=Path("/runtime/control"),
            session_commands=("/effort high",),
        )
        command = control_runner_command(config)
        self.assertEqual(command[1:3], ("-m", "agents_remember.serving.harness_control_runner"))
        self.assertEqual(parse_runner_config(command[3]), config)
        for malformed in ("not-base64", "W10=", "e30="):
            with self.subTest(malformed=malformed), self.assertRaises(HarnessControlError):
                parse_runner_config(malformed)

    def test_codex_runner_uses_app_server_and_removes_tui_only_knobs(self) -> None:
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
            _adapter_argv(config),
            ("codex", "app-server", "--sandbox", "workspace-write", "--model"),
        )
        self.assertEqual(
            _adapter_argv(replace(config, harness_id="claude", argv=("claude", "--verbose"))),
            ("claude", "--verbose"),
        )

    def test_factory_maps_all_builtins_and_keeps_custom_unsupported(self) -> None:
        self.assertIsInstance(
            create_harness_protocol_adapter("claude", env={}), ClaudeStreamJsonAdapter
        )
        self.assertIsInstance(
            create_harness_protocol_adapter(
                "codex", env={"AR_SPAWN_MODEL": "gpt-test", "AR_SPAWN_EFFORT": "high"}
            ),
            CodexAppServerAdapter,
        )
        self.assertIsInstance(create_harness_protocol_adapter("pi", env={}), PiRpcAdapter)
        self.assertIsInstance(
            create_harness_protocol_adapter("custom", env={}),
            UnsupportedHarnessProtocolAdapter,
        )


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


if __name__ == "__main__":
    unittest.main()
