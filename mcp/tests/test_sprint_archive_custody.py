"""Sprint-archive recovery, legacy-row, and preview-bound regressions (MIK-R82 rule 1).

Each test mirrors a Reviewer CODE R1 probe scenario that the L82-W1 candidate
failed: a same-request retry must refuse lost master custody and changed archived
sprint bytes, a caught failure on a resumed prefix must restore the whole record
prefix truthfully, an open legacy seat row must refuse like a typed row, and a
fifteen-master sprint must preview completely inside the public response bounds.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import bound_for
from agents_remember.tasks import SubTaskRef, TaskDocument, write_task_doc
from test_sprint_archive import ArchiveWorld
from test_task_execution_topology import REPOSITORY, _master

pytestmark = pytest.mark.integration


def _whole(answer):
    return json.loads(Path(answer["log"]).read_text(encoding="utf-8"))


def _archive(world, *, dry_run=False, fields=None, task_name="sprint"):
    arguments: dict[str, Any] = {"operation": "archive_sprint"}
    if dry_run:
        arguments["dry_run"] = True
    with tool_call_context("task_doc", arguments, config=world.cfg):
        return world.archive(dry_run=dry_run, fields=fields, task_name=task_name)


@pytest.fixture
def world(tmp_path):
    return ArchiveWorld(tmp_path)


def _interrupt_on(target_name: str):
    real_rename = Path.rename

    def side_effect(path, target):
        if Path(target).name == target_name:
            raise KeyboardInterrupt(f"crash before {target_name}")
        return real_rename(path, target)

    return side_effect


def _legacy_seat(world: ArchiveWorld, slug: str, status: str = "inProgress") -> None:
    seat = TaskDocument.model_validate(
        {
            "id": "SEAT-A",
            "slug": slug,
            "title": "Manager A",
            "kind": "subTask",
            "status": status,
            "repo": REPOSITORY,
            "type": "SubTask",
            "createdAt": "2026-01-01T00:00:00Z",
            "references": ["../master-a/task.json"],
        }
    )
    write_task_doc(world.tasks / "sprint", seat)


def test_recorded_retry_refuses_missing_master_custody(world):
    with (
        patch.object(Path, "rename", autospec=True, side_effect=_interrupt_on("master-a")),
        pytest.raises(KeyboardInterrupt),
    ):
        world.archive()
    assert (world.tasks / "0_archive" / "sprint").is_dir()
    for name in ("master-a", "master-b"):
        shutil.rmtree(world.tasks / name)
    before = world.snapshot()
    for dry_run in (True, False):
        with pytest.raises(ValueError, match=r"master-a/task\.json.*restore that master's folder"):
            world.archive(dry_run=dry_run)
        assert world.snapshot() == before
    assert [path.name for path in (world.tasks / "0_archive").iterdir()] == ["sprint"]


def test_recorded_retry_refuses_changed_archived_sprint_bytes(world):
    result = _archive(world)
    assert result["ok"] and _whole(result)["state"] == "archived"
    sprint_json = world.tasks / "0_archive" / "sprint" / "task.json"
    data = json.loads(sprint_json.read_text())
    data["status"] = "inProgress"
    sprint_json.write_text(json.dumps(data))
    for dry_run in (True, False):
        with pytest.raises(
            ValueError,
            match=r"0_archive/sprint/task\.json.*changed since archive admission",
        ):
            world.archive(dry_run=dry_run)
    assert (world.tasks / "0_archive" / "sprint").is_dir()


def test_resumed_prefix_failure_restores_all_and_removes_record(world):
    original = world.snapshot()
    with (
        patch.object(Path, "rename", autospec=True, side_effect=_interrupt_on("master-a")),
        pytest.raises(KeyboardInterrupt),
    ):
        world.archive()
    assert not (world.tasks / "sprint").exists()
    assert (world.tasks / "0_archive" / "sprint" / "task.json").is_file()
    assert (world.tasks / "master-a").is_dir()

    real_rename = Path.rename

    def fail_master_b(path, target):
        if Path(target).name == "master-b":
            raise OSError("retry move failed")
        return real_rename(path, target)

    with (
        patch.object(Path, "rename", autospec=True, side_effect=fail_master_b),
        pytest.raises(ValueError, match=r"archive failed before archival.*restored"),
    ):
        world.archive()
    # The whole record prefix came back: master-a and the sprint, masters first.
    assert (world.tasks / "sprint").is_dir()
    assert (world.tasks / "master-a").is_dir()
    assert (world.tasks / "master-b").is_dir()
    assert not (world.tasks / "0_archive" / "sprint").exists()
    assert not (world.tasks / "0_archive" / "master-a").exists()
    assert not (world.tasks / "sprint" / "notes" / "reports" / "sprint-archive.json").exists()
    assert world.snapshot() == original
    # Complete restoration removed the record, so the next same request starts fresh.
    result = _archive(world)
    assert result["ok"] and _whole(result)["state"] == "archived"
    assert not _whole(result)["archiveResumed"]


def test_resumed_retry_dry_run_previews_remaining_moves(world):
    with (
        patch.object(Path, "rename", autospec=True, side_effect=_interrupt_on("master-a")),
        pytest.raises(KeyboardInterrupt),
    ):
        world.archive()
    before = world.snapshot()
    preview = _archive(world, dry_run=True)
    assert preview["ok"] and _whole(preview)["state"] == "would-archive"
    assert _whole(preview)["archiveResumed"] and _whole(preview)["archiveState"] == "recorded"
    moves = [
        change
        for change in _whole(preview)["wouldChange"]
        if change.startswith("task folder: move")
    ]
    assert len(moves) == 2
    assert all("0_archive/sprint" not in change for change in moves)
    assert all("sprint-archive.json" not in change for change in _whole(preview)["wouldChange"])
    assert world.snapshot() == before
    result = _archive(world)
    assert (
        result["ok"] and _whole(result)["state"] == "archived" and _whole(result)["archiveResumed"]
    )


def test_open_legacy_seat_row_refuses_and_terminal_passes(world):
    _legacy_seat(world, "00_master-a-seat")
    sprint = world.sprint().model_copy(
        update={
            "subTasks": [
                SubTaskRef(
                    number="0",
                    name="Manager A",
                    status="inProgress",
                    file="00_master-a-seat.md",
                ),
                world.sprint().subTasks[1],
            ]
        }
    )
    write_task_doc(world.tasks / "sprint", sprint)
    before = world.snapshot()
    for dry_run in (True, False):
        with pytest.raises(
            ValueError, match=r"row '0'.*master-a/task\.json.*'inProgress'.*Completed or abandoned"
        ):
            world.archive(dry_run=dry_run)
        assert world.snapshot() == before
    assert not (world.tasks / "0_archive").exists()
    # The same correlated seat row, terminal, archives like a typed row.
    sprint = world.sprint().model_copy(
        update={
            "subTasks": [
                world.sprint().subTasks[0].model_copy(update={"status": "Completed"}),
                world.sprint().subTasks[1],
            ]
        }
    )
    write_task_doc(world.tasks / "sprint", sprint)
    result = _archive(world)
    assert result["ok"] and _whole(result)["state"] == "archived"


def test_unknown_seat_row_refuses(world):
    sprint = world.sprint().model_copy(
        update={
            "subTasks": [
                *world.sprint().subTasks,
                SubTaskRef(number="7", name="Ghost", status="inProgress", file="00_ghost-seat.md"),
            ]
        }
    )
    write_task_doc(world.tasks / "sprint", sprint)
    before = world.snapshot()
    with pytest.raises(ValueError, match=r"row '7'.*00_ghost-seat.*cannot be read"):
        world.archive()
    assert world.snapshot() == before


def test_ambiguous_legacy_rows_refuse(world):
    _legacy_seat(world, "00_master-a-seat", status="Completed")
    second = TaskDocument.model_validate(
        {
            "id": "SEAT-B",
            "slug": "00_master-a-second-seat",
            "title": "Manager A again",
            "kind": "subTask",
            "status": "Completed",
            "repo": REPOSITORY,
            "type": "SubTask",
            "createdAt": "2026-01-01T00:00:00Z",
            "references": ["../master-a/task.json"],
        }
    )
    write_task_doc(world.tasks / "sprint", second)
    sprint = world.sprint().model_copy(
        update={
            "subTasks": [
                *world.sprint().subTasks,
                SubTaskRef(
                    number="0",
                    name="Manager A",
                    status="Completed",
                    file="00_master-a-seat.md",
                ),
                SubTaskRef(
                    number="1",
                    name="Manager A again",
                    status="Completed",
                    file="00_master-a-second-seat.md",
                ),
            ]
        }
    )
    write_task_doc(world.tasks / "sprint", sprint)
    before = world.snapshot()
    with pytest.raises(ValueError, match=r"2 seat rows.*master-a/task\.json.*exactly one row"):
        world.archive()
    assert world.snapshot() == before


def _fifteen_master_world(world: ArchiveWorld):
    refs = list(world.refs)
    for n in range(13):
        name = f"extra-{n}"
        refs.append(TaskDocumentRef(repository=REPOSITORY, path=f"{name}/task.json"))
        write_task_doc(
            world.tasks / name,
            _master(identity=name, execution_nature="atomic").model_copy(
                update={"status": "Completed"}
            ),
        )
    sprint = world.sprint().model_copy(
        update={
            "orchestrates": [Path(ref.path).parent.name for ref in refs],
            "subTasks": [
                SubTaskRef(number=str(i), name=f"Master {i}", status="Completed", masterRef=ref)
                for i, ref in enumerate(refs)
            ],
        }
    )
    write_task_doc(world.tasks / "sprint", sprint)
    return refs


def test_fifteen_master_preview_bounded_and_exact(world):
    refs = _fifteen_master_world(world)
    before = world.snapshot()
    # MIK-R111 rule 2 projects a dry run to its loss and diff, so the whole
    # fifteen-master preview need not force the answer into overflow anymore.
    # Measure the delivered answer itself; keep every exact move/cleanup
    # assertion on the unchanged whole result in the named detail file.
    with tool_call_context(
        "task_doc",
        {"operation": "archive_sprint"},
        config=SimpleNamespace(
            coordination_root=world.coord,
            transcript_root=world.coord / "logs" / "mcp",
        ),
    ):
        preview = world.archive(dry_run=True)
    assert preview["ok"] and _whole(preview)["state"] == "would-archive"
    assert count_response_tokens(preview) <= bound_for("task_doc", "archive_sprint")
    assert preview["log"].endswith(".json")
    whole = json.loads(Path(preview["log"]).read_text(encoding="utf-8"))
    assert len(whole["wouldChange"]) <= 16
    assert len(whole["wouldWrite"]) <= 16
    assert whole["sprintArchive"]["moveOrder"][0] == "sprint"
    assert len(whole["sprintArchive"]["moveOrder"]) == 16
    assert {entry["masterRef"]["path"] for entry in whole["archivedMasters"]} == {
        ref.path for ref in refs
    }
    assert len(whole["taskArchive"]["cleanups"]) == 16
    assert world.snapshot() == before
    result = _archive(world)
    assert result["ok"] and _whole(result)["state"] == "archived"
    for ref in refs:
        assert (world.tasks / "0_archive" / Path(ref.path).parent / "task.json").is_file()
    # The repeat still retains all sixteen cleanups in its detail file;
    # its projected dry-run answer is measured against the same bound.
    with tool_call_context(
        "task_doc",
        {"operation": "archive_sprint"},
        config=SimpleNamespace(
            coordination_root=world.coord,
            transcript_root=world.coord / "logs" / "mcp",
        ),
    ):
        repeat = world.archive(dry_run=True)
    assert repeat["ok"] and _whole(repeat)["state"] == "archived"
    assert count_response_tokens(repeat) <= bound_for("task_doc", "archive_sprint")
    repeat_whole = json.loads(Path(repeat["log"]).read_text(encoding="utf-8"))
    assert repeat_whole["wouldChange"] == []


def test_shared_keeper_preview_and_hook_failure_preview(world):
    other = _master(identity="other", orchestrates=["master-b"]).model_copy(
        update={
            "status": "inProgress",
            "subTasks": [
                SubTaskRef(number="0", name="Shared", status="inProgress", masterRef=world.refs[1])
            ],
        }
    )
    write_task_doc(world.tasks / "other-sprint", other)
    preview = _archive(world, dry_run=True)
    assert preview["ok"] and _whole(preview)["state"] == "would-archive"
    assert [entry["masterRef"]["path"] for entry in _whole(preview)["stayedMasters"]] == [
        "master-b/task.json"
    ]
    moves = [
        change
        for change in _whole(preview)["wouldChange"]
        if change.startswith("task folder: move")
    ]
    assert len(moves) == 2
    assert all("master-b" not in change for change in moves)
    result = _archive(world)
    assert result["ok"] and (world.tasks / "master-b").is_dir()

    world2 = ArchiveWorld(world.coord.parent / "hook-preview")
    hook_notes = world2.tasks / "master-a" / "notes"
    hook_notes.mkdir()
    for name in ("a.sqlite", "b.sqlite"):
        connection = sqlite3.connect(hook_notes / name)
        connection.executescript(
            "CREATE TABLE invariant (id TEXT); CREATE TABLE invariant_revision (id TEXT)"
        )
        connection.close()
    failed = world2.tasks / "0_archive" / "master-a" / "notes" / "b.sqlite"
    unlink = Path.unlink

    def partial_delete(path, *args, **kwargs):
        if path == failed:
            raise OSError("dataset is busy")
        return unlink(path, *args, **kwargs)

    with patch.object(Path, "unlink", autospec=True, side_effect=partial_delete):
        failed_run = _archive(world2)
        assert not failed_run["ok"]
        assert _whole(failed_run)["state"] == "archived-with-hook-failures"
        preview = _archive(world2, dry_run=True)
        assert _whole(preview)["state"] == "would-clean-up"
        assert any("delete 1" in change for change in _whole(preview)["wouldChange"])
        assert len(_whole(preview)["wouldChange"]) <= 16
        assert len(_whole(preview)["wouldWrite"]) <= 16
