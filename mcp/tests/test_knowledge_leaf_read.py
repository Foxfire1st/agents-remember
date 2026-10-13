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
from agents_remember.application.knowledge_read import (
    open_read_context,
    select_knowledge_scope,
)
from agents_remember.application.published_intent import (
    PublishedIntentSelection,
    published_intent_block,
    read_published_intent,
    resolve_published_memory_tree,
)
from agents_remember.kernel.coordination_context.models import CoordinationContext
from agents_remember.mcp.tools.knowledge import (
    ReadToolRequest,
    # MIK-R108: answers are bounded and carry no token count. Tests call the public
    # payload boundary (model-validated answer) and resolve whole results through the
    # answer's own continuation pointer, the way the mounted tool serves them.
    _read_result,
    knowledge_read_payload,
)
from agents_remember.memory.knowledge.read import SelectedScope
from agents_remember.memory.knowledge_index import (
    INDEX_REPOSITORY_ID,
    KnowledgeIndex,
    text_uuid,
)
from agents_remember.models.knowledge.continuation import (
    MAX_QUEUED_SEEDS,
    decode_continuation,
)
from agents_remember.models.knowledge.read import InvariantIdentitySeed, PathSeed
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    file_sidecar_path,
    record_path,
)
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import bound_for
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
    for identifier in (
        "INV-AAAAAA",
        "INV-BBBBBB",
        "INV-CCCCCC",
        "INV-DDDDDD",
        "INV-EEEEEE",
    ):
        _invariant(root, identifier)
    _invariant(root, "INV-XXXXXX")
    _invariant(root, "INV-RRRRRR", status="retired")
    _family(root, "FAM-F11111", ["INV-AAAAAA", "INV-CCCCCC", "INV-DDDDDD"], ["src"])
    _family(
        root,
        "FAM-F22222",
        ["INV-BBBBBB", "INV-CCCCCC", "INV-EEEEEE", "INV-RRRRRR"],
        ["."],
    )
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
        ReadToolRequest(memory_root=str(root), **fields),
        workspace_root=None if workspace is None else str(workspace),
        coordination_root=str(tmp_path / "coordination"),
    )


def _read_body(tmp_path: Path, root: Path, *, workspace: Path | None = None, **fields: Any) -> Any:
    """The same read as a domain-unit body, for tails the mounted bound does not retain.

    Only overflow pages take this path (marked at each call): the public answer keeps
    the overflow contract, and the rows resolve here. Never a claim of pointer retrieval.
    """

    body, _selection, _resolver = _read_result(
        ReadToolRequest(memory_root=str(root), **fields),
        workspace_root=None if workspace is None else str(workspace),
        coordination_root=str(tmp_path / "coordination"),
    )
    return body


def _shape(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(row["kind"], row["id"]) for row in rows]


def _projected_shape(rows: list[dict[str, Any]]) -> list[tuple[str, str, str | None]]:
    # Projected rows name their record with the endpoint riding the role.
    return [(row["kind"], row["id"], row.get("role")) for row in rows]


_STRUCTURAL_ENDPOINTS = {
    "member": ("invariant", "member"),
    "member_reference": ("invariant", "reference"),
    "family_header": ("family", "header"),
    "advertised_family": ("family", "advertised"),
    "chain_family": ("family", "chain"),
    "family_header_reference": ("family", "reference"),
}


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
        # 4. MIK-R05: the route chain (src, then .) after the MIK-R01 content, nearest first
        ("chain_family", "FAM-F11111"),
        ("chain_family", "FAM-F22222"),
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
    assert (reference["title"], reference["titleDerivedFrom"]) == (
        "INV-CCCCCC holds.",
        "statement",
    )
    assert rows[("advertised_family", "FAM-F33333")]["via"] == [
        "INV-CCCCCC",
        "INV-DDDDDD",
    ]
    # Rule 7: a member names every family containing it, the advertised one included.
    assert rows[("member", "INV-CCCCCC")]["families"] == [
        "FAM-F11111",
        "FAM-F22222",
        "FAM-F33333",
    ]
    assert page["counts"] == {
        "families": 2,
        "members": 5,
        "entries": 6,
        "realizations": 5,
        "proofs": 1,
        "distinctPaths": 5,
        "invariantsByState": {
            "stale": 0,
            "unverifiable": 5,
            "unrealized": 0,
            "current": 0,
        },
        "advertisedFamilies": 1,
        "chainFamilies": 2,
        "rowsTotal": 17,
        "rowsReturned": 17,
        "rowsRemaining": 0,
    }
    assert block["policyVersion"] == "family-complete-leaf/v2"  # ruling Q5
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
    # The read names what the leaf read returns: same rows by ID, with
    # structural rows projected to their record kind and endpoint role,
    # and no text.
    read_shape = _projected_shape(read["payload"]["rows"])
    block_shape = _shape(block["rows"])
    assert [row_id for _, row_id, _ in read_shape] == [row_id for _, row_id in block_shape]
    for (read_kind, row_id, read_role), (block_kind, _) in zip(
        read_shape, block_shape, strict=True
    ):
        expected = _STRUCTURAL_ENDPOINTS.get(block_kind, (block_kind, None))
        assert (read_kind, read_role) == expected, (row_id, read_kind, read_role, block_kind)
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
    # The view names its families; their titles stay behind the answer's log.
    assert [entry["id"] for entry in view["families"]] == [
        "FAM-F11111",
        "FAM-F22222",
        "FAM-F33333",
    ]
    assert "Title of FAM-F11111" not in json.dumps(view), view


def test_the_conforming_example_returns_the_whole_family_in_one_response(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    init_repository(root)
    write_review_tree(root)
    commit_all(root)
    response = _read(tmp_path, root, view="source_context", source_path=REVIEW_PATH)
    assert response["state"] == "page" and response["payload"].get("continuation") is None, response
    assert response_tokens(response) <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS
    rows = response["payload"]["rows"]
    by_id = {row["id"]: row for row in rows if isinstance(row, dict)}
    # The family's members are all named, with usable kinds and states.
    assert {REVIEW_INVARIANT, SIBLING_INVARIANT} <= set(by_id), by_id
    for row in rows:
        assert row["kind"] in {
            "invariant",
            "realization",
            "family",
            "decision",
            "proof",
        }, row
        assert row["state"] in {"current", "stale", "unverifiable", "unrealized"}, row
        location = row["location"]
        assert location is not None, row
        assert Path(location["file"]).is_file(), row
    # The members' records open as written.
    member = by_id[REVIEW_INVARIANT]
    assert "statement" not in member and "guarantee" not in member, member


def test_a_path_without_entries_still_returns_its_route_chain(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    _graph(root)
    # MIK-R05: a path without entries still returns its route-chain families, and states the
    # registration_absent it would otherwise be refused with.
    governed = published_intent_block(_context(tmp_path, root), ["src/nothing.py"])["seeds"][0]
    assert governed["state"] == "page", governed
    assert governed["registration"]["state"] == "registration_absent"
    assert "realization or proof claim" in governed["registration"]["detail"]  # review N6
    assert _shape(governed["rows"]) == [
        ("chain_family", "FAM-F11111"),
        ("chain_family", "FAM-F22222"),
    ]


def test_absent_and_partial_states_are_named(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    _graph(root)
    # With no route covering the path either (F2 leaves ``.``), it is refused, and the refusal
    # states the chain (MIK-R05).
    _family(
        root,
        "FAM-F22222",
        ["INV-BBBBBB", "INV-CCCCCC", "INV-EEEEEE", "INV-RRRRRR"],
        ["src"],
    )
    commit_all(root)
    refused = published_intent_block(_context(tmp_path, root), ["docs/nothing.md"])
    absent = refused["seeds"][0]
    assert (absent["state"], absent["refusalCode"]) == (
        "refused",
        "registration_absent",
    )
    assert "realization or proof claim" in absent["refusalDetail"]  # review N6
    assert absent["routeChain"]["state"] == "no_governing_family"
    # Review N5: the leaf read applied, and refused every path.
    assert refused["policyVersion"] == "family-complete-leaf/v2"
    read = _read(tmp_path, root, view="source_context", source_path="docs/nothing.md")
    assert read["refusalCode"] == "registration_absent"
    assert read["routeChain"] == absent["routeChain"]
    # Review N3 (rule 9): a refused tree read names the memory tree and its index state.
    assert read["memoryTree"]["treeId"] == refused["memoryTree"]["treeId"]
    assert read["indexComplete"] is True


def test_a_partial_index_is_named_on_both_surfaces(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    _graph(root)
    _family(
        root,
        "FAM-F22222",
        ["INV-BBBBBB", "INV-CCCCCC", "INV-EEEEEE", "INV-RRRRRR"],
        ["src"],
    )
    commit_all(root)
    (root / "knowledge" / "invariants" / "INV-BRKN01-x.json").write_text("{}", "utf-8")
    partial = published_intent_block(_context(tmp_path, root), [SEED])["seeds"][0]
    assert partial["indexState"] == "partial" and partial["enumerationComplete"] is False
    assert partial["rows"]  # the partial answer is still returned, and says it is partial
    missing = _read(tmp_path, root, view="source_context", source_path="docs/nothing.md")
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
            tmp_path,
            root,
            view="source_context",
            source_path=SEED,
            repository_root=str(named),
        )
        assert refused["state"] == "refused", refused
        assert refused["refusalCode"] == "selected_input_unavailable"
        assert str(named) in refused["refusalDetail"]


def test_a_leaf_walk_resumes_at_its_code_tree_from_the_named_repository(
    tmp_path: Path,
) -> None:
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
    # Rule 2 states the rows: forty long members fit within the bound as
    # names (no text), and the mounted answer carries the continuation.
    assert first["state"] == "page", first
    assert count_response_tokens(first) <= bound_for("knowledge_read"), first
    assert "holds " not in json.dumps(first["payload"]["rows"]), first
    assert first["continuation"] is not None, first
    body = _read_body(
        tmp_path,
        root,
        view="source_context",
        source_path=SEED,
        code_tree_id=tree,
        repository_root=str(code),
    )
    assert body["page"]["codeTreeId"] == tree and body["continuation"] is not None
    token = decode_continuation(body["continuation"])
    assert token is not None and str(code) not in json.dumps(token.model_dump())
    resumed = _read(
        tmp_path, root, workspace=code, view="source_context", continuation=body["continuation"]
    )
    assert resumed["state"] == "page", resumed
    resumed_body = _read_body(
        tmp_path, root, workspace=code, view="source_context", continuation=body["continuation"]
    )
    assert resumed_body["state"] == "page", resumed_body
    assert resumed_body["page"]["codeTreeId"] == tree
    assert resumed_body["currentness"]["codeTree"]["treeId"] == tree
    # The continued family's header reference, in L109's family/role vocabulary, on both
    # the resumed answer (rule 2) and the domain body behind it.
    assert resumed_body["payload"]["rows"][0]["id"] == "FAM-WK0001"
    assert resumed["payload"]["rows"][0]["kind"] == "family", resumed["payload"]["rows"][0]
    assert resumed["payload"]["rows"][0].get("role") == "reference", resumed["payload"]["rows"][0]
    assert resumed["payload"]["rows"][0]["id"] == "FAM-WK0001"
    elsewhere = tmp_path / "elsewhere"
    init_repository(elsewhere)
    commit_all(elsewhere, "unrelated")
    moved = _read(
        tmp_path,
        root,
        view="source_context",
        continuation=body["continuation"],
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


def test_a_derived_reference_title_is_the_first_sentence_cut_to_a_fixed_length() -> None:
    assert derived_title("One rule holds.  A second sentence follows.") == "One rule holds."
    assert derived_title("Version 2.1 applies here. Then more.") == "Version 2.1 applies here."
    assert derived_title("No sentence end") == "No sentence end"
    long = derived_title("word " * 40 + ". Rest.")
    assert len(long) == DERIVED_TITLE_LENGTH and long.endswith("\u2026")


def _seed_key(seed: dict[str, Any]) -> str:
    return json.dumps(seed, sort_keys=True)


def _identify_item(item: dict[str, Any], raw_kind: Any, resolve: Any = None) -> str:
    """One raw scope item's record key: the entry names its own record, never its invariant."""

    generic = ("record_id", "invariant_id", "family_id", "member_id", "claim_id")
    if raw_kind in ("realization_claim", "proof_claim", "proof"):
        # An entry names its own record, never the invariant it
        # realizes or proves.
        ordered = ("claim_id", "entry_id", "proof_id", *generic)
    elif raw_kind == "family_membership":
        # A membership names its member record, never the family.
        ordered = ("invariant_id", "invariant_revision_id", "member_id", *generic)
    else:
        ordered = generic
    identified = None
    for key in ordered:
        value = item.get(key)
        if isinstance(value, str):
            identified = resolve(value) if resolve is not None else value
            break
    if not isinstance(identified, str):
        identified = item["item_id"]
    return str(_bare_key(identified))


def _item_keys(page: dict[str, Any], resolve: Any = None) -> list[tuple[str, str, str]]:
    """A page's returned rows in one key space: (kind, text ID, role).

    Raw scope ``items`` carry projected UUIDs and raw leaf ``rows`` carry
    role kinds; projected answer rows already carry text IDs, kinds and
    states. With ``resolve`` (UUID to text ID) every shape maps to the
    record text ID, so a walk that mixes block pages and registered
    answers still detects a repeat or a gap. Distinct rows that name one
    record (a revision, its membership and its realization) key apart by
    kind and role: a membership names its member record (kind invariant)
    with the member role beside it. The continued family's header
    reference is framing (see ``_counted``) and is skipped here too.
    """

    # Raw scope-item kinds to the projected record vocabulary the answers
    # carry, with the implied role: the walk meets each entity in both shapes.
    kinds = {
        "invariant_revision": ("invariant", ""),
        "family_revision": ("family", ""),
        "family_membership": ("invariant", "member"),
        "realization_claim": ("realization", ""),
        "advertised_family": ("family", "advertised"),
    }
    if "items" in page:
        keys = []
        for item in page["items"]:
            raw_kind = item.get("kind")
            kind, role = kinds.get(raw_kind, (raw_kind, ""))
            keys.append((str(kind), _identify_item(item, raw_kind, resolve), str(role)))
        return keys
    rows = page["rows"]
    if isinstance(rows, dict):
        # A transport-truncated list carries its entries under items; the
        # cursor still counts delivered rows.
        rows = rows.get("items", [])
    if rows and isinstance(rows[0], dict) and "item_id" in rows[0]:
        return [("row", str(row["item_id"]), "") for row in rows]
    return [
        (str(row.get("kind")), str(row["id"]), str(row.get("role") or ""))
        for row in rows
        if isinstance(row, dict)
        and row.get("kind") != "family_header_reference"
        and not (row.get("kind") == "family" and (row.get("role") or "") == "reference")
    ]


def _bare_key(value: str) -> str:
    """One key without a ``@revision`` suffix or ``family/member`` composite head."""

    bare = value.split("@", 1)[0]
    if "/" in bare:
        head, _, tail = bare.partition("/")
        if head.split("-", 1)[0] == "FAM" and tail.split("-", 1)[0] in (
            "INV",
            "DEC",
            "ASM",
            "REQ",
            "RLZ",
            "PRF",
        ):
            return tail
    return bare


def _walk_entries(
    tmp_path: Path, root: Path, block: dict[str, Any]
) -> dict[str, list[tuple[str, str, str]]]:
    """Every seed's rows: from the block, then through each continuation it hands out."""

    selection = resolve_published_memory_tree(_context(tmp_path, root))
    index_path = getattr(selection, "database_path", None)
    tree_key = getattr(getattr(selection, "memory_tree", None), "tree_key", None)

    def resolve(value: str) -> str:
        if index_path is None or tree_key is None:
            return value
        with KnowledgeIndex(index_path, expected_key=tree_key) as index:
            resolved = index.text_id(value) or value
        bare, _, _suffix = resolved.partition("@")
        if "@" in resolved and bare.split("-", 1)[0] in (
            "INV",
            "FAM",
            "DEC",
            "ASM",
            "REQ",
            "RLZ",
            "PRF",
        ):
            return bare
        return resolved

    walked: dict[str, list[tuple[str, str, str]]] = {}
    for entry in block["seeds"]:
        if entry["state"] == "page":
            walked.setdefault(_seed_key(entry["seed"]), []).extend(_item_keys(entry, resolve))
        view, continuation = entry.get("continuationView"), entry.get("continuation")
        while continuation is not None:
            response = _read(tmp_path, root, view=view, continuation=continuation)
            assert response["state"] == "page", response
            if response.get("overflow"):
                # Tail beyond the mounted bound: the overflow short form keeps the
                # contract but no rows and no next token, so rows resolve through
                # the same domain body the mounted tool serves (explicitly marked
                # here, never as pointer retrieval).
                assert response["resultKept"] is False, response
            else:
                # MIK-R108: answers no longer carry the count; the measured fact is recomputed here.
                assert response_tokens(response) <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS
            body = _read_body(tmp_path, root, view=view, continuation=continuation)
            assert body["state"] == "page", body
            page = body["payload"]
            walked.setdefault(_seed_key(page["seed"]), []).extend(_item_keys(page, resolve))
            view, continuation = page["continuationView"], body.get("continuation")
    return walked


def test_identity_seeds_on_a_tree_keep_the_scope_read_beside_the_leaf(
    tmp_path: Path,
) -> None:
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
    assert {entry["continuationView"] for entry in collapsed} == {
        "invariant",
        "source_context",
    }
    walked = _walk_entries(tmp_path, root, block)
    assert len(walked) == len(seeds)
    context = open_read_context(selection.database_path, INDEX_REPOSITORY_ID)
    with KnowledgeIndex(selection.database_path) as _resolve_index:

        def _resolve(value: str) -> str:
            return _resolve_index.text_id(value) or value

        for seed in seeds:
            keys = walked[_seed_key(seed.model_dump(mode="json"))]
            # Keys carry the role beside kind and ID: a membership names its
            # member record (kind invariant) with the member role, so it keys
            # apart from the revision row for the same record. No exact row
            # repeats anywhere.
            assert len(keys) == len(set(keys)), seed
            if isinstance(seed, PathSeed):
                with KnowledgeIndex(selection.database_path) as index:
                    structure = select_leaf(index, seed.path)
                assert structure is not None
                assert len(keys) == len(structure.order), seed
            else:
                scope = select_knowledge_scope(selection.database_path, context, seed)
                assert isinstance(scope, SelectedScope)
                # The walked rows are exactly the scope's items, in either
                # shape: raw items mirror to the projected vocabulary with
                # the implied role, so no gap and no extra row survives.
                mirror = {
                    "invariant_revision": ("invariant", ""),
                    "family_revision": ("family", ""),
                    "family_membership": ("invariant", "member"),
                    "realization_claim": ("realization", ""),
                    "advertised_family": ("family", ""),
                }
                expected = set()
                for item in scope.items:
                    dumped = item.model_dump(mode="json")
                    raw_kind = dumped.get("kind")
                    if not isinstance(raw_kind, str):
                        continue
                    kind, role = mirror.get(raw_kind, (raw_kind, ""))
                    expected.add(
                        (
                            str(kind),
                            _identify_item(dumped, raw_kind, _resolve),
                            str(role),
                        )
                    )
                assert set(keys) == expected, seed
