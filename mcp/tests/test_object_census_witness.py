"""Git's object census runs only when the store it counts moved (D569).

``count_objects`` answers what ``git count-objects -v`` said while a stat witness of the store
stays the same: the loose fan-out directories, the pack directory's entries, the alternates
lists and the files Git reads to find and admit the store. A new loose object, a new pack and a
new alternate each move the witness, and the answer then equals a fresh census.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from pathlib import Path

import pytest
from agents_remember.kernel import git_command
from agents_remember.kernel.git_command import GitRunnerOptions
from agents_remember.kernel.request_memo import request_memo_scope
from agents_remember.kernel.witnessed_memo import WitnessedMemo
from agents_remember.serving import changeset_cache, changeset_generation
from agents_remember.serving.changeset_cache import ChangesetCache, RepositoryRead
from agents_remember.serving.changeset_generation import count_objects, repository_layout
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


def _pack_objects(repo: Path, count: int) -> None:
    prefix = repo / ".git" / "objects" / "pack" / "pack"
    for index in range(count):
        blob = git_command.run_git(
            repo, ["hash-object", "-w", "--stdin"], GitRunnerOptions(input_text=f"pack {index}\n")
        )
        assert blob.returncode == 0, blob.stderr
        packed = git_command.run_git(
            repo, ["pack-objects", str(prefix)], GitRunnerOptions(input_text=blob.stdout)
        )
        assert packed.returncode == 0, packed.stderr


def _connect_alternates(repo: Path, count: int) -> None:
    stores = tuple(repo.parent / f"alternate-{index:02d}.git" for index in range(count))
    for store in stores:
        git(repo, "init", "--bare", str(store))
    (repo / ".git/objects/info/alternates").write_text(f"{stores[0] / 'objects'}\n")
    (stores[0] / "objects/info/alternates").write_text(
        "".join(f"{store / 'objects'}\n" for store in stores[1:])
    )


@pytest.mark.parametrize("store_count", (8, 64))
def test_native_pack_and_alternate_witnesses_leave_with_the_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, censuses: list[Path], store_count: int
) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    _pack_objects(repo, store_count)
    _connect_alternates(repo, store_count)
    fresh = _fresh(repo)
    assert fresh[1] == store_count and len(fresh[2]) == store_count
    dashboard: WitnessedMemo[Path, tuple[object, ...], tuple[int, int, tuple[str, ...]]] = (
        WitnessedMemo(1024)
    )
    monkeypatch.setattr(changeset_generation, "_censuses", dashboard)

    for expected in (1, 2):
        with request_memo_scope():
            layout = repository_layout(repo)
            assert set(map(str, layout.object_directories[1:])) == set(fresh[2])
            assert count_objects(repo) == fresh
        assert len(dashboard) == 0
        assert censuses == [repo] * expected

    assert count_objects(repo) == count_objects(repo) == fresh
    assert len(dashboard) == 1
    assert censuses == [repo] * 3


def _expected_revision(repo: Path, prior: str = "") -> str:
    return hashlib.sha256(repr((str(repo), *_fresh(repo), prior)).encode()).hexdigest()


def test_native_generation_keeps_a_fixed_size_alternate_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    _connect_alternates(repo, 64)
    monkeypatch.setattr(changeset_cache, "_OBJECT_HISTORY", OrderedDict())
    first, other = ChangesetCache(), ChangesetCache()
    with request_memo_scope():
        assert first.open_snapshot(RepositoryRead(repo)) is not None
    initial = _expected_revision(repo)
    assert other._object_revision(repo, tolerate_additions=True) == initial
    assert other._object_revision(repo) == initial

    added = repo / "extra.txt"
    added.write_text("an additional loose object\n", encoding="utf-8")
    git(repo, "hash-object", "-w", str(added))
    strict = _expected_revision(repo, initial)
    assert first._object_revision(repo, tolerate_additions=True) == initial
    assert other._object_revision(repo) == strict and strict != initial

    git(repo, "repack", "-ad")
    tolerant = _expected_revision(repo, initial)
    assert other._object_revision(repo, tolerate_additions=True) == tolerant != initial
    after_pack = _expected_revision(repo, strict)
    assert first._object_revision(repo) == after_pack != strict

    nested = tmp_path / "alternate-00.git/objects/info/alternates"
    nested.write_text("\n".join(nested.read_text().splitlines()[:-1]) + "\n", encoding="utf-8")
    assert other._object_revision(repo, tolerate_additions=True) == _expected_revision(
        repo, tolerant
    )
    assert first._object_revision(repo) == _expected_revision(repo, after_pack)

    paths = tuple(path.encode() for path in _fresh(repo)[2])
    expected = hashlib.sha256(b"".join(len(path).to_bytes(8, "big") + path for path in paths))
    assert len(paths) == 63
    assert {entry[2] for entry in changeset_cache._OBJECT_HISTORY.values()} == {expected.digest()}
