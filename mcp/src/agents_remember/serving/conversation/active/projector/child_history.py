"""Bounded selected-child native-history hydration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from agents_remember.errors import NativeHistoryLimitExceeded, NativeHistoryUnavailable
from agents_remember.serving.conversation.active.agent_history import (
    AgentHistoryHydration,
    agent_history_state_item,
)
from agents_remember.serving.conversation.projectors import HarnessProjector
from agents_remember.serving.conversation.projectors.common import MappedItem
from agents_remember.serving.harness_control_client import ControlledSession
from agents_remember.serving.harness_control_models import NativeEvidencePage

from .agent_authority import AgentAuthority, is_agent_roster_item
from .mutation_stream import ProjectionMutationStream
from .native_ingestion import NATIVE_PAGE_LIMIT, NativeEvidenceIngestion
from .references import ProjectionEvidenceRefs

Clock = Callable[[], str]
NativePageReader = Callable[..., NativeEvidencePage]
MAX_AGENT_NATIVE_INFLIGHT = 64


class ChildHistoryProjection:
    """Own child-history eligibility, singleflight, and local failure state."""

    def __init__(
        self,
        *,
        parent_thread_id: str | None,
        bridge_epoch: str,
        entry: ControlledSession,
        mapper: HarnessProjector,
        stream: ProjectionMutationStream,
        agents: AgentAuthority,
        native: NativeEvidenceIngestion,
        refs: ProjectionEvidenceRefs,
        native_page_reader: NativePageReader,
        apply_lock: asyncio.Lock,
        clock: Clock,
    ) -> None:
        self._parent_thread_id = parent_thread_id
        self._bridge_epoch = bridge_epoch
        self._entry = entry
        self._mapper = mapper
        self._stream = stream
        self._agents = agents
        self._native = native
        self._refs = refs
        self._read_native_page = native_page_reader
        self._apply_lock = apply_lock
        self._clock = clock
        self.walked: set[str] = set()
        self.failures: set[str] = set()
        self.inflight: dict[str, asyncio.Task[AgentHistoryHydration]] = {}

    def reset(self) -> None:
        self.walked.clear()

    async def close(self) -> None:
        tasks = tuple(self.inflight.values())
        self.inflight.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def release(self) -> None:
        self.walked.clear()
        self.failures.clear()
        for task in self.inflight.values():
            task.cancel()
        self.inflight.clear()

    async def refresh(self, thread_id: str) -> AgentHistoryHydration:
        if (
            not self._mapper.uses_native_pages
            or self._mapper.eager_native_continuation
            or thread_id == self._parent_thread_id
            or thread_id not in self._agents.live_threads
        ):
            return AgentHistoryHydration(status="not-eligible", thread_id=thread_id)
        if thread_id in self.walked:
            return AgentHistoryHydration(status="already-hydrated", thread_id=thread_id)
        task = self.inflight.get(thread_id)
        if task is None:
            if len(self.inflight) >= MAX_AGENT_NATIVE_INFLIGHT:
                return AgentHistoryHydration(
                    status="unavailable",
                    thread_id=thread_id,
                    detail="too many selected child history reads are already in progress",
                    code="child-history-capacity",
                )
            task = asyncio.create_task(self._hydrate(thread_id))
            self.inflight[thread_id] = task
            task.add_done_callback(
                lambda completed, selected=thread_id: self._finished(selected, completed)
            )
        return await asyncio.shield(task)

    def _finished(
        self, thread_id: str, task: asyncio.Task[AgentHistoryHydration]
    ) -> None:
        if self.inflight.get(thread_id) is task:
            self.inflight.pop(thread_id, None)
        if not task.cancelled():
            task.exception()

    async def _hydrate(self, thread_id: str) -> AgentHistoryHydration:
        try:
            await self._walk(thread_id)
        except (NativeHistoryLimitExceeded, NativeHistoryUnavailable) as exc:
            async with self._apply_lock:
                self.failures.add(thread_id)
                self._stream.apply_outputs(
                    [
                        agent_history_state_item(
                            agent=self._agents.ref(thread_id),
                            status="unavailable",
                            detail=str(exc),
                            observed_at=self._clock(),
                        )
                    ],
                    self._refs.native(f"{thread_id}:history-unavailable"),
                )
                return AgentHistoryHydration(
                    status="unavailable",
                    thread_id=thread_id,
                    detail=str(exc),
                    code=exc.code,
                )
        async with self._apply_lock:
            self.walked.add(thread_id)
            if thread_id in self.failures:
                self.failures.remove(thread_id)
                self._stream.apply_outputs(
                    [
                        agent_history_state_item(
                            agent=self._agents.ref(thread_id),
                            status="recovered",
                            detail="the selected child history retry succeeded",
                            observed_at=self._clock(),
                        )
                    ],
                    self._refs.native(f"{thread_id}:history-recovered"),
                )
            return AgentHistoryHydration(status="hydrated", thread_id=thread_id)

    async def _walk(self, thread_id: str) -> None:
        live_native_turns: set[str] = set()
        cursor: str | None = None
        while True:
            page: NativeEvidencePage = await asyncio.to_thread(
                self._read_native_page,
                self._entry,
                cursor=cursor,
                limit=NATIVE_PAGE_LIMIT,
                expected_bridge_epoch=self._bridge_epoch,
                thread_id=thread_id,
            )
            async with self._apply_lock:
                for frame in page.frames:
                    ref = self._refs.native(f"{thread_id}:{frame.native_id}")
                    outputs = self._native.suppress_live_twins(
                        self._mapper.map_native_frame(frame, evidence_ref=ref),
                        live_native_turns,
                        thread_id,
                    )
                    outputs = [
                        output
                        for output in outputs
                        if not (
                            isinstance(output, MappedItem)
                            and is_agent_roster_item(output.item)
                        )
                    ]
                    outputs = [
                        self._agents.scope_native_item(output, thread_id) for output in outputs
                    ]
                    outputs = [self._agents.bind_thread(output, thread_id) for output in outputs]
                    self._stream.apply_outputs(outputs, ref)
            cursor = page.next_cursor
            if (page.next_cursor is None and not page.truncated) or not page.frames:
                return
