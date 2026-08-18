"""Orchestrator portfolio loop: judgment over the authoritative closeout queue.

L7 is the intelligence layer over the fact-producing scheduler (L1-L6). The queue already
projects candidates into ready/waiting/blocked/in-flight and enforces a deterministic
first-ready selection; this module adds the durable orchestrator *decisions* -- recorded
rationale, reshape classification, and the manager graph/queue slice -- without inventing a
seat-local watcher or re-deriving order from transcripts or labels.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.models.closeout_queue import (
    CloseoutCandidateRecord,
    CloseoutQueueState,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.closeout_queue_evidence import PRIORITY_RANK
from agents_remember.worktrees.closeout_queue_graph import QueueGraphContext

MAX_PORTFOLIO_DECISIONS = 256
MAX_PORTFOLIO_TEXT = 8192
MAX_PORTFOLIO_SHORT = 256
MAX_PORTFOLIO_EVIDENCE = 64
MAX_PORTFOLIO_CANDIDATES = 256

ReshapeKind = Literal["ordinary", "substantial"]
OrchestratorDecisionKind = Literal[
    "select",
    "reprioritize",
    "withdraw",
    "handle-failure",
    "escalate-strategist",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrchestratorDecision(_StrictModel):
    """One durable orchestrator portfolio decision with recorded rationale."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: OrchestratorDecisionKind
    sprintTaskDocumentRef: TaskDocumentRef
    reshapeKind: ReshapeKind
    subject: TaskDocumentRef | None = None
    rationale: str = Field(max_length=MAX_PORTFOLIO_TEXT)
    evidenceRefs: list[str] = Field(default_factory=list, max_length=MAX_PORTFOLIO_EVIDENCE)
    decidedBy: str = Field(max_length=MAX_PORTFOLIO_SHORT)
    decidedAt: str = Field(max_length=MAX_PORTFOLIO_SHORT)


class FrontierCandidate(_StrictModel):
    """One mechanically-clear, grade-current candidate in the orchestrator's frontier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    taskDocumentRef: TaskDocumentRef
    owningMaster: TaskDocumentRef
    priority: str
    nodeOrder: int


class ManagerCandidateView(_StrictModel):
    """One candidate as seen by its owning manager."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    taskDocumentRef: TaskDocumentRef
    state: str
    priority: str | None = None
    ready: bool = False


class ManagerSlice(_StrictModel):
    """The graph/queue slice a manager needs for their master."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    masterRef: TaskDocumentRef
    executionNature: str
    incompletePredecessors: list[TaskDocumentRef] = Field(default_factory=list)
    candidates: list[ManagerCandidateView] = Field(default_factory=list)


def classify_reshape(
    *,
    edge_changed: bool = False,
    nature_changed: bool = False,
    leaf_moved: bool = False,
    new_foundation: bool = False,
) -> ReshapeKind:
    """Substantial reshapes need a fresh strategist; ordinary changes stay with the orchestrator.

    The four signals correspond to L7-R4: edge changes, master reclassification, large leaf
    moves, and a new common foundation. Any one of them is substantial.
    """

    if edge_changed or nature_changed or leaf_moved or new_foundation:
        return "substantial"
    return "ordinary"


def _frontier_ready(
    graph: QueueGraphContext,
    state: CloseoutQueueState,
    candidate: CloseoutCandidateRecord,
) -> bool:
    """Graph/state-level readiness, mirroring the queue's waiting reasons.

    Mechanical/evidence blockers (source, ledger, route, curator) stay with the queue's
    declaration-time validation; this is the dependency + grade + lane + atomic-blocker check.
    """

    reasons: list[str] = []
    if graph.incomplete_predecessors.get(candidate.owningMaster):
        reasons.append("predecessor-incomplete")
    if candidate.grade is None:
        reasons.append("explicit-grade-required")
    blocker = state.activeBlocker
    if blocker is None and (
        candidate.owningMaster in graph.masters
        and graph.masters[candidate.owningMaster].document.executionNature == "atomic"
    ):
        reasons.append("atomic-blocker-required")
    return not reasons


def recompute_frontier(
    graph: QueueGraphContext, state: CloseoutQueueState
) -> list[FrontierCandidate]:
    """Return the dependency-safe frontier of grade-current, unblocked candidates.

    Ordering is not applied here; ``choose`` owns the deterministic tie-break.
    """

    frontier: list[FrontierCandidate] = []
    for candidate in state.candidates.values():
        if not _frontier_ready(graph, state, candidate):
            continue
        grade = candidate.grade
        assert grade is not None
        frontier.append(
            FrontierCandidate(
                taskDocumentRef=candidate.taskDocumentRef,
                owningMaster=candidate.owningMaster,
                priority=grade.priority,
                nodeOrder=graph.node_order[candidate.owningMaster],
            )
        )
    return frontier


def choose(frontier: list[FrontierCandidate]) -> FrontierCandidate:
    """Select the deterministic first candidate: priority rank, node order, then task key."""

    if not frontier:
        raise ValueError("cannot choose from an empty frontier")
    return min(
        frontier,
        key=lambda candidate: (
            PRIORITY_RANK[candidate.priority],
            candidate.nodeOrder,
            candidate.taskDocumentRef.key,
        ),
    )


def manager_slice(
    graph: QueueGraphContext, state: CloseoutQueueState, master_ref: TaskDocumentRef
) -> ManagerSlice:
    """Build the graph/queue slice for one manager's master.

    A manager sees only their own master's candidates and dependency status; they can never
    reserve the global lane or integrate out of order because those operations are
    task-addressed and revalidated at the queue boundary (L7-R5).
    """

    master = graph.masters.get(master_ref)
    execution_nature: str = "unknown"
    if master is not None and master.document.executionNature is not None:
        execution_nature = master.document.executionNature
    ready_keys = {candidate.taskDocumentRef.key for candidate in recompute_frontier(graph, state)}
    views: list[ManagerCandidateView] = []
    for candidate in state.candidates.values():
        if candidate.owningMaster != master_ref:
            continue
        views.append(
            ManagerCandidateView(
                taskDocumentRef=candidate.taskDocumentRef,
                state=candidate.state,
                priority=candidate.grade.priority if candidate.grade is not None else None,
                ready=candidate.taskDocumentRef.key in ready_keys,
            )
        )
    views.sort(key=lambda view: view.taskDocumentRef.key)
    return ManagerSlice(
        masterRef=master_ref,
        executionNature=execution_nature,
        incompletePredecessors=list(graph.incomplete_predecessors.get(master_ref, ())),
        candidates=views,
    )
