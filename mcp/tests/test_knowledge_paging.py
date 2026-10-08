"""Bounded continuation accepted by the mounted read (MIK-R02).

Every bounded read of a converted memory tree is cut by one token threshold, and the continuation a
page carries is accepted by ``knowledge_read`` whichever surface minted it:

* **Cross-surface walk.** The packet's conforming example -- a 40-member family whose first page
  comes from the published-intent block of ``read_ar_files`` and whose later pages come from
  ``knowledge_read`` -- returns every selected row exactly once, and a page that continues the
  family starts with its header reference row. Since MIK-R01 the pages of a path are the
  family-complete leaf read's rows (``payload.rows``); the family header reference is their literal
  first row.
* **Threshold adherence** over randomized families, on both the scope pages and the view pages.
* **An oversized row** is returned alone and flagged.
* **Binding mismatches** are refused with a named code and no page, the changed tree named.
* **Projection over the artifact limit** (carried from the L23 review) continues in parts, or is
  refused, and never raises.
"""

from __future__ import annotations

import base64
import json
import random
import string
import zlib
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agents_remember.application.knowledge_leaf import select_leaf
from agents_remember.application.knowledge_paging import (
    KNOWLEDGE_PAGE_THRESHOLD_TOKENS,
    KNOWLEDGE_PAGE_TOKENIZER,
)
from agents_remember.application.knowledge_paging.threshold import response_tokens
from agents_remember.application.published_intent import (
    published_intent_block,
    select_knowledge_dataset,
)
from agents_remember.kernel.coordination_context.models import CoordinationContext
from agents_remember.mcp.tools.knowledge import (
    ReadToolRequest,
    knowledge_read_payload,
)
from agents_remember.memory.knowledge_index import KnowledgeIndex, text_uuid
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    file_sidecar_path,
    record_path,
)
from knowledge_index_test_support import (
    anchor,
    commit_all,
    git,
    init_repository,
    write_document,
)

SEED_PATH = "src/pkg/seed.py"
# Seeded and bounded: enough trials to vary member counts, row sizes and scripts, and few enough
# that the case stays in the unit lane's time budget.
RANDOMIZED_TRIALS = 8
_ALPHABET = string.ascii_lowercase + "äöüßéñçøåæ" + "книгаданные" + "数据知识"
FAMILY = "FAM-PG0001"


def _member(number: int) -> str:
    return f"INV-P{number:05d}"


def _member_path(number: int, directory: str = "src/pkg") -> str:
    return SEED_PATH if number == 0 else f"{directory}/member_{number}.py"


def write_family_tree(
    root: Path,
    statements: list[str],
    *,
    applicability: str = "Always.",
    blobs: Mapping[str, str] | None = None,
    directory: str = "src/pkg",
) -> None:
    """A converted tree: one family of ``len(statements)`` members, one realization per member.

    Member 0 is realized at :data:`SEED_PATH`; every other member in a file of its own.
    """

    write_document(root, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "x"})
    for number, statement in enumerate(statements):
        identifier = _member(number)
        write_document(
            root,
            record_path("invariant", identifier, f"member-{number}"),
            {
                "schema": "ar-invariant/v1",
                "id": identifier,
                "origin": {"task": "260928-MIK", "leaf": "260928-MIK-L02"},
                "revision": 1,
                "status": "accepted",
                "statement": statement,
                "applicability": applicability,
                "conditions": [f"condition of member {number}"],
                "exclusions": [],
                "supersedes": [],
                "admission": "legacy-unassessed",
            },
        )
        path = _member_path(number, directory)
        write_document(
            root,
            file_sidecar_path(path),
            {
                "schema": "ar-onboarding-file/v1",
                "path": path,
                "references": {},
                "realizes": [
                    {
                        "id": f"RLZ-P{number:05d}",
                        "invariant": identifier,
                        "anchor": anchor(f"member_{number}", **_blob(blobs, path)),
                        "role": "enforcement",
                        "rationale": f"member_{number} enforces {identifier}.",
                    }
                ],
            },
        )
    write_document(
        root,
        record_path("family", FAMILY, "paged-family"),
        {
            "schema": "ar-family/v1",
            "id": FAMILY,
            "origin": {"task": "260928-MIK", "leaf": "260928-MIK-L02"},
            "revision": 1,
            "status": "accepted",
            "title": "A family large enough to page",
            "guarantee": "Every member holds together.",
            "members": [_member(number) for number in range(len(statements))],
            "routes": ["src/pkg"],
            "admission": "legacy-unassessed",
        },
    )


def _blob(blobs: Mapping[str, str] | None, path: str) -> dict[str, str]:
    return {} if blobs is None else {"blob": blobs[path]}


def _tree(tmp_path: Path, statements: list[str], **options: Any) -> Path:
    root = tmp_path / "memory"
    init_repository(root)
    write_family_tree(root, statements, **options)
    commit_all(root)
    return root


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


def _read(
    tmp_path: Path, root: Path, *, workspace: Path | None = None, **arguments: Any
) -> dict[str, Any]:
    return knowledge_read_payload(
        ReadToolRequest(memory_root=str(root), **arguments),
        workspace_root=None if workspace is None else str(workspace),
        coordination_root=str(tmp_path / "coordination"),
    )


def _statements(count: int, words: int = 60) -> list[str]:
    return [f"Member {number} " + " ".join(["holds"] * words) + "." for number in range(count)]


def _within_threshold(response: dict[str, Any]) -> bool:
    return response_tokens(response) <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS


def _counted(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A leaf page's returned rows: its header reference row repeats an identity already returned."""

    return [row for row in rows if row["kind"] != "family_header_reference"]


def _leaf_key(row: dict[str, Any]) -> str:
    return f"{row['kind']}:{row['id']}:{row.get('via', '')}"


def _walk_scope(tmp_path: Path, root: Path) -> list[dict[str, Any]]:
    """Page 1 from the published-intent block, every later page from ``knowledge_read``."""

    block = published_intent_block(_context(tmp_path, root), [SEED_PATH])
    first = block["seeds"][0]
    assert first["state"] == "page", first
    assert first["continuationOperation"] == "knowledge_read"
    pages = [first]
    continuation = first["continuation"]
    while continuation is not None:
        response = _read(tmp_path, root, view=first["continuationView"], continuation=continuation)
        assert response["state"] == "page", response
        assert response["tokens"] <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS or response["page"].get(
            "flags"
        ) == ["oversized_row"]
        pages.append({**response["payload"], "page": response["page"]})
        continuation = response.get("continuation")
    return pages


def test_a_forty_member_family_pages_from_read_ar_files_through_knowledge_read_exactly_once(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path, _statements(40, words=150))
    pages = _walk_scope(tmp_path, root)
    assert len(pages) >= 3
    items = [item for page in pages for item in _counted(page["rows"])]
    total = pages[0]["page"]["total"]
    ids = [_leaf_key(item) for item in items]
    assert len(ids) == total == len(set(ids))
    assert {item["kind"] for item in items} >= {"family_header", "member", "realization"}
    returned = 0
    for page in pages:
        facts = page["page"]
        assert facts["threshold"] == {
            "tokens": KNOWLEDGE_PAGE_THRESHOLD_TOKENS,
            "tokenizer": KNOWLEDGE_PAGE_TOKENIZER,
        }
        assert _within_threshold(page) or facts.get("flags") == ["oversized_row"]
        returned += len(_counted(page["rows"]))
        assert (facts["returned"], facts["remaining"]) == (returned, total - returned)
        assert facts["enumerationComplete"] is (facts["remaining"] == 0)
        first = _counted(page["rows"])[0]
        if facts["start"] > 0 and first["kind"] != "family_header":
            # A page that continues a family starts with its header reference as a literal row.
            reference = page["rows"][0]
            assert reference["kind"] == "family_header_reference"
            assert (reference["id"], reference["title"]) == (
                FAMILY,
                "A family large enough to page",
            )
    assert pages[-1]["enumerationComplete"] is True and pages[-1].get("continuation") is None
    assert [page["page"]["manifestDigest"] for page in pages] == [pages[0]["manifestDigest"]] * len(
        pages
    )


def _walk_view(tmp_path: Path, root: Path, **subject: Any) -> list[dict[str, Any]]:
    responses = [_read(tmp_path, root, **subject)]
    while responses[-1].get("continuation") is not None:
        assert responses[-1]["state"] == "view", responses[-1]
        responses.append(
            _read(tmp_path, root, view=subject["view"], continuation=responses[-1]["continuation"])
        )
    assert responses[-1]["state"] == "view", responses[-1]
    return responses


def _row_key(row: dict[str, Any]) -> str:
    return json.dumps([row["subject"], row.get("fact_kind"), row["order"]["position"]])


def test_pages_stay_within_the_threshold_over_randomized_families(tmp_path: Path) -> None:
    rng = random.Random(20260929)
    for trial in range(RANDOMIZED_TRIALS):
        count = rng.randint(3, 45)
        statements = [
            "Member "
            + str(number)
            + " "
            + " ".join(
                "".join(rng.choices(_ALPHABET, k=rng.randint(3, 11)))
                for _ in range(rng.randint(1, 600))
            )
            for number in range(count)
        ]
        base = tmp_path / f"trial-{trial}"
        base.mkdir()
        root = _tree(base, statements)
        scope = _walk_scope(base, root)
        scope_ids = [_leaf_key(item) for page in scope for item in _counted(page["rows"])]
        assert len(scope_ids) == len(set(scope_ids)) == scope[0]["page"]["total"]
        family = text_uuid("revision", f"{FAMILY}@1")
        views = _walk_view(base, root, view="family", family_revision_id=family)
        rows = [_row_key(row) for response in views for row in response["payload"]["rows"]]
        assert len(rows) == len(set(rows)) == views[0]["page"]["total"]
        for response in views:
            assert response["tokens"] <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS, response["page"]
            assert "flags" not in response["page"]
            start = response["page"]["start"]
            if start > 0:
                reference = response["page"]["headerReference"]
                assert reference["kind"] == "family_header_reference"
                assert (reference["family"], reference["title"]) == (
                    f"{FAMILY}@1",
                    "A family large enough to page",
                )
        assert views[-1]["page"]["enumerationComplete"] is True


def test_a_row_larger_than_the_threshold_is_returned_alone_and_flagged(tmp_path: Path) -> None:
    rng = random.Random(7)
    noise = "".join(rng.choices(string.ascii_letters + string.digits, k=PROSE_MAX_LENGTH - 20))
    root = _tree(tmp_path, [f"Big {noise}", "Small member."], applicability=noise)
    pages = _walk_scope(tmp_path, root)
    flagged = [page for page in pages if page["page"].get("flags") == ["oversized_row"]]
    assert flagged, [page["page"] for page in pages]
    for page in flagged:
        assert len(_counted(page["rows"])) == 1
        assert noise in json.dumps(_counted(page["rows"])[0])  # returned whole, never cut short
    ids = [_leaf_key(item) for page in pages for item in _counted(page["rows"])]
    assert len(ids) == len(set(ids)) == pages[0]["page"]["total"]


def _token_fields(token: str) -> dict[str, Any]:
    body = token.removeprefix("kc2.")
    return json.loads(zlib.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))))


def _token(fields: dict[str, Any]) -> str:
    text = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return "kc2." + base64.urlsafe_b64encode(zlib.compress(text.encode())).decode().rstrip("=")


def test_a_continuation_whose_binding_does_not_hold_is_refused_with_no_page(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path, _statements(40))
    family = text_uuid("revision", f"{FAMILY}@1")
    first = _read(tmp_path, root, view="family", family_revision_id=family)
    token = first["continuation"]
    assert token is not None and token.startswith("kc2.")
    fields = _token_fields(token)
    minted_view = f"family:{first['snapshot']}:1"
    cases: dict[str, tuple[dict[str, Any], str, str]] = {
        "another view's walk": ({"view": "invariant", "continuation": token}, "unreadable", ""),
        "not a token": ({"view": "family", "continuation": "kc2.!!"}, "unreadable", ""),
        "a view token on a tree": (
            {"view": "family", "continuation": minted_view},
            "unreadable",
            "",
        ),
        "another threshold": (
            {"view": "family", "continuation": _token({**fields, "th": 4000})},
            "binding_mismatch",
            "4000",
        ),
        "another policy": (
            {"view": "family", "continuation": _token({**fields, "pv": "v0"})},
            "binding_mismatch",
            "v0",
        ),
        "another selection": (
            {"view": "family", "continuation": _token({**fields, "m": "0" * 64})},
            "binding_mismatch",
            "0" * 64,
        ),
        "past the end": (
            {"view": "family", "continuation": _token({**fields, "p": 10_000})},
            "binding_mismatch",
            "10000",
        ),
        "another seed": (
            {"view": "family", "continuation": token, "family_revision_id": text_uuid("x", "y")},
            "binding_mismatch",
            "family_revision_id",
        ),
    }
    for name, (arguments, code, named) in cases.items():
        refused = _read(tmp_path, root, **arguments)
        assert refused["state"] == "refused", name
        assert refused["refusalCode"] == f"continuation_{code}", (name, refused)
        assert named in refused["refusalDetail"], (name, refused["refusalDetail"])
        assert "payload" not in refused and "page" not in refused, name

    # The memory tree changed between pages: refused, naming the tree the read now selects.
    target = next((root / "knowledge" / "invariants").glob(f"{_member(3)}-*.json"))
    document = json.loads(target.read_text("utf-8"))
    target.write_text(
        json.dumps({**document, "statement": "Changed."}, indent=2) + "\n", encoding="utf-8"
    )
    changed = _read(tmp_path, root, view="family", continuation=token)
    assert changed["refusalCode"] == "continuation_binding_mismatch"
    assert fields["t"] in changed["refusalDetail"]
    fresh = _read(tmp_path, root, view="family", family_revision_id=family)
    assert fresh["memoryTree"]["treeId"] in changed["refusalDetail"]
    assert fresh["memoryTree"]["treeId"] != fields["t"]


def _walk_block(tmp_path: Path, root: Path, block: dict[str, Any]) -> dict[str, list[str]]:
    """Every seed's item ids: from the block, then through every continuation it hands out."""

    walked: dict[str, list[str]] = {}
    for entry in block["seeds"]:
        if entry["state"] == "page":
            walked.setdefault(entry["seed"]["path"], []).extend(
                _leaf_key(item) for item in _counted(entry["rows"])
            )
        view, continuation = entry.get("continuationView"), entry.get("continuation")
        while continuation is not None:
            response = _read(tmp_path, root, view=view, continuation=continuation)
            assert response["state"] == "page", response
            # Over the threshold only as a single row that is too large on its own: here a family
            # header whose currentness names every member at a deeply nested path.
            assert response["tokens"] <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS or (
                response["page"].get("flags") == ["oversized_row"]
                and response["page"]["rowsOnPage"] == 1
            )
            page = response["payload"]
            walked.setdefault(page["seed"]["path"], []).extend(
                _leaf_key(item) for item in _counted(page["rows"])
            )
            view, continuation = page["continuationView"], response.get("continuation")
    return walked


def _selection_size(tmp_path: Path, root: Path, path: str) -> int:
    selected = select_knowledge_dataset(root, coordination_root=tmp_path / "coordination")
    with KnowledgeIndex(selected.database_path) as index:
        structure = select_leaf(index, path)
    assert structure is not None
    return len(structure.order)


# A deep directory makes each deferred entry large enough that sixteen of them cannot all fit.
_DEEP = "src/" + "/".join(f"deeply_nested_package_level_{level:02d}" for level in range(24))


@pytest.mark.parametrize(
    ("seeds", "members", "directory"),
    [(5, 40, "src/pkg"), (16, 18, _DEEP)],
    ids=["mounted-maximum", "synthetic-16"],
)
def test_the_whole_knowledge_block_of_several_seeds_stays_within_the_threshold(
    tmp_path: Path, seeds: int, members: int, directory: str
) -> None:
    root = _tree(tmp_path, _statements(members, words=20), directory=directory)
    paths = [_member_path(number, directory) for number in range(seeds)]
    block = published_intent_block(_context(tmp_path, root), paths)
    # The block as a whole, envelope and currentness included, is what the threshold bounds.
    assert response_tokens(block) <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS
    assert block["threshold"]["tokens"] == KNOWLEDGE_PAGE_THRESHOLD_TOKENS
    states = [entry["state"] for entry in block["seeds"]]
    assert states[0] == "page" and "deferred" in states, states
    assert all(not entry.get("page", {}).get("flags") for entry in block["seeds"])
    if seeds == 16:
        # Sixteen deferred entries would not fit: the tail is one entry with one continuation.
        assert "seeds" in block["seeds"][-1] and len(block["seeds"]) < seeds
    walked = _walk_block(tmp_path, root, block)
    assert sorted(walked) == sorted(paths)
    for path, ids in walked.items():
        assert len(ids) == len(set(ids)) == _selection_size(tmp_path, root, path), path


def test_a_view_walk_resumes_in_its_own_ordering_and_refuses_another(tmp_path: Path) -> None:
    root = _tree(tmp_path, _statements(40))
    family = text_uuid("revision", f"{FAMILY}@1")
    first = _read(
        tmp_path, root, view="family", family_revision_id=family, ordering_input="registered_role"
    )
    token = first["continuation"]
    resumed = _read(tmp_path, root, view="family", continuation=token)
    assert resumed["state"] == "view", resumed
    orderings = {row["order"]["ordering_input"] for row in resumed["payload"]["rows"]}
    assert orderings == {"registered_role"}
    same = _read(
        tmp_path, root, view="family", continuation=token, ordering_input="registered_role"
    )
    assert same["state"] == "view"
    other = _read(
        tmp_path, root, view="family", continuation=token, ordering_input="stable_ordering"
    )
    assert other["refusalCode"] == "continuation_binding_mismatch"
    assert "orderingInput" in other["refusalDetail"]
    # A defaulted ordering is bound like a named one.
    default = _read(tmp_path, root, view="family", family_revision_id=family)
    declared = _read(
        tmp_path,
        root,
        view="family",
        continuation=default["continuation"],
        ordering_input="declared_priority",
    )
    assert declared["refusalCode"] == "continuation_binding_mismatch"


def _entry_states(rows: list[dict[str, Any]]) -> dict[str, str]:
    return {row["id"]: row["state"] for row in rows if row["kind"] == "realization"}


def test_a_resumed_leaf_walk_observes_entries_at_page_one_code_tree(tmp_path: Path) -> None:
    code = tmp_path / "code"
    init_repository(code)
    count = 30
    for number in range(count):
        target = code / _member_path(number)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"def member_{number}():\n    return {number}\n", encoding="utf-8")
    commit_all(code, "first")
    first_tree = git(code, "rev-parse", "HEAD^{tree}")
    blobs = {
        _member_path(number): git(code, "rev-parse", f"HEAD:{_member_path(number)}")
        for number in range(count)
    }
    root = _tree(tmp_path, _statements(count, words=150), blobs=blobs)
    page = published_intent_block(_context(tmp_path, root), [SEED_PATH])["seeds"][0]
    assert page["page"]["codeTreeId"] == first_tree
    assert page["continuation"] is not None  # the walk has resumed pages
    for number in range(count):  # the code moves on after page 1
        (code / _member_path(number)).write_text(f"def member_{number}():\n    return -1\n")
    commit_all(code, "second")
    second_tree = git(code, "rev-parse", "HEAD^{tree}")

    states: dict[str, str] = {}
    continuation = page["continuation"]
    while continuation is not None:
        response = _read(
            tmp_path, root, workspace=code, view="source_context", continuation=continuation
        )
        assert response["state"] == "page", response
        assert response["page"]["codeTreeId"] == first_tree
        # Currentness is observed at the walk's tree too, not at no tree (the caller named none).
        assert response["currentness"]["codeTree"]["treeId"] == first_tree
        states.update(_entry_states(response["payload"]["rows"]))
        continuation = response.get("continuation")
    assert states and set(states.values()) == {"current"}

    # The same entries read at the moved tree are stale, so the binding is what held them.
    moved: dict[str, str] = {}
    response = _read(
        tmp_path,
        root,
        view="source_context",
        source_path=SEED_PATH,
        code_tree_id=second_tree,
        repository_root=str(code),
    )
    while True:
        assert response["state"] == "page", response
        moved.update(_entry_states(response["payload"]["rows"]))
        if response.get("continuation") is None:
            break
        response = _read(
            tmp_path,
            root,
            view="source_context",
            continuation=response["continuation"],
            repository_root=str(code),
        )
    assert set(states) <= set(moved) and {moved[entry] for entry in states} == {"stale"}

    refused = _read(
        tmp_path,
        root,
        view="source_context",
        continuation=page["continuation"],
        code_tree_id=second_tree,
        repository_root=str(code),
    )
    assert refused["refusalCode"] == "continuation_binding_mismatch"
    assert first_tree in refused["refusalDetail"] and second_tree in refused["refusalDetail"]


def _code_repository(tmp_path: Path, count: int) -> tuple[Path, str, dict[str, str]]:
    code = tmp_path / "code"
    init_repository(code)
    for number in range(count):
        target = code / _member_path(number)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"def member_{number}():\n    return {number}\n", encoding="utf-8")
    commit_all(code, "first")
    blobs = {
        _member_path(number): git(code, "rev-parse", f"HEAD:{_member_path(number)}")
        for number in range(count)
    }
    return code, git(code, "rev-parse", "HEAD^{tree}"), blobs


def test_a_view_walk_is_bound_to_its_named_code_tree(tmp_path: Path) -> None:
    code, first_tree, blobs = _code_repository(tmp_path, 40)
    root = _tree(tmp_path, _statements(40), blobs=blobs)
    family = text_uuid("revision", f"{FAMILY}@1")
    first = _read(
        tmp_path,
        root,
        view="family",
        family_revision_id=family,
        code_tree_id=first_tree,
        repository_root=str(code),
    )
    assert first["page"]["codeTreeId"] == first_tree
    assert first["currentness"]["codeTree"]["treeId"] == first_tree
    (code / SEED_PATH).write_text("def member_0():\n    return -1\n", encoding="utf-8")
    commit_all(code, "second")
    second_tree = git(code, "rev-parse", "HEAD^{tree}")

    resumed = _read(
        tmp_path, root, workspace=code, view="family", continuation=first["continuation"]
    )
    assert resumed["state"] == "view", resumed
    assert resumed["page"]["codeTreeId"] == first_tree
    assert resumed["currentness"]["codeTree"]["treeId"] == first_tree
    other = _read(
        tmp_path,
        root,
        workspace=code,
        view="family",
        continuation=first["continuation"],
        code_tree_id=second_tree,
    )
    assert other["refusalCode"] == "continuation_binding_mismatch"
    assert first_tree in other["refusalDetail"] and second_tree in other["refusalDetail"]
    assert other["threshold"]["tokens"] == KNOWLEDGE_PAGE_THRESHOLD_TOKENS

    # The token names no path: a root that does not hold the walk's tree is refused by name.
    elsewhere = tmp_path / "elsewhere"
    init_repository(elsewhere)
    commit_all(elsewhere, "unrelated")
    moved = _read(
        tmp_path,
        root,
        view="family",
        continuation=first["continuation"],
        repository_root=str(elsewhere),
    )
    assert moved["refusalCode"] == "selected_input_unavailable"
    assert str(elsewhere) in moved["refusalDetail"] and "repositoryRoot" in moved["refusalDetail"]
    assert str(code) not in json.dumps(_token_fields(first["continuation"]))


def test_an_empty_ordering_is_refused_not_defaulted_on_every_path(tmp_path: Path) -> None:
    root = _tree(tmp_path, _statements(3))
    family = text_uuid("revision", f"{FAMILY}@1")
    tree = _read(tmp_path, root, view="family", family_revision_id=family, ordering_input="")
    assert tree["refusalCode"] == "unadmitted_ordering_input"
    assert tree["threshold"]["tokens"] == KNOWLEDGE_PAGE_THRESHOLD_TOKENS  # rule 1, refusals too
