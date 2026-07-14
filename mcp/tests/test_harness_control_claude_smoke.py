"""Opt-in credential-safe smoke for the pinned Claude Code stream-json adapter."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.serving.harness_control_claude import ClaudeStreamJsonAdapter
from agents_remember.serving.harness_control_models import (
    ControlIdentity,
    LaunchSpec,
    PromptRequest,
)


@unittest.skipUnless(
    os.environ.get("AR_CLAUDE_STREAM_SMOKE") == "1",
    "set AR_CLAUDE_STREAM_SMOKE=1 and optionally AR_CLAUDE_STREAM_BINARY to run",
)
class ClaudeStreamJsonLiveSmoke(unittest.IsolatedAsyncioTestCase):
    async def test_pinned_cli_completes_one_correlated_local_command_without_secret_output(
        self,
    ) -> None:
        binary = os.environ.get("AR_CLAUDE_STREAM_BINARY") or shutil.which("claude")
        self.assertIsNotNone(binary, "Claude Code is not installed")
        assert binary is not None
        identity = ControlIdentity(
            ar_session_id="claude-stream-smoke",
            tmux_name="claude-stream-smoke",
            created_at="2026-07-14T00:00:00+00:00",
        )
        adapter = ClaudeStreamJsonAdapter()
        handshake = await adapter.start(
            LaunchSpec(
                identity=identity,
                harness_id="claude",
                cwd=Path.cwd(),
                argv=(binary,),
                env=dict(os.environ),
            )
        )
        self.assertEqual(
            handshake.snapshot.control,
            "ready",
            "live smoke requires the pinned Claude Code 2.1.207 binary",
        )
        try:
            receipt = await asyncio.wait_for(
                adapter.submit(
                    PromptRequest(
                        request_id="claude-stream-smoke-request",
                        source="terminal",
                        text="/cost",
                        submitted_at="2026-07-14T00:00:00+00:00",
                    )
                ),
                timeout=120,
            )
            self.assertIn(receipt.acceptance, {"immediate", "queued"})

            async def terminal_outcome() -> str:
                async for event in adapter.subscribe():
                    if event.kind == "completed" and event.transcript:
                        result = event.transcript[-1].terminal_result
                        if result is not None:
                            return result.outcome
                raise AssertionError("Claude stream ended without a terminal result")

            self.assertEqual(await asyncio.wait_for(terminal_outcome(), timeout=120), "completed")
        finally:
            await adapter.stop("graceful")


if __name__ == "__main__":
    unittest.main()
