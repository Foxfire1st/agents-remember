"""MIK-R10@v2: every unexplained change needs an authored disposition.

The worklist cases reuse MIK-R08's real Git code and converted memory repositories
(``test_knowledge_worklist.World``): ``pkg/review.py`` carries four realization entries in K_B, so it
is *covered*; a new file is covered only when its governing onboarding route is ``migrated``. The
curator's answers go through the real writer, and every run checks that the stored-item predicate the
gate uses (:func:`unexplained_item_open`) agrees with the worklist's own ``satisfiedBy``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_worklist import (
    ITEM_KINDS,
    leaf_onboarding_trace_sides,
    leaf_worklist,
)
from agents_remember.application.knowledge_worklist.onboarding_trace import trace_context
from agents_remember.application.knowledge_worklist.surface import leaf_worklist_fields
from agents_remember.application.knowledge_writer.memory_state import Owner
from agents_remember.application.knowledge_writer.writer import WriteRequest, write_knowledge
from agents_remember.application.memory_quality.controller import _needed_rows_dropped
from agents_remember.memory_quality.knowledge_worklist_section import knowledge_worklist_lines
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.history import (
    UnexplainedChangeRow,
    row_kind_for_subject,
)
from agents_remember.models.knowledge_files.unexplained import (
    hunk_item_id,
    unexplained_item_open,
    unexplained_satisfied_by,
)
from agents_remember.worktrees.modules.onboarding_trace import onboarding_trace_result
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
    git,
    invariant,
)
from test_planned_knowledge_effects import _contract

LEAF = "260928-MIK-L99"
UNEXPLAINED = ("unexplained_hunk", "unexplained_file")
FIFTH = "\n\ndef fifth():\n    return 5\n"
WITH_FIFTH = REVIEW_V1 + FIFTH
FRESH = "pkg/fresh.py"


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


def unexplained(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The run's unexplained items by subject, after checking the gate's predicate agrees."""

    assert document["state"] == "complete", document.get("incomplete")
    found = {item["subject"]: item for item in document["items"] if item["kind"] in UNEXPLAINED}
    rows = _rows(document)
    for item in found.values():
        assert unexplained_satisfied_by(item, rows) == item["satisfiedBy"], item
        assert unexplained_item_open(item, rows) is (item["satisfiedBy"] is None)
    return found


def _rows(document: dict[str, Any]) -> dict[str, str]:
    """The leaf's history rows by subject, read from the memory working tree the run captured.

    Every case either names that working tree as K_C or commits all of it before the run.
    """

    location = Path(document["pairing"]["memoryCandidate"]["location"])
    path = location / f"knowledge/history/{LEAF}.json"
    if not path.is_file():
        return {}
    return {row["subject"]: row["id"] for row in json.loads(path.read_text())["rows"]}


def only(document: dict[str, Any], path: str) -> dict[str, Any]:
    (item,) = [one for one in unexplained(document).values() if one["facts"]["path"] == path]
    return item


def write(world: World, document: dict[str, Any]) -> Any:
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=Owner(task="260928-MIK", kind="leaf", id=LEAF),
            handoff_path="handoff.json",
            document=document,
            commit=True,
        )
    )


def written(world: World, document: dict[str, Any]) -> Any:
    report = write(world, document)
    assert report.state == "written", (report.problems, report.violations)
    return report


def no_invariant(subject: str, reason: str = "logging-only helper") -> dict[str, Any]:
    return {"subject": subject, "disposition": "no_invariant", "reason": reason}


def attach(invariant_id: str, locator: dict[str, Any], path: str = REVIEW) -> dict[str, Any]:
    """An ``attach``: a new realization entry on a stored invariant, its statement unchanged."""

    return {
        "id": f"attach-{invariant_id}",
        "statement": "It holds.",
        "kind": "clause",
        "invariant_id": invariant_id,
        "target": [{"path": path, "locator": locator, "rationale": "It enforces the rule."}],
    }


def worklist(world: World, code: str, **options: Any) -> dict[str, Any]:
    return world.worklist(code, memory_candidate=world.memory, **options)


def reset_memory(world: World) -> None:
    git(world.memory, "checkout", "-q", "--", ".")
    git(world.memory, "clean", "-qfd")


# --------------------------------------------------------------------------------------------------
# Registration and the row (rules 2 and 3; failure: no_invariant without a reason)
# --------------------------------------------------------------------------------------------------


def test_the_kinds_are_registered_and_no_invariant_is_the_only_row_disposition() -> None:
    for name in UNEXPLAINED:
        kind = ITEM_KINDS[name]
        assert kind.owner == "MIK-R10" and "coverage" in kind.facts
        assert "no_invariant" in kind.satisfying_row and "onboarding" in kind.satisfying_row
    subject = f"hunk:pkg/a.py@absent..sha256:{'1' * 64}"
    assert ITEM_KINDS["unexplained_hunk"].accepts_subject(subject)
    assert hunk_item_id(subject) == hunk_item_id(subject)
    row = {"id": "ROW-AAAAAA", "disposition": "no_invariant", "reason": "Logging.", "items": []}
    for answered in (f"hunk:{hunk_item_id(subject)}", f"file:pkg/data.bin@{'a' * 40}"):
        assert row_kind_for_subject(answered).name == "unexplained"
        UnexplainedChangeRow.model_validate({**row, "subject": answered})
    answered = f"hunk:{hunk_item_id(subject)}"
    for refused, message in (
        ({**row, "subject": answered, "disposition": "no_impact"}, "not allowed"),
        ({**row, "subject": answered, "reason": " "}, "reason"),
        ({**row, "subject": answered, "covers": []}, "Extra inputs"),
        ({**row, "subject": "hunk:pkg/a.py"}, "subject"),
    ):
        with pytest.raises(ValidationError, match=message):
            UnexplainedChangeRow.model_validate(refused)


# --------------------------------------------------------------------------------------------------
# Coverage (rule 1): entries in K_B, or the governing route migrated in the latest census entry
# --------------------------------------------------------------------------------------------------


def _census(census: str, status: str, at: str) -> dict[str, str | bytes | None]:
    root = f"knowledge/census/{census}"
    return {
        f"{root}/baseline.json": canonical_text(
            {
                "schema": "ar-census-baseline/v1",
                "census": census,
                "code": {"commit": "c" * 40},
                "memory": {"commit": "d" * 40},
                "scope": [],
            }
        ),
        f"{root}/inventory.json": canonical_text(
            {"schema": "ar-census-inventory/v1", "census": census, "sources": [], "artifacts": []}
        ),
        f"{root}/routes/pkg.json": canonical_text(
            {
                "schema": "ar-census-route/v1",
                "census": census,
                "route": "pkg",
                "statuses": [
                    {
                        "status": status,
                        "reason": f"{status} by the wave",
                        "tree": "e" * 40,
                        "provenance": {"wave": "260930-MIG-W1", "session": "s", "at": at},
                    }
                ],
            }
        ),
    }


def test_coverage_is_entries_in_k_b_or_the_latest_census_status_of_the_governing_route(
    world: World,
) -> None:
    code = world.code_commit({REVIEW: WITH_FIFTH, FRESH: "VALUE = 1\n"})
    document = world.worklist(code)
    covered = only(document, REVIEW)["facts"]["coverage"]
    assert covered == {
        "state": "covered",
        "realizationEntries": 4,
        "route": None,
        "routeStatus": "pending",
    }
    assert only(document, FRESH)["facts"]["coverage"]["state"] == "uncovered"

    # K_B gains the route `pkg` and a census that marks it migrated: the new file is covered.
    migrated = world.memory_commit(
        {
            "onboarding/pkg/overview.md": "# pkg\n",
            **_census("wave-1", "migrated", "2026-09-29T08:00:00+02:00"),
        },
        world.code_base,
    )
    fresh = only(world.worklist(code, memory_base=migrated), FRESH)["facts"]["coverage"]
    assert fresh == {
        "state": "covered",
        "realizationEntries": 0,
        "route": "pkg",
        "routeStatus": "migrated",
        "census": "wave-1",
    }
    assert only(world.worklist(code, memory_base=migrated), FRESH)["facts"]["admits"] == [
        "attach",
        "author",
        "no_invariant",
    ]
    # A later entry in another census governs: the route is back in progress, so uncovered.
    reopened = world.memory_commit(
        _census("wave-2", "in_progress", "2026-09-29T09:00:00+02:00"), world.code_base
    )
    fresh = only(world.worklist(code, memory_base=reopened), FRESH)
    assert fresh["facts"]["coverage"]["routeStatus"] == "in_progress"
    assert fresh["facts"]["coverage"]["state"] == "uncovered"
    assert fresh["facts"]["admits"] == ["onboarding_trace"]
    # A census that does not read leaves coverage undecidable: the run is incomplete, naming K_B.
    broken = world.memory_commit(
        {"knowledge/census/wave-3/routes/pkg.json": "{}\n"}, world.code_base
    )
    failed = world.worklist(code, memory_base=broken)
    assert failed["state"] == "incomplete" and failed["incomplete"][0]["input"] == "K_B"
    assert "censuses of K_B" in failed["incomplete"][0]["detail"]


# --------------------------------------------------------------------------------------------------
# Covered files: attach, author or no_invariant -- never onboarding (rule 3, examples)
# --------------------------------------------------------------------------------------------------


def test_a_covered_hunk_is_answered_by_no_invariant_attach_or_author_and_never_by_onboarding(
    world: World,
) -> None:
    code = world.code_commit({REVIEW: WITH_FIFTH})
    item = only(worklist(world, code), REVIEW)
    assert item["satisfiedBy"] is None and not item["facts"]["deleteOnly"]
    assert item["facts"]["admits"] == ["attach", "author", "no_invariant"]
    assert item["facts"]["row"] == f"hunk:{item['id']}"
    assert item["subject"].startswith(f"hunk:{REVIEW}@absent..sha256:")

    # Non-conforming example: an onboarding trace for the file does not answer a covered item.
    written(
        world, {"history": [dict(no_invariant(f"onboarding:{REVIEW}"), disposition="no_impact")]}
    )
    assert only(worklist(world, code), REVIEW)["satisfiedBy"] is None
    assert unexplained_item_open(item, {f"onboarding:{REVIEW}": "ROW-X"})

    # Conforming: no_invariant with a reason.
    report = written(world, {"history": [no_invariant(item["facts"]["row"])]})
    answered = only(worklist(world, code), REVIEW)
    assert answered["id"] == item["id"] and answered["satisfiedBy"] == report.rows[0].id

    # Conforming: attach -- a new entry over the hunk links it, and the invariant's own item follows.
    reset_memory(world)
    written(world, {"entries": [attach("INV-DDDDDD", {"kind": "symbol", "value": "fifth"})]})
    document = worklist(world, code)
    assert unexplained(document) == {}
    touched = {(one["kind"], one["subject"]): one for one in document["items"]}
    assert touched[("touched_invariant", "INV-DDDDDD")]["facts"]["added"]

    # Conforming: author -- a new invariant with its entry; it raises no item of its own.
    reset_memory(world)
    authored = {
        "id": "e-fifth",
        "statement": "The fifth value is five.",
        "kind": "clause",
        "scope": {"applicability": "Always.", "conditions": [], "exclusions": []},
        "admission": {"criteria": ["prevents_costly_mistake"], "justification": "Callers rely."},
        "target": [
            {
                "path": REVIEW,
                "locator": {"kind": "symbol", "value": "fifth"},
                "rationale": "It returns five.",
            }
        ],
    }
    written(world, {"entries": [authored]})
    document = worklist(world, code)
    assert unexplained(document) == {}
    assert not [one for one in document["items"] if one["kind"] == "touched_invariant"]


def test_a_delete_only_hunk_admits_only_no_invariant(world: World) -> None:
    code = world.code_commit({REVIEW: REVIEW_V1.replace('LISTED = {"a"}\n', "")})
    item = only(worklist(world, code), REVIEW)
    assert item["facts"]["deleteOnly"] and item["facts"]["admits"] == ["no_invariant"]
    assert item["facts"]["changedLines"]["candidate"] == "absent"
    # A new entry whose range surrounds the deletion point does not link a delete-only hunk: the
    # deleted line is not at C, so an attach cannot answer it (rule 4).
    written(
        world,
        {"entries": [attach("INV-DDDDDD", {"kind": "line_range", "start": 6, "end": 10})]},
    )
    after = worklist(world, code)
    assert only(after, REVIEW)["id"] == item["id"] and only(after, REVIEW)["satisfiedBy"] is None
    (change,) = [one for one in after["changes"] if one["path"] == REVIEW]
    assert [hunk["linked"] for hunk in change["hunks"]] == [False]
    report = written(world, {"history": [no_invariant(item["facts"]["row"], "dead constant")]})
    assert only(worklist(world, code), REVIEW)["satisfiedBy"] == report.rows[-1].id


# --------------------------------------------------------------------------------------------------
# Non-text changes (rule 2) and currentness (rule 6)
# --------------------------------------------------------------------------------------------------


def test_a_non_text_change_opens_a_file_item_bound_to_its_c_blob(world: World) -> None:
    (world.code / REVIEW).chmod(0o755)
    code = world.code_commit({"pkg/extra.bin": b"\x00new\x00"})
    document = worklist(world, code)
    mode = only(document, REVIEW)
    blob = git(world.code, "rev-parse", f"{code}:{REVIEW}")
    assert mode["kind"] == "unexplained_file" and mode["subject"] == f"file:{REVIEW}@{blob}"
    assert mode["facts"]["modeChange"] and mode["facts"]["coverage"]["state"] == "covered"
    binary = only(document, "pkg/extra.bin")
    assert binary["facts"]["content"] == "binary"
    assert binary["facts"]["coverage"]["state"] == "uncovered"
    # The writer refuses a row about another change of the path (unknown subject).
    refused = write(world, {"history": [no_invariant(f"file:{REVIEW}@{'0' * 40}")]})
    assert refused.state == "refused"
    assert "names no change at C" in "; ".join(one.render() for one in refused.problems)
    report = written(world, {"history": [no_invariant(mode["subject"], "executable bit")]})
    assert only(worklist(world, code), REVIEW)["satisfiedBy"] == report.rows[0].id
    # A second, different change to the binary opens a new item.
    again = world.code_commit({"pkg/extra.bin": b"\x00newer\x00"})
    assert only(worklist(world, again), "pkg/extra.bin")["subject"] != binary["subject"]


def test_an_edit_elsewhere_in_the_file_never_reopens_an_answered_item(world: World) -> None:
    code = world.code_commit({REVIEW: WITH_FIFTH})
    item = only(worklist(world, code), REVIEW)
    report = written(world, {"history": [no_invariant(item["facts"]["row"])]})
    # Boundary example: a different function in the file changes; the answer stays current.
    other = world.code_commit({REVIEW: WITH_FIFTH.replace("value + 1", "value + 2")})
    document = worklist(world, other)
    still = only(document, REVIEW)
    assert still["id"] == item["id"] and still["satisfiedBy"] == report.rows[0].id
    assert ("touched_invariant", "INV-BBBBBB") in {
        (one["kind"], one["subject"]) for one in document["items"]
    }
    # Editing the unexplained lines themselves is a new change: a new item, open.
    edited = world.code_commit({REVIEW: WITH_FIFTH.replace("return 5", "return 6")})
    changed = only(worklist(world, edited), REVIEW)
    assert changed["id"] != item["id"] and changed["satisfiedBy"] is None
    assert worklist(world, edited)["unexplained"]["unnecessaryRows"] == [
        {"subject": item["facts"]["row"], "row": report.rows[0].id}
    ]


def test_symlinks_deleted_non_text_and_uncovered_deletions_take_their_own_records(
    world: World,
) -> None:
    """Review N6: a symlink's ``file:`` subject names its C object, a non-text path deleted at C is
    ``@absent`` and admits only no_invariant, and an uncovered delete-only hunk takes its trace."""

    base = world.code_commit({"pkg/extra.bin": b"\x00old\x00"})
    migrated = world.memory_commit(
        {
            "onboarding/pkg/overview.md": "# pkg\n",
            **_census("wave-1", "migrated", "2026-09-29T08:00:00+02:00"),
        },
        base,
    )
    os.symlink("review.py", world.code / "pkg/link")
    code = world.code_commit(
        {"pkg/extra.bin": None, TESTS: TESTS_V1.replace("from pkg.review import _not_listed\n", "")}
    )
    document = world.worklist(code, base=base, memory_base=migrated)
    link = only(document, "pkg/link")
    target = git(world.code, "rev-parse", f"{code}:pkg/link")
    assert link["subject"] == f"file:pkg/link@{target}" and link["facts"]["content"] != "text"
    assert link["facts"]["coverage"]["state"] == "covered"  # through the migrated route `pkg`
    gone = only(document, "pkg/extra.bin")
    assert gone["subject"] == "file:pkg/extra.bin@absent"
    assert gone["facts"]["status"] == "deleted" and gone["facts"]["admits"] == ["no_invariant"]
    deleted = only(document, TESTS)  # a proof is not a realization: tests/ is uncovered
    assert deleted["facts"]["deleteOnly"] and deleted["facts"]["admits"] == ["onboarding_trace"]
    assert deleted["facts"]["onboardingTrace"]["subject"] == f"onboarding:{TESTS}"
    # The scratch census is minimal, so the rows are committed as a history file directly.
    rows = {
        link["subject"]: "ROW-AAAAA1",
        gone["subject"]: "ROW-BBBBB2",
        f"onboarding:{TESTS}": "ROW-CCCCC3",
    }
    history = {
        "schema": "ar-history/v1",
        "leaf": LEAF,
        "closed": False,
        "rows": [
            {
                "id": row_id,
                "subject": subject,
                "disposition": "no_impact" if subject.startswith("onboarding:") else "no_invariant",
                "reason": "Judged.",
                "items": [],
            }
            for subject, row_id in rows.items()
        ],
    }
    world.memory_commit({f"knowledge/history/{LEAF}.json": canonical_text(history)}, base)
    answered = world.worklist(code, base=base, memory_base=migrated)
    for subject, path in (
        (link["subject"], "pkg/link"),
        (gone["subject"], "pkg/extra.bin"),
        (f"onboarding:{TESTS}", TESTS),
    ):
        assert only(answered, path)["satisfiedBy"] == rows[subject]


# --------------------------------------------------------------------------------------------------
# Uncovered files: the onboarding trace (rule 5), through the leaf route
# --------------------------------------------------------------------------------------------------


def test_an_uncovered_new_file_is_satisfied_by_its_onboarding_trace(
    world: World, tmp_path: Path
) -> None:
    task_root = tmp_path / "task"
    task_root.mkdir()
    (task_root / "task.md").write_text("# task\n", encoding="utf-8")
    world.code_commit({FRESH: "VALUE = 1\n"})
    contract = load_contract(_contract(world, task_root))

    document = leaf_worklist(contract)
    assert document is not None
    item = only(document, FRESH)
    assert item["facts"]["coverage"]["state"] == "uncovered" and item["satisfiedBy"] is None
    assert item["facts"]["onboardingTrace"] == {
        "subject": f"onboarding:{FRESH}",
        "item": None,
        "countedChange": False,
    }
    # A no_invariant row is not the record an uncovered file takes.
    stray = written(world, {"history": [no_invariant(item["facts"]["row"])]}).rows[0]
    rowed = leaf_worklist(contract)
    assert rowed is not None and only(rowed, FRESH)["satisfiedBy"] is None
    # Review N2: it answers nothing, so it is reported as unnecessary (report-only).
    assert rowed["unexplained"]["unnecessaryRows"] == [
        {"subject": item["facts"]["row"], "row": stray.id}
    ]
    lines = knowledge_worklist_lines(rowed, "w.json")
    assert any(f"needs the onboarding trace `onboarding:{FRESH}`" in line for line in lines)

    # The curator writes the file's card: a counted onboarding change satisfies the item.
    (world.memory / f"onboarding/{FRESH}.md").parent.mkdir(parents=True, exist_ok=True)
    (world.memory / f"onboarding/{FRESH}.md").write_text("# fresh\n\nA value.\n", encoding="utf-8")
    document = leaf_worklist(contract)
    assert document is not None
    settled = only(document, FRESH)
    trace = next(one for one in document["items"] if one["subject"] == f"onboarding:{FRESH}")
    assert settled["id"] == item["id"] and settled["satisfiedBy"] == "counted-change"
    assert settled["facts"]["onboardingTrace"]["item"] == trace["id"]
    assert document["unexplained"]["openCount"] == len(
        [one for one in unexplained(document).values() if one["satisfiedBy"] is None]
    )
    tool = leaf_worklist_fields(str(contract.contract_path))["worklist"]
    assert tool["itemsByKind"]["unexplained_hunk"] == 1


# --------------------------------------------------------------------------------------------------
# Writer refusals (Failure And Recovery)
# --------------------------------------------------------------------------------------------------


def test_the_writer_refuses_what_the_packet_refuses(world: World) -> None:
    code = world.code_commit({REVIEW: WITH_FIFTH})
    item = only(worklist(world, code), REVIEW)
    fifth = {"kind": "symbol", "value": "fifth"}

    def refusal(document: dict[str, Any]) -> str:
        report = write(world, document)
        assert report.state == "refused"
        return "; ".join(one.render() for one in report.problems)

    assert "no stored record INV-ZZZZZZ" in refusal({"entries": [attach("INV-ZZZZZZ", fifth)]})
    retired = json.loads(invariant("INV-DDDDDD"))
    retired["status"] = "retired"
    world.memory_commit(
        {"knowledge/invariants/INV-DDDDDD-rule.json": canonical_text(retired)}, code
    )
    assert "INV-DDDDDD is retired" in refusal({"entries": [attach("INV-DDDDDD", fifth)]})
    row = item["facts"]["row"]
    assert "'reason'" in refusal({"history": [no_invariant(row, " ")]})
    assert "carries no ['covers']" in refusal(
        {"history": [{**no_invariant(row), "covers": ["RLZ-D00001"]}]}
    )
    assert "not allowed" in refusal(
        {"history": [{**no_invariant(row), "disposition": "no_impact"}]}
    )


def test_an_onboarding_row_that_answers_an_uncovered_item_is_not_reported_unnecessary(
    world: World, tmp_path: Path
) -> None:
    """Ruling Q3: MIK-R30 gates no card for a path without one, so its report would call the
    leaf's ``onboarding:<path>`` row unnecessary -- but that row is the trace MIK-R10 rule 5 needs."""

    task_root = tmp_path / "task"
    task_root.mkdir()
    (task_root / "task.md").write_text("# task\n", encoding="utf-8")
    world.code_commit({FRESH: "VALUE = 1\n"})
    contract = load_contract(_contract(world, task_root))
    stray = "onboarding:pkg/untouched.py"  # a file the leaf did not change: still unnecessary
    report = written(
        world,
        {
            "history": [
                dict(
                    no_invariant(f"onboarding:{FRESH}", "a new constant"), disposition="no_impact"
                ),
                dict(no_invariant(stray, "nothing"), disposition="no_impact"),
            ]
        },
    )
    document = leaf_worklist(contract)
    assert document is not None
    assert only(document, FRESH)["satisfiedBy"] == report.rows[0].id
    assert [row["subject"] for row in document["onboardingTrace"]["unnecessaryRows"]] == [stray]

    # The memory-quality run's report-only findings leave the answering row out the same way.
    sides = leaf_onboarding_trace_sides(contract)
    assert sides is not None
    gate = onboarding_trace_result(trace_context(contract), [FRESH], sides)
    reported = {one["subject"] for one in gate.report_only_findings()}
    assert reported == {f"onboarding:{FRESH}", stray}
    response: dict[str, object] = {"onboardingTrace": gate.brief()}
    assert gate.brief()["unnecessaryRowCount"] == 2
    kept = _needed_rows_dropped(gate.report_only_findings(), (document, None), response)
    assert [one["subject"] for one in kept] == [stray]
    # The tool response's count agrees with the adjusted list.
    assert response["onboardingTrace"] == {**gate.brief(), "unnecessaryRowCount": 1}
