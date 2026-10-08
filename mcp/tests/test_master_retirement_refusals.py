"""Refusals of the retire operation: each names its file or its action, and the action works.

A request addressed to a document that cannot be read is refused by the file's name. A refusal
that tells the operator to take a master out of a sprint names the edit that works on that
sprint: ``detach_master`` where a typed row stands for the master, the edit of ``orchestrates``
where none does. Every test that sees such a refusal carries the named edit out and repeats the
request. A refused request leaves nothing behind, a directory included, and a master has one
retirement record at one place.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
)
from agents_remember.application.task_docs.task_retirement_records import proof_path
from agents_remember.mcp.tools.task_doc import task_doc_payload
from agents_remember.tasks import SubTaskRef, TaskDocument, read_task_doc, write_task_doc
from test_master_retirement import World
from test_master_retirement_state_machine import Case, _damages, _kill, _refused_without_change
from test_standalone_master_retirement import REASON, lone, retire_lone
from test_task_execution_topology import REPOSITORY, _master

pytestmark = pytest.mark.integration


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


@pytest.mark.parametrize("shape", ["directory", "dangling-symlink", "malformed-file"])
def test_public_retirement_refuses_existing_unreadable_canonical_proof_objects(world, shape):
    contract, ref = lone(world)
    stored = proof_path(contract.task_root)
    stored.parent.mkdir(parents=True, exist_ok=True)
    if shape == "directory":
        stored.mkdir()
        (stored / "retained.txt").write_text("retain this proof object")
    elif shape == "dangling-symlink":
        stored.symlink_to("missing-proof.json")
    else:
        stored.write_text("{unreadable retained reason")
    before, inode = world.snapshot(), stored.lstat().st_ino
    for dry_run in (True, False):
        said = _refused(
            lambda dry_run=dry_run: retire_lone(
                world, "outside", ref, dry_run=dry_run, reason="another reason"
            )
        )
        assert f"retirement proof {stored} is unreadable" in said
        assert "restore the admitted proof before retrying task_doc.retire_master" in said
        assert contract.task_root.is_dir() and not (world.tasks / "0_archive/outside").exists()
        assert world.snapshot() == before and stored.lstat().st_ino == inode
        if shape == "dangling-symlink":
            assert stored.readlink().as_posix() == "missing-proof.json"


def _edit(world: World, sprint: str, operation: str, **fields: Any) -> dict[str, Any]:
    return task_doc_payload(
        world.cfg,
        TaskDocTarget(repo_id=REPOSITORY, task_name=sprint),
        operation=operation,
        edit=TaskDocEdit(fields=fields),
        call=TaskDocCall(),
    )


def _second_sprint(world: World, *commanded: str, name: str = "sprint2") -> None:
    """A graph-less sprint that commands its masters without a typed row for any of them."""
    write_task_doc(
        world.tasks / name,
        _master(identity=name, orchestrates=list(commanded)).model_copy(
            update={"integrationBranch": "super"}
        ),
    )


def _graphless(world: World) -> None:
    data = world.sprint().model_dump(mode="json")
    data.update(executionGraph=None, sections=[], integrationBranch="super")
    write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))


def _refused(call) -> str:
    with pytest.raises(ValueError) as refused:
        call()
    return str(refused.value)


def _carry_out_removals(world: World, refusal: str) -> list[str]:
    """Perform every membership edit a refusal names, with the fields it writes."""
    done = []
    typed = r"on sprint agents-remember/(\S+)/task\.json: task_doc\.detach_master with "
    for sprint, master in re.findall(
        typed + r"fields=\{masterRef:agents-remember/(\S+)\}", refusal
    ):
        _edit(
            world,
            sprint,
            "detach_master",
            masterRef={"repository": REPOSITORY, "path": master},
        )
        done.append(f"detach_master on {sprint}")
    untyped = (
        r"on sprint agents-remember/(\S+)/task\.json, which holds no typed row for it: "
        r"task_doc\.set_field with fields=\{orchestrates:(\[[^\]]*\])(, executionGraph:<[^>]*>)?\}"
    )
    for sprint, remaining, graph in re.findall(untyped, refusal):
        fields: dict[str, Any] = {"orchestrates": json.loads(remaining)}
        if graph:
            # "the graph as it is, without the node of <master> and without the edges that touch it"
            master = re.search(r"without the node of agents-remember/(\S+) and", graph)
            assert master is not None, graph
            current = read_task_doc(world.tasks / sprint / "task.json").model_dump(mode="json")
            gone = {"repository": REPOSITORY, "path": master.group(1)}
            fields["executionGraph"] = {
                "nodes": [node for node in current["executionGraph"]["nodes"] if node != gone],
                "edges": [
                    edge
                    for edge in current["executionGraph"]["edges"]
                    if gone not in (edge["predecessor"], edge["successor"])
                ],
            }
        _edit(world, sprint, "set_field", **fields)
        done.append(f"set_field on {sprint}")
    return done


# -- a request addressed to a document that cannot be read ----------------------------------------


@pytest.mark.parametrize("sprint", [True, False], ids=["with-a-sprint", "without-a-sprint"])
def test_a_master_document_that_cannot_be_read_refuses_every_request_and_is_named(world, sprint):
    case = Case(world, sprint=sprint)
    document = case.live / "task.json"
    original, mode = document.read_bytes(), document.stat().st_mode
    damages = {
        **_damages(original + b'"orchestrates"'),
        "cut": original[: len(original) // 2],
    }
    before = world.snapshot()
    for damage, content in damages.items():
        if content is None and os.geteuid() == 0:
            continue
        if content is None:
            document.chmod(0)
        else:
            document.write_bytes(content)
        try:
            for call in (case.retire, case.elsewhere):
                for dry_run in (True, False):
                    said = _refused(lambda call=call, dry_run=dry_run: call(dry_run=dry_run))
                    assert document.as_posix() in said, (damage, said)
                    assert "task_doc.retire_master" in said, (damage, said)
        finally:
            document.chmod(mode)
            document.write_bytes(original)
        assert world.snapshot() == before, damage
    assert case.retire(dry_run=True)["state"] == "would-retire"


@pytest.mark.parametrize("field", ["orchestrates", "subTasks"])
def test_a_task_document_with_a_malformed_list_field_refuses_and_is_named(world, field):
    document = world.tasks / "unrelated" / "notes" / "task.json"
    document.parent.mkdir(parents=True)
    document.write_text(json.dumps({"kind": "master", field: "not a list"}))
    before = world.snapshot()
    for dry_run in (True, False):
        said = _refused(lambda dry_run=dry_run: world.retire(dry_run=dry_run))
        assert str(document) in said and "Repair or remove that file" in said
        assert world.snapshot() == before
    document.unlink()
    assert world.retire(dry_run=True)["state"] == "would-retire"


def test_a_request_addressed_to_an_unreadable_document_names_it_before_anything_else(world):
    """The addressed document is named even where nothing else about the request is right."""
    sprint = world.tasks / "sprint" / "task.json"
    intact = sprint.read_bytes()
    sprint.write_bytes(b"")
    ghost = {**world.fields, "masterRef": {"repository": REPOSITORY, "path": "ghost/task.json"}}
    for fields in (world.fields, ghost):
        said = _refused(lambda fields=fields: world.retire(fields=fields, dry_run=True))
        assert f"was called on {sprint.as_posix()}, which cannot be opened, decoded" in said
        assert "Repair that file, then repeat task_doc.retire_master" in said
    sprint.write_bytes(intact)
    assert world.retire(dry_run=True)["state"] == "would-retire"


@pytest.mark.skipif(os.geteuid() == 0, reason="nothing is closed by mode to the superuser")
def test_a_task_folder_that_cannot_be_looked_into_stops_a_retirement_and_is_named(world):
    """The sprint that commands the master may be in the folder nobody can look into."""
    case = Case(world, sprint=False)
    _second_sprint(world, "outside")
    closed = world.tasks / "sprint2"
    before = world.snapshot()
    closed.chmod(0)
    try:
        for dry_run in (True, False):
            said = _refused(lambda dry_run=dry_run: case.retire(dry_run=dry_run))
            assert (closed / "task.json").as_posix() in said, said
            assert "cannot be opened, decoded or parsed" in said
    finally:
        closed.chmod(0o755)
    assert world.snapshot() == before and case.live.is_dir()
    # With the folder open again the sprint in it is seen, and the request is sent there.
    assert "commanded by sprint agents-remember/sprint2/task.json" in _refused(case.retire)


# -- the edit that takes a master out of a sprint -------------------------------------------------


@pytest.mark.parametrize("graph", [False, True], ids=["graph-less", "with-a-graph"])
def test_a_master_put_back_without_a_typed_row_is_removed_by_the_edit_the_refusal_names(
    world, graph
):
    """The recording sprint commands the retired master again, by ``orchestrates`` alone."""
    if not graph:
        _graphless(world)
    else:
        data = world.sprint().model_dump(mode="json")
        data["integrationBranch"] = "super"
        write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
    case = Case(world, sprint=True)
    # No successor edge is left to affirm, so the request is the same with a graph and without.
    assert world.retire()["ok"]
    case.archive.rename(case.live)
    fields: dict[str, Any] = {"orchestrates": [*world.sprint().orchestrates, "master-a"]}
    if graph:
        current = world.sprint().model_dump(mode="json")["executionGraph"]
        current["nodes"].append(world.refs[0].model_dump())
        fields["executionGraph"] = current
    _edit(world, "sprint", "set_field", **fields)
    assert "master-a" in world.sprint().orchestrates
    assert not any(row.masterRef == world.refs[0] for row in world.sprint().subTasks)
    refusals = {
        _refused(lambda dry_run=dry_run: world.retire(dry_run=dry_run)) for dry_run in (True, False)
    }
    (refusal,) = refusals
    assert "reattached after retirement" in refusal and "detach_master" not in refusal
    assert 'task_doc.set_field with fields={orchestrates:["master-b", "master-c"]' in refusal
    assert ("executionGraph:<" in refusal) == graph
    # What the refusal used to name does not work here, which is why it is not named.
    assert "no typed row links" in _refused(
        lambda: _edit(world, "sprint", "detach_master", masterRef=world.refs[0].model_dump())
    )
    assert _carry_out_removals(world, refusal) == ["set_field on sprint"]
    done = world.retire()
    assert done["ok"] and done["retirementResumed"] and case.archive.is_dir()
    assert world.sprint().orchestrates == ["master-b", "master-c"]
    assert not graph or len(world.validate()) == 2


def test_a_lone_masters_record_names_the_edit_that_works_on_the_sprint_that_took_it(world):
    case = Case(world, sprint=False)
    _kill(case, "archive")
    write_task_doc(
        world.tasks / "master-z", _master(identity="master-z", execution_nature="atomic")
    )
    _second_sprint(world, "master-z", "outside")
    stored = proof_path(case.live)
    refusal = _refused(case.retire)
    assert f"{stored}" in refusal and "sprint2/task.json commands it now" in refusal
    assert 'task_doc.set_field with fields={orchestrates:["master-z"]}' in refusal
    assert "detach_master" not in refusal
    assert _carry_out_removals(world, refusal) == ["set_field on sprint2"]
    done = case.retire()
    assert done["ok"] and done["retirementResumed"] and case.archive.is_dir()


def test_several_commanding_sprints_are_each_named_with_the_edit_that_works_there(world):
    """One sprint holds a typed row, one commands by name only, one commands nothing else."""
    write_task_doc(
        world.tasks / "master-z", _master(identity="master-z", execution_nature="atomic")
    )
    _second_sprint(world, "master-z", "master-a")
    _second_sprint(world, "master-a", name="sprint3")
    before = world.snapshot()
    refusal = _refused(world.retire)
    assert _refused(lambda: world.retire(dry_run=True)) == refusal and world.snapshot() == before
    assert "commanded by several sprints" in refusal
    assert (
        "on sprint agents-remember/sprint/task.json: task_doc.detach_master with "
        "fields={masterRef:agents-remember/master-a/task.json}" in refusal
    )
    assert (
        "on sprint agents-remember/sprint2/task.json, which holds no typed row for it: "
        'task_doc.set_field with fields={orchestrates:["master-z"]}' in refusal
    )
    assert (
        "on sprint agents-remember/sprint3/task.json, which holds no typed row for it and "
        "commands no other master: this build has no edit that takes a sprint's last master "
        "away, so keep this sprint as the one that commands it" in refusal
    )
    # The edit that is not offered is refused by the build, which is why it is not offered.
    assert "integrationBranch belongs only to an orchestration sprint" in _refused(
        lambda: _edit(world, "sprint3", "set_field", orchestrates=[])
    )
    # Removing it from all but one: the two named edits, after the edges the detach asks for.
    said = _refused(lambda: _carry_out_removals(world, refusal))
    assert "edge(s) still touch" in said and "author_execution_graph" in said
    _edit(
        world,
        "sprint",
        "author_execution_graph",
        mutations=[
            {"op": "remove_edge", **{key: edge[key] for key in edge if key != "reason"}}
            for edge in world.edges
        ],
    )
    assert _carry_out_removals(world, refusal) == [
        "detach_master on sprint",
        "set_field on sprint2",
    ]
    fields = {"masterRef": world.refs[0].model_dump(), "reason": "retired through sprint3"}
    done = task_doc_payload(
        world.cfg,
        TaskDocTarget(repo_id=REPOSITORY, task_name="sprint3"),
        operation="retire_master",
        edit=TaskDocEdit(fields=fields),
        call=TaskDocCall(),
    )
    assert done["ok"] and (world.tasks / "0_archive" / "master-a").is_dir()


def test_the_named_edit_says_that_the_sprint_must_declare_its_integration_branch_with_it(world):
    """An edit of a sprint's membership is refused while the sprint declares no integration branch."""
    write_task_doc(
        world.tasks / "master-z", _master(identity="master-z", execution_nature="atomic")
    )
    write_task_doc(
        world.tasks / "sprint2", _master(identity="sprint2", orchestrates=["master-z", "master-a"])
    )
    refusal = _refused(world.retire)
    assert (
        'task_doc.set_field with fields={orchestrates:["master-z"], integrationBranch:<the '
        "sprint's integration branch, which this edit requires and the sprint does not declare "
        "yet>}" in refusal
    )
    assert "must declare integrationBranch" in _refused(
        lambda: _edit(world, "sprint2", "set_field", orchestrates=["master-z"])
    )
    _edit(world, "sprint2", "set_field", orchestrates=["master-z"], integrationBranch="super")
    assert world.retire(dry_run=True)["state"] == "would-retire"


def test_an_entry_that_names_two_masters_is_refused_with_an_edit_that_works(world):
    """No typed row is involved in an ambiguous entry, so ``detach_master`` is not its repair."""
    other = world.tasks / "master-b"
    write_task_doc(
        other, read_task_doc(other / "task.json").model_copy(update={"title": "master-a"})
    )
    refusal = _refused(lambda: world.retire(dry_run=True))
    assert "orchestrates entry 'master-a' of sprint agents-remember/sprint/task.json" in refusal
    assert "resolves to 2 masters" in refusal
    for named in ("agents-remember/master-a/task.json", "agents-remember/master-b/task.json"):
        assert named in refusal
    assert "detach_master" not in refusal and "task_doc.set_field" in refusal
    # The named edit: the master that is not meant gets a title of its own.
    task_doc_payload(
        world.cfg,
        TaskDocTarget(repo_id=REPOSITORY, task_name="master-b"),
        operation="set_field",
        edit=TaskDocEdit(fields={"title": "Human master-b"}),
        call=TaskDocCall(),
    )
    assert world.retire(dry_run=True)["state"] == "would-retire"


# -- a refused request leaves nothing behind ------------------------------------------------------


def _directories(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()}


@pytest.mark.parametrize("existing", ["nothing", "notes", "notes/reports"])
def test_a_refused_first_phase_leaves_no_directory_it_made(world, existing):
    contract, ref = lone(world)
    if existing != "nothing":
        (contract.task_root / existing).mkdir(parents=True)
    files, directories = world.snapshot(), _directories(world.tasks)
    with (
        patch.object(Path, "rename", side_effect=OSError("disk refused rename")),
        pytest.raises(ValueError, match=r"task folder and proof were restored"),
    ):
        retire_lone(world, "outside", ref)
    assert world.snapshot() == files and _directories(world.tasks) == directories


# -- one record, at one place ---------------------------------------------------------------------


@pytest.mark.parametrize("second", ["a proof file in its folder", "a row on another sprint"])
def test_a_master_with_two_retirement_records_is_refused_and_both_are_named(world, second):
    case = Case(world, sprint=True)
    _kill(case, "archive")
    row = world.sprint().subTasks[0]
    assert row.retirement is not None
    if second == "a proof file in its folder":
        stored = proof_path(case.live)
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_text(row.retirement.model_dump_json(indent=2) + "\n")
        other = f"the proof file {stored}"
    else:
        other_row = row.model_copy(update={"number": "R"}).model_dump(
            mode="json", exclude_none=True
        )
        data = _master(identity="sprint2").model_dump(mode="json")
        data["subTasks"] = [other_row]
        (world.tasks / "sprint2").mkdir()
        (world.tasks / "sprint2" / "task.json").write_text(json.dumps(data))
        other = "row 'R' of sprint agents-remember/sprint2/task.json"
    for call in (case.retire, lambda: case.retire(dry_run=True), case.elsewhere):
        before = world.snapshot()
        said = _refused(call)
        assert "has more than one retirement record" in said, said
        assert "row '0' of sprint agents-remember/sprint/task.json" in said and other in said
        assert "remove the surplus record by hand" in said and world.snapshot() == before
    assert case.live.is_dir() and not case.archive.exists()


def test_a_new_retirement_row_never_takes_a_number_that_is_in_use(world):
    """A master without a row gains one: its id, its folder name, or the next free suffix."""
    data = world.sprint().model_dump(mode="json")
    taken = [
        SubTaskRef(number=number, name=f"Something else {number}").model_dump()
        for number in ("master-b", "master-b-2")
    ]
    data["subTasks"] = [data["subTasks"][0], *taken, data["subTasks"][2]]
    write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
    fields = {"masterRef": world.refs[1].model_dump(), "reason": "No row stood for it."}
    edge = world.edges[1]
    preview = world.retire(fields=fields, dry_run=True)
    assert preview["retirementRow"]["number"] == "master-b-3"
    done = world.retire(fields=fields)
    assert done["ok"] and done["retirementRow"]["number"] == "master-b-3" and edge
    numbers = [row.number for row in world.sprint().subTasks]
    assert numbers == ["0", "master-b", "master-b-2", "2", "master-b-3"]
    assert len(set(numbers)) == len(numbers)
    assert [row.retirement is not None for row in world.sprint().subTasks] == [
        False,
        False,
        False,
        False,
        True,
    ]


@pytest.mark.parametrize("sprint", [True, False], ids=["with-a-sprint", "without-a-sprint"])
def test_a_recorded_master_whose_folder_is_at_both_places_is_refused(world, sprint):
    case = Case(world, sprint=sprint)
    assert case.retire()["ok"]
    shutil.copytree(case.archive, case.live)
    wanted = r"must exist at exactly one of" if sprint else r"exists at both"
    for dry_run in (True, False):
        _refused_without_change(case, lambda dry_run=dry_run: case.retire(dry_run=dry_run), wanted)
    assert case.live.is_dir() and case.archive.is_dir()
    shutil.rmtree(case.live)
    again = case.retire()
    assert again["ok"] and again["retirementState"] == "hook-finished"


# -- what an interrupted atomic write leaves is no record -----------------------------------------


def _dead_process_id() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _leftover(target: Path, content: str) -> Path:
    """The private temporary file a writer of ``target`` leaves when it is killed."""
    target.parent.mkdir(parents=True, exist_ok=True)
    left = target.with_name(f".{target.name}.{_dead_process_id()}.{'a' * 32}.tmp")
    left.write_text(content)
    return left


@pytest.mark.parametrize("sprint", [True, False], ids=["with-a-sprint", "without-a-sprint"])
def test_what_an_interrupted_write_left_is_no_record_and_goes_with_the_next_write(world, sprint):
    case = Case(world, sprint=sprint)
    # A whole record of another request, as a death inside the write of the record leaves it.
    other = case.retire(dry_run=True, reason="the request that died")
    if sprint:
        retired = world.sprint().model_dump(mode="json")
        retired["subTasks"][0] = other["retirementRow"]
        retired["orchestrates"] = ["master-b", "master-c"]
        left = _leftover(world.tasks / "sprint" / "task.json", json.dumps(retired))
    else:
        left = _leftover(proof_path(case.live), json.dumps(other["retirementProof"]))
    receipt = _leftover(
        case.live / "notes" / "reports" / "review-artifact-cleanup.json",
        json.dumps({"attempt": 7, "state": "deleted", "failures": []}),
    )
    preview = case.retire(dry_run=True)
    assert preview["retirementState"] == "not-recorded" and not preview["retirementResumed"]
    assert preview["taskArchive"]["reviewArtifacts"]["attempt"] == 1
    assert case.records() == []
    done = case.retire()
    assert done["ok"] and not done["retirementResumed"] and len(case.records()) == 1
    reason = REASON if not sprint else world.fields["reason"]
    recorded = done["retirementRow"]["retirement"] if sprint else done["retirementProof"]
    assert recorded["reason"] == reason
    assert [entry["attempt"] for entry in case.receipts()] == [1]
    moved = case.archive / receipt.relative_to(case.live)
    assert not left.exists() and not moved.exists() and not receipt.exists()
    if not sprint:
        assert not (case.archive / left.relative_to(case.live)).exists()
