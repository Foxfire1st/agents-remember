"""The protocol adapter boundary, explicit registry, and normalized event reducer."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_control_models import (
    CONTROL_PROTOCOL_VERSION,
    AdapterEvent,
    AdapterHandshake,
    AdapterSnapshot,
    ControlState,
    InteractionResponse,
    LaunchSpec,
    PromptRequest,
    ReconciliationResult,
    ShutdownMode,
    SubmissionReceipt,
)


class HarnessProtocolAdapter(Protocol):
    """The sole vendor boundary owned by a hosted control bridge."""

    async def start(self, launch: LaunchSpec) -> AdapterHandshake: ...

    async def snapshot(self) -> AdapterSnapshot: ...

    def subscribe(self) -> AsyncIterator[AdapterEvent]: ...

    async def submit(self, request: PromptRequest) -> SubmissionReceipt: ...

    async def respond(self, response: InteractionResponse) -> None: ...

    async def reconcile(self, request_id: str) -> ReconciliationResult: ...

    async def stop(self, mode: ShutdownMode) -> None: ...


class HarnessProtocolRegistry:
    """Explicit harness-id to protocol-adapter factories; absent ids are unsupported."""

    def __init__(
        self,
        factories: Mapping[str, Callable[[], HarnessProtocolAdapter]] | None = None,
    ) -> None:
        self._factories = dict(factories or {})

    def create(self, harness_id: str) -> HarnessProtocolAdapter:
        factory = self._factories.get(harness_id)
        return factory() if factory is not None else UnsupportedHarnessProtocolAdapter(harness_id)

    def status(self, harness_id: str) -> ControlState:
        return "starting" if harness_id in self._factories else "unsupported"


DEFAULT_HARNESS_PROTOCOL_REGISTRY = HarnessProtocolRegistry()
"""Factory-free status registry; the hosted runner constructs built-ins from spawn settings."""

BUILTIN_PROTOCOL_HARNESSES = frozenset({"claude", "codex", "pi"})


def protocol_adapter_status(
    harness_id: str,
    *,
    registry: HarnessProtocolRegistry = DEFAULT_HARNESS_PROTOCOL_REGISTRY,
) -> ControlState:
    """Report settings/builtin harnesses without a registered protocol adapter as unsupported."""

    if harness_id in BUILTIN_PROTOCOL_HARNESSES:
        return "starting"
    return registry.status(harness_id)


class UnsupportedHarnessProtocolAdapter:
    """Explicit unsupported state for a configured harness; never a pane/log fallback."""

    def __init__(self, harness_id: str) -> None:
        self._harness_id = harness_id
        self._snapshot: AdapterSnapshot | None = None

    async def start(self, launch: LaunchSpec) -> AdapterHandshake:
        self._snapshot = AdapterSnapshot(
            identity=launch.identity,
            control="unsupported",
            activity="unknown",
            acceptance="unsupported",
            raw={"harnessId": self._harness_id},
        )
        return AdapterHandshake(
            protocol_version=CONTROL_PROTOCOL_VERSION,
            adapter_id=f"unsupported:{self._harness_id}",
            identity=launch.identity,
            capabilities=frozenset(),
            snapshot=self._snapshot,
        )

    async def snapshot(self) -> AdapterSnapshot:
        if self._snapshot is None:
            raise HarnessControlError("unsupported adapter was not started")
        return self._snapshot

    def subscribe(self) -> AsyncIterator[AdapterEvent]:
        raise HarnessControlError("unsupported adapter has no event subscription")

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        return SubmissionReceipt(
            request_id=request.request_id,
            acceptance="unsupported",
            submitted_at=request.submitted_at,
            detail=f"no protocol adapter is registered for {self._harness_id!r}",
        )

    async def respond(self, response: InteractionResponse) -> None:
        del response
        raise HarnessControlError(f"no protocol adapter is registered for {self._harness_id!r}")

    async def reconcile(self, request_id: str) -> ReconciliationResult:
        return ReconciliationResult(
            request_id=request_id,
            state="unsupported",
            reconciled_at=datetime.now(UTC).isoformat(),
            detail=f"no protocol adapter is registered for {self._harness_id!r}",
        )

    async def stop(self, mode: ShutdownMode) -> None:
        del mode


def reduce_adapter_event(snapshot: AdapterSnapshot, event: AdapterEvent) -> AdapterSnapshot:
    """Reduce one normalized event; unknown kinds retain raw detail without guessing semantics."""

    if event.identity != snapshot.identity:
        raise HarnessControlError("adapter event identity does not match the hosted session")
    if event.sequence <= snapshot.last_event_sequence:
        raise HarnessControlError("adapter event sequence must increase monotonically")
    raw = {**snapshot.raw, **event.raw}
    if event.kind in {"state", "completed", "disconnected", "failed"}:
        if event.snapshot is None:
            raise HarnessControlError(f"adapter {event.kind} event requires a snapshot")
        if event.snapshot.identity != snapshot.identity:
            raise HarnessControlError("adapter event snapshot identity does not match the bridge")
        return replace(event.snapshot, last_event_sequence=event.sequence, raw=raw)
    if event.kind == "transcript" and not event.transcript:
        raise HarnessControlError("adapter transcript event requires at least one entry")
    return replace(snapshot, last_event_sequence=event.sequence, raw=raw)
