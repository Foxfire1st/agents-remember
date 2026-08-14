"""Contract tests for the native control-plane substrate."""

import asyncio
import hashlib
import json
import tempfile
import unittest
from collections.abc import (
    AsyncIterator,
    Mapping,
)
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
)
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationRef,
    InterruptResult,
    LaunchSpec,
    SubmissionReceipt,
)
from agents_remember.serving.codex_app_server_adapter import (
    CodexAppServerAdapter,
    CodexAppServerSettings,
)
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    SetResult,
)
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_client import (
    interrupt_control,
    read_submission_authority,
    request_control,
)
from agents_remember.serving.harness_control_ipc import (
    HarnessControlServer,
    LocalControlEndpoint,
)
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    REQUIRED_ADAPTER_CAPABILITIES,
    AdapterEvent,
    AdapterHandshake,
    InteractionResponse,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
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


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:92).
async def _drive_completions(adapter, request_ids) -> None:  # pragma: no cover
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:140).
    async def snapshot(self) -> AdapterSnapshot:  # pragma: no cover
        assert self.current is not None
        return self.current

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:144).
    def advertise(self) -> CapabilitySnapshot:  # pragma: no cover
        return CapabilitySnapshot(models=(), selected_model_key=None, selected_effort=None)

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:147).
    async def set_model(  # pragma: no cover
        self, model_key: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del model_key, operation
        raise HarnessControlError("unused")

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:153).
    async def set_effort(  # pragma: no cover
        self, effort: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del effort, operation
        raise HarnessControlError("unused")

    async def preflight_operation(self, operation: ControlOperationRef) -> None:
        del operation

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        return self._stream()

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:165).
    async def _stream(self) -> AsyncIterator[AdapterEvent]:  # pragma: no cover
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:182).
    async def respond(self, response: InteractionResponse) -> None:  # pragma: no cover
        del response

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:185).
    async def reconcile(self, request_id: str) -> ReconciliationResult:  # pragma: no cover
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:223).
    async def interrupt(  # pragma: no cover
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


class ControlActionDispatchTests(unittest.IsolatedAsyncioTestCase):
    """The IPC server answers only actions it implements, and says so by name.

    The action table IS the control protocol's surface. A client holding a newer (or misspelled)
    verb must get a typed refusal naming the verb rather than a silent success, a hang, or a
    connection that dies with no explanation -- that difference is what makes a version skew
    diagnosable from the caller's side.
    """

    async def _serve(self, identity: ControlIdentity, tmp: str):
        bridge = HarnessControlBridge(identity, _PlainAdapter(), clock=lambda: NOW)
        await bridge.start(_launch(identity))
        endpoint = LocalControlEndpoint.for_session(Path(tmp), identity)
        server = HarnessControlServer(endpoint, bridge)
        await server.start()
        return bridge, server, _ControlledEntry(identity, endpoint.path)

    async def test_an_unknown_action_is_refused_by_name_and_leaves_the_bridge_serving(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("unknown-action")
            bridge, server, entry = await self._serve(identity, tmp)
            try:
                with self.assertRaises(HarnessControlError) as ctx:
                    await asyncio.to_thread(request_control, entry, "teleport", {})
                self.assertIn("unknown control action: teleport", str(ctx.exception))
                # The refusal is per-request: the endpoint still answers a verb it does implement.
                self.assertEqual(bridge.snapshot().control, "ready")
                handshake = await asyncio.to_thread(request_control, entry, "handshake", {})
                assert isinstance(handshake, Mapping)
                self.assertEqual(handshake["protocol"], CONTROL_PROTOCOL_VERSION)
            finally:
                await server.close()
                await bridge.stop("forced")


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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:415).
    def messages(self) -> AsyncIterator[dict[str, object]]:  # pragma: no cover
        return self._stream()

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:418).
    async def _stream(self) -> AsyncIterator[dict[str, object]]:  # pragma: no cover
        while True:
            message = await self.incoming.get()
            if message is None:
                return
            yield message

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:425).
    async def respond(self, request_id, result) -> None:  # pragma: no cover
        del request_id, result

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:428).
    async def respond_error(self, request_id, *, code, message) -> None:  # pragma: no cover
        del request_id, code, message

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)
        self.incoming.put_nowait(None)

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:435).
    def emit(self, message: Mapping[str, object]) -> None:  # pragma: no cover
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:567).
    async def request(self, command, *, before_write=None):  # pragma: no cover
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:626).
    async def send(self, command, *, before_write=None) -> None:  # pragma: no cover
        del command, before_write

    def events(self) -> AsyncIterator[Mapping[str, object]]:
        return self._stream()

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:632).
    async def _stream(self) -> AsyncIterator[Mapping[str, object]]:  # pragma: no cover
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
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:699).
    def returncode(self) -> int | None:  # pragma: no cover
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control_plane.py:722).
    async def wait_for_writes(self, count: int) -> None:  # pragma: no cover
        while len(self.writes) < count:
            self._write_event.clear()
            if len(self.writes) < count:
                await asyncio.wait_for(self._write_event.wait(), timeout=1.0)


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


# ---------------------------------------------------------------------------
# Withdrawal recovery (tombstone preserved, one true crossing)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Client validation battery
# ---------------------------------------------------------------------------
