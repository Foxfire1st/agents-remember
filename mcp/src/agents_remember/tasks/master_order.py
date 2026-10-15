"""Pure, state-aware order derived from the explicitly listed leaves of a master.

The sprint's indexed DAG/wave analysis orders the condensed dependency graph.
Only the master-specific state anchors and legacy-cycle condensation live here;
no waves are persisted and no execution permission is decided here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import ValidationError

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_execution_edges import SprintExecutionEdge

from . import store as task_store
from .document import SprintExecutionNode, TaskDocument
from .execution_graph_validation import validate_execution_graph


@dataclass(frozen=True)
class MasterOrderTask:
    id: str
    title: str
    status: str
    file: str
    waits_for: tuple[str, ...]
    wave: int
    cycle: bool = False


@dataclass(frozen=True)
class MasterOrder:
    tasks: tuple[MasterOrderTask, ...]
    ready_ids: tuple[str, ...]
    cycle_ids: tuple[str, ...]


def load_master_children(
    task_root: Path,
    master: TaskDocument,
    overrides: Mapping[Path, TaskDocument] | None = None,
) -> dict[str, TaskDocument]:
    """Read only explicit listed files, never a directory census or inferred slug."""
    children: dict[str, TaskDocument] = {}
    for row in master.subTasks:
        if not row.file:
            continue
        path = task_root / Path(row.file).with_suffix(".json")
        try:
            child = (overrides or {}).get(path) or task_store.read_task_doc(path)
        except (OSError, ValueError, ValidationError):
            continue
        if child.kind == "subTask":
            children[child.id] = child
    return children


def dependency_components(waits_for: Mapping[str, Sequence[str]]) -> tuple[tuple[str, ...], ...]:
    """Iterative SCCs in input order, including self-cycles, without recursion limits."""
    adjacency = {
        key: tuple(dict.fromkeys(ref for ref in refs if ref in waits_for))
        for key, refs in waits_for.items()
    }
    reverse: dict[str, list[str]] = {key: [] for key in adjacency}
    for key, refs in adjacency.items():
        for ref in refs:
            reverse[ref].append(key)
    finished = _dependency_finish_order(adjacency)
    groups: list[tuple[str, ...]] = []
    assigned: set[str] = set()
    indexes = {key: index for index, key in enumerate(adjacency)}
    for start in reversed(finished):
        if start in assigned:
            continue
        members: list[str] = []
        todo = [start]
        while todo:
            key = todo.pop()
            if key in assigned:
                continue
            assigned.add(key)
            members.append(key)
            todo.extend(ref for ref in reverse[key] if ref not in assigned)
        groups.append(tuple(sorted(members, key=indexes.__getitem__)))
    return tuple(sorted(groups, key=lambda group: indexes[group[0]]))


def _dependency_finish_order(adjacency: Mapping[str, Sequence[str]]) -> list[str]:
    seen: set[str] = set()
    finished: list[str] = []
    for start in adjacency:
        if start in seen:
            continue
        stack = [(start, False)]
        while stack:
            key, leaving = stack.pop()
            if leaving:
                finished.append(key)
            elif key not in seen:
                seen.add(key)
                stack.append((key, True))
                stack.extend((ref, False) for ref in reversed(adjacency[key]) if ref not in seen)
    return finished


def dependency_cycles(waits_for: Mapping[str, Sequence[str]]) -> tuple[tuple[str, ...], ...]:
    cycles = []
    for group in dependency_components(waits_for):
        first = next(iter(group))
        if len(group) > 1 or first in waits_for[first]:
            cycles.append(group)
    return tuple(cycles)


def _task_population(
    master: TaskDocument, children: Mapping[str, TaskDocument]
) -> dict[str, MasterOrderTask]:
    tasks: dict[str, MasterOrderTask] = {}
    for row in master.subTasks:
        child = children.get(row.number)
        if child is not None and (child.id != row.number or child.kind != "subTask"):
            child = None
        tasks[row.number] = MasterOrderTask(
            id=row.number,
            title=child.title if child else row.name,
            status=row.status,
            file=row.file,
            waits_for=tuple(child.waitsFor or ()) if child else (),
            wave=-1,
        )
    for task in tuple(tasks.values()):
        for ref in task.waits_for:
            if ref not in tasks:
                tasks[ref] = MasterOrderTask(ref, ref, "gone", "", (), 0)
    return tasks


def _component_waves(
    tasks: Mapping[str, MasterOrderTask],
) -> tuple[dict[str, int], tuple[str, ...]]:
    stored = {key: task.waits_for for key, task in tasks.items()}
    full_groups = dependency_components(stored)
    cycle_groups = []
    for group in full_groups:
        first = next(iter(group))
        if len(group) > 1 or first in stored[first]:
            cycle_groups.append(group)
    unfinished = {
        key: tuple(
            ref for ref in task.waits_for if ref in tasks and tasks[ref].status != "Completed"
        )
        for key, task in tasks.items()
        if task.status != "Completed"
    }
    groups = tuple(
        remaining
        for group in full_groups
        if (remaining := tuple(key for key in group if key in unfinished))
    )
    owners = {key: index for index, group in enumerate(groups) for key in group}
    # Synthetic refs exist only inside this pure calculation; never stored or served.
    nodes = [
        SprintExecutionNode(ref=TaskDocumentRef(repository="derived-order", path=f"{index}.json"))
        for index in range(len(groups))
    ]
    pairs = sorted(
        {
            (owners[ref], owners[key])
            for key, refs in unfinished.items()
            for ref in refs
            if owners[ref] != owners[key]
        }
    )
    edges = [
        SprintExecutionEdge(predecessor=nodes[a].ref, successor=nodes[b].ref, reason="waitsFor")
        for a, b in pairs
    ]
    analysis = validate_execution_graph(nodes, edges)
    by_ref = {node.ref: index for index, node in enumerate(nodes)}
    prerequisites: dict[int, list[int]] = {index: [] for index in range(len(groups))}
    for a, b in analysis.resolvedEdgeIndexes:
        prerequisites[b].append(a)
    waves: dict[int, int] = {}
    for layer in analysis.waves:
        for node in layer:
            index = by_ref[node.ref]
            group = groups[index]
            if any(tasks[key].status in {"inProgress", "gone", "abandoned"} for key in group):
                waves[index] = 0
            else:
                waves[index] = max([1, *(waves[ref] + 1 for ref in prerequisites[index])])
    cycle_ids = {key for group in cycle_groups for key in group}
    task_waves = {key: waves[index] for key, index in owners.items()}
    for group in cycle_groups:
        wave = next((task_waves[key] for key in group if key in task_waves), -1)
        task_waves.update({key: wave for key in group})
    return (
        task_waves,
        tuple(key for key in tasks if key in cycle_ids),
    )


def derive_master_order(
    master: TaskDocument, children: Mapping[str, TaskDocument]
) -> MasterOrder | None:
    """Derive one immutable view; null means no structured order was declared."""
    if master.kind != "master" or master.is_sprint:
        return None
    if not any(
        child is not None and child.id == row.number and child.kind == "subTask" and child.waitsFor
        for row in master.subTasks
        for child in [children.get(row.number)]
    ):
        return None
    tasks = _task_population(master, children)
    waves, cycle_ids = _component_waves(tasks)
    cyclic = set(cycle_ids)
    ready = tuple(
        key
        for key, task in tasks.items()
        if task.status == "planning"
        and key not in cyclic
        and all(tasks[ref].status == "Completed" for ref in task.waits_for)
    )
    return MasterOrder(
        tasks=tuple(
            replace(task, wave=waves.get(key, -1), cycle=key in cyclic)
            for key, task in tasks.items()
        ),
        ready_ids=ready,
        cycle_ids=cycle_ids,
    )
