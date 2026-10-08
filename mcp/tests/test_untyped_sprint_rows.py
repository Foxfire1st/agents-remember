"""A sprint that commands a master without a typed row blocks neither finalize nor retire.

The fixture reproduces the rows of the real sprint: a master behind a legacy seat row whose seat
document names it, a master behind a seat row whose seat document names nobody (so the sprint
holds no row that stands for it), and a master with a typed row. The linkage report states the
first two as facts; finalize and retire work on all three.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
)
from agents_remember.mcp.tools.task_doc import task_doc_payload
from agents_remember.tasks import SubTaskRef, TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees.modules.finalize import FinalizeArgs, finalize_result
from agents_remember.worktrees.worktree_contract import WorktreeContract
from test_master_retirement import World
from test_task_execution_topology import REPOSITORY

pytestmark = pytest.mark.integration

SEAT_A, SEAT_B = "01_manage-master-a", "02_manage-master-b"


def _seat(world: World, slug: str, references: list[str]) -> None:
    write_task_doc(
        world.tasks / "sprint",
        TaskDocument(
            id=f"SPRINT-{slug[:2]}",
            slug=slug,
            title=f"Manager seat {slug}",
            kind="subTask",
            repo=REPOSITORY,
            createdAt="2026-07-27T13:20:00+00:00",
            references=references,
        ),
    )


class SeatWorld(World):
    """The sprint of :class:`World` with the real sprint's rows, and one contract per master."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.contracts: dict[str, WorktreeContract] = {}


@pytest.fixture
def world(tmp_path):
    """master-a behind a seat row that names it, master-b behind one that names nobody, master-c typed."""
    made = SeatWorld(tmp_path)
    _seat(made, SEAT_A, ["../master-a/task.json", "task.json"])
    _seat(made, SEAT_B, ["task.json"])
    data = made.sprint().model_dump(mode="json")
    typed = data["subTasks"][2]
    data["subTasks"] = [
        SubTaskRef(number="S-A0", name="Architect seat", file="00_architect-seat.md").model_dump(),
        SubTaskRef(
            number="S-M1", name="Manage master-a", status="inProgress", file=f"{SEAT_A}.md"
        ).model_dump(),
        SubTaskRef(
            number="S-M2", name="Manage master-b", status="inProgress", file=f"{SEAT_B}.md"
        ).model_dump(),
        typed,
    ]
    write_task_doc(made.tasks / "sprint", TaskDocument.model_validate(data))
    return made


def _facts(world: World) -> list[dict[str, Any]]:
    report = task_doc_payload(
        world.cfg, world.target, operation="linkage_report", edit=TaskDocEdit(), call=TaskDocCall()
    )
    return cast("list[dict[str, Any]]", report["linkageFacts"])


def _row(world: World, number: str) -> SubTaskRef:
    return next(row for row in world.sprint().subTasks if row.number == number)


def _finalize(world: SeatWorld, name: str, *, dry_run: bool = False) -> dict[str, Any]:
    if name not in world.contracts:
        world.contracts[name] = world.series(name)
    contract = world.contracts[name]
    result = finalize_result(FinalizeArgs(contract_path=contract.contract_path, dry_run=dry_run))
    return cast("dict[str, Any]", result.payload)


def test_the_fixture_has_the_real_sprints_linkage_facts_and_validates(world):
    kinds = sorted((fact["kind"], fact.get("master", fact.get("number"))) for fact in _facts(world))
    assert kinds == [
        ("membership-without-row", "agents-remember/master-b/task.json"),
        ("seat-doc-row", "agents-remember/master-a/task.json"),
        ("seat-doc-row-unresolved", "S-A0"),
        ("seat-doc-row-unresolved", "S-M2"),
        ("slug-only-membership", "agents-remember/master-a/task.json"),
    ]
    assert len(world.validate()) == 3


def test_finalize_completes_the_one_correlated_seat_row(world):
    world.contracts["master-a"] = world.series("master-a")
    before = world.snapshot()
    preview = _finalize(world, "master-a", dry_run=True)
    assert preview["state"] == "would-finalize" and world.snapshot() == before
    assert preview["taskUpdates"]["sprint"]["subtaskNumber"] == "S-M1"
    done = _finalize(world, "master-a")
    assert done["state"] == "finalized", done
    assert done["taskUpdates"]["sprint"] == {
        "state": "updated",
        "docPath": str(world.tasks / "sprint" / "task.json"),
        "status": world.sprint().status,
        "renderedPath": str(world.tasks / "sprint" / "task.md"),
        "subtaskNumber": "S-M1",
    }
    assert _row(world, "S-M1").status == "Completed" and _row(world, "S-M1").file == f"{SEAT_A}.md"
    assert _row(world, "S-M2").status == "inProgress"
    assert done["taskArchive"]["reason"] == "sprint-commands-master"
    assert (world.tasks / "master-a").is_dir() and len(world.validate()) == 3


@pytest.mark.parametrize("shape", ["no-row", "two-correlating-rows"])
def test_finalize_completes_the_master_and_reports_the_sprint_row_as_skipped(world, shape):
    name, fact = "master-b", {"kind": "membership-without-row"}
    if shape == "two-correlating-rows":
        name = "master-a"
        _seat(world, "03_manage-master-a-again", ["../master-a/task.json"])
        data = world.sprint().model_dump(mode="json")
        data["subTasks"].append(
            SubTaskRef(
                number="S-M3", name="Second seat", file="03_manage-master-a-again.md"
            ).model_dump()
        )
        write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
        fact = {"kind": "seat-doc-row-ambiguous", "rows": ["S-M1", "S-M3"]}
    fact["master"] = f"agents-remember/{name}/task.json"
    sprint_pair = {path: path.read_bytes() for path in (world.tasks / "sprint").glob("task.*")}
    for dry_run in (True, False):
        done = _finalize(world, name, dry_run=dry_run)
        assert done["state"] == ("would-finalize" if dry_run else "finalized"), done
        sprint = done["taskUpdates"]["sprint"]
        assert sprint["state"] == "skipped" and sprint["linkageFact"] == fact
        assert sprint["docPath"] == str(world.tasks / "sprint" / "task.json")
        assert "attach_master" not in str(done)
        assert all(path.read_bytes() == content for path, content in sprint_pair.items())
    master = read_task_doc(world.tasks / name / "task.json")
    assert master.status == "Completed" and master.decisions[0].decision.startswith("Finalize")
    assert done["taskArchive"]["reason"] == "sprint-commands-master"


def test_retire_replaces_the_correlated_seat_row_and_adds_a_row_where_none_stands(world):
    before = world.snapshot()
    preview = world.retire(dry_run=True)
    assert preview["state"] == "would-retire" and world.snapshot() == before
    assert preview["replacedLegacyRow"] == {"number": "S-M1", "file": f"{SEAT_A}.md"}
    assert "record the retirement on row 'S-M1'" in preview["wouldChange"][0]
    done = world.retire()
    assert done["ok"] and done["removedOrchestrates"] == ["master-a"]
    assert done["removedGraphNodes"] == 1 and len(done["removedEdges"]) == 2
    rows = world.sprint().subTasks
    assert [row.number for row in rows] == ["S-A0", "S-M1", "S-M2", "2"]
    assert rows[1].name == "Manage master-a" and rows[1].retirement
    # The row that took the seat row's place keeps its file cell: the seat's documents stay
    # reachable from the sprint, and the linkage report says whose they are.
    assert rows[1].file == f"{SEAT_A}.md" and rows[1].masterRef is None
    assert (world.tasks / "sprint" / f"{SEAT_A}.json").is_file()
    assert f"{SEAT_A}.md" in (world.tasks / "sprint" / "task.md").read_text()
    seat_facts = [fact for fact in _facts(world) if fact.get("number") == "S-M1"]
    assert seat_facts == [
        {
            "kind": "retired-master-seat-documents",
            "number": "S-M1",
            "file": f"{SEAT_A}.md",
            "master": "agents-remember/master-a/task.json",
            "documents": [f"{SEAT_A}.json", f"{SEAT_A}.md"],
        }
    ]
    assert (world.tasks / "0_archive" / "master-a").is_dir() and len(world.validate()) == 2
    again = world.retire()
    assert again["ok"] and again["retirementResumed"]
    assert again["replacedLegacyRow"] == preview["replacedLegacyRow"]
    # A master for which the sprint holds no row gains one plain row at the end.
    fields = {"masterRef": world.refs[1].model_dump(), "reason": "No row stood for it."}
    done = world.retire(fields=fields)
    assert done["ok"] and "replacedLegacyRow" not in done
    rows = world.sprint().subTasks
    assert [row.number for row in rows] == ["S-A0", "S-M1", "S-M2", "2", "master-b"]
    added = rows[4].retirement
    assert rows[4].name == "Human master-b" and added and added.reason == fields["reason"]
    assert rows[2].file == f"{SEAT_B}.md" and rows[2].retirement is None
    assert len(world.validate()) == 1 and world.sprint().orchestrates == ["master-c"]
    assert world.retire(fields=fields)["retirementResumed"]


def test_a_retired_masters_kept_seat_document_is_readable_and_its_writes_refuse(world):
    seat_path = world.tasks / "sprint" / f"{SEAT_A}.json"
    seat = read_task_doc(seat_path)
    write_task_doc(seat_path.parent, seat.model_copy(update={"id": "S-M1"}))
    assert world.retire()["ok"]
    target = TaskDocTarget(repo_id=REPOSITORY, task_name="sprint", slug=SEAT_A)
    read = task_doc_payload(
        world.cfg, target, operation="get", edit=TaskDocEdit(), call=TaskDocCall()
    )
    assert read["ok"] and read["docPath"] == str(seat_path)
    before = world.snapshot()
    for operation, fields in (
        ("set_status", {"status": "abandoned"}),
        ("set_field", {"title": "Changed"}),
    ):
        with pytest.raises(ValueError, match="the master is retired") as refused:
            task_doc_payload(
                world.cfg,
                target,
                operation=operation,
                edit=TaskDocEdit(fields=fields),
                call=TaskDocCall(),
            )
        assert "row 'S-M1'" in str(refused.value)
        assert world.snapshot() == before


def test_a_retirement_row_that_kept_a_seat_file_never_stands_for_a_master_again(world):
    """The row records a retirement. It is no seat row, also when its master is commanded again."""
    assert world.retire()["ok"]
    (world.tasks / "0_archive" / "master-a").rename(world.tasks / "master-a")
    sprint = world.sprint().model_dump(mode="json")
    graph = sprint["executionGraph"]
    graph["nodes"].append(world.refs[0].model_dump())
    task_doc_payload(
        world.cfg,
        world.target,
        operation="set_field",
        edit=TaskDocEdit(
            fields={
                "orchestrates": [*sprint["orchestrates"], "master-a"],
                "executionGraph": graph,
                "integrationBranch": "super",
            }
        ),
        call=TaskDocCall(),
    )
    facts = _facts(world)
    master = "agents-remember/master-a/task.json"
    assert {"kind": "membership-without-row", "master": master} in facts
    assert not any(fact["kind"] == "slug-only-membership" for fact in facts)
    assert [
        fact["number"] for fact in facts if fact["kind"] == "retired-master-seat-documents"
    ] == ["S-M1"]
    done = _finalize(world, "master-a")
    assert done["state"] == "finalized", done
    assert done["taskUpdates"]["sprint"]["state"] == "skipped"
    assert done["taskUpdates"]["sprint"]["linkageFact"] == {
        "kind": "membership-without-row",
        "master": master,
    }
    kept = _row(world, "S-M1")
    assert kept.retirement is not None and kept.status == "abandoned"


def test_a_legacy_commanded_master_called_on_itself_is_sent_to_its_sprint(world):
    before = world.snapshot()
    with pytest.raises(ValueError, match=r"commanded by sprint .*sprint/task.json.*on that sprint"):
        task_doc_payload(
            world.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="master-a"),
            operation="retire_master",
            edit=TaskDocEdit(fields=world.fields),
            call=TaskDocCall(),
        )
    assert world.snapshot() == before


def test_finalize_reads_only_the_documents_it_needs(world):
    """A broken task document elsewhere stops nothing; the commanding sprint's refuses by name."""
    elsewhere = world.tasks / "unrelated"
    elsewhere.mkdir()
    (elsewhere / "task.json").write_text("{broken")
    assert _finalize(world, "master-a", dry_run=True)["state"] == "would-finalize"
    assert _finalize(world, "master-c", dry_run=True)["taskArchive"]["reason"] == (
        "sprint-commands-master"
    )
    sprint = world.tasks / "sprint" / "task.json"
    intact = sprint.read_text()
    for broken in (intact[:-3], intact.replace('"kind": "master"', '"kind": "master", "x": 1', 1)):
        sprint.write_text(broken)
        before = world.snapshot()
        refused = _finalize(world, "master-a")
        assert refused["state"] == "task-document-resolution-blocked"
        assert str(sprint) in " ".join(refused["blockers"]) and world.snapshot() == before
    sprint.write_text(intact)
    assert _finalize(world, "master-a")["state"] == "finalized"
