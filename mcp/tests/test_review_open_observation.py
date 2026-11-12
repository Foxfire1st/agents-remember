"""One observation per open of the review page, shared by that open's reads (D569).

The developer's binding: one stamp per input per open, shared by the open's reads as a snapshot and
never kept across opens; the page sends one id with its reads and marks the open's last read;
a read without an id stamps for itself; capacity eviction is a counted re-stamp; and the snapshot
never reaches the watch-event build, so a change landing mid-open still builds.

The app here makes the opening's six reads as the real routes make them: concurrently, each on a
worker thread entered through ``read_while_connected`` (the context the middleware bound is copied
into it), each stamping the same comparison inputs twice -- before and after its work, as the
answer reuse does. The inputs are real: a Git repository and two files, stamped by the real
``ChangesetCache``; only the count of its fresh observations is added.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import subprocess
import threading
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from agents_remember.application.review_overview_precompute import OverviewPrecompute
from agents_remember.kernel import read_cancellation
from agents_remember.kernel.read_cancellation import PageReads, ReadCancelled
from agents_remember.serving import changeset_cache as cache_module
from agents_remember.serving import review_open_observation as module
from agents_remember.serving.changeset_cache import (
    ChangesetCache,
    ContentRead,
    ReadInput,
    RepositoryRead,
)
from agents_remember.serving.explainer_watcher import LeafSelection
from agents_remember.serving.read_disconnect import read_while_connected
from agents_remember.serving.review_open_observation import (
    LAST_MARK,
    OPEN_HEADER,
    PAGE_HEADER,
    OpenObservations,
    ReviewOpenMiddleware,
    install_review_open,
    open_stamp,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, wait_until
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient

#: The opening's six reads (dashboard reading/useChangeSet.ts and the top bar's /stale).
SIX = ("intent", "entries", "summary", "overview", "files", "stale")


def _observations(capacity: int = module.OPEN_CAPACITY) -> OpenObservations:
    """A table of its own, holding its opens on page reads of its own: none outlives the test."""

    return OpenObservations(capacity=capacity, reads=PageReads())


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env={"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(root), "PATH": "/usr/bin:/bin"},
    )


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[ReadInput, ...]:
    repository = tmp_path / "code"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    (repository / "a.py").write_text("a\n", encoding="utf-8")
    _git(repository, "add", "a.py")
    _git(repository, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "a")
    record = tmp_path / "comparison-7.json"
    record.write_text("{}", encoding="utf-8")
    document = tmp_path / "leaf.json"
    document.write_text("{}", encoding="utf-8")
    return (
        RepositoryRead(repository, references=("HEAD",)),
        ContentRead(record),
        ContentRead(document),
    )


class Counted:
    """The real cache, counting its fresh observations per input."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.cache = ChangesetCache()
        self.observed: Counter[ReadInput] = Counter()
        self._lock = threading.Lock()
        real = ChangesetCache.snapshot

        def counted(cache: ChangesetCache, source: ReadInput) -> str | None:
            with self._lock:
                self.observed[source] += 1
            return real(cache, source)

        monkeypatch.setattr(cache_module.ChangesetCache, "snapshot", counted)

    def total(self) -> int:
        with self._lock:
            return sum(self.observed.values())


def _app(
    observations: OpenObservations, counted: Counted, inputs: tuple[ReadInput, ...]
) -> FastAPI:
    app = FastAPI()
    executor = ThreadPoolExecutor(max_workers=len(SIX), thread_name_prefix="review-trees")
    arrived = threading.Barrier(len(SIX), timeout=HANG_GUARD_SECONDS)

    def read(concurrent: bool) -> Response:
        if concurrent:
            arrived.wait()  # all six are in flight at once, as on the page
        before = [counted.cache.open_snapshot(source) for source in inputs]
        after = [counted.cache.open_snapshot(source) for source in inputs]
        return JSONResponse({"before": before, "after": after})

    @app.get("/read/{name}")
    async def one(request: Request, name: str, concurrent: bool = False) -> Response:
        return await read_while_connected(request, lambda: read(concurrent), executor=executor)

    @app.get("/api/explainer/{repository}/{master}/{leaf}/stale")
    async def stale(request: Request, repository: str, master: str, leaf: str) -> Response:
        return await read_while_connected(request, lambda: read(False), executor=executor)

    install_review_open(app, observations)
    return app


@pytest.fixture
def opened(
    monkeypatch: pytest.MonkeyPatch, inputs: tuple[ReadInput, ...]
) -> Iterator[tuple[TestClient, OpenObservations, Counted]]:
    observations = _observations()
    counted = Counted(monkeypatch)
    with TestClient(_app(observations, counted, inputs)) as client:
        yield client, observations, counted


def _header(open_id: str, *, last: bool = False) -> dict[str, str]:
    return {OPEN_HEADER: f"{open_id}; {LAST_MARK}" if last else open_id}


def _six(client: TestClient, open_id: str | None) -> list[dict[str, list[str | None]]]:
    """The opening's six reads at once; the page marks the sixth it starts as the open's last."""

    answers: dict[str, dict[str, list[str | None]]] = {}

    def one(name: str) -> None:
        headers = _header(open_id, last=name == SIX[-1]) if open_id else {}
        response = client.get(f"/read/{name}", params={"concurrent": "true"}, headers=headers)
        assert response.status_code == 200
        answers[name] = response.json()

    threads = [threading.Thread(target=one, args=(name,)) for name in SIX]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(HANG_GUARD_SECONDS)
    assert set(answers) == set(SIX)
    return [answers[name] for name in SIX]


def test_one_open_observes_each_input_once_across_its_six_concurrent_reads(
    opened: tuple[TestClient, OpenObservations, Counted],
    inputs: tuple[ReadInput, ...],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, observations, counted = opened
    caplog.set_level(logging.INFO, logger=module.__name__)
    answers = _six(client, "open-1")
    assert counted.observed == Counter(dict.fromkeys(inputs, 1))  # one stamp per input per open
    assert all(answer == answers[0] for answer in answers)  # every read saw the one snapshot
    ended = [record.getMessage() for record in caplog.records if "ended" in record.getMessage()]
    assert ended == [
        f"review open open-1 ended: 6 reads, {len(inputs)} inputs observed, "
        f"{6 * 2 * len(inputs) - len(inputs)} stamps shared; answers: none"
    ]
    assert observations.held() == 0 and observations.evicted == 0


def test_an_opens_first_read_names_its_leaf_once_and_a_stream_read_names_nothing(
    opened: tuple[TestClient, OpenObservations, Counted],
) -> None:
    """The page open is the leaf build's interactive trigger; the stream's reads are not (D570)."""

    client, observations, _counted = opened
    told: list[tuple[str, str, str]] = []
    observations.bind_opened(told.append)
    leaf = {"repo": "agents-remember", "master": "260101_tree_review", "leaf": "260101-TRV-L1"}
    named = (leaf["repo"], leaf["master"], leaf["leaf"])
    client.get("/read/intent", params=leaf, headers=_header("open-1"))
    client.get("/read/entries", params=leaf, headers=_header("open-1", last=True))
    assert told == [named], "an open names its leaf once, from its first read"
    client.get("/read/intent", params=leaf)  # the stream's reads, and any other, send no open id
    assert told == [named]
    client.get(
        "/api/explainer/agents-remember/260101_tree_review/260101-TRV-L1/stale",
        headers=_header("open-2"),
    )  # an open whose first read is the explainer's
    assert told == [named, named]


def test_a_read_without_an_open_id_stamps_for_itself(
    opened: tuple[TestClient, OpenObservations, Counted], inputs: tuple[ReadInput, ...]
) -> None:
    client, observations, counted = opened
    _six(client, None)
    assert counted.observed == Counter(dict.fromkeys(inputs, 6 * 2))  # each read, before and after
    assert observations.held() == 0


def test_after_the_open_ends_a_read_with_the_same_id_observes_again(
    opened: tuple[TestClient, OpenObservations, Counted], inputs: tuple[ReadInput, ...]
) -> None:
    """The consequence, not the mechanism: an entry left resident would answer this read itself."""

    client, _observations, counted = opened
    client.get("/read/intent", headers=_header("open-1"))
    assert counted.total() == len(inputs)
    client.get("/read/entries", headers=_header("open-1", last=True))
    assert counted.total() == len(inputs)  # still the open: shared
    client.get("/read/intent", headers=_header("open-1"))  # after the open's last read ended
    assert counted.observed == Counter(dict.fromkeys(inputs, 2))


def test_a_change_between_two_opens_is_seen_and_an_open_keeps_its_snapshot(
    opened: tuple[TestClient, OpenObservations, Counted], inputs: tuple[ReadInput, ...]
) -> None:
    client, _observations, _counted = opened
    document = inputs[2]
    assert isinstance(document, ContentRead)
    first = client.get("/read/intent", headers=_header("open-1")).json()["before"]
    document.path.write_text('{"edited": true}', encoding="utf-8")
    during = client.get("/read/entries", headers=_header("open-1", last=True)).json()["before"]
    assert during == first  # the open's one observation: its reads agree with each other
    second = client.get("/read/intent", headers=_header("open-2")).json()["before"]
    assert second[:2] == first[:2] and second[2] != first[2]  # the next open sees the edit


def test_an_open_evicted_at_capacity_observes_again_and_the_eviction_is_counted(
    monkeypatch: pytest.MonkeyPatch,
    inputs: tuple[ReadInput, ...],
    caplog: pytest.LogCaptureFixture,
) -> None:
    observations = _observations(capacity=1)
    counted = Counted(monkeypatch)
    caplog.set_level(logging.INFO, logger=module.__name__)
    with TestClient(_app(observations, counted, inputs)) as client:
        client.get("/read/intent", headers=_header("open-1"))
        client.get("/read/intent", headers=_header("open-2"))  # open-1 is evicted
        client.get("/read/entries", headers=_header("open-1"))  # re-stamps, borrows nothing
    assert counted.observed == Counter(dict.fromkeys(inputs, 3))
    assert observations.evicted == 2  # open-1, then open-2 when open-1's later read came back
    assert any("review open open-1 evicted" in record.getMessage() for record in caplog.records)


def test_a_failed_observation_is_never_shared_and_the_waiting_read_observes_itself() -> None:
    observations = _observations()
    started, release = threading.Event(), threading.Event()
    seen: list[str | None] = []

    def cancelled() -> str | None:
        started.set()
        assert release.wait(HANG_GUARD_SECONDS)
        raise ReadCancelled  # the observing read's client left

    def owner() -> None:
        with observations.reading("open-1"), pytest.raises(ReadCancelled):
            open_stamp("input", cancelled)

    def waiter() -> None:
        with observations.reading("open-1"):
            seen.append(open_stamp("input", lambda: "its own"))

    first = threading.Thread(target=owner)
    first.start()
    assert started.wait(HANG_GUARD_SECONDS)
    second = threading.Thread(target=waiter)
    second.start()
    wait_until(lambda: observations.entry("open-1").shared == 1, "the waiting read")
    release.set()
    first.join(HANG_GUARD_SECONDS)
    second.join(HANG_GUARD_SECONDS)
    assert seen == ["its own"]


def test_the_watch_build_never_takes_an_opens_snapshot(tmp_path: Path) -> None:
    """A change landing mid-open still builds: the build captures in its own context."""

    observations = _observations()
    captured: list[str | None] = []
    done = threading.Event()

    def capture(_selection: LeafSelection) -> None:
        captured.append(open_stamp("the leaf's worktree", lambda: "fresh after the change"))
        done.set()

    build = OverviewPrecompute(
        object(),  # type: ignore[arg-type] -- nothing is computed: the capture returns no record
        object(),  # type: ignore[arg-type]
        capture=capture,
        quiet=0.0,
        ceiling=0.0,
    )
    try:
        with observations.reading("open-1"):
            assert open_stamp("the leaf's worktree", lambda: "before the change") == (
                "before the change"
            )
            build.changed(LeafSelection("repo", "master", "leaf"))  # its threads start in here
            assert done.wait(HANG_GUARD_SECONDS)
    finally:
        build.shutdown()
    assert captured == ["fresh after the change"]


def test_an_opens_answers_are_named_computed_read_or_joined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The open's log says, per answer, whether the open computed it, found it, or waited for it."""

    from test_review_answer_open_leaf import fixture, query  # noqa: PLC0415 -- its answer owner

    gate, waiting = threading.Event(), threading.Event()
    owner, produce, calls, _worktree, _plain = fixture(tmp_path, monkeypatch, produce_gate=gate)
    join = read_cancellation._Flight.join

    def watched(flight: object, *args: object, **kwargs: object) -> object:
        waiting.set()  # the second asker is waiting on the first's computation
        return join(flight, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(read_cancellation._Flight, "join", watched)
    observations = _observations()
    caplog.set_level(logging.INFO, logger=module.__name__)

    def first() -> None:
        with observations.reading("open-1"):
            owner.trees(query(), produce)

    def second() -> None:
        with observations.reading("open-1"):
            owner.trees(query(), produce)

    computing = threading.Thread(target=first)
    computing.start()
    wait_until(lambda: calls, "the first answer computing")
    joining = threading.Thread(target=second)
    joining.start()
    assert waiting.wait(HANG_GUARD_SECONDS)
    gate.set()
    computing.join(HANG_GUARD_SECONDS)
    joining.join(HANG_GUARD_SECONDS)
    with observations.reading("open-1"):
        owner.trees(query(), produce)  # kept now: read
    observations.end("open-1")
    ended = next(
        r.getMessage() for r in caplog.records if "review open open-1 ended" in r.getMessage()
    )
    named = [part.split()[:2] for part in ended.split("answers: ", 1)[1].split(", ")]
    assert sorted(named) == sorted([["trees", "computed"], ["trees", "joined"], ["trees", "read"]])
    assert calls == [1]


# --- an open is held from its first read until it is released (D585) -----------------------------


def _first_read(observations: OpenObservations, open_id: str, page: str | None = None) -> None:
    """One read of the open, answered: the open goes on, its other reads not sent yet."""

    with observations.reading(open_id, ("repo", "master", "leaf"), page):
        pass


class _Ends:
    """How the open's last-marked request ends: answered, failing, or cancelled."""

    @staticmethod
    async def answered(_scope: object, _receive: object, _send: object) -> None:
        return None

    @staticmethod
    async def raising(_scope: object, _receive: object, _send: object) -> None:
        raise RuntimeError("the read failed")

    @staticmethod
    async def client_gone(_scope: object, _receive: object, _send: object) -> None:
        raise ReadCancelled  # its every asker left

    @staticmethod
    async def server_cancelled(_scope: object, _receive: object, _send: object) -> None:
        raise asyncio.CancelledError  # the server cancelled the request (shutdown, transport)


@pytest.mark.parametrize("end", ["answered", "raising", "client_gone", "server_cancelled"])
def test_an_open_is_held_from_its_first_read_and_released_however_its_last_read_ends(
    end: str,
) -> None:
    reads = PageReads()
    observations = OpenObservations(reads=reads)
    _first_read(observations, "open-1", "page-1")
    assert set(reads.held()) == {"open-1"} and reads.busy(), "the open is not held between reads"
    assert not reads.busy(opens=False), "a held open counted as a read being answered"
    last = ReviewOpenMiddleware(getattr(_Ends, end), observations)
    scope = {
        "type": "http",
        "path": "/read/files",
        "query_string": b"",
        "headers": [
            (OPEN_HEADER.encode(), f"open-1; {LAST_MARK}".encode()),
            (PAGE_HEADER.encode(), b"page-1"),
        ],
    }
    with contextlib.suppress(RuntimeError, ReadCancelled, asyncio.CancelledError):
        asyncio.run(last(scope, None, None))  # type: ignore[arg-type]
    assert reads.held() == {}, f"the open stayed held after its last read ended ({end})"
    assert not reads.busy()


def test_a_page_that_leaves_or_opens_again_releases_its_open_and_another_page_keeps_its_own(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Between two reads of an open no request exists, so its page's stream says it left."""

    reads = PageReads()
    observations = OpenObservations(reads=reads)
    caplog.set_level(logging.INFO, logger=module.__name__)
    _first_read(observations, "open-1", "page-1")
    _first_read(observations, "other", "page-2")
    _first_read(observations, "open-2", "page-1")  # the same page opens again, another leaf
    assert set(reads.held()) == {"other", "open-2"}, "the page's earlier open was not superseded"
    with observations.page_stream("page-2"), observations.page_stream("page-2"):
        pass  # both of page-2's streams close: page-2 left
    assert set(reads.held()) == {"open-2"}, "a page that left kept its open held"
    with observations.page_stream("page-1"):
        with observations.page_stream("page-1"):
            pass  # one stream reconnects (a new scope): the page is still here
        assert set(reads.held()) == {"open-2"}, "the page's open was released while it stayed"
    assert reads.held() == {}, "the page left and its open stayed held"
    released = [r.getMessage() for r in caplog.records if "released" in r.getMessage()]
    assert released == [
        "review open open-1 released: a new open of its page superseded it",
        "review open other released: its page's stream closed",
        "review open open-2 released: its page's stream closed",
    ]


def test_an_open_evicted_at_capacity_or_cleared_is_released_and_one_without_a_page_ends_alone() -> (
    None
):
    reads = PageReads()
    observations = OpenObservations(capacity=1, reads=reads)
    _first_read(observations, "open-1", "page-1")
    _first_read(observations, "open-2")  # no page names it: its end or eviction releases it
    assert set(reads.held()) == {"open-2"}, "an evicted open stayed held"
    observations.end("open-2")
    assert reads.held() == {}
    _first_read(observations, "open-3", "page-3")
    observations.clear()
    assert reads.held() == {}, "a cleared open stayed held"
