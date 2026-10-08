"""Unchanged context is admitted by the exact memory trees of the requested source pair.

These cases use the converted World enclosure and public HTTP routes. Each listing persists the
actual four-tree comparison; no canonical freeze or dataset snapshot participates in admission.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import apsw
import pytest
from agents_remember.application.knowledge_read import open_read_context, read_knowledge_scope
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.review_candidate_resolution import review_namespace
from agents_remember.application.review_source_realization_link import _proof_at_path
from agents_remember.memory.knowledge import read as read_selection
from agents_remember.models.knowledge.read import KnowledgeReadRequest, PathSeed
from agents_remember.models.knowledge.review_source_content import ReviewSourceExpansion
from agents_remember.models.knowledge_files import canonical_text
from fastapi.testclient import TestClient
from test_knowledge_review_source_content import _blob_text, _object_id
from test_knowledge_review_source_endpoints import _public_resolution, _source_app, _source_params
from test_review_git_trees import CODE_FILE, CODE_V1, INVARIANT, World, build_world, commit, git

pytestmark = pytest.mark.evidence_unit

ATTRIBUTED_PATH = "pkg/context.py"
BEFORE_ONLY_PATH = "pkg/before.py"
UNRELATED_PATH = "pkg/unrelated.py"
_PADDINGS = (" {}", "{} ", "\t{}", "{}\n")
_NOT_LINKED = "no realization recorded in the comparison's knowledge links it"


@pytest.fixture
def attributed_fixture(tmp_path: Path) -> World:
    world = build_world(tmp_path / "attributed")
    world.code_base = commit(
        world.code, {path: CODE_V1 for path in (ATTRIBUTED_PATH, BEFORE_ONLY_PATH, UNRELATED_PATH)}
    )
    trees = CodeTrees.open(world.code, world.code_base, world.code_base)
    files: dict[str, str] = {}
    for index, path in enumerate((ATTRIBUTED_PATH, BEFORE_ONLY_PATH)):
        blob = trees.base()[path]
        entries = []
        for offset, name in enumerate(("land", "keep")):
            locator = {"kind": "symbol", "name": name}
            anchor = trees.resolve(path, locator, blob, blob)
            assert anchor is not None
            entries.append(
                {
                    "id": f"RLZ-A{10 + index * 2 + offset:05}",
                    "invariant": INVARIANT,
                    "anchor": {"locator": locator, "blob": blob, "content": anchor.content},
                    "role": "primary-authority",
                    "rationale": "The rule holds here.",
                }
            )
        files[f"onboarding/{path}.md"] = "# source context\n"
        files[f"onboarding/{path}.json"] = canonical_text(
            {"schema": "ar-onboarding-file/v1", "path": path, "references": {}, "realizes": entries}
        )
    world.memory_base = commit(world.memory, files, trailer=world.code_base)
    git(world.code_worktree, "reset", "--hard", world.code_base)
    git(world.memory_worktree, "reset", "--hard", world.memory_base)
    world.contract()
    world.edit()
    (world.memory_worktree / f"onboarding/{BEFORE_ONLY_PATH}.json").unlink()
    return world


def _listed_inventory(world: World) -> dict:
    with TestClient(_source_app(world)) as client:
        response = client.get("/api/review/intent", params=_source_params())
    assert response.status_code == 200, response.text
    return response.json()["payload"]["source"]["inventory"]


def _expand(world: World, path: str, *, before: str, after: str) -> dict:
    with TestClient(_source_app(world)) as client:
        response = client.get(
            "/api/review/intent/source-content",
            params={
                **_source_params(),
                "path": path,
                "beforeCodeTreeId": before,
                "afterCodeTreeId": after,
            },
        )
    return {**response.json(), "_status": response.status_code}


def _pair(inventory: dict) -> dict[str, str]:
    return {"before": inventory["before_code_tree_id"], "after": inventory["after_code_tree_id"]}


def _refused(body: dict, path: str) -> str:
    assert body["_status"] == 400 and body["state"] == "refused", body
    assert body["refusal"]["code"] == "source_content_unresolved" and "expansion" not in body
    detail = body["refusal"]["detail"]
    assert path in detail
    return detail


def _attributed(body: dict) -> dict:
    assert body["_status"] == 200, body
    expansion = body["expansion"]
    ReviewSourceExpansion.model_validate(expansion)
    assert expansion["admission"] == "attributed_unchanged" and expansion["status"] == "unchanged"
    assert expansion["path_bound"] == "requested_generation"
    return expansion


def test_an_unchanged_path_a_recorded_realization_links_opens_as_context_without_counting(
    attributed_fixture: World,
) -> None:
    world = attributed_fixture
    inventory = _listed_inventory(world)
    assert {ATTRIBUTED_PATH, BEFORE_ONLY_PATH}.isdisjoint(
        entry["path"] for entry in inventory["entries"]
    )
    for path, sides in ((ATTRIBUTED_PATH, "before and after"), (BEFORE_ONLY_PATH, "before")):
        expansion = _attributed(_expand(world, path, **_pair(inventory)))
        blob = _object_id(world.code, inventory["before_code_tree_id"], path)
        assert (
            blob is not None
            and _object_id(world.code, inventory["after_code_tree_id"], path) == blob
        )
        text = _blob_text(world.code, inventory["before_code_tree_id"], path)
        for side in ("before", "after"):
            assert expansion[side]["state"] == "present"
            assert expansion[side]["object_id"] == blob and expansion[side]["text"] == text
        assert f"comparison's {sides} knowledge" in expansion["admission_detail"]
        assert expansion["currentness"] == "current"
    assert _listed_inventory(world) == inventory


def test_an_unchanged_path_no_recorded_realization_links_is_still_refused(
    attributed_fixture: World,
) -> None:
    inventory = _listed_inventory(attributed_fixture)
    assert UNRELATED_PATH not in {entry["path"] for entry in inventory["entries"]}
    detail = _refused(
        _expand(attributed_fixture, UNRELATED_PATH, **_pair(inventory)), UNRELATED_PATH
    )
    assert (
        "the before snapshot records none" in detail and "the after snapshot records none" in detail
    )


def test_a_path_linked_only_in_another_comparison_is_refused_for_this_one(
    attributed_fixture: World,
) -> None:
    world = attributed_fixture
    first = _listed_inventory(world)
    (world.memory / f"onboarding/{BEFORE_ONLY_PATH}.json").unlink()
    world.memory_base = commit(world.memory, {}, trailer=world.code_base)
    commit(world.code_worktree, {CODE_FILE: "# a later comparison\n"})
    current = _listed_inventory(world)
    assert current["after_code_tree_id"] != first["after_code_tree_id"]
    detail = _refused(_expand(world, BEFORE_ONLY_PATH, **_pair(current)), BEFORE_ONLY_PATH)
    assert (
        "the before snapshot records none" in detail and "the after snapshot records none" in detail
    )
    earlier = _attributed(_expand(world, BEFORE_ONLY_PATH, **_pair(first)))
    assert "comparison 1" in earlier["admission_detail"] and earlier["currentness"] == "superseded"


def test_after_the_live_tree_moves_the_listed_pair_keeps_its_exact_attributed_bytes(
    attributed_fixture: World,
) -> None:
    world = attributed_fixture
    first = _listed_inventory(world)
    # Capture an intermediate source endpoint without listing it: no tree comparison records it.
    commit(world.code_worktree, {CODE_FILE: "# never listed comparison\n"})
    unrecorded_tree = git(world.code_worktree, "rev-parse", "HEAD^{tree}")
    moved = "# context rewritten after the first listing\n"
    commit(world.code_worktree, {ATTRIBUTED_PATH: moved})
    third = _listed_inventory(world)
    assert len({first["after_code_tree_id"], unrecorded_tree, third["after_code_tree_id"]}) == 3
    historical = _attributed(_expand(world, ATTRIBUTED_PATH, **_pair(first)))
    original = _blob_text(world.code, first["after_code_tree_id"], ATTRIBUTED_PATH)
    assert (
        original != moved
        and historical["before"]["text"] == historical["after"]["text"] == original
    )
    assert historical["after_code_tree_id"] == first["after_code_tree_id"]
    assert (
        historical["currentness"] == "superseded"
        and "comparison 1" in historical["admission_detail"]
    )
    detail = _refused(
        _expand(world, ATTRIBUTED_PATH, before=first["before_code_tree_id"], after=unrecorded_tree),
        ATTRIBUTED_PATH,
    )
    assert (
        "no comparison generation this leaf published records exactly the requested pair" in detail
    )
    changed = _expand(world, ATTRIBUTED_PATH, **_pair(third))
    assert changed["_status"] == 200 and changed["expansion"]["admission"] == "changed"
    assert (
        changed["expansion"]["status"] == "modified"
        and changed["expansion"]["after"]["text"] == moved
    )
    assert ATTRIBUTED_PATH in {entry["path"] for entry in third["entries"]}


def test_a_closed_leaf_opens_its_attributed_path_from_the_retained_generation(
    attributed_fixture: World,
) -> None:
    world = attributed_fixture
    inventory = _listed_inventory(world)
    world.contract(live=False)
    shutil.rmtree(world.root / "group")
    expansion = _attributed(_expand(world, ATTRIBUTED_PATH, **_pair(inventory)))
    assert expansion["after"]["text"] == _blob_text(
        world.code, inventory["after_code_tree_id"], ATTRIBUTED_PATH
    )
    assert expansion["currentness"] == "current"
    changed = _expand(world, CODE_FILE, **_pair(inventory))
    assert changed["_status"] == 200 and changed["expansion"]["admission"] == "changed"
    _refused(_expand(world, UNRELATED_PATH, **_pair(inventory)), UNRELATED_PATH)


def test_a_padded_spelling_is_never_admitted_as_attributed_context(
    attributed_fixture: World,
) -> None:
    world = attributed_fixture
    inventory = _listed_inventory(world)
    for path in (ATTRIBUTED_PATH, UNRELATED_PATH, CODE_FILE):
        for padding in _PADDINGS:
            spelling = padding.format(path)
            detail = _refused(_expand(world, spelling, **_pair(inventory)), spelling.strip())
            assert "not exactly a path a recorded source anchor can carry" in detail
    assert (
        _attributed(_expand(world, ATTRIBUTED_PATH, **_pair(inventory)))["path"] == ATTRIBUTED_PATH
    )
    changed = _expand(world, CODE_FILE, **_pair(inventory))
    assert changed["_status"] == 200 and changed["expansion"]["admission"] == "changed"


def test_a_selection_bound_below_the_scope_cannot_refuse_a_linked_path(
    attributed_fixture: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = attributed_fixture
    inventory = _listed_inventory(world)
    monkeypatch.setattr(read_selection, "SELECTION_ITEM_LIMIT", 1)
    resolved = _public_resolution(world)
    database = resolved.candidate_database
    namespace = review_namespace(resolved.repository_id, database)
    scope = read_knowledge_scope(
        database,
        open_read_context(database, namespace),
        KnowledgeReadRequest(seed=PathSeed(path=ATTRIBUTED_PATH)),
    )
    assert scope.refusal is not None and scope.refusal.code == "selection_incomplete", scope
    assert (
        "comparison's before and after knowledge"
        in _attributed(_expand(world, ATTRIBUTED_PATH, **_pair(inventory)))["admission_detail"]
    )


def test_an_unreadable_snapshot_leaves_the_link_undetermined_rather_than_absent(
    attributed_fixture: World,
) -> None:
    world = attributed_fixture
    inventory = _listed_inventory(world)
    resolved = _public_resolution(world)
    assert resolved.trees is not None
    world.contract(live=False)
    tree = resolved.trees.record.memory_candidate.tree
    # Release the actual memory pin and let Git reclaim that candidate after enclosure cleanup.
    ref = resolved.trees.record.memory_candidate.ref
    assert ref is not None
    git(world.memory, "update-ref", "-d", ref)
    git(world.memory, "worktree", "remove", "--force", str(world.memory_worktree))
    git(world.memory, "reflog", "expire", "--expire=now", "--all")
    git(world.memory, "gc", "-q", "--prune=now")
    linked = _attributed(_expand(world, BEFORE_ONLY_PATH, **_pair(inventory)))
    assert "comparison's before knowledge" in linked["admission_detail"]
    body = _expand(world, UNRELATED_PATH, **_pair(inventory))
    detail = _refused(body, UNRELATED_PATH)
    assert "could not be determined" in detail and _NOT_LINKED not in detail
    assert "the before snapshot records none" in detail
    assert tree in detail and "unavailable" in detail
    assert "restore" in body["refusal"]["next_action"] or "repair" in body["refusal"]["next_action"]


def test_unconverted_knowledge_names_its_unavailability_without_canonical_initialization(
    attributed_fixture: World,
) -> None:
    world = attributed_fixture
    for root in (world.memory, world.memory_worktree):
        git(root, "rm", "-q", "-r", "-f", "knowledge", "onboarding")
        git(root, "commit", "-q", "-m", "unconverted memory")
    inventory = _listed_inventory(world)
    body = _expand(world, UNRELATED_PATH, **_pair(inventory))
    detail = _refused(body, UNRELATED_PATH)
    assert "could not be determined" in detail and "legacy-unavailable" in detail
    assert "initialize" not in body["refusal"]["next_action"]
    assert (
        "recorded" in body["refusal"]["next_action"] or "source" in body["refusal"]["next_action"]
    )


def test_a_tree_index_links_a_path_its_proof_entry_is_anchored_at() -> None:
    index = apsw.Connection(":memory:")
    index.execute(
        "CREATE TABLE ix_entry (id TEXT, kind TEXT, invariant TEXT, path TEXT, sidecar TEXT, document TEXT)"
    )
    index.execute(
        "INSERT INTO ix_entry VALUES ('PRF-AAAAAA', 'proof', 'INV-AAAAAA', 'tests/t.py', 's', '{}')"
    )
    assert _proof_at_path(index, "tests/t.py")
    assert not _proof_at_path(index, "tests/other.py")
    assert not _proof_at_path(apsw.Connection(":memory:"), "tests/t.py")
