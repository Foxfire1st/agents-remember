"""A sprint that lost its last master to a retirement stays a sprint, for every tool.

The fixture is the shape of a real sprint: no execution graph, an integration branch and a seat.
After its only master is retired it commands nothing, and it is still read as a sprint by the
altitude, the role check, the scheduling mode, the topology validation, the closeout queue, the
linkage report, ``task_doc get`` and the dashboard's queue listing. No answer names a next action
that cannot work, and a master can be attached to it again.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
)
from agents_remember.mcp.tools.task_doc import task_doc_payload
from agents_remember.models.queue.closeout_queue import CloseoutQueueRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.projections.snapshots_impl._closeout_queue import (
    read_closeout_queues,
)
from agents_remember.tasks import SubTaskRef, TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.integration.integration_branch_authority import (
    integration_surfaces,
)
from agents_remember.worktrees.integration.integration_topology_collisions import (
    _is_standalone_atomic_master,
)
from agents_remember.worktrees.queue.closeout_queue import QueueActor, closeout_queue_tool
from agents_remember.worktrees.scheduling_mode import resolve_scheduling_mode
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    default_series_contract,
)
from test_master_retirement import World
from test_task_execution_topology import REPOSITORY, _master

pytestmark = pytest.mark.integration

SPRINT = TaskDocumentRef(repository=REPOSITORY, path="sprint/task.json")


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    data = made.sprint().model_dump(mode="json")
    data.update(
        orchestrates=["master-a"],
        subTasks=[data["subTasks"][0]],
        executionGraph=None,
        sections=[],
        integrationBranch="super",
        seats=[{"role": "architect", "label": "Sprint architect", "state": "active"}],
    )
    write_task_doc(made.tasks / "sprint", TaskDocument.model_validate(data))
    return made


def _task_doc(world: World, operation: str, **fields: Any) -> dict[str, Any]:
    return task_doc_payload(
        world.cfg,
        world.target,
        operation=operation,
        edit=TaskDocEdit(fields=fields),
        call=TaskDocCall(),
    )


def _read_as_a_sprint(world: World) -> dict[str, Any]:
    """What every reader of the sprint says about it."""
    topology = TaskDocumentTopology(world.coord)
    queue = closeout_queue_tool(
        world.cfg,
        CloseoutQueueRequest(action="rebuild", sprint_task_document_ref=SPRINT),
        actor=QueueActor(role="architect", task_document_ref=SPRINT),
    )
    listed = read_closeout_queues(world.coord, now=datetime.now(UTC))
    return {
        "altitude": topology.altitude(SPRINT),
        "architect": topology.validate_role(SPRINT, "architect"),
        "scheduling": resolve_scheduling_mode(topology, SPRINT).mode,
        "commanded": [
            master.ref.path for master in topology.commanded_masters(topology.resolve(SPRINT))
        ],
        "queue": (queue["state"], queue.get("nextAction")),
        "linkage": _task_doc(world, "linkage_report")["linkageFacts"],
        "get": _task_doc(world, "get").get("linkageFacts"),
        "listed": len(listed),
    }


@pytest.mark.usefixtures("worktree_services")
def test_a_sprint_that_lost_its_last_master_is_read_as_a_sprint_by_every_tool(world):
    before = _read_as_a_sprint(world)
    assert before == {
        "altitude": "sprint",
        "architect": "sprint",
        "scheduling": "atomic-sequential",
        "commanded": ["master-a/task.json"],
        "queue": ("valid-built", None),
        "linkage": [],
        "get": [],
        "listed": 1,
    }
    preview = world.retire(dry_run=True)
    assert preview["state"] == "would-retire"
    done = world.retire()
    assert done["ok"] and done["state"] == "retired" and "nextAction" not in done
    # The answer names no action that cannot work: the sprint's queue was rebuilt.
    assert [
        (effect["rebuild"]["outcome"], effect.get("nextAction"))
        for effect in done["projectionEffects"]
    ] == [("published", None)]
    sprint = world.sprint()
    assert sprint.orchestrates == [] and sprint.is_sprint
    assert sprint.integrationBranch == "super" and [seat.role for seat in sprint.seats] == [
        "architect"
    ]
    assert _read_as_a_sprint(world) == {**before, "commanded": []}
    assert TaskDocumentTopology(world.coord).validate_execution_topology(SPRINT) == ()
    # Its integration line is still the sprint's own, and the sprint is nobody's atomic master.
    asking = default_series_contract(
        ContractTask(
            name="another-task",
            repo_name=REPOSITORY,
            coordination_root=world.coord,
            workflow_kind="light-task",
            memory_mode="disabled",
        ),
        code=RepoBranchPlan(
            repo_path=world.code,
            source_branch="main",
            work_branch="ar/another-task",
            base_commit=world.base,
        ),
    )
    owned = {
        (surface.kind, surface.branch)
        for surface in integration_surfaces(asking)
        if surface.owner == SPRINT.key
    }
    assert owned == {("sprint-super", "super")}
    emptied = TaskDocumentTopology(world.coord).resolve(SPRINT)
    assert not _is_standalone_atomic_master(emptied, set())


@pytest.mark.usefixtures("worktree_services")
def test_an_emptied_sprint_is_edited_and_commands_a_master_again(world):
    assert world.retire()["ok"]
    # A generic edit keeps it a sprint, with its integration branch and its seats, and its own
    # queue projection follows the edit.
    edited = _task_doc(world, "set_field", title="The sprint, renamed")
    assert [
        (effect["sprintTaskDocumentRef"]["path"], effect["rebuild"]["outcome"])
        for effect in edited["projectionEffects"]
    ] == [("sprint/task.json", "published")]
    sprint = world.sprint()
    assert sprint.title == "The sprint, renamed" and sprint.integrationBranch == "super"
    assert sprint.is_sprint and len(sprint.seats) == 1
    # What a sprint may not do, it still may not: give up its integration branch, take an
    # execution nature, or author a graph for a master it does not command.
    for fields, refusal in (
        ({"integrationBranch": None}, r"must declare integrationBranch"),
        ({"executionNature": "atomic"}, r"an orchestration sprint has no executionNature"),
    ):
        with pytest.raises(ValueError, match=refusal):
            _task_doc(world, "set_field", **fields)
    with pytest.raises(ValueError) as refused:
        _task_doc(
            world,
            "author_execution_graph",
            mutations=[{"op": "add_node", "ref": world.refs[1].model_dump()}],
        )
    assert "requires an orchestration sprint document" not in str(refused.value)
    # It is no master: it is not retired as one, not attached to another sprint, and no typed
    # row of another sprint may name it.
    with pytest.raises(ValueError, match=r"is a sprint, not a master"):
        world.retire(fields={"masterRef": SPRINT.model_dump(), "reason": "retire the sprint"})
    other = _master(identity="sprint2", orchestrates=["master-c"]).model_copy(
        update={"integrationBranch": "super-2"}
    )
    write_task_doc(world.tasks / "sprint2", other)
    with pytest.raises(ValueError, match=r"task-sprint-linkage-target-is-sprint"):
        task_doc_payload(
            world.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="sprint2"),
            operation="attach_master",
            edit=TaskDocEdit(fields={"masterRef": SPRINT.model_dump(), "number": "9"}),
            call=TaskDocCall(),
        )
    typed = other.model_copy(
        update={
            "orchestrates": ["master-c", "sprint"],
            "subTasks": [SubTaskRef(number="9", name="The other sprint", masterRef=SPRINT)],
        }
    )
    topology = TaskDocumentTopology(world.coord)
    sprint2 = TaskDocumentRef(repository=REPOSITORY, path="sprint2/task.json")
    with pytest.raises(TaskDocumentRefError, match=r"must name a commanded master") as refused:
        topology.validate_sprint_linkage(sprint2, overrides={sprint2: typed})
    assert refused.value.status == "task-sprint-linkage-target-not-a-master"
    _task_doc(world, "attach_master", masterRef=world.refs[1].model_dump(), number="7")
    assert world.sprint().orchestrates == ["master-b"]
    after = _read_as_a_sprint(world)
    assert after["commanded"] == ["master-b/task.json"] and after["queue"] == ("valid-built", None)
