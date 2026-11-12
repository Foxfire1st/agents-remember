"""Saved-page publication reaches a connected reader without recursive task watching."""

import asyncio
import contextlib
import threading
from types import SimpleNamespace
from typing import cast
from unittest.mock import ANY

from agents_remember.canvas.explainer_state import record_request
from agents_remember.serving import _app_routes as routes
from agents_remember.serving._app_common import _ServingRuntime
from agents_remember.serving.explainer_move_watch import MoveNoticeWatch
from agents_remember.serving.review_publication_watch import (
    PublicationInputs,
    publication_events,
    safe_publication_events,
)
from fastapi.sse import ServerSentEvent

IDENTITY = ("repo", "master", "leaf")


def watch_inputs(root, pointer):
    return PublicationInputs(
        root,
        pointer,
        root / "environment/leaf",
        root / "notes/reports/review-frames/leaf/explainer/seat-generation",
        root / "environment/leaf/state/requests.json",
    )


def route_inputs(monkeypatch, root):
    monkeypatch.setattr(routes, "leaf_workspace", lambda *_: root / "environment/leaf")
    monkeypatch.setattr(
        routes,
        "generation_path",
        lambda *_: root / "notes/reports/review-frames/leaf/explainer/seat-generation",
    )
    monkeypatch.setattr(
        routes, "ledger_path", lambda *_: root / "environment/leaf/state/requests.json"
    )


def test_future_frame_and_ready_replacement_are_observed_and_worker_reaped(tmp_path):
    async def run():
        root = tmp_path / "task"
        root.mkdir()
        pointer = root / "notes/reports/review-frames/leaf/explainer/ready.json"
        stream = publication_events(IDENTITY, lambda _: watch_inputs(root, pointer))
        async with contextlib.aclosing(stream):
            baseline = await asyncio.wait_for(
                anext(stream),
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                5,
            )
            assert baseline.event == "review.explainer.changed"
            assert baseline.data["kind"] == "baseline"
            pointer.parent.mkdir(parents=True)
            pointer.write_text('{"revision":1}')
            first = await asyncio.wait_for(
                anext(stream),
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                5,
            )
            assert first.data["revision"] != baseline.data["revision"]
            assert first.data["kind"] == "page"
            assert first.data["pageStamp"] is not None
            # Allow the worker to settle/rearm, draining any registration baseline.
            pending = asyncio.create_task(anext(stream))
            while True:
                done, _ = await asyncio.wait(
                    {pending},
                    # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                    timeout=0.15,
                )
                if not done:
                    break
                pending.result()
                pending = asyncio.create_task(anext(stream))
            (pointer.parent / "unrelated.html").write_text("not a publication")
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            assert not (await asyncio.wait({pending}, timeout=0.15))[0]
            temporary = pointer.with_suffix(".tmp")
            temporary.write_text('{"revision":2}')
            temporary.replace(pointer)
            replaced = await asyncio.wait_for(
                pending,
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                5,
            )
            assert replaced.event == "review.explainer.changed"
            pointer.unlink()
            assert (
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                (await asyncio.wait_for(anext(stream), 5)).event == "review.explainer.changed"
            )
        assert not any(t.name.startswith("review-publication-") for t in threading.enumerate())

    asyncio.run(run())


def test_cancel_pending_read_reaps_native_worker(tmp_path):
    async def run():
        pointer = tmp_path / "ready.json"
        stream = publication_events(IDENTITY, lambda _: watch_inputs(tmp_path, pointer))
        await anext(stream)
        pending = asyncio.create_task(anext(stream))
        # load-independent: parks the task on the real condition before the assertion; nothing reads the clock
        await asyncio.sleep(0.02)
        pending.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pending
        await stream.aclose()
        assert not any(t.name.startswith("review-publication-") for t in threading.enumerate())

    asyncio.run(run())


def test_bad_scope_reports_error_without_touching_resolver():
    async def run():
        def reject(_):
            raise AssertionError("invalid identity reached admission")

        stream = safe_publication_events(("repo", "../master", "leaf"), reject)
        event = await anext(stream)
        assert event.event == "review.explainer.watch.error"
        await stream.aclose()

    asyncio.run(run())


def test_landed_publication_stream_does_not_admit_a_live_memory_root(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "leaf_task_root", lambda *_: tmp_path)
    route_inputs(monkeypatch, tmp_path)
    runtime = cast(
        _ServingRuntime,
        SimpleNamespace(
            config=SimpleNamespace(coordination_root=tmp_path),
            explainer_application=SimpleNamespace(
                frame_scope=lambda *_: SimpleNamespace(frame_dir=tmp_path / "frame"),
            ),
        ),
    )

    def denied_memory(*_):
        raise AssertionError("a landed publication must not read live memory")

    async def ordinary():
        yield ServerSentEvent(event="snapshot", data={})
        await asyncio.Event().wait()

    async def run():
        source = routes._review_stream(ordinary(), None, denied_memory, None)
        stream = routes._publication_stream(source, runtime, "repo/master/leaf")
        async with contextlib.aclosing(stream):
            seen = set()
            while "review.explainer.changed" not in seen:
                event = await asyncio.wait_for(
                    anext(stream),
                    # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                    5,
                )
                seen.add(event.event)
                assert event.event not in {
                    "review.memory.error",
                    "review.explainer.watch.error",
                }
        assert not any(t.name.startswith("review-publication-") for t in threading.enumerate())

    asyncio.run(run())


def test_completion_notice_is_scoped_and_not_duplicated_for_live_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "leaf_task_root", lambda *_: tmp_path)
    calls = []

    async def moved(identity, _watch):
        calls.append(identity)
        yield ServerSentEvent(event="review.explainer.moved", data={"cleared": True})
        await asyncio.Event().wait()

    monkeypatch.setattr(routes, "_moved_events", moved)
    route_inputs(monkeypatch, tmp_path)
    runtime = cast(
        _ServingRuntime,
        SimpleNamespace(
            config=SimpleNamespace(coordination_root=tmp_path),
            explainer_application=SimpleNamespace(
                frame_scope=lambda *_: SimpleNamespace(frame_dir=tmp_path / "frame"),
            ),
        ),
    )

    async def ordinary():
        yield ServerSentEvent(event="snapshot", data={})
        await asyncio.Event().wait()

    async def run(already):
        stream = routes._publication_stream(
            ordinary(), runtime, "repo/master/leaf", cast(MoveNoticeWatch, object()), already
        )
        async with contextlib.aclosing(stream):
            seen = set()
            while "review.explainer.changed" not in seen or (
                not already and "review.explainer.moved" not in seen
            ):
                seen.add(
                    # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                    (await asyncio.wait_for(anext(stream), 5)).event
                )

    asyncio.run(run(False))
    assert calls == [IDENTITY]
    asyncio.run(run(True))
    assert calls == [IDENTITY]
    assert not any(t.name.startswith("review-publication-") for t in threading.enumerate())


def test_a_reconnect_baseline_names_the_same_publication_state_until_the_environment_moves(
    tmp_path,
):
    """D569: every connection's baseline carries the page and environment stamps of what it saw.

    The revision is the connection's own, so only the stamps tell a page that a reconnect changed
    nothing; a moved environment is a different environment stamp.
    """

    async def baseline(root, pointer):
        stream = publication_events(IDENTITY, lambda _: watch_inputs(root, pointer))
        async with contextlib.aclosing(stream):
            return (
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                await asyncio.wait_for(anext(stream), 5)
            ).data

    async def run():
        root = tmp_path / "task"
        (root / "environment").mkdir(parents=True)
        pointer = root / "notes/reports/review-frames/leaf/explainer/ready.json"
        pointer.parent.mkdir(parents=True)
        pointer.write_text('{"revision":1}')
        first, second = await baseline(root, pointer), await baseline(root, pointer)
        assert first["kind"] == second["kind"] == "baseline"
        assert first["revision"] != second["revision"]
        assert first["environmentStamp"] is not None
        assert (first["pageStamp"], first["environmentStamp"]) == (
            second["pageStamp"],
            second["environmentStamp"],
        )
        # MIK-R211 v5 rule 12: the leaf's state lives in its folder, so a refused build that wrote
        # its failure there is the environment moving; a counter alone, with no folder, is not.
        inputs = watch_inputs(root, pointer)
        inputs.generation_file.write_text("1\n")
        assert await baseline(root, pointer) == {**first, "revision": ANY}
        record_request(
            inputs.ledger,
            "leaf",
            "refused",
            {
                "state": "failed",
                "beforeCreate": True,
                "generation": 1,
                "at": "2026-10-27T00:00:00Z",
            },
        )
        moved = await baseline(root, pointer)
        assert moved["pageStamp"] == first["pageStamp"]
        assert moved["environmentStamp"] != first["environmentStamp"]

    asyncio.run(run())


def test_a_quiet_stream_is_named_alive_while_its_source_says_nothing(monkeypatch):
    """The transport's keepalive is a comment an EventSource never surfaces; the route names it."""

    monkeypatch.setattr(routes, "STREAM_ALIVE_SECONDS", 0.01)

    async def quiet():
        await asyncio.Event().wait()
        yield ServerSentEvent(event="never", data={})

    async def run():
        stream = routes._merged_stream(quiet(), routes._alive_events())
        async with contextlib.aclosing(stream):
            events = [
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                await asyncio.wait_for(anext(stream), 5)
                for _ in range(2)
            ]
        assert [event.event for event in events] == [routes.STREAM_ALIVE_EVENT] * 2

    asyncio.run(run())
