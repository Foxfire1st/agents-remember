"""Deterministic child-process coverage for Pi RPC JSONL transport behavior."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from agents_remember.errors import HarnessAdapterDisconnectedError, HarnessControlError
from agents_remember.serving.harness_control_models import ControlIdentity, LaunchSpec
from agents_remember.serving.pi_rpc_process import PiRpcSubprocess


def _child_launch(script: str) -> LaunchSpec:
    return LaunchSpec(
        identity=ControlIdentity(
            ar_session_id="pi-process-test",
            tmux_name="pi-process-test",
            created_at="2026-07-14T09:00:00+00:00",
        ),
        harness_id="pi-test-child",
        cwd=Path.cwd(),
        argv=(sys.executable, "-u", "-c", script),
    )


class PiRpcSubprocessTests(unittest.IsolatedAsyncioTestCase):
    async def test_correlates_response_streams_event_and_stops_cleanly(self) -> None:
        script = """
import json
import sys

sys.stderr.write("child-ready")
sys.stderr.flush()
for line in sys.stdin:
    command = json.loads(line)
    if command["type"] == "ping":
        print(json.dumps({"type": "queue_update", "steering": [], "followUp": []}))
        print(json.dumps({
            "type": "response",
            "id": command["id"],
            "command": "ping",
            "success": True,
        }))
        sys.stdout.flush()
"""
        transport = PiRpcSubprocess()
        await transport.start(_child_launch(script))
        events = transport.events()
        try:
            response = await transport.request({"id": "ping-1", "type": "ping"})
            self.assertEqual(response["id"], "ping-1")
            self.assertEqual(response["command"], "ping")
            event = await anext(events)
            self.assertEqual(event["type"], "queue_update")
            self.assertIn("child-ready", transport.stderr_tail)
        finally:
            await transport.stop("graceful")
        with self.assertRaises(HarnessAdapterDisconnectedError):
            await anext(events)

    async def test_malformed_stdout_fails_transport_without_reclassification(self) -> None:
        transport = PiRpcSubprocess()
        await transport.start(_child_launch('print("{\\\"type\\\":]", flush=True)'))
        try:
            with self.assertRaisesRegex(HarnessControlError, "malformed Pi RPC JSONL frame"):
                await anext(transport.events())
            with self.assertRaisesRegex(HarnessControlError, "malformed Pi RPC JSONL frame"):
                await transport.request({"id": "after-malformed", "type": "get_state"})
        finally:
            await transport.stop("forced")

    async def test_eof_during_correlated_request_is_ambiguous(self) -> None:
        script = """
import sys

sys.stdin.readline()
"""
        transport = PiRpcSubprocess()
        await transport.start(_child_launch(script))
        try:
            with self.assertRaises(HarnessAdapterDisconnectedError) as raised:
                await transport.request({"id": "lost-1", "type": "prompt"})
            self.assertTrue(raised.exception.may_have_sent)
            self.assertEqual(raised.exception.vendor_correlation_id, "lost-1")
        finally:
            await transport.stop("forced")


if __name__ == "__main__":
    unittest.main()
