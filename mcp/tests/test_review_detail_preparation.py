"""After the opening answers, the build prepares each changed invariant's detail (D574).

One by one, in their order, after the opening answers are kept: an open that joins the build
returns at those answers, and a second open meanwhile builds nothing again. The queue never
lengthens a page read -- it starts nothing while one is answered, and the item running when one
arrives stops at its next safe point and is asked again afterwards -- and a click on a detail not
yet prepared computes it at once, which the queue then finds kept: one computation per invariant.
A newer build, another leaf's build waiting or a shutdown ends the queue.

The ports are fakes here; ``test_review_opening_answers`` reads prepared details through the real
routes.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import pytest
import uvicorn
from agents_remember.application import review_overview_precompute as precompute_module
from agents_remember.application.review_overview_precompute import (
    DETAIL_YIELDS,
    OverviewPrecompute,
)
from agents_remember.cli.dashboard import OpeningPorts, detail_answers
from agents_remember.kernel import read_cancellation
from agents_remember.kernel.read_cancellation import (
    PAGE_READS,
    BuildToken,
    PageReads,
    SharedFlights,
    YieldsToPageReads,
    raise_if_read_cancelled,
)
from agents_remember.kernel.reviewer_worklist_process import ReviewerWorklistProcesses
from agents_remember.models.knowledge.review_tree_overview import (
    ReviewInvariantChanges,
    ReviewOverview,
    ReviewSubjectSummary,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
    ReviewTreesResult,
)
from agents_remember.serving.app import LiveProjectionInputs, create_app
from agents_remember.serving.explainer_watcher import LeafSelection
from agents_remember.serving.review_open_observation import OPEN_OBSERVATIONS
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from test_explainer_moved_watcher import _config, _record, _selection
from test_serving import _config as _serving_config

pytestmark = pytest.mark.evidence_unit

LOGGER = "agents_remember.application.review_overview_precompute"


def _until(condition: Callable[[], object], what: str) -> None:
    deadline = time.monotonic() + HANG_GUARD_SECONDS
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.005)


class _Details:
    """Each invariant's detail behind one shared flight, kept once computed, as the stores do.

    An item computes until it is released, at safe points throughout, so a page read or a newer
    build can stop it there.
    """

    def __init__(self, keys: tuple[str, ...]) -> None:
        self.keys = keys
        self.started: list[str] = []
        self.computed: Counter[str] = Counter()
        self.release = {key: threading.Event() for key in keys}
        self._kept: set[str] = set()
        self._flights: SharedFlights[str, None] = SharedFlights()

    def ask(self, key: str) -> None:
        if key in self._kept:
            return

        def produce() -> None:
            self.started.append(key)
            while not self.release[key].is_set():
                raise_if_read_cancelled()
                time.sleep(0.005)
            raise_if_read_cancelled()
            self.computed[key] += 1
            self._kept.add(key)

        self._flights.share(key, produce)

    def listed(
        self, _selection: LeafSelection, _number: int, _previous: int | None = None
    ) -> list[tuple[str, Callable[[], None], bool]]:
        return [(key, lambda key=key: self.ask(key), False) for key in self.keys]

    def click(self, key: str) -> threading.Thread:
        """The page's read of one detail, counted as a page read is, on its own thread."""

        def read() -> None:
            with PAGE_READS.answering():
                self.ask(key)

        clicked = threading.Thread(target=read, daemon=True)
        clicked.start()
        return clicked


class _Precompute(OverviewPrecompute):
    """The build, its opening answers one held flight an open can join."""

    def __init__(self, tmp_path: Path, record: ReviewTreeComparisonRecord) -> None:
        super().__init__(
            _config(tmp_path),
            ReviewerWorklistProcesses(),
            capture=lambda _selection: record,
            latest=lambda _selection: record,
        )
        self.opening = threading.Event()  # released: the opening answers are kept
        self.entered = threading.Event()
        self.computed: list[int] = []
        self.flights: SharedFlights[str, str] = SharedFlights()

    def _opening(self) -> str:
        self.entered.set()
        assert self.opening.wait(HANG_GUARD_SECONDS)
        return "the opening answers"

    def compute(self, selection: LeafSelection, record: ReviewTreeComparisonRecord) -> None:
        del selection
        self.computed.append(record.number)
        self.flights.share("opening", self._opening)


class _Build:
    """The precompute, and the record whose details it lists (another build's list is empty)."""

    def __init__(self, tmp_path: Path, details: _Details, record: ReviewTreeComparisonRecord):
        self.record = record
        self.owner = _Precompute(tmp_path, record)
        self.opening, self.entered, self.computed = (
            self.owner.opening,
            self.owner.entered,
            self.owner.computed,
        )
        self.owner.bind_details(
            lambda selection, number, previous: (
                details.listed(selection, number, previous) if number == record.number else []
            )
        )

    def join(self) -> tuple[str, bool]:
        """An open's read of the opening answers, joining the build's flight."""

        with PAGE_READS.answering():
            return self.owner.flights.share("opening", lambda: "read by the open")


@pytest.fixture
def details() -> _Details:
    return _Details(("first", "second", "third"))


@pytest.fixture
def build(tmp_path: Path, details: _Details) -> Iterator[_Build]:
    made = _Build(tmp_path, details, _record(41, code_candidate="4" * 40))
    yield made
    for event in details.release.values():
        event.set()
    made.opening.set()
    made.owner.shutdown()
    for thread in made.owner._threads:  # its line never lands in the next test's log
        thread.join(HANG_GUARD_SECONDS)


def test_the_details_follow_the_opening_answers_in_their_order(
    build: _Build, details: _Details, caplog: pytest.LogCaptureFixture
) -> None:
    for event in details.release.values():
        event.set()
    with caplog.at_level("INFO", logger=LOGGER):
        build.owner.offer(_selection(), build.record)
        assert build.entered.wait(HANG_GUARD_SECONDS)
        assert details.started == [], "a detail was prepared before the opening answers"
        build.opening.set()
        _until(lambda: "prepared the details" in caplog.text, "the queue's line")
    assert details.started == ["first", "second", "third"]
    assert details.computed == Counter(first=1, second=1, third=1)
    assert "prepared the details of 3 of 3 changed invariants" in caplog.text
    assert (
        "comparison #41" in caplog.text
        and "the first in order is first, the last third" in caplog.text
    )


def test_an_open_that_joins_returns_at_the_opening_answers_and_a_second_open_builds_nothing(
    build: _Build, details: _Details, monkeypatch: pytest.MonkeyPatch
) -> None:
    joined = threading.Event()
    join = read_cancellation._Flight.join

    def watched(flight: Any, asker: Any) -> bool:
        taken = join(flight, asker)
        if taken:
            joined.set()
        return taken

    monkeypatch.setattr(read_cancellation._Flight, "join", watched)
    build.owner.offer(_selection(), build.record)
    assert build.entered.wait(HANG_GUARD_SECONDS)
    answered: list[tuple[str, bool]] = []
    opener = threading.Thread(target=lambda: answered.append(build.join()), daemon=True)
    opener.start()
    assert joined.wait(HANG_GUARD_SECONDS)
    build.opening.set()
    opener.join(HANG_GUARD_SECONDS)
    assert answered == [("the opening answers", False)], "the open did not join the build"
    _until(lambda: details.started, "the first detail")
    assert details.computed == Counter(), "the open waited for a detail"

    # A second open while the queue works: its capture names the trees being built, and its
    # read (here the store's answer) does not wait for the item the queue holds.
    build.owner.opened(_selection())
    assert build.join() == ("read by the open", True)
    assert details.started == ["first"] and details.computed == Counter()
    time.sleep(0.2)  # load-independent: a second build would have started within this
    assert build.computed == [41] and build.owner.superseded == 0


def test_a_page_read_stops_the_running_detail_which_is_asked_again_once_it_is_answered(
    build: _Build, details: _Details, caplog: pytest.LogCaptureFixture
) -> None:
    build.opening.set()
    with caplog.at_level("INFO", logger=LOGGER):
        build.owner.offer(_selection(), build.record)
        _until(lambda: details.started == ["first"], "the first detail")
        with PAGE_READS.answering():
            details.release["first"].set()  # it would finish now, but a page read is answered
            time.sleep(0.2)  # load-independent: the item stops at a safe point within this
            assert details.computed == Counter(), "the queue computed beside a page read"
            assert details.started == ["first"], "the queue started an item beside a page read"
        for event in details.release.values():
            event.set()
        _until(lambda: "prepared the details" in caplog.text, "the queue's line")
    assert details.started == ["first", "first", "second", "third"]
    assert details.computed == Counter(first=1, second=1, third=1)
    assert "1 stopped for a page read and asked again" in caplog.text


def test_a_click_on_an_unprepared_detail_computes_it_at_once_and_the_queue_never_again(
    build: _Build, details: _Details
) -> None:
    build.opening.set()
    build.owner.offer(_selection(), build.record)
    _until(lambda: details.started == ["first"], "the first detail")
    details.release["third"].set()
    clicked = details.click("third")  # the last in order, behind the queue
    clicked.join(HANG_GUARD_SECONDS)
    assert not clicked.is_alive(), "the click waited behind the queue"
    assert details.computed == Counter(third=1)
    details.release["first"].set()
    details.release["second"].set()
    _until(lambda: sum(details.computed.values()) == 3, "the queue")
    time.sleep(0.1)  # load-independent: a second computation of the clicked one would be here
    assert details.computed == Counter(first=1, second=1, third=1), "a detail was computed twice"
    assert details.started.count("third") == 1


def _a_newer_build(build: _Build) -> None:
    build.owner.offer(_selection(), _record(42, code_candidate="5" * 40))


def _another_leafs_build(build: _Build) -> None:
    other = LeafSelection(_selection().repository_id, _selection().master, "260101-OTHER-L1")
    build.owner.offer(other, _record(43, code_candidate="6" * 40))


def _a_shutdown(build: _Build) -> None:
    build.owner.shutdown()


@pytest.mark.parametrize("end", [_a_newer_build, _another_leafs_build, _a_shutdown])
def test_a_newer_build_another_leafs_or_a_shutdown_ends_the_queue(
    build: _Build,
    details: _Details,
    caplog: pytest.LogCaptureFixture,
    end: Callable[[_Build], None],
) -> None:
    build.opening.set()
    with caplog.at_level("INFO", logger=LOGGER):
        build.owner.offer(_selection(), build.record)
        _until(lambda: details.started == ["first"], "the first detail")
        end(build)
        _until(lambda: "prepared the details" in caplog.text, "the queue's line")
    assert details.computed == Counter() and details.started == ["first"]
    assert "prepared the details of 0 of 3" in caplog.text and "ended early" in caplog.text


def test_the_token_yields_to_page_reads_and_ends_with_its_build() -> None:
    reads, build, waiting = PageReads(), BuildToken(), threading.Event()
    token = YieldsToPageReads(build, waiting=waiting.is_set, reads=reads)
    assert not token.is_set() and reads.wait_answered(0)
    with reads.answering():
        assert token.is_set() and not token.must_end() and not reads.wait_answered(0)
    assert not token.is_set() and token.lets_children_land()
    reads.hold("an open")  # between two reads of an open being served
    assert token.is_set() and not token.past_opens().is_set() and not reads.wait_answered(0)
    reads.release("an open")
    assert not token.is_set() and reads.wait_answered(0)
    waiting.set()
    assert token.is_set() and token.must_end()
    waiting.clear()
    build.mark_stale()
    assert token.must_end() and token.lets_children_land()  # a stale build's child lands
    build.stop()
    assert not token.lets_children_land()


def test_an_item_stopped_for_page_reads_again_and_again_is_left_for_a_click_and_the_queue_ends(
    build: _Build, details: _Details, caplog: pytest.LogCaptureFixture
) -> None:
    """Termination, not a hold-back: an item is asked at most DETAIL_YIELDS times."""

    build.opening.set()
    with caplog.at_level("INFO", logger=LOGGER):
        build.owner.offer(_selection(), build.record)
        for attempt in range(1, DETAIL_YIELDS + 1):
            _until(lambda attempt=attempt: details.started == ["first"] * attempt, "an attempt")
            with PAGE_READS.answering():  # a page read arrives while the item computes
                time.sleep(0.05)  # load-independent: the item stops at its next safe point
        details.release["second"].set()
        details.release["third"].set()
        _until(lambda: "prepared the details" in caplog.text, "the queue's line")
    assert details.started == ["first"] * DETAIL_YIELDS + ["second", "third"]
    assert details.computed == Counter(second=1, third=1)
    assert "prepared the details of 2 of 3 changed invariants" in caplog.text
    assert (
        f"{DETAIL_YIELDS} stopped for a page read and asked again, "
        f"1 left for a click after {DETAIL_YIELDS} stops"
    ) in caplog.text


@contextlib.contextmanager
def _served(tmp_path: Path) -> Iterator[str]:
    """The real app on a real socket; its base URL."""

    app = create_app(
        _serving_config(tmp_path),
        live_inputs=LiveProjectionInputs(landing_state=False, change_watch=False),
    )
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=1))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    serving = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    serving.start()
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        serving.join(HANG_GUARD_SECONDS)
        sock.close()


def test_a_stream_held_open_is_no_page_read_and_never_stalls_the_queue(
    tmp_path: Path, build: _Build, details: _Details, caplog: pytest.LogCaptureFixture
) -> None:
    """The page's two streams stay open for its whole life; were they page reads, no detail
    would ever be prepared."""

    with (
        _served(tmp_path) as base,
        urlopen(f"{base}/api/stream", timeout=HANG_GUARD_SECONDS) as stream,
        urlopen(f"{base}/api/events", timeout=HANG_GUARD_SECONDS) as events,
    ):
        assert stream.readline() and events.status == 200  # both are open and serving
        assert not PAGE_READS.busy(), "an open stream counts as a page read"
        for event in details.release.values():
            event.set()
        build.opening.set()
        with caplog.at_level("INFO", logger=LOGGER):
            build.owner.offer(_selection(), build.record)
            _until(lambda: "prepared the details" in caplog.text, "the queue's line")
        assert details.computed == Counter(first=1, second=1, third=1)
        assert "0 stopped for a page read" in caplog.text


# --- a page open is served whole (D585) -----------------------------------------------------------


@pytest.fixture
def an_open_between_its_reads() -> Iterator[str]:
    """One review open whose first read was answered and whose others are not sent yet."""

    OPEN_OBSERVATIONS.clear()
    with OPEN_OBSERVATIONS.reading("open-1", ("repo", "master", "leaf")):
        pass
    yield "open-1"
    OPEN_OBSERVATIONS.clear()


def test_the_queue_starts_no_detail_while_a_page_open_is_held_between_its_reads(
    build: _Build,
    details: _Details,
    an_open_between_its_reads: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Structural: the step loop consults the open registry and waits while one is held -- not a
    window of time. No read is being answered; only the open is."""

    for event in details.release.values():
        event.set()
    polls: list[bool] = []
    wait_answered = PAGE_READS.wait_answered

    def watched(timeout: float, **only: bool) -> bool:
        answered = wait_answered(timeout, **only)
        polls.append(answered)
        return answered

    monkeypatch.setattr(PAGE_READS, "wait_answered", watched)
    build.opening.set()
    with caplog.at_level("INFO", logger=LOGGER):
        build.owner.offer(_selection(), build.record)
        # The loop has asked the registry twice and been told to wait, or it started an item.
        _until(lambda: polls.count(False) >= 2 or details.started, "the step loop")
        assert details.started == [], "the queue started a detail while a page open was held"
        OPEN_OBSERVATIONS.end(an_open_between_its_reads)
        _until(lambda: "prepared the details" in caplog.text, "the queue's line")
    assert details.computed == Counter(first=1, second=1, third=1)
    assert build.owner.waited_out == 0


def test_a_detail_that_waited_out_a_held_open_is_asked_counted_and_named(
    build: _Build,
    details: _Details,
    an_open_between_its_reads: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The deadline is the bound, never the excuse: every item asked past it is counted and
    logged with the open it waited for, so an open held after its page went is a visible fault."""

    monkeypatch.setattr(precompute_module, "DETAIL_OPEN_WAIT_SECONDS", 0.05)
    for event in details.release.values():
        event.set()
    build.opening.set()
    with caplog.at_level("INFO", logger=LOGGER):
        build.owner.offer(_selection(), build.record)
        _until(lambda: "prepared the details" in caplog.text, "the queue's line")
    assert details.computed == Counter(first=1, second=1, third=1), "the deadline did not bound"
    assert build.owner.waited_out == 3
    warned = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warned) == 3 and all(
        "waited" in line and f"{an_open_between_its_reads} held" in line for line in warned
    ), warned
    assert "3 asked after waiting out a held open" in caplog.text


def test_a_pages_stream_closing_releases_the_open_it_was_being_served(tmp_path: Path) -> None:
    """The page left between two reads of its open: its stream is the one request that says so."""

    OPEN_OBSERVATIONS.clear()
    try:
        with _served(tmp_path) as base:
            with OPEN_OBSERVATIONS.reading("open-1", ("repo", "master", "leaf"), "page-1"):
                pass
            with urlopen(f"{base}/api/stream?page=page-1", timeout=HANG_GUARD_SECONDS) as stream:
                assert stream.readline()  # connected and serving
                assert "open-1" in PAGE_READS.held(), "the open was released while its page stayed"
            _until(lambda: "open-1" not in PAGE_READS.held(), "the release at the stream's close")
    finally:
        OPEN_OBSERVATIONS.clear()


# --- changed-first, the tail still served (D574 with D576's set) -----------------------------------


def _row(key: str, changed: int, span: int = 1) -> ReviewSubjectSummary:
    return ReviewSubjectSummary(
        invariant_key=key, changed=changed, unchanged=0, not_comparable=0, changed_span=span
    )


class _Ports:
    """The opening ports a detail list reads: the comparison's overview and the move's changes."""

    def __init__(self, changes: ReviewInvariantChanges | None) -> None:
        self.asked: list[tuple[int, int]] = []
        self._changes = changes

    def trees(self, query: ReviewTreesQuery) -> ReviewTreesResult:
        rows = (_row("a", 3), _row("b", 2), _row("c", 1), _row("quiet", 0))
        return ReviewTreesResult(
            state="trees",
            repository_id=query.repository_id,
            master=query.master,
            leaf_id=query.leaf_id,
            overview=ReviewOverview(state="ready", subjects=rows),
        )

    def changes(self, *question: Any) -> ReviewInvariantChanges:
        self.asked.append(question[3:])
        assert self._changes is not None
        return self._changes

    def opening(self) -> OpeningPorts:
        return OpeningPorts(None, None, None, self.trees, None, self.changes)


def _named(*keys: str) -> ReviewInvariantChanges:
    return ReviewInvariantChanges(
        state="named", from_comparison=40, to_comparison=41, invariants=keys
    )


@pytest.mark.parametrize(
    ("previous", "changes", "order", "moved"),
    [
        (40, _named("c", "quiet"), ["c", "a", "b"], [True, False, False]),
        (None, _named("c"), ["a", "b", "c"], [False, False, False]),
        (
            40,
            ReviewInvariantChanges(state="unavailable", from_comparison=40, to_comparison=41),
            ["a", "b", "c"],
            [False, False, False],
        ),
    ],
    ids=["the-moves-set-first", "no-earlier-build", "the-move-unknown"],
)
def test_the_moves_changed_invariants_come_first_and_every_other_one_still_follows(
    previous: int | None,
    changes: ReviewInvariantChanges,
    order: list[str],
    moved: list[bool],
) -> None:
    ports = _Ports(changes)
    listed = detail_answers(ports.opening(), _selection(), 41, previous)
    assert [key for key, _ask, _moved in listed] == order, "the order, or the coverage, changed"
    assert [flag for _key, _ask, flag in listed] == moved
    assert ports.asked == ([] if previous is None else [(previous, 41)])


def test_each_build_names_the_leafs_previous_built_number_to_its_details(
    tmp_path: Path, details: _Details
) -> None:
    for event in details.release.values():
        event.set()
    seen: list[tuple[int, int | None]] = []
    owner = _Precompute(tmp_path, _record(41, code_candidate="4" * 40))
    owner.opening.set()

    def listed(selection: LeafSelection, number: int, previous: int | None) -> list[Any]:
        seen.append((number, previous))
        return details.listed(selection, number, previous)

    owner.bind_details(listed)
    try:
        owner.offer(_selection(), _record(41, code_candidate="4" * 40))
        _until(lambda: len(seen) == 1 and sum(details.computed.values()) == 3, "the first queue")
        owner.offer(_selection(), _record(42, code_candidate="5" * 40))
        _until(lambda: len(seen) == 2, "the second queue")
    finally:
        owner.shutdown()
        for thread in owner._threads:
            thread.join(HANG_GUARD_SECONDS)
    assert seen == [(41, None), (42, 41)]


# --- the order is the page's own (D585) ----------------------------------------------------------


@pytest.mark.parametrize("arrives", ["before the queue starts", "after the first is asked"])
def test_the_queue_prepares_in_the_order_the_page_drew_and_reorders_what_is_left(
    build: _Build, details: _Details, caplog: pytest.LogCaptureFixture, arrives: str
) -> None:
    """Structural: the step loop consults the page's drawn order before each item; ids it lists
    nothing for are dropped and counted, and a late order is counted as what it moved."""

    drawn = ("third", "a-stranger", "second")
    build.opening.set()
    with caplog.at_level("INFO", logger=LOGGER):
        if arrives == "before the queue starts":
            build.owner.order(_selection(), build.record.number, "open-1", drawn)
            build.owner.order(_selection(), build.record.number + 1, "open-0", ("first",))
            build.owner.order(_selection(), build.record.number, "open-1", drawn)  # the latest wins
        build.owner.offer(_selection(), build.record)
        _until(lambda: details.started, "the first detail")
        if arrives == "after the first is asked":
            build.owner.order(_selection(), build.record.number, "open-1", drawn)
        for event in details.release.values():
            event.set()
        _until(lambda: "prepared the details" in caplog.text, "the queue's line")
    if arrives == "before the queue starts":
        assert details.started == ["third", "second", "first"], "the queue kept its own order"
        line = "reached the queue before it started: 2 of the 3 left moved"
        assert build.owner.reordered == 0
    else:
        assert details.started == ["first", "third", "second"], "the late order was not applied"
        line = "reached the queue after 1 were asked: 2 of the 2 left moved"
        assert build.owner.reordered == 2
    assert f"the page's order of comparison #41 (open open-1) {line}" in caplog.text
    assert "1 of its 3 invariants dropped, not among the comparison's changed" in caplog.text
    assert details.computed == Counter(first=1, second=1, third=1)


def test_the_order_route_hands_the_drawn_order_over_and_bounds_what_it_takes() -> None:
    from agents_remember.serving.review_trees import (  # noqa: PLC0415 -- the route under test
        KNOWLEDGE_REVIEW_DETAIL_ORDER_ROUTE,
        register_review_detail_order_route,
    )
    from fastapi import FastAPI  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    handed: list[tuple[object, ...]] = []
    wired, unwired = FastAPI(), FastAPI()
    register_review_detail_order_route(wired, lambda *drawn: handed.append(drawn))
    register_review_detail_order_route(unwired, None)
    body = {"repo": "r", "master": "m", "leaf": "l", "comparison": 7, "open": "o", "order": ["b"]}
    assert TestClient(wired).post(KNOWLEDGE_REVIEW_DETAIL_ORDER_ROUTE, json=body).status_code == 204
    assert handed == [("r", "m", "l", 7, "o", ("b",))]
    assert (
        TestClient(unwired).post(KNOWLEDGE_REVIEW_DETAIL_ORDER_ROUTE, json=body).status_code == 503
    )
    for bad in ({"comparison": 0}, {"order": ["x" * 65]}, {"order": ["k"] * 501}):
        answer = TestClient(wired).post(KNOWLEDGE_REVIEW_DETAIL_ORDER_ROUTE, json={**body, **bad})
        assert answer.status_code == 422, bad
    assert len(handed) == 1, "a refused order was handed over"
