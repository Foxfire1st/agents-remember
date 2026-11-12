"""Single-flight preparation keeps native work owned through joins and cancellation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import cast

import pytest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving import master_preparation as subject
from agents_remember.serving.changeset_cache import ChangesetCache
from agents_remember.serving.changeset_scheduler import read_async
from agents_remember.serving.master_net_generation import MasterEndpointAbsent, MasterNetPins
from fastapi import Request
from fastapi.responses import Response


def service(monkeypatch, prepare):
    owner = subject.MasterPreparation(
        cast(McpRuntimeConfig, object()), ChangesetCache(), lambda *_args, **_kwargs: {}
    )
    key = subject.MasterPreparationKey(
        "repo", "master", Path("/code"), None, MasterNetPins("a", "b")
    )
    monkeypatch.setattr(owner, "_resolve", lambda *_args: key)

    def prepared(selected, leaves, cancellation):
        prepare(selected, leaves, cancellation)
        return {
            "generation": {"codeBase": "a", "codeTip": "b", "memoryBase": "", "memoryTip": ""},
            "leaves": ["leaf"] if leaves else [],
        }

    monkeypatch.setattr(owner, "_prepare", prepared)
    return owner


async def until(event: Event) -> None:
    async with asyncio.timeout(3):
        while not event.is_set():
            await asyncio.sleep(0.001)


def test_startup_and_http_join_one_exact_net_and_net_does_not_wait_for_leaves(monkeypatch):
    async def scenario():
        net_started, release_net, leaf_started, release_leaf = (Event() for _ in range(4))
        calls = []

        def prepare(_key, leaves, cancellation):
            calls.append(leaves)
            (leaf_started if leaves else net_started).set()
            release = release_leaf if leaves else release_net
            assert (
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                release.wait(3) or cancellation.is_set()
            )

        owner = service(monkeypatch, prepare)
        owner.prepare([("repo", "master")])
        await until(net_started)
        request = asyncio.create_task(owner.ensure("repo", "master", include_leaves=False))
        # load-independent: parks the task on the real condition before the assertion; nothing reads the clock
        await asyncio.sleep(0.01)
        release_net.set()
        pins = await asyncio.wait_for(
            request,
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            3,
        )
        assert pins == MasterNetPins("a", "b")
        await until(leaf_started)
        assert calls == [False, True]
        assert not release_leaf.is_set()
        release_leaf.set()
        await owner.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["not-recorded", "unresolvable"])
def test_background_absence_is_quiet_but_broken_record_and_requested_absence_stay_visible(
    monkeypatch, caplog, kind
):
    async def scenario():
        owner = service(monkeypatch, lambda *_args: None)

        def absent(*_args):
            raise MasterEndpointAbsent("no selected result", kind=kind)

        monkeypatch.setattr(owner, "_resolve", absent)
        owner.prepare([("repo", "master")])
        await asyncio.gather(*owner._offered.values())
        assert bool(caplog.records) is (kind == "unresolvable")
        caplog.clear()
        assert owner.offer_master("repo", "master")
        await asyncio.gather(*owner._offered.values())
        assert bool(caplog.records) is (kind == "unresolvable")
        with pytest.raises(MasterEndpointAbsent, match="no selected result"):
            await owner.answer("repo", "master", include_leaves=False)
        await owner.aclose()

    asyncio.run(scenario())


def test_one_departing_reader_does_not_cancel_another(monkeypatch):
    async def scenario():
        started, release, finished = Event(), Event(), Event()
        cancellations = []

        def prepare(_key, _leaves, cancellation):
            started.set()
            assert release.wait(
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                3
            )
            cancellations.append(cancellation.is_set())
            finished.set()

        owner = service(monkeypatch, prepare)
        first = asyncio.create_task(owner.ensure("repo", "master", include_leaves=False))
        await until(started)
        second = asyncio.create_task(owner.ensure("repo", "master", include_leaves=False))
        # load-independent: parks the task on the real condition before the assertion; nothing reads the clock
        await asyncio.sleep(0.01)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert (
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            await asyncio.wait_for(second, 3) == MasterNetPins("a", "b")
        )
        assert finished.is_set() and cancellations == [False]
        await owner.aclose()

    asyncio.run(scenario())


def test_shutdown_signals_and_drains_actual_worker(monkeypatch):
    async def scenario():
        started, finished = Event(), Event()

        def prepare(_key, _leaves, cancellation):
            started.set()
            assert cancellation.wait(
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                3
            )
            finished.set()

        owner = service(monkeypatch, prepare)
        owner.offer_master("repo", "master")
        await until(started)
        await asyncio.wait_for(
            owner.aclose(),
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            3,
        )
        assert finished.is_set()
        assert owner._jobs == {} and owner._offered == {}
        assert not owner.offer_master("repo", "master")

    asyncio.run(scenario())


def test_different_tips_never_join_and_bound_refuses_distinct_work(monkeypatch):
    async def scenario():
        entered = Event()
        count = 0

        def prepare(_key, _leaves, cancellation):
            nonlocal count
            count += 1
            if count == 2:
                entered.set()
            assert cancellation.wait(
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                3
            )

        owner = service(monkeypatch, prepare)
        owner._capacity = 2
        one = subject.MasterPreparationKey(
            "repo", "master", Path("/code"), None, MasterNetPins("a", "b")
        )
        two = subject.MasterPreparationKey(
            "repo", "master", Path("/code"), None, MasterNetPins("a", "c")
        )
        three = subject.MasterPreparationKey(
            "repo", "master", Path("/code"), None, MasterNetPins("a", "d")
        )
        first = asyncio.create_task(owner._join(one, False))
        second = asyncio.create_task(owner._join(two, False))
        await until(entered)
        with pytest.raises(subject.PreparationBusy):
            await owner._join(three, False)
        await owner.aclose()
        await asyncio.gather(first, second, return_exceptions=True)
        assert count == 2

    asyncio.run(scenario())


def test_new_reader_drains_previous_readers_cancellation_before_retry(monkeypatch):
    async def scenario():
        entered, canceled, release_old = Event(), Event(), Event()
        calls = 0

        def prepare(_key, _leaves, cancellation):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                assert cancellation.wait(
                    # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                    3
                )
                canceled.set()
                assert release_old.wait(
                    # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                    3
                )

        owner = service(monkeypatch, prepare)
        first = asyncio.create_task(owner.ensure("repo", "master", include_leaves=False))
        await until(entered)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await until(canceled)
        second = asyncio.create_task(owner.ensure("repo", "master", include_leaves=False))
        # load-independent: parks the task on the real condition before the assertion; nothing reads the clock
        await asyncio.sleep(0.01)
        assert calls == 1 and not second.done()
        release_old.set()
        assert (
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            await asyncio.wait_for(second, 3) == MasterNetPins("a", "b")
        )
        assert calls == 2
        await owner.aclose()

    asyncio.run(scenario())


def test_shared_answer_returns_one_live_leaf_read_and_same_body(monkeypatch):
    async def scenario():
        entered, release = Event(), Event()
        calls = []

        def prepare(_key, leaves, cancellation):
            calls.append(leaves)
            if leaves:
                entered.set()
                assert (
                    # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                    release.wait(3) or cancellation.is_set()
                )

        owner = service(monkeypatch, prepare)
        first = asyncio.create_task(owner.answer("repo", "master", include_leaves=True))
        await until(entered)
        second = asyncio.create_task(owner.answer("repo", "master", include_leaves=True))
        # load-independent: parks the task on the real condition before the assertion; nothing reads the clock
        await asyncio.sleep(0.01)
        release.set()
        one, two = await asyncio.wait_for(
            asyncio.gather(first, second),
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            3,
        )
        assert one is two and one["leaves"] == ["leaf"]
        assert calls.count(True) == 1
        await owner.aclose()

    asyncio.run(scenario())


def test_leaf_work_does_not_take_net_capacity(monkeypatch):
    async def scenario():
        entered, release = Event(), Event()

        def prepare(_key, leaves, cancellation):
            if leaves:
                entered.set()
                assert (
                    # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                    release.wait(3) or cancellation.is_set()
                )

        owner = service(monkeypatch, prepare)
        owner.offer_master("repo", "master")
        await until(entered)
        assert await asyncio.wait_for(
            owner.ensure("repo", "other", include_leaves=False),
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            1,
        )
        release.set()
        await owner.aclose()

    asyncio.run(scenario())


class DisconnectedRequest:
    async def is_disconnected(self):
        return True


def test_async_route_disconnect_cancels_and_drains_its_producer():
    async def scenario():
        ended = asyncio.Event()

        async def produce():
            try:
                await asyncio.Future()
            finally:
                ended.set()
            return Response()

        response = await read_async(cast(Request, DisconnectedRequest()), produce)
        assert response.status_code == 499 and ended.is_set()

    asyncio.run(scenario())


def test_async_route_returns_completed_response_without_duplicate_production():
    async def scenario():
        calls = []

        async def produce():
            calls.append(1)
            return Response("ready")

        class ConnectedRequest:
            async def is_disconnected(self):
                return False

        response = await read_async(cast(Request, ConnectedRequest()), produce)
        assert response.body == b"ready" and calls == [1]

    asyncio.run(scenario())


def test_input_discovery_offloads_and_only_offers_exact_planned_masters(monkeypatch):
    async def scenario():
        started, release = Event(), Event()
        owner = service(monkeypatch, lambda *_args: None)
        owner._config = cast(
            McpRuntimeConfig, SimpleNamespace(coordination_root=Path("/coordination"))
        )
        calls = []
        offered = []

        def plan(root, *, include_series_refs):
            calls.append((root, include_series_refs))
            started.set()
            assert release.wait(
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                3
            )
            return SimpleNamespace(complete=True, series_masters=(("repo", "one"), ("repo", "two")))

        monkeypatch.setattr(subject, "task_input_plan", plan)
        monkeypatch.setattr(owner, "offer_master", lambda *address: offered.append(address) or True)
        discovery = asyncio.create_task(owner.prepare_inputs())
        try:
            await until(started)
            # The loop can answer while the synchronous input scan is still running.
            answered = asyncio.Event()
            asyncio.get_running_loop().call_soon(answered.set)
            await asyncio.wait_for(
                answered.wait(),
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                3,
            )
            assert not discovery.done() and offered == []
            release.set()
            await asyncio.wait_for(
                discovery,
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                3,
            )
        finally:
            release.set()
            await owner.aclose()
        assert calls == [(Path("/coordination/tasks"), True)]
        assert offered == [("repo", "one"), ("repo", "two")]

    asyncio.run(scenario())


def test_startup_batch_names_overload_without_eager_work_or_another_queue(monkeypatch, caplog):
    async def scenario():
        owner = service(monkeypatch, lambda *_args: None)
        owner._capacity = 2
        masters = (("repo", name) for name in ("one", "two", "three"))
        owner.prepare(masters)
        assert set(owner._offered) == {("repo", "one"), ("repo", "two")}
        assert len(caplog.records) == 1
        assert caplog.records[0].getMessage() == (
            "series preparation queue declined 1 master offer(s) in this batch"
        )
        await owner.aclose()

    asyncio.run(scenario())


def test_incomplete_input_plan_is_refused_before_any_master_is_offered(monkeypatch):
    async def scenario():
        owner = service(monkeypatch, lambda *_args: None)
        owner._config = cast(
            McpRuntimeConfig, SimpleNamespace(coordination_root=Path("/coordination"))
        )
        monkeypatch.setattr(
            subject,
            "task_input_plan",
            lambda *_args, **_kwargs: SimpleNamespace(complete=False, errors=("unreadable task",)),
        )
        offers = []
        monkeypatch.setattr(owner, "offer_master", lambda *address: offers.append(address) or True)
        with pytest.raises(OSError, match="master preparation inputs incomplete: unreadable task"):
            await owner.prepare_inputs()
        assert offers == []
        await owner.aclose()

    asyncio.run(scenario())
