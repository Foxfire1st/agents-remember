"""Family row writer alignment with the knowledge gate for empty families and absent members."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest
from agents_remember.application.knowledge_currentness.observe import open_code_tree
from agents_remember.application.knowledge_gate import GateContext, GateResult, evaluate_leaf_gate
from agents_remember.application.knowledge_gate.predicates import family_row_open
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.leaf import CandidateTrees
from agents_remember.application.knowledge_writer import Owner, WriteRequest
from agents_remember.application.knowledge_writer import write_knowledge as native_write_knowledge
from agents_remember.memory_quality.knowledge_validator import knowledge_tree_from_directory
from agents_remember.models.knowledge_files import canonical_text, derived_record_id
from agents_remember.models.knowledge_files.history import HistoryFile
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from agents_remember.worktrees.worktree_contract import load_contract
from knowledge_writer_test_support import (
    BASE_FAMILY,
    BASE_INVARIANT,
    LEAF_ID,
    TASK_ID,
    World,
    build_world,
    commit_all,
    git,
    read_json,
    tree_bytes,
    write,
)
from writer_worklist_test_support import prepare_worklist

FAMILY_PATH = f"knowledge/families/{BASE_FAMILY}-landing.json"
INVARIANT_PATH = f"knowledge/invariants/{BASE_INVARIANT}-landing-pair.json"
SIDECAR_PATH = "onboarding/pkg/landing.py.json"
HISTORY_PATH = f"knowledge/history/{LEAF_ID}.json"
OWNER = Owner(task=TASK_ID, kind="leaf", id=LEAF_ID)
WAVE_OWNER = Owner(task=TASK_ID, kind="wave", id="W1")
SURVIVOR_INVARIANT = derived_record_id("invariant", "legacy-invariant-survivor")
SURVIVOR_PATH = f"knowledge/invariants/{SURVIVOR_INVARIANT}-survivor.json"
EMPTY_REFUSAL = "a family row names every member the curator examined"


def _set_family(world: World, **updates: Any) -> dict[str, Any]:
    family = read_json(world.memory, FAMILY_PATH)
    family.update(updates)
    write(world.memory, {FAMILY_PATH: canonical_text(family)})
    return family


def _update_contract_memory_base(world: World, commit: str) -> None:
    text = world.contract.read_text(encoding="utf-8")
    lines = text.splitlines()
    in_memory = False
    for index, line in enumerate(lines):
        if line == "memory:":
            in_memory = True
        elif in_memory and line.startswith("  base_commit:"):
            lines[index] = f"  base_commit: {commit}"
            break
    world.contract.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _empty_family_world(tmp_path: Path, *, initial_status: str = "accepted") -> World:
    world = build_world(tmp_path)
    _set_family(world, members=[], status=initial_status)
    head = commit_all(world.memory, "empty family at base")
    _update_contract_memory_base(world, head)
    return world


def _reached_family_item(worklist: Mapping[str, Any] | None, family_id: str) -> dict[str, Any]:
    assert worklist is not None
    items = [
        item
        for item in worklist.get("items", [])
        if item.get("kind") == "reached_family" and item.get("subject") == family_id
    ]
    assert len(items) == 1, worklist.get("items")
    return items[0]


def _gate_context(world: World) -> GateContext:
    history_doc = (
        read_json(world.memory, HISTORY_PATH) if (world.memory / HISTORY_PATH).exists() else None
    )
    history = None if history_doc is None else HistoryFile.model_validate(history_doc)
    side = KnowledgeSide.from_tree(
        "candidate", "candidate", knowledge_tree_from_directory(world.memory)
    )
    return GateContext(LEAF_ID, history, side, open_code_tree(None))


def _evaluate_gate(world: World, *, parent_tip: str) -> GateResult:
    contract = load_contract(world.contract)
    with TemporaryDirectory() as scratch:
        candidate = CandidateTrees(
            code=worktree_candidate_tree(world.code, Path(scratch) / "code" / "index"),
            memory=worktree_candidate_tree(world.memory, Path(scratch) / "memory" / "index"),
        )
    result = evaluate_leaf_gate(
        contract,
        candidate,
        parent_memory_tip=parent_tip,
        persist_worklist=False,
    )
    assert result is not None
    return result


@pytest.mark.parametrize("family_status", ["active", "retired"])
@pytest.mark.parametrize("examined_field", ["empty_list", "omitted"])
def test_empty_family_admits_empty_or_omitted_examined_and_answers_gate(
    tmp_path: Path, family_status: str, examined_field: str
) -> None:
    initial_status = "accepted" if family_status == "active" else "retired"
    world = _empty_family_world(tmp_path, initial_status=initial_status)
    base_tip = git(world.memory, "rev-parse", "HEAD")

    # An empty family is reached only when its record changes between K_B and K_C.
    _set_family(world, title="Landing (updated)", revision=2)

    row_input: dict[str, Any] = {
        "subject": BASE_FAMILY,
        "disposition": "no_impact" if family_status == "active" else "retired",
        "reason": "The empty family has no members on either side.",
    }
    if examined_field == "empty_list":
        row_input["examined"] = []

    prepared = prepare_worklist(
        WriteRequest(
            world.memory,
            world.code,
            OWNER,
            "notes/handoff.json",
            {"history": [row_input]},
            commit=True,
        ),
        contract=world.contract,
    )
    reached = _reached_family_item(prepared.worklist, BASE_FAMILY)
    assert reached["facts"]["members"] == []

    # Before the write, the gate holds the reached_family item open.
    before_gate = _evaluate_gate(world, parent_tip=base_tip)
    assert any(
        finding.code == "knowledge-item-open" and BASE_FAMILY in finding.message
        for finding in before_gate.findings
    )

    written = native_write_knowledge(prepared)
    assert written.state == "written", written.render()

    (stored_row,) = read_json(world.memory, HISTORY_PATH)["rows"]
    assert stored_row["subject"] == BASE_FAMILY
    assert stored_row["examined"] == []
    assert stored_row["revision"] == 2
    assert stored_row["items"] == [reached["id"]]

    # After the write, both the direct predicate and the full leaf gate accept the row.
    assert family_row_open(reached, _gate_context(world)) is None
    after_gate = _evaluate_gate(world, parent_tip=base_tip)
    open_items = [f for f in after_gate.findings if f.code == "knowledge-item-open"]
    assert open_items == [], open_items


@pytest.mark.parametrize("examined_field", ["empty_list", "omitted"])
def test_empty_examined_refused_when_family_has_members_at_either_side(
    tmp_path: Path, examined_field: str
) -> None:
    def _row() -> dict[str, Any]:
        entry: dict[str, Any] = {
            "subject": BASE_FAMILY,
            "disposition": "no_impact",
            "reason": "Attempted empty examined list.",
        }
        if examined_field == "empty_list":
            entry["examined"] = []
        return entry

    # Case 1: member at K_B only (removed from K_C family record, committed at leaf HEAD
    # so state.base_record also shows members=[], proving worklist facts govern).
    world_kb = build_world(tmp_path / "kb_only")
    k_b = git(world_kb.memory, "rev-parse", "HEAD")
    _set_family(world_kb, members=[], revision=2)
    commit_all(world_kb.memory, "drop member from family on leaf branch")
    before_kb = tree_bytes(world_kb.memory)
    prepared_kb = prepare_worklist(
        WriteRequest(
            world_kb.memory,
            world_kb.code,
            OWNER,
            "notes/handoff.json",
            {"history": [_row()]},
            commit=True,
            memory_base=k_b,
        ),
        contract=world_kb.contract,
    )
    assert len(_reached_family_item(prepared_kb.worklist, BASE_FAMILY)["facts"]["members"]) == 1
    refused_kb = native_write_knowledge(prepared_kb)
    assert refused_kb.state == "refused" and EMPTY_REFUSAL in refused_kb.render()
    assert tree_bytes(world_kb.memory) == before_kb

    # Case 2: member at K_C only (K_B family was empty, K_C family gained a member).
    world_kc = _empty_family_world(tmp_path / "kc_only")
    _set_family(world_kc, members=[BASE_INVARIANT], revision=2)
    before_kc = tree_bytes(world_kc.memory)
    prepared_kc = prepare_worklist(
        WriteRequest(
            world_kc.memory,
            world_kc.code,
            OWNER,
            "notes/handoff.json",
            {"history": [_row()]},
            commit=True,
        ),
        contract=world_kc.contract,
    )
    assert len(_reached_family_item(prepared_kc.worklist, BASE_FAMILY)["facts"]["members"]) == 1
    refused_kc = native_write_knowledge(prepared_kc)
    assert refused_kc.state == "refused" and EMPTY_REFUSAL in refused_kc.render()
    assert tree_bytes(world_kc.memory) == before_kc


def test_empty_examined_refused_without_reached_family_item_or_worklist(tmp_path: Path) -> None:
    world = _empty_family_world(tmp_path)
    before = tree_bytes(world.memory)

    # Family record did not change, so the prepared worklist has no reached_family item.
    prepared = prepare_worklist(
        WriteRequest(
            world.memory,
            world.code,
            OWNER,
            "notes/handoff.json",
            {
                "history": [
                    {
                        "subject": BASE_FAMILY,
                        "disposition": "no_impact",
                        "reason": "No worklist item reached this family.",
                        "examined": [],
                    }
                ]
            },
            commit=True,
        ),
        contract=world.contract,
    )
    assert prepared.worklist is not None
    assert not any(
        item.get("kind") == "reached_family" and item.get("subject") == BASE_FAMILY
        for item in prepared.worklist.get("items", [])
    )
    refused_no_item = native_write_knowledge(prepared)
    assert refused_no_item.state == "refused" and EMPTY_REFUSAL in refused_no_item.render()
    assert tree_bytes(world.memory) == before

    # An owner with no worklist (wave) is also refused an empty examined list.
    refused_no_worklist = native_write_knowledge(
        WriteRequest(
            world.memory,
            world.code,
            WAVE_OWNER,
            "notes/handoff.json",
            {
                "history": [
                    {
                        "subject": BASE_FAMILY,
                        "disposition": "no_impact",
                        "reason": "Wave has no leaf worklist.",
                        "examined": [],
                    }
                ]
            },
            commit=True,
            worklist=None,
        )
    )
    assert refused_no_worklist.state == "refused" and EMPTY_REFUSAL in refused_no_worklist.render()
    assert tree_bytes(world.memory) == before


@pytest.mark.parametrize("examined_field", ["empty_list", "omitted"])
def test_empty_examined_refused_when_same_write_adds_a_member(
    tmp_path: Path, examined_field: str
) -> None:
    world = _empty_family_world(tmp_path)
    _set_family(world, title="Landing (updated)", revision=2)
    before = tree_bytes(world.memory)

    row_input: dict[str, Any] = {
        "subject": BASE_FAMILY,
        "disposition": "assigned",
        "reason": "Adding a member in the same write.",
    }
    if examined_field == "empty_list":
        row_input["examined"] = []

    prepared = prepare_worklist(
        WriteRequest(
            world.memory,
            world.code,
            OWNER,
            "notes/handoff.json",
            {
                "records": [
                    {
                        "key": "family",
                        "kind": "family",
                        "id": BASE_FAMILY,
                        "fields": {"members": [BASE_INVARIANT]},
                    }
                ],
                "history": [row_input],
            },
            commit=True,
        ),
        contract=world.contract,
    )
    assert _reached_family_item(prepared.worklist, BASE_FAMILY)["facts"]["members"] == []

    refused = native_write_knowledge(prepared)
    assert refused.state == "refused" and EMPTY_REFUSAL in refused.render()
    assert tree_bytes(world.memory) == before


def test_member_that_left_candidate_stamps_base_revision_and_answers_gate(
    tmp_path: Path,
) -> None:
    world = build_world(tmp_path)
    # Place BASE_INVARIANT at revision 3 and add a surviving member at revision 2 in K_B.
    base_inv = read_json(world.memory, INVARIANT_PATH)
    base_inv["revision"] = 3
    survivor = {
        **base_inv,
        "id": SURVIVOR_INVARIANT,
        "revision": 2,
        "statement": "The surviving invariant stays in K_C.",
        "origin": {
            "task": "260101-OLD",
            "leaf": "260101-OLD-L1",
            "legacyId": "legacy-invariant-survivor",
        },
    }
    write(
        world.memory,
        {
            INVARIANT_PATH: canonical_text(base_inv),
            SURVIVOR_PATH: canonical_text(survivor),
        },
    )
    _set_family(world, members=[BASE_INVARIANT, SURVIVOR_INVARIANT])
    k_b = commit_all(world.memory, "family with two members at distinct revisions")
    _update_contract_memory_base(world, k_b)

    # In K_C: BASE_INVARIANT leaves K_C completely, while SURVIVOR_INVARIANT advances to revision 4.
    (world.memory / INVARIANT_PATH).unlink()
    sidecar = read_json(world.memory, SIDECAR_PATH)
    sidecar["realizes"] = []
    survivor["revision"] = 4
    write(
        world.memory,
        {
            SIDECAR_PATH: canonical_text(sidecar),
            SURVIVOR_PATH: canonical_text(survivor),
        },
    )
    _set_family(world, members=[SURVIVOR_INVARIANT], revision=2)
    commit_all(world.memory, "member left K_C on leaf branch")

    prepared = prepare_worklist(
        WriteRequest(
            world.memory,
            world.code,
            OWNER,
            "notes/handoff.json",
            {
                "history": [
                    {
                        "subject": BASE_FAMILY,
                        "disposition": "assigned",
                        "reason": "Examined the departed member at its base revision and the survivor at K_C.",
                        "examined": [BASE_INVARIANT, SURVIVOR_INVARIANT],
                    }
                ]
            },
            commit=True,
            memory_base=k_b,
        ),
        contract=world.contract,
    )
    reached = _reached_family_item(prepared.worklist, BASE_FAMILY)
    expected_facts = sorted(
        [
            {"id": BASE_INVARIANT, "base": 3, "candidate": None},
            {"id": SURVIVOR_INVARIANT, "base": 2, "candidate": 4},
        ],
        key=lambda entry: entry["id"],
    )
    assert reached["facts"]["members"] == expected_facts

    # Before writing, the gate rejects the missing family row.
    before_reason = family_row_open(reached, _gate_context(world))
    assert before_reason is not None and "holds no family row" in before_reason

    written = native_write_knowledge(prepared)
    assert written.state == "written", written.render()

    (stored_row,) = read_json(world.memory, HISTORY_PATH)["rows"]
    expected_examined = sorted(
        [
            {"id": BASE_INVARIANT, "revision": 3},
            {"id": SURVIVOR_INVARIANT, "revision": 4},
        ],
        key=lambda entry: entry["id"],
    )
    assert stored_row["examined"] == expected_examined
    assert stored_row["revision"] == 2
    assert stored_row["items"] == [reached["id"]]

    # The written row satisfies the gate's family predicate (member absent in K_C keeps base rev).
    assert family_row_open(reached, _gate_context(world)) is None


def test_examined_member_neither_stored_nor_in_worklist_facts_is_refused(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    _set_family(world, title="Landing (updated)", revision=2)
    before = tree_bytes(world.memory)

    for bad_member in ("INV-UNKNWN", BASE_FAMILY):
        prepared = prepare_worklist(
            WriteRequest(
                world.memory,
                world.code,
                OWNER,
                "notes/handoff.json",
                {
                    "history": [
                        {
                            "subject": BASE_FAMILY,
                            "disposition": "no_impact",
                            "reason": "Invalid examined member.",
                            "examined": [BASE_INVARIANT, bad_member],
                        }
                    ]
                },
                commit=True,
            ),
            contract=world.contract,
        )
        refused = native_write_knowledge(prepared)
        assert refused.state == "refused"
        assert f"examined member {bad_member!r} is not a stored invariant" in refused.render()
        assert tree_bytes(world.memory) == before

    # A worklist entry with no base revision (base: None or 0) does not license an absent member.
    for invalid_base in (None, 0):
        worklist = {
            "schema": "knowledge-worklist/v1",
            "state": "complete",
            "items": [
                {
                    "id": "sha256:" + "a" * 64,
                    "kind": "reached_family",
                    "subject": BASE_FAMILY,
                    "facts": {
                        "members": [
                            {"id": BASE_INVARIANT, "base": 1, "candidate": 1},
                            {"id": "INV-GHOST1", "base": invalid_base, "candidate": None},
                        ],
                        "reachedBy": ["record-changed"],
                    },
                }
            ],
        }
        refused = native_write_knowledge(
            WriteRequest(
                world.memory,
                world.code,
                OWNER,
                "notes/handoff.json",
                {
                    "history": [
                        {
                            "subject": BASE_FAMILY,
                            "disposition": "no_impact",
                            "reason": "Absent member lacks a base revision >= 1.",
                            "examined": [BASE_INVARIANT, "INV-GHOST1"],
                        }
                    ]
                },
                commit=True,
                worklist=worklist,
            )
        )
        assert refused.state == "refused"
        assert "examined member 'INV-GHOST1' is not a stored invariant" in refused.render()
        assert tree_bytes(world.memory) == before
