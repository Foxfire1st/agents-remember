"""Regression draft: owned detached checkouts do not move a bound comparison."""

from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.review_answer_preparation import RecordedReviewAnswers
from agents_remember.application.review_comparison_preparation import (
    PreparedComparison,
    SharedComparisonPreparation,
)
from agents_remember.canvas.explainer_worktrees import checkout_tree
from agents_remember.kernel.git_command import run_git_bytes
from agents_remember.kernel.recorded_reads import changed_observations
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.serving.changeset_cache import ChangesetCache, ObjectStoreRead
from agents_remember.serving.review_answer_reuse import RecordedAnswerReuse
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember_test_support.testing.review_git_trees import (
    CODE_FILE,
    LEAF,
    MASTER,
    REPO,
    build_world,
    git,
)


def _comparison(prepared: PreparedComparison) -> ReviewTreeComparisonRecord:
    record = prepared.result.comparison
    assert record is not None
    return record


def prepared_world(tmp_path: Path, *, recorded: bool):
    world = build_world(tmp_path)
    world.edit()
    shared = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )
    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF)
    prepared = shared.prepare(query)
    assert isinstance(prepared, PreparedComparison), prepared
    if recorded:
        world.contract(live=False)
        prepared = shared.prepare(replace(query, recorded=True))
        assert isinstance(prepared, PreparedComparison), prepared
    assert prepared.witness.current()
    return world, prepared


@pytest.mark.parametrize("recorded", [False, True])
def test_bound_witness_survives_owned_checkouts_without_source_edits(tmp_path, recorded):
    world, prepared = prepared_world(tmp_path / "world", recorded=recorded)
    before_head = git(world.code_worktree, "rev-parse", "HEAD")
    checkout_root = tmp_path / "checkouts"
    checkout_root.mkdir()
    record = _comparison(prepared)
    checkout_tree(
        world.code, record.code_candidate.tree, checkout_root / "code", root=checkout_root
    )
    checkout_tree(
        world.memory, record.memory_candidate.tree, checkout_root / "memory", root=checkout_root
    )
    assert git(world.code_worktree, "rev-parse", "HEAD") == before_head
    assert changed_observations(dict(prepared.witness.reads)) == ()
    assert prepared.witness.current()


@pytest.mark.parametrize("recorded", [False, True])
def test_compensated_loss_of_a_bound_blob_is_still_refused(tmp_path, recorded):
    world, prepared = prepared_world(tmp_path / "world", recorded=recorded)
    blob = git(world.code, "rev-parse", f"{world.code_base}:{CODE_FILE}")
    replacement = tmp_path / "unrelated-blob.txt"
    replacement.write_text("a distinct unrelated object\n")
    git(world.code, "hash-object", "-w", str(replacement))
    (world.code / ".git/objects" / blob[:2] / blob[2:]).unlink()
    assert not prepared.witness.current()


@pytest.mark.parametrize("recorded", [False, True])
def test_config_change_is_still_refused(tmp_path, recorded):
    world, prepared = prepared_world(tmp_path / "world", recorded=recorded)
    git(world.code, "config", "core.ignorecase", "true")
    assert not prepared.witness.current()


@pytest.mark.parametrize("recorded", [False, True])
def test_replacement_ref_is_still_refused(tmp_path, recorded):
    world, prepared = prepared_world(tmp_path / "world", recorded=recorded)
    base_blob = git(world.code, "rev-parse", f"{world.code_base}:{CODE_FILE}")
    replacement = tmp_path / "replacement.txt"
    replacement.write_text("a replacement of the bound body\n")
    other_blob = git(world.code, "hash-object", "-w", str(replacement))
    git(world.code, "replace", base_blob, other_blob)
    assert not prepared.witness.current()


def test_changed_staged_content_is_still_refused(tmp_path):
    world, prepared = prepared_world(tmp_path / "world", recorded=False)
    (world.code_worktree / CODE_FILE).write_text("def changed_staged():\n    return 1\n")
    git(world.code_worktree, "add", CODE_FILE)
    assert not prepared.witness.current()


def test_negative_object_store_witness_remains_strict_on_owned_checkout(tmp_path):
    world, prepared = prepared_world(tmp_path / "world", recorded=False)
    cache = ChangesetCache()
    source = ObjectStoreRead(world.code)
    before = cache.snapshot(source)
    checkout_root = tmp_path / "checkouts"
    checkout_root.mkdir()
    checkout_tree(
        world.code,
        _comparison(prepared).code_candidate.tree,
        checkout_root / "code",
        root=checkout_root,
    )
    assert cache.snapshot(source) != before
    assert prepared.witness.current()


def test_cached_missing_object_refusal_is_invalidated_when_the_object_is_restored(tmp_path):
    world = build_world(tmp_path / "world")
    blob = git(world.code, "rev-parse", f"{world.code_base}:{CODE_FILE}")
    original = run_git_bytes(world.code, ["cat-file", "blob", blob])
    assert original.returncode == 0
    restoration = tmp_path / "restore-exact-blob"
    restoration.write_bytes(original.stdout)
    (world.code / ".git/objects" / blob[:2] / blob[2:]).unlink()
    reuse = RecordedAnswerReuse()
    sources = (ObjectStoreRead(world.code),)
    key = ("native-blob", blob)
    calls = []

    def read_blob():
        calls.append(blob)
        result = run_git_bytes(world.code, ["cat-file", "blob", blob])
        if result.returncode:
            return {"state": "refused", "reason": "missing-object", "object": blob}
        return {"state": "found", "object": blob, "body": result.stdout}

    refused = reuse.answer(key, sources, read_blob)
    assert refused == {"state": "refused", "reason": "missing-object", "object": blob}
    assert reuse.answer(key, sources, read_blob) == refused
    assert calls == [blob]
    assert reuse.retained() == 1
    assert git(world.code, "hash-object", "-w", str(restoration)) == blob
    restored = reuse.answer(key, sources, read_blob)
    assert restored == {"state": "found", "object": blob, "body": original.stdout}
    assert reuse.answer(key, sources, read_blob) == restored
    assert calls == [blob, blob]
