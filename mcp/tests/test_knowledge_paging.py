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
import os
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
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.kernel.coordination_context.models import CoordinationContext
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
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
from tool_entry_point_world import EntryPointWorld

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
    # What the caller walks is the delivered answer, not the detail file
    # behind it: the hermetic coordination root keeps the log writes inside
    # the fixture, and the answer's own rows carry the paging position.
    coord = tmp_path / "coordination"
    config = McpRuntimeConfig(
        config_path=coord / "settings.json",
        coordination_root=coord,
        workspace_root=coord,
        transcript_root=coord / "logs" / "mcp",
    )
    call_arguments: dict[str, Any] = {
        "memoryRoot": str(root),
        **arguments,
    }
    with tool_call_context("knowledge_read", call_arguments, config=config):
        answer = knowledge_read_payload(
            ReadToolRequest(memory_root=str(root), **arguments),
            workspace_root=None if workspace is None else str(workspace),
            coordination_root=str(tmp_path / "coordination"),
        )
    assert isinstance(answer, dict) and answer, "paging fixture: unreadable answer"
    return answer


def _statements(count: int, words: int = 60) -> list[str]:
    return [f"Member {number} " + " ".join(["holds"] * words) + "." for number in range(count)]


def _within_threshold(response: dict[str, Any]) -> bool:
    return response_tokens(response) <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS


def _counted(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A leaf page's returned rows: its header reference row repeats an identity already returned."""

    return [
        row
        for row in rows
        if row["kind"] != "family_header_reference"
        and not (row["kind"] == "family" and row.get("role") == "reference")
    ]


_ENDPOINT_ROLES = frozenset({"member", "reference", "header", "advertised", "chain"})

_BLOCK_ENDPOINTS = {
    "member": ("invariant", "member"),
    "member_reference": ("invariant", "reference"),
    "family_header": ("family", "header"),
    "advertised_family": ("family", "advertised"),
    "chain_family": ("family", "chain"),
    "family_header_reference": ("family", "reference"),
}


def _leaf_key(row: dict[str, Any]) -> str:
    # Projected rows name their record with the endpoint riding the role;
    # block rows still carry structural kinds (and entry roles, which are
    # not endpoint roles). Both shapes key identically so a walk that
    # starts inline and continues projected counts one identity once.
    kind, role = row["kind"], row.get("role")
    if role in _ENDPOINT_ROLES and kind in (
        "invariant",
        "family",
        "realization",
        "decision",
        "proof",
    ):
        endpoint = role
    else:
        kind, endpoint = _BLOCK_ENDPOINTS.get(kind, (kind, None))
    return f"{kind}:{row['id']}:{row.get('via', '')}:{endpoint or ''}"


def _walk_scope(tmp_path: Path, root: Path) -> list[dict[str, Any]]:
    """Page 1 and every later page from ``knowledge_read``: what a caller walks."""

    first = _read(tmp_path, root, view="source_context", source_path=SEED_PATH)
    assert first["state"] == "page", first
    pages = [{**first["payload"], "page": first["page"]}]
    continuation = first.get("continuation")
    while continuation is not None:
        response = _read(tmp_path, root, view="source_context", continuation=continuation)
        assert response["state"] == "page", response
        assert response_tokens(response) <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS or response["page"].get(
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
    assert {item["kind"] for item in items} >= {
        "family",
        "invariant",
        "realization",
    }
    assert {(item["kind"], item.get("role")) for item in items} >= {
        ("family", "header"),
        ("invariant", "member"),
        ("realization", None),
    }
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
        if facts["start"] > 0 and (first["kind"], first.get("role")) != ("family", "header"):
            # A page that continues a family starts with its header reference as a literal row.
            reference = page["rows"][0]
            assert reference["kind"] == "family", reference
            assert reference.get("role") == "reference", reference
            assert reference["id"] == FAMILY
    assert pages[-1]["enumerationComplete"] is True and pages[-1].get("continuation") is None
    assert [page["page"]["manifestDigest"] for page in pages] == [pages[0]["manifestDigest"]] * len(
        pages
    )


def _walk_view(tmp_path: Path, root: Path, **subject: Any) -> list[dict[str, Any]]:
    responses = [_read(tmp_path, root, **subject)]
    while responses[-1].get("continuation") is not None:
        assert responses[-1]["state"] == "view", responses[-1]
        responses.append(
            _read(
                tmp_path,
                root,
                view=subject["view"],
                continuation=responses[-1]["continuation"],
            )
        )
    assert responses[-1]["state"] == "view", responses[-1]
    return responses


def _row_key(row: dict[str, Any]) -> str:
    return json.dumps([row.get("kind"), row.get("id")])


def test_pages_stay_within_the_threshold_over_randomized_families(
    tmp_path: Path,
) -> None:
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
            assert response_tokens(response) <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS, response["page"]
            assert "flags" not in response["page"]
            start = response["page"]["start"]
            if start > 0:
                reference = response["page"]["headerReference"]
                # MIK-R109 round-4 F11: a header reference names a family --
                # its kind is the family's, never the row's structural role.
                assert reference["kind"] == "family", reference
                assert reference["family"] == f"{FAMILY}@1", reference
                assert reference["state"] in {
                    "current",
                    "stale",
                    "unverifiable",
                    "unrealized",
                }, reference
        assert views[-1]["page"]["enumerationComplete"] is True


def test_a_row_larger_than_the_threshold_is_returned_alone_and_flagged(
    tmp_path: Path,
) -> None:
    rng = random.Random(7)
    noise = "".join(rng.choices(string.ascii_letters + string.digits, k=PROSE_MAX_LENGTH - 20))
    root = _tree(tmp_path, [f"Big {noise}", "Small member."], applicability=noise)
    pages = _walk_scope(tmp_path, root)
    # Large prose projects to small names: the prose-heavy row is named,
    # not flagged -- it fits the delivered bound once its prose stays
    # behind the answer's log.
    flagged = [page for page in pages if page["page"].get("flags") == ["oversized_row"]]
    assert not flagged, [page["page"] for page in pages]
    for page in pages:
        assert _within_threshold(page), page["page"]
        for row in _counted(page["rows"]):
            assert noise not in json.dumps(row), row["id"]
    ids = [_leaf_key(item) for page in pages for item in _counted(page["rows"])]
    assert len(ids) == len(set(ids)) == pages[0]["page"]["total"]


def test_a_row_whose_names_exceed_the_bound_is_returned_alone_and_flagged() -> None:
    # The pager's oversized branch, against the delivered measure: one row
    # whose projected names exceed the bound is returned alone and flagged,
    # so the caller knows the row itself does not fit.
    from agents_remember.application.knowledge_paging.pager import (  # noqa: PLC0415
        CutBudget,
        PageCut,
        PageRow,
        cut_page,
    )

    rows = [PageRow({"id": "only"})]

    def render(cut: PageCut) -> dict[str, Any]:
        return {"rows": [dict(rows[index].body) for index in range(cut.start, cut.end)]}

    def project(body: dict[str, Any]) -> dict[str, Any]:
        return {**body, "padding": "x" * (KNOWLEDGE_PAGE_THRESHOLD_TOKENS * 10)}

    cut, body = cut_page(rows, 0, render, budget=CutBudget(project=project))
    assert isinstance(cut, PageCut) and cut.oversized
    assert body["rows"] == [{"id": "only"}]


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
        "another view's walk": (
            {"view": "invariant", "continuation": token},
            "unreadable",
            "",
        ),
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
            {
                "view": "family",
                "continuation": token,
                "family_revision_id": text_uuid("x", "y"),
            },
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
        json.dumps({**document, "statement": "Changed."}, indent=2) + "\n",
        encoding="utf-8",
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
            assert response_tokens(response) <= KNOWLEDGE_PAGE_THRESHOLD_TOKENS or (
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


def test_a_view_walk_resumes_in_its_own_ordering_and_refuses_another(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path, _statements(40))
    family = text_uuid("revision", f"{FAMILY}@1")
    first = _read(
        tmp_path,
        root,
        view="family",
        family_revision_id=family,
        ordering_input="registered_role",
    )
    token = first["continuation"]
    resumed = _read(tmp_path, root, view="family", continuation=token)
    assert resumed["state"] == "view", resumed
    # The walk resumes where it stopped: the resumed rows name what the first page did
    # not, with no repeats, under the same manifest.
    first_ids = [row["id"] for row in first["payload"]["rows"]]
    resumed_ids = [row["id"] for row in resumed["payload"]["rows"]]
    assert not set(first_ids) & set(resumed_ids)
    assert resumed["page"]["manifestDigest"] == first["page"]["manifestDigest"]
    same = _read(
        tmp_path,
        root,
        view="family",
        continuation=token,
        ordering_input="registered_role",
    )
    assert same["state"] == "view"
    other = _read(
        tmp_path,
        root,
        view="family",
        continuation=token,
        ordering_input="stable_ordering",
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


def test_a_resumed_leaf_walk_observes_entries_at_page_one_code_tree(
    tmp_path: Path,
) -> None:
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
            tmp_path,
            root,
            workspace=code,
            view="source_context",
            continuation=continuation,
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


def test_a_family_read_at_a_moved_tree_states_stale_values(
    tmp_path: Path,
) -> None:
    # MIK-R109 round-4 F11: evaluated states arrive as values on every
    # carrier. The members recorded at the first tree are stale at the
    # second, and the rows state that evaluated value -- never a default
    # and never a bare key. (Family/header carriers are pinned at unit
    # level and on the family-view walk: these views carry no families
    # extras, so the evaluated rows here are the registered stale proof.)
    code, _, blobs = _code_repository(tmp_path, 3)
    for number in range(3):  # the code moves on past the recorded blobs
        target = code / _member_path(number)
        target.write_text(f"def member_{number}():\n    return -1\n", encoding="utf-8")
    commit_all(code, "second")
    second_tree = git(code, "rev-parse", "HEAD^{tree}")
    root = _tree(tmp_path, _statements(3, words=150), blobs=blobs)
    family = text_uuid("revision", f"{FAMILY}@1")
    response = _read(
        tmp_path,
        root,
        view="family",
        family_revision_id=family,
        code_tree_id=second_tree,
        repository_root=str(code),
    )
    assert response["state"] == "view", response
    assert response["currentness"]["codeTree"]["treeId"] == second_tree
    rows = [row for row in response["payload"]["rows"] if isinstance(row, dict)]
    assert rows, response
    row_states = {str(row["state"]) for row in rows}
    assert row_states <= {"current", "stale", "unverifiable", "unrealized"}, row_states
    assert "stale" in row_states, rows


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
        tmp_path,
        root,
        workspace=code,
        view="family",
        continuation=first["continuation"],
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


def test_an_empty_ordering_is_refused_not_defaulted_on_every_path(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path, _statements(3))
    family = text_uuid("revision", f"{FAMILY}@1")
    tree = _read(tmp_path, root, view="family", family_revision_id=family, ordering_input="")
    assert tree["refusalCode"] == "unadmitted_ordering_input"
    assert tree["threshold"]["tokens"] == KNOWLEDGE_PAGE_THRESHOLD_TOKENS  # rule 1, refusals too


def _registered_walk(world: EntryPointWorld, memory: Path) -> list[dict[str, Any]]:
    """Page 1 and every later page through the registered handler.

    What the caller walks is the delivered answer, never a detail file
    behind it: continuations carry the walk, so the walk completes with
    every page's log deleted, unwritten, or exhausted.
    """

    outcome, first = world.call(
        "knowledge_read",
        {"memoryRoot": str(memory), "view": "source_context", "sourcePath": SEED_PATH},
    )
    assert outcome == "RETURNED", first
    assert isinstance(first, dict) and first["state"] == "page", first
    pages = [first]
    continuation = first.get("continuation")
    while continuation is not None:
        outcome, response = world.call(
            "knowledge_read",
            {
                "memoryRoot": str(memory),
                "view": "source_context",
                "sourcePath": SEED_PATH,
                "continuation": continuation,
            },
        )
        assert outcome == "RETURNED", response
        assert isinstance(response, dict) and response["state"] == "page", response
        pages.append(response)
        continuation = response.get("continuation")
    return pages


def _enclosure_log_dir(world: EntryPointWorld, memory: Path) -> Path:
    """The folder one world's answers log into, from a probe answer's log.

    The server resolves leaf calls into the worktree-group logs, not the
    coordination logs: derive it from a delivered log path instead of
    assuming either root.
    """

    from pathlib import Path as _Path  # noqa: PLC0415

    outcome, probe = world.call(
        "knowledge_read",
        {"memoryRoot": str(memory), "view": "source_context", "sourcePath": SEED_PATH},
    )
    assert outcome == "RETURNED", probe
    assert isinstance(probe, dict) and "log" in probe, probe
    return _Path(str(probe["log"])).parent.parent


class TestRegisteredWalkCompleteness:
    """Registered no-repeat walks: >20 rows, prose, headers, log outage.

    MIK-R109 round-3 F13: the direct-payload walkers stop at the builder;
    these drive the same completeness through the registered handler.
    Every walk is verified from the delivered answers alone -- no log
    file is ever opened -- including walks whose detail logs are
    unavailable (unwritable) or exhausted (over budget): the pages still
    arrive, qualified by logError instead of log.
    """

    def test_forty_members_walk_registered_exactly_once(self, tmp_path: Path) -> None:
        world = EntryPointWorld()
        try:
            world.build()
            root = _tree(tmp_path, _statements(40, words=150))
            pages = _registered_walk(world, root)
            assert len(pages) >= 3
            items = [item for page in pages for item in _counted(page["payload"]["rows"])]
            total = pages[0]["page"]["total"]
            ids = [_leaf_key(item) for item in items]
            assert len(ids) == total == len(set(ids))
            assert {item["kind"] for item in items} >= {
                "family",
                "invariant",
                "realization",
            }
            assert {(item["kind"], item.get("role")) for item in items} >= {
                ("family", "header"),
                ("invariant", "member"),
                ("realization", None),
            }
            for page in pages[1:]:
                rows = [
                    row
                    for row in page["payload"]["rows"]
                    if isinstance(row, dict)
                    and row.get("kind") == "family"
                    and row.get("role") == "reference"
                ]
                assert rows, page["payload"]
                for row in rows:
                    assert row.get("id"), row
                    assert "state" in row, row
            # MIK-R109 round-4 F11: every delivered row states a value from
            # the vocabulary -- never a bare key. (This leaf walk carries
            # its header references as literal rows; the page-level
            # headerReference values are pinned on the family-view walk
            # in test_pages_stay_within_the_threshold_over_randomized_families.)
            states = {
                str(row.get("state"))
                for page in pages
                for row in page["payload"]["rows"]
                if isinstance(row, dict)
            }
            assert states <= {"current", "stale", "unverifiable", "unrealized"}, states
            for page in pages[1:]:
                reference = page["page"].get("headerReference")
                if reference is None:
                    continue
                assert reference["kind"] == "family", reference
                assert reference["state"] in {
                    "current",
                    "stale",
                    "unverifiable",
                    "unrealized",
                }, reference
        finally:
            world.close()

    def test_large_prose_walks_registered_as_small_names(self, tmp_path: Path) -> None:
        rng = random.Random(11)
        noise = "".join(rng.choices(string.ascii_letters + string.digits, k=PROSE_MAX_LENGTH - 20))
        world = EntryPointWorld()
        try:
            world.build()
            root = _tree(tmp_path, [f"Big {noise}", "Small member."])
            pages = _registered_walk(world, root)
            assert len(pages) >= 2
            for page in pages:
                assert _within_threshold(page["payload"]), page["page"]
                for row in _counted(page["payload"]["rows"]):
                    assert noise not in json.dumps(row), row["id"]
            ids = [_leaf_key(item) for page in pages for item in _counted(page["payload"]["rows"])]
            assert len(ids) == len(set(ids)) == pages[0]["page"]["total"]
        finally:
            world.close()

    def test_walk_completes_when_logs_are_unavailable(self, tmp_path: Path) -> None:
        world = EntryPointWorld()
        try:
            world.build()
            root = _tree(tmp_path, _statements(24, words=30))
            log_dir = _enclosure_log_dir(world, root)
            # The log folder becomes a file: every detail and line write
            # is unwritable, so answers qualify with logError instead of log.
            import shutil as _shutil  # noqa: PLC0415

            _shutil.rmtree(log_dir, ignore_errors=True)
            log_dir.write_text("r3-unavailable", encoding="utf-8")
            try:
                pages = _registered_walk(world, root)
            finally:
                log_dir.unlink(missing_ok=True)
                log_dir.mkdir(parents=True, exist_ok=True)
                from agents_remember.application.tool_answer_log import (  # noqa: PLC0415
                    reset_log_budgets,
                )

                reset_log_budgets()
            assert len(pages) >= 2
            for page in pages:
                assert page["state"] == "page", page
                assert "log" not in page, page
                assert page.get("logError"), page
            items = [item for page in pages for item in _counted(page["payload"]["rows"])]
            ids = [_leaf_key(item) for item in items]
            assert len(ids) == pages[0]["page"]["total"] == len(set(ids))
        finally:
            world.close()

    def test_walk_completes_when_logs_are_exhausted(self, tmp_path: Path) -> None:
        world = EntryPointWorld()
        try:
            world.build()
            root = _tree(tmp_path, _statements(24, words=30))
            log_dir = _enclosure_log_dir(world, root)
            # A sparse file over the folder budget: the census counts its
            # logical size, eviction cannot remove it, so every detail
            # write is dropped with a budget logError and no log.
            fill = log_dir / "r3-budget-fill.bin"
            try:
                with open(os.open(str(fill), os.O_CREAT | os.O_WRONLY, 0o600), "wb") as handle:
                    handle.truncate(300 * 1024 * 1024)
                from agents_remember.application.tool_answer_log import (  # noqa: PLC0415
                    reset_log_budgets as _reset_before_walk,
                )

                # Force a fresh census past the cached size: the fill must
                # count before the walk's first write, not after.
                _reset_before_walk()
                pages = _registered_walk(world, root)
            finally:
                fill.unlink(missing_ok=True)
                from agents_remember.application.tool_answer_log import (  # noqa: PLC0415
                    reset_log_budgets,
                )

                reset_log_budgets()
            assert len(pages) >= 2
            for page in pages:
                assert page["state"] == "page", page
                assert "log" not in page, page
                assert page.get("logError"), page
            items = [item for page in pages for item in _counted(page["payload"]["rows"])]
            ids = [_leaf_key(item) for item in items]
            assert len(ids) == pages[0]["page"]["total"] == len(set(ids))
        finally:
            world.close()


def test_the_record_view_answers_one_records_truth_as_a_view(tmp_path: Path) -> None:
    """MIK-R73 rule 2: `knowledge_read` view `record` reads one record and its history.

    Through the registered tool, on a converted tree: the family's own record by its text ID, with
    its link states, its members and its history rows; a record the tree does not hold is refused
    by name; and the view without a `recordId` is refused before anything is read.
    """

    world = EntryPointWorld()
    try:
        write_family_tree(world.memory, ["One.", "Two."])
        outcome, answer = world.call(
            "knowledge_read",
            {"memoryRoot": str(world.memory), "view": "record", "recordId": FAMILY},
        )
        assert outcome == "RETURNED" and answer is not None, answer
        assert answer["state"] == "view" and answer["view"] == "record", answer
        assert answer["record"]["id"] == FAMILY, answer
        # F03: the answer names the record and where it is, and never carries its document.
        assert "document" not in answer["record"], answer
        assert answer["record"]["path"].endswith(".json"), answer
        assert answer["record"]["heading"], answer
        # F01: every link states what its target is at this read's code tree.
        assert answer["outgoing"], answer
        for link in answer["incoming"] + answer["outgoing"]:
            assert "targetState" in link, link
        assert {
            link["target"]["id"]: link["targetState"]["state"]
            for link in answer["outgoing"]
            if link["relation"] == "member"
        } == {_member(0): "present", _member(1): "present"}
        # F02: the timeline is read at the tree's commit, not reported as uncommitted-and-unread.
        assert answer["timeline"]["sources"], answer["timeline"]
        assert all(
            source["state"] != "the memory tree has no commit"
            for source in answer["timeline"]["sources"].values()
        ), answer["timeline"]

        missing_outcome, missing = world.call(
            "knowledge_read",
            {"memoryRoot": str(world.memory), "view": "record", "recordId": "FAM-NOT0001"},
        )
        assert missing_outcome == "RETURNED" and missing is not None, missing
        assert missing["state"] == "refused" and missing["refusalCode"] == "selector_absent"

        unnamed_outcome, unnamed = world.call(
            "knowledge_read", {"memoryRoot": str(world.memory), "view": "record"}
        )
        assert unnamed_outcome == "RETURNED" and unnamed is not None, unnamed
        assert unnamed["state"] == "refused" and unnamed["refusalCode"] == "invalid-input"
    finally:
        world.close()
