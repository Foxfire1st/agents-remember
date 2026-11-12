"""The background build computes exactly the answers the review page opens with (D569, D570).

One enumeration (:data:`~agents_remember.cli.dashboard.OPENING_ORDER`) drives the build. The page's
own six opening reads, sent as the page sends them through the dashboard's real routes, then name
exactly those answers and read every one: the build and the page asked one question each, under
one key. A page read arriving while the build computes the same answer waits for it, so it is
computed once. The top bar's staleness is kept on every read it made, and each named input that
moves computes it again. An object another worktree adds to the object store they all share
changes nothing an answer read, so every opening answer stays kept, whether it lands after the
build or while the build computes. Real Git repositories and the real producers throughout.
"""

from __future__ import annotations

import json
import subprocess
import threading
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
)
from agents_remember.application.review_evidence_packet import (
    COMPARISON_OWNER,
    comparison_reference,
)
from agents_remember.application.review_frame_store import (
    begin_draft,
    frame_scope,
    publish_revision,
)
from agents_remember.canvas.explainer_comparison_inputs import LeafScope
from agents_remember.canvas.explainer_grades import grades_path
from agents_remember.canvas.explainer_paths import EXPLAINER_FRAME, leaf_workspace
from agents_remember.canvas.explainer_state import ExplainerBinding, write_binding
from agents_remember.cli.dashboard import (
    LEAF_WIDE_VIEW,
    OPENING_ANSWERS,
    OPENING_ORDER,
    detail_answers,
    opening_answers,
    opening_ports,
    serving_collaborators,
)
from agents_remember.kernel import read_cancellation
from agents_remember.kernel.primitives.runtime_config import RepositoryScope
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.knowledge.review_frame import FrameAuthoring, FrameBinding, FrameLimits
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.serving import explainer
from agents_remember.serving import review_answer_reuse as answer_reuse
from agents_remember.serving.app import create_app
from agents_remember.serving.explainer_watcher import LeafSelection
from agents_remember.serving.review_answer_reuse import RecordedAnswerReuse
from agents_remember.serving.review_open_observation import OPEN_HEADER, OPEN_OBSERVATIONS
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember_test_support.testing.review_git_trees import (
    CODE_FILE,
    CODE_V1,
    INVARIANT,
    LEAF,
    MASTER,
    REPO,
    World,
    build_world,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from canvas_host_test_support import ASSETS, packet
from fastapi.testclient import TestClient

AT = "2026-10-26T09:00:00Z"
STALE_ROUTE = f"/api/explainer/{REPO}/{MASTER}/{LEAF}/stale"


class _Opened:
    def __init__(self, world: World) -> None:
        self.world = world
        self.collaborators = serving_collaborators(world.config)
        # No lifespan is entered: no background loop runs, and the build is the test's to start.
        self.client = TestClient(create_app(world.config, collaborators=self.collaborators))
        resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
        assert isinstance(resolved, ReviewCandidateResolution) and resolved.trees is not None
        self.record = resolved.trees.record
        _publish(world.task_root, self.record)
        self.ports = opening_ports(world.config, self.collaborators)

    def build(self, steps: list[str] | None = None) -> None:
        """The build's opening answers for the comparison, as the background build runs them."""

        selection = LeafSelection(REPO, MASTER, LEAF)
        view = (lambda: steps.append(LEAF_WIDE_VIEW)) if steps is not None else (lambda: None)
        opening_answers(self.ports, selection, self.record.number, view)

    def page(self, open_id: str, reads: tuple[str, ...] | None = None) -> list[tuple[str, str]]:
        """The page's opening reads as the page sends them; the answers its open noted.

        These are the page's own six requests (``explainerApi.ts`` and the review panel's reads),
        written out here rather than taken from the build's enumeration they are checked against.
        """

        query = {"repo": REPO, "master": MASTER, "leaf": LEAF}
        number = str(self.record.number)
        sent = {
            "live intent": ("/api/review/intent", query),
            "live entries": ("/api/review/intent/entries", query),
            "stale": (STALE_ROUTE, {"comparisonNumber": number, "comparisonOwner": "recorded"}),
            "summary": ("/api/review/intent/summary", {**query, "comparison": number}),
            "overview": ("/api/review/trees", {**query, "comparison": number, "view": "overview"}),
            "files": ("/api/review/trees", {**query, "comparison": number, "lane": "files"}),
        }
        for name in sent if reads is None else reads:
            route, params = sent[name]
            response = self.client.get(route, params=params, headers={OPEN_HEADER: open_id})
            assert response.status_code == 200, (name, response.text)
        return [(name, how) for name, how, _ in OPEN_OBSERVATIONS.entry(open_id).answers]


def _publish(task_root: Path, comparison: ReviewTreeComparisonRecord) -> None:
    """One retained explainer revision bound to ``comparison``, as a build publishes it."""

    scope = frame_scope(task_root, LEAF, EXPLAINER_FRAME)
    begin_draft(
        scope,
        working_directory=task_root.parent / "authoring" / "req-1",
        request_id="req-1",
        authoring=FrameAuthoring(request_id="req-1", session_id="seed", started_at=AT),
        focus="the explained change",
        assets=dict(ASSETS),
        limits=FrameLimits(),
        now=AT,
    )
    publication = publish_revision(
        scope,
        request_id="req-1",
        entrypoint="index.html",
        evidence=packet(comparison),
        supplemental=(),
        created_at=AT,
        limits=FrameLimits(),
    )
    assert publication.manifest is not None


@pytest.fixture
def opened(tmp_path: Path) -> Iterator[_Opened]:
    reset_worktree_services()
    OPEN_OBSERVATIONS.clear()
    world = build_world(tmp_path)
    world.edit()
    # The explainer's environment is admitted only for a repository the settings name.
    scope = RepositoryScope(repo_id=REPO, path=world.code, memory_root=world.memory)
    world.config = replace(world.config, repositories={REPO: scope})
    made = _Opened(world)
    yield made
    shutdown = made.collaborators.review_trees_shutdown
    assert shutdown is not None
    shutdown()
    OPEN_OBSERVATIONS.clear()
    reset_worktree_services()


def test_the_build_computes_exactly_what_the_page_opens_with_and_the_page_reads_it_all(opened):
    steps: list[str] = []
    with OPEN_OBSERVATIONS.reading("the-build"):  # the build's answers, named as an open's are
        opened.build(steps)
    built = [
        (name, how)
        for name, how, _ in OPEN_OBSERVATIONS.entry("the-build").answers
        if name in OPENING_ANSWERS
    ]
    order = [step for step in OPENING_ORDER if step != LEAF_WIDE_VIEW]
    assert built == [(name, "computed") for name in order], "each opening answer, once, in order"
    assert steps == [LEAF_WIDE_VIEW]

    page = opened.page("the-page")
    assert {name for name, _ in page} == set(OPENING_ANSWERS), "the page opens with another set"
    assert all(how == "read" for _name, how in page), page  # one key: the build's answer is read


def test_a_page_read_arriving_while_the_build_computes_its_answer_waits_and_it_is_computed_once(
    opened, monkeypatch: pytest.MonkeyPatch
):
    produced: Counter[str] = Counter()
    entered, released, waiting = threading.Event(), threading.Event(), threading.Event()
    original = RecordedAnswerReuse.answer

    def answer(self: Any, key: Any, sources: Any, produce: Any, **options: Any) -> Any:
        if key[:2] != ("live-kept", "intent"):
            return original(self, key, sources, produce, **options)

        def gated() -> Any:  # the live intent's computation, held until the page's read waits
            produced["intent"] += 1
            entered.set()
            assert released.wait(HANG_GUARD_SECONDS)
            return produce()

        return original(self, key, sources, gated, **options)

    original_wait = read_cancellation._Flight.wait

    def wait(self: Any, asker: Any) -> Any:
        waiting.set()  # the page's read joined the build's flight and waits for its answer
        return original_wait(self, asker)

    monkeypatch.setattr(RecordedAnswerReuse, "answer", answer)
    monkeypatch.setattr(read_cancellation._Flight, "wait", wait)
    build = threading.Thread(target=opened.build, daemon=True)
    build.start()
    assert entered.wait(HANG_GUARD_SECONDS)
    page: list[list[tuple[str, str]]] = []
    reader = threading.Thread(
        target=lambda: page.append(opened.page("the-page", ("live intent",))), daemon=True
    )
    reader.start()
    assert waiting.wait(HANG_GUARD_SECONDS)
    released.set()
    reader.join(HANG_GUARD_SECONDS)
    build.join(HANG_GUARD_SECONDS)
    assert page == [[("live intent", "joined")]]
    assert produced == Counter(intent=1)


# --- the top bar's staleness, kept on every read it made ------------------------------------------


def _counted_stale(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    computed: Counter[str] = Counter()
    original = explainer._stale_answer

    def counted(*args: Any, **kwargs: Any) -> Any:
        computed["stale"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(explainer, "_stale_answer", counted)
    return computed


def _stale(opened: _Opened) -> dict[str, Any]:
    scope = LeafScope(REPO, MASTER, LEAF)
    return explainer.stale_view(
        opened.ports.stale, scope, selector=(opened.record.number, "recorded")
    )


def _add_reference_note(opened: _Opened) -> None:
    note = opened.world.task_root / "notes" / "reference" / LEAF / "messages" / "a-message.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("a message between roles\n")


def _edit_reference_note(opened: _Opened) -> None:
    _add_reference_note(opened)
    _stale(opened)  # the note is now one of the answer's inputs
    note = opened.world.task_root / "notes" / "reference" / LEAF / "messages" / "a-message.md"
    note.write_text("the message, edited\n")


def _write_grades(opened: _Opened) -> None:
    path = grades_path(opened.world.task_root, LEAF)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"leaf_id": LEAF}))


def _bind_an_environment(opened: _Opened) -> None:
    workspace = leaf_workspace(opened.world.config, REPO, MASTER, LEAF)
    workspace.mkdir(parents=True, exist_ok=True)
    binding = FrameBinding.from_record(
        opened.record,
        comparison_owner=COMPARISON_OWNER,
        comparison_reference=comparison_reference(opened.record),
    )
    write_binding(
        workspace,
        ExplainerBinding(
            binding=binding,
            snapshot="snapshot-1",
            prepared_at=AT,
            packet_digest="sha256:" + "0" * 64,
        ),
    )


def _write_the_leaf_document(opened: _Opened) -> None:
    document = opened.world.task_root / "01_the-leaf.json"
    document.write_text(json.dumps({"id": LEAF, "slug": "01_the-leaf", "requirements": []}))


@pytest.mark.parametrize(
    "move",
    [
        _add_reference_note,
        _edit_reference_note,
        _write_grades,
        _bind_an_environment,
        _write_the_leaf_document,
    ],
    ids=lambda move: move.__name__.lstrip("_"),
)
def test_the_staleness_is_kept_while_every_read_holds_and_each_named_input_computes_it_again(
    opened, monkeypatch: pytest.MonkeyPatch, move: Callable[[_Opened], None]
):
    computed = _counted_stale(monkeypatch)
    first = _stale(opened)
    assert first["explanation"] is not None and "unavailable" not in first["stale"]
    assert _stale(opened) == first
    assert computed["stale"] == 1, "an unchanged staleness was computed again"
    move(opened)
    before = computed["stale"]
    _stale(opened)
    assert computed["stale"] == before + 1


# --- a build that ends mid-sequence --------------------------------------------------------------


@pytest.mark.parametrize("ending", ["superseded", "failed"])
def test_a_build_ended_mid_sequence_keeps_each_answer_it_finished_and_the_open_computes_the_rest(
    opened, monkeypatch: pytest.MonkeyPatch, ending: str
):
    """No answer depends on another: each is kept on its own question and reads, or not at all."""

    token = read_cancellation.BuildToken()
    original = explainer._stale_answer
    steps: list[str] = []

    def ended(*args: Any, **kwargs: Any) -> Any:
        if ending == "failed":
            raise RuntimeError("the staleness could not be computed")
        answered = original(*args, **kwargs)
        token.mark_stale()  # a newer change supersedes the build as this answer is finished
        return answered

    monkeypatch.setattr(explainer, "_stale_answer", ended)
    expected = read_cancellation.ReadCancelled if ending == "superseded" else RuntimeError
    with read_cancellation.cancellable_read(token), pytest.raises(expected):
        opened.build(steps)
    assert steps == [], "the build went on past the answer it ended in"
    monkeypatch.setattr(explainer, "_stale_answer", original)

    # The open notes its opening answers and, beside a computed overview, its inner comparison.
    page = {name: how for name, how in opened.page("the-page") if name in OPENING_ANSWERS}
    assert page == {
        "live intent": "read",
        "live entries": "read",
        "stale": "computed",  # the superseded answer was not kept; the failed one never existed
        "summary": "computed",
        "overview": "computed",
        "files": "computed",
    }
    assert all(how == "read" for _name, how in opened.page("the-next-page"))


def test_an_answer_kept_for_one_comparison_is_never_read_for_another(opened):
    """A reader for comparison N never takes an answer kept for M: each names its own comparison."""

    opened.build()  # every opening answer of M is kept
    kept = opened.record.number
    (opened.world.code_worktree / CODE_FILE).write_text(CODE_V1.replace("value", "moved"))
    resolved = resolve_review_candidate(opened.world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution) and resolved.trees is not None
    opened.record = resolved.trees.record
    assert opened.record.number != kept

    page = {name: how for name, how in opened.page("the-page") if name in OPENING_ANSWERS}
    # Made for N -- by the page, or by the build its open started, which it may join -- never read.
    assert set(page) == set(OPENING_ANSWERS), page
    assert set(page.values()) <= {"computed", "joined"}, page
    query = {"repo": REPO, "master": MASTER, "leaf": LEAF}
    listed = opened.client.get("/api/review/intent/entries", params=query).json()
    assert listed["comparison"] == opened.record.number


# --- each changed invariant's detail, prepared after the opening answers (D574) -----------------


def _click(opened: _Opened, open_id: str, key: str) -> list[tuple[str, str]]:
    """The page's two reads of one invariant's detail, as a click sends them."""

    query = {"repo": REPO, "master": MASTER, "leaf": LEAF}
    for route, params in (
        (
            "/api/review/trees",
            {**query, "comparison": str(opened.record.number), "invariants": key},
        ),
        ("/api/review/intent", {**query, "selectorKind": "invariant", "selectorId": key}),
    ):
        response = opened.client.get(route, params=params, headers={OPEN_HEADER: open_id})
        assert response.status_code == 200, (route, response.text)
    return _noted(open_id)


def _noted(open_id: str) -> list[tuple[str, str]]:
    answers = OPEN_OBSERVATIONS.entry(open_id).answers
    return [(name, how) for name, how, _ in answers if name in ("trees", "live intent")]


@pytest.mark.parametrize("prepared", [True, False], ids=["prepared", "unprepared"])
def test_a_click_reads_a_prepared_detail_and_computes_an_unprepared_one_once(opened, prepared):
    opened.build()
    details = detail_answers(opened.ports, LeafSelection(REPO, MASTER, LEAF), opened.record.number)
    key = text_uuid("identity", INVARIANT)
    assert [listed for listed, _ask, _moved in details] == [key], "the changed list is another"
    if prepared:
        for _key, ask, _moved in details:
            ask()
    how = "read" if prepared else "computed"
    assert _click(opened, "the-click", key) == [("trees", how), ("live intent", how)]
    with OPEN_OBSERVATIONS.reading("the-queue"):  # the queue, after the click
        for _key, ask, _moved in details:
            ask()
    # Never computed again: read, or joined -- the click's open starts a build of the same trees,
    # whose own queue may be reading the kept answer under its flight at that moment.
    queue = _noted("the-queue")
    assert [name for name, _how in queue] == ["trees", "live intent"]
    assert {how for _name, how in queue} <= {"read", "joined"}, queue


def test_more_details_than_the_page_keeps_answers_for_never_push_its_opening_answers_out(opened):
    """A leaf's details outnumber the opening answers' store (D574): they are kept apart from it."""

    opened.build()
    for index in range(70):  # more than the 64 answers the opening store holds
        query = ReviewTreesQuery(
            repository_id=REPO,
            master=MASTER,
            leaf_id=LEAF,
            number=opened.record.number,
            invariants=(f"no-such-invariant-{index:02d}",),
        )
        assert opened.ports.trees(query).state == "trees"
    page = opened.page("the-page")
    assert {name for name, _ in page} == set(OPENING_ANSWERS)
    assert all(how == "read" for _name, how in page), page


# --- a store every worktree shares -------------------------------------------------------------


def _another_worktrees_object(opened: _Opened) -> None:
    """One object written into the code and the memory store, as any worktree's commit writes it."""

    for root in (opened.world.code, opened.world.memory):
        subprocess.run(
            ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
            input=f"another worktree's object {uuid.uuid4()}\n".encode(),
            check=True,
            capture_output=True,
        )


def test_an_object_another_worktree_adds_after_the_build_keeps_every_opening_answer(opened):
    opened.build()
    _another_worktrees_object(opened)
    page = opened.page("the-page")
    assert all(how == "read" for _name, how in page), page


def test_an_object_another_worktree_adds_while_the_build_computes_keeps_every_opening_answer(
    opened, monkeypatch: pytest.MonkeyPatch
):
    noted = answer_reuse.note_outcome

    def computed_meanwhile(outcome: str) -> None:
        if outcome == "computed":  # the computation is done; its inputs are observed again next
            _another_worktrees_object(opened)
        noted(outcome)

    monkeypatch.setattr(answer_reuse, "note_outcome", computed_meanwhile)
    opened.build()
    monkeypatch.setattr(answer_reuse, "note_outcome", noted)
    page = opened.page("the-page")
    assert all(how == "read" for _name, how in page), page
