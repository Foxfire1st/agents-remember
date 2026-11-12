"""The stores of removed parts: named by the install, removed by one command (MIK-R113 rule 5).

The parts this task removed wrote stores under the coordination root. The install names
what it finds once, with sizes, and deletes nothing. Only ``agents-remember removed-stores``
removes them, on the user's word; ``--dry-run`` lists and changes nothing. The gate logs are
on the same list: the landing chain still reads them and no part of the product decides a
gate any more, so the install report and the command name them with the way out.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from agents_remember.cli import removed_stores
from agents_remember.install import provider_watchers as install_provider_watchers
from agents_remember.install import runtime as install_runtime
from agents_remember.install.removed_stores import (
    REMOVED_PART_STORE_PATHS,
    REMOVED_STORES_NOTICE,
    STORED_GATE_LOG_PATTERNS,
    removed_part_stores,
)
from agents_remember.install.runtime import InstallSummary
from agents_remember.kernel.primitives.gate_vocab import STORED_GATE_WAY_OUT
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)
from test_config import settings_payload
from test_install_runtime import call_registered_tool, create_runtime_source, watcher_result

WORKSPACE = "logs/observer/workspace"
# Three folders, some 350 characters. The paths in an install's answer grow with its root, and
# an answer over its bound reaches the caller as the short form: with the six stores below,
# 472 tokens were measured at a root of 67 characters, and the bound of 600 was passed from
# about 245. Under these folders the caller is handed the short form.
WIDE = Path(
    "a-coordination-root-wide-enough-that-the-answer-of-an-install-passes-its-bound-"
    "and-the-caller-is-handed-the-short-form",
    "which-names-the-detail-file-that-holds-the-answer-whole-with-the-count-of-the-stores-"
    "and-the-path-of-the-filed-report",
    "so-that-a-reader-of-the-short-form-opens-that-file-and-finds-there-what-the-install-"
    "found-and-where-its-report-lies",
)
# A folder is closed to the test by its mode, and no mode closes anything to the superuser.
closed_by_mode = pytest.mark.skipif(
    os.geteuid() == 0, reason="nothing is closed by mode to the superuser"
)


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _coordination_root(tmp_path: Path) -> tuple[Path, Path]:
    """A settings file and the coordination root it names."""
    settings = tmp_path / "mcp-settings.json"
    settings.write_text(json.dumps(settings_payload(tmp_path)), encoding="utf-8")
    return settings, load_config(settings).coordination_root


def _args(settings: Path, *, dry_run: bool = False) -> argparse.Namespace:
    return argparse.Namespace(config=str(settings), dry_run=dry_run)


def _populate(root: Path) -> dict[str, Path]:
    """One store of every kind, beside files of parts that stay."""
    workspace = root / WORKSPACE
    return {
        "inbox": _write(workspace / "operator-inbox.jsonl", ""),
        "inbox_copy": _write(workspace / "operator-inbox.jsonl.pre-repair.bak", "a" * 40),
        "inbox_lock": _write(workspace / "operator-inbox.lock", ""),
        "nudges": _write(workspace / "orchestration-nudges.jsonl", '{"id": "N1"}\n'),
        "session_lock": _write(root / "logs/dashboard/.terminal-sessions.json.lock", ""),
        "seat_lock": _write(root / "runtime/structural-seat-locks/seat-a/owner.json", "{}"),
        "workspace_gates": _write(workspace / "gates.jsonl", '{"gateId": "G1"}\n'),
        "workspace_gates_lock": _write(workspace / "gates.lock", ""),
        "lifecycle_gates": _write(root / "logs/observer/lifecycles/L1/gates.jsonl", "{}\n"),
        "events": _write(workspace / "events.jsonl", '{"kind": "kept"}\n'),
        "dismissals": _write(workspace / "attention-dismissals.json", "{}"),
        "lifecycle_events": _write(root / "logs/observer/lifecycles/L1/events.jsonl", "{}\n"),
    }


STAYING = ("workspace_gates_lock", "events", "dismissals")


def _lifecycle_contract(root: Path, name: str, lifecycle_id: str, *, terminal: bool) -> Path:
    contract = default_contract(
        ContractTask(
            name=name,
            repo_name="repo",
            coordination_root=root,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        leaf=LeafIdentity(worktree_name=name, lifecycle_id=lifecycle_id),
        code=RepoBranchPlan(
            repo_path=root / "repo",
            source_branch="main",
            work_branch=f"ar/{name}",
            base_commit="0" * 40,
        ),
        memory=RepoBranchPlan(
            repo_path=root / "memory",
            source_branch="main",
            work_branch=f"ar/{name}-memory",
            base_commit="1" * 40,
        ),
    )
    if terminal:
        contract = replace(contract, cleanup="completed")
    contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
    write_contract(contract.contract_path, contract)
    return contract.contract_path


def test_explicit_command_reclaims_terminal_lock_only_subjects_at_two_sizes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for size in (8, 64):
        base = tmp_path / str(size)
        base.mkdir()
        settings, root = _coordination_root(base)
        lifecycles = root / "logs/observer/lifecycles"
        for index in range(size):
            subject = f"terminal-{index}"
            _lifecycle_contract(root, subject, subject, terminal=True)
            _write(lifecycles / subject / "gates.jsonl.lock", "")
        # One terminal contract cannot overrule another live contract naming the subject.
        _lifecycle_contract(root, "shared-terminal", "shared", terminal=True)
        _lifecycle_contract(root, "shared-active", "shared", terminal=False)
        _lifecycle_contract(root, "history", "history", terminal=True)
        _lifecycle_contract(root, "other-file", "other-file", terminal=True)
        kept = [
            _write(lifecycles / subject / "gates.jsonl.lock", "")
            for subject in ("shared", "unknown", "history", "other-file")
        ]
        _write(lifecycles / "history/gates.jsonl", '{"state":"applied"}\n')
        other = _write(lifecycles / "other-file/owned.json", "kept")
        obsolete = [
            _write(lifecycles / "old" / name, "old")
            for name in ("events.jsonl", "heartbeat.json.lock", "served.jsonl.repair")
        ]
        assert not any(item["path"].endswith("terminal-0") for item in removed_part_stores(root))
        assert removed_stores.run(_args(settings, dry_run=True)) == 0
        assert all(path.exists() for path in (*kept, *obsolete))
        assert (lifecycles / "terminal-0/gates.jsonl.lock").exists()
        assert removed_stores.run(_args(settings)) == 0
        assert all(not (lifecycles / f"terminal-{index}").exists() for index in range(size))
        assert all(path.exists() for path in kept)
        assert other.read_text() == "kept"
        assert all(not path.exists() for path in obsolete)
        # Explicit gate-log removal retains its lock this pass; history was not
        # admitted as a lock-only directory in the same inventory.
        assert not (lifecycles / "history/gates.jsonl").exists()
        capsys.readouterr()


def test_an_incomplete_contract_census_preserves_terminal_locks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings, root = _coordination_root(tmp_path)
    _lifecycle_contract(root, "terminal", "terminal", terminal=True)
    lock = _write(root / "logs/observer/lifecycles/terminal/gates.jsonl.lock", "")
    broken = _write(root / "tasks/repo/broken/series-contract.md", "unreadable contract")
    assert removed_stores.run(_args(settings)) == 1
    assert lock.exists()
    assert str(broken) in capsys.readouterr().err


def test_every_listed_store_lies_under_the_observer_dashboard_or_runtime_folders() -> None:
    assert len(set(REMOVED_PART_STORE_PATHS)) == len(REMOVED_PART_STORE_PATHS)
    for relative in (*REMOVED_PART_STORE_PATHS, *STORED_GATE_LOG_PATTERNS):
        assert not Path(relative).is_absolute()
        assert ".." not in Path(relative).parts
        assert relative.startswith(
            (f"{WORKSPACE}/", "logs/dashboard/", "logs/observer/", "runtime/")
        )


def test_a_coordination_root_without_such_stores_names_none(tmp_path: Path) -> None:
    settings, root = _coordination_root(tmp_path)
    _write(root / WORKSPACE / "events.jsonl")

    assert removed_part_stores(root) == []
    assert removed_stores.run(_args(settings)) == 0
    assert "removed_part_stores" not in InstallSummary().report()
    assert "stored_gate_logs" not in InstallSummary().report()


def test_a_store_is_named_with_what_stands_beside_it_and_a_gate_log_without_its_lock(
    tmp_path: Path,
) -> None:
    _settings, root = _coordination_root(tmp_path)
    files = _populate(root)

    found = {
        Path(item["path"]).relative_to(root).as_posix(): item for item in removed_part_stores(root)
    }

    assert sorted(found) == [
        "logs/dashboard/terminal-sessions.json",
        "logs/observer/lifecycles/L1/events.jsonl",
        "logs/observer/lifecycles/L1/gates.jsonl",
        f"{WORKSPACE}/gates.jsonl",
        f"{WORKSPACE}/operator-inbox.jsonl",
        f"{WORKSPACE}/orchestration-nudges.jsonl",
        "runtime/structural-seat-locks",
    ]
    inbox = found[f"{WORKSPACE}/operator-inbox.jsonl"]
    assert inbox["files"] == [
        files["inbox"].as_posix(),
        files["inbox_copy"].as_posix(),
        files["inbox_lock"].as_posix(),
    ]
    assert inbox["bytes"] == 40
    assert inbox["gateLog"] is False
    # The lock of a store that is gone is still a file of the removed part.
    assert found["logs/dashboard/terminal-sessions.json"]["files"] == [
        files["session_lock"].as_posix()
    ]
    assert found["runtime/structural-seat-locks"]["files"] == [files["seat_lock"].as_posix()]
    gate_logs = [name for name, item in found.items() if item["gateLog"]]
    assert sorted(gate_logs) == [
        "logs/observer/lifecycles/L1/gates.jsonl",
        f"{WORKSPACE}/gates.jsonl",
    ]
    assert found[f"{WORKSPACE}/gates.jsonl"]["files"] == [files["workspace_gates"].as_posix()]
    listed = {name for item in found.values() for name in item["files"]}
    assert not listed & {files[name].as_posix() for name in STAYING}


def test_the_install_names_the_stores_once_and_deletes_none(tmp_path: Path) -> None:
    source_root = create_runtime_source(tmp_path)
    root = tmp_path / "ar-coordination"
    files = _populate(root)

    with patch.object(
        install_provider_watchers.lifecycle,
        "watchers_run",
        side_effect=lambda _args, action: watcher_result(action),
    ):
        summary = install_runtime.install_runtime(
            source_root,
            root,
            dry_run=False,
            provider_deps=install_runtime.ProviderDependencyInstall(
                settings={}, timeout=1800, enabled=False
            ),
        )

    report = summary.report()
    assert all(path.is_file() for path in files.values())
    assert report.count("removed_part_stores=[") == 1
    assert report.count("stored_gate_logs=[") == 1
    stores, gate_logs = report.split(" stored_gate_logs=[")
    for store in (
        f"{WORKSPACE}/operator-inbox.jsonl",
        f"{WORKSPACE}/orchestration-nudges.jsonl",
        "logs/dashboard/terminal-sessions.json",
        "runtime/structural-seat-locks",
    ):
        assert stores.count(f"{(root / store).as_posix()} (") == 1
    assert "gates.jsonl" not in stores
    assert gate_logs.count("gates.jsonl") == 2
    assert gate_logs.endswith(f"({STORED_GATE_WAY_OUT})")
    assert "agents-remember removed-stores" in STORED_GATE_WAY_OUT


def _answer_whole(answer: dict[str, Any]) -> dict[str, Any] | None:
    """A tool's answer as its caller reads it whole, whatever the width of the root.

    An answer over its bound arrives as the short form, and the detail file that form names
    holds the answer whole. Under a root so wide that the form cannot carry the path of that
    file it names none, and there is no whole answer to read.
    """
    if not answer.get("overflow"):
        return answer
    if "log" not in answer:
        return None
    return json.loads(Path(answer["log"]).read_text(encoding="utf-8"))


def test_the_registered_install_files_the_stores_and_answers_their_count(tmp_path: Path) -> None:
    """What a harness gets from ``runtime_install``: the list in the report, the count in the answer.

    Once under an ordinary root and once under a wide one, where the answer is over its bound:
    the report is filed whole either way, and the answer the caller reads carries the count.
    """
    digest = {
        "count": 7,
        "bytes": 78,
        "notice": REMOVED_STORES_NOTICE,
        "storedGates": STORED_GATE_WAY_OUT,
    }
    assert "`agents-remember removed-stores --dry-run` lists them" in REMOVED_STORES_NOTICE
    for base in (tmp_path / "ordinary", tmp_path / WIDE):
        base.mkdir(parents=True)
        settings, root = _coordination_root(base)
        files = _populate(root)
        # The event log of a part that stays takes the call's own event; the stores take nothing.
        stores = [path for name, path in files.items() if name not in STAYING]
        before = {path: path.read_bytes() for path in stores}

        with patch.object(
            install_provider_watchers.lifecycle,
            "watchers_run",
            side_effect=lambda _args, action: watcher_result(action),
        ):
            answer = call_registered_tool(load_config(settings), "runtime_install", {})

        assert answer["ok"] is True
        assert {path: path.read_bytes() for path in stores} == before
        # The whole result lies where every install files it, whatever the answer could carry.
        [report] = (root / "temp/tool-reports/runtime_install").glob("*-install.json")
        filed = json.loads(report.read_text(encoding="utf-8"))["removedPartStores"]
        assert {key: value for key, value in filed.items() if key != "stores"} == digest
        assert {
            Path(item["path"]).relative_to(root).as_posix(): (item["bytes"], item["gateLog"])
            for item in filed["stores"]
        } == {
            "logs/dashboard/terminal-sessions.json": (0, False),
            f"{WORKSPACE}/operator-inbox.jsonl": (40, False),
            f"{WORKSPACE}/orchestration-nudges.jsonl": (13, False),
            "runtime/structural-seat-locks": (2, False),
            f"{WORKSPACE}/gates.jsonl": (17, True),
            "logs/observer/lifecycles/L1/gates.jsonl": (3, True),
            "logs/observer/lifecycles/L1/events.jsonl": (3, False),
        }
        assert filed["stores"] == removed_part_stores(root)
        whole = _answer_whole(answer)
        if whole is not None:
            assert whole["removedPartStores"] == digest
            assert Path(whole["reportPath"]) == report


def test_a_dry_run_lists_the_stores_and_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings, root = _coordination_root(tmp_path)
    files = _populate(root)
    before = {path: path.read_bytes() for path in files.values()}

    assert removed_stores.run(_args(settings, dry_run=True)) == 0

    assert {path: path.read_bytes() for path in files.values()} == before
    out = capsys.readouterr().out
    assert f"would remove {files['nudges'].as_posix()} (13 bytes)" in out
    assert f"removed-stores: {STORED_GATE_WAY_OUT}." in out
    assert "dry run, 7 stores (9 files) left alone." in out


def test_the_command_removes_the_stores_and_leaves_what_other_parts_own(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings, root = _coordination_root(tmp_path)
    files = _populate(root)

    assert removed_stores.run(_args(settings)) == 0

    for name, path in files.items():
        assert path.exists() is (name in STAYING), name
    # A store folder that holds no file any more goes with its files.
    assert not (root / "runtime/structural-seat-locks").exists()
    assert (root / "runtime").is_dir()
    out = capsys.readouterr().out
    assert "removed 7 stores, freed" in out
    assert f"removed-stores: {STORED_GATE_WAY_OUT}." in out
    assert removed_part_stores(root) == []
    assert removed_stores.run(_args(settings)) == 0
    assert "no stores of removed parts remain" in capsys.readouterr().out


def test_a_store_folder_goes_whole_with_its_links_and_what_they_point_to_stays(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An installed application holds a dependency tree whose ``.bin`` entries are links."""
    settings, root = _coordination_root(tmp_path)
    application = root / "runtime" / "eve-agent"
    _write(application / "package.json", "{}")
    program = _write(application / "node_modules" / "eve" / "bin" / "eve.js", "run")
    outside = _write(tmp_path / "outside" / "tool.js", "kept")
    links = application / "node_modules" / ".bin"
    links.mkdir()
    (links / "eve").symlink_to(program)
    (links / "tool").symlink_to(outside)
    (links / "gone").symlink_to(application / "nowhere")

    (found,) = removed_part_stores(root)

    assert [Path(name).relative_to(application).as_posix() for name in found["files"]] == [
        "node_modules/.bin/eve",
        "node_modules/.bin/gone",
        "node_modules/.bin/tool",
        "node_modules/eve/bin/eve.js",
        "package.json",
    ]

    assert removed_stores.run(_args(settings)) == 0

    assert not application.exists()
    assert (root / "runtime").is_dir()
    assert outside.read_text(encoding="utf-8") == "kept"
    assert "removed 1 stores" in capsys.readouterr().out
    assert removed_part_stores(root) == []


def test_no_gate_log_means_no_sentence_about_gates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings, root = _coordination_root(tmp_path)
    _write(root / WORKSPACE / "supervisor-heartbeat.json", "{}")

    assert removed_stores.run(_args(settings)) == 0

    assert STORED_GATE_WAY_OUT not in capsys.readouterr().out


def test_a_linked_store_is_not_followed_and_a_folder_outside_the_root_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings, root = _coordination_root(tmp_path)
    outside = tmp_path / "outside"
    kept = _write(outside / "store" / "kept.txt", "kept")
    (root / "runtime").mkdir(parents=True)
    (root / "runtime" / "eve-carriers").symlink_to(outside / "store", target_is_directory=True)

    assert removed_part_stores(root) == []

    # A workspace folder that is itself a link leads the listed file outside the root.
    foreign = _write(outside / "workspace" / "expectation-rows.jsonl", "row\n")
    (root / "logs" / "observer").mkdir(parents=True)
    (root / WORKSPACE).symlink_to(outside / "workspace", target_is_directory=True)

    assert removed_stores.run(_args(settings)) == 1

    assert kept.read_text(encoding="utf-8") == "kept"
    assert foreign.read_text(encoding="utf-8") == "row\n"
    assert "outside the coordination root" in capsys.readouterr().err


@closed_by_mode
@pytest.mark.parametrize("unlisted", ["runtime/harness-control", WORKSPACE])
def test_a_place_that_cannot_be_listed_is_named_and_the_other_stores_are_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], unlisted: str
) -> None:
    """A store folder, and the folder in which the lock and the copies of a store file stand."""
    settings, root = _coordination_root(tmp_path)
    place = root / unlisted
    kept = _write(place / "operator-inbox.jsonl.pre-repair.bak", "kept")
    named = f"cannot read: {place.as_posix()}: [Errno 13] Permission denied"
    place.chmod(0o300)
    try:
        # What cannot be listed is not reported as absent, with or without removal.
        for dry_run in (True, False):
            assert removed_stores.run(_args(settings, dry_run=dry_run)) == 1
            captured = capsys.readouterr()
            assert named in captured.err
            assert "no stores of removed parts remain" not in captured.out

        independent = _write(root / "logs/dashboard/terminal-sessions.json", "{}")
        assert removed_stores.run(_args(settings)) == 1
        captured = capsys.readouterr()
        assert not independent.exists()
        # The place is named once for what it is; nothing in it was tried.
        assert named in captured.err and "removal failed" not in captured.err
        assert "incomplete, removed 1 of 1 files, freed 2 bytes." in captured.out
    finally:
        place.chmod(0o755)
    assert kept.read_text(encoding="utf-8") == "kept"


@closed_by_mode
def test_a_file_and_a_folder_that_cannot_be_removed_are_named_and_the_others_go(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings, root = _coordination_root(tmp_path)
    inbox = _write(root / WORKSPACE / "operator-inbox.jsonl", "row\n")
    sessions = _write(root / "logs/dashboard/terminal-sessions.json", "{}")
    control = _write(root / "runtime/harness-control/owner.json", "{}").parent
    locks = _write(root / "runtime/structural-seat-locks/seat-a/owner.json", "{}").parents[1]
    # A file cannot leave a closed folder, and an emptied store folder cannot leave one either.
    closed = (sessions.parent, root / "runtime")
    for folder in closed:
        folder.chmod(0o555)
    try:
        assert removed_stores.run(_args(settings)) == 1
    finally:
        for folder in closed:
            folder.chmod(0o755)

    captured = capsys.readouterr()
    for stuck in (sessions, control, locks):
        assert f"{stuck.as_posix()}: [Errno 13] Permission denied" in captured.err
    assert sessions.read_text(encoding="utf-8") == "{}"
    assert list(control.iterdir()) == [] and list(locks.iterdir()) == []
    assert not inbox.exists()
    assert "incomplete, removed 3 of 4 files, freed 8 bytes." in captured.out


@closed_by_mode
def test_a_folder_that_cannot_be_removed_holds_up_no_folder_beside_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings, root = _coordination_root(tmp_path)
    locks = root / "runtime/structural-seat-locks"
    beside = _write(locks / "seat-a/owner.json", "{}").parent
    stuck = _write(locks / "seat-b/held/owner.json", "{}").parent
    # The emptied folder cannot leave the closed one; the one beside it is not concerned.
    stuck.parent.chmod(0o555)
    try:
        assert removed_stores.run(_args(settings)) == 1
    finally:
        stuck.parent.chmod(0o755)

    captured = capsys.readouterr()
    assert f"removal failed: {stuck.as_posix()}: [Errno 13] Permission denied" in captured.err
    # The folders above the one that stayed are not tried, so they are not named.
    assert captured.err.count("Permission denied") == 1
    assert not beside.exists() and list(stuck.iterdir()) == []
    assert "incomplete, removed 2 of 2 files, freed 4 bytes." in captured.out
