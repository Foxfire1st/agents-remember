"""D569: a comparison after a one-blob change recomputes only what that blob changes.

The review's pure answers are kept per process by the exact inputs they are functions of -- blob
ids, tree ids, document bytes -- so a kept answer is the same answer bit for bit, and four of those
memos are kept on disk for the next process. Each case below shows one kept answer being reused
(the computation is not run again) and the answer staying the one the computation gives.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agents_remember.application import review_memo_store, review_tree_knowledge
from agents_remember.application.knowledge_leaf import chain
from agents_remember.application.knowledge_worklist import code as worklist_code
from agents_remember.application.knowledge_worklist import route_context
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.kernel.recorded_reads import record_read, recorded_reads
from agents_remember.memory.conversion.code_objects import (
    CodeObjectError,
    CodeObjects,
)
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo
from agents_remember.memory.knowledge_index import MemoryTreeSnapshot, git_tree_snapshot
from agents_remember.memory.knowledge_index import build as index_build
from agents_remember.memory_quality.style.citations import extents
from agents_remember.serving.review_answer_reuse import ANSWER_WRITES
from test_knowledge_worklist import (
    OTHER,
    REVIEW,
    REVIEW_V1,
    World,
    git,
)
from test_knowledge_worklist import world as _world_fixture

world = _world_fixture

SOURCE = "sha256:" + "d569" * 16
"""A producing build for the store; any digest names one exactly."""

MODULE = '''"""A module."""


def alpha():
    return 1


def beta():
    return alpha() + 1


class Holder:
    def gamma(self):
        return beta()
'''


def _repository(root: Path, files: Mapping[str, str | bytes | None]) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "memo fixture")
    _commit(root, files)
    return root


def _commit(root: Path, files: Mapping[str, str | bytes | None]) -> str:
    for relative, content in files.items():
        target = root / relative
        if content is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", "fixture")
    return git(root, "rev-parse", "HEAD^{tree}")


@pytest.fixture
def counted_parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every tree-sitter parse the code objects ask for, by path."""

    parses: list[str] = []
    real = extents.definitions

    def counting(path: str, lines: Any) -> Any:
        parses.append(path)
        return real(path, lines)

    monkeypatch.setattr(extents, "definitions", counting)
    return parses


@pytest.fixture
def git_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every argv the worklist's code module runs through ``run_git``."""

    calls: list[list[str]] = []
    real = worklist_code.run_git

    def counting(root: Path, args: list[str], options: Any = None) -> Any:
        calls.append(list(args))
        return real(root, args, options)

    monkeypatch.setattr(worklist_code, "run_git", counting)
    return calls


@pytest.fixture
def cold_memos(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """This process as a new one: no memo entry and no memo store opened yet."""

    monkeypatch.setattr(review_memo_store, "_STORES", {})
    for stored in review_memo_store.STORED:
        stored.memo.clear()
    yield
    for stored in review_memo_store.STORED:
        stored.memo.clear()


def _forget() -> None:
    """Start over as another new process would (inside :func:`cold_memos`)."""

    review_memo_store._STORES.clear()
    for stored in review_memo_store.STORED:
        stored.memo.clear()


# -- symbol answers ---------------------------------------------------------------------------------


def test_a_symbol_answer_is_kept_by_blob_path_and_name_across_reads(
    tmp_path: Path, counted_parses: list[str]
) -> None:
    repository = _repository(tmp_path / "code", {"pkg/module.py": MODULE})
    tree = git(repository, "rev-parse", "HEAD^{tree}")
    blob = CodeObjects(repository).tree(tree)["pkg/module.py"]

    first = CodeObjects(repository).symbol_span("pkg/module.py", blob, "Holder.gamma")
    again = CodeObjects(repository).symbol_span("pkg/module.py", blob, "Holder.gamma")

    assert first == again == (13, 14)
    assert counted_parses == ["pkg/module.py"]  # the second read parsed nothing
    # Another path is another question: its grammar and test-case eligibility come from the path.
    CodeObjects(repository).symbol_span("tests/test_module.py", blob, "alpha")
    assert counted_parses == ["pkg/module.py", "tests/test_module.py"]


def test_a_kept_symbol_answer_still_reads_the_blob_from_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path / "code", {"pkg/module.py": MODULE})
    tree = git(repository, "rev-parse", "HEAD^{tree}")
    blob = CodeObjects(repository).tree(tree)["pkg/module.py"]
    assert CodeObjects(repository).symbol_span("pkg/module.py", blob, "beta") == (8, 9)

    def unreadable(self: CodeObjects, blob_ids: Any) -> None:
        raise CodeObjectError("code blobs cannot be read: the store lost them")

    monkeypatch.setattr(CodeObjects, "prefetch", unreadable)
    with pytest.raises(CodeObjectError, match="the store lost them"):
        CodeObjects(repository).symbol_span("pkg/module.py", blob, "beta")


# -- hunks ------------------------------------------------------------------------------------------


def test_blob_hunks_are_kept_by_pair_and_used_only_for_blobs_the_store_holds(
    tmp_path: Path, git_calls: list[list[str]]
) -> None:
    repository = _repository(tmp_path / "code", {"pkg/module.py": MODULE})
    before = git(repository, "rev-parse", "HEAD^{tree}")
    after = _commit(repository, {"pkg/module.py": MODULE.replace("return 1", "return 2")})
    first = CodeTrees.open(repository, before, after)
    old, new = first.base()["pkg/module.py"], first.candidate()["pkg/module.py"]
    hunks = first.hunks(old, new)
    diffs = [call for call in git_calls if call[0] == "diff"]
    assert len(diffs) == 1

    unread = CodeTrees.open(repository, before, after)
    assert unread.hunks(old, new) == hunks  # neither blob read in this run: Git is asked again
    assert len([call for call in git_calls if call[0] == "diff"]) == 2

    held = CodeTrees.open(repository, before, after)
    held.objects.prefetch((old, new))
    assert held.hunks(old, new) == hunks
    assert len([call for call in git_calls if call[0] == "diff"]) == 2


# -- the paths that hold a name's text ----------------------------------------------------------------


def _searched_paths(calls: list[list[str]], tree: str) -> list[list[str]]:
    return [
        call[call.index("--") + 1 :] if "--" in call else ["<whole tree>"]
        for call in calls
        if call[0] == "grep" and tree in call
    ]


def test_a_new_candidate_searches_only_the_paths_that_changed(
    tmp_path: Path, git_calls: list[list[str]]
) -> None:
    files: dict[str, str | bytes | None] = {
        f"pkg/m{number}.py": f"def needle{number}():\n    return {number}\n" for number in range(8)
    }
    files |= {"pkg/[glob].py": "needle = 1\n", "pkg/plain.txt": "no match here\n"}
    repository = _repository(tmp_path / "code", files)
    first = git(repository, "rev-parse", "HEAD^{tree}")
    listed = CodeTrees.open(repository, first, first)._holding("needle")
    assert "pkg/m1.py" in listed and "pkg/plain.txt" not in listed
    (repository / "pkg/m6.py").chmod(0o755)
    second = _commit(
        repository,
        {
            "pkg/m1.py": "def other():\n    return 0\n",  # loses the text
            "pkg/plain.txt": "needle3 mentioned\n",  # gains it
            "pkg/m2.py": None,  # deleted
            "pkg/new.py": "def needle3():\n    return 9\n",  # added: a second binder
        },
    )
    git_calls.clear()

    incremental = CodeTrees.open(repository, first, second)._holding("needle")
    searched = _searched_paths(git_calls, second)
    assert [sorted(paths) for paths in searched] == [
        [
            ":(literal)pkg/m1.py",
            ":(literal)pkg/m2.py",
            ":(literal)pkg/m6.py",
            ":(literal)pkg/new.py",
            ":(literal)pkg/plain.txt",
        ]
    ]
    whole = CodeTrees.open(repository, first, second)._grep("needle", ())
    assert incremental == whole
    assert "pkg/m1.py" not in incremental and "pkg/plain.txt" in incremental
    assert "pkg/[glob].py" in incremental  # a bracket is an address, never a pattern
    # The binder answer over the new tree is the whole tree's: `needle3` is now bound twice.
    assert CodeTrees.open(repository, first, first).unique_binder("needle3") == "pkg/m3.py"
    assert CodeTrees.open(repository, first, second).unique_binder("needle3") is None


def test_a_changed_name_holding_a_carriage_return_searches_the_whole_tree(
    tmp_path: Path, git_calls: list[list[str]]
) -> None:
    repository = _repository(tmp_path / "code", {"pkg/a.py": "needle = 1\n"})
    first = git(repository, "rev-parse", "HEAD^{tree}")
    CodeTrees.open(repository, first, first)._holding("needle")
    second = _commit(repository, {"pkg/odd\rname.py": "needle = 2\n"})
    git_calls.clear()

    listed = CodeTrees.open(repository, first, second)._holding("needle")

    assert _searched_paths(git_calls, second) == [["<whole tree>"]]
    assert listed == CodeTrees.open(repository, first, second)._grep("needle", ())


# -- parsed documents and route chains -----------------------------------------------------------------


def test_documents_are_parsed_once_per_bytes_inside_the_kept_scope(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = git_tree_snapshot(world.memory, "HEAD")
    broken = MemoryTreeSnapshot(
        key=snapshot.key,
        source=snapshot.source,
        location=snapshot.location,
        files={**snapshot.files, "knowledge/invariants/INV-ZZZZZZ-rule.json": b"{not json"},
    )
    monkeypatch.setattr(index_build, "DOCUMENT_PARSES", BoundedMemo(65_536))
    parsed: list[str] = []
    real = index_build.parse_document_text

    def counting(text: str) -> Any:
        parsed.append(text)
        return real(text)

    monkeypatch.setattr(index_build, "parse_document_text", counting)
    plain = index_build.parse_tree(broken)
    plain_count = len(parsed)
    with index_build.kept_document_parses():
        kept = index_build.parse_tree(broken)
        first_count = len(parsed) - plain_count
        again = index_build.parse_tree(broken)
    assert first_count == plain_count  # the first kept parse parses everything
    assert len(parsed) - plain_count - first_count == 1  # then only the file that fails
    assert again.problems == kept.problems == plain.problems != []
    assert again.records == kept.records == plain.records
    assert again.entries == plain.entries
    index_build.parse_tree(broken)  # outside the scope nothing is reused
    assert len(parsed) - plain_count - first_count - 1 == plain_count


def test_route_chains_are_walked_once_per_memory_tree(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(route_context, "ROUTE_CHAINS", BoundedMemo(65_536))
    walks: list[str] = []
    real = chain.select_chain

    def counting(index: Any, path: str, seed: Any) -> Any:
        walks.append(path)
        return real(index, path, seed)

    monkeypatch.setattr(chain, "select_chain", counting)
    edited = REVIEW_V1.replace("return value + 1", "return value + 2")
    first = world.worklist(world.code_commit({REVIEW: edited}))
    assert walks and len(walks) == len(set(walks))  # one walk per path, whatever the items
    walks.clear()
    second = world.worklist(world.code_commit({REVIEW: edited.replace("+ 2", "+ 3")}))
    assert walks == []  # the same memory tree: every chain is kept
    assert _chains(second) == _chains(first)


def _chains(document: Mapping[str, Any]) -> list[Any]:
    return [
        item["facts"]["context"]["routeChain"]
        for item in document["items"]
        if "routeChain" in item["facts"].get("context", {})
    ]


# -- the worklist and the view ----------------------------------------------------------------------


def test_the_view_takes_its_side_parts_where_the_sequential_view_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    witness = tmp_path / "witness.txt"
    witness.write_text("read by the inventory", encoding="utf-8")

    def inventory(trees: Any) -> str:
        record_read(witness)
        return "the inventory"

    def currentness(trees: Any) -> Any:
        raise RuntimeError("currentness failed")

    monkeypatch.setattr(review_tree_knowledge, "tree_entry_inventory", inventory)
    monkeypatch.setattr(review_tree_knowledge, "side_currentness", currentness)
    monkeypatch.setattr(review_tree_knowledge, "restore_review_memos", lambda *_: None)
    monkeypatch.setattr(review_tree_knowledge, "save_review_memos", lambda: None)
    with recorded_reads() as reads:
        contract = SimpleNamespace(coordination_root=tmp_path)
        beside = review_tree_knowledge._Beside(contract, object())  # type: ignore[arg-type]
        beside._done.wait()
        assert reads == {}  # computed, not yet taken: nothing reached the request's reads
        assert beside.inventory() == "the inventory"
        assert list(reads) == [witness.as_posix()]
        with pytest.raises(RuntimeError, match="currentness failed"):
            beside.currentness()


def test_snake_keys_reads_every_shape_as_before() -> None:
    class Ordered(dict[str, Any]):
        pass

    value = Ordered(
        camelKey=[{"innerKey": (1, 2.5, None, True)}, "plainString"],
        mixedCase=Ordered(deepKey={"pkg/path.py": {"keepMe": "x"}, "RLZ-A00001": []}),
    )
    shaped = review_tree_knowledge.snake_keys(value)
    assert json.dumps(shaped) == json.dumps(
        {
            "camel_key": [{"inner_key": [1, 2.5, None, True]}, "plainString"],
            "mixed_case": {"deep_key": {"pkg/path.py": {"keep_me": "x"}, "RLZ-A00001": []}},
        }
    )


def test_the_worklist_is_the_same_document_cold_warm_and_restored_from_disk(
    world: World, cold_memos: None, counted_parses: list[str]
) -> None:
    git(world.code, "mv", OTHER, "pkg/moved.py")
    ambiguous = REVIEW_V1 + "\n\nclass Holder:\n    def third(self):\n        return 3\n"
    candidate = world.code_commit({REVIEW: ambiguous})
    review_memo_store.restore_review_memos(world.root, SOURCE)
    cold = world.worklist(candidate)
    warm = world.worklist(candidate)
    review_memo_store.save_review_memos()
    ANSWER_WRITES.drain()  # the writes run on the answer writer
    _forget()
    review_memo_store.restore_review_memos(world.root, SOURCE)
    counted_parses.clear()
    restored = world.worklist(candidate)
    assert json.dumps(restored) == json.dumps(warm) == json.dumps(cold)
    assert counted_parses == []  # every symbol answer came from the store
    helper = next(
        entry
        for item in restored["items"]
        for entry in item["facts"].get("entries", ())
        if entry["id"] == "RLZ-B00002"
    )
    assert helper["uniqueMatch"] == {"path": "pkg/moved.py", "label": "mechanical"}


# -- the memos kept on disk -------------------------------------------------------------------------


def _store_files(root: Path) -> list[Path]:
    return sorted(root.joinpath(*review_memo_store.MEMO_DIRECTORY).glob("*.review.json"))


_COLD_PROCESS = """
import json, sys
from pathlib import Path
from agents_remember.application.knowledge_worklist import code as worklist_code
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.review_memo_store import restore_review_memos
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory_quality.style.citations import extents

root, repository, before, after, source = sys.argv[1:6]
asked = []
real = worklist_code.run_git
def counting(where, args, options=None):
    asked.append(args[0])
    return real(where, args, options)
worklist_code.run_git = counting
def no_parse(path, lines):
    raise AssertionError("parsed " + path)
extents.definitions = no_parse
restore_review_memos(Path(root), source)
trees = CodeTrees.open(Path(repository), before, after)
old, new = trees.base()["pkg/module.py"], trees.candidate()["pkg/module.py"]
trees.objects.prefetch((old, new))
print(json.dumps({
    "span": CodeObjects(Path(repository)).symbol_span("pkg/module.py", new, "Holder.gamma"),
    "hunks": [hunk.to_document() for hunk in trees.hunks(old, new) or ()],
    "binder": trees.unique_binder("alpha"),
    "asked": asked,
}))
"""


def _persisted_answers(root: Path, source: str) -> tuple[Path, str, str, dict[str, Any]]:
    """Compute one answer of each stored memo in this process and store them."""

    repository = _repository(root / "code", {"pkg/module.py": MODULE})
    before = git(repository, "rev-parse", "HEAD^{tree}")
    after = _commit(repository, {"pkg/module.py": MODULE.replace("return 1", "return 2")})
    review_memo_store.restore_review_memos(root, source)
    trees = CodeTrees.open(repository, before, after)
    old, new = trees.base()["pkg/module.py"], trees.candidate()["pkg/module.py"]
    expected = {
        "span": list(
            CodeObjects(repository).symbol_span("pkg/module.py", new, "Holder.gamma") or ()
        ),
        "hunks": [hunk.to_document() for hunk in trees.hunks(old, new) or ()],
        "binder": trees.unique_binder("alpha"),
    }
    review_memo_store.save_review_memos()
    ANSWER_WRITES.drain()  # the writes run on the answer writer
    return repository, before, after, expected


def _cold_process(root: Path, repository: Path, before: str, after: str) -> dict[str, Any]:
    run = subprocess.run(
        [sys.executable, "-c", _COLD_PROCESS, str(root), str(repository), before, after, SOURCE],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_a_cold_process_restores_each_persisted_answer(tmp_path: Path, cold_memos: None) -> None:
    repository, before, after, expected = _persisted_answers(tmp_path, SOURCE)
    assert len(_store_files(tmp_path)) == len(review_memo_store.STORED)

    answered = _cold_process(tmp_path, repository, before, after)

    assert {key: answered[key] for key in expected} == expected
    assert "diff" not in answered["asked"] and "grep" not in answered["asked"]  # nor a parse


@pytest.mark.parametrize("damage", ["corrupt", "unreadable", "another-build"])
def test_a_damaged_or_foreign_store_is_absent_and_everything_is_computed(
    tmp_path: Path,
    cold_memos: None,
    counted_parses: list[str],
    git_calls: list[list[str]],
    damage: str,
) -> None:
    repository, before, after, expected = _persisted_answers(tmp_path, SOURCE)
    source = SOURCE
    assert len(_store_files(tmp_path)) == len(review_memo_store.STORED) > 0
    for path in _store_files(tmp_path):
        if damage == "corrupt":
            path.write_bytes(path.read_bytes()[: path.stat().st_size // 2])
        elif damage == "unreadable":
            path.chmod(0)
    if damage == "another-build":
        source = "sha256:" + "0" * 64
    _forget()
    counted_parses.clear()
    git_calls.clear()

    review_memo_store.restore_review_memos(tmp_path, source)
    trees = CodeTrees.open(repository, before, after)
    old, new = trees.base()["pkg/module.py"], trees.candidate()["pkg/module.py"]
    answered = {
        "span": list(
            CodeObjects(repository).symbol_span("pkg/module.py", new, "Holder.gamma") or ()
        ),
        "hunks": [hunk.to_document() for hunk in trees.hunks(old, new) or ()],
        "binder": trees.unique_binder("alpha"),
    }

    assert answered == expected
    assert counted_parses and [call[0] for call in git_calls].count("diff") == 1
    assert "grep" in [call[0] for call in git_calls]
    for path in _store_files(tmp_path):
        path.chmod(0o600)


def test_a_changed_blob_is_computed_and_the_unchanged_answer_restored(
    tmp_path: Path, cold_memos: None, counted_parses: list[str]
) -> None:
    repository = _repository(tmp_path / "code", {"pkg/module.py": MODULE, "pkg/other.py": MODULE})
    tree = git(repository, "rev-parse", "HEAD^{tree}")
    review_memo_store.restore_review_memos(tmp_path, SOURCE)
    blobs = CodeObjects(repository).tree(tree)
    assert CodeObjects(repository).symbol_span("pkg/other.py", blobs["pkg/other.py"], "beta")
    assert CodeObjects(repository).symbol_span("pkg/module.py", blobs["pkg/module.py"], "beta")
    review_memo_store.save_review_memos()
    ANSWER_WRITES.drain()  # the writes run on the answer writer
    changed = MODULE.replace("def beta():\n", "def beta():\n    x = 0\n")
    after = CodeObjects(repository).tree(_commit(repository, {"pkg/module.py": changed}))
    _forget()
    counted_parses.clear()

    review_memo_store.restore_review_memos(tmp_path, SOURCE)
    unchanged = CodeObjects(repository).symbol_span("pkg/other.py", after["pkg/other.py"], "beta")
    moved = CodeObjects(repository).symbol_span("pkg/module.py", after["pkg/module.py"], "beta")

    assert counted_parses == ["pkg/module.py"]  # only the changed blob is parsed
    assert unchanged == (8, 9) and moved == (8, 10)
