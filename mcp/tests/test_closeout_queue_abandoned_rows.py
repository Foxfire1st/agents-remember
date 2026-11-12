"""An abandoned row of a master is outside the closeout queue's walks of that master's rows."""

from __future__ import annotations

from typing import Any, cast

import pytest
from agents_remember.mcp.tools import closeout_queue_payload
from agents_remember.models.queue.closeout_queue import CloseoutQueueRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    DocStatus,
    SprintExecutionGraph,
    SubTaskRef,
    TaskDocument,
    read_task_doc,
    write_task_doc,
)
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.queue.closeout_queue_graph import graph_context
from test_master_retirement import World
from test_task_execution_topology import REPOSITORY

pytestmark = pytest.mark.integration

SPRINT = TaskDocumentRef(repository=REPOSITORY, path="sprint/task.json")
NOW = "2026-10-04T00:00:00+00:00"


def _set_rows(world: World, rows: list[SubTaskRef]) -> None:
    folder = world.tasks / "master-a"
    master = read_task_doc(folder / "task.json")
    write_task_doc(folder, master.model_copy(update={"subTasks": rows}))


def _rebuild(world: World) -> dict[str, Any]:
    """Rebuild the sprint's projection as the registered tool does, as the sprint's architect."""

    address = SPRINT.model_dump()
    return closeout_queue_payload(
        world.cfg,
        CloseoutQueueRequest.model_validate(
            {
                "action": "rebuild",
                "sprint_task_document_ref": address,
                "caller": {"role": "architect", "task_document_ref": address},
            }
        ),
    )


def _candidate_population(world: World) -> int:
    topology = TaskDocumentTopology(world.coord)
    authored = cast(SprintExecutionGraph, topology.resolve(SPRINT).document.executionGraph)
    graph = graph_context(topology, SPRINT, authored_graph=authored, strict_registers=False)
    return graph.semantic_topology_index.populationWork.candidateCount


def test_abandoned_rows_need_no_document_and_other_rows_still_do(tmp_path):
    world = World(tmp_path)
    folder = world.tasks / "master-a"
    (folder / "unreadable.json").write_text("{broken", encoding="utf-8")
    write_task_doc(
        folder,
        TaskDocument(
            id="KEPT",
            slug="kept",
            title="Kept",
            kind="subTask",
            repo=REPOSITORY,
            createdAt=NOW,
            status="Completed",
        ),
    )
    kept = SubTaskRef(number="KEPT", name="Kept", file="kept.md", status="Completed")

    def rows(status: DocStatus) -> list[SubTaskRef]:
        return [
            kept,
            SubTaskRef(number="NOFILE", name="No file", file="", status=status),
            SubTaskRef(number="GONE", name="Gone", file="gone.md", status=status),
            SubTaskRef(number="BROKEN", name="Broken", file="unreadable.md", status=status),
        ]

    _set_rows(world, rows("abandoned"))

    built = _rebuild(world)

    assert (built["state"], built["sourceProblems"]) == ("valid-built", [])
    assert _candidate_population(world) == 1  # the kept row only

    # The same three rows in any other state are demanded as before.
    _set_rows(world, rows("planning"))

    refused = _rebuild(world)

    assert refused["state"] == "invalid-empty"
    problems = {
        (problem["address"].rsplit("/", 1)[-1], problem["errorType"])
        for problem in refused["sourceProblems"]
    }
    assert problems == {
        ("gone.json", "task-document-not-found"),
        ("unreadable.json", "task-document-invalid"),
    }
    assert _candidate_population(world) == 3
