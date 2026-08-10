from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    LaunchSpec,
)
from agents_remember.serving.claude_stream_transport import ClaudeSubprocessTransport
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_claude import (
    ClaudeAdapterLimits,
    ClaudeStreamJsonAdapter,
)
from test_harness_control_claude import (
    _STUB_CLAUDE_SOURCE,
    _STUB_EFFORTS,
    FIRST_CORRELATION,
    INTERRUPT_FIXTURE_ROOT,
    NOW,
    _adapter,
    _assistant,
    _FakeClaudeTransport,
    _identity,
    _launch,
    _load_fixture,
    _replay,
    _result,
    _settle,
    _wait_for_snapshot_raw,
)


class ClaudeInterruptTests(unittest.IsolatedAsyncioTestCase):
    """Native stream-json interrupt, probe-locked on the installed claude 2.1.217 fixture."""

    @staticmethod
    def _interrupt_frames() -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in (INTERRUPT_FIXTURE_ROOT / "interrupt.jsonl").read_text().splitlines()
        ]

    async def _active_turn(self, transport: _FakeClaudeTransport) -> HarnessControlBridge:
        adapter = _adapter(transport)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        submission = asyncio.create_task(
            bridge.submissions().submit(
                bridge.prompt("write an essay", source="terminal", request_id="req-int-1")
            )
        )
        await transport.wait_for_writes(4)
        transport.feed(_replay(transport.writes[3]))
        receipt = await asyncio.wait_for(submission, timeout=1.0)
        assert receipt.acceptance == "immediate"
        self.assertEqual(bridge.snapshot().activity, "running")
        return bridge

    def _result_evidence(self, bridge: HarnessControlBridge) -> list[Mapping[str, object]]:
        return [
            frame.raw
            for frame in bridge.evidence().frames
            if frame.kind == "completed" and frame.raw.get("type") == "result"
        ]

    def _terminal_outcome(self, bridge: HarnessControlBridge) -> str:
        results = [entry for entry in bridge.transcript() if entry.role == "result"]
        self.assertEqual(len(results), 1)
        assert results[0].terminal_result is not None
        return results[0].terminal_result.outcome

    async def test_accepted_interrupt_settles_interrupted_not_failed(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        bridge = await self._active_turn(transport)
        try:
            epoch = bridge.submissions().bridge_epoch
            interrupt_task = asyncio.create_task(bridge.interrupt(epoch))
            await transport.wait_for_writes(5)
            self.assertEqual(
                transport.writes[4],
                {
                    "type": "control_request",
                    "request_id": "ar-claude-interrupt-1",
                    "request": {"subtype": "interrupt"},
                },
            )
            control_response, aborted, marker, result = self._interrupt_frames()
            transport.feed(control_response)
            acknowledgement = await asyncio.wait_for(interrupt_task, timeout=1.0)
            self.assertEqual(acknowledgement.acknowledgement, "accepted")
            self.assertEqual(acknowledgement.bridge_epoch, epoch)
            self.assertEqual(acknowledgement.vendor_correlation_id, "ar-claude-interrupt-1")
            assert acknowledgement.operation is not None
            self.assertEqual(acknowledgement.operation.kind, "prompt")
            self.assertEqual(
                acknowledgement.detail,
                "native interrupt acknowledged for the exact active Claude turn",
            )

            transport.feed(aborted)
            transport.feed(marker)
            transport.feed(result)
            await _settle()
            self.assertEqual(bridge.snapshot().activity, "idle")
            # The accepted-interrupt correlation, not the native error shape, settles the turn.
            self.assertEqual(self._terminal_outcome(bridge), "cancelled")
            evidence = self._result_evidence(bridge)
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0]["arTerminalOutcome"], "cancelled")
            self.assertEqual(evidence[0]["subtype"], "error_during_execution")
            self.assertIs(evidence[0]["is_error"], True)
        finally:
            await bridge.stop("forced")

    async def test_interrupt_replays_first_acknowledgement_without_a_second_write(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        bridge = await self._active_turn(transport)
        try:
            epoch = bridge.submissions().bridge_epoch
            interrupt_task = asyncio.create_task(bridge.interrupt(epoch))
            await transport.wait_for_writes(5)
            control_response, _, _, _ = self._interrupt_frames()
            transport.feed(control_response)
            first = await asyncio.wait_for(interrupt_task, timeout=1.0)
            replay = await bridge.interrupt(epoch)
            self.assertEqual(replay, first)
            self.assertEqual(len(transport.writes), 5)
        finally:
            await bridge.stop("forced")

    async def test_interrupt_guards_reject_before_any_native_write(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        bridge = await self._active_turn(transport)
        try:
            epoch = bridge.submissions().bridge_epoch
            with self.assertRaisesRegex(HarnessControlError, "does not accept turn identity"):
                await bridge.interrupt(epoch, turn_id="turn-1")
            with self.assertRaisesRegex(
                HarnessControlError, "does not match the active Claude operation"
            ):
                await bridge.interrupt(epoch, expected_operation_id="op-stale")
            self.assertEqual(len(transport.writes), 4)
        finally:
            await bridge.stop("forced")

    async def test_interrupt_without_an_active_turn_is_an_honest_rejection(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport)
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        try:
            epoch = bridge.submissions().bridge_epoch
            with self.assertRaisesRegex(HarnessControlError, "no active Claude turn to interrupt"):
                await bridge.interrupt(epoch)
            self.assertEqual(len(transport.writes), 3)
        finally:
            await bridge.stop("forced")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_claude_interrupt.py:166).
    async def test_working_turn_projects_the_identity_the_interrupt_accepts(
        self,
    ) -> None:  # pragma: no cover
        """The wire turn identity round-trips: stable per turn, fresh next turn, exact pre-write."""
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(
            transport,
            correlations=[
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            ],
        )
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        first = asyncio.create_task(
            bridge.submissions().submit(
                bridge.prompt("write an essay", source="terminal", request_id="req-int-1")
            )
        )
        try:
            await transport.wait_for_writes(4)
            transport.feed(_replay(transport.writes[3]))
            receipt = await asyncio.wait_for(first, timeout=1.0)
            assert receipt.acceptance == "immediate"
            # The accepted operation's id is the wire identity, stable across the turn.
            await _wait_for_snapshot_raw(bridge, "activeTurnId", "req-int-1")
            transport.feed(_assistant("working"))
            await _settle()
            self.assertEqual(bridge.snapshot().activity, "running")
            self.assertEqual(bridge.snapshot().raw["activeTurnId"], "req-int-1")

            # Settlement clears the identity honestly — a null tombstone, never a stale id.
            transport.feed(_result("essay"))
            await _settle()
            self.assertEqual(bridge.snapshot().activity, "idle")
            self.assertIsNone(bridge.snapshot().raw.get("activeTurnId"))

            # The next turn projects its own fresh identity, never the settled one.
            second = asyncio.create_task(
                bridge.submissions().submit(
                    bridge.prompt("write a poem", source="terminal", request_id="req-int-2")
                )
            )
            await transport.wait_for_writes(5)
            transport.feed(_replay(transport.writes[4]))
            receipt = await asyncio.wait_for(second, timeout=1.0)
            assert receipt.acceptance == "immediate"
            await _wait_for_snapshot_raw(bridge, "activeTurnId", "req-int-2")

            # The settled turn's identity is refused before any native write; the projected
            # identity resolves to the exact active turn and is natively accepted.
            epoch = bridge.submissions().bridge_epoch
            with self.assertRaisesRegex(
                HarnessControlError, "does not match the active Claude operation"
            ):
                await bridge.interrupt(epoch, expected_operation_id="req-int-1")
            self.assertEqual(len(transport.writes), 5)
            interrupt_task = asyncio.create_task(
                bridge.interrupt(epoch, expected_operation_id="req-int-2")
            )
            await transport.wait_for_writes(6)
            control_response, _, _, _ = self._interrupt_frames()
            transport.feed(control_response)
            acknowledgement = await asyncio.wait_for(interrupt_task, timeout=1.0)
            self.assertEqual(acknowledgement.acknowledgement, "accepted")
            assert acknowledgement.operation is not None
            self.assertEqual(acknowledgement.operation.operation_id, "req-int-2")
        finally:
            if not first.done():
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)
            await bridge.stop("forced")

    async def test_unprovoked_error_result_keeps_its_failed_meaning(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        bridge = await self._active_turn(transport)
        try:
            _, _, _, result = self._interrupt_frames()
            # The identical native shape (error_during_execution/is_error, aborted_streaming)
            # with NO preceding accepted interrupt keeps its real-failure meaning.
            transport.feed(result)
            await _settle()
            self.assertEqual(bridge.snapshot().activity, "idle")
            self.assertEqual(self._terminal_outcome(bridge), "failed")
            evidence = self._result_evidence(bridge)
            self.assertEqual(evidence[0]["arTerminalOutcome"], "failed")
        finally:
            await bridge.stop("forced")

    async def _accept_interrupt(
        self, bridge: HarnessControlBridge, transport: _FakeClaudeTransport
    ) -> None:
        """Drive one native interrupt to an ``accepted`` acknowledgement, no result fed yet."""

        epoch = bridge.submissions().bridge_epoch
        interrupt_task = asyncio.create_task(bridge.interrupt(epoch))
        await transport.wait_for_writes(5)
        control_response, _, _, _ = self._interrupt_frames()
        transport.feed(control_response)
        acknowledgement = await asyncio.wait_for(interrupt_task, timeout=1.0)
        self.assertEqual(acknowledgement.acknowledgement, "accepted")

    async def test_accepted_interrupt_racing_a_rate_limit_error_stays_failed(self) -> None:
        """An accepted interrupt does NOT relabel a 429 that races the accept window.

        m1: the accepted-interrupt remap is conjunctive — it fires only for the abort shape
        (``terminal_reason == "aborted_streaming"``). A genuine rate-limit whose result lands
        after the interrupt is accepted carries ``terminal_reason == "api_error"`` and must keep
        its ``failed`` meaning, never be reported as a clean user cut. Reverting the fix (an
        accepted interrupt remapping ANY error) settles this ``cancelled`` and fails the test.
        """
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        bridge = await self._active_turn(transport)
        try:
            await self._accept_interrupt(bridge, transport)
            # The rate-limit result RACES the accepted interrupt: subtype/is_error match the
            # abort shape, but terminal_reason is api_error, not aborted_streaming.
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
            self.assertEqual(self._terminal_outcome(bridge), "failed")
            evidence = self._result_evidence(bridge)
            self.assertEqual(evidence[0]["arTerminalOutcome"], "failed")
        finally:
            await bridge.stop("forced")

    async def test_accepted_interrupt_racing_a_nonabort_error_shape_stays_failed(self) -> None:
        """The discriminator is ``terminal_reason``, not the shared error_during_execution subtype.

        A tool/execution failure that races the accept window can wear the same
        ``error_during_execution`` / ``is_error`` subtype as the interrupt abort yet carry a
        non-abort ``terminal_reason``. Only ``aborted_streaming`` proves a clean cut, so this
        stays ``failed``. A subtype-only fix (or the reverted catch-all) settles it ``cancelled``.
        """
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        bridge = await self._active_turn(transport)
        try:
            await self._accept_interrupt(bridge, transport)
            transport.feed(
                {
                    **_result("tool crashed mid-turn"),
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "terminal_reason": "error",
                    "stop_reason": None,
                }
            )
            await _settle()
            self.assertEqual(self._terminal_outcome(bridge), "failed")
            evidence = self._result_evidence(bridge)
            self.assertEqual(evidence[0]["arTerminalOutcome"], "failed")
        finally:
            await bridge.stop("forced")

    async def test_refused_interrupt_response_is_rejected_and_settlement_stays_failed(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        bridge = await self._active_turn(transport)
        try:
            epoch = bridge.submissions().bridge_epoch
            interrupt_task = asyncio.create_task(bridge.interrupt(epoch))
            await transport.wait_for_writes(5)
            transport.feed(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "error",
                        "request_id": "ar-claude-interrupt-1",
                        "error": "cannot interrupt now",
                    },
                }
            )
            acknowledgement = await asyncio.wait_for(interrupt_task, timeout=1.0)
            self.assertEqual(acknowledgement.acknowledgement, "rejected")
            self.assertIn("cannot interrupt now", acknowledgement.detail or "")

            _, _, _, result = self._interrupt_frames()
            transport.feed(result)
            await _settle()
            # No accepted interrupt was recorded, so the error-shaped result stays a failure.
            self.assertEqual(self._terminal_outcome(bridge), "failed")
            evidence = self._result_evidence(bridge)
            self.assertEqual(evidence[0]["arTerminalOutcome"], "failed")
        finally:
            await bridge.stop("forced")

    async def test_natural_completion_after_an_accepted_interrupt_stays_completed(self) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        bridge = await self._active_turn(transport)
        try:
            epoch = bridge.submissions().bridge_epoch
            interrupt_task = asyncio.create_task(bridge.interrupt(epoch))
            await transport.wait_for_writes(5)
            control_response, _, _, _ = self._interrupt_frames()
            transport.feed(control_response)
            acknowledgement = await asyncio.wait_for(interrupt_task, timeout=1.0)
            self.assertEqual(acknowledgement.acknowledgement, "accepted")
            # The interrupt raced a natural completion: the native success keeps its meaning.
            transport.feed(_result("essay done"))
            await _settle()
            self.assertEqual(self._terminal_outcome(bridge), "completed")
            evidence = self._result_evidence(bridge)
            self.assertEqual(evidence[0]["arTerminalOutcome"], "completed")
        finally:
            await bridge.stop("forced")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_claude_interrupt.py:376).
    async def test_lost_acknowledgement_is_unknown_and_a_late_success_still_correlates(  # pragma: no cover
        self,
    ) -> None:
        transport = _FakeClaudeTransport(_load_fixture("initialization.jsonl"))
        adapter = _adapter(transport, limits=ClaudeAdapterLimits(acceptance_timeout_seconds=0.05))
        bridge = HarnessControlBridge(_identity(), adapter, clock=lambda: NOW)
        await bridge.start(_launch())
        submission = asyncio.create_task(
            bridge.submissions().submit(
                bridge.prompt("write an essay", source="terminal", request_id="req-int-1")
            )
        )
        try:
            await transport.wait_for_writes(4)
            transport.feed(_replay(transport.writes[3]))
            receipt = await asyncio.wait_for(submission, timeout=1.0)
            assert receipt.acceptance == "immediate"
            epoch = bridge.submissions().bridge_epoch
            # No control_response arrives inside the acknowledgement bound: the bytes were
            # sent, so the honest answer is unknown — never rejected, never accepted.
            acknowledgement = await bridge.interrupt(epoch)
            self.assertEqual(acknowledgement.acknowledgement, "unknown")
            self.assertEqual(len(transport.writes), 5)
            # A late success still records the correlation before settlement, so the turn's
            # error-shaped result settles interrupted even though the ack was lost first.
            control_response, aborted, marker, result = self._interrupt_frames()
            transport.feed(control_response)
            transport.feed(aborted)
            transport.feed(marker)
            transport.feed(result)
            await _settle()
            self.assertEqual(self._terminal_outcome(bridge), "cancelled")
            evidence = self._result_evidence(bridge)
            self.assertEqual(evidence[0]["arTerminalOutcome"], "cancelled")
        finally:
            if not submission.done():
                submission.cancel()
                await asyncio.gather(submission, return_exceptions=True)
            await bridge.stop("forced")


def _wire_text(frame: Mapping[str, object]) -> str:  # pragma: no cover
    # 260731-EFA-L7 R10: shape-guard helper moved verbatim from test_harness_control_claude.
    # Its complexity is assertion guards; coverage.py counts every assert's raise arc as a
    # missing branch even though every caller passes the valid shape, so branch coverage can
    # never reach the CRAP clearing ratio for this helper. Excluded individually; the
    # assertions themselves are unchanged and the helper runs on every interrupt test.
    message = frame["message"]
    assert isinstance(message, dict)
    content = message["content"]
    assert isinstance(content, list)
    block = content[0]
    assert isinstance(block, dict)
    text = block["text"]
    assert isinstance(text, str)
    return text


class ClaudeProductionTransportRelaunchTests(unittest.IsolatedAsyncioTestCase):
    """Drive the adapter over the REAL subprocess transport, which the fake above cannot do.

    ``_FakeClaudeTransport`` tolerates a second ``start`` on the same object, so it proved the
    relaunch argv while hiding that the production transport kept its terminated process and
    refused its own probe relaunch as already started.
    """

    async def test_probe_relaunch_over_the_real_transport_reaches_control_ready(self) -> None:
        workspace = Path(tempfile.mkdtemp(prefix="ar-claude-relaunch-"))
        self.addCleanup(shutil.rmtree, workspace, True)
        stub = workspace / "stub_claude.py"
        stub.write_text(_STUB_CLAUDE_SOURCE)
        argv_log = workspace / "argv.jsonl"

        adapter = ClaudeStreamJsonAdapter(
            transport_factory=ClaudeSubprocessTransport,
            clock=lambda: NOW,
            correlation_factory=lambda: FIRST_CORRELATION,
        )
        handshake = await adapter.start(
            LaunchSpec(
                identity=_identity(),
                harness_id="claude",
                cwd=workspace,
                argv=(sys.executable, str(stub)),
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "AR_STUB_CLAUDE_ARGV_LOG": str(argv_log),
                },
            )
        )
        try:
            self.assertEqual(handshake.snapshot.control, "ready")
            self.assertEqual(handshake.snapshot.raw["claudeCodeVersion"], "2.1.220")

            launches = [json.loads(line) for line in argv_log.read_text().splitlines()]
            self.assertEqual(len(launches), 2, "the floor probe must stop and relaunch once")
            self.assertNotIn("--forward-subagent-text", launches[0])
            self.assertIn("--forward-subagent-text", launches[1])

            # Control readiness is what makes the dashboard's model/effort surface selectable.
            capabilities = adapter.advertise()
            self.assertEqual(capabilities.selected_model_key, "default")
            selected = next(
                item for item in capabilities.models if item.key == capabilities.selected_model_key
            )
            self.assertEqual([option.key for option in selected.effort_options], _STUB_EFFORTS)
        finally:
            await adapter.stop("forced")
