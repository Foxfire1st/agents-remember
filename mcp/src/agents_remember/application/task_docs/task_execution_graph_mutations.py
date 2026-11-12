"""One candidate mutation engine shared by graph authoring and master retirement."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agents_remember.errors import AgentsRememberError
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    MasterExecutionNature,
    SprintExecutionEdge,
    SprintExecutionEndpoint,
    SprintExecutionGraph,
    SprintExecutionNode,
    resolve_graph_endpoint,
)


class ExecutionTopologyError(AgentsRememberError):
    """An execution-topology authoring edit is structurally invalid."""


class _AuthoringMutationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("judgmentId", check_fields=False)
    @classmethod
    def _trim_judgment_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("mutation judgmentId must not be blank")
        return trimmed


class _AddNodeMutation(_AuthoringMutationBase):
    op: Literal["add_node"]
    ref: TaskDocumentRef
    kind: Literal["master", "segment"] = "master"
    leafIds: list[str] = Field(default_factory=list)
    judgmentId: str | None = None


class _RemoveNodeMutation(_AuthoringMutationBase):
    op: Literal["remove_node"]
    ref: TaskDocumentRef
    # A leaf id sampling the segment to remove; omitted when the master has one node.
    leafId: str | None = None
    judgmentId: str | None = None


class _AddEdgeMutation(_AuthoringMutationBase):
    op: Literal["add_edge"]
    predecessor: TaskDocumentRef | SprintExecutionEndpoint
    successor: TaskDocumentRef | SprintExecutionEndpoint
    reason: str
    # Optional at parse so a missing judgmentId surfaces the typed
    # task-execution-graph-judgment-required refusal (L15-R8 F5), not a raw
    # pydantic "Field required" like the segment ops already produced.
    judgmentId: str | None = None


class _RemoveEdgeMutation(_AuthoringMutationBase):
    op: Literal["remove_edge"]
    predecessor: TaskDocumentRef | SprintExecutionEndpoint
    successor: TaskDocumentRef | SprintExecutionEndpoint
    judgmentId: str | None = None


class _MoveLeafMutation(_AuthoringMutationBase):
    op: Literal["move_leaf"]
    ref: TaskDocumentRef
    leafId: str
    # A leaf id sampling the target segment (segments are addressed, never named).
    toSegment: str
    judgmentId: str | None = None


class _SetNatureMutation(_AuthoringMutationBase):
    op: Literal["set_nature"]
    ref: TaskDocumentRef
    executionNature: MasterExecutionNature
    judgmentId: str | None = None


_GraphAuthoringMutation = Annotated[
    _AddNodeMutation
    | _RemoveNodeMutation
    | _AddEdgeMutation
    | _RemoveEdgeMutation
    | _MoveLeafMutation
    | _SetNatureMutation,
    Field(discriminator="op"),
]


class _ExecutionGraphAuthoring(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mutations: list[_GraphAuthoringMutation] = Field(min_length=1)


@dataclass
class _GraphDraft:
    """The mutable working copy one authoring batch mutates before final validation."""

    nodes: list[SprintExecutionNode]
    edges: list[SprintExecutionEdge]
    natures: dict[TaskDocumentRef, MasterExecutionNature]


def _statically_judgment_bearing(mutation: _GraphAuthoringMutation) -> bool:
    if isinstance(mutation, _AddNodeMutation):
        return mutation.kind == "segment"
    # remove_node is decided against the resolved node at application time.
    return not isinstance(mutation, _RemoveNodeMutation)


def _require_mutation_judgment(mutation: _AddNodeMutation | _RemoveNodeMutation) -> None:
    if not (mutation.judgmentId or "").strip():
        raise ExecutionTopologyError(
            "task-execution-graph-judgment-required: "
            f"{mutation.op} on a segment requires a judgmentId from the sprint Judgment Register"
        )


def _apply_add_node(
    draft: _GraphDraft, mutation: _AddNodeMutation, _commanded: set[TaskDocumentRef]
) -> None:
    try:
        node = SprintExecutionNode(kind=mutation.kind, ref=mutation.ref, leafIds=mutation.leafIds)
    except ValidationError as exc:
        raise ExecutionTopologyError(f"invalid add_node mutation: {exc}") from exc
    if node.kind == "segment":
        _require_mutation_judgment(mutation)
    if node in draft.nodes:
        raise ExecutionTopologyError(
            f"task-execution-graph-node-duplicate: node is already declared: {node.ref.key}"
        )
    draft.nodes.append(node)


def _draft_node(
    draft: _GraphDraft, ref: TaskDocumentRef, leaf_id: str | None
) -> SprintExecutionNode:
    nodes = [node for node in draft.nodes if node.ref == ref]
    if leaf_id is not None:
        nodes = [node for node in nodes if leaf_id in node.leafIds]
    if not nodes:
        raise ExecutionTopologyError(
            f"task-execution-graph-node-unknown: no node of {ref.key} matches the mutation"
        )
    if len(nodes) > 1:
        raise ExecutionTopologyError(
            f"task-execution-graph-node-ambiguous: {ref.key} has {len(nodes)} nodes; "
            "name a leafId of the target segment"
        )
    return nodes[0]


def _edge_touches_node(edge: SprintExecutionEdge, node: SprintExecutionNode) -> bool:
    for endpoint in (edge.predecessor, edge.successor):
        ref = endpoint.ref if isinstance(endpoint, SprintExecutionEndpoint) else endpoint
        leaf = endpoint.leafId if isinstance(endpoint, SprintExecutionEndpoint) else None
        if ref == node.ref and (leaf is None or leaf in node.leafIds):
            return True
    return False


def _apply_remove_node(
    draft: _GraphDraft, mutation: _RemoveNodeMutation, _commanded: set[TaskDocumentRef]
) -> None:
    target = _draft_node(draft, mutation.ref, mutation.leafId)
    if target.kind == "segment":
        _require_mutation_judgment(mutation)
    if any(_edge_touches_node(edge, target) for edge in draft.edges):
        raise ExecutionTopologyError(
            f"task-execution-graph-node-in-use: node {target.ref.key} still has edges; "
            "remove them first"
        )
    draft.nodes.remove(target)


def _resolved_draft_edge(
    draft: _GraphDraft, edge: SprintExecutionEdge
) -> tuple[SprintExecutionNode, SprintExecutionNode]:
    try:
        return (
            resolve_graph_endpoint(draft.nodes, edge.predecessor),
            resolve_graph_endpoint(draft.nodes, edge.successor),
        )
    except ValueError as exc:
        raise ExecutionTopologyError(str(exc)) from exc


def _apply_add_edge(
    draft: _GraphDraft, mutation: _AddEdgeMutation, _commanded: set[TaskDocumentRef]
) -> None:
    try:
        edge = SprintExecutionEdge(
            predecessor=mutation.predecessor,
            successor=mutation.successor,
            reason=mutation.reason,
            judgmentId=mutation.judgmentId,
        )
    except ValidationError as exc:
        raise ExecutionTopologyError(f"invalid add_edge mutation: {exc}") from exc
    pair = _resolved_draft_edge(draft, edge)
    if pair[0] == pair[1]:
        raise ExecutionTopologyError("execution-graph edge cannot point a node to itself")
    if any(_resolved_draft_edge(draft, existing) == pair for existing in draft.edges):
        raise ExecutionTopologyError(
            "task-execution-graph-edge-duplicate: the edge is already declared"
        )
    draft.edges.append(edge)


def _apply_remove_edge(
    draft: _GraphDraft, mutation: _RemoveEdgeMutation, _commanded: set[TaskDocumentRef]
) -> None:
    probe = SprintExecutionEdge(
        predecessor=mutation.predecessor,
        successor=mutation.successor,
        reason="removal probe",
        judgmentId=mutation.judgmentId,
    )
    pair = _resolved_draft_edge(draft, probe)
    matches = [
        index for index, edge in enumerate(draft.edges) if _resolved_draft_edge(draft, edge) == pair
    ]
    if not matches:
        raise ExecutionTopologyError(
            "task-execution-graph-edge-unknown: no declared edge matches the mutation endpoints"
        )
    _remove_edge_indexes(draft, {matches[0]})


def _apply_move_leaf(
    draft: _GraphDraft, mutation: _MoveLeafMutation, _commanded: set[TaskDocumentRef]
) -> None:
    segments = [node for node in draft.nodes if node.ref == mutation.ref and node.kind == "segment"]
    source = next((node for node in segments if mutation.leafId in node.leafIds), None)
    target = next((node for node in segments if mutation.toSegment in node.leafIds), None)
    if target is None:
        raise ExecutionTopologyError(
            f"task-execution-graph-segment-unknown: no segment of {mutation.ref.key} contains "
            f"leaf {mutation.toSegment!r}"
        )
    _require_move_does_not_retarget_edge(draft, mutation)
    if source is None:
        # Placing a leaf the master gained after authoring (L11-R2): no source segment.
        draft.nodes[draft.nodes.index(target)] = target.model_copy(
            update={"leafIds": [*target.leafIds, mutation.leafId]}
        )
        return
    if source == target:
        raise ExecutionTopologyError(
            f"task-execution-graph-leaf-already-placed: leaf {mutation.leafId!r} is already "
            "in that segment"
        )
    remaining = [leaf for leaf in source.leafIds if leaf != mutation.leafId]
    if not remaining:
        raise ExecutionTopologyError(
            f"task-execution-graph-segment-empty: move_leaf would empty a segment of "
            f"{mutation.ref.key}; use remove_node instead"
        )
    draft.nodes[draft.nodes.index(source)] = source.model_copy(update={"leafIds": remaining})
    draft.nodes[draft.nodes.index(target)] = target.model_copy(
        update={"leafIds": [*target.leafIds, mutation.leafId]}
    )


def _require_move_does_not_retarget_edge(draft: _GraphDraft, mutation: _MoveLeafMutation) -> None:
    """Refuse a move whose leaf samples an edge endpoint (L15-R8 F3).

    Moving such a leaf silently retargets the edge to the destination segment and
    the resulting acyclicity refusal never names the real cause; name it here.
    """

    for edge in draft.edges:
        for endpoint, role in (
            (edge.predecessor, "predecessor"),
            (edge.successor, "successor"),
        ):
            if not isinstance(endpoint, SprintExecutionEndpoint):
                continue
            if endpoint.leafId != mutation.leafId or endpoint.ref != mutation.ref:
                continue
            raise ExecutionTopologyError(
                "task-execution-graph-move-retargets-edge: moving leaf "
                f"{mutation.leafId!r} of {mutation.ref.key} would retarget the "
                f"{role} endpoint of an edge that samples that leaf; remove the "
                "edge first or target the segment explicitly"
            )


def _apply_set_nature(
    draft: _GraphDraft, mutation: _SetNatureMutation, commanded: set[TaskDocumentRef]
) -> None:
    if mutation.ref not in commanded:
        raise ExecutionTopologyError(
            "task-execution-graph-membership-invalid: set_nature target is not commanded by "
            f"the sprint: {mutation.ref.key}"
        )
    draft.natures[mutation.ref] = mutation.executionNature


_MUTATION_HANDLERS: dict[str, Callable[[_GraphDraft, Any, set[TaskDocumentRef]], None]] = {
    "add_node": _apply_add_node,
    "remove_node": _apply_remove_node,
    "add_edge": _apply_add_edge,
    "remove_edge": _apply_remove_edge,
    "move_leaf": _apply_move_leaf,
    "set_nature": _apply_set_nature,
}


def _remove_edge_indexes(draft: _GraphDraft, indexes: set[int]) -> None:
    """One deletion owner for both a named graph mutation and a batch retirement."""
    draft.edges[:] = [edge for index, edge in enumerate(draft.edges) if index not in indexes]


def remove_retired_master_edges(
    graph: SprintExecutionGraph, master_ref: TaskDocumentRef
) -> tuple[SprintExecutionGraph, list[SprintExecutionEdge]]:
    """Resolve the existing graph once and batch touching edges through the mutation owner."""
    draft = _GraphDraft(list(graph.nodes), list(graph.edges), {})
    target_nodes = {index for index, node in enumerate(graph.nodes) if node.ref == master_ref}
    indexes = {
        index
        for index, (predecessor, successor) in enumerate(
            graph._execution_graph_analysis().resolvedEdgeIndexes
        )
        if predecessor in target_nodes or successor in target_nodes
    }
    touching = [edge for index, edge in enumerate(graph.edges) if index in indexes]
    _remove_edge_indexes(draft, indexes)
    return SprintExecutionGraph(nodes=draft.nodes, edges=draft.edges), touching
