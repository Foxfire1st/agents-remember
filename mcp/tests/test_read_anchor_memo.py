"""The anchor-observation memo: same answers, fewer Git reads, and nothing remembered that can change.

Anchor observation remembers, for the life of the process, the answers Git and the parser give about
complete object ids. These cases hold it to the things that make that safe, against a real Git
repository rather than a stub:

* a remembered answer is the answer the unremembered path gives, and a repeat costs only the
  per-resolver probe that the tree is still there;
* a failure is never remembered, so a later success is observed;
* whether a repository still holds the tree is never remembered, so a tree that is pruned or whose
  alternate is revoked is reported unavailable rather than answered from memory;
* one repository's answers are never served for another's, and one grammar's parse never for
  another's;
* a question asked with anything but a complete object id is never remembered;
* the table stays within its bound under least-recently-used eviction and under concurrent use.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from agents_remember.memory.knowledge import read_anchors
from agents_remember.memory.knowledge.read_anchor_memo import (
    BLOB_DEFINITIONS,
    BLOB_LINES,
    TREE_ENTRIES,
    BoundedMemo,
    is_complete_object_id,
)
from agents_remember.memory_quality.style.citations import extents

SOURCE = '''"""A module with a constant, a class with a method, and a function."""

LIMIT = 3


class Holder:
    def method(self) -> int:
        return LIMIT


def helper() -> int:
    return 1
'''
MODULE = "src/module.py"
NOTES = "src/notes.txt"
GONE = "src/gone.py"
_TABLES = (TREE_ENTRIES, BLOB_LINES, BLOB_DEFINITIONS)
UNAVAILABLE = "recorded_object_unavailable"


@dataclass(frozen=True)
class _Tree:
    root: Path
    tree_id: str
    blobs: dict[str, str]


def _git(root: Path, *args: str) -> str:
    environment = {"PATH": os.environ["PATH"], "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _forgotten() -> Iterator[None]:
    """Each case starts and ends with an empty memo, whatever ran in this worker before it."""

    for table in _TABLES:
        table.clear()
    yield
    for table in _TABLES:
        table.clear()


@pytest.fixture
def tree(tmp_path: Path) -> _Tree:
    """A real repository holding one parsed module and one text file, as a written tree object."""

    root = tmp_path / "repository"
    (root / "src").mkdir(parents=True)
    _git(root, "init", "-q")
    (root / MODULE).write_text(SOURCE, encoding="utf-8")
    (root / NOTES).write_text("first\nsecond\n", encoding="utf-8")
    _git(root, "add", "-A")
    tree_id = _git(root, "write-tree")
    blobs = {path: _git(root, "rev-parse", f"{tree_id}:{path}") for path in (MODULE, NOTES)}
    return _Tree(root=root, tree_id=tree_id, blobs=blobs)


@pytest.fixture
def git_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every Git argv the observation owner runs, delegated to the real runner."""

    calls: list[list[str]] = []
    real = read_anchors.run_git

    def spy(root: Path, args: list[str], *rest: Any, **options: Any) -> Any:
        calls.append(list(args))
        return real(root, args, *rest, **options)

    monkeypatch.setattr(read_anchors, "run_git", spy)
    return calls


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every path the shipped extractor is asked to parse."""

    parsed: list[str] = []
    real = extents.definitions

    def spy(path: str, lines: list[str]) -> Any:
        parsed.append(path)
        return real(path, lines)

    monkeypatch.setattr(extents, "definitions", spy)
    return parsed


def _claim(path: str, object_id: str, locator: dict[str, Any]) -> dict[str, Any]:
    return {
        "anchor_id": str(uuid4()),
        "path": path,
        "source_identity": {"object_id": object_id},
        "locator": locator,
    }


def _symbol(name: str) -> dict[str, Any]:
    return {"kind": "symbol", "language": "python", "qualified_name": name}


def _claims(tree: _Tree) -> list[dict[str, Any]]:
    """Every outcome the memo sits behind: symbols, a range, a file, an absence and a mismatch."""

    module = tree.blobs[MODULE]
    return [
        _claim(MODULE, module, _symbol("helper")),
        _claim(MODULE, module, _symbol("Holder.method")),
        _claim(MODULE, module, _symbol("LIMIT")),
        _claim(MODULE, module, _symbol("Invented.method")),
        _claim(MODULE, module, {"kind": "file"}),
        _claim(NOTES, tree.blobs[NOTES], {"kind": "line_range", "start_line": 1, "end_line": 2}),
        _claim(NOTES, tree.blobs[NOTES], {"kind": "line_range", "start_line": 2, "end_line": 9}),
        _claim(GONE, module, {"kind": "file"}),
        _claim(MODULE, "0" * 40, {"kind": "file"}),
    ]


def _observe(tree: _Tree, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One owner's pass: a fresh resolver, as every reader builds one, over every claim."""

    resolver = read_anchors._TreeAnchorResolver(tree.root, tree.tree_id)
    return [resolver(claim).model_dump(mode="json") for claim in claims]


def _unremembered(tree: _Tree, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same pass with the memo emptied before every observation: the path before the memo."""

    observed = []
    for claim in claims:
        for table in _TABLES:
            table.clear()
        observed.append(_observe(tree, [claim])[0])
    return observed


def _git_kind(calls: list[list[str]], *prefix: str) -> int:
    return sum(call[: len(prefix)] == list(prefix) for call in calls)


def test_repeated_observations_answer_identically_from_one_read_per_object(
    tree: _Tree, git_calls: list[list[str]], parses: list[str]
) -> None:
    """Every owner re-observes the same anchors; the memo answers them once per object."""

    claims = _claims(tree)
    baseline = _unremembered(tree, claims)
    for table in _TABLES:
        table.clear()
    git_calls.clear()
    parses.clear()

    first = _observe(tree, claims)
    after_first = list(git_calls)
    second = _observe(tree, claims)

    assert first == baseline, "a remembered answer is the answer the unremembered path gives"
    assert second == first
    assert git_calls[len(after_first) :] == [["cat-file", "-e", f"{tree.tree_id}^{{tree}}"]], (
        "a repeat pass asks Git only whether the tree is still there"
    )
    assert _git_kind(after_first, "cat-file", "-e") == 1
    assert _git_kind(after_first, "ls-tree") == 3, (
        "one lookup per distinct path, the absent one too"
    )
    assert _git_kind(after_first, "cat-file", "blob") == 2, "one read per distinct blob"
    assert parses == [MODULE], "one parse serves every symbol anchored in the blob"
    resolutions = [observed["resolution"] for observed in first]
    assert resolutions == [
        "exact_recorded_blob",
        "exact_recorded_blob",
        "exact_recorded_blob",
        "recorded_blob_mismatch",
        "exact_recorded_blob",
        "exact_recorded_blob",
        "exact_recorded_blob",
        "path_absent",
        "recorded_blob_mismatch",
    ]
    assert first[1]["resolved_ranges"] == [{"kind": "line_range", "start_line": 7, "end_line": 8}]
    assert first[6]["resolved_ranges"] == [], "a range past the blob's end stays unresolved"


def test_a_failure_is_never_remembered_and_the_later_success_is_observed(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tree probe, a lookup and a blob read that fail once are each asked again next time."""

    real = read_anchors.run_git
    failed: set[str] = set()
    calls: list[list[str]] = []

    def fails_once_per_kind(root: Path, args: list[str], *rest: Any, **options: Any) -> Any:
        calls.append(list(args))
        kind = " ".join(args[:2])
        if kind not in failed:
            failed.add(kind)
            return subprocess.CompletedProcess(args, 128, stdout="", stderr="fatal: transient")
        return real(root, args, *rest, **options)

    monkeypatch.setattr(read_anchors, "run_git", fails_once_per_kind)
    claim = [_claim(MODULE, tree.blobs[MODULE], _symbol("helper"))]

    answers = [_observe(tree, claim)[0] for _attempt in range(4)]

    assert [answer["resolution"] for answer in answers] == [
        "recorded_object_unavailable",  # the tree probe failed
        "recorded_object_unavailable",  # the lookup failed
        "unsupported_locator",  # the blob read failed
        "exact_recorded_blob",
    ]
    assert "exited 128" in answers[1]["detail"]
    assert "could not read blob" in answers[2]["detail"]
    assert [" ".join(call[:2]) for call in calls] == [
        "cat-file -e",  # failed: asked again below
        "cat-file -e",  # every resolver probes the tree afresh
        "ls-tree -z",  # failed: asked again below
        "cat-file -e",
        "ls-tree -z",
        "cat-file blob",  # failed: asked again below
        "cat-file -e",
        "cat-file blob",
    ], "each failed question is asked again, and each answered one is not"
    settled = len(calls)
    assert _observe(tree, claim)[0] == answers[3]
    assert [" ".join(call[:2]) for call in calls[settled:]] == ["cat-file -e"], (
        "only the success is remembered, and it is then served behind a fresh probe"
    )


def test_a_question_asked_with_an_abbreviated_tree_id_is_never_remembered(
    tree: _Tree, git_calls: list[list[str]]
) -> None:
    """An abbreviation can name another object later, so its answers are asked of Git every time."""

    abbreviated = tree.tree_id[:12]
    claim = _claim(NOTES, tree.blobs[NOTES], {"kind": "file"})
    for _attempt in range(2):
        observed = read_anchors.observe_anchor(
            claim, repository_root=tree.root, tree_id=abbreviated
        )
        assert observed.resolution == "exact_recorded_blob"
        assert read_anchors._tree_exists(tree.root, abbreviated)

    assert _git_kind(git_calls, "ls-tree") == 2
    assert _git_kind(git_calls, "cat-file", "-e") == 2
    assert len(TREE_ENTRIES) == 0
    complete = [
        is_complete_object_id(value)
        for value in (tree.tree_id, "a" * 64, abbreviated, "HEAD", "A" * 40, "a" * 40 + "\n")
    ]
    assert complete == [True, True, False, False, False, False]


def test_the_memo_evicts_the_least_recently_used_past_its_weight_bound() -> None:
    """Weight, not entry count, is the bound; a read refreshes an entry; an oversized one is refused."""

    memo: BoundedMemo[str, str] = BoundedMemo(10, weigh=len)
    memo.put("a", "aaaa")
    memo.put("b", "bbbb")
    assert memo.get("a") == ("aaaa",), "a read makes 'a' the most recently used"
    memo.put("c", "cccc")

    assert memo.get("b") is None, "the least recently used entry was evicted to fit"
    assert memo.get("a") == ("aaaa",) and memo.get("c") == ("cccc",)
    assert memo.weight == 8
    memo.put("huge", "x" * 11)
    assert memo.get("huge") is None and len(memo) == 2, "a value over the bound is not stored"
    memo.put("a", "a")
    assert memo.weight == 5, "an overwrite replaces the old weight rather than adding to it"
    memo.put("absent", "")
    assert memo.get("absent") == ("",), "a remembered empty answer is a hit, not a miss"

    for inserted in (100, 20_000):
        scaled: BoundedMemo[int, int] = BoundedMemo(64, weigh=lambda value: value % 3 + 1)
        for key in range(inserted):
            scaled.put(key, key)
        held = [key for key in range(inserted) if scaled.get(key) is not None]
        assert scaled.weight == sum(key % 3 + 1 for key in held) <= 64, inserted
        assert held == list(range(inserted - len(held), inserted)), "the newest entries remain"
        assert scaled.weight > 64 - 3, "eviction stops as soon as the next entry fits"


def test_concurrent_readers_share_answers_and_the_bound_holds(tree: _Tree) -> None:
    """A multi-threaded server: every thread gets the serial answer and the table stays bounded.

    The interpreter's switch interval is shortened so the threads really interleave inside the
    table's bookkeeping rather than each running to completion inside one time slice.
    """

    claims = _claims(tree)
    serial = _observe(tree, claims)
    for table in _TABLES:
        table.clear()
    memo: BoundedMemo[int, int] = BoundedMemo(50, weigh=lambda value: value % 7 + 1)
    results: list[list[dict[str, Any]]] = []
    lock = threading.Lock()

    def reader(offset: int) -> Callable[[], None]:
        def run() -> None:
            observed = _observe(tree, claims)
            for key in range(offset, offset + 4000):
                memo.put(key % 97, key % 97)
                memo.get((key * 31) % 97)
            with lock:
                results.append(observed)

        return run

    threads = [threading.Thread(target=reader(index * 13)) for index in range(8)]
    previous_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(previous_interval)

    assert results == [serial] * 8
    held = [key for key in range(97) if memo.get(key) is not None]
    assert memo.weight == sum(key % 7 + 1 for key in held) <= 50


def _prune_unreachable(tree: _Tree, _borrower: Path) -> Path:
    """Drop the only thing keeping the tree reachable and prune: the release-then-gc workflow."""

    (tree.root / ".git" / "index").unlink()
    _git(tree.root, "prune", "--expire=now")
    return tree.root


def _revoke_alternate(tree: _Tree, borrower: Path) -> Path:
    """Remove the alternate through which a second repository read the tree's objects."""

    (borrower / ".git" / "objects" / "info" / "alternates").unlink()
    return borrower


@pytest.mark.parametrize("lose", [_prune_unreachable, _revoke_alternate], ids=["pruned", "revoked"])
def test_a_tree_that_stops_being_available_is_reported_unavailable_not_remembered(
    tree: _Tree, tmp_path: Path, lose: Callable[[_Tree, Path], Path]
) -> None:
    """Holding an object is a fact about the repository, not the id, so it is asked every time.

    Every anchor is first observed and its answers remembered; then the tree stops being available
    -- released and pruned, or its alternate revoked -- and a fresh resolver must say so for every
    anchor rather than resolving them from the remembered entries, lines and definitions.
    """

    borrower = tmp_path / "borrower"
    borrower.mkdir()
    _git(borrower, "init", "-q")
    alternates = borrower / ".git" / "objects" / "info" / "alternates"
    alternates.write_text(f"{tree.root / '.git' / 'objects'}\n", encoding="utf-8")
    claims = _claims(tree)
    for root in (tree.root, borrower):
        warmed = _observe(_Tree(root, tree.tree_id, tree.blobs), claims)
        assert warmed[0]["resolution"] == "exact_recorded_blob"
    assert len(TREE_ENTRIES) and len(BLOB_LINES) and len(BLOB_DEFINITIONS)

    lost = lose(tree, borrower)

    after = _observe(_Tree(lost, tree.tree_id, tree.blobs), claims)
    assert [observed["resolution"] for observed in after] == [UNAVAILABLE] * len(claims)
    assert not any(observed.get("resolved_ranges") for observed in after)
    assert not read_anchors._tree_exists(lost, tree.tree_id)


def test_one_repositorys_answers_are_never_served_for_another(tree: _Tree, tmp_path: Path) -> None:
    """The same object ids in a second repository are asked of that repository, never remembered.

    Two neighbours share the first repository's tree id. One holds nothing: observed without a
    resolver -- the path a caller that lists the tree itself takes -- its lookup fails rather than
    returning the first repository's remembered entry. The other holds the tree but none of its
    blobs, as a blobless partial clone does: its entries are its own, and a range or symbol over a
    blob it cannot read is refused rather than answered from the first repository's lines.
    """

    claims = _claims(tree)
    first = _observe(tree, claims)
    assert first[0]["resolution"] == "exact_recorded_blob"

    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "-q")
    file_claim = _claim(MODULE, tree.blobs[MODULE], {"kind": "file"})
    direct = read_anchors.observe_anchor(file_claim, repository_root=empty, tree_id=tree.tree_id)
    assert direct.resolution == UNAVAILABLE, "another repository's tree entry was served"

    blobless = tmp_path / "blobless"
    blobless.mkdir()
    _git(blobless, "init", "-q")
    listed = _git(
        tree.root, "ls-tree", "-r", "-t", "--format=%(objecttype) %(objectname)", tree.tree_id
    )
    subtrees = [line.split()[1] for line in listed.splitlines() if line.startswith("tree ")]
    for tree_object in (tree.tree_id, *subtrees):
        raw = subprocess.run(
            ["git", "-C", str(tree.root), "cat-file", "tree", tree_object],
            check=True,
            capture_output=True,
        ).stdout
        written = subprocess.run(
            ["git", "-C", str(blobless), "hash-object", "-t", "tree", "-w", "--stdin"],
            input=raw,
            check=True,
            capture_output=True,
            env={"PATH": os.environ["PATH"], "HOME": str(blobless), "GIT_CONFIG_NOSYSTEM": "1"},
        ).stdout.decode()
        assert written.strip() == tree_object
    reading = [
        _claim(NOTES, tree.blobs[NOTES], {"kind": "line_range", "start_line": 1, "end_line": 2}),
        _claim(MODULE, tree.blobs[MODULE], _symbol("helper")),
    ]
    neighbour = _observe(_Tree(blobless, tree.tree_id, tree.blobs), reading)
    assert [observed["resolution"] for observed in neighbour] == [
        "exact_recorded_blob",  # the entry is the neighbour's own; its lines are not
        "unsupported_locator",
    ]
    assert all(not observed["resolved_ranges"] for observed in neighbour), (
        "another repository's blob lines or definitions were served"
    )
    assert all("could not read blob" in observed["detail"] for observed in neighbour)
    home = _observe(tree, reading)
    assert [(observed["resolution"], observed["resolved_ranges"]) for observed in home] == [
        ("exact_recorded_blob", [{"kind": "line_range", "start_line": 1, "end_line": 2}]),
        ("exact_recorded_blob", first[0]["resolved_ranges"]),
    ], "the first repository still answers from its own objects"


def test_one_blob_is_parsed_per_grammar_whichever_is_asked_first(tmp_path: Path) -> None:
    """The same bytes define different names under different grammars, so each keeps its own parse."""

    root = tmp_path / "grammars"
    root.mkdir()
    _git(root, "init", "-q")
    body = "x = 1\nfunction g() { return 1 }\n"
    for path in ("a.py", "a.ts"):
        (root / path).write_text(body, encoding="utf-8")
    _git(root, "add", "-A")
    tree_id = _git(root, "write-tree")
    blob = _git(root, "rev-parse", f"{tree_id}:a.py")
    assert _git(root, "rev-parse", f"{tree_id}:a.ts") == blob
    shared = _Tree(root=root, tree_id=tree_id, blobs={"a.py": blob, "a.ts": blob})

    def resolved(order: tuple[str, ...]) -> dict[tuple[str, str], bool]:
        for table in _TABLES:
            table.clear()
        claims = [_claim(path, blob, _symbol(name)) for path in order for name in ("x", "g")]
        observed = _observe(shared, claims)
        return {
            (claim["path"], claim["locator"]["qualified_name"]): bool(answer.get("resolved_ranges"))
            for claim, answer in zip(claims, observed, strict=True)
        }

    expected = {
        ("a.py", "x"): True,
        ("a.py", "g"): False,
        ("a.ts", "x"): False,
        ("a.ts", "g"): True,
    }
    assert resolved(("a.py", "a.ts")) == expected
    assert resolved(("a.ts", "a.py")) == expected
