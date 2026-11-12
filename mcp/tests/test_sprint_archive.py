"""Archiving a sprint as a whole (MIK-R82 rule 1): preconditions, dry-run exactness, staying masters."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from agents_remember.application.task_docs.task_doc_tools import (
    VALID_OPERATIONS,
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
)
from agents_remember.application.task_docs.task_sprint_linkage import (
    SPRINT_LINKAGE_OPERATIONS,
)
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.mcp.tools.task_doc import task_doc_payload
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import SubTaskRef, TaskDocument, read_task_doc, write_task_doc
from test_task_execution_topology import REPOSITORY, _config, _master
from test_task_sprint_linkage import _register_section
from test_worktree_support import git, init_repo

pytestmark = pytest.mark.integration

REASON = "The sprint completed; archiving it as a whole."


class ArchiveWorld:
    """A Completed sprint commanding two Completed masters, ready to archive."""

    def __init__(self, root: Path) -> None:
        self.coord = root / "coord"
        self.tasks = self.coord / "tasks" / REPOSITORY
        self.code = root / "code"
        self.base = init_repo(self.code)
        git(self.code, "branch", "super")
        self.cfg = _config(self.coord, self.code)
        self.refs = [
            TaskDocumentRef(repository=REPOSITORY, path=f"master-{name}/task.json")
            for name in ("a", "b")
        ]
        for ref in self.refs:
            write_task_doc(
                self.tasks / Path(ref.path).parent,
                _master(
                    identity=Path(ref.path).parent.name,
                    execution_nature="atomic",
                    title=f"Human {Path(ref.path).parent.name}",
                ).model_copy(update={"status": "Completed"}),
            )
        sprint = _master(
            identity="sprint",
            orchestrates=["master-a", "master-b"],
        ).model_copy(
            update={
                "status": "Completed",
                "subTasks": [
                    SubTaskRef(number=str(i), name=f"Master {i}", status="Completed", masterRef=ref)
                    for i, ref in enumerate(self.refs)
                ],
            }
        )
        write_task_doc(self.tasks / "sprint", sprint)
        self.target = TaskDocTarget(repo_id=REPOSITORY, task_name="sprint")
        self.fields = {"reason": REASON}

    def archive(self, *, dry_run: bool = False, fields=None, task_name: str = "sprint"):
        arguments: dict[str, object] = {"operation": "archive_sprint"}
        if dry_run:
            arguments["dry_run"] = True
        with tool_call_context("task_doc", arguments, config=self.cfg):
            return task_doc_payload(
                self.cfg,
                TaskDocTarget(repo_id=REPOSITORY, task_name=task_name),
                operation="archive_sprint",
                edit=TaskDocEdit(fields=fields or self.fields),
                call=TaskDocCall(dry_run=dry_run),
            )

    def snapshot(self):
        return {
            path.relative_to(self.tasks).as_posix(): path.read_bytes()
            for path in self.tasks.rglob("*")
            if path.is_file()
        }

    def sprint(self):
        return read_task_doc(self.tasks / "sprint" / "task.json")


@pytest.fixture
def world(tmp_path):
    return ArchiveWorld(tmp_path)


def _whole(answer):
    return json.loads(Path(answer["log"]).read_text(encoding="utf-8"))


def test_archive_dry_run_reports_exact_moves_and_real_run_archives(world):
    before = world.snapshot()
    preview = world.archive(dry_run=True)
    assert preview["ok"] and _whole(preview)["state"] == "would-archive"
    assert (
        _whole(preview)["archiveState"] == "not-recorded" and not _whole(preview)["archiveResumed"]
    )
    moves = [
        change
        for change in _whole(preview)["wouldChange"]
        if change.startswith("task folder: move")
    ]
    assert moves == [
        f"task folder: move {(world.tasks / 'sprint').as_posix()} "
        f"to {(world.tasks / '0_archive' / 'sprint').as_posix()}",
        f"task folder: move {(world.tasks / 'master-a').as_posix()} "
        f"to {(world.tasks / '0_archive' / 'master-a').as_posix()}",
        f"task folder: move {(world.tasks / 'master-b').as_posix()} "
        f"to {(world.tasks / '0_archive' / 'master-b').as_posix()}",
    ]
    assert _whole(preview)["wouldChange"][0].startswith("archive record: write sprint-archive.json")
    assert (world.tasks / "sprint" / "notes" / "reports" / "sprint-archive.json").as_posix() in (
        _whole(preview)["wouldWrite"]
    )
    assert world.snapshot() == before

    result = world.archive()
    assert result["ok"] and _whole(result)["state"] == "archived"
    assert result["archiveState"] == "hook-finished" and not _whole(result)["archiveResumed"]
    assert {entry["masterRef"]["path"] for entry in _whole(result)["archivedMasters"]} == {
        "master-a/task.json",
        "master-b/task.json",
    }
    assert _whole(result)["stayedMasters"] == []
    assert _whole(result)["sprintArchive"]["reason"] == REASON
    assert {entry["folder"] for entry in _whole(result)["sprintArchive"]["masters"]} == {
        "master-a",
        "master-b",
    }
    for name in ("master-a", "master-b", "sprint"):
        assert (world.tasks / "0_archive" / name / "task.json").is_file()
        assert not (world.tasks / name).exists()
    record = world.tasks / "0_archive" / "sprint" / "notes" / "reports" / "sprint-archive.json"
    assert record.is_file()
    cleanups = _whole(result)["taskArchive"]["cleanups"]
    assert [entry["archivePath"] for entry in cleanups] == [
        (world.tasks / "0_archive" / name).as_posix() for name in ("sprint", "master-a", "master-b")
    ]
    assert all("deleted" in entry and "failed" in entry for entry in cleanups)


def test_archive_moves_sprint_first_then_masters_and_records_move_order(world):
    real_rename = Path.rename
    destinations = []

    def record_order(target, destination):
        destinations.append(Path(destination).name)
        return real_rename(target, destination)

    with patch.object(Path, "rename", autospec=True, side_effect=record_order):
        result = world.archive()
    assert result["ok"] and _whole(result)["state"] == "archived"
    assert destinations == ["sprint", "master-a", "master-b"]
    assert _whole(result)["sprintArchive"]["moveOrder"] == ["sprint", "master-a", "master-b"]


def test_archive_refuses_non_completed_sprint_naming_file_and_fix(world):
    write_task_doc(
        world.tasks / "sprint",
        world.sprint().model_copy(update={"status": "inProgress"}),
    )
    before = world.snapshot()
    for dry_run in (True, False):
        with pytest.raises(
            ValueError, match=r"sprint .* status is 'inProgress'.*task\.json.*Completed"
        ):
            world.archive(dry_run=dry_run)
        assert world.snapshot() == before


def test_archive_refuses_nonterminal_row_naming_row_and_file(world):
    data = world.sprint().model_dump(mode="json")
    data["subTasks"][0]["status"] = "inProgress"
    write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
    before = world.snapshot()
    for dry_run in (True, False):
        with pytest.raises(
            ValueError, match=r"row '0'.*master-a.*task\.json.*Completed or abandoned"
        ):
            world.archive(dry_run=dry_run)
        assert world.snapshot() == before


def test_archive_refuses_open_work_naming_master_and_action(world):
    contract = world.tasks / "master-a" / "series-contract.md"
    contract.write_text("not a contract\n")
    before = world.snapshot()
    for dry_run in (True, False):
        with pytest.raises(ValueError, match=r"master .*master-a/task\.json.*is not ready.*repair"):
            world.archive(dry_run=dry_run)
        assert world.snapshot() == before


def test_shared_master_stays_and_names_remaining_live_sprint(world):
    other = _master(identity="other", orchestrates=["master-b"]).model_copy(
        update={
            "status": "inProgress",
            "subTasks": [
                SubTaskRef(number="0", name="Shared", status="inProgress", masterRef=world.refs[1])
            ],
        }
    )
    write_task_doc(world.tasks / "other-sprint", other)
    result = world.archive()
    assert result["ok"] and _whole(result)["state"] == "archived"
    assert [entry["masterRef"]["path"] for entry in _whole(result)["stayedMasters"]] == [
        "master-b/task.json"
    ]
    assert "other-sprint/task.json" in _whole(result)["stayedMasters"][0]["reason"]
    assert (world.tasks / "master-b").is_dir()
    assert (world.tasks / "0_archive" / "master-a").is_dir()
    assert (world.tasks / "0_archive" / "sprint").is_dir()
    assert not (world.tasks / "0_archive" / "master-b").exists()


def test_only_graphed_master_retire_refusal_names_archive_sprint(world):
    data = world.sprint().model_dump(mode="json")
    data["orchestrates"] = ["master-a"]
    data["subTasks"] = [data["subTasks"][0]]
    data["executionGraph"] = {
        "nodes": [world.refs[0].model_dump()],
        "edges": [],
    }
    data["sections"] = [_register_section("J-1").model_dump()]
    write_task_doc(world.tasks / "sprint", TaskDocument.model_validate(data))
    before = world.snapshot()
    with (
        pytest.raises(ValueError, match=r"a graph cannot be empty.*task_doc\.archive_sprint"),
        tool_call_context("task_doc", {"operation": "retire_master"}, config=world.cfg),
    ):
        task_doc_payload(
            world.cfg,
            world.target,
            operation="retire_master",
            edit=TaskDocEdit(
                fields={
                    "masterRef": world.refs[0].model_dump(),
                    "reason": "This work is deliberately retired.",
                }
            ),
            call=TaskDocCall(),
        )
    assert world.snapshot() == before


def test_archive_sprint_is_a_registered_task_doc_operation():
    assert "archive_sprint" in VALID_OPERATIONS
    assert "archive_sprint" in SPRINT_LINKAGE_OPERATIONS
