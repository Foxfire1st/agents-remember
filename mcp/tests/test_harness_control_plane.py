"""Contract tests for the native control-plane substrate."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import tempfile
import unittest
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

from agents_remember.errors import (
    CodexAppServerError,
    CodexAppServerRpcError,
    HarnessAdapterDisconnectedError,
    HarnessBridgeEpochMismatchError,
    HarnessControlError,
    HarnessRequestConflictError,
)
from agents_remember.serving.codex_app_server_adapter import (
    CodexAppServerAdapter,
    CodexAppServerSettings,
)
from agents_remember.serving.harness_capabilities import CapabilitySnapshot, SetResult
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_claude import ClaudeStreamJsonAdapter
from agents_remember.serving.harness_control_client import (
    _interrupt_result,
    _operation_timeline,
    _withdrawal_result,
    interrupt_control,
    read_operation_timeline,
    read_submission_authority,
    set_control_effort,
    set_control_model,
    submit_control_prompt,
    withdraw_control_submission,
)
from agents_remember.serving.harness_control_ipc import HarnessControlServer, LocalControlEndpoint
from agents_remember.serving.harness_control_models import (
    AR_EVIDENCE_KEY,
    CONTROL_PROTOCOL_VERSION,
    EVIDENCE_PAGE_BYTE_BUDGET,
    REQUIRED_ADAPTER_CAPABILITIES,
    AdapterEvent,
    AdapterHandshake,
    AdapterSnapshot,
    AssetReference,
    ControlIdentity,
    ControlOperationRef,
    InteractionResponse,
    InterruptResult,
    LaunchSpec,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
    SubmissionReceipt,
    operation_timeline_item_json,
    operation_timeline_item_wire_bytes,
)
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter

NOW = "2026-07-19T12:00:00+00:00"
CODEX_FIXTURE = Path(__file__).parent / "fixtures" / "codex_app_server_0_144_3.json"


def _identity(session: str = "ar-l2e-1") -> ControlIdentity:
    return ControlIdentity(
        ar_session_id=session,
        tmux_name=f"ar-{session}",
        created_at="2026-07-19T11:00:00+00:00",
    )


def _launch(identity: ControlIdentity, *, harness_id: str = "fake") -> LaunchSpec:
    return LaunchSpec(
        identity=identity,
        harness_id=harness_id,
        cwd=Path("/workspace"),
        argv=("fake-harness", "protocol-mode"),
        env={"PRESERVE_INSTALLED_AUTH": "1"},
    )


def _obj(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return cast(Mapping[str, object], value)


async def _settle(steps: int = 6) -> None:
    for _ in range(steps):
        await asyncio.sleep(0)


async def _drive_completions(adapter, request_ids) -> None:
    """Complete each request only after the adapter actually received its dispatch."""

    for request_id in request_ids:
        for _ in range(400):
            if any(item.request_id == request_id for item in adapter.submissions):
                break
            await asyncio.sleep(0)
        else:
            raise AssertionError(f"adapter never received {request_id}")
        adapter.complete(request_id)


class _ControlledEntry:
    def __init__(self, identity: ControlIdentity, endpoint: Path) -> None:
        self.id = identity.ar_session_id
        self.tmux_name = identity.tmux_name
        self.created_at = identity.created_at
        self.control_endpoint = endpoint


class _PlainAdapter:
    """Minimal protocol adapter with no interrupt/asset capability."""

    def __init__(self) -> None:
        self.current: AdapterSnapshot | None = None
        self.events: asyncio.Queue[AdapterEvent | None] = asyncio.Queue()
        self.event_sequence = 0
        self.submissions: list[PromptRequest] = []
        self.stop_modes: list[ShutdownMode] = []

    async def start(self, launch: LaunchSpec) -> AdapterHandshake:
        self.current = AdapterSnapshot(
            identity=launch.identity,
            control="ready",
            activity="idle",
            acceptance="immediate",
            vendor_session_id="vendor-1",
            raw={"fake": True},
        )
        return AdapterHandshake(
            protocol_version=CONTROL_PROTOCOL_VERSION,
            adapter_id="fake",
            identity=launch.identity,
            capabilities=REQUIRED_ADAPTER_CAPABILITIES,
            snapshot=self.current,
        )

    async def snapshot(self) -> AdapterSnapshot:
        assert self.current is not None
        return self.current

    def advertise(self) -> CapabilitySnapshot:
        return CapabilitySnapshot(models=(), selected_model_key=None, selected_effort=None)

    async def set_model(
        self, model_key: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del model_key, operation
        raise HarnessControlError("unused")

    async def set_effort(
        self, effort: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del effort, operation
        raise HarnessControlError("unused")

    async def preflight_operation(self, operation: ControlOperationRef) -> None:
        del operation

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[AdapterEvent]:
        while True:
            event = await self.events.get()
            if event is None:
                return
            yield event

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self.submissions.append(request)
        return SubmissionReceipt(
            request_id=request.request_id,
            acceptance="immediate",
            submitted_at=request.submitted_at,
            vendor_correlation_id=f"vendor-{request.request_id}",
            accepted_at=request.submitted_at,
        )

    async def respond(self, response: InteractionResponse) -> None:
        del response

    async def reconcile(self, request_id: str) -> ReconciliationResult:
        return ReconciliationResult(request_id=request_id, state="unresolved", reconciled_at=NOW)

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)

    def complete(self, request_id: str) -> None:
        request = next(item for item in self.submissions if item.request_id == request_id)
        assert request.operation is not None
        assert self.current is not None
        self.event_sequence += 1
        completed = replace_snapshot_idle(self.current)
        self.events.put_nowait(
            AdapterEvent(
                sequence=self.event_sequence,
                kind="completed",
                identity=completed.identity,
                created_at=NOW,
                snapshot=completed,
                operation=request.operation,
            )
        )


def replace_snapshot_idle(snapshot: AdapterSnapshot) -> AdapterSnapshot:
    return replace(snapshot, activity="idle", acceptance="immediate")


class _CapableAdapter(_PlainAdapter):
    """Adds interrupt + asset capability with call recording."""

    def __init__(self) -> None:
        super().__init__()
        self.interrupt_calls: list[tuple[str | None, str | None]] = []
        self.asset_submissions: list[PromptRequest] = []
        self.interrupt_error: Exception | None = None
        self.active_operation: ControlOperationRef | None = None

    async def interrupt(
        self,
        *,
        turn_id: str | None,
        expected_operation_id: str | None,
    ) -> InterruptResult:
        self.interrupt_calls.append((turn_id, expected_operation_id))
        if self.interrupt_error is not None:
            raise self.interrupt_error
        return InterruptResult(
            acknowledgement="accepted",
            bridge_epoch="",
            operation=self.active_operation,
            vendor_correlation_id="fake-turn-1",
            detail="fake accepted",
        )

    async def submit_with_assets(self, request: PromptRequest) -> SubmissionReceipt:
        self.asset_submissions.append(request)
        return await self.submit(request)


class InterruptBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def _serve(self, adapter, identity: ControlIdentity, tmp: str):
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
        await bridge.start(_launch(identity))
        endpoint = LocalControlEndpoint.for_session(Path(tmp), identity)
        server = HarnessControlServer(endpoint, bridge)
        await server.start()
        return bridge, server, _ControlledEntry(identity, endpoint.path)

    async def test_interrupt_round_trip_epoch_stamp_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("interrupt-ipc")
            adapter = _CapableAdapter()
            operation = ControlOperationRef(
                bridge_epoch="e", sequence=1, operation_id="op-1", kind="prompt"
            )
            adapter.active_operation = operation
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                result = await asyncio.to_thread(
                    interrupt_control,
                    entry,
                    expected_bridge_epoch=descriptor.bridge_epoch,
                    turn_id="turn-1",
                    expected_operation_id="op-1",
                )
                self.assertEqual(result.acknowledgement, "accepted")
                self.assertEqual(result.bridge_epoch, descriptor.bridge_epoch)
                self.assertEqual(result.operation, operation)
                self.assertEqual(result.vendor_correlation_id, "fake-turn-1")
                self.assertEqual(adapter.interrupt_calls, [("turn-1", "op-1")])
                with self.assertRaises(HarnessBridgeEpochMismatchError):
                    await asyncio.to_thread(
                        interrupt_control,
                        entry,
                        expected_bridge_epoch="not-the-epoch",
                    )
                self.assertEqual(len(adapter.interrupt_calls), 1)
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_interrupt_adapter_mint_epoch_refused(self) -> None:
        class _MintingAdapter(_CapableAdapter):
            async def interrupt(self, *, turn_id, expected_operation_id) -> InterruptResult:
                del turn_id, expected_operation_id
                return InterruptResult(
                    acknowledgement="accepted",
                    bridge_epoch="minted-by-adapter",
                    vendor_correlation_id="fake-turn-1",
                )

        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("interrupt-mint")
            adapter = _MintingAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                with self.assertRaises(HarnessControlError) as caught:
                    await asyncio.to_thread(
                        interrupt_control,
                        entry,
                        expected_bridge_epoch=descriptor.bridge_epoch,
                    )
                self.assertIn("must not mint the bridge epoch", str(caught.exception))
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_interrupt_unsupported_fails_typed_naming_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("interrupt-unsupported")
            adapter = _PlainAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                with self.assertRaises(HarnessControlError) as caught:
                    await asyncio.to_thread(
                        interrupt_control,
                        entry,
                        expected_bridge_epoch=descriptor.bridge_epoch,
                    )
                self.assertIn("_PlainAdapter", str(caught.exception))
                self.assertIn("does not support native interrupt", str(caught.exception))
            finally:
                await server.close()
                await bridge.stop("forced")


# ---------------------------------------------------------------------------
# Codex interrupt (fake transport)
# ---------------------------------------------------------------------------


def _codex_fixture() -> dict[str, object]:
    return json.loads(CODEX_FIXTURE.read_text(encoding="utf-8"))


def _fixture_object(data: Mapping[str, object], *path: str) -> dict[str, object]:
    value: object = data
    for key in path:
        assert isinstance(value, dict)
        value = value[key]
    assert isinstance(value, dict)
    return value


class _FakeCodexTransport:
    def __init__(self) -> None:
        self.responses: dict[str, list[dict[str, object] | Exception]] = {}
        self.requests: list[tuple[str, dict[str, object]]] = []
        self.incoming: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        self.stop_modes: list[ShutdownMode] = []

    def queue(self, method: str, response: Mapping[str, object] | Exception) -> None:
        self.responses.setdefault(method, []).append(
            response if isinstance(response, Exception) else deepcopy(dict(response))
        )

    async def start(self, launch: LaunchSpec) -> None:
        del launch

    async def request(self, method, params, *, before_write=None):
        if before_write is not None:
            before_write()
        self.requests.append((method, dict(params)))
        response = self.responses[method].pop(0)
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)

    async def notify(self, method, params) -> None:
        del method, params

    def messages(self) -> AsyncIterator[dict[str, object]]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[dict[str, object]]:
        while True:
            message = await self.incoming.get()
            if message is None:
                return
            yield message

    async def respond(self, request_id, result) -> None:
        del request_id, result

    async def respond_error(self, request_id, *, code, message) -> None:
        del request_id, code, message

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)
        self.incoming.put_nowait(None)

    def emit(self, message: Mapping[str, object]) -> None:
        self.incoming.put_nowait(deepcopy(dict(message)))


def _codex_adapter(transport: _FakeCodexTransport) -> CodexAppServerAdapter:
    settings = CodexAppServerSettings(
        reasoning_effort="xhigh",
        model="gpt-5.6-sol",
        ephemeral=True,
    )
    return CodexAppServerAdapter(
        settings,
        transport_factory=lambda: transport,
        clock=lambda: NOW,
    )


def _prime_codex_start(transport: _FakeCodexTransport) -> None:
    data = _codex_fixture()
    transport.queue("initialize", _fixture_object(data, "initializeResult"))
    transport.queue("model/list", _fixture_object(data, "modelListResult"))
    transport.queue("thread/start", _fixture_object(data, "threadStartResult"))


async def _codex_active_turn(transport: _FakeCodexTransport, adapter: CodexAppServerAdapter) -> str:
    data = _codex_fixture()
    turn_result = deepcopy(_fixture_object(data, "turnStartResult"))
    _fixture_object(turn_result, "turn")["id"] = "turn-1"
    _fixture_object(turn_result, "turn")["status"] = "inProgress"
    transport.queue("turn/start", turn_result)
    operation = ControlOperationRef(
        bridge_epoch="codex-test-epoch", sequence=1, operation_id="req-1", kind="prompt"
    )
    await adapter.preflight_operation(operation)
    receipt = await adapter.submit(
        PromptRequest(
            request_id="req-1",
            source="durable",
            text="work",
            submitted_at=NOW,
            operation=operation,
        )
    )
    assert receipt.acceptance == "immediate"
    return "turn-1"


class CodexInterruptTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupt_write_replay_and_guards(self) -> None:
        transport = _FakeCodexTransport()
        _prime_codex_start(transport)
        adapter = _codex_adapter(transport)
        await adapter.start(_launch(_identity("codex-int"), harness_id="codex"))
        try:
            await _codex_active_turn(transport, adapter)
            transport.queue("turn/interrupt", {})
            result = await adapter.interrupt(turn_id="turn-1", expected_operation_id=None)
            self.assertEqual(result.acknowledgement, "accepted")
            self.assertEqual(result.vendor_correlation_id, "turn-1")
            self.assertEqual(result.bridge_epoch, "")
            self.assertEqual(
                [request for request in transport.requests if request[0] == "turn/interrupt"],
                [("turn/interrupt", {"threadId": "thread-1", "turnId": "turn-1"})],
            )
            self.assertEqual(result.operation.operation_id, "req-1")  # type: ignore[union-attr]
            replay = await adapter.interrupt(turn_id="turn-1", expected_operation_id=None)
            self.assertEqual(replay, result)
            self.assertEqual(
                len([request for request in transport.requests if request[0] == "turn/interrupt"]),
                1,
            )
            with self.assertRaises(CodexAppServerError):
                await adapter.interrupt(turn_id="turn-other", expected_operation_id=None)
            self.assertEqual(
                len([request for request in transport.requests if request[0] == "turn/interrupt"]),
                1,
            )
        finally:
            await adapter.stop("forced")

    async def test_interrupt_no_active_turn_typed(self) -> None:
        transport = _FakeCodexTransport()
        _prime_codex_start(transport)
        adapter = _codex_adapter(transport)
        await adapter.start(_launch(_identity("codex-int-idle"), harness_id="codex"))
        try:
            with self.assertRaises(CodexAppServerError):
                await adapter.interrupt(turn_id=None, expected_operation_id=None)
            self.assertEqual(
                [request for request in transport.requests if request[0] == "turn/interrupt"],
                [],
            )
        finally:
            await adapter.stop("forced")

    async def test_interrupt_rpc_failure_is_rejected_acknowledgement(self) -> None:
        transport = _FakeCodexTransport()
        _prime_codex_start(transport)
        transport.queue(
            "turn/interrupt", CodexAppServerRpcError("turn/interrupt", -32600, "bad interrupt")
        )
        adapter = _codex_adapter(transport)
        await adapter.start(_launch(_identity("codex-int-rpc"), harness_id="codex"))
        try:
            await _codex_active_turn(transport, adapter)
            result = await adapter.interrupt(turn_id=None, expected_operation_id=None)
            self.assertEqual(result.acknowledgement, "rejected")
            self.assertIn("bad interrupt", result.detail or "")
        finally:
            await adapter.stop("forced")


# ---------------------------------------------------------------------------
# Pi interrupt (fake transport)
# ---------------------------------------------------------------------------


class _FakePiTransport:
    def __init__(self, entries: list[dict[str, object]] | None = None) -> None:
        self.entries = entries or []
        self.commands: list[dict[str, object]] = []
        self.event_queue: asyncio.Queue[Mapping[str, object] | None] = asyncio.Queue()
        self.stop_modes: list[ShutdownMode] = []
        self.abort_results: list[bool] = []

    async def start(self, launch: LaunchSpec) -> None:
        del launch

    @property
    def event_token(self) -> int:
        return 0

    async def request(self, command, *, before_write=None):
        if before_write is not None:
            before_write()
        copied = dict(command)
        self.commands.append(copied)
        request_id = copied["id"]
        command_type = copied["type"]
        if command_type == "get_state":
            return _pi_success(
                request_id,
                "get_state",
                {
                    "sessionId": "pi-session-1",
                    "sessionFile": "/sessions/pi-session-1.jsonl",
                    "isStreaming": False,
                    "isCompacting": False,
                    "pendingMessageCount": 0,
                    "thinkingLevel": "high",
                    "model": {"provider": "anthropic", "id": "claude-test"},
                },
            )
        if command_type == "get_available_models":
            return _pi_success(
                request_id,
                "get_available_models",
                {
                    "models": [
                        {
                            "id": "claude-test",
                            "name": "Claude Test",
                            "provider": "anthropic",
                            "reasoning": True,
                            "thinkingLevelMap": {"high": "high"},
                        }
                    ]
                },
            )
        if command_type == "get_entries":
            return _pi_success(
                request_id,
                "get_entries",
                {
                    "entries": list(self.entries),
                    "leafId": self.entries[-1]["id"] if self.entries else None,
                },
            )
        if command_type == "prompt":
            return {"id": request_id, "type": "response", "command": "prompt", "success": True}
        if command_type == "abort":
            success = self.abort_results.pop(0) if self.abort_results else True
            return {
                "id": request_id,
                "type": "response",
                "command": "abort",
                "success": success,
                **({} if success else {"error": "nothing to abort"}),
            }
        raise AssertionError(f"unexpected fake pi command: {command_type}")

    async def send(self, command, *, before_write=None) -> None:
        del command, before_write

    def events(self) -> AsyncIterator[Mapping[str, object]]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[Mapping[str, object]]:
        while True:
            frame = await self.event_queue.get()
            if frame is None:
                raise HarnessAdapterDisconnectedError("fake pi stopped", may_have_sent=False)
            yield frame

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)
        self.event_queue.put_nowait(None)

    def emit(self, frame: Mapping[str, object]) -> None:
        self.event_queue.put_nowait(frame)


def _pi_success(request_id: str, command: str, data: object) -> dict[str, object]:
    return {"id": request_id, "type": "response", "command": command, "success": True, "data": data}


async def _pi_active_operation(adapter: PiRpcAdapter, operation_id: str) -> ControlOperationRef:
    operation = ControlOperationRef(
        bridge_epoch="pi-test-epoch", sequence=1, operation_id=operation_id, kind="prompt"
    )
    await adapter.preflight_operation(operation)
    receipt = await adapter.submit(
        PromptRequest(
            request_id=operation_id,
            source="durable",
            text="work",
            submitted_at=NOW,
            operation=operation,
        )
    )
    assert receipt.acceptance == "immediate"
    return operation


class PiInterruptTests(unittest.IsolatedAsyncioTestCase):
    async def test_abort_write_guard_replay_and_successor_refusal(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=lambda: transport, clock=lambda: NOW)
        await adapter.start(_launch(_identity("pi-int"), harness_id="pi"))
        try:
            operation = await _pi_active_operation(adapter, "op-a")
            result = await adapter.interrupt(turn_id=None, expected_operation_id="op-a")
            self.assertEqual(result.acknowledgement, "accepted")
            self.assertEqual(result.operation, operation)
            aborts = [command for command in transport.commands if command["type"] == "abort"]
            self.assertEqual(len(aborts), 1)
            self.assertEqual(result.vendor_correlation_id, aborts[0]["id"])
            # Matching replay: same pair, first acknowledgement, zero additional writes.
            replay = await adapter.interrupt(turn_id=None, expected_operation_id="op-a")
            self.assertEqual(replay, result)
            self.assertEqual(
                len([command for command in transport.commands if command["type"] == "abort"]), 1
            )
            # Mismatch guard: typed before any write.
            with self.assertRaises(HarnessControlError):
                await adapter.interrupt(turn_id=None, expected_operation_id="op-other")
            with self.assertRaises(HarnessControlError):
                await adapter.interrupt(turn_id="turn-1", expected_operation_id=None)
            self.assertEqual(
                len([command for command in transport.commands if command["type"] == "abort"]), 1
            )
            # Operation A settles; successor B starts. A stale reconcile for A must fail
            # typed with ZERO additional abort writes (the successor-operation rule).
            events = adapter.subscribe()
            transport.emit({"type": "agent_settled"})
            settled = await asyncio.wait_for(anext(events), timeout=1.0)
            self.assertEqual(settled.kind, "completed")
            self.assertIsNone(adapter.active_operation)
            await _pi_active_operation(adapter, "op-b")
            with self.assertRaises(HarnessControlError):
                await adapter.interrupt(turn_id=None, expected_operation_id="op-a")
            self.assertEqual(
                len([command for command in transport.commands if command["type"] == "abort"]), 1
            )
            # A matching reconcile against the still-active successor writes exactly once.
            second = await adapter.interrupt(turn_id=None, expected_operation_id="op-b")
            self.assertEqual(second.acknowledgement, "accepted")
            self.assertEqual(
                len([command for command in transport.commands if command["type"] == "abort"]), 2
            )
            replay_b = await adapter.interrupt(turn_id=None, expected_operation_id="op-b")
            self.assertEqual(replay_b, second)
            self.assertEqual(
                len([command for command in transport.commands if command["type"] == "abort"]), 2
            )
        finally:
            await adapter.stop("forced")

    async def test_abort_no_active_operation_typed(self) -> None:
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=lambda: transport, clock=lambda: NOW)
        await adapter.start(_launch(_identity("pi-int-idle"), harness_id="pi"))
        try:
            with self.assertRaises(HarnessControlError):
                await adapter.interrupt(turn_id=None, expected_operation_id=None)
            self.assertEqual(
                [command for command in transport.commands if command["type"] == "abort"], []
            )
        finally:
            await adapter.stop("forced")

    async def test_contentless_message_end_crosses_as_evidence_without_failing(self) -> None:
        """An interrupted turn's textless message_end mints no entry and no failure."""

        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=lambda: transport, clock=lambda: NOW)
        await adapter.start(_launch(_identity("pi-aborted-msg"), harness_id="pi"))
        events = adapter.subscribe()
        try:
            transport.emit(
                {
                    "type": "message_end",
                    "message": {"role": "assistant", "content": []},
                }
            )
            event = await asyncio.wait_for(anext(events), timeout=1.0)
            self.assertEqual(event.kind, "pi:message_end")
            self.assertEqual(event.transcript, ())
            self.assertEqual(_obj(event.raw[AR_EVIDENCE_KEY])["type"], "message_end")
            self.assertEqual(_obj(event.raw["piEvent"])["type"], "message_end")
            self.assertEqual((await adapter.snapshot()).control, "ready")
            transport.emit({"type": "message_end", "message": {"role": "system", "content": []}})
            failure = await asyncio.wait_for(anext(events), timeout=1.0)
            self.assertEqual(failure.kind, "failed")
            assert failure.snapshot is not None
            self.assertIn("user or assistant role", str(failure.snapshot.raw.get("adapterError")))
        finally:
            await adapter.stop("forced")

    async def test_abort_native_failure_is_rejected_acknowledgement(self) -> None:
        transport = _FakePiTransport()
        transport.abort_results.append(False)
        adapter = PiRpcAdapter(transport_factory=lambda: transport, clock=lambda: NOW)
        await adapter.start(_launch(_identity("pi-int-rpc"), harness_id="pi"))
        try:
            await _pi_active_operation(adapter, "op-c")
            result = await adapter.interrupt(turn_id=None, expected_operation_id="op-c")
            self.assertEqual(result.acknowledgement, "rejected")
            self.assertIn("nothing to abort", result.detail or "")
        finally:
            await adapter.stop("forced")


# ---------------------------------------------------------------------------
# Claude interrupt (REAL stream-json adapter, fake transport, bridge + IPC)
# ---------------------------------------------------------------------------


_CLAUDE_FIXTURES = Path(__file__).parent / "fixtures" / "claude_stream_json"
_CLAUDE_SESSION = "11111111-1111-4111-8111-111111111111"


def _claude_fixture(version: str, name: str) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in (_CLAUDE_FIXTURES / version / name).read_text().splitlines()
    ]


def _claude_replay(written: Mapping[str, object]) -> dict[str, object]:
    return {**written, "isReplay": True, "session_id": _CLAUDE_SESSION, "timestamp": NOW}


class _FakeClaudeTransport:
    def __init__(self, frames: list[dict[str, object]] | None = None) -> None:
        self.frames: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        for frame in frames or []:
            self.frames.put_nowait(frame)
        self.writes: list[dict[str, object]] = []
        self.stop_modes: list[ShutdownMode] = []
        self._returncode: int | None = None
        self._write_event = asyncio.Event()

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def start(self, argv, *, cwd, env) -> None:
        del argv, cwd, env

    async def read_frame(self) -> dict[str, object] | None:
        return await self.frames.get()

    async def write_frame(self, frame, *, before_write=None) -> None:
        if before_write is not None:
            before_write()
        self.writes.append(dict(frame))
        self._write_event.set()

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)
        self._returncode = 0
        self.frames.put_nowait(None)

    def feed(self, frame: Mapping[str, object]) -> None:
        self.frames.put_nowait(dict(frame))

    async def wait_for_writes(self, count: int) -> None:
        while len(self.writes) < count:
            self._write_event.clear()
            if len(self.writes) < count:
                await asyncio.wait_for(self._write_event.wait(), timeout=1.0)


class ClaudeInterruptTests(unittest.IsolatedAsyncioTestCase):
    """The REAL claude adapter behind the bridge + IPC interrupt route (probe-locked shape)."""

    async def _serve(self, adapter, identity: ControlIdentity, tmp: str):
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
        await bridge.start(_launch(identity, harness_id="claude"))
        endpoint = LocalControlEndpoint.for_session(Path(tmp), identity)
        server = HarnessControlServer(endpoint, bridge)
        await server.start()
        return bridge, server, _ControlledEntry(identity, endpoint.path)

    async def _active_turn(self, transport: _FakeClaudeTransport, bridge) -> None:
        submission = asyncio.create_task(
            bridge.submit(
                bridge.prompt("write an essay", source="terminal", request_id="req-int-1")
            )
        )
        await transport.wait_for_writes(4)
        transport.feed(_claude_replay(transport.writes[3]))
        receipt = await asyncio.wait_for(submission, timeout=1.0)
        assert receipt.acceptance == "immediate"

    async def test_interrupt_routes_through_bridge_and_ipc_and_settles_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("claude-int-ipc")
            transport = _FakeClaudeTransport(_claude_fixture("2.1.210", "initialization.jsonl"))
            adapter = ClaudeStreamJsonAdapter(
                transport_factory=lambda: transport, clock=lambda: NOW
            )
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                await self._active_turn(transport, bridge)
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                interrupt_task = asyncio.create_task(
                    asyncio.to_thread(
                        interrupt_control,
                        entry,
                        expected_bridge_epoch=descriptor.bridge_epoch,
                    )
                )
                await transport.wait_for_writes(5)
                self.assertEqual(
                    transport.writes[4],
                    {
                        "type": "control_request",
                        "request_id": "ar-claude-interrupt-1",
                        "request": {"subtype": "interrupt"},
                    },
                )
                control_response, aborted, marker, result = _claude_fixture(
                    "2.1.217", "interrupt.jsonl"
                )
                transport.feed(control_response)
                acknowledgement = await asyncio.wait_for(interrupt_task, timeout=5.0)
                self.assertEqual(acknowledgement.acknowledgement, "accepted")
                self.assertEqual(acknowledgement.bridge_epoch, descriptor.bridge_epoch)
                self.assertEqual(acknowledgement.vendor_correlation_id, "ar-claude-interrupt-1")
                assert acknowledgement.operation is not None
                self.assertEqual(acknowledgement.operation.kind, "prompt")

                transport.feed(aborted)
                transport.feed(marker)
                transport.feed(result)
                await _settle()
                results = [entry for entry in bridge.transcript() if entry.role == "result"]
                self.assertEqual(len(results), 1)
                assert results[0].terminal_result is not None
                self.assertEqual(results[0].terminal_result.outcome, "cancelled")
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_interrupt_without_an_active_turn_fails_typed_over_ipc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("claude-int-idle")
            transport = _FakeClaudeTransport(_claude_fixture("2.1.210", "initialization.jsonl"))
            adapter = ClaudeStreamJsonAdapter(
                transport_factory=lambda: transport, clock=lambda: NOW
            )
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                with self.assertRaisesRegex(HarnessControlError, "no active Claude turn"):
                    await asyncio.to_thread(
                        interrupt_control,
                        entry,
                        expected_bridge_epoch=descriptor.bridge_epoch,
                    )
                self.assertEqual(len(transport.writes), 3)
            finally:
                await server.close()
                await bridge.stop("forced")


# ---------------------------------------------------------------------------
# Operation timeline (bridge + IPC + validated client)
# ---------------------------------------------------------------------------


class _SetterAdapter(_CapableAdapter):
    async def set_model(
        self, model_key: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del operation
        return SetResult(
            ok=True, acceptance="immediate", requested_value=model_key, detail="fake model"
        )

    async def set_effort(
        self, effort: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del operation
        return SetResult(
            ok=True, acceptance="immediate", requested_value=effort, detail="fake effort"
        )


class OperationTimelineTests(unittest.IsolatedAsyncioTestCase):
    async def _serve(self, adapter, identity: ControlIdentity, tmp: str, **bridge_kwargs):
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW, **bridge_kwargs)
        await bridge.start(_launch(identity))
        endpoint = LocalControlEndpoint.for_session(Path(tmp), identity)
        server = HarnessControlServer(endpoint, bridge)
        await server.start()
        return bridge, server, _ControlledEntry(identity, endpoint.path)

    async def test_all_sources_and_kinds_enumerate_never_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("timeline-all")
            adapter = _SetterAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "cockpit body",
                    source="cockpit",
                    request_id="tl-cockpit",
                    expected_bridge_epoch=epoch,
                )
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "terminal body",
                    source="terminal",
                    request_id="tl-terminal",
                )
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "durable body",
                    source="durable",
                    request_id="tl-durable",
                )
                await _drive_completions(adapter, ["tl-cockpit", "tl-terminal", "tl-durable"])
                await asyncio.to_thread(set_control_model, entry, "model-b")
                await asyncio.to_thread(set_control_effort, entry, "max")
                page = await asyncio.to_thread(
                    read_operation_timeline, entry, expected_bridge_epoch=epoch
                )
                self.assertEqual(page.bridge_epoch, epoch)
                self.assertEqual(len(page.items), 5)
                kinds = {item.operation_id: item.kind for item in page.items}
                self.assertEqual(kinds["tl-cockpit"], "prompt")
                setter_kinds = {item.kind for item in page.items if item.kind != "prompt"}
                self.assertEqual(setter_kinds, {"set-model", "set-effort"})
                sources = {item.operation_id: item.source for item in page.items}
                self.assertEqual(sources["tl-cockpit"], "cockpit")
                self.assertEqual(sources["tl-terminal"], "terminal")
                self.assertEqual(sources["tl-durable"], "durable")
                setter_sources = {item.source for item in page.items if item.kind != "prompt"}
                self.assertEqual(setter_sources, {None})
                sequences = [item.sequence for item in page.items]
                self.assertEqual(sequences, sorted(sequences))
                self.assertEqual(page.latest_sequence, max(sequences))
                # Never bodies: exact ten-key shape, no text, no setter values.
                for item in page.items:
                    serialized = operation_timeline_item_json(item)
                    self.assertEqual(
                        set(serialized),
                        {
                            "operationId",
                            "kind",
                            "source",
                            "state",
                            "sequence",
                            "submittedAt",
                            "updatedAt",
                            "acceptedAt",
                            "payloadDigestPresent",
                            "vendorCorrelationId",
                        },
                    )
                prompt_states = {item.operation_id: item.state for item in page.items[:3]}
                self.assertTrue(
                    all(
                        state in {"delivered", "queued", "dispatching"}
                        for state in prompt_states.values()
                    )
                )
                self.assertTrue(all(item.payload_digest_present for item in page.items[:3]))
                self.assertFalse(any(item.payload_digest_present is None for item in page.items))
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_paged_union_no_overlap_gap_tolerant_and_epoch_flip_typed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("timeline-page")
            adapter = _SetterAdapter()
            bridge, server, entry = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                for index in range(12):
                    await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        f"body-{index}-{'y' * 200}",
                        source="durable",
                        request_id=f"tl-p-{index}",
                    )
                chained: list[int] = []
                after = 0
                truncated_pages = 0
                for _ in range(20):
                    page = await asyncio.to_thread(
                        read_operation_timeline,
                        entry,
                        expected_bridge_epoch=epoch,
                        after_sequence=after,
                        limit=3,
                    )
                    chained.extend(item.sequence for item in page.items)
                    if not page.truncated:
                        break
                    truncated_pages += 1
                    after = page.items[-1].sequence
                else:
                    self.fail("paged enumeration never terminated")
                self.assertEqual(truncated_pages, 3)
                self.assertEqual(len(chained), len(set(chained)))
                self.assertEqual(chained, list(range(1, 13)))
                with self.assertRaises(HarnessBridgeEpochMismatchError):
                    await asyncio.to_thread(
                        read_operation_timeline,
                        entry,
                        expected_bridge_epoch="not-the-epoch",
                    )
                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(
                        read_operation_timeline,
                        entry,
                        expected_bridge_epoch=epoch,
                        after_sequence="opaque-cursor",  # type: ignore[arg-type]
                    )
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_eviction_floor_disclosed_and_rereads_converge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("timeline-evict")
            adapter = _SetterAdapter()
            bridge, server, entry = await self._serve(
                adapter, identity, tmp, submission_limit=4, queue_limit=4
            )
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                ids = [f"tl-e-{index}" for index in range(9)]
                for request_id in ids:
                    await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        f"evict-{request_id}",
                        source="durable",
                        request_id=request_id,
                    )
                    await _drive_completions(adapter, [request_id])
                page = await asyncio.to_thread(
                    read_operation_timeline, entry, expected_bridge_epoch=epoch
                )
                self.assertGreater(page.evicted_before_sequence, 0)
                self.assertGreaterEqual(page.latest_sequence, 9)
                retained = [item.sequence for item in page.items]
                self.assertTrue(
                    all(sequence > page.evicted_before_sequence for sequence in retained)
                )
                # A re-read from zero converges to the current retained union; the floor
                # honestly discloses the jump instead of pretending completeness.
                reread = await asyncio.to_thread(
                    read_operation_timeline,
                    entry,
                    expected_bridge_epoch=epoch,
                    after_sequence=0,
                )
                self.assertEqual([item.sequence for item in reread.items], retained)
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_full_ledger_budget_edge_pages_within_measured_budget(self) -> None:
        with tempfile.TemporaryDirectory():
            identity = _identity("timeline-budget")
            adapter = _SetterAdapter()
            bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
            await bridge.start(_launch(identity))
            try:
                epoch = bridge.submission_authority().bridge_epoch
                ids = [
                    f"caller-minted-operation-id-{index:03d}-" + "x" * 64 for index in range(256)
                ]
                for request_id in ids:
                    await bridge.submit(
                        bridge.prompt(
                            f"payload-{request_id[:12]}-{'z' * 24}",
                            source="durable",
                            request_id=request_id,
                        )
                    )
                    await _drive_completions(adapter, [request_id])
                chained: list[int] = []
                after = 0
                pages = 0
                worst_page_bytes = 0
                while True:
                    page = await bridge.operation_timeline(
                        epoch,
                        after_sequence=after,
                        byte_budget=EVIDENCE_PAGE_BYTE_BUDGET,
                    )
                    pages += 1
                    page_bytes = sum(
                        operation_timeline_item_wire_bytes(item) for item in page.items
                    )
                    worst_page_bytes = max(worst_page_bytes, page_bytes)
                    chained.extend(item.sequence for item in page.items)
                    if not page.truncated:
                        break
                    after = page.items[-1].sequence
                    if pages > 64:
                        self.fail("budget-paged enumeration never terminated")
                self.assertEqual(chained, list(range(1, 257)))
                self.assertGreater(pages, 1)
                self.assertLessEqual(worst_page_bytes, EVIDENCE_PAGE_BYTE_BUDGET)
            finally:
                await bridge.stop("forced")


# ---------------------------------------------------------------------------
# Asset channel (schema, confinement, verification, construction, idempotence)
# ---------------------------------------------------------------------------


def _stage_asset(root: Path, request_id: str, asset_id: str, data: bytes) -> dict[str, object]:
    target = root / "assets" / request_id / asset_id
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {
        "assetId": asset_id,
        "mimeType": "image/png",
        "byteSize": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


class AssetChannelTests(unittest.IsolatedAsyncioTestCase):
    async def _serve(self, adapter, identity: ControlIdentity, tmp: str):
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
        await bridge.start(_launch(identity))
        endpoint = LocalControlEndpoint.for_session(Path(tmp), identity)
        server = HarnessControlServer(endpoint, bridge)
        await server.start()
        return bridge, server, _ControlledEntry(identity, endpoint.path), endpoint

    async def test_schema_battery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("asset-schema")
            adapter = _CapableAdapter()
            bridge, server, entry, _endpoint = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                staged = _stage_asset(Path(tmp), "req-ok", "a1", b"png-bytes")
                base: dict[str, object] = {
                    "text": "with asset",
                    "source": "cockpit",
                    "request_id": "req-ok",
                    "expected_bridge_epoch": epoch,
                }

                async def submit_with(**overrides: object):
                    kwargs = dict(base)
                    kwargs.update(overrides)
                    return await asyncio.to_thread(submit_control_prompt, entry, **kwargs)  # type: ignore[arg-type]  # type: ignore[arg-type]

                ok = await submit_with(assets=[staged])
                self.assertEqual(ok.acceptance, "immediate")
                self.assertEqual(
                    [ref.asset_id for ref in adapter.asset_submissions[0].assets], ["a1"]
                )
                bad_cases = [
                    {"assets": "not-a-list"},
                    {"assets": []},
                    {"assets": [staged] * 2},
                    {"assets": ["not-an-object"]},
                    {"assets": [{**staged, "mimeType": "application/pdf"}]},
                    {"assets": [{**staged, "byteSize": 0}]},
                    {"assets": [{**staged, "byteSize": 6 * 1024 * 1024}]},
                    {"assets": [{**staged, "byteSize": "big"}]},
                    {"assets": [{**staged, "sha256": "zz" * 32}]},
                    {"assets": [{**staged, "sha256": "AB" * 32}]},
                    {"assets": [staged, staged]},
                    {"assets": [{**staged, "byteSize": cast(int, staged["byteSize"]) + 1}]},
                    {"assets": [{**staged, "sha256": "0" * 64}]},
                ]
                for index, overrides in enumerate(bad_cases):
                    with self.subTest(case=index), self.assertRaises(HarnessControlError):
                        await submit_with(request_id=f"req-bad-{index}", **overrides)
                self.assertEqual(len(adapter.asset_submissions), 1)
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_traversal_battery_either_component(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("asset-traversal")
            adapter = _CapableAdapter()
            bridge, server, entry, _endpoint = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                staged = _stage_asset(Path(tmp), "req-trav", "a1", b"png")
                bad_asset_ids = [
                    "../escape",
                    "/etc/passwd",
                    "a/b",
                    "a\\b",
                    ".",
                    "..",
                    "x" * 256,
                    "nul\0id",
                ]
                for bad in bad_asset_ids:
                    with self.subTest(assetId=bad), self.assertRaises(HarnessControlError):
                        await asyncio.to_thread(
                            submit_control_prompt,
                            entry,
                            "trav",
                            source="cockpit",
                            request_id="req-trav",
                            expected_bridge_epoch=epoch,
                            assets=[{**staged, "assetId": bad}],
                        )
                bad_request_ids = ["../escape", "a/b", ".", "..", "x" * 256]
                for bad in bad_request_ids:
                    with self.subTest(requestId=bad), self.assertRaises(HarnessControlError):
                        await asyncio.to_thread(
                            submit_control_prompt,
                            entry,
                            "trav",
                            source="cockpit",
                            request_id=bad,
                            expected_bridge_epoch=epoch,
                            assets=[staged],
                        )
                self.assertEqual(adapter.asset_submissions, [])
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_digest_and_size_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("asset-verify")
            adapter = _CapableAdapter()
            bridge, server, entry, _endpoint = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                staged = _stage_asset(Path(tmp), "req-v", "a1", b"real-png-bytes")
                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        "v",
                        source="cockpit",
                        request_id="req-v",
                        expected_bridge_epoch=epoch,
                        assets=[{**staged, "byteSize": cast(int, staged["byteSize"]) + 3}],
                    )
                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        "v",
                        source="cockpit",
                        request_id="req-v",
                        expected_bridge_epoch=epoch,
                        assets=[{**staged, "sha256": "f" * 64}],
                    )
                missing = dict(staged)
                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        "v",
                        source="cockpit",
                        request_id="req-missing",
                        expected_bridge_epoch=epoch,
                        assets=[missing],
                    )
                self.assertEqual(adapter.asset_submissions, [])
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_non_capable_adapter_returns_unsupported_and_timeline_marks_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("asset-unsupported")
            adapter = _PlainAdapter()
            bridge, server, entry, _endpoint = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                staged = _stage_asset(Path(tmp), "req-u", "a1", b"png")
                receipt = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "u",
                    source="cockpit",
                    request_id="req-u",
                    expected_bridge_epoch=epoch,
                    assets=[staged],
                )
                self.assertEqual(receipt.acceptance, "unsupported")
                self.assertIn("asset submissions", receipt.detail or "")
                page = await asyncio.to_thread(
                    read_operation_timeline, entry, expected_bridge_epoch=epoch
                )
                record = next(item for item in page.items if item.operation_id == "req-u")
                self.assertEqual(record.state, "unsupported")
                self.assertEqual(adapter.submissions, [])
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_idempotence_digest_covers_asset_identity_only_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("asset-idem")
            adapter = _CapableAdapter()
            bridge, server, entry, _endpoint = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                first = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "same text",
                    source="cockpit",
                    request_id="req-idem",
                    expected_bridge_epoch=epoch,
                )
                replay = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "same text",
                    source="cockpit",
                    request_id="req-idem",
                    expected_bridge_epoch=epoch,
                )
                self.assertEqual(replay.acceptance, first.acceptance)
                await _drive_completions(adapter, ["req-idem"])
                # Same text, now with an asset: conflict, never a silent dedupe.
                staged = _stage_asset(Path(tmp), "req-idem", "a1", b"png")
                with self.assertRaises(HarnessRequestConflictError):
                    await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        "same text",
                        source="cockpit",
                        request_id="req-idem",
                        expected_bridge_epoch=epoch,
                        assets=[staged],
                    )
                # Identical replay with the same asset set dedupes honestly.
                staged_b = _stage_asset(Path(tmp), "req-idem-b", "a1", b"png-b")
                first_b = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "asset text",
                    source="cockpit",
                    request_id="req-idem-b",
                    expected_bridge_epoch=epoch,
                    assets=[staged_b],
                )
                replay_b = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "asset text",
                    source="cockpit",
                    request_id="req-idem-b",
                    expected_bridge_epoch=epoch,
                    assets=[staged_b],
                )
                self.assertEqual(replay_b.acceptance, first_b.acceptance)
                self.assertEqual(len(adapter.asset_submissions), 1)
            finally:
                await server.close()
                await bridge.stop("forced")


class AssetNativeConstructionTests(unittest.IsolatedAsyncioTestCase):
    def _asset_ref(self, root: Path, request_id: str, asset_id: str, data: bytes) -> AssetReference:
        _stage_asset(root, request_id, asset_id, data)
        return AssetReference(
            asset_id=asset_id,
            mime_type="image/png",
            byte_size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            spool_path=root / "assets" / request_id / asset_id,
        )

    async def test_codex_local_image_blocks_and_receipt_asset_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transport = _FakeCodexTransport()
            _prime_codex_start(transport)
            adapter = _codex_adapter(transport)
            await adapter.start(_launch(_identity("codex-asset"), harness_id="codex"))
            try:
                data = _codex_fixture()
                turn_result = deepcopy(_fixture_object(data, "turnStartResult"))
                _fixture_object(turn_result, "turn")["id"] = "turn-assets"
                _fixture_object(turn_result, "turn")["status"] = "inProgress"
                transport.queue("turn/start", turn_result)
                ref = self._asset_ref(root, "req-img", "img-1", b"\x89PNG-fake")
                operation = ControlOperationRef(
                    bridge_epoch="e", sequence=1, operation_id="req-img", kind="prompt"
                )
                await adapter.preflight_operation(operation)
                receipt = await adapter.submit_with_assets(
                    PromptRequest(
                        request_id="req-img",
                        source="cockpit",
                        text="see image",
                        submitted_at=NOW,
                        operation=operation,
                        assets=(ref,),
                    )
                )
                self.assertEqual(receipt.acceptance, "immediate")
                turn_start = next(r for r in transport.requests if r[0] == "turn/start")
                blocks = _obj(turn_start[1])["input"]
                assert isinstance(blocks, list)
                self.assertEqual(_obj(blocks[0]), {"type": "text", "text": "see image"})
                self.assertEqual(
                    _obj(blocks[1]),
                    {"type": "localImage", "path": str(root / "assets" / "req-img" / "img-1")},
                )
                self.assertEqual(receipt.raw["assetIds"], ["img-1"])
                # Corrupt the staged file: pre-verification must reject with no native write.
                (root / "assets" / "req-img" / "img-1").write_bytes(b"corrupted")
                rejected = await adapter.submit_with_assets(
                    PromptRequest(
                        request_id="req-img-2",
                        source="cockpit",
                        text="see image",
                        submitted_at=NOW,
                        operation=operation,
                        assets=(ref,),
                    )
                )
                self.assertEqual(rejected.acceptance, "rejected")
                self.assertEqual(len([r for r in transport.requests if r[0] == "turn/start"]), 1)
            finally:
                await adapter.stop("forced")

    async def test_pi_image_content_blocks_and_receipt_asset_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transport = _FakePiTransport()
            adapter = PiRpcAdapter(transport_factory=lambda: transport, clock=lambda: NOW)
            await adapter.start(_launch(_identity("pi-asset"), harness_id="pi"))
            try:
                payload = b"\x89PNG-pi"
                ref = self._asset_ref(root, "req-pi-img", "img-1", payload)
                operation = ControlOperationRef(
                    bridge_epoch="e", sequence=1, operation_id="req-pi-img", kind="prompt"
                )
                await adapter.preflight_operation(operation)
                receipt = await adapter.submit_with_assets(
                    PromptRequest(
                        request_id="req-pi-img",
                        source="cockpit",
                        text="see image",
                        submitted_at=NOW,
                        operation=operation,
                        assets=(ref,),
                    )
                )
                self.assertEqual(receipt.acceptance, "immediate")
                prompt = next(c for c in transport.commands if c["type"] == "prompt")
                self.assertEqual(prompt["message"], "see image")
                images = prompt["images"]
                assert isinstance(images, list)
                self.assertEqual(
                    [_obj(image) for image in images],
                    [
                        {
                            "type": "image",
                            "mimeType": "image/png",
                            "data": base64.b64encode(payload).decode("ascii"),
                        }
                    ],
                )
                self.assertEqual(receipt.raw["assetIds"], ["img-1"])
                (root / "assets" / "req-pi-img" / "img-1").write_bytes(b"corrupted")
                rejected = await adapter.submit_with_assets(
                    PromptRequest(
                        request_id="req-pi-img-2",
                        source="cockpit",
                        text="see image",
                        submitted_at=NOW,
                        operation=operation,
                        assets=(ref,),
                    )
                )
                self.assertEqual(rejected.acceptance, "rejected")
                self.assertEqual(len([c for c in transport.commands if c["type"] == "prompt"]), 1)
            finally:
                await adapter.stop("forced")


# ---------------------------------------------------------------------------
# Withdrawal recovery (tombstone preserved, one true crossing)
# ---------------------------------------------------------------------------


class WithdrawalRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_crosses_once_then_tombstone_and_never_on_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("recovery-1")
            adapter = _CapableAdapter()
            bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(Path(tmp), identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            entry = _ControlledEntry(identity, endpoint.path)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                staged = _stage_asset(Path(tmp), "req-rec", "a1", b"png")
                # Queue two submissions so the first stays withdrawable behind the second.
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "first in flight",
                    source="cockpit",
                    request_id="req-head",
                    expected_bridge_epoch=epoch,
                )
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "recovery body exact",
                    source="cockpit",
                    request_id="req-rec",
                    expected_bridge_epoch=epoch,
                    assets=[staged],
                )
                result = await asyncio.to_thread(
                    withdraw_control_submission,
                    entry,
                    expected_bridge_epoch=epoch,
                    request_id="req-rec",
                )
                self.assertEqual(result.outcome, "withdrawn")
                assert result.recovery is not None
                self.assertEqual(result.recovery.text, "recovery body exact")
                self.assertEqual([asset.asset_id for asset in result.recovery.assets], ["a1"])
                self.assertEqual(
                    result.recovery.assets[0].sha256, hashlib.sha256(b"png").hexdigest()
                )
                replay = await asyncio.to_thread(
                    withdraw_control_submission,
                    entry,
                    expected_bridge_epoch=epoch,
                    request_id="req-rec",
                )
                self.assertEqual(replay.outcome, "withdrawn")
                self.assertEqual(replay.withdrawn_at, result.withdrawn_at)
                self.assertIsNone(replay.recovery)
                page = await asyncio.to_thread(
                    read_operation_timeline, entry, expected_bridge_epoch=epoch
                )
                record = next(item for item in page.items if item.operation_id == "req-rec")
                self.assertEqual(record.state, "withdrawn")
                terminal = await asyncio.to_thread(
                    withdraw_control_submission,
                    entry,
                    expected_bridge_epoch=epoch,
                    request_id="req-terminal-missing",
                )
                self.assertEqual(terminal.outcome, "not-found")
                self.assertIsNone(terminal.recovery)
            finally:
                await server.close()
                await bridge.stop("forced")


# ---------------------------------------------------------------------------
# Client validation battery
# ---------------------------------------------------------------------------


class ClientValidationTests(unittest.TestCase):
    def test_interrupt_result_validation(self) -> None:
        with self.assertRaises(HarnessControlError):
            _interrupt_result(
                {"acknowledgement": "sometimes", "bridgeEpoch": "e"}, expected_bridge_epoch="e"
            )
        with self.assertRaises(HarnessBridgeEpochMismatchError):
            _interrupt_result(
                {"acknowledgement": "accepted", "bridgeEpoch": "other"},
                expected_bridge_epoch="e",
            )
        ok = _interrupt_result(
            {
                "acknowledgement": "accepted",
                "bridgeEpoch": "e",
                "operation": {
                    "bridgeEpoch": "e",
                    "operationSequence": 3,
                    "operationId": "op-3",
                    "operationKind": "prompt",
                },
                "vendorCorrelationId": "turn-9",
                "detail": None,
                "raw": {},
            },
            expected_bridge_epoch="e",
        )
        self.assertEqual(ok.operation.operation_id, "op-3")  # type: ignore[union-attr]

    def test_timeline_validation_battery(self) -> None:
        base_item = {
            "operationId": "op-1",
            "kind": "prompt",
            "source": "cockpit",
            "state": "queued",
            "sequence": 1,
            "submittedAt": NOW,
            "updatedAt": NOW,
            "acceptedAt": None,
            "payloadDigestPresent": True,
            "vendorCorrelationId": None,
        }

        def page(**overrides: object) -> dict[str, object]:
            base: dict[str, object] = {
                "bridgeEpoch": "e",
                "latestSequence": 2,
                "evictedBeforeSequence": 0,
                "truncated": False,
                "items": [dict(base_item), {**base_item, "operationId": "op-2", "sequence": 2}],
            }
            base.update(overrides)
            return base

        self.assertEqual(len(_operation_timeline(page(), expected_bridge_epoch="e").items), 2)
        for overrides in (
            {"items": [{**base_item, "kind": "set-theme"}]},
            {"items": [{**base_item, "source": "moon"}]},
            {"items": [{**base_item, "sequence": 0}]},
            {"items": [dict(base_item, sequence=2), dict(base_item, sequence=2)]},
            {
                "items": [dict(base_item, sequence=3), dict(base_item, sequence=2)],
                "latestSequence": 3,
            },
            {"evictedBeforeSequence": 5},
            {"truncated": "yes"},
            {"truncated": True, "items": []},
            {"latestSequence": 1},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(HarnessControlError):
                _operation_timeline(page(**overrides), expected_bridge_epoch="e")

    def test_withdrawal_recovery_validation(self) -> None:
        with self.assertRaises(HarnessControlError):
            _withdrawal_result(
                {"requestId": "r", "outcome": "withdrawn", "recovery": "text"},
                request_id="r",
            )
        ok = _withdrawal_result(
            {
                "requestId": "r",
                "outcome": "withdrawn",
                "state": "withdrawn",
                "withdrawnAt": NOW,
                "detail": None,
                "recovery": {
                    "text": "body",
                    "assets": [
                        {
                            "assetId": "a1",
                            "mimeType": "image/png",
                            "byteSize": 3,
                            "sha256": "0" * 64,
                        }
                    ],
                },
            },
            request_id="r",
        )
        assert ok.recovery is not None
        self.assertEqual(ok.recovery.assets[0].asset_id, "a1")
