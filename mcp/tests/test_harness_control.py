"""Fake-adapter conformance coverage for the protocol-backed hosted control bridge."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
import tempfile
import unittest
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import HarnessAdapterDisconnectedError, HarnessControlError
from agents_remember.serving.harness_capabilities import CapabilitySnapshot
from agents_remember.serving.harness_control_adapter import (
    HarnessProtocolRegistry,
    protocol_adapter_status,
)
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_ipc import (
    HarnessControlClient,
    HarnessControlServer,
    LocalControlEndpoint,
)
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    REQUIRED_ADAPTER_CAPABILITIES,
    AcceptanceState,
    ActivityState,
    AdapterCapability,
    AdapterEvent,
    AdapterHandshake,
    AdapterSnapshot,
    ControlIdentity,
    InteractionResponse,
    LaunchSpec,
    PendingInteraction,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
    SubmissionReceipt,
    TerminalResult,
    TranscriptEntry,
)
from agents_remember.serving.harness_terminal_surface import HarnessTerminalSurface


class _FakeAdapter:
    """A deterministic protocol adapter that owns the conformance test event stream."""

    def __init__(
        self,
        *,
        capabilities: frozenset[AdapterCapability] = REQUIRED_ADAPTER_CAPABILITIES,
        handshake_identity: ControlIdentity | None = None,
    ) -> None:
        self.capabilities = capabilities
        self.handshake_identity = handshake_identity
        self.current: AdapterSnapshot | None = None
        self.events: asyncio.Queue[AdapterEvent | None] = asyncio.Queue()
        self.acceptances: deque[AcceptanceState] = deque()
        self.disconnects: deque[bool | None] = deque()
        self.reconciliations: dict[str, ReconciliationResult] = {}
        self.submissions: list[PromptRequest] = []
        self.responses: list[InteractionResponse] = []
        self.stop_modes: list[ShutdownMode] = []
        self.launches: list[LaunchSpec] = []

    async def start(self, launch: LaunchSpec) -> AdapterHandshake:
        self.launches.append(launch)
        identity = self.handshake_identity or launch.identity
        self.current = AdapterSnapshot(
            identity=identity,
            control="ready",
            activity="idle",
            acceptance="immediate",
            vendor_session_id="vendor-session-1",
            raw={"fake": True},
        )
        return AdapterHandshake(
            protocol_version=CONTROL_PROTOCOL_VERSION,
            adapter_id="fake",
            identity=identity,
            capabilities=self.capabilities,
            snapshot=self.current,
        )

    async def snapshot(self) -> AdapterSnapshot:
        assert self.current is not None
        return self.current

    def advertise(self) -> CapabilitySnapshot:
        return CapabilitySnapshot(models=(), selected_model_key=None, selected_effort=None)

    async def _event_stream(self) -> AsyncIterator[AdapterEvent]:
        while True:
            event = await self.events.get()
            if event is None:
                return
            yield event

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        return self._event_stream()

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self.submissions.append(request)
        if self.disconnects:
            may_have_sent = self.disconnects.popleft()
            if may_have_sent is not None:
                raise HarnessAdapterDisconnectedError(
                    "fake adapter disconnected during submit",
                    may_have_sent=may_have_sent,
                    vendor_correlation_id=f"vendor-{request.request_id}",
                )
        acceptance = self.acceptances.popleft() if self.acceptances else "immediate"
        return SubmissionReceipt(
            request_id=request.request_id,
            acceptance=acceptance,
            submitted_at=request.submitted_at,
            vendor_correlation_id=f"vendor-{request.request_id}",
            accepted_at=request.submitted_at if acceptance == "immediate" else None,
        )

    async def respond(self, response: InteractionResponse) -> None:
        self.responses.append(response)
        assert self.current is not None
        self.current = replace(
            self.current,
            activity="settling",
            acceptance="queued",
            pending_interaction=None,
        )

    async def reconcile(self, request_id: str) -> ReconciliationResult:
        return self.reconciliations.get(
            request_id,
            ReconciliationResult(
                request_id=request_id,
                state="unresolved",
                reconciled_at="2026-07-13T18:00:00+00:00",
            ),
        )

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)

    def emit(self, event: AdapterEvent) -> None:
        if event.snapshot is not None:
            self.current = event.snapshot
        self.events.put_nowait(event)


class _BlockingSubmitAdapter(_FakeAdapter):
    """Hold one adapter submission so queue stop/error races are deterministic."""

    def __init__(self, *, error: Exception | None = None) -> None:
        super().__init__()
        self.error = error
        self.submit_started = asyncio.Event()
        self.release_submit = asyncio.Event()

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self.submit_started.set()
        await self.release_submit.wait()
        if self.error is not None:
            raise self.error
        return await super().submit(request)


class _ObservedHarnessControlServer(HarnessControlServer):
    """Expose completion of the fire-and-forget asyncio client callback to tests."""

    def __init__(self, endpoint: LocalControlEndpoint, bridge: HarnessControlBridge) -> None:
        super().__init__(endpoint, bridge)
        self.connection_finished = asyncio.Event()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await super()._handle_connection(reader, writer)
        finally:
            self.connection_finished.set()


def _identity(session: str = "ar-session-1") -> ControlIdentity:
    return ControlIdentity(
        ar_session_id=session,
        tmux_name=f"ar-{session}",
        created_at="2026-07-13T17:30:00+00:00",
    )


def _launch(identity: ControlIdentity) -> LaunchSpec:
    return LaunchSpec(
        identity=identity,
        harness_id="fake",
        cwd=Path("/workspace"),
        argv=("fake-harness", "protocol-mode"),
        env={"PRESERVE_INSTALLED_AUTH": "1"},
    )


async def _settle_events() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


class HarnessControlConformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_and_ordered_terminal_durable_acceptance(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        adapter.acceptances.extend(("immediate", "queued"))
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: "2026-07-13T18:00:00+00:00")
        await bridge.start(_launch(identity))
        surface = HarnessTerminalSurface(bridge)
        try:
            first = await surface.submit_terminal("terminal prompt", request_id="request-1")
            second = await surface.submit_durable("durable prompt", request_id="request-2")

            self.assertEqual((first.acceptance, second.acceptance), ("immediate", "queued"))
            self.assertEqual(
                [(item.request_id, item.source) for item in adapter.submissions],
                [("request-1", "terminal"), ("request-2", "durable")],
            )
            self.assertEqual(first.vendor_correlation_id, "vendor-request-1")
            self.assertEqual(adapter.launches, [_launch(identity)])
            with self.assertRaisesRegex(HarnessControlError, "already started"):
                await bridge.start(_launch(identity))
        finally:
            await bridge.stop("forced")

    async def test_automated_delivery_preserves_draft_and_draft_submits_next(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        surface = HarnessTerminalSurface(bridge)
        try:
            surface.update_draft("human draft in progress")
            automated = await surface.submit_durable(
                "automated whole message", request_id="delivery-1"
            )
            self.assertEqual(automated.acceptance, "immediate")
            self.assertEqual(surface.draft.text, "human draft in progress")

            committed = await surface.submit_draft(request_id="draft-1")
            self.assertEqual(committed.acceptance, "immediate")
            self.assertEqual(
                [(item.source, item.text) for item in adapter.submissions],
                [
                    ("durable", "automated whole message"),
                    ("terminal", "human draft in progress"),
                ],
            )
            self.assertEqual(surface.draft.text, "")
            self.assertFalse(hasattr(surface, "discard_draft"))
        finally:
            await bridge.stop("forced")

    async def test_newer_human_edit_survives_in_flight_draft_submission(self) -> None:
        identity = _identity()
        adapter = _BlockingSubmitAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        surface = HarnessTerminalSurface(bridge)
        surface.update_draft("committed revision")
        submission = asyncio.create_task(surface.submit_draft(request_id="draft-1"))
        try:
            await asyncio.wait_for(adapter.submit_started.wait(), timeout=1.0)
            surface.update_draft("newer uncommitted revision")
            adapter.release_submit.set()
            await asyncio.wait_for(submission, timeout=1.0)
            self.assertEqual(surface.draft.text, "newer uncommitted revision")
            self.assertEqual([item.text for item in adapter.submissions], ["committed revision"])
        finally:
            adapter.release_submit.set()
            if not submission.done():
                submission.cancel()
                await asyncio.gather(submission, return_exceptions=True)
            await bridge.stop("forced")

    async def test_ambiguous_draft_submission_retains_human_text(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        adapter.disconnects.append(True)
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        surface = HarnessTerminalSurface(bridge)
        surface.update_draft("retain until acceptance is known")
        try:
            receipt = await surface.submit_draft(request_id="draft-unknown")
            self.assertEqual(receipt.acceptance, "unknown")
            self.assertEqual(surface.draft.text, "retain until acceptance is known")
            self.assertEqual(len(adapter.submissions), 1)
        finally:
            await bridge.stop("forced")

    async def test_snapshot_subscription_is_bounded_and_coalesces_to_latest_state(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter, subscriber_queue_limit=1)
        await bridge.start(_launch(identity))
        stream = bridge.subscribe()
        try:
            initial = await anext(stream)
            self.assertEqual(initial.activity, "idle")
            updates: tuple[tuple[int, ActivityState], ...] = (
                (1, "running"),
                (2, "settling"),
            )
            for sequence, activity in updates:
                adapter.emit(
                    AdapterEvent(
                        sequence=sequence,
                        kind="state",
                        identity=identity,
                        created_at=f"time-{sequence}",
                        snapshot=replace(
                            bridge.snapshot(),
                            activity=activity,
                            acceptance="queued",
                        ),
                    )
                )
                await _settle_events()
            latest = await asyncio.wait_for(anext(stream), timeout=1.0)
            self.assertEqual(latest.activity, "settling")
        finally:
            await stream.aclose()
            await bridge.stop("forced")

    async def test_busy_blocked_settling_completion_and_readable_transcript(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            pending = PendingInteraction(
                interaction_id="approval-1",
                kind="approval",
                prompt="Allow command?",
                created_at="2026-07-13T18:01:00+00:00",
                choices=("allow", "deny"),
            )
            states: tuple[tuple[ActivityState, AcceptanceState, PendingInteraction | None], ...] = (
                ("running", "queued", None),
                ("blocked", "rejected", pending),
                ("settling", "queued", None),
            )
            for sequence, (activity, acceptance, interaction) in enumerate(states, start=1):
                snapshot = AdapterSnapshot(
                    identity=identity,
                    control="ready",
                    activity=activity,
                    acceptance=acceptance,
                    vendor_session_id="vendor-session-1",
                    pending_interaction=interaction,
                )
                adapter.emit(
                    AdapterEvent(
                        sequence=sequence,
                        kind="state",
                        identity=identity,
                        created_at=f"2026-07-13T18:0{sequence}:00+00:00",
                        snapshot=snapshot,
                    )
                )
                await _settle_events()
                self.assertEqual(bridge.snapshot().activity, activity)

            completed = AdapterSnapshot(
                identity=identity,
                control="ready",
                activity="idle",
                acceptance="immediate",
                vendor_session_id="vendor-session-1",
            )
            entry = TranscriptEntry(
                sequence=1,
                role="result",
                text="done\x1b[31m",
                created_at="2026-07-13T18:04:00+00:00",
                request_id="request-1",
                terminal_result=TerminalResult(
                    outcome="completed", completed_at="2026-07-13T18:04:00+00:00"
                ),
            )
            adapter.emit(
                AdapterEvent(
                    sequence=4,
                    kind="completed",
                    identity=identity,
                    created_at=entry.created_at,
                    snapshot=completed,
                    transcript=(entry,),
                )
            )
            await _settle_events()

            self.assertEqual(bridge.snapshot().activity, "idle")
            rendered = HarnessTerminalSurface(bridge).render().decode()
            self.assertIn("result=completed", rendered)
            self.assertNotIn("\x1b", rendered)
        finally:
            await bridge.stop("forced")

    async def test_pending_interaction_response_uses_adapter_snapshot(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            pending = PendingInteraction(
                interaction_id="question-1",
                kind="question",
                prompt="Continue?",
                created_at="2026-07-13T18:00:00+00:00",
            )
            blocked = replace(
                bridge.snapshot(),
                activity="blocked",
                acceptance="rejected",
                pending_interaction=pending,
            )
            adapter.emit(AdapterEvent(1, "state", identity, pending.created_at, snapshot=blocked))
            await _settle_events()

            result = await bridge.respond(
                InteractionResponse(
                    interaction_id="question-1",
                    response="continue",
                    responded_at="2026-07-13T18:01:00+00:00",
                )
            )
            self.assertEqual(result.activity, "settling")
            self.assertIsNone(result.pending_interaction)
        finally:
            await bridge.stop("forced")

    async def test_disconnect_before_and_after_send_never_blindly_resends(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        adapter.disconnects.extend((False, True))
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            before = await bridge.submit(
                bridge.prompt("before", source="terminal", request_id="before")
            )
            after = await bridge.submit(
                bridge.prompt("after", source="durable", request_id="after")
            )
            self.assertEqual((before.acceptance, after.acceptance), ("rejected", "unknown"))
            self.assertEqual(len(adapter.submissions), 2)

            adapter.reconciliations["after"] = ReconciliationResult(
                request_id="after",
                state="accepted",
                reconciled_at="2026-07-13T18:05:00+00:00",
                vendor_correlation_id="vendor-after",
            )
            reconciled = await bridge.reconcile("after")
            self.assertEqual(reconciled.state, "accepted")
            self.assertEqual(len(adapter.submissions), 2)
        finally:
            await bridge.stop("forced")

    async def test_operator_resolution_and_ambiguous_ledger_are_bounded(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        adapter.disconnects.extend((True, True))
        bridge = HarnessControlBridge(identity, adapter, submission_limit=2)
        await bridge.start(_launch(identity))
        try:
            for request_id in ("unknown-1", "unknown-2"):
                receipt = await bridge.submit(
                    bridge.prompt("prompt", source="durable", request_id=request_id)
                )
                self.assertEqual(receipt.acceptance, "unknown")
            refused = await bridge.submit(
                bridge.prompt("third", source="durable", request_id="unknown-3")
            )
            self.assertEqual(refused.acceptance, "rejected")
            self.assertIn("ledger", refused.detail or "")
            self.assertEqual(len(adapter.submissions), 2)

            resolution = await bridge.resolve_unknown(
                "unknown-1", state="rejected", detail="operator confirmed no visible turn"
            )
            self.assertEqual(resolution.state, "rejected")
            self.assertEqual(len(adapter.submissions), 2)
        finally:
            await bridge.stop("forced")

    async def test_unexpected_adapter_error_fails_active_and_queued_commands(self) -> None:
        identity = _identity()
        adapter = _BlockingSubmitAdapter(error=RuntimeError("probe failure"))
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        first = asyncio.create_task(
            bridge.submit(bridge.prompt("first", source="terminal", request_id="first"))
        )
        try:
            await asyncio.wait_for(adapter.submit_started.wait(), timeout=1.0)
            second = asyncio.create_task(
                bridge.submit(bridge.prompt("second", source="durable", request_id="second"))
            )
            await asyncio.sleep(0)
            adapter.release_submit.set()
            for task in (first, second):
                with self.assertRaisesRegex(
                    HarnessControlError, "unexpected adapter RuntimeError.*probe failure"
                ):
                    await asyncio.wait_for(task, timeout=1.0)
            self.assertEqual(bridge.snapshot().control, "failed")
            with self.assertRaisesRegex(HarnessControlError, "control bridge failed"):
                await asyncio.wait_for(
                    bridge.submit(bridge.prompt("third", source="terminal", request_id="third")),
                    timeout=1.0,
                )
        finally:
            adapter.release_submit.set()
            if not first.done():
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)
            await bridge.stop("forced")

    async def test_graceful_stop_race_rejects_new_command_without_hanging(self) -> None:
        identity = _identity()
        adapter = _BlockingSubmitAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        active = asyncio.create_task(
            bridge.submit(bridge.prompt("active", source="terminal", request_id="active"))
        )
        stop = None
        try:
            await asyncio.wait_for(adapter.submit_started.wait(), timeout=1.0)
            stop = asyncio.create_task(bridge.stop("graceful"))
            await asyncio.sleep(0)
            with self.assertRaisesRegex(HarnessControlError, "control bridge is stopped"):
                await asyncio.wait_for(
                    bridge.submit(bridge.prompt("racing", source="durable", request_id="racing")),
                    timeout=1.0,
                )
            adapter.release_submit.set()
            self.assertEqual((await asyncio.wait_for(active, timeout=1.0)).acceptance, "immediate")
            await asyncio.wait_for(stop, timeout=1.0)
        finally:
            adapter.release_submit.set()
            if not active.done():
                active.cancel()
                await asyncio.gather(active, return_exceptions=True)
            if stop is not None and not stop.done():
                stop.cancel()
                await asyncio.gather(stop, return_exceptions=True)
            if bridge.snapshot().control != "disconnected":
                await bridge.stop("forced")

    async def test_force_stop_cancellation_drains_active_and_queued_commands(self) -> None:
        identity = _identity()
        adapter = _BlockingSubmitAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        active = asyncio.create_task(
            bridge.submit(bridge.prompt("active", source="terminal", request_id="active"))
        )
        await asyncio.wait_for(adapter.submit_started.wait(), timeout=1.0)
        queued = asyncio.create_task(
            bridge.submit(bridge.prompt("queued", source="durable", request_id="queued"))
        )
        await asyncio.sleep(0)
        await asyncio.wait_for(bridge.stop("forced"), timeout=1.0)
        for task in (active, queued):
            with self.assertRaisesRegex(HarnessControlError, "cancelled"):
                await asyncio.wait_for(task, timeout=1.0)

    async def test_evicted_reconciliation_fails_loudly_and_runner_survives(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter, submission_limit=1)
        await bridge.start(_launch(identity))
        try:
            await bridge.submit(bridge.prompt("old", source="terminal", request_id="old"))
            await bridge.submit(bridge.prompt("new", source="terminal", request_id="new"))
            with self.assertRaisesRegex(HarnessControlError, "only an unknown submission"):
                await asyncio.wait_for(bridge.reconcile("old"), timeout=1.0)
            survived = await asyncio.wait_for(
                bridge.submit(bridge.prompt("after", source="terminal", request_id="after")),
                timeout=1.0,
            )
            self.assertEqual(survived.acceptance, "immediate")
        finally:
            await bridge.stop("forced")

    async def test_unsupported_submission_ledger_is_bounded_at_two_sizes(self) -> None:
        registry = HarnessProtocolRegistry()
        for total in (8, 64):
            identity = _identity(f"unsupported-{total}")
            bridge = HarnessControlBridge(
                identity,
                registry.create("settings-harness"),
                submission_limit=4,
            )
            await bridge.start(replace(_launch(identity), harness_id="settings-harness"))
            try:
                for index in range(total):
                    receipt = await bridge.submit(
                        bridge.prompt(
                            f"message-{index}",
                            source="durable",
                            request_id=f"request-{index}",
                        )
                    )
                    self.assertEqual(receipt.acceptance, "unsupported")
                self.assertLessEqual(bridge.retained_submission_count, 4)
            finally:
                await bridge.stop("forced")

    async def test_additive_event_is_retained_and_malformed_event_fails_loudly(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            adapter.emit(
                AdapterEvent(
                    sequence=1,
                    kind="future-vendor-detail",
                    identity=identity,
                    created_at="2026-07-13T18:00:00+00:00",
                    raw={"futureField": {"nested": True}},
                )
            )
            await _settle_events()
            self.assertEqual(bridge.snapshot().raw["futureField"], {"nested": True})
            self.assertEqual(bridge.snapshot().control, "ready")

            adapter.emit(
                AdapterEvent(
                    sequence=2,
                    kind="state",
                    identity=identity,
                    created_at="2026-07-13T18:01:00+00:00",
                )
            )
            await _settle_events()
            self.assertEqual(bridge.snapshot().control, "failed")
            self.assertIn("requires a snapshot", str(bridge.snapshot().raw["bridgeError"]))
        finally:
            await bridge.stop("forced")

    async def test_capability_and_identity_mismatch_force_adapter_shutdown(self) -> None:
        identity = _identity()
        missing = _FakeAdapter(capabilities=frozenset({"state-snapshot"}))
        bridge = HarnessControlBridge(identity, missing)
        with self.assertRaisesRegex(HarnessControlError, "capability mismatch"):
            await bridge.start(_launch(identity))
        self.assertEqual(missing.stop_modes, ["forced"])

        wrong = _FakeAdapter(handshake_identity=_identity("wrong-session"))
        bridge = HarnessControlBridge(identity, wrong)
        with self.assertRaisesRegex(HarnessControlError, "identity"):
            await bridge.start(_launch(identity))
        self.assertEqual(wrong.stop_modes, ["forced"])

    async def test_transcript_reclamation_is_bounded_at_two_input_sizes(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter, transcript_limit=3)
        await bridge.start(_launch(identity))
        try:
            first = tuple(
                TranscriptEntry(i, "assistant", f"line-{i}", f"time-{i}") for i in range(1, 3)
            )
            adapter.emit(AdapterEvent(1, "transcript", identity, "time-2", transcript=first))
            await _settle_events()
            self.assertEqual(len(bridge.transcript()), 2)

            second = tuple(
                TranscriptEntry(i, "assistant", f"line-{i}", f"time-{i}") for i in range(3, 8)
            )
            adapter.emit(AdapterEvent(2, "transcript", identity, "time-7", transcript=second))
            await _settle_events()
            self.assertEqual([entry.sequence for entry in bridge.transcript()], [5, 6, 7])
        finally:
            await bridge.stop("forced")

    async def test_graceful_forced_and_unsupported_shutdown(self) -> None:
        identity = _identity()
        graceful_adapter = _FakeAdapter()
        graceful = HarnessControlBridge(identity, graceful_adapter)
        await graceful.start(_launch(identity))
        await graceful.stop("graceful")
        self.assertEqual(graceful_adapter.stop_modes, ["graceful"])

        forced_adapter = _FakeAdapter()
        forced = HarnessControlBridge(identity, forced_adapter)
        await forced.start(_launch(identity))
        await forced.stop("forced")
        self.assertEqual(forced_adapter.stop_modes, ["forced"])

        registry = HarnessProtocolRegistry()
        unsupported = HarnessControlBridge(identity, registry.create("settings-harness"))
        snapshot = await unsupported.start(
            replace(_launch(identity), harness_id="settings-harness")
        )
        self.assertEqual(snapshot.control, "unsupported")
        receipt = await unsupported.submit(
            unsupported.prompt("hello", source="terminal", request_id="unsupported-1")
        )
        self.assertEqual(receipt.acceptance, "unsupported")
        self.assertEqual(protocol_adapter_status("settings-harness"), "unsupported")
        await unsupported.stop("graceful")


class HarnessControlIpcTests(unittest.IsolatedAsyncioTestCase):
    async def test_peer_timeout_after_submit_preserves_reconciliation_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            identity = _identity()
            adapter = _BlockingSubmitAdapter()
            adapter.disconnects.append(True)
            adapter.reconciliations["ipc-timeout"] = ReconciliationResult(
                request_id="ipc-timeout",
                state="accepted",
                reconciled_at="2026-07-14T17:36:00+02:00",
                vendor_correlation_id="vendor-ipc-timeout",
            )
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(Path(tmp_str), identity)
            server = _ObservedHarnessControlServer(endpoint, bridge)
            await server.start()
            loop = asyncio.get_running_loop()
            prior_exception_handler = loop.get_exception_handler()
            callback_exceptions: list[dict[str, object]] = []
            loop.set_exception_handler(lambda _loop, context: callback_exceptions.append(context))
            try:
                _, writer = await asyncio.open_unix_connection(endpoint.path)
                request = {
                    "protocol": CONTROL_PROTOCOL_VERSION,
                    "identity": identity.to_json(),
                    "action": "submit",
                    "payload": {
                        "requestId": "ipc-timeout",
                        "source": "durable",
                        "text": "hello",
                        "submittedAt": "2026-07-14T17:36:00+02:00",
                    },
                }
                writer.write(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
                await writer.drain()
                await asyncio.wait_for(adapter.submit_started.wait(), timeout=1.0)
                writer.transport.abort()
                adapter.release_submit.set()

                await asyncio.wait_for(server.connection_finished.wait(), timeout=1.0)
                await asyncio.sleep(0)
                self.assertEqual(callback_exceptions, [])
                reconciled = await asyncio.wait_for(bridge.reconcile("ipc-timeout"), timeout=1.0)
                self.assertEqual(reconciled.state, "accepted")
                self.assertEqual(reconciled.vendor_correlation_id, "vendor-ipc-timeout")
                self.assertEqual(
                    [submission.request_id for submission in adapter.submissions],
                    ["ipc-timeout"],
                )
            finally:
                loop.set_exception_handler(prior_exception_handler)
                adapter.release_submit.set()
                await server.close()
                await bridge.stop("forced")

    async def test_private_endpoint_exact_identity_and_submission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            identity = _identity()
            adapter = _FakeAdapter()
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(Path(tmp_str) / "control", identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            try:
                self.assertEqual(stat.S_IMODE(endpoint.path.parent.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(endpoint.path.stat().st_mode), 0o600)

                client = HarnessControlClient(endpoint)
                handshake = await client.request("handshake")
                assert isinstance(handshake, dict)
                self.assertEqual(handshake["protocol"], CONTROL_PROTOCOL_VERSION)
                result = await client.request(
                    "submit",
                    {
                        "requestId": "ipc-request-1",
                        "source": "durable",
                        "text": "hello",
                        "submittedAt": "2026-07-13T18:00:00+00:00",
                    },
                )
                assert isinstance(result, dict)
                self.assertEqual(result["acceptance"], "immediate")

                wrong = HarnessControlClient(
                    LocalControlEndpoint(path=endpoint.path, identity=_identity("wrong"))
                )
                with self.assertRaisesRegex(HarnessControlError, "identity"):
                    await wrong.request("snapshot")
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_malformed_ipc_request_is_rejected_without_control_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            identity = _identity()
            adapter = _FakeAdapter()
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(Path(tmp_str), identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            try:
                reader, writer = await asyncio.open_unix_connection(endpoint.path)
                writer.write(b"not-json\n")
                await writer.drain()
                response = json.loads(await reader.readline())
                writer.close()
                await writer.wait_closed()
                self.assertFalse(response["ok"])
                self.assertEqual(adapter.submissions, [])
            finally:
                await server.close()
                await bridge.stop("forced")


if __name__ == "__main__":
    unittest.main()
