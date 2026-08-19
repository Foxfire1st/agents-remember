"""Application rules for explicit sprint execution topology authoring."""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.git_command import run_git
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    MasterExecutionNature,
    SprintExecutionEdge,
    SprintExecutionEndpoint,
    SprintExecutionGraph,
    SprintExecutionNode,
    TaskDocument,
    completion_blockers,
    json_path_for,
    leaf_placement_facts,
    markdown_path_for,
    numbering_drift_hints,
    read_task_doc,
    render_markdown,
    resolve_graph_endpoint,
    write_task_doc_batch,
)
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.worktrees.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.closeout_queue_evidence import (
    JUDGMENT_REGISTER_SECTION,
    planning_authorities,
)
from agents_remember.worktrees.integration_branch_authority import (
    require_topology_migration_authority,
)


class ExecutionTopologyError(AgentsRememberError):
    """An execution-topology authoring edit is structurally invalid."""


@dataclass(frozen=True)
class ExecutionTopologyAuthoringRequest:
    coordination_root: Path
    repo_id: str
    code_repository: Path
    memory_repository: Path | None
    task_root: Path
    slug: str | None
    fields: dict[str, Any]
    dry_run: bool


@dataclass(frozen=True)
class ExecutionTopologyEditRequest:
    coordination_root: Path
    repo_id: str
    task_root: Path
    operation: str
    original: TaskDocument | None
    candidate: TaskDocument
    fields: dict[str, Any]


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
    judgmentId: str


class _RemoveEdgeMutation(_AuthoringMutationBase):
    op: Literal["remove_edge"]
    predecessor: TaskDocumentRef | SprintExecutionEndpoint
    successor: TaskDocumentRef | SprintExecutionEndpoint
    judgmentId: str


class _MoveLeafMutation(_AuthoringMutationBase):
    op: Literal["move_leaf"]
    ref: TaskDocumentRef
    leafId: str
    # A leaf id sampling the target segment (segments are addressed, never named).
    toSegment: str
    judgmentId: str


class _SetNatureMutation(_AuthoringMutationBase):
    op: Literal["set_nature"]
    ref: TaskDocumentRef
    executionNature: MasterExecutionNature
    judgmentId: str


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


@dataclass(frozen=True)
class _AuthoringCandidate:
    """The fully validated authoring result, ready for preview or publication."""

    graph: SprintExecutionGraph
    sprint: TaskDocument
    overrides: dict[TaskDocumentRef, TaskDocument]
    documents: list[tuple[TaskDocumentRef, Path, TaskDocument]]
    placement_facts: list[dict[str, Any]]


def author_execution_graph(request: ExecutionTopologyAuthoringRequest) -> dict[str, Any]:
    """Apply one validated batch of structural mutations to a sprint executionGraph.

    The incremental authoring operation (L11-R5): add/remove node, add/remove edge,
    move leaf between segments, reclassify execution nature. Judgment-bearing
    mutations (edges, segmentation, nature) require a judgmentId resolved against the
    sprint's canonical Judgment Register; the mechanism never invents one. A graph-less
    sprint is the bootstrap seam (L13): the batch starts from an empty draft, so the
    first ``add_node`` batch creates the graph; final validation requires exact
    orchestrates membership and an explicit nature for every commanded master (a
    ``set_nature`` mutation in the same batch when the master document lacks one).
    The batch validates the candidate graph, cross-document membership, node-kind
    legality, and partition completeness before anything is written; dry_run previews
    every affected JSON/Markdown pair with rendered diff and wouldLose.
    """

    try:
        authoring = _ExecutionGraphAuthoring.model_validate(request.fields)
    except ValidationError as exc:
        raise ExecutionTopologyError(f"invalid execution-graph authoring: {exc}") from exc
    sprint_path = _existing_json(request.task_root, request.slug)
    sprint = read_task_doc(sprint_path)
    if sprint.kind != "master" or not sprint.orchestrates:
        raise ExecutionTopologyError(
            "author_execution_graph requires an orchestration sprint document"
        )
    topology = TaskDocumentTopology(request.coordination_root)
    try:
        sprint_ref = topology.canonical_ref(request.repo_id, sprint_path)
    except TaskDocumentRefError as exc:
        raise ExecutionTopologyError(f"{exc.status}: {exc}") from exc
    _verify_authoring_judgments(topology, sprint_ref, authoring.mutations)
    prepared = _prepare_authoring(
        topology,
        sprint_ref,
        sprint,
        request.task_root,
        authoring,
    )
    result: dict[str, Any] = {
        "ok": True,
        "operation": "task_doc.author_execution_graph",
        "state": "would-author" if request.dry_run else "authored",
        "bootstrapped": sprint.executionGraph is None,
        "sprintTaskDocumentRef": sprint_ref.model_dump(mode="json"),
        "appliedMutations": [
            mutation.model_dump(mode="json", exclude_none=True) for mutation in authoring.mutations
        ],
        "executionWaves": [
            [node.model_dump(mode="json") for node in wave]
            for wave in prepared.graph.derived_waves()
        ],
        "leafPlacementFacts": prepared.placement_facts,
        "numberingHints": numbering_drift_hints(prepared.graph),
    }
    if request.dry_run:
        with integration_authority_lock(request.coordination_root, request.repo_id):
            _require_authoring_publication_authority(request, prepared.overrides)
        result["dryRun"] = True
        result["documents"] = [
            _document_preview(ref, root, document) for ref, root, document in prepared.documents
        ]
        return result
    result["documents"] = _publish_authoring(request, topology, sprint_ref, prepared)
    return result


def _prepare_authoring(
    topology: TaskDocumentTopology,
    sprint_ref: TaskDocumentRef,
    sprint: TaskDocument,
    task_root: Path,
    authoring: _ExecutionGraphAuthoring,
) -> _AuthoringCandidate:
    graph = sprint.executionGraph
    if graph is None:
        # Bootstrap seam (L13): no prior graph, so commanded membership comes from the
        # orchestrates aliases; the final _validate_topology below then requires the
        # candidate graph plus natures to cover every commanded master exactly.
        commanded = set(topology.children(sprint_ref))
    else:
        try:
            commanded = {master.ref for master in topology.validate_execution_topology(sprint_ref)}
        except TaskDocumentRefError as exc:
            raise ExecutionTopologyError(f"{exc.status}: {exc}") from exc
    draft = _GraphDraft(
        nodes=list(graph.nodes) if graph is not None else [],
        edges=list(graph.edges) if graph is not None else [],
        natures={},
    )
    for mutation in authoring.mutations:
        _MUTATION_HANDLERS[mutation.op](draft, mutation, commanded)
    try:
        candidate_graph = SprintExecutionGraph(nodes=draft.nodes, edges=draft.edges)
    except ValidationError as exc:
        raise ExecutionTopologyError(f"invalid execution graph after mutations: {exc}") from exc
    sprint_data = sprint.model_dump(by_alias=True)
    sprint_data["executionGraph"] = candidate_graph.model_dump(mode="json")
    candidate_sprint = TaskDocument.model_validate(sprint_data)
    overrides: dict[TaskDocumentRef, TaskDocument] = {sprint_ref: candidate_sprint}
    documents: list[tuple[TaskDocumentRef, Path, TaskDocument]] = [
        (sprint_ref, task_root, candidate_sprint)
    ]
    for ref, nature in draft.natures.items():
        # `ref` passed the commanded-membership check in _apply_set_nature, so it resolves.
        resolved = topology.resolve(ref)
        data = resolved.document.model_dump(by_alias=True)
        data["executionNature"] = nature
        candidate = TaskDocument.model_validate(data)
        overrides[ref] = candidate
        documents.append((ref, resolved.path.parent, candidate))
    _validate_topology(topology, sprint_ref, overrides)
    _require_complete_partitions(topology, sprint_ref, overrides)
    return _AuthoringCandidate(
        graph=candidate_graph,
        sprint=candidate_sprint,
        overrides=overrides,
        documents=documents,
        placement_facts=_authoring_placement_facts(topology, sprint_ref, overrides),
    )


def _publish_authoring(
    request: ExecutionTopologyAuthoringRequest,
    topology: TaskDocumentTopology,
    sprint_ref: TaskDocumentRef,
    prepared: _AuthoringCandidate,
) -> list[dict[str, Any]]:
    def publication() -> list[tuple[Path, Path]]:
        with integration_authority_lock(request.coordination_root, request.repo_id):
            _require_authoring_publication_authority(request, prepared.overrides)
            return write_task_doc_batch(
                [(root, document) for _ref, root, document in prepared.documents]
            )

    queue = CloseoutQueueStore(request.coordination_root, sprint_ref)
    written = queue.publish_sprint_update(
        publication,
        completed=prepared.sprint.status == "Completed",
        recorded_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        validate_completion=lambda: require_commanded_masters_completed(
            topology,
            sprint_ref,
            prepared.overrides,
        ),
    )
    return [
        {
            "taskDocumentRef": ref.model_dump(mode="json"),
            "docPath": json_path.as_posix(),
            "renderedPath": markdown_path.as_posix(),
        }
        for (ref, _root, _document), (json_path, markdown_path) in zip(
            prepared.documents, written, strict=True
        )
    ]


def _verify_authoring_judgments(
    topology: TaskDocumentTopology,
    sprint_ref: TaskDocumentRef,
    mutations: list[_GraphAuthoringMutation],
) -> None:
    """Verify every claimed judgmentId against the sprint's canonical Judgment Register.

    Fail-closed with a named recovery: a sprint without a ``Judgment Register
    (canonical judgment authority)`` section cannot take judgment-bearing mutations.
    """

    claimed = [
        (mutation.op, judgment_id)
        for mutation in mutations
        if (judgment_id := getattr(mutation, "judgmentId", None))
    ]
    statically_required = [
        mutation.op
        for mutation in mutations
        if _statically_judgment_bearing(mutation) and not getattr(mutation, "judgmentId", None)
    ]
    if statically_required:
        raise ExecutionTopologyError(
            "task-execution-graph-judgment-required: mutations "
            f"{statically_required!r} require a judgmentId from the sprint Judgment Register"
        )
    verify_sprint_judgment_ids(topology, sprint_ref, claimed)


def verify_sprint_judgment_ids(
    topology: TaskDocumentTopology,
    sprint_ref: TaskDocumentRef,
    claimed: list[tuple[str, str]],
) -> None:
    """Verify ``(operation, judgmentId)`` claims against the sprint's Judgment Register.

    Shared by the graph-authoring batch and the sprint linkage operations (L14): the
    sprint ref must already be canonicalized by the caller. An empty claim list is a
    no-op; otherwise a missing/malformed register or an unknown or wrongly-authored
    row refuses fail-closed.
    """

    if not claimed:
        return
    # The sprint ref was canonicalized and resolved by the caller just above.
    sprint = topology.resolve(sprint_ref)
    headings = {section.heading.strip().casefold() for section in sprint.document.sections}
    if JUDGMENT_REGISTER_SECTION not in headings:
        raise ExecutionTopologyError(
            "task-execution-graph-judgment-register-missing: "
            f"sprint {sprint_ref.key} has no 'Judgment Register (canonical judgment "
            "authority)' section; restore it with task_doc.set_section (sprint creation "
            "scaffolds the empty canonical registers; the write path validates their shape)"
        )
    try:
        judgments, _priorities = planning_authorities(sprint)
    except CloseoutQueueError as exc:
        raise ExecutionTopologyError(str(exc)) from exc
    for op, judgment_id in claimed:
        judgment = judgments.get(judgment_id)
        if judgment is None:
            raise ExecutionTopologyError(
                "task-execution-graph-judgment-unknown: "
                f"canonical Judgment Register has no row {judgment_id!r} (mutation {op})"
            )
        if judgment.author not in {"strategist", "orchestrator"}:
            raise ExecutionTopologyError(
                "task-execution-graph-judgment-author-refused: "
                f"judgment {judgment_id!r} was authored by {judgment.author!r}; "
                "only strategist/orchestrator rows may back graph authoring"
            )


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
    draft.edges.pop(matches[0])


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


def _require_complete_partitions(
    topology: TaskDocumentTopology,
    sprint_ref: TaskDocumentRef,
    overrides: dict[TaskDocumentRef, TaskDocument],
) -> None:
    """Refuse to persist a graph whose segments do not exactly cover the live leaf sets."""

    # Runs after _validate_topology with the same overrides, so validation cannot fail here.
    placements = topology.execution_leaf_placement(sprint_ref, overrides=overrides)
    unknown = [
        f"{report.master.ref.key}:{leaf}"
        for report in placements
        for leaf in report.placement.unknown_leaf_ids
    ]
    if unknown:
        raise ExecutionTopologyError(
            "task-execution-graph-partition-unknown-leaf: segments place leafs outside the "
            f"master's planned leaf set: {unknown!r}"
        )
    unplaced = [
        f"{report.master.ref.key}:{leaf}"
        for report in placements
        for leaf in report.placement.unplaced_leaf_ids
    ]
    if unplaced:
        raise ExecutionTopologyError(
            "task-execution-graph-partition-incomplete: every planned leaf of a segmented "
            f"master must be placed; unplaced={unplaced!r}"
        )


def _authoring_placement_facts(
    topology: TaskDocumentTopology,
    sprint_ref: TaskDocumentRef,
    overrides: dict[TaskDocumentRef, TaskDocument],
) -> list[dict[str, Any]]:
    # Runs after _validate_topology with the same overrides, so validation cannot fail here.
    placements = topology.execution_leaf_placement(sprint_ref, overrides=overrides)
    return [
        fact
        for report in placements
        for fact in leaf_placement_facts(report.master.ref.key, report.placement)
    ]


def _require_authoring_publication_authority(
    request: ExecutionTopologyAuthoringRequest,
    overrides: dict[TaskDocumentRef, TaskDocument],
) -> None:
    try:
        require_topology_migration_authority(
            request.coordination_root,
            request.repo_id,
            request.code_repository,
            request.memory_repository,
            overrides,
        )
    except RuntimeError as exc:
        raise ExecutionTopologyError(str(exc)) from exc


def require_commanded_masters_completed(
    topology: TaskDocumentTopology,
    sprint_ref: TaskDocumentRef,
    overrides: dict[TaskDocumentRef, TaskDocument],
) -> None:
    """Refuse sprint completion until every exact graph master is terminal."""

    try:
        masters = topology.validate_execution_topology(sprint_ref, overrides=overrides)
    except TaskDocumentRefError as exc:
        raise ExecutionTopologyError(f"{exc.status}: {exc}") from exc
    incomplete = sorted(
        master.ref.key
        for master in masters
        if master.document.status != "Completed" or completion_blockers(master.document)
    )
    if incomplete:
        raise ExecutionTopologyError(
            "orchestration sprint cannot complete while commanded masters remain incomplete: "
            f"{incomplete!r}"
        )


def enforce_execution_topology_edit(request: ExecutionTopologyEditRequest) -> None:
    """Validate authoring operations that create or change execution topology."""

    if not _execution_topology_edit_required(request):
        return
    topology = TaskDocumentTopology(request.coordination_root)
    json_path = json_path_for(request.task_root, request.candidate)
    ref = _task_document_ref(request, json_path)
    overrides = {ref: request.candidate}
    try:
        sprint_refs: set[TaskDocumentRef] = set()
        if request.candidate.orchestrates:
            sprint_refs.add(ref)
        elif request.original is not None and request.original.orchestrates:
            raise ExecutionTopologyError(
                "an orchestration sprint cannot remove its execution topology through "
                f"task_doc.{request.operation}; use task_doc.author_execution_graph"
            )
        if (
            request.original is not None
            and request.original.executionGraph is not None
            and request.candidate.executionGraph is None
        ):
            # The default mode may be chosen at creation, but an authored graph is
            # only ever retired through the graph-authoring seam, never by dropping
            # the field from a write.
            raise ExecutionTopologyError(
                "an orchestration sprint cannot remove its executionGraph through "
                f"task_doc.{request.operation}; use task_doc.author_execution_graph"
            )
        sprint_refs.update(
            sprint.ref
            for sprint in topology.execution_sprints_affected_by_master(
                ref,
                original=request.original,
                candidate=request.candidate,
            )
        )
        for sprint_ref in sorted(sprint_refs, key=lambda item: item.key):
            sprint = topology.resolve_candidate(sprint_ref, overrides)
            if sprint.document.executionGraph is None:
                # The atomic-sequential default (L13-R1): a graph-less sprint has no
                # topology contract to validate; the series lane serializes masters.
                continue
            topology.validate_execution_topology(sprint_ref, overrides=overrides)
    except TaskDocumentRefError as exc:
        raise ExecutionTopologyError(f"{exc.status}: {exc}") from exc


def _execution_topology_edit_required(request: ExecutionTopologyEditRequest) -> bool:
    relevant = {"title", "orchestrates", "executionNature", "executionGraph"}
    if request.operation not in {"create", "replace", "set_field"}:
        return False
    original_is_master = request.original is not None and request.original.kind == "master"
    if request.candidate.kind != "master" and not original_is_master:
        return False
    if request.operation == "set_field":
        return bool(relevant.intersection(request.fields))
    return True


def _task_document_ref(request: ExecutionTopologyEditRequest, json_path: Path) -> TaskDocumentRef:
    root = (request.coordination_root / "tasks" / request.repo_id).resolve(strict=False)
    resolved = json_path.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ExecutionTopologyError(
            f"task document is outside tasks/{request.repo_id}: {json_path}"
        )
    return TaskDocumentRef(
        repository=request.repo_id,
        path=resolved.relative_to(root).as_posix(),
    )


def _existing_json(task_root: Path, slug: str | None) -> Path:
    path = task_root / f"{slug or 'task'}.json"
    if not path.exists():
        raise ExecutionTopologyError(f"task document not found: {path} (create it first)")
    return path


def _validate_topology(
    topology: TaskDocumentTopology,
    sprint_ref: TaskDocumentRef,
    overrides: dict[TaskDocumentRef, TaskDocument],
) -> None:
    try:
        topology.validate_execution_topology(sprint_ref, overrides=overrides)
    except TaskDocumentRefError as exc:
        raise ExecutionTopologyError(f"{exc.status}: {exc}") from exc


def _document_preview(
    ref: TaskDocumentRef, task_root: Path, document: TaskDocument
) -> dict[str, Any]:
    rendered = render_markdown(document)
    markdown_path = markdown_path_for(task_root, document)
    existing = markdown_path.read_text(encoding="utf-8") if markdown_path.exists() else ""
    diff = "".join(
        difflib.unified_diff(
            existing.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=f"{markdown_path.name} (on disk)",
            tofile=f"{markdown_path.name} (rendered)",
        )
    )
    rendered_lines = set(rendered.splitlines())
    return {
        "taskDocumentRef": ref.model_dump(mode="json"),
        "docPath": json_path_for(task_root, document).as_posix(),
        "renderedPath": markdown_path.as_posix(),
        "rendered": rendered,
        "diff": diff,
        "wouldLose": any(
            line.strip() and line not in rendered_lines for line in existing.splitlines()
        ),
    }


@dataclass(frozen=True)
class ExecutionTopologyInventoryRequest:
    coordination_root: Path
    repo_id: str
    code_repository: Path


def inventory_execution_topology(
    request: ExecutionTopologyInventoryRequest,
) -> dict[str, Any]:
    """Enumerate every persistent sprint and commanded master before graph authoring.

    Read-only. The proposed explicit nature preserves current behavior: a commanded master
    that already has an ``ar/<slug>`` branch is proposed ``atomic`` (it keeps its branch),
    everything else is proposed ``organizational`` (direct-super ancestry). Legacy sprints
    carry no dependency edges, so the proposed graph is parallel and edges are left for an
    accepted strategist/orchestrator ruling — never inferred from file order or names.
    """

    topology = TaskDocumentTopology(request.coordination_root)
    masters = topology.repository_masters(request.repo_id)
    sprints = sorted((m for m in masters if m.document.orchestrates), key=lambda m: m.ref.key)
    branch_slugs = _branch_slugs(request.code_repository)

    def _blockers(entry: ResolvedTaskDocument) -> list[dict[str, Any]]:
        return [b.model_dump(mode="json") for b in completion_blockers(entry.document)]

    sprint_rows: list[dict[str, Any]] = []
    for sprint in sprints:
        commanded = sorted(topology.children(sprint.ref), key=lambda ref: ref.key)
        graph = sprint.document.executionGraph
        sprint_rows.append(
            {
                "taskDocumentRef": sprint.ref.model_dump(mode="json"),
                "status": sprint.document.status,
                "executionGraph": "present" if graph is not None else "missing",
                "commandedMasters": [ref.model_dump(mode="json") for ref in commanded],
                "proposedEdges": [],
                "edgesRequireRuling": True,
                "blockers": _blockers(sprint),
            }
        )

    commanded = sorted((m for m in masters if not m.document.orchestrates), key=lambda m: m.ref.key)
    master_rows: list[dict[str, Any]] = []
    for master in commanded:
        slug = master.path.parent.name
        branch = f"ar/{slug}" if slug in branch_slugs else None
        proposed = "atomic" if branch is not None else "organizational"
        master_rows.append(
            {
                "taskDocumentRef": master.ref.model_dump(mode="json"),
                "currentNature": master.document.executionNature,
                "proposedNature": proposed,
                "branch": branch,
                "status": master.document.status,
                "enclosures": [e.model_dump(mode="json") for e in master.document.enclosures],
                "blockers": _blockers(master),
            }
        )

    return {
        "ok": True,
        "operation": "task_doc.inventory_execution_topology",
        "repoId": request.repo_id,
        "sprintCount": len(sprint_rows),
        "commandedMasterCount": len(master_rows),
        "sprints": sprint_rows,
        "commandedMasters": master_rows,
    }


def _branch_slugs(code_repository: Path) -> frozenset[str]:
    """Return the ``ar/<slug>`` local branch slugs, used to detect branch-backed masters."""

    result = run_git(code_repository, ["branch", "--format=%(refname:short)"])
    if result.returncode != 0:
        raise ExecutionTopologyError(
            f"cannot enumerate branches: {(result.stderr or result.stdout).strip()}"
        )
    slugs: set[str] = set()
    for line in result.stdout.splitlines():
        name = line.strip()
        if name.startswith("ar/"):
            slugs.add(name.removeprefix("ar/"))
    return frozenset(slugs)
