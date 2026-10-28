"""Exact-input reuse retains native Git integrity and candidate-parse boundaries."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application import review_answer_preparation as recorded_reads
from agents_remember.application.knowledge_gate import gate as gate_module
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
