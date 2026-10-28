"""Git's object census runs only when the store it counts moved (D569).

``count_objects`` answers what ``git count-objects -v`` said while a stat witness of the store
stays the same: the loose fan-out directories, the pack directory's entries, the alternates
lists and the files Git reads to find and admit the store. A new loose object, a new pack and a
new alternate each move the witness, and the answer then equals a fresh census.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agents_remember.kernel import git_command
from agents_remember.serving.changeset_cache import ChangesetCache, RepositoryRead
from agents_remember.serving.changeset_generation import count_objects
from test_worktree_support import git, init_repo

pytestmark = pytest.mark.evidence_unit


@pytest.fixture
def censuses(monkeypatch) -> list[Path]:
    """The roots of every ``git count-objects`` child started during the test."""

    roots: list[Path] = []
    original = git_command._run_git

    def measured(*args, **kwargs):
        if "count-objects" in args[1]:
            roots.append(Path(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(git_command, "_run_git", measured)
    return roots


def _fresh(root: Path) -> tuple[int, int, tuple[str, ...]]:
    """A census taken now, outside the measured runner."""

    fields: dict[str, str] = {}
    alternates: list[str] = []
    for line in git(root, "count-objects", "-v").splitlines():
        key, _, value = line.partition(": ")
        if key == "alternate":
            alternates.append(value)
        else:
            fields[key] = value
    return int(fields["count"]), int(fields["in-pack"]), tuple(sorted(alternates))


def test_two_identical_requests_run_one_census(tmp_path, censuses) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    cache = ChangesetCache()
    stamps = []
    for _request in range(2):
        with cache.reading():
            stamps.append(cache.snapshot(RepositoryRead(repo)))

    assert stamps[0] is not None
    assert stamps[0] == stamps[1]
    assert censuses == [repo]


def test_an_unchanged_store_answers_without_a_census(tmp_path, censuses) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)

    first = count_objects(repo)
    second = count_objects(repo)

    assert first == second == _fresh(repo)
    assert censuses == [repo]


def test_a_new_loose_object_is_counted(tmp_path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    before = count_objects(repo)
    blob = repo / "new.txt"
    blob.write_text("a new loose object\n", encoding="utf-8")
    git(repo, "hash-object", "-w", str(blob))

    after = count_objects(repo)

    assert after == _fresh(repo)
    assert after[0] == before[0] + 1


def test_a_new_pack_is_counted(tmp_path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    before = count_objects(repo)
    assert before[1] == 0
    git(repo, "repack", "-q")

    after = count_objects(repo)

    assert after == _fresh(repo)
    assert after[1] > 0


def test_a_linked_worktree_sees_its_common_store_move(tmp_path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", "-b", "side", str(linked))
    before = count_objects(linked)
    git(repo, "commit", "-q", "--allow-empty", "-m", "a new commit object")

    after = count_objects(linked)

    assert after == _fresh(linked)
    assert after[0] == before[0] + 1


def test_a_new_alternate_is_listed(tmp_path) -> None:
    repo = tmp_path / "repo"
    other = tmp_path / "other"
    init_repo(repo)
    init_repo(other)
    assert count_objects(repo)[2] == ()
    (repo / ".git" / "objects" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "objects" / "info" / "alternates").write_text(
        f"{other / '.git' / 'objects'}\n", encoding="utf-8"
    )

    after = count_objects(repo)

    assert after == _fresh(repo)
    assert len(after[2]) == 1
