"""An open leaf's numbered tree view is kept on its exact inputs, and one question computes once."""

import json
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any, cast

from agents_remember.application import review_answer_preparation as reads
from agents_remember.application.knowledge_currentness.observe import (
    OpenedCodeTree,
    prefetch_observed_blobs,
)
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.kernel import read_cancellation
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.recorded_reads import record_read, recorded_reads
from agents_remember.memory.conversion.code_objects import CodeObjectError
from agents_remember.memory.knowledge_index import Entry
from agents_remember.models.knowledge.review_trees import ReviewTreesResult
from agents_remember.serving.changeset_cache import ContentRead
from agents_remember.serving.review_answer_reuse import ANSWER_WRITES, RecordedAnswerReuse
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, wait_until


def fixture(
    tmp_path: Path,
    monkeypatch: Any,
    *,
    produce_gate: Event | None = None,
    unnamed: Path | None = None,
):
    plain = tmp_path / "contract.md"
    plain.write_text("contract")
    index = tmp_path / "index.sqlite"
    index.write_bytes(b"index projection rows")
    worktree = tmp_path / "code-worktree"
    worktree.mkdir()
    contract = SimpleNamespace(code_worktree=worktree, memory_worktree=None)
    monkeypatch.setattr(reads, "_recorded_contract", lambda *_: contract)
    monkeypatch.setattr(reads, "_recorded_sides", lambda *_: ())
    monkeypatch.setattr(reads, "_trees_still_readable", lambda *_: True)
    monkeypatch.setattr(reads, "_sources_of", lambda *_: (ContentRead(plain),))
    monkeypatch.setattr(
        reads,
        "_persistent_inputs",
        lambda *_: ((ContentRead(index),), ("logical-before", "logical-after")),
    )
    monkeypatch.setattr(reads, "recorded_latest", lambda *_: 3)
    monkeypatch.setattr(reads, "delivered_notice", lambda *_: None)
    calls: list[int] = []

    def produce() -> ReviewTreesResult:
        record_read(plain)
        if unnamed is not None:
            record_read(unnamed)  # a worklist child's task or requirement file, say
        calls.append(1)
        if produce_gate is not None:
            assert produce_gate.wait(timeout=HANG_GUARD_SECONDS)
        return ReviewTreesResult(
            state="trees", repository_id="repo", master="master", leaf_id="leaf", latest=2
        )

    owner = reads.RecordedReviewAnswers(
        McpRuntimeConfig(
            workspace_root=tmp_path,
            coordination_root=tmp_path,
            config_path=tmp_path / "config.json",
            transcript_root=tmp_path / "transcripts",
        ),
        RecordedAnswerReuse(directory=tmp_path / "cache", build_namespace="source-digest"),
    )
    return owner, produce, calls, worktree, plain


def _owner_again(tmp_path: Path) -> reads.RecordedReviewAnswers:
    """Another process over the same answer store."""
    ANSWER_WRITES.drain()  # the earlier process's stored answers are written
    return reads.RecordedReviewAnswers(
        McpRuntimeConfig(
            workspace_root=tmp_path,
            coordination_root=tmp_path,
            config_path=tmp_path / "config.json",
            transcript_root=tmp_path / "transcripts",
        ),
        RecordedAnswerReuse(directory=tmp_path / "cache", build_namespace="source-digest"),
    )


def query(number: int | None = 3) -> ReviewTreesQuery:
    return ReviewTreesQuery(repository_id="repo", master="master", leaf_id="leaf", number=number)


def test_an_open_leafs_numbered_comparison_is_answered_once(tmp_path, monkeypatch):
    owner, produce, calls, _worktree, _plain = fixture(tmp_path, monkeypatch)

    first = owner.trees(query(), produce)
    second = owner.trees(query(), produce)

    assert calls == [1]
    assert first == second


def test_an_answer_is_kept_against_a_read_its_question_does_not_name(tmp_path, monkeypatch):
    requirement = tmp_path / "requirement.md"
    requirement.write_text("one requirement")
    owner, produce, calls, _worktree, _plain = fixture(tmp_path, monkeypatch, unnamed=requirement)

    first = owner.trees(query(), produce)
    second = owner.trees(query(), produce)

    assert calls == [1]
    assert first == second


def test_a_kept_answer_whose_unnamed_read_changed_is_computed_again(tmp_path, monkeypatch):
    requirement = tmp_path / "requirement.md"
    requirement.write_text("one requirement")
    owner, produce, calls, _worktree, _plain = fixture(tmp_path, monkeypatch, unnamed=requirement)

    owner.trees(query(), produce)
    requirement.write_text("the requirement, revised")
    owner.trees(query(), produce)
    owner.trees(query(), produce)

    assert calls == [1, 1]


def test_after_a_restart_an_answer_is_taken_while_every_read_it_made_compares_identical(
    tmp_path, monkeypatch
):
    requirement = tmp_path / "requirement.md"
    requirement.write_text("one requirement")
    owner, produce, calls, _worktree, _plain = fixture(tmp_path, monkeypatch, unnamed=requirement)

    owner.trees(query(), produce)
    _owner_again(tmp_path).trees(query(), produce)

    assert calls == [1]


def test_after_a_restart_an_answer_whose_read_changed_is_computed_again(tmp_path, monkeypatch):
    requirement = tmp_path / "requirement.md"
    requirement.write_text("one requirement")
    owner, produce, calls, _worktree, _plain = fixture(tmp_path, monkeypatch, unnamed=requirement)

    owner.trees(query(), produce)
    requirement.write_text("the requirement, revised")
    _owner_again(tmp_path).trees(query(), produce)

    assert calls == [1, 1]


def test_a_stored_answer_whose_reads_are_not_a_valid_read_set_is_absent(tmp_path, monkeypatch):
    requirement = tmp_path / "requirement.md"
    requirement.write_text("one requirement")
    owner, produce, calls, _worktree, _plain = fixture(tmp_path, monkeypatch, unnamed=requirement)
    owner.trees(query(), produce)
    ANSWER_WRITES.drain()
    (stored,) = [
        path for path in (tmp_path / "cache").glob("*.review.json") if '"reads"' in path.read_text()
    ]
    envelope = json.loads(stored.read_text())
    envelope["reads"] = {"relative/path.md": "not an identity"}
    stored.write_text(json.dumps(envelope))

    _owner_again(tmp_path).trees(query(), produce)

    assert calls == [1, 1]


def test_a_fully_named_answer_is_still_stored_for_another_process(tmp_path, monkeypatch):
    owner, produce, calls, _worktree, _plain = fixture(tmp_path, monkeypatch)

    owner.trees(query(), produce)
    _owner_again(tmp_path).trees(query(), produce)

    assert calls == [1]


def test_a_vanished_capture_worktree_is_another_answer(tmp_path, monkeypatch):
    owner, produce, calls, worktree, _plain = fixture(tmp_path, monkeypatch)

    owner.trees(query(), produce)
    worktree.rmdir()
    owner.trees(query(), produce)

    assert calls == [1, 1]


def test_an_open_leafs_unnumbered_view_is_computed_every_time(tmp_path, monkeypatch):
    owner, produce, calls, _worktree, _plain = fixture(tmp_path, monkeypatch)
    unnumbered = ReviewTreesQuery(
        repository_id="repo", master="master", leaf_id="leaf", recorded=True
    )

    owner.trees(unnumbered, produce)
    owner.trees(unnumbered, produce)

    assert calls == [1, 1]


def _watch_joiners(monkeypatch) -> list[int]:
    """Counts the askers that join an answer already in flight."""
    joined: list[int] = []
    join = read_cancellation._Flight.join

    def watched(flight, asker):
        taken = join(flight, asker)
        if taken:
            joined.append(1)
        return taken

    monkeypatch.setattr(read_cancellation._Flight, "join", watched)
    return joined


def test_identical_questions_in_flight_share_one_computation_and_its_reads(tmp_path, monkeypatch):
    gate = Event()
    owner, produce, calls, _worktree, plain = fixture(tmp_path, monkeypatch, produce_gate=gate)
    joined = _watch_joiners(monkeypatch)
    results: dict[str, object] = {}
    waiter_reads: dict[str, str] = {}

    def ask(name: str) -> None:
        if name == "waiter":
            with recorded_reads() as seen:
                results[name] = owner.trees(query(), produce)
            waiter_reads.update(seen)
        else:
            results[name] = owner.trees(query(), produce)

    first = Thread(target=ask, args=("owner",))
    first.start()
    wait_until(lambda: calls, "the owner computing")
    second = Thread(target=ask, args=("waiter",))
    second.start()
    wait_until(lambda: joined, "the second asker waiting for the first's answer")
    gate.set()
    first.join(timeout=HANG_GUARD_SECONDS)
    second.join(timeout=HANG_GUARD_SECONDS)

    assert calls == [1]
    assert results["owner"] == results["waiter"]
    assert results["owner"] is not results["waiter"]
    assert str(plain) in {key for key in waiter_reads}


def _entry(entry_id: str, path: str, blob: str, kind: str) -> Entry:
    return Entry(
        id=entry_id,
        kind="realization",
        invariant="INV-1",
        path=path,
        sidecar=f"{path}.json",
        document={"anchor": {"blob": blob, "locator": {"kind": kind}, "content": ""}},
    )


class _Objects:
    def __init__(self, error: bool = False) -> None:
        self.asked: list[set[str]] = []
        self.presence: list[set[str]] = []
        self.error = error

    def prefetch(self, blob_ids):
        self.asked.append(set(blob_ids))
        if self.error:
            raise CodeObjectError("store unreadable")

    def prefetch_presence(self, blob_ids):
        self.presence.append(set(blob_ids))


def _opened(objects: _Objects) -> OpenedCodeTree:
    trees = cast(CodeTrees, SimpleNamespace(objects=objects))
    files = {"a.py": "b1", "b.py": "b2", "c.py": "b3"}
    return OpenedCodeTree(tree=None, trees=trees, files=files, problem=None)


def test_the_blobs_observation_reads_are_fetched_in_one_batch():
    objects = _Objects()
    entries = [
        _entry("E1", "a.py", "b1", "symbol"),  # unchanged, but a symbol is decoded
        _entry("E2", "b.py", "old", "lines"),  # changed: resolved
        _entry("E3", "c.py", "b3", "lines"),  # unchanged lines: decided without reading
        _entry("E4", "gone.py", "b9", "symbol"),  # absent: decided without reading
    ]

    prefetch_observed_blobs(entries, _opened(objects))

    assert objects.asked == [{"b1", "b2"}]


def test_a_failed_batch_is_left_to_each_entrys_own_read():
    objects = _Objects(error=True)

    prefetch_observed_blobs([_entry("E1", "a.py", "b1", "symbol")], _opened(objects))

    assert objects.asked == [{"b1"}]


def test_changed_line_ranges_ask_at_once_whether_their_recorded_blobs_are_held():
    objects = _Objects()
    entries = [
        _entry("E1", "a.py", "r1", "line_range"),  # changed: has_blob(r1) is asked
        _entry("E2", "b.py", "b2", "line_range"),  # unchanged: decided without asking
        _entry("E3", "gone.py", "r3", "line_range"),  # absent: decided without asking
        _entry("E4", "c.py", "r4", "symbol"),  # a symbol never asks for its recorded blob
    ]

    prefetch_observed_blobs(entries, _opened(objects))

    assert objects.presence == [{"r1"}]
