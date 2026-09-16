"""Native-adapter conformance for the eve session protocol.

Every case here drives the real :class:`EveSessionAdapter` and its real event mapper through the
same transport seam the production HTTP client implements. The eve *process* is replaced; the
protocol, the event translation, the absolute stream cursor, the replay guard and the operation
bookkeeping are not.

The six scenarios the requirement names each have a dedicated case, and each one asserts both
directions: the behavior that must happen and the failure it would otherwise hide.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import sys
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp" / "src"))

from agents_remember.errors import (
    HarnessAdapterBusyError,
    HarnessAdapterDisconnectedError,
    HarnessControlError,
)
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationKind,
    ControlOperationRef,
    InterruptResult,
    LaunchSpec,
    SubmissionReceipt,
)
from agents_remember.serving.eve_adapter import (
    DEFAULT_EVE_ADAPTER_LIMITS,
    EVE_ADAPTER_ID,
    EveAdapterLimits,
    EveSessionAdapter,
)
from agents_remember.serving.eve_runtime_launch import (
    EFFORT_ENV,
    MODEL_ENV,
    launch_spec_selection,
)
from agents_remember.serving.harness_capabilities import SetResult
from agents_remember.serving.harness_control_models import (
    AdapterEvent,
    AdapterHandshake,
    InteractionResponse,
    PromptRequest,
)
from eve_adapter_test_support import (
    FakeEveRuntime,
    FakeRuntimeFactory,
    FakeTurn,
    raw_payload,
)

REQUIRED_CAPABILITY_COUNT = 7


def _clock() -> str:
    return datetime.now(UTC).isoformat()


def _identity() -> ControlIdentity:
    return ControlIdentity(
        ar_session_id="ar-session-1",
        tmux_name="ar-eve-1",
        created_at="2026-09-16T00:00:00+00:00",
    )


def _launch(model: str = "fixture-deterministic-1", effort: str = "provider-default") -> LaunchSpec:
    return LaunchSpec(
        identity=_identity(),
        harness_id="eve",
        cwd=Path("/tmp/ar-eve-workspace"),
        argv=("eve",),
        env={
            MODEL_ENV: model,
            EFFORT_ENV: effort,
            "AR_WORKSPACE_ROOT": "/tmp/ar-eve-workspace",
            "AR_BINDING_REF": "ar-binding:leaf-test",
        },
    )


class _Pump:
    """One standing subscriber driving the adapter while a fixture emits into the record.

    A single long-lived subscription is what makes these cases deterministic: the adapter reads the
    durable record through it, so a test can emit, wait for the translated event, and assert
    without racing a reconnect.
    """

    def __init__(self, adapter: EveSessionAdapter) -> None:
        self._adapter = adapter
        self.events: list[AdapterEvent] = []
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        async def consume() -> None:
            async for event in self._adapter.subscribe():
                self.events.append(event)

        self._task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)

    async def drain(
        self,
        *,
        until: str | None = None,
        settled: bool = False,
        expect: int = 0,
        timeout: float = 3.0,
        limit: int = 200,
    ) -> list[AdapterEvent]:
        """Wait until one terminal condition holds, returning every event seen meanwhile.

        ``expect`` is how many further events the caller is waiting for. It is what makes a fixture
        deterministic: the wait is on the events the subscriber actually collected, not on the
        adapter's internal sequence, which no caller can observe before a translation happens.
        """

        start = len(self.events)
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            batch = self.events[start:]
            if len(batch) >= expect > 0:
                return list(batch)
            if until is not None and any(event.kind == until for event in batch):
                return list(batch)
            if settled and await self._settled():
                return list(batch)
            if len(batch) >= limit:
                return list(batch)
            await asyncio.sleep(0.01)
        return self.events[start:]

    def mark(self) -> int:
        """Record the current collection position so a later read can start from it."""

        return len(self.events)

    def since(self, mark: int) -> list[AdapterEvent]:
        return self.events[mark:]

    async def _settled(self) -> bool:
        snapshot = await self._adapter.snapshot()
        return (
            snapshot.raw.get("observedTurnId") is None
            and self._adapter._active_operation is None
            and snapshot.activity != "running"
        )

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@dataclass
class _Harness:
    """One started adapter, its deterministic runtime, and its standing subscriber."""

    adapter: EveSessionAdapter
    runtime: FakeEveRuntime
    factory: FakeRuntimeFactory
    handshake: AdapterHandshake
    pump: _Pump

    async def aclose(self) -> None:
        await self.pump.stop()
        await self.adapter.stop("forced")

    async def snapshot(self) -> AdapterSnapshot:
        return await self.adapter.snapshot()

    async def prepare(
        self, request_id: str, *, sequence: int = 1, kind: ControlOperationKind = "prompt"
    ) -> ControlOperationRef:
        ref = ControlOperationRef(
            bridge_epoch="epoch-1",
            sequence=sequence,
            operation_id=request_id,
            kind=kind,
        )
        await self.adapter.preflight_operation(ref)
        return ref

    async def submit(self, request_id: str, text: str, *, sequence: int = 1) -> SubmissionReceipt:
        ref = await self.prepare(request_id, sequence=sequence)
        return await self.adapter.submit(
            PromptRequest(
                request_id=request_id,
                source="cockpit",
                text=text,
                submitted_at=_clock(),
                operation=ref,
            )
        )

    async def mark(self) -> int:
        """Record a read position; combine with :meth:`since` to read a whole scripted turn."""

        return self.pump.mark()

    def since(self, mark: int) -> list[AdapterEvent]:
        return self.pump.since(mark)

    async def read_until(self, kind: str, *, timeout: float = 3.0) -> list[AdapterEvent]:
        """Read until an event of that kind arrives, starting from the current position."""

        return await self.pump.drain(until=kind, timeout=timeout)

    async def emit_observed(
        self, session_id: str, event_type: str, data: dict[str, object]
    ) -> list[AdapterEvent]:
        """Emit one event and wait until its translated form has been consumed."""

        self.runtime.emit(session_id, event_type, data)
        events = await self.pump.drain(expect=1, timeout=3.0, limit=8)
        if not events:
            raise AssertionError(f"adapter did not consume {event_type}")
        return events

    async def emit_turn(self, session_id: str, turn: FakeTurn) -> list[AdapterEvent]:
        """Record one complete native turn and wait until the adapter consumed its boundary."""

        self.runtime.turn_events(session_id, turn)
        return await self.pump.drain(until=turn.boundary, timeout=3.0, limit=64)

    async def emit_until_turn_observed(self, session_id: str, turn_id: str) -> None:
        """Emit one turn start and wait until the adapter has bound that turn identity."""

        for _ in range(20):
            await self.emit_observed(session_id, "turn.started", {"sequence": 0, "turnId": turn_id})
            if (await self.snapshot()).raw.get("observedTurnId") == turn_id:
                return
        raise AssertionError(f"the adapter never observed turn {turn_id!r}")


async def _started(
    *,
    limits: EveAdapterLimits = DEFAULT_EVE_ADAPTER_LIMITS,
    runtime: FakeEveRuntime | None = None,
    launch: LaunchSpec | None = None,
) -> _Harness:
    resolved_runtime = runtime or FakeEveRuntime()
    factory = FakeRuntimeFactory(resolved_runtime)
    adapter = EveSessionAdapter(runtime_factory=factory, limits=limits, clock=_clock)
    handshake = await adapter.start(launch or _launch())
    pump = _Pump(adapter)
    await pump.start()
    return _Harness(
        adapter=adapter,
        runtime=resolved_runtime,
        factory=factory,
        handshake=handshake,
        pump=pump,
    )


class EveAdapterHandshakeTests(unittest.IsolatedAsyncioTestCase):
    """Start/handshake reports protocol-derived readiness, never a successful spawn."""

    async def test_start_reports_protocol_readiness_and_settings_identity(self) -> None:
        harness = await _started()
        try:
            self.assertEqual(harness.handshake.adapter_id, EVE_ADAPTER_ID)
            self.assertEqual(len(harness.handshake.capabilities), REQUIRED_CAPABILITY_COUNT)
            self.assertEqual(harness.handshake.snapshot.control, "ready")
            self.assertEqual(harness.handshake.snapshot.activity, "idle")
            self.assertIsNone(harness.handshake.snapshot.vendor_session_id)
            self.assertEqual(harness.runtime.health_calls, 1)
            self.assertEqual(
                raw_payload(harness.handshake.snapshot.raw, "eveHealth")["workflowId"],
                "workflow//eve//workflowEntry",
            )
            self.assertEqual(
                harness.handshake.snapshot.raw["runtimeEndpoint"], harness.runtime.endpoint
            )
            self.assertEqual((await harness.snapshot()).raw["streamCursor"], 0)
        finally:
            await harness.aclose()

    async def test_start_refuses_when_the_runtime_never_becomes_healthy(self) -> None:
        runtime = FakeEveRuntime()
        runtime.health_error = HarnessControlError("eve health route did not report ready")
        adapter = EveSessionAdapter(
            runtime_factory=FakeRuntimeFactory(runtime),
            limits=EveAdapterLimits(health_timeout_seconds=0.5),
            clock=_clock,
        )
        with self.assertRaises(HarnessControlError):
            await adapter.start(_launch())
        self.assertEqual(runtime.stop_modes, ["forced"])

    async def test_start_refuses_a_runtime_whose_selection_differs_from_the_launch(self) -> None:
        runtime = FakeEveRuntime()
        selection = launch_spec_selection(_launch(model="fixture-deterministic-1"))
        adapter = EveSessionAdapter(
            runtime_factory=FakeRuntimeFactory(runtime),
            clock=_clock,
            expected_launch=selection,
        )
        # The launch handed to start names a different model than the pinned expectation.
        with self.assertRaises(HarnessControlError):
            await adapter.start(_launch(model="some-other-model"))

    async def test_discover_is_token_free_and_does_not_keep_the_runtime(self) -> None:
        runtime = FakeEveRuntime()
        factory = FakeRuntimeFactory(runtime)
        adapter = EveSessionAdapter(runtime_factory=factory, clock=_clock)
        catalog = await adapter.discover(_launch())
        self.assertEqual(catalog.selected_model_key, "fixture-deterministic-1")
        self.assertEqual(catalog.selected_effort, "provider-default")
        self.assertEqual(runtime.stop_modes, ["forced"])
        self.assertEqual(runtime.created, [])
        with self.assertRaises(HarnessControlError):
            adapter.advertise()


class EveAdapterCapabilityTests(unittest.IsolatedAsyncioTestCase):
    """Model and effort controls report what eve can actually do, without a silent change."""

    async def test_advertise_reports_the_launch_selection_and_no_unbacked_effort_menu(self) -> None:
        # The catalog publishes the model the runtime compiles and REPORTS the launch's effort as
        # configuration, but offers no effort option: the pinned application reads no effort value,
        # so a menu would advertise a control whose every value produces the same run. The launch
        # vocabulary still validates a settings-named value at the launch boundary.
        harness = await _started(launch=_launch(model="fixture-model-a", effort="high"))
        try:
            catalog = harness.adapter.advertise()
            self.assertEqual(catalog.selected_model_key, "fixture-model-a")
            self.assertEqual(catalog.selected_effort, "high")
            model = catalog.models[0]
            self.assertEqual(model.key, "fixture-model-a")
            self.assertEqual(model.effort_options, ())
            self.assertFalse(model.supports_effort)
            self.assertIsNone(model.default_effort)
            self.assertEqual([option.config_id for option in catalog.config_options], ["model"])
            self.assertEqual(catalog.config_options[0].current_value, "fixture-model-a")
        finally:
            await harness.aclose()

    async def test_set_model_and_effort_refuse_without_claiming_a_change(self) -> None:
        harness = await _started()
        try:
            model_result = await harness.adapter.set_model("another-model")
            effort_result = await harness.adapter.set_effort("xhigh")
            for result in (model_result, effort_result):
                self.assertIsInstance(result, SetResult)
                self.assertFalse(result.ok)
                self.assertEqual(result.acceptance, "unsupported")
                # No change became effective, so the running model is what both report.
                self.assertEqual(result.effective_value, "fixture-deterministic-1")
            self.assertEqual(effort_result.requested_value, "xhigh")
            rejected = await harness.adapter.set_effort("not-an-effort")
            self.assertFalse(rejected.ok)
            self.assertIn("reasoning effort must be one of", rejected.detail or "")
        finally:
            await harness.aclose()


class EveAdapterSubmissionTests(unittest.IsolatedAsyncioTestCase):
    """Scenario 1: accepted submit -> deltas -> tool request -> response -> turn boundary."""

    async def test_full_protocol_fixture_distinguishes_acceptance_from_completion(self) -> None:
        harness = await _started()
        try:
            receipt = await harness.submit("req-1", "run the fixture")
            session_id = harness.runtime.created[0]
            self.assertEqual(receipt.acceptance, "immediate")
            self.assertEqual(receipt.raw["turnPolicy"], "queue")
            self.assertEqual(receipt.raw["sessionId"], session_id)
            self.assertEqual(harness.runtime.sessions[session_id].messages, ["run the fixture"])

            mark = await harness.mark()
            await harness.emit_turn(
                session_id,
                FakeTurn(
                    number=0,
                    deltas=["fixture ", "answer"],
                    message="fixture answer",
                    reasoning=["think"],
                    actions=[
                        {
                            "callId": "call_1",
                            "input": {"path": "note.txt"},
                            "kind": "tool-call",
                            "toolName": "ar_workspace_write",
                        }
                    ],
                    results=[
                        {
                            "callId": "call_1",
                            "kind": "tool-result",
                            "output": {"sha256": "abc"},
                            "toolName": "ar_workspace_write",
                        }
                    ],
                ),
            )
            events = harness.since(mark)

            kinds = [event.kind for event in events]
            self.assertIn("delta", kinds)
            self.assertIn("transcript", kinds)
            self.assertEqual(kinds[-1], "completed")

            transcript = [entry for event in events for entry in event.transcript]
            assistant_blocks = [entry.text for entry in transcript if entry.role == "assistant"]

            # This case must be able to tell a second materialization from a first, so its premise
            # is asserted rather than assumed: the deltas it streams reconstruct the finalized
            # block exactly. Without that property a mapper could render the block twice and leave
            # the transcript looking unchanged.
            self.assertEqual(
                "".join(["fixture ", "answer"]),
                "fixture answer",
                "the scripted deltas must reconstruct the finalized block, or this case cannot "
                "distinguish a re-rendered block from the first rendering",
            )
            # Named first, so a double render fails with the duplicate spelled out: any mapper that
            # materializes the finalized block a second time (from the accumulated deltas, from a
            # duplicate completion, or by re-emitting it at the turn boundary) puts the same text
            # in the transcript twice.
            duplicated = sorted(
                {text for text in assistant_blocks if assistant_blocks.count(text) > 1}
            )
            self.assertEqual(
                duplicated,
                [],
                f"a block was materialized more than once in the assistant transcript: "
                f"{duplicated} (full sequence {assistant_blocks})",
            )
            # Then the whole ordered sequence: a dropped block, a reordered block or an extra
            # distinct block all change it, and the failure prints what actually arrived.
            self.assertEqual(
                assistant_blocks,
                ["think", "fixture ", "answer", "fixture answer"],
                f"assistant blocks must be materialized exactly once each, in stream order; "
                f"got {assistant_blocks}",
            )
            self.assertEqual(
                [entry.text for entry in transcript if entry.role == "interaction"],
                ["ar_workspace_write"],
            )
            results = [entry for entry in transcript if entry.role == "result"]
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].vendor_correlation_id, "call_1")
            self.assertIn("abc", results[0].text)

            terminal = raw_payload(events[-1].raw, "terminalResult")
            self.assertEqual(terminal["outcome"], "completed")
            snapshot = await harness.snapshot()
            self.assertEqual(snapshot.control, "ready")
            self.assertEqual(snapshot.activity, "idle")
            self.assertEqual(
                snapshot.raw["streamCursor"],
                harness.runtime.last_event_index(session_id) + 1,
            )
        finally:
            await harness.aclose()

    async def test_input_request_becomes_a_pending_interaction_and_response_targets_it(
        self,
    ) -> None:
        harness = await _started()
        try:
            await harness.submit("req-1", "ask me")
            session_id = harness.runtime.created[0]
            await harness.emit_turn(session_id, FakeTurn(number=0))
            await harness.emit_observed(
                session_id,
                "input.requested",
                {
                    "requests": [
                        {
                            "action": {
                                "callId": "call_q",
                                "input": {},
                                "kind": "tool-call",
                                "toolName": "ask_question",
                            },
                            "kind": "question",
                            "options": [
                                {"id": "approve", "label": "Approve"},
                                {"id": "deny", "label": "Deny"},
                            ],
                            "prompt": "Proceed?",
                            "requestId": "req_input_1",
                        }
                    ],
                    "sequence": 0,
                    "stepIndex": 0,
                    "turnId": "turn_0",
                },
            )

            snapshot = await harness.snapshot()
            pending = snapshot.pending_interaction
            self.assertIsNotNone(pending)
            assert pending is not None
            self.assertEqual(pending.interaction_id, "req_input_1")
            self.assertEqual(pending.kind, "question")
            self.assertEqual(pending.prompt, "Proceed?")
            self.assertEqual(pending.choices, ("approve", "deny"))
            self.assertEqual(snapshot.activity, "blocked")
            self.assertEqual(snapshot.acceptance, "rejected")

            await harness.adapter.respond(
                InteractionResponse(
                    interaction_id="req_input_1",
                    response="approve",
                    responded_at=_clock(),
                )
            )
            self.assertEqual(
                harness.runtime.sessions[session_id].input_responses,
                [{"requestId": "req_input_1", "optionId": "approve"}],
            )
            harness.runtime.complete_requested_input(session_id, ["req_input_1"])
            await harness.read_until("state")
            self.assertIsNone((await harness.snapshot()).pending_interaction)
        finally:
            await harness.aclose()

    async def test_free_text_response_uses_the_text_field_and_unknown_ids_are_refused(self) -> None:
        harness = await _started()
        try:
            await harness.submit("req-1", "ask me")
            session_id = harness.runtime.created[0]
            await harness.emit_turn(session_id, FakeTurn(number=0))
            await harness.emit_observed(
                session_id,
                "input.requested",
                {
                    "requests": [
                        {
                            "allowFreeform": True,
                            "kind": "question",
                            "prompt": "Anything to add?",
                            "requestId": "req_input_2",
                        }
                    ],
                    "sequence": 0,
                    "stepIndex": 0,
                    "turnId": "turn_0",
                },
            )
            await harness.adapter.respond(
                InteractionResponse(
                    interaction_id="req_input_2",
                    response="a free-form answer",
                    responded_at=_clock(),
                )
            )
            self.assertEqual(
                harness.runtime.sessions[session_id].input_responses,
                [{"requestId": "req_input_2", "text": "a free-form answer"}],
            )
            with self.assertRaises(HarnessControlError):
                await harness.adapter.respond(
                    InteractionResponse(
                        interaction_id="req_never_seen",
                        response="approve",
                        responded_at=_clock(),
                    )
                )
        finally:
            await harness.aclose()

    async def test_preflight_refuses_while_an_input_request_is_pending(self) -> None:
        harness = await _started()
        try:
            await harness.submit("req-1", "ask me")
            session_id = harness.runtime.created[0]
            await harness.emit_turn(session_id, FakeTurn(number=0))
            await harness.emit_observed(
                session_id,
                "input.requested",
                {
                    "requests": [
                        {"kind": "tool-approval", "prompt": "Approve?", "requestId": "req_a"}
                    ],
                    "sequence": 0,
                    "stepIndex": 0,
                    "turnId": "turn_0",
                },
            )
            with self.assertRaises(HarnessAdapterBusyError):
                await harness.prepare("req-2", sequence=2)
        finally:
            await harness.aclose()


class EveAdapterQueuePolicyTests(unittest.IsolatedAsyncioTestCase):
    """Ordinary deliveries queue; a second delivery never cancels the active turn."""

    async def test_a_follow_up_is_queued_and_does_not_cancel_the_active_turn(self) -> None:
        harness = await _started()
        try:
            await harness.submit("req-1", "long task")
            session_id = harness.runtime.created[0]
            await harness.emit_until_turn_observed(session_id, "turn_0")
            self.assertEqual((await harness.snapshot()).activity, "running")

            # The first turn is still open, so the adapter holds its operation rather than
            # cancelling the active turn to make room for the second delivery.
            with self.assertRaises(HarnessAdapterBusyError):
                await harness.prepare("req-2", sequence=2)

            await harness.emit_observed(
                session_id, "turn.completed", {"sequence": 0, "turnId": "turn_0"}
            )
            await harness.emit_observed(
                session_id, "session.waiting", {"wait": "next-user-message"}
            )
            second = await harness.submit("req-2", "second task", sequence=2)
            self.assertEqual(second.acceptance, "immediate")

            session = harness.runtime.sessions[session_id]
            self.assertEqual(session.messages, ["long task", "second task"])
            self.assertEqual(session.cancelled_turns, [])
            self.assertEqual(second.raw["sessionId"], session_id)
        finally:
            await harness.aclose()

    async def test_an_operation_without_matching_preflight_is_refused_before_any_write(
        self,
    ) -> None:
        harness = await _started()
        try:
            with self.assertRaises(HarnessAdapterBusyError):
                await harness.adapter.submit(
                    PromptRequest(
                        request_id="req-1",
                        source="cockpit",
                        text="no preflight",
                        submitted_at=_clock(),
                        operation=ControlOperationRef(
                            bridge_epoch="epoch-1",
                            sequence=1,
                            operation_id="req-1",
                            kind="prompt",
                        ),
                    )
                )
            self.assertEqual(harness.runtime.sessions, {})
        finally:
            await harness.aclose()


class EveAdapterReconnectTests(unittest.IsolatedAsyncioTestCase):
    """Scenario 2: drop the stream, reconnect from the cursor, duplicate nothing."""

    async def test_reconnect_from_the_cursor_does_not_duplicate_the_transcript(self) -> None:
        harness = await _started()
        try:
            await harness.submit("req-1", "first")
            session_id = harness.runtime.created[0]
            await harness.emit_turn(
                session_id, FakeTurn(number=0, deltas=["one"], message="first answer")
            )
            await harness.read_until("completed")
            cursor_after_first = (await harness.snapshot()).raw["streamCursor"]
            self.assertEqual(cursor_after_first, harness.runtime.last_event_index(session_id) + 1)

            # Nothing new is durable yet, so a reconnect from the cursor yields nothing at all.
            self.assertEqual(await harness.read_until("__none__", timeout=0.4), [])

            second_mark = await harness.mark()
            await harness.emit_turn(
                session_id, FakeTurn(number=1, deltas=["two"], message="second answer")
            )
            second_pass = harness.since(second_mark)
            transcript = [entry.text for event in second_pass for entry in event.transcript]
            self.assertIn("second answer", transcript)
            self.assertNotIn("first answer", transcript)
        finally:
            await harness.aclose()

    async def test_replay_of_a_rewound_record_is_deduplicated_by_event_id(self) -> None:
        harness = await _started()
        try:
            await harness.submit("req-1", "first")
            session_id = harness.runtime.created[0]
            await harness.emit_turn(session_id, FakeTurn(number=0, message="answer"))

            # Force the adapter's own cursor back, as a restart against a shorter record would.
            harness.adapter._cursor = 0
            replayed = await harness.read_until("__none__", timeout=0.6)
            self.assertEqual(
                [entry.text for event in replayed for entry in event.transcript],
                [],
                "the same durable event delivered twice must not be translated twice",
            )
        finally:
            await harness.aclose()

    async def test_a_refused_stream_connection_is_reported_and_then_recovered(self) -> None:
        harness = await _started()
        try:
            await harness.submit("req-1", "first")
            session_id = harness.runtime.created[0]
            harness.runtime.stream_refusals = 1
            mark = await harness.mark()
            await harness.emit_turn(session_id, FakeTurn(number=0, message="answer"))
            events = harness.since(mark)
            self.assertIn("disconnected", [event.kind for event in events])
            self.assertEqual(events[-1].kind, "completed")
            self.assertEqual((await harness.snapshot()).control, "ready")
        finally:
            await harness.aclose()


class EveAdapterReconcileTests(unittest.IsolatedAsyncioTestCase):
    """Scenario 3: a lost submit response reconciles without a second write."""

    async def _settled_first_turn(self, harness: _Harness) -> str:
        await harness.submit("req-first", "first task")
        session_id = harness.runtime.created[0]
        await harness.emit_turn(session_id, FakeTurn(number=0, message="first answer"))
        await harness.pump.drain(settled=True)
        return session_id

    async def test_lost_submit_response_reconciles_accepted_without_resending(self) -> None:
        harness = await _started()
        try:
            session_id = await self._settled_first_turn(harness)
            await harness.prepare("req-lost", sequence=2)
            harness.runtime.send_error = HarnessAdapterDisconnectedError(
                "eve route did not answer", may_have_sent=True
            )
            with self.assertRaises(HarnessAdapterDisconnectedError):
                await harness.submit("req-lost", "possibly delivered", sequence=2)
            self.assertEqual(
                harness.runtime.sessions[session_id].messages,
                ["first task", "possibly delivered"],
            )

            harness.runtime.send_error = None
            await harness.emit_turn(session_id, FakeTurn(number=1, message="second answer"))
            result = await harness.adapter.reconcile("req-lost")
            self.assertEqual(result.state, "accepted")
            # The detail names the proof that actually held. A lost response supplies no delivery
            # id, so claiming one would report evidence the adapter never had.
            self.assertIn("holds this exact message verbatim", result.detail or "")
            self.assertNotIn("delivery id", result.detail or "")
            self.assertIn("no resend performed", result.detail or "")
            self.assertEqual(
                harness.runtime.sessions[session_id].messages,
                ["first task", "possibly delivered"],
                "reconciliation must never repeat a possibly accepted write",
            )
        finally:
            await harness.aclose()

    async def test_lost_submit_response_without_durable_evidence_is_unresolved(self) -> None:
        harness = await _started()
        try:
            session_id = await self._settled_first_turn(harness)
            await harness.prepare("req-lost", sequence=2)
            harness.runtime.send_error = HarnessAdapterDisconnectedError(
                "eve route did not answer", may_have_sent=True
            )
            with self.assertRaises(HarnessAdapterDisconnectedError):
                await harness.submit("req-lost", "possibly delivered", sequence=2)
            harness.runtime.send_error = None
            # Neither the response nor the durable record carries any marker of this request: the
            # write is unprovable, so the honest answer is unresolved rather than accepted.
            record = harness.runtime.sessions[session_id]
            record.message_ids.clear()
            record.events = [
                event for event in record.events if event["type"] != "message.received"
            ]
            result = await harness.adapter.reconcile("req-lost")
            self.assertEqual(result.state, "unresolved")
            self.assertEqual(len(harness.runtime.created), 1)
        finally:
            await harness.aclose()

    async def test_a_refused_write_reconciles_rejected(self) -> None:
        harness = await _started()
        try:
            await harness.prepare("req-refused")
            harness.runtime.create_error = HarnessControlError("eve refused the message")
            with self.assertRaises(HarnessControlError):
                await harness.submit("req-refused", "never accepted")
            harness.runtime.create_error = None
            result = await harness.adapter.reconcile("req-refused")
            self.assertEqual(result.state, "rejected")
        finally:
            await harness.aclose()

    async def test_reconcile_refuses_an_unknown_request_id(self) -> None:
        harness = await _started()
        try:
            with self.assertRaises(HarnessControlError):
                await harness.adapter.reconcile("req-never-submitted")
        finally:
            await harness.aclose()


class EveAdapterInterruptTests(unittest.IsolatedAsyncioTestCase):
    """Scenario 4: cancel the exact observed turn, then keep using the live session."""

    async def test_cancel_targets_the_observed_turn_and_the_session_accepts_the_next_message(
        self,
    ) -> None:
        harness = await _started()
        try:
            await harness.submit("req-1", "long task")
            session_id = harness.runtime.created[0]
            await harness.emit_until_turn_observed(session_id, "turn_7")

            result = await harness.adapter.interrupt(
                turn_id="turn_7", expected_operation_id="req-1"
            )
            self.assertIsInstance(result, InterruptResult)
            self.assertEqual(result.acknowledgement, "accepted")
            self.assertEqual(result.vendor_correlation_id, "turn_7")
            self.assertIn("turn boundary", result.detail or "")
            # The request the adapter made, not the fake's response to it: the exact durable
            # session and the exact observed turn id eve's cancel route addresses.
            self.assertEqual(
                harness.runtime.sessions[session_id].cancel_requests,
                [(session_id, "turn_7")],
            )

            mark = await harness.mark()
            await harness.emit_observed(
                session_id, "turn.cancelled", {"sequence": 0, "turnId": "turn_7"}
            )
            await harness.emit_observed(
                session_id, "session.waiting", {"wait": "next-user-message"}
            )
            events = harness.since(mark)
            outcomes = [
                raw_payload(event.raw, "terminalResult")["outcome"]
                for event in events
                if "terminalResult" in event.raw
            ]
            self.assertEqual(outcomes, ["cancelled", "completed"])
            self.assertEqual((await harness.snapshot()).activity, "idle")

            # The live session accepts another message on the same durable id.
            receipt = await harness.submit("req-2", "next task", sequence=2)
            self.assertEqual(receipt.raw["sessionId"], session_id)
            self.assertEqual(
                harness.runtime.sessions[session_id].messages, ["long task", "next task"]
            )
        finally:
            await harness.aclose()

    async def test_interrupt_refuses_a_mismatched_turn_or_operation(self) -> None:
        harness = await _started()
        try:
            await harness.submit("req-1", "long task")
            session_id = harness.runtime.created[0]
            await harness.emit_until_turn_observed(session_id, "turn_7")
            with self.assertRaises(HarnessControlError):
                await harness.adapter.interrupt(turn_id="turn_9", expected_operation_id="req-1")
            with self.assertRaises(HarnessControlError):
                await harness.adapter.interrupt(turn_id="turn_7", expected_operation_id="req-other")
            self.assertEqual(harness.runtime.sessions[session_id].cancelled_turns, [])
        finally:
            await harness.aclose()

    async def test_a_repeated_interrupt_replays_without_a_second_native_write(self) -> None:
        harness = await _started()
        try:
            await harness.submit("req-1", "long task")
            session_id = harness.runtime.created[0]
            await harness.emit_until_turn_observed(session_id, "turn_7")
            first = await harness.adapter.interrupt(turn_id="turn_7", expected_operation_id="req-1")
            second = await harness.adapter.interrupt(
                turn_id="turn_7", expected_operation_id="req-1"
            )
            self.assertEqual(first, second)
            # One request only: the replay is answered from the recorded acknowledgement.
            self.assertEqual(
                harness.runtime.sessions[session_id].cancel_requests,
                [(session_id, "turn_7")],
            )
        finally:
            await harness.aclose()

    async def test_no_active_turn_is_reported_honestly(self) -> None:
        harness = await _started()
        try:
            await harness.submit("req-1", "long task")
            harness.runtime.cancel_status = "no_active_turn"
            result = await harness.adapter.interrupt(turn_id=None, expected_operation_id="req-1")
            self.assertEqual(result.acknowledgement, "unknown")
            self.assertIn("no active turn", result.detail or "")
        finally:
            await harness.aclose()


class EveAdapterRestartTests(unittest.IsolatedAsyncioTestCase):
    """Scenario 5: a bridge restart attaches to the durable session; unknown ids fail."""

    async def test_restarted_bridge_attaches_to_the_same_durable_session(self) -> None:
        runtime = FakeEveRuntime()
        first = await _started(runtime=runtime)
        await first.submit("req-1", "first task")
        session_id = runtime.created[0]
        await first.emit_turn(session_id, FakeTurn(number=0, message="first answer"))
        await first.pump.stop()
        await first.adapter.stop("graceful")

        # A new bridge epoch over the same durable record: the same session id, no new session.
        resumed = await _started(runtime=runtime)
        try:
            resumed.adapter.attach_durable_session(session_id)
            self.assertEqual((await resumed.snapshot()).vendor_session_id, session_id)
            self.assertEqual(await resumed.read_until("__none__", timeout=0.4), [])
            self.assertEqual(runtime.created, [session_id])
            self.assertEqual(len(runtime.sessions), 1)
        finally:
            await resumed.aclose()

    async def test_an_unknown_or_retired_session_id_never_creates_a_replacement(self) -> None:
        harness = await _started()
        try:
            await harness.submit("req-1", "first task")
            session_id = harness.runtime.created[0]
            del harness.runtime.sessions[session_id]
            with self.assertRaises(HarnessControlError):
                await harness.runtime.send_message(session_id, "again")
            self.assertEqual(harness.runtime.created, [session_id])
        finally:
            await harness.aclose()

    async def test_stopping_the_adapter_stops_only_its_own_runtime(self) -> None:
        runtime = FakeEveRuntime()
        harness = await _started(runtime=runtime)
        await harness.submit("req-1", "first task")
        session_id = runtime.created[0]
        await harness.emit_turn(session_id, FakeTurn(number=0, message="answer"))
        await harness.pump.stop()
        await harness.adapter.stop("graceful")
        await harness.adapter.stop("forced")
        self.assertEqual(runtime.stop_modes, ["graceful"])
        # The durable session survives the adapter's shutdown, as eve's own contract states.
        self.assertIn(session_id, runtime.sessions)
        with self.assertRaises(HarnessControlError):
            await harness.snapshot()


class EveAdapterSessionCompletionTests(unittest.IsolatedAsyncioTestCase):
    """Session terminal states are distinct from turn boundaries."""

    async def test_session_completed_is_terminal_and_not_a_turn_boundary(self) -> None:
        harness = await _started()
        try:
            await harness.submit("req-1", "first task")
            session_id = harness.runtime.created[0]
            mark = await harness.mark()
            await harness.emit_turn(
                session_id, FakeTurn(number=0, message="done", boundary="session.completed")
            )
            events = harness.since(mark)
            self.assertEqual(raw_payload(events[-1].raw, "terminalResult")["outcome"], "completed")
            self.assertIn("session.completed", str(events[-1].raw["eveEvent"]))
            with self.assertRaises(HarnessAdapterBusyError):
                await harness.prepare("req-2", sequence=2)
            with self.assertRaises(HarnessControlError):
                await harness.submit("req-3", "after terminal", sequence=3)
        finally:
            await harness.aclose()

    async def test_a_failed_turn_is_a_failure_and_the_session_stays_usable(self) -> None:
        harness = await _started()
        try:
            await harness.submit("req-1", "first task")
            session_id = harness.runtime.created[0]
            mark = await harness.mark()
            await harness.emit_observed(
                session_id, "turn.started", {"sequence": 0, "turnId": "turn_0"}
            )
            await harness.emit_observed(
                session_id,
                "step.failed",
                {
                    "code": "MODEL_CALL_FAILED",
                    "message": "provider refused",
                    "sequence": 0,
                    "stepIndex": 0,
                    "turnId": "turn_0",
                },
            )
            await harness.emit_observed(
                session_id,
                "turn.failed",
                {
                    "code": "MODEL_CALL_FAILED",
                    "message": "provider refused",
                    "sequence": 0,
                    "turnId": "turn_0",
                },
            )
            await harness.emit_observed(
                session_id, "session.waiting", {"wait": "next-user-message"}
            )
            events = harness.since(mark)
            failures = [event for event in events if event.kind == "failed"]
            self.assertEqual(raw_payload(failures[-1].raw, "terminalResult")["outcome"], "failed")
            self.assertEqual(events[-1].kind, "completed")
            receipt = await harness.submit("req-2", "retry", sequence=2)
            self.assertEqual(receipt.acceptance, "immediate")
        finally:
            await harness.aclose()


class EveAdapterIsolationTests(unittest.IsolatedAsyncioTestCase):
    """Scenario 6: two concurrent sessions with interleaved events stay isolated."""

    async def test_two_sessions_do_not_share_transcript_turn_or_pending_state(self) -> None:
        runtime = FakeEveRuntime()
        first = await _started(runtime=runtime)
        second = await _started(runtime=runtime)
        try:
            await first.submit("req-a", "session a")
            await second.submit("req-b", "session b")
            session_a, session_b = runtime.created
            self.assertNotEqual(session_a, session_b)

            # Interleave the two durable records deliberately.
            mark_a = await first.mark()
            mark_b = await second.mark()
            runtime.emit(session_a, "turn.started", {"sequence": 0, "turnId": "turn_0"})
            runtime.emit(session_b, "turn.started", {"sequence": 0, "turnId": "turn_0"})
            runtime.emit(
                session_a,
                "message.completed",
                {
                    "finishReason": "stop",
                    "message": "answer a",
                    "sequence": 0,
                    "stepIndex": 0,
                    "turnId": "turn_0",
                },
            )
            runtime.emit(
                session_b,
                "message.completed",
                {
                    "finishReason": "stop",
                    "message": "answer b",
                    "sequence": 0,
                    "stepIndex": 0,
                    "turnId": "turn_0",
                },
            )
            runtime.emit(
                session_b,
                "input.requested",
                {
                    "requests": [{"kind": "question", "prompt": "b?", "requestId": "req_b_in"}],
                    "sequence": 0,
                    "stepIndex": 0,
                    "turnId": "turn_0",
                },
            )
            runtime.emit(session_a, "session.waiting", {"wait": "next-user-message"})
            runtime.emit(session_b, "session.waiting", {"wait": "next-user-message"})

            await first.pump.drain(until="completed")
            await second.pump.drain(until="completed")
            events_a = first.since(mark_a)
            events_b = second.since(mark_b)

            texts_a = [entry.text for event in events_a for entry in event.transcript]
            texts_b = [entry.text for event in events_b for entry in event.transcript]
            self.assertIn("answer a", texts_a)
            self.assertNotIn("answer b", texts_a)
            self.assertIn("answer b", texts_b)
            self.assertNotIn("answer a", texts_b)

            snapshot_a = await first.snapshot()
            snapshot_b = await second.snapshot()
            self.assertEqual(snapshot_a.vendor_session_id, session_a)
            self.assertEqual(snapshot_b.vendor_session_id, session_b)
            self.assertIsNone(snapshot_a.pending_interaction)
            self.assertIsNotNone(snapshot_b.pending_interaction)
            self.assertEqual(runtime.sessions[session_a].messages, ["session a"])
            self.assertEqual(runtime.sessions[session_b].messages, ["session b"])
        finally:
            await first.aclose()
            await second.aclose()

    async def test_a_child_session_event_on_this_session_is_refused(self) -> None:
        harness = await _started()
        try:
            await harness.submit("req-a", "session a")
            session_a = harness.runtime.created[0]
            await harness.emit_until_turn_observed(session_a, "turn_0")
            # A turn this session never opened: a forwarded child event, or a foreign session's.
            harness.runtime.emit(
                session_a,
                "message.completed",
                {
                    "finishReason": "stop",
                    "message": "child output",
                    "sequence": 0,
                    "stepIndex": 0,
                    "turnId": "turn_child",
                },
            )
            events = await harness.read_until("failed")
            self.assertTrue(events, "the refusal was never translated")
            self.assertEqual(events[-1].kind, "failed")
            self.assertIn("never opened", str(events[-1].raw["eveEvent"]))
        finally:
            await harness.aclose()


LIVE_EVIDENCE_ENTRY_POINT = Path(__file__).parent / "live_eve_native_fixture.py"


def test_the_live_native_evidence_entry_point_keeps_its_documented_surface() -> None:
    """The only surface that runs the native eve runtime end to end must stay runnable.

    Losing this script would silently remove the live native evidence the adapter's native claims
    rest on, so the case fails if the entry point disappears, stops parsing as Python, loses its
    ``main`` entry, or drops the ``--report-dir`` flag the recorded runs are cited with.
    """

    source = LIVE_EVIDENCE_ENTRY_POINT.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LIVE_EVIDENCE_ENTRY_POINT))
    top_level_functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "main" in top_level_functions
    assert '"--report-dir"' in source


if __name__ == "__main__":
    unittest.main()
