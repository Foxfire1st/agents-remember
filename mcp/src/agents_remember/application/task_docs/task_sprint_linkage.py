"""Sprint↔master linkage: one atomic attach/detach operation and the drift report.

``attach_master`` supersedes the three-write manual flow (``set_subtask`` +
``set_field orchestrates`` + ``author_execution_graph add_node``) that produced the
M16/rc7 drift (R4-F1/F2). One validated batch writes the typed subTask row
(``masterRef``), the ``orchestrates`` membership slug, the executionGraph lump node
(only when the sprint has a graph — the L13 atomic-sequential default governs a
graph-less sprint and the result reports ``graphNode: deferred-no-graph-default``),
and the master's ``executionNature`` assertion (a nature-less master requires
``executionNature`` plus a ``judgmentId`` verified against the sprint's canonical
Judgment Register; disagreeing with an existing nature refuses). Full topology
validation — or, on a graph-less sprint, the linkage cross-check — precedes the
single atomic batch write, so a partial attach is structurally impossible; dry-run
previews every affected JSON/Markdown pair.

``detach_master`` is symmetric: it removes the typed row, the membership slug, and
the graph node, refuses while any edge touches the master's node, and never deletes
files (seat documents stay on disk as historical records).

``linkage_report`` is the read-only drift surface (L14-R5): legacy and inconsistent
linkage shapes — seat-doc rows, slug-only membership, row/membership mismatches,
uncommanded masters named in sprint decisions — are reported as facts, never as hard
errors (L14-R7 backward tolerance), and it never raises.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_document import DocStatus, MasterExecutionNature
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    SprintExecutionEndpoint,
    SprintExecutionGraph,
    SprintExecutionNode,
    SubTaskRef,
    TaskDocSourceSnapshot,
    TaskDocument,
    completion_blockers,
    json_path_for,
    markdown_path_for,
    missing_task_doc_source,
    read_graph_titles,
    read_task_doc,
    read_task_doc_with_source,
    render_markdown,
    write_task_doc_batch,
)
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
)
from agents_remember.tasks.leaf_doc import (
    TerminalLeafResolutionError,
    resolve_terminal_leaf_doc,
)
from agents_remember.tasks.serving_preflight import (
    TopologyServingBuildError,
    require_serving_topology_schema,
)

from .task_doc_graph_titles import build_publication_batch_graph_titles
from .task_doc_publication import (
    TaskDocPublicationTransaction,
    preview_task_doc_transaction_projection_effects,
    publish_task_doc_transaction_and_refresh,
    task_doc_scope_changes,
    validate_task_doc_transaction,
)
from .task_execution_topology import (
    ExecutionTopologyError,
    verify_sprint_judgment_ids,
)

SPRINT_LINKAGE_OPERATIONS = ("attach_master", "detach_master", "linkage_report")


def _require_serving_topology_schema() -> None:
    """Wrap the served-build preflight in the linkage error family (L15-R4)."""

    try:
        require_serving_topology_schema()
    except TopologyServingBuildError as exc:
        raise SprintLinkageError(str(exc)) from exc


_SEAT_DOC_FILE = re.compile(r"^(\d+_manage-|00_.*-seat)")
_SEAT_MASTER_REFERENCE = re.compile(r"\.\./([^/]+)/task\.json$")


class SprintLinkageError(AgentsRememberError):
    """A sprint linkage edit is structurally invalid or unverifiable."""


@dataclass(frozen=True)
class SprintLinkageRequest:
    coordination_root: Path
    repo_id: str
    code_repository: Path
    memory_repository: Path | None
    task_root: Path
    slug: str | None
    fields: dict[str, Any]
    dry_run: bool


@dataclass(frozen=True)
class _LinkagePublication:
    topology: TaskDocumentTopology
    sprint_ref: TaskDocumentRef
    overrides: dict[TaskDocumentRef, TaskDocument]
    documents: list[tuple[TaskDocumentRef, Path, TaskDocument]]
    source_snapshots: tuple[TaskDocSourceSnapshot, ...]


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    masterRef: TaskDocumentRef

    @field_validator("judgmentId", check_fields=False)
    @classmethod
    def _trim_judgment_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("judgmentId must not be blank")
        return trimmed


class _AttachMasterPayload(_Payload):
    number: str
    name: str | None = None
    scope: str = ""
    status: DocStatus = "planning"
    executionNature: MasterExecutionNature | None = None
    judgmentId: str | None = None

    @field_validator("number")
    @classmethod
    def _trim_number(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("attach_master requires a nonblank row number")
        return trimmed

    @field_validator("name")
    @classmethod
    def _trim_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("attach_master row name must not be blank")
        return trimmed


class _DetachMasterPayload(_Payload):
    pass


_PayloadT = TypeVar("_PayloadT", bound=_Payload)


@dataclass(frozen=True)
class SprintLinkageCall:
    """The tool-layer call context for one sprint linkage operation."""

    config: McpRuntimeConfig
    repo_id: str
    task_root: Path
    slug: str | None
    fields: dict[str, Any]
    dry_run: bool


def sprint_linkage_operation(operation: str, call: SprintLinkageCall) -> dict[str, Any]:
    """Dispatch one sprint linkage operation; the tool layer wraps SprintLinkageError."""

    repository = call.config.repositories[call.repo_id]
    request = SprintLinkageRequest(
        coordination_root=call.config.coordination_root,
        repo_id=call.repo_id,
        code_repository=repository.path,
        memory_repository=repository.memory_root,
        task_root=call.task_root,
        slug=call.slug,
        fields=call.fields,
        dry_run=call.dry_run,
    )
    if operation == "attach_master":
        return attach_master(request)
    if operation == "detach_master":
        return detach_master(request)
    return linkage_report(request)


def attach_master(request: SprintLinkageRequest) -> dict[str, Any]:
    """Attach one master to a sprint as a single validated atomic batch (L14-R4)."""

    _require_serving_topology_schema()
    payload = _parse_payload(_AttachMasterPayload, request.fields, "attach_master")
    topology, sprint_ref, sprint, sprint_source = _sprint_context(request, "attach_master")
    master, master_source = _resolve_attach_target(topology, sprint_ref, payload.masterRef)
    _require_not_attached(sprint, master)
    if any(row.number == payload.number for row in sprint.subTasks):
        raise SprintLinkageError(
            f"task-sprint-linkage-row-number-taken: row {payload.number!r} already exists"
        )
    candidate_master = _assert_execution_nature(topology, sprint_ref, master, payload)
    if payload.status == "Completed":
        _require_completed_master(candidate_master or master.document, master.ref, payload.number)
    candidate_sprint, graph_node = _attach_candidate(sprint, master, payload)
    overrides: dict[TaskDocumentRef, TaskDocument] = {sprint_ref: candidate_sprint}
    if candidate_master is not None:
        overrides[master.ref] = candidate_master
    _validate_candidate(topology, sprint_ref, overrides)
    documents: list[tuple[TaskDocumentRef, Path, TaskDocument]] = [
        (sprint_ref, request.task_root, candidate_sprint)
    ]
    if candidate_master is not None:
        documents.append((master.ref, master.path.parent, candidate_master))
    result: dict[str, Any] = {
        "ok": True,
        "operation": "task_doc.attach_master",
        "state": "would-attach" if request.dry_run else "attached",
        "sprintTaskDocumentRef": sprint_ref.model_dump(mode="json"),
        "masterRef": master.ref.model_dump(mode="json"),
        "subtaskNumber": payload.number,
        "graphNode": graph_node,
        "executionNatureAsserted": candidate_master is not None,
    }
    if request.dry_run:
        build_publication_batch_graph_titles(documents)
        transaction = _publication_transaction(
            request,
            overrides,
            (sprint_source, master_source),
            lambda: [],
        )
        validate_task_doc_transaction(transaction)
        result["projectionEffects"] = [
            effect.model_dump(mode="json")
            for effect in preview_task_doc_transaction_projection_effects(transaction)
        ]
        result["dryRun"] = True
        result["documents"] = [
            _document_preview(ref, root, document) for ref, root, document in documents
        ]
        return result
    published_documents, effects = _publish(
        request,
        _LinkagePublication(
            topology=topology,
            sprint_ref=sprint_ref,
            overrides=overrides,
            documents=documents,
            source_snapshots=(sprint_source, master_source),
        ),
    )
    result["documents"] = published_documents
    result["projectionEffects"] = effects
    return result


def detach_master(request: SprintLinkageRequest) -> dict[str, Any]:
    """Detach one master from a sprint; refuses while graph edges touch its node."""

    _require_serving_topology_schema()
    payload = _parse_payload(_DetachMasterPayload, request.fields, "detach_master")
    topology, sprint_ref, sprint, sprint_source = _sprint_context(request, "detach_master")
    master_ref = payload.masterRef
    if master_ref.repository != request.repo_id:
        raise SprintLinkageError(
            "task-sprint-linkage-cross-repo: cannot detach outside the sprint repository: "
            f"{master_ref.key}"
        )
    master, master_source = _resolve_tolerantly(topology, master_ref)
    rows = [row for row in sprint.subTasks if row.masterRef == master_ref]
    if not rows:
        raise SprintLinkageError(
            f"task-sprint-linkage-not-attached: no typed row links {master_ref.key}"
        )
    if len(rows) > 1:
        raise SprintLinkageError(
            f"task-sprint-linkage-row-duplicate: {len(rows)} rows link {master_ref.key}; "
            "repair the index before detaching"
        )
    candidate_sprint, removed_entries, removed_nodes = _detach_candidate(sprint, master_ref, master)
    overrides: dict[TaskDocumentRef, TaskDocument] = {sprint_ref: candidate_sprint}
    _validate_candidate(topology, sprint_ref, overrides)
    documents: list[tuple[TaskDocumentRef, Path, TaskDocument]] = [
        (sprint_ref, request.task_root, candidate_sprint)
    ]
    result: dict[str, Any] = {
        "ok": True,
        "operation": "task_doc.detach_master",
        "state": "would-detach" if request.dry_run else "detached",
        "sprintTaskDocumentRef": sprint_ref.model_dump(mode="json"),
        "masterRef": master_ref.model_dump(mode="json"),
        "removedSubtask": rows[0].number,
        "removedOrchestrates": removed_entries,
        "removedGraphNodes": removed_nodes,
        "masterResolved": master is not None,
    }
    if request.dry_run:
        build_publication_batch_graph_titles(documents)
        transaction = _publication_transaction(
            request,
            overrides,
            (sprint_source, master_source),
            lambda: [],
        )
        validate_task_doc_transaction(transaction)
        result["projectionEffects"] = [
            effect.model_dump(mode="json")
            for effect in preview_task_doc_transaction_projection_effects(transaction)
        ]
        result["dryRun"] = True
        result["documents"] = [
            _document_preview(ref, root, document) for ref, root, document in documents
        ]
        return result
    published_documents, effects = _publish(
        request,
        _LinkagePublication(
            topology=topology,
            sprint_ref=sprint_ref,
            overrides=overrides,
            documents=documents,
            source_snapshots=(sprint_source, master_source),
        ),
    )
    result["documents"] = published_documents
    result["projectionEffects"] = effects
    return result


def linkage_report(request: SprintLinkageRequest) -> dict[str, Any]:
    """The read-only sprint linkage drift report (L14-R5); never mutates anything."""

    topology, sprint_ref, _sprint, _source = _sprint_context(request, "linkage_report")
    return {
        "ok": True,
        "operation": "task_doc.linkage_report",
        "sprintTaskDocumentRef": sprint_ref.model_dump(mode="json"),
        "linkageFacts": collect_linkage_facts(topology, sprint_ref),
    }


def linkage_facts_for_get(
    coordination_root: Path, repo_id: str, json_path: Path, doc: TaskDocument
) -> list[dict[str, Any]] | None:
    """The ``linkageFacts`` surface for ``task_doc.get``; None for non-sprint targets."""

    if doc.kind != "master" or not doc.orchestrates:
        return None
    topology = TaskDocumentTopology(coordination_root)
    try:
        sprint_ref = topology.canonical_ref(repo_id, json_path)
    except TaskDocumentRefError:
        return None
    return collect_linkage_facts(topology, sprint_ref)


def collect_linkage_facts(
    topology: TaskDocumentTopology, sprint_ref: TaskDocumentRef
) -> list[dict[str, Any]]:
    """Compute the sprint's linkage drift facts (L14-R5). Read-only; never raises."""

    try:
        sprint = topology.resolve(sprint_ref)
        masters = [
            master
            for master in topology.repository_masters(sprint_ref.repository)
            if master.ref != sprint_ref and not master.document.orchestrates
        ]
    except (TaskDocumentRefError, OSError, ValueError) as exc:
        return [{"kind": "sprint-scan-failed", "detail": str(exc)}]
    membership = _commanded_membership(sprint, masters)
    facts = [
        {"kind": "orchestrates-entry-unresolved", "entry": entry, "matches": matches}
        for entry, (master, matches) in membership.items()
        if master is None
    ]
    referenced = _row_facts(sprint, membership, facts)
    facts.extend(_membership_facts(membership, referenced))
    facts.extend(_uncommanded_facts(sprint, masters, membership))
    return facts


def validate_completed_master_row(task_root: Path, ref: SubTaskRef) -> None:
    """Terminal check for one row newly marked Completed on a master document.

    A typed ``masterRef`` row (L14) completes against the linked master document
    itself; any other row resolves the terminal leaf doc exactly as before.
    """

    if ref.masterRef is not None:
        # TaskDocumentRef confines the path (no absolute or parent parts), so the
        # linked master document always sits inside the repository task root.
        master_path = task_root.parent / ref.masterRef.path
        try:
            master = read_task_doc(master_path)
        except (OSError, ValueError) as exc:
            raise SprintLinkageError(
                f"cannot mark master row {ref.number!r} Completed: cannot read the linked "
                f"master document: {exc}"
            ) from exc
        _require_completed_master(master, ref.masterRef, ref.number)
        return
    asserted = (task_root / Path(ref.file).with_suffix(".json")) if ref.file else None
    try:
        resolved = resolve_terminal_leaf_doc(task_root, ref.number, asserted_path=asserted)
    except TerminalLeafResolutionError as exc:
        raise SprintLinkageError(f"cannot mark master row {ref.number!r} Completed: {exc}") from exc
    if resolved is None:
        raise SprintLinkageError(
            f"cannot mark master row {ref.number!r} Completed: no leaf task document exists"
        )
    _path, leaf = resolved
    blockers = completion_blockers(leaf)
    if blockers:
        exact = [blocker.model_dump() for blocker in blockers]
        raise SprintLinkageError(
            f"cannot mark master row {ref.number!r} Completed: task completion refused; "
            f"unresolved work units: {exact!r}"
        )


# --- payload + sprint context -------------------------------------------------


def _parse_payload(model: type[_PayloadT], fields: dict[str, Any], operation: str) -> _PayloadT:
    try:
        return model.model_validate(fields)
    except ValidationError as exc:
        raise SprintLinkageError(f"invalid {operation} payload: {exc}") from exc


def _sprint_context(
    request: SprintLinkageRequest, operation: str
) -> tuple[TaskDocumentTopology, TaskDocumentRef, TaskDocument, TaskDocSourceSnapshot]:
    json_path = request.task_root / f"{request.slug or 'task'}.json"
    if not json_path.exists():
        raise SprintLinkageError(f"task document not found: {json_path} (create it first)")
    sprint, source = read_task_doc_with_source(json_path)
    if sprint.kind != "master" or not sprint.orchestrates:
        raise SprintLinkageError(f"task_doc.{operation} requires an orchestration sprint document")
    topology = TaskDocumentTopology(request.coordination_root)
    try:
        sprint_ref = topology.canonical_ref(request.repo_id, json_path)
    except TaskDocumentRefError as exc:
        raise SprintLinkageError(f"{exc.status}: {exc}") from exc
    return topology, sprint_ref, sprint, source


# --- attach -------------------------------------------------------------------


def _resolve_attach_target(
    topology: TaskDocumentTopology, sprint_ref: TaskDocumentRef, master_ref: TaskDocumentRef
) -> tuple[ResolvedTaskDocument, TaskDocSourceSnapshot]:
    if master_ref.repository != sprint_ref.repository:
        raise SprintLinkageError(
            "task-sprint-linkage-cross-repo: orchestrates membership is same-repository; "
            f"cannot attach {master_ref.key}"
        )
    if master_ref == sprint_ref:
        raise SprintLinkageError("task-sprint-linkage-self-attach: a sprint cannot attach itself")
    try:
        resolved = topology.resolve(master_ref)
    except TaskDocumentRefError as exc:
        raise SprintLinkageError(f"{exc.status}: {exc}") from exc
    document, source = read_task_doc_with_source(resolved.path)
    master = ResolvedTaskDocument(master_ref, resolved.path, document)
    if master.document.repo != master_ref.repository:
        raise SprintLinkageError(
            f"task-document-repo-mismatch: {master_ref.key} changed repository identity"
        )
    if master.document.kind != "master":
        raise SprintLinkageError(
            f"task-sprint-linkage-target-not-a-master: {master_ref.key} is a "
            f"{master.document.kind} document"
        )
    if master.document.orchestrates:
        raise SprintLinkageError(
            f"task-sprint-linkage-target-is-sprint: {master_ref.key} itself orchestrates; "
            "a sprint cannot be commanded as a master"
        )
    return master, source


def _require_not_attached(sprint: TaskDocument, master: ResolvedTaskDocument) -> None:
    if any(row.masterRef == master.ref for row in sprint.subTasks):
        raise SprintLinkageError(
            f"task-sprint-linkage-already-attached: a typed row already links {master.ref.key}"
        )
    names = {master.path.parent.name, master.document.id, master.document.title}
    if names.intersection(sprint.orchestrates):
        raise SprintLinkageError(
            f"task-sprint-linkage-already-attached: orchestrates already commands "
            f"{master.ref.key}; detach it first"
        )
    graph = sprint.executionGraph
    if graph is not None and master.ref in graph.master_refs():
        raise SprintLinkageError(
            "task-sprint-linkage-already-attached: the executionGraph already places "
            f"{master.ref.key}"
        )


def _assert_execution_nature(
    topology: TaskDocumentTopology,
    sprint_ref: TaskDocumentRef,
    master: ResolvedTaskDocument,
    payload: _AttachMasterPayload,
) -> TaskDocument | None:
    """Return the nature-asserted master candidate, or None when nature is unchanged."""

    existing = master.document.executionNature
    if existing is None and (payload.executionNature is None or payload.judgmentId is None):
        raise SprintLinkageError(
            f"task-sprint-linkage-nature-required: nature-less master {master.ref.key} "
            "requires executionNature plus a judgmentId from the sprint Judgment Register"
        )
    if (
        existing is not None
        and payload.executionNature is not None
        and payload.executionNature != existing
    ):
        raise SprintLinkageError(
            f"task-sprint-linkage-nature-mismatch: master {master.ref.key} already carries "
            f"executionNature {existing!r}; reclassify it with task_doc.author_execution_graph"
        )
    try:
        verify_sprint_judgment_ids(
            topology,
            sprint_ref,
            [("attach_master", payload.judgmentId)] if payload.judgmentId else [],
        )
    except ExecutionTopologyError as exc:
        raise SprintLinkageError(str(exc)) from exc
    if existing is not None:
        return None
    data = master.document.model_dump(by_alias=True)
    data["executionNature"] = payload.executionNature
    return TaskDocument.model_validate(data)


def _attach_candidate(
    sprint: TaskDocument, master: ResolvedTaskDocument, payload: _AttachMasterPayload
) -> tuple[TaskDocument, str]:
    row: dict[str, Any] = {
        "number": payload.number,
        "name": payload.name or master.document.title,
        "status": payload.status,
        "masterRef": master.ref.model_dump(mode="json"),
    }
    if payload.scope:
        row["scope"] = payload.scope
    data = sprint.model_dump(by_alias=True)
    data["subTasks"] = [*data.get("subTasks", []), row]
    # _require_not_attached refused every alias of the master already, so the folder
    # slug cannot be present; membership grows by exactly this entry.
    data["orchestrates"] = [*sprint.orchestrates, master.path.parent.name]
    graph_node = "deferred-no-graph-default"
    if sprint.executionGraph is not None:
        # The graph was valid at read and gains one unique lump node (its absence was
        # checked above); edges are carried unchanged, so construction cannot fail.
        graph = SprintExecutionGraph(
            nodes=[*sprint.executionGraph.nodes, SprintExecutionNode(ref=master.ref)],
            edges=list(sprint.executionGraph.edges),
        )
        data["executionGraph"] = graph.model_dump(mode="json")
        graph_node = "added"
    # The sprint was valid at read and the batch adds only schema-valid pieces;
    # _validate_candidate runs the cross-document topology checks afterwards.
    candidate = TaskDocument.model_validate(data)
    return candidate, graph_node


# --- detach -------------------------------------------------------------------


def _resolve_tolerantly(
    topology: TaskDocumentTopology, master_ref: TaskDocumentRef
) -> tuple[ResolvedTaskDocument | None, TaskDocSourceSnapshot]:
    """Resolve the detach target; a deleted master document still allows cleanup."""

    try:
        resolved = topology.resolve(master_ref)
    except TaskDocumentRefError as exc:
        if exc.status != "task-document-not-found":
            raise SprintLinkageError(f"{exc.status}: {exc}") from exc
        return None, missing_task_doc_source(topology.path_for_ref(master_ref))
    document, source = read_task_doc_with_source(resolved.path)
    if document.repo != master_ref.repository:
        raise SprintLinkageError(
            f"task-document-repo-mismatch: {master_ref.key} changed repository identity"
        )
    return ResolvedTaskDocument(master_ref, resolved.path, document), source


def _detach_candidate(
    sprint: TaskDocument, master_ref: TaskDocumentRef, master: ResolvedTaskDocument | None
) -> tuple[TaskDocument, list[str], int]:
    graph = sprint.executionGraph
    removed_nodes = 0
    if graph is not None:
        _require_no_touching_edges(graph, master_ref)
        remaining = [node for node in graph.nodes if node.ref != master_ref]
        removed_nodes = len(graph.nodes) - len(remaining)
        if not remaining:
            raise SprintLinkageError(
                "task-sprint-linkage-graph-empty: detaching the last master would empty the "
                "executionGraph; the graph has no retire operation"
            )
        # The graph was valid at read; dropping whole nodes cannot invalidate it
        # (touching edges were refused above), so construction cannot fail.
        data_graph: Any = SprintExecutionGraph(nodes=remaining, edges=list(graph.edges)).model_dump(
            mode="json"
        )
    names = {Path(master_ref.path).parent.name}
    if master is not None:
        names |= {master.document.id, master.document.title}
    removed_entries = [entry for entry in sprint.orchestrates if entry in names]
    kept_rows = [
        row.model_dump(mode="json", by_alias=True, exclude_none=True)
        for row in sprint.subTasks
        if row.masterRef != master_ref
    ]
    data = sprint.model_dump(by_alias=True)
    data["subTasks"] = kept_rows
    data["orchestrates"] = [entry for entry in sprint.orchestrates if entry not in names]
    if graph is not None:
        data["executionGraph"] = data_graph
    # Valid at read; the batch only removes pieces, so re-validation cannot fail.
    candidate = TaskDocument.model_validate(data)
    return candidate, removed_entries, removed_nodes


def _require_no_touching_edges(graph: SprintExecutionGraph, master_ref: TaskDocumentRef) -> None:
    def touches(endpoint: TaskDocumentRef | SprintExecutionEndpoint) -> bool:
        ref = endpoint.ref if isinstance(endpoint, SprintExecutionEndpoint) else endpoint
        return ref == master_ref

    touching = [
        edge for edge in graph.edges if touches(edge.predecessor) or touches(edge.successor)
    ]
    if touching:
        raise SprintLinkageError(
            f"task-sprint-linkage-node-in-use: {len(touching)} edge(s) still touch "
            f"{master_ref.key}; remove them with task_doc.author_execution_graph first"
        )


# --- shared validation + publication ------------------------------------------


def _require_completed_master(
    document: TaskDocument, master_ref: TaskDocumentRef, row_number: str
) -> None:
    blockers = completion_blockers(document)
    if document.status != "Completed" or blockers:
        exact = [blocker.model_dump() for blocker in blockers]
        raise SprintLinkageError(
            f"cannot mark master row {row_number!r} Completed: linked master "
            f"{master_ref.key} is {document.status}, unresolved units: {exact!r}"
        )


def _validate_candidate(
    topology: TaskDocumentTopology,
    sprint_ref: TaskDocumentRef,
    overrides: dict[TaskDocumentRef, TaskDocument],
) -> None:
    """Full topology validation on a graphed sprint; the linkage cross-check otherwise."""

    sprint = topology.resolve(sprint_ref, overrides)
    try:
        if sprint.document.executionGraph is not None:
            topology.validate_execution_topology(sprint_ref, overrides=overrides)
        else:
            # The L13 atomic-sequential default governs a graph-less sprint; only the
            # typed linkage cross-check applies (L14-R5).
            topology.commanded_masters(sprint, overrides=overrides)
            topology.validate_sprint_linkage(sprint_ref, overrides=overrides)
    except TaskDocumentRefError as exc:
        raise SprintLinkageError(f"{exc.status}: {exc}") from exc


def _publish(
    request: SprintLinkageRequest,
    batch: _LinkagePublication,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    graph_titles = build_publication_batch_graph_titles(batch.documents)
    publication = publish_task_doc_transaction_and_refresh(
        _publication_transaction(
            request,
            batch.overrides,
            batch.source_snapshots,
            lambda: write_task_doc_batch(
                [(root, document) for _ref, root, document in batch.documents],
                graph_titles=graph_titles,
            ),
        )
    )
    documents = [
        {
            "taskDocumentRef": ref.model_dump(mode="json"),
            "docPath": json_path.as_posix(),
            "renderedPath": markdown_path.as_posix(),
        }
        for (ref, _root, _document), (json_path, markdown_path) in zip(
            batch.documents, publication.written, strict=True
        )
    ]
    effects = [effect.model_dump(mode="json") for effect in publication.projection_effects]
    return documents, effects


def _publication_transaction(
    request: SprintLinkageRequest,
    overrides: dict[TaskDocumentRef, TaskDocument],
    source_snapshots: tuple[TaskDocSourceSnapshot, ...],
    publisher: Callable[[], list[tuple[Path, Path]]],
) -> TaskDocPublicationTransaction:
    return TaskDocPublicationTransaction(
        coordination_root=request.coordination_root,
        target_repo_id=request.repo_id,
        source_snapshots=source_snapshots,
        scope_changes=task_doc_scope_changes(
            request.coordination_root,
            request.repo_id,
            overrides,
            source_snapshots,
        ),
        publisher=publisher,
    )


def _document_preview(
    ref: TaskDocumentRef, task_root: Path, document: TaskDocument
) -> dict[str, Any]:
    rendered = render_markdown(
        document,
        graph_titles=(
            read_graph_titles(task_root.parents[1], document.executionGraph)
            if document.executionGraph is not None
            else None
        ),
    )
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


# --- linkage facts ------------------------------------------------------------


def _commanded_membership(
    sprint: ResolvedTaskDocument, masters: list[ResolvedTaskDocument]
) -> dict[str, tuple[ResolvedTaskDocument | None, int]]:
    """Tolerant alias resolution: orchestrates entry → (exactly one master, match count)."""

    membership: dict[str, tuple[ResolvedTaskDocument | None, int]] = {}
    for entry in sprint.document.orchestrates:
        matches = [
            master
            for master in masters
            if entry in {master.path.parent.name, master.document.id, master.document.title}
        ]
        membership[entry] = (matches[0] if len(matches) == 1 else None, len(matches))
    return membership


def _row_facts(
    sprint: ResolvedTaskDocument,
    membership: dict[str, tuple[ResolvedTaskDocument | None, int]],
    facts: list[dict[str, Any]],
) -> dict[TaskDocumentRef, SubTaskRef]:
    """Append seat-doc-row and row-without-membership facts; return master ref → row."""

    commanded = {master.ref for master, _matches in membership.values() if master is not None}
    referenced: dict[TaskDocumentRef, SubTaskRef] = {}
    for row in sprint.document.subTasks:
        master_ref = row.masterRef
        if master_ref is None and _SEAT_DOC_FILE.match(row.file or ""):
            master_ref = _correlate_seat_row(sprint, row)
            if master_ref is None:
                # The seat doc exists but carries no ../<master>/task.json
                # reference: report the correlation miss so a later
                # membership-without-row fact for its master reads as a miss,
                # not a genuinely missing row (L15-R8 F8).
                facts.append(
                    {
                        "kind": "seat-doc-row-unresolved",
                        "number": row.number,
                        "file": row.file,
                    }
                )
            else:
                facts.append(
                    {
                        "kind": "seat-doc-row",
                        "number": row.number,
                        "file": row.file,
                        "master": master_ref.key,
                    }
                )
        if master_ref is None:
            continue
        referenced.setdefault(master_ref, row)
        if master_ref not in commanded:
            facts.append(
                {
                    "kind": "row-without-membership",
                    "number": row.number,
                    "master": master_ref.key,
                }
            )
    return referenced


def _correlate_seat_row(sprint: ResolvedTaskDocument, row: SubTaskRef) -> TaskDocumentRef | None:
    """The master a legacy seat-doc row coordinates, via the seat doc's references."""

    seat_json = (sprint.path.parent / (row.file or "")).with_suffix(".json")
    try:
        seat = read_task_doc(seat_json)
    except (OSError, ValueError):
        return None
    for reference in seat.references:
        match = _SEAT_MASTER_REFERENCE.search(reference.strip())
        if match:
            return TaskDocumentRef(
                repository=sprint.ref.repository, path=f"{match.group(1)}/task.json"
            )
    return None


def _membership_facts(
    membership: dict[str, tuple[ResolvedTaskDocument | None, int]],
    referenced: dict[TaskDocumentRef, SubTaskRef],
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    resolved = [master for master, _matches in membership.values() if master is not None]
    for master in sorted(resolved, key=lambda entry: entry.ref.key):
        row = referenced.get(master.ref)
        if row is None:
            facts.append({"kind": "membership-without-row", "master": master.ref.key})
        elif row.masterRef is None:
            facts.append(
                {"kind": "slug-only-membership", "master": master.ref.key, "row": row.number}
            )
    return facts


def _uncommanded_facts(
    sprint: ResolvedTaskDocument,
    masters: list[ResolvedTaskDocument],
    membership: dict[str, tuple[ResolvedTaskDocument | None, int]],
) -> list[dict[str, Any]]:
    """Masters named in the sprint's decisions but never commanded (L14-R5 report)."""

    commanded = {master.ref for master, _matches in membership.values() if master is not None}
    facts: list[dict[str, Any]] = []
    for master in sorted(masters, key=lambda entry: entry.ref.key):
        if master.ref in commanded:
            continue
        names = {master.path.parent.name, master.document.id}
        mentioned = [
            decision.at
            for decision in sprint.document.decisions
            if any(name and name in f"{decision.decision} {decision.rationale}" for name in names)
        ]
        if mentioned:
            facts.append(
                {
                    "kind": "uncommanded-master",
                    "master": master.ref.key,
                    "decisionAt": mentioned[0],
                }
            )
    return facts
