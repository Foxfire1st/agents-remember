"""Open-review native invalidation, tested against real filesystem notifications."""

import asyncio
import contextlib
import socket
import subprocess
import threading
import time
from types import SimpleNamespace
from typing import cast
from urllib.request import urlopen

import uvicorn
from agents_remember.application import review_candidate_resolution
from agents_remember.application import review_overview_precompute as precompute_module
from agents_remember.application.review_overview_precompute import OverviewPrecompute
from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.kernel.reviewer_worklist_process import ReviewerWorklistProcesses
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.serving import leaf_worktree_watch as worktree_watch
from agents_remember.serving import review_memory_watch as module
from agents_remember.serving._app_routes import _register_projection_routes
from agents_remember.serving.app import (
    LIVE_PROJECTION_CLOCK,
    LiveProjectionInputs,
    ServingCollaborators,
    _build_serving_runtime,
    create_app,
)
from agents_remember.serving.explainer_watcher import LeafSelection
from agents_remember.serving.leaf_worktree_watch import process_worktree_watch
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
            # The arming baseline alone also names how long ago memory last changed.
            assert {
                k: v for k, v in event.data.items() if k not in {"revision", "changedAgoMs"}
            } == {
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
    # D569: the stream subscribes to the process's one worktree watch instead of installing its
    # own native handle; the edit lands right after the subscription armed, before the baseline.
    watch = process_worktree_watch()
    subscribe = watch.subscribe
    installed = threading.Event()

    def installing(*args):
        unsubscribe = subscribe(*args)
        (tmp_path / "registered.md").write_text("outside edit during installation")
        installed.set()
        return unsubscribe

    monkeypatch.setattr(watch, "subscribe", installing)

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


def test_arming_baseline_names_how_long_ago_memory_last_changed(tmp_path):
    """The first event carries the age of the newest change before arming; later ones do not.

    A reader that asked its HTTP answers before arming compares this age with its own open time,
    so an unchanged memory costs no second read while an edit before arming is still caught.
    """

    root = tmp_path / "memory"
    (root / "onboarding").mkdir(parents=True)
    card = root / "onboarding" / "card.md"
    card.write_text("authored long before the review opened")
    settle = 0.4
    time.sleep(settle)

    async def arm():
        stream = module._memory_events(root, ("repo", "master", "leaf"))
        try:
            first = await asyncio.wait_for(anext(stream), HANG_GUARD_SECONDS)
            return first, stream
        except BaseException:
            await stream.aclose()
            raise

    async def check():
        quiet, stream = await arm()
        await stream.aclose()
        # Timestamp granularity may round a stamp down a little, which only makes it look older.
        assert quiet.data["changedAgoMs"] >= settle * 1000 - 20, quiet.data
        before = time.monotonic()
        card.unlink()  # a delete leaves no file to stat: its directory's stamp carries it
        edited, stream = await arm()
        elapsed_ms = (time.monotonic() - before) * 1000
        try:
            assert edited.data["changedAgoMs"] <= elapsed_ms + 20, (edited.data, elapsed_ms)
            pending = asyncio.create_task(anext(stream))
            # load-independent: a scheduling yield so the armed worker is blocked in its watch
            await asyncio.sleep(0.05)
            (root / "onboarding" / "next.md").write_text("an edit after arming")
            later = await asyncio.wait_for(pending, HANG_GUARD_SECONDS)
            assert later.event == "review.memory"
            assert "changedAgoMs" not in later.data
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
    # D569: each connection subscribes to the process's one worktree watch for its memory root,
    # and its close unsubscribes; the walk of the root happens once per subscription.
    watch = process_worktree_watch()
    subscribe = watch.subscribe
    registrations, walks, closed = [], [], []
    walked = worktree_watch.walked_directories

    def install(key, listen_root, notify):
        registrations.append((key, listen_root))
        unsubscribe = subscribe(key, listen_root, notify)

        def close():
            unsubscribe()
            closed.append((key, listen_root))

        return close

    def walk(path):
        walks.append(path)
        return walked(path)

    monkeypatch.setattr(watch, "subscribe", install)
    monkeypatch.setattr(worktree_watch, "walked_directories", walk)
    asyncio.run(_route_lifecycle(tmp_path, root, walks))
    assert registrations
    assert closed == registrations
    assert all(listen_root == root for _key, listen_root in registrations)
    assert watch.reasons(("repo", "master", "leaf")) == frozenset(), "a closed stream kept its leaf"
    assert str(outside) not in {str(path) for path in walks}
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
    # `publication` is F17's fix and it is the shape, not a path list: the client sends the leaf's
    # publication scope and the server opens its publication watcher only for that key, so a new
    # revision of this leaf's explainer reaches an open page without a reload. `page` names the
    # page itself: when its last stream closes, the open it was being served is released (D585).
    assert names == {"reviewRepo", "reviewMaster", "reviewLeaf", "publication", "page"}

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
    # D569: the connection's native watch is its subscription to the process watch; "retired" is
    # its unsubscription.
    exited = threading.Event()
    watch = process_worktree_watch()
    subscribe = watch.subscribe

    def tracked_subscribe(*args):
        unsubscribe = subscribe(*args)

        def retired():
            try:
                unsubscribe()
            finally:
                exited.set()

        return retired

    monkeypatch.setattr(watch, "subscribe", tracked_subscribe)
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


# --- D569: one process watch, streams subscribe, every change source builds -----------------------


def _memory_repository(root):
    root.mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "x@example.invalid"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    (root / "onboarding").mkdir()
    (root / "onboarding" / "card.md").write_text("a card\n")
    return root


def test_one_memory_edit_under_a_stream_only_leaf_is_one_event_one_capture_one_build(
    tmp_path, monkeypatch
):
    """A leaf with only an open stream -- no seat, no environment -- is covered by the stream.

    One edit gives one native event, one stream event and, through the build's debounce, one
    capture and one build.
    """
    root = _memory_repository(tmp_path / "memory")
    key = ("repo", "master", "leaf")
    watch = process_worktree_watch()
    native: list[int] = []
    handle = watch.handle

    def counted(buffer):
        ours = watch._maps.wds_of.get(key, set())
        native.extend(1 for wd, _mask, _name in worktree_watch._events(buffer) if wd in ours)
        handle(buffer)

    monkeypatch.setattr(watch, "handle", counted)
    captures, builds = [], []
    record = SimpleNamespace(number=1, same_trees=lambda _other: False)

    def capture(selection: LeafSelection) -> ReviewTreeComparisonRecord:
        captures.append(selection)
        return cast("ReviewTreeComparisonRecord", record)  # a stand-in: only its number is read

    build = OverviewPrecompute(
        _config(tmp_path),
        ReviewerWorklistProcesses(),
        capture=capture,
        latest=lambda _s: None,
        quiet=0.2,
        ceiling=1.0,
    )
    monkeypatch.setattr(build, "compute", lambda selection, _record: builds.append(selection))
    monkeypatch.setattr(precompute_module, "trees_digest", lambda _record: "one tree state")
    watch.bind(lambda _key: (), lambda leaf: build.changed(LeafSelection(*leaf)), lambda: None)

    async def check():
        stream = module._memory_events(root, key)
        try:
            baseline = await asyncio.wait_for(anext(stream), HANG_GUARD_SECONDS)
            assert "changedAgoMs" in baseline.data
            assert watch.reasons(key) == frozenset({"open stream"}), "the stream did not cover it"
            pending = asyncio.create_task(anext(stream))
            (root / "onboarding" / "card.md").write_text("one edit\n")
            await asyncio.wait_for(pending, HANG_GUARD_SECONDS)
            wait_until(lambda: builds, "the build")
            follow = asyncio.create_task(anext(stream))
            # load-independent: no second event is expected; the window is the assertion
            done, _ = await asyncio.wait({follow}, timeout=0.5)
            assert not done, "one edit gave a second stream event"
            follow.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await follow
        finally:
            await stream.aclose()

    try:
        asyncio.run(check())
    finally:
        build.shutdown()
    assert native == [1], native
    assert len(captures) == 1 and len(builds) == 1, (captures, builds)
    assert watch.reasons(key) == frozenset()


def test_streams_opened_and_closed_repeatedly_leave_one_subscriber_and_one_event_per_edit(
    tmp_path,
):
    root = _memory_repository(tmp_path / "memory")
    key = ("repo", "master", "leaf")
    watch = process_worktree_watch()

    async def check():
        for _ in range(6):
            churned = module._memory_events(root, key)
            await asyncio.wait_for(anext(churned), HANG_GUARD_SECONDS)
            await churned.aclose()
        assert watch.reasons(key) == frozenset() and not watch.covered(key)
        stream = module._memory_events(root, key)
        try:
            await asyncio.wait_for(anext(stream), HANG_GUARD_SECONDS)
            assert len(watch._maps.subscribers.get(key, ())) == 1
            pending = asyncio.create_task(anext(stream))
            (root / "onboarding" / "card.md").write_text("an edit after the churn\n")
            changed = await asyncio.wait_for(pending, HANG_GUARD_SECONDS)
            assert changed.event == "review.memory", "the edit was missed"
            follow = asyncio.create_task(anext(stream))
            # load-independent: no duplicate is expected; the window is the assertion
            done, _ = await asyncio.wait({follow}, timeout=0.5)
            assert not done, "a closed stream's subscription delivered a duplicate"
            follow.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await follow
        finally:
            await stream.aclose()

    asyncio.run(check())
