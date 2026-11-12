"""Focused entry selection, complete sidebar inventory, and authoritative line-count contracts."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import replace
from itertools import product
from pathlib import Path
from typing import Any, Literal
from unittest import mock

import pytest
from agents_remember.application import review_tree_entries, review_tree_knowledge
from agents_remember.application.knowledge_currentness import CodeTree
from agents_remember.application.knowledge_currentness.observe import (
    OBSERVATIONS,
    OpenedCodeTree,
    open_code_tree,
)
from agents_remember.application.knowledge_worklist.code import CodeReadError, CodeTrees
from agents_remember.application.review_answer_preparation import _tree_key
from agents_remember.application.review_line_changes import line_changes
from agents_remember.application.review_tree_knowledge import read_review_trees
from agents_remember.kernel import git_command
from agents_remember.kernel.git_command import memo_git_object_lookups, shared_blob_reads
from agents_remember.memory.knowledge_index import Entry, KnowledgeIndex, text_uuid
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.review_tree_entries import ReviewTreeEntrySide
from agents_remember.models.knowledge.review_trees import ReviewTreesResult
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.anchor_content import content_identity
from agents_remember.serving.review_trees import (
    ReviewTreesQuery,
    ReviewTreesSelection,
    register_review_trees_route,
)
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember_test_support.testing.review_git_trees import (
    CODE_FILE,
    INVARIANT,
    LEAF,
    MASTER,
    REPO,
    World,
    _write,
    commit,
    git,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_review_git_trees import SECOND, _memory_facts, _query, _resolve
from test_review_git_trees import world as _world_fixture

world = _world_fixture


@pytest.fixture(autouse=True)
def _no_bound_services() -> Iterator[None]:
    yield
    reset_worktree_services()


@pytest.mark.parametrize(
    "fields",
    [
        {"entry": "RLZ-A00001"},
        {"entry_side": "before"},
        {"entry": "RLZ-A00001", "entry_side": "base"},
        {"entry": "", "entry_side": "after"},
        {"entry": "a" * 65, "entry_side": "after"},
        {"entry": "RLZ-A00001", "entry_side": "after", "invariants": "id"},
        {"entry": "RLZ-A00001", "entry_side": "after", "lane": "files"},
        {"entry": "RLZ-A00001", "entry_side": "after", "file": "a.py"},
    ],
)
def test_entry_selection_rejects_incomplete_or_combined_questions(fields):
    assert ReviewTreesSelection(**fields).problem() is not None


def test_entry_route_carries_exact_selector_and_keeps_existing_invariants():
    seen = []

    def port(query):
        seen.append(query)
        return ReviewTreesResult(
            state="not-converted", repository_id=REPO, master=MASTER, leaf_id=LEAF
        )

    app = FastAPI()
    register_review_trees_route(app, port)
    with TestClient(app) as client:
        address = {"repo": REPO, "master": MASTER, "leaf": LEAF, "comparison": 3}
        response = client.get(
            "/api/review/trees", params={**address, "entry": "RLZ-A00001", "entry_side": "before"}
        )
        assert response.status_code == 200
        assert (seen[-1].entry, seen[-1].entry_side, seen[-1].number) == ("RLZ-A00001", "before", 3)
        assert seen[-1].focused
        response = client.get("/api/review/trees", params={**address, "entry": "RLZ-A00001"})
        assert response.status_code == 400 and len(seen) == 1
        response = client.get("/api/review/trees", params={**address, "invariants": "a,b"})
        assert response.status_code == 200 and seen[-1].invariants == ("a", "b")


def test_entry_owner_is_one_indexed_lookup_and_cards_match_its_invariant(world, monkeypatch):
    _memory_facts(world)
    resolved = _resolve(world)
    trees = resolved.trees
    assert trees is not None and resolved.contract is not None
    monkeypatch.setattr(
        review_tree_knowledge, "resolve_tree_comparison", lambda *_: (resolved.contract, trees)
    )
    query = ReviewTreesQuery(REPO, MASTER, LEAF, entry="RLZ-A00005", entry_side="after")
    actual = review_tree_knowledge.read_review_trees(world.config, query)
    expected = review_tree_entries.tree_entries(trees, (text_uuid("identity", SECOND),))
    assert actual.state == "trees" and actual.entries == expected.entries
    assert actual.entry_memory == expected.memory
    assert {entry.invariant for entry in actual.entries} == {SECOND}
    assert actual.worklist is None and actual.currentness is None
    assert trees.after.database is not None
    with KnowledgeIndex(trees.after.database) as index:
        statements = []
        index._connection.set_exec_trace(
            lambda cursor, statement, bindings: statements.append(statement) or True
        )
        found = index.entry("RLZ-A00005")
        assert found.value is not None and found.value.invariant == SECOND
        assert len(statements) == 1 and "WHERE id = ?" in statements[0]


def test_entry_does_not_search_other_side_or_read_all_cards_when_owner_missing(world):
    _memory_facts(world)
    trees = _resolve(world).trees
    assert trees is not None
    with mock.patch.object(
        review_tree_entries, "tree_entries", side_effect=AssertionError("unexpected cards")
    ):
        cases: tuple[tuple[str, Literal["before", "after"]], ...] = (
            ("RLZ-A00005", "before"),
            ("RLZ-UNKNOWN", "after"),
        )
        for entry_id, side in cases:
            answer = review_tree_entries.tree_entry_cards(trees, entry_id, side)
            assert isinstance(answer, ReviewRefusal)
            assert answer.code == "subject_unresolved"
            assert "not recorded" in answer.detail
        missing = replace(trees, after=replace(trees.after, database=None))
        answer = review_tree_entries.tree_entry_cards(missing, "RLZ-A00001", "after")
        assert isinstance(answer, ReviewRefusal)
        assert answer.code == "subject_unresolved" and "unavailable" in answer.detail
    before = review_tree_entries.tree_entry_cards(trees, "RLZ-A00002", "before")
    assert isinstance(before, review_tree_entries.CardEntries)
    assert {entry.invariant for entry in before.entries} == {INVARIANT}


def test_entry_selectors_have_distinct_recorded_cache_keys():
    original = ReviewTreesQuery(
        REPO, MASTER, LEAF, number=3, entry="RLZ-A00001", entry_side="before"
    )
    questions = [
        original,
        replace(original, entry="RLZ-A00002"),
        replace(original, entry_side="after"),
        replace(original, number=4),
        replace(original, entry=None, entry_side=None, invariants=("invariant",)),
    ]
    assert len({_tree_key(question) for question in questions}) == len(questions)


def test_entry_selection_uses_the_named_side_when_ownership_changes(world: World) -> None:
    _memory_facts(world)
    path = world.memory_worktree / f"onboarding/{CODE_FILE}.json"
    sidecar = json.loads(path.read_text())
    next(entry for entry in sidecar["realizes"] if entry["id"] == "RLZ-A00001")["invariant"] = (
        SECOND
    )
    path.write_text(canonical_text(sidecar))
    trees = _resolve(world).trees
    assert trees is not None
    cases: tuple[tuple[Literal["before", "after"], str], ...] = (
        ("before", INVARIANT),
        ("after", SECOND),
    )
    for side, owner in cases:
        cards = review_tree_entries.tree_entry_cards(trees, "RLZ-A00001", side)
        assert isinstance(cards, review_tree_entries.CardEntries)
        assert {entry.invariant for entry in cards.entries} == {owner}


def test_entry_selection_does_not_treat_a_partial_index_as_proven_absence(world: World) -> None:
    world.edit()
    _write(world.memory_worktree, {"knowledge/invariants/INV-BBBBBB-broken.json": "{}\n"})
    trees = _resolve(world).trees
    assert trees is not None
    with mock.patch.object(
        review_tree_entries, "tree_entries", side_effect=AssertionError("unexpected cards")
    ):
        answer = review_tree_entries.tree_entry_cards(trees, "RLZ-UNKNOWN", "after")
    assert isinstance(answer, ReviewRefusal)
    assert answer.code == "subject_unresolved" and "partial" in answer.detail


def test_entry_inventory_matches_all_cards_without_constructing_detail(world: World) -> None:
    _memory_facts(world)
    trees = _resolve(world).trees
    assert trees is not None
    cards = review_tree_entries.tree_entries(
        trees, (text_uuid("identity", INVARIANT), text_uuid("identity", SECOND))
    )
    with (
        mock.patch.object(review_tree_entries, "_excerpt", side_effect=AssertionError("excerpt")),
        mock.patch.object(
            review_tree_entries, "_memory_values", side_effect=AssertionError("metrics")
        ),
        mock.patch.object(KnowledgeIndex, "invariant", side_effect=AssertionError("full bundle")),
    ):
        inventory = review_tree_entries.tree_entry_inventory(trees)
    assert inventory.state == "ready" and inventory.reason is None
    assert [entry.id for entry in inventory.entries] == [entry.id for entry in cards.entries]
    assert Counter(entry.change for entry in inventory.entries) == Counter(
        entry.change for entry in cards.entries
    )
    assert set(entry.change for entry in inventory.entries) == {
        "changed",
        "unchanged",
        "undetermined",
    }
    for light, full in zip(inventory.entries, cards.entries, strict=True):
        assert (light.kind, light.invariant_key, light.change) == (
            full.kind,
            full.invariant_key,
            full.change,
        )
        assert light.memory is None
        assert light.line_changes == full.line_changes
        for side in ("before", "after"):
            light_side, full_side = getattr(light, side), getattr(full, side)
            assert light_side.excerpt is light_side.rationale is light_side.facet is None
            assert light_side.model_dump(
                exclude={"excerpt", "excerpt_truncated", "rationale", "facet"}
            ) == full_side.model_dump(
                exclude={"excerpt", "excerpt_truncated", "rationale", "facet"}
            )


def test_entry_inventory_unavailable_sides_never_claim_zero_counts(world: World) -> None:
    world.edit()
    _write(world.memory_worktree, {"knowledge/invariants/INV-BBBBBB-broken.json": "{}\n"})
    trees = _resolve(world).trees
    assert trees is not None
    for comparison in (trees, replace(trees, after=replace(trees.after, database=None))):
        inventory = review_tree_entries.tree_entry_inventory(comparison)
        assert inventory.state == "unavailable" and inventory.reason
        assert inventory.entries == ()


def test_main_tree_answer_contains_inventory_and_focused_answer_stays_unchanged(
    world: World,
) -> None:
    _memory_facts(world)
    main = read_review_trees(world.config, _query())
    assert main.state == "trees" and main.entry_inventory is not None
    assert main.entry_inventory.state == "ready" and main.entry_inventory.entries
    assert main.entries == () and main.entry_memory is None
    focused = read_review_trees(
        world.config, _query(invariants=(text_uuid("identity", INVARIANT),))
    )
    assert focused.entry_inventory is None
    assert {entry.invariant for entry in focused.entries} == {INVARIANT}


def test_line_changes_matches_shortest_line_edits_and_frontend_text_semantics() -> None:
    for old, new, expected in (
        ("a\nb\nc\n", "a\nx\nc\nd\n", (2, 1)),
        ("a\n", "a", (0, 0)),
        ("a\r\n", "a\n", (1, 1)),
        ("a\n\n", "a\n", (0, 1)),
    ):
        measured = line_changes(old, new)
        assert measured is not None
        assert (measured.added, measured.removed) == expected
    sequences = [tuple(p) for size in range(4) for p in product(("a", "b"), repeat=size)]
    for before in sequences:
        for after in sequences:
            # Independent dynamic-programming LCS oracle; repeated lines exercise Myers ties.
            lengths = [[0] * (len(after) + 1) for _ in range(len(before) + 1)]
            for row, old in enumerate(before, 1):
                for col, new in enumerate(after, 1):
                    lengths[row][col] = (
                        lengths[row - 1][col - 1] + 1
                        if old == new
                        else max(lengths[row - 1][col], lengths[row][col - 1])
                    )
            measured = line_changes("\n".join(before), "\n".join(after))
            assert measured is not None
            common = lengths[-1][-1]
            assert (measured.added, measured.removed) == (
                len(after) - common,
                len(before) - common,
            )


def test_line_changes_retains_the_edit_walk_ceiling_without_an_excerpt_limit() -> None:
    assert line_changes("a\n" * 4002, "b\n") is None
    # Removing a common prefix/tail can prove a large pure deletion without an edit walk.
    measured = line_changes("a\n" * 5000, "")
    assert measured is not None and (measured.added, measured.removed) == (0, 5000)


def test_line_changes_counts_beyond_the_excerpt_from_exact_resolved_ranges(world: World) -> None:
    before_text = "".join(f"line {number}\n" for number in range(1, 696))
    after_text = "".join(f"line {number}\n" for number in range(1, 696) if number < 690)
    old_commit = commit(world.code_worktree, {CODE_FILE: before_text})
    new_commit = commit(world.code_worktree, {CODE_FILE: after_text})

    def side(revision: str) -> Any:
        tree = git(world.code_worktree, "rev-parse", f"{revision}^{{tree}}")
        code = open_code_tree(CodeTree(world.code_worktree, tree))
        assert code.trees is not None
        blob = code.files[CODE_FILE]
        resolved = code.trees.resolve(CODE_FILE, {"kind": "file"}, blob, blob)
        assert resolved is not None
        entry = Entry(
            "RLZ-A00001",
            "realization",
            INVARIANT,
            CODE_FILE,
            f"onboarding/{CODE_FILE}.json",
            {"anchor": {"locator": {"kind": "file"}, "blob": blob, "content": resolved.content}},
        )
        return review_tree_entries._Side({entry.id: entry}, None, code, True)

    before, after = side(old_commit), side(new_commit)
    light = review_tree_entries._entry("RLZ-A00001", before, after, {}, details=False)
    full = review_tree_entries._entry("RLZ-A00001", before, after, {})
    assert full.before.excerpt_truncated and full.after.excerpt_truncated
    assert full.before.excerpt == full.after.excerpt  # all six removals are after line 400
    assert full.before.end_line == 695 and full.after.end_line == 689
    assert light.before.excerpt is light.after.excerpt is None
    assert light.line_changes == full.line_changes
    assert light.line_changes is not None
    assert light.line_changes.model_dump() == {"added": 0, "removed": 6}


def test_line_changes_leaves_unresolved_unreadable_or_nontext_ranges_unknown() -> None:
    code = mock.Mock(spec=OpenedCodeTree)
    code.trees = mock.Mock(spec=CodeTrees)
    code.trees._blob.return_value = b"old\nfirst\nlast\n"
    old = ReviewTreeEntrySide(
        recorded=True,
        state="resolved",
        path=CODE_FILE,
        blob="a" * 40,
        start_line=2,
        end_line=2,
        content="old",
    )
    new = old.model_copy(update={"content": "new", "start_line": 3, "end_line": 3})
    measured = review_tree_entries._line_changes(old, new, code, code)
    assert measured is not None and (measured.added, measured.removed) == (1, 1)
    for state in ("unresolved", "unavailable"):
        assert (
            review_tree_entries._line_changes(
                old.model_copy(update={"state": state}), new, code, code
            )
            is None
        )
    code.trees._blob.return_value = b"\xff\n"
    binary_old = old.model_copy(update={"start_line": 1, "end_line": 1})
    binary_new = new.model_copy(update={"start_line": 1, "end_line": 1})
    assert review_tree_entries._line_changes(binary_old, binary_new, code, code) is None
    code.trees._blob.side_effect = CodeReadError("unreadable")
    assert review_tree_entries._line_changes(old, new, code, code) is None
    unchanged = review_tree_entries._line_changes(old, old, code, code)
    assert unchanged is not None and (unchanged.added, unchanged.removed) == (0, 0)


@pytest.fixture
def _prefetch_answers() -> Iterator[None]:
    review_tree_entries.PLACEMENTS.clear()
    OBSERVATIONS.clear()
    yield
    review_tree_entries.PLACEMENTS.clear()
    OBSERVATIONS.clear()


def _prefetch_sides(
    repository: Path, before: str, after: str
) -> tuple[review_tree_entries._Side, review_tree_entries._Side]:
    result = []
    for tree, parity in ((before, 0), (after, 1)):
        code = open_code_tree(CodeTree(repository, tree))
        assert code.trees is not None
        entries = {}
        for index in range(parity, 12, 2):
            path = f"part-{index}.py"
            blob = code.files[path]
            data = code.trees.objects.blob(blob)
            identifier = f"RLZ-A{index:05d}"
            entries[identifier] = Entry(
                identifier,
                "realization",
                "INV-AAAAAA",
                path,
                f"onboarding/{path}.json",
                {
                    "anchor": {
                        "blob": blob,
                        "locator": {"kind": "file"},
                        "content": content_identity(data),
                    }
                },
            )
        # Build the input without leaving its blob reader populated before measurement.
        result.append(
            review_tree_entries._Side(
                entries, None, open_code_tree(CodeTree(repository, tree)), True
            )
        )
    return result[0], result[1]


@pytest.mark.parametrize("fault", [None, "missing", "corrupt"])
def test_exact_path_warm_preserves_full_native_inventory_and_failed_blobs(
    tmp_path: Path,
    fault: str | None,
    _prefetch_answers: None,
) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    before = commit(tmp_path, {f"part-{i}.py": f"value_{i} = {i}\n" for i in range(12)})
    after = commit(tmp_path, {f"part-{i}.py": f"value_{i} = {i + 1}\n" for i in range(12)})
    before_side, after_side = _prefetch_sides(tmp_path, before, after)
    if fault:
        blob = after_side.code.files["part-0.py"]
        object_path = tmp_path / ".git" / "objects" / blob[:2] / blob[2:]
        if fault == "missing":
            object_path.unlink()
        else:
            object_path.chmod(0o600)
            object_path.write_bytes(b"corrupt object")

    def read(warm: bool) -> tuple[bytes, int]:
        review_tree_entries.PLACEMENTS.clear()
        OBSERVATIONS.clear()
        # A new object owner and request scope on each arm, just as a fresh native call.
        current = tuple(
            review_tree_entries._Side(
                side.entries, None, open_code_tree(CodeTree(tmp_path, tree)), True
            )
            for side, tree in ((before_side, before), (after_side, after))
        )
        with (
            memo_git_object_lookups(),
            shared_blob_reads(),
            mock.patch.object(git_command, "_batch_blobs", wraps=git_command._batch_blobs) as batch,
            mock.patch.object(
                review_tree_entries, "_comparison_sides", return_value=(*current, None)
            ),
            mock.patch.object(CodeTrees, "warm", CodeTrees.warm if warm else lambda *_: None),
        ):
            answer = review_tree_entries.tree_entry_inventory(mock.Mock())
        assert answer.state == "ready" and len(answer.entries) == 12
        if fault:
            failed = next(entry for entry in answer.entries if entry.after.path == "part-0.py")
            assert failed.after.state == "unavailable" and failed.change == "undetermined"
        return answer.model_dump_json().encode(), batch.call_count

    plain, plain_calls = read(False)
    warmed, warmed_calls = read(True)
    assert warmed == plain
    if fault is None:
        assert (plain_calls, warmed_calls) == (24, 2)
