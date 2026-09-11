"""Sprint scheduling-mode resolution for graph and atomic-sequential planning (L13).

A sprint carries at most one scheduling authority. An authored ``executionGraph``
selects the ``dag`` mode; its absence selects the ``atomic-sequential`` default.
Source-pair activation separately decides which durable atomic master may expose
implementation work; series-contract presence is never scheduling ownership. This
module only reads canonical documents and stored terminal artifacts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document import MasterExecutionNature, TaskDocument
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.tasks.store import read_task_doc
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import ContractError, load_contract

SchedulingModeName = Literal["dag", "atomic-sequential"]

# The cleanup values at which a series contract stops owning anything (L13-R5b):
# reclaimed after integration, discarded by abandonment, or reset by a reopen.
TERMINAL_SERIES_CLEANUP = frozenset({"completed", "abandoned", "reopened"})


@dataclass(frozen=True)
class SchedulingMode:
    """One sprint's resolved scheduling authority and its commanded masters."""

    mode: SchedulingModeName
    sprint: ResolvedTaskDocument
    masters: tuple[ResolvedTaskDocument, ...]
    facts: tuple[str, ...]


def resolve_scheduling_mode(
    topology: TaskDocumentTopology, sprint_ref: TaskDocumentRef
) -> SchedulingMode:
    """Resolve the one scheduling mode of a canonical orchestration sprint."""

    sprint = topology.resolve(sprint_ref)
    if sprint.document.kind != "master" or not sprint.document.orchestrates:
        raise TaskDocumentRefError(
            "task-execution-graph-sprint-required",
            f"scheduling mode requires an orchestration sprint: {sprint_ref.key}",
        )
    masters = tuple(topology.resolve(ref) for ref in topology.children(sprint_ref))
    if sprint.document.executionGraph is None:
        return SchedulingMode(
            mode="atomic-sequential",
            sprint=sprint,
            masters=masters,
            facts=(
                "executionGraph absent: atomic-sequential default — one source-pair-selected "
                "atomic master exposes implementation work at a time",
            ),
        )
    return SchedulingMode(
        mode="dag",
        sprint=sprint,
        masters=masters,
        facts=("executionGraph present: dependency-aware scheduling",),
    )


def commanded_sprint_masters(
    topology: TaskDocumentTopology,
    sprint: ResolvedTaskDocument,
    *,
    overrides: Mapping[TaskDocumentRef, TaskDocument] | None = None,
) -> tuple[ResolvedTaskDocument, ...]:
    """The exact commanded masters of a sprint under either scheduling mode (L13-R1).

    Graph sprints validate membership and natures; under the atomic-sequential
    default the orchestrates aliases derive membership and source-pair activation —
    not contract presence or a graph — serializes implementation exposure.
    """

    if sprint.document.executionGraph is None:
        return topology.commanded_masters(sprint, overrides=overrides)
    return topology.validate_execution_topology(sprint.ref, overrides=overrides)


def effective_execution_nature(
    master: TaskDocument, sprint: TaskDocument | None
) -> MasterExecutionNature:
    """The nature a master executes under after the atomic-sequential default (L13-R1).

    Under the default mode — a sprint without an executionGraph — every commanded
    master executes atomically regardless of the declared cell. Under an authored
    graph the declared nature rules, and a nature-less commanded master stays
    invalid (set one with ``task_doc.author_execution_graph`` / ``set_nature``).
    A standalone master keeps its explicit nature; a nature-less standalone master
    is atomic by default (L13-R5e), so legacy masters need no migration.
    """

    if sprint is not None and sprint.executionGraph is None:
        return "atomic"
    if master.executionNature is not None:
        return master.executionNature
    if sprint is None:
        return "atomic"
    raise TaskDocumentRefError(
        "task-execution-topology-migration-required",
        f"commanded master {master.slug!r} has no executionNature; "
        "set one with task_doc.author_execution_graph (set_nature)",
    )


def stale_series_artifact_fact(task_root: Path) -> dict[str, str] | None:
    """The fact that an organizational master carries an ignorable terminal artifact.

    A terminal series contract (cleanup completed/abandoned/reopened) under an
    organizational master no longer owns anything (L13-R5b): starts ignore it and
    report this fact instead of refusing.
    """

    task_path = task_root / "task.json"
    document: TaskDocument | None = None
    if task_path.is_file():
        try:
            document = read_task_doc(task_path)
        except (OSError, ValueError):
            document = None
    path = series_contract_path(task_root)
    contract = None
    if path.is_file():
        try:
            contract = load_contract(path)
        except (ContractError, OSError):
            contract = None
    if (
        document is None
        or document.kind != "master"
        or document.executionNature != "organizational"
        or contract is None
        or contract.cleanup not in TERMINAL_SERIES_CLEANUP
    ):
        return None
    return {
        "fact": "staleSeriesArtifact",
        "contractPath": path.as_posix(),
        "cleanup": contract.cleanup,
    }
