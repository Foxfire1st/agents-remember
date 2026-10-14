"""MIK-R53 Part B: every curator judgment about knowledge has a row.

RED-first coverage for packet rules 6-9: the ``membership`` row kind and the
``unassigned_invariant`` worklist item (rule 6), the ``record`` row kind and the
``touched_record`` item (rule 8), and the shared checks over the new kinds
(rule 9). Rule 7 is this leaf's curation; the writer path it uses is covered
here and exercised on a scratch clone as evidence.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agents_remember.application.knowledge_gate.predicates import (
    GateContext,
    membership_row_open,
    record_row_open,
)
from agents_remember.application.knowledge_reader import ReaderQuery, read_knowledge_reader
from agents_remember.application.knowledge_worklist import ITEM_KINDS
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.membership import (
    UNASSIGNED_INVARIANT_KIND,
    membership_items,
)
from agents_remember.application.knowledge_worklist.touched_records import (
    TOUCHED_RECORD_KIND,
    touched_record_items,
)
from agents_remember.application.knowledge_writer import Owner, WriteRequest, write_knowledge
from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.application.knowledge_writer.handoff import read_handoff
from agents_remember.application.knowledge_writer.writer import _writer_split
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.memory.knowledge_index import MemoryTreeSnapshot
from agents_remember.memory.knowledge_index.tree import directory_snapshot, git_tree_snapshot
from agents_remember.memory_quality.knowledge_validator.parsed import parse_tree
from agents_remember.memory_quality.knowledge_validator.registry import (
    ValidationContext,
    merge_report_note,
)
from agents_remember.memory_quality.knowledge_validator.report import (
    KnowledgeValidationError,
    ValidationReport,
    Violation,
)
from agents_remember.memory_quality.knowledge_validator.rules_history import check_history_rows
from agents_remember.memory_quality.knowledge_validator.trees import (
    CodeDirectory,
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
)
from agents_remember.memory_quality.knowledge_validator.validator import require_valid_commit
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.history import (
    HISTORY_ROW_KINDS,
    HistoryFile,
    MembershipRow,
    RecordRow,
    membership_subject,
)
from knowledge_validator_test_support import encode, tree
from knowledge_writer_test_support import (
    BASE_FAMILY,
    BASE_INVARIANT,
    CODE_FILE,
    LEAF_ID,
    TASK_ID,
    build_world,
    commit_all,
    write,
)

OWNER = Owner(task=TASK_ID, kind="leaf", id=LEAF_ID)
HISTORY_PATH = f"knowledge/history/{LEAF_ID}.json"
INVARIANT = "INV-AAAAAA"
OTHER_INVARIANT = "INV-BBBBBB"
FAMILY = "FAM-CCCCC1"
DECISION = "DEC-DDDDDD"
ORIGIN = {"task": "260928-MIK", "leaf": LEAF_ID}
LAYOUT = {"schema": "ar-memory-layout/v2", "conversion": "1"}


def _invariant(identifier: str, **changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": "ar-invariant/v1",
        "id": identifier,
        "origin": ORIGIN,
        "revision": 1,
        "status": "accepted",
        "statement": "Every judgment has a row.",
        "applicability": "All curated knowledge.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
    }
    document.update(changes)
    return document


def _family(identifier: str, members: list[str]) -> dict[str, Any]:
    return {
        "schema": "ar-family/v1",
        "id": identifier,
        "origin": ORIGIN,
        "revision": 1,
        "status": "accepted",
        "title": "Judgments",
        "guarantee": "Every judgment has a row.",
        "members": members,
        "routes": [],
        "admission": "legacy-unassessed",
    }


def _decision(identifier: str, **changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": "ar-decision/v1",
        "id": identifier,
        "origin": ORIGIN,
        "revision": 1,
        "status": "active",
        "context": "A choice was needed.",
        "alternatives": [
            {"option": "Rows", "status": "chosen", "reason": "They bind."},
            {
                "option": "No rows",
                "status": "rejected",
                "reason": "They drift.",
                "reconsider_when": "Judgments stop drifting.",
            },
        ],
        "consequences": ["Judgments are recorded."],
        "decider": "developer",
        "supersedes": [],
        "admission": "legacy-unassessed",
        "links": [],
    }
    document.update(changes)
    return document


def _side(files: dict[str, bytes], label: str) -> KnowledgeSide:
    return KnowledgeSide.from_snapshot(
        label,
        MemoryTreeSnapshot(key=label, source="directory", location=label, files=files),
    )


def _files(*documents: tuple[str, dict[str, Any]]) -> dict[str, bytes]:
    files = {"knowledge/layout.json": canonical_text(LAYOUT).encode("utf-8")}
    for path, document in documents:
        files[path] = canonical_text(document).encode("utf-8")
    return files


def _history(rows: list[dict[str, Any]]) -> HistoryFile:
    return HistoryFile.model_validate(
        {"schema": "ar-history/v1", "leaf": LEAF_ID, "closed": False, "rows": rows}
    )


def _context(history: HistoryFile, revision: int | None = 1) -> GateContext:
    candidate = SimpleNamespace(revision=lambda record_id: revision)
    return GateContext(
        owner=LEAF_ID, history=history, candidate=cast(Any, candidate), code=cast(Any, None)
    )


# rule 6: no family ---------------------------------------------------------------------------------


def test_membership_row_validates() -> None:
    row = MembershipRow.model_validate(
        {
            "id": "ROW-AAAAAA",
            "subject": membership_subject(INVARIANT),
            "disposition": "no_family",
            "reason": "No joint obligation is supported.",
            "items": [],
            "revision": 1,
            "considered": [FAMILY],
        }
    )
    assert row.revision == 1
    assert list(row.considered) == [FAMILY]


def test_membership_row_rejects_another_disposition() -> None:
    with pytest.raises(ValueError, match="no_family"):
        MembershipRow.model_validate(
            {
                "id": "ROW-AAAAAA",
                "subject": membership_subject(INVARIANT),
                "disposition": "changed",
                "reason": "No joint obligation is supported.",
                "items": [],
                "revision": 1,
            }
        )


def test_eight_row_kinds_are_registered() -> None:
    assert [kind.name for kind in HISTORY_ROW_KINDS] == [
        "invariant",
        "family",
        "onboarding_trace",
        "planned",
        "unexplained",
        "reconsideration",
        "membership",
        "record",
    ]


def test_unassigned_item_raised_for_a_new_unlisted_invariant() -> None:
    base = _side(_files(), "base")
    candidate = _side(
        _files((f"knowledge/invariants/{INVARIANT}-row.json", _invariant(INVARIANT))),
        "candidate",
    )
    items = membership_items(base, candidate)
    assert [(item["kind"], item["subject"]) for item in items] == [
        (UNASSIGNED_INVARIANT_KIND.name, membership_subject(INVARIANT))
    ]


def test_unassigned_item_raised_for_a_newly_unlisted_invariant() -> None:
    base = _side(
        _files(
            (f"knowledge/invariants/{INVARIANT}-row.json", _invariant(INVARIANT)),
            (f"knowledge/families/{FAMILY}-rows.json", _family(FAMILY, [INVARIANT])),
        ),
        "base",
    )
    candidate = _side(
        _files(
            (f"knowledge/invariants/{INVARIANT}-row.json", _invariant(INVARIANT)),
            (f"knowledge/families/{FAMILY}-rows.json", _family(FAMILY, [])),
        ),
        "candidate",
    )
    items = membership_items(base, candidate)
    assert [(item["kind"], item["subject"]) for item in items] == [
        (UNASSIGNED_INVARIANT_KIND.name, membership_subject(INVARIANT))
    ]


def test_unassigned_item_not_raised_while_a_family_lists_the_invariant() -> None:
    files = _files(
        (f"knowledge/invariants/{INVARIANT}-row.json", _invariant(INVARIANT)),
        (f"knowledge/families/{FAMILY}-rows.json", _family(FAMILY, [INVARIANT])),
    )
    assert membership_items(_side(files, "base"), _side(files, "candidate")) == []


def test_unassigned_item_not_raised_for_an_old_orphan() -> None:
    files = _files((f"knowledge/invariants/{INVARIANT}-row.json", _invariant(INVARIANT)))
    assert membership_items(_side(files, "base"), _side(files, "candidate")) == []


def test_membership_row_satisfies_at_the_current_revision() -> None:
    history = _history(
        [
            {
                "id": "ROW-AAAAAA",
                "subject": membership_subject(INVARIANT),
                "disposition": "no_family",
                "reason": "No joint obligation is supported.",
                "items": [],
                "revision": 1,
                "considered": [],
            }
        ]
    )
    item = {
        "id": "sha256:" + "e" * 64,
        "kind": UNASSIGNED_INVARIANT_KIND,
        "subject": membership_subject(INVARIANT),
        "facts": {"revision": 1},
    }
    assert membership_row_open(item, _context(history, revision=1)) is None


def test_membership_row_at_a_stale_revision_leaves_the_item_open() -> None:
    history = _history(
        [
            {
                "id": "ROW-AAAAAA",
                "subject": membership_subject(INVARIANT),
                "disposition": "no_family",
                "reason": "No joint obligation is supported.",
                "items": [],
                "revision": 1,
                "considered": [],
            }
        ]
    )
    item = {
        "id": "sha256:" + "e" * 64,
        "kind": UNASSIGNED_INVARIANT_KIND,
        "subject": membership_subject(INVARIANT),
        "facts": {"revision": 2},
    }
    reason = membership_row_open(item, _context(history, revision=2))
    assert reason is not None
    assert "revision" in reason


def test_writer_refuses_a_membership_row_for_a_familied_invariant(
    tmp_path: Path,
) -> None:
    world = build_world(tmp_path)
    report = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document={
                "history": [
                    {
                        "subject": membership_subject(BASE_INVARIANT),
                        "disposition": "no_family",
                        "reason": "No joint obligation is supported.",
                        "considered": [],
                    }
                ]
            },
            commit=True,
        ),
        code=CodeSnapshot(root=world.code, tree="candidate", blobs={}),
    )
    assert report.state == "refused"
    assert any("family" in problem.render() for problem in report.problems)


def test_writer_records_a_membership_row_for_an_unlisted_invariant(
    tmp_path: Path,
) -> None:
    world = build_world(tmp_path)
    fresh = "INV-AAAAAB"
    document = _invariant(
        fresh,
        statement="A lone judgment.",
        admission={
            "criteria": ["prevents_costly_mistake"],
            "justification": "Guards the costly mistake of landing an unpaired commit.",
        },
        origin={"task": TASK_ID, "leaf": LEAF_ID},
    )
    write(world.memory, {f"knowledge/invariants/{fresh}-lone.json": canonical_text(document)})
    report = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document={
                "history": [
                    {
                        "subject": membership_subject(fresh),
                        "disposition": "no_family",
                        "reason": "No joint obligation is supported.",
                        "considered": [BASE_FAMILY],
                    }
                ]
            },
            commit=True,
        ),
        code=CodeSnapshot(root=world.code, tree="candidate", blobs={}),
    )
    assert report.state == "written"
    stored = HistoryFile.model_validate(
        json.loads((world.memory / HISTORY_PATH).read_text(encoding="utf-8"))
    )
    row = stored.row_about(membership_subject(fresh))
    assert isinstance(row, MembershipRow)
    assert row.revision == 1
    assert list(row.considered) == [BASE_FAMILY]


# rule 8: decisions and facets ------------------------------------------------------------------------


def test_record_row_validates_a_change() -> None:
    row = RecordRow.model_validate(
        {
            "id": "ROW-AAAAAA",
            "subject": DECISION,
            "disposition": "changed",
            "reason": "The alternatives were rewritten.",
            "items": [],
            "revision": 2,
            "effect": "clarify",
        }
    )
    assert row.effect == "clarify"


def test_record_changed_row_requires_its_effect() -> None:
    with pytest.raises(ValueError, match="effect"):
        RecordRow.model_validate(
            {
                "id": "ROW-AAAAAA",
                "subject": DECISION,
                "disposition": "changed",
                "reason": "The alternatives were rewritten.",
                "items": [],
                "revision": 2,
            }
        )


def test_record_retired_row_carries_retire() -> None:
    row = RecordRow.model_validate(
        {
            "id": "ROW-AAAAAA",
            "subject": DECISION,
            "disposition": "retired",
            "reason": "The decision no longer governs.",
            "items": [],
            "revision": 1,
            "effect": "retire",
        }
    )
    assert row.effect == "retire"


def test_record_no_impact_row_carries_no_effect() -> None:
    with pytest.raises(ValueError, match="effect"):
        RecordRow.model_validate(
            {
                "id": "ROW-AAAAAA",
                "subject": DECISION,
                "disposition": "no_impact",
                "reason": "The record is as it was.",
                "items": [],
                "revision": 1,
                "effect": "clarify",
            }
        )


def test_touched_record_raised_when_the_json_differs() -> None:
    base = _side(
        _files((f"knowledge/decisions/{DECISION}-rows.json", _decision(DECISION))),
        "base",
    )
    candidate = _side(
        _files(
            (
                f"knowledge/decisions/{DECISION}-rows.json",
                _decision(DECISION, revision=2, context="Another choice was needed."),
            )
        ),
        "candidate",
    )
    items = touched_record_items(base, candidate)
    assert [(item["kind"], item["subject"]) for item in items] == [
        (TOUCHED_RECORD_KIND.name, DECISION)
    ]


def test_touched_record_not_raised_for_a_new_record() -> None:
    base = _side(_files(), "base")
    candidate = _side(
        _files((f"knowledge/decisions/{DECISION}-rows.json", _decision(DECISION))),
        "candidate",
    )
    assert touched_record_items(base, candidate) == []


def test_touched_record_not_raised_for_the_leaf_own_raise_mark() -> None:
    base = _side(
        _files((f"knowledge/decisions/{DECISION}-rows.json", _decision(DECISION))),
        "base",
    )
    candidate_files = _files(
        (
            f"knowledge/decisions/{DECISION}-rows.json",
            _decision(DECISION, status="under_reconsideration"),
        ),
        (
            HISTORY_PATH,
            {
                "schema": "ar-history/v1",
                "leaf": LEAF_ID,
                "closed": False,
                "rows": [
                    {
                        "id": "ROW-AAAAAA",
                        "subject": f"reconsider:{DECISION}#1",
                        "disposition": "raise",
                        "reason": "The grounds changed.",
                        "items": [],
                    }
                ],
            },
        ),
    )
    candidate = _side(candidate_files, "candidate")
    assert touched_record_items(base, candidate, LEAF_ID) == []


def _record_item(**facts: Any) -> dict[str, Any]:
    return {
        "id": "sha256:" + "f" * 64,
        "kind": TOUCHED_RECORD_KIND,
        "subject": DECISION,
        "facts": facts,
    }


def _record_history(disposition: str, revision: int) -> HistoryFile:
    fields: dict[str, Any] = {
        "id": "ROW-AAAAAA",
        "subject": DECISION,
        "disposition": disposition,
        "reason": "Judged.",
        "items": [],
        "revision": revision,
    }
    if disposition in {"changed", "retired"}:
        fields["effect"] = "clarify" if disposition == "changed" else "retire"
    return _history([fields])


def test_changed_is_required_when_the_revision_is_higher() -> None:
    item = _record_item(baseRevision=1, revision=2, baseStatus="active", status="active")
    assert record_row_open(item, _context(_record_history("changed", 2), revision=2)) is None
    reason = record_row_open(item, _context(_record_history("no_impact", 2), revision=2))
    assert reason is not None
    assert "changed" in reason


def test_retired_is_required_when_the_status_became_retired() -> None:
    item = _record_item(baseRevision=1, revision=1, baseStatus="active", status="retired")
    assert record_row_open(item, _context(_record_history("retired", 1), revision=1)) is None
    reason = record_row_open(item, _context(_record_history("no_impact", 1), revision=1))
    assert reason is not None
    assert "retired" in reason


def test_retired_wins_when_the_revision_also_grew() -> None:
    item = _record_item(baseRevision=1, revision=2, baseStatus="active", status="retired")
    assert record_row_open(item, _context(_record_history("retired", 2), revision=2)) is None
    reason = record_row_open(item, _context(_record_history("changed", 2), revision=2))
    assert reason is not None
    assert "retired" in reason


def test_no_impact_answers_an_unchanged_record() -> None:
    item = _record_item(baseRevision=1, revision=1, baseStatus="active", status="active")
    assert record_row_open(item, _context(_record_history("no_impact", 1), revision=1)) is None


# F05 (round 1): real git-tree/directory routes see record prose ----------------


ASM_JSON = "knowledge/assumptions/ASM-100K0R-under-100k.json"
ASM_MD = "knowledge/assumptions/ASM-100K0R-under-100k.md"


def _assumption_files(prose: str | None) -> dict[str, str | bytes]:
    files: dict[str, str | bytes] = {
        "knowledge/layout.json": canonical_text(
            {"schema": "ar-memory-layout/v2", "conversion": "1"}
        ),
        ASM_JSON: canonical_text(
            {
                "schema": "ar-assumption/v1",
                "id": "ASM-100K0R",
                "revision": 1,
                "status": "accepted",
                "proposition": "Knowledge stays small.",
                "basis": "Today's memory holds few records.",
                "links": [],
                "origin": {"task": "260928-MIK", "leaf": "260928-MIK-L53"},
            }
        ),
    }
    if prose is not None:
        files[ASM_MD] = prose
    return files


def _repo(root: Path, files: dict[str, str | bytes]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "f05@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "f05"], cwd=root, check=True)
    write(root, files)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=root, check=True)
    return root


def _real_sides(root: Path) -> tuple[KnowledgeSide, KnowledgeSide]:
    base = KnowledgeSide.from_snapshot("K_B", git_tree_snapshot(root, "HEAD"))
    candidate = KnowledgeSide.from_snapshot("K_C", directory_snapshot(root))
    return base, candidate


def _subjects(items: list[dict[str, Any]]) -> list[str]:
    return [item["subject"] for item in items]


def test_markdown_only_edit_raises_through_real_snapshots(tmp_path: Path) -> None:
    root = _repo(tmp_path / "memory", _assumption_files("Basis prose.\n"))
    (root / ASM_MD).write_text("Basis prose, revised.\n", encoding="utf-8")
    base, candidate = _real_sides(root)
    assert _subjects(touched_record_items(base, candidate)) == ["ASM-100K0R"]


def test_added_prose_raises_through_real_snapshots(tmp_path: Path) -> None:
    root = _repo(tmp_path / "memory", _assumption_files(None))
    (root / ASM_MD).write_text("Basis prose, added.\n", encoding="utf-8")
    base, candidate = _real_sides(root)
    assert _subjects(touched_record_items(base, candidate)) == ["ASM-100K0R"]


def test_deleted_prose_raises_through_real_snapshots(tmp_path: Path) -> None:
    root = _repo(tmp_path / "memory", _assumption_files("Basis prose.\n"))
    (root / ASM_MD).unlink()
    base, candidate = _real_sides(root)
    assert _subjects(touched_record_items(base, candidate)) == ["ASM-100K0R"]


def test_unchanged_prose_raises_nothing_through_real_snapshots(tmp_path: Path) -> None:
    root = _repo(tmp_path / "memory", _assumption_files("Basis prose.\n"))
    base, candidate = _real_sides(root)
    assert touched_record_items(base, candidate) == []


# F06 (round 1): only the current leaf's own status-only raise is exempt --------


RAISE_SUBJECT = f"reconsider:{DECISION}#1"
DEC_JSON = f"knowledge/decisions/{DECISION}-rows.json"
DEC_MD = f"knowledge/decisions/{DECISION}-rows.md"


def _raise_history(leaf: str, *, closed: bool) -> bytes:
    return canonical_text(
        {
            "schema": "ar-history/v1",
            "leaf": leaf,
            "closed": closed,
            "rows": [
                {
                    "id": "ROW-RA15E0",
                    "subject": RAISE_SUBJECT,
                    "disposition": "raise",
                    "reason": "Ask the developer.",
                    "items": [],
                }
            ],
        }
    ).encode("utf-8")


def _marked_json() -> bytes:
    return canonical_text(_decision(DECISION, status="under_reconsideration")).encode("utf-8")


def _encoded(files: dict[str, str | bytes]) -> dict[str, bytes]:
    return {
        path: content if isinstance(content, bytes) else content.encode("utf-8")
        for path, content in files.items()
    }


def _raise_sides(
    *,
    leaf: str = LEAF_ID,
    closed: bool = False,
    candidate_md: bytes | None = b"Decision prose.\n",
) -> tuple[KnowledgeSide, KnowledgeSide]:
    base_files: dict[str, str | bytes] = {
        "knowledge/layout.json": canonical_text(LAYOUT),
        DEC_JSON: canonical_text(_decision(DECISION)),
        DEC_MD: "Decision prose.\n",
    }
    candidate_files: dict[str, str | bytes] = dict(base_files)
    candidate_files[DEC_JSON] = _marked_json()
    history = _raise_history(leaf, closed=closed)
    history_path = f"knowledge/history/{leaf}.json"
    base_files[history_path] = history
    candidate_files[history_path] = history
    if candidate_md is None:
        del candidate_files[DEC_MD]
    else:
        candidate_files[DEC_MD] = candidate_md
    base = KnowledgeSide.from_snapshot(
        "K_B",
        MemoryTreeSnapshot(key="b", source="directory", location="b", files=_encoded(base_files)),
    )
    candidate = KnowledgeSide.from_snapshot(
        "K_C",
        MemoryTreeSnapshot(
            key="c", source="directory", location="c", files=_encoded(candidate_files)
        ),
    )
    return base, candidate


def test_own_open_raise_with_status_only_change_is_exempt() -> None:
    base, candidate = _raise_sides()
    assert touched_record_items(base, candidate, LEAF_ID) == []


def test_foreign_raise_does_not_exempt_a_status_edit() -> None:
    base, candidate = _raise_sides(leaf="OTHER-LEAF")
    assert _subjects(touched_record_items(base, candidate, LEAF_ID)) == [DECISION]


def test_closed_raise_does_not_exempt_a_status_edit() -> None:
    base, candidate = _raise_sides(closed=True)
    assert _subjects(touched_record_items(base, candidate, LEAF_ID)) == [DECISION]


def test_own_raise_with_changed_prose_does_not_exempt() -> None:
    base, candidate = _raise_sides(candidate_md=b"Decision prose, revised.\n")
    assert _subjects(touched_record_items(base, candidate, LEAF_ID)) == [DECISION]


def test_other_json_change_beside_a_raise_still_raises() -> None:
    base, candidate = _raise_sides()
    files = dict(candidate.files)
    document = json.loads(files[DEC_JSON])
    document["consequences"] = ["Judgments are recorded, loudly."]
    files[DEC_JSON] = canonical_text(document).encode("utf-8")
    candidate = KnowledgeSide.from_snapshot(
        "K_C", MemoryTreeSnapshot(key="c2", source="directory", location="c", files=files)
    )
    assert _subjects(touched_record_items(base, candidate, LEAF_ID)) == [DECISION]


def _decision_fields(context: str) -> dict[str, Any]:
    return {
        "context": context,
        "alternatives": [
            {"option": "A", "status": "chosen", "reason": "It binds."},
            {
                "option": "B",
                "status": "rejected",
                "reason": "It drifts.",
                "reconsider_when": "Drift stops.",
            },
        ],
        "consequences": ["Recorded."],
        "decider": "developer",
        "supersedes": [],
        "links": [],
        "admission": {
            "criteria": ["real_alternatives"],
            "justification": "Two real designs were weighed.",
        },
    }


def _ruling_entry(entry_id: str, evidence: str) -> dict[str, Any]:
    return {
        "id": entry_id,
        "statement": "Evidence is stored in the row.",
        "kind": "finding",
        "target": [],
        "found_at": [],
        "disposition": "recorded",
        "evidence": evidence,
    }


# F07 (round 1): aliases resolve to canonical subjects before dispatch ---------


def _write_document(world: Any, document: dict[str, Any]) -> Any:
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document=document,
            commit=True,
        ),
        code=CodeSnapshot(root=world.code, tree="candidate", blobs={}),
    )


def _stored_history(world: Any) -> HistoryFile:
    return HistoryFile.model_validate(
        json.loads((world.memory / HISTORY_PATH).read_text(encoding="utf-8"))
    )


def _decision_world(tmp_path: Path) -> Any:
    world = build_world(tmp_path)
    write(
        world.memory,
        {
            f"knowledge/decisions/{DECISION}-rows.json": canonical_text(
                _decision(
                    DECISION,
                    admission={
                        "criteria": ["real_alternatives"],
                        "justification": "Two real designs were weighed.",
                    },
                )
            ),
            f"knowledge/decisions/{DECISION}-rows.md": "Decision prose.\n",
        },
    )
    return world


def _alias_document() -> dict[str, Any]:
    return {
        "records": [{"key": "D-9", "kind": "decision", "fields": {}, "id": DECISION}],
        "history": [
            {
                "subject": "handoff:D-9",
                "disposition": "changed",
                "effect": "clarify",
                "reason": "The meaning sharpened.",
            }
        ],
    }


def test_handoff_alias_writes_the_canonical_record_subject(tmp_path: Path) -> None:
    world = _decision_world(tmp_path)
    report = _write_document(world, _alias_document())
    assert report.state == "written", report.render()
    row = _stored_history(world).row_about(DECISION)
    assert isinstance(row, RecordRow)
    assert row.disposition == "changed"


def test_handoff_alias_rerun_keeps_the_row_id(tmp_path: Path) -> None:
    world = _decision_world(tmp_path)
    first = _write_document(world, _alias_document())
    assert first.state == "written", first.render()
    second = _write_document(world, _alias_document())
    assert second.state == "written", second.render()
    again = _stored_history(world).row_about(DECISION)
    assert again is not None and again.id == first.rows[0].id


def test_bare_invariant_rerun_keeps_the_row_id(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    write(
        world.memory,
        {
            "knowledge/invariants/INV-CCCCCC-lone.json": canonical_text(
                _invariant(
                    "INV-CCCCCC",
                    admission={
                        "criteria": ["prevents_costly_mistake"],
                        "justification": "Guards the costly mistake of landing unpaired.",
                    },
                )
            ),
        },
    )
    document = {
        "history": [
            {
                "subject": "INV-CCCCCC",
                "disposition": "no_family",
                "reason": "Stands alone.",
                "considered": [],
            }
        ]
    }
    first = _write_document(world, document)
    assert first.state == "written", first.render()
    canonical = _stored_history(world).row_about("membership:INV-CCCCCC")
    assert isinstance(canonical, MembershipRow)
    second = _write_document(world, document)
    assert second.state == "written", second.render()
    kept = _stored_history(world).row_about("membership:INV-CCCCCC")
    assert kept is not None
    assert kept.id == canonical.id


def test_unknown_handoff_alias_refuses_without_writing(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    assert not (world.memory / HISTORY_PATH).exists()
    report = _write_document(
        world,
        {
            "history": [
                {
                    "subject": "handoff:NOPE",
                    "disposition": "changed",
                    "effect": "clarify",
                    "reason": "Nothing names this.",
                }
            ]
        },
    )
    assert report.state == "refused"
    assert "handoff:NOPE" in report.render()
    assert not (world.memory / HISTORY_PATH).exists()


def test_membership_alias_for_a_decision_refuses(tmp_path: Path) -> None:
    world = _decision_world(tmp_path)
    report = _write_document(
        world,
        {
            "history": [
                {
                    "subject": f"membership:{DECISION}",
                    "disposition": "no_family",
                    "reason": "Not an invariant.",
                    "considered": [],
                }
            ]
        },
    )
    assert report.state == "refused"


# F08 (round 1): malformed considered is named and refuses ---------------------


def _lone_world(tmp_path: Path) -> Any:
    world = build_world(tmp_path)
    write(
        world.memory,
        {
            "knowledge/invariants/INV-CCCCCC-lone.json": canonical_text(
                _invariant(
                    "INV-CCCCCC",
                    admission={
                        "criteria": ["prevents_costly_mistake"],
                        "justification": "Guards the costly mistake of landing unpaired.",
                    },
                )
            ),
        },
    )
    return world


def _considered_document(considered: Any) -> dict[str, Any]:
    return {
        "history": [
            {
                "subject": "INV-CCCCCC",
                "disposition": "no_family",
                "reason": "Stands alone.",
                "considered": considered,
            }
        ]
    }


def test_malformed_considered_container_is_named_and_refused(tmp_path: Path) -> None:
    world = _lone_world(tmp_path)
    report = _write_document(world, _considered_document([17]))
    assert report.state == "refused"
    assert "considered" in report.render()
    assert not (world.memory / HISTORY_PATH).exists()


def test_mixed_considered_members_are_named_and_refused(tmp_path: Path) -> None:
    world = _lone_world(tmp_path)
    report = _write_document(world, _considered_document(["FAM-CCCCC1", 17, "  "]))
    assert report.state == "refused"
    assert "considered" in report.render()
    assert not (world.memory / HISTORY_PATH).exists()


def test_bare_string_considered_is_refused(tmp_path: Path) -> None:
    world = _lone_world(tmp_path)
    report = _write_document(world, _considered_document("FAM-CCCCC1"))
    assert report.state == "refused"
    assert "considered" in report.render()
    assert not (world.memory / HISTORY_PATH).exists()


def test_considered_parses_strictly_at_read(tmp_path: Path) -> None:
    del tmp_path
    document, problems = read_handoff(_considered_document([17]))
    assert document.rows == ()
    assert any("considered" in problem.render() for problem in problems)
    document, problems = read_handoff(_considered_document([]))
    assert len(document.rows) == 1 and not problems


def test_writer_stores_foreign_evidence_in_the_record_row(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    first = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document={
                "entries": [_ruling_entry("e1", "mcp/tests/test_x.py::test_y")],
                "records": [
                    {
                        "key": "r1",
                        "kind": "decision",
                        "entry": "e1",
                        "slug": "row",
                        "fields": _decision_fields("A choice."),
                    }
                ],
            },
            commit=True,
        ),
        code=CodeSnapshot(root=world.code, tree="candidate", blobs={}),
    )
    assert first.state == "written"
    decision = first.records[0].id
    other = Owner(task=TASK_ID, kind="leaf", id="260928-MIK-L98")
    second = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=other,
            handoff_path="handoff.json",
            document={
                "entries": [_ruling_entry("e2", "mcp/tests/test_x.py::test_z")],
                "records": [
                    {
                        "key": "r2",
                        "kind": "decision",
                        "id": decision,
                        "entry": "e2",
                        "fields": {"context": "Another choice."},
                    }
                ],
                "history": [
                    {
                        "subject": decision,
                        "disposition": "changed",
                        "effect": "clarify",
                        "reason": "The alternatives were rewritten.",
                    }
                ],
            },
            commit=True,
        ),
        code=CodeSnapshot(root=world.code, tree="candidate", blobs={}),
    )
    assert second.state == "written"
    assert not [
        problem for problem in second.problems if "authored by another leaf" in problem.render()
    ]
    stored = HistoryFile.model_validate(
        json.loads(
            (world.memory / f"knowledge/history/{other.id}.json").read_text(encoding="utf-8")
        )
    )
    row = stored.row_about(decision)
    assert isinstance(row, RecordRow)
    assert "test_z" in row.reason


# F09 (round 1): foreign evidence survives later row rewrites --------------------


def _foreign_world(tmp_path: Path) -> tuple[Any, str]:
    world = build_world(tmp_path)
    first = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document={
                "entries": [_ruling_entry("e1", "mcp/tests/test_x.py::test_y")],
                "records": [
                    {
                        "key": "r1",
                        "kind": "decision",
                        "entry": "e1",
                        "slug": "row",
                        "fields": _decision_fields("A choice."),
                    }
                ],
            },
            commit=True,
        ),
        code=CodeSnapshot(root=world.code, tree="candidate", blobs={}),
    )
    assert first.state == "written"
    return world, first.records[0].id


def _foreign_update(  # noqa: PLR0913 -- one call carries a whole foreign update
    world: Any,
    decision: str,
    owner: Any,
    *,
    entry: str,
    evidence: str,
    reason: str,
    context: str,
) -> Any:
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=owner,
            handoff_path="handoff.json",
            document={
                "entries": [_ruling_entry(entry, evidence)],
                "records": [
                    {
                        "key": "r2",
                        "kind": "decision",
                        "id": decision,
                        "entry": entry,
                        "fields": {"context": context},
                    }
                ],
                "history": [
                    {
                        "subject": decision,
                        "disposition": "changed",
                        "effect": "clarify",
                        "reason": reason,
                    }
                ],
            },
            commit=True,
        ),
        code=CodeSnapshot(root=world.code, tree="candidate", blobs={}),
    )


def _foreign_row(world: Any, owner_id: str, decision: str) -> Any:
    stored = HistoryFile.model_validate(
        json.loads(
            (world.memory / f"knowledge/history/{owner_id}.json").read_text(encoding="utf-8")
        )
    )
    row = stored.row_about(decision)
    assert isinstance(row, RecordRow)
    return row


def test_second_foreign_evidence_keeps_the_first(tmp_path: Path) -> None:
    world, decision = _foreign_world(tmp_path)
    other = Owner(task=TASK_ID, kind="leaf", id="260928-MIK-L98")
    second = _foreign_update(
        world,
        decision,
        other,
        entry="e2",
        evidence="mcp/tests/test_x.py::test_a",
        reason="First.",
        context="Choice A.",
    )
    assert second.state == "written", second.render()
    third = _foreign_update(
        world,
        decision,
        other,
        entry="e3",
        evidence="mcp/tests/test_x.py::test_b",
        reason="Second.",
        context="Choice B.",
    )
    assert third.state == "written", third.render()
    row = _foreign_row(world, other.id, decision)
    assert "test_a" in row.reason and "test_b" in row.reason


def test_reason_only_rewrite_keeps_foreign_evidence(tmp_path: Path) -> None:
    world, decision = _foreign_world(tmp_path)
    other = Owner(task=TASK_ID, kind="leaf", id="260928-MIK-L98")
    second = _foreign_update(
        world,
        decision,
        other,
        entry="e2",
        evidence="mcp/tests/test_x.py::test_a",
        reason="First.",
        context="Choice A.",
    )
    assert second.state == "written", second.render()
    third = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=other,
            handoff_path="handoff.json",
            document={
                "history": [
                    {
                        "subject": decision,
                        "disposition": "changed",
                        "effect": "clarify",
                        "reason": "Third, no new evidence.",
                    }
                ],
            },
            commit=True,
        ),
        code=CodeSnapshot(root=world.code, tree="candidate", blobs={}),
    )
    assert third.state == "written", third.render()
    row = _foreign_row(world, other.id, decision)
    assert "test_a" in row.reason
    assert "Third, no new evidence." in row.reason
    paths = list((world.memory / "knowledge" / "decisions").glob(f"{decision}-*.json"))
    assert len(paths) == 1
    record = json.loads(paths[0].read_text(encoding="utf-8"))
    assert record["origin"]["task"] == TASK_ID and record["origin"]["leaf"] == LEAF_ID


# F11 (round 1): writer reports state their reason and refusing route -----------


def test_writer_reported_governs_states_reason_and_refusing_route(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    report = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document={
                "entries": [_ruling_entry("e1", "mcp/tests/test_x.py::test_y")],
                "records": [
                    {
                        "key": "r1",
                        "kind": "decision",
                        "entry": "e1",
                        "slug": "row",
                        "fields": _decision_fields("A choice."),
                    }
                ],
            },
            commit=True,
        ),
        code=CodeSnapshot(root=world.code, tree="candidate", blobs={}),
    )
    assert report.state == "written", report.render()
    (governs,) = [violation for violation in report.violations if violation.rule == "R13.3-governs"]
    assert governs.report_only
    assert "writer" in governs.message and "commit" in governs.message
    with pytest.raises(KnowledgeValidationError, match=r"R13\.3-governs"):
        require_valid_commit(
            knowledge_tree_from_directory(world.memory),
            bases=[],
            code=CodeDirectory("paired code", world.code),
        )


def test_writer_reported_rules_name_the_writer_and_the_refusing_route() -> None:
    rules = (
        "R04.1-carried-route-absent",
        "R04.4-unrealized-family",
        "R13.3-governs",
        "R22.6-carried-stale",
        "R27.2-existing-record",
    )
    validation = ValidationReport(
        candidate="candidate",
        violations=tuple(
            Violation(path="knowledge", field="", rule=rule, message=f"{rule} fires")
            for rule in rules
        ),
    )
    refusals, reported = _writer_split(validation)
    assert refusals == ()
    assert {one.rule for one in reported} == set(rules)
    for one in reported:
        assert one.report_only
        assert f"{one.rule} fires" in one.message
        assert "writer" in one.message and "commit" in one.message


# rule 9: the same checks everywhere --------------------------------------------------------------------


def test_subject_check_reports_an_unknown_membership_subject() -> None:
    files = dict(
        {
            "knowledge/layout.json": encode({"schema": "ar-memory-layout/v2", "conversion": "1"}),
            "knowledge/history/leaf.json": encode(
                {
                    "schema": "ar-history/v1",
                    "leaf": "leaf",
                    "closed": False,
                    "rows": [
                        {
                            "id": "ROW-AAAAAA",
                            "subject": membership_subject("INV-AAAAAA"),
                            "disposition": "no_family",
                            "reason": "No joint obligation is supported.",
                            "items": [],
                            "revision": 1,
                            "considered": [],
                        }
                    ],
                }
            ),
        }
    )
    candidate = tree(files)
    context = ValidationContext(candidate=candidate, parsed=parse_tree(candidate))
    findings = list(check_history_rows(context))
    assert any(membership_subject("INV-AAAAAA") in finding.message for finding in findings)


def test_subject_check_reports_an_unknown_record_subject() -> None:
    files = dict(
        {
            "knowledge/layout.json": encode({"schema": "ar-memory-layout/v2", "conversion": "1"}),
            "knowledge/history/leaf.json": encode(
                {
                    "schema": "ar-history/v1",
                    "leaf": "leaf",
                    "closed": False,
                    "rows": [
                        {
                            "id": "ROW-AAAAAA",
                            "subject": DECISION,
                            "disposition": "no_impact",
                            "reason": "The record is as it was.",
                            "items": [],
                            "revision": 1,
                        }
                    ],
                }
            ),
        }
    )
    candidate = tree(files)
    context = ValidationContext(candidate=candidate, parsed=parse_tree(candidate))
    findings = list(check_history_rows(context))
    assert any(DECISION in finding.message for finding in findings)


def test_both_item_kinds_register_facts_and_a_satisfying_row() -> None:
    for kind in (UNASSIGNED_INVARIANT_KIND, TOUCHED_RECORD_KIND):
        assert kind.name in ITEM_KINDS
        assert ITEM_KINDS[kind.name].facts
        assert ITEM_KINDS[kind.name].satisfying_row


# rule 6: the reader shows the row ----------------------------------------------------------------------


def _reader_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    assert result.returncode == 0, result.stderr.strip()
    return result.stdout.strip()


def _reader_world(tmp_path: Path) -> Any:
    code = tmp_path / "ws" / "demo"
    code.mkdir(parents=True)
    _reader_git(code, "init", "-q", "-b", "main")
    _reader_git(code, "config", "user.email", "fixture@example.invalid")
    _reader_git(code, "config", "user.name", "reader fixture")
    (code / "pkg").mkdir(parents=True)
    (code / "pkg" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _reader_git(code, "add", "-A")
    _reader_git(code, "commit", "-q", "-m", "code")
    code_commit = _reader_git(code, "rev-parse", "HEAD")
    memory = tmp_path / "coord" / "memory-repos" / "ar-demo"
    memory.mkdir(parents=True)
    _reader_git(memory, "init", "-q", "-b", "main")
    _reader_git(memory, "config", "user.email", "fixture@example.invalid")
    _reader_git(memory, "config", "user.name", "reader fixture")
    documents: dict[str, dict[str, Any]] = {
        "knowledge/layout.json": {"schema": "ar-memory-layout/v2", "conversion": "1"},
        f"knowledge/invariants/{INVARIANT}-lone.json": _invariant(INVARIANT),
        "knowledge/history/leaf.json": {
            "schema": "ar-history/v1",
            "leaf": "leaf",
            "closed": True,
            "rows": [
                {
                    "id": "ROW-AAAAAA",
                    "subject": membership_subject(INVARIANT),
                    "disposition": "no_family",
                    "reason": "No joint obligation is supported.",
                    "items": [],
                    "revision": 1,
                    "considered": [],
                }
            ],
        },
    }
    for path, document in documents.items():
        target = memory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(canonical_text(document), encoding="utf-8")
    _reader_git(memory, "add", "-A")
    _reader_git(memory, "commit", "-q", "-m", f"converted\n\nCode-Commit: {code_commit}")
    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path / "coord",
        workspace_root=tmp_path / "ws",
        transcript_root=tmp_path / "logs",
        repositories={"demo": RepositoryScope(repo_id="demo", path=code)},
    )
    return config, read_knowledge_reader, ReaderQuery


def test_reader_invariant_view_shows_the_membership_row(tmp_path: Path) -> None:
    config, read, query = _reader_world(tmp_path)
    view = read(config, query(view="record", repository_id="demo", record_id=INVARIANT))
    assert view["state"] == "view"
    membership = view["invariant"]["membership"]
    assert [row["subject"] for row in membership] == [membership_subject(INVARIANT)]


def test_reader_timeline_lists_membership_rows(tmp_path: Path) -> None:
    config, read, query = _reader_world(tmp_path)
    view = read(config, query(view="record", repository_id="demo", record_id=INVARIANT))
    assert view["state"] == "view"
    history_events = [
        event for event in view["timeline"]["events"] if event.get("source") == "history"
    ]
    assert any(
        event.get("disposition") == "no_family" and event.get("row") == "ROW-AAAAAA"
        for event in history_events
    )


def test_record_satisfying_row_states_retired_wins_at_any_revision() -> None:
    text = TOUCHED_RECORD_KIND.satisfying_row
    assert "retired exactly when the status became retired, at any revision" in text
    assert "not newly retired" in text
    assert text.index("retired exactly") < text.index("changed exactly")


def _attempt_history(
    leaf: str, *, closed: bool, attempt: int | None, rows: list[dict[str, Any]] | None = None
) -> bytes:
    document: dict[str, Any] = {
        "schema": "ar-history/v1",
        "leaf": leaf,
        "closed": closed,
        "rows": (
            rows
            if rows is not None
            else [
                {
                    "id": "ROW-RA15E0",
                    "subject": RAISE_SUBJECT,
                    "disposition": "raise",
                    "reason": "Ask the developer.",
                    "items": [],
                }
            ]
        ),
    }
    if attempt is not None:
        document["attempt"] = attempt
    return canonical_text(document).encode("utf-8")


def test_closed_attempt_raise_does_not_exempt_a_later_open_attempt() -> None:
    """F06 round 3: a closed own raise must not survive the merge into an open attempt.

    Attempt 1 (closed) holds the leaf's raise; attempt 2 (open) is unrelated. The merged
    per-owner aggregate carries the raise into the open attempt, but the exemption reads
    per-file flags, so the status-only mark change still raises.
    """

    base_files: dict[str, str | bytes] = {
        "knowledge/layout.json": canonical_text(LAYOUT),
        DEC_JSON: canonical_text(_decision(DECISION)),
        DEC_MD: "Decision prose.\n",
        f"knowledge/history/{LEAF_ID}.json": _attempt_history(LEAF_ID, closed=True, attempt=None),
        f"knowledge/history/{LEAF_ID}-attempt-2.json": _attempt_history(
            LEAF_ID, closed=False, attempt=2, rows=[]
        ),
    }
    candidate_files: dict[str, str | bytes] = dict(base_files)
    candidate_files[DEC_JSON] = _marked_json()
    base = KnowledgeSide.from_snapshot(
        "K_B",
        MemoryTreeSnapshot(key="b", source="directory", location="b", files=_encoded(base_files)),
    )
    candidate = KnowledgeSide.from_snapshot(
        "K_C",
        MemoryTreeSnapshot(
            key="c", source="directory", location="c", files=_encoded(candidate_files)
        ),
    )
    assert _subjects(touched_record_items(base, candidate, LEAF_ID)) == [DECISION]


def _lone_invariant_world(tmp_path: Path) -> Any:
    world = build_world(tmp_path)
    write(
        world.memory,
        {
            "knowledge/invariants/INV-CCCCCC-lone.json": canonical_text(
                _invariant(
                    "INV-CCCCCC",
                    admission={
                        "criteria": ["prevents_costly_mistake"],
                        "justification": "Guards the costly mistake of landing unpaired.",
                    },
                )
            ),
        },
    )
    return world


def _membership_document(subject: str) -> dict[str, Any]:
    return {
        "history": [
            {
                "subject": subject,
                "disposition": "no_family",
                "reason": "Stands alone.",
                "considered": [],
            }
        ]
    }


def test_handoff_invariant_alias_matches_literal_and_bare_membership(tmp_path: Path) -> None:
    """F07 round 3: handoff:<INV> no_family writes the same membership row as both other forms.

    The literal, bare-invariant and handoff-alias forms resolve to membership:<INV> before
    dispatch, so sequential writes keep one stable row ID with equivalent fields.
    """

    world = _lone_invariant_world(tmp_path)
    first = _write_document(world, _membership_document("membership:INV-CCCCCC"))
    assert first.state == "written", first.render()
    second = _write_document(world, _membership_document("INV-CCCCCC"))
    assert second.state == "written", second.render()
    third = _write_document(world, _membership_document("handoff:INV-CCCCCC"))
    assert third.state == "written", third.render()
    row = _stored_history(world).row_about("membership:INV-CCCCCC")
    assert isinstance(row, MembershipRow)
    assert row.id == first.rows[0].id == second.rows[0].id == third.rows[0].id
    assert row.disposition == "no_family"
    assert row.reason == "Stands alone."
    assert row.revision == 1
    assert list(row.considered) == []


def test_unknown_handoff_alias_with_no_family_still_refuses(tmp_path: Path) -> None:
    """F07 round 3: a handoff: no_family alias naming no stored invariant keeps the refusal."""

    world = _lone_invariant_world(tmp_path)
    report = _write_document(world, _membership_document("handoff:NOPE"))
    assert report.state == "refused"
    assert "handoff:NOPE" in report.render()


def test_reason_only_rewrite_keeps_evidence_ending_in_the_marker(tmp_path: Path) -> None:
    """F09 round 3: two foreign updates plus a reason-only rewrite keep every evidence value.

    The second evidence ends in the literal ``" Evidence ("`` marker, which the split form
    drops on the later rewrite; the scan form retains that suffix byte-exact, with the
    foreign origin unchanged.
    """

    world, decision = _foreign_world(tmp_path)
    other = Owner(task=TASK_ID, kind="leaf", id="260928-MIK-L98")
    second = _foreign_update(
        world,
        decision,
        other,
        entry="e2",
        evidence="mcp/tests/test_x.py::test_a",
        reason="First.",
        context="Choice A.",
    )
    assert second.state == "written", second.render()
    third = _foreign_update(
        world,
        decision,
        other,
        entry="e3",
        evidence="mcp/tests/test_x.py::test_b Evidence (",
        reason="Second.",
        context="Choice B.",
    )
    assert third.state == "written", third.render()
    row = _foreign_row(world, other.id, decision)
    assert "test_a" in row.reason and "test_b Evidence (" in row.reason
    fourth = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=other,
            handoff_path="handoff.json",
            document={
                "history": [
                    {
                        "subject": decision,
                        "disposition": "changed",
                        "effect": "clarify",
                        "reason": "Fourth, no new evidence.",
                    }
                ],
            },
            commit=True,
        ),
        code=CodeSnapshot(root=world.code, tree="candidate", blobs={}),
    )
    assert fourth.state == "written", fourth.render()
    kept = _foreign_row(world, other.id, decision)
    assert "Fourth, no new evidence." in kept.reason
    assert "test_a" in kept.reason and "test_b Evidence (" in kept.reason
    paths = list((world.memory / "knowledge" / "decisions").glob(f"{decision}-*.json"))
    assert len(paths) == 1
    record = json.loads(paths[0].read_text(encoding="utf-8"))
    assert record["origin"]["task"] == TASK_ID and record["origin"]["leaf"] == LEAF_ID


# F11 (round 3): each writer-reported rule fires on a real run and refuses commit ----------


def _five_document() -> dict[str, Any]:
    return {
        "history": [
            {
                "subject": f"onboarding:{CODE_FILE}",
                "disposition": "no_impact",
                "reason": "The card was read and needs no change.",
            }
        ]
    }


def _five_run(world: Any) -> Any:
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document=_five_document(),
            commit=True,
        ),
        code=CodeSnapshot.capture(world.code),
    )


def _five_reported(report: Any, rule: str) -> Any:
    matching = [violation for violation in report.violations if violation.rule == rule]
    assert len(matching) == 1, report.render()
    assert matching[0].report_only
    return matching[0]


def _five_refuses_at_commit(world: Any, rule: str) -> None:
    with pytest.raises(KnowledgeValidationError, match=rule.replace(".", r"\.")):
        require_valid_commit(
            knowledge_tree_from_directory(world.memory),
            bases=[knowledge_tree_from_git(world.memory, "HEAD")],
            code=CodeDirectory("paired code", world.code),
        )


def test_writer_governs_report_names_reason_and_commit_refusal(tmp_path: Path) -> None:
    """F11 round 3: R13.3-governs on a real run reports, then refuses the commit route."""

    world = build_world(tmp_path)
    report = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="handoff.json",
            document={
                "entries": [_ruling_entry("e1", "mcp/tests/test_x.py::test_y")],
                "records": [
                    {
                        "key": "r1",
                        "kind": "decision",
                        "entry": "e1",
                        "slug": "row",
                        "fields": _decision_fields("A choice."),
                    }
                ],
            },
            commit=True,
        ),
        code=CodeSnapshot.capture(world.code),
    )
    assert report.state == "written", report.render()
    governs = _five_reported(report, "R13.3-governs")
    assert "writer" in governs.message and "commit" in governs.message
    _five_refuses_at_commit(world, "R13.3-governs")


def test_writer_existing_record_report_names_reason_and_commit_refusal(
    tmp_path: Path,
) -> None:
    """F11 round 3: R27.2-existing-record on a real run reports, then refuses commit."""

    world = build_world(tmp_path)
    write(
        world.memory,
        {
            "knowledge/invariants/INV-EEEEEE-exist.json": canonical_text(
                _invariant(
                    "INV-EEEEEE",
                    admission={
                        "criteria": ["guarded_by_test"],
                        "justification": "Tests guard this claim in words.",
                    },
                )
            ),
        },
    )
    commit_all(world.memory, "existing guarded invariant")
    report = _five_run(world)
    assert report.state == "written", report.render()
    existing = _five_reported(report, "R27.2-existing-record")
    assert "writer" in existing.message and "commit" in existing.message
    _five_refuses_at_commit(world, "R27.2-existing-record")


def test_writer_unrealized_family_report_names_reason_and_commit_refusal(
    tmp_path: Path,
) -> None:
    """F11 round 3: R04.4-unrealized-family on a real run reports, then refuses commit."""

    world = build_world(tmp_path)
    write(
        world.memory,
        {
            "knowledge/invariants/INV-NEWFAM-lone.json": canonical_text(
                _invariant(
                    "INV-NEWFAM",
                    admission={
                        "criteria": ["prevents_costly_mistake"],
                        "justification": "An unrealized family member waits for its entry.",
                    },
                )
            ),
            "knowledge/families/FAM-NEWFAM-new.json": canonical_text(
                {
                    **_family("FAM-NEWFAM", ["INV-NEWFAM"]),
                    "routes": ["pkg"],
                }
            ),
        },
    )
    commit_all(world.memory, "unrealized family")
    report = _five_run(world)
    assert report.state == "written", report.render()
    unrealized = _five_reported(report, "R04.4-unrealized-family")
    assert "writer" in unrealized.message and "commit" in unrealized.message
    _five_refuses_at_commit(world, "R04.4-unrealized-family")


def test_writer_carried_route_absent_report_names_reason_and_commit_refusal(
    tmp_path: Path,
) -> None:
    """F11 round 3: R04.1-carried-route-absent on a real run reports, then refuses commit."""

    world = build_world(tmp_path)
    write(
        world.memory,
        {
            "knowledge/families/FAM-ABSENT-routes.json": canonical_text(
                {
                    **_family("FAM-ABSENT", [BASE_INVARIANT]),
                    "routes": ["pkg", "ghost-dir"],
                }
            ),
        },
    )
    commit_all(world.memory, "family with an absent carried route")
    report = _five_run(world)
    assert report.state == "written", report.render()
    absent = _five_reported(report, "R04.1-carried-route-absent")
    assert "writer" in absent.message and "commit" in absent.message
    _five_refuses_at_commit(world, "R04.1-carried-route-absent")


def test_writer_carried_stale_report_names_reason_and_commit_refusal(
    tmp_path: Path,
) -> None:
    """F11 round 3: R22.6-carried-stale on a real run reports, then refuses commit."""

    world = build_world(tmp_path)
    anchor = {
        "locator": {"kind": "file"},
        "blob": "c" * 40,
        "content": "sha256:" + "2" * 64,
    }
    write(
        world.memory,
        {
            "onboarding/pkg/stale.py.md": "# pkg/stale.py\n",
            "onboarding/pkg/stale.py.json": canonical_text(
                {
                    "schema": "ar-onboarding-file/v1",
                    "path": "pkg/stale.py",
                    "references": {},
                    "realizes": [
                        {
                            "id": "RLZ-SEED90",
                            "invariant": BASE_INVARIANT,
                            "anchor": dict(anchor),
                            "role": "support",
                            "rationale": "Stale points at a removed file.",
                        }
                    ],
                }
            ),
        },
    )
    commit_all(world.memory, "sidecar with a stale carried anchor")
    report = _five_run(world)
    assert report.state == "written", report.render()
    stale = _five_reported(report, "R22.6-carried-stale")
    assert "writer" in stale.message and "commit" in stale.message
    _five_refuses_at_commit(world, "R22.6-carried-stale")


def test_standalone_conversion_names_conversion_not_the_merge(tmp_path: Path) -> None:
    """F11 round 3: a standalone conversion report names conversion and the next refusal."""

    world = build_world(tmp_path)
    candidate = knowledge_tree_from_directory(world.memory)
    sync_context = ValidationContext(candidate=candidate, parsed=parse_tree(candidate))
    assert "sync merge" in merge_report_note(sync_context)
    conversion_context = ValidationContext(
        candidate=candidate, parsed=parse_tree(candidate), conversion=True
    )
    note = merge_report_note(conversion_context)
    assert "standalone conversion" in note
    assert "sync merge" not in note
    assert "publication or landing" in note
