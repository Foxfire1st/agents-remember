"""The existing read, view and comparison code runs over the index unchanged (MIK-R23 rule 6).

* **Parity.** A legacy database fixture and the tree the fixture converter writes from it are the
  same graph. The reused recorded-scope selection must select the same set over the index as over
  the database, seed by seed, once both sides' UUIDs are translated to the legacy identities the
  converted records carry in ``origin.legacyId``.
* **Views and comparison.** The view seam and the two-snapshot comparison open the index file as
  their dataset.
* **Published intent.** The ordinary read's knowledge section selects a converted memory tree and
  reads it through the index; an unconverted memory root names the conversion prerequisite.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import apsw
import pytest
from agents_remember.application.knowledge_diff import diff_knowledge_scope, open_diff_side
from agents_remember.application.knowledge_read import open_read_context
from agents_remember.application.knowledge_views import read_knowledge_view
from agents_remember.application.published_intent import published_intent_block
from agents_remember.kernel.coordination_context.models import CoordinationContext
from agents_remember.memory.knowledge.read import SelectionQuery, select_recorded_scope
from agents_remember.memory.knowledge_index import (
    KnowledgeIndex,
    KnowledgeIndexCache,
    directory_key,
    text_uuid,
)
from agents_remember.models.knowledge.diff import KnowledgeDiffRequest, KnowledgeDiffSide
from agents_remember.models.knowledge.read import (
    FamilyIdentitySeed,
    InvariantIdentitySeed,
    KnowledgeReadSeed,
    PathSeed,
    ReadItem,
)
from agents_remember.models.knowledge.view import ViewRequest
from knowledge_index_test_support import (
    FAMILY,
    PARITY_PATHS,
    REVIEW_INVARIANT,
    REVIEW_PATH,
    SIBLING_PATHS,
    build_parity_dataset,
    commit_all,
    convert_dataset,
    init_repository,
    write_review_tree,
)

# --- parity with the database -----------------------------------------------------------------


def _legacy_locator(text: str) -> str:
    locator = json.loads(text)
    return json.dumps(locator, sort_keys=True)


def _database_key(connection: apsw.Connection, item: ReadItem) -> tuple[str, ...]:
    def one(statement: str, value: str | None) -> str:
        row = next(iter(connection.execute(statement, (value,))))
        return str(row[0])

    identity_of_revision = "SELECT invariant_id FROM invariant_revision WHERE revision_id = ?"
    family_of_revision = "SELECT family_id FROM family_revision WHERE revision_id = ?"
    if item.kind == "invariant_revision":
        return ("invariant", str(item.invariant_id), str(item.statement))
    if item.kind == "family_revision":
        return ("family", str(item.family_id), str(item.joint_guarantee))
    if item.kind in {"family_membership", "advertised_family"}:
        return (
            item.kind,
            one(family_of_revision, item.family_revision_id),
            one(identity_of_revision, item.invariant_revision_id),
        )
    row = next(
        iter(
            connection.execute(
                "SELECT a.path, a.locator FROM realization_claim c JOIN source_anchor a "
                "ON a.anchor_id = c.anchor_id WHERE c.claim_id = ?",
                (item.claim_id,),
            )
        )
    )
    return (
        "claim",
        one(identity_of_revision, item.invariant_revision_id),
        str(row[0]),
        _legacy_locator(str(row[1])),
        str(item.role),
        str(item.rationale),
    )


def _index_key(
    index: KnowledgeIndex, connection: apsw.Connection, item: ReadItem
) -> tuple[str, ...]:
    def legacy(projected: str | None) -> str:
        text_id = index.text_id(str(projected))
        assert text_id is not None
        record = index.record(text_id.split("@")[0]).value
        assert record is not None
        return str(record.document["origin"]["legacyId"])

    if item.kind == "invariant_revision":
        return ("invariant", legacy(item.invariant_id), str(item.statement))
    if item.kind == "family_revision":
        return ("family", legacy(item.family_id), str(item.joint_guarantee))
    if item.kind in {"family_membership", "advertised_family"}:
        return (item.kind, legacy(item.family_revision_id), legacy(item.invariant_revision_id))
    row = next(
        iter(
            connection.execute(
                "SELECT a.path, a.locator FROM realization_claim c JOIN source_anchor a "
                "ON a.anchor_id = c.anchor_id WHERE c.claim_id = ?",
                (item.claim_id,),
            )
        )
    )
    return (
        "claim",
        legacy(item.invariant_revision_id),
        str(row[0]),
        _legacy_locator(str(row[1])),
        str(item.role),
        str(item.rationale),
    )


def _selection(connection: apsw.Connection, repository_id: str, seed: KnowledgeReadSeed) -> Any:
    return select_recorded_scope(
        connection,
        SelectionQuery(repository_id=repository_id, seed=seed, resolve_anchor=lambda _row: None),
    )


def test_the_reused_selection_selects_the_same_set_over_the_index_as_over_the_database(
    tmp_path: Path,
) -> None:
    database, repository_id = build_parity_dataset(tmp_path / "dataset")
    tree = tmp_path / "memory"
    init_repository(tree)
    new_ids = convert_dataset(database, tree)
    commit_all(tree)
    legacy_of = {new: legacy for legacy, new in new_ids.items()}

    database_connection = apsw.Connection(str(database), flags=apsw.SQLITE_OPEN_READONLY)
    cache = KnowledgeIndexCache(tmp_path / "cache")
    with cache.for_git_tree(tree, "HEAD") as index:
        assert index.state.complete
        index_connection = apsw.Connection(
            str(index.database_path), flags=apsw.SQLITE_OPEN_READONLY
        )
        invariants = [
            str(row[0]) for row in database_connection.execute("SELECT invariant_id FROM invariant")
        ]
        families = [
            str(row[0]) for row in database_connection.execute("SELECT family_id FROM family")
        ]
        seed_pairs: list[tuple[KnowledgeReadSeed, KnowledgeReadSeed]] = [
            (PathSeed(path=path), PathSeed(path=path)) for path in PARITY_PATHS
        ]
        seed_pairs += [
            (
                InvariantIdentitySeed(invariant_id=legacy),
                InvariantIdentitySeed(invariant_id=text_uuid("identity", new_ids[legacy])),
            )
            for legacy in invariants
        ]
        seed_pairs += [
            (
                FamilyIdentitySeed(family_id=legacy),
                FamilyIdentitySeed(family_id=text_uuid("identity", new_ids[legacy])),
            )
            for legacy in families
        ]
        compared = 0
        for database_seed, index_seed in seed_pairs:
            over_database = _selection(database_connection, repository_id, database_seed)
            over_index = _selection(index_connection, index.repository_id, index_seed)
            expected = sorted(
                _database_key(database_connection, item) for item in over_database.items
            )
            observed = sorted(
                _index_key(index, index_connection, item) for item in over_index.items
            )
            assert observed == expected, database_seed
            assert over_index.counts.distinct_source_paths_total == (
                over_database.counts.distinct_source_paths_total
            )
            compared += len(expected)
        index_connection.close()
    database_connection.close()
    # The comparison is not vacuous: the stopping rule's frontier and all four claim paths appear.
    assert compared > 40
    assert set(legacy_of) >= set(new_ids.values())


# --- views and comparison over the index ------------------------------------------------------


@pytest.fixture
def review_tree(tmp_path: Path) -> Path:
    root = tmp_path / "memory"
    init_repository(root)
    write_review_tree(root)
    commit_all(root)
    return root


def test_the_views_read_the_index_as_their_dataset(review_tree: Path, tmp_path: Path) -> None:
    cache = KnowledgeIndexCache(tmp_path / "cache")
    with cache.for_directory(review_tree) as index:
        context = open_read_context(index.database_path, index.repository_id)
        requests = (
            ViewRequest(
                view="family",
                repository_id=index.repository_id,
                family_revision_id=text_uuid("revision", f"{FAMILY}@1"),
            ),
            ViewRequest(
                view="source_context", repository_id=index.repository_id, source_path=REVIEW_PATH
            ),
            ViewRequest(
                view="invariant",
                repository_id=index.repository_id,
                invariant_revision_id=text_uuid("revision", f"{REVIEW_INVARIANT}@1"),
            ),
        )
        results = [
            read_knowledge_view(index.database_path, context, request) for request in requests
        ]
    for result in results:
        assert result.state == "view", result.refusal
    family = results[0].payload
    assert family is not None
    assert family.counts.registered_realizations.value == 5  # type: ignore[union-attr]


def test_a_comparison_reads_two_trees_through_their_indexes(
    review_tree: Path, tmp_path: Path
) -> None:
    cache = KnowledgeIndexCache(tmp_path / "cache")
    target = next((review_tree / "knowledge" / "invariants").glob(f"{REVIEW_INVARIANT}-*.json"))
    document = json.loads(target.read_text("utf-8"))
    document.update(revision=2, statement="A comparison shows every unchanged realization.")
    from agents_remember.models.knowledge_files.canonical import canonical_text  # noqa: PLC0415

    target.write_text(canonical_text(document), encoding="utf-8")
    with (
        cache.for_git_tree(review_tree, "HEAD") as before,
        cache.for_directory(review_tree) as after,
    ):
        assert before.state.key != after.state.key
        seed = InvariantIdentitySeed(invariant_id=text_uuid("identity", REVIEW_INVARIANT))
        request = KnowledgeDiffRequest(
            selector=seed,
            before=KnowledgeDiffSide(
                context=open_diff_side(before.database_path, before.repository_id)
            ),
            after=KnowledgeDiffSide(
                context=open_diff_side(after.database_path, after.repository_id)
            ),
        )
        result = diff_knowledge_scope(
            request, before_path=before.database_path, after_path=after.database_path
        )
    assert result.refusal is None, result.refusal
    assert "every unchanged realization" in result.model_dump_json()


# --- the published memory-tree selection ------------------------------------------------------


def _context(tmp_path: Path, memory_root: Path) -> CoordinationContext:
    code = tmp_path / "code"
    code.mkdir(exist_ok=True)
    return cast(
        CoordinationContext,
        SimpleNamespace(
            memory_root=memory_root,
            coordination_root=tmp_path / "coordination",
            code_repository_root=code,
            code_repository_name="agents-remember",
        ),
    )


def test_the_ordinary_read_selects_a_converted_memory_tree_through_its_index(
    review_tree: Path, tmp_path: Path
) -> None:
    block = published_intent_block(_context(tmp_path, review_tree), [REVIEW_PATH])
    assert block["state"] == "recorded"
    assert block["memoryTree"]["treeId"] == directory_key(review_tree)
    assert block["memoryTree"]["indexState"] == "complete"
    assert (
        Path(block["datasetPath"]).parent
        == tmp_path / "coordination" / "runtime" / "knowledge-index"
    )
    page = block["seeds"][0]
    assert page["state"] == "page"
    assert page["indexState"] == "complete"
    assert page["enumerationComplete"] is (not page["hasMore"])
    statements = {row.get("statement") for row in page["rows"]}  # the leaf read (MIK-R01)
    assert "A comparison shows unchanged realizations beside changed ones." in statements


def test_a_partial_tree_selection_says_it_is_partial(review_tree: Path, tmp_path: Path) -> None:
    (review_tree / "knowledge" / "invariants" / "INV-BRKN01-x.json").write_text("{}", "utf-8")
    block = published_intent_block(_context(tmp_path, review_tree), [REVIEW_PATH])
    assert block["state"] == "recorded"
    assert block["memoryTree"]["indexState"] == "partial"
    assert block["memoryTree"]["problems"][0]["path"] == "knowledge/invariants/INV-BRKN01-x.json"
    # A page read from a partial index is never presented as complete (MIK-R23, Failure).
    page = block["seeds"][0]
    assert page["state"] == "page" and page["indexState"] == "partial"
    assert page["enumerationComplete"] is False


def test_an_unconverted_memory_root_refuses_the_retired_database_selection(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    init_repository(legacy)
    (legacy / "onboarding").mkdir()
    retired = legacy / "knowledge.sqlite"
    retired.write_bytes(b"a retired canonical dataset is not a readable index\n")
    block = published_intent_block(_context(tmp_path, legacy), [SIBLING_PATHS[0]])
    assert block["state"] == "unusable"
    assert block["refusalCode"] == "legacy-format"
    assert "conversion command" in block["refusalDetail"]
    assert "sync it across the text-storage boundary" in block["refusalDetail"]
    assert block["datasetPath"] == str(legacy)
    assert "memoryTree" not in block
    assert block["seeds"] == []
    assert retired.read_bytes() == b"a retired canonical dataset is not a readable index\n"
    assert not (tmp_path / "coordination" / "runtime" / "knowledge-index").exists()
