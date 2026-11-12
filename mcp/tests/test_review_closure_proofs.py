"""A closure proven by one process is taken by the next only while every storage witness agrees."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from threading import Event, Thread
from unittest import mock

import pytest
from agents_remember.application import review_answer_preparation as recorded_reads
from agents_remember.application.review_closure_proofs import ClosureProofStore, ProvenClosure
from agents_remember.kernel import read_cancellation
from agents_remember.serving.changeset_generation import repository_layout
from agents_remember_test_support.testing.review_git_trees import (
    CODE_FILE,
    World,
    _write,
    git,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, wait_until
from test_review_git_trees import world as _world_fixture

world = _world_fixture


def _sides(world: World) -> tuple[tuple[Path, str], ...]:
    repository = world.code_worktree
    return ((repository, git(repository, "rev-parse", "HEAD^{tree}")),)


def _restarted() -> None:
    """A new process holds no proof of its own."""
    recorded_reads._CLOSURE_PROOFS.clear()
    recorded_reads._LATEST_PROOF.clear()


def _bodies():
    return mock.patch.object(
        recorded_reads, "_object_bodies_readable", wraps=recorded_reads._object_bodies_readable
    )


def test_a_restarted_process_takes_the_stored_proof_without_consuming_the_bodies_again(
    world: World, tmp_path: Path
) -> None:
    proofs = ClosureProofStore(tmp_path / "closures", "source-digest")
    sides = _sides(world)
    _restarted()
    with _bodies() as bodies:
        assert recorded_reads._trees_still_readable(sides, proofs)
        _restarted()
        assert recorded_reads._trees_still_readable(sides, proofs)
        assert bodies.call_count == 1


def test_another_build_namespace_proves_again(world: World, tmp_path: Path) -> None:
    sides = _sides(world)
    _restarted()
    with _bodies() as bodies:
        assert recorded_reads._trees_still_readable(sides, ClosureProofStore(tmp_path, "one"))
        _restarted()
        assert recorded_reads._trees_still_readable(sides, ClosureProofStore(tmp_path, "two"))
        assert bodies.call_count == 2


@pytest.mark.parametrize("damage", ["delete", "corrupt"])
def test_a_stored_proof_never_hides_a_missing_or_corrupt_descendant(
    world: World, tmp_path: Path, damage: str
) -> None:
    proofs = ClosureProofStore(tmp_path / "closures", "source-digest")
    repository = world.code_worktree
    sides = _sides(world)
    _restarted()
    assert recorded_reads._trees_still_readable(sides, proofs)
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
    _restarted()
    assert not recorded_reads._trees_still_readable(sides, proofs)


def test_a_new_replace_reference_proves_again(world: World, tmp_path: Path) -> None:
    proofs = ClosureProofStore(tmp_path / "closures", "source-digest")
    repository = world.code_worktree
    sides = _sides(world)
    _restarted()
    with _bodies() as bodies:
        assert recorded_reads._trees_still_readable(sides, proofs)
        blob = git(repository, "rev-parse", f"HEAD:{CODE_FILE}")
        _write(repository, {"replacement.txt": "replacement body\n"})
        replacement = git(repository, "hash-object", "-w", "replacement.txt")
        git(repository, "replace", blob, replacement)
        _restarted()
        assert recorded_reads._trees_still_readable(sides, proofs)
        assert bodies.call_count == 2


def test_an_unreadable_stored_proof_is_absent(world: World, tmp_path: Path) -> None:
    directory = tmp_path / "closures"
    proofs = ClosureProofStore(directory, "source-digest")
    sides = _sides(world)
    _restarted()
    with _bodies() as bodies:
        assert recorded_reads._trees_still_readable(sides, proofs)
        for stored in directory.glob("*.closure.json"):
            stored.write_text("{not json")
        _restarted()
        assert recorded_reads._trees_still_readable(sides, proofs)
        assert bodies.call_count == 2


def test_the_store_keeps_a_bounded_number_of_proofs(world: World, tmp_path: Path) -> None:
    directory = tmp_path / "closures"
    proofs = ClosureProofStore(directory, "source-digest", capacity=2)
    repository = world.code_worktree
    for index in range(4):
        trees = (f"{index:040x}",)
        proofs.save(
            repository,
            trees,
            ProvenClosure(("a" * 40,), ("witness",), "references", repository_layout(repository)),
        )
    assert len(list(directory.glob("*.closure.json"))) == 2


def _closure(repository: Path, tree: str) -> tuple[str, ...]:
    listed = git(repository, "rev-list", "--objects", "--no-object-names", f"{tree}^{{tree}}")
    return tuple(sorted(listed.splitlines()))


def _later_tree(repository: Path) -> str:
    """A root beside HEAD's that adds one blob, written without rewriting any shared object."""
    _write(repository, {"other.txt": "a later file\n"})
    blob = git(repository, "hash-object", "-w", "other.txt")
    listing = git(repository, "ls-tree", "HEAD^{tree}") + f"\n100644 blob {blob}\tother.txt\n"
    made = subprocess.run(
        ["git", "mktree"], cwd=repository, input=listing, capture_output=True, text=True, check=True
    )
    return made.stdout.strip()


def test_a_later_closure_consumes_only_the_bodies_no_agreeing_proof_holds(world: World) -> None:
    repository = world.code_worktree
    _restarted()
    earlier = _sides(world)
    assert recorded_reads._trees_still_readable(earlier)
    later = _later_tree(repository)
    added = set(_closure(repository, later)) - set(_closure(repository, earlier[0][1]))
    assert added and len(added) < len(_closure(repository, later))
    with _bodies() as bodies:
        assert recorded_reads._trees_still_readable(((repository, later),))
        assert bodies.call_count == 1
        assert set(bodies.call_args.args[1]) == added


def test_a_shared_object_rewritten_in_place_is_consumed_again(world: World) -> None:
    repository = world.code_worktree
    _restarted()
    assert recorded_reads._trees_still_readable(_sides(world))
    later = _later_tree(repository)
    blob = git(repository, "rev-parse", f"HEAD:{CODE_FILE}")
    objects = Path(git(repository, "rev-parse", "--path-format=absolute", "--git-path", "objects"))
    path = objects / blob[:2] / blob[2:]
    before = path.stat()
    data = path.read_bytes()
    path.chmod(0o644)
    path.write_bytes(b"?" * len(data))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert not recorded_reads._trees_still_readable(((repository, later),))


def _watch_joiners(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Counts the askers that join a check already in flight."""
    joined: list[int] = []
    join = read_cancellation._Flight.join

    def watched(flight, asker):
        taken = join(flight, asker)
        if taken:
            joined.append(1)
        return taken

    monkeypatch.setattr(read_cancellation._Flight, "join", watched)
    return joined


def test_one_closure_asked_twice_at_once_is_traversed_and_consumed_once(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    sides = _sides(world)
    _restarted()
    gate = Event()
    consumed: list[int] = []
    validate = recorded_reads._object_bodies_readable

    def gated(repository: Path, objects: list[str]) -> bool:
        consumed.append(1)
        assert gate.wait(timeout=HANG_GUARD_SECONDS)
        return validate(repository, objects)

    monkeypatch.setattr(recorded_reads, "_object_bodies_readable", gated)
    joined = _watch_joiners(monkeypatch)
    answers: list[bool] = []
    first = Thread(target=lambda: answers.append(recorded_reads._trees_still_readable(sides)))
    first.start()
    wait_until(lambda: consumed, "the first asker consuming the closure")
    second = Thread(target=lambda: answers.append(recorded_reads._trees_still_readable(sides)))
    second.start()
    wait_until(lambda: joined, "the second asker waiting for the first's check")
    gate.set()
    first.join(timeout=HANG_GUARD_SECONDS)
    second.join(timeout=HANG_GUARD_SECONDS)

    assert answers == [True, True]
    assert consumed == [1]
