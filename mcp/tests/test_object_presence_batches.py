"""Batched object-presence reads answer exactly what the one-by-one reads answer, with less work.

``CodeObjects.prefetch_presence`` answers ``has_blob`` for many IDs with two ``cat-file
--batch-check`` reads; ``RepositoryLayout.objects_presence`` lists each loose fan-out directory
once instead of stat'ing every name of a closure. Neither may change an answer or move a failure.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from agents_remember.memory.conversion import code_objects
from agents_remember.memory.conversion.code_objects import CodeObjectError, CodeObjects
from agents_remember.serving.changeset_generation import (
    RepositoryLayout,
    identity,
    repository_layout,
)
from knowledge_conversion_test_support import commit_files, git, init_repository

_MISSING = "e" * 40


def _store(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A repository holding a blob, a tree and a commit, plus an ID it does not hold."""

    root = tmp_path / "code"
    init_repository(root)
    commit = commit_files(root, {"a.py": "A = 1\n", "b.py": "B = 2\n"}, "first")
    return root, {
        "blob": git(root, "rev-parse", f"{commit}:a.py"),
        "other_blob": git(root, "rev-parse", f"{commit}:b.py"),
        "tree": git(root, "rev-parse", f"{commit}^{{tree}}"),
        "commit": commit,
        "missing": _MISSING,
    }


def _one_by_one(root: Path, blob_id: str) -> bool | str:
    """``has_blob`` with nothing prefetched: the answer, or the error it raises."""

    try:
        return CodeObjects(root).has_blob(blob_id)
    except CodeObjectError as error:
        return f"raised: {error}"


def _counting(calls: list[list[str]]) -> Any:
    real = code_objects.run_git

    def counted(repository: Path, args: list[str], *rest: Any, **named: Any) -> Any:
        calls.append(list(args))
        return real(repository, args, *rest, **named)

    return counted


def test_prefetched_presence_answers_present_other_type_and_asks_again_for_missing(
    tmp_path: Path,
) -> None:
    root, ids = _store(tmp_path)
    objects = CodeObjects(root)
    calls: list[list[str]] = []
    with mock.patch.object(code_objects, "run_git", _counting(calls)):
        objects.prefetch_presence(ids.values())
        assert len(calls) == 2  # one batch per question, whatever the number of IDs
        assert calls[0][:2] == ["--no-replace-objects", "cat-file"]
        assert calls[1][0] == "cat-file"
        calls.clear()
        assert objects.has_blob(ids["blob"]) is True
        assert objects.has_blob(ids["other_blob"]) is True
        assert objects.has_blob(ids["tree"]) is False
        assert objects.has_blob(ids["commit"]) is False
        assert calls == []  # every object the store holds was answered by the batches
        assert objects.has_blob(ids["missing"]) is False
        assert objects.has_blob(ids["missing"]) is False
    # A missing object is never kept: each call asks Git again, exactly as before.
    assert [call[:2] for call in calls] == [["cat-file", "-e"], ["cat-file", "-e"]]
    for blob_id in ids.values():
        assert objects.has_blob(blob_id) == _one_by_one(root, blob_id)


def test_a_one_by_one_answer_for_a_held_object_is_kept_and_a_missing_one_is_not(
    tmp_path: Path,
) -> None:
    root, ids = _store(tmp_path)
    objects = CodeObjects(root)
    calls: list[list[str]] = []
    with mock.patch.object(code_objects, "run_git", _counting(calls)):
        for _ in range(3):
            assert objects.has_blob(ids["blob"]) is True
            assert objects.has_blob(ids["tree"]) is False
            assert objects.has_blob(ids["missing"]) is False
    asked = [(call[1], call[-1]) for call in calls]
    assert asked.count(("-e", ids["blob"])) == 1
    assert asked.count(("-e", ids["tree"])) == 1
    assert asked.count(("-e", ids["missing"])) == 3


def test_replace_refs_give_the_batched_answer_the_one_by_one_reads_give(tmp_path: Path) -> None:
    """``-e`` reads the stored object, ``-t`` its replacement: the batches ask both."""

    root, ids = _store(tmp_path)
    absent = git(root, "hash-object", "--stdin", input_text="never stored\n")
    git(root, "replace", "-f", absent, ids["other_blob"])  # an absent object replaced by a blob
    git(root, "replace", "-f", ids["blob"], ids["tree"])  # a stored blob replaced by a tree
    wanted = [*ids.values(), absent]
    expected = {blob_id: _one_by_one(root, blob_id) for blob_id in wanted}
    assert expected[absent] is False and expected[ids["blob"]] is False
    objects = CodeObjects(root)
    objects.prefetch_presence(wanted)
    assert {blob_id: objects.has_blob(blob_id) for blob_id in wanted} == expected


def test_a_failed_batch_keeps_nothing_and_the_failure_is_raised_where_it_was(
    tmp_path: Path,
) -> None:
    root, ids = _store(tmp_path)
    real = code_objects.run_git

    def failing(repository: Path, args: list[str], *rest: Any, **named: Any) -> Any:
        if any(arg.startswith("--batch-check") for arg in args) or args[:2] == ["cat-file", "-e"]:
            return subprocess.CompletedProcess(args, 128, "", "fatal: the store is unreadable")
        return real(repository, args, *rest, **named)

    def timing_out(repository: Path, args: list[str], *rest: Any, **named: Any) -> Any:
        if any(arg.startswith("--batch-check") for arg in args):
            raise subprocess.TimeoutExpired(args, 30)
        return real(repository, args, *rest, **named)

    objects = CodeObjects(root)
    with mock.patch.object(code_objects, "run_git", failing):
        objects.prefetch_presence(ids.values())
        with pytest.raises(CodeObjectError, match="the store is unreadable"):
            objects.has_blob(ids["blob"])
    with mock.patch.object(code_objects, "run_git", timing_out):
        objects.prefetch_presence(ids.values())
    assert objects.has_blob(ids["blob"]) is True  # read one by one, as before


def test_an_unexpected_batch_reply_keeps_nothing(tmp_path: Path) -> None:
    root, ids = _store(tmp_path)
    real = code_objects.run_git

    def short(repository: Path, args: list[str], *rest: Any, **named: Any) -> Any:
        if any(arg.startswith("--batch-check") for arg in args):
            return subprocess.CompletedProcess(args, 0, f"{ids['blob']} blob\n", "")
        return real(repository, args, *rest, **named)

    objects = CodeObjects(root)
    calls: list[list[str]] = []
    with mock.patch.object(code_objects, "run_git", short):
        objects.prefetch_presence(ids.values())
    with mock.patch.object(code_objects, "run_git", _counting(calls)):
        assert objects.has_blob(ids["tree"]) is False
    assert ["cat-file", "-e"] in [call[:2] for call in calls]


def test_only_full_object_ids_and_unread_ones_are_batched(tmp_path: Path) -> None:
    root, ids = _store(tmp_path)
    objects = CodeObjects(root)
    objects.blob(ids["blob"])  # read bytes are already known present
    calls: list[list[str]] = []
    with mock.patch.object(code_objects, "run_git", _counting(calls)):
        objects.prefetch_presence([ids["blob"], "HEAD", ids["tree"][:12], ""])
    assert calls == []
    assert objects.has_blob(ids["tree"][:12]) == _one_by_one(root, ids["tree"][:12])


def _named_one_by_one(layout: RepositoryLayout, oids: tuple[str, ...]) -> tuple[str, ...]:
    """The witnesses as the closure read named them before fan-out listing: one stat per name."""

    packs: str | None = None
    result: list[str] = []
    for oid in oids:
        for directory in layout.object_directories:
            loose = directory / oid[:2] / oid[2:]
            stamp = identity(loose)
            if stamp:
                result.append(repr(("loose", str(loose), stamp)))
                break
        else:
            if packs is None:
                stamps = []
                for directory in layout.object_directories:
                    packed = directory / "pack"
                    entries = (
                        tuple(
                            sorted(
                                (entry.name, identity(entry))
                                for entry in packed.iterdir()
                                if entry.name.endswith((".idx", ".pack"))
                            )
                        )
                        if packed.is_dir()
                        else ()
                    )
                    stamps.append((str(directory), entries))
                packs = repr(("packed", tuple(stamps)))
            result.append(packs)
    return tuple(result)


def test_fanout_listing_gives_the_witnesses_one_stat_per_name_gives(tmp_path: Path) -> None:
    root = tmp_path / "code"
    init_repository(root)
    commit_files(root, {"a.py": "A = 1\n"}, "first")
    git(root, "repack", "-a", "-d", "-q")
    commit_files(root, {"b.py": "B = 2\n"}, "second")  # loose beside packed
    oids = tuple(git(root, "rev-list", "--objects", "--no-object-names", "--all").split())
    layout = repository_layout(root)
    witnesses = layout.objects_presence((*oids, _MISSING))
    assert witnesses == _named_one_by_one(layout, (*oids, _MISSING))
    assert any(one.startswith("('loose'") for one in witnesses)
    assert any(one.startswith("('packed'") for one in witnesses)
    assert layout.object_presence(oids[0]) == _named_one_by_one(layout, oids[:1])[0]


def test_a_fanout_that_cannot_be_listed_is_stated_name_by_name(tmp_path: Path) -> None:
    store = tmp_path / "objects"
    store.mkdir()
    (store / "ee").write_bytes(b"not a directory")
    layout = RepositoryLayout(
        root=tmp_path,
        git=tmp_path,
        common=tmp_path,
        configuration=(),
        object_directories=(store,),
        observed=(),
        environment=(),
    )
    with pytest.raises(NotADirectoryError):
        _named_one_by_one(layout, (_MISSING,))
    with pytest.raises(NotADirectoryError):
        layout.objects_presence((_MISSING,))
