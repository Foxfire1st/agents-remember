"""The one join of authored tasks, worktree contracts and host-agent activity."""

from __future__ import annotations

import posixpath
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.host_activity import HostActivitySnapshot, PublishedAgentActivity
from agents_remember.observer.projection import (
    EnclosureNode,
    TaskCounts,
    TaskDocNode,
    TaskEntry,
    TaskState,
    TaskSummary,
)

_STAGES = {
    "planning": "planned",
    "inProgress": "started",
    "abandoned": "aborted",
    "Completed": "completed",
}


@dataclass(frozen=True)
class DeclaredTaskSource:
    reference: TaskDocumentRef | None
    id: str
    title: str
    status: str
    scope: str | None = None


@dataclass(frozen=True)
class TaskDocumentSource:
    path: Path
    summary: TaskDocNode
    children: tuple[DeclaredTaskSource, ...] = ()
    declared_parent: TaskDocumentRef | None = None

    @property
    def reference(self) -> TaskDocumentRef:
        return TaskDocumentRef(
            repository=self.path.parents[1].name,
            path=f"{self.path.parent.name}/{self.path.name}",
        )


@dataclass(frozen=True)
class TaskEntries:
    tasks: list[TaskEntry]
    counts: TaskCounts
    unbound_enclosures: list[EnclosureNode]


def task_state(
    status: str,
    agents: Iterable[PublishedAgentActivity],
    *,
    host_known: bool,
) -> TaskState:
    """Terminal/planned stage wins; only a whole host observation can say paused."""
    stage = _STAGES[status]
    if stage != "started":
        return TaskState.model_validate({"stage": stage})
    if not host_known:
        return TaskState(stage="started", activity="unknown")
    observed = list(agents)
    working = [agent for agent in observed if agent.state in {"in-turn", "permission"}]
    contributors = working or observed
    times = [agent.eventAt for agent in contributors if agent.eventAt is not None]
    event = max(times) if times else None
    return TaskState(
        stage="started",
        activity="works" if working else "paused",
        eventAt=event.isoformat() if event is not None else None,
    )


def task_counts(tasks: Iterable[TaskEntry]) -> TaskCounts:
    counts = TaskCounts()
    for task in tasks:
        setattr(counts, task.stage, getattr(counts, task.stage) + 1)
        if task.activity is not None:
            setattr(counts, task.activity, getattr(counts, task.activity) + 1)
    return counts


def build_task_entries(
    sources: list[TaskDocumentSource],
    enclosures: list[EnclosureNode],
    host: HostActivitySnapshot,
) -> TaskEntries:
    agents: dict[str, list[PublishedAgentActivity]] = defaultdict(list)
    for agent in host.agents:
        agents[agent.task_key].append(agent.activity)
    by_key: dict[str, TaskEntry] = {}
    by_binding: dict[tuple[str, str, str], TaskEntry] = {}
    for source in sources:
        doc = source.summary
        ref = source.reference
        summary = TaskSummary.model_validate(
            source.summary.model_dump(
                exclude={
                    "id",
                    "title",
                    "repository",
                    "status",
                    "docPath",
                    "stepsDone",
                    "stepsTotal",
                    "subTasks",
                }
            )
        )
        state = task_state(doc.status, agents[ref.key], host_known=host.known)
        entry = TaskEntry(
            **state.model_dump(),
            key=ref.key,
            reference=ref,
            id=doc.id,
            title=doc.title,
            stepsDone=source.summary.stepsDone,
            stepsTotal=source.summary.stepsTotal,
            summary=summary,
        )
        by_key[ref.key] = entry
        by_binding[(ref.repository, source.path.parent.as_posix(), doc.id)] = entry
    unbound = _bind_worktrees(enclosures, by_binding)

    masters = sorted(
        (source for source in sources if source.summary.kind == "master"),
        key=lambda source: source.reference.key,
    )
    authored = {source.reference.key: source for source in sources}
    membership = _Membership(by_key)
    for source in masters:
        parent = by_key[source.reference.key]
        for row in source.children:
            ref = row.reference
            key = ref.key if ref is not None else f"{parent.key}#{row.id}"
            child = by_key.get(key)
            if child is None:
                state = task_state(row.status, agents[key], host_known=host.known)
                child = TaskEntry(
                    **state.model_dump(), key=key, reference=ref, id=row.id, title=row.title
                )
                by_key[key] = child
            document = authored.get(key)
            if (
                document is not None
                and document.summary.kind == "subTask"
                and document.declared_parent != parent.reference
            ):
                continue
            if membership.link(parent, child):
                child.scope = row.scope
        _link_commanded_masters(source, masters, parent, by_key, membership)
        parent.counts = task_counts(by_key[key] for key in parent.children)
    tasks = list(by_key.values())
    return TaskEntries(tasks, task_counts(tasks), unbound)


def _bind_worktrees(
    enclosures: list[EnclosureNode], by_binding: dict[tuple[str, str, str], TaskEntry]
) -> list[EnclosureNode]:
    unbound = []
    for enclosure in enclosures:
        bound = by_binding.get((enclosure.repoName, enclosure.taskRoot, enclosure.leafId))
        if bound is None:
            unbound.append(enclosure)
        else:
            bound.worktrees.append(enclosure)

    return unbound


def _link_commanded_masters(
    source: TaskDocumentSource,
    masters: list[TaskDocumentSource],
    parent: TaskEntry,
    by_key: dict[str, TaskEntry],
    membership: _Membership,
) -> None:
    # Older sprint documents name their masters in orchestrates. Resolve that
    # existing task-domain relation here, never again in a browser consumer.
    for name in source.summary.orchestrates:
        matches = [
            item.reference
            for item in masters
            if item.reference.repository == source.reference.repository
            and name in {item.path.parent.name, item.summary.id, item.summary.title}
        ]
        if len(matches) == 1:
            membership.link(parent, by_key[matches[0].key])


class _Membership:
    """One deterministic parent per child and an acyclic, wholly reachable forest."""

    def __init__(self, entries: dict[str, TaskEntry]) -> None:
        self._roots = {key: key for key in entries}
        self._ranks: dict[str, int] = {}

    def _root(self, key: str) -> str:
        self._roots.setdefault(key, key)
        while self._roots[key] != key:
            self._roots[key] = self._roots[self._roots[key]]
            key = self._roots[key]
        return key

    def link(self, parent: TaskEntry, child: TaskEntry) -> bool:
        if child.parent is not None:
            return False
        left, right = self._root(parent.key), self._root(child.key)
        if left == right:
            return False
        if self._ranks.get(left, 0) < self._ranks.get(right, 0):
            left, right = right, left
        self._roots[right] = left
        if self._ranks.get(left, 0) == self._ranks.get(right, 0):
            self._ranks[left] = self._ranks.get(left, 0) + 1
        child.parent = parent.key
        parent.children.append(child.key)
        return True


def relative_task_reference(parent: TaskDocumentRef, value: str | None) -> TaskDocumentRef | None:
    if not value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute():
        return None
    normalized = posixpath.normpath(
        str(PurePosixPath(parent.path).parent / path.with_suffix(".json"))
    )
    try:
        return TaskDocumentRef(repository=parent.repository, path=normalized)
    except ValueError:
        return None
