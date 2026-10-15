"""L81 F08: native events from actual serving producers converge without self wakes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import watchfiles.main
from agents_remember.application.knowledge_worklist.leaf import leaf_worklist
from agents_remember.controlplane import durable_store
from agents_remember.kernel import atomic_write, own_writes
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.observer.events import Event
from agents_remember.observer.store import EventStore
from agents_remember.providers.current_state import current_state_path, write_current_provider_state
from agents_remember.serving import change_watcher
from agents_remember.serving.change_watcher import ProjectionInputWatcher
from agents_remember.serving.projections.projection_store import ProviderStateRefresher
from agents_remember.serving.projector import (
    ProjectionCadence,
    ProjectionRefreshers,
    ProjectionReplay,
    Projector,
)
from agents_remember.worktrees.worktree_contract import load_contract
from agents_remember_test_support.testing.review_git_trees import build_world
from agents_remember_test_support.testing.waits import async_wait_until


class _World:
    def __init__(self, root: Path, *, config: McpRuntimeConfig) -> None:
        self.root = root
        self.observer = root / "logs/observer"
        (self.observer / "lifecycles/L1").mkdir(parents=True)
        (self.observer / "workspace").mkdir()
        self.config = config
        self.now = datetime(2026, 10, 13, 12, tzinfo=UTC)


class NativeWriterCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_whole_projection_settles_and_heals_capture_window_then_wakes_on_outside_update(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            state = await self._whole_projection_setup(Path(tmp), stack)
            try:
                await self._settle_whole_projection(state)
                foreign = await self._heal_capture_window(state)
                await self._outside_update_whole_projection(state, foreign)
            finally:
                state.running.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await state.running

    async def _whole_projection_setup(self, tmp: Path, stack: contextlib.ExitStack):
        review = build_world(Path(tmp) / "review")
        world = _World(review.config.coordination_root, config=review.config)
        EventStore(world.observer).append(
            Event(
                id="started",
                ts=world.now.isoformat(),
                kind="lifecycle.started",
                lifecycleId="L1",
                actor="system",
                trust="observed",
                data={"phase": "build", "cwd": str(world.root)},
            )
        )
        contract = load_contract(review.contract())
        registry = own_writes.OwnWriteRegistry()
        clock = [0.0]
        consumed = []
        wakes = []
        owner_passes = []
        real_consume = ProjectionInputWatcher._consume_input_changes

        async def consume(watcher, changes, target):
            pending = await real_consume(watcher, changes, target)
            consumed.append(changes)
            return pending

        def owners(moment):
            # Test-side scheduling, actual writers; only host facts are isolated.
            write_current_provider_state(world.config, {}, checked_at=moment)
            document = leaf_worklist(contract)
            assert document is not None
            owner_passes.append(document)

        for module in (atomic_write, durable_store, change_watcher, own_writes):
            stack.enter_context(mock.patch.object(module, "OWN_WRITES", registry))
        stack.enter_context(
            mock.patch.object(change_watcher, "time", SimpleNamespace(monotonic=lambda: clock[0]))
        )
        stack.enter_context(
            mock.patch.object(ProjectionInputWatcher, "_consume_input_changes", consume)
        )
        watcher = ProjectionInputWatcher(world.config)
        projector = Projector(
            world.config,
            cadence=ProjectionCadence(interval=1, heartbeat=15),
            replay=ProjectionReplay(now=lambda: world.now, before_tick=owners),
            refreshers=ProjectionRefreshers(
                change_watcher=watcher,
                provider=ProviderStateRefresher(),
            ),
        )
        await projector.prime()
        pacer = projector._pacer
        assert pacer is not None
        notify = mock.Mock(wraps=pacer.notify_change)
        stack.enter_context(mock.patch.object(pacer, "notify_change", notify))
        next_refresh = projector._next_refresh

        def record_wake(wake):
            wakes.append((clock[0], wake.reason))
            return next_refresh(wake)

        stack.enter_context(mock.patch.object(projector, "_next_refresh", record_wake))
        running = asyncio.create_task(projector.run())

        return SimpleNamespace(
            world=world,
            projector=projector,
            pacer=pacer,
            clock=clock,
            consumed=consumed,
            wakes=wakes,
            notify=notify,
            owner_passes=owner_passes,
            provider_path=current_state_path(world.config),
            registry=registry,
            running=running,
        )

    async def _scheduled_projection_cycle(self, state, *, full=False):
        before = state.projector.projection_count
        previous = len(state.consumed)
        state.clock[0] = (
            state.pacer._last_full_wake + change_watcher.FULL_REFRESH_SECONDS
            if full
            else state.pacer._next_deadline()[0]
        )
        state.world.now += timedelta(seconds=15)
        state.pacer._event.set()  # Advance the injected state.clock, without a change notification.
        await async_wait_until(
            lambda: state.projector.projection_count > before or state.running.done(),
            "actual projection publication",
        )
        self.assertFalse(state.running.done())
        await self._observed(state.consumed, state.provider_path, after=previous)

    async def _settle_whole_projection(self, state):
        await async_wait_until(lambda: state.pacer.watcher_healthy, "native watch ready")
        for _ in range(3):
            await self._scheduled_projection_cycle(state)
        # Include an actual full healing cycle, not heartbeat-only retained reads.
        await self._scheduled_projection_cycle(state, full=True)
        await async_wait_until(lambda: state.wakes[-1][1] == "full", "scheduled healing")
        settled = len(state.wakes)
        notification_count = state.notify.call_count
        for _ in range(4):
            await self._scheduled_projection_cycle(state)
        await self._scheduled_projection_cycle(state, full=True)
        self.assertEqual(state.notify.call_count, notification_count)
        self.assertEqual(
            [reason for _, reason in state.wakes[settled:]], ["heartbeat"] * 4 + ["full"]
        )
        self.assertGreaterEqual(len(state.owner_passes), 9)

        state.quiet_notifications = state.notify.call_count

    async def _heal_capture_window(self, state):
        # A real lock-ignoring append is interposed after the owner's flush.
        path = state.world.observer / "lifecycles/L1/events.jsonl"
        foreign = Event(
            id="foreign-healed",
            ts=(state.world.now + timedelta(seconds=1)).isoformat(),
            kind="lifecycle.phase-changed",
            lifecycleId="L1",
            actor="system",
            trust="observed",
            data={"phase": "decide"},
        )
        captured = []
        real_fstat = os.fstat

        def append_before_capture(fd):
            if not captured and os.readlink(f"/proc/self/fd/{fd}") == str(path):
                captured.append(state.clock[0])
                with path.open("a") as outside:
                    outside.write(foreign.model_dump_json(by_alias=True) + "\n")
                    outside.flush()
            return real_fstat(fd)

        previous = len(state.consumed)
        with mock.patch.object(durable_store.os, "fstat", append_before_capture):
            owner = Event(
                id="owner-window",
                ts=state.world.now.isoformat(),
                kind="lifecycle.phase-changed",
                lifecycleId="L1",
                actor="system",
                trust="observed",
                data={"phase": "build"},
            )
            # Exercise the existing owned-append boundary on a retained projection input.
            # This test-side writer adds no production EventStore interface.
            durable_store.append_line(
                path, owner.model_dump_json(by_alias=True, exclude_none=True), note_own=True
            )
        self.assertEqual(len(captured), 1)
        await self._observed(state.consumed, path, after=previous)
        self.assertTrue(state.registry.is_own_echo(str(path), change="modified"))
        self.assertEqual(state.notify.call_count, state.quiet_notifications)
        before = state.projector.projection_count
        previous = len(state.consumed)
        state.clock[0] = state.pacer._last_full_wake + change_watcher.FULL_REFRESH_SECONDS
        state.world.now += timedelta(seconds=15)
        state.pacer._event.set()
        await async_wait_until(
            lambda: state.projector.projection_count > before, "race healing publication"
        )
        await self._observed(state.consumed, state.provider_path, after=previous)
        self.assertEqual(state.wakes[-1][1], "full")
        self.assertLessEqual(state.clock[0] - captured[0], change_watcher.FULL_REFRESH_SECONDS)
        projection = state.projector.current()[1]
        assert projection is not None
        lifecycle = next(item for item in projection.lifecycles if item.id == "L1")
        self.assertEqual(lifecycle.phase, foreign.data["phase"])
        disk = json.loads((state.world.observer / "latest-state.json").read_text())
        self.assertEqual(
            next(item for item in disk["lifecycles"] if item["id"] == "L1")["phase"],
            foreign.data["phase"],
        )
        return foreign

    async def _outside_update_whole_projection(self, state, foreign):
        # In the same settled composition, an ordinary outside update state.wakes immediately.
        before = state.projector.projection_count
        notification_count = state.notify.call_count
        outside = foreign.model_copy(
            update={
                "id": "foreign-immediate",
                "ts": state.world.now.isoformat(),
                "data": {"phase": "close"},
            }
        )
        path = state.world.observer / "lifecycles/L1/events.jsonl"
        EventStore(state.world.observer).append(outside)
        await async_wait_until(
            lambda: state.notify.call_count > notification_count, "genuine outside wake"
        )
        self.assertIn(str(path), state.notify.call_args.args[1])
        await self._scheduled_projection_cycle(state)
        self.assertEqual(state.wakes[-1][1], "change")
        self.assertLessEqual(state.clock[0] - state.wakes[-2][0], 3)
        self.assertGreater(state.projector.projection_count, before)
        self.assertEqual(
            next(item for item in state.projector.current()[1].lifecycles if item.id == "L1").phase,
            outside.data["phase"],
        )

    async def _observed(
        self, consumed: list[set[tuple[watchfiles.Change, str]]], path: Path, *, after: int = 0
    ) -> None:
        await async_wait_until(
            lambda: any(
                any(candidate == path.as_posix() for _, candidate in batch)
                for batch in consumed[after:]
            ),
            what="actual native producer event",
        )
