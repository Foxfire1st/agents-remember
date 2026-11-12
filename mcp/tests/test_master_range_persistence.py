"""Master-only immutable pair persistence, through native Git and fresh cache owners.

Scratch draft: the source owner's proposed API has not been applied or executed.
Generic task/filediff restart behavior belongs to test_changeset_cache and is unchanged.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from agents_remember.serving import changeset_reads
from agents_remember.serving.changeset import master_changeset
from agents_remember.serving.changeset_cache import ChangesetCache
from agents_remember.serving.changeset_ranges import MasterRangeAnswers
from agents_remember.worktrees.worktree_contract import load_contract, write_contract
from test_master_net_generation import (
    KEEP_PATH,
    LEAF_ONE,
    LEAF_TWO,
    MASTER,
    MEMORY_PATH,
    REPO,
    MasterFixture,
    _commit,
    _git,
    build_master_fixture,
)

pytestmark = pytest.mark.evidence_unit


def _owner(directory: Path, namespace: str = "f8-test") -> ChangesetCache:
    return ChangesetCache(master_ranges=MasterRangeAnswers(directory, namespace))


def _view(cache: ChangesetCache, world: MasterFixture, *, leaves: bool) -> dict[str, Any]:
    with cache.reading():
        return master_changeset(world.config, REPO, MASTER, include_leaves=leaves)


def _wire(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.fixture
def measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    world = build_master_fixture(tmp_path / "world")
    calls: list[tuple[Path, str, str | None]] = []
    original = changeset_reads.git_changes.changed_files_with_counts

    def native(root: Path, base: str, head: str | None = None, **kwargs: Any):
        calls.append((root, base, head))
        return original(root, base, head, **kwargs)

    monkeypatch.setattr(changeset_reads.git_changes, "changed_files_with_counts", native)
    return world, tmp_path / "ranges", calls


@pytest.mark.parametrize("leaves", [False, True])
def test_master_pairs_survive_fresh_store_and_cache_owners(measured, leaves: bool) -> None:
    world, directory, calls = measured
    first = _view(_owner(directory), world, leaves=leaves)
    assert len(calls) == (4 if leaves else 2)
    calls.clear()
    restored = _view(_owner(directory), world, leaves=leaves)
    assert _wire(restored) == _wire(first)
    assert calls == []


@pytest.mark.parametrize("side", ["code", "memory"])
def test_new_tip_misses_only_its_immutable_pair(measured, side: str) -> None:
    world, directory, calls = measured
    first = _view(_owner(directory), world, leaves=False)
    repo = world.code_repo if side == "code" else world.memory_repo
    path = KEEP_PATH if side == "code" else MEMORY_PATH
    _git(repo, ["checkout", "-q", "series"])
    with (repo / path).open("a", encoding="utf-8") as stream:
        stream.write("\n# new endpoint\n")
    tip = _commit(repo, "advance one master endpoint")
    calls.clear()
    current = _view(_owner(directory), world, leaves=False)
    assert current["generation"][f"{side}Tip"] == tip
    assert current["generation"]["digest"] != first["generation"]["digest"]
    assert calls == [(repo, world.base_code if side == "code" else world.base_memory, tip)]
    other = "memory" if side == "code" else "code"
    assert current[other] == first[other]


def test_include_leaves_adds_pairs_but_never_persists_membership(measured) -> None:
    world, directory, calls = measured
    net = _view(_owner(directory), world, leaves=False)
    assert len(calls) == 2 and net["leaves"] == []
    calls.clear()
    full = _view(_owner(directory), world, leaves=True)
    assert len(calls) == 2
    assert full["code"] == net["code"] and full["memory"] == net["memory"]
    assert len(full["leaves"]) == 2
    calls.clear()
    assert _wire(_view(_owner(directory), world, leaves=True)) == _wire(full)
    assert calls == []
    path = (
        world.config.coordination_root
        / "tasks"
        / REPO
        / MASTER
        / "enclosures"
        / LEAF_TWO
        / "series-contract.md"
    )
    path.unlink()
    changed = _view(_owner(directory), world, leaves=True)
    assert len(changed["leaves"]) == 1
    assert changed["leaves"][0]["leafId"] != LEAF_TWO
    assert changed["code"] == full["code"] and changed["memory"] == full["memory"]
    assert calls == []


def test_native_rename_binary_and_complete_rows_round_trip(measured) -> None:
    world, directory, calls = measured
    renamed = "src/renamed\tkeep.py"
    (world.code_repo / KEEP_PATH).rename(world.code_repo / renamed)
    (world.code_repo / "binary.bin").write_bytes(b"\0binary\xff\0")
    for index in range(129):
        (world.code_repo / f"row-{index:03}.txt").write_text(f"{index}\n", encoding="utf-8")
    _commit(world.code_repo, "rename, binary, and many native rows")
    first = _view(_owner(directory), world, leaves=False)
    rows = {row["path"]: row for row in first["code"]}
    assert len(rows) == 131
    assert rows[renamed]["status"] == "R"
    assert rows["binary.bin"]["insertions"] is None
    assert rows["binary.bin"]["deletions"] is None
    assert "row-000.txt" in rows and "row-128.txt" in rows
    calls.clear()
    assert _wire(_view(_owner(directory), world, leaves=False)) == _wire(first)
    assert calls == []


def test_live_leaf_attribution_is_never_restored_as_a_fixed_pair(measured) -> None:
    world, directory, calls = measured
    path = (
        world.config.coordination_root
        / "tasks"
        / REPO
        / MASTER
        / "enclosures"
        / LEAF_ONE
        / "series-contract.md"
    )
    write_contract(path, replace(load_contract(path), code_worktree=world.code_repo))
    first = _view(_owner(directory), world, leaves=True)
    calls.clear()
    with (world.code_repo / KEEP_PATH).open("a", encoding="utf-8") as stream:
        stream.write("\n# still uncommitted\n")
    current = _view(_owner(directory), world, leaves=True)
    first_leaf = next(row for row in first["leaves"] if row["leafId"] == LEAF_ONE)
    current_leaf = next(row for row in current["leaves"] if row["leafId"] == LEAF_ONE)
    assert current_leaf["state"] == "working"
    assert current_leaf["counters"] != first_leaf["counters"]
    assert current["code"] == first["code"]
    assert calls == [(world.code_repo, world.base_code, None)]


def test_generic_pair_reads_do_not_acquire_master_persistence(measured) -> None:
    world, directory, calls = measured
    results = []
    for _ in range(2):
        with _owner(directory).reading():
            results.append(
                changeset_reads.changed_files_with_counts(
                    world.code_repo, world.base_code, world.tip_one
                )
            )
    assert _wire(results[0]) == _wire(results[1])
    assert calls == [(world.code_repo, world.base_code, world.tip_one)] * 2
    assert not list(directory.rglob("*.json"))


def test_truncated_saved_answer_is_recomputed_not_reported_empty(measured) -> None:
    world, directory, calls = measured
    first = _view(_owner(directory), world, leaves=False)
    saved = list(directory.rglob("*.json"))
    assert saved
    for path in saved:
        path.write_bytes(b'{"truncated":')
    calls.clear()
    assert _wire(_view(_owner(directory), world, leaves=False)) == _wire(first)
    assert len(calls) == 2


def test_new_namespace_cannot_restore_old_pair_answers(measured) -> None:
    world, directory, calls = measured
    first = _view(_owner(directory, "first-build"), world, leaves=False)
    calls.clear()
    assert _wire(_view(_owner(directory, "second-build"), world, leaves=False)) == _wire(first)
    assert len(calls) == 2


def test_cold_absent_pair_is_refused_twice_without_publishing_zero(measured) -> None:
    world, directory, calls = measured
    for _ in range(2):
        with pytest.raises(RuntimeError), _owner(directory).reading():
            changeset_reads.master_changed_files_with_counts(
                world.code_repo, world.base_code, "f" * 40
            )
    assert len(calls) == 2
    assert not list(directory.rglob("*.json"))


def test_abandoned_read_does_not_publish_master_pair(measured) -> None:
    world, directory, calls = measured
    cancelled = Event()
    with _owner(directory).reading(abandoned=cancelled):
        first = changeset_reads.master_changed_files_with_counts(
            world.code_repo, world.base_code, world.tip_one
        )
        cancelled.set()
    calls.clear()
    with _owner(directory).reading():
        restored = changeset_reads.master_changed_files_with_counts(
            world.code_repo, world.base_code, world.tip_one
        )
    assert _wire(restored) == _wire(first)
    assert calls == [(world.code_repo, world.base_code, world.tip_one)]
