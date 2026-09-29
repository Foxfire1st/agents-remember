"""MIK-R11@v2: a leaf's declared knowledge effects, reconciled against its history rows.

The declaration is the task document's ``expectedKnowledgeEffects``; the rows are the leaf's history
file in K_C (MIK-R07). The worklist cases reuse MIK-R08's real Git code and converted memory
repositories (``test_knowledge_worklist.World``), commit the leaf's rows into K_C, and compute the
worklist over the four named sides exactly as the leaf route does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_worklist import (
    ITEM_KINDS,
    leaf_worklist,
)
from agents_remember.application.knowledge_worklist.planned_effects import (
    Declaration,
    declarations_from,
)
from agents_remember.application.knowledge_worklist.surface import leaf_worklist_fields
from agents_remember.application.knowledge_writer.memory_state import Owner
from agents_remember.application.knowledge_writer.writer import WriteRequest, write_knowledge
from agents_remember.application.task_docs.task_doc_tools import TaskDocEdit, _apply
from agents_remember.memory_quality.knowledge_worklist_section import knowledge_worklist_lines
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.planned import planned_item_open
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.document_field_effects import (
    TaskDocumentFieldEffect,
    TaskDocumentMutationClass,
    classify_task_document_mutation,
    fields_with_effect,
)
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.leaf_decisions import leaf_decision_refusal
from agents_remember.tasks.render import render_markdown
from agents_remember.tasks.task_intent import task_intent_identity, task_intent_projection
from agents_remember.worktrees.worktree_contract import load_contract
from pydantic import ValidationError
from test_knowledge_worklist import (
    DATA,
    DATA_V1,
    LINES,
    LINES_V1,
    OTHER,
    OTHER_V1,
    REVIEW,
    REVIEW_V1,
    TESTS,
    TESTS_V1,
    World,
    _init,
    base_memory,
    invariant,
)

LEAF = "260928-MIK-L99"
DECISION_AT = "2026-09-29T10:00+02:00"


@pytest.fixture
def world(tmp_path: Path) -> World:
    """MIK-R08's converted fixture (``test_knowledge_worklist.world``), built the same way."""

    built = World(root=tmp_path, code=tmp_path / "code", memory=tmp_path / "memory")
    _init(built.code)
    built.code_base = built.code_commit(
        {REVIEW: REVIEW_V1, OTHER: OTHER_V1, TESTS: TESTS_V1, LINES: LINES_V1, DATA: DATA_V1}
    )
    _init(built.memory)
    built.memory_base = built.memory_commit(base_memory(built, built.code_base), built.code_base)
    return built


def effect(subject: str, label: str, ref: str = "MIK-R11@v2") -> dict[str, str]:
    return {"subject": subject, "effect": label, "requirementRef": ref}


def leaf_document(**fields: Any) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": LEAF,
            "slug": "99_leaf",
            "title": "Leaf",
            "kind": "subTask",
            "repo": "agents-remember",
            "createdAt": "2026-09-29T00:00+02:00",
            "objective": "Strengthen the listing rule.",
            **fields,
        }
    )


def row(subject: str, disposition: str, row_id: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": row_id,
        "subject": subject,
        "disposition": disposition,
        "reason": "Judged.",
        "items": [],
        **fields,
    }


def history(*rows: dict[str, Any]) -> str:
    return canonical_text({"schema": "ar-history/v1", "leaf": LEAF, "closed": False, "rows": rows})


# --------------------------------------------------------------------------------------------------
# The declaration (rules 1 and 2; failure: a malformed declaration)
# --------------------------------------------------------------------------------------------------


def test_the_field_is_optional_normative_intent_settable_and_refuses_malformed_declarations(
    tmp_path: Path,
) -> None:
    plain = leaf_document()
    assert plain.expectedKnowledgeEffects is None
    assert "expectedKnowledgeEffects" not in plain.model_dump(by_alias=True, exclude_none=True)
    assert "expectedKnowledgeEffects" in fields_with_effect(
        TaskDocument, TaskDocumentFieldEffect.NORMATIVE_INTENT
    )
    candidate = ResolvedTaskDocument(
        ref=TaskDocumentRef(repository="agents-remember", path="t/99_leaf.json"),
        path=tmp_path / "99_leaf.json",
        document=plain,
    )
    # No declaration: the intent projects exactly its former slots, so existing digests hold.
    canonical = task_intent_projection(tmp_path, candidate).canonical_value()
    assert "expectedKnowledgeEffects" not in canonical

    declared = [effect("invariant:INV-AAAAAA", "strengthen"), effect("new:e1", "introduce")]
    edited = _apply("set_field", plain, TaskDocEdit(fields={"expectedKnowledgeEffects": declared}))
    assert [one.subject for one in edited.expectedKnowledgeEffects or ()] == [
        "invariant:INV-AAAAAA",
        "new:e1",
    ]
    assert classify_task_document_mutation(plain, edited).classes == frozenset(
        {TaskDocumentMutationClass.INTENT}
    )
    with_effects = ResolvedTaskDocument(candidate.ref, candidate.path, edited)
    assert (
        task_intent_projection(tmp_path, with_effects).canonical_value()["expectedKnowledgeEffects"]
        == declared
    )
    assert task_intent_identity(tmp_path, with_effects) != task_intent_identity(tmp_path, candidate)
    cleared = _apply("set_field", edited, TaskDocEdit(fields={"expectedKnowledgeEffects": None}))
    assert task_intent_identity(
        tmp_path, ResolvedTaskDocument(candidate.ref, candidate.path, cleared)
    ) == task_intent_identity(tmp_path, candidate)
    rendered = render_markdown(edited)
    assert "**Expected knowledge effects:**" in rendered
    assert "- `invariant:INV-AAAAAA` — `strengthen` (MIK-R11@v2)" in rendered

    refused = {
        "a repeated declaration": [declared[0], {**declared[0], "requirementRef": "MIK-R12@v2"}],
        "an unknown subject form": [effect("hunk:abc", "strengthen")],
        "a malformed invariant ID": [effect("invariant:INV-0143", "strengthen")],
        "a label outside the vocabulary": [effect("family:FAM-F00001", "preserve")],
        "a prose requirement reference": [effect("new:e1", "introduce", "the packet")],
        "an empty declaration": [],
        "an extra key": [{**declared[0], "position": 1}],
    }
    for why, value in refused.items():
        with pytest.raises(ValidationError):
            leaf_document(expectedKnowledgeEffects=value)
            pytest.fail(f"{why} was accepted")
    with pytest.raises(ValidationError, match="leaf document"):
        TaskDocument.model_validate(
            {
                "id": "M",
                "slug": "master",
                "title": "Master",
                "kind": "master",
                "repo": "agents-remember",
                "createdAt": "2026-09-29T00:00+02:00",
                "expectedKnowledgeEffects": declared,
            }
        )


# --------------------------------------------------------------------------------------------------
# Matching and classification (rules 3, 4 and 6; the examples)
# --------------------------------------------------------------------------------------------------

EDITED_REVIEW = REVIEW_V1.replace(
    "    return path not in LISTED\n", "    return path not in frozenset(LISTED)\n"
).replace("    return value * 2\n", "    return value * 3\n")

DECLARED = (
    # matched by a changed row with the same effect
    Declaration("invariant:INV-AAAAAA", "strengthen", "MIK-R11@v2"),
    # label mismatch: the changed row's effect is strengthen
    Declaration("invariant:INV-AAAAAA", "clarify", "MIK-R11@v2"),
    # non-conforming example: a no_impact row does not clear a declared strengthening
    Declaration("invariant:INV-BBBBBB", "strengthen", "MIK-R11@v2"),
    # retire is delivered by a deleted row that retires the invariant
    Declaration("invariant:INV-DDDDDD", "retire", "MIK-R11@v2"),
    # a family matches only a changed family row
    Declaration("family:FAM-F00001", "strengthen", "MIK-R11@v2"),
    # an ID K_C does not hold
    Declaration("invariant:INV-ZZZZZZ", "strengthen", "MIK-R11@v2"),
    # a new invariant of this leaf, by its origin's hand-off entry; and a label nobody wrote
    Declaration("new:e1", "introduce", "MIK-R11@v2"),
    Declaration("new:e2", "introduce", "MIK-R11@v2"),
)


def _leaf_rows(world: World, code: str, *extra: dict[str, Any]) -> None:
    rows = [
        row("INV-AAAAAA", "changed", "ROW-AAAAAA", covers=[], revision=2, effect="strengthen"),
        row("INV-BBBBBB", "no_impact", "ROW-BBBBBB", covers=[], revision=1),
        row("INV-DDDDDD", "deleted", "ROW-DDDDDD", covers=[], revision=1, effect="retire"),
        row(
            "FAM-F00001", "no_impact", "ROW-FFFFFF", examined=[{"id": "INV-AAAAAA", "revision": 2}]
        ),
        *extra,
    ]
    new_record = json.loads(invariant("INV-NNNNNN"))
    new_record["origin"] = {"task": "260928-MIK", "leaf": LEAF, "handoffEntry": "e1"}
    world.memory_commit(
        {
            "knowledge/invariants/INV-AAAAAA-rule.json": invariant("INV-AAAAAA", revision=2),
            "knowledge/invariants/INV-NNNNNN-new.json": canonical_text(new_record),
            f"knowledge/history/{LEAF}.json": history(*rows),
        },
        code,
    )


def _worklist(world: World, code: str, declared: tuple[Declaration, ...] | None) -> dict[str, Any]:
    document = world.worklist(code, expected_effects=declared)
    assert document["state"] == "complete", document.get("incomplete")
    return document


def _by_subject(document: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(item["kind"], item["subject"]): item for item in document["items"]}


def test_declarations_match_only_the_rows_that_deliver_them_and_mark_every_item(
    world: World,
) -> None:
    code = world.code_commit({REVIEW: EDITED_REVIEW})
    _leaf_rows(world, code)
    document = _worklist(world, code, DECLARED)
    items = _by_subject(document)
    # Rule 4: touched invariants and reached families are planned when declared, else unplanned.
    assert items[("touched_invariant", "INV-AAAAAA")]["planning"] == "planned"
    assert items[("reached_family", "FAM-F00001")]["planning"] == "planned"
    assert items[("touched_invariant", "INV-CCCCCC")]["planning"] == "unplanned"
    assert items[("reached_family", "FAM-F00002")]["planning"] == "unplanned"
    untouched = {
        subject: item for (kind, subject), item in items.items() if kind == "planned_untouched"
    }
    assert set(untouched) == {
        "planned:invariant:INV-AAAAAA#clarify",
        "planned:invariant:INV-BBBBBB#strengthen",
        "planned:family:FAM-F00001#strengthen",
        "planned:invariant:INV-ZZZZZZ#strengthen",
        "planned:new:e2#introduce",
    }
    assert untouched["planned:invariant:INV-ZZZZZZ#strengthen"]["facts"]["unmatched"] == (
        "subject_unknown"
    )
    no_impact = untouched["planned:invariant:INV-BBBBBB#strengthen"]["facts"]
    assert no_impact["unmatched"] == "no_matching_row"
    assert no_impact["rows"] == [{"id": "ROW-BBBBBB", "disposition": "no_impact"}]
    mismatch = untouched["planned:invariant:INV-AAAAAA#clarify"]["facts"]
    assert mismatch["rows"] == [
        {"id": "ROW-AAAAAA", "disposition": "changed", "effect": "strengthen"}
    ]
    assert mismatch["declared"] == {
        "subject": "invariant:INV-AAAAAA",
        "effect": "clarify",
        "requirementRef": "MIK-R11@v2",
    }
    summary = {entry["key"]: entry for entry in document["plannedEffects"]["entries"]}
    assert summary["planned:invariant:INV-AAAAAA#strengthen"]["matchedBy"] == "ROW-AAAAAA"
    assert summary["planned:invariant:INV-DDDDDD#retire"]["matchedBy"] == "ROW-DDDDDD"
    assert summary["planned:new:e1#introduce"]["matchedBy"] == "INV-NNNNNN"
    assert all(item["satisfiedBy"] is None for item in untouched.values())

    # The subject key is built from the declaration, not its position: reordering changes nothing.
    reordered = _worklist(world, code, tuple(reversed(DECLARED)))
    assert reordered["items"] == document["items"] and reordered["digest"] == document["digest"]

    # Rule 6: no declaration, every item unplanned and no planned_untouched item.
    undeclared = _worklist(world, code, None)
    assert undeclared["plannedEffects"] == {"declared": False}
    assert {item.get("planning") for item in undeclared["items"]} == {"unplanned"}
    assert not [item for item in undeclared["items"] if item["kind"] == "planned_untouched"]

    # The remaining branches, over a second fixture commit (review R1 F3).
    _leaf_rows(
        world,
        code,
        row("FAM-F00002", "changed", "ROW-FFFFF2", examined=[{"id": "INV-CCCCCC", "revision": 1}]),
        row(
            "INV-EEEEEE",
            "deleted",
            "ROW-EEEEEE",
            revision=1,
            covers=[
                {
                    "id": "RLZ-E00001",
                    "before": {
                        **world.anchor(
                            world.code_base, LINES, {"kind": "line_range", "start": 3, "end": 5}
                        ),
                        "path": LINES,
                    },
                    "after": "absent",
                }
            ],
        ),
    )
    other_leaf = json.loads(invariant("INV-PPPPPP"))
    other_leaf["origin"] = {"task": "260928-MIK", "leaf": "260928-MIK-L98", "handoffEntry": "e3"}
    in_base = json.loads(invariant("INV-FFFFFF"))
    in_base["origin"] = {"task": "260928-MIK", "leaf": LEAF, "handoffEntry": "e4"}
    world.memory_commit(
        {
            "knowledge/invariants/INV-PPPPPP-other.json": canonical_text(other_leaf),
            "knowledge/invariants/INV-FFFFFF-rule.json": canonical_text(in_base),
        },
        code,
    )
    second = (
        Declaration("family:FAM-F00002", "split", "MIK-R11@v2"),
        Declaration("new:e3", "introduce", "MIK-R11@v2"),
        Declaration("new:e4", "introduce", "MIK-R11@v2"),
        Declaration("invariant:INV-EEEEEE", "retire", "MIK-R11@v2"),
        Declaration("invariant:INV-CCCCCC", "strengthen", "MIK-R11@v2"),
    )
    # The leaf's base has moved past the memory base (`third` changed), so INV-CCCCCC is stale.
    stale_code = world.code_commit({REVIEW: EDITED_REVIEW.replace("value * 3", "value * 4")})
    later = world.worklist(stale_code, base=code, expected_effects=second)
    assert later["state"] == "complete", later.get("incomplete")
    marks = _by_subject(later)
    # A stale invariant is marked like a touched one.
    assert marks[("stale_invariant", "INV-CCCCCC")]["planning"] == "planned"
    assert marks[("reached_family", "FAM-F00002")]["planning"] == "planned"
    matched = {entry["key"]: entry for entry in later["plannedEffects"]["entries"]}
    # A changed family row delivers a family declaration.
    assert matched["planned:family:FAM-F00002#split"]["matchedBy"] == "ROW-FFFFF2"
    # new: matches only an invariant this leaf first created: not another leaf's, not one in K_B.
    assert matched["planned:new:e3#introduce"]["matched"] is False
    assert matched["planned:new:e4#introduce"]["matched"] is False
    # A deleted row that does not retire the invariant does not deliver `retire`.
    retire = matched["planned:invariant:INV-EEEEEE#retire"]
    assert retire["matched"] is False and retire["unmatched"] == "no_matching_row"
    assert marks[("planned_untouched", "planned:invariant:INV-EEEEEE#retire")]["facts"]["rows"] == [
        {"id": "ROW-EEEEEE", "disposition": "deleted"}
    ]


def test_a_planned_row_answers_its_item_and_the_stored_predicate_agrees(world: World) -> None:
    code = world.code_commit({REVIEW: EDITED_REVIEW})
    answers = [
        row(
            "planned:invariant:INV-BBBBBB#strengthen",
            "deferred",
            "ROW-PPPPP1",
            ref={"leaf": "260928-MIK-L98"},
        ),
        row(
            "planned:family:FAM-F00001#strengthen",
            "realized_elsewhere",
            "ROW-PPPPP2",
            ref={"row": "ROW-AAAAAA"},
        ),
    ]
    _leaf_rows(world, code)
    before = _by_subject(_worklist(world, code, DECLARED))
    _leaf_rows(world, code, *answers)
    document = _worklist(world, code, DECLARED)
    items = _by_subject(document)
    answered = items[("planned_untouched", "planned:invariant:INV-BBBBBB#strengthen")]
    assert answered["satisfiedBy"] == "ROW-PPPPP1"
    assert items[("planned_untouched", "planned:family:FAM-F00001#strengthen")]["satisfiedBy"] == (
        "ROW-PPPPP2"
    )
    assert answered["id"] == before[("planned_untouched", answered["subject"])]["id"]
    rows = json.loads((world.memory / f"knowledge/history/{LEAF}.json").read_text())["rows"]
    by_subject = {one["subject"]: one["id"] for one in rows}
    for item in document["items"]:
        if item["kind"] == "planned_untouched":
            assert planned_item_open(item, by_subject) == (item["satisfiedBy"] is None)
    # The registered kind declares its four things (MIK-R08 rule 1).
    kind = ITEM_KINDS["planned_untouched"]
    assert kind.owner == "MIK-R11" and kind.facts == ("declared", "unmatched", "rows")
    assert kind.accepts_subject(answered["subject"]) and "planned row" in kind.satisfying_row


# --------------------------------------------------------------------------------------------------
# The row (rule 5; failure: a dropped row whose decision does not resolve)
# --------------------------------------------------------------------------------------------------


def _write(world: World, rows: list[dict[str, Any]], decisions: Any = None) -> Any:
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=Owner(task="260928-MIK", kind="leaf", id=LEAF),
            handoff_path="handoff.json",
            document={"history": rows},
            decisions=decisions,
        )
    )


def test_the_writer_writes_planned_rows_and_refuses_what_they_cannot_name(
    world: World, tmp_path: Path
) -> None:
    task_root = tmp_path / "task"
    task_root.mkdir()
    decision = {"at": DECISION_AT, "decision": "Drop it.", "rationale": "Out of scope."}
    (task_root / "99_leaf.json").write_text(
        leaf_document(
            decisions=[decision, {**decision, "at": "twice"}, {**decision, "at": "twice"}]
        ).model_dump_json(by_alias=True, exclude_none=True),
        encoding="utf-8",
    )
    assert leaf_decision_refusal(task_root, LEAF, DECISION_AT) is None
    assert "no decision entry" in str(leaf_decision_refusal(task_root, LEAF, "never"))
    assert "ambiguous" in str(leaf_decision_refusal(task_root, LEAF, "twice"))
    assert "no task document" in str(leaf_decision_refusal(task_root, "260928-MIK-L00", "x"))
    # The strict resolver (review R1 F1): a second document claiming the leaf, or the leaf's
    # document in an unreadable state, is a named refusal -- never the first readable match.
    doubtful = tmp_path / "doubtful"
    doubtful.mkdir()
    readable = leaf_document(decisions=[decision]).model_dump_json(by_alias=True, exclude_none=True)
    (doubtful / "99_leaf.json").write_text(readable, encoding="utf-8")
    (doubtful / "99_copy.json").write_text(readable, encoding="utf-8")
    assert "ambiguous" in str(leaf_decision_refusal(doubtful, LEAF, DECISION_AT))
    (doubtful / "99_copy.json").write_text(
        json.dumps({**json.loads(readable), "unknownField": 1}), encoding="utf-8"
    )
    assert "cannot be read: 99_copy.json" in str(leaf_decision_refusal(doubtful, LEAF, DECISION_AT))
    (doubtful / "99_copy.json").unlink()
    (doubtful / f"{LEAF}.json").write_text("{not json", encoding="utf-8")
    assert "cannot read" in str(leaf_decision_refusal(doubtful, LEAF, DECISION_AT))
    (doubtful / f"{LEAF}.json").unlink()
    (doubtful / "preview.json").write_text('{"id": "a preview"}', encoding="utf-8")
    assert leaf_decision_refusal(doubtful, LEAF, DECISION_AT) is None  # names no leaf: ignored

    def owner(at: str) -> str | None:
        return leaf_decision_refusal(task_root, LEAF, at)

    subject = "planned:invariant:INV-AAAAAA#strengthen"
    dropped = {"subject": subject, "disposition": "dropped", "reason": "Ruled out."}
    written = _write(world, [{**dropped, "ref": {"decision": DECISION_AT}}], owner)
    assert written.state == "planned", written.render()
    assert [one.subject for one in written.rows] == [subject]
    realized = {
        "subject": "planned:new:e1#introduce",
        "disposition": "realized_elsewhere",
        "reason": "Delivered by the existing rule.",
        "ref": {"invariant": "INV-BBBBBB"},
    }
    assert _write(world, [realized], owner).state == "planned"
    deferred = {
        "subject": "planned:family:FAM-F00001#merge",
        "disposition": "deferred",
        "reason": "Follow-up packet.",
        "ref": {
            "requirement": {
                "task": {"repository": "agents-remember", "path": "260928_mik"},
                "packet": "requirements/MIK-R14-v2.md",
                "id": "MIK-R14",
                "version": "v2",
            }
        },
    }
    assert _write(world, [deferred], owner).state == "planned"

    refusals = {
        "does not resolve": [{**dropped, "ref": {"decision": "never"}}],
        "no task owner": [{**dropped, "ref": {"decision": DECISION_AT}}],
        "ref names one of": [{**dropped, "ref": {"leaf": "260928-MIK-L98"}}],
        "names no stored invariant": [{**realized, "ref": {"invariant": "INV-ZZZZZZ"}}],
        "names no history row": [{**realized, "ref": {"row": "ROW-ZZZZZZ"}}],
        "carries 'ref'": [{**dropped}],
        "and no ['effect']": [{**dropped, "ref": {"decision": DECISION_AT}, "effect": "retire"}],
        "exactly one": [{**dropped, "ref": {"decision": DECISION_AT, "leaf": "260928-MIK-L98"}}],
        "not allowed": [{**dropped, "disposition": "no_impact", "ref": {"decision": DECISION_AT}}],
    }
    for expected, rows in refusals.items():
        report = _write(world, rows, None if expected == "no task owner" else owner)
        assert report.state == "refused", expected
        assert expected in report.render(), (expected, report.render())
    # A label outside the vocabulary is no planned subject key, so no registered row kind claims it.
    wrong = {**dropped, "subject": "planned:invariant:INV-AAAAAA#preserve"}
    assert _write(world, [{**wrong, "ref": {"decision": DECISION_AT}}], owner).state == "refused"


# --------------------------------------------------------------------------------------------------
# A leaf: the declaration from its task document, the checklist and the tool (rule 7)
# --------------------------------------------------------------------------------------------------


def _contract(world: World, task_root: Path) -> Path:
    enclosure = task_root / "enclosures" / LEAF.lower()
    enclosure.mkdir(parents=True, exist_ok=True)
    path = enclosure / "series-contract.md"
    path.write_text(
        "---\nschema: ar-series-contract/v1\nschemaVersion: 1.0\nkind: leaf\n"
        "task_id: 260928_PLANNED-CASE\ntask_name: planned_case\nrepo_name: agents-remember\n"
        "workflow_kind: light-task\nmemory_mode: external\n\ncoordination:\n"
        f"  root: {world.root}\n  task_root: {task_root}\n"
        f"  task_artifact: {task_root / 'task.md'}\n  worktree_group: {world.root}\n"
        f"  leaf_id: {LEAF}\n  parent_task_name: planned_case\n\ncode:\n"
        f"  repo_path: {world.code}\n  source_branch: main\n  work_branch: main\n"
        f"  base_commit: {world.code_base}\n  worktree: {world.code}\n\nmemory:\n"
        f"  mode: external\n  repo_path: {world.memory}\n  source_branch: main\n"
        f"  work_branch: main\n  base_commit: {world.memory_base}\n"
        f"  worktree: {world.memory}\n  ledger: {world.memory / 'memory.md'}\n---\n",
        encoding="utf-8",
    )
    return path


def test_a_leaf_reads_its_declaration_and_the_checklist_and_tool_show_the_marks(
    world: World, tmp_path: Path
) -> None:
    task_root = tmp_path / "task"
    task_root.mkdir()
    (task_root / "task.md").write_text("# task\n", encoding="utf-8")
    declared = [effect("invariant:INV-AAAAAA", "strengthen"), effect("family:FAM-F00002", "split")]
    (task_root / "99_leaf.json").write_text(
        leaf_document(expectedKnowledgeEffects=declared).model_dump_json(
            by_alias=True, exclude_none=True
        ),
        encoding="utf-8",
    )
    code = world.code_commit({REVIEW: EDITED_REVIEW})
    _leaf_rows(world, code)
    contract = _contract(world, task_root)
    document = leaf_worklist(load_contract(contract))
    assert document is not None and document["state"] == "complete", document
    items = _by_subject(document)
    assert items[("touched_invariant", "INV-AAAAAA")]["planning"] == "planned"
    assert items[("reached_family", "FAM-F00002")]["planning"] == "planned"
    assert items[("touched_invariant", "INV-CCCCCC")]["planning"] == "unplanned"
    assert ("planned_untouched", "planned:family:FAM-F00002#split") in items
    assert declarations_from(declared) == (
        Declaration("invariant:INV-AAAAAA", "strengthen", "MIK-R11@v2"),
        Declaration("family:FAM-F00002", "split", "MIK-R11@v2"),
    )
    lines = knowledge_worklist_lines(document, "w.json")
    assert any("| touched_invariant | INV-AAAAAA | planned |" in line for line in lines)
    assert any("| touched_invariant | INV-CCCCCC | unplanned |" in line for line in lines)
    assert any(
        "planned:family:FAM-F00002#split" in line
        and "unmatched" in line
        and "needs a planned row" in line
        for line in lines
    )
    assert any(
        "| planned_untouched | planned:family:FAM-F00002#split |" in line
        and "needs a planned row" in line
        for line in lines
    )
    tool = leaf_worklist_fields(str(contract))["worklist"]
    assert tool["plannedEffects"]["declared"] is True
    planning = {item["subject"]: item.get("planning") for item in tool["items"]}
    assert planning["INV-AAAAAA"] == "planned" and planning["INV-CCCCCC"] == "unplanned"
    assert planning["planned:family:FAM-F00002#split"] is None

    # Fail closed (review R1 F2): the leaf's task document exists but cannot be read, so the run
    # is incomplete and names it -- never a worklist with no declaration.
    document_path = task_root / "99_leaf.json"
    document_path.write_text(
        json.dumps({**json.loads(document_path.read_text()), "unknownField": 1}), encoding="utf-8"
    )
    failed = leaf_worklist(load_contract(contract))
    assert failed is not None and failed["state"] == "incomplete"
    assert failed["incomplete"][0]["input"] == "leaf task document"
    assert "99_leaf.json" in failed["incomplete"][0]["detail"]
    assert "plannedEffects" not in failed and failed["items"] == []
