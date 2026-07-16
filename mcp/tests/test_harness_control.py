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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.operator_inbox_records import create_operator_inbox_entry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.errors import HarnessAdapterDisconnectedError, HarnessControlError
from agents_remember.serving.harness_capabilities import CapabilitySnapshot, SetResult
from agents_remember.serving.harness_control_adapter import (
    HarnessProtocolRegistry,
    protocol_adapter_status,
)
from agents_remember.serving.harness_control_api import register_harness_control_routes
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_client import (
    read_control_capabilities,
    reconcile_control_prompt,
    set_control_effort,
    set_control_model,
    submit_control_prompt,
)
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
from agents_remember.serving.inbox_delivery import deliver_inbox_entry
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    TerminalLivenessObservation,
)


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
        self.reconciliation_requests: list[str] = []
        self.submissions: list[PromptRequest] = []
        self.responses: list[InteractionResponse] = []
        self.stop_modes: list[ShutdownMode] = []
        self.launches: list[LaunchSpec] = []
        self.control_log: list[tuple[str, str]] = []
        self.set_results: deque[SetResult] = deque()

    async def start(self, launch: LaunchSpec) -> AdapterHandshake:
        self.launches.append(launch)
        self.control_log.append(("launch", launch.harness_id))
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
        self.control_log.append(("prompt", request.request_id))
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

    async def set_model(self, model_key: str) -> SetResult:
        self.control_log.append(("model", model_key))
        if self.set_results:
            return self.set_results.popleft()
        return SetResult(
            ok=True,
            acceptance="immediate",
            requested_value=model_key,
            detail="fake accepted without an effective echo",
        )

    async def set_effort(self, effort: str) -> SetResult:
        self.control_log.append(("effort", effort))
        if self.set_results:
            return self.set_results.popleft()
        return SetResult(
            ok=True,
            acceptance="immediate",
            requested_value=effort,
            detail="fake accepted without an effective echo",
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
        self.reconciliation_requests.append(request_id)
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


class _BlockingSetAdapter(_FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.set_started = asyncio.Event()
        self.release_set = asyncio.Event()

    async def set_model(self, model_key: str) -> SetResult:
        self.control_log.append(("model", model_key))
        self.set_started.set()
        await self.release_set.wait()
        return SetResult(
            ok=True,
            acceptance="queued",
            requested_value=model_key,
            detail="fake queued set",
        )


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


class _DropFirstSubmitResponseServer(HarnessControlServer):
    """Dispatch one real submit, then close its outer socket before writing the receipt."""

    def __init__(self, endpoint: LocalControlEndpoint, bridge: HarnessControlBridge) -> None:
        super().__init__(endpoint, bridge)
        self.dropped = asyncio.Event()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        if self.dropped.is_set():
            await super()._handle_connection(reader, writer)
            return
        line = await reader.readline()
        raw = json.loads(line)
        assert isinstance(raw, dict) and raw.get("action") == "submit"
        await self._dispatch(raw)
        self.dropped.set()
        writer.close()
        await writer.wait_closed()


@dataclass(frozen=True)
class _ControlledEntry:
    id: str
    tmux_name: str
    created_at: str
    control_endpoint: Path


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

    async def test_capability_setters_share_launch_set_prompt_queue_order(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            model_task = asyncio.create_task(bridge.set_model("model-b"))
            await asyncio.sleep(0)
            prompt_task = asyncio.create_task(
                bridge.submit(bridge.prompt("after set", source="durable", request_id="after-set"))
            )
            model, receipt = await asyncio.gather(model_task, prompt_task)
            self.assertEqual((model.ok, model.acceptance), (True, "immediate"))
            self.assertEqual(receipt.acceptance, "immediate")
            self.assertEqual(
                adapter.control_log,
                [
                    ("launch", "fake"),
                    ("model", "model-b"),
                    ("prompt", "after-set"),
                ],
            )
        finally:
            await bridge.stop("forced")

    async def test_cancelled_setter_late_completion_does_not_kill_command_queue(self) -> None:
        identity = _identity()
        adapter = _BlockingSetAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            setter = asyncio.create_task(bridge.set_model("model-b"))
            await asyncio.wait_for(adapter.set_started.wait(), timeout=1.0)
            setter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await setter
            adapter.release_set.set()

            receipt = await asyncio.wait_for(
                bridge.submit(
                    bridge.prompt(
                        "still alive",
                        source="durable",
                        request_id="after-cancel",
                    )
                ),
                timeout=1.0,
            )
            self.assertEqual(receipt.acceptance, "immediate")
            self.assertEqual(adapter.control_log[-1], ("prompt", "after-cancel"))
            self.assertEqual(bridge.snapshot().control, "ready")
        finally:
            await bridge.stop("forced")

    async def test_set_result_truth_invariants_fail_bad_adapter_results_without_poisoning(
        self,
    ) -> None:
        invalid = (
            SetResult(True, "echo-verified", "value"),
            SetResult(True, "queued", "value", effective_value="value"),
            SetResult(True, "unknown", "value"),
            SetResult(False, "immediate", "value"),
            SetResult(False, "unsupported", "value", effective_value="value"),
            SetResult(False, cast(Any, "garbage"), "value"),
            SetResult(False, cast(Any, "rejected"), "value"),
            SetResult(False, cast(Any, ""), "value"),
        )
        for result in invalid:
            with self.subTest(result=result):
                identity = _identity()
                adapter = _FakeAdapter()
                adapter.set_results.append(result)
                bridge = HarnessControlBridge(identity, adapter)
                await bridge.start(_launch(identity))
                try:
                    with self.assertRaises(HarnessControlError):
                        await bridge.set_model("value")
                    receipt = await bridge.submit(
                        bridge.prompt(
                            "runner survives",
                            source="durable",
                            request_id="survives",
                        )
                    )
                    self.assertEqual(receipt.acceptance, "immediate")
                finally:
                    await bridge.stop("forced")

    async def test_unregistered_adapter_setters_remain_explicitly_unsupported(self) -> None:
        adapter = HarnessProtocolRegistry().create("custom-harness")
        model = await adapter.set_model("anything")
        effort = await adapter.set_effort("anything")
        self.assertEqual((model.ok, model.acceptance), (False, "unsupported"))
        self.assertEqual((effort.ok, effort.acceptance), (False, "unsupported"))

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

    async def test_duplicate_request_id_returns_retained_result_without_resubmission(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            first = await bridge.submit(
                bridge.prompt("only once", source="terminal", request_id="request-duplicate")
            )
            self.assertEqual(first.acceptance, "immediate")
            duplicate = await bridge.submit(
                bridge.prompt(
                    "must not replace the first payload",
                    source="terminal",
                    request_id="request-duplicate",
                )
            )
            self.assertEqual(duplicate, first)
            self.assertEqual(
                [request.request_id for request in adapter.submissions],
                ["request-duplicate"],
            )
            self.assertEqual(adapter.submissions[0].text, "only once")
        finally:
            await bridge.stop("forced")

    async def test_pending_duplicate_waits_for_first_result_without_resubmission(self) -> None:
        identity = _identity()
        adapter = _BlockingSubmitAdapter()
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        try:
            first = asyncio.create_task(
                bridge.submit(
                    bridge.prompt("first payload", source="terminal", request_id="pending-id")
                )
            )
            await asyncio.wait_for(adapter.submit_started.wait(), timeout=1.0)
            duplicate = asyncio.create_task(
                bridge.submit(
                    bridge.prompt(
                        "ignored duplicate payload",
                        source="terminal",
                        request_id="pending-id",
                    )
                )
            )
            await asyncio.sleep(0)
            self.assertFalse(duplicate.done())
            adapter.release_submit.set()
            first_receipt, duplicate_receipt = await asyncio.gather(first, duplicate)
            self.assertEqual(duplicate_receipt, first_receipt)
            self.assertEqual(len(adapter.submissions), 1)
            self.assertEqual(adapter.submissions[0].text, "first payload")
        finally:
            adapter.release_submit.set()
            await bridge.stop("forced")

    async def test_known_receipts_reconcile_without_native_reconciliation(self) -> None:
        identity = _identity()
        adapter = _FakeAdapter()
        adapter.acceptances.extend(("immediate", "queued", "rejected", "unsupported"))
        bridge = HarnessControlBridge(identity, adapter)
        await bridge.start(_launch(identity))
        expected = ("accepted", "accepted", "rejected", "unsupported")
        try:
            for index, state in enumerate(expected):
                request_id = f"known-{index}"
                receipt = await bridge.submit(
                    bridge.prompt("payload", source="terminal", request_id=request_id)
                )
                result = await bridge.reconcile(request_id)
                self.assertEqual(result.state, state)
                self.assertEqual(result.vendor_correlation_id, receipt.vendor_correlation_id)
            self.assertEqual(adapter.reconciliation_requests, [])
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
            with self.assertRaisesRegex(HarnessControlError, "no longer retained"):
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
    async def test_exact_session_ipc_advertises_and_returns_set_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            identity = _identity()
            adapter = _FakeAdapter()
            adapter.set_results.extend(
                (
                    SetResult(True, "queued", "model-b", detail="next turn"),
                    SetResult(False, "unsupported", "max", detail="model gated"),
                )
            )
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(Path(tmp_str), identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            entry = _ControlledEntry(
                identity.ar_session_id,
                identity.tmux_name,
                identity.created_at,
                endpoint.path,
            )
            try:
                capabilities = await asyncio.to_thread(read_control_capabilities, entry)
                model = await asyncio.to_thread(set_control_model, entry, "model-b")
                effort = await asyncio.to_thread(set_control_effort, entry, "max")
                self.assertEqual(capabilities.models, ())
                self.assertEqual((model.ok, model.acceptance), (True, "queued"))
                self.assertEqual((effort.ok, effort.acceptance), (False, "unsupported"))
                self.assertEqual(
                    adapter.control_log[-2:], [("model", "model-b"), ("effort", "max")]
                )
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_outer_socket_lost_receipt_reconciles_retained_known_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            identity = _identity("outer-loss")
            adapter = _FakeAdapter()
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(Path(tmp_str), identity)
            server = _DropFirstSubmitResponseServer(endpoint, bridge)
            await server.start()
            entry = _ControlledEntry(
                identity.ar_session_id,
                identity.tmux_name,
                identity.created_at,
                endpoint.path,
            )
            try:
                receipt = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "one complete message",
                    source="durable",
                    request_id="outer-loss-request",
                )
                self.assertEqual(receipt.acceptance, "unknown")
                self.assertEqual(receipt.request_id, "outer-loss-request")
                await asyncio.wait_for(server.dropped.wait(), timeout=1.0)

                reconciled = await asyncio.to_thread(
                    reconcile_control_prompt, entry, "outer-loss-request"
                )

                self.assertEqual(reconciled.state, "accepted")
                self.assertEqual(
                    reconciled.vendor_correlation_id,
                    "vendor-outer-loss-request",
                )
                self.assertEqual(len(adapter.submissions), 1)
                self.assertEqual(adapter.reconciliation_requests, [])
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_durable_inbox_outer_loss_converges_by_reconcile_without_resend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            root = Path(tmp_str)
            identity = _identity("durable-outer-loss")
            adapter = _FakeAdapter()
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(root / "control", identity)
            server = _DropFirstSubmitResponseServer(endpoint, bridge)
            await server.start()
            catalog = TerminalCatalog(root / "terminal-sessions.json")
            catalog.upsert(
                TerminalCatalogEntry(
                    id=identity.ar_session_id,
                    label="Worker",
                    kind="harness",
                    harness="claude",
                    lifecycle_id="L1",
                    cwd=root,
                    tmux_name=identity.tmux_name,
                    command=("claude",),
                    created_at=identity.created_at,
                    last_attached_at=identity.created_at,
                    status="running",
                    control_state="ready",
                    control_endpoint=endpoint.path,
                    control_protocol=CONTROL_PROTOCOL_VERSION,
                )
            )
            store = OperatorInboxStore(root)
            inbox = create_operator_inbox_entry(
                entry_id="durable-request",
                now=identity.created_at,
                lifecycle_id="L1",
                agent_id=identity.ar_session_id,
                ask="Continue",
                response="Review the result",
                created_by="manager",
                created_via="cli",
            )
            store.append(inbox)
            paster = mock.Mock()
            host = TerminalHost(tmux_probe=lambda _name: True)
            try:
                first = await asyncio.to_thread(
                    deliver_inbox_entry,
                    store=store,
                    catalog=catalog,
                    host=host,
                    paster=paster,
                    entry=inbox,
                )
                self.assertEqual(first.adapterDeliveryState, "unknown")
                self.assertEqual(first.adapterRequestId, "durable-request")

                recovered = await asyncio.to_thread(
                    deliver_inbox_entry,
                    store=store,
                    catalog=catalog,
                    host=host,
                    paster=paster,
                    entry=first,
                )

                self.assertEqual(recovered.deliveryState, "delivered")
                self.assertEqual(recovered.adapterDeliveryState, "accepted")
                self.assertEqual(
                    recovered.adapterVendorCorrelationId,
                    "vendor-durable-request",
                )
                self.assertEqual(len(adapter.submissions), 1)
                self.assertEqual(adapter.reconciliation_requests, [])
                paster.paste.assert_not_called()
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_public_duplicate_returns_retained_result_with_one_adapter_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_str:
            root = Path(tmp_str)
            identity = _identity("api-idempotent")
            adapter = _BlockingSubmitAdapter()
            bridge = HarnessControlBridge(identity, adapter)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(root / "control", identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            catalog = TerminalCatalog(root / "terminal-sessions.json")
            entry = TerminalCatalogEntry(
                id=identity.ar_session_id,
                label="Worker",
                kind="harness",
                harness="claude",
                lifecycle_id=None,
                cwd=root,
                tmux_name=identity.tmux_name,
                command=("claude",),
                created_at=identity.created_at,
                last_attached_at=identity.created_at,
                status="running",
                control_state="ready",
                control_endpoint=endpoint.path,
                control_protocol=CONTROL_PROTOCOL_VERSION,
            )
            catalog.upsert(entry)
            app = FastAPI()
            register_harness_control_routes(
                app,
                workspace_root=root,
                harness_registry=lambda: (),
                catalog=catalog,
                host=TerminalHost(tmux_probe=lambda _name: True),
                liveness_clock=lambda: datetime(2026, 7, 16, 8, 0, tzinfo=UTC),
                liveness_config=TerminalCatalogLivenessConfig(),
            )
            try:
                with (
                    mock.patch(
                        "agents_remember.serving.harness_control_api.observe_terminal_liveness",
                        return_value=TerminalLivenessObservation(entry, True),
                    ),
                    TestClient(app) as client,
                ):
                    first_call = asyncio.create_task(
                        asyncio.to_thread(
                            client.post,
                            f"/api/terminal/{identity.ar_session_id}/submit",
                            json={"requestId": "same-id", "text": "first payload"},
                        )
                    )
                    await asyncio.wait_for(adapter.submit_started.wait(), timeout=1.0)
                    duplicate_call = asyncio.create_task(
                        asyncio.to_thread(
                            client.post,
                            f"/api/terminal/{identity.ar_session_id}/submit",
                            json={
                                "requestId": "same-id",
                                "text": "ignored replacement",
                            },
                        )
                    )
                    await asyncio.sleep(0)
                    adapter.release_submit.set()
                    first, duplicate = await asyncio.gather(first_call, duplicate_call)
                    reconciled = await asyncio.to_thread(
                        client.post,
                        f"/api/terminal/{identity.ar_session_id}/reconcile",
                        json={"requestId": "same-id"},
                    )

                self.assertEqual((first.status_code, duplicate.status_code), (200, 200))
                self.assertEqual(duplicate.json(), first.json())
                self.assertEqual(reconciled.status_code, 200)
                self.assertEqual(reconciled.json()["state"], "accepted")
                self.assertEqual(reconciled.json()["vendorCorrelationId"], "vendor-same-id")
                self.assertEqual(len(adapter.submissions), 1)
                self.assertEqual(adapter.submissions[0].text, "first payload")
                self.assertEqual(adapter.reconciliation_requests, [])
            finally:
                adapter.release_submit.set()
                await server.close()
                await bridge.stop("forced")

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
