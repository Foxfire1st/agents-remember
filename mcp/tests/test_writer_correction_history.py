"""MIK-R54 writer corrections, exercised against real paired Git trees."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_worklist import ExplicitSides, worklist_for_sides
from agents_remember.application.knowledge_writer import WriteRequest
from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.history import InvariantRow
from knowledge_writer_test_support import (
    ADMISSION,
    BASE_FAMILY,
    BASE_INVARIANT,
    BASE_REALIZATION,
    CODE,
    CODE_FILE,
    LEAF_ID,
    SCOPE,
    World,
    build_world,
    commit_all,
    entry,
    git,
    read_json,
    target,
    tree_bytes,
    write,
)
from test_knowledge_writer import _every_kind, _ingest
from writer_corrections_test_support import OWNER, run
from writer_worklist_test_support import prepare_worklist


def _relocated_extension(tmp_path: Path) -> tuple[World, dict[str, Any], list[dict[str, Any]]]:
    """One synthetic leaf adds an entry and relocates two carried entries of the same invariant."""
    world = build_world(tmp_path)
    sidecar_path = f"onboarding/{CODE_FILE}.json"
    sidecar = read_json(world.memory, sidecar_path)
    carried = sidecar["realizes"]
    carried.append(
        {
            **carried[0],
            "id": "RLZ-CARRY1",
            "anchor": CodeSnapshot.capture(world.code)
            .resolve(CODE_FILE, {"kind": "symbol", "name": "Helper.check"})
            .to_document(),
            "origin": {"leaf": "260101-OLD-L1", "handoffEntry": "old-carry"},
        }
    )
    write(world.memory, {sidecar_path: canonical_text(sidecar)})
    commit_all(world.memory, "two carried entries")
    moved = "pkg/moved/landing.py"
    (world.code / "pkg/moved").mkdir(parents=True)
    git(world.code, "mv", CODE_FILE, moved)
    write(world.memory, {f"onboarding/{moved}.md": "# Landing\n"})
    document = {
        "entries": [
            entry(
                "ADDED",
                invariant_id=BASE_INVARIANT,
                statement="Code and memory land as one pair.",
                target=[target("Other.check", path=moved)],
            )
        ],
        "history": [
            {
                "subject": BASE_INVARIANT,
                "disposition": "extended",
                "reason": "One entry added; the carried entries moved without changing meaning.",
                "covers": [
                    *({"id": one["id"], "path": moved} for one in carried),
                    {"handoff": "ADDED"},
                ],
            }
        ],
    }
    return world, document, carried


def test_extended_row_adds_one_entry_and_reanchors_its_carried_entries(tmp_path: Path) -> None:
    world, document, carried = _relocated_extension(tmp_path)
    record_path = next((world.memory / "knowledge/invariants").glob("*.json"))
    invariant_before = record_path.read_bytes()
    worklist = prepare_worklist(
        WriteRequest(world.memory, world.code, OWNER, "notes/handoff.json", document)
    ).worklist
    report = run(world, document, worklist=worklist)
    assert report.state == "written", report.render()
    assert record_path.read_bytes() == invariant_before
    old = read_json(world.memory, f"onboarding/{CODE_FILE}.json")
    assert old["realizes"] == []
    moved = document["entries"][0]["target"][0]["path"]
    after = read_json(world.memory, f"onboarding/{moved}.json")["realizes"]
    assert len(after) == 3
    by_id = {one["id"]: one for one in after}
    for before in carried:
        anchor = CodeSnapshot.capture(world.code).resolve(moved, before["anchor"]["locator"])
        assert by_id[before["id"]] == {**before, "anchor": anchor.to_document()}
    (added,) = [one for one in after if one["id"] not in {old["id"] for old in carried}]
    assert added["origin"]["handoffEntry"] == "ADDED"
    history = read_json(world.memory, f"knowledge/history/{LEAF_ID}.json")
    (row,) = [one for one in history["rows"] if one["subject"] == BASE_INVARIANT]
    parsed = InvariantRow.model_validate(row)
    assert parsed.disposition == "extended" and parsed.revision == 1
    assert len(parsed.covers) == 3
    assert sum(cover.added for cover in parsed.covers) == 1
    assert sum(cover.reanchored for cover in parsed.covers) == 2
    for cover in row["covers"]:
        assert cover["after"] == {**by_id[cover["id"]]["anchor"], "path": moved}
        if cover["id"] != added["id"]:
            before = next(one for one in carried if one["id"] == cover["id"])
            assert cover["before"] == {**before["anchor"], "path": CODE_FILE}
    written = tree_bytes(world.memory)
    assert run(world, document, worklist=worklist).state == "written"
    assert tree_bytes(world.memory) == written


def test_no_impact_refuses_the_same_added_and_relocated_entry_request(tmp_path: Path) -> None:
    world, document, _carried = _relocated_extension(tmp_path)
    document["history"][0]["disposition"] = "no_impact"
    before = tree_bytes(world.memory)
    report = run(world, document)
    assert report.refused
    assert any(
        one.message == "a cover names another source path only on a moved row"
        for one in report.problems
    )
    assert tree_bytes(world.memory) == before


@pytest.mark.parametrize("disposition", ["no_impact", "moved", "extended"])
def test_real_ingest_refuses_a_row_hiding_closed_attempt_change(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    disposition: str,
) -> None:
    world = build_world(tmp_path)
    memory_base = git(world.memory, "rev-parse", "HEAD")
    changed = {
        "subject": BASE_INVARIANT,
        "disposition": "changed",
        "effect": "clarify",
        "reason": "Sharper.",
    }
    first = run(
        world,
        {
            "entries": [
                entry("A1", invariant_id=BASE_INVARIANT, statement="Both halves always land.")
            ],
            "history": [changed],
        },
    )
    assert first.state == "written", first.render()
    history_path = first.rows[0].history
    history = read_json(world.memory, history_path)
    write(world.memory, {history_path: canonical_text({**history, "closed": True})})
    commit_all(world.memory, "closed first attempt")
    closed_bytes = (world.memory / history_path).read_bytes()
    write(world.code, {CODE_FILE: CODE.replace("return (code, memory)", "return (memory, code)")})
    commit_all(world.code, "stale invariant beside the closed change")
    worklist = worklist_for_sides(
        ExplicitSides(
            code_repository=world.code,
            base=git(world.code, "rev-parse", "HEAD"),
            memory_repository=world.memory,
            memory_base=memory_base,
            memory_candidate=git(world.memory, "rev-parse", "HEAD"),
            code_candidate=git(world.code, "rev-parse", "HEAD"),
            owner=OWNER.id,
        )
    )
    assert worklist is not None
    same = [item for item in worklist["items"] if item.get("subject") == BASE_INVARIANT]
    assert [item["kind"] for item in same] == ["stale_invariant", "touched_invariant"]
    listed = world.task_root / "handoff.json"
    row: dict[str, Any] = {
        "subject": BASE_INVARIANT,
        "disposition": disposition,
        "reason": "No new change.",
    }
    document: dict[str, Any] = {"history": [row]}
    if disposition == "moved":
        write(world.code, {CODE_FILE: "# shifted\n" + CODE})
        row["covers"] = [BASE_REALIZATION]
    elif disposition == "extended":
        document["entries"] = [
            entry(
                "A2",
                invariant_id=BASE_INVARIANT,
                statement="Both halves always land.",
                target=[target("Helper.check")],
            )
        ]
        row["covers"] = [{"handoff": "A2"}]
    write(world.task_root, {"handoff.json": json.dumps(document)})
    for ordered in (worklist["items"], list(reversed(worklist["items"]))):
        write(
            world.contract.parent,
            {"knowledge-worklist.json": json.dumps({**worklist, "items": ordered})},
        )
        before = tree_bytes(world.memory)
        assert _ingest(world, listed, "--commit") == 1
        report = json.loads(capsys.readouterr().out)
        assert "name the `changed` row again" in str(report)
        assert tree_bytes(world.memory) == before
        assert (world.memory / history_path).read_bytes() == closed_bytes


def test_ingest_unreadable_worklist_names_path_and_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    world = build_world(tmp_path)
    write(world.contract.parent, {"knowledge-worklist.json": "{broken", "handoff.json": "[]"})
    assert _ingest(world, world.task_root / "handoff.json", "--commit") == 2
    assert str(world.contract.parent / "knowledge-worklist.json") in capsys.readouterr().out


@pytest.mark.parametrize(
    "worklist",
    [
        None,
        [],
        {"items": None},
        {"items": 1},
        {"items": {}},
        {"items": [1]},
        {"items": [{"facts": 1}]},
        {"items": [{"facts": {"record": 1}}]},
        {"items": [{"facts": {"changed": 1}}]},
        {"items": [{"facts": {"changed": [1]}}]},
        {"items": [{"kind": []}]},
        {"items": [{"facts": {"record": {"baseRevision": []}}}]},
        {"items": [{"facts": {"onboardingTrace": []}}]},
        {"items": [{"facts": {"changed": [{"entry": {"candidate": []}}]}}]},
        {"items": [{"facts": {"reachedBy": 1}}]},
    ],
)
def test_ingest_structurally_unreadable_worklist_refuses_without_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], worklist: Any
) -> None:
    world = build_world(tmp_path)
    path = world.contract.parent / "knowledge-worklist.json"
    path.write_text(json.dumps(worklist))
    listed = world.task_root / "handoff.json"
    listed.write_text("{}")
    before = tree_bytes(world.memory)
    assert _ingest(world, listed, "--commit") in (1, 2)
    text = capsys.readouterr().out
    assert str(path) in text and "memory_quality_check" in text
    assert tree_bytes(world.memory) == before


@pytest.mark.parametrize("disposition", ["no_impact", "assigned"])
def test_reached_family_guarantee_change_needs_changed_row(
    tmp_path: Path, disposition: str
) -> None:
    world = build_world(tmp_path)
    item = {
        "id": "sha256:" + "b" * 64,
        "kind": "reached_family",
        "subject": BASE_FAMILY,
        "facts": {"reachedBy": ["guarantee-changed"]},
    }
    report = run(
        world,
        {
            "history": [
                {
                    "subject": BASE_FAMILY,
                    "disposition": disposition,
                    "examined": [BASE_INVARIANT],
                    "reason": "Judged.",
                }
            ]
        },
        worklist={"items": [item]},
    )
    assert report.refused
    assert "name the `changed` row again" in report.render()


@pytest.mark.parametrize("subject", ["literal", "handoff:A1", "A1"])
def test_record_first_authored_in_leaf_needs_no_itemless_row(tmp_path: Path, subject: str) -> None:
    world = build_world(tmp_path)
    held = prepare_worklist(
        WriteRequest(world.memory, world.code, OWNER, "notes/handoff.json", {})
    ).worklist
    assert held is not None
    authored = run(world, [entry("A1", scope=SCOPE, admission=ADMISSION)], worklist=held)
    assert authored.state == "written", authored.render()
    history = [
        {
            "subject": authored.records[0].id if subject == "literal" else subject,
            "disposition": "no_impact",
            "reason": "Judged.",
        }
    ]
    before = tree_bytes(world.memory)
    for entries in ([], [entry("A1", scope=SCOPE, admission=ADMISSION)]):
        report = run(world, {"entries": entries, "history": history}, worklist=held)
        assert report.refused
        assert (
            "a record first authored in this leaf needs no row (MIK-R07 rule 6)" in report.render()
        )
        assert tree_bytes(world.memory) == before
    eligible = prepare_worklist(
        WriteRequest(world.memory, world.code, OWNER, "notes/handoff.json", {})
    ).worklist
    assert eligible is not None and any(
        item.get("subject") == f"membership:{authored.records[0].id}" for item in eligible["items"]
    )
    admitted = run(world, {"history": history}, worklist=eligible)
    assert admitted.state == "written", admitted.render()
    assert admitted.rows[0].subject == authored.records[0].id


@pytest.mark.parametrize(
    "kind",
    [
        "decision",
        "incident",
        "assumption",
        "limitation",
        "failure_mode",
        "scenario",
        "diagnostic",
        "term",
    ],
)
def test_stored_record_alias_uses_canonical_own_record_eligibility(
    tmp_path: Path, kind: str
) -> None:
    world = build_world(tmp_path)
    held = prepare_worklist(
        WriteRequest(world.memory, world.code, OWNER, "notes/handoff.json", {})
    ).worklist
    authored = run(world, _every_kind(world), worklist=held)
    assert authored.state == "written", authored.render()
    record = next(one for one in authored.records if one.kind == kind)
    before = tree_bytes(world.memory)
    for subject in (record.id, f"handoff:{record.handoff_entry}"):
        report = run(
            world,
            {"history": [{"subject": subject, "disposition": "no_impact", "reason": "Judged."}]},
            worklist=held,
        )
        assert report.refused, report.render()
        assert (
            "a record first authored in this leaf needs no row (MIK-R07 rule 6)" in report.render()
        )
        assert tree_bytes(world.memory) == before


def test_a_planned_subject_containing_an_own_id_is_not_a_row_about_that_record(
    tmp_path: Path,
) -> None:
    world = build_world(tmp_path)
    authored = run(world, [entry("A1", scope=SCOPE, admission=ADMISSION)])
    assert authored.state == "written", authored.render()
    identity = authored.records[0].id
    report = run(
        world,
        {"history": [{"subject": identity, "disposition": "no_impact", "reason": "Judged."}]},
        worklist={
            "items": [
                {
                    "id": "sha256:" + "d" * 64,
                    "kind": "planned_effect",
                    "subject": f"planned:invariant:{identity}#strengthen",
                    "facts": {},
                }
            ]
        },
    )
    assert report.refused
    assert "a record first authored in this leaf needs no row (MIK-R07 rule 6)" in report.render()


@pytest.mark.parametrize("subject", ["planned:new:A1#introduce", "hunk:" + "a" * 64])
def test_unmatched_worklist_subject_names_remedy(tmp_path: Path, subject: str) -> None:
    world = build_world(tmp_path)
    report = run(
        world,
        {
            "history": [
                {
                    "subject": subject,
                    "disposition": "deferred" if subject.startswith("planned:") else "no_invariant",
                    "reason": "Judged.",
                    "ref": {
                        "requirement": {
                            "task": {"repository": "repo", "path": "task"},
                            "packet": "requirements/R1.md",
                            "id": "MIK-R01",
                            "version": "v1",
                        }
                    },
                }
            ]
        },
    )
    assert report.refused
    assert f"{subject}: it answers no item of the leaf's worklist" in report.render()
