"""Refusals about a master reach the operator through the public tools with the repair named.

A sprint that commands a master whose folder was archived by hand is answered by
``curator_coherence`` and ``closeout_queue`` with the master, the ``0_archive`` location and the
retire operation; every other topology failure stays a bounded record. ``task_doc.retire_master``
names a series contract's worktree-group cell and the edit that clears it.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agents_remember.mcp.tools import closeout_queue_payload, curator_coherence_payload
from agents_remember.memory_quality.incremental_scope.candidate import observe_contract_task
from agents_remember.memory_quality.incremental_scope.errors import ScopeUnprovenError
from agents_remember.models.closeout.projection import MAX_CLOSEOUT_TEXT
from agents_remember.models.lifecycles.curator_coherence import CuratorCoherenceRequest
from agents_remember.models.queue.closeout_queue import CloseoutQueueRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.worktrees.queue import closeout_projection
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import write_contract
from test_closeout_queue import MASTER_A, SPRINT, QueueFixture
from test_master_retirement import World
from test_task_execution_topology import REPOSITORY

pytestmark = pytest.mark.integration

MEMBERSHIP_INVALID = "task-execution-graph-membership-invalid"
RETIRE = "task_doc.retire_master"


def _archive_by_hand(tasks: Path, master: str = "master-b") -> Path:
    """Move a commanded master under ``0_archive`` the way an operator's ``mv`` does."""

    archive = tasks / "0_archive"
    archive.mkdir()
    (tasks / master).rename(archive / master)
    return archive / master / "task.json"


def _queue(config: Any, sprint: TaskDocumentRef, action: str) -> dict[str, Any]:
    """Call ``closeout_queue`` as the registered tool does, as the sprint's architect."""

    address = sprint.model_dump()
    return closeout_queue_payload(
        config,
        CloseoutQueueRequest.model_validate(
            {
                "action": action,
                "sprint_task_document_ref": address,
                "caller": {"role": "architect", "task_document_ref": address},
            }
        ),
    )


def _coherence(fixture: QueueFixture, action: str) -> dict[str, Any]:
    """Call ``curator_coherence`` as the registered tool does, for the leaf of ``master-a``."""

    return curator_coherence_payload(
        fixture.cfg,
        CuratorCoherenceRequest.model_validate(
            {
                "action": action,
                "contract_path": fixture.contracts[MASTER_A].contract_path.as_posix(),
            }
        ),
    )


@pytest.fixture(scope="module")
def archived(tmp_path_factory) -> tuple[QueueFixture, Path]:
    """A graph sprint that still commands ``master-b`` after its folder was archived by hand."""

    fixture = QueueFixture(tmp_path_factory.mktemp("archived-master"))
    return fixture, _archive_by_hand(fixture.tasks)


@pytest.mark.parametrize("action", ["status", "prepare"])
def test_curator_coherence_names_the_archived_master_and_the_retire_operation(archived, action):
    fixture, found = archived

    with pytest.raises(CloseoutQueueError) as refused:
        _coherence(fixture, action)

    assert refused.value.status == MEMBERSHIP_INVALID
    for named in ("commands master 'master-b'", found.as_posix(), RETIRE):
        assert named in refused.value.detail, (named, refused.value.detail)


@pytest.mark.parametrize("action", ["status", "rebuild"])
def test_closeout_queue_names_the_archived_master_and_the_retire_operation(archived, action):
    fixture, found = archived

    answer = _queue(fixture.cfg, SPRINT, action)

    assert answer["state"] == "invalid-empty"
    (problem,) = answer["sourceProblems"]
    assert (problem["kind"], problem["address"], problem["state"], problem["errorType"]) == (
        "task",
        SPRINT.key,
        "invalid",
        MEMBERSHIP_INVALID,
    )
    for named in ("commands master 'master-b'", found.as_posix(), RETIRE):
        assert named in problem["repairAction"], (named, problem["repairAction"])


def test_the_memory_quality_scope_names_the_archived_master_too(archived):
    """The task observation of ``memory_quality_check`` reads the same graph and says the same."""
    fixture, found = archived

    with pytest.raises(ScopeUnprovenError) as refused:
        observe_contract_task(fixture.contracts[MASTER_A])

    failure = refused.value.failure
    assert failure.code == "task-owner-unavailable"
    for named in ("commands master 'master-b'", found.as_posix(), RETIRE):
        assert named in failure.detail, (named, failure.detail)


def test_another_topology_failure_stays_a_bounded_record_in_both_tools(tmp_path):
    fixture = QueueFixture(tmp_path)
    sprint = read_task_doc(fixture.tasks / "sprint" / "task.json")
    # A second alias of master-b: the same status, but reader text that names the entry.
    write_task_doc(
        fixture.tasks / "sprint",
        sprint.model_copy(update={"orchestrates": ["master-a", "master-b", "MASTER-B"]}),
    )

    with pytest.raises(CloseoutQueueError) as refused:
        _coherence(fixture, "status")
    (problem,) = _queue(fixture.cfg, SPRINT, "status")["sourceProblems"]

    assert refused.value.status == problem["errorType"] == MEMBERSHIP_INVALID
    assert json.loads(refused.value.detail) == {
        "errorType": "TaskDocumentRefError",
        "name": "execution-graph",
        "observed": {"state": "blocked"},
        "side": "task-document",
        "stage": "queue-topology-validation",
    }
    assert problem["repairAction"] == "repair canonical task topology"
    with pytest.raises(ScopeUnprovenError) as unproven:
        observe_contract_task(fixture.contracts[MASTER_A])
    assert unproven.value.failure.detail == "canonical task owner refused: CloseoutQueueError"


def _world_sprint() -> TaskDocumentRef:
    return TaskDocumentRef(repository=REPOSITORY, path="sprint/task.json")


def test_a_text_longer_than_the_field_keeps_master_location_and_operation(tmp_path):
    world = World(tmp_path)
    archive = world.tasks / "0_archive"
    # Forty archived documents answer to the same master id, so the location list alone is longer
    # than one closeout text field.
    names = [f"{index:02d}-" + "x" * 200 for index in range(40)]
    for name in names:
        shutil.copytree(world.tasks / "master-b", archive / name)
    shutil.rmtree(world.tasks / "master-b")

    (problem,) = _queue(world.cfg, _world_sprint(), "status")["sourceProblems"]

    repair = problem["repairAction"]
    assert len(repair) == MAX_CLOSEOUT_TEXT
    first = (archive / names[0] / "task.json").as_posix()
    for named in ("commands master 'master-b'", first, " [...] ", RETIRE):
        assert named in repair, named
    assert repair.endswith("before archival.")


def test_a_master_that_goes_missing_between_census_and_graph_is_still_named(tmp_path, monkeypatch):
    world = World(tmp_path)
    found = _archive_by_hand(world.tasks)
    # The census reads membership first and the graph validates it again; a folder moved between
    # the two reads is seen by the graph only.
    monkeypatch.setattr(
        closeout_projection,
        "commanded_sprint_masters",
        lambda topology, sprint, overrides=None: tuple(
            topology.resolve(ref) for ref in topology.children(sprint.ref)
        ),
    )

    (problem,) = _queue(world.cfg, _world_sprint(), "status")["sourceProblems"]

    assert problem["errorType"] == MEMBERSHIP_INVALID
    for named in ("commands master 'master-b'", found.as_posix(), RETIRE):
        assert named in problem["repairAction"], (named, problem["repairAction"])


def test_a_recorded_worktree_group_of_another_layout_does_not_refuse_a_retirement(tmp_path):
    world = World(tmp_path)
    contract = world.series()
    recorded = tmp_path / "elsewhere" / "master-a-ar"
    write_contract(contract.contract_path, replace(contract, worktree_group=recorded))
    before = world.snapshot()

    assert world.retire(dry_run=True)["state"] == "would-retire"
    assert world.snapshot() == before
    assert world.retire()["state"] == "retired"
