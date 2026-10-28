"""MIK-R25: the reviewer on Git trees -- four trees, pins, the tree view, reopen, legacy.

The fixture is a coordination root holding one task and one leaf enclosure, a code repository and a
converted memory repository (``main`` is the official line of both), and the leaf's two linked
worktrees on their work branches. The leaf edits code and knowledge without committing, which is
the live review's ordinary state.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager as _contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import apsw
import pytest
from agents_remember.application import review_tree_comparison, review_tree_entries
from agents_remember.application.knowledge_currentness import CodeTree
from agents_remember.application.knowledge_currentness.observe import OpenedCodeTree
from agents_remember.application.knowledge_reader.marks import open_weakness_counts_in
from agents_remember.application.knowledge_review import (
    compose_review,
    list_knowledge_review_entries,
    read_complete_knowledge_review,
)
from agents_remember.application.knowledge_worklist.code import CodeReadError, CodeTrees
from agents_remember.application.review_answer_preparation import RecordedReviewAnswers
from agents_remember.application.review_artifact_cleanup import (
    cleanup_review_artifacts,
)
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
)
from agents_remember.application.review_source_content import read_review_source_content
from agents_remember.application.review_tree_knowledge import read_review_trees
from agents_remember.application.reviewer_worklist_child import isolated_leaf_worklist
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo
from agents_remember.memory.knowledge_index import KnowledgeIndex, MemoryTreeError, text_uuid
from agents_remember.models.knowledge.read import InvariantIdentitySeed
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.review_source_content import ReviewSourceContentRequest
from agents_remember.models.knowledge.review_tree_entries import (
    EXCERPT_MAX_LINES,
    ReviewTreeEntry,
)
from agents_remember.models.knowledge.review_trees import ReviewTreeSideState
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.serving.review_answer_reuse import RecordedAnswerReuse
from agents_remember.serving.review_trees import ReviewTreesQuery, register_review_trees_route
from agents_remember.worktrees.modules.capture_baseline import (
    begin_capture_baseline,
    capture_baseline_unchanged,
    capture_entries,
    memo_capture_baselines,
    note_capture_baseline,
)
from agents_remember.worktrees.modules.git import (
    worktree_candidate_tree,
)
from agents_remember.worktrees.services import (
    ReviewArtifactCleanupRequest,
    reset_worktree_services,
)
from agents_remember_test_support.testing.review_git_trees import (
    CODE_FILE,
    FAMILY,
    INVARIANT,
    LEAF,
    MASTER,
    ORIGIN,
    REPO,
    World,
    _write,
    build_world,
    commit,
    converted_memory,
    git,
    history_file,
    invariant,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def world(tmp_path: Path) -> World:
    return build_world(tmp_path)


def _resolve(world: World, *, recorded: bool = False) -> ReviewCandidateResolution:
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF, recorded=recorded)
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    return resolved


def _refs(repository: Path) -> dict[str, str]:
    listed = git(repository, "for-each-ref", "--format=%(refname) %(objectname)", "refs/ar/")
    return dict(line.split(" ", 1) for line in listed.splitlines() if line)


def _has_object(repository: Path, object_id: str) -> bool:
    return (
        subprocess.run(
            ["git", "cat-file", "-e", object_id], cwd=repository, capture_output=True, check=False
        ).returncode
        == 0
    )


def _query(**fields: Any) -> ReviewTreesQuery:
    return ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF, **fields)


def _seed() -> InvariantIdentitySeed:
    return InvariantIdentitySeed(invariant_id=text_uuid("identity", INVARIANT))


# -- rule 1: the comparison ------------------------------------------------------------------------


def test_a_live_review_is_four_trees_with_its_uncommitted_candidates_pinned_and_reused(
    world: World,
) -> None:
    world.edit()
    resolved = _resolve(world)
    trees = resolved.trees
    assert trees is not None and trees.record_path is not None
    record = trees.record
    ref = f"refs/ar/review/{MASTER}/{LEAF}/1"
    assert record.number == 1 and record.task_id == MASTER
    # Committed sides name their commits and need no ref; the two uncommitted candidates are pinned.
    assert record.code_base.commit == world.code_base and record.code_base.ref is None
    assert record.memory_base.commit == world.memory_base and record.memory_base.ref is None
    assert record.code_candidate.ref == ref and record.memory_candidate.ref == ref
    assert _refs(world.code) == {ref: record.code_candidate.tree}
    assert _refs(world.memory) == {ref: record.memory_candidate.tree}
    assert git(world.memory, "cat-file", "-t", ref) == "tree"
    assert json.loads(trees.record_path.read_text())["schema"] == "ar-review-tree-comparison/v1"
    # Each memory side is read through the index of its own tree -- never a dataset copy.
    cache = world.config.coordination_root / "runtime" / "knowledge-index"
    assert resolved.baseline_database.parent == cache
    assert resolved.candidate_database == cache / f"{record.memory_candidate.tree}.sqlite"
    # The landed composition reads the index unchanged (rule 6), and declares the tree comparison.
    review = compose_review(resolved, world.review(selector=_seed()))
    assert review.state == "review" and review.payload is not None, review.refusal
    knowledge = review.payload.knowledge
    assert knowledge.before_statement.text == "Values land unchanged."
    assert knowledge.after_statement.text == "Values land exactly as given."
    assert "review:trees:1" in review.payload.limitations
    assert "knowledge-index:after:complete" in review.payload.limitations
    entries = list_knowledge_review_entries(world.config, REPO, MASTER, LEAF)
    assert entries.state == "entries" and {e.label for e in entries.entries} == {
        INVARIANT,
        "Landing",
    }
    # Reading again reuses the record, re-creating a pin that has gone; a new edit is a new
    # comparison with its own pins.
    git(world.memory, "update-ref", "-d", ref)
    assert _resolve(world).trees.record.number == 1  # type: ignore[union-attr]
    assert _refs(world.memory) == {ref: record.memory_candidate.tree}
    (world.code_worktree / "pkg" / "b.py").write_text("B = 1\n", encoding="utf-8")
    second = _resolve(world).trees
    assert second is not None and second.record.number == 2
    assert set(_refs(world.code)) == {ref, f"refs/ar/review/{MASTER}/{LEAF}/2"}
    # The memory candidate did not change, so its pin at 2 names the same tree as at 1.
    assert _refs(world.memory)[f"refs/ar/review/{MASTER}/{LEAF}/2"] == record.memory_candidate.tree


def _state(world: World) -> dict[str, object]:
    """Every ref, every object and every recorded comparison file, byte for byte."""

    def objects(repository: Path) -> list[str]:
        listed = git(repository, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)")
        return sorted(listed.split())

    records = world.task_root / "notes" / "reports" / "review-comparisons"
    return {
        "refs": {repo.name: git(repo, "for-each-ref") for repo in (world.code, world.memory)},
        "objects": {repo.name: objects(repo) for repo in (world.code, world.memory)},
        "records": {
            str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in sorted(records.rglob("*.json"))
        },
    }


def test_a_read_writes_only_review_refs_and_comparison_objects_and_a_repeat_writes_nothing(
    world: World,
) -> None:
    world.edit()
    before = _state(world)
    view = read_review_trees(world.config, _query())
    assert view.state == "trees" and view.comparison is not None
    first = _state(world)
    # The only refs the read wrote are the review pins; no branch, HEAD or other ref moved.
    for repo in ("code", "memory"):
        old = set(before["refs"][repo].splitlines())  # type: ignore[index]
        new = set(first["refs"][repo].splitlines())  # type: ignore[index]
        assert old <= new and all("refs/ar/review/" in line for line in new - old)
    # The objects it added are the comparison's own: the two captured candidate trees and theirs.
    added = set(first["objects"]["memory"]) - set(before["objects"]["memory"])  # type: ignore[index]
    assert view.comparison.memory_candidate.tree in added
    # Reading again -- the tree view, the review, the entry list -- writes nothing new at all.
    read_review_trees(world.config, _query())
    compose_review(_resolve(world), world.review(selector=_seed()))
    list_knowledge_review_entries(world.config, REPO, MASTER, LEAF)
    assert _state(world) == first


def test_committed_candidates_need_no_ref_and_a_failed_pin_refuses_naming_repository_and_ref(
    world: World,
) -> None:
    # Nothing edited: both candidates are the durable line's own trees, so nothing is pinned.
    committed = _resolve(world).trees
    assert committed is not None
    assert committed.record.code_candidate.commit == world.code_base
    assert committed.record.memory_candidate.commit == world.memory_base
    assert _refs(world.code) == {} and _refs(world.memory) == {}
    world.edit()
    real = review_tree_comparison.run_git

    def refusing(repository: Path, args: list[str], *rest: Any) -> Any:
        if args[0] == "update-ref" and repository == world.memory:
            return subprocess.CompletedProcess(args, 1, "", "cannot lock ref")
        return real(repository, args, *rest)

    with mock.patch.object(review_tree_comparison, "run_git", refusing):
        refused = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(refused, ReviewRefusal)
    assert refused.offending_input == f"refs/ar/review/{MASTER}/{LEAF}/2"
    assert str(world.memory) in refused.detail and "not published" in refused.detail
    # The code pin this attempt made was removed again, and nothing was recorded.
    assert _refs(world.code) == {} and _refs(world.memory) == {}
    assert len(list(committed.record_path.parent.glob("*.json"))) == 1  # type: ignore[union-attr]


# -- rules 2 and 3: the tree view ----------------------------------------------------------------------


def test_the_tree_view_shows_currentness_per_side_and_the_worklist_without_a_knowledge_diff(
    world: World,
) -> None:
    world.edit()
    view = read_review_trees(world.config, _query())
    assert view.state == "trees" and view.comparison is not None
    assert "knowledge_diff" not in view.model_dump(by_alias=True)
    assert [side.index_state for side in view.knowledge_sides] == ["complete", "complete"]
    # Currentness per side, each against its own code tree: the body edit makes the after side stale.
    assert view.currentness is not None
    before, after = view.currentness["before"], view.currentness["after"]
    # Every key is snake_case on the wire (MIK-L25 review F9), including the owners' own documents.
    assert before["code_tree"]["tree_id"] == view.comparison.code_base.tree
    assert {one["id"]: one["state"] for one in before["invariants"]} == {INVARIANT: "current"}
    assert {one["id"]: one["state"] for one in after["invariants"]} == {INVARIANT: "stale"}
    assert before["invariants"][0]["entries"] == []
    assert [one["id"] for one in after["invariants"][0]["entries"]] == ["RLZ-A00001"]
    assert before["counts"] == {"stale": 0, "unverifiable": 0, "unrealized": 0, "current": 1}
    assert after["counts"] == {"stale": 1, "unverifiable": 0, "unrealized": 0, "current": 0}
    assert after["families"] == [
        {"id": FAMILY, "members": 1, "stale_members": 1, "stale": [INVARIANT]}
    ]
    # The worklist view: the items, the history rows about their subjects, the gate linkage.
    worklist = view.worklist
    assert worklist is not None and worklist.source == "computed" and worklist.bound
    kinds = {(item["kind"], item["subject"]) for item in worklist.items}
    assert ("touched_invariant", INVARIANT) in kinds and ("reached_family", FAMILY) in kinds
    assert [(row["subject"], row["disposition"]) for row in worklist.history_rows] == [
        (FAMILY, "no_impact")
    ]
    assert "current" not in worklist.history_rows[0] and "stale" not in worklist.history_rows[0]
    assert worklist.history_rows[0]["owner_kind"] == "leaf"
    assert view.entries == ()  # the cards' entries are read per selection, never leaf-wide
    change = next(one for one in worklist.changes if one["path"] == CODE_FILE)
    assert [hunk["linked"] for hunk in change["hunks"]] == [True]


@pytest.mark.parametrize("extra_files", [0, 8])
def test_a_subject_read_starts_at_most_40_git_children(world: World, extra_files: int) -> None:
    """MIK-R81 rule 7: one subject read stays within its Git child budget.

    operation the dashboard's default review route renders -- with a selected subject, not the
    trees-only route: the asserts below require the composed knowledge pane, the measured
    source inventory and the bound comparison, which a trees-only answer cannot produce, so a
    fix pointed at the wrong route fails here rather than passing. The count is taken at the
    runner, so a child saved is a child counted.

    The first recording read, the next cold read on recorded state and the warm read
    are each counted, including comparison writes, pins and index construction. Both stay within budget, and both answers are identical:
    rule 7 changes how many requests are sent, not what a response holds. The read is sampled at
    two corpus sizes (the fixture world, and the same world with eight extra code files and
    eight extra invariants riding the candidate): the count is per read, not per file, so both
    samples stay within budget.
    """
    world.edit()
    for index in range(extra_files):
        (world.code_worktree / f"pkg/extra{index}.py").write_text(
            f"def f{index}():\n    return {index}\n", encoding="utf-8"
        )
        (world.memory_worktree / f"knowledge/invariants/INV-X{index:05d}-e.json").write_text(
            invariant(1, f"Extra {index}.").replace(INVARIANT, f"INV-X{index:05d}"),
            encoding="utf-8",
        )
    children = 0
    child_argv: list[list[str]] = []
    real_run = subprocess.run

    def counting(*args: Any, **kwargs: Any) -> Any:
        nonlocal children
        argv = args[0] if args else []
        if isinstance(argv, list) and argv and argv[0] == "git":
            children += 1
            child_argv.append(argv)
        return real_run(*args, **kwargs)

    def counted() -> Any:
        nonlocal children
        children = 0
        child_argv.clear()
        with mock.patch.object(subprocess, "run", counting):
            return read_complete_knowledge_review(world.config, world.review(selector=_seed()))

    # The recording read is budgeted too: rule 7 names no prime exception, so the read that
    # records the comparison, writes the pins and builds the indexes stays within the same
    # ceiling as the reads that follow it.
    primed = counted()
    prime_children = children
    assert primed.state == "review" and primed.payload is not None, primed.refusal
    assert prime_children <= 40, (
        f"recording subject read started {prime_children} git children: {child_argv!r}"
    )

    cold = counted()
    cold_children = children
    warm = counted()
    warm_children = children
    assert cold.state == "review" and cold.payload is not None, cold.refusal
    assert warm.state == "review" and warm.payload is not None, warm.refusal
    assert primed.model_dump(mode="json", by_alias=True) == cold.model_dump(
        mode="json", by_alias=True
    ), "the recording read answers what the later reads answer"
    assert cold_children <= 40, f"cold subject read started {cold_children} git children"
    assert warm_children <= 40, f"warm subject read started {warm_children} git children"
    assert warm.model_dump(mode="json", by_alias=True) == cold.model_dump(
        mode="json", by_alias=True
    )
    # The actual subject read, not the trees route: the composed knowledge pane states the
    # selected subject on both sides, the source pane measured the bound pair's inventory,
    # and the payload binds the comparison and the candidate.
    for result in (cold, warm):
        assert result.payload is not None
        knowledge = result.payload.knowledge
        assert knowledge.before_statement.text == "Values land unchanged."
        assert knowledge.after_statement.text == "Values land exactly as given."
        assert result.payload.comparison is not None
        assert result.payload.candidate.leaf_id == LEAF
        assert CODE_FILE in [entry.path for entry in result.payload.source.inventory.entries]


def _captured(
    repo: Path, index_path: Path
) -> tuple[str, tuple[tuple[str, int, int, int, int], ...]]:
    """One capture and the census it recorded, as the composition's own capture reads them."""

    seen: list[tuple[str, int, int, int, int]] = []

    def observe(index: Path) -> None:
        seen.extend(capture_entries(index) or ())

    return worktree_candidate_tree(repo, index_path, captured=observe), tuple(seen)


def test_an_unmoved_worktree_skips_to_the_tree_a_rederive_would_derive(world: World) -> None:
    """F17: the fast recheck stands for the same tree, not a cheaper different one.

    Classification of the recheck captures the read-latency instruments count: the resolve
    captures each worktree, the recheck proves the worktree unmoved and skips the second
    derivation. This test pins the skip's meaning — a forced fresh derivation beside a hit
    names the identical tree — so the instruments' lower counts record the same proof for
    fewer children, not a skipped currentness check. A moved worktree misses and re-derives
    to its new tree.
    """

    world.edit()
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        for worktree in (world.code_worktree, world.memory_worktree):
            with memo_capture_baselines():
                before = begin_capture_baseline(worktree)
                tree, entries = _captured(worktree, scratch / "resolve.index")
                note_capture_baseline(worktree, tree=tree, entries=entries, before=before)
                assert capture_baseline_unchanged(worktree, tree=tree)
                rederived = worktree_candidate_tree(worktree, scratch / "recheck.index")
                assert rederived == tree
        moved = world.code_worktree / "pkg" / "a.py"
        moved.write_text("VALUE = 2\n", encoding="utf-8")
        with memo_capture_baselines():
            before = begin_capture_baseline(world.code_worktree)
            tree, entries = _captured(world.code_worktree, scratch / "resolve2.index")
            note_capture_baseline(world.code_worktree, tree=tree, entries=entries, before=before)
            moved.write_text("VALUE = 3\n", encoding="utf-8")
            assert not capture_baseline_unchanged(world.code_worktree, tree=tree)
            assert worktree_candidate_tree(world.code_worktree, scratch / "recheck2.index") != tree


def _side_record(before_tree: str, after_tree: str) -> Any:
    """A minimal comparison record naming two memory trees (F19 unit seam)."""

    return SimpleNamespace(
        converted_base=None,
        memory_base=SimpleNamespace(tree=before_tree),
        memory_candidate=SimpleNamespace(tree=after_tree),
    )


def test_a_failed_cache_construction_is_both_sides_unavailable_history(world: World) -> None:
    """F19: a faulted real cache constructor keeps the old typed side outcomes."""
    record = _side_record("0" * 40, "1" * 40)
    for fault in (MemoryTreeError("index build broke"), OSError("disk gone")):
        with mock.patch.object(review_tree_comparison, "KnowledgeIndexCache", side_effect=fault):
            trees = review_tree_comparison._open_sides(
                world.config.coordination_root, record, world.memory, None
            )
        assert trees.before.wire.state == "unavailable-history"
        assert trees.after.wire.state == "unavailable-history"
        assert "cannot be indexed" in (trees.before.wire.detail or "")
        assert trees.before.database is None and trees.after.database is None


def test_both_sides_unavailable_never_constructs_the_cache(world: World) -> None:
    """F19: known-unavailable sides never touch the real cache directory."""
    record = _side_record("0" * 40, "1" * 40)
    states: dict[str, tuple[ReviewTreeSideState, str]] = {
        "before": ("unavailable-history", "before is gone"),
        "after": ("unavailable-history", "after is gone"),
    }
    with mock.patch.object(
        review_tree_comparison, "KnowledgeIndexCache", side_effect=AssertionError("constructed")
    ) as constructor:
        trees = review_tree_comparison._open_sides(
            world.config.coordination_root, record, world.memory, None, states=states
        )
    constructor.assert_not_called()
    assert trees.before.wire.state == "unavailable-history"
    assert trees.before.wire.detail == "before is gone"
    assert trees.after.wire.state == "unavailable-history"
    assert trees.after.wire.detail == "after is gone"


def test_live_sides_share_one_constructed_cache(world: World) -> None:
    """F19: successful two-side sharing is preserved, with exact response bytes."""
    asked: list[str] = []

    class _FakeIndex:
        def __init__(self, tree: str) -> None:
            self.state = SimpleNamespace(state="complete", problems=())
            self.database_path = world.config.coordination_root / f"{tree}.sqlite"

    class _FakeCache:
        def __init__(self, directory: object) -> None:
            self.directory = directory

        def for_git_tree(self, _repository: object, tree: str) -> Any:
            asked.append(tree)

            @_contextmanager
            def _open() -> Any:
                yield _FakeIndex(tree)

            return _open()

    record = _side_record("0" * 40, "1" * 40)
    made: list[_FakeCache] = []
    real = review_tree_comparison.KnowledgeIndexCache

    def _construct(directory: object) -> _FakeCache:
        cache = _FakeCache(directory)
        made.append(cache)
        return cache

    with mock.patch.object(review_tree_comparison, "KnowledgeIndexCache", _construct):
        trees = review_tree_comparison._open_sides(
            world.config.coordination_root, record, world.memory, None
        )
    assert len(made) == 1
    assert asked == ["0" * 40, "1" * 40]
    assert trees.before.wire.state == "available"
    assert trees.before.wire.tree == "0" * 40
    assert trees.before.wire.index_state == "complete"
    assert trees.after.wire.state == "available"
    assert trees.after.wire.tree == "1" * 40
    assert real is not None


def _without_keep(world: World) -> str:
    sidecar = json.loads((world.memory_worktree / f"onboarding/{CODE_FILE}.json").read_text())
    sidecar["realizes"] = [one for one in sidecar["realizes"] if one["id"] != "RLZ-A00002"]
    return canonical_text(sidecar)


def test_a_partial_index_shows_its_state_on_the_affected_side(world: World) -> None:
    world.edit()
    _write(world.memory_worktree, {"knowledge/invariants/INV-BBBBBB-broken.json": "{}\n"})
    resolved = _resolve(world)
    assert resolved.trees is not None
    after = resolved.trees.after.wire
    assert after.index_state == "partial"
    assert [path for path, _ in after.problems] == ["knowledge/invariants/INV-BBBBBB-broken.json"]
    assert resolved.trees.before.wire.index_state == "complete"
    review = compose_review(resolved, world.review(selector=_seed()))
    assert review.payload is not None
    assert "knowledge-index:after:partial" in review.payload.limitations


# -- rule 4: reopen, the converted base and legacy comparisons ---------------------------------------


def test_a_comparison_reopens_from_its_tree_ids_and_names_a_tree_git_can_no_longer_produce(
    world: World,
) -> None:
    world.edit()
    live = _resolve(world).trees
    assert live is not None
    world.contract(live=False)  # the enclosure closes; the record is what remains
    reopened = _resolve(world, recorded=True)
    assert reopened.trees is not None and reopened.trees.record == live.record
    review = compose_review(reopened, world.review(selector=_seed()))
    assert review.payload is not None
    assert review.payload.knowledge.after_statement.text == "Values land exactly as given."
    # The pins go and Git collects the candidate tree: the side is unavailable-history, named.
    for repository in (world.code, world.memory):
        for ref in _refs(repository):
            git(repository, "update-ref", "-d", ref)
    git(world.memory, "worktree", "remove", "--force", str(world.memory_worktree))
    git(world.memory, "reflog", "expire", "--expire=now", "--all")
    git(world.memory, "gc", "-q", "--prune=now")
    lost = _resolve(world, recorded=True)
    assert lost.trees is not None
    after = lost.trees.after.wire
    tree = live.record.memory_candidate.tree
    assert after.state == "unavailable-history" and tree in (after.detail or "")
    assert lost.trees.before.wire.state == "available"
    refused = compose_review(lost, world.review(selector=_seed()))
    assert refused.state == "refused" and refused.refusal is not None
    assert f"after:unavailable-history:{tree}" in (refused.refusal.offending_input or "")
    # The code sides stay: the task-context review still carries the source inventory.
    context = compose_review(lost, world.review())
    assert context.payload is not None and context.payload.source.inventory.state != "unavailable"
    assert "history:intent:after:unavailable-history" in context.payload.limitations
    assert [side.state for side in lost.trees.code_sides] == ["available", "available"]
    # Git collects the code candidate too: that code side is unavailable-history, named as well.
    git(world.code, "worktree", "remove", "--force", str(world.code_worktree))
    git(world.code, "reflog", "expire", "--expire=now", "--all")
    git(world.code, "gc", "-q", "--prune=now")
    code_lost = _resolve(world, recorded=True).trees
    assert code_lost is not None
    candidate = live.record.code_candidate.tree
    assert [(side.side, side.state) for side in code_lost.code_sides] == [
        ("base", "available"),
        ("candidate", "unavailable-history"),
    ]
    assert candidate in (code_lost.code_sides[1].detail or "")
    view = read_review_trees(world.config, _query(recorded=True))
    assert [side.state for side in view.code_sides] == ["available", "unavailable-history"]
    assert f"history:code:candidate:unavailable-history:{candidate}" in (
        compose_review(_resolve(world, recorded=True), world.review()).payload.limitations  # type: ignore[union-attr]
    )


def test_an_unconverted_before_side_is_compared_as_its_conversion(world: World) -> None:
    # The official line's base is unconverted (legacy onboarding only); the leaf is converted.
    legacy_code = commit(world.code, {"pkg/c.py": "C = 1\n"})
    git(world.memory, "rm", "-q", "-r", "knowledge")
    legacy_memory = commit(world.memory, {"onboarding/legacy.md": "# legacy\n"}, legacy_code)
    world.code_base = legacy_code
    world.contract()
    world.edit()
    converted = {
        **converted_memory(world.code, world.code_base),
        "onboarding/legacy.md": "# legacy\n",
    }
    files = {path: text.encode() for path, text in converted.items()}
    with mock.patch.object(
        review_tree_comparison, "converted_base_files", return_value=files
    ) as convert:
        trees = _resolve(world).trees
        assert trees is not None and convert.call_count == 1
        base = trees.record.converted_base
        assert base is not None and base.commit == legacy_memory and base.version == "1"
        assert trees.before.wire.tree == base.tree != trees.record.memory_base.tree
        # The conversion itself is no change: only the leaf's own edits are in the knowledge diff.
        view = read_review_trees(world.config, _query())
        review = compose_review(_resolve(world), world.review(selector=_seed()))
    assert view.currentness is not None
    assert "knowledge_diff" not in view.model_dump(by_alias=True)
    assert review.payload is not None
    assert "review:converted-base:1" in review.payload.limitations
    assert review.payload.knowledge.before_statement.text == "Values land unchanged."
    # A reopen re-derives a converted base Git no longer holds, and checks its tree id.
    world.contract(live=False)
    git(world.memory, "worktree", "remove", "--force", str(world.memory_worktree))
    git(world.memory, "reflog", "expire", "--expire=now", "--all")
    git(world.memory, "gc", "-q", "--prune=now")
    assert not _has_object(world.memory, base.tree)
    with mock.patch.object(review_tree_comparison, "converted_base_files", return_value=files):
        reopened = _resolve(world, recorded=True).trees
    assert reopened is not None and reopened.before.wire.state == "available"


def test_a_comparison_recorded_before_the_conversion_keeps_its_code_sides_only(
    world: World,
) -> None:
    # The leaf closed on an unconverted memory line; the official line has converted since.
    git(world.memory, "rm", "-q", "-r", "knowledge")
    legacy_head = commit(world.memory, {"onboarding/old.md": "# old\n"})
    landed = commit(world.code, {"pkg/d.py": "D = 1\n"})
    commit(world.memory, converted_memory(world.code, world.code_base), trailer=landed)
    world.contract(live=False, code_commit=landed, memory_content_commit=legacy_head)
    opened: list[str] = []
    real = apsw.Connection

    def recording(path: str, *args: Any, **kwargs: Any) -> apsw.Connection:
        opened.append(path)
        return real(path, *args, **kwargs)

    with mock.patch.object(apsw, "Connection", recording):
        resolved = _resolve(world, recorded=True)
        assert resolved.trees is None
        assert {state for _, state, _ in resolved.knowledge_unavailable} == {"legacy-unavailable"}
        subject = compose_review(resolved, world.review(selector=_seed()))
        context = compose_review(resolved, world.review())
    assert subject.state == "refused" and subject.refusal is not None
    assert "legacy-unavailable" in subject.refusal.detail
    assert context.payload is not None
    assert context.payload.source.inventory.state != "unavailable"
    assert {"history:legacy-comparison", "history:intent:before:legacy-unavailable"} <= set(
        context.payload.limitations
    )
    assert opened == [], "a legacy comparison's knowledge must not be read from any database"


def test_no_review_path_opens_a_database_other_than_the_derived_index(world: World) -> None:
    world.edit()
    cache = world.config.coordination_root / "runtime" / "knowledge-index"
    opened: list[str] = []
    real_apsw, real_sqlite = apsw.Connection, sqlite3.connect

    def recording(path: str, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(path))
        return real_apsw(path, *args, **kwargs)

    def recording_sqlite(path: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(path))
        return real_sqlite(path, *args, **kwargs)

    with (
        mock.patch.object(apsw, "Connection", recording),
        mock.patch.object(sqlite3, "connect", recording_sqlite),
    ):
        resolved = _resolve(world)
        compose_review(resolved, world.review(selector=_seed()))
        compose_review(resolved, world.review())
        list_knowledge_review_entries(world.config, REPO, MASTER, LEAF)
        read_review_trees(world.config, _query())
    assert opened, "the review read no knowledge at all"
    assert all(Path(path).parent == cache for path in opened), sorted(set(opened))
    assert not list(world.root.rglob("knowledge.sqlite"))


def test_an_unconverted_leaf_keeps_source_and_names_knowledge_unavailable(world: World) -> None:
    # Both lines are unconverted: only the source endpoints remain readable, and no dataset is opened.
    for repository in (world.memory, world.memory_worktree):
        git(repository, "rm", "-q", "-r", "knowledge")
        git(repository, "commit", "-q", "-m", "unconverted")
    world.edit()
    resolved = _resolve(world)
    assert resolved.trees is None
    assert {state for _, state, _ in resolved.knowledge_unavailable} == {"legacy-unavailable"}
    assert not resolved.candidate_database.exists()
    assert _refs(world.code) == {} and _refs(world.memory) == {}
    unreadable = read_review_trees(world.config, _query())
    assert unreadable.state == "refused" and unreadable.refusal is not None
    assert "legacy-unavailable" in unreadable.refusal.detail
    # What such a review reads now: the source identities of its code pair, and no knowledge.
    context = compose_review(resolved, world.review())
    assert context.payload is not None and context.payload.comparison is None
    assert context.payload.source.inventory.state == "measured"
    assert CODE_FILE in {entry.path for entry in context.payload.source.inventory.entries}
    assert "history:intent:after:legacy-unavailable" in context.payload.limitations


# -- rule 5: pins are named by the task directory (the archive hook: test_review_artifact_cleanup)


def test_pins_are_named_by_the_task_directory_and_archival_removes_them(world: World) -> None:
    # task.json's id (260101-TRV) names no review ref: pins live under the directory name (R5).
    world.edit()
    ref = f"refs/ar/review/{MASTER}/{LEAF}/1"
    assert _resolve(world).trees.record.task_id == MASTER  # type: ignore[union-attr]
    assert set(_refs(world.code)) == {ref} and set(_refs(world.memory)) == {ref}
    report = cleanup_review_artifacts(
        ReviewArtifactCleanupRequest(world.task_root, MASTER, world.code, world.memory, False)
    )
    assert {entry["ref"] for entry in report["reviewRefs"]} == {ref}
    assert _refs(world.code) == {} and _refs(world.memory) == {}


def test_the_tree_view_route_serves_the_port_and_refuses_when_unwired(world: World) -> None:
    world.edit()
    app = FastAPI()
    seen: list[ReviewTreesQuery] = []

    def port(query: ReviewTreesQuery) -> Any:
        seen.append(query)
        return read_review_trees(world.config, query)

    register_review_trees_route(app, port)
    client = TestClient(app)
    body = client.get("/api/review/trees", params={"repo": REPO, "master": MASTER, "leaf": LEAF})
    assert body.status_code == 200 and body.json()["state"] == "trees"
    assert body.json()["comparison"]["schema"] == "ar-review-tree-comparison/v1"
    numbered = client.get(
        "/api/review/trees",
        params={"repo": REPO, "master": MASTER, "leaf": LEAF, "comparison": 1},
    )
    assert numbered.json()["comparison"]["number"] == 1 and seen[-1].number == 1
    # Pinned to comparison 1, which is the live leaf's current one: its worklist stays computed.
    assert numbered.json()["worklist"]["source"] == "computed"
    cards = client.get(
        "/api/review/trees",
        params={"repo": REPO, "master": MASTER, "leaf": LEAF, "invariants": "a,b"},
    )
    assert cards.status_code == 200 and seen[-1].invariants == ("a", "b")
    too_long = client.get(
        "/api/review/trees",
        params={"repo": REPO, "master": MASTER, "leaf": LEAF, "invariants": "k" * 65},
    )
    assert too_long.status_code == 400 and "65" not in too_long.json()["detail"]
    too_many = ",".join(str(number) for number in range(501))
    assert (
        client.get(
            "/api/review/trees",
            params={"repo": REPO, "master": MASTER, "leaf": LEAF, "invariants": too_many},
        ).status_code
        == 400
    )
    unwired = FastAPI()
    register_review_trees_route(unwired, None)
    assert (
        TestClient(unwired)
        .get("/api/review/trees", params={"repo": REPO, "master": MASTER, "leaf": LEAF})
        .status_code
        == 503
    )


def _with_proof_and_unresolved(world: World) -> str:
    """K_C's sidecar: ``keep`` retired (RLZ-A00002), a proof added, a realization of a gone name."""

    sidecar = json.loads(_without_keep(world))
    anchor = dict(sidecar["realizes"][0]["anchor"])
    sidecar["proves"] = [
        {
            "id": "PRF-A00003",
            "invariant": INVARIANT,
            "anchor": {**anchor, "locator": {"kind": "symbol", "name": "keep"}},
            "facet": "keep answers one.",
        }
    ]
    sidecar["realizes"].append(
        {
            **sidecar["realizes"][0],
            "id": "RLZ-A00004",
            "anchor": {**anchor, "locator": {"kind": "symbol", "name": "gone"}},
        }
    )
    return canonical_text(sidecar)


def test_the_cards_read_locates_each_entry_of_the_named_invariants_on_both_code_sides(
    world: World,
) -> None:
    world.edit()
    _write(
        world.memory_worktree, {f"onboarding/{CODE_FILE}.json": _with_proof_and_unresolved(world)}
    )
    key = text_uuid("identity", INVARIANT)
    view = read_review_trees(world.config, _query(invariants=(key,)))
    assert view.state == "trees" and view.worklist is None
    entries = {entry.id: entry for entry in view.entries}
    assert list(entries) == ["PRF-A00003", "RLZ-A00001", "RLZ-A00002", "RLZ-A00004"]
    assert {entry.invariant_key for entry in view.entries} == {key}
    _changed_range(entries["RLZ-A00001"])
    _retired_entry(entries["RLZ-A00002"])
    _proof_entry(entries["PRF-A00003"])
    _unresolved_entry(entries["RLZ-A00004"])
    # An identity no memory tree holds contributes nothing.
    assert read_review_trees(world.config, _query(invariants=("no-such-key",))).entries == ()


def _changed_range(land: ReviewTreeEntry) -> None:
    """The edited body: one changed range, each side's excerpt from its own blob and R03 state."""

    assert land.change == "changed"
    assert (land.after.role, land.after.rationale) == ("primary-authority", "It is the rule.")
    assert (land.before.start_line, land.before.end_line) == (1, 2)
    assert (land.after.start_line, land.after.end_line) == (1, 2)
    assert land.before.excerpt == "def land(value):\n    return value\n"
    assert land.after.excerpt == "def land(value):\n    return value + 0\n"
    assert (land.before.currentness, land.after.currentness) == ("current", "stale")


def _retired_entry(keep: ReviewTreeEntry) -> None:
    """Retired in K_C: still located on both sides, its excerpt carried once, named unrecorded."""

    assert keep.change == "unchanged"
    assert (keep.before.recorded, keep.after.recorded) == (True, False)
    assert keep.before.excerpt is None
    assert keep.after.excerpt == "def keep():\n    return 1\n"
    assert (keep.after.currentness, keep.after.rationale) == (None, None)


def _proof_entry(proof: ReviewTreeEntry) -> None:
    """A proof carries its facet and no role or rationale; added in K_C, it is unrecorded at B."""

    assert (proof.kind, proof.after.facet) == ("proof", "keep answers one.")
    assert (proof.after.role, proof.before.recorded) == (None, False)


def _unresolved_entry(gone: ReviewTreeEntry) -> None:
    """A locator that resolves nowhere is unresolved, with the reason and no range."""

    assert (gone.change, gone.after.state, gone.after.start_line) == (
        "undetermined",
        "unresolved",
        None,
    )
    assert "does not resolve" in (gone.after.reason or "")


SECOND = "INV-CCCCCC"


def _memory_facts(world: World) -> None:
    """K_C for the memory values: a second invariant at the path, a decision link, two weaknesses."""

    world.edit()
    sidecar = json.loads(_with_proof_and_unresolved(world))
    first = sidecar["realizes"][0]
    second_anchor = {**first["anchor"], "locator": {"kind": "file"}}
    sidecar["realizes"].append(
        {**first, "id": "RLZ-A00005", "invariant": SECOND, "anchor": second_anchor}
    )
    weakness = {
        "schema": "ar-weakness/v1",
        "revision": 1,
        "kind": "time",
        "statement": "The land body is read twice.",
        "anchor": {**first["anchor"], "path": CODE_FILE},
        "seen": {"task": LEAF, "role": "worker"},
        "origin": ORIGIN,
    }
    decision = {
        "schema": "ar-decision/v1",
        "id": "DEC-AAAAAA",
        "revision": 1,
        "status": "active",
        "context": "Values land as given.",
        "alternatives": [{"option": "Land", "status": "chosen", "reason": "Exact."}],
        "consequences": ["Nothing is rounded."],
        "decider": "developer",
        "supersedes": [],
        "admission": "legacy-unassessed",
        "links": [{"target": INVARIANT, "relation": "constrains"}],
        "origin": ORIGIN,
    }
    closed = {"task": LEAF, "commit": "c" * 40}
    _write(
        world.memory_worktree,
        {
            f"onboarding/{CODE_FILE}.json": canonical_text(sidecar),
            f"knowledge/invariants/{SECOND}-second.json": canonical_text(
                {**json.loads(invariant()), "id": SECOND}
            ),
            "knowledge/decisions/DEC-AAAAAA-land.json": canonical_text(decision),
            "knowledge/weaknesses/WKN-7K3F9Q-open.json": canonical_text(
                {**weakness, "id": "WKN-7K3F9Q", "state": "open"}
            ),
            "knowledge/weaknesses/WKN-8M4G0R-closed.json": canonical_text(
                {**weakness, "id": "WKN-8M4G0R", "state": "closed", "closed": closed}
            ),
        },
    )


def test_the_cards_read_gives_each_entry_its_four_memory_values_at_the_after_tree(
    world: World,
) -> None:
    _memory_facts(world)
    keys = tuple(text_uuid("identity", one) for one in (INVARIANT, SECOND))
    view = read_review_trees(world.config, _query(invariants=keys))
    assert view.comparison is not None and view.entry_memory is not None
    assert (view.entry_memory.state, view.entry_memory.index_state) == ("available", "complete")
    assert view.entry_memory.tree == view.comparison.memory_candidate.tree
    entries = {entry.id: entry for entry in view.entries}
    assert list(entries) == ["PRF-A00003", "RLZ-A00001", "RLZ-A00002", "RLZ-A00004", "RLZ-A00005"]
    trees = _resolve(world).trees
    assert trees is not None and trees.after.database is not None
    with KnowledgeIndex(trees.after.database) as index:
        # Each value is its definition, asked of the bound after-side index itself (evidence 4).
        at_path = {one.invariant for one in index.entries_at_path(CODE_FILE).value.realizations}
        links = {one: len(index.incoming_links(one).value) for one in (INVARIANT, SECOND)}
        unproved = {record.id for record in index.invariants_without_proof().value}
        weaknesses = open_weakness_counts_in(index)
        # The family's member link counts beside the decision's link (W15); linked_from omits it.
        assert len(index.invariant(INVARIANT).value.linked_from) == 1
    assert (at_path, links, unproved, weaknesses) == (
        {INVARIANT, SECOND},
        {INVARIANT: 2, SECOND: 0},
        {SECOND},
        {CODE_FILE: 1},  # the closed weakness is not counted
    )
    for entry in view.entries:
        assert entry.memory is not None and entry.memory.path == CODE_FILE
        assert (
            entry.memory.invariants_at_path,
            entry.memory.incoming_links,
            entry.memory.without_proof,
            entry.memory.open_weaknesses,
        ) == (len(at_path), links[entry.invariant], entry.invariant in unproved, 1)
    # Each side names its locator's symbol; a whole-file locator names none (W12).
    land, keep, whole = entries["RLZ-A00001"], entries["RLZ-A00002"], entries["RLZ-A00005"]
    assert (land.before.symbol, land.after.symbol, keep.after.symbol) == ("land", "land", "keep")
    assert (whole.before.symbol, whole.after.symbol) == (None, None)
    # Only the card read carries memory values; the lane answer is unchanged by them.
    assert read_review_trees(world.config, _query(lane=True)).entry_memory is None


def test_the_cards_read_answers_without_memory_values_when_they_cannot_be_read(
    world: World,
) -> None:
    world.edit()
    keys = (text_uuid("identity", INVARIANT),)
    trees = _resolve(world).trees
    assert trees is not None
    # An after side with no index: the entries are answered, without values, and say why.
    gone = trees.after.wire.model_copy(update={"state": "unavailable-history", "detail": "gone"})
    lost = review_tree_comparison.TreeKnowledge(wire=gone, database=None)
    cards = review_tree_entries.tree_entries(replace(trees, after=lost), keys)
    assert (cards.memory.state, cards.memory.reason, cards.memory.tree) == (
        "unavailable",
        "gone",
        trees.record.memory_candidate.tree,
    )
    assert cards.entries and all(entry.memory is None for entry in cards.entries)
    # A read that fails on a readable side: unavailable, naming the tree, and never kept (INV-5M9WY3).
    world.contract(live=False)
    reuse = RecordedAnswerReuse()
    answers = RecordedReviewAnswers(world.config, reuse)
    query = _query(recorded=True, invariants=keys)
    # An after index that cannot be built is a failed read too, never the record's state.
    cache, after = review_tree_comparison.KnowledgeIndexCache, trees.record.memory_candidate.tree
    built = cache.for_git_tree

    def build(self: Any, repository: Path, tree: str) -> KnowledgeIndex:
        if tree == after:
            raise OSError("the index directory is full")
        return built(self, repository, tree)

    with mock.patch.object(cache, "for_git_tree", build):
        unbuilt = answers.trees(query, lambda: read_review_trees(world.config, query))
    assert [side.state for side in unbuilt.knowledge_sides if side.side == "after"] == [
        "unavailable-history"
    ]
    assert reuse.retained() == 0
    failure = apsw.Error("disk I/O error")
    with mock.patch.object(KnowledgeIndex, "incoming_links", side_effect=failure):
        failed = answers.trees(query, lambda: read_review_trees(world.config, query))
    assert failed.entry_memory is not None and failed.entry_memory.state == "unavailable"
    assert trees.record.memory_candidate.tree in (failed.entry_memory.reason or "")
    assert "disk I/O error" in (failed.entry_memory.reason or "")
    assert failed.entries and all(entry.memory is None for entry in failed.entries)
    assert reuse.retained() == 0
    answered = answers.trees(query, lambda: read_review_trees(world.config, query))
    assert answered.entry_memory is not None and answered.entry_memory.state == "available"
    assert reuse.retained() == 1
    assert answers.trees(query, lambda: pytest.fail("a kept answer is computed again")) == answered


def test_the_cards_read_names_an_unreadable_side_unavailable_and_a_missing_file_absent(
    world: World,
) -> None:
    world.edit()
    (world.code_worktree / CODE_FILE).unlink()
    keys = (text_uuid("identity", INVARIANT),)
    land = next(e for e in read_review_trees(world.config, _query(invariants=keys)).entries)
    assert (land.before.state, land.after.state, land.change) == ("resolved", "absent", "changed")
    record = read_review_trees(world.config, _query(invariants=keys)).comparison
    assert record is not None
    real_open = review_tree_entries.open_code_tree

    def unreadable_candidate(tree: CodeTree | None) -> OpenedCodeTree:
        if tree is not None and tree.tree == record.code_candidate.tree:
            return OpenedCodeTree(tree=tree, trees=None, files={}, problem="the tree is gone")
        return real_open(tree)

    with mock.patch.object(review_tree_entries, "open_code_tree", unreadable_candidate):
        land = next(e for e in read_review_trees(world.config, _query(invariants=keys)).entries)
    assert (land.after.state, land.after.reason) == ("unavailable", "the tree is gone")
    assert land.change == "undetermined" and land.after.excerpt is None


def test_history_rows_are_found_by_the_row_subject_an_item_names(world: World) -> None:
    world.edit()
    item = {"kind": "unexplained_hunk", "subject": "hunk:x", "facts": {"row": FAMILY}}
    document = {"items": [item], "changes": [], "incomplete": [], "state": "open"}

    def observed_document(*args: Any, **kwargs: Any) -> dict[str, Any]:
        # Keep the real computation's input observations before replacing its item for this
        # history projection case; a read-free mock is correctly refused by the view's guard.
        assert isolated_leaf_worklist(*args, **kwargs) is not None
        return document

    with mock.patch(
        "agents_remember.application.review_tree_knowledge.isolated_leaf_worklist",
        side_effect=observed_document,
    ):
        worklist = read_review_trees(world.config, _query()).worklist
    assert worklist is not None
    assert [(row["subject"], row["owner_kind"]) for row in worklist.history_rows] == [
        (FAMILY, "leaf")
    ]
    # L37 ruling B: the leaf answers the subject again in its next attempt file; that judgment
    # governs, so it is the one row of this leaf shown. Another owner's row is still shown.
    again = json.loads(history_file())
    again["attempt"], again["rows"][0]["id"] = 2, "ROW-2ATTMP"
    again["rows"][0]["reason"] = "examined again after the closeout"
    other = json.loads(history_file())
    other["leaf"], other["rows"][0]["id"] = "260928-MIK-L98", "ROW-0THER1"
    _write(
        world.memory_worktree,
        {
            f"knowledge/history/{LEAF}-attempt-2.json": canonical_text(again),
            "knowledge/history/260928-MIK-L98.json": canonical_text(other),
        },
    )
    with mock.patch(
        "agents_remember.application.review_tree_knowledge.isolated_leaf_worklist",
        side_effect=observed_document,
    ):
        worklist = read_review_trees(world.config, _query()).worklist
    assert worklist is not None
    assert sorted((row["id"], row["path"]) for row in worklist.history_rows) == [
        ("ROW-0THER1", "knowledge/history/260928-MIK-L98.json"),
        ("ROW-2ATTMP", f"knowledge/history/{LEAF}-attempt-2.json"),
    ]


def test_an_excerpt_longer_than_its_bound_is_a_stated_prefix() -> None:
    """MIK-R31 review F3: a range longer than the excerpt bound is carried as a prefix, marked."""

    blob = "a" * 40
    long_text = "".join(f"line {number}\n" for number in range(1, 501)).encode()
    trees = mock.Mock()
    trees.objects.blob.return_value = long_text
    bounded = review_tree_entries._excerpt(trees, blob, 1, 500)
    assert bounded["excerpt_truncated"] is True
    assert bounded["excerpt"].splitlines() == [f"line {n}" for n in range(1, EXCERPT_MAX_LINES + 1)]
    assert review_tree_entries._excerpt(trees, blob, 3, 4) == {
        "excerpt": "line 3\nline 4\n",
        "excerpt_truncated": False,
    }


def test_the_placement_cache_remembers_answers_only_and_stays_within_its_bound() -> None:
    """MIK-R31 review F3: a placement answer is remembered, a failed read is asked again, and the
    table never holds more than its bound."""

    blob = "a" * 40
    trees = mock.Mock()
    locator = {"kind": "symbol", "name": "land"}
    trees.resolve.side_effect = [None, CodeReadError("gone"), None]
    fresh = BoundedMemo(3)
    with mock.patch.object(review_tree_entries, "PLACEMENTS", fresh):
        assert review_tree_entries._resolve(trees, "a.py", locator, blob, "b" * 40) is None
        assert review_tree_entries._resolve(trees, "a.py", locator, blob, "b" * 40) is None
        assert trees.resolve.call_count == 1  # the answer "does not resolve" was remembered
        with pytest.raises(CodeReadError):
            review_tree_entries._resolve(trees, "a.py", locator, blob, "c" * 40)
        assert review_tree_entries._resolve(trees, "a.py", locator, blob, "c" * 40) is None
        assert trees.resolve.call_count == 3  # a failed read was asked again
        for number in range(5):
            fresh.put(("k", str(number), "", "", ""), None)
        assert fresh.get(("k", "0", "", "", "")) is None and fresh.get(("k", "4", "", "", ""))
    assert review_tree_entries.PLACEMENTS._capacity == 8_192


def _inventory(world: World) -> Any:
    payload = compose_review(_resolve(world), world.review()).payload
    assert payload is not None
    return payload.source.inventory


def _named(tree: str | None) -> str:
    assert tree is not None
    return tree


def test_an_unchanged_path_only_a_proof_names_opens_in_a_tree_review(world: World) -> None:
    """MIK-R31 rule 5 / ruling Q1 at the route: a tree comparison admits an unchanged test file its
    K_C proof entry is anchored at, and says why; with no such proof it is refused."""

    test_file = "tests/test_land.py"
    world.code_base = commit(world.code, {test_file: "def test_land():\n    assert True\n"})
    git(world.code_worktree, "merge", "-q", "--ff-only", "main")
    world.memory_base = commit(world.memory, {}, trailer=world.code_base)
    world.contract()
    world.edit()
    tree = git(world.code, "rev-parse", f"{world.code_base}^{{tree}}")
    blob = CodeTrees.open(world.code, tree, tree).base()[test_file]
    content = CodeTrees.open(world.code, tree, tree).resolve(
        test_file, {"kind": "symbol", "name": "test_land"}, blob, blob
    )
    assert content is not None
    inventory = _inventory(world)

    def expand() -> Any:
        return read_review_source_content(
            world.config,
            ReviewSourceContentRequest(
                repository_id=REPO,
                master=MASTER,
                leaf_id=LEAF,
                path=test_file,
                before_code_tree_id=_named(inventory.before_code_tree_id),
                after_code_tree_id=_named(inventory.after_code_tree_id),
            ),
        )

    refused = expand()
    assert refused.state == "refused" and refused.refusal is not None
    _write(
        world.memory_worktree,
        {
            f"onboarding/{test_file}.md": "# t\n",
            f"onboarding/{test_file}.json": canonical_text(
                {
                    "schema": "ar-onboarding-file/v1",
                    "path": test_file,
                    "references": {},
                    "realizes": [],
                    "proves": [
                        {
                            "id": "PRF-A00009",
                            "invariant": INVARIANT,
                            "anchor": {
                                "locator": {"kind": "symbol", "name": "test_land"},
                                "blob": blob,
                                "content": content.content,
                            },
                            "facet": "Values land.",
                        }
                    ],
                }
            ),
        },
    )
    inventory = _inventory(world)
    opened = expand()
    assert opened.state == "content" and opened.expansion is not None
    assert opened.expansion.admission == "attributed_unchanged"
    assert opened.expansion.admission_detail.startswith(
        "a realization or proof recorded for the path in the comparison's after knowledge"
    )
    assert "the after snapshot records a proof here" in opened.expansion.admission_detail


@pytest.fixture(autouse=True)
def _no_bound_services() -> Iterator[None]:
    yield
    reset_worktree_services()
