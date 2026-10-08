"""Standing Eve subscriber and event-driven adapter harness for conformance tests."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime

from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlOperationKind,
    ControlOperationRef,
    SubmissionReceipt,
)
from agents_remember.serving.eve_adapter import EveSessionAdapter
from agents_remember.serving.harness_control_models import (
    AdapterEvent,
    AdapterHandshake,
    PromptRequest,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, async_wait_until
from eve_adapter_test_support import FakeEveRuntime, FakeRuntimeFactory, FakeTurn


def _clock() -> str:
    return datetime.now(UTC).isoformat()


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
        await asyncio.sleep(0)

    async def drain(
        self,
        *,
        until: str | None = None,
        settled: bool = False,
        expect: int = 0,
        timeout: float = HANG_GUARD_SECONDS,
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

    async def read_until(
        self, kind: str, *, timeout: float = HANG_GUARD_SECONDS
    ) -> list[AdapterEvent]:
        """Read until an event of that kind arrives, starting from the current position."""

        return await self.pump.drain(until=kind, timeout=timeout)

    async def read_completed_pass(self) -> list[AdapterEvent]:
        """Read after the subscriber exhausted one exact durable-record pass."""
        mark = self.pump.mark()
        pass_mark = len(self.runtime.stream_passes)
        session_id = (await self.snapshot()).vendor_session_id
        start_index = self.adapter._cursor
        await async_wait_until(
            lambda: any(
                session == session_id and start == start_index
                for session, start, _tail in self.runtime.stream_passes[pass_mark:]
            ),
            "the subscriber to exhaust its durable-record pass",
        )
        return self.pump.since(mark)

    async def emit_observed(
        self, session_id: str, event_type: str, data: dict[str, object]
    ) -> list[AdapterEvent]:
        """Emit one event and wait until its translated form has been consumed."""

        self.runtime.emit(session_id, event_type, data)
        events = await self.pump.drain(expect=1, timeout=HANG_GUARD_SECONDS, limit=8)
        if not events:
            raise AssertionError(f"adapter did not consume {event_type}")
        return events

    async def emit_turn(self, session_id: str, turn: FakeTurn) -> list[AdapterEvent]:
        """Record one complete native turn and wait until the adapter consumed its boundary."""

        self.runtime.turn_events(session_id, turn)
        return await self.pump.drain(until=turn.boundary, timeout=HANG_GUARD_SECONDS, limit=64)

    async def emit_until_turn_observed(self, session_id: str, turn_id: str) -> None:
        """Emit one turn start and wait until the adapter has bound that turn identity."""

        for _ in range(20):
            await self.emit_observed(session_id, "turn.started", {"sequence": 0, "turnId": turn_id})
            if (await self.snapshot()).raw.get("observedTurnId") == turn_id:
                return
        raise AssertionError(f"the adapter never observed turn {turn_id!r}")
