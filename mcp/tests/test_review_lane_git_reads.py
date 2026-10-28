"""D569: the files lane asks Git once per kind of read, not once per changed path.

A lane read classifies every changed path of a comparison. Each modified text file's hunks used to
cost one ``git diff`` of its two blobs, and each blob a classification read one ``git cat-file``.
They now come from one ``git diff`` of the two trees and one ``git cat-file --batch`` per request,
and the answers are the ones the per-path reads give, byte for byte.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.review_lane_classification import TreeLane
from agents_remember.application.review_unexplained_lane import lane_summary, unexplained_lane
from test_review_unexplained_lane import Lane, _commit, _repository
from test_review_unexplained_lane import lane as _lane_fixture

lane = _lane_fixture


@pytest.fixture
def git_children(monkeypatch: pytest.MonkeyPatch) -> Iterator[Counter[str]]:
    """Every Git child this process starts, by its subcommand."""

    started: Counter[str] = Counter()
    real = subprocess.Popen.__init__

    def counting(self: subprocess.Popen[Any], args: Any, *rest: Any, **options: Any) -> None:
        argv = [str(one) for one in args] if not isinstance(args, str | bytes) else [str(args)]
        if argv and Path(argv[0]).name == "git":
            at = 1
            while at < len(argv) and argv[at] in {"-C", "-c"}:
                at += 2
            started[argv[at] if at < len(argv) else "?"] += 1
        real(self, args, *rest, **options)

    monkeypatch.setattr(subprocess.Popen, "__init__", counting)
    yield started


def _per_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lane as it read before: no read-ahead, every path asks Git itself."""

    monkeypatch.setattr(TreeLane, "prepare", lambda self, changes, *, hunks=True: None)


def test_the_lane_compares_the_trees_once_and_reads_its_blobs_in_one_batch(
    lane: Lane, git_children: Counter[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    trees = lane.precuration()
    unexplained_lane(trees)  # the comparison's indexes are built once, outside the count
    git_children.clear()

    batched = unexplained_lane(trees)

    # The change inventory's diff, the trees' diff and one attribute question; one blob batch.
    assert git_children["diff"] == 2 and git_children["check-attr"] == 1
    assert git_children["cat-file"] <= 1
    batched_children = sum(git_children.values())
    git_children.clear()
    with monkeypatch.context() as alone:
        _per_path(alone)
        per_path = unexplained_lane(lane.precuration())
    assert git_children["diff"] > 2  # one per modified text file before (D569)
    assert sum(git_children.values()) > batched_children
    assert batched.model_dump_json() == per_path.model_dump_json()


def test_the_entry_count_reads_its_placed_blobs_in_one_batch(
    lane: Lane, git_children: Counter[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    trees = lane.trees(lane.knowledge(curated=True), lane.knowledge(curated=True))
    lane_summary(trees)
    git_children.clear()
    counted = lane_summary(trees)
    assert git_children["cat-file"] <= 1 and git_children["diff"] == 1
    with monkeypatch.context() as alone:
        _per_path(alone)
        assert lane_summary(trees).model_dump_json() == counted.model_dump_json()


def test_a_path_with_a_diff_attribute_is_compared_alone(
    lane: Lane, git_children: Counter[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A blob diff looks attributes up under the names it is given, the two blob ids: here the
    # candidate blob of pkg/whole.py is marked binary by its id, which no path of the tree diff
    # (nor the change inventory) is. That pair is asked of Git by itself, and stays binary.
    whole = CodeTrees.open(lane.code.repository, lane.code.base, lane.code.candidate)
    blob = whole.candidate()["pkg/whole.py"]
    (lane.code.repository / ".gitattributes").write_text(f"{blob} -diff\n", encoding="utf-8")
    trees = lane.precuration()
    unexplained_lane(trees)
    git_children.clear()

    batched = unexplained_lane(trees)

    assert git_children["diff"] == 3
    with monkeypatch.context() as alone:
        _per_path(alone)
        assert unexplained_lane(trees).model_dump_json() == batched.model_dump_json()
    assert whole.hunks(whole.base()["pkg/whole.py"], blob) is None  # binary under its id


ODD_BASE: dict[str, str | bytes] = {
    "crlf.txt": "one\r\ntwo\r\nthree\r\n",
    "carriage.txt": "a\rb\nc\n",
    "patchlike.txt": "diff --git a/x b/x\nindex 1..2\n@@ -1 +1 @@\n",
    "no newline.txt": "last line",
    "binary.dat": b"\0\1\2" * 50,
    "mode.sh": "echo 1\n",
    "café.txt": "accent\n",
    "tab\there.txt": "tab\n",
    "same.txt": "unchanged\n",
    "twin/a.txt": "twin\n",
    "twin/b.txt": "twin\n",
}
ODD_CANDIDATE: dict[str, str | bytes] = {
    **ODD_BASE,
    "crlf.txt": "one\r\nTWO\r\nthree\r\nfour\r\n",
    "carriage.txt": "a\r@@ -9 +9 @@\nc\nd\n",
    "patchlike.txt": "diff --git a/y b/y\nindex 3..4\n@@ -1 +1 @@\nmore\n",
    "no newline.txt": "last line changed",
    "binary.dat": b"\0\3\2" * 50,
    "mode.sh": "echo 2\n",
    "café.txt": "accent changed\n",
    "tab\there.txt": "tab changed\n",
    "twin/a.txt": "twin changed\n",
    "twin/b.txt": "twin changed\n",
}


def test_each_prepared_pair_has_its_own_blob_diffs_hunks(tmp_path: Path) -> None:
    code = _repository(tmp_path / "code")
    base = _commit(code, ODD_BASE)
    candidate = _commit(code, ODD_CANDIDATE, frozenset({"mode.sh"}))
    batch = CodeTrees.open(code, base, candidate)
    before, after = batch.base(), batch.candidate()
    changed = [(path, before[path], after[path]) for path in before if before[path] != after[path]]
    assert len(changed) == 10  # same.txt is unchanged; the twins' two paths share one pair

    batch.prepare_hunks(changed)
    prepared = {path for path, old, new in changed if (old, new) in batch._hunks}

    alone = CodeTrees.open(code, base, candidate)
    for _path, old, new in changed:
        assert batch.hunks(old, new) == alone.hunks(old, new)
    # Git quotes a name holding a tab or a non-ASCII character in its patch header: such a pair
    # is compared alone.
    assert prepared == {path for path, _, _ in changed} - {"tab\there.txt", "café.txt"}
    assert batch.hunks(before["binary.dat"], after["binary.dat"]) is None


def test_a_tree_diff_git_refuses_keeps_nothing(tmp_path: Path) -> None:
    code = _repository(tmp_path / "code")
    base = _commit(code, ODD_BASE)
    candidate = _commit(code, ODD_CANDIDATE)
    trees = CodeTrees.open(code, base, "f" * 40)  # a candidate tree the store does not hold
    trees.prepare_hunks([("crlf.txt", "a" * 40, "b" * 40)])
    assert trees._hunks == {}
    assert CodeTrees.open(code, base, candidate).candidate()  # the real trees still read
