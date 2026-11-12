"""Route-chain family retrieval (MIK-R05): the families a path lies under, returned compactly.

* **The chain** is the path's directory and every ancestor up to ``.``, never a child or a sibling;
  a family is found through any of its routes on it, nearest first, and a retired one never.
* **Compact entries** after the MIK-R01 content, each saying whether a member is at the seed path;
  a path with no entry still returns them, and a path no route covers states
  ``no_governing_family``.
* **Expansion**: a family seed returns the full MIK-R01 family content.
* **Budget**: chain entries are rows of the same paged walk, within the one threshold.
* A ``read_ar_files`` naming never shortens: the server keeps no per-session
  state, so a repeated read names the same files again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_leaf import select_leaf
from agents_remember.application.knowledge_leaf.chain import chain_directory
from agents_remember.application.knowledge_paging import KNOWLEDGE_PAGE_THRESHOLD_TOKENS
from agents_remember.application.knowledge_paging.threshold import response_tokens
from agents_remember.application.published_intent import published_intent_block
from agents_remember.application.read_files import read_ar_files_tool
from agents_remember.memory.knowledge_index import KnowledgeIndex, text_uuid
from agents_remember.models.knowledge.continuation import (
    decode_continuation,
    encode_continuation,
)
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    record_path,
)
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import bound_for
from knowledge_index_test_support import commit_all, init_repository, write_document
from test_knowledge_leaf_read import (
    SEED,
    _context,
    _graph,
    _invariant,
    _read,
    _read_body,
    _shape,
    _sidecar,
)
from test_read_ar_files import REPO, _build_context, _make_config


def _family(
    root: Path, identifier: str, members: list[str], routes: list[str], **fields: str
) -> None:
    """One family record; ``fields`` overrides its ``status``, ``title`` or ``guarantee``."""

    write_document(
        root,
        record_path("family", identifier, "x"),
        {
            "schema": "ar-family/v1",
            "id": identifier,
            "origin": {"task": "260928-MIK", "leaf": "260928-MIK-L05"},
            "revision": 1,
            "status": "accepted",
            "title": f"Title of {identifier}",
            "guarantee": f"{identifier} guarantees its members together.",
            "members": members,
            "routes": routes,
            "admission": "legacy-unassessed",
            **fields,
        },
    )


def _tree(root: Path) -> None:
    init_repository(root)
    write_document(root, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "x"})


def _chain(rows: list[dict[str, Any]]) -> list[tuple[str, list[str]]]:
    return [(row["id"], row["via"]) for row in rows if row["kind"] == "chain_family"]


def _nested(root: Path) -> None:
    """Families at ``.``, ``a``, ``a/b`` (+ ``a``), ``a/b/c``, a sibling, a child, a retired one."""

    _tree(root)
    _invariant(root, "INV-N00001")
    for identifier, routes in (
        ("FAM-C00000", ["."]),
        ("FAM-C11111", ["a"]),
        ("FAM-C22222", ["a/b", "a"]),
        ("FAM-C33333", ["a/b/c"]),
        ("FAM-C44444", ["a/x"]),
        ("FAM-C55555", ["a/b/c/d"]),
    ):
        _family(root, identifier, ["INV-N00001"], routes)
    _family(root, "FAM-C66666", ["INV-N00001"], ["a/b"], status="retired")
    commit_all(root)


def test_the_chain_is_the_directory_and_its_ancestors_never_a_child_or_a_sibling(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    _nested(root)
    block = published_intent_block(_context(tmp_path, root), ["a/b/c/new.py"])
    page = block["seeds"][0]
    assert page["state"] == "page", page
    # Nearest route first; a family with two routes on the chain is one entry through both; the
    # child (a/b/c/d), the sibling (a/x) and the retired family are not on it.
    assert _chain(page["rows"]) == [
        ("FAM-C33333", ["a/b/c"]),
        ("FAM-C22222", ["a/b", "a"]),
        ("FAM-C11111", ["a"]),
        ("FAM-C00000", ["."]),
    ]
    assert page["routeChain"] == {
        "directory": "a/b/c",
        "links": 4,
        "derivation": "mechanical",
        "state": "governed",
        "families": 4,
    }
    assert page["counts"]["chainFamilies"] == 4 == page["counts"]["rowsTotal"]
    # Ties at one distance fall back to the family ID; a file at the root sees only ``.``.
    sibling = published_intent_block(_context(tmp_path, root), ["a/x/y/z.py"])["seeds"][0]
    assert _chain(sibling["rows"]) == [
        ("FAM-C44444", ["a/x"]),
        ("FAM-C11111", ["a"]),
        ("FAM-C22222", ["a"]),
        ("FAM-C00000", ["."]),
    ]
    at_root = published_intent_block(_context(tmp_path, root), ["setup.py"])["seeds"][0]
    assert _chain(at_root["rows"]) == [("FAM-C00000", ["."])]
    assert at_root["routeChain"]["directory"] == "." and at_root["routeChain"]["links"] == 1
    assert chain_directory("a/b/c/new.py") == "a/b/c" and chain_directory("setup.py") == "."


_F4_ENTRY = {
    "kind": "chain_family",
    "id": "FAM-F44444",
    "revision": 1,
    "title": "Title of FAM-F44444",
    "guarantee": "FAM-F44444 guarantees its members together.",
    "routes": ["src"],
    "memberCount": 1,
    "via": ["src"],
    "memberAtSeed": False,
    "expand": {
        "operation": "knowledge_read",
        "view": "source_context",
        "familyRevisionId": "FAM-F44444",
    },
}


def _graph_with_a_routed_neighbour(root: Path) -> None:
    """L01's graph, plus ``FAM-F44444`` = {X} routed at ``src`` with no member at the seed."""

    _graph(root)  # F1 {A, C, D} at src, F2 {B, C, E, retired R} at ., F3 {C, D, X} at lib
    _family(root, "FAM-F44444", ["INV-XXXXXX"], ["src"])
    commit_all(root)


def _first_chain_row(rows: list[dict[str, Any]]) -> int:
    return next(
        index
        for index, row in enumerate(rows)
        if row["kind"] == "chain_family"
        or (row["kind"] == "family" and (row.get("role") or "") == "chain")
    )


def test_chain_entries_are_compact_after_the_leaf_and_name_a_member_at_the_seed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    _graph_with_a_routed_neighbour(root)
    block = published_intent_block(_context(tmp_path, root), [SEED])
    page = block["seeds"][0]
    rows = page["rows"]
    first = _first_chain_row(rows)
    # Rule 5: every chain entry comes after the whole MIK-R01 content.
    assert rows[first - 1]["kind"] == "advertised_family"
    assert _chain(rows) == [
        ("FAM-F11111", ["src"]),
        ("FAM-F44444", ["src"]),
        ("FAM-F22222", ["."]),
    ]
    entries = {row["id"]: row for row in rows[first:]}
    assert len(entries) == len(rows) - first
    assert entries["FAM-F44444"] == _F4_ENTRY
    # Every entry is compact: no member list, no statement.
    assert [set(row) for row in entries.values()] == [set(_F4_ENTRY)] * 3
    with KnowledgeIndex(Path(block["datasetPath"])) as index:
        structure = select_leaf(index, SEED)
    assert structure is not None
    assert len(structure.order) == page["counts"]["rowsTotal"]


def test_a_chain_entry_flags_a_family_expanded_above_and_counts_live_members(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    _graph_with_a_routed_neighbour(root)
    rows = published_intent_block(_context(tmp_path, root), [SEED])["seeds"][0]["rows"]
    entries = {row["id"]: row for row in rows if row["kind"] == "chain_family"}
    # A family with a member at the seed was expanded above and is not repeated; the entry says so.
    # A retired member (F2's R) is not counted.
    assert {key: (row["memberAtSeed"], row["memberCount"]) for key, row in entries.items()} == {
        "FAM-F11111": (True, 3),
        "FAM-F44444": (False, 1),
        "FAM-F22222": (True, 3),
    }
    assert ("family_header", "FAM-F11111") in _shape(rows)


def test_entries_no_route_covers_state_no_governing_family(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    _graph(root)
    # F2 leaves ``.``: nothing covers ``tests/``, where INV-DDDDDD's proof is.
    _family(root, "FAM-F22222", ["INV-BBBBBB", "INV-CCCCCC", "INV-EEEEEE"], ["src"])
    commit_all(root)
    ungoverned = published_intent_block(_context(tmp_path, root), ["tests/test_d.py"])["seeds"][0]
    # A state, not an error: the page is the MIK-R01 content, with no chain entry and no
    # registration statement.
    assert ungoverned["state"] == "page"
    assert "registration" not in ungoverned
    assert ungoverned["routeChain"]["state"] == "no_governing_family"
    assert _shape(ungoverned["rows"])[0] == ("member", "INV-DDDDDD")
    assert _chain(ungoverned["rows"]) == []
    assert ungoverned["counts"]["chainFamilies"] == 0


def test_the_conforming_example_returns_the_governing_family_of_an_unattributed_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    _tree(root)
    _invariant(root, "INV-W00001")
    worktrees = "mcp/src/agents_remember/worktrees"
    _family(
        root,
        "FAM-W0RKTR",
        ["INV-W00001"],
        [worktrees],
        title="attribution-and-landing-pairing",
    )
    _sidecar(root, f"{worktrees}/landing.py", [("RLZ-W00001", "INV-W00001")])
    commit_all(root)
    new_helper = f"{worktrees}/new_helper.py"
    block = published_intent_block(_context(tmp_path, root), [new_helper])["seeds"][0]
    # Non-conforming would be registration_absent alone: the family comes back, compactly.
    assert block["state"] == "page", block
    assert block["registration"]["state"] == "registration_absent"
    assert [(row["kind"], row["title"], row["memberAtSeed"]) for row in block["rows"]] == [
        ("chain_family", "attribution-and-landing-pairing", False)
    ]
    read = _read(tmp_path, root, view="source_context", source_path=new_helper)
    assert read["state"] == "page", read
    assert [(row["kind"], row["id"], row.get("role")) for row in read["payload"]["rows"]] == [
        ("family", "FAM-W0RKTR", "chain")
    ]
    assert read["payload"]["routeChain"] == block["routeChain"]


def test_a_family_seed_returns_the_full_family_content(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    _graph(root)
    path_read = _read(tmp_path, root, view="source_context", source_path=SEED)
    by_key = {
        (row["kind"], row["id"], row.get("role")): row for row in path_read["payload"]["rows"]
    }
    assert ("family", "FAM-F22222", "chain") in by_key
    # The row names the family; the family is read by its named ID.
    family = _read(tmp_path, root, view="source_context", family_revision_id="FAM-F22222")
    assert family["state"] == "page", family
    rows = family["payload"]["rows"]
    assert family["payload"]["seed"] == {"kind": "family", "id": "FAM-F22222"}
    assert "routeChain" not in family["payload"]
    # The header, every live member with its entries, then the members' other families.
    assert [(row["kind"], row["id"], row.get("role")) for row in rows] == [
        ("family", "FAM-F22222", "header"),
        ("invariant", "INV-BBBBBB", "member"),
        ("realization", "RLZ-B00001", None),
        ("invariant", "INV-CCCCCC", "member"),
        ("realization", "RLZ-C00001", None),
        ("invariant", "INV-EEEEEE", "member"),
        ("realization", "RLZ-E00001", None),
        ("family", "FAM-F11111", "advertised"),
        ("family", "FAM-F33333", "advertised"),
    ]
    # The same family content the path's leaf read names for it.
    assert [(row["kind"], row["id"], row.get("role")) for row in rows[:7]] == [
        ("family", "FAM-F22222", "header"),
        ("invariant", "INV-BBBBBB", "member"),
        ("realization", "RLZ-B00001", None),
        ("invariant", "INV-CCCCCC", "member"),
        ("realization", "RLZ-C00001", None),
        ("invariant", "INV-EEEEEE", "member"),
        ("realization", "RLZ-E00001", None),
    ]
    assert family["page"]["selectionPolicy"] == "family-complete-leaf"


def _family_manifest(tmp_path: Path, root: Path, named: str) -> str | None:
    """The manifest of a family seed named ``named``, or ``None`` when it is refused."""

    read = _read(tmp_path, root, view="source_context", family_revision_id=named)
    assert read["state"] in ("page", "refused"), read
    if read["state"] == "refused":
        assert read["refusalCode"] == "selector_absent"
        return None
    return read["page"]["manifestDigest"]


def test_a_family_seed_is_named_by_id_revision_or_projected_uuid(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    _graph(root)
    spellings = ("FAM-F22222", "FAM-F22222@1", text_uuid("revision", "FAM-F22222@1"))
    manifests = {_family_manifest(tmp_path, root, named) for named in spellings}
    # Every spelling of the tree's revision names the same seed ...
    assert len(manifests) == 1 and None not in manifests
    # ... and a revision the tree does not hold, or an unknown family, is selector_absent.
    assert _family_manifest(tmp_path, root, "FAM-F22222@2") is None
    assert _family_manifest(tmp_path, root, "FAM-F22222@") is None  # a bare ``@`` names none
    assert _family_manifest(tmp_path, root, "FAM-ZZZZZZ") is None


def _words(identifier: str, count: int) -> str:
    return f"{identifier} " + "holds together " * count


def _walk(
    tmp_path: Path, root: Path, first: dict[str, Any], family: str | None = None
) -> list[dict[str, Any]]:
    """Every row of a walk: ``first``'s page, then each ``knowledge_read`` continuation (each
    resumed naming ``family`` as ``familyRevisionId`` too, when given)."""

    # A public tool answer nests rows under ``payload``; a published-intent block seed
    # carries them at the top, and the domain body behind the mounted tool carries the
    # next token at its own top level. Tails the mounted bound does not retain arrive
    # as the overflow short form (contract only, no rows, no next token), so rows and
    # advancement resolve through that same domain body, explicitly marked, never as
    # pointer proof.
    page = first.get("payload", first)
    rows = list(page["rows"])
    continuation = first.get("continuation") or page.get("continuation")
    while continuation is not None:
        response = _read(
            tmp_path,
            root,
            view="source_context",
            continuation=continuation,
            family_revision_id=family,
        )
        assert response["state"] == "page", response
        if response.get("overflow"):
            assert response["resultKept"] is False, response
        else:
            # MIK-R108: answers no longer carry the count; the measured fact is recomputed here.
            assert response_tokens(response) <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS
        body = _read_body(
            tmp_path,
            root,
            view="source_context",
            continuation=continuation,
            family_revision_id=family,
        )
        assert body["state"] == "page", body
        rows.extend(body["payload"]["rows"])
        continuation = body.get("continuation")
    # The continued family's header reference is framing, not a counted
    # row: it repeats an identity the walk already returned, in either the
    # retired vocabulary or L109's family/role vocabulary (rule 2).
    return [
        row
        for row in rows
        if row["kind"] != "family_header_reference"
        and not (row["kind"] == "family" and (row.get("role") or "") == "reference")
    ]


_LONG_MEMBERS = [f"INV-B{number:05d}" for number in range(24)]


@pytest.fixture(scope="module")
def long_tree(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """``FAM-B16000``: 24 long members at ``src``, one realized at ``SEED``; and 24 one-member
    families with long guarantees routed at ``.``, so the leaf and its chain need several pages."""

    tmp_path = tmp_path_factory.mktemp("long")
    root = tmp_path / "memory"
    _tree(root)
    for identifier in _LONG_MEMBERS:
        _invariant(root, identifier)
        path = root / record_path("invariant", identifier, "x")
        document = {
            **json.loads(path.read_text()),
            "statement": _words(identifier, 160),
        }
        write_document(root, record_path("invariant", identifier, "x"), document)
    _family(root, "FAM-B16000", _LONG_MEMBERS, ["src"])
    _sidecar(root, SEED, [("RLZ-B00000", _LONG_MEMBERS[0])])
    for number, member in enumerate(_LONG_MEMBERS):
        family = f"FAM-G{number:05d}"
        _family(root, family, [member], ["."], guarantee=_words(family, 80))
    commit_all(root)
    return tmp_path, root


def test_chain_entries_count_toward_the_threshold_and_resume_like_the_leaf(
    long_tree: tuple[Path, Path],
) -> None:
    tmp_path, root = long_tree
    page = published_intent_block(_context(tmp_path, root), [SEED])["seeds"][0]
    assert page["continuation"] is not None  # the chain does not fit beside the leaf
    rows = _walk(tmp_path, root, page)
    keys = {f"{row['kind']}:{row['id']}:{row.get('role') or ''}" for row in rows}
    assert len(keys) == len(rows) == page["counts"]["rowsTotal"]  # each row exactly once
    # The 25 chain entries are the walk's last 25 rows, in the walker's raw
    # body vocabulary (chain_family); the projected family/chain form is
    # pinned on mounted answers elsewhere.
    assert _first_chain_row(rows) == len(rows) - 25
    assert {row["kind"] for row in rows[-25:]} == {"chain_family"}


def test_a_family_seed_walk_resumes_under_any_spelling_of_its_family(
    long_tree: tuple[Path, Path],
) -> None:
    tmp_path, root = long_tree
    family = _read(tmp_path, root, view="source_context", family_revision_id="FAM-B16000")
    # Rule 2 states the rows: the seed's members fit within the bound as
    # names (no text), and the mounted answer carries the continuation.
    assert family["state"] == "page", family
    assert count_response_tokens(family) <= bound_for("knowledge_read"), family
    assert "holds together " not in json.dumps(family["payload"]["rows"]), family
    assert family["continuation"] is not None, family
    first = _read_body(tmp_path, root, view="source_context", family_revision_id="FAM-B16000")
    assert first["state"] == "page" and first["continuation"] is not None
    # Review F4: the token binds the bare family ID; ``ID@rev`` and the projected UUID name it too.
    uuid = text_uuid("revision", "FAM-B16000@1")
    for named in ("FAM-B16000", "FAM-B16000@1", uuid):
        walked = _walk(tmp_path, root, first, family=named)
        # The shared walker yields raw body rows: memberships ride kind
        # member here; the projected invariant/member form is pinned on
        # mounted answers elsewhere.
        assert [row["kind"] for row in walked].count("member") == len(_LONG_MEMBERS)
    other = _read(
        tmp_path,
        root,
        view="source_context",
        continuation=first["continuation"],
        family_revision_id="FAM-G00000",
    )
    assert other["refusalCode"] == "continuation_binding_mismatch"


@pytest.mark.parametrize("named", ["FAM-B16000@99", "FAM-B16000@"])
def test_a_family_seed_walk_resumed_naming_a_revision_the_tree_lacks_is_refused(
    long_tree: tuple[Path, Path], named: str
) -> None:
    tmp_path, root = long_tree
    family = _read(tmp_path, root, view="source_context", family_revision_id="FAM-B16000")
    assert family["state"] == "page", family
    body = _read_body(tmp_path, root, view="source_context", family_revision_id="FAM-B16000")
    # Ruling R2-1: resuming is held to the named revision exactly as a fresh read is.
    resumed = _read(
        tmp_path,
        root,
        view="source_context",
        continuation=body["continuation"],
        family_revision_id=named,
    )
    fresh = _read(tmp_path, root, view="source_context", family_revision_id=named)
    assert (resumed["state"], resumed["refusalCode"]) == ("refused", "selector_absent")
    assert fresh["refusalCode"] == resumed["refusalCode"]


def test_a_continuation_minted_under_the_v1_policy_is_refused(
    long_tree: tuple[Path, Path],
) -> None:
    tmp_path, root = long_tree
    page = published_intent_block(_context(tmp_path, root), [SEED])["seeds"][0]
    token = decode_continuation(page["continuation"])
    assert token is not None and token.policy_version == "v2"
    # Ruling Q4: the chain rows are a new selection, so a v1 token no longer binds.
    stale = encode_continuation(token.model_copy(update={"policy_version": "v1"}))
    refused = _read(tmp_path, root, view="source_context", continuation=stale)
    assert refused["state"] == "refused"
    assert refused["refusalCode"] == "continuation_binding_mismatch"


@pytest.fixture
def lifecycle(tmp_path: Path) -> Any:
    root = tmp_path / "memory"
    _graph(root)
    _family(root, "FAM-F44444", ["INV-XXXXXX"], ["src"])
    commit_all(root)
    (root / "onboarding").mkdir(exist_ok=True)
    code = tmp_path / "workspace" / REPO
    code.mkdir(parents=True)
    config = _make_config(tmp_path, code)
    context = _build_context(
        code,
        root / "onboarding",
        coordination_root=config.coordination_root,
        storage_mode="external",
    )

    def read(paths: list[str]) -> dict[str, Any]:
        return read_ar_files_tool(
            config,
            repo_id=REPO,
            files=list(paths),
            _context=context,
        )

    yield root, read


def _served(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    kinds = ("chain_family", "served_earlier")
    return [(row["kind"], row["id"]) for row in rows if row["kind"] in kinds]


def test_read_ar_files_names_the_same_files_on_every_call(tmp_path: Path, lifecycle: Any) -> None:
    """A read names; it never shortens (MIK-R109 rule 1).

    The server keeps no per-session state about what it named: a repeated
    read names the same files with the same cards, and ``refresh`` is a
    removed argument, refused by name.
    """

    root, read = lifecycle
    first = read([SEED])
    assert first["state"] == "named", first
    assert [entry["path"] for entry in first["files"]] == [SEED]
    again = read([SEED])
    assert again["files"] == first["files"]
    assert "served_earlier" not in json.dumps(again)
    # knowledge_read names the same governing families the chain computes.
    direct = _read(tmp_path, root, view="source_context", source_path=SEED)
    assert direct["state"] == "page", direct
    assert [
        row["id"]
        for row in direct["payload"]["rows"]
        if row["kind"] == "family" and (row.get("role") or "") == "chain"
    ] == [
        "FAM-F11111",
        "FAM-F44444",
        "FAM-F22222",
    ]
