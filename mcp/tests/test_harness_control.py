"""Fake-adapter conformance coverage for the protocol-backed hosted control bridge."""

import asyncio
import json
import sys
import unittest
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import (
    dataclass,
    replace,
)
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import HarnessAdapterDisconnectedError
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    SetResult,
)
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_ipc import (
    HarnessControlServer,
    LocalControlEndpoint,
)
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    REQUIRED_ADAPTER_CAPABILITIES,
    AcceptanceState,
    AdapterCapability,
    AdapterEvent,
    AdapterHandshake,
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationRef,
    InteractionResponse,
    LaunchSpec,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
    SubmissionReceipt,
)
from agents_remember.serving.terminal_catalog import TerminalCatalogEntry


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
        self.stop_error: BaseException | None = None
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
        self.setter_operations: list[ControlOperationRef] = []
        self.event_sequence = 0

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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control.py:102).
    async def _event_stream(self) -> AsyncIterator[AdapterEvent]:  # pragma: no cover
        while True:
            event = await self.events.get()
            if event is None:
                return
            yield event

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        return self._event_stream()

    async def preflight_operation(self, operation: ControlOperationRef) -> None:
        del operation

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control.py:115).
    async def submit(self, request: PromptRequest) -> SubmissionReceipt:  # pragma: no cover
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

    async def set_model(
        self, model_key: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        assert operation is not None
        self.setter_operations.append(operation)
        self.control_log.append(("model", model_key))
        if self.set_results:
            return self.set_results.popleft()
        return SetResult(
            ok=True,
            acceptance="immediate",
            requested_value=model_key,
            detail="fake accepted without an effective echo",
        )

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_harness_control.py:150).
    async def set_effort(  # pragma: no cover
        self, effort: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        assert operation is not None
        self.setter_operations.append(operation)
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
            # The answered multiplexed entry settles too.
            pending_interactions=tuple(
                entry
                for entry in self.current.pending_interactions
                if entry.interaction_id != response.interaction_id
            ),
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
        if self.stop_error is not None:
            raise self.stop_error

    def emit(self, event: AdapterEvent) -> None:
        if event.snapshot is not None:
            self.current = event.snapshot
        self.events.put_nowait(event)

    def complete(self, request_id: str) -> None:
        request = next(item for item in self.submissions if item.request_id == request_id)
        assert request.operation is not None
        assert self.current is not None
        self.event_sequence += 1
        completed = replace(
            self.current,
            activity="idle",
            acceptance="immediate",
            pending_interaction=None,
        )
        self.emit(
            AdapterEvent(
                sequence=self.event_sequence,
                kind="completed",
                identity=completed.identity,
                created_at=f"completion-{self.event_sequence}",
                snapshot=completed,
                operation=request.operation,
            )
        )


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

    async def set_model(
        self, model_key: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        del operation
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


def _catalog_entry(identity: ControlIdentity) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=identity.ar_session_id,
        label="control-test",
        kind="harness",
        harness="fake",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=identity.tmux_name,
        command=("fake",),
        created_at=identity.created_at,
        last_attached_at=identity.created_at,
        status="running",
        leaf_key=None,
    )


async def _settle_events() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
