"""A turn end's capture computes the leaf-wide view before the page asks (D569 mean 3).

The moved watcher hands each capture it makes at a turn end to :class:`OverviewPrecompute`, and
only those: a page open's capture is the page's own. The precompute computes the page's own
leaf-wide view for exactly the captured trees, records nothing, and the page's later comparison of
the same trees finds the view kept under the memo key no comparison number enters.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application import (
    review_leaf_view_memo,
    review_overview_precompute,
    review_tree_knowledge,
)
from agents_remember.application import review_tree_knowledge as knowledge
from agents_remember.application.knowledge_gate.memo import GateMemoKey
from agents_remember.application.review_leaf_view_memo import (
    LEAF_VIEW_MEMO,
    LeafViewKey,
    LeafViewParts,
)
from agents_remember.application.review_overview_precompute import OverviewPrecompute, trees_digest
from agents_remember.application.review_planned_comparison import explainer_application_ports
from agents_remember.application.review_tree_comparison import comparison_directory
from agents_remember.kernel import read_cancellation
from agents_remember.kernel.recorded_reads import file_identity
from agents_remember.kernel.reviewer_worklist_process import ReviewerWorklistProcesses
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.serving.explainer_move_watch import MoveNoticeWatch, WatchOptions
from agents_remember.serving.explainer_watcher import (
    CapturedComparison,
    ComparisonMeasurement,
    GitComparisonOwner,
    LeafSelection,
    MoveNoticeChannel,
)
from agents_remember_test_support.testing.review_git_trees import LEAF, MASTER, REPO
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from test_explainer_moved_watcher import (
    WORKER,
    FakeCounter,
    FakeOwner,
    _binding,
    _config,
    _environment,
    _generation,
    _record,
    _selection,
    _task_root,
    _worker_receipt,
)
from test_review_git_trees import world
from test_review_read_latency import _live

pytestmark = pytest.mark.evidence_unit

__all__ = ["world"]


@pytest.fixture(autouse=True)
def _fresh_memo() -> Iterator[None]:
    LEAF_VIEW_MEMO.clear()
    yield
    LEAF_VIEW_MEMO.clear()


def _handing_watch(
    tmp_path: Path, owner: FakeOwner
) -> tuple[MoveNoticeWatch, list[tuple[LeafSelection, ReviewTreeComparisonRecord]]]:
    config = _config(tmp_path)
    task_root = _task_root(tmp_path)
    _environment(config, _binding(_record(7)))
    _generation(config)
    _worker_receipt(task_root)
    handed: list[tuple[LeafSelection, ReviewTreeComparisonRecord]] = []
    ports = explainer_application_ports(lambda *capture: handed.append(capture)).watcher
    watch = MoveNoticeWatch(
        config,
        ports,
        options=WatchOptions(
            measurement=ComparisonMeasurement(owner=owner, counter=FakeCounter(3)),
            channel=MoveNoticeChannel(),
            cooldown=0,
        ),
    )
    return watch, handed


def test_a_turn_end_hands_its_capture_on_and_a_page_open_does_not(tmp_path: Path) -> None:
    captured = _record(1, code_candidate="e" * 40)
    owner = FakeOwner(captured)
    watch, handed = _handing_watch(tmp_path, owner)

    assert watch.page_opened(_selection()) is None
    assert (owner.calls, handed) == (0, []), "a page open captures nothing and hands nothing on"

    assert watch.after_turn_ends([WORKER])
    assert owner.calls == 1, "the turn end captures once, through the watch's own owner"
    assert handed == [(_selection(), captured)]


def test_only_the_latest_waiting_capture_of_a_leaf_is_computed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running at most one, pending at most one: a third change replaces the pending one."""
    started, release = threading.Event(), threading.Event()
    computed: list[int] = []

    def compute(_self: OverviewPrecompute, _selection: LeafSelection, record) -> None:
        computed.append(record.number)
        started.set()
        assert release.wait(HANG_GUARD_SECONDS)

    monkeypatch.setattr(OverviewPrecompute, "compute", compute)
    precompute = OverviewPrecompute(_config(tmp_path), ReviewerWorklistProcesses())
    try:
        # Three captures of three different tree states (identical trees build nothing).
        precompute.offer(_selection(), _record(1, code_candidate="1" * 40))
        assert started.wait(HANG_GUARD_SECONDS)
        started.clear()
        precompute.offer(_selection(), _record(2, code_candidate="2" * 40))
        precompute.offer(_selection(), _record(3, code_candidate="3" * 40))
        building, pending = precompute._building, list(precompute._pending.values())
        assert building is not None and building.record.number == 1 and building.token.stale
        assert [record.number for _selection, record in pending] == [3], "appended, not replaced"
        assert precompute.superseded == 1
        release.set()
        assert started.wait(HANG_GUARD_SECONDS)
    finally:
        precompute.shutdown()
        release.set()
    assert computed == [1, 3], "a capture replaced before it started is never computed"


def test_a_failed_precompute_leaves_the_next_capture_computed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    done = threading.Event()
    computed: list[int] = []

    def compute(_self: OverviewPrecompute, _selection: LeafSelection, record) -> None:
        computed.append(record.number)
        if record.number == 1:
            raise RuntimeError("unreadable side")
        done.set()

    monkeypatch.setattr(OverviewPrecompute, "compute", compute)
    precompute = OverviewPrecompute(_config(tmp_path), ReviewerWorklistProcesses())
    try:
        precompute.offer(_selection(), _record(1))
        precompute.offer(LeafSelection(REPO, MASTER, "another-leaf"), _record(2))
        assert done.wait(HANG_GUARD_SECONDS)
    finally:
        precompute.shutdown()
    assert sorted(computed) == [1, 2]


def test_the_page_finds_kept_the_view_its_turn_end_capture_computed(
    world, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.edit()
    selection = LeafSelection(REPO, MASTER, LEAF)
    captured = GitComparisonOwner(world.config, explainer_application_ports().watcher).capture(
        selection
    )
    assert isinstance(captured, CapturedComparison)
    records = comparison_directory(world.task_root, LEAF)
    processes = ReviewerWorklistProcesses()
    try:
        OverviewPrecompute(world.config, processes).compute(selection, captured.record)
        assert len(LEAF_VIEW_MEMO) == 1
        assert not list(records.glob("*.json")), "the precompute records nothing"

        computed: list[str] = []
        compute = review_tree_knowledge._compute_leaf_parts

        def counted(*args, **kwargs):
            computed.append("computed")
            return compute(*args, **kwargs)

        monkeypatch.setattr(review_tree_knowledge, "_compute_leaf_parts", counted)
        contract, trees = _live(world)
        assert trees.record.number == 1 and list(records.glob("*.json"))
        parts = review_tree_knowledge._leaf_wide_parts(contract, trees, processes=processes)
        assert not isinstance(parts, review_tree_knowledge.ReviewRefusal)
        assert computed == [], "the page's comparison of the same trees is served the kept view"
    finally:
        processes.shutdown()


def test_the_precompute_computes_through_the_pages_own_leaf_wide_view() -> None:
    assert review_overview_precompute._leaf_wide_parts is review_tree_knowledge._leaf_wide_parts


def _view_key(task_root: Path) -> LeafViewKey:
    gate = GateMemoKey("c" * 40, "m" * 40, "contract@sha256:0", None, None, "sha256:0", "build")
    return LeafViewKey(gate, "a" * 40, "b" * 40, "d" * 40, task_root.as_posix(), ())


def test_a_view_kept_longer_than_fifteen_minutes_is_served_while_its_reads_are_identical(
    tmp_path: Path,
) -> None:
    """D570: a precomputed view is kept by identity alone; age never discards it, a change does."""

    read = tmp_path / "outside.json"
    read.write_text("{}", encoding="utf-8")
    key = _view_key(tmp_path / "task")
    parts = LeafViewParts(worklist=cast(Any, "worklist"))
    review_leaf_view_memo.remember(key, parts, {str(read): file_identity(read)})

    # load-independent: the clock is moved past the old fifteen-minute bound, never waited on.
    later = time.monotonic() + 15 * 60 + 1
    with mock.patch.object(time, "monotonic", return_value=later):
        assert review_leaf_view_memo.remembered(key) is parts
        read.write_text('{"moved": true}', encoding="utf-8")
        assert review_leaf_view_memo.remembered(key) is None, "a changed read is computed again"


# --- every change source builds off the open (D569) ------------------------------------------------


def _watched(  # noqa: PLR0913 -- the fixture, its captures and the three bounds under test
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    captures: list[ReviewTreeComparisonRecord],
    *,
    latest: ReviewTreeComparisonRecord | None = None,
    quiet: float = 0.2,
    ceiling: float = 0.6,
    mint=None,
) -> tuple[OverviewPrecompute, list[float], list[int]]:
    """A build owner whose capture answers ``captures`` in turn and whose compute is recorded."""
    captured_at: list[float] = []
    computed: list[int] = []

    def capture(_selection: LeafSelection) -> ReviewTreeComparisonRecord:
        captured_at.append(time.monotonic())
        return captures[min(len(captured_at), len(captures)) - 1]

    def compute(_self: OverviewPrecompute, _selection: LeafSelection, record) -> None:
        computed.append(record.number)

    monkeypatch.setattr(OverviewPrecompute, "compute", compute)
    owner = OverviewPrecompute(
        _config(tmp_path),
        ReviewerWorklistProcesses(),
        capture=capture,
        latest=lambda _selection: latest,
        mint=mint,
        quiet=quiet,
        ceiling=ceiling,
    )
    return owner, captured_at, computed


def _until(condition, what: str) -> None:
    deadline = time.monotonic() + HANG_GUARD_SECONDS
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.01)


def test_a_watched_change_is_captured_at_its_quiet_moment_and_built_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The background trigger: ten rapid edits wait for quiet and give one build, two at most."""
    owner, captured_at, computed = _watched(
        tmp_path, monkeypatch, [_record(5, code_candidate="5" * 40)]
    )
    try:
        changed = time.monotonic()
        for _ in range(10):  # ten rapid edits, well inside the quiet moment of 0.2 s
            owner.changed(_selection())
            # load-independent: the edit cadence, not a bound
            time.sleep(0.01)
        _until(lambda: computed, "the build")
        # load-independent: a lower bound; load only makes the capture later
        assert captured_at[0] - changed >= 0.2
        assert 1 <= len(captured_at) <= 2 and computed[:1] == [5] and len(computed) <= 2
    finally:
        owner.shutdown()


def test_an_open_inside_the_quiet_moment_captures_at_once_and_starts_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interactive trigger: a page open skips the quiet moment a watched change waits for."""
    never = 100 * HANG_GUARD_SECONDS  # no quiet moment or ceiling comes within the test
    owner, captured_at, computed = _watched(
        tmp_path, monkeypatch, [_record(5, code_candidate="5" * 40)], quiet=never, ceiling=never
    )
    try:
        owner.changed(_selection())
        owner.opened(_selection())
        _until(lambda: computed, "the open's build")
        assert len(captured_at) == 1 and computed == [5]
    finally:
        owner.shutdown()


@pytest.mark.parametrize("first", ["a watched change", "an open"])
def test_trees_another_reader_minted_are_built_once_and_never_while_built_or_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first: str
) -> None:
    """Another reader may mint the trees first -- the page's open, or the detail queue's live
    review, whose witness captures the working views. The capture finds them the latest record
    and builds them anyway, once (D571): an open's capture racing the build, and one after it, build
    nothing again."""
    latest = _record(8, code_candidate="8" * 40)
    owner, captured_at, computed = _watched(
        tmp_path, monkeypatch, [_record(0, code_candidate="8" * 40)], latest=latest
    )
    entered, release = threading.Event(), threading.Event()
    recorded = OverviewPrecompute.compute

    def held(self: OverviewPrecompute, selection: LeafSelection, record) -> None:
        recorded(self, selection, record)
        entered.set()
        assert release.wait(HANG_GUARD_SECONDS)

    monkeypatch.setattr(OverviewPrecompute, "compute", held)
    try:
        (owner.changed if first == "a watched change" else owner.opened)(_selection())
        assert entered.wait(HANG_GUARD_SECONDS), f"{first} did not build the latest recorded trees"
        owner.opened(_selection())  # the page's open races the build of the same trees
        _until(lambda: len(captured_at) == 2, "the racing open's capture")
        release.set()
        owner.changed(_selection())  # a later watched change of the same trees, built already
        _until(lambda: len(captured_at) == 3, "the later capture")
        # load-independent: no build is expected; the window is the assertion
        time.sleep(0.3)
        assert computed == [8], "the same trees were built twice"
    finally:
        release.set()
        owner.shutdown()


def test_continuous_editing_is_captured_by_the_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, captured_at, _computed = _watched(
        tmp_path, monkeypatch, [_record(5, code_candidate="5" * 40)], quiet=0.3, ceiling=0.6
    )
    try:
        deadline = time.monotonic() + HANG_GUARD_SECONDS
        # An edit every 50 ms, kept up until a capture is seen: the leaf never has a quiet moment
        # (0.3 s), so only the ceiling can have captured it.
        while not captured_at:
            assert time.monotonic() < deadline, "continuous edits starved the capture"
            owner.changed(_selection())
            # load-independent: the edit cadence, not a bound; the loop ends on the capture
            time.sleep(0.05)
    finally:
        owner.shutdown()


def test_identical_trees_mint_nothing_and_are_built_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    latest = _record(4, code_candidate="4" * 40)
    minted: list[int] = []

    def mint(_selection: LeafSelection, draft: ReviewTreeComparisonRecord):
        minted.append(draft.number)
        return draft

    owner, _captured_at, computed = _watched(
        tmp_path, monkeypatch, [_record(9, code_candidate="4" * 40)], latest=latest, mint=mint
    )
    try:
        owner.changed(_selection())
        # The latest record's trees, built nowhere yet: built once, and minted again never.
        _until(lambda: computed == [4], "the build of the latest recorded trees")
        assert minted == [], "the latest recorded trees were minted again"
        owner.offer(_selection(), _record(6, code_candidate="6" * 40))
        _until(lambda: computed == [4, 6], "the new trees' build")
        owner.offer(_selection(), _record(7, code_candidate="6" * 40))
        # load-independent: no build is expected; the window is the assertion
        time.sleep(0.3)
        assert computed == [4, 6], "trees already built were built again"
    finally:
        owner.shutdown()


def test_a_capture_is_minted_at_capture_and_its_minted_number_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One identity: the build builds the number its capture minted, as the page would mint it."""

    numbers = iter((31, 32))
    drafts: list[str] = []

    def mint(_selection: LeafSelection, draft: ReviewTreeComparisonRecord):
        drafts.append(draft.code_candidate.tree)
        return draft.model_copy(update={"number": next(numbers)})

    owner, _captured_at, computed = _watched(
        tmp_path, monkeypatch, [_record(1, code_candidate="7" * 40)], mint=mint
    )
    try:
        owner.changed(_selection())  # a watched change
        _until(lambda: computed == [31], "the watched capture's minted build")
        owner.offer(_selection(), _record(1, code_candidate="8" * 40))  # a turn end's draft
        _until(lambda: computed == [31, 32], "the turn end's minted build")
        assert drafts == ["7" * 40, "8" * 40]
    finally:
        owner.shutdown()


def test_the_build_computes_every_opening_answer_for_its_minted_number_with_the_view_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[object] = []
    monkeypatch.setattr(review_overview_precompute, "recorded_leaf_contract", lambda *_a: object())
    monkeypatch.setattr(review_overview_precompute, "drafted_review_trees", lambda *_a: object())
    monkeypatch.setattr(
        review_overview_precompute,
        "_leaf_wide_parts",
        lambda *_a, **_k: order.append("leaf-wide view"),
    )
    alone = OverviewPrecompute(_config(tmp_path), ReviewerWorklistProcesses())
    alone.compute(_selection(), _record(31, code_candidate="7" * 40))
    assert order == ["leaf-wide view"], "with no answers bound the build prepares the view alone"

    def answers(selection: LeafSelection, number: int, leaf_wide: Callable[[], None]) -> None:
        order.append(("before the view", selection.leaf_id, number))
        leaf_wide()
        order.append(("after the view", number))

    owner = OverviewPrecompute(_config(tmp_path), ReviewerWorklistProcesses())
    owner.bind_answers(answers)
    owner.compute(_selection(), _record(31, code_candidate="7" * 40))
    assert order[1:] == [
        ("before the view", _selection().leaf_id, 31),
        "leaf-wide view",
        ("after the view", 31),
    ]


def test_the_builds_own_threads_run_at_lowered_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    def compute(_self: OverviewPrecompute, _selection: LeafSelection, _record) -> None:
        seen.append(os.getpriority(os.PRIO_PROCESS, threading.get_native_id()))

    monkeypatch.setattr(OverviewPrecompute, "compute", compute)
    owner = OverviewPrecompute(_config(tmp_path), ReviewerWorklistProcesses())
    try:
        owner.offer(_selection(), _record(3, code_candidate="3" * 40))
        _until(lambda: seen, "the build")
    finally:
        owner.shutdown()
    assert seen[0] >= review_overview_precompute.BUILD_NICENESS


def test_a_newer_capture_supersedes_the_build_in_flight_and_the_latest_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    started = threading.Event()
    computed: list[int] = []
    kept: list[int] = []

    def compute(_self: OverviewPrecompute, _selection: LeafSelection, record) -> None:
        computed.append(record.number)
        started.set()
        deadline = time.monotonic() + HANG_GUARD_SECONDS
        while record.number == 1 and time.monotonic() < deadline:
            read_cancellation.raise_if_read_cancelled()  # the build's safe points
            time.sleep(0.01)
        kept.append(record.number)

    monkeypatch.setattr(OverviewPrecompute, "compute", compute)
    owner = OverviewPrecompute(_config(tmp_path), ReviewerWorklistProcesses())
    try:
        with caplog.at_level(
            "INFO", logger="agents_remember.application.review_overview_precompute"
        ):
            owner.offer(_selection(), _record(1, code_candidate="1" * 40))
            assert started.wait(HANG_GUARD_SECONDS)
            owner.offer(_selection(), _record(2, code_candidate="2" * 40))
            _until(lambda: kept == [2], "the latest capture's build")
    finally:
        owner.shutdown()
    assert computed == [1, 2] and kept == [2], "the superseded build kept something"
    assert owner.superseded == 1
    first = trees_digest(_record(1, code_candidate="1" * 40))
    second = trees_digest(_record(2, code_candidate="2" * 40))
    assert f"trees {first} for its newer trees {second}" in caplog.text
    assert f"build of {_selection().leaf_id} trees {first} kept nothing" in caplog.text


def test_an_open_during_a_build_joins_it_and_a_cancelled_build_still_answers_the_open(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """One computation for one view key in flight; the build leaving does not stop the open's."""
    gate, entered = threading.Event(), threading.Event()
    computations: list[int] = []
    parts = cast(Any, mock.Mock(spec=LeafViewParts))

    def computed(*_args: Any, **_kwargs: Any) -> Any:
        computations.append(1)
        entered.set()
        assert gate.wait(HANG_GUARD_SECONDS)
        return parts, False, {}

    joined: list[int] = []
    join = read_cancellation._Flight.join

    def watched(flight: Any, asker: Any) -> bool:
        taken = join(flight, asker)
        if taken:
            joined.append(1)
        return taken

    key = cast(Any, ("view", "key"))
    monkeypatch.setattr(knowledge, "leaf_view_key", lambda *_a: key)
    monkeypatch.setattr(knowledge, "remembered", lambda _key: None)
    monkeypatch.setattr(knowledge, "moved_inputs", lambda *_a: ())
    monkeypatch.setattr(knowledge, "_compute_leaf_parts", computed)
    monkeypatch.setattr(knowledge, "deepcopy", lambda value: value, raising=False)
    monkeypatch.setattr(read_cancellation._Flight, "join", watched)
    contract = cast(Any, mock.Mock(leaf_id=LEAF))
    answers: list[tuple[str, Any]] = []
    left = threading.Event()

    def build() -> None:
        with read_cancellation.cancellable_read(left):
            answers.append(("build", knowledge._leaf_wide_parts(contract, cast(Any, None))))

    def open_page() -> None:
        answers.append(("open", knowledge._leaf_wide_parts(contract, cast(Any, None))))

    builder = threading.Thread(target=build)
    builder.start()
    assert entered.wait(HANG_GUARD_SECONDS)
    with caplog.at_level("INFO", logger=knowledge.__name__):
        opener = threading.Thread(target=open_page)
        opener.start()
        _until(lambda: joined, "the open joining the build")
        left.set()  # a newer change cancels the build; the open still waits on it
        gate.set()
        opener.join(HANG_GUARD_SECONDS)
        builder.join(HANG_GUARD_SECONDS)
    assert computations == [1], "the open started a second computation"
    assert ("open", parts) in answers
    assert f"joined the leaf-wide view in flight for {LEAF}; waited" in caplog.text


@pytest.mark.parametrize("view", ["computed", "found kept"])
def test_a_superseded_build_whose_child_landed_stores_nothing_and_answers_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, view: str
) -> None:
    """Stale when its view is had: no leaf-wide view is kept and no answer is computed (D569)."""
    token = read_cancellation.BuildToken()
    parts = cast(Any, mock.Mock(spec=LeafViewParts))

    def landed(*_args: Any, **_kwargs: Any) -> Any:
        token.mark_stale()  # the newer change arrived while the child computed; it landed anyway
        return parts, True, {}

    def kept(_key: Any) -> Any:
        if view == "computed":
            return None
        token.mark_stale()
        return parts

    key = cast(Any, ("view", "key"))
    monkeypatch.setattr(knowledge, "leaf_view_key", lambda *_a: key)
    monkeypatch.setattr(knowledge, "remembered", kept)
    monkeypatch.setattr(knowledge, "moved_inputs", lambda *_a: ())
    monkeypatch.setattr(knowledge, "_compute_leaf_parts", landed)
    remembered: list[object] = []
    monkeypatch.setattr(knowledge, "remember", lambda *args: remembered.append(args))
    monkeypatch.setattr(review_overview_precompute, "recorded_leaf_contract", lambda *_a: object())
    monkeypatch.setattr(review_overview_precompute, "drafted_review_trees", lambda *_a: object())
    answered: list[int] = []
    owner = OverviewPrecompute(_config(tmp_path), ReviewerWorklistProcesses())

    def answers(_selection: LeafSelection, number: int, leaf_wide: Callable[[], None]) -> None:
        leaf_wide()
        answered.append(number)

    owner.bind_answers(answers)
    contract = cast(Any, mock.Mock(leaf_id=LEAF))
    monkeypatch.setattr(
        review_overview_precompute,
        "_leaf_wide_parts",
        lambda *_a, **_k: knowledge._leaf_wide_parts(contract, cast(Any, None)),
    )
    with read_cancellation.cancellable_read(token), pytest.raises(read_cancellation.ReadCancelled):
        owner.compute(_selection(), _record(31, code_candidate="7" * 40))
    assert remembered == [] and answered == [], "the stale build stored or answered something"


def test_the_builds_worklist_children_run_at_lowered_priority() -> None:
    processes = ReviewerWorklistProcesses(niceness=10)
    try:
        child = processes._launch(dict(os.environ), os.getcwd())
        assert os.getpriority(os.PRIO_PROCESS, child.pid) == os.getpriority(os.PRIO_PROCESS, 0) + 10
    finally:
        processes.shutdown()
