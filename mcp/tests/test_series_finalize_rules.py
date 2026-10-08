"""What finalizing a master decides again, reads, and refuses by name.

Finalization completes a master's document and its sprint row. It decides once more that no
abandoned row has landed, it needs no other member of the sprint to be readable, it does not
finalize a task whose own document cannot be read, and it refuses a master that several sprints
command with the edit that works on each of them.
"""

from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
)
from agents_remember.application.worktree_tools import lifecycle_finalize_task_tool
from agents_remember.mcp.tools.task_doc import task_doc_payload
from agents_remember.tasks import SubTaskRef, TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees.modules.finalize import FinalizeArgs, finalize_result
from agents_remember.worktrees.task_resolver import leaf_enclosure_path
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract
from test_master_retirement import World
from test_master_retirement_state_machine import _damages
from test_task_execution_topology import REPOSITORY, _master
from test_untyped_sprint_rows import SEAT_A, SeatWorld, _seat

pytestmark = pytest.mark.integration


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def _finalize(contract: WorktreeContract, *, dry_run: bool = False) -> dict[str, Any]:
    result = finalize_result(FinalizeArgs(contract_path=contract.contract_path, dry_run=dry_run))
    return cast("dict[str, Any]", result.payload)


def _blocked(world: World, contract: WorktreeContract) -> str:
    """The refusal of the dry run and of the real run, which agree and change nothing."""
    before, cells = world.snapshot(), contract.contract_path.read_bytes()
    said = []
    for dry_run in (True, False):
        payload = _finalize(contract, dry_run=dry_run)
        assert payload["state"] == "task-document-resolution-blocked", payload
        said.append(" ".join(cast("list[str]", payload["blockers"])))
        assert world.snapshot() == before and contract.contract_path.read_bytes() == cells
    assert said[0] == said[1]
    return said[0]


def _leaf_rows(world: World, contract: WorktreeContract, *, landed: bool) -> None:
    """A completed row and an abandoned row whose enclosure does or does not record a landing."""
    for leaf_id, status in (("L0", "Completed"), ("L1", "abandoned")):
        write_task_doc(
            contract.task_root,
            TaskDocument.model_validate(
                {
                    "id": leaf_id,
                    "slug": leaf_id.lower(),
                    "title": leaf_id,
                    "kind": "subTask",
                    "repo": REPOSITORY,
                    "createdAt": "2026-10-02T00:00:00+00:00",
                    "status": status,
                }
            ),
        )
    master = read_task_doc(contract.task_root / "task.json")
    rows = [
        SubTaskRef(number="L0", name="L0", file="l0.md", status="Completed"),
        SubTaskRef(number="L1", name="L1", file="l1.md", status="abandoned"),
    ]
    write_task_doc(contract.task_root, master.model_copy(update={"subTasks": rows}))
    child = replace(
        contract,
        kind="leaf",
        leaf_id="L1",
        contract_path=leaf_enclosure_path(contract.task_root, "L1"),
        parent_contract_path=contract.contract_path,
        parent_task_name=contract.task_name,
        code_source_branch=contract.code_work_branch,
        code_work_branch="leaf-l1",
        code_worktree=world.coord.parent / "wt-l1",
        integration_status="completed" if landed else "not-started",
        integrated_code_commit=world.base if landed else "",
    )
    write_contract(child.contract_path, child)


@pytest.mark.parametrize("nature", ["atomic", "organizational"])
def test_finalize_refuses_an_abandoned_row_that_landed_and_names_the_row(world, nature):
    """The row's label may have been set after the closeout looked, so finalize looks again."""
    contract = world.series("outside")
    write_task_doc(contract.task_root, _master(identity="outside", execution_nature=nature))
    _leaf_rows(world, contract, landed=True)
    said = _blocked(world, contract)
    enclosure = leaf_enclosure_path(contract.task_root, "L1")
    assert f"row 'L1' is abandoned but enclosure {enclosure} records completed integration" in said
    assert "task_doc set_subtask on the master" in said and "retry finalization" in said
    # The named action: the leaf landed, so its row is completed.
    task_doc_payload(
        world.cfg,
        TaskDocTarget(repo_id=REPOSITORY, task_name="outside"),
        operation="set_subtask",
        edit=TaskDocEdit(subtask={"number": "L1", "status": "Completed"}),
        call=TaskDocCall(),
    )
    assert _finalize(contract)["state"] == "finalized"
    assert read_task_doc(contract.task_root / "task.json").status == "Completed"


def test_finalize_completes_over_an_abandoned_row_that_did_not_land(world):
    contract = world.series("outside")
    write_task_doc(contract.task_root, _master(identity="outside", execution_nature="atomic"))
    _leaf_rows(world, contract, landed=False)
    assert _finalize(contract, dry_run=True)["state"] == "would-finalize"
    assert _finalize(contract)["state"] == "finalized"


def test_finalize_refuses_an_unreadable_enclosure_of_an_abandoned_row(world):
    contract = world.series("outside")
    write_task_doc(contract.task_root, _master(identity="outside", execution_nature="atomic"))
    _leaf_rows(world, contract, landed=False)
    enclosure = leaf_enclosure_path(contract.task_root, "L1")
    enclosure.write_text("not a contract\n")
    said = _blocked(world, contract)
    assert f"enclosure {enclosure} of abandoned row 'L1' cannot be read" in said
    assert "repair or remove that file, then retry finalization" in said


def test_another_member_that_cannot_be_read_does_not_refuse_and_is_reported_by_file(world):
    contract = world.series()
    other = world.tasks / "master-b" / "task.json"
    other.write_text("{broken")
    for dry_run in (True, False):
        payload = _finalize(contract, dry_run=dry_run)
        assert payload["state"] == ("would-finalize" if dry_run else "finalized"), payload
        sprint = payload["taskUpdates"]["sprint"]
        assert sprint["subtaskNumber"] == "0"
        (fact,) = sprint["linkageFacts"]
        assert fact["kind"] == "sprint-member-unreadable" and fact["entry"] == "master-b"
        assert fact["file"] == other.as_posix() and "cannot be read" in fact["detail"]
    assert world.sprint().subTasks[0].status == "Completed" and other.read_text() == "{broken"
    assert read_task_doc(world.tasks / "master-a" / "task.json").status == "Completed"


def test_a_seat_row_is_found_by_its_file_while_another_member_cannot_be_read(tmp_path):
    world = SeatWorld(tmp_path)
    _seat(world, SEAT_A, ["../master-a/task.json", "task.json"])
    data = world.sprint().model_dump(mode="json")
    data["subTasks"] = [
        SubTaskRef(number="S-M1", name="Manage master-a", file=f"{SEAT_A}.md").model_dump(),
        *data["subTasks"][1:],
    ]
    write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
    (world.tasks / "master-c" / "task.json").write_text("")
    payload = _finalize(world.series("master-a"))
    assert payload["state"] == "finalized", payload
    sprint = payload["taskUpdates"]["sprint"]
    assert sprint["subtaskNumber"] == "S-M1"
    assert [fact["entry"] for fact in sprint["linkageFacts"]] == ["master-c"]


def test_a_member_that_is_missing_still_refuses_while_another_cannot_be_read(world):
    """Only the unreadable member is set aside; every other fault of the linkage refuses."""
    contract = world.series()
    (world.tasks / "master-b" / "task.json").write_text("{broken")
    data = world.sprint().model_dump(mode="json")
    data["orchestrates"].append("ghost-master")
    write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
    said = _blocked(world, contract)
    assert "ghost-master" in said and "repair sprint linkage" in said


@pytest.mark.parametrize("alias", ["id", "title"])
def test_public_finalize_requires_canonical_identity_for_an_unreadable_member(world, alias):
    world.cfg.retirement = SimpleNamespace(auto_land_on_finalize=False)
    contract = world.series()
    other = world.tasks / "master-b" / "task.json"
    sibling = read_task_doc(other)
    data = world.sprint().model_dump(mode="json")
    entry = getattr(sibling, alias)
    data["orchestrates"][1] = entry
    write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
    world.validate()
    other.write_text("{broken")
    before = world.snapshot()
    nodes = world.sprint().executionGraph.model_dump()
    row = world.sprint().subTasks[1].model_dump()
    for dry_run in (True, False):
        payload = lifecycle_finalize_task_tool(
            world.cfg, str(contract.contract_path), dry_run=dry_run
        )
        if alias == "title":
            assert not payload["ok"] and payload["state"] == "task-document-resolution-blocked"
            text = str(payload)
            assert repr(entry) in text and "unresolved identity" in text
            assert "canonical folder in orchestrates" in text
            assert "live task folder is missing" not in text
            assert world.snapshot() == before
        else:
            assert payload["ok"] and payload["state"] == (
                "would-finalize" if dry_run else "finalized"
            ), payload
            (fact,) = payload["taskUpdates"]["sprint"]["linkageFacts"]
            assert fact["entry"] == "master-b" and fact["file"] == str(other)
        assert world.sprint().executionGraph.model_dump() == nodes
        assert world.sprint().subTasks[1].model_dump() == row
        assert world.sprint().orchestrates == data["orchestrates"]
        assert other.read_text() == "{broken"
    if alias == "title":
        # The existing admitted reference becomes explicit; no alias mapping or new field.
        data["orchestrates"][1] = "master-b"
        write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
        payload = lifecycle_finalize_task_tool(world.cfg, str(contract.contract_path))
        assert payload["ok"] and payload["state"] == "finalized", payload
        (fact,) = payload["taskUpdates"]["sprint"]["linkageFacts"]
        assert fact["file"] == str(other) and other.read_text() == "{broken"


def test_public_finalize_keeps_a_readable_admitted_title_member(world):
    world.cfg.retirement = SimpleNamespace(auto_land_on_finalize=False)
    contract = world.series()
    other = world.tasks / "master-b" / "task.json"
    data = world.sprint().model_dump(mode="json")
    data["orchestrates"][1] = read_task_doc(other).title
    write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
    world.validate()
    other_bytes = other.read_bytes()
    payload = lifecycle_finalize_task_tool(world.cfg, str(contract.contract_path))
    assert payload["ok"] and payload["state"] == "finalized", payload
    assert other.read_bytes() == other_bytes
    assert world.sprint().orchestrates == data["orchestrates"]


def test_public_finalize_refuses_a_missing_admitted_canonical_member(world):
    contract = world.series()
    (world.tasks / "master-b" / "task.json").unlink()
    said = _blocked(world, contract)
    assert "master-b" in said and "repair sprint linkage" in said


def test_public_finalize_does_not_hide_duplicate_typed_rows_for_an_unreadable_member(world):
    contract = world.series()
    data = world.sprint().model_dump(mode="json")
    duplicate = dict(data["subTasks"][1], number="duplicate-b")
    data["subTasks"].append(duplicate)
    write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
    (world.tasks / "master-b" / "task.json").write_text("{broken")
    said = _blocked(world, contract)
    assert "same canonical master reference" in said and "repair sprint linkage" in said


def test_public_finalize_does_not_hide_a_foreign_typed_ref_with_the_same_unreadable_path(world):
    contract = world.series()
    data = world.sprint().model_dump(mode="json")
    data["subTasks"][1]["masterRef"]["repository"] = "other-repository"
    write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
    (world.tasks / "master-b" / "task.json").write_text("{broken")
    said = _blocked(world, contract)
    assert "task-sprint-linkage-cross-repo" in said and "other-repository" in said


def test_a_task_whose_own_document_cannot_be_read_is_not_finalized_and_the_file_is_named(world):
    """Its kind cannot be known. Earlier builds finalized it as an ordinary standalone task."""
    contract = world.series("plain")
    plain = TaskDocument(
        id="PLAIN",
        slug="plain",
        title="Plain",
        kind="light",
        repo=REPOSITORY,
        createdAt="2026-10-02T00:00:00+00:00",
    )
    write_task_doc(contract.task_root, plain)
    document = contract.task_root / "task.json"
    original, mode = document.read_bytes(), document.stat().st_mode
    for damage, content in _damages(original + b'"orchestrates"').items():
        if content is None and os.geteuid() == 0:
            continue
        if content is None:
            document.chmod(0)
        else:
            document.write_bytes(content)
        try:
            payload = _finalize(contract, dry_run=True)
            assert payload["state"] == "task-document-resolution-blocked", (damage, payload)
            said = " ".join(cast("list[str]", payload["blockers"]))
            assert f"the task's own document {document} cannot be read" in said, (damage, said)
            assert "it is not known whether it is a master" in said, (damage, said)
            assert "restore or repair that file and retry finalization" in said
        finally:
            document.chmod(mode)
            document.write_bytes(original)
    assert _finalize(contract)["state"] == "finalized"


@pytest.mark.parametrize("shape", ["directory", "dangling-symlink"])
def test_public_finalize_refuses_existing_unreadable_own_document_objects(world, shape):
    world.cfg.retirement = SimpleNamespace(auto_land_on_finalize=False)
    contract = world.series("outside")
    write_task_doc(contract.task_root, _master(identity="outside", execution_nature="atomic"))
    document = contract.task_root / "task.json"
    document.unlink()
    if shape == "directory":
        document.mkdir()
        (document / "retained.txt").write_text("retain this object")
    else:
        document.symlink_to("missing-own-document.json")
    before, cells, inode = (
        world.snapshot(),
        contract.contract_path.read_bytes(),
        document.lstat().st_ino,
    )
    for dry_run in (True, False):
        payload = lifecycle_finalize_task_tool(
            world.cfg, str(contract.contract_path), dry_run=dry_run
        )
        assert not payload["ok"] and payload["state"] == "task-document-resolution-blocked"
        text = str(payload)
        assert f"the task's own document {document} cannot be read" in text
        assert "restore or repair that file and retry finalization" in text
        assert world.snapshot() == before and contract.contract_path.read_bytes() == cells
        assert document.lstat().st_ino == inode
        if shape == "dangling-symlink":
            assert document.readlink().as_posix() == "missing-own-document.json"


def test_public_finalize_preserves_a_genuinely_absent_ordinary_document(world):
    world.cfg.retirement = SimpleNamespace(auto_land_on_finalize=False)
    contract = world.series("plain")
    document = contract.task_root / "task.json"
    assert not document.exists() and not document.is_symlink()
    for dry_run in (True, False):
        payload = lifecycle_finalize_task_tool(
            world.cfg, str(contract.contract_path), dry_run=dry_run
        )
        assert payload["ok"] and payload["state"] == (
            "would-finalize" if dry_run else "finalized"
        ), payload
        assert not document.exists() and not document.is_symlink()


def test_finalize_refuses_a_master_several_sprints_command_and_names_the_edit_for_each(world):
    contract = world.series()
    write_task_doc(
        world.tasks / "master-z", _master(identity="master-z", execution_nature="atomic")
    )
    write_task_doc(
        world.tasks / "sprint2",
        _master(identity="sprint2", orchestrates=["master-z", "master-a"]).model_copy(
            update={"integrationBranch": "super"}
        ),
    )
    said = _blocked(world, contract)
    assert "commanded by several sprints" in said
    for sprint in ("sprint", "sprint2"):
        assert (world.tasks / sprint / "task.json").as_posix() in said
    assert (
        "on sprint agents-remember/sprint/task.json: task_doc.detach_master with "
        "fields={masterRef:agents-remember/master-a/task.json}" in said
    )
    assert (
        "on sprint agents-remember/sprint2/task.json, which holds no typed row for it: "
        'task_doc.set_field with fields={orchestrates:["master-z"]}' in said
    )
    task_doc_payload(
        world.cfg,
        TaskDocTarget(repo_id=REPOSITORY, task_name="sprint2"),
        operation="set_field",
        edit=TaskDocEdit(fields={"orchestrates": ["master-z"]}),
        call=TaskDocCall(),
    )
    assert _finalize(contract)["state"] == "finalized"
    assert world.sprint().subTasks[0].status == "Completed"
