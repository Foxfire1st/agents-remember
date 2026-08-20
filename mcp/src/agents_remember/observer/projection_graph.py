"""Render-ready sprint execution graph projection (L12-R4).

The persisted ``executionGraph`` ships raw refs and leaf ids; this module turns
it into the per-node view the dashboard renders directly: node kind, master
ref + title, leaf ids + titles, derived wave index, mechanically derived
frontier state, execution nature, and predecessors with their recorded
reasons. The frontend never joins raw paths or re-derives waves/state.

Layer contract: the ``observer`` package must not import the ``tasks`` package,
so this module is primitives-only. The serving layer (which may import
``tasks``) walks the persisted graph -- derived waves, resolved edge endpoints,
joined titles, per-master status/nature facts -- and feeds this builder plain
data. The structural protocols below declare exactly the surface the builder
consumes; the concrete types live in ``agents_remember.tasks``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.models.task_document_ref import TaskDocumentRef

FRONTIER_LANDED = "landed"
FRONTIER_READY = "ready"
FRONTIER_WAITING = "waiting"
FRONTIER_IN_FLIGHT = "in-flight"

FrontierState = Literal["landed", "ready", "waiting", "in-flight"]


class GraphNodeLike(Protocol):
    """The structural surface the builder needs from one graph node.

    The concrete ``SprintExecutionNode`` (tasks package) satisfies this
    protocol; the builder never imports it.
    """

    kind: Literal["master", "segment"]
    ref: TaskDocumentRef
    leafIds: list[str]


class GraphTitlesLike(Protocol):
    """Joined master/leaf titles; the concrete ``SprintGraphTitles`` lives in tasks."""

    @property
    def master_titles(self) -> dict[str, str]: ...

    @property
    def leaf_titles(self) -> dict[str, str]: ...


@dataclass(frozen=True)
class GraphPredecessorFacts:
    """One resolved predecessor edge: the predecessor node plus its recorded reason."""

    predecessor: GraphNodeLike
    reason: str
    judgmentId: str | None = None


@dataclass(frozen=True)
class MasterGraphFacts:
    """Primitives-only per-master facts the frontier derivation needs.

    The serving layer extracts these from the master documents (task status,
    execution nature, per-leaf declared statuses); missing entries project a
    conservative frontier state (never landed, never in-flight).
    """

    status: str
    executionNature: str | None = None
    leaf_statuses: Mapping[str, str] = field(default_factory=dict)


class TaskExecutionPredecessorNode(BaseModel):
    """One predecessor edge of a projected graph node with its recorded reason."""

    model_config = ConfigDict(extra="forbid")

    predecessorRef: TaskDocumentRef
    predecessorTitle: str
    reason: str
    judgmentId: str | None = None


class TaskExecutionNodeView(BaseModel):
    """Render-ready per-node sprint graph model (L12-R4).

    ``nodeId`` is a stable semantic identity: a lump's master ref key, or the
    ref key plus a segment ordinal for a segmented master. ``waveIndex`` is the
    1-based derived wave; ``frontierState`` is derived mechanically from task
    statuses and the graph edges -- the dashboard styles, never re-derives.
    """

    model_config = ConfigDict(extra="forbid")

    nodeId: str
    kind: Literal["lump", "segment"]
    masterRef: TaskDocumentRef
    masterTitle: str
    leafIds: list[str] = Field(default_factory=list)
    leafTitles: list[str] = Field(default_factory=list)
    waveIndex: int
    frontierState: FrontierState
    executionNature: str | None = None
    predecessors: list[TaskExecutionPredecessorNode] = Field(default_factory=list)


class TaskExecutionGraphView(BaseModel):
    """The render-ready sprint graph, ordered by derived wave then node order.

    Nodes within a wave keep the persisted declaration order, so a re-render
    with an unchanged graph is byte-stable.
    """

    model_config = ConfigDict(extra="forbid")

    nodes: list[TaskExecutionNodeView] = Field(default_factory=list)


def node_identity(nodes: Sequence[GraphNodeLike], node: GraphNodeLike) -> str:
    """A stable semantic node identity: ref key, or ref key + segment ordinal.

    Segments of one master are ordered by declaration, so the ordinal is stable
    under appended segments (existing segments keep their identity).
    """

    if node.kind == "master":
        return node.ref.key
    siblings = [candidate for candidate in nodes if candidate.ref == node.ref]
    return f"{node.ref.key}#seg{siblings.index(node) + 1}"


def _leaf_statuses(facts: MasterGraphFacts, leaf_ids: Sequence[str]) -> list[str]:
    return [facts.leaf_statuses.get(leaf_id, "planning") for leaf_id in leaf_ids]


def _node_is_landed(node: GraphNodeLike, facts: MasterGraphFacts | None) -> bool:
    if facts is None:
        return False
    if node.kind == "master":
        return facts.status == "Completed"
    return all(status == "Completed" for status in _leaf_statuses(facts, node.leafIds))


def _node_is_in_flight(node: GraphNodeLike, facts: MasterGraphFacts | None) -> bool:
    if facts is None:
        return False
    if node.kind == "master":
        return facts.status in ("inProgress", "blocked")
    return any(
        status in ("inProgress", "blocked") for status in _leaf_statuses(facts, node.leafIds)
    )


def _frontier_state(
    node: GraphNodeLike,
    facts: MasterGraphFacts | None,
    landed: Mapping[GraphNodeLike, bool],
    predecessor_edges: Mapping[GraphNodeLike, Sequence[GraphPredecessorFacts]],
) -> FrontierState:
    if _node_is_landed(node, facts):
        return FRONTIER_LANDED
    if any(not landed.get(edge.predecessor, False) for edge in predecessor_edges[node]):
        return FRONTIER_WAITING
    if _node_is_in_flight(node, facts):
        return FRONTIER_IN_FLIGHT
    return FRONTIER_READY


@dataclass(frozen=True)
class _GraphViewContext:
    """The primitives-only inputs one node view needs (bundled for bounded args)."""

    nodes: Sequence[GraphNodeLike]
    wave_of: Mapping[GraphNodeLike, int]
    titles: GraphTitlesLike | None
    masters: Mapping[TaskDocumentRef, MasterGraphFacts]
    landed: Mapping[GraphNodeLike, bool]
    predecessor_edges: Mapping[GraphNodeLike, Sequence[GraphPredecessorFacts]]


def _node_view(node: GraphNodeLike, context: _GraphViewContext) -> TaskExecutionNodeView:
    facts = context.masters.get(node.ref)
    master_title = (
        context.titles.master_titles.get(node.ref.key, node.ref.key)
        if context.titles is not None
        else node.ref.key
    )
    leaf_titles = (
        [context.titles.leaf_titles.get(leaf_id, leaf_id) for leaf_id in node.leafIds]
        if context.titles is not None
        else list(node.leafIds)
    )
    return TaskExecutionNodeView(
        nodeId=node_identity(context.nodes, node),
        kind="lump" if node.kind == "master" else "segment",
        masterRef=node.ref,
        masterTitle=master_title,
        leafIds=list(node.leafIds),
        leafTitles=leaf_titles,
        waveIndex=context.wave_of[node],
        frontierState=_frontier_state(node, facts, context.landed, context.predecessor_edges),
        executionNature=facts.executionNature if facts is not None else None,
        predecessors=[
            TaskExecutionPredecessorNode(
                predecessorRef=edge.predecessor.ref,
                predecessorTitle=(
                    context.titles.master_titles.get(
                        edge.predecessor.ref.key, edge.predecessor.ref.key
                    )
                    if context.titles is not None
                    else edge.predecessor.ref.key
                ),
                reason=edge.reason,
                judgmentId=edge.judgmentId,
            )
            for edge in context.predecessor_edges[node]
        ],
    )


def build_execution_graph_view(
    nodes: Sequence[GraphNodeLike],
    waves: Sequence[Sequence[GraphNodeLike]],
    predecessor_edges: Mapping[GraphNodeLike, Sequence[GraphPredecessorFacts]],
    masters: Mapping[TaskDocumentRef, MasterGraphFacts],
    titles: GraphTitlesLike | None = None,
) -> TaskExecutionGraphView:
    """Build the render-ready per-node view from primitives-only inputs.

    The serving layer supplies the walked graph: ``nodes`` in declaration
    order, ``waves`` ordered by derived wave (nodes within a wave in
    declaration order), ``predecessor_edges`` keyed by successor node with the
    resolved predecessor nodes and their recorded reasons, per-master facts,
    and joined titles. A missing master projects a conservative frontier state
    (never landed, never in-flight) and ref-key/leaf-id fallback labels.
    """

    wave_of = {node: index for index, wave in enumerate(waves, start=1) for node in wave}
    landed = {node: _node_is_landed(node, masters.get(node.ref)) for node in nodes}
    context = _GraphViewContext(
        nodes=nodes,
        wave_of=wave_of,
        titles=titles,
        masters=masters,
        landed=landed,
        predecessor_edges=predecessor_edges,
    )
    views = [
        _node_view(node, context)
        for wave in waves
        for node in wave  # the serving layer already orders within a wave by declaration
    ]
    return TaskExecutionGraphView(nodes=views)
