"""Exact-input reuse retains native Git integrity and candidate-parse boundaries."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Hashable
from dataclasses import dataclass, replace
from pathlib import Path, PurePath
from sys import getsizeof
from unittest import mock

import pytest
from agents_remember.application import review_answer_preparation as recorded_reads
from agents_remember.application import review_leaf_view_memo, review_tree_knowledge
from agents_remember.application.knowledge_gate import gate as gate_module
from agents_remember.kernel.memory_graph import retained_graph_bytes
from agents_remember.kernel.recorded_reads import file_identity, record_read, replay_reads
from agents_remember.kernel.recorded_reads import recorded_reads as observe_reads
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo
from agents_remember.models.knowledge.review_trees import ReviewWorklistView
from agents_remember.models.knowledge_files import canonical_text
from agents_remember_test_support.testing.review_git_trees import (
    CODE_FILE,
    World,
    _write,
    commit,
    git,
)
from test_knowledge_closeout_gate import Gated
from test_knowledge_gate_routes import world as _gate_fixture
from test_review_git_trees import world as _world_fixture
from test_review_read_latency import _live

gate_world = _gate_fixture
world = _world_fixture


def test_recorded_closure_reuses_body_validation_only_for_unchanged_storage(world: World) -> None:
    repository = world.code_worktree
    tree = git(repository, "rev-parse", "HEAD^{tree}")
    sides = ((repository, tree),)
    recorded_reads._CLOSURE_PROOFS.clear()
    recorded_reads._LATEST_PROOF.clear()
    with mock.patch.object(
        recorded_reads, "_object_bodies_readable", wraps=recorded_reads._object_bodies_readable
    ) as bodies:
        assert recorded_reads._trees_still_readable(sides)
        assert recorded_reads._trees_still_readable(sides)
        assert bodies.call_count == 1
        other = commit(repository, {"other.txt": "another tree\n"})
        assert recorded_reads._trees_still_readable(
            ((repository, git(repository, "rev-parse", f"{other}^{{tree}}")),)
        )
        assert bodies.call_count == 2


@pytest.mark.parametrize("damage", ["delete", "corrupt"])
def test_recorded_closure_keeps_missing_and_corrupt_descendant_detection(
    world: World,
    damage: str,
) -> None:
    repository = world.code_worktree
    tree = git(repository, "rev-parse", "HEAD^{tree}")
    sides = ((repository, tree),)
    assert recorded_reads._trees_still_readable(sides)
    blob = git(repository, "rev-parse", f"HEAD:{CODE_FILE}")
    objects = Path(git(repository, "rev-parse", "--path-format=absolute", "--git-path", "objects"))
    path = objects / blob[:2] / blob[2:]
    if damage == "delete":
        path.unlink()
        _write(repository, {"unrelated.txt": "compensating unrelated object\n"})
        git(repository, "hash-object", "-w", "unrelated.txt")
    else:
        before = path.stat()
        data = path.read_bytes()
        path.chmod(0o644)
        path.write_bytes(b"?" * len(data))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert git(repository, "cat-file", "-t", tree) == "tree"  # root survives both faults
    assert not recorded_reads._trees_still_readable(sides)


def test_recorded_closure_rechecks_repacked_objects_and_detects_pack_corruption(
    world: World,
) -> None:
    repository = world.code_worktree
    tree = git(repository, "rev-parse", "HEAD^{tree}")
    sides = ((repository, tree),)
    assert recorded_reads._trees_still_readable(sides)
    git(repository, "gc", "--prune=now")
    with mock.patch.object(
        recorded_reads, "_object_bodies_readable", wraps=recorded_reads._object_bodies_readable
    ) as bodies:
        assert recorded_reads._trees_still_readable(sides)
        assert recorded_reads._trees_still_readable(sides)
        assert bodies.call_count == 1
    objects = Path(git(repository, "rev-parse", "--path-format=absolute", "--git-path", "objects"))
    pack = next((objects / "pack").glob("*.pack"))
    before = pack.stat()
    pack.chmod(0o644)
    pack.write_bytes(b"?" * before.st_size)
    os.utime(pack, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert not recorded_reads._trees_still_readable(sides)


def test_recorded_closure_rechecks_replacement_references(world: World) -> None:
    repository = world.code_worktree
    tree = git(repository, "rev-parse", "HEAD^{tree}")
    sides = ((repository, tree),)
    assert recorded_reads._trees_still_readable(sides)
    other = commit(repository, {"replacement.txt": "replacement content\n"})
    replacement = git(repository, "rev-parse", f"{other}^{{tree}}")
    git(repository, "replace", tree, replacement)
    with mock.patch.object(
        recorded_reads, "_object_bodies_readable", wraps=recorded_reads._object_bodies_readable
    ) as bodies:
        assert recorded_reads._trees_still_readable(sides)
        assert bodies.call_count == 1
    blob = git(repository, "rev-parse", f"{other}:replacement.txt")
    objects = Path(git(repository, "rev-parse", "--path-format=absolute", "--git-path", "objects"))
    (objects / blob[:2] / blob[2:]).unlink()
    assert not recorded_reads._trees_still_readable(sides)


def test_recorded_closure_does_not_retain_a_layout_that_changed_during_validation(
    world: World,
) -> None:
    repository = world.code_worktree
    tree = git(repository, "rev-parse", "HEAD^{tree}")
    sides = ((repository, tree),)
    recorded_reads._CLOSURE_PROOFS.clear()
    recorded_reads._LATEST_PROOF.clear()
    config = Path(git(repository, "rev-parse", "--path-format=absolute", "--git-path", "config"))
    validate = recorded_reads._object_bodies_readable

    def mutate_after_validation(root: Path, objects: list[str]) -> bool:
        result = validate(root, objects)
        config.write_text(config.read_text() + "\n# changed during validation\n")
        return result

    with mock.patch.object(
        recorded_reads, "_object_bodies_readable", side_effect=mutate_after_validation
    ) as bodies:
        assert recorded_reads._trees_still_readable(sides)
        assert recorded_reads._trees_still_readable(sides)
        assert bodies.call_count == 2


def test_candidate_parse_reuse_is_exact_isolated_and_rechecks_git(gate_world: Gated) -> None:
    candidate = gate_world.candidate()
    trees = gate_module.GateTrees(
        gate_world.code,
        candidate.code,
        gate_world.memory,
        candidate.memory,
        (gate_world.memory_base,),
        gate_world.code_base,
    )
    gate_module._CANDIDATE_PARSES.clear()
    with mock.patch.object(
        gate_module.KnowledgeSide, "from_snapshot", wraps=gate_module.KnowledgeSide.from_snapshot
    ) as parse:
        first = gate_module._candidate(trees, None)
        assert not isinstance(first, gate_module.GateFinding)
        recorded = set(first.candidate.parsed.records)
        assert recorded
        first.candidate.parsed.records.clear()
        second = gate_module._candidate(trees, None)
        assert not isinstance(second, gate_module.GateFinding)
        assert set(second.candidate.parsed.records) == recorded and parse.call_count == 1

        memory_file = next((gate_world.memory / "knowledge/invariants").glob("*.json"))
        contents = json.loads(memory_file.read_text())
        contents["statement"] += " The candidate changed."
        memory_file.write_text(canonical_text(contents))
        other = gate_world.candidate()
        assert other.memory != candidate.memory
        changed = gate_module._candidate(replace(trees, memory_tree=other.memory), None)
        assert not isinstance(changed, gate_module.GateFinding) and parse.call_count == 2

        relative = memory_file.relative_to(gate_world.memory).as_posix()
        blob = git(gate_world.memory, "rev-parse", f"{candidate.memory}:{relative}")
        objects = Path(
            git(gate_world.memory, "rev-parse", "--path-format=absolute", "--git-path", "objects")
        )
        (objects / blob[:2] / blob[2:]).unlink()
        lost = gate_module._candidate(trees, None)
        assert isinstance(lost, gate_module.GateFinding)
        assert lost.code == gate_module.RUN_INCOMPLETE


def test_candidate_parse_weight_evicts_a_small_side_and_skips_an_oversized_side(
    gate_world: Gated, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial = gate_world.candidate()
    trees = gate_module.GateTrees(
        gate_world.code,
        initial.code,
        gate_world.memory,
        initial.memory,
        (gate_world.memory_base,),
        gate_world.code_base,
    )
    memo = gate_module._CANDIDATE_PARSES
    memo.clear()
    monkeypatch.setattr(memo, "_capacity", 128 * 1024)
    memory_file = next((gate_world.memory / "knowledge/invariants").glob("*.json"))
    contents = json.loads(memory_file.read_text())

    companion = memory_file.with_suffix(".md")

    def tree_with_prose_bytes(size: int) -> gate_module.GateTrees:
        contents["statement"] = f"A candidate with {size} bytes of companion prose."
        memory_file.write_text(canonical_text(contents))
        companion.write_text("x" * size)
        return replace(trees, memory_tree=gate_world.candidate().memory)

    small = tree_with_prose_bytes(52 * 1024)
    large = tree_with_prose_bytes(83 * 1024)
    oversized = tree_with_prose_bytes(128 * 1024)
    try:
        with mock.patch.object(
            gate_module.KnowledgeSide,
            "from_snapshot",
            wraps=gate_module.KnowledgeSide.from_snapshot,
        ) as parse:
            assert not isinstance(gate_module._candidate(small, None), gate_module.GateFinding)
            small_weight = memo.weight
            assert 52 * 1024 < small_weight < memo.capacity
            assert not isinstance(gate_module._candidate(large, None), gate_module.GateFinding)
            large_weight = memo.weight
            assert 83 * 1024 < large_weight < memo.capacity
            assert len(memo) == 1 and small_weight < large_weight
            assert not isinstance(gate_module._candidate(large, None), gate_module.GateFinding)
            assert parse.call_count == 2

            assert not isinstance(gate_module._candidate(oversized, None), gate_module.GateFinding)
            assert memo.weight == large_weight
            assert not isinstance(gate_module._candidate(oversized, None), gate_module.GateFinding)
            assert parse.call_count == 4 and memo.weight == large_weight

            assert not isinstance(gate_module._candidate(small, None), gate_module.GateFinding)
            assert parse.call_count == 5
    finally:
        memo.clear()


@dataclass(slots=True)
class _Nested:
    paths: dict[PurePath, bytes]
    related: list[object]


def test_graph_bytes_count_paths_payloads_and_shared_objects_once() -> None:
    payload = b"x" * 2048
    path = PurePath("onboarding", "source.py.json")
    related: list[object] = []
    graph = _Nested(paths={path: payload}, related=related)
    related.extend((graph, payload, path))
    ceiling = 16 * 1024

    size = retained_graph_bytes(graph, ceiling=ceiling)
    assert size >= sum(map(getsizeof, (graph, graph.paths, related, path, payload)))
    assert size < 2 * getsizeof(payload)
    graph.paths[path] = b"y" * 8192
    assert retained_graph_bytes(graph, ceiling=ceiling) > size + 8192 - 2048
    assert retained_graph_bytes(graph, ceiling=1024) > 1024


def _assert_bounded_answers[K: Hashable, V](
    memo: BoundedMemo[K, V], ask: Callable[[int, int], int], witness: Path
) -> None:
    mebibyte = 1024 * 1024
    size = 3 * mebibyte
    witness.write_text("observed input")
    memo.clear()
    try:
        assert ask(1, size) == ask(2, size) == 1
        assert ask(1, size) == 0
        assert ask(3, size) == 1
        assert len(memo) == 2
        assert ask(1, size) == ask(3, size) == 0
        weight = memo.weight
        assert 2 * size < weight <= memo.capacity == 8 * mebibyte

        oversized = memo.capacity + 1
        assert ask(4, oversized) == ask(4, oversized) == 1
        assert len(memo) == 2 and memo.weight == weight
        assert ask(2, size) == 1  # the third entry evicted the older answer

        witness.write_text("changed input")
        assert ask(3, size) == 1
    finally:
        memo.clear()


def test_gate_reuses_exact_small_verdicts_but_recomputes_oversized_verdicts(
    gate_world: Gated, tmp_path: Path
) -> None:
    base = gate_world.candidate()
    witness = tmp_path / "gate-input.txt"

    def ask(number: int, size: int) -> int:
        def evaluate(*_args: object, **_kwargs: object) -> gate_module.GateResult:
            record_read(witness)
            return gate_module.GateResult(
                owner=None,
                worklist={"state": "complete", "items": [{"detail": str(number) * size}]},
                worklist_path=None,
                findings=(),
            )

        candidate = replace(base, code=f"{number:040x}")
        with (
            mock.patch.object(gate_module, "_evaluate", side_effect=evaluate) as compute,
            observe_reads() as reads,
        ):
            result = gate_module.evaluate_leaf_gate(
                gate_world.contract,
                candidate,
                parent_memory_tip=gate_world.memory_base,
                persist_worklist=False,
            )
        assert result is not None and result.ok
        detail = result.worklist["items"][0]["detail"]
        assert len(detail) == size and detail[0] == str(number)
        assert reads[witness.as_posix()] == file_identity(witness)
        return compute.call_count

    _assert_bounded_answers(gate_module.memo.GATE_MEMO, ask, witness)


def test_leaf_view_reuses_exact_small_parts_but_recomputes_oversized_parts(
    world: World, tmp_path: Path
) -> None:
    world.edit()
    contract, trees = _live(world)
    base = review_leaf_view_memo.leaf_view_key(contract, trees)
    assert base is not None
    witness = tmp_path / "view-input.txt"

    def ask(number: int, size: int) -> int:
        def compute_parts(
            *_args: object, **_kwargs: object
        ) -> tuple[review_leaf_view_memo.LeafViewParts, bool, dict[str, str]]:
            reads = {witness.as_posix(): file_identity(witness)}
            replay_reads(reads)
            worklist = ReviewWorklistView(
                source="computed", state="complete", items=({"detail": str(number) * size},)
            )
            return review_leaf_view_memo.LeafViewParts(worklist), True, reads

        key = replace(base, memory_before=f"{number:040x}")
        with (
            mock.patch.object(review_tree_knowledge, "leaf_view_key", return_value=key),
            mock.patch.object(
                review_tree_knowledge, "_compute_leaf_parts", side_effect=compute_parts
            ) as compute,
            observe_reads() as reads,
        ):
            parts = review_tree_knowledge._leaf_wide_parts(contract, trees)
        assert isinstance(parts, review_leaf_view_memo.LeafViewParts)
        detail = parts.worklist.items[0]["detail"]
        assert len(detail) == size and detail[0] == str(number)
        assert reads[witness.as_posix()] == file_identity(witness)
        return compute.call_count

    _assert_bounded_answers(review_leaf_view_memo.LEAF_VIEW_MEMO, ask, witness)
