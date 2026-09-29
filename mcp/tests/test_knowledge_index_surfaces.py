"""The index behind the worklist scope and the mounted knowledge tools (MIK-R23 rule 6).

* **Registered scope.** ``construct_registered_scope`` runs over an index through the adapter and
  constructs the same scope as over the equivalent database fixture.
* **Retired records** are never presented as live: the reused reads do not select them, and the
  index answers them with their ``retired`` status.
* **Mounted tools.** ``knowledge_read``, ``knowledge_diff`` and ``knowledge_project`` resolve a
  ``databasePath`` naming a converted memory tree (its root, or its published ``knowledge.sqlite``
  location) through the tree's index, name the tree and the index state, and keep today's behaviour
  for everything else.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import apsw
import pytest
from agents_remember.application.knowledge_read import open_read_context, read_knowledge_scope
from agents_remember.mcp.tools.knowledge import (
    DiffToolRequest,
    ProjectToolRequest,
    ReadToolRequest,
    knowledge_diff_payload,
    knowledge_project_payload,
    knowledge_read_payload,
)
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.registered_scope import (
    construct_registered_scope,
    snapshot_source,
)
from agents_remember.memory.knowledge.store import open_knowledge_store
from agents_remember.memory.knowledge_index import (
    INDEX_REPOSITORY_ID,
    KnowledgeIndex,
    KnowledgeIndexCache,
    scope_snapshot_declaration,
    scope_snapshot_source,
    text_uuid,
)
from agents_remember.models.knowledge.read import (
    KnowledgeReadBudget,
    KnowledgeReadRequest,
    PathSeed,
)
from agents_remember.models.knowledge.registered_scope import (
    RegisteredScopeManifest,
    RegisteredScopeRequest,
    ScopeSnapshotDeclaration,
)
from agents_remember.models.knowledge_files.canonical import canonical_text
from knowledge_index_test_support import (
    FAMILY,
    PARITY_PATHS,
    REVIEW_INVARIANT,
    REVIEW_PATH,
    SIBLING_INVARIANT,
    add_parity_claim,
    build_parity_dataset,
    commit_all,
    convert_dataset,
    init_repository,
    write_review_tree,
)

# --- the registered scope over the index ------------------------------------------------------


def _canonical(
    connections: tuple[apsw.Connection, ...], value: str, identity: Callable[[str], str]
) -> str:
    """Spell one scope identity by what it records, with record identities made comparable.

    Each side's connection is asked in turn, so a member only one side holds is still spelled.
    """

    def first(statement: str) -> tuple[object, ...] | None:
        for connection in connections:
            row = next(iter(connection.execute(statement, (value,))), None)
            if row is not None:
                return tuple(row)
        return None

    for statement, tag in (
        ("SELECT invariant_id FROM invariant_revision WHERE revision_id = ?", "invariant"),
        ("SELECT family_id FROM family_revision WHERE revision_id = ?", "family"),
    ):
        row = first(statement)
        if row is not None:
            return f"{tag}:{identity(str(row[0]))}"
    row = first("SELECT path, locator FROM source_anchor WHERE anchor_id = ?")
    if row is not None:
        return f"anchor:{row[0]}:{json.dumps(json.loads(str(row[1])), sort_keys=True)}"
    row = first(
        "SELECT r.invariant_id, a.path, a.locator FROM realization_claim c "
        "JOIN source_anchor a ON a.anchor_id = c.anchor_id "
        "JOIN invariant_revision r ON r.revision_id = c.invariant_revision_id WHERE c.claim_id = ?"
    )
    if row is not None:
        locator = json.dumps(json.loads(str(row[2])), sort_keys=True)
        return f"claim:{identity(str(row[0]))}:{row[1]}:{locator}"
    row = first(
        "SELECT f.family_id, r.invariant_id FROM family_member m "
        "JOIN family_revision f ON f.revision_id = m.family_revision_id "
        "JOIN invariant_revision r ON r.revision_id = m.invariant_revision_id WHERE m.member_id = ?"
    )
    if row is not None:
        return f"member:{identity(str(row[0]))}:{identity(str(row[1]))}"
    return value


def _canonical_scope(
    manifest: RegisteredScopeManifest,
    connections: tuple[apsw.Connection, ...],
    identity: Callable[[str], str],
) -> dict[str, list[str]]:
    def spell(value: str) -> str:
        return _canonical(connections, value, identity)

    membership = manifest.membership
    return {
        "invariants": sorted(map(spell, membership.invariant_revision_ids)),
        "families": sorted(map(spell, membership.family_revision_ids)),
        "claims": sorted(map(spell, membership.realization_claim_ids)),
        "anchors": sorted(map(spell, membership.source_anchor_ids)),
        "references": sorted(membership.recorded_reference_refs),
        "edges": sorted(
            f"{edge.edge_kind}|{edge.mapping_side}|{spell(edge.edge_id)}|"
            f"{spell(edge.from_record_id)}|{spell(edge.to_record_id)}"
            for edge in manifest.followed_edges
        ),
    }


def _declaration(side: str, path: Path) -> ScopeSnapshotDeclaration:
    return ScopeSnapshotDeclaration(
        side=side,  # type: ignore[arg-type]
        snapshot=dataset_identity(path),
        selector_policy_version="recorded-family-frontier/v1",
    )


@pytest.mark.parametrize("divergent", [False, True], ids=["same-sides", "candidate-changed"])
def test_the_registered_scope_constructs_the_same_scope_over_the_index(
    tmp_path: Path, divergent: bool
) -> None:
    base_database, repository_id = build_parity_dataset(tmp_path / "dataset")
    candidate_database = base_database
    tree = tmp_path / "memory"
    init_repository(tree)
    convert_dataset(base_database, tree)
    commit_all(tree)
    if divergent:
        # The candidate adds a realization of K at the batch path, which only that side holds: K
        # and its membership join the scope from the candidate side alone.
        candidate_database = tmp_path / "candidate" / "knowledge.sqlite"
        candidate_database.parent.mkdir()
        shutil.copyfile(base_database, candidate_database)
        add_parity_claim(candidate_database, repository_id, "K", PARITY_PATHS[2])
        convert_dataset(candidate_database, tree)
    paths = (PARITY_PATHS[0], PARITY_PATHS[2])
    databases = {"base": base_database, "candidate": candidate_database}
    database_sources = tuple(
        snapshot_source(
            side,  # type: ignore[arg-type]
            databases[side],
            open_knowledge_store(databases[side], repository_id),
        )
        for side in ("base", "candidate")
    )
    over_database = construct_registered_scope(
        RegisteredScopeRequest(
            scope_id="parity",
            repository_id=repository_id,
            snapshots=tuple(_declaration(side, path) for side, path in databases.items()),
            changed_paths=paths,
        ),
        database_sources,
    )
    cache = KnowledgeIndexCache(tmp_path / "cache")
    with cache.for_git_tree(tree, "HEAD") as base, cache.for_directory(tree) as candidate:
        assert (base.state.key != candidate.state.key) is divergent
        index_sources = (
            scope_snapshot_source("base", base),
            scope_snapshot_source("candidate", candidate),
        )
        over_index = construct_registered_scope(
            RegisteredScopeRequest(
                scope_id="parity",
                repository_id=base.repository_id,
                snapshots=(
                    scope_snapshot_declaration("base", base),
                    scope_snapshot_declaration("candidate", candidate),
                ),
                changed_paths=paths,
            ),
            index_sources,
        )
        assert over_index.manifest is not None, over_index.refusal

        def legacy(projected: str) -> str:
            text_id = base.text_id(projected) or candidate.text_id(projected)
            assert text_id is not None
            record = base.record(text_id).value
            assert record is not None
            return str(record.document["origin"]["legacyId"])

        observed = _canonical_scope(
            over_index.manifest, tuple(source.store.connection for source in index_sources), legacy
        )
        for source in index_sources:
            source.store.connection.close()
    assert over_database.manifest is not None, over_database.refusal
    expected = _canonical_scope(
        over_database.manifest,
        tuple(source.store.connection for source in database_sources),
        lambda value: value,
    )
    for source in database_sources:
        source.store.close()
    assert observed == expected
    # Not vacuous: both sides, the claims at both paths, and the memberships they reach.
    assert len(expected["edges"]) >= 10 and len(expected["families"]) == 3
    candidate_edges = [edge for edge in expected["edges"] if "|candidate|" in edge]
    base_edges = [edge for edge in expected["edges"] if "|base|" in edge]
    assert (len(candidate_edges) > len(base_edges)) is divergent


# --- retired records ----------------------------------------------------------------------------


def _retire(root: Path, record_id: str, directory: str) -> None:
    target = next((root / "knowledge" / directory).glob(f"{record_id}-*.json"))
    document = json.loads(target.read_text("utf-8"))
    document["status"] = "retired"
    target.write_text(canonical_text(document), encoding="utf-8")


def test_a_retired_record_is_never_selected_as_live_and_is_answered_as_retired(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    init_repository(root)
    write_review_tree(root)
    _retire(root, REVIEW_INVARIANT, "invariants")
    commit_all(root)
    cache = KnowledgeIndexCache(tmp_path / "cache")
    with cache.for_directory(root) as index:
        context = open_read_context(index.database_path, index.repository_id)
        page = read_knowledge_scope(
            index.database_path,
            context,
            KnowledgeReadRequest(
                seed=PathSeed(path=REVIEW_PATH), budget=KnowledgeReadBudget(max_items=64)
            ),
        ).page
        answer = index.invariant(REVIEW_INVARIANT).value
        family = index.family(FAMILY).value
        projected = {
            str(row[0])
            for row in apsw.Connection(
                str(index.database_path), flags=apsw.SQLITE_OPEN_READONLY
            ).execute("SELECT id FROM ix_uuid")
        }
    assert page is not None
    selected = {index_text for index_text in projected}
    assert f"{REVIEW_INVARIANT}@1" not in selected and "RLZ-RVW001" not in selected
    assert f"{SIBLING_INVARIANT}@1" in selected and f"{FAMILY}/{REVIEW_INVARIANT}" not in selected
    statements = {item.statement for item in page.items if item.kind == "invariant_revision"}
    assert statements == {"Family context is read from the comparison's own endpoints."}
    # The index still answers the retired record, and says it is retired.
    assert answer.record is not None and answer.record.status == "retired"
    assert [entry.id for entry in answer.realizations] == ["RLZ-RVW001"]
    assert REVIEW_INVARIANT in family.members


# --- the mounted tools ----------------------------------------------------------------------------


def _converted(tmp_path: Path) -> Path:
    root = tmp_path / "memory"
    init_repository(root)
    write_review_tree(root)
    commit_all(root)
    return root


def test_knowledge_read_resolves_a_converted_memory_tree_through_its_index(tmp_path: Path) -> None:
    root = _converted(tmp_path)
    coordination = tmp_path / "coordination"
    for selection in (root, root / "knowledge.sqlite"):
        response = knowledge_read_payload(
            ReadToolRequest(
                database_path=str(selection),
                repository_id=INDEX_REPOSITORY_ID,
                view="family",
                family_revision_id=text_uuid("revision", f"{FAMILY}@1"),
            ),
            coordination_root=str(coordination),
        )
        assert response["state"] == "view", response
        assert response["memoryTree"]["memoryRoot"] == str(root)
        assert response["memoryTree"]["indexState"] == "complete"
    cached = list((coordination / "runtime" / "knowledge-index").glob("*.sqlite"))
    assert [path.stem for path in cached] == [response["memoryTree"]["treeId"]]

    refused = knowledge_read_payload(
        ReadToolRequest(database_path=str(root), repository_id=INDEX_REPOSITORY_ID, view="family")
    )
    assert refused["state"] == "refused"
    assert refused["refusalCode"] == "snapshot_unavailable"
    assert "no coordination root" in refused["refusalDetail"]


def test_knowledge_diff_and_project_resolve_converted_trees(tmp_path: Path) -> None:
    root = _converted(tmp_path)
    before = tmp_path / "before"
    shutil.copytree(root, before)
    target = next((root / "knowledge" / "invariants").glob(f"{REVIEW_INVARIANT}-*.json"))
    document = json.loads(target.read_text("utf-8"))
    document.update(revision=2, statement="A comparison shows every unchanged realization.")
    target.write_text(canonical_text(document), encoding="utf-8")
    coordination = str(tmp_path / "coordination")
    cache = KnowledgeIndexCache(tmp_path / "coordination" / "runtime" / "knowledge-index")
    with cache.for_directory(before) as base, cache.for_directory(root) as candidate:
        body = {
            "selector": {
                "kind": "invariant",
                "invariant_id": text_uuid("identity", REVIEW_INVARIANT),
            },
            "before": {"context": _context(base)},
            "after": {"context": _context(candidate)},
        }
    compared = knowledge_diff_payload(
        DiffToolRequest(
            database_path=str(root),
            repository_id=INDEX_REPOSITORY_ID,
            before_path=str(before),
            after_path=str(root / "knowledge.sqlite"),
            body=body,
        ),
        coordination_root=coordination,
    )
    assert compared["state"] == "compared", compared
    assert set(compared["memoryTrees"]) == {"before", "after"}
    assert "every unchanged realization" in json.dumps(compared["payload"])

    destination = tmp_path / "vault"
    projected = knowledge_project_payload(
        ProjectToolRequest(
            database_path=str(root),
            repository_id=INDEX_REPOSITORY_ID,
            destination_root=str(destination),
            views=(
                {
                    "view": "family",
                    "family_revision_id": text_uuid("revision", f"{FAMILY}@1"),
                },
            ),
        ),
        coordination_root=coordination,
    )
    assert projected["state"] == "projected", projected
    assert projected["memoryTree"]["memoryRoot"] == str(root)
    assert projected["published"]


def test_an_unconverted_selection_keeps_the_database_path(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    init_repository(legacy)
    response = knowledge_read_payload(
        ReadToolRequest(
            database_path=str(legacy / "knowledge.sqlite"),
            repository_id=INDEX_REPOSITORY_ID,
            view="family",
        ),
        coordination_root=str(tmp_path / "coordination"),
    )
    assert response["refusalCode"] == "selected_input_unavailable"
    assert "memoryTree" not in response
    assert not (tmp_path / "coordination").exists()


def _context(index: KnowledgeIndex) -> dict[str, object]:
    return open_read_context(index.database_path, index.repository_id).model_dump(mode="json")


def test_a_retired_family_is_never_selected_as_live(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    init_repository(root)
    write_review_tree(root)
    _retire(root, FAMILY, "families")
    commit_all(root)
    with KnowledgeIndexCache(tmp_path / "cache").for_directory(root) as index:
        context = open_read_context(index.database_path, index.repository_id)
        page = read_knowledge_scope(
            index.database_path,
            context,
            KnowledgeReadRequest(
                seed=PathSeed(path=REVIEW_PATH), budget=KnowledgeReadBudget(max_items=64)
            ),
        ).page
        family = index.family(FAMILY).value
        invariant = index.invariant(REVIEW_INVARIANT).value
        assert index.text_id(text_uuid("revision", f"{FAMILY}@1")) is None
    assert page is not None
    assert {item.kind for item in page.items} == {"invariant_revision", "realization_claim"}
    # The index still answers the family and its members, and says it is retired.
    assert family.record is not None and family.record.status == "retired"
    assert family.members == (REVIEW_INVARIANT, SIBLING_INVARIANT)
    assert invariant.families == (FAMILY,)


# --- F1: a partial index is never presented as complete -------------------------------------------


def _partial(tmp_path: Path) -> Path:
    root = _converted(tmp_path)
    (root / "knowledge" / "invariants" / "INV-BRKN01-x.json").write_text("{}", "utf-8")
    return root


def test_every_tool_surface_reports_a_partial_index_as_incomplete(tmp_path: Path) -> None:
    root = _partial(tmp_path)
    coordination = str(tmp_path / "coordination")
    read = knowledge_read_payload(
        ReadToolRequest(
            database_path=str(root),
            repository_id=INDEX_REPOSITORY_ID,
            view="family",
            family_revision_id=text_uuid("revision", f"{FAMILY}@1"),
        ),
        coordination_root=coordination,
    )
    assert read["state"] == "view"
    assert read["completeWithinDeclaredScope"] is False
    assert read["payload"]["completeness"]["complete_within_declared_scope"] is False
    assert read["indexComplete"] is False and read["memoryTree"]["indexState"] == "partial"

    cache = KnowledgeIndexCache(tmp_path / "coordination" / "runtime" / "knowledge-index")
    with cache.for_directory(root) as index:
        context = _context(index)
    compared = knowledge_diff_payload(
        DiffToolRequest(
            database_path=str(root),
            repository_id=INDEX_REPOSITORY_ID,
            before_path=str(root),
            after_path=str(root),
            body={
                "selector": {
                    "kind": "invariant",
                    "invariant_id": text_uuid("identity", REVIEW_INVARIANT),
                },
                "before": {"context": context},
                "after": {"context": context},
            },
        ),
        coordination_root=coordination,
    )
    assert compared["state"] == "compared", compared
    assert compared["indexComplete"] is False
    assert compared["memoryTrees"]["after"]["indexState"] == "partial"

    projected = knowledge_project_payload(
        ProjectToolRequest(
            database_path=str(root),
            repository_id=INDEX_REPOSITORY_ID,
            destination_root=str(tmp_path / "vault"),
            views=({"view": "family", "family_revision_id": text_uuid("revision", f"{FAMILY}@1")},),
        ),
        coordination_root=coordination,
    )
    assert projected["state"] == "projected", projected
    assert projected["indexComplete"] is False

    complete_root = _converted(tmp_path / "complete")
    complete = knowledge_read_payload(
        ReadToolRequest(
            database_path=str(complete_root),
            repository_id=INDEX_REPOSITORY_ID,
            view="family",
            family_revision_id=text_uuid("revision", f"{FAMILY}@1"),
        ),
        coordination_root=coordination,
    )
    assert complete["completeWithinDeclaredScope"] is True and complete["indexComplete"] is True


# --- F3: an index that cannot be built is a refusal on every surface ------------------------------


def test_an_unbuildable_index_is_refused_not_raised(tmp_path: Path) -> None:
    root = _converted(tmp_path)
    blocked = tmp_path / "coordination-is-a-file"
    blocked.write_text("not a directory", "utf-8")
    projected = knowledge_project_payload(
        ProjectToolRequest(
            database_path=str(root),
            repository_id=INDEX_REPOSITORY_ID,
            destination_root=str(tmp_path / "vault"),
            views=({"view": "family", "family_revision_id": text_uuid("revision", f"{FAMILY}@1")},),
        ),
        coordination_root=str(blocked),
    )
    assert projected["state"] == "refused"
    assert projected["refusalCode"] in {"snapshot_unavailable", "selected_input_unavailable"}
    compared = knowledge_diff_payload(
        DiffToolRequest(
            database_path=str(root),
            repository_id=INDEX_REPOSITORY_ID,
            before_path=str(root),
            after_path=str(root),
            body={
                "selector": {"kind": "invariant", "invariant_id": text_uuid("identity", "x")},
                "before": {"context": {}},
                "after": {"context": {}},
            },
        ),
        coordination_root=str(blocked),
    )
    assert compared["state"] == "refused"


# --- F6: an unconverted root with a real database reads exactly as before -------------------------


def test_a_real_database_in_an_unconverted_root_reads_identically(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    init_repository(legacy)
    database, repository_id = build_parity_dataset(legacy)
    commit_all(legacy)
    family_revision = str(
        next(
            iter(apsw.Connection(str(database)).execute("SELECT revision_id FROM family_revision"))
        )[0]
    )
    request = ReadToolRequest(
        database_path=str(database),
        repository_id=repository_id,
        view="family",
        family_revision_id=family_revision,
    )
    coordination = tmp_path / "coordination"
    with_root = knowledge_read_payload(request, coordination_root=str(coordination))
    without_root = knowledge_read_payload(request)
    assert with_root["state"] == "view", with_root
    assert with_root == without_root
    assert "memoryTree" not in with_root and "indexComplete" not in with_root
    assert not coordination.exists()
