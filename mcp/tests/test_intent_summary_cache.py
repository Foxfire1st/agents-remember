"""Native intent-summary reads retain exact results and revalidate without Git children."""

from __future__ import annotations

import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from agents_remember.application import review_intent_summary as native
from agents_remember.application.review_tree_comparison import comparison_directory
from agents_remember.cli import dashboard_review_summary
from agents_remember.cli.dashboard_review_summary import review_intent_summary_port
from agents_remember.kernel import git_command
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.review_intent_summary import ReviewIntentSummaryResult
from agents_remember.serving import changeset_generation
from agents_remember.serving.changeset import register_changeset_routes
from agents_remember.serving.changeset_cache import ChangesetCache, ContentRead
from agents_remember.serving.intent_summary_cache import (
    DEFAULT_ADMISSIONS,
    DEFAULT_MAX_AGE_SECONDS,
    IntentSummaryCache,
    ReviewIntentSummaryPort,
)
from agents_remember.serving.review_summary import register_review_summary_route
from agents_remember_test_support.testing.review_git_trees import (
    CODE_FILE,
    INVARIANT_PATH,
    LEAF,
    MASTER,
    REPO,
    build_world,
    git,
    invariant,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.evidence_unit
PARAMS = {"repo": REPO, "master": MASTER, "leaf": LEAF}
ROUTE = "/api/review/intent/summary"


@pytest.fixture
def world(tmp_path, monkeypatch):
    fixture = build_world(tmp_path)
    fixture.edit()
    app = FastAPI()
    cache = register_changeset_routes(app, fixture.config)
    register_review_summary_route(app, review_intent_summary_port(fixture.config), cache)
    children = []
    original = git_command._run_git

    def measured(*args, **kwargs):
        children.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(git_command, "_run_git", measured)
    with TestClient(app) as client:
        yield fixture, client, children


def _reads(children):
    """Git children that compute, apart from the freshness checks (MIK-R176 rule 5).

    A working view is decided by ``git status`` and the object store by Git's own
    ``count-objects`` census; neither computes a summary. Every other child is the
    resolution/computation the tests pin.
    """
    return [
        child
        for child in children
        if "--untracked-files=all" not in child and child[:1] != ["count-objects"]
    ]


def _settled(client):
    # Native first resolution can create its comparison record/pins and indexes.
    # Only a complete stable before/after generation is retained.
    first = client.get(ROUTE, params=PARAMS)
    second = client.get(ROUTE, params=PARAMS)
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    return second


def test_native_counted_bytes_and_conditional_return_use_zero_git(world, monkeypatch):
    fixture, client, children = world
    counted = []
    original = native.intent_summary_of

    def count(*args, **kwargs):
        counted.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(native, "intent_summary_of", count)
    first = client.get(ROUTE, params=PARAMS)
    assert first.json()["state"] == "counted" and len(counted) == 1
    children.clear()
    again = client.get(ROUTE, params=PARAMS, headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and not again.content and not _reads(children)
    assert len(counted) == 1
    expected = native.read_review_intent_summary(fixture.config, REPO, MASTER, LEAF)
    assert first.json() == expected.model_dump(mode="json", exclude_none=True)


@pytest.mark.parametrize("source", ["code", "memory", "index", "head", "record"])
def test_native_input_changes_invalidate_then_settle_without_more_git(world, source):
    fixture, client, children = world
    _settled(client)
    children.clear()
    if source == "code":
        path = fixture.code_worktree / CODE_FILE
        before = path.stat()
        path.write_text(path.read_text().replace("value + 0", "value + 1"))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif source == "memory":
        path = fixture.memory_worktree / INVARIANT_PATH
        path.write_text(invariant(3, "The revised statement is current."))
    elif source == "index":
        git(fixture.code_worktree, "add", "-A")
    elif source == "head":
        git(fixture.code_worktree, "commit", "--allow-empty", "-q", "-m", "new head")
    else:
        path = next(comparison_directory(fixture.task_root, LEAF).glob("*.json"))
        path.write_bytes(path.read_bytes() + b"\n")
    changed = client.get(ROUTE, params=PARAMS)
    assert changed.status_code == 200 and children
    expected = native.read_review_intent_summary(fixture.config, REPO, MASTER, LEAF)
    assert changed.json() == expected.model_dump(mode="json", exclude_none=True)
    _settled(client)
    children.clear()
    assert client.get(ROUTE, params=PARAMS).content == changed.content
    assert not _reads(children)


def test_closed_native_record_and_lost_object_keep_the_owners_refusal(world):
    fixture, client, children = world
    _settled(client)
    fixture.contract(live=False)
    first = _settled(client)
    children.clear()
    assert client.get(ROUTE, params=PARAMS).content == first.content and not _reads(children)
    object_id = git(fixture.memory, "rev-parse", f"{fixture.memory_base}^{{tree}}")
    object_path = fixture.memory / ".git/objects" / object_id[:2] / object_id[2:]
    object_path.unlink()
    changed = client.get(ROUTE, params=PARAMS)
    expected = native.read_review_intent_summary(fixture.config, REPO, MASTER, LEAF)
    assert children and changed.json() == expected.model_dump(mode="json", exclude_none=True)
    assert changed.json()["state"] == "unavailable" and "counts" not in changed.json()


def test_derived_index_timestamp_touch_reuses_but_changed_bytes_are_reopened(world):
    fixture, client, children = world
    first = _settled(client)
    index = next((fixture.config.coordination_root / "runtime/knowledge-index").glob("*.sqlite"))
    os.utime(index, None)
    children.clear()
    assert client.get(ROUTE, params=PARAMS).content == first.content and not _reads(children)
    index.write_bytes(b"unreadable derived index")
    result = client.get(ROUTE, params=PARAMS)
    assert result.status_code == 200 and children
    expected = native.read_review_intent_summary(fixture.config, REPO, MASTER, LEAF)
    assert result.json() == expected.model_dump(mode="json", exclude_none=True)


def test_no_contract_and_invalid_address_keep_native_refusals(world):
    fixture, client, children = world
    for leaf in ("not-authored", "../outside"):
        params = {**PARAMS, "leaf": leaf}
        expected = native.read_review_intent_summary(fixture.config, REPO, MASTER, leaf)
        first = client.get(ROUTE, params=params)
        assert first.json() == expected.model_dump(mode="json", exclude_none=True)
        children.clear()
        assert client.get(ROUTE, params=params).content == first.content and not _reads(children)


def test_unreadable_index_observation_uses_native_read_each_time(world, monkeypatch):
    _fixture, client, children = world
    first = _settled(client)
    original = Path.read_bytes

    def read_bytes(path):
        if path.suffix == ".sqlite":
            raise PermissionError("index observation denied")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    for _ in range(2):
        children.clear()
        assert client.get(ROUTE, params=PARAMS).content == first.content and children


def test_dependency_manifest_and_values_remain_bounded_over_growing_histories(tmp_path: Path):
    input = tmp_path / "index"
    input.write_bytes(b"derived index")
    calls = []

    def read(repo, master, leaf, _number, observe):
        calls.append(leaf)
        observe((ContentRead(input),))
        return ReviewIntentSummaryResult(
            state="unavailable",
            repository_id=repo,
            master=master,
            leaf_id=leaf,
            refusal=ReviewRefusal(
                code="candidate_dataset_absent", detail="not authored", next_action="author"
            ),
        )

    reader = IntentSummaryCache(
        ReviewIntentSummaryPort(inputs=lambda *_: (), read=read), ChangesetCache(capacity=128)
    )
    for count in (300, 600):
        for index in range(count):
            with reader.cache.reading():
                reader._response("repo", "master", str(index))
        assert reader.retained_inputs == reader.cache.retained[0] == 128
    calls.clear()
    with reader.cache.reading():
        reader._response("repo", "master", "599")
    assert not calls


@pytest.mark.parametrize("live", [False, True])
def test_first_refused_revalidation_is_free_and_restored_objects_are_seen(world, live):
    fixture, client, children = world
    if not live:
        first = client.get(ROUTE, params=PARAMS)
        assert first.status_code == 200
        fixture.contract(live=False)
        assert client.get(ROUTE, params=PARAMS).status_code == 200
    object_id = git(fixture.memory, "rev-parse", f"{fixture.memory_base}^{{tree}}")
    path = fixture.memory / ".git/objects" / object_id[:2] / object_id[2:]
    data, mode = path.read_bytes(), path.stat().st_mode
    path.unlink()
    for edit in range(3):
        if live:
            (fixture.code_worktree / CODE_FILE).write_text(f"def value(): return {edit}\n")
        refused = client.get(ROUTE, params=PARAMS)
        assert refused.json()["state"] == "unavailable"
        for _ in range(3):
            children.clear()
            again = client.get(
                ROUTE, params=PARAMS, headers={"If-None-Match": refused.headers["etag"]}
            )
            assert again.status_code == 304 and not _reads(children)
    path.write_bytes(data)
    path.chmod(mode)
    restored = client.get(ROUTE, params=PARAMS)
    assert restored.json()["state"] == "counted"
    assert restored.json() == native.read_review_intent_summary(
        fixture.config, REPO, MASTER, LEAF
    ).model_dump(mode="json", exclude_none=True)


def test_unchanged_intent_inputs_do_not_walk_unrelated_master_files(world, monkeypatch):
    fixture, client, children = world
    client.get(ROUTE, params=PARAMS)

    original = changeset_generation.os.scandir
    scans = []

    def counted(path, *args, **kwargs):
        if isinstance(path, int):
            return original(path, *args, **kwargs)
        scans.append(Path(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(changeset_generation.os, "scandir", counted)
    observed = []
    for size in (40, 4000):
        unrelated = fixture.task_root / "notes" / f"unrelated-{size}"
        unrelated.mkdir(parents=True)
        for index in range(size):
            (unrelated / str(index)).write_text("not a summary input\n")
        children.clear()
        scans.clear()
        assert client.get(ROUTE, params=PARAMS).status_code == 200
        assert not _reads(children)
        assert not any(path.is_relative_to(fixture.task_root / "notes") for path in scans)
        observed.append(len(scans))
    assert observed[0] == observed[1]


def test_object_restored_after_native_refusal_is_not_adopted_as_the_refusals_input(
    world, monkeypatch
):
    fixture, client, children = world
    object_id = git(fixture.memory, "rev-parse", f"{fixture.memory_base}^{{tree}}")
    path = fixture.memory / ".git/objects" / object_id[:2] / object_id[2:]
    data = path.read_bytes()
    path.unlink()
    original = dashboard_review_summary.summarize_review

    def restore_after_read(*args, **kwargs):
        result = original(*args, **kwargs)
        if result.state == "unavailable":
            path.write_bytes(data)
        return result

    monkeypatch.setattr(dashboard_review_summary, "summarize_review", restore_after_read)
    assert client.get(ROUTE, params=PARAMS).json()["state"] == "unavailable"
    children.clear()
    assert client.get(ROUTE, params=PARAMS).json()["state"] == "counted"
    assert children


@pytest.mark.parametrize(
    "route,extra",
    [
        ("/api/changeset/task", {"leaf": LEAF, "mode": "working"}),
        ("/api/changeset/file-diff", {"leaf": LEAF, "mode": "working", "path": CODE_FILE}),
        ("/api/changeset/master", {}),
    ],
)
def test_slow_intent_does_not_serialize_other_reads(world, route, extra):

    fixture, _, _ = world
    entered, release = Event(), Event()

    def slow(repo, master, leaf, _number, observe):
        entered.set()
        assert release.wait(timeout=HANG_GUARD_SECONDS)
        return ReviewIntentSummaryResult(
            state="unavailable",
            repository_id=repo,
            master=master,
            leaf_id=leaf,
            refusal=ReviewRefusal(
                code="candidate_dataset_absent", detail="test", next_action="author"
            ),
        )

    app = FastAPI()
    cache = register_changeset_routes(app, fixture.config)
    register_review_summary_route(
        app, ReviewIntentSummaryPort(inputs=lambda *_: (), read=slow), cache
    )
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(client.get, ROUTE, params=PARAMS)
        try:
            assert entered.wait(timeout=HANG_GUARD_SECONDS)
            other = pool.submit(client.get, route, params={"repo": REPO, "master": MASTER, **extra})
            assert other.result(timeout=HANG_GUARD_SECONDS).status_code in (200, 404)
        finally:
            release.set()
        assert pending.result(timeout=HANG_GUARD_SECONDS).status_code == 200


class _Elapsed:
    """A clock the test moves, so an age bound can be exercised without waiting."""

    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds


def test_a_kept_summary_answer_expires_and_is_then_produced_again(tmp_path: Path):
    """Rule 3 bounds reuse by age as well as by size, and expiry is a native read."""

    input = tmp_path / "index"
    input.write_bytes(b"derived index")
    produced: list[str] = []
    elapsed = _Elapsed()

    def read(repo, master, leaf, _number, observe):
        produced.append(leaf)
        observe((ContentRead(input),))
        return ReviewIntentSummaryResult(
            state="unavailable",
            repository_id=repo,
            master=master,
            leaf_id=leaf,
            refusal=ReviewRefusal(
                code="candidate_dataset_absent", detail="not authored", next_action="author"
            ),
        )

    reader = IntentSummaryCache(
        ReviewIntentSummaryPort(inputs=lambda *_: (), read=read),
        ChangesetCache(capacity=8),
        clock=elapsed,
    )
    with reader.cache.reading():
        first = reader._response("repo", "master", "leaf")
    assert produced == ["leaf"]

    elapsed.seconds = 60.0
    with reader.cache.reading():
        inside = reader._response("repo", "master", "leaf")
    assert produced == ["leaf"], "an answer inside the age bound is served, not re-read"
    assert inside.body == first.body

    elapsed.seconds = 60.0 + DEFAULT_MAX_AGE_SECONDS + 1.0
    with reader.cache.reading():
        expired = reader._response("repo", "master", "leaf")
    assert produced == ["leaf", "leaf"], "an answer past the age bound is produced again"
    assert expired.body == first.body, "reuse and expiry answer the same bytes"


def test_a_changed_input_still_invalidates_a_summary_answer_inside_the_age_bound(tmp_path: Path):
    input = tmp_path / "index"
    input.write_bytes(b"derived index")
    produced: list[str] = []
    elapsed = _Elapsed()

    def read(repo, master, leaf, _number, observe):
        produced.append(leaf)
        observe((ContentRead(input),))
        return ReviewIntentSummaryResult(
            state="unavailable",
            repository_id=repo,
            master=master,
            leaf_id=leaf,
            refusal=ReviewRefusal(
                code="candidate_dataset_absent", detail="not authored", next_action="author"
            ),
        )

    reader = IntentSummaryCache(
        ReviewIntentSummaryPort(inputs=lambda *_: (), read=read),
        ChangesetCache(capacity=8),
        clock=elapsed,
    )
    with reader.cache.reading():
        reader._response("repo", "master", "leaf")
    input.write_bytes(b"derived index, revised")
    elapsed.seconds = 1.0
    with reader.cache.reading():
        reader._response("repo", "master", "leaf")
    assert produced == ["leaf", "leaf"]


def test_a_bounded_admission_table_cannot_resurrect_an_over_age_answer(tmp_path: Path):
    """L177-R1-06 after eviction: the clock alone decides the window, so the answer is produced again.

    The exact counterexample the Reviewer ran against A3: the target plus every admission the table
    holds, then a clock past the bound. An answer must not be reused because its admission record
    was evicted.
    """

    input = tmp_path / "index"
    input.write_bytes(b"derived index")
    produced: Counter[str] = Counter()
    elapsed = _Elapsed()

    def read(repo, master, leaf, _number, observe):
        produced[leaf] += 1
        observe((ContentRead(input),))
        return ReviewIntentSummaryResult(
            state="unavailable",
            repository_id=repo,
            master=master,
            leaf_id=leaf,
            refusal=ReviewRefusal(
                code="candidate_dataset_absent", detail="not authored", next_action="author"
            ),
        )

    reader = IntentSummaryCache(
        ReviewIntentSummaryPort(inputs=lambda *_: (), read=read),
        ChangesetCache(capacity=512),
        clock=elapsed,
    )

    def fetch(leaf: str):
        with reader.cache.reading():
            return reader._response("repo", "master", leaf)

    first = fetch("target")
    assert produced["target"] == 1
    for index in range(DEFAULT_ADMISSIONS):
        fetch(f"other-{index}")
    assert ("repo", "master", "target", None) not in reader._kept, (
        "the admission record was evicted"
    )

    elapsed.seconds = DEFAULT_MAX_AGE_SECONDS + 1.0
    after = fetch("target")
    assert produced["target"] == 2, (
        "an over-age answer was served because its admission was evicted"
    )
    assert after.body == first.body
