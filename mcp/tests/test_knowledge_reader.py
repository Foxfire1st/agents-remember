"""MIK-R29: the path-based knowledge reader -- explorer, path view, truth views, timeline, census.

The fixture is a coordination root with one repository: a code repository and its converted memory
repository at ``memory-repos/ar-demo``. The memory holds MIK-R23's review-example tree (a family
realized across four files, a test proof, a decision and an incident linking to an invariant, a
route sidecar, two leaves' history rows), onboarding prose with numbered references, a census, and
two commits so every timeline source has something to say:

* commit 1 (``Code-Commit`` of the code commit): the tree as written;
* commit 2: the invariant's statement revised, one realization re-anchored and one moved to another
  file, a third leaf's history row, an assumption facet, and a decision superseding the first.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_paging import PageBinding
from agents_remember.application.knowledge_paging.bindings import mint_continuation
from agents_remember.application.knowledge_paging.threshold import response_tokens
from agents_remember.application.knowledge_reader import ReaderQuery, read_knowledge_reader
from agents_remember.application.knowledge_reader.selection import ReaderSelection, open_selection
from agents_remember.application.knowledge_reader.subtree import SUBTREE_VIEW, subtree_page
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.memory.knowledge_census import build_inventory
from agents_remember.memory_quality.knowledge_census import (
    baseline_path,
    claims_path,
    inventory_path,
    route_status_path,
)
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.documents import file_sidecar_path, record_path
from agents_remember.serving.knowledge_reader import register_knowledge_reader_route
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = "demo"
REVIEW_PATH = "dashboard/src/data/review.ts"
SIBLING_PATHS = (
    "dashboard/src/views/family.ts",
    "mcp/src/agents_remember/application/review_family_context.py",
    "mcp/src/agents_remember/models/knowledge/review_family_source.py",
)
TEST_PATH = "mcp/tests/test_review_family_context.py"
REVIEW_INVARIANT = "INV-RVW001"
SIBLING_INVARIANT = "INV-SBX002"
OUTSIDER_INVARIANT = "INV-0TS003"
RETIRED_INVARIANT = "INV-RETRD1"
SIBLING_PREFIX_PATH = "dashboard/src/database.ts"
BINARY_PATH = "dashboard/public/logo.bin"
FAMILY = "FAM-CMPBND"
DECISION = "DEC-D12RTE"
INCIDENT = "INC-1NC1DT"
LEAF = "260928-MIK-L23"
OTHER_LEAF = "260928-MIK-L07"
READER_LEAF = "260928-MIK-L29"
SUPERSEDING = "DEC-SPRSD2"
ASSUMPTION = "ASM-A55MPT"
CENSUS = "wave-1"
MOVED_PATH = "dashboard/src/views/moved.ts"
FAKE_BLOB = "d8baa15ca43be010159ae15f6b0e744949e6d33a"
REVIEW_CODE = "export function loadReview() {\n  return 1;\n}\n\nexport function other() {}\n"
PROSE = "# dashboard/src/data/review.ts\n\nLoads the review [1]. It is governed by [2].\n"
INVARIANT_FILE = record_path("invariant", REVIEW_INVARIANT, "unchanged-realization-context")
ORIGIN = {"task": "260928-MIK", "leaf": LEAF}


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    if result.returncode != 0:
        raise AssertionError(f"fixture git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _repository(root: Path) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "reader fixture")
    return root


def _commit(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


def _json(root: Path, path: str) -> dict[str, Any]:
    return json.loads((root / path).read_text(encoding="utf-8"))


def _write_json(root: Path, path: str, document: dict[str, Any]) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_text(document), encoding="utf-8")


def anchor(name: str, *, blob: str = FAKE_BLOB, path: str | None = None) -> dict[str, Any]:
    content = "sha256:" + hashlib.sha256(name.encode("utf-8")).hexdigest()
    value: dict[str, Any] = {
        "locator": {"kind": "symbol", "name": name},
        "blob": blob,
        "content": content,
    }
    return value if path is None else {**value, "path": path}


def _invariant(identifier: str, statement: str, *, status: str = "accepted") -> dict[str, Any]:
    return {
        "schema": "ar-invariant/v1",
        "id": identifier,
        "origin": ORIGIN,
        "revision": 1,
        "status": status,
        "statement": statement,
        "applicability": "Every review comparison.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
    }


def _sidecar(path: str, realizes: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "schema": "ar-onboarding-file/v1",
        "path": path,
        "references": {},
        "realizes": realizes,
        **extra,
    }


def _realization(identifier: str, invariant: str, name: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "invariant": invariant,
        "anchor": anchor(name),
        "role": "enforcement",
        "rationale": f"{name} enforces {invariant}.",
    }


def _history(leaf: str, *, closed: bool, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema": "ar-history/v1", "leaf": leaf, "closed": closed, "rows": rows}


def _invariant_row(row_id: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": row_id,
        "subject": REVIEW_INVARIANT,
        "disposition": "no_impact",
        "reason": reason,
        "items": [],
        "covers": [],
        "revision": 1,
        **extra,
    }


def _write_tree(memory: Path) -> None:
    """A family realized across four files, a test proof, a decision and an incident linking to an
    invariant, a route sidecar citing it, and two leaves' history rows about it."""

    documents: dict[str, dict[str, Any]] = {
        "knowledge/layout.json": {"schema": "ar-memory-layout/v2", "conversion": "fixture"},
        INVARIANT_FILE: _invariant(REVIEW_INVARIANT, "A comparison shows unchanged realizations."),
        record_path("invariant", SIBLING_INVARIANT, "family-context-source"): _invariant(
            SIBLING_INVARIANT, "Family context is read from the comparison's own endpoints."
        ),
        record_path("invariant", OUTSIDER_INVARIANT, "outsider"): _invariant(
            OUTSIDER_INVARIANT, "An unrelated invariant."
        ),
        record_path("invariant", RETIRED_INVARIANT, "retired"): _invariant(
            RETIRED_INVARIANT, "A retired invariant.", status="retired"
        ),
        # A file whose path shares ``dashboard/src/data``'s spelling but lies beside it.
        file_sidecar_path(SIBLING_PREFIX_PATH): _sidecar(
            SIBLING_PREFIX_PATH, [_realization("RLZ-DBS001", OUTSIDER_INVARIANT, "connect")]
        ),
        record_path("family", FAMILY, "comparison-bound-context"): {
            "schema": "ar-family/v1",
            "id": FAMILY,
            "origin": ORIGIN,
            "revision": 1,
            "status": "accepted",
            "title": "Comparison-bound unchanged realization context",
            "guarantee": "Every realization of the family is shown at the comparison's endpoints.",
            "members": [REVIEW_INVARIANT, SIBLING_INVARIANT],
            "routes": ["dashboard/src", "mcp/src/agents_remember/application"],
            "admission": "legacy-unassessed",
        },
        record_path("decision", DECISION, "local-family-routes"): {
            "schema": "ar-decision/v1",
            "id": DECISION,
            "origin": {"task": "260928-MIK"},
            "revision": 1,
            "status": "active",
            "context": "Families span subtrees.",
            "alternatives": [
                {"option": "Local routes", "status": "chosen", "reason": "Accurate."},
                {
                    "option": "One route",
                    "status": "rejected",
                    "reason": "Collapses to the root.",
                    "reconsider_when": "Families stop spanning subtrees.",
                },
            ],
            "consequences": ["Coverage runs per route set."],
            "decider": "developer",
            "supersedes": [],
            "admission": "legacy-unassessed",
            "links": [
                {"target": REVIEW_INVARIANT, "relation": "constrains"},
                {"target": REVIEW_INVARIANT, "relation": "reconsider_on", "alternative": 1},
                {"target": "route:dashboard/src", "relation": "constrains"},
            ],
        },
        record_path("incident", INCIDENT, "stale-context"): {
            "schema": "ar-incident/v1",
            "id": INCIDENT,
            "origin": {"task": "260921-ICR"},
            "revision": 1,
            "status": "accepted",
            "occurrence": "The review showed stale context.",
            "observed_at": "260921-ICR-L40",
            "observed_effect": "A reviewer saw the wrong realization.",
            "detection": "Manual review.",
            "cause": "The context was read from the working tree.",
            "cause_uncertainty": "None.",
            "applicability": "unresolved",
            "links": [
                {"target": REVIEW_INVARIANT, "relation": "violated"},
                {"target": anchor("loadReview", path=REVIEW_PATH), "relation": "occurred_at"},
            ],
        },
        file_sidecar_path(REVIEW_PATH): _sidecar(
            REVIEW_PATH,
            [
                _realization("RLZ-RVW001", REVIEW_INVARIANT, "loadReview"),
                _realization("RLZ-RVW002", SIBLING_INVARIANT, "familyContext"),
                _realization("RLZ-RET001", RETIRED_INVARIANT, "other"),
            ],
        ),
        file_sidecar_path(TEST_PATH): _sidecar(
            TEST_PATH,
            [],
            proves=[
                {
                    "id": "PRF-T3ST0K",
                    "invariant": REVIEW_INVARIANT,
                    "anchor": anchor("test_unchanged_context"),
                    "facet": "unchanged realizations are shown",
                }
            ],
        ),
        "onboarding/dashboard/src/overview.json": {
            "schema": "ar-onboarding-route/v1",
            "path": "dashboard/src",
            "references": {
                "1": {"targets": [{"kind": "invariant", "id": REVIEW_INVARIANT}]},
                "2": {
                    "targets": [{"kind": "code", "anchor": anchor("loadReview", path=REVIEW_PATH)}]
                },
            },
        },
        f"knowledge/history/{OTHER_LEAF}.json": _history(
            OTHER_LEAF,
            closed=True,
            rows=[
                _invariant_row("ROW-AAAAA1", "Only formatting changed near loadReview."),
                {
                    "id": "ROW-AAAAA2",
                    "subject": FAMILY,
                    "disposition": "no_impact",
                    "reason": "Members unchanged.",
                    "items": [],
                    "examined": [
                        {"id": REVIEW_INVARIANT, "revision": 1},
                        {"id": SIBLING_INVARIANT, "revision": 1},
                    ],
                },
            ],
        ),
        f"knowledge/history/{LEAF}.json": _history(
            LEAF,
            closed=False,
            rows=[_invariant_row("ROW-BBBBB1", "The index reads this code.", because=[DECISION])],
        ),
    }
    for number, path in enumerate(SIBLING_PATHS, start=1):
        documents[file_sidecar_path(path)] = _sidecar(
            path, [_realization(f"RLZ-SBX00{number}", SIBLING_INVARIANT, "render")]
        )
    for path, document in documents.items():
        _write_json(memory, path, document)
    (memory / "onboarding/dashboard/src/overview.md").write_text(
        "# dashboard/src\n", encoding="utf-8"
    )


def _write_census(memory: Path, code_paths: list[str]) -> None:
    """Census ``wave-1`` over two routes: three assessed claims on one route, two statuses each."""

    def at(hour: int) -> dict[str, str]:
        return {
            "wave": "260930-MIG-W1",
            "session": "curator-7",
            "at": f"2026-09-29T0{hour}:00:00+02:00",
        }

    def claim(number: int, verdict: str) -> dict[str, Any]:
        evidence = [{"targets": [{"kind": "external", "document": {"document": "code review"}}]}]
        return {
            "id": f"CLM-{number:06d}",
            "text": f"Legacy claim {number} holds.",
            "location": {"artifact": "onboarding/dashboard/src/overview.md"},
            "kind": "current_behavior",
            "applicability": "assessable",
            "assessments": [{"verdict": verdict, "evidence": evidence, "provenance": at(8)}],
            "disposition": "kept_as_prose",
        }

    memory_paths = [p.relative_to(memory).as_posix() for p in memory.rglob("*") if p.is_file()]
    inventory = build_inventory(CENSUS, code_paths=code_paths, memory_paths=memory_paths)
    documents: dict[str, dict[str, Any]] = {
        baseline_path(CENSUS): {
            "schema": "ar-census-baseline/v1",
            "census": CENSUS,
            "code": {"commit": "c" * 40},
            "memory": {"commit": "d" * 40},
            "scope": [],
        },
        inventory_path(CENSUS): inventory.to_document(),
        claims_path(CENSUS, "dashboard/src"): {
            "schema": "ar-census-claims/v1",
            "census": CENSUS,
            "route": "dashboard/src",
            "claims": [
                claim(1, "no_concern_found"),
                claim(2, "no_concern_found"),
                claim(3, "concern_found"),
            ],
        },
    }
    for route in ("dashboard/src", "."):
        documents[route_status_path(CENSUS, route)] = {
            "schema": "ar-census-route/v1",
            "census": CENSUS,
            "route": route,
            "statuses": [
                {
                    "status": status,
                    "reason": f"{status} by wave 1",
                    "tree": "e" * 40,
                    "provenance": at(hour),
                }
                for status, hour in (("in_progress", 8), ("migrated", 9))
            ],
        }
    for path, document in documents.items():
        _write_json(memory, path, document)


@dataclass
class World:
    config: McpRuntimeConfig
    code: Path
    memory: Path
    code_commit: str
    first: str
    second: str
    unconverted: str

    def read(self, view: str, **fields: Any) -> dict[str, Any]:
        return read_knowledge_reader(
            self.config, ReaderQuery(view=view, repository_id=REPO, **fields)
        )


def _entry(sidecar: dict[str, Any], entry_id: str) -> dict[str, Any]:
    return next(one for one in sidecar["realizes"] if one["id"] == entry_id)


def _first_commit(memory: Path, code: Path, code_commit: str) -> None:
    _write_tree(memory)
    tree = git(code, "rev-parse", f"{code_commit}^{{tree}}")
    trees = CodeTrees.open(code, tree, tree)
    blob = trees.base()[REVIEW_PATH]
    resolved = trees.resolve(REVIEW_PATH, {"kind": "symbol", "name": "loadReview"}, blob, blob)
    assert resolved is not None
    sidecar = _json(memory, file_sidecar_path(REVIEW_PATH))
    _entry(sidecar, "RLZ-RVW001")["anchor"] = {
        "locator": {"kind": "symbol", "name": "loadReview"},
        "blob": blob,
        "content": resolved.content,
    }
    sidecar["references"] = {
        "1": {"targets": [{"kind": "code", "anchor": anchor("loadReview", blob=blob)}]},
        "2": {
            "note": "the invariant and the decision behind it",
            "targets": [
                {"kind": "invariant", "id": REVIEW_INVARIANT},
                {"kind": "decision", "id": DECISION},
            ],
        },
    }
    _write_json(memory, file_sidecar_path(REVIEW_PATH), sidecar)
    (memory / f"onboarding/{REVIEW_PATH}.md").write_text(PROSE, encoding="utf-8")
    _write_census(memory, sorted(git(code, "ls-files").splitlines()))


def _second_commit(memory: Path) -> None:
    record = _json(memory, INVARIANT_FILE)
    record.update(revision=2, statement="A comparison shows every unchanged realization.")
    _write_json(memory, INVARIANT_FILE, record)
    review = _json(memory, file_sidecar_path(REVIEW_PATH))
    # RLZ-RVW002 re-anchored in place: another locator in the same file (MIK-R29 F5).
    _entry(review, "RLZ-RVW002")["anchor"] = anchor("renderFamilyContext", blob="a" * 40)
    _write_json(memory, file_sidecar_path(REVIEW_PATH), review)
    source = _json(memory, file_sidecar_path(SIBLING_PATHS[0]))
    moved = source["realizes"].pop()
    _write_json(memory, file_sidecar_path(SIBLING_PATHS[0]), source)
    _write_json(
        memory,
        file_sidecar_path(MOVED_PATH),
        {
            "schema": "ar-onboarding-file/v1",
            "path": MOVED_PATH,
            "references": {},
            "realizes": [moved],
        },
    )
    _write_json(
        memory,
        f"knowledge/history/{READER_LEAF}.json",
        {
            "schema": "ar-history/v1",
            "leaf": READER_LEAF,
            "closed": True,
            "rows": [
                {
                    "id": "ROW-CCCCC1",
                    "subject": REVIEW_INVARIANT,
                    "disposition": "no_impact",
                    "reason": "The statement was sharpened; nothing in the code moved.",
                    "items": [],
                    "covers": [],
                    "revision": 2,
                }
            ],
        },
    )
    decision = _json(memory, record_path("decision", DECISION, "local-family-routes"))
    decision.update(id=SUPERSEDING, supersedes=[DECISION], links=[])
    _write_json(memory, record_path("decision", SUPERSEDING, "narrower-routes"), decision)
    _write_json(
        memory,
        record_path("assumption", ASSUMPTION, "review-reads-trees"),
        {
            "schema": "ar-assumption/v1",
            "id": ASSUMPTION,
            "origin": {"task": "260928-MIK"},
            "revision": 1,
            "status": "accepted",
            "proposition": "A review reads Git trees, never a working tree.",
            "basis": "MIK-R25.",
            "links": [{"target": REVIEW_INVARIANT, "relation": "underlies"}],
        },
    )


@pytest.fixture
def world(tmp_path: Path) -> World:
    code = _repository(tmp_path / "ws" / REPO)
    for path in (REVIEW_PATH, *SIBLING_PATHS, TEST_PATH):
        (code / path).parent.mkdir(parents=True, exist_ok=True)
        (code / path).write_text(
            REVIEW_CODE if path == REVIEW_PATH else "x = 1\n", encoding="utf-8"
        )
    (code / BINARY_PATH).parent.mkdir(parents=True)
    (code / BINARY_PATH).write_bytes(b"UTF-8 text, with a NUL \x00 byte\n" * 64)
    code_commit = _commit(code, "code")
    memory = _repository(tmp_path / "coord" / "memory-repos" / f"ar-{REPO}")
    (memory / "memory.md").write_text("# legacy\n", encoding="utf-8")
    unconverted = _commit(memory, "before the conversion")
    _first_commit(memory, code, code_commit)
    first = _commit(memory, f"converted\n\nCode-Commit: {code_commit}")
    _second_commit(memory)
    second = _commit(memory, f"second\n\nCode-Commit: {code_commit}")
    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path / "coord",
        workspace_root=tmp_path / "ws",
        transcript_root=tmp_path / "logs",
        repositories={REPO: RepositoryScope(repo_id=REPO, path=code)},
    )
    return World(config, code, memory, code_commit, first, second, unconverted)


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in rows}


def _code_tree(world: World) -> str:
    return git(world.code, "rev-parse", f"{world.code_commit}^{{tree}}")


def _snapshot(world: World) -> tuple[str, ...]:
    """Refs, status, object counts and the index file of both repositories: what a read may not
    change."""

    facts: list[str] = []
    for root in (world.code, world.memory):
        facts.extend(
            git(root, *args)
            for args in (
                ("for-each-ref",),
                ("--no-optional-locks", "status", "--porcelain"),
                ("count-objects", "-v"),
            )
        )
        index = root / ".git" / "index"
        facts.append(f"{index.stat().st_mtime_ns}:{hashlib.sha256(index.read_bytes()).hexdigest()}")
    return tuple(facts)


# --------------------------------------------------------------------------------------------------
# The path view
# --------------------------------------------------------------------------------------------------


def _assert_references(view: dict[str, Any]) -> None:
    assert view["prose"]["state"] == "present" and "[1]" in view["prose"]["text"]
    references = {item["number"]: item for item in view["references"]["items"]}
    code_target = references["1"]["targets"][0]
    assert code_target["kind"] == "code" and code_target["path"] == REVIEW_PATH
    assert code_target["anchor"]["locator"] == {"kind": "symbol", "name": "loadReview"}
    assert [t["id"] for t in references["2"]["targets"]] == [REVIEW_INVARIANT, DECISION]
    assert references["2"]["targets"][1]["record"]["kind"] == "decision"


def _assert_file_invariants(view: dict[str, Any], world: World) -> None:
    invariants = _by_id(view["invariants"])
    # The retired invariant's entry at this path is never shown as a live invariant.
    assert set(invariants) == {REVIEW_INVARIANT, SIBLING_INVARIANT}
    own = invariants[REVIEW_INVARIANT]
    # Its entry here is current at the paired code tree; its proof elsewhere is not (MIK-R03 rule 2).
    assert [(e["id"], e["state"]) for e in own["entries"]] == [("RLZ-RVW001", "current")]
    assert own["state"] == "stale" and invariants[SIBLING_INVARIANT]["state"] == "stale"
    assert view["currentness"]["codeTree"]["treeId"] == _code_tree(world)


def _assert_file_records(view: dict[str, Any]) -> None:
    records = {row["record"]["id"]: row for row in view["records"]}
    assert set(records) == {DECISION, INCIDENT}
    decision = records[DECISION]["decision"]
    rejected = decision["alternatives"][1]
    assert decision["derivedStatus"] == "active"
    assert (rejected["status"], rejected["reconsiderWhen"]) == (
        "rejected",
        "Families stop spanning subtrees.",
    )
    assert rejected["reconsiderOn"] == [{"kind": "record", "id": REVIEW_INVARIANT}]
    assert {link["relation"] for link in records[INCIDENT]["links"]} == {"violated", "occurred_at"}


def test_a_file_path_view_shows_prose_references_entries_families_and_linked_records(
    world: World,
) -> None:
    view = world.read("path", commit=world.first, path=REVIEW_PATH)

    assert (view["state"], view["kind"], view["testFile"]) == ("view", "file", False)
    assert view["selection"]["kind"] == "commit" and view["selection"]["indexState"] == "complete"
    _assert_references(view)
    _assert_file_invariants(view, world)
    family = _by_id(view["families"])[FAMILY]
    assert family["member"] is True and family["via"] == ["dashboard/src"]
    assert family["guarantee"].startswith("Every realization")
    assert [row["path"] for row in family["otherLocations"]] == sorted([*SIBLING_PATHS, TEST_PATH])
    _assert_file_records(view)


def test_a_directory_view_is_bounded_to_its_own_level_children_and_route(world: World) -> None:
    view = world.read("path", commit=world.first, path="dashboard/src")

    assert (view["kind"], view["prose"]["text"]) == ("directory", "# dashboard/src\n")
    assert _target_kinds(view) == {"invariant", "code"}
    assert _by_id(view["families"])[FAMILY]["via"] == ["dashboard/src"]
    # Own level only: the one file directly in the directory; deeper files are counted per child.
    assert _entry_paths(view) == {SIBLING_PREFIX_PATH}
    assert view["children"] == [
        {"name": "data", "path": "dashboard/src/data", "entries": 2},
        {"name": "database.ts", "path": SIBLING_PREFIX_PATH, "entries": 1},
        {"name": "views", "path": "dashboard/src/views", "entries": 1},
    ]
    assert view["subtree"] == {"entries": 4, "view": "subtree"}
    assert _route_links(view) == [("constrains", "dashboard/src")]


def _target_kinds(view: dict[str, Any]) -> set[str]:
    return {t["kind"] for item in view["references"]["items"] for t in item["targets"]}


def _entry_paths(view: dict[str, Any]) -> set[str]:
    return {e["path"] for group in view["invariants"] for e in group["entries"]}


def _route_links(view: dict[str, Any]) -> list[tuple[str, str]]:
    links = [link for row in view["records"] for link in row["links"]]
    return [(link["relation"], link["target"]) for link in links if link["targetKind"] == "route"]


def _page(world: World, commit: str | None, path: str, token: str | None, threshold: int) -> Any:
    """One subtree page as the reader answers it: the page with the reader's envelope around it."""

    selection = open_selection(world.config, REPO, commit)
    assert isinstance(selection, ReaderSelection)
    with selection as opened:
        page = subtree_page(opened, path, token, threshold=threshold)
        return {"view": "subtree", "selection": opened.to_document(), **page}


def _walk(world: World, path: str, *, threshold: int) -> list[dict[str, Any]]:
    """Every page of a directory's subtree, following the continuation to the end."""

    pages: list[dict[str, Any]] = []
    token: str | None = None
    while not pages or token:
        pages.append(_page(world, world.first, path, token, threshold))
        token = pages[-1]["continuation"]
    # The whole answer, the reader's envelope included, stays within the bound (review F17).
    assert max(response_tokens(page) for page in pages) <= threshold
    return pages


def test_a_directorys_subtree_pages_through_the_shared_continuation(world: World) -> None:
    pages = _walk(world, "dashboard/src", threshold=800)

    assert len(pages) > 1 and all(page["state"] == "view" for page in pages)
    rows = [row["id"] for page in pages for row in page["rows"]]
    # Ordered by path then ID; the sibling ``database.ts`` sorts after ``data/``; no retired entry.
    assert rows == ["RLZ-RVW001", "RLZ-RVW002", "RLZ-DBS001", "RLZ-SBX001"]
    assert pages[-1]["page"]["enumerationComplete"] and pages[-1]["continuation"] is None
    assert pages[0]["page"]["selectionPolicy"] == "knowledge-reader-subtree"
    assert set(pages[0]["states"]) == {row["invariant"] for row in pages[0]["rows"]}
    # ``dashboard/src/data`` never holds its sibling ``dashboard/src/database.ts``.
    data = _walk(world, "dashboard/src/data", threshold=8000)
    assert [row["id"] for row in data[0]["rows"]] == ["RLZ-RVW001", "RLZ-RVW002"]


def test_a_subtree_walk_measures_every_page_at_the_code_tree_it_began_at(world: World) -> None:
    first = _page(world, None, "dashboard/src", None, 800)
    (world.code / REVIEW_PATH).write_text(REVIEW_CODE + "// moved on\n", encoding="utf-8")
    moved = _commit(world.code, "code moves between pages")
    resumed = world.read("subtree", path="dashboard/src", continuation=first["continuation"])
    fresh = _page(world, None, "dashboard/src", None, 800)
    walk_tree = first["page"]["codeTreeId"]
    # Page 2 of the walk still measures at page 1's tree; a new walk measures at the new HEAD (F16),
    # and the answer's selection names the tree its page measured at (R3-1).
    assert resumed["page"]["codeTreeId"] == walk_tree != fresh["page"]["codeTreeId"]
    assert resumed["selection"]["codeTree"]["treeId"] == walk_tree
    assert "moved since" in resumed["selection"]["codeNote"]
    assert fresh["page"]["codeTreeId"] == git(world.code, "rev-parse", f"{moved}^{{tree}}")
    # A walk tree the repository no longer holds is refused by name, never measured at (R3-2).
    gone = replace(_binding(first), code_tree_id="f" * 40)
    token = mint_continuation(
        gone, response="view", view=SUBTREE_VIEW, seeds=({"path": "dashboard/src"},), position=1
    )
    refused = world.read("subtree", path="dashboard/src", continuation=token)
    assert (refused["state"], refused["code"]) == ("refused", "continuation_binding_mismatch")
    assert "no longer holds" in refused["detail"]


def _binding(page: dict[str, Any]) -> PageBinding:
    facts = page["page"]
    return PageBinding(
        memory_tree_id=facts["memoryTreeId"],
        selection_policy=facts["selectionPolicy"],
        policy_version=facts["selectionPolicyVersion"],
        manifest_digest=facts["manifestDigest"],
        code_tree_id=facts["codeTreeId"],
    )


def test_a_subtree_continuation_of_another_walk_is_refused(world: World) -> None:
    token = _walk(world, "dashboard/src", threshold=800)[0]["continuation"]

    other_path = world.read("subtree", commit=world.first, path="dashboard", continuation=token)
    assert (other_path["state"], other_path["code"]) == ("refused", "continuation_binding_mismatch")
    other_tree = world.read(
        "subtree", commit=world.second, path="dashboard/src", continuation=token
    )
    assert other_tree["code"] == "continuation_binding_mismatch"
    assert "memory tree changed" in other_tree["detail"]
    garbage = world.read("subtree", commit=world.first, path="dashboard/src", continuation="x")
    assert garbage["code"] == "continuation_unreadable"


def test_a_test_file_shows_its_proofs_by_invariant_with_their_facets(world: World) -> None:
    view = world.read("path", commit=world.first, path=TEST_PATH)

    assert view["testFile"] is True
    (group,) = view["invariants"]
    assert group["id"] == REVIEW_INVARIANT
    (proof,) = group["entries"]
    assert (proof["kind"], proof["facet"]) == ("proof", "unchanged realizations are shown")


def test_the_explorer_lists_code_and_onboarding_children_with_entry_counts(world: World) -> None:
    root = world.read("tree", commit=world.second, path="")
    assert root["code"] == {"state": "listed"}
    assert {row["name"]: row["kind"] for row in root["children"]} == {
        "dashboard": "dir",
        "mcp": "dir",
    }
    data = world.read("tree", commit=world.second, path="dashboard/src/views")
    rows = {row["name"]: row for row in data["children"]}
    assert rows["family.ts"]["inCode"] is True and rows["family.ts"]["entries"] == 0
    # The moved realization's new file has onboarding but no code: listed, and marked so.
    assert rows["moved.ts"] == {
        "name": "moved.ts",
        "kind": "file",
        "inCode": False,
        "onboarding": True,
        "entries": 1,
        "path": MOVED_PATH,
    }
    # The retired invariant's entry is not counted.
    listing = world.read("tree", commit=world.second, path="dashboard/src/data")
    assert (
        _by_id([{"id": r["name"], **r} for r in listing["children"]])["review.ts"]["entries"] == 2
    )


def test_the_without_proof_list_is_filterable_by_path(world: World) -> None:
    everything = world.read("without-proof", commit=world.first)
    assert [row["id"] for row in everything["invariants"]] == [
        OUTSIDER_INVARIANT,
        SIBLING_INVARIANT,
    ]
    narrowed = world.read("without-proof", commit=world.first, path="mcp/src")
    assert [row["id"] for row in narrowed["invariants"]] == [SIBLING_INVARIANT]
    assert narrowed["total"] == 2


# --------------------------------------------------------------------------------------------------
# Truth views and the timeline
# --------------------------------------------------------------------------------------------------


def _assert_invariant_part(view: dict[str, Any]) -> None:
    record = view["record"]["document"]
    assert record["statement"] == "A comparison shows every unchanged realization."
    assert {"applicability", "conditions", "exclusions", "status", "admission"} <= set(record)
    invariant = view["invariant"]
    assert invariant["state"] == "stale"
    assert [(e["id"], e["state"]) for e in invariant["realizations"]] == [("RLZ-RVW001", "current")]
    assert [(e["id"], e["state"]) for e in invariant["proofs"]] == [("PRF-T3ST0K", "stale")]
    assert [f["id"] for f in invariant["families"]] == [FAMILY]
    linked = {row["record"]["id"]: row for row in invariant["linked"]}
    assert set(linked) == {DECISION, INCIDENT, ASSUMPTION}
    # Every linked view shows a decision's derived status: the second commit superseded it.
    assert linked[DECISION]["decision"]["derivedStatus"] == "superseded"


def _of(events: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    return [event for event in events if event["source"] == source]


def _assert_three_sources(timeline: dict[str, Any]) -> None:
    states = {name: one["state"] for name, one in timeline["sources"].items()}
    assert states == dict.fromkeys(("record", "history", "entries"), "read")
    events = timeline["events"]
    record_events = _of(events, "record")
    assert [e["change"] for e in record_events] == ["modified", "added"]
    meaning = {m["field"]: (m["before"], m["after"]) for m in record_events[0]["meaning"]}
    assert set(meaning) == {"revision", "statement"} and meaning["revision"] == (1, 2)
    owners = [e["owner"] for e in _of(events, "history")]
    assert owners[0] == READER_LEAF and set(owners) == {READER_LEAF, LEAF, OTHER_LEAF}
    dates = [e["date"] for e in events]
    assert dates == sorted(dates, reverse=True)


def test_an_invariant_truth_view_has_every_field_state_link_and_a_three_source_timeline(
    world: World,
) -> None:
    view = world.read("record", commit=world.second, record_id=REVIEW_INVARIANT)

    _assert_invariant_part(view)
    _assert_three_sources(view["timeline"])


def test_the_timeline_labels_moves_and_re_anchors_and_reads_only_the_sidecars_naming_them(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agents_remember.application.knowledge_reader import timeline  # noqa: PLC0415

    timeline.TIMELINES.clear()
    read: set[str] = set()
    real = timeline.read_git_blobs_bytes

    def counted(root: Path, blobs: Any) -> dict[str, bytes]:
        wanted = set(blobs)
        read.update(wanted)
        return real(root, wanted)

    monkeypatch.setattr(timeline, "read_git_blobs_bytes", counted)
    view = world.read("record", commit=world.second, record_id=SIBLING_INVARIANT)

    events = _of(view["timeline"]["events"], "entries")
    changes = {(e["entry"], e["change"]) for e in _at_commit(events, world.second)}
    # A new locator in the same file is a re-anchor; a new file is a move (review F5).
    assert changes == {("RLZ-RVW002", "re-anchored"), ("RLZ-SBX001", "moved")}
    moved = next(e for e in events if e["change"] == "moved")
    assert (moved["before"]["path"], moved["after"]["path"]) == (SIBLING_PATHS[0], MOVED_PATH)
    # Only sidecars whose diff names an entry are read: never the test's or database.ts's (F1).
    unrelated = {
        _blob_at(world, world.first, file_sidecar_path(one))
        for one in (TEST_PATH, SIBLING_PREFIX_PATH)
    }
    assert not unrelated & read


def _at_commit(events: list[dict[str, Any]], commit: str) -> list[dict[str, Any]]:
    return [event for event in events if event["commit"] == commit]


def _blob_at(world: World, commit: str, path: str) -> str:
    return git(world.memory, "rev-parse", f"{commit}:{path}")


def test_a_complete_timeline_is_served_again_from_the_bounded_cache(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agents_remember.application.knowledge_reader import timeline  # noqa: PLC0415

    timeline.TIMELINES.clear()
    built: list[str] = []
    real = timeline._timeline

    def counting(selection: Any, record: Any, *args: Any) -> dict[str, Any]:
        built.append(record.id)
        return real(selection, record, *args)

    monkeypatch.setattr(timeline, "_timeline", counting)
    first = world.read("record", commit=world.second, record_id=FAMILY)["timeline"]
    again = world.read("record", commit=world.second, record_id=FAMILY)["timeline"]
    older = world.read("record", commit=world.first, record_id=FAMILY)["timeline"]
    assert first == again and built == [FAMILY, FAMILY]
    assert len(older["events"]) < len(first["events"])

    # A timeline with a source that could not be read is not remembered: the next read asks again.
    def failing(*_arguments: Any) -> list[Any]:
        raise timeline.ReaderReadError("git log failed")

    with monkeypatch.context() as broken:
        broken.setattr(timeline, "_log", failing)
        failed = world.read("record", commit=world.first, record_id=INCIDENT)["timeline"]
    recovered = world.read("record", commit=world.first, record_id=INCIDENT)["timeline"]
    assert (failed["sources"]["record"]["state"], recovered["sources"]["record"]["state"]) == (
        "unavailable",
        "read",
    )


def test_a_family_truth_view_shows_guarantee_members_routes_and_every_location(
    world: World,
) -> None:
    view = world.read("record", commit=world.first, record_id=FAMILY)

    family = view["family"]
    assert [m["id"] for m in family["members"]] == [REVIEW_INVARIANT, SIBLING_INVARIANT]
    assert family["routes"] == ["dashboard/src", "mcp/src/agents_remember/application"]
    assert [row["path"] for row in family["locations"]] == sorted(
        [REVIEW_PATH, *SIBLING_PATHS, TEST_PATH]
    )
    assert family["staleMembers"] == [REVIEW_INVARIANT, SIBLING_INVARIANT]
    assert {e["source"] for e in view["timeline"]["events"]} == {"record", "history", "entries"}


def test_a_decision_truth_view_shows_alternatives_and_derived_supersession(world: World) -> None:
    older = world.read("record", commit=world.second, record_id=DECISION)["decision"]
    assert (older["storedStatus"], older["derivedStatus"]) == ("active", "superseded")
    assert [one["id"] for one in older["supersededBy"]] == [SUPERSEDING]
    assert [(a["status"], a["reason"]) for a in older["alternatives"]] == [
        ("chosen", "Accurate."),
        ("rejected", "Collapses to the root."),
    ]
    assert {g["relation"] for g in older["governs"]} == {"constrains"}
    newer = world.read("record", commit=world.second, record_id=SUPERSEDING)
    assert [one["id"] for one in newer["decision"]["supersedes"]] == [DECISION]
    before = world.read("record", commit=world.first, record_id=DECISION)["decision"]
    assert before["derivedStatus"] == "active"


def test_incident_and_facet_views_show_every_field_and_typed_links_both_ways(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident = world.read("record", commit=world.second, record_id=INCIDENT)
    fields = incident["facet"]
    assert {"cause", "cause_uncertainty", "occurrence", "observed_effect", "applicability"} <= set(
        fields
    )
    assert [(o["relation"], o["target"]["kind"]) for o in incident["outgoing"]] == [
        ("violated", "record"),
        ("occurred_at", "code"),
    ]
    assumption = world.read("record", commit=world.second, record_id=ASSUMPTION)
    assert assumption["facet"]["proposition"].startswith("A review reads")
    target = world.read("record", commit=world.second, record_id=REVIEW_INVARIANT)
    incoming = {(link["source"], link["relation"]) for link in target["incoming"]}
    assert {(ASSUMPTION, "underlies"), (INCIDENT, "violated"), (DECISION, "constrains")} <= incoming
    # Links that cannot be read are named, never shown as "no outgoing link" (F11).
    from agents_remember.application.knowledge_reader import truth  # noqa: PLC0415

    monkeypatch.setattr(truth, "RECORD_MODELS", {})
    unreadable = world.read("record", commit=world.second, record_id=INCIDENT)
    assert unreadable["outgoingState"]["state"] == "unavailable"


def test_the_census_view_shows_measures_and_each_routes_status_history(world: World) -> None:
    view = world.read("census", commit=world.first)

    assert view["state"] == "census" and view["known"] == [CENSUS]
    (report,) = view["censuses"]
    counts = report["counts"]
    assert (counts["N"], counts["T"], counts["F"], counts["U"], counts["P"]) == (3, 2, 1, 0, 0)
    assert report["measures"] and report["routeStatuses"]["migrated"] == 2
    history = report["routes"][0]["history"]
    assert [entry["status"] for entry in history] == ["in_progress", "migrated"]
    missing = world.read("census", commit=world.first, census_id="wave-9")
    assert (missing["state"], missing["known"]) == ("not-found", [CENSUS])


# --------------------------------------------------------------------------------------------------
# Selections, failures, the route, and read-only
# --------------------------------------------------------------------------------------------------


def test_any_commit_is_selectable_by_its_hexadecimal_name_only(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = world.read("selections")
    assert options["default"] == "published" and options["published"]["converted"] is True
    marks = {row["commit"]: row["converted"] for row in options["commits"]}
    assert marks == {world.second: True, world.first: True, world.unconverted: False}
    old = world.read("record", commit=world.first[:10], record_id=REVIEW_INVARIANT)
    assert (old["selection"]["commit"], old["record"]["revision"]) == (world.first, 1)
    assert old["selection"]["codeSource"] == "paired-code-commit"
    unconverted = world.read("path", commit=world.unconverted, path=REVIEW_PATH)
    assert unconverted["state"] == "not-converted"
    # A branch name is not a commit spelling, even one that resolves (review F6).
    assert world.read("path", commit="main", path=REVIEW_PATH)["state"] == "invalid-request"
    # A commit list that cannot be read is named, never an empty list (F11).
    from agents_remember.application.knowledge_reader import selection  # noqa: PLC0415

    def failing(_repository: Path) -> list[dict[str, Any]]:
        raise selection.ReaderReadError("git log failed")

    monkeypatch.setattr(selection, "_recent_commits", failing)
    assert world.read("selections")["commitsState"]["state"] == "unavailable"


def test_the_published_tree_reads_uncommitted_state_writes_nothing_and_offers_a_pin(
    world: World,
) -> None:
    clean = world.read("path", path=REVIEW_PATH)["selection"]
    assert (clean["kind"], clean["pinnedCommit"], clean["codeSource"]) == (
        "published",
        world.second,
        "checkout-head",
    )
    # Read once clean, so a stale cached timeline would be served below if the tree key were not
    # part of the cache key (review F17).
    assert world.read("record", record_id=REVIEW_INVARIANT)["timeline"]["events"][0]["commit"]
    record = _json(world.memory, INVARIANT_FILE)
    record["statement"] = "Working-tree statement."
    _write_json(world.memory, INVARIANT_FILE, record)
    before = _snapshot(world)
    published = world.read("record", record_id=REVIEW_INVARIANT)
    for view, fields in (("path", {"path": "dashboard/src"}), ("subtree", {"path": "."})):
        assert world.read(view, **fields)["state"] == "view"
    assert _snapshot(world) == before
    assert published["record"]["document"]["statement"] == "Working-tree statement."
    assert published["timeline"]["events"][0]["subject"] == "uncommitted"
    # A tree with uncommitted state has no pinned revision to share.
    assert published["selection"]["pinnedCommit"] is None


def test_a_live_leafs_candidate_is_selectable_and_names_what_its_code_tree_leaves_out(
    world: World, tmp_path: Path
) -> None:
    group = tmp_path / "group"
    git(world.code, "worktree", "add", "-q", "-b", "leaf", str(group / "code"))
    git(world.memory, "worktree", "add", "-q", "-b", "leaf", str(group / "memory"), world.second)
    _write_leaf_contract(world, group)
    leaf = world.read("record", commit="leaf:group", record_id=REVIEW_INVARIANT)
    assert (leaf["selection"]["kind"], leaf["record"]["revision"]) == ("leaf", 2)
    assert "uncommitted code is not included" in leaf["selection"]["codeNote"]
    assert world.read("records", commit="leaf:nowhere")["state"] == "unavailable"


def _write_leaf_contract(world: World, group: Path) -> None:
    task_root = world.config.coordination_root / "tasks" / REPO / "260101_reader"
    enclosure = task_root / "enclosures" / "260101-rdr-l1"
    enclosure.mkdir(parents=True)
    (task_root / "task.md").write_text("# task\n", encoding="utf-8")
    lines = [
        "---",
        "schema: ar-series-contract/v1",
        "schemaVersion: 1.0",
        "kind: leaf",
        "task_id: 260101_READER",
        "task_name: 260101_reader",
        f"repo_name: {REPO}",
        "workflow_kind: light-task",
        "memory_mode: external",
        "coordination:",
        f"  root: {world.config.coordination_root}",
        f"  task_root: {task_root}",
        f"  task_artifact: {task_root / 'task.md'}",
        f"  worktree_group: {group}",
        "  leaf_id: 260101-RDR-L1",
        "  parent_task_name: 260101_reader",
        "code:",
        f"  repo_path: {world.code}",
        "  source_branch: main",
        "  work_branch: leaf",
        f"  base_commit: {world.code_commit}",
        f"  worktree: {group / 'code'}",
        "memory:",
        "  mode: external",
        f"  repo_path: {world.memory}",
        "  source_branch: main",
        "  work_branch: leaf",
        f"  base_commit: {world.second}",
        f"  worktree: {group / 'memory'}",
        f"  ledger: {group / 'memory' / 'memory.md'}",
        "closeout:",
        "  status: not-started",
        "---",
        "",
    ]
    (enclosure / "series-contract.md").write_text("\n".join(lines), encoding="utf-8")


def test_a_partial_index_and_an_unavailable_code_tree_are_named_where_they_apply(
    world: World,
) -> None:
    (world.memory / "knowledge/invariants/INV-BR0KEN-broken.json").write_text("{", encoding="utf-8")
    git(world.memory, "add", "-A")
    git(world.memory, "commit", "-q", "-m", "no trailer, one broken file")
    broken = git(world.memory, "rev-parse", "HEAD")

    view = world.read("path", commit=broken, path=REVIEW_PATH)
    selection = view["selection"]
    assert selection["indexState"] == "partial"
    assert [p["path"] for p in selection["problems"]] == [
        "knowledge/invariants/INV-BR0KEN-broken.json"
    ]
    assert selection["codeTree"] is None and "no Code-Commit trailer" in selection["codeNote"]
    assert "no code tree was requested" in view["currentness"]["unverifiableReason"]
    assert {group["state"] for group in view["invariants"]} == {"unverifiable"}
    listing = world.read("tree", commit=broken, path="dashboard/src/data")
    assert listing["code"]["state"] == "unavailable"
    assert [row["name"] for row in listing["children"]] == ["review.ts"]


def _client(world: World) -> TestClient:
    app = FastAPI()
    register_knowledge_reader_route(app, lambda query: read_knowledge_reader(world.config, query))
    return TestClient(app)


def test_the_route_serves_every_view_and_the_reader_writes_nothing(world: World) -> None:
    before = _snapshot(world)
    client = _client(world)
    symbol = json.dumps({"kind": "symbol", "name": "loadReview"})
    answers = {
        view: client.get(
            f"/api/knowledge/reader/{view}", params={"repo": REPO, "commit": world.second, **params}
        )
        for view, params in (
            ("path", {"path": REVIEW_PATH}),
            ("subtree", {"path": "dashboard"}),
            ("record", {"id": REVIEW_INVARIANT}),
            ("records", {}),
            ("census", {}),
            ("tree", {"path": "dashboard"}),
            ("code", {"path": REVIEW_PATH, "locator": symbol}),
        )
    }
    assert {view: (r.status_code, r.json()["state"]) for view, r in answers.items()} == {
        "path": (200, "view"),
        "subtree": (200, "view"),
        "record": (200, "view"),
        "records": (200, "view"),
        "census": (200, "census"),
        "tree": (200, "view"),
        "code": (200, "present"),
    }
    assert answers["code"].json()["locator"] == {"state": "resolved", "lines": [1, 3]}
    unwired = FastAPI()
    register_knowledge_reader_route(unwired, None)
    assert (
        TestClient(unwired).get("/api/knowledge/reader/path", params={"repo": REPO}).status_code
        == 503
    )
    assert _snapshot(world) == before


def _assert_control_characters_refused(client: TestClient) -> None:
    """A NUL or other control character in a path is a bad request, never a Git call (F15)."""

    statuses = {
        (view, spelled): client.get(
            f"/api/knowledge/reader/{view}", params={"repo": REPO, "path": spelled}
        ).status_code
        for view in ("path", "code", "tree", "subtree", "without-proof")
        for spelled in ("a\x00b", "a\nb")
    }
    assert set(statuses.values()) == {400}


def test_bad_requests_are_400_and_large_or_binary_code_is_a_bounded_notice(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(world)

    def code(path: str, **params: str) -> Any:
        return client.get(
            "/api/knowledge/reader/code",
            params={"repo": REPO, "commit": world.second, "path": path, **params},
        )

    # A locator that is not a JSON object with a string kind is the caller's error (review F3).
    for locator in ("[]", "1", "{", '{"name": "loadReview"}'):
        refused = code(REVIEW_PATH, locator=locator)
        assert (refused.status_code, refused.json()["state"]) == (400, "invalid-request")
    unresolved = code(REVIEW_PATH, locator='{"kind": "line_range", "start": "x", "end": 2}').json()
    assert unresolved["locator"]["state"] == "unresolved"
    assert (
        client.get(
            "/api/knowledge/reader/path", params={"repo": REPO, "path": "../etc"}
        ).status_code
        == 400
    )
    assert client.get("/api/knowledge/reader/nope", params={"repo": REPO}).status_code == 400
    _assert_control_characters_refused(client)
    directory = code("dashboard").json()
    assert (directory["state"], "not a file" in directory["detail"]) == ("absent", True)
    # A binary blob, or one above the bound, is named with its size and never served (F14).
    binary = code(BINARY_PATH).json()
    assert binary["state"] == "binary" and "text" not in binary
    from agents_remember.application.knowledge_reader import files  # noqa: PLC0415

    monkeypatch.setattr(files, "CODE_TEXT_LIMIT", 16)
    read: list[str] = []
    real = files.read_git_blobs_bytes
    monkeypatch.setattr(
        files, "read_git_blobs_bytes", lambda root, blobs: read.extend(blobs) or real(root, blobs)
    )
    large = code(REVIEW_PATH).json()
    # The size is asked first: a blob above the bound is never read (F17).
    assert (large["state"], "bytes" in large["detail"], read) == ("too-large", True, [])
