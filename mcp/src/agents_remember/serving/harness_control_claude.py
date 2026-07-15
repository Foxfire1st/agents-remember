"""Capability-negotiated Claude Code long-lived stream-json adapter."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from agents_remember.errors import HarnessAdapterDisconnectedError, HarnessControlError
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
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    LaunchKnobs,
    ModelCapability,
    SetResult,
)
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
    TerminalResult,
)
from agents_remember.serving.harness_launch import ResolvedLaunch, verify_effective_launch

Clock = Callable[[], str]
CorrelationFactory = Callable[[], str]
TransportFactory = Callable[[], ClaudeStreamTransport]


@dataclass(frozen=True)
class _ClaudeSetExpectation:
    command: str
    value: str
    exact_result: str
    prefix_match: bool
    allowed_results: frozenset[str]


class ClaudeStreamJsonAdapter:
    """Validate startup, then delegate Claude frames to the bounded normalized state machine."""

    def __init__(
        self,
        *,
        transport_factory: TransportFactory = ClaudeSubprocessTransport,
        clock: Clock = lambda: datetime.now(UTC).isoformat(),
        correlation_factory: CorrelationFactory = lambda: str(uuid4()),
        limits: ClaudeAdapterLimits | None = None,
        expected_launch: ResolvedLaunch | None = None,
    ) -> None:
        self._transport = transport_factory()
        self._clock = clock
        self._correlation_factory = correlation_factory
        self._limits = limits or ClaudeAdapterLimits()
        self._expected_launch = expected_launch
        self._identity: ControlIdentity | None = None
        self._unsupported_snapshot: AdapterSnapshot | None = None
        self._capabilities: CapabilitySnapshot | None = None
        self._state: ClaudeStreamState | None = None
        self._transport_started = False
        self._started = False
        self._stopping = False
        self._unsupported_detail: str | None = None
        self._set_sequence = 0

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
        if self._expected_launch is not None:
            try:
                verify_effective_launch(
                    self._expected_launch,
                    self._capabilities,
                    require_effort_echo=False,
                )
            except HarnessControlError:
                # Negotiation succeeded, so an expected-launch echo mismatch is a rejected launch,
                # not an unsupported protocol. Close here and let the runner expose bridgeError.
                await self._transport.stop("forced")
                self._transport_started = False
                raise
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
                "requestedLaunchModel": (
                    self._expected_launch.model_key if self._expected_launch is not None else None
                ),
                "requestedLaunchEffort": (
                    self._expected_launch.effort if self._expected_launch is not None else None
                ),
                "launchEffortEvidence": (
                    "catalog-validated native --effort; stream-json init has no effort echo"
                    if self._expected_launch is not None
                    else None
                ),
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

    def launch_knobs(self, *, model_key: str, effort: str | None) -> LaunchKnobs:
        """Return Claude's native launch-time model and effort flags."""

        if not model_key or model_key != model_key.strip():
            raise HarnessControlError(
                "Claude launch model must be non-empty with no outer whitespace"
            )
        if effort is None or not effort or effort != effort.strip():
            raise HarnessControlError(
                "Claude launch effort must be non-empty with no outer whitespace"
            )
        return LaunchKnobs(
            argv=("--model", model_key, "--effort", effort),
            owned_argv_options=("--model", "--effort"),
        )

    async def set_model(self, model_key: str) -> SetResult:
        capabilities = self.advertise()
        model = next((item for item in capabilities.models if item.key == model_key), None)
        if model is None or not model.selectable:
            return self._unsupported_set(model_key, "model is not selectable in list_models")
        result = await self._submit_set_command(
            "model",
            model_key,
            expected_result=f"Set model to {model.display_name} for this session only",
            allowed_results=_model_terminal_results(model, capabilities),
        )
        if result.acceptance == "echo-verified":
            effort_keys = {option.key for option in model.effort_options}
            selected_effort = capabilities.selected_effort
            self._capabilities = replace(
                capabilities,
                selected_model_key=model.key,
                selected_effort=(selected_effort if selected_effort in effort_keys else None),
            )
        return result

    async def set_effort(self, effort: str) -> SetResult:
        capabilities = self.advertise()
        model = self._selected_model(capabilities)
        option = next((item for item in model.effort_options if item.key == effort), None)
        if option is None or not option.session_settable:
            return self._unsupported_set(
                effort,
                f"effort is not session-settable for Claude model {model.key!r}",
            )
        result = await self._submit_set_command(
            "effort",
            effort,
            expected_result=f"Set effort level to {effort} (this session only)",
            prefix_match=True,
        )
        if result.acceptance == "echo-verified":
            self._capabilities = replace(capabilities, selected_effort=effort)
        return result

    async def _submit_set_command(
        self,
        command: str,
        value: str,
        *,
        expected_result: str,
        prefix_match: bool = False,
        allowed_results: frozenset[str] = frozenset(),
    ) -> SetResult:
        state = self._state
        if state is None:
            return self._unsupported_set(value, self._unsupported_detail or "adapter is unavailable")
        expectation = _ClaudeSetExpectation(
            command=command,
            value=value,
            exact_result=expected_result,
            prefix_match=prefix_match,
            allowed_results=allowed_results,
        )
        self._set_sequence += 1
        request = PromptRequest(
            request_id=f"ar-claude-set-{command}-{self._set_sequence}",
            source="durable",
            text=f"/{command} {value}",
            submitted_at=self._clock(),
        )
        receipt = await _submit_set_request(state, request, value)
        if isinstance(receipt, SetResult):
            return receipt
        if receipt.acceptance not in {"immediate", "queued"}:
            state.abandon_submission(request.request_id)
            return _unaccepted_set_result(expectation, receipt)
        terminal = await _wait_set_terminal(state, request, value)
        if isinstance(terminal, SetResult):
            return terminal
        return _terminal_set_result(expectation, terminal)

    @staticmethod
    def _selected_model(capabilities: CapabilitySnapshot) -> ModelCapability:
        selected = next(
            (item for item in capabilities.models if item.key == capabilities.selected_model_key),
            None,
        )
        if selected is None:
            raise HarnessControlError("Claude selected model is absent from list_models")
        return selected

    @staticmethod
    def _unsupported_set(value: str, detail: str) -> SetResult:
        return SetResult(
            ok=False,
            acceptance="unsupported",
            requested_value=value,
            detail=detail,
        )

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


async def _submit_set_request(
    state: ClaudeStreamState,
    request: PromptRequest,
    value: str,
) -> SubmissionReceipt | SetResult:
    try:
        return await state.submit(request)
    except asyncio.CancelledError:
        state.abandon_submission(request.request_id)
        raise
    except (HarnessAdapterDisconnectedError, HarnessControlError) as exc:
        state.abandon_submission(request.request_id)
        return _unknown_set_result(value, str(exc))


async def _wait_set_terminal(
    state: ClaudeStreamState,
    request: PromptRequest,
    value: str,
) -> TerminalResult | SetResult:
    try:
        terminal = await state.wait_terminal(request.request_id)
    except asyncio.CancelledError:
        state.abandon_submission(request.request_id)
        raise
    except (HarnessAdapterDisconnectedError, HarnessControlError) as exc:
        state.abandon_submission(request.request_id)
        return _unknown_set_result(value, str(exc))
    if terminal is None:
        return _unknown_set_result(
            value,
            "Claude did not terminate the structured session command in time",
        )
    return terminal


def _unaccepted_set_result(
    expectation: _ClaudeSetExpectation,
    receipt: SubmissionReceipt,
) -> SetResult:
    if receipt.acceptance == "unsupported":
        return SetResult(
            ok=False,
            acceptance="unsupported",
            requested_value=expectation.value,
            detail=receipt.detail or "Claude refused the session command",
        )
    return _unknown_set_result(
        expectation.value,
        receipt.detail or "Claude did not accept the structured session command",
    )


def _terminal_set_result(
    expectation: _ClaudeSetExpectation,
    terminal: TerminalResult,
) -> SetResult:
    detail = terminal.detail or ""
    if terminal.outcome == "completed":
        return _completed_set_result(expectation, detail)
    acceptance = "unsupported" if terminal.outcome == "failed" else "unknown"
    if terminal.outcome == "failed" and expectation.command == "model":
        detail = (
            f"{detail}; use the native launch flag when session model switching is unavailable"
            if detail
            else "Claude refused session model switching; use the native launch flag"
        )
    return SetResult(
        ok=False,
        acceptance=acceptance,
        requested_value=expectation.value,
        detail=detail or f"Claude session command {terminal.outcome}",
    )


def _completed_set_result(
    expectation: _ClaudeSetExpectation,
    detail: str,
) -> SetResult:
    matched = (
        detail == expectation.exact_result
        or (
            expectation.prefix_match
            and detail.startswith(f"{expectation.exact_result}: ")
        )
        or detail in expectation.allowed_results
    )
    if matched:
        return SetResult(
            ok=True,
            acceptance="echo-verified",
            requested_value=expectation.value,
            effective_value=expectation.value,
            detail="Claude returned the exact session-only command result",
        )
    return SetResult(
        ok=True,
        acceptance="immediate",
        requested_value=expectation.value,
        detail=(
            "Claude completed the structured session command but did not echo the requested "
            f"effective value: {detail or 'empty result'}"
        ),
    )


def _unknown_set_result(value: str, detail: str) -> SetResult:
    return SetResult(
        ok=False,
        acceptance="unknown",
        requested_value=value,
        detail=detail,
    )


def _model_terminal_results(
    model: ModelCapability,
    capabilities: CapabilitySnapshot,
) -> frozenset[str]:
    """Derive only exact terminal strings supported by this dynamic catalog row."""

    aliases = [model]
    if model.resolved_model is not None:
        aliases.extend(
            candidate
            for candidate in capabilities.models
            if candidate is not model and candidate.resolved_model == model.resolved_model
        )
    labels = {candidate.display_name for candidate in aliases}
    labels.update(
        label
        for candidate in aliases
        if (label := _resolved_model_terminal_label(candidate.resolved_model)) is not None
    )
    if model.is_default:
        labels.update(f"{label} (default)" for label in tuple(labels))
    return frozenset(f"Set model to {label} for this session only" for label in labels)


def _resolved_model_terminal_label(resolved_model: str | None) -> str | None:
    """Humanize Claude's dynamic resolved id when its family/version grammar is explicit."""

    if resolved_model is None:
        return None
    marker = resolved_model.lower().rfind("claude-")
    if marker < 0:
        return None
    value = resolved_model[marker + len("claude-") :]
    context_match = re.search(r"\[(?P<context>[^\]]+)\]$", value)
    context = context_match.group("context") if context_match is not None else None
    if context_match is not None:
        value = value[: context_match.start()]
    parts = [part for part in value.split("-") if part]
    family_index = next(
        (
            index
            for index, part in enumerate(parts)
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", part)
            and re.fullmatch(r"v\d+", part, flags=re.IGNORECASE) is None
        ),
        None,
    )
    if family_index is None:
        return None
    family = parts[family_index].replace("_", " ").title()
    version_parts = parts[:family_index] if family_index else parts[family_index + 1 :]
    version = [part for part in version_parts if part.isdigit() and len(part) <= 2]
    if version:
        family = f"{family} {'.'.join(version)}"
    if context is not None:
        family = f"{family} ({context.upper()} context)"
    return family
