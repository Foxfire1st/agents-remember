"""R10 cross-adapter hosted bridge conformance from ready through shutdown/recovery."""

from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import unittest
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

from agents_remember.errors import HarnessAdapterDisconnectedError, HarnessControlError
from agents_remember.serving.harness_capabilities import CapabilitySnapshot, SetResult
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_client import (
    ControlSubmission,
    read_control_snapshot,
    read_control_transcript,
    reconcile_control_prompt,
    request_control,
    respond_control_interaction,
    stop_control_session,
    submit_control_prompt,
)
from agents_remember.serving.harness_control_ipc import HarnessControlServer, LocalControlEndpoint
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    REQUIRED_ADAPTER_CAPABILITIES,
    AcceptanceState,
    AdapterEvent,
    AdapterHandshake,
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationRef,
    InteractionResponse,
    LaunchSpec,
    PendingInteraction,
    PromptRequest,
    ReconciliationResult,
    SubmissionReceipt,
    TerminalResult,
    TranscriptEntry,
)
from agents_remember.serving.terminal_catalog import TerminalCatalogEntry

HARNESSES = ("claude", "codex", "pi")


class _Adapter:
    def __init__(self, harness_id: str) -> None:
        self.harness_id = harness_id
        self.current: AdapterSnapshot | None = None
        self.events: asyncio.Queue[AdapterEvent | None] = asyncio.Queue()
        self.acceptances: deque[AcceptanceState] = deque()
        self.disconnect_next = False
        self.reconciliations: dict[str, ReconciliationResult] = {}
        self.submissions: list[PromptRequest] = []
        self.responses: list[InteractionResponse] = []
        self.stop_modes: list[str] = []

    async def start(self, launch: LaunchSpec) -> AdapterHandshake:
        self.current = AdapterSnapshot(
            identity=launch.identity,
            control="ready",
            activity="idle",
            acceptance="immediate",
            vendor_session_id=f"{self.harness_id}-vendor-session",
            raw={"harness": self.harness_id},
        )
        return AdapterHandshake(
            protocol_version=CONTROL_PROTOCOL_VERSION,
            adapter_id=f"test:{self.harness_id}",
            identity=launch.identity,
            capabilities=REQUIRED_ADAPTER_CAPABILITIES,
            snapshot=self.current,
        )

    async def snapshot(self) -> AdapterSnapshot:
        assert self.current is not None
        return self.current

    def advertise(self) -> CapabilitySnapshot:
        return CapabilitySnapshot(models=(), selected_model_key=None, selected_effort=None)

    async def _subscribe(self) -> AsyncIterator[AdapterEvent]:
        while True:
            event = await self.events.get()
            if event is None:
                return
            yield event

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        return self._subscribe()

    async def preflight_operation(self, operation: ControlOperationRef) -> None:
        del operation

    async def set_model(
        self, model_key: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del operation
        return SetResult(
            ok=True,
            acceptance="immediate",
            requested_value=model_key,
        )

    async def set_effort(
        self, effort: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del operation
        return SetResult(
            ok=True,
            acceptance="immediate",
            requested_value=effort,
        )

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self.submissions.append(request)
        if self.disconnect_next:
            self.disconnect_next = False
            raise HarnessAdapterDisconnectedError(
                "transport disappeared after write",
                may_have_sent=True,
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
                reconciled_at="2026-07-14T10:00:00+00:00",
            ),
        )

    async def stop(self, mode: str) -> None:
        self.stop_modes.append(mode)

    def emit(self, event: AdapterEvent) -> None:
        if event.snapshot is not None:
            self.current = event.snapshot
        self.events.put_nowait(event)


def _identity(harness_id: str) -> ControlIdentity:
    return ControlIdentity(
        ar_session_id=f"session-{harness_id}",
        tmux_name=f"ar-session-{harness_id}",
        created_at="2026-07-14T09:00:00+00:00",
    )


def _entry(identity: ControlIdentity, endpoint: Path, harness_id: str) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=identity.ar_session_id,
        label=harness_id,
        kind="harness",
        harness=harness_id,
        lifecycle_id=f"L-{harness_id}",
        cwd=Path("/workspace"),
        tmux_name=identity.tmux_name,
        command=(harness_id,),
        created_at=identity.created_at,
        last_attached_at=identity.created_at,
        status="running",
        control_state="starting",
        control_endpoint=endpoint,
        control_protocol=CONTROL_PROTOCOL_VERSION,
    )


class HostedControlConformanceTests(unittest.IsolatedAsyncioTestCase):
    async def _start(self, harness_id: str):
        root = Path(tempfile.mkdtemp())
        identity = _identity(harness_id)
        adapter = _Adapter(harness_id)
        bridge = HarnessControlBridge(identity, adapter)
        endpoint = LocalControlEndpoint.for_session(root, identity)
        server = HarnessControlServer(endpoint, bridge)
        await server.start()
        await bridge.start(
            LaunchSpec(
                identity=identity,
                harness_id=harness_id,
                cwd=Path("/workspace"),
                argv=(harness_id, "protocol-mode"),
            )
        )
        return adapter, bridge, server, _entry(identity, endpoint.path, harness_id), root

    async def test_ready_delivery_blocked_completion_ambiguity_and_shutdown(self) -> None:
        for harness_id in HARNESSES:
            with self.subTest(harness=harness_id):
                adapter, bridge, server, entry, _root = await self._start(harness_id)
                try:
                    snapshot = await asyncio.to_thread(read_control_snapshot, entry)
                    self.assertEqual(snapshot.control, "ready")
                    self.assertEqual(snapshot.vendor_session_id, f"{harness_id}-vendor-session")

                    first = await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        "first",
                        ControlSubmission(source="durable", request_id=f"{harness_id}-immediate"),
                    )
                    second = await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        "second",
                        ControlSubmission(source="durable", request_id=f"{harness_id}-queued"),
                    )
                    self.assertEqual((first.acceptance, second.acceptance), ("immediate", "queued"))
                    first_operation = adapter.submissions[-1].operation
                    assert first_operation is not None

                    assert adapter.current is not None
                    blocked = replace(
                        adapter.current,
                        activity="blocked",
                        acceptance="rejected",
                        pending_interaction=PendingInteraction(
                            interaction_id=f"{harness_id}-approval",
                            kind="approval",
                            prompt="Allow the action?",
                            created_at="2026-07-14T10:01:00+00:00",
                            choices=("allow", "deny"),
                        ),
                    )
                    adapter.emit(
                        AdapterEvent(
                            sequence=1,
                            kind="state",
                            identity=blocked.identity,
                            created_at="2026-07-14T10:01:00+00:00",
                            snapshot=blocked,
                        )
                    )
                    await asyncio.sleep(0)
                    observed = await asyncio.to_thread(read_control_snapshot, entry)
                    self.assertEqual(observed.activity, "blocked")
                    await asyncio.to_thread(
                        respond_control_interaction,
                        entry,
                        interaction_id=f"{harness_id}-approval",
                        response="allow",
                    )
                    self.assertEqual(adapter.responses[-1].response, "allow")
                    self.assertEqual(adapter.responses[-1].operation, first_operation)

                    completed = replace(
                        adapter.current,
                        activity="idle",
                        acceptance="immediate",
                    )
                    adapter.emit(
                        AdapterEvent(
                            sequence=2,
                            kind="completed",
                            identity=completed.identity,
                            created_at="2026-07-14T10:02:00+00:00",
                            snapshot=completed,
                            transcript=(
                                TranscriptEntry(
                                    sequence=1,
                                    role="result",
                                    text="done",
                                    created_at="2026-07-14T10:02:00+00:00",
                                    request_id=f"{harness_id}-immediate",
                                    terminal_result=TerminalResult(
                                        outcome="completed",
                                        completed_at="2026-07-14T10:02:00+00:00",
                                    ),
                                ),
                            ),
                            operation=first_operation,
                        )
                    )
                    while len(adapter.submissions) < 2:
                        await asyncio.sleep(0)
                    transcript = await asyncio.to_thread(read_control_transcript, entry)
                    terminal_result = transcript[-1]["terminalResult"]
                    self.assertIsInstance(terminal_result, dict)
                    assert isinstance(terminal_result, dict)
                    self.assertEqual(terminal_result["outcome"], "completed")

                    second_operation = adapter.submissions[-1].operation
                    assert second_operation is not None
                    adapter.emit(
                        AdapterEvent(
                            sequence=3,
                            kind="completed",
                            identity=completed.identity,
                            created_at="2026-07-14T10:02:30+00:00",
                            snapshot=completed,
                            operation=second_operation,
                        )
                    )
                    await asyncio.sleep(0)

                    adapter.disconnect_next = True
                    unknown = await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        "ambiguous",
                        ControlSubmission(source="durable", request_id=f"{harness_id}-unknown"),
                    )
                    self.assertEqual(unknown.acceptance, "unknown")
                    adapter.reconciliations[unknown.request_id] = ReconciliationResult(
                        request_id=unknown.request_id,
                        state="accepted",
                        reconciled_at="2026-07-14T10:03:00+00:00",
                    )
                    reconciled = await asyncio.to_thread(
                        reconcile_control_prompt, entry, unknown.request_id
                    )
                    self.assertEqual(reconciled.state, "accepted")

                    await asyncio.to_thread(stop_control_session, entry)
                    self.assertEqual(adapter.stop_modes[-1], "graceful")
                finally:
                    await server.close()
                    await bridge.stop("forced")

    async def test_restart_recovery_and_incompatible_protocol(self) -> None:
        for harness_id in HARNESSES:
            _adapter, bridge, server, entry, root = await self._start(harness_id)
            await server.close()
            await bridge.stop("forced")

            restarted = _Adapter(harness_id)
            next_bridge = HarnessControlBridge(_identity(harness_id), restarted)
            endpoint = LocalControlEndpoint.for_session(root, _identity(harness_id))
            next_server = HarnessControlServer(endpoint, next_bridge)
            await next_server.start()
            await next_bridge.start(
                LaunchSpec(
                    identity=_identity(harness_id),
                    harness_id=harness_id,
                    cwd=Path("/workspace"),
                    argv=(harness_id, "protocol-mode"),
                )
            )
            try:
                recovered = await asyncio.to_thread(read_control_snapshot, entry)
                self.assertEqual(recovered.control, "ready")

                def incompatible_request(
                    socket_path: Path = endpoint.path,
                    identity: ControlIdentity = endpoint.identity,
                ) -> dict[str, object]:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.connect(str(socket_path))
                        client.sendall(
                            json.dumps(
                                {
                                    "protocol": "ar-harness-control/v999",
                                    "identity": identity.to_json(),
                                    "action": "snapshot",
                                    "payload": {},
                                }
                            ).encode()
                            + b"\n"
                        )
                        return json.loads(client.recv(4096))

                refused = await asyncio.to_thread(incompatible_request)
                self.assertFalse(refused["ok"])
                self.assertIn("protocol version mismatch", str(refused["error"]))

                malformed_root = Path(tempfile.mkdtemp())
                malformed_path = malformed_root / "malformed.sock"

                async def malformed(
                    _reader: asyncio.StreamReader, writer: asyncio.StreamWriter
                ) -> None:
                    writer.write(b"not-json\n")
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()

                malformed_server = await asyncio.start_unix_server(malformed, path=malformed_path)
                malformed_entry = replace(entry, control_endpoint=malformed_path)
                try:
                    with self.assertRaisesRegex(HarnessControlError, "malformed JSON"):
                        await asyncio.to_thread(request_control, malformed_entry, "snapshot")
                finally:
                    malformed_server.close()
                    await malformed_server.wait_closed()
            finally:
                await next_server.close()
                await next_bridge.stop("forced")
