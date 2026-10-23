"""The one join of authored tasks, worktree contracts and host-agent activity."""

from __future__ import annotations

import posixpath
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.host_activity import (
    HostActivitySnapshot,
    PublishedAgentActivity,
    TaskAgentActivity,
)
from agents_remember.observer.projection import (
    EnclosureNode,
    TaskCounts,
    TaskDocNode,
    TaskEntry,
    TaskListDisagreement,
    TaskState,
    TaskSummary,
)
from agents_remember.observer.provider_limits import (
    ProviderLimits,
    now_utc,
    task_provider_limit,
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
    # The record's own `master` text as written: `declared_parent` is None both when the field
    # is absent and when its text resolves to no task document.
    master_literal: str | None = None

    @property
    def unresolvable_master(self) -> str | None:
        """The `master` text of a record whose binding is present but names no task document."""
        if self.master_literal is None or self.declared_parent is not None:
            return None
        return self.master_literal

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


@dataclass(frozen=True)
class TaskLimitFacts:
    """What rule 13 needs beside a task's activity: the one file, and the task's seats."""

    limits: ProviderLimits
    seats: Iterable[TaskAgentActivity] = ()
    now: datetime | None = None


def task_state(
    status: str,
    agents: Iterable[PublishedAgentActivity],
    *,
    host_known: bool,
    limited: TaskLimitFacts | None = None,
) -> TaskState:
    """Terminal/planned stage wins; only a whole host observation can say paused.

    MIK-R190 rule 4: an agent works when it is in a turn (``in-turn`` or
    ``permission``) or when it has a running provider subagent and has not ended —
    which is what Paseo's own workspace list shows. Rule 13 adds the task's
    provider limit beside it, from the one limited-fact file.
    """

    stage = _STAGES[status]
    if stage != "started":
        return TaskState.model_validate({"stage": stage})
    if not host_known:
        return TaskState(stage="started", activity="unknown")
    observed = list(agents)
    working = [agent for agent in observed if agent_works(agent)]
    contributors = working or observed
    times = [event for event in (_event_of(agent) for agent in contributors) if event is not None]
    event = max(times) if times else None
    activity = "works" if working else "paused"
    provider = (
        task_provider_limit(limited.limits, limited.seats, limited.now or now_utc())
        if not working and limited is not None
        else None
    )
    return TaskState(
        stage="started",
        activity=activity,
        eventAt=event.isoformat() if event is not None else None,
        providerLimit=provider,
    )


def agent_works(agent: PublishedAgentActivity) -> bool:
    """MIK-R190 rule 4: in a turn, or with a running subagent and not ended.

    The one definition of "this agent is working": the task state, the stall predicate
    (MIK-R200 rule 4) and the pause suppression all read it here.
    """

    if agent.state in {"in-turn", "permission"}:
        return True
    return agent.state != "ended" and (agent.runningSubagents or 0) > 0


def _event_of(agent: PublishedAgentActivity) -> datetime | None:
    """The time the published word runs from: the turn's start, else the oldest subagent."""

    if agent.state in {"in-turn", "permission"}:
        return agent.eventAt
    if agent.state != "ended" and (agent.runningSubagents or 0) > 0:
        return agent.subagentsSince or agent.eventAt
    return agent.eventAt


def task_counts(tasks: Iterable[TaskEntry]) -> TaskCounts:
    counts = TaskCounts()
    for task in tasks:
        setattr(counts, task.stage, getattr(counts, task.stage) + 1)
        if task.activity is not None:
            setattr(counts, task.activity, getattr(counts, task.activity) + 1)
    return counts


def _limit_facts(
    host: HostActivitySnapshot, seats: list[TaskAgentActivity]
) -> TaskLimitFacts | None:
    """Rule 13's inputs, when the publication carried the one file at all."""

    return None if host.limits is None else TaskLimitFacts(host.limits, tuple(seats))


def build_task_entries(
    sources: list[TaskDocumentSource],
    enclosures: list[EnclosureNode],
    host: HostActivitySnapshot,
) -> TaskEntries:
    agents: dict[str, list[PublishedAgentActivity]] = defaultdict(list)
    seats: dict[str, list[TaskAgentActivity]] = defaultdict(list)
    for agent in host.agents:
        agents[agent.task_key].append(agent.activity)
        seats[agent.task_key].append(agent)
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
        state = task_state(
            doc.status,
            agents[ref.key],
            host_known=host.known,
            limited=_limit_facts(host, seats[ref.key]),
        )
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
    listed: set[str] = set()
    for source in masters:
        parent = by_key[source.reference.key]
        for row in source.children:
            ref = row.reference
            key = ref.key if ref is not None else f"{parent.key}#{row.id}"
            child = by_key.get(key)
            if child is None:
                state = task_state(
                    row.status,
                    agents[key],
                    host_known=host.known,
                    limited=_limit_facts(host, seats[key]),
                )
                child = TaskEntry(
                    **state.model_dump(), key=key, reference=ref, id=row.id, title=row.title
                )
                by_key[key] = child
            _place_listed_child(parent, child, row, authored.get(key), membership)
            listed.add(key)
        _link_commanded_masters(source, masters, parent, by_key, membership)
        parent.counts = task_counts(by_key[key] for key in parent.children)
    _report_unlisted_records(sources, listed, by_key, membership)
    tasks = list(by_key.values())
    return TaskEntries(tasks, task_counts(tasks), unbound)


def _place_listed_child(
    parent: TaskEntry,
    child: TaskEntry,
    row: DeclaredTaskSource,
    document: TaskDocumentSource | None,
    membership: _Membership,
) -> None:
    """Bind one listed row; a record that names no master belongs to the master listing it.

    A record that names another master, or whose master text names no task, stays out of this
    list. That disagreement, and a row whose record another list holds already, is reported on
    this master, never dropped.
    """
    leaf = document if document is not None and document.summary.kind == "subTask" else None
    if leaf is not None and leaf.unresolvable_master is not None:
        membership.report(
            parent,
            TaskListDisagreement(
                kind="unresolvable-master",
                record=leaf.reference,
                row=row.id,
                masterLiteral=leaf.unresolvable_master,
            ),
        )
        return
    if leaf is not None and leaf.declared_parent not in (None, parent.reference):
        membership.report(
            parent,
            TaskListDisagreement(
                kind="names-another-master",
                record=leaf.reference,
                row=row.id,
                recordMaster=leaf.declared_parent,
            ),
        )
        return
    if membership.link(parent, child):
        child.scope = row.scope
    elif leaf is not None and child.parent not in (None, parent.key):
        membership.report(
            parent,
            TaskListDisagreement(
                kind="listed-twice", record=leaf.reference, row=row.id, parent=child.parent
            ),
        )


def _report_unlisted_records(
    sources: list[TaskDocumentSource],
    listed: set[str],
    by_key: dict[str, TaskEntry],
    membership: _Membership,
) -> None:
    """A task record that no list holds is reported on the master it names, else its folder's."""
    for source in sources:
        if source.summary.kind != "subTask" or source.reference.key in listed:
            continue
        home = source.declared_parent or TaskDocumentRef(
            repository=source.reference.repository,
            path=f"{source.path.parent.name}/task.json",
        )
        master = by_key.get(home.key)
        if master is None or master.summary is None or master.summary.kind != "master":
            continue
        literal = source.unresolvable_master
        membership.report(
            master,
            TaskListDisagreement(
                kind="not-listed" if literal is None else "unresolvable-master",
                record=source.reference,
                recordMaster=source.declared_parent,
                masterLiteral=literal,
            ),
        )


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
    """One deterministic parent per child and an acyclic, wholly reachable forest.

    It also keeps each master's disagreements once: a list that repeats a conflicting row
    reports one disagreement, and a dispute that differs in any field stays its own.
    """

    def __init__(self, entries: dict[str, TaskEntry]) -> None:
        self._roots = {key: key for key in entries}
        self._ranks: dict[str, int] = {}
        self._reported: set[tuple[str, str]] = set()

    def report(self, master: TaskEntry, disagreement: TaskListDisagreement) -> None:
        seen = (master.key, disagreement.model_dump_json())
        if seen in self._reported:
            return
        self._reported.add(seen)
        if master.disagreements is None:
            master.disagreements = []
        master.disagreements.append(disagreement)

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
