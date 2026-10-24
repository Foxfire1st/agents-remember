"""Open-review native invalidation, tested against real filesystem notifications."""

import asyncio
import contextlib
import socket
import threading
import time
from types import SimpleNamespace
from urllib.request import urlopen

import uvicorn
from agents_remember.application import review_candidate_resolution
from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.serving import review_memory_watch as module
from agents_remember.serving._app_routes import _register_projection_routes
from agents_remember.serving.app import (
    LIVE_PROJECTION_CLOCK,
    LiveProjectionInputs,
    ServingCollaborators,
    _build_serving_runtime,
    create_app,
)
from agents_remember.serving.projector import ProjectionCadence
from agents_remember.serving.review_memory_watch import multiplex_review_memory
from agents_remember_test_support.testing.review_git_trees import LEAF, MASTER, REPO, build_world
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, wait_until
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.sse import ServerSentEvent
from test_serving import _config


async def _revision(stream):
    while True:
        event = await anext(stream)
        if event.event == "review.memory":
            assert {k: v for k, v in event.data.items() if k != "revision"} == {
                "repo": "repo",
                "master": "master",
                "leaf": "leaf",
            }
            return event.data["revision"]
        assert event.event != "review.memory.error", event.data


async def _settled_next(stream):
    pending = asyncio.create_task(_revision(stream))
    while True:
        # load-independent: no further event is expected; the window is the assertion
        done, _ = await asyncio.wait({pending}, timeout=0.1)
        if not done:
            return pending
        pending.result()
        pending = asyncio.create_task(_revision(stream))


def _marker(root, number):
    # Ordinary authored onboarding paths may have names also used by unrelated runtime caches.
    nested = root / "onboarding" / "tmp" / "captures"
    markdown = nested / "record.md"
    record = nested / "record.json"
    if number == 0:
        nested.mkdir(parents=True)
        draft = nested / ".record.md.tmp"
        draft.write_text("first")
        draft.rename(markdown)
    elif number == 1:
        markdown.write_text("edited markdown")
    elif number == 2:
        record.write_text("{}")
    elif number == 3:
        record.write_text('{"changed":true}')
    elif number == 4:
        record.unlink()
    elif number == 5:
        markdown.rename(nested / "moved.md")
    elif number == 6:
        (nested / "moved.md").unlink()
    elif number == 7:
        (nested / "new" / "deep").mkdir(parents=True)
        (nested / "new" / "deep" / "card.md").write_text("new nested directory")
    elif number == 8:
        (nested / "new" / "deep" / "card.md").write_text("edited new nested directory")
    else:
        (nested / "new" / "deep" / "card.md").unlink()


def test_native_open_review_ten_markers_and_close(tmp_path):
    asyncio.run(_native_open_review_ten_markers_and_close(tmp_path))


async def _native_open_review_ten_markers_and_close(tmp_path):
    root = tmp_path / "memory"
    root.mkdir()
    provider = root / "provider-runtime"
    provider.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    stopped = asyncio.Event()

    async def ordinary():
        try:
            yield ServerSentEvent(event="snapshot", data={"ordinary": True})
            await asyncio.Event().wait()
        finally:
            stopped.set()

    calls = []

    def resolve(*identity):
        calls.append(identity)
        return root

    closed = multiplex_review_memory(ordinary(), None, resolve)
    assert (await anext(closed)).event == "snapshot"
    await closed.aclose()
    assert calls == []
    assert not any(t.name.startswith("review-memory-") for t in threading.enumerate())
    stream = multiplex_review_memory(ordinary(), ("repo", "master", "leaf"), resolve)
    revisions, latencies = [], []
    try:
        revisions.append(await asyncio.wait_for(_revision(stream), HANG_GUARD_SECONDS))
        pending = await _settled_next(stream)
        for i in range(10):
            start = time.monotonic()
            _marker(root, i)
            revisions.append(await asyncio.wait_for(pending, HANG_GUARD_SECONDS))
            latencies.append(time.monotonic() - start)
            pending = await _settled_next(stream)
        assert max(latencies) <= 3
        print(f"ten native outside markers: max={max(latencies):.4f}s; all={latencies}")
        (provider / "noise.md").write_text("ignored")
        (outside / "noise.md").write_text("ignored")
        (root / "overview.index.json").write_text("{}")
        # load-independent: the pollution below must raise no event; the window is the assertion
        done, _ = await asyncio.wait({pending}, timeout=0.25)
        assert not done
        pending.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pending
    finally:
        await stream.aclose()
    assert len(set(revisions)) == 11
    assert calls == [("repo", "master", "leaf")]
    assert stopped.is_set()
    assert not any(t.name.startswith("review-memory-") for t in threading.enumerate())


def test_change_during_registration_is_not_swallowed_by_baseline(tmp_path, monkeypatch):
    native = module.RustNotify
    installed = threading.Event()

    def installing(*args):
        handle = native(*args)
        (tmp_path / "registered.md").write_text("outside edit during installation")
        installed.set()
        return handle

    monkeypatch.setattr(module, "RustNotify", installing)

    async def check():
        stream = module._memory_events(tmp_path, ("repo", "master", "leaf"))
        initial = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        # Hold the consumer while real native delivery queues both ready and change.
        wait_until(installed.is_set, "native registration")
        # load-independent: a scheduling yield while both deliveries queue, not a bound
        time.sleep(0.15)
        try:
            first = await initial
            second = await asyncio.wait_for(anext(stream), HANG_GUARD_SECONDS)
            assert first.data["revision"] != second.data["revision"]
        finally:
            await stream.aclose()

    asyncio.run(check())


def test_cancel_during_root_admission_reaps_the_worker(tmp_path):
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    def resolve(*_identity):
        entered.set()
        wait_until(release.is_set, "release of the root-admission worker")
        exited.set()
        return tmp_path

    async def ordinary():
        await asyncio.Event().wait()
        yield ServerSentEvent(event="unused", data={})

    async def check():
        stream = multiplex_review_memory(ordinary(), ("repo", "master", "leaf"), resolve)
        pending = asyncio.create_task(anext(stream))
        await asyncio.to_thread(entered.wait, 3)
        pending.cancel()
        # load-independent: cancellation must not return before the worker is released
        done, _ = await asyncio.wait({pending}, timeout=0.05)
        try:
            assert not done, (
                "cancellation returned while the root-admission worker was still running"
            )
        finally:
            release.set()
            with contextlib.suppress(asyncio.CancelledError):
                await pending
            await stream.aclose()
        assert exited.is_set()

    asyncio.run(check())


async def _baseline(stream):
    events = []
    while not any(e.event == "review.memory" for e in events):
        events.append(await asyncio.wait_for(anext(stream), HANG_GUARD_SECONDS))
    return events


def test_route_multiplexes_native_and_real_projector_and_reconnects(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    root.mkdir()
    (root / "provider-runtime").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    native = module.RustNotify
    registrations, walks, closed = [], [], []
    directories = module._directories

    def install(*args):
        registrations.append(args)
        handle = native(*args)

        def close():
            handle.close()
            closed.append(args)

        return SimpleNamespace(watch=handle.watch, close=close)

    def walk(path):
        walks.append(path)
        return directories(path)

    monkeypatch.setattr(module, "RustNotify", install)
    monkeypatch.setattr(module, "_directories", walk)
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", "true")
    asyncio.run(_route_lifecycle(tmp_path, root, walks))
    assert registrations
    assert closed == registrations
    assert all(args[2] is False and args[4] is False for args in registrations)
    assert all(args[0] == [str(root)] for args in registrations)
    assert not any(t.name.startswith("review-memory-") for t in threading.enumerate())


def _runtime(tmp_path, port):
    runtime, _metrics = _build_serving_runtime(
        _config(tmp_path),
        ProjectionCadence(interval=100),
        LIVE_PROJECTION_CLOCK,
        LiveProjectionInputs(landing_state=False, change_watch=False),
        ServingCollaborators(review_memory_root=port),
    )
    return runtime


def _stream_endpoint(app):
    route = next(r for r in app.routes if isinstance(r, APIRoute) and r.path == "/api/stream")
    return route.endpoint


async def _route_lifecycle(tmp_path, root, walks):
    runtime = _runtime(tmp_path, lambda *_: root)
    projector = runtime.projector
    await projector.prime()
    app = FastAPI()
    _register_projection_routes(app, runtime)
    endpoint = _stream_endpoint(app)
    stream = endpoint(reviewRepo="repo", reviewMaster="master", reviewLeaf="leaf")
    events = await _baseline(stream)
    assert any(e.event == "snapshot" for e in events)
    first = next(e.data["revision"] for e in events if e.event == "review.memory")
    assert len(walks) == 1
    pending = asyncio.create_task(anext(stream))
    # load-independent: no idle reconciliation must walk the tree; the window is the assertion
    await asyncio.sleep(0.1)
    assert len(walks) == 1  # no idle filesystem reconciliation
    (root / "record.json").write_text("{}")
    changed = await asyncio.wait_for(pending, HANG_GUARD_SECONDS)
    assert changed.event == "review.memory"
    assert changed.data["revision"] != first
    await stream.aclose()
    assert not projector._subscribers
    reopened = endpoint(reviewRepo="repo", reviewMaster="master", reviewLeaf="leaf")
    second = next(
        e.data["revision"] for e in await _baseline(reopened) if e.event == "review.memory"
    )
    await reopened.aclose()
    assert first != second
    assert not projector._subscribers


def test_existing_sse_route_accepts_review_context_and_refuses_unwired(tmp_path):
    app = FastAPI()
    _register_projection_routes(app, _runtime(tmp_path, None))
    endpoint = _stream_endpoint(app)
    names = {p["name"] for p in app.openapi()["paths"]["/api/stream"]["get"]["parameters"]}
    assert names == {"reviewRepo", "reviewMaster", "reviewLeaf"}

    async def check():
        for selection in (
            dict(reviewRepo="repo", reviewMaster="master", reviewLeaf="leaf"),
            dict(reviewRepo="repo"),
            dict(reviewRepo="../escape", reviewMaster="master", reviewLeaf="leaf"),
        ):
            stream = endpoint(**selection)
            try:
                event = await asyncio.wait_for(anext(stream), HANG_GUARD_SECONDS)
                assert event.event == "review.memory.error"
                assert event.data["detail"] and event.data["nextAction"]
            finally:
                await stream.aclose()

    asyncio.run(check())


def test_watch_refusal_or_native_failure_keeps_ordinary_stream_usable(tmp_path):
    async def check(port):
        release = asyncio.Event()

        async def ordinary():
            yield ServerSentEvent(event="snapshot", data={})
            await release.wait()
            yield ServerSentEvent(event="contractSnapshot", data={"still": "usable"})
            await asyncio.Event().wait()

        stream = multiplex_review_memory(ordinary(), ("repo", "master", "leaf"), port)
        try:
            while True:
                event = await asyncio.wait_for(anext(stream), HANG_GUARD_SECONDS)
                if event.event == "review.memory.error":
                    assert all(k in event.data for k in ("repo", "master", "leaf", "detail"))
                    break
            release.set()
            while True:
                event = await asyncio.wait_for(anext(stream), HANG_GUARD_SECONDS)
                if event.event == "contractSnapshot":
                    assert event.data == {"still": "usable"}
                    break
        finally:
            await stream.aclose()

    asyncio.run(check(None))
    asyncio.run(check(lambda *_: tmp_path / "absent"))


def test_app_factory_passes_the_connection_root_port(tmp_path):
    calls = []

    def port(*identity):
        calls.append(identity)
        return tmp_path

    app = create_app(
        _config(tmp_path),
        live_inputs=LiveProjectionInputs(landing_state=False, change_watch=False),
        collaborators=ServingCollaborators(review_memory_root=port),
    )
    endpoint = _stream_endpoint(app)

    async def check():
        stream = endpoint(reviewRepo="repo", reviewMaster="master", reviewLeaf="leaf")
        try:
            event = await asyncio.wait_for(anext(stream), HANG_GUARD_SECONDS)
            assert event.event == "review.memory"
        finally:
            await stream.aclose()

    asyncio.run(check())
    assert calls == [("repo", "master", "leaf")]
    assert not any(t.name.startswith("review-memory-") for t in threading.enumerate())


def test_real_http_disconnect_retires_native_watch_before_server_shutdown(tmp_path, monkeypatch):
    exited = threading.Event()
    worker = module._native_worker

    def tracked_worker(*args):
        try:
            worker(*args)
        finally:
            exited.set()

    monkeypatch.setattr(module, "_native_worker", tracked_worker)
    calls = []

    def root(*identity):
        calls.append(identity)
        return tmp_path

    app = create_app(
        _config(tmp_path),
        live_inputs=LiveProjectionInputs(landing_state=False, change_watch=False),
        collaborators=ServingCollaborators(review_memory_root=root),
    )
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=1))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]})
    thread.start()
    address = f"http://127.0.0.1:{sock.getsockname()[1]}/api/stream"
    try:
        with urlopen(address, timeout=HANG_GUARD_SECONDS) as response:
            frame = []
            while (line := response.readline()).strip():
                frame.append(line.strip())
            assert b"event: snapshot" in frame
        assert calls == []
        with urlopen(
            address + "?reviewRepo=repo&reviewMaster=master&reviewLeaf=leaf",
            timeout=HANG_GUARD_SECONDS,
        ) as response:
            while (line := response.readline()).strip() != b"event: review.memory":
                assert line, "Stream ended before the native watch was ready"
            assert calls == [("repo", "master", "leaf")]
            assert not exited.is_set()
        wait_until(exited.is_set, "the native watch retiring after the HTTP close")
        assert exited.is_set(), "HTTP close left its native watch alive before server shutdown"
    finally:
        server.should_exit = True
        thread.join(HANG_GUARD_SECONDS)
        sock.close()
    assert not thread.is_alive()
    assert not any(t.name.startswith("review-memory-") for t in threading.enumerate())


def test_dashboard_port_resolves_only_canonical_live_memory_without_capture(tmp_path, monkeypatch):
    world = build_world(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("root admission must not capture a review")

    monkeypatch.setattr(review_candidate_resolution, "_captured_candidate", forbidden)
    port = serving_collaborators(world.config).review_memory_root
    assert callable(port)
    assert port(REPO, MASTER, LEAF) == world.memory_worktree.resolve()
    assert port(REPO, "different-master", LEAF) is None
    assert port("../escape", MASTER, LEAF) is None
    contract = world.contract()
    text = contract.read_text()
    contract.write_text(
        text.replace(str(world.config.coordination_root), str(tmp_path / "foreign"))
    )
    assert port(REPO, MASTER, LEAF) is None
    contract.write_text(text)
    world.contract(live=False)
    assert port(REPO, MASTER, LEAF) is None
