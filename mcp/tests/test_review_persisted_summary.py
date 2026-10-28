"""A summary disk hit preserves the native selection and every current input."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest
from agents_remember.application import review_intent_summary as native
from agents_remember.application.knowledge_worklist.leaf import CandidateTrees, leaf_worklist
from agents_remember.application.review_answer_preparation import RecordedReviewAnswers
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
)
from agents_remember.application.review_tree_comparison import comparison_directory
from agents_remember.cli import dashboard_review_summary as summary
from agents_remember.kernel.recorded_reads import record_read
from agents_remember.serving import changeset_cache
from agents_remember.serving.review_answer_reuse import RecordedAnswerReuse
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember_test_support.testing.review_git_trees import LEAF, MASTER, REPO, build_world


@pytest.fixture
def comparison(tmp_path, monkeypatch):
    reset_worktree_services()
    world = build_world(tmp_path)
    world.edit()
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution) and resolved.trees is not None
    assert resolved.contract is not None
    record = resolved.trees.record
    document = leaf_worklist(
        resolved.contract,
        candidate=CandidateTrees(record.code_candidate.tree, record.memory_candidate.tree),
        persist=False,
    )
    assert document is not None and document["state"] == "complete"
    worklist = resolved.contract.contract_path.parent / "knowledge-worklist.json"
    worklist.write_text(json.dumps(document))
    world.contract(live=False)
    produced = []
    selections = []
    original_count = native.intent_summary_of
    original_resolve = summary.resolve_review_comparison

    def count(*args, **kwargs):
        produced.append(True)
        return original_count(*args, **kwargs)

    def resolve(config, repo, master, leaf, number):
        selections.append(number)
        return original_resolve(config, repo, master, leaf, number)

    monkeypatch.setattr(native, "intent_summary_of", count)
    monkeypatch.setattr(summary, "resolve_review_comparison", resolve)

    def answer(number=None):
        owner = RecordedReviewAnswers(
            world.config,
            RecordedAnswerReuse(directory=tmp_path / "answers", build_namespace="summary-test"),
        )
        port = summary.review_intent_summary_port(world.config, recorded_answers=owner)
        return port.read(REPO, MASTER, LEAF, number, lambda _: None)

    yield world, resolved, answer, produced, selections, worklist
    reset_worktree_services()


def test_summary_survives_a_fresh_owner_and_resolves_the_native_question_each_time(comparison):
    _world, _resolved, answer, produced, selections, _worklist = comparison
    first = answer()
    assert first.state == "counted" and first.unexplained_open is not None
    second = answer()
    assert second.model_dump(mode="json") == first.model_dump(mode="json")
    assert len(produced) == 1
    assert selections == [None, None]


@pytest.mark.parametrize("changed", ["worklist", "decision", "index"])
def test_current_non_tree_summary_inputs_invalidate_a_persisted_answer(
    comparison, changed, monkeypatch, tmp_path
):
    _world, resolved, answer, produced, _selections, worklist = comparison
    decision = tmp_path / "current-decision.json"
    decision.write_text("before")
    original = summary.resolve_review_comparison

    def resolve(*args):
        record_read(decision)
        return original(*args)

    monkeypatch.setattr(summary, "resolve_review_comparison", resolve)
    first = answer()
    assert first.unexplained_open is not None
    if changed == "worklist":
        worklist.write_text(worklist.read_text() + "\n")
    elif changed == "decision":
        decision.write_text("after")
    else:
        assert resolved.trees.after.database is not None
        with closing(sqlite3.connect(resolved.trees.after.database)) as connection, connection:
            connection.execute("CREATE TABLE probe_summary_bytes (value TEXT)")
            connection.execute("INSERT INTO probe_summary_bytes VALUES ('changed bytes')")
    second = answer()
    assert second.model_dump(mode="json") == first.model_dump(mode="json")
    assert len(produced) == 2


def test_missing_worklist_cannot_reuse_the_old_measured_open_count(comparison):
    """The worklist's absence is an input like its bytes: the unmeasured summary is kept on it (D570)."""
    _world, _resolved, answer, produced, _selections, worklist = comparison
    measured = worklist.read_text()
    assert answer().unexplained_open is not None
    worklist.unlink()
    assert answer().unexplained_open is None
    assert answer().unexplained_open is None
    assert len(produced) == 2
    worklist.write_text(measured)
    assert answer().unexplained_open is not None
    assert len(produced) == 3


@pytest.mark.parametrize(
    ("stored", "kept"),
    [('{"state": "incomplete"}', True), ("{not json", False)],
    ids=["unmeasured-by-its-bytes", "unreadable"],
)
def test_an_unmeasured_open_count_is_kept_only_as_a_fact_of_the_worklist(comparison, stored, kept):
    _world, _resolved, answer, produced, _selections, worklist = comparison
    worklist.write_text(stored)
    assert answer().unexplained_open is None
    assert answer().unexplained_open is None
    assert len(produced) == (1 if kept else 2)


def test_live_default_summary_keeps_the_native_producer(comparison, tmp_path):
    world, _resolved, answer, produced, _selections, _worklist = comparison
    world.contract(live=True)
    assert answer().state == "counted"
    assert answer().state == "counted"
    assert len(produced) == 2
    assert not list((tmp_path / "answers").glob("*.review.json"))


def test_missing_selected_object_never_returns_the_old_summary(comparison):
    world, resolved, answer, produced, _selections, _worklist = comparison
    assert answer().state == "counted"
    tree = resolved.trees.record.code_candidate.tree
    (world.code / ".git" / "objects" / tree[:2] / tree[2:]).unlink()
    changed = answer()
    assert changed.attribution is not None and changed.attribution.state != "counted"
    assert len(produced) == 2


def test_an_unlisted_producer_read_is_compared_before_a_stored_summary_is_reused(
    comparison, monkeypatch, tmp_path
):
    _world, _resolved, answer, produced, _selections, _worklist = comparison
    extra = tmp_path / "not-a-summary-input"
    extra.write_text("unlisted")
    original = native.intent_summary_of

    def unexpected(*args, **kwargs):
        record_read(extra)
        return original(*args, **kwargs)

    monkeypatch.setattr(native, "intent_summary_of", unexpected)
    assert answer().state == "counted"
    assert answer().state == "counted"
    assert len(produced) == 1
    extra.write_text("the unlisted input changed")
    assert answer().state == "counted"
    assert len(produced) == 2


def test_native_chosen_default_is_not_replaced_by_a_newer_record(comparison, monkeypatch):
    world, _resolved, answer, produced, selections, _worklist = comparison
    original = summary.resolve_review_comparison
    chosen = original(world.config, REPO, MASTER, LEAF, None)
    assert isinstance(chosen, ReviewCandidateResolution) and chosen.trees is not None
    assert selections == [None]  # The explicit native setup resolution above is observed too.
    number = chosen.trees.record.number
    record_path = comparison_directory(world.task_root, LEAF) / f"{number}.json"
    later = json.loads(record_path.read_text())
    later["number"] = number + 1
    record_path.with_name(f"{number + 1}.json").write_text(json.dumps(later))

    def native_choice(_config, _repo, _master, _leaf, requested):
        selections.append(requested)
        assert requested is None
        record_read(record_path)
        return chosen

    monkeypatch.setattr(summary, "resolve_review_comparison", native_choice)
    assert answer().comparison == number
    assert answer().comparison == number
    assert len(produced) == 1
    # One setup resolution and both port reads retain the native unnumbered question.
    assert selections == [None, None, None]


def test_summary_restart_reuses_exact_inputs_despite_prior_object_history(comparison, monkeypatch):
    _world, _resolved, answer, produced, _selections, _worklist = comparison
    original = changeset_cache.ChangesetCache._object_revision
    seeded = [True]
    observed = []

    def revision(self, root, *, tolerate_additions=False):
        if seeded[0] and (root, tolerate_additions) not in changeset_cache._OBJECT_HISTORY:
            loose, packed, alternates = changeset_cache.count_objects(root)
            # Synthetic earlier census; the actual repository never changes.
            with monkeypatch.context() as patch:
                patch.setattr(
                    changeset_cache, "count_objects", lambda _: (loose + 1, packed, alternates)
                )
                original(self, root, tolerate_additions=tolerate_additions)
        result = original(self, root, tolerate_additions=tolerate_additions)
        observed.append((seeded[0], root, tolerate_additions, result))
        return result

    monkeypatch.setattr(changeset_cache.ChangesetCache, "_object_revision", revision)
    first = answer()
    assert first.state == "counted"
    seeded[0] = False
    changeset_cache._OBJECT_HISTORY.clear()  # a restarted process begins with no census history
    assert answer().model_dump_json() == first.model_dump_json()
    assert len(produced) == 1
    before = {(root, tolerance): token for old, root, tolerance, token in observed if old}
    after = {(root, tolerance): token for old, root, tolerance, token in observed if not old}
    assert any(before[key] != after[key] for key in before.keys() & after.keys())
