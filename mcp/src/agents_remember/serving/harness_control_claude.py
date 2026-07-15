"""Capability-negotiated Claude Code long-lived stream-json adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from uuid import uuid4

from agents_remember.errors import HarnessControlError
from agents_remember.serving.claude_stream_limits import ClaudeAdapterLimits
from agents_remember.serving.claude_stream_protocol import (
    CLAUDE_STREAM_PROTOCOL,
    build_claude_stream_argv,
    restore_pending_interaction,
)
from agents_remember.serving.claude_stream_startup import (
    negotiate_claude_catalog,
    negotiate_claude_startup,
)
from agents_remember.serving.claude_stream_state import ClaudeStreamState
from agents_remember.serving.claude_stream_transport import (
    ClaudeStreamTransport,
    ClaudeSubprocessTransport,
)
from agents_remember.serving.harness_capabilities import CapabilitySnapshot
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    REQUIRED_ADAPTER_CAPABILITIES,
    AdapterEvent,
    AdapterHandshake,
    AdapterSnapshot,
    ControlIdentity,
    InteractionResponse,
    LaunchSpec,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
    SubmissionReceipt,
)

Clock = Callable[[], str]
CorrelationFactory = Callable[[], str]
TransportFactory = Callable[[], ClaudeStreamTransport]


class ClaudeStreamJsonAdapter:
    """Validate startup, then delegate Claude frames to the bounded normalized state machine."""

    def __init__(
        self,
        *,
        transport_factory: TransportFactory = ClaudeSubprocessTransport,
        clock: Clock = lambda: datetime.now(UTC).isoformat(),
        correlation_factory: CorrelationFactory = lambda: str(uuid4()),
        limits: ClaudeAdapterLimits | None = None,
    ) -> None:
        self._transport = transport_factory()
        self._clock = clock
        self._correlation_factory = correlation_factory
        self._limits = limits or ClaudeAdapterLimits()
        self._identity: ControlIdentity | None = None
        self._unsupported_snapshot: AdapterSnapshot | None = None
        self._capabilities: CapabilitySnapshot | None = None
        self._state: ClaudeStreamState | None = None
        self._transport_started = False
        self._started = False
        self._stopping = False
        self._unsupported_detail: str | None = None

    @property
    def retained_submission_count(self) -> int:
        return self._state.retained_submission_count if self._state is not None else 0

    async def start(self, launch: LaunchSpec) -> AdapterHandshake:
        self._validate_launch(launch)
        self._identity = launch.identity
        version: str | None = None
        try:
            argv = build_claude_stream_argv(launch.argv)
            await self._transport.start(argv, cwd=launch.cwd, env=launch.env)
            self._transport_started = True
            control_init, system_init = await negotiate_claude_startup(
                self._transport,
                cwd=launch.cwd,
                timeout_seconds=self._limits.startup_timeout_seconds,
            )
            self._capabilities = await negotiate_claude_catalog(
                self._transport,
                current_model=system_init.model,
                timeout_seconds=self._limits.startup_timeout_seconds,
            )
            version = system_init.version
            supported_commands = control_init.commands | system_init.commands
            pending, pending_frame = restore_pending_interaction(
                control_init.pending_requests, created_at=self._clock()
            )
        except HarnessControlError as exc:
            return await self._unsupported_handshake(launch, str(exc), version=version)
        snapshot = AdapterSnapshot(
            identity=launch.identity,
            control="ready",
            activity="blocked" if pending is not None else "idle",
            acceptance="immediate",
            vendor_session_id=system_init.session_id,
            pending_interaction=pending,
            raw={
                "claudeCodeVersion": version,
                "effectiveModel": system_init.model,
                "permissionMode": system_init.permission_mode,
                "supportedSessionCommands": sorted(supported_commands),
                "transport": "stream-json",
            },
        )
        self._state = ClaudeStreamState(
            identity=launch.identity,
            snapshot=snapshot,
            transport=self._transport,
            supported_commands=supported_commands,
            clock=self._clock,
            correlation_factory=self._correlation_factory,
            limits=self._limits,
            pending_interaction_frame=pending_frame,
        )
        self._state.start_reader()
        self._started = True
        return AdapterHandshake(
            protocol_version=CONTROL_PROTOCOL_VERSION,
            adapter_id=f"{CLAUDE_STREAM_PROTOCOL}:{version}",
            identity=launch.identity,
            capabilities=REQUIRED_ADAPTER_CAPABILITIES,
            snapshot=snapshot,
            raw={
                "claudeCodeVersion": version,
                "capabilityProbe": "control_request/initialize + system/init",
            },
        )

    async def snapshot(self) -> AdapterSnapshot:
        if self._state is not None:
            return self._state.snapshot
        if self._unsupported_snapshot is not None:
            return self._unsupported_snapshot
        raise HarnessControlError("Claude stream-json adapter is not started")

    async def discover(self, launch: LaunchSpec) -> CapabilitySnapshot:
        await self.start(launch)
        try:
            return self.advertise()
        finally:
            await self.stop("forced")

    def advertise(self) -> CapabilitySnapshot:
        if (
            self._state is None
            or self._state.snapshot.control != "ready"
            or self._capabilities is None
        ):
            raise HarnessControlError(self._unsupported_detail or "Claude adapter is unsupported")
        return self._capabilities

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        if self._state is None:
            raise HarnessControlError(self._unsupported_detail or "Claude adapter is unsupported")
        return self._state.subscribe()

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        if self._state is not None:
            return await self._state.submit(request)
        self._require_started()
        return SubmissionReceipt(
            request_id=request.request_id,
            acceptance="unsupported",
            submitted_at=request.submitted_at,
            detail=self._unsupported_detail,
        )

    async def respond(self, response: InteractionResponse) -> None:
        if self._state is None:
            raise HarnessControlError(self._unsupported_detail or "Claude adapter is unsupported")
        await self._state.respond(response)

    async def reconcile(self, request_id: str) -> ReconciliationResult:
        if self._state is not None:
            return self._state.reconcile(request_id)
        self._require_started()
        return ReconciliationResult(
            request_id=request_id,
            state="unsupported",
            reconciled_at=self._clock(),
            detail=self._unsupported_detail,
        )

    async def stop(self, mode: ShutdownMode) -> None:
        if self._stopping:
            return
        self._stopping = True
        if self._transport_started:
            await self._transport.stop(mode)
        if self._state is not None:
            await self._state.finish_reader(mode)

    def _validate_launch(self, launch: LaunchSpec) -> None:
        if self._started or self._identity is not None:
            raise HarnessControlError("Claude stream-json adapter is already started")
        if launch.harness_id != "claude":
            raise HarnessControlError("Claude stream-json adapter requires harness_id='claude'")
        if not launch.argv or not launch.argv[0]:
            raise HarnessControlError("Claude launch argv requires an executable")

    async def _unsupported_handshake(
        self,
        launch: LaunchSpec,
        detail: str,
        *,
        version: str | None = None,
    ) -> AdapterHandshake:
        if self._transport_started:
            await self._transport.stop("forced")
            self._transport_started = False
        self._unsupported_detail = detail
        self._unsupported_snapshot = AdapterSnapshot(
            identity=launch.identity,
            control="unsupported",
            activity="unknown",
            acceptance="unsupported",
            raw={
                "claudeCodeVersion": version,
                "detail": detail,
            },
        )
        self._started = True
        return AdapterHandshake(
            protocol_version=CONTROL_PROTOCOL_VERSION,
            adapter_id=CLAUDE_STREAM_PROTOCOL,
            identity=launch.identity,
            capabilities=frozenset(),
            snapshot=self._unsupported_snapshot,
            raw={"detail": detail},
        )

    def _require_started(self) -> None:
        if not self._started:
            raise HarnessControlError("Claude stream-json adapter is not started")
