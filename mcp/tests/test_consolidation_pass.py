"""A consolidation pass: the items its list raises, the rows that answer them, and the writer.

The worklist and gate cases reuse MIK-R08's real Git code and converted memory repositories
(``test_knowledge_worklist.World``) and compute the worklist over the four named sides exactly as
the leaf route does; the gate case runs the leaf gate over a contract whose task document names the
list. The predicate cases decide stored items through the registered predicates. The writer cases
write hand-offs through ``write_knowledge`` on the writer's paired fixture trees.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agents_remember.application.knowledge_gate import evaluate_leaf_gate
from agents_remember.application.knowledge_gate.predicates import GateContext, item_open_reason
from agents_remember.application.knowledge_worklist.consolidation import (
    CONSOLIDATION_KIND,
    LISTED_MARK,
    Listing,
)
from agents_remember.application.knowledge_worklist.leaf import CandidateTrees
from agents_remember.application.knowledge_worklist.registry import item_id
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.history import HistoryFile
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.document_field_effects import (
    TaskDocumentFieldEffect,
    fields_with_effect,
)
from agents_remember.tasks.render import render_markdown
from agents_remember.worktrees.worktree_contract import load_contract
from knowledge_writer_test_support import (
    BASE_FAMILY,
    BASE_INVARIANT,
    build_world,
    entry,
    read_json,
)
from reconsideration_test_support import write_master
from test_knowledge_worklist import World, family, git, invariant
from test_knowledge_worklist import world as _world_fixture
from test_planned_knowledge_effects import LEAF, history, leaf_document, row
from writer_corrections_test_support import run

world = _world_fixture  # the converted fixture repositories, as the worklist tests build them

A, B, C, D, E, F = (f"INV-{letter * 6}" for letter in "ABCDEF")
F1, F2, F3 = "FAM-F00001", "FAM-F00002", "FAM-F00003"
DECISION = "DEC-7K3F9Q"
PASSED = [{"test": "tests/test_review.py::test_refuses_unlisted", "result": "passed"}]


def _decision(revision: int) -> str:
    return canonical_text(
        {
            "schema": "ar-decision/v1",
            "id": DECISION,
            "origin": {"task": "260928-MIK"},
            "revision": revision,
            "status": "active",
            "context": f"The listing rule, as decided at revision {revision}.",
            "alternatives": [{"option": "List", "status": "chosen", "reason": "Accurate."}],
            "consequences": ["Unlisted paths are refused."],
            "decider": "developer",
            "supersedes": [],
            "admission": "legacy-unassessed",
            "links": [],
        }
    )


def _shared_family_base(world: World) -> None:
    """K_B gains FAM-F00003 (B and C, so B is in two families) and a decision."""

    world.memory_base = world.memory_commit(
        {
            f"knowledge/families/{F3}-shared.json": family(F3, [B, C]),
            f"knowledge/decisions/{DECISION}-listing.json": _decision(1),
        },
        world.code_base,
    )


def _items(document: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    assert document["state"] == "complete", document.get("incomplete")
    return {(item["kind"], item["subject"]): item for item in document["items"]}


def _listed(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        subject: item
        for (kind, subject), item in _items(document).items()
        if kind == CONSOLIDATION_KIND
    }


# --------------------------------------------------------------------------------------------------
# The worklist: the list's items beside today's
# --------------------------------------------------------------------------------------------------


def test_the_list_raises_one_item_per_family_and_rule_beside_todays_and_none_unlisted(
    world: World,
) -> None:
    _shared_family_base(world)
    listing = Listing(families=(F1, F3), rules=(A, E))
    unchanged = world.worklist(world.code_base, consolidation=listing)
    listed = _listed(unchanged)
    # One item per listed family, per listed rule and per member of a listed family; B is a member
    # of both listed families and still has one item; A is listed and a member, once too.
    assert set(listed) == {F1, F3, A, B, C, E}
    assert listed[B]["facts"]["families"] == [F1, F3] and listed[B]["facts"]["listed"] is False
    assert listed[A]["facts"]["listed"] is True and listed[A]["facts"]["families"] == [F1]
    assert listed[E]["facts"] == {
        "record": "invariant",
        "revision": 1,
        "families": [],
        "listed": True,
    }
    # With no change and no list there is no item; an unlisted unchanged record raises none.
    assert not [one for one in unchanged["items"] if one["kind"] != CONSOLIDATION_KIND]
    assert not _items(world.worklist(world.code_base))
    # A changed rule record and a changed decision raise today's items beside the list's; the
    # unlisted unchanged rule F raises nothing.
    world.memory_commit(
        {
            f"knowledge/invariants/{D}-rule.json": invariant(D, revision=2, statement="It moved."),
            f"knowledge/decisions/{DECISION}-listing.json": _decision(2),
        },
        world.code_base,
    )
    changed = world.worklist(world.code_base, consolidation=listing)
    kinds = {key for key in _items(changed) if key[0] != CONSOLIDATION_KIND}
    assert ("touched_invariant", D) in kinds and ("touched_record", DECISION) in kinds
    assert set(_listed(changed)) == set(listed)
    assert not [key for key in _items(changed) if key[1] == F]
    # A new revision of a listed rule makes a new item: the old row no longer answers it.
    world.memory_commit(
        {f"knowledge/invariants/{A}-rule.json": invariant(A, revision=2)}, world.code_base
    )
    rewritten = _listed(world.worklist(world.code_base, consolidation=listing))
    assert rewritten[A]["id"] != listed[A]["id"] and rewritten[A]["facts"]["revision"] == 2
    assert rewritten[E]["id"] == listed[E]["id"]
    # Today's items of a listed subject are marked; those of unlisted subjects are not.
    marks = _items(world.worklist(world.code_base, consolidation=listing))
    assert marks[("touched_invariant", A)][LISTED_MARK] is True
    assert marks[("reached_family", F1)][LISTED_MARK] is True
    assert LISTED_MARK not in marks[("touched_invariant", D)]


# --------------------------------------------------------------------------------------------------
# The gate: held while an item is open, passed when each is closed
# --------------------------------------------------------------------------------------------------


def _consolidation_contract(world: World) -> Path:
    task_root = world.root / "tasks/agents-remember/consolidation_case"
    task_root.mkdir(parents=True)
    write_master(task_root, leaf_document())
    document = leaf_document(consolidationList={"families": [F1]})
    (task_root / "99_leaf.json").write_text(
        document.model_dump_json(by_alias=True, exclude_none=True), encoding="utf-8"
    )
    enclosure = task_root / "enclosures" / LEAF.lower()
    enclosure.mkdir(parents=True)
    path = enclosure / "series-contract.md"
    path.write_text(
        "---\nschema: ar-series-contract/v1\nschemaVersion: 1.0\nkind: leaf\n"
        "task_id: 260928_CONSOLIDATION-CASE\ntask_name: consolidation_case\n"
        "repo_name: agents-remember\nworkflow_kind: light-task\nmemory_mode: external\n\n"
        f"coordination:\n  root: {world.root}\n  task_root: {task_root}\n"
        f"  task_artifact: {task_root / 'task.md'}\n  worktree_group: {world.root}\n"
        f"  leaf_id: {LEAF}\n  parent_task_name: consolidation_case\n\ncode:\n"
        f"  repo_path: {world.code}\n  source_branch: main\n  work_branch: main\n"
        f"  base_commit: {world.code_base}\n  worktree: {world.code}\n\nmemory:\n"
        f"  mode: external\n  repo_path: {world.memory}\n  source_branch: main\n"
        f"  work_branch: main\n  base_commit: {world.memory_base}\n"
        f"  worktree: {world.memory}\n  ledger: {world.memory / 'memory.md'}\n---\n",
        encoding="utf-8",
    )
    return path


def _open_items(world: World, contract_path: Path) -> dict[tuple[str, str], str]:
    candidate = CandidateTrees(
        code=git(world.code, "rev-parse", "HEAD^{tree}"),
        memory=git(world.memory, "rev-parse", "HEAD^{tree}"),
    )
    result = evaluate_leaf_gate(load_contract(contract_path), candidate, persist_worklist=False)
    assert result is not None
    by_id = {item["id"]: item for item in result.worklist["items"]}
    open_items = {
        (by_id[finding.item]["kind"], by_id[finding.item]["subject"]): finding.message
        for finding in result.findings
        if finding.item in by_id
    }
    assert result.ok == (not result.findings), result.findings
    return open_items


def test_the_gate_holds_until_each_item_of_the_list_and_beside_it_is_closed(world: World) -> None:
    contract = _consolidation_contract(world)
    # The list's items are open before any row: the family and each member.
    assert set(_open_items(world, contract)) == {(CONSOLIDATION_KIND, s) for s in (F1, A, B)}
    # The pass corrects A (a new revision): today's touched rule and reached family stand beside.
    # A stands row of the family answers only the list's item, never today's reached family.
    changed_a = row(A, "changed", "ROW-AAAAA1", covers=[], revision=2, effect="clarify")
    stands_b = row(B, "stands", "ROW-BBBBB1", covers=[], revision=1, testsRun=PASSED)
    stands_f1 = row(
        F1,
        "stands",
        "ROW-FFFFF1",
        revision=1,
        examined=[{"id": A, "revision": 2}, {"id": B, "revision": 1}],
    )
    world.memory_commit(
        {
            f"knowledge/invariants/{A}-rule.json": invariant(A, revision=2, statement="Clearer."),
            f"knowledge/history/{LEAF}.json": history(changed_a, stands_b, stands_f1),
        },
        world.code_base,
    )
    held = _open_items(world, contract)
    assert set(held) == {("reached_family", F1)}
    assert "answers an item of the consolidation list only" in held[("reached_family", F1)]
    # The one row today's item asks for (no_impact on the family) closes both items: the gate passes.
    no_impact_f1 = {**stands_f1, "disposition": "no_impact"}
    world.memory_commit(
        {f"knowledge/history/{LEAF}.json": history(changed_a, stands_b, no_impact_f1)},
        world.code_base,
    )
    assert _open_items(world, contract) == {}


# --------------------------------------------------------------------------------------------------
# The two new dispositions against today's items
# --------------------------------------------------------------------------------------------------


def _context(*rows: dict[str, Any], revisions: dict[str, int]) -> GateContext:
    document = {"schema": "ar-history/v1", "leaf": LEAF, "closed": False, "rows": list(rows)}
    candidate = SimpleNamespace(revision=revisions.get)
    return GateContext(
        LEAF,
        HistoryFile.model_validate(document),
        candidate=cast(Any, candidate),
        code=cast(Any, None),
    )


def _touched(subject: str, *, base: int, now: int, listed: bool, entries: Any = ()) -> dict:
    item: dict[str, Any] = {
        "id": item_id(CONSOLIDATION_KIND, subject, [1]),
        "kind": "touched_invariant",
        "subject": subject,
        "facts": {
            "entries": list(entries),
            "added": [],
            "retired": [],
            "reanchored": [],
            "record": {"changed": True, "baseRevision": base, "candidateRevision": now},
        },
    }
    return {**item, LISTED_MARK: True} if listed else item


def test_stands_and_unresolved_close_todays_items_only_in_the_listed_cases() -> None:
    title_only = row(A, "stands", "ROW-AAAAA1", covers=[], revision=1, noProofRecorded=True)
    unresolved = row(
        A,
        "unresolved",
        "ROW-AAAAA1",
        covers=[],
        revision=1,
        testsRun=[{"test": "tests/test_review.py::test_refuses_unlisted", "result": "failed"}],
    )
    revisions = {A: 1}
    # A title given to a listed rule changes its record at an unchanged revision: its stands row
    # answers today's touched item. Unlisted, the same row does not.
    listed_title = _touched(A, base=1, now=1, listed=True)
    assert item_open_reason(listed_title, _context(title_only, revisions=revisions)) is None
    unlisted_title = _touched(A, base=1, now=1, listed=False)
    reason = item_open_reason(unlisted_title, _context(title_only, revisions=revisions))
    assert reason is not None and "consolidation list only" in reason
    # A stands row never answers an item that asks to cover an entry, listed or not.
    touched_entry = [{"id": "RLZ-A00001", "class": "touched"}]
    covering = _touched(A, base=1, now=1, listed=True, entries=touched_entry)
    assert item_open_reason(covering, _context(title_only, revisions=revisions)) is not None
    # An unresolved row answers today's item of a listed subject, and only of a listed one.
    assert item_open_reason(covering, _context(unresolved, revisions=revisions)) is None
    unlisted = _touched(A, base=1, now=1, listed=False, entries=touched_entry)
    assert item_open_reason(unlisted, _context(unresolved, revisions=revisions)) is not None
    family_item = {
        "id": "sha256:f1",
        "kind": "reached_family",
        "subject": F1,
        "facts": {"members": [{"id": A}], "routes": {}, "reachedBy": [f"touched:{A}"]},
    }
    family_unresolved = row(F1, "unresolved", "ROW-FFFFF1", examined=[{"id": A, "revision": 1}])
    context = _context(family_unresolved, revisions={A: 1, F1: 1})
    assert item_open_reason({**family_item, LISTED_MARK: True}, context) is None
    assert item_open_reason(family_item, context) is not None
    # The list's own item takes either new disposition, at the subject's current revision only.
    target = {"id": "sha256:t", "kind": CONSOLIDATION_KIND, "subject": A, "facts": {"revision": 1}}
    assert item_open_reason(target, _context(title_only, revisions=revisions)) is None
    assert item_open_reason(target, _context(unresolved, revisions=revisions)) is None
    stale = item_open_reason(target, _context(title_only, revisions={A: 2}))
    assert stale is not None and "revision 2" in stale
    missing = item_open_reason(target, _context(revisions=revisions))
    assert missing is not None and "no row about" in missing
    unknown = {**target, "facts": {"unknown": True}}
    assert "correct the list" in str(item_open_reason(unknown, _context(revisions={})))


# --------------------------------------------------------------------------------------------------
# The writer: the rows of a consolidation task
# --------------------------------------------------------------------------------------------------


def _pass_worklist(*subjects: str) -> dict[str, Any]:
    return {
        "items": [
            {
                "id": item_id(CONSOLIDATION_KIND, subject, [1]),
                "kind": CONSOLIDATION_KIND,
                "subject": subject,
                "facts": {},
            }
            for subject in subjects
        ]
    }


def _history_of(world: Any) -> dict[str, Any]:
    (path,) = sorted((world.memory / "knowledge/history").glob("*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("ordinary", [False, True], ids=["consolidation", "ordinary"])
def test_a_rule_row_of_a_consolidation_task_names_its_tests(tmp_path: Path, ordinary: bool) -> None:
    world = build_world(tmp_path)
    worklist = {"items": []} if ordinary else _pass_worklist(BASE_INVARIANT, BASE_FAMILY)
    bare = {"subject": BASE_INVARIANT, "disposition": "stands", "reason": "The code holds it."}
    report = run(world, {"history": [bare]}, worklist=worklist)
    if ordinary:
        assert report.state == "written", report.render()
        assert "consolidationSubjects" not in _history_of(world)
        return
    assert report.state == "refused"
    assert "testsRun" in report.render() and "noProofRecorded" in report.render()


def test_stands_unresolved_and_untested_rows_and_a_title_that_raises_no_revision(
    tmp_path: Path,
) -> None:
    world = build_world(tmp_path)
    worklist = _pass_worklist(BASE_INVARIANT, BASE_FAMILY)
    not_run = [{"test": "tests/test_landing.py::test_plain", "reason": "needs a browser"}]
    stands = {
        "subject": BASE_INVARIANT,
        "disposition": "stands",
        "reason": "land_pair returns both halves together, as the rule says.",
        "testsRun": [
            {
                "test": "tests/test_landing.py::LandingTests::test_pair_lands_together",
                "result": "passed",
            }
        ],
        "testsNotRun": not_run,
    }
    family_stands = {
        "subject": BASE_FAMILY,
        "disposition": "stands",
        "reason": "Its one member still holds.",
        "examined": [BASE_INVARIANT],
    }
    title = entry("A-9", invariant_id=BASE_INVARIANT, statement="Code and memory land as one pair.")
    report = run(
        world,
        {"entries": [{**title, "title": "Landing pair"}], "history": [stands, family_stands]},
        worklist=worklist,
    )
    assert report.state == "written", report.render()
    rule = read_json(world.memory, f"knowledge/invariants/{BASE_INVARIANT}-landing-pair.json")
    assert (rule["title"], rule["revision"]) == ("Landing pair", 1)
    written = _history_of(world)
    assert written["consolidationSubjects"] == sorted([BASE_INVARIANT, BASE_FAMILY])
    by_subject = {one["subject"]: one for one in written["rows"]}
    assert by_subject[BASE_INVARIANT]["disposition"] == "stands"
    assert by_subject[BASE_INVARIANT]["revision"] == 1  # the revision that was read
    assert by_subject[BASE_INVARIANT]["testsNotRun"] == not_run
    assert by_subject[BASE_FAMILY]["revision"] == 1
    # An unresolved row with its failing test, and a row that says no proof is recorded.
    unresolved = {
        "subject": BASE_INVARIANT,
        "disposition": "unresolved",
        "reason": "The rule says one pair; land_pair accepts a missing half.",
        "testsRun": [{"test": "tests/test_landing.py::test_plain", "result": "failed"}],
    }
    assert run(world, {"history": [unresolved]}, worklist=worklist).state == "written"
    rows = {one["subject"]: one for one in _history_of(world)["rows"]}
    assert rows[BASE_INVARIANT]["disposition"] == "unresolved"
    unproven = {**stands, "testsRun": None, "testsNotRun": None, "noProofRecorded": True}
    unproven = {key: value for key, value in unproven.items() if value is not None}
    assert run(world, {"history": [unproven]}, worklist=worklist).state == "written"
    stored = {one["subject"]: one for one in _history_of(world)["rows"]}[BASE_INVARIANT]
    assert stored["noProofRecorded"] is True and "testsRun" not in stored


# --------------------------------------------------------------------------------------------------
# The task document: the list's shape, its render line and its plane
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("listing", "refusal"),
    [
        ({}, "at least one family"),
        ({"families": [], "rules": []}, "at least one family"),
        ({"rules": [F1]}, "pattern"),
        ({"families": [A]}, "pattern"),
        ({"rules": [A, A]}, "names an id twice"),
        ({"rules": [A], "size": 1}, "Extra inputs"),
        ({"families": [F1, F2], "rules": [A]}, None),
    ],
)
def test_the_task_document_names_the_list_of_a_pass(listing: dict, refusal: str | None) -> None:
    if refusal is not None:
        with pytest.raises(ValueError, match=refusal):
            leaf_document(consolidationList=listing)
        return
    document = leaf_document(consolidationList=listing)
    assert document.consolidationList is not None
    assert document.consolidationList.listed_ids() == (F1, F2, A)
    assert f"**Consolidation list:** `{F1}`, `{F2}`, `{A}`" in render_markdown(document)
    assert "consolidationList" in fields_with_effect(
        TaskDocument, TaskDocumentFieldEffect.LIFECYCLE
    )
    master = document.model_dump(by_alias=True, exclude_none=True) | {"kind": "master"}
    with pytest.raises(ValueError, match="belongs to a task document"):
        TaskDocument.model_validate({**master, "steps": [], "intentReview": None})


def test_persisted_consolidation_target_worklist_passes_writer_shape_check(tmp_path: Path) -> None:
    """Real persisted consolidation_target items carry string facts.record and are accepted."""
    world = build_world(tmp_path)
    persisted = {
        "schema": "knowledge-worklist/v1",
        "state": "complete",
        "items": [
            {
                "id": item_id(CONSOLIDATION_KIND, BASE_FAMILY, (LISTED_MARK,)),
                "kind": CONSOLIDATION_KIND,
                "subject": BASE_FAMILY,
                "facts": {"families": [], "listed": True, "record": "family", "revision": 1},
            },
            {
                "id": item_id(CONSOLIDATION_KIND, BASE_INVARIANT, (LISTED_MARK,)),
                "kind": CONSOLIDATION_KIND,
                "subject": BASE_INVARIANT,
                "facts": {
                    "families": [BASE_FAMILY],
                    "listed": False,
                    "record": "invariant",
                    "revision": 1,
                },
            },
        ],
    }
    report = run(
        world,
        {
            "history": [
                {
                    "subject": BASE_INVARIANT,
                    "disposition": "stands",
                    "reason": "Checked against code; rule still holds.",
                    "noProofRecorded": True,
                },
                {
                    "subject": BASE_FAMILY,
                    "disposition": "stands",
                    "reason": "Joint guarantee still holds.",
                    "examined": [BASE_INVARIANT],
                },
            ]
        },
        worklist=persisted,
    )
    assert report.state == "written", report.render()
