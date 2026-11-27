"""Focused unit tests for KNO-R07 rules 8-10: derived FTS5 ix_fts and knowledge_read query views."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_reader.marks_compact import compact_dependent_row
from agents_remember.kernel.primitives.runtime_config import RepositoryScope
from agents_remember.mcp.tools.knowledge import ReadToolRequest, knowledge_read_payload
from agents_remember.memory.knowledge_index import (
    INDEX_FORMAT,
    KnowledgeIndexCache,
)
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.documents import (
    file_sidecar_path,
    record_path,
)
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import TOOL_ANSWER_EXCEPTIONS, bound_for
from test_knowledge_currentness import REVIEW, TESTS, World, _anchor, _body_edit, git
from test_knowledge_paging import SEED_PATH, _member, _member_path, write_family_tree


def _write_json(root: Path, rel: str, payload: dict[str, Any]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_text(payload), encoding="utf-8")


def _add_extra_records(world: World) -> None:
    """Add long-statement invariant, family sibling, weakness, decision, and deferred dependent."""

    long_words = " ".join(f"clause{i}" for i in range(1, 105))
    _write_json(
        world.memory,
        record_path("invariant", "INV-KN7001", "long-statement"),
        {
            "schema": "ar-invariant/v1",
            "id": "INV-KN7001",
            "revision": 1,
            "title": "Long statement sibling rule",
            "statement": f"Host-confirmed archival or absence holds across retries. {long_words}.",
            "applicability": "Applies to CLI delivery handlers.",
            "conditions": ["Archive confirmation must be durable."],
            "exclusions": ["Synthetic dry runs."],
            "supersedes": [],
            "admission": "legacy-unassessed",
            "origin": {"task": "261112-KNO", "leaf": "261112-KNO-L7"},
        },
    )
    _write_json(
        world.memory,
        record_path("invariant", "INV-KN7002", "out-of-scope-sibling"),
        {
            "schema": "ar-invariant/v1",
            "id": "INV-KN7002",
            "revision": 1,
            "statement": "Closing mark stays unique per finalized leaf.",
            "applicability": "Applies to closing marks.",
            "conditions": [],
            "exclusions": [],
            "supersedes": [],
            "admission": "legacy-unassessed",
            "origin": {"task": "261112-KNO", "leaf": "261112-KNO-L7"},
        },
    )
    _write_json(
        world.memory,
        record_path("invariant", "INV-KN7999", "retired-rule"),
        {
            "schema": "ar-invariant/v1",
            "id": "INV-KN7999",
            "revision": 1,
            "status": "retired",
            "statement": "Host-confirmed archival retired rule should never appear in FTS5.",
            "applicability": "Retired.",
            "conditions": [],
            "exclusions": [],
            "supersedes": [],
            "admission": "legacy-unassessed",
            "origin": {"task": "261112-KNO", "leaf": "261112-KNO-L7"},
        },
    )
    _write_json(
        world.memory,
        record_path("family", "FAM-Q00001", "archival-family"),
        {
            "schema": "ar-family/v1",
            "id": "FAM-Q00001",
            "revision": 1,
            "title": "Archival and review family",
            "guarantee": "Review and archival states remain consistent across siblings.",
            "members": ["INV-KN7001", "INV-KN7002"],
            "routes": ["pkg"],
            "admission": "legacy-unassessed",
            "origin": {"task": "261112-KNO", "leaf": "261112-KNO-L7"},
        },
    )

    sibling_path = "pkg/sibling.py"
    sibling_file = world.code / sibling_path
    sibling_file.parent.mkdir(parents=True, exist_ok=True)
    sibling_file.write_text("def mark_closed():\n    return True\n", encoding="utf-8")
    git(world.code, "add", sibling_path)
    git(world.code, "commit", "-m", "add sibling")
    code_tree = git(world.code, "rev-parse", "HEAD^{tree}")
    review_blob = git(world.code, "rev-parse", f"HEAD:{REVIEW}")
    sibling_anchor = _anchor(
        world.code, code_tree, sibling_path, {"kind": "symbol", "name": "mark_closed"}
    )
    review_anchor = _anchor(
        world.code, code_tree, REVIEW, {"kind": "symbol", "name": "_not_listed"}
    )

    # Sidecar for sibling file holding INV-KN7002 (out-of-scope sibling when querying REVIEW).
    _write_json(
        world.memory,
        file_sidecar_path(sibling_path),
        {
            "schema": "ar-onboarding-file/v1",
            "path": sibling_path,
            "references": {},
            "realizes": [
                {
                    "id": "RLZ-S7B001",
                    "invariant": "INV-KN7002",
                    "anchor": sibling_anchor,
                    "rationale": "Implements the closing mark.",
                    "role": "primary-authority",
                }
            ],
            "proves": [],
        },
    )
    # Add INV-KN7001 realization to REVIEW's existing sidecar.
    review_sidecar_path = world.memory / file_sidecar_path(REVIEW)
    review_sidecar = json.loads(review_sidecar_path.read_text(encoding="utf-8"))
    review_sidecar["realizes"].append(
        {
            "id": "RLZ-KN7001",
            "invariant": "INV-KN7001",
            "anchor": review_anchor,
            "rationale": "Exercises archival check in review.",
            "role": "enforcement",
        }
    )
    review_sidecar_path.write_text(canonical_text(review_sidecar), encoding="utf-8")

    # Open weakness + closed weakness (closed must be excluded from ix_fts).
    _write_json(
        world.memory,
        record_path("weakness", "WKN-KN7001", "open-debt"),
        {
            "schema": "ar-weakness/v1",
            "id": "WKN-KN7001",
            "revision": 1,
            "kind": "debt",
            "statement": "Duplicate archival retry scan in review module.",
            "anchor": {"path": REVIEW, **review_anchor},
            "seen": {"task": "261112-KNO", "role": "worker"},
            "state": "open",
            "origin": {"task": "261112-KNO", "leaf": "261112-KNO-L7"},
        },
    )
    _write_json(
        world.memory,
        record_path("weakness", "WKN-KN7002", "closed-debt"),
        {
            "schema": "ar-weakness/v1",
            "id": "WKN-KN7002",
            "revision": 1,
            "kind": "debt",
            "statement": "Host-confirmed archival closed weakness must stay out of FTS5.",
            "anchor": {"path": REVIEW, **review_anchor},
            "seen": {"task": "261112-KNO", "role": "worker"},
            "state": "closed",
            "closed": {"task": "261112-KNO", "reason": "Fixed"},
            "origin": {"task": "261112-KNO", "leaf": "261112-KNO-L7"},
        },
    )
    # Decision record (excluded from default search kinds, included with kinds=["decision"]).
    _write_json(
        world.memory,
        record_path("decision", "DEC-KN7001", "archival-decision"),
        {
            "schema": "ar-decision/v1",
            "id": "DEC-KN7001",
            "revision": 1,
            "status": "active",
            "context": "Host-confirmed archival requires an explicit durable decision record.",
            "alternatives": [{"option": "durable", "status": "chosen", "reason": "safe"}],
            "consequences": ["Keeps archival auditable."],
            "decider": "developer",
            "supersedes": [],
            "links": [],
            "admission": "legacy-unassessed",
            "origin": {"task": "261112-KNO", "leaf": "261112-KNO-L7"},
        },
    )
    # Dependent standing on REVIEW with an open deferral.
    doc_rel = "docs/review-guide.md"
    (world.code / doc_rel).parent.mkdir(parents=True, exist_ok=True)
    (world.code / doc_rel).write_text("# Review Guide\n", encoding="utf-8")
    git(world.code, "add", doc_rel)
    git(world.code, "commit", "-m", "add doc")
    code_head = git(world.code, "rev-parse", "HEAD")
    _write_json(
        world.memory,
        record_path("dependent", "DPD-KN7001", "review-guide"),
        {
            "schema": "ar-dependent/v1",
            "id": "DPD-KN7001",
            "revision": 1,
            "document": doc_rel,
            "stands_on": [
                {"kind": "file", "path": REVIEW, "blob": "0" * 40},
            ],
            "confirmed": {
                "task": "261112-KNO",
                "role": "curator",
                "commit": code_head,
                "uncommitted": False,
                "statement": "Guide matched prior review module.",
            },
            "deferrals": [
                {
                    "id": "DFR-000001",
                    "passage": "Review section",
                    "reason": "Waiting on follow-up leaf",
                    "condition": {"task": "261124-CAP-L5", "file": REVIEW},
                    "fileBlob": review_blob,
                    "owner": "curator",
                    "createdAt": "2026-11-26T00:00:00Z",
                    "createdByLeaf": "261112-KNO-L7",
                }
            ],
            "origin": {"task": "261112-KNO", "leaf": "261112-KNO-L7"},
        },
    )
    git(world.memory, "add", "-A")
    git(world.memory, "commit", "-m", "add kno-r07 test records")


def test_ix_fts_is_deterministic_and_excludes_retired_closed_and_history(tmp_path: Path) -> None:
    world = World(tmp_path / "fts-world")
    _add_extra_records(world)

    cache1 = KnowledgeIndexCache(tmp_path / "cache1")
    cache2 = KnowledgeIndexCache(tmp_path / "cache2")
    with cache1.for_directory(world.memory) as idx1, cache2.for_directory(world.memory) as idx2:
        assert INDEX_FORMAT == "ar-knowledge-index/v8"
        hits1 = idx1.search_fts("host-confirmed archival or absence").value
        hits2 = idx2.search_fts("host-confirmed archival or absence").value
        assert hits1 == hits2
        ids = [h.record_id for h in hits1]
        assert "INV-KN7001" in ids
        assert "INV-KN7999" not in ids
        assert "WKN-KN7002" not in ids
        # Default kinds exclude decision until explicitly requested.
        assert "DEC-KN7001" not in ids
        top = hits1[0]
        assert top.record_id == "INV-KN7001"
        assert top.phrase_matched is True
        assert top.terms_matched == top.terms_total

        dec_hits = idx1.search_fts("host-confirmed archival", kinds=("decision",)).value
        assert [h.record_id for h in dec_hits] == ["DEC-KN7001"]


def test_knowledge_read_impact_search_records_and_compact_dependent(tmp_path: Path) -> None:
    world = World(tmp_path / "query-world")
    _add_extra_records(world)
    # Edit REVIEW so INV-AAAAAA & INV-KN7001 become stale while INV-KN7002 stays current.
    world.change({REVIEW: _body_edit()})

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    repos = {
        "demo": RepositoryScope(repo_id="demo", path=world.code, memory_root=world.memory),
    }
    coord = tmp_path / "coord"

    # 1. view="impact": deduplicated, family-grouped, 1-line out-of-scope sibling, 80-word trim,
    #    weaknesses, dependents, compact mode, 0 unverifiable.
    impact = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(world.memory),
            view="impact",
            files=(REVIEW, TESTS),
        ),
        workspace_root=str(projects_root),
        coordination_root=str(coord),
        repositories=repos,
    )
    assert impact["state"] == "view", impact
    payload = impact["payload"]
    assert payload["codePairing"]["paired"] is True
    fam_by_id = {f["id"]: f for f in payload["families"]}
    assert "FAM-Q00001" in fam_by_id
    fam = fam_by_id["FAM-Q00001"]
    assert "guarantee" in fam
    # Out-of-scope sibling INV-KN7002 surfaced as 1-line entry without querying pkg/sibling.py.
    assert any(
        line.startswith("INV-KN7002:") and "pkg/sibling.py" in line
        for line in fam["familySiblings"]
    )
    by_id = {r["id"]: r for r in fam["rules"]}
    assert set(by_id) == {"INV-KN7001"}
    assert "… (full: records INV-KN7001)" in by_id["INV-KN7001"]["statement"]
    assert "unverifiable" not in by_id["INV-KN7001"]["standing"]
    assert any(w["id"] == "WKN-KN7001" for w in payload["weaknesses"])
    assert any(d["id"] == "DPD-KN7001" for d in payload["dependents"])
    assert count_response_tokens(impact) <= 3500

    # Compact impact mode emits one line per rule.
    impact_compact = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(world.memory),
            view="impact",
            folder="pkg",
            compact=True,
        ),
        workspace_root=str(projects_root),
        coordination_root=str(coord),
        repositories=repos,
    )
    assert impact_compact["state"] == "view"
    assert all(isinstance(r, str) for r in impact_compact["payload"]["families"][0]["rules"])

    # 2. view="search": FTS5 + BM25 + phrase/termsMatched + folder filter + history refused.
    search = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(world.memory),
            view="search",
            query="host-confirmed archival or absence",
            folder="pkg",
        ),
        workspace_root=str(projects_root),
        coordination_root=str(coord),
        repositories=repos,
    )
    assert search["state"] == "view", search
    results = search["payload"]["results"]
    assert results[0]["id"] == "INV-KN7001"
    assert "+phrase" in results[0]["termsMatched"]
    assert "… (full: records INV-KN7001)" in results[0]["statement"]
    assert count_response_tokens(search) <= 3500

    bad_history = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(world.memory),
            view="search",
            query="archival",
            kinds=("history",),
        ),
        workspace_root=str(projects_root),
        coordination_root=str(coord),
        repositories=repos,
    )
    assert bad_history["state"] == "refused"
    assert bad_history["refusalCode"] == "invalid-input"


def test_knowledge_read_records_and_compact_dependent(tmp_path: Path) -> None:
    world = World(tmp_path / "records-world")
    _add_extra_records(world)
    world.change({REVIEW: _body_edit()})

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    repos = {
        "demo": RepositoryScope(repo_id="demo", path=world.code, memory_root=world.memory),
    }
    coord = tmp_path / "coord"

    # 3. view="records": batch verbatim lookup (never trims), inline staleAnchors when stale > 0,
    #    refuses > 25 IDs, 5,000-token bound.
    assert bound_for("knowledge_read") == 5000
    assert TOOL_ANSWER_EXCEPTIONS[("knowledge_read", None)][0] == 5000

    records = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(world.memory),
            view="records",
            record_ids=("INV-AAAAAA", "INV-KN7001", "INV-999999"),
        ),
        workspace_root=str(projects_root),
        coordination_root=str(coord),
        repositories=repos,
    )
    assert records["state"] == "view", records
    rec_payload = records["payload"]
    assert rec_payload["missing"] == ["INV-999999"]
    rec_map = {r["id"]: r for r in rec_payload["records"]}
    # Never trims statements!
    assert "clause104" in rec_map["INV-KN7001"]["statement"]
    assert "… (full: records" not in rec_map["INV-KN7001"]["statement"]
    assert rec_map["INV-KN7001"]["conditions"] == ["Archive confirmation must be durable."]
    assert rec_map["INV-KN7001"]["exclusions"] == ["Synthetic dry runs."]
    assert f"{REVIEW}::_not_listed" in rec_map["INV-KN7001"]["realizedIn"]
    # Stale invariant names stale anchor paths and states inline + total stale count.
    assert rec_map["INV-AAAAAA"]["staleAnchors"]["count"] >= 1
    assert any(e["path"] == REVIEW for e in rec_map["INV-AAAAAA"]["staleAnchors"]["entries"])
    assert count_response_tokens(records) <= 5000

    too_many = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(world.memory),
            view="records",
            record_ids=tuple(f"INV-{i:06d}" for i in range(26)),
        ),
        workspace_root=str(projects_root),
        coordination_root=str(coord),
        repositories=repos,
    )
    assert too_many["state"] == "refused"
    assert too_many["refusalCode"] == "invalid-input"

    # 4. view="dependent" with compact=True: short rows, 0 blob hashes, 0 movingCommitUnknown prose.
    dep = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(world.memory),
            view="dependent",
            stale_only=True,
            compact=True,
        ),
        workspace_root=str(projects_root),
        coordination_root=str(coord),
        repositories=repos,
    )
    assert dep["state"] == "view", dep
    assert len(dep["rows"]) == 1
    row = dep["rows"][0]
    assert row["id"] == "DPD-KN7001"
    assert row["state"] == "deferred"
    assert row["deferred"] is True
    assert row["conditionTask"] == "261124-CAP-L5"
    assert row["conditionFile"] == REVIEW
    assert row["confirmedTask"] == "261112-KNO"
    assert len(row["confirmedCommit"]) == 10
    assert row["confirmedUncommitted"] is False
    assert isinstance(row["movedLinks"], int) and row["movedLinks"] == 1
    serialized = str(dep)
    assert "0" * 40 not in serialized
    assert "movingCommitUnknown" not in serialized
    assert count_response_tokens(dep) <= 1500


def test_compact_dependent_row_aggregates_multiple_deferrals() -> None:
    compact = compact_dependent_row(
        {
            "id": "DPD-M7AT11",
            "document": "docs/a.md",
            "state": "deferred",
            "deferred": True,
            "confirmed": {
                "task": "261112-KNO",
                "commit": "abcdef1234567890abcdef1234567890abcdef12",
                "uncommitted": True,
            },
            "deferrals": [
                {"condition": {"task": "T-2", "file": "b.py"}},
                {"condition": {"task": "T-1", "file": "a.py"}},
            ],
            "movedLinks": [{"path": "a.py"}, {"path": "b.py"}],
        }
    )
    assert compact == {
        "id": "DPD-M7AT11",
        "document": "docs/a.md",
        "state": "deferred",
        "deferred": True,
        "conditionTask": ["T-1", "T-2"],
        "conditionFile": ["a.py", "b.py"],
        "confirmedTask": "261112-KNO",
        "confirmedCommit": "abcdef1234",
        "confirmedUncommitted": True,
        "movedLinks": 2,
    }


def _init_paired_family(tmp_path: Path, count: int) -> tuple[Path, Path]:
    code = tmp_path / "code"
    memory = tmp_path / "memory"
    code.mkdir(parents=True)
    memory.mkdir(parents=True)
    for repo in (code, memory):
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "Test")
    for n in range(count):
        rel = _member_path(n)
        target = code / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"def member_{n}():\n    return {n}\n", encoding="utf-8")
    git(code, "add", "-A")
    git(code, "commit", "-q", "-m", "family code")
    statements = [f"Member {n} holds unconditionally across callers." for n in range(count)]
    write_family_tree(memory, statements)
    # Attach explicit symbol locators to each member sidecar so realizedIn carries path::symbol.
    code_tree = git(code, "rev-parse", "HEAD^{tree}")
    for n in range(count):
        rel = _member_path(n)
        sc_path = memory / file_sidecar_path(rel)
        sc = json.loads(sc_path.read_text(encoding="utf-8"))
        sc["realizes"][0]["anchor"] = _anchor(
            code, code_tree, rel, {"kind": "symbol", "name": f"member_{n}"}
        )
        sc_path.write_text(canonical_text(sc), encoding="utf-8")
    git(memory, "add", "-A")
    git(memory, "commit", "-q", "-m", "family memory")
    return code, memory


def test_records_preserves_symbols_at_15_ids_and_all_multi_symbol_proofs(tmp_path: Path) -> None:
    """KNO-R07-F1: >=13 IDs never collapse realizedIn symbols, and proofs keep every symbol verbatim."""

    code, memory = _init_paired_family(tmp_path, 15)
    proof_rel = "tests/test_multi_proof.py"
    proof_file = code / proof_rel
    proof_file.parent.mkdir(parents=True, exist_ok=True)
    proof_file.write_text(
        "\n\n".join(f"def test_proof_{i}():\n    assert {i} == {i}" for i in range(1, 5)) + "\n",
        encoding="utf-8",
    )
    git(code, "add", proof_rel)
    git(code, "commit", "-q", "-m", "add four-symbol proof file")
    code_tree = git(code, "rev-parse", "HEAD^{tree}")
    _write_json(
        memory,
        file_sidecar_path(proof_rel),
        {
            "schema": "ar-onboarding-file/v1",
            "path": proof_rel,
            "references": {},
            "realizes": [],
            "proves": [
                {
                    "id": f"PRF-M0000{i}",
                    "invariant": _member(0),
                    "anchor": _anchor(
                        code, code_tree, proof_rel, {"kind": "symbol", "name": f"test_proof_{i}"}
                    ),
                    "facet": f"Proves facet {i}.",
                }
                for i in range(1, 5)
            ],
        },
    )
    git(memory, "add", "-A")
    git(memory, "commit", "-q", "-m", "add four-symbol proof sidecar")

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    repos = {"demo": RepositoryScope(repo_id="demo", path=code, memory_root=memory)}
    coord = tmp_path / "coord"

    requested = tuple(_member(i) for i in range(15))
    collected: dict[str, dict[str, Any]] = {}
    cursor: str | None = None
    while True:
        page = knowledge_read_payload(
            ReadToolRequest(
                memory_root=str(memory),
                view="records",
                record_ids=requested,
                continuation=cursor,
            ),
            workspace_root=str(projects_root),
            coordination_root=str(coord),
            repositories=repos,
        )
        assert page["state"] == "view", page
        assert count_response_tokens(page) <= 5000
        for row in page["payload"]["records"]:
            collected[row["id"]] = row
        cursor = page.get("continuation")
        if cursor is None:
            break

    assert set(collected) == set(requested)
    for n in range(15):
        inv_id = _member(n)
        expected_realized = f"{_member_path(n)}::member_{n}"
        assert expected_realized in collected[inv_id]["realizedIn"], (inv_id, collected[inv_id])
    proof_entries = collected[_member(0)]["proofs"]
    assert len(proof_entries) == 1
    assert "(+" not in proof_entries[0]
    for i in range(1, 5):
        assert f"test_proof_{i}" in proof_entries[0]


def test_impact_30_member_family_names_every_out_of_scope_sibling(tmp_path: Path) -> None:
    """KNO-R07-F2: 30-member family names all 29 out-of-scope siblings via deterministic pagination."""

    code, memory = _init_paired_family(tmp_path, 30)
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    repos = {"demo": RepositoryScope(repo_id="demo", path=code, memory_root=memory)}
    coord = tmp_path / "coord"

    siblings: list[str] = []
    pages = 0
    cursor: str | None = None
    while True:
        impact = knowledge_read_payload(
            ReadToolRequest(
                memory_root=str(memory),
                view="impact",
                files=(SEED_PATH,),
                continuation=cursor,
            ),
            workspace_root=str(projects_root),
            coordination_root=str(coord),
            repositories=repos,
        )
        assert impact["state"] == "view", impact
        assert count_response_tokens(impact) <= 3500
        pages += 1
        families = impact["payload"]["families"]
        assert len(families) == 1
        page_siblings = families[0]["familySiblings"]
        # Must remain a plain list (never wrapped by L108 truncate_lists) with zero +N truncation.
        assert isinstance(page_siblings, list), page_siblings
        assert not any("more siblings" in line for line in page_siblings)
        siblings.extend(page_siblings)
        cursor = impact.get("continuation")
        if cursor is None:
            assert impact["payload"]["enumerationComplete"] is True
            break
        assert impact["payload"]["enumerationComplete"] is False

    assert pages == 2
    assert len(siblings) == 29
    for n in range(1, 30):
        inv_id = _member(n)
        assert any(line.startswith(f"{inv_id}:") for line in siblings), inv_id
