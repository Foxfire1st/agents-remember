"""State-conversion-only diffs count as untouched in the knowledge worklist."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from agents_remember.application.knowledge_gate.predicates import GateContext, record_row_open
from agents_remember.application.knowledge_worklist import ExplicitSides, worklist_for_sides
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.touched_records import touched_record_items
from agents_remember.memory.conversion.state_conversion import (
    plan_state_conversion,
    state_conversion_only,
)
from agents_remember.memory.conversion.task_review_facts import TaskOwners
from agents_remember.memory.knowledge_index import MemoryTreeSnapshot
from agents_remember.memory.knowledge_index.tree import directory_snapshot, git_tree_snapshot
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.converted_reviews import CONVERTED_REVIEWS_PATH
from agents_remember.models.knowledge_files.history import HistoryFile, RecordRow
from agents_remember.models.knowledge_files.history_product import record_content_digest
from test_knowledge_worklist import (
    OTHER,
    OTHER_V1,
    REVIEW,
    REVIEW_V1,
    TESTS,
    TESTS_V1,
    World,
    _init,
)

AT = "2026-11-26T10:00:00+00:00"
TASK = "260101-MAS"
L1 = "260101-MAS-L1"
L2 = "260101-MAS-L2"
LEAF = "260101-MAS-L9"
ORIGIN = {"task": TASK, "leaf": L1}
ADMISSION = {"criteria": ["real_alternatives"], "justification": "Weighed and chosen."}
LAYOUT = {"schema": "ar-memory-layout/v2", "conversion": "1"}

INV_A = "INV-AAAAAA"
INV_B = "INV-BBBBBB"
INV_R = "INV-RRRRRR"
FAM_1 = "FAM-F00001"
ASM_1 = "ASM-100K0R"
DEC_1 = "DEC-D18TXT"
DPD_1 = "DPD-100D0C"

INV_A_PATH = f"knowledge/invariants/{INV_A}-alpha.json"
INV_B_PATH = f"knowledge/invariants/{INV_B}-beta.json"
INV_R_PATH = f"knowledge/invariants/{INV_R}-retired.json"
FAM_1_PATH = f"knowledge/families/{FAM_1}-core.json"
ASM_1_PATH = f"knowledge/assumptions/{ASM_1}-under-100k.json"
ASM_1_MD = f"knowledge/assumptions/{ASM_1}-under-100k.md"
DEC_1_PATH = f"knowledge/decisions/{DEC_1}-text-files.json"
DPD_1_PATH = f"knowledge/dependents/{DPD_1}-guide.json"

_UNKNOWN_FIELDS = ("role", "harness", "model", "effort", "seat")
CONVERSION_STAMP: dict[str, Any] = {
    "by": "conversion",
    "operation": "state-conversion",
    **dict.fromkeys(_UNKNOWN_FIELDS, "unknown"),
    "task": TASK,
    "leaf": L1,
    "at": AT,
    "unknown": dict.fromkeys(_UNKNOWN_FIELDS, "not recorded before writers were stamped"),
}

AGENT_STAMP: dict[str, Any] = {
    "by": "agent",
    "role": "curator",
    "harness": "codex",
    "model": "gpt-5",
    "effort": "high",
    "seat": "seat-1",
    "task": TASK,
    "leaf": L2,
    "at": "2026-11-20T09:00:00+00:00",
}

PRODUCT_STAMP: dict[str, Any] = {
    "by": "product",
    "operation": "crossing-sync",
    **dict.fromkeys(_UNKNOWN_FIELDS, "unknown"),
    "task": TASK,
    "crossing": f"{TASK}-crossing-1",
    "at": "2026-11-21T09:00:00+00:00",
    "unknown": dict.fromkeys(_UNKNOWN_FIELDS, "combined by product"),
}


def _invariant(
    record_id: str,
    *,
    revision: int = 1,
    status: str | None = "proposed",
    statement: str = "The rule holds.",
    writer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema": "ar-invariant/v1",
        "id": record_id,
        "revision": revision,
        "statement": statement,
        "applicability": "Always.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
        "origin": ORIGIN,
    }
    if status is not None:
        doc["status"] = status
    if writer is not None:
        doc["writer"] = writer
    return doc


def _family(
    record_id: str,
    members: list[str],
    *,
    status: str | None = "proposed",
    guarantee: str = "The members hold together.",
    writer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema": "ar-family/v1",
        "id": record_id,
        "revision": 1,
        "title": "Core family",
        "guarantee": guarantee,
        "members": members,
        "routes": ["pkg"],
        "admission": "legacy-unassessed",
        "origin": ORIGIN,
    }
    if status is not None:
        doc["status"] = status
    if writer is not None:
        doc["writer"] = writer
    return doc


def _assumption(
    record_id: str = ASM_1,
    *,
    status: str | None = "accepted",
    proposition: str = "Knowledge stays small.",
    writer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema": "ar-assumption/v1",
        "id": record_id,
        "revision": 1,
        "proposition": proposition,
        "basis": "Observed.",
        "links": [],
        "origin": ORIGIN,
    }
    if status is not None:
        doc["status"] = status
    if writer is not None:
        doc["writer"] = writer
    return doc


def _decision(
    record_id: str = DEC_1,
    *,
    status: str | None = "active",
    writer: dict[str, Any] | None = None,
    reconsider_targets: tuple[str, ...] = (INV_A, FAM_1, ASM_1),
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema": "ar-decision/v1",
        "id": record_id,
        "revision": 1,
        "context": "A choice binds later work.",
        "alternatives": [
            {"option": "Text files", "status": "chosen", "reason": "They diff cleanly."},
            {
                "option": "Database",
                "status": "rejected",
                "reason": "Harder to diff.",
                "reconsider_when": "A linked truth changes.",
            },
        ],
        "consequences": ["Files stay canonical."],
        "decider": "developer",
        "supersedes": [],
        "links": [
            {"relation": "reconsider_on", "target": target, "alternative": 1}
            for target in reconsider_targets
        ],
        "admission": ADMISSION,
        "origin": ORIGIN,
    }
    if status is not None:
        doc["status"] = status
    if writer is not None:
        doc["writer"] = writer
    return doc


def _dependent(
    code_commit: str,
    *,
    revision: int = 1,
    writer: dict[str, Any] | None = None,
    deferrals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema": "ar-dependent/v1",
        "id": DPD_1,
        "revision": revision,
        "document": REVIEW,
        "stands_on": [{"kind": "file", "path": REVIEW, "blob": "a" * 40}],
        "confirmed": {"task": L1, "role": "curator", "commit": code_commit, "statement": "Ok."},
        "origin": ORIGIN,
    }
    if writer is not None:
        doc["writer"] = writer
    if deferrals is not None:
        doc["deferrals"] = deferrals
    return doc


def _side(files: dict[str, bytes], label: str) -> KnowledgeSide:
    return KnowledgeSide.from_snapshot(
        label,
        MemoryTreeSnapshot(key=label, source="directory", location=label, files=files),
    )


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    return canonical_text(document).encode("utf-8")


def _history_l2(inv_b_digest: str) -> dict[str, Any]:
    return {
        "schema": "ar-history/v1",
        "leaf": L2,
        "closed": True,
        "written": [
            {"record": INV_B, "revision": 2, "writer": AGENT_STAMP, "contentDigest": inv_b_digest}
        ],
        "rows": [
            {
                "id": "ROW-B00001",
                "subject": INV_B,
                "disposition": "changed",
                "reason": "Clarified beta.",
                "items": [],
                "covers": [],
                "revision": 2,
                "effect": "clarify",
            }
        ],
    }


def _build_world(
    tmp_path: Path, *, with_asm_md: str | None = "Assumption prose.\n"
) -> tuple[World, dict[str, bytes]]:
    world = World(root=tmp_path, code=tmp_path / "code", memory=tmp_path / "memory")
    _init(world.code)
    world.code_base = world.code_commit({REVIEW: REVIEW_V1, OTHER: OTHER_V1, TESTS: TESTS_V1})
    _init(world.memory)
    inv_b = _invariant(INV_B, revision=2, status="accepted", statement="Beta holds.")
    entries = {
        "RLZ-A00001": (INV_A, REVIEW, {"kind": "symbol", "name": "_not_listed"}, "realizes"),
        "RLZ-B00001": (INV_B, OTHER, {"kind": "symbol", "name": "helper"}, "realizes"),
    }
    base_files: dict[str, str | bytes | None] = {
        "knowledge/layout.json": canonical_text(LAYOUT),
        INV_A_PATH: canonical_text(_invariant(INV_A, status="proposed", statement="Alpha holds.")),
        INV_B_PATH: canonical_text(inv_b),
        INV_R_PATH: canonical_text(_invariant(INV_R, status="retired", statement="Retired rule.")),
        FAM_1_PATH: canonical_text(_family(FAM_1, [INV_A, INV_B], status="proposed")),
        ASM_1_PATH: canonical_text(_assumption(ASM_1, status="accepted")),
        DEC_1_PATH: canonical_text(_decision(DEC_1, status="active")),
        DPD_1_PATH: canonical_text(_dependent(world.code_base)),
        f"knowledge/history/{L1}.json": canonical_text(
            {"schema": "ar-history/v1", "leaf": L1, "closed": True, "rows": []}
        ),
        f"knowledge/history/{L2}.json": canonical_text(_history_l2(record_content_digest(inv_b))),
        **world.sidecars(world.code_base, entries),
    }
    if with_asm_md is not None:
        base_files[ASM_1_MD] = with_asm_md
    world.memory_base = world.memory_commit(base_files, world.code_base)
    json_files = {
        path: content.encode("utf-8")
        for path, content in base_files.items()
        if isinstance(content, str) and path.startswith("knowledge/") and path.endswith(".json")
    }
    return world, json_files


def _apply_plan(world: World, changes: dict[str, bytes]) -> None:
    for rel, data in changes.items():
        target = world.memory / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _run_worklist(world: World, *, owner: str = LEAF) -> dict[str, Any]:
    document = worklist_for_sides(
        ExplicitSides(
            code_repository=world.code,
            base=world.code_base,
            memory_repository=world.memory,
            memory_base=world.memory_base,
            memory_candidate=world.memory,
            code_candidate=world.code_base,
            owner=owner,
        )
    )
    assert document is not None
    assert document["state"] == "complete"
    return document


def test_plan_changes_satisfy_predicate_and_raise_no_worklist_items(tmp_path: Path) -> None:
    world, json_files = _build_world(tmp_path)
    plan = plan_state_conversion(json_files, TaskOwners(tasks={L1: TASK, L2: TASK}), at=AT)

    record_paths = {path for path in plan.changes if path != CONVERTED_REVIEWS_PATH}
    assert record_paths == {
        INV_A_PATH,
        INV_B_PATH,
        INV_R_PATH,
        FAM_1_PATH,
        ASM_1_PATH,
        DEC_1_PATH,
        DPD_1_PATH,
    }
    stamp_sources: set[str] = set()
    for path in sorted(record_paths):
        original = plan.originals[path]
        assert original is not None
        assert state_conversion_only(original, plan.changes[path]), path
        stamp_sources.add(json.loads(plan.changes[path])["writer"]["by"])
    assert stamp_sources == {"conversion", "agent"}

    _apply_plan(world, plan.changes)
    document = _run_worklist(world)
    assert document["items"] == []
    assert document["scope"]["reachedFamilies"] == []
    assert document["scope"]["classifiedEntries"] == 0


@pytest.mark.parametrize("stamp", [CONVERSION_STAMP, AGENT_STAMP, PRODUCT_STAMP])
def test_predicate_accepts_every_valid_writer_stamp_source(stamp: dict[str, Any]) -> None:
    base = _canonical_bytes(_invariant(INV_A, status="proposed"))
    candidate = _canonical_bytes(_invariant(INV_A, status=None, writer=stamp))
    assert state_conversion_only(base, candidate)


def test_predicate_handles_status_only_removal_and_rejects_formatting_or_bad_stamps() -> None:
    stamped_base = _canonical_bytes(_invariant(INV_A, status="accepted", writer=AGENT_STAMP))
    status_removed = _canonical_bytes(_invariant(INV_A, status=None, writer=AGENT_STAMP))
    assert state_conversion_only(stamped_base, status_removed)
    assert not state_conversion_only(stamped_base, stamped_base)

    base_doc = _invariant(INV_A, status="proposed")
    base_raw = _canonical_bytes(base_doc)
    converted_doc = _invariant(INV_A, status=None, writer=CONVERSION_STAMP)
    converted_raw = _canonical_bytes(converted_doc)

    # Non-canonical base or candidate is never hidden.
    non_canonical_base = json.dumps(base_doc, indent=4).encode("utf-8") + b"\n"
    non_canonical_candidate = json.dumps(converted_doc, indent=4).encode("utf-8") + b"\n"
    assert not state_conversion_only(non_canonical_base, converted_raw)
    assert not state_conversion_only(base_raw, non_canonical_candidate)

    # Partial conversion (missing stamp or lingering legacy status) is rejected.
    assert not state_conversion_only(base_raw, _canonical_bytes(_invariant(INV_A, status=None)))
    assert not state_conversion_only(
        base_raw, _canonical_bytes(_invariant(INV_A, status="proposed", writer=CONVERSION_STAMP))
    )

    # Invalid writer stamps fail validation.
    invalid_stamps = [
        {**AGENT_STAMP, "operation": "state-conversion"},
        {**CONVERSION_STAMP, "unknown": {}},
        {**CONVERSION_STAMP, "extra": "nope"},
        "not-a-dict",
    ]
    for bad in invalid_stamps:
        bad_doc = {**converted_doc, "writer": bad}
        assert not state_conversion_only(base_raw, _canonical_bytes(bad_doc))


def test_rule_3_non_legacy_status_writer_change_and_field_edit_still_raise(tmp_path: Path) -> None:
    world, json_files = _build_world(tmp_path)
    # Add an already-stamped invariant to the base so we can test changing/removing its writer.
    inv_s = "INV-SSSSSS"
    inv_s_path = f"knowledge/invariants/{inv_s}-stamped.json"
    base_stamped = _invariant(inv_s, status="proposed", writer=AGENT_STAMP)
    world.memory_base = world.memory_commit(
        {inv_s_path: canonical_text(base_stamped)}, world.code_base
    )
    json_files[inv_s_path] = _canonical_bytes(base_stamped)
    plan = plan_state_conversion(json_files, TaskOwners(tasks={L1: TASK, L2: TASK}), at=AT)
    _apply_plan(world, plan.changes)

    # 1. Non-legacy status removed (retired invariant) or changed (active decision).
    retired_doc = json.loads((world.memory / INV_R_PATH).read_bytes())
    del retired_doc["status"]
    (world.memory / INV_R_PATH).write_bytes(_canonical_bytes(retired_doc))
    assert not state_conversion_only(
        json_files[INV_R_PATH], (world.memory / INV_R_PATH).read_bytes()
    )

    dec_doc = json.loads((world.memory / DEC_1_PATH).read_bytes())
    dec_doc["status"] = "under_reconsideration"
    (world.memory / DEC_1_PATH).write_bytes(_canonical_bytes(dec_doc))
    assert not state_conversion_only(
        json_files[DEC_1_PATH], (world.memory / DEC_1_PATH).read_bytes()
    )

    # 2. Existing writer changed (and also check removal at predicate level).
    stamped_doc = json.loads((world.memory / inv_s_path).read_bytes())
    without_writer = {k: v for k, v in stamped_doc.items() if k != "writer"}
    assert not state_conversion_only(json_files[inv_s_path], _canonical_bytes(without_writer))
    stamped_doc["writer"] = CONVERSION_STAMP
    (world.memory / inv_s_path).write_bytes(_canonical_bytes(stamped_doc))
    assert not state_conversion_only(
        json_files[inv_s_path], (world.memory / inv_s_path).read_bytes()
    )

    # 3. Any other field changed alongside conversion (invariant, family, assumption).
    inv_a_doc = json.loads((world.memory / INV_A_PATH).read_bytes())
    inv_a_doc["statement"] = "Alpha changed."
    (world.memory / INV_A_PATH).write_bytes(_canonical_bytes(inv_a_doc))

    fam_doc = json.loads((world.memory / FAM_1_PATH).read_bytes())
    fam_doc["guarantee"] = "Core family guarantee changed."
    (world.memory / FAM_1_PATH).write_bytes(_canonical_bytes(fam_doc))

    asm_doc = json.loads((world.memory / ASM_1_PATH).read_bytes())
    asm_doc["proposition"] = "Knowledge grew."
    (world.memory / ASM_1_PATH).write_bytes(_canonical_bytes(asm_doc))

    document = _run_worklist(world)
    raised = {(item["kind"], item["subject"]) for item in document["items"]}
    assert ("touched_invariant", INV_R) in raised
    assert ("touched_invariant", inv_s) in raised
    assert ("touched_invariant", INV_A) in raised
    assert ("reached_family", FAM_1) in raised
    assert ("touched_record", DEC_1) in raised
    assert ("touched_record", ASM_1) in raised
    assert ("reconsideration_candidate", f"reconsider:{DEC_1}#1") in raised

    # Record absent on either side is not covered by the predicate.
    base_side = KnowledgeSide.from_snapshot(
        "K_B", git_tree_snapshot(world.memory, world.memory_base)
    )
    cand_side = KnowledgeSide.from_snapshot("K_C", directory_snapshot(world.memory))
    (world.memory / INV_B_PATH).unlink()
    cand_missing = KnowledgeSide.from_snapshot("K_C2", directory_snapshot(world.memory))
    assert base_side.record_file_changed(cand_missing, INV_B)
    assert cand_missing.record_file_changed(base_side, INV_B)
    assert not base_side.record_file_changed(cand_side, INV_B)


def test_moved_and_converted_records_still_raise(tmp_path: Path) -> None:
    world, json_files = _build_world(tmp_path)
    plan = plan_state_conversion(json_files, TaskOwners(tasks={L1: TASK, L2: TASK}), at=AT)
    _apply_plan(world, plan.changes)

    # Move + convert an invariant, a family, and an assumption targeted by reconsider_on.
    (world.memory / INV_A_PATH).rename(world.memory / f"knowledge/invariants/{INV_A}-renamed.json")
    (world.memory / FAM_1_PATH).rename(world.memory / f"knowledge/families/{FAM_1}-renamed.json")
    (world.memory / ASM_1_PATH).rename(world.memory / f"knowledge/assumptions/{ASM_1}-renamed.json")
    (world.memory / ASM_1_MD).rename(world.memory / f"knowledge/assumptions/{ASM_1}-renamed.md")

    document = _run_worklist(world)
    raised = {(item["kind"], item["subject"]) for item in document["items"]}
    assert ("touched_invariant", INV_A) in raised
    assert ("reached_family", FAM_1) in raised
    assert ("reconsideration_candidate", f"reconsider:{DEC_1}#1") in raised


@pytest.mark.parametrize(
    ("base_md", "candidate_md"),
    [
        pytest.param("Initial prose.\n", "Edited prose.\n", id="edited-sidecar"),
        pytest.param("Initial prose.\n", None, id="deleted-sidecar"),
        pytest.param(None, "Added prose.\n", id="added-sidecar"),
    ],
)
def test_changed_markdown_sidecar_still_raises_on_converted_record(
    tmp_path: Path, base_md: str | None, candidate_md: str | None
) -> None:
    world, json_files = _build_world(tmp_path, with_asm_md=base_md)
    plan = plan_state_conversion(json_files, TaskOwners(tasks={L1: TASK, L2: TASK}), at=AT)
    _apply_plan(world, plan.changes)

    sidecar = world.memory / ASM_1_MD
    if candidate_md is None:
        sidecar.unlink()
    else:
        sidecar.write_text(candidate_md, encoding="utf-8")

    document = _run_worklist(world)
    assert [(item["kind"], item["subject"]) for item in document["items"]] == [
        ("touched_record", ASM_1)
    ]


def _history_for(row: RecordRow) -> HistoryFile:
    return HistoryFile.model_validate(
        {"schema": "ar-history/v1", "leaf": LEAF, "closed": False, "rows": [row.to_document()]}
    )


def test_raise_mark_only_and_deferral_answering_rule_are_unchanged() -> None:
    base_files = {
        "knowledge/layout.json": _canonical_bytes(LAYOUT),
        DEC_1_PATH: _canonical_bytes(_decision(DEC_1, status="active")),
        DPD_1_PATH: _canonical_bytes(_dependent("1" * 40, revision=1)),
    }
    deferral = {
        "id": "DFR-ABC123",
        "passage": "Section 1",
        "reason": "Waiting on follow-up leaf.",
        "condition": {"task": LEAF},
        "owner": TASK,
        "createdAt": AT,
        "createdByLeaf": L1,
    }
    raise_row = {
        "id": "ROW-RA15E0",
        "subject": f"reconsider:{DEC_1}#1",
        "disposition": "raise",
        "reason": "Ask the developer.",
        "items": [],
    }
    cand_files = {
        **base_files,
        DEC_1_PATH: _canonical_bytes(_decision(DEC_1, status="under_reconsideration")),
        DPD_1_PATH: _canonical_bytes(
            _dependent("1" * 40, revision=2, writer=AGENT_STAMP, deferrals=[deferral])
        ),
        f"knowledge/history/{LEAF}.json": _canonical_bytes(
            {"schema": "ar-history/v1", "leaf": LEAF, "closed": False, "rows": [raise_row]}
        ),
    }
    base, candidate = _side(base_files, "base"), _side(cand_files, "candidate")

    # Own open raise exempts DEC_1; without owner (or foreign owner) it still raises.
    items_for_owner = touched_record_items(base, candidate, owner=LEAF)
    assert [item["subject"] for item in items_for_owner] == [DPD_1]
    assert items_for_owner[0]["facts"].get("deferralOnly") is True
    assert [item["subject"] for item in touched_record_items(base, candidate, owner=None)] == [
        DEC_1,
        DPD_1,
    ]

    # Deferral answering rule in record_row_open stays intact.
    dep_item = items_for_owner[0]
    for disp in ("deferred", "re_deferred", "deferral_lifted"):
        row = RecordRow(
            id="ROW-DFR001",
            subject=DPD_1,
            disposition=disp,
            reason="Deferred.",
            items=(),
            revision=2,
            deferralIds=("DFR-ABC123",),
        )
        assert (
            record_row_open(
                dep_item, GateContext(LEAF, _history_for(row), candidate, cast(Any, None))
            )
            is None
        )
    wrong = RecordRow(
        id="ROW-CHG001",
        subject=DPD_1,
        disposition="changed",
        reason="Wrong disposition.",
        items=(),
        revision=2,
        effect="clarify",
    )
    assert (
        record_row_open(
            dep_item, GateContext(LEAF, _history_for(wrong), candidate, cast(Any, None))
        )
        is not None
    )
