"""Native recorded trees prove persistence and each external-input invalidator."""

import hashlib
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from agents_remember.application import review_answer_preparation as reads
from agents_remember.application.review_answer_preparation import (
    RecordedReviewAnswers,
    _recorded_contract,
)
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
)
from agents_remember.application.review_tree_comparison import (
    comparison_directory,
    reopen_review_trees,
)
from agents_remember.application.review_tree_knowledge import read_review_trees
from agents_remember.kernel.reviewer_worklist_process import ReviewerWorklistProcesses
from agents_remember.models.knowledge.review_trees import ReviewTrees
from agents_remember.serving.changeset_generation import count_objects
from agents_remember.serving.review_answer_reuse import RecordedAnswerReuse
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember_test_support.testing.review_git_trees import (
    CODE_FILE,
    INVARIANT,
    LEAF,
    MASTER,
    REPO,
    build_world,
    git,
)


@pytest.fixture
def native(tmp_path: Path):
    reset_worktree_services()
    world = build_world(tmp_path)
    world.edit()
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    assert resolved.trees is not None
    number = resolved.trees.record.number
    world.contract(live=False)
    contract = _recorded_contract(world.config, REPO, MASTER, LEAF)
    assert contract is not None
    trees = reopen_review_trees(world.config.coordination_root, contract, number)
    assert isinstance(trees, ReviewTrees)
    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF, number=number)
    calls = []
    processes = ReviewerWorklistProcesses()

    def answer(selected=query, *, held=None):
        def produce():
            calls.append(1)
            return read_review_trees(world.config, selected, processes=processes)

        owner = held or RecordedReviewAnswers(
            world.config,
            RecordedAnswerReuse(directory=tmp_path / "answers", build_namespace="native-test"),
        )
        return owner.trees(selected, produce)

    try:
        yield world, trees, query, answer, calls
    finally:
        processes.shutdown()
        reset_worktree_services()


def test_real_recorded_main_is_reused_by_a_fresh_owner(native):
    _world, _trees, _query, answer, calls = native
    first = answer()
    assert first.state == "trees"
    second = answer()
    assert len(calls) == 1
    assert first.model_dump_json() == second.model_dump_json()


def test_real_recorded_focus_is_reused_by_a_fresh_owner(native):
    _world, _trees, query, answer, calls = native
    focused = ReviewTreesQuery(
        repository_id=REPO,
        master=MASTER,
        leaf_id=LEAF,
        number=query.number,
        invariants=(INVARIANT,),
    )
    first = answer(focused)
    assert first.state == "trees"
    assert answer(focused).model_dump(mode="json") == first.model_dump(mode="json")
    assert len(calls) == 1


def test_real_index_projection_bytes_invalidate_persistence(native):
    _world, trees, _query, answer, calls = native
    assert answer().state == "trees"
    assert trees.after.database is not None
    # A foreign table, named for what it is: a byte probe, never index structure. It must not
    # borrow the index's own ``ix_*`` namespace -- those names are the projection's tables, and
    # MIK-R74 rule 3's census refuses any test that appears to stage rows into one.
    with closing(sqlite3.connect(trees.after.database)) as connection, connection:
        connection.execute("CREATE TABLE probe_persistence_bytes (value TEXT)")
        connection.execute("INSERT INTO probe_persistence_bytes VALUES ('changed projection')")
    assert answer().state == "trees"
    assert len(calls) == 2


def test_real_wal_only_index_change_invalidates_persistence(native):
    _world, trees, _query, answer, calls = native
    assert trees.after.database is not None
    with closing(sqlite3.connect(trees.after.database)) as connection, connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE probe_persistence_bytes (value TEXT)")
        connection.commit()
        assert answer().state == "trees"
        before = hashlib.sha256(trees.after.database.read_bytes()).hexdigest()
        connection.execute("INSERT INTO probe_persistence_bytes VALUES ('wal changed')")
        connection.commit()
        assert Path(f"{trees.after.database}-wal").stat().st_size > 0
        assert hashlib.sha256(trees.after.database.read_bytes()).hexdigest() == before
        assert answer().state == "trees"
        assert len(calls) == 2


def test_real_missing_recorded_object_never_uses_persistence(native):
    world, trees, _query, answer, calls = native
    assert answer().state == "trees"
    tree = trees.record.code_candidate.tree
    object_path = world.code / ".git" / "objects" / tree[:2] / tree[2:]
    assert object_path.is_file()
    object_path.unlink()
    fresh = answer()
    assert len(calls) == 2
    assert any(side.state == "unavailable-history" for side in fresh.code_sides)


def test_real_in_place_comparison_json_change_invalidates_persistence(native):
    world, _trees, query, answer, calls = native
    assert answer().state == "trees"
    record = comparison_directory(world.task_root, LEAF) / f"{query.number}.json"
    record.write_text(record.read_text() + "\n")
    assert answer().state == "trees"
    assert len(calls) == 2


def test_real_delivery_is_refreshed_after_static_main_was_persisted(native):
    world, _trees, _query, answer, calls = native
    assert answer().delivered is None
    world.contract(
        live=False,
        closeout={
            "status": "completed",
            "code_commit": world.code_base,
            "memory_commit": world.memory_base,
        },
    )
    refreshed = answer()
    assert refreshed.delivered is not None
    assert refreshed.delivered.state != "delivered"
    assert len(calls) == 2


def test_native_warm_binding_observes_retargeted_index_with_old_database_present(native):
    world, trees, _query, answer, calls = native
    assert trees.after.database is not None
    held = RecordedReviewAnswers(
        world.config,
        RecordedAnswerReuse(directory=world.root / "held-answers", build_namespace="native-test"),
    )
    assert answer(held=held).state == "trees"
    original = trees.after.database
    previous = original.with_name("retained-old-index.sqlite")
    alternate = original.with_name("changed-new-index.sqlite")
    original.rename(previous)
    shutil.copyfile(previous, alternate)
    with closing(sqlite3.connect(alternate)) as connection, connection:
        connection.execute("CREATE TABLE probe_persistence_bytes (value TEXT)")
        connection.execute("INSERT INTO probe_persistence_bytes VALUES ('retargeted')")
    original.symlink_to(alternate)
    assert previous.is_file()
    assert answer(held=held).state == "trees"
    assert len(calls) == 2


def test_native_warm_binding_revalidates_changed_index_metadata(native, monkeypatch):
    world, trees, _query, answer, _calls = native
    assert trees.after.database is not None
    held = RecordedReviewAnswers(
        world.config,
        RecordedAnswerReuse(directory=world.root / "held-answers", build_namespace="native-test"),
    )
    original = reads._persistent_inputs
    preparations = []

    def counted(*args):
        preparations.append(1)
        return original(*args)

    monkeypatch.setattr(reads, "_persistent_inputs", counted)
    assert answer(held=held).state == "trees"
    assert answer(held=held).state == "trees"
    assert len(preparations) == 1
    with closing(sqlite3.connect(trees.after.database)) as connection, connection:
        connection.execute("UPDATE ix_meta SET value = 'wrong-tree' WHERE name = 'key'")
    assert answer(held=held).state == "trees"
    assert len(preparations) == 2


def test_compensated_recorded_object_loss_cannot_restore_same_census(native):
    world, trees, _query, answer, calls = native
    held = RecordedReviewAnswers(
        world.config,
        RecordedAnswerReuse(directory=world.root / "answers", build_namespace="native-test"),
    )
    assert answer(held=held).state == "trees"
    before = count_objects(world.code)
    tree = trees.record.code_candidate.tree
    blob = git(world.code, "rev-parse", f"{tree}:{CODE_FILE}")
    object_path = world.code / ".git" / "objects" / blob[:2] / blob[2:]
    assert object_path.is_file()
    object_path.unlink()
    unrelated = world.code / "persistence-unrelated.txt"
    unrelated.write_text("unrelated compensating object\n")
    git(world.code, "hash-object", "-w", str(unrelated))
    assert count_objects(world.code) == before
    assert git(world.code, "cat-file", "-t", tree) == "tree"
    assert not reads._object_bodies_readable(world.code, [blob])
    assert not reads._trees_still_readable(((world.code, tree),))
    fresh = answer(held=held)
    assert len(calls) == 2
    assert fresh.state == "trees"
    assert held._reuse.retained() == 0
    assert answer(held=held).state == "trees"
    assert len(calls) == 3
