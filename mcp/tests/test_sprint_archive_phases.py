"""Sprint archive phases (MIK-R82 rule 1): first-phase rollback, hook-failure retry, repeat rules."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from agents_remember.application.task_docs.task_doc_tools import TaskDocCall, TaskDocEdit
from agents_remember.mcp.tools.task_doc import task_doc_payload
from test_sprint_archive import REASON, ArchiveWorld

pytestmark = pytest.mark.integration


@pytest.fixture
def world(tmp_path):
    return ArchiveWorld(tmp_path)


def test_first_phase_failure_restores_everything_and_removes_record(world):
    before = world.snapshot()
    real_rename = Path.rename
    calls = []

    def fail_second(target, destination):
        calls.append(destination)
        if len(calls) == 2:
            raise OSError("disk refused rename")
        return real_rename(target, destination)

    with (
        patch.object(Path, "rename", autospec=True, side_effect=fail_second),
        pytest.raises(ValueError, match=r"archive failed before archival.*restored"),
    ):
        world.archive()
    assert world.snapshot() == before
    assert not (world.tasks / "0_archive").exists()
    assert not (world.tasks / "sprint" / "notes").exists()


def test_first_phase_failure_restores_masters_before_sprint_and_removes_record(world):
    before = world.snapshot()
    real_rename = Path.rename
    calls = []

    def fail_master_b(target, destination):
        calls.append(Path(destination).name)
        if Path(destination).name == "master-b":
            raise OSError("disk refused rename")
        return real_rename(target, destination)

    with (
        patch.object(Path, "rename", autospec=True, side_effect=fail_master_b),
        pytest.raises(ValueError, match=r"archive failed before archival.*restored"),
    ):
        world.archive()
    # Sprint moved first, master-a followed, master-b failed: rollback restores in reverse
    # (the restore renames are the trailing calls).
    assert calls[:3] == ["sprint", "master-a", "master-b"]
    assert calls[3:] == ["master-a", "sprint"]
    assert world.snapshot() == before
    assert not (world.tasks / "0_archive").exists()
    assert not (world.tasks / "sprint" / "notes").exists()


def test_hook_failure_keeps_archive_with_per_folder_lists_and_retry(world):
    notes = world.tasks / "master-a" / "notes"
    notes.mkdir()
    for name in ("a.sqlite", "b.sqlite"):
        connection = sqlite3.connect(notes / name)
        connection.executescript(
            "CREATE TABLE invariant (id TEXT); CREATE TABLE invariant_revision (id TEXT)"
        )
        connection.close()
    archive = world.tasks / "0_archive" / "master-a"
    deleted, failed = archive / "notes" / "a.sqlite", archive / "notes" / "b.sqlite"
    unlink = Path.unlink
    fail = True

    def partial_delete(path, *args, **kwargs):
        if path == failed and fail:
            raise OSError("dataset is busy")
        return unlink(path, *args, **kwargs)

    with patch.object(Path, "unlink", autospec=True, side_effect=partial_delete):
        result = world.archive()
        assert not result["ok"] and result["state"] == "archived-with-hook-failures"
        assert result["archiveState"] == "hook-failed"
        assert "archive_sprint" in result["nextAction"] and REASON not in result["nextAction"]
        by_folder = {entry["archivePath"]: entry for entry in result["taskArchive"]["cleanups"]}
        assert set(by_folder) == {
            (world.tasks / "0_archive" / name).as_posix()
            for name in ("master-a", "master-b", "sprint")
        }
        master_cleanup = by_folder[archive.as_posix()]
        assert master_cleanup["deleted"] == [deleted.as_posix()]
        assert master_cleanup["failed"] == [
            {"target": failed.as_posix(), "detail": "dataset is busy"}
        ]
        assert not deleted.exists() and failed.exists()
        with pytest.raises(ValueError, match=r"differs from its retained record"):
            world.archive(fields={"reason": "a different reason"})
        fail = False
        retry = world.archive()
        assert retry["ok"] and retry["state"] == "archived"
        assert retry["archiveResumed"] and retry["archiveState"] == "hook-finished"
        assert not failed.exists()
        again = world.archive()
        assert again["ok"] and again["state"] == "archived" and again["archiveResumed"]
        assert (world.tasks / "master-a").exists() is False
    receipts = sorted(
        (world.tasks / "0_archive" / "master-a" / "notes" / "reports").glob(
            "review-artifact-cleanup*"
        )
    )
    assert len(receipts) >= 2


def test_repeated_same_request_converges_and_changed_request_refused(world):
    result = world.archive()
    assert result["ok"] and result["state"] == "archived"
    record = world.tasks / "0_archive" / "sprint" / "notes" / "reports" / "sprint-archive.json"
    recorded = record.read_bytes()
    again = world.archive()
    assert again["ok"] and again["state"] == "archived" and again["archiveResumed"]
    assert again["sprintArchive"] == result["sprintArchive"]
    assert record.read_bytes() == recorded
    preview = world.archive(dry_run=True)
    assert preview["ok"] and preview["state"] == "archived"
    assert preview["wouldChange"] == []
    before = world.snapshot()
    with pytest.raises(ValueError, match=r"differs from its retained record"):
        world.archive(fields={"reason": "a different reason"})
    assert world.snapshot() == before


def test_interrupted_first_phase_resumes_from_record(world):
    real_rename = Path.rename

    def fail_master_a_move(target, destination):
        if Path(destination).name == "master-a":
            raise KeyboardInterrupt
        return real_rename(target, destination)

    with (
        patch.object(Path, "rename", autospec=True, side_effect=fail_master_a_move),
        pytest.raises(KeyboardInterrupt),
    ):
        world.archive()
    # The sprint moved first and carries the record; the failed master never moved.
    assert (
        world.tasks / "0_archive" / "sprint" / "notes" / "reports" / "sprint-archive.json"
    ).is_file()
    assert (world.tasks / "master-a").is_dir()
    assert not (world.tasks / "sprint").exists()
    with pytest.raises(ValueError, match=r"differs from its retained record"):
        world.archive(fields={"reason": "a different reason"})
    result = world.archive()
    assert result["ok"] and result["state"] == "archived" and result["archiveResumed"]
    assert (world.tasks / "0_archive" / "sprint" / "task.json").is_file()
    assert not (world.tasks / "sprint").exists()


def test_archive_payload_requires_reason(world):
    before = world.snapshot()
    with pytest.raises(ValueError, match=r"archive_sprint requires a nonblank reason"):
        task_doc_payload(
            world.cfg,
            world.target,
            operation="archive_sprint",
            edit=TaskDocEdit(fields={"reason": "  "}),
            call=TaskDocCall(),
        )
    assert world.snapshot() == before
