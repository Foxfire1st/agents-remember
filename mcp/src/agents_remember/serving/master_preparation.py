"""Event-driven, app-owned preparation of exact master counter generations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

from agents_remember.kernel.git_command import cancellable_git_commands
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.background_accounting import (
    OWNER_MASTER_PREPARATION,
    BackgroundLedger,
    offload,
)
from agents_remember.serving.changeset_cache import ChangesetCache
from agents_remember.serving.changeset_endpoints import NOT_RECORDED
from agents_remember.serving.master_counter import CounterReader, PreparationBusy
from agents_remember.serving.master_net_generation import (
    MasterEndpointAbsent,
    MasterNetPins,
    load_master_contract,
    select_master_net,
)
from agents_remember.serving.projections.task_input_plan import task_input_plan
from agents_remember.serving.series_preparation_events import offer_series_changes

logger = logging.getLogger(__name__)
UNPINNED_MASTER = MasterNetPins()
NET_PREPARATION_WORKERS = 2


@dataclass(frozen=True)
class MasterPreparationKey:
    """One resolved generation, including the repositories that own its objects."""

    repo: str
    master: str
    code_repository: Path | None
    memory_repository: Path | None
    pins: MasterNetPins


@dataclass
class _Job:
    task: asyncio.Task[dict[str, Any]]
    cancellation: Event
    readers: int = 0


class MasterPreparation:
    """Join preparation by exact generation, with no timer and no retained answer copy.

    Callbacks retain the native computation, persistence and admission policy. This
    owner only schedules and joins that work. The ordinary HTTP reader still resolves
    its current selection after the wait and reads those same stored native pairs.
    """

    def __init__(
        self,
        config: McpRuntimeConfig,
        cache: ChangesetCache,
        reader: CounterReader,
        *,
        ledger: BackgroundLedger | None = None,
        capacity: int = 64,
    ) -> None:
        if capacity < NET_PREPARATION_WORKERS:
            raise ValueError("preparation capacity must cover positive concurrency")
        self._config = config
        self._cache = cache
        self._reader = reader
        self._ledger = ledger
        self._capacity = capacity
        self._net_slots = asyncio.Semaphore(NET_PREPARATION_WORKERS)
        self._leaf_slots = asyncio.Semaphore(1)
        self._jobs: dict[tuple[MasterPreparationKey, bool], _Job] = {}
        self._offered: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._changed: set[tuple[str, str]] = set()
        self._closed = False
        self._resolutions: dict[asyncio.Task[MasterPreparationKey | None], Event] = {}

    def bind_ledger(self, ledger: BackgroundLedger | None) -> None:
        """The composition root supplies the app ledger before any work starts."""
        self._ledger = ledger

    def _resolve(
        self, repo: str, master: str, pins: MasterNetPins, cancellation: Event
    ) -> MasterPreparationKey | None:
        with cancellable_git_commands(cancellation), self._cache.reading(abandoned=cancellation):
            selection = select_master_net(self._config, repo, master, pins)
            contract = load_master_contract(self._config, repo, master)
            if selection is None or contract is None:
                return None
            endpoints = selection.endpoints
            return MasterPreparationKey(
                repo,
                master,
                contract.code_repo_path,
                contract.memory_repo_path,
                MasterNetPins(
                    endpoints.code_base,
                    endpoints.code_tip,
                    endpoints.memory_base,
                    endpoints.memory_tip,
                ),
            )

    async def _selection(
        self, repo: str, master: str, pins: MasterNetPins
    ) -> MasterPreparationKey | None:
        if len(self._resolutions) >= self._capacity:
            raise PreparationBusy("master selection capacity reached")
        cancellation = Event()

        async def resolve() -> MasterPreparationKey | None:
            return await offload(
                OWNER_MASTER_PREPARATION,
                self._ledger,
                self._resolve,
                repo,
                master,
                pins,
                cancellation,
            )

        task = asyncio.create_task(resolve())
        self._resolutions[task] = cancellation
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation.set()
            task.cancel()
            await self._drain({task})
            raise
        finally:
            self._resolutions.pop(task, None)

    def _prepare(
        self, selected: MasterPreparationKey, leaves: bool, cancellation: Event
    ) -> dict[str, Any]:
        with cancellable_git_commands(cancellation), self._cache.reading(abandoned=cancellation):
            return self._reader(
                self._config,
                selected.repo,
                selected.master,
                include_leaves=leaves,
                pins=selected.pins,
            )

    async def ensure(
        self,
        repo: str,
        master: str,
        *,
        include_leaves: bool,
        pins: MasterNetPins = UNPINNED_MASTER,
    ) -> MasterNetPins:
        """Prepare the exact selected generation; concurrent readers join one job.

        A net-only reader never waits for the leaf phase. A disconnected reader
        withdraws its claim, but cannot cancel another reader or a ref-event offer.
        """
        value = await self.answer(repo, master, include_leaves=include_leaves, pins=pins)
        generation = value.get("generation")
        if generation is None:
            return pins
        return MasterNetPins(
            generation["codeBase"],
            generation["codeTip"],
            generation["memoryBase"],
            generation["memoryTip"],
        )

    async def answer(
        self,
        repo: str,
        master: str,
        *,
        include_leaves: bool,
        pins: MasterNetPins = UNPINNED_MASTER,
    ) -> dict[str, Any]:
        """Return the exact native counter answer already admitted by shared work.

        The unmeasured case still calls the same reader. No stale answer is retained
        here, and a live leaf is read once for this shared response, not once again
        after joining the prepared result.
        """
        if self._closed:
            raise RuntimeError("master preparation is closed")
        selected = await self._selection(repo, master, pins)
        if selected is None:
            selected = MasterPreparationKey(repo, master, None, None, pins)
        net = await self._join(selected, False)
        return await self._join(selected, True) if include_leaves else net

    async def _join(self, selected: MasterPreparationKey, leaves: bool) -> dict[str, Any]:
        key = selected, leaves
        job = self._jobs.get(key)
        while job is not None and job.cancellation.is_set():
            # A previous sole reader left. Drain its canceled producer before a
            # new reader starts the same exact generation, rather than inheriting
            # that reader's cancellation or overlapping the abandoned native work.
            await asyncio.gather(asyncio.shield(job.task), return_exceptions=True)
            job = self._jobs.get(key)
        if job is None:
            if self._closed:
                raise RuntimeError("master preparation is closed")
            if len(self._jobs) >= self._capacity:
                raise PreparationBusy("master preparation capacity reached")
            cancellation = Event()
            task = asyncio.create_task(self._compute(selected, leaves, cancellation))
            job = _Job(task, cancellation)
            self._jobs[key] = job
            task.add_done_callback(lambda finished: self._finished(key, job, finished))
        job.readers += 1
        try:
            return await asyncio.shield(job.task)
        finally:
            job.readers -= 1
            if job.readers == 0 and not job.task.done():
                job.cancellation.set()
                job.task.cancel()

    async def _compute(
        self, selected: MasterPreparationKey, leaves: bool, cancellation: Event
    ) -> dict[str, Any]:
        async with self._leaf_slots if leaves else self._net_slots:
            if cancellation.is_set():
                raise asyncio.CancelledError
            return await offload(
                OWNER_MASTER_PREPARATION,
                self._ledger,
                self._prepare,
                selected,
                leaves,
                cancellation,
            )

    def _finished(
        self,
        key: tuple[MasterPreparationKey, bool],
        job: _Job,
        task: asyncio.Task[dict[str, Any]],
    ) -> None:
        if self._jobs.get(key) is job:
            self._jobs.pop(key)
        if not task.cancelled():
            task.exception()  # Observe abandoned failures without changing an awaiter's result.

    def offer_master(self, repo: str, master: str) -> bool:
        """Offer a native ref event, coalescing repeated events for the same master.

        False is an explicit overload/closed outcome. The caller records it; the
        ordinary reader can still join/prepare its exact generation later.
        """
        if self._closed:
            return False
        address = repo, master
        if address in self._offered:
            self._changed.add(address)
            return True
        if len(self._offered) >= self._capacity:
            return False
        task = asyncio.create_task(self._background(address))
        self._offered[address] = task
        return True

    async def _background(self, address: tuple[str, str]) -> None:
        try:
            while not self._closed:
                self._changed.discard(address)
                await self._prepare_offered(address)
                if address not in self._changed:
                    break
        finally:
            self._offered.pop(address, None)
            self._changed.discard(address)

    async def _prepare_offered(self, address: tuple[str, str]) -> None:
        try:
            await self.ensure(*address, include_leaves=True)
        except MasterEndpointAbsent as error:
            # A master with no recorded result has nothing to prepare. Requests
            # still receive the same named absence; broken recorded pairs warn.
            if error.kind != NOT_RECORDED:
                logger.exception("master preparation failed for %s/%s", *address)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("master preparation failed for %s/%s", *address)

    async def prepare_inputs(self) -> None:
        """Discover admitted masters and offer their preparation without awaiting reads."""
        plan = await offload(
            OWNER_MASTER_PREPARATION,
            self._ledger,
            task_input_plan,
            self._config.coordination_root / "tasks",
            include_series_refs=True,
        )
        if not plan.complete:
            raise OSError("master preparation inputs incomplete: " + "; ".join(plan.errors))
        self.prepare(plan.series_masters)

    def prepare(self, masters: Iterable[tuple[str, str]]) -> None:
        """Offer startup masters to the same bounded jobs as ref events and requests."""
        offer_series_changes(self.offer_master, masters)

    async def aclose(self) -> None:
        """Stop offers, cancel native work and await physical completion, even if canceled twice."""
        self._closed = True
        for job in self._jobs.values():
            job.cancellation.set()
        for cancellation in self._resolutions.values():
            cancellation.set()
        tasks: list[
            asyncio.Task[None]
            | asyncio.Task[dict[str, Any]]
            | asyncio.Task[MasterPreparationKey | None]
        ] = [job.task for job in self._jobs.values()]
        tasks.extend(self._offered.values())
        tasks.extend(self._resolutions)
        for task in tasks:
            task.cancel()
        await self._drain(tasks)
        self._jobs.clear()
        self._offered.clear()
        self._changed.clear()

    @staticmethod
    async def _drain(tasks: Iterable[Awaitable[object]]) -> None:
        drained = asyncio.gather(*tasks, return_exceptions=True)
        cancelled = False
        while not drained.done():
            try:
                await asyncio.shield(drained)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
