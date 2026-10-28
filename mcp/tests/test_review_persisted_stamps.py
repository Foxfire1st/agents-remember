"""Disk census identity is deterministic; memory keeps observed history changes."""

import pytest
from agents_remember.serving import changeset_cache, review_answer_reuse
from agents_remember.serving.changeset_cache import (
    ChangesetCache,
    ContentRead,
    ObjectStoreRead,
    RepositoryRead,
)
from agents_remember.serving.review_answer_reuse import (
    ANSWER_WRITES,
    AnswerCodec,
    RecordedAnswerReuse,
)
from agents_remember_test_support.testing.review_git_trees import git
from pydantic import BaseModel


class Answer(BaseModel):
    value: str


CODEC = AnswerCodec[Answer](Answer)


def owner(root):
    ANSWER_WRITES.drain()  # the earlier process's stored answers are written
    changeset_cache._OBJECT_HISTORY.clear()  # a new process begins with no census history
    return RecordedAnswerReuse(directory=root / "answers", build_namespace="test-source")


def census(monkeypatch, value):
    # These synthetic census cases do not need a physical repository layout.
    monkeypatch.setattr(ChangesetCache, "_layout", lambda *_: None)
    monkeypatch.setattr(changeset_cache, "count_objects", lambda _root: value[0])
    monkeypatch.setattr(review_answer_reuse, "count_objects", lambda _root: value[0])


def test_restart_at_same_census_restores_after_different_memory_history(tmp_path, monkeypatch):
    current = [(10, 20, ())]
    census(monkeypatch, current)
    source = ObjectStoreRead(tmp_path)
    first = owner(tmp_path)
    first.cache._object_revision(tmp_path)
    current[0] = (11, 20, ())
    before = first.cache._object_revision(tmp_path)
    result = first.answer(("trees",), (source,), lambda: Answer(value="saved"), codec=CODEC)
    second = owner(tmp_path)
    assert second.cache._object_revision(tmp_path) != before
    calls = []

    def produce():
        calls.append(1)
        return Answer(value="unexpected")

    assert second.answer(("trees",), (source,), produce, codec=CODEC).model_dump_json() == (
        result.model_dump_json()
    )
    assert calls == []


@pytest.mark.parametrize("changed", [(11, 20, ()), (10, 21, ()), (10, 20, ("alternate",))])
def test_changed_census_refuses_disk_answer(tmp_path, monkeypatch, changed):
    current = [(10, 20, ())]
    census(monkeypatch, current)
    sources = (ObjectStoreRead(tmp_path),)
    owner(tmp_path).answer(("trees",), sources, lambda: Answer(value="old"), codec=CODEC)
    current[0] = changed
    assert (
        owner(tmp_path)
        .answer(("trees",), sources, lambda: Answer(value="fresh"), codec=CODEC)
        .value
        == "fresh"
    )


def test_unreadable_direct_census_never_admits_memoized_memory_stamp(tmp_path, monkeypatch):
    reuse = owner(tmp_path)
    monkeypatch.setattr(ChangesetCache, "snapshot", lambda *_: "memory-memo")

    def unreadable(_root):
        raise OSError("unreadable census")

    monkeypatch.setattr(review_answer_reuse, "count_objects", unreadable)
    assert (
        reuse.answer(
            ("trees",), (ObjectStoreRead(tmp_path),), lambda: Answer(value="fresh"), codec=CODEC
        ).value
        == "fresh"
    )
    assert reuse.retained() == 0
    assert not list((tmp_path / "answers").glob("*.review.json"))


def test_disk_after_check_bypasses_request_memo_and_refuses_race(tmp_path, monkeypatch):
    current = [(10, 20, ())]
    census(monkeypatch, current)
    sources = (ObjectStoreRead(tmp_path),)
    owner(tmp_path).answer(("trees",), sources, lambda: Answer(value="old"), codec=CODEC)
    reuse = owner(tmp_path)
    monkeypatch.setattr(ChangesetCache, "snapshot", lambda *_: "memory-memo")
    original = AnswerCodec.decode

    def decode(self, body):
        result = original(self, body)
        current[0] = (11, 20, ())
        return result

    monkeypatch.setattr(AnswerCodec, "decode", decode)
    assert reuse.answer(("trees",), sources, lambda: Answer(value="fresh"), codec=CODEC).value == (
        "fresh"
    )
    assert reuse.retained() == 0


def test_observed_memory_aba_blocks_save_despite_equal_disk_census(tmp_path, monkeypatch):
    current = [(10, 20, ())]
    census(monkeypatch, current)
    reuse = owner(tmp_path)
    source = ObjectStoreRead(tmp_path)

    def produce():
        current[0] = (11, 20, ())
        reuse.cache._object_revision(tmp_path)
        current[0] = (10, 20, ())
        reuse.cache._object_revision(tmp_path)
        return Answer(value="raced")

    assert reuse.answer(("trees",), (source,), produce, codec=CODEC).value == "raced"
    assert reuse.retained() == 0
    assert not list((tmp_path / "answers").glob("*.review.json"))


@pytest.mark.parametrize(
    ("meanwhile", "kept"),
    [((11, 20, ()), True), ((9, 20, ()), False), ((0, 30, ()), False)],
    ids=["an-object-added", "an-object-removed", "a-repack"],
)
def test_a_repository_store_that_only_gained_objects_meanwhile_keeps_the_answer_in_memory(
    tmp_path, monkeypatch, meanwhile, kept
):
    """Another worktree's object in the store they share changes nothing the answer read (D570).

    The memory stamps are one memo throughout; Git's own census, read before and after, decides.
    The stricter disk identity moved, so no stored copy is written either way.
    """

    current = [(10, 20, ())]
    census(monkeypatch, current)
    monkeypatch.setattr(ChangesetCache, "snapshot", lambda *_: "memory-memo")
    reuse = owner(tmp_path)

    def produce():
        current[0] = meanwhile
        return Answer(value="computed")

    source = RepositoryRead(tmp_path)
    assert reuse.answer(("trees",), (source,), produce, codec=CODEC).value == "computed"
    ANSWER_WRITES.drain()
    assert reuse.retained() == (1 if kept else 0)
    assert not list((tmp_path / "answers").glob("*.review.json"))


def test_disk_identity_keeps_ordinary_source_stamps_exact(tmp_path, monkeypatch):
    current = [(10, 20, ())]
    census(monkeypatch, current)
    source = tmp_path / "record.json"
    source.write_text("first")
    sources = (ContentRead(source), ObjectStoreRead(tmp_path))
    owner(tmp_path).answer(("trees",), sources, lambda: Answer(value="old"), codec=CODEC)
    source.write_text("second")
    assert (
        owner(tmp_path)
        .answer(("trees",), sources, lambda: Answer(value="fresh"), codec=CODEC)
        .value
        == "fresh"
    )


def test_memory_only_owner_does_not_add_disk_census_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(ChangesetCache, "snapshot", lambda *_: "memory-stamp")

    def unexpected(_root):
        raise AssertionError("disk census must not run")

    monkeypatch.setattr(review_answer_reuse, "count_objects", unexpected)
    reuse = owner(tmp_path)
    reuse.answer(("trees",), (ObjectStoreRead(tmp_path),), lambda: Answer(value="fresh"))
    assert reuse.retained() == 1


@pytest.mark.parametrize("change", ["reference", "configuration"])
def test_repository_refs_and_configuration_are_not_normalized(tmp_path, change):
    repository = tmp_path / "repo"
    repository.mkdir()
    git(repository, "init")
    git(repository, "config", "user.email", "test@example.invalid")
    git(repository, "config", "user.name", "Test")
    source = repository / "file.txt"
    source.write_text("first")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "first")
    first = git(repository, "rev-parse", "HEAD")
    source.write_text("second")
    git(repository, "commit", "-am", "second")
    inputs = (RepositoryRead(repository, references=("HEAD",)),)
    owner(tmp_path).answer(("summary",), inputs, lambda: Answer(value="old"), codec=CODEC)
    before = review_answer_reuse.count_objects(repository)
    if change == "reference":
        git(repository, "update-ref", "HEAD", first)
    else:
        git(repository, "config", "core.ignoreCase", "true")
    assert review_answer_reuse.count_objects(repository) == before
    assert (
        owner(tmp_path)
        .answer(("summary",), inputs, lambda: Answer(value="fresh"), codec=CODEC)
        .value
        == "fresh"
    )


def test_repository_without_object_observation_keeps_original_stamp(tmp_path, monkeypatch):
    monkeypatch.setattr(ChangesetCache, "snapshot", lambda *_: "exact-repository-stamp")

    def unexpected(_root):
        raise AssertionError("object observation was explicitly disabled")

    monkeypatch.setattr(review_answer_reuse, "count_objects", unexpected)
    source = RepositoryRead(tmp_path, observe_objects=False)
    first = owner(tmp_path).answer(
        ("summary",), (source,), lambda: Answer(value="saved"), codec=CODEC
    )
    second = owner(tmp_path).answer(
        ("summary",), (source,), lambda: Answer(value="wrong"), codec=CODEC
    )
    assert second == first
