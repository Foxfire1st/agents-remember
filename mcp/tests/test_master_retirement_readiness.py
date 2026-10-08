"""Retirement readiness and the action that clears each refusal.

Readiness refuses open work of the master's leaves, and an unfinished or unreadable operation
of the master's own enclosure.

Open work is a leaf worktree directory that exists, a leaf branch that holds commits its landing
line does not reach, and an operation record that is unfinished or cannot be read. Every test
that sees a refusal carries out the action the refusal names, exactly as it is written, and then
repeats the request. The layout and the age of a contract never refuse: they are facts, reported
by the dry run and written into the retirement record.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anyio
import pytest
from agents_remember.tasks import SubTaskRef, TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees import task_retirement as readiness
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleLocatorObservation,
    lifecycle_operation_locator_path,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location_errors import (
    LifecycleOperationLocationError,
)
from agents_remember.worktrees.modules.terminal_validation import require_series_children_retired
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncSideRecord,
    sync_operation_path,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract
from test_master_retirement import World, _historical_operation
from test_task_execution_topology import REPOSITORY
from test_worktree_start_preview import LEAF, MASTER, REPO, PreviewFixture
from test_worktree_support import commit_file, git, init_repo

pytestmark = pytest.mark.integration


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def _leaf(world: World, series: WorktreeContract, leaf_id: str = "L1", **cells: Any):
    """A cleaned-up leaf of the master: a row, a contract, and no worktree or branch."""
    slug = leaf_id.lower()
    path = series.task_root / "enclosures" / slug / "series-contract.md"
    child = replace(
        series,
        kind="leaf",
        leaf_id=leaf_id,
        contract_path=path,
        parent_contract_path=series.contract_path,
        code_source_branch=series.code_work_branch,
        code_work_branch=f"leaf-{slug}",
        code_worktree=world.coord.parent / "leaf-worktrees" / slug,
    )
    child = replace(child, **cells)
    write_contract(path, child)
    master = read_task_doc(series.task_root / "task.json")
    if leaf_id not in {row.number for row in master.subTasks}:
        master.subTasks = [
            *master.subTasks,
            SubTaskRef(number=leaf_id, name=f"Leaf {leaf_id}", status="Completed"),
        ]
        write_task_doc(series.task_root, master)
    return child


def _refusal(world: World) -> str:
    """The refusal of the dry run and of the real request, which must agree and change nothing."""
    before, refs = world.snapshot(), git(world.code, "show-ref")
    said = []
    for dry_run in (True, False):
        with pytest.raises(ValueError, match="has open work, so it is not retired") as refused:
            world.retire(dry_run=dry_run)
        said.append(str(refused.value))
    assert said[0] == said[1] and world.snapshot() == before
    assert git(world.code, "show-ref") == refs
    assert said[0].rstrip(". ").endswith("repeat task_doc.retire_master")
    return said[0]


def _carry_out(refusal: str) -> list[str]:
    """Run every command a refusal names, exactly as the refusal writes it."""
    commands = re.findall(
        r"(git -C \S+ (?:worktree remove|branch -D) \S+|mv (?:-n )?\S+ \S+|rm \S+)", refusal
    )
    for command in commands:
        subprocess.run(shlex.split(command), check=True, capture_output=True)
    return commands


def _facts(world: World) -> list[str]:
    preview = world.retire(dry_run=True)
    assert preview["state"] == "would-retire", preview
    return preview["readinessFacts"]


def test_a_leaf_worktree_directory_that_exists_is_open_work_and_the_named_command_removes_it(
    world,
):
    series = world.series()
    child = _leaf(world, series)
    assert child.code_worktree is not None
    git(world.code, "branch", child.code_work_branch, series.code_work_branch)
    git(world.code, "worktree", "add", "-q", child.code_worktree.as_posix(), child.code_work_branch)
    refusal = _refusal(world)
    assert f"leaf 'L1': the code worktree directory {child.code_worktree} exists" in refusal
    assert f"git -C {world.code} worktree remove {child.code_worktree}" in refusal
    assert "worktree_cleanup" not in refusal and "worktree_abandon" not in refusal
    assert len(_carry_out(refusal)) == 1 and not child.code_worktree.exists()
    facts = _facts(world)
    # The branch holds nothing beyond its landing line: it stays, and the record lists it.
    kept = (
        f"the code branch 'leaf-l1' in {world.code} exists and holds nothing beyond 'ar/master-a'"
    )
    assert any(kept in fact and "leaves it in place" in fact for fact in facts), facts
    done = world.retire()
    assert done["state"] == "retired" and done["readinessFacts"] == facts
    assert world.sprint().subTasks[0].retirement.readinessFacts == facts
    assert "leaf-l1" in git(world.code, "branch", "--list", "leaf-l1")


def test_a_leaf_branch_with_unlanded_commits_is_open_work_on_either_side(world, tmp_path):
    series = world.series()
    memory = tmp_path / "memory"
    memory_base = init_repo(memory)
    git(memory, "branch", "memory-line")
    child = _leaf(
        world,
        series,
        memory_mode="external",
        memory_repo_path=memory,
        memory_source_branch="memory-line",
        memory_work_branch="leaf-l1-memory",
        memory_base_commit=memory_base,
        memory_worktree=tmp_path / "gone-memory-worktree",
        ledger_path=memory / "memory.md",
        memory_state="",
    )
    for repository, branch, line, count in (
        (world.code, child.code_work_branch, series.code_work_branch, 2),
        (memory, "leaf-l1-memory", "memory-line", 1),
    ):
        git(repository, "checkout", "-q", "-b", branch, line)
        for number in range(count):
            commit_file(repository, f"work-{number}.txt", "unlanded\n", f"Unlanded work {number}")
        git(repository, "checkout", "-q", "main")
    refusal = _refusal(world)
    assert (
        f"leaf 'L1': the code branch 'leaf-l1' in {world.code} holds 2 commit(s) that are not "
        "reachable from 'ar/master-a', the line it lands on" in refusal
    )
    assert (
        f"leaf 'L1': the memory branch 'leaf-l1-memory' in {memory} holds 1 commit(s) that are "
        "not reachable from 'memory-line', the line it lands on" in refusal
    )
    assert "(1) " in refusal and "(2) " in refusal and "land them first to keep them" in refusal
    assert _carry_out(refusal) == [
        f"git -C {world.code} branch -D leaf-l1",
        f"git -C {memory} branch -D leaf-l1-memory",
    ]
    assert _facts(world) is not None and world.retire()["state"] == "retired"


def test_the_masters_own_branch_and_worktree_never_refuse_and_are_listed_with_their_state(
    world, tmp_path
):
    series = world.series()
    own = tmp_path / "own-worktree"
    git(world.code, "worktree", "add", "-q", own.as_posix(), series.code_work_branch)
    commit_file(own, "master-work.txt", "not landed on the sprint line\n", "Master work")
    commit_file(own, "master-work-2.txt", "nor this\n", "More master work")
    commit_file(own, "master-work-3.txt", "nor this\n", "Still more master work")
    write_contract(series.contract_path, replace(series, cleanup="pending", code_worktree=own))
    facts = _facts(world)
    assert any(f"the master's own code worktree {own} exists" in fact for fact in facts), facts
    assert any(
        f"the master's own code branch 'ar/master-a' in {world.code} holds 3 commit(s) that are "
        "not reachable from 'super'" in fact
        for fact in facts
    ), facts
    assert any("records cleanup 'pending'; no open work was found" in fact for fact in facts)
    tip = git(world.code, "rev-parse", "ar/master-a")
    assert world.retire()["state"] == "retired"
    assert world.sprint().subTasks[0].retirement.readinessFacts == facts
    assert own.is_dir() and git(world.code, "rev-parse", "ar/master-a") == tip


def _old_group(world: World, series: WorktreeContract) -> None:
    write_contract(
        series.contract_path, replace(series, worktree_group=series.task_root / "enclosures")
    )


def _sprint_line_as_leaf_source(world: World, series: WorktreeContract) -> None:
    _leaf(world, series, code_source_branch=series.code_source_branch)


def _other_series_contract_path(world: World, series: WorktreeContract) -> None:
    _leaf(world, series)
    master = read_task_doc(series.task_root / "task.json")
    master.subTasks = [SubTaskRef(number="L1", name="Leaf", file="l1.md", status="Completed")]
    write_task_doc(series.task_root, master)
    write_task_doc(
        series.task_root,
        TaskDocument(
            id="L1",
            slug="l1",
            title="Leaf",
            kind="subTask",
            repo=REPOSITORY,
            createdAt="2026-10-02T00:00:00+00:00",
            status="Completed",
            seriesContractPath="../somewhere-else/series-contract.md",
        ),
    )


def _leaf_document_gone(world: World, series: WorktreeContract) -> None:
    master = read_task_doc(series.task_root / "task.json")
    master.subTasks = [SubTaskRef(number="L1", name="Leaf", file="l1.md", status="Completed")]
    write_task_doc(series.task_root, master)


def _leaf_named_by_no_row(world: World, series: WorktreeContract) -> None:
    _leaf(world, series, "L9")
    master = read_task_doc(series.task_root / "task.json")
    master.subTasks = []
    write_task_doc(series.task_root, master)


def _foreign(cell: str):
    def alter(world: World, series: WorktreeContract) -> None:
        wrong = "foreign" if cell in {"repo_name", "task_name"} else world.coord / "foreign"
        write_contract(series.contract_path, replace(series, **{cell: wrong}))

    return alter


LAYOUTS = {
    "a worktree group of the old layout": (_old_group, None),
    "a leaf that names the sprint line as its source": (_sprint_line_as_leaf_source, None),
    "a leaf document with another seriesContractPath": (_other_series_contract_path, None),
    "a row whose document is gone": (_leaf_document_gone, "its document"),
    "a leaf contract that no row names": (_leaf_named_by_no_row, "no row of the master"),
    "another coordination root": (_foreign("coordination_root"), "coordination.root"),
    "another task root": (_foreign("task_root"), "coordination.task_root"),
    "another repository name": (_foreign("repo_name"), "repo_name"),
    "another task name": (_foreign("task_name"), "task_name"),
    "a code repository that is none here": (_foreign("code_repo_path"), "is no Git repository"),
}


@pytest.mark.parametrize("layout", LAYOUTS)
def test_the_layout_and_the_age_of_a_contract_never_refuse(world, layout):
    arrange, fact = LAYOUTS[layout]
    arrange(world, world.series())
    before = world.snapshot()
    facts = _facts(world)
    assert world.snapshot() == before
    if fact is not None:
        assert any(fact in entry and "refuse" not in entry for entry in facts), facts
    done = world.retire()
    assert done["state"] == "retired" and done["readinessFacts"] == facts
    assert world.sprint().subTasks[0].retirement.readinessFacts == facts


def test_an_operation_report_where_an_older_layout_kept_it_is_a_fact(world):
    """No tool of this build can finish it, so it is told and written down, and does not refuse."""
    series = world.series()
    _old_group(world, series)
    reports = series.task_root / "enclosures" / "reports"
    reports.mkdir(parents=True)
    unfinished = reports / "closeout-operation.json"
    unfinished.write_text(
        json.dumps({"status": "input-required", "phase": "contract-finalization"})
    )
    (reports / "integrate-operation.json").write_text(json.dumps({"status": "completed"}))
    broken = reports / "direct-landing-operation.json"
    broken.write_text("{broken")
    facts = _facts(world)
    assert (
        f"{unfinished} is an operation report of an older layout and records status "
        "'input-required', phase 'contract-finalization'; this build keeps operation records "
        "under .lifecycle and does not read it as one" in facts
    )
    assert any(
        f"{broken} is an operation report of an older layout and cannot be read" in fact
        for fact in facts
    )
    assert not any("integrate-operation.json" in fact for fact in facts)
    assert world.retire()["state"] == "retired"
    assert world.sprint().subTasks[0].retirement.readinessFacts == facts


def test_the_series_validation_of_the_terminal_mutation_still_asks_for_todays_layout(world):
    """The readiness is a function of its own; the census of an atomic series is as it was."""
    series = world.series()
    _old_group(world, series)
    old = replace(series, worktree_group=series.task_root / "enclosures")
    with pytest.raises(RuntimeError, match=r"coordination\.worktree_group cell.*Set coordination"):
        require_series_children_retired(old)
    assert _facts(world) is not None


def test_a_contract_that_cannot_be_read_refuses_and_names_the_file(world):
    series = world.series()
    child = _leaf(world, series)
    child.contract_path.write_text("not a contract\n", encoding="utf-8")
    before = world.snapshot()
    for dry_run in (True, False):
        with pytest.raises(ValueError, match="cannot read contract") as refused:
            world.retire(dry_run=dry_run)
        assert child.contract_path.as_posix() in str(refused.value)
        assert "repair that file before task_doc.retire_master" in str(refused.value)
    assert world.snapshot() == before


def test_an_enclosure_with_a_live_operation_locator_is_open_work_and_names_the_tools(world):
    series = world.series()
    child = _leaf(world, series)
    assert child.code_worktree is not None
    child.code_worktree.mkdir(parents=True)
    inspect = readiness.inspect_lifecycle_operation_locator

    def live(coordination_root: Path, contract_path: Path) -> LifecycleLocatorObservation:
        found = inspect(coordination_root, contract_path)
        if contract_path != child.contract_path:
            return found
        return LifecycleLocatorObservation(found.path, "addressable")

    with patch.object(readiness, "inspect_lifecycle_operation_locator", side_effect=live):
        refusal = _refusal(world)
    assert "is addressable" in refusal and "worktree_operation_control" in refusal
    # While the locator is live the product's own tools act on the enclosure, and are named.
    assert (
        f"the code worktree directory {child.code_worktree} exists; finish it with "
        f"worktree_cleanup, or give it up with worktree_abandon (contract_path="
        f"{child.contract_path})" in refusal
    )
    assert "git -C" not in refusal


@pytest.fixture
def paired_world(tmp_path):
    """A commanded organizational master, so the leaf is its only live enclosure."""
    world = PreviewFixture(tmp_path / "world", external=True, commanded=True)
    master = read_task_doc(world.task_root / "task.json")
    write_task_doc(world.task_root, master.model_copy(update={"executionNature": "organizational"}))
    world._document(
        world.task_root.parent / "kept",
        id="KEPT",
        slug="task",
        kind="master",
        executionNature="atomic",
    )
    sprint = read_task_doc(world.task_root.parent / "sprint" / "task.json")
    data = sprint.model_dump(mode="json")
    data.update(
        orchestrates=[MASTER, "kept"],
        executionGraph={
            "nodes": [
                {"repository": REPO, "path": f"{name}/task.json"} for name in (MASTER, "kept")
            ],
            "edges": [],
        },
    )
    write_task_doc(world.task_root.parent / "sprint", TaskDocument.model_validate(data))
    return world


def test_a_real_paired_enclosures_named_tool_is_carried_out_then_retirement_is_repeated(
    paired_world,
):
    """All repositories, control-plane authority and tool calls belong to the scratch world."""
    world = paired_world
    started = world.start(dry_run=False)
    assert not started.isError, started
    assert started.structuredContent is not None and started.structuredContent["ok"], started
    path = Path(started.structuredContent["enclosure_path"])
    child = readiness.load_contract(path)
    assert child.code_worktree is not None and child.memory_worktree is not None
    assert child.code_worktree.exists() and child.memory_worktree.exists()
    locator = readiness.inspect_lifecycle_operation_locator(world.coord, path)
    assert locator.state == "addressable", locator
    world.record("real-locator", {"path": str(locator.path), "state": locator.state})
    arguments: dict[str, object] = {
        "repo_id": REPO,
        "task_name": "sprint",
        "operation": "retire_master",
        "fields": {
            "masterRef": {"repository": REPO, "path": f"{MASTER}/task.json"},
            "reason": "The scratch master is deliberately given up.",
        },
        "dry_run": True,
    }
    before = world.snapshot()
    refused = anyio.run(world._call, "task_doc", arguments)
    world.record("retirement-refused", refused.model_dump(mode="json"))
    assert refused.isError, refused
    text = " ".join(getattr(content, "text", "") for content in refused.content)
    assert f"worktree_abandon (contract_path={path})" in text, text
    assert str(child.code_worktree) in text and str(child.memory_worktree) in text
    assert world.snapshot() == before
    abandon_arguments: dict[str, object] = {"contract_path": str(path)}
    abandoned = anyio.run(world._call, "worktree_abandon", abandon_arguments)
    world.record("named-tool-carried-out", abandoned.model_dump(mode="json"))
    assert not abandoned.isError and abandoned.structuredContent is not None, abandoned
    assert abandoned.structuredContent["ok"] and abandoned.structuredContent["state"] == "abandoned"
    assert not child.code_worktree.exists() and not child.memory_worktree.exists()
    assert (
        readiness.inspect_lifecycle_operation_locator(world.coord, path).state
        == "terminal-archived"
    )
    repeated = anyio.run(world._call, "task_doc", arguments)
    world.record("same-retirement-repeated", repeated.model_dump(mode="json"))
    assert not repeated.isError and repeated.structuredContent is not None, repeated
    assert repeated.structuredContent["state"] == "would-retire"
    assert git(world.code, "branch", "--list", child.code_work_branch) == ""
    assert git(world.memory, "branch", "--list", child.memory_work_branch) == ""
    assert child.leaf_id == LEAF


def _broken(name: str):
    def arrange(world: World, series: WorktreeContract) -> Path:
        record = series.worktree_group / ".lifecycle" / name
        record.parent.mkdir(parents=True)
        record.write_text("{broken")
        return record

    return arrange


def _unfinished_generation(world: World, series: WorktreeContract) -> Path:
    record = series.worktree_group / ".lifecycle" / "closeout-operation.generation-1.json"
    record.parent.mkdir(parents=True)
    record.write_text(_historical_operation(series, "running").model_dump_json())
    return record


def _unfinished_sync(world: World, series: WorktreeContract) -> Path:
    side = SyncSideRecord(
        side="code",
        repository=world.code.as_posix(),
        worktree=world.code.as_posix(),
        sourceBranch=series.code_source_branch,
        workBranch=series.code_work_branch,
        sourceCommit=world.base,
        preSyncHead=world.base,
        baseCommit=world.base,
        backupRef="refs/agents-remember/sync/test/before",
        sourceBackupRef="refs/agents-remember/sync/test/source",
        baseBackupRef="refs/agents-remember/sync/test/base",
        plan="merge",
    )
    record = SyncOperationRecord(
        generation=1,
        contractPath=series.contract_path.as_posix(),
        taskId=series.task_id,
        contractKind="series",
        codeBaseFrom=world.base,
        phase="running-code",
        code=side,
        createdAt="2026-10-02T00:00:00+00:00",
        updatedAt="2026-10-02T00:00:00+00:00",
    )
    journal = sync_operation_path(series.worktree_group)
    journal.parent.mkdir(parents=True)
    journal.write_text(record.model_dump_json())
    return journal


def _unreadable_locator(world: World, series: WorktreeContract) -> Path:
    locator = lifecycle_operation_locator_path(world.coord, series.contract_path)
    locator.parent.mkdir(parents=True, exist_ok=True)
    locator.write_text("{broken")
    return locator


OPERATIONS = {
    "a current record that cannot be read": (_broken("closeout-operation.json"), "mv"),
    "a retained generation that cannot be read": (
        _broken("closeout-operation.generation-1.json"),
        "mv",
    ),
    "a legacy-intent generation that cannot be read": (
        _broken("closeout-operation.legacy-missing-intent-generation-1.json"),
        "mv",
    ),
    "a retained generation that is unfinished": (_unfinished_generation, "mv"),
    "a sync that is unfinished": (_unfinished_sync, "mv"),
    "a locator that cannot be read": (_unreadable_locator, "rm"),
}


@pytest.mark.parametrize("operation", OPERATIONS)
def test_an_operation_record_that_is_unfinished_or_unreadable_is_open_work(world, operation):
    """Without a live locator no lifecycle tool acts on the enclosure, so none is named."""
    arrange, verb = OPERATIONS[operation]
    record = arrange(world, world.series())
    kept = record.read_bytes()
    refusal = _refusal(world)
    assert record.as_posix() in refusal and record.read_bytes() == kept
    for tool in ("worktree_operation_control", "worktree_sync", "worktree_cleanup"):
        assert tool not in refusal, refusal
    (command,) = _carry_out(refusal)
    assert command.startswith(f"{verb}{' -n' if verb == 'mv' else ''} {record}")
    assert not record.exists()
    assert _facts(world) is not None and world.retire()["state"] == "retired"


@pytest.mark.parametrize("operation", [name for name in OPERATIONS if "locator" not in name])
def test_an_operation_record_of_an_enclosure_with_a_live_locator_names_the_tool(world, operation):
    series = world.series()
    record = OPERATIONS[operation][0](world, series)
    inspect = readiness.inspect_lifecycle_operation_locator

    def live(coordination_root: Path, contract_path: Path) -> LifecycleLocatorObservation:
        return LifecycleLocatorObservation(
            inspect(coordination_root, contract_path).path, "addressable"
        )

    with patch.object(readiness, "inspect_lifecycle_operation_locator", side_effect=live):
        refusal = _refusal(world)
    tool = "worktree_sync" if "sync" in operation else "worktree_operation_control"
    assert tool in refusal and f"mv -n {record}" not in refusal, refusal
    if "sync" not in operation:
        assert record.as_posix() in refusal


def test_the_manual_move_keeps_an_earlier_given_up_record(world):
    series = world.series()
    record = _unfinished_generation(world, series)
    original = record.read_bytes()
    destination = series.worktree_group / (record.name + ".given-up")
    destination.write_text("an earlier operation\n")
    refusal = _refusal(world)
    assert "choose an unused destination" in refusal
    assert "Verify that the original record has moved before repeating" in refusal
    assert "A later build's adoption route replaces this manual step" in refusal
    (command,) = _carry_out(refusal)
    assert destination.read_text() == "an earlier operation\n" and record.read_bytes() == original
    assert _refusal(world) == refusal
    unused = destination.with_suffix(".given-up-2")
    subprocess.run([*shlex.split(command)[:-1], str(unused)], check=True, capture_output=True)
    assert unused.read_bytes() == original and not record.exists()
    assert world.retire(dry_run=True)["state"] == "would-retire"


def test_a_locator_that_cannot_be_looked_for_is_open_work_and_no_tool_is_named(world):
    series = world.series()
    child = _leaf(world, series)
    assert child.code_worktree is not None
    child.code_worktree.mkdir(parents=True)

    def lost(coordination_root: Path, contract_path: Path) -> LifecycleLocatorObservation:
        raise LifecycleOperationLocationError(
            "operation-location-unconfined",
            "the contract address leaves the coordination root",
            expected={},
            observed={},
        )

    with patch.object(readiness, "inspect_lifecycle_operation_locator", side_effect=lost):
        refusal = _refusal(world)
    assert (
        "its operation locator cannot be looked for (the contract address leaves the "
        "coordination root)" in refusal
    )
    # Nothing says that a tool can act on such an enclosure, so the worktree gets its command.
    assert f"git -C {world.code} worktree remove {child.code_worktree}" in refusal
    assert "worktree_cleanup" not in refusal


def test_the_repositorys_own_checkout_is_nobodys_leaf_worktree(world):
    series = world.series()
    _leaf(world, series, code_worktree=world.code, code_work_branch=series.code_work_branch)
    facts = _facts(world)
    assert any("records the repository's own checkout" in fact for fact in facts), facts
    assert any("which is the master's own line" in fact for fact in facts), facts
