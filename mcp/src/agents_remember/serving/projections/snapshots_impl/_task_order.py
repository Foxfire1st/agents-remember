"""Project a master's derived order and each leaf's two direct dependency lists."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.projection import TaskDependencyNode
from agents_remember.observer.projection_graph import (
    FrontierState,
    TaskExecutionGraphView,
    TaskExecutionNodeView,
    TaskExecutionPredecessorNode,
)
from agents_remember.tasks import TASK_DOCUMENT_SCHEMA, TaskDocument
from agents_remember.tasks.master_order import MasterOrder, MasterOrderTask, derive_master_order

from ._common import _read_json
from ._task_document_parse import projected_document


@dataclass(frozen=True)
class OrderFields:
    graph: TaskExecutionGraphView | None = None
    waits_for: tuple[TaskDependencyNode, ...] = ()
    needed_by: tuple[TaskDependencyNode, ...] = ()


def _reference(master: TaskDocument, path: Path, task: MasterOrderTask) -> TaskDocumentRef:
    return TaskDocumentRef(
        repository=master.repo,
        path=f"{path.parent.name}/{Path(task.file).with_suffix('.json').name}"
        if task.file
        else f"{path.parent.name}/{path.name}",
    )


def _dependency(path: Path, task: MasterOrderTask) -> TaskDependencyNode:
    return TaskDependencyNode(
        id=task.id,
        title=task.title,
        status="gone" if task.status == "abandoned" else task.status,
        docPath=str(path.parent / Path(task.file).with_suffix(".json")) if task.file else None,
    )


def _graph(master: TaskDocument, path: Path, order: MasterOrder) -> TaskExecutionGraphView:
    tasks = {task.id: task for task in order.tasks}
    ready_ids = set(order.ready_ids)
    nodes = []
    for task in order.tasks:
        if not task.file:
            continue
        state: FrontierState = "waiting"
        if task.status == "Completed":
            state = "landed"
        elif task.status == "abandoned":
            state = "abandoned"
        elif task.status == "inProgress" and not task.cycle:
            state = "in-flight"
        elif task.id in ready_ids:
            state = "ready"
        ref = _reference(master, path, task)
        nodes.append(
            TaskExecutionNodeView(
                nodeId=ref.key,
                kind="lump",
                masterRef=ref,
                masterTitle=f"{task.title} (gone)" if task.status == "gone" else task.title,
                waveIndex=task.wave,
                frontierState=state,
                cycle=True if task.cycle else None,
                predecessors=[
                    TaskExecutionPredecessorNode(
                        predecessorRef=_reference(master, path, tasks[dependency]),
                        predecessorTitle=(
                            f"{dependency} (gone)"
                            if tasks[dependency].status == "gone"
                            else tasks[dependency].title
                        ),
                        reason="waitsFor",
                    )
                    for dependency in task.waits_for
                ],
            )
        )
    return TaskExecutionGraphView(nodes=nodes)


def build_order_fields(documents: Mapping[Path, TaskDocument]) -> dict[Path, OrderFields]:
    """Use the already-read corpus once, never re-read a master's children per leaf."""
    result: dict[Path, OrderFields] = {}
    for path, master in documents.items():
        if master.kind != "master" or master.is_sprint:
            continue
        listed = {
            path.parent / Path(row.file).with_suffix(".json") for row in master.subTasks if row.file
        }
        children = {
            child.id: child
            for child_path in listed
            if (child := documents.get(child_path)) is not None and child.kind == "subTask"
        }
        order = derive_master_order(master, children)
        if order is None:
            continue
        result[path] = OrderFields(graph=_graph(master, path, order))
        tasks = {task.id: task for task in order.tasks}
        successors: dict[str, list[MasterOrderTask]] = {task.id: [] for task in order.tasks}
        for task in order.tasks:
            for dependency in task.waits_for:
                successors[dependency].append(task)
        for task in order.tasks:
            if task.file:
                result[path.parent / Path(task.file).with_suffix(".json")] = OrderFields(
                    waits_for=tuple(_dependency(path, tasks[ref]) for ref in task.waits_for),
                    needed_by=tuple(_dependency(path, item) for item in successors[task.id]),
                )
    return result


def read_order_fields(path: Path, doc: TaskDocument) -> dict[Path, OrderFields]:
    """On demand, read only the owning master and the explicit files it lists."""
    master_path = (
        path
        if doc.kind == "master"
        else (path.parent / Path(doc.master or "task.md").with_suffix(".json"))
    )
    documents = {path: doc}
    if master_path != path:
        payload = _read_json(master_path)
        master = (
            projected_document(payload)
            if payload is not None and payload.get("schema") == TASK_DOCUMENT_SCHEMA
            else None
        )
        if master is None:
            return {}
        documents[master_path] = master
    else:
        master = doc
    if master.kind != "master" or master.is_sprint:
        return {}
    for row in master.subTasks:
        child_path = master_path.parent / Path(row.file).with_suffix(".json") if row.file else None
        if child_path is None or child_path in documents:
            continue
        payload = _read_json(child_path)
        child = (
            projected_document(payload)
            if payload is not None and payload.get("schema") == TASK_DOCUMENT_SCHEMA
            else None
        )
        if child is not None:
            documents[child_path] = child
    return build_order_fields(documents)
