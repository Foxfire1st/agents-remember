"""Request locators protect real detail preparation and saved-receipt restoration."""

from __future__ import annotations

import hashlib
import json
import stat
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest
from agents_remember.cli import role_launch_preparation, role_launch_receipts
from agents_remember.cli.role_handover_artifacts import first_message
from agents_remember.cli.role_handover_file import handover_file_reference
from agents_remember.cli.role_handover_publication import publish_handover_files
from agents_remember.tasks import write_task_doc
from agents_remember.worktrees import role_reports
from agents_remember.worktrees.role_handover_locator import ensure_locator, locator_directory
from agents_remember.worktrees.worktree_contract import WorktreeContract
from fastapi import HTTPException

pytest_plugins = ["test_capsule_replay_integrity"]


class HostReached(Exception):
    """End the fake host call without starting an agent."""


def prepare(fixture, monkeypatch: pytest.MonkeyPatch, request_id: uuid.UUID):
    context, workspace = fixture.launch_of("worker")
    assert context.task is not None
    write_task_doc(context.task.path.parent, context.task.document)
    monkeypatch.setattr(role_launch_preparation, "_resolve_workspace", lambda *_: workspace)
    return role_launch_preparation.prepare_role_handover(
        fixture.config, context, agent_override=None, request_id=request_id
    )


@pytest.fixture
def detailed_receipt(leaf_preparation, monkeypatch: pytest.MonkeyPatch):
    request_id = uuid.uuid4()
    prepared = prepare(leaf_preparation, monkeypatch, request_id)
    compiled = prepared.handover
    report = compiled["taskReportPath"]
    artifact, handover_file = publish_handover_files(
        report,
        compiled["prompt"],
        compiled["handoverFileContent"],
        expected=compiled["handoverFile"],
        request_id=str(request_id),
    )
    opening = first_message(artifact, compiled["prompt"], handover_file)
    receipt = {
        "schema": role_launch_receipts.RECEIPT_SCHEMA,
        "requestId": str(request_id),
        "agentId": str(uuid.uuid4()),
        "role": "worker",
        "selection": {"role": "worker"},
        "status": "unknown",
        "instructionComposition": compiled["instructionComposition"],
        "report": {"path": report, "canonicalPath": compiled["canonicalTaskReportPath"]},
        "handoverArtifact": artifact,
        "handoverFile": {**handover_file, "content": compiled["handoverFileContent"]},
        "replayRequest": {"agent": {"prompt": opening}},
        "workspace": prepared.workspace,
    }
    details = receipt["instructionComposition"]["detailReferences"]
    assert details
    paths = [Path(artifact["canonicalPath"]), Path(handover_file["canonicalPath"])]
    paths.extend(Path(reference["path"]) for reference in details)
    frozen = {
        path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) for path in paths
    }
    receipt_path = Path(prepared.workspace["scratchRoot"]) / "replay.json"
    role_launch_receipts._write_receipt(receipt_path, receipt)
    return leaf_preparation.config, receipt_path, receipt, opening, frozen


def test_detailed_receipt_rebuilds_only_its_lost_locator_before_host_replay(
    detailed_receipt, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, receipt_path, receipt, opening, frozen = detailed_receipt
    artifact = receipt["handoverArtifact"]
    locator = Path(artifact["path"]).parent
    paths = list(frozen)
    calls = []

    def host(*args, **kwargs):
        calls.append(args[1])
        raise HostReached

    monkeypatch.setattr(role_launch_receipts, "run_launch_call", host)
    for _ in range(2):
        locator.unlink()
        # Read only the persisted receipt, as after a process restart or resume.
        saved = json.loads(receipt_path.read_text())
        with pytest.raises(HostReached):
            role_launch_receipts._execute_prepared_launch(config, receipt_path, saved)
        assert locator.resolve() == Path(artifact["canonicalPath"]).parent
        inode = locator.lstat().st_ino
        with pytest.raises(HostReached):
            role_launch_receipts._execute_prepared_launch(config, receipt_path, saved)
        assert locator.lstat().st_ino == inode
        assert calls[-1]["agent"]["prompt"] == opening
        assert hashlib.sha256(Path(artifact["path"]).read_bytes()).hexdigest() == artifact["sha256"]
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o444 for path in paths)
        assert {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in paths} == frozen

    calls.clear()
    elsewhere = locator.parent / "foreign"
    elsewhere.mkdir()
    locator.unlink()
    locator.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(
        HTTPException, match="does not lead to this request's report folder"
    ) as error:
        role_launch_receipts._execute_prepared_launch(config, receipt_path, receipt)
    assert error.value.status_code == 409
    assert calls == []
    assert locator.resolve() == elsewhere
    assert list(elsewhere.iterdir()) == []
    locator.unlink()
    detail = Path(receipt["instructionComposition"]["detailReferences"][0]["path"])
    detail.chmod(0o644)
    detail.write_bytes(b"Altered frozen detail.\n")
    detail.chmod(0o444)
    with pytest.raises(ValueError, match="frozen instruction detail"):
        role_launch_receipts._execute_prepared_launch(config, receipt_path, receipt)
    assert calls == []


def test_real_detailed_preparation_refuses_a_collision_before_artifact_writes(
    leaf_preparation, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    request_id = uuid.uuid4()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    marker = foreign / "kept.txt"
    marker.write_bytes(b"Foreign bytes.\n")
    locator = locator_directory(str(request_id))
    ensure_locator(locator, foreign)
    with pytest.raises(ValueError, match="does not lead to this request's report folder"):
        prepare(leaf_preparation, monkeypatch, request_id)
    assert locator.resolve() == foreign
    assert marker.read_bytes() == b"Foreign bytes.\n"
    assert list(foreign.iterdir()) == [marker]
    root = leaf_preparation.config.coordination_root.parent
    assert not list(root.rglob("*.detail-*.md"))
    assert not list(root.rglob("*.handover.txt"))
    assert not list(root.rglob("*.handover.json"))
    assert not list(root.rglob("*-native-executions/**/*.json"))


@pytest.mark.parametrize("count", [2, 8])
def test_terminal_group_reclaims_each_alias_once_and_keeps_every_report(
    tmp_path: Path, count: int
) -> None:
    group, task = tmp_path / "group", tmp_path / "task"
    aliases = []
    frozen = {}
    for number in range(count):
        request_id = str(uuid.UUID(int=number + 1))
        folder = group / "role-reports" / "worker" / request_id / "role-launch"
        folder.mkdir(parents=True)
        report = folder / "worker.md"
        report.write_bytes(b"Collected report.\n")
        artifact, _ = publish_handover_files(
            str(report),
            "Frozen handover.",
            "{}\n",
            expected=handover_file_reference(str(report), "{}\n"),
            request_id=request_id,
        )
        aliases.append(Path(artifact["path"]).parent)
        frozen.update(
            {p.relative_to(group / "role-reports"): p.read_bytes() for p in folder.iterdir()}
        )
    contract = cast(WorktreeContract, SimpleNamespace(worktree_group=group, task_root=task))
    assert all(alias.is_symlink() for alias in aliases)
    with patch.object(role_reports, "remove_locator", wraps=role_reports.remove_locator) as remove:
        role_reports.remove_group_role_folders(contract, dry_run=False)
        assert remove.call_count == count
    assert not any(alias.is_symlink() for alias in aliases)
    assert not (group / "role-reports").exists()
    collected = task / "notes" / "reports"
    assert {
        p.relative_to(collected): p.read_bytes() for p in collected.rglob("*") if p.is_file()
    } == frozen
    assert len(frozen) == 3 * count
