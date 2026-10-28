"""A held closure proof whose objects Git freshened reads only those bodies again, never re-traverses.

A capture's ``git add`` or ``write-tree`` of content the store already holds rewrites that object's
file times, which moves its witness. The proof is witnessed again for exactly the moved objects; a
lost or rewritten object still refuses.
"""

from __future__ import annotations

import os
from pathlib import Path
from threading import Event, Thread
from unittest import mock

import pytest
from agents_remember.application import review_answer_preparation as recorded_reads
from agents_remember.kernel import read_cancellation
from agents_remember_test_support.testing.review_git_trees import CODE_FILE, World, git
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, wait_until
from test_review_git_trees import world as _world_fixture

world = _world_fixture


def _sides(world: World) -> tuple[tuple[Path, str], ...]:
    repository = world.code_worktree
    return ((repository, git(repository, "rev-parse", "HEAD^{tree}")),)


def _proved(world: World) -> tuple[tuple[Path, str], ...]:
    recorded_reads._CLOSURE_PROOFS.clear()
    recorded_reads._LATEST_PROOF.clear()
    sides = _sides(world)
    assert recorded_reads._trees_still_readable(sides)
    return sides


def _loose(repository: Path, oid: str) -> Path:
    objects = Path(git(repository, "rev-parse", "--path-format=absolute", "--git-path", "objects"))
    return objects / oid[:2] / oid[2:]


def _backdated(world: World) -> str:
    """The code file's blob, its file times set a second back so a freshening is always seen."""
    repository = world.code_worktree
    blob = git(repository, "rev-parse", f"HEAD:{CODE_FILE}")
    path = _loose(repository, blob)
    before = path.stat()
    os.utime(path, ns=(before.st_atime_ns - 10**9, before.st_mtime_ns - 10**9))
    return blob


def _freshen(world: World, blob: str) -> None:
    """Git writes the blob it already holds, as a capture's ``git add`` does: its times move."""
    repository = world.code_worktree
    before = _loose(repository, blob).stat().st_mtime_ns
    assert git(repository, "hash-object", "-w", CODE_FILE) == blob
    assert _loose(repository, blob).stat().st_mtime_ns != before, "Git did not freshen the blob"


def _traversals(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    traversed: list[int] = []
    run = recorded_reads.run_git

    def counted(repository, args, *rest, **kwargs):
        if args and args[0] == "rev-list":
            traversed.append(1)
        return run(repository, args, *rest, **kwargs)

    monkeypatch.setattr(recorded_reads, "run_git", counted)
    return traversed


def test_a_held_proof_whose_object_git_freshened_reads_that_body_and_never_traverses(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob = _backdated(world)
    sides = _proved(world)
    _freshen(world, blob)
    traversed = _traversals(monkeypatch)
    with mock.patch.object(
        recorded_reads, "_object_bodies_readable", wraps=recorded_reads._object_bodies_readable
    ) as bodies:
        assert recorded_reads._trees_still_readable(sides)
        assert recorded_reads._trees_still_readable(sides)
    assert traversed == [], "a freshened closure was traversed again"
    assert [call.args[1] for call in bodies.call_args_list] == [[blob]]


def test_six_reads_in_flight_over_a_freshened_closure_read_its_bodies_once(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob = _backdated(world)
    sides = _proved(world)
    _freshen(world, blob)
    traversed = _traversals(monkeypatch)
    gate = Event()
    consumed: list[int] = []
    validate = recorded_reads._object_bodies_readable

    def gated(repository: Path, objects: list[str]) -> bool:
        consumed.append(1)
        assert gate.wait(timeout=HANG_GUARD_SECONDS)
        return validate(repository, objects)

    monkeypatch.setattr(recorded_reads, "_object_bodies_readable", gated)
    joined: list[int] = []
    join = read_cancellation._Flight.join

    def watched(flight, asker):
        taken = join(flight, asker)
        if taken:
            joined.append(1)
        return taken

    monkeypatch.setattr(read_cancellation._Flight, "join", watched)
    answers: list[bool] = []
    readers = [
        Thread(target=lambda: answers.append(recorded_reads._trees_still_readable(sides)))
        for _ in range(6)
    ]
    readers[0].start()
    wait_until(lambda: consumed, "the first reader reading the moved bodies")
    for reader in readers[1:]:
        reader.start()
    wait_until(lambda: len(joined) == 5, "five readers waiting for the first's check")
    gate.set()
    for reader in readers:
        reader.join(timeout=HANG_GUARD_SECONDS)
    assert answers == [True] * 6
    assert consumed == [1]
    assert traversed == []


def test_an_object_lost_under_a_held_proof_is_refused(world: World) -> None:
    sides = _proved(world)
    repository = world.code_worktree
    path = _loose(repository, git(repository, "rev-parse", f"HEAD:{CODE_FILE}"))
    path.unlink()
    assert not recorded_reads._trees_still_readable(sides)


def test_an_object_rewritten_in_place_under_a_held_proof_is_refused(world: World) -> None:
    sides = _proved(world)
    repository = world.code_worktree
    path = _loose(repository, git(repository, "rev-parse", f"HEAD:{CODE_FILE}"))
    before = path.stat()
    data = path.read_bytes()
    path.chmod(0o644)
    path.write_bytes(b"?" * len(data))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert not recorded_reads._trees_still_readable(sides)
