"""Sprint archival retires moved masters' enclosures through abandonment's safety checks."""

from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.task_docs import task_sprint_archive as archive_owner
from agents_remember.application.task_docs.task_sprint_archive import (
    _enclosure_contracts,
    _retire_enclosures,
)
from agents_remember.tasks import SubTaskRef, write_task_doc
from agents_remember.worktrees.modules import abandon
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    WorktreeContract,
    default_series_contract,
    load_contract,
    write_contract,
)
from test_record_landing import _fixture
from test_sprint_archive import ArchiveWorld, _whole
from test_sprint_archive_custody import _interrupt_on
from test_task_execution_topology import REPOSITORY, _master
from test_worktree_support import init_repo

pytestmark = pytest.mark.integration


def _slot(live: Path, key: str = "MASTER") -> Any:
    return SimpleNamespace(
        master=SimpleNamespace(ref=SimpleNamespace(key=key)),
        live=live,
        archive=live.parent / "0_archive" / live.name,
        staying=False,
    )


def _world(root: Path) -> tuple[Path, Any]:
    """A master folder that holds one enclosure contract, and that contract's worktree group."""

    contract = _fixture(root)
    master_folder = contract.contract_path.parent
    enclosure = master_folder / "enclosures" / "doc"
    enclosure.mkdir(parents=True)
    place = enclosure / "series-contract.md"
    # A contract is used only where its own path cell names it, so the copy carries that cell.
    contract = replace(contract, contract_path=place)
    write_contract(place, contract)
    group = contract.worktree_group
    for name in ("reports", ".lifecycle", "logs"):
        (group / name).mkdir(parents=True, exist_ok=True)
        (group / name / "kept.txt").write_text(name + "\n", encoding="utf-8")
    assert _enclosure_contracts(master_folder), "the fixture's enclosure is not under enclosures/*"
    return master_folder, _slot(master_folder)


def test_the_archive_retires_the_enclosure_and_names_what_it_removed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        task_root, slot = _world(Path(tmp))
        group = _fixture_contract_group(task_root)

        dry = _retire_enclosures((slot,), dry_run=True)
        assert [entry["state"] for entry in dry] == ["would-retire"]
        assert (group / "reports").is_dir()  # a dry run removes nothing

        done = _retire_enclosures((slot,), dry_run=False)
        assert [entry["state"] for entry in done] == ["retired"]
        named = done[0]["directories"]
        assert isinstance(named, dict)
        reports = named["reports"]
        assert reports["removed"] is True and reports["path"] == (group / "reports").as_posix()
        assert named["lifecycle"]["removed"] is True
        assert (named["logs"]["removed"] is True) if "logs" in named else True
        assert not (group / "reports").exists()
        assert not (group / ".lifecycle").exists()


def test_a_master_without_an_enclosure_contract_is_named_not_guessed_at() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "task-root"
        empty.mkdir()
        retired = _retire_enclosures((_slot(empty, key="MASTER-B"),), dry_run=False)
        assert retired == [{"master": "MASTER-B", "state": "no-enclosure"}]


def test_a_master_that_stays_keeps_its_enclosure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _task_root, slot = _world(Path(tmp))
        staying: Any = SimpleNamespace(master=slot.master, live=slot.live, staying=True)
        assert _retire_enclosures((staying,), dry_run=False) == []


def _fixture_contract_group(task_root: Path) -> Path:
    """The worktree group the fixture's contract names, read back through its own loader."""

    contract_path = next(iter(_enclosure_contracts(task_root)))
    return load_contract(contract_path).worktree_group


def _archive_world(root: Path) -> tuple[ArchiveWorld, WorktreeContract]:
    world = ArchiveWorld(root)
    memory = root / "memory"
    memory_base = init_repo(memory)
    master = world.tasks / "master-a"
    contract = default_series_contract(
        ContractTask("master-a", REPOSITORY, world.coord, "light-task", "external"),
        code=RepoBranchPlan(world.code, "main", "main", world.base),
        memory=RepoBranchPlan(memory, "main", "main", memory_base),
        task_root=master,
    )
    contract = replace(contract, contract_path=master / "enclosures" / "doc" / "series-contract.md")
    contract.contract_path.parent.mkdir(parents=True)
    write_contract(contract.contract_path, contract)
    for name in ("reports", ".lifecycle", "logs"):
        folder = contract.worktree_group / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "kept.txt").write_text(name + "\n", encoding="utf-8")
    return world, contract


@pytest.mark.parametrize("stage", ["fresh", "folders-archived", "partial-move"])
def test_archive_entry_retires_enclosures_from_their_current_folder(
    tmp_path: Path, stage: str
) -> None:
    world, contract = _archive_world(tmp_path)
    if stage == "folders-archived":
        with (
            patch.object(archive_owner, "_phase_two", side_effect=KeyboardInterrupt),
            pytest.raises(KeyboardInterrupt),
        ):
            world.archive()
    elif stage == "partial-move":
        with (
            patch.object(Path, "rename", autospec=True, side_effect=_interrupt_on("master-b")),
            pytest.raises(KeyboardInterrupt),
        ):
            world.archive()

    group = contract.worktree_group
    before = world.snapshot()
    with patch.object(
        abandon, "_abandon_directories", wraps=abandon._abandon_directories
    ) as remove:
        preview = _whole(world.archive(dry_run=True))
        planned, missing = preview["taskArchive"]["enclosures"]
        assert planned["master"] == world.refs[0].key
        assert planned["state"] == "would-retire"
        assert missing == {"master": world.refs[1].key, "state": "no-enclosure"}
        assert planned["directories"]["reports"]["would_remove"] is True
        if stage == "folders-archived":
            assert preview["state"] == "would-clean-up"
            assert preview["archiveState"] == "folders-archived"
            assert "Nothing would change" not in preview.get("detail", "")
        assert (group / "reports" / "kept.txt").read_text(encoding="utf-8") == "reports\n"
        assert world.snapshot() == before

        result = _whole(world.archive())
        retired, missing = result["taskArchive"]["enclosures"]
        assert result["ok"] and result["archiveState"] == "hook-finished"
        assert result["archiveResumed"] is (stage != "fresh")
        assert retired["state"] == "retired"
        assert missing == {"master": world.refs[1].key, "state": "no-enclosure"}
        assert (
            retired["contractPath"]
            == (
                world.tasks / "0_archive" / "master-a" / "enclosures" / "doc" / "series-contract.md"
            ).as_posix()
        )
        assert retired["directories"]["reports"] == {
            "path": (group / "reports").as_posix(),
            "removed": True,
        }
        assert retired["directories"]["lifecycle"]["removed"] is True
        assert all(not (group / name).exists() for name in ("reports", ".lifecycle", "logs"))
        assert [call.kwargs for call in remove.call_args_list] == [
            {"dry_run": True, "force": False},
            {"dry_run": False, "force": False},
        ]


def test_archive_resume_after_hooks_previews_and_finishes_enclosure_retirement(
    tmp_path: Path,
) -> None:
    world, contract = _archive_world(tmp_path)
    # Interrupt after real moves and hooks, with only enclosure retirement left to execute.
    with (
        patch.object(archive_owner, "_retire_enclosures", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        world.archive()

    group = contract.worktree_group
    before = world.snapshot()
    group_before = {
        path.relative_to(group): path.read_bytes() for path in group.rglob("*") if path.is_file()
    }
    assert (world.tasks / "0_archive" / "master-a" / "task.json").is_file()
    assert (group / "reports" / "kept.txt").is_file()
    with patch.object(
        abandon, "_abandon_directories", wraps=abandon._abandon_directories
    ) as remove:
        preview = _whole(world.archive(dry_run=True))
        planned, missing = preview["taskArchive"]["enclosures"]
        assert preview["archiveResumed"] is True
        assert preview["state"] == "would-clean-up"
        assert preview["archiveState"] == "folders-archived"
        assert "Nothing would change" not in preview.get("detail", "")
        assert planned["master"] == world.refs[0].key
        assert planned["state"] == "would-retire"
        assert missing == {"master": world.refs[1].key, "state": "no-enclosure"}
        pending = {
            row["path"] for row in planned["directories"].values() if row.get("would_remove")
        }
        assert {(group / "reports").as_posix(), (group / ".lifecycle").as_posix()} <= pending
        for path in pending:
            assert any(
                planned["master"] in change and path in change for change in preview["wouldChange"]
            )
        assert world.snapshot() == before
        assert {
            path.relative_to(group): path.read_bytes()
            for path in group.rglob("*")
            if path.is_file()
        } == group_before

        result = _whole(world.archive())
        retired = result["taskArchive"]["enclosures"][0]
        assert result["ok"] and result["archiveState"] == "hook-finished"
        assert retired["state"] == "retired"
        assert retired["directories"]["reports"]["removed"] is True
        assert retired["directories"]["lifecycle"]["removed"] is True
        assert all(not (group / name).exists() for name in ("reports", ".lifecycle", "logs"))
        assert [call.kwargs for call in remove.call_args_list] == [
            {"dry_run": True, "force": False},
            {"dry_run": False, "force": False},
        ]

    completed = world.snapshot()
    preview = _whole(world.archive(dry_run=True))
    assert preview["state"] == "archived" and preview["archiveState"] == "hook-finished"
    assert preview["wouldChange"] == []
    assert world.snapshot() == completed


def test_archive_entry_preserves_the_enclosure_of_a_master_another_sprint_commands(
    tmp_path: Path,
) -> None:
    world, contract = _archive_world(tmp_path)
    keeper = _master(identity="other", orchestrates=["master-a"]).model_copy(
        update={
            "status": "inProgress",
            "subTasks": [
                SubTaskRef(number="0", name="Shared", status="inProgress", masterRef=world.refs[0])
            ],
        }
    )
    write_task_doc(world.tasks / "other-sprint", keeper)
    with patch.object(
        abandon, "_abandon_directories", wraps=abandon._abandon_directories
    ) as remove:
        result = _whole(world.archive())
    assert result["ok"]
    assert result["taskArchive"]["enclosures"] == [
        {"master": world.refs[1].key, "state": "no-enclosure"}
    ]
    assert [entry["masterRef"]["path"] for entry in result["stayedMasters"]] == [
        "master-a/task.json"
    ]
    assert (world.tasks / "master-a").is_dir()
    assert (contract.worktree_group / "reports" / "kept.txt").is_file()
    remove.assert_not_called()
