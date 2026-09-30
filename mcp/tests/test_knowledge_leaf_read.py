"""The family-complete leaf read (MIK-R01) and the obligations L01 carries from L02.

* **Selection and order.** A path seed selects its entries' invariants, every family containing
  them, those families' members and every member's entries -- one family hop -- in the declared
  order: the seed's own invariants, then each family header followed by its remaining members, then
  the advertised frontier. A shared member is returned once and referenced after; a retired member
  is never selected.
* **One selection on both surfaces.** The ``read_ar_files`` block and ``knowledge_read``'s
  ``source_context`` view return the same rows under the same selection-manifest digest.
* **Family names in the narrower views**, the conforming example in one response, the failure
  states, and the carried obligations: the empty-repository root, the leaf walk's code tree, the
  seed-queue cap and the projection's 64-row cap.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from agents_remember.application.knowledge_leaf import select_leaf
from agents_remember.application.knowledge_leaf.selection import (
    DERIVED_TITLE_LENGTH,
    derived_title,
)
from agents_remember.application.knowledge_paging import KNOWLEDGE_PAGE_THRESHOLD_TOKENS
from agents_remember.application.knowledge_paging.threshold import response_tokens
from agents_remember.application.knowledge_read import open_read_context, select_knowledge_scope
from agents_remember.application.published_intent import (
    PublishedIntentSelection,
    published_intent_block,
    read_published_intent,
    resolve_published_memory_tree,
)
from agents_remember.kernel.coordination_context.models import CoordinationContext
from agents_remember.mcp.tools.knowledge import (
    ProjectToolRequest,
    ReadToolRequest,
    knowledge_project_payload,
    knowledge_read_payload,
)
from agents_remember.memory.knowledge.read import SelectedScope
from agents_remember.memory.knowledge_index import INDEX_REPOSITORY_ID, KnowledgeIndex, text_uuid
from agents_remember.models.knowledge.continuation import MAX_QUEUED_SEEDS, decode_continuation
from agents_remember.models.knowledge.read import InvariantIdentitySeed, PathSeed
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    file_sidecar_path,
    record_path,
)
from knowledge_index_test_support import (
    REVIEW_INVARIANT,
    REVIEW_PATH,
    SIBLING_INVARIANT,
    anchor,
    commit_all,
    git,
    init_repository,
    write_document,
    write_review_tree,
)

SEED = "src/seed.py"


def _invariant(root: Path, identifier: str, *, status: str = "accepted") -> None:
    write_document(
        root,
        record_path("invariant", identifier, "x"),
        {
            "schema": "ar-invariant/v1",
            "id": identifier,
            "origin": {"task": "260928-MIK", "leaf": "260928-MIK-L01"},
            "revision": 1,
            "status": status,
            "statement": f"{identifier} holds.",
            "applicability": f"Where {identifier} applies.",
            "conditions": [f"{identifier} condition"],
            "exclusions": [f"{identifier} exclusion"],
            "supersedes": [],
            "admission": "legacy-unassessed",
        },
    )


def _family(root: Path, identifier: str, members: list[str], routes: list[str]) -> None:
    write_document(
        root,
        record_path("family", identifier, "x"),
        {
            "schema": "ar-family/v1",
            "id": identifier,
            "origin": {"task": "260928-MIK", "leaf": "260928-MIK-L01"},
            "revision": 1,
            "status": "accepted",
            "title": f"Title of {identifier}",
            "guarantee": f"{identifier} guarantees its members together.",
            "members": members,
            "routes": routes,
            "admission": "legacy-unassessed",
        },
    )


def _sidecar(
    root: Path,
    path: str,
    realizes: list[tuple[str, str]],
    proves: list[tuple[str, str]] | None = None,
) -> None:
    document: dict[str, Any] = {
        "schema": "ar-onboarding-file/v1",
        "path": path,
        "references": {},
        "realizes": [
            {
                "id": entry,
                "invariant": invariant,
                "anchor": anchor(entry.lower()),
                "role": "enforcement",
                "rationale": f"{entry} realizes {invariant}.",
            }
            for entry, invariant in realizes
        ],
    }
    if proves is not None:
        document["proves"] = [
            {
                "id": entry,
                "invariant": invariant,
                "anchor": anchor(entry.lower()),
                "facet": f"{entry} proves {invariant}",
            }
            for entry, invariant in proves
        ]
    write_document(root, file_sidecar_path(path), document)


def _graph(root: Path) -> None:
    """``SEED`` realizes A and B. F1 = {A, C, D}; F2 = {B, C, E, R (retired)}; F3 = {C, D, X}."""

    init_repository(root)
    write_document(root, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "x"})
    for identifier in ("INV-AAAAAA", "INV-BBBBBB", "INV-CCCCCC", "INV-DDDDDD", "INV-EEEEEE"):
        _invariant(root, identifier)
    _invariant(root, "INV-XXXXXX")
    _invariant(root, "INV-RRRRRR", status="retired")
    _family(root, "FAM-F11111", ["INV-AAAAAA", "INV-CCCCCC", "INV-DDDDDD"], ["src"])
    _family(root, "FAM-F22222", ["INV-BBBBBB", "INV-CCCCCC", "INV-EEEEEE", "INV-RRRRRR"], ["."])
    _family(root, "FAM-F33333", ["INV-CCCCCC", "INV-DDDDDD", "INV-XXXXXX"], ["lib"])
    _sidecar(root, SEED, [("RLZ-A00001", "INV-AAAAAA"), ("RLZ-B00001", "INV-BBBBBB")])
    _sidecar(root, "src/c.py", [("RLZ-C00001", "INV-CCCCCC")])
    _sidecar(root, "src/d.py", [("RLZ-D00001", "INV-DDDDDD")])
    _sidecar(root, "src/e.py", [("RLZ-E00001", "INV-EEEEEE"), ("RLZ-R00001", "INV-RRRRRR")])
    _sidecar(root, "lib/x.py", [("RLZ-X00001", "INV-XXXXXX")])
    _sidecar(root, "tests/test_d.py", [], proves=[("PRF-D00001", "INV-DDDDDD")])
    commit_all(root)


def _context(tmp_path: Path, memory: Path, code: Path | None = None) -> CoordinationContext:
    return cast(
        CoordinationContext,
        SimpleNamespace(
            memory_root=memory,
            coordination_root=tmp_path / "coordination",
            code_repository_root=code,
            code_repository_name="agents-remember",
        ),
    )


def _read(tmp_path: Path, root: Path, *, workspace: Path | None = None, **fields: Any) -> Any:
    return knowledge_read_payload(
        ReadToolRequest(database_path=str(root), repository_id=INDEX_REPOSITORY_ID, **fields),
        workspace_root=None if workspace is None else str(workspace),
        coordination_root=str(tmp_path / "coordination"),
    )


def _shape(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(row["kind"], row["id"]) for row in rows]


def test_a_path_selects_one_family_hop_in_the_declared_order(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    _graph(root)
    block = published_intent_block(_context(tmp_path, root), [SEED])
    page = block["seeds"][0]
    assert page["state"] == "page" and page["continuation"] is None, page
    assert _shape(page["rows"]) == [
        # 1. the seed path's own invariants, each with its entries
        ("member", "INV-AAAAAA"),
        ("realization", "RLZ-A00001"),
        ("member", "INV-BBBBBB"),
        ("realization", "RLZ-B00001"),
        # 2. each family header, then its remaining members (a seed invariant is not repeated)
        ("family_header", "FAM-F11111"),
        ("member", "INV-CCCCCC"),
        ("realization", "RLZ-C00001"),
        ("member", "INV-DDDDDD"),
        ("realization", "RLZ-D00001"),
        ("proof", "PRF-D00001"),
        ("family_header", "FAM-F22222"),
        ("member_reference", "INV-CCCCCC"),  # rule 5: returned under FAM-F11111
        ("member", "INV-EEEEEE"),
        ("realization", "RLZ-E00001"),
        # 3. the advertised frontier: named, never expanded -- one row per family (review N4)
        ("advertised_family", "FAM-F33333"),
    ]
    rows = {(row["kind"], row["id"]): row for row in page["rows"]}
    header = rows[("family_header", "FAM-F22222")]
    assert header["title"] == "Title of FAM-F22222"
    assert header["guarantee"] == "FAM-F22222 guarantees its members together."
    assert header["routes"] == ["."]
    # The retired member is not live: it is neither counted nor listed nor selected.
    assert header["members"] == ["INV-BBBBBB", "INV-CCCCCC", "INV-EEEEEE"]
    assert header["memberCount"] == 3
    assert "RLZ-R00001" not in json.dumps(page["rows"])
    member = rows[("member", "INV-DDDDDD")]
    assert {key: member[key] for key in ("statement", "applicability", "status", "admission")} == {
        "statement": "INV-DDDDDD holds.",
        "applicability": "Where INV-DDDDDD applies.",
        "status": "accepted",
        "admission": "legacy-unassessed",
    }
    assert (member["conditions"], member["exclusions"]) == (
        ["INV-DDDDDD condition"],
        ["INV-DDDDDD exclusion"],
    )
    proof = rows[("proof", "PRF-D00001")]
    assert (proof["path"], proof["locator"], proof["facet"]) == (
        "tests/test_d.py",
        {"kind": "symbol", "name": "prf-d00001"},
        "PRF-D00001 proves INV-DDDDDD",
    )
    assert rows[("realization", "RLZ-C00001")]["role"] == "enforcement"
    assert {row["state"] for row in page["rows"] if "state" in row} == {"unverifiable"}
    reference = rows[("member_reference", "INV-CCCCCC")]
    assert reference["returnedUnder"] == "FAM-F11111"
    # Ruling Q1: an invariant has no title; the reference's is derived from its statement.
    assert (reference["title"], reference["titleDerivedFrom"]) == ("INV-CCCCCC holds.", "statement")
    assert rows[("advertised_family", "FAM-F33333")]["via"] == ["INV-CCCCCC", "INV-DDDDDD"]
    # Rule 7: a member names every family containing it, the advertised one included.
    assert rows[("member", "INV-CCCCCC")]["families"] == ["FAM-F11111", "FAM-F22222", "FAM-F33333"]
    assert page["counts"] == {
        "families": 2,
        "members": 5,
        "entries": 6,
        "realizations": 5,
        "proofs": 1,
        "distinctPaths": 5,
        "invariantsByState": {"stale": 0, "unverifiable": 5, "unrealized": 0, "current": 0},
        "advertisedFamilies": 1,
        "rowsTotal": 15,
        "rowsReturned": 15,
        "rowsRemaining": 0,
    }
    assert block["policyVersion"] == "family-complete-leaf/v1"  # ruling Q5
    tree = block["memoryTree"]["treeId"]
    assert page["memoryTreeId"] == tree == page["page"]["memoryTreeId"]  # rule 9
    assert page["page"]["selectionPolicy"] == "family-complete-leaf"

    # A path whose only entry is a proof seeds the read too.
    proof_only = published_intent_block(_context(tmp_path, root), ["tests/test_d.py"])["seeds"][0]
    assert _shape(proof_only["rows"])[:3] == [
        ("member", "INV-DDDDDD"),
        ("realization", "RLZ-D00001"),
        ("proof", "PRF-D00001"),
    ]


def test_both_surfaces_return_one_selection_under_one_manifest(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    _graph(root)
    block = published_intent_block(_context(tmp_path, root), [SEED])["seeds"][0]
    read = _read(tmp_path, root, view="source_context", source_path=SEED)
    assert read["state"] == "page", read
    assert (
        read["page"]["manifestDigest"] == block["manifestDigest"] == block["page"]["manifestDigest"]
    )
    assert read["payload"]["rows"] == block["rows"]
    assert read["page"]["selectionPolicy"] == block["page"]["selectionPolicy"]
    # The currentness block covers what the page returns, by state.
    assert {one["id"] for one in read["currentness"]["invariants"]} == {
        "INV-AAAAAA",
        "INV-BBBBBB",
        "INV-CCCCCC",
        "INV-DDDDDD",
        "INV-EEEEEE",
    }
    assert read["currentness"]["unverifiableReason"] == "no code tree was requested"


def test_the_invariant_view_names_its_families(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    _graph(root)
    view = _read(
        tmp_path,
        root,
        view="invariant",
        invariant_revision_id=text_uuid("revision", "INV-CCCCCC@1"),
    )
    assert view["state"] == "view", view
    assert view["families"] == [
        {"id": "FAM-F11111", "title": "Title of FAM-F11111"},
        {"id": "FAM-F22222", "title": "Title of FAM-F22222"},
        {"id": "FAM-F33333", "title": "Title of FAM-F33333"},
    ]


def test_the_conforming_example_returns_the_whole_family_in_one_response(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    init_repository(root)
    write_review_tree(root)
    commit_all(root)
    response = _read(tmp_path, root, view="source_context", source_path=REVIEW_PATH)
    assert response["state"] == "page" and response.get("continuation") is None, response
    assert response["tokens"] <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS
    rows = response["payload"]["rows"]
    assert (rows[0]["kind"], rows[0]["id"]) == ("member", REVIEW_INVARIANT)  # its own invariant
    header = next(row for row in rows if row["kind"] == "family_header")
    assert header["title"] == "Comparison-bound unchanged realization context"
    assert header["guarantee"].startswith("Every realization of the family")
    statements = {row["statement"] for row in rows if row["kind"] == "member"}
    assert len(statements) == 2  # every member's statement
    realized = {row["path"] for row in rows if row["kind"] == "realization"}
    assert REVIEW_PATH in realized and len(realized) == 4  # every member entry, four files
    assert {row["invariant"] for row in rows if row["kind"] == "realization"} == {
        REVIEW_INVARIANT,
        SIBLING_INVARIANT,
    }


def test_absent_and_partial_states_are_named(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    _graph(root)
    refused = published_intent_block(_context(tmp_path, root), ["src/nothing.py"])
    absent = refused["seeds"][0]
    assert (absent["state"], absent["refusalCode"]) == ("refused", "registration_absent")
    assert "realization or proof claim" in absent["refusalDetail"]  # review N6
    # Review N5: the leaf read applied, and refused every path.
    assert refused["policyVersion"] == "family-complete-leaf/v1"
    read = _read(tmp_path, root, view="source_context", source_path="src/nothing.py")
    assert read["refusalCode"] == "registration_absent"
    # Review N3 (rule 9): a refused tree read names the memory tree and its index state.
    assert read["memoryTree"]["treeId"] == refused["memoryTree"]["treeId"]
    assert read["indexComplete"] is True

    (root / "knowledge" / "invariants" / "INV-BRKN01-x.json").write_text("{}", "utf-8")
    partial = published_intent_block(_context(tmp_path, root), [SEED])["seeds"][0]
    assert partial["indexState"] == "partial" and partial["enumerationComplete"] is False
    assert partial["rows"]  # the partial answer is still returned, and says it is partial
    missing = _read(tmp_path, root, view="source_context", source_path="src/nothing.py")
    assert missing["memoryTree"]["indexState"] == "partial" and missing["indexComplete"] is False


def test_a_repository_root_with_no_commit_is_refused_by_name(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    _graph(root)
    empty = tmp_path / "empty"
    init_repository(empty)
    plain = tmp_path / "plain"
    plain.mkdir()
    for named in (empty, plain):
        refused = _read(
            tmp_path, root, view="source_context", source_path=SEED, repository_root=str(named)
        )
        assert refused["state"] == "refused", refused
        assert refused["refusalCode"] == "selected_input_unavailable"
        assert str(named) in refused["refusalDetail"]


def test_a_leaf_walk_resumes_at_its_code_tree_from_the_named_repository(tmp_path: Path) -> None:
    code = tmp_path / "code"
    init_repository(code)
    (code / "src").mkdir()
    (code / SEED).write_text("def rlz_a00001():\n    return 1\n", encoding="utf-8")
    commit_all(code)
    tree = git(code, "rev-parse", "HEAD^{tree}")
    root = tmp_path / "memory"
    init_repository(root)
    write_document(root, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "x"})
    members = [f"INV-M{number:05d}" for number in range(40)]
    for identifier in members:
        _invariant(root, identifier)
        write_document(
            root,
            record_path("invariant", identifier, "x"),
            {
                **json.loads((root / record_path("invariant", identifier, "x")).read_text()),
                "statement": f"{identifier} " + "holds " * 150,
            },
        )
    _family(root, "FAM-WK0001", members, ["src"])
    # Member 0 is realized at the seed path; every other member in a file of its own.
    _sidecar(root, SEED, [("RLZ-M00000", members[0])])
    for number in range(1, 40):
        _sidecar(root, f"src/m_{number}.py", [(f"RLZ-M{number:05d}", members[number])])
    commit_all(root)

    first = _read(
        tmp_path,
        root,
        view="source_context",
        source_path=SEED,
        code_tree_id=tree,
        repository_root=str(code),
    )
    assert first["page"]["codeTreeId"] == tree and first["continuation"] is not None
    token = decode_continuation(first["continuation"])
    assert token is not None and str(code) not in json.dumps(token.model_dump())
    resumed = _read(
        tmp_path, root, workspace=code, view="source_context", continuation=first["continuation"]
    )
    assert resumed["state"] == "page", resumed
    assert resumed["page"]["codeTreeId"] == tree
    assert resumed["currentness"]["codeTree"]["treeId"] == tree
    # A page that continues the family starts with its header reference as a literal row.
    assert resumed["payload"]["rows"][0]["kind"] == "family_header_reference"
    assert resumed["payload"]["rows"][0]["id"] == "FAM-WK0001"
    elsewhere = tmp_path / "elsewhere"
    init_repository(elsewhere)
    commit_all(elsewhere, "unrelated")
    moved = _read(
        tmp_path,
        root,
        view="source_context",
        continuation=first["continuation"],
        repository_root=str(elsewhere),
    )
    assert moved["refusalCode"] == "selected_input_unavailable"
    assert str(elsewhere) in moved["refusalDetail"]


_DEEP = "src/" + "/".join(f"deeply_nested_package_level_{level:02d}" for level in range(24))


def test_a_tail_longer_than_one_queue_is_refused_by_name_within_the_threshold(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    init_repository(root)
    write_document(root, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "x"})
    paths = []
    for number in range(MAX_QUEUED_SEEDS + 40):
        identifier = f"INV-Q{number:05d}"
        path = f"{_DEEP}/module_{number}.py"
        _invariant(root, identifier)
        _sidecar(root, path, [(f"RLZ-Q{number:05d}", identifier)])
        paths.append(path)
    commit_all(root)
    block = published_intent_block(_context(tmp_path, root), paths)
    assert response_tokens(block) <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS
    refused = block["seeds"][-1]
    assert refused["state"] == "refused" and refused["refusalCode"] == "seed_queue_exceeded"
    laid = [entry for entry in block["seeds"] if entry["state"] == "page"]
    assert laid and refused["seedCount"] == len(paths) - len(laid)
    assert refused["firstSeed"] == {"kind": "path", "path": paths[len(laid)]}


def test_a_tree_projection_carries_every_row_of_a_view(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    init_repository(root)
    write_document(root, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "x"})
    members = [f"INV-W{number:05d}" for number in range(40)]
    for number, identifier in enumerate(members):
        _invariant(root, identifier)
        _sidecar(root, f"src/w_{number}.py", [(f"RLZ-W{number:05d}", identifier)])
    _family(root, "FAM-WDE001", members, ["src"])
    commit_all(root)
    family = text_uuid("revision", "FAM-WDE001@1")
    whole = _read(tmp_path, root, view="family", family_revision_id=family)
    total = whole["page"]["total"]
    assert total > 64  # the renderer's slice would have cut it
    vault = tmp_path / "vault"
    projected = knowledge_project_payload(
        ProjectToolRequest(
            database_path=str(root),
            repository_id=INDEX_REPOSITORY_ID,
            destination_root=str(vault),
            formats=("json",),
            views=({"view": "family", "subject": "FAM-WDE001", "familyRevisionId": family},),
        ),
        coordination_root=str(tmp_path / "coordination"),
    )
    assert projected["state"] == "projected", projected
    rows = [
        row
        for artifact in sorted((vault / "family").glob("FAM-WDE001*.json"))
        for row in json.loads(artifact.read_text(encoding="utf-8"))["rows"]
    ]
    assert len(rows) == total


def test_a_derived_reference_title_is_the_first_sentence_cut_to_a_fixed_length() -> None:
    assert derived_title("One rule holds.  A second sentence follows.") == "One rule holds."
    assert derived_title("Version 2.1 applies here. Then more.") == "Version 2.1 applies here."
    assert derived_title("No sentence end") == "No sentence end"
    long = derived_title("word " * 40 + ". Rest.")
    assert len(long) == DERIVED_TITLE_LENGTH and long.endswith("\u2026")


def _seed_key(seed: dict[str, Any]) -> str:
    return json.dumps(seed, sort_keys=True)


def _item_keys(page: dict[str, Any]) -> list[str]:
    """A page's returned rows: scope ``items`` by ID, leaf ``rows`` by kind and ID."""

    if "items" in page:
        return [item["item_id"] for item in page["items"]]
    return [
        f"{row['kind']}:{row['id']}"
        for row in page["rows"]
        if row["kind"] != "family_header_reference"
    ]


def _walk_entries(tmp_path: Path, root: Path, block: dict[str, Any]) -> dict[str, list[str]]:
    """Every seed's rows: from the block, then through each continuation it hands out."""

    walked: dict[str, list[str]] = {}
    for entry in block["seeds"]:
        if entry["state"] == "page":
            walked.setdefault(_seed_key(entry["seed"]), []).extend(_item_keys(entry))
        view, continuation = entry.get("continuationView"), entry.get("continuation")
        while continuation is not None:
            response = _read(tmp_path, root, view=view, continuation=continuation)
            assert response["state"] == "page", response
            assert response["tokens"] <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS
            page = response["payload"]
            walked.setdefault(_seed_key(page["seed"]), []).extend(_item_keys(page))
            view, continuation = page["continuationView"], response.get("continuation")
    return walked


def test_identity_seeds_on_a_tree_keep_the_scope_read_beside_the_leaf(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    init_repository(root)
    write_document(root, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "x"})
    members = [f"INV-S{number:05d}" for number in range(30)]
    for number, identifier in enumerate(members):
        _invariant(root, identifier)
        _sidecar(root, f"src/s_{number}.py", [(f"RLZ-S{number:05d}", identifier)])
    _family(root, "FAM-SC0001", members, ["src"])
    commit_all(root)
    selection = resolve_published_memory_tree(_context(tmp_path, root))
    assert isinstance(selection, PublishedIntentSelection)
    identities = [
        InvariantIdentitySeed(invariant_id=text_uuid("identity", identifier))
        for identifier in members
    ]
    # An identity seed first (a scope page laid in the block), then a path, then the rest: the
    # block fills, and each kind's tail collapses into a continuation of its own.
    seeds: list[Any] = [identities[0], PathSeed(path="src/s_0.py"), *identities[1:]]
    block = read_published_intent(selection, seeds)
    assert response_tokens(block) <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS
    # A block with an identity seed is not uniformly leaf: it keeps the scope policy, and each
    # page states its own (the mixed-block ruling).
    assert block["policyVersion"] == "recorded-family-frontier/v1"
    first = block["seeds"][0]
    assert (
        first["state"] == "page" and first["page"]["selectionPolicy"] == "recorded-family-frontier"
    )
    collapsed = [entry for entry in block["seeds"] if "seeds" in entry]
    assert {entry["continuationView"] for entry in collapsed} == {"invariant", "source_context"}
    walked = _walk_entries(tmp_path, root, block)
    assert len(walked) == len(seeds)
    context = open_read_context(selection.database_path, INDEX_REPOSITORY_ID)
    for seed in seeds:
        keys = walked[_seed_key(seed.model_dump(mode="json"))]
        if isinstance(seed, PathSeed):
            with KnowledgeIndex(selection.database_path) as index:
                structure = select_leaf(index, seed.path)
            assert structure is not None
            total = len(structure.order)
        else:
            scope = select_knowledge_scope(selection.database_path, context, seed)
            assert isinstance(scope, SelectedScope)
            total = len(scope.items)
        assert len(keys) == len(set(keys)) == total, seed
