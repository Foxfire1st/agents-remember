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

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import field_validator

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_document import DocStatus, MasterExecutionNature
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    SprintExecutionGraph,
    SprintExecutionNode,
    SubTaskRef,
    TaskDocSourceSnapshot,
    TaskDocument,
    missing_task_doc_source,
    read_task_doc_with_source,
    write_task_doc_batch,
)
from agents_remember.tasks.document_refs import (
    ResolvedTaskDocument,
    TaskDocumentRefError,
    TaskDocumentTopology,
    repository_master_documents,
)
from agents_remember.tasks.sprint_membership_refusal import (
    archived_master_paths,
    missing_master_detail,
)
from agents_remember.tasks.sprint_rows import SEAT_DOC_FILE, correlate_seat_row

from .task_doc_graph_titles import build_publication_batch_graph_titles
from .task_doc_publication import (
    preview_task_doc_transaction_projection_effects,
    publish_task_doc_transaction_and_refresh,
    validate_task_doc_transaction,
)
from .task_execution_topology import (
    ExecutionTopologyError,
    verify_sprint_judgment_ids,
)
from .task_master_retirement import retire_master
from .task_sprint_candidates import SprintLinkageError, _detach_candidate
from .task_sprint_context import (
    SprintLinkageRequest,
    _document_preview,
    _parse_payload,
    _Payload,
    _publication_transaction,
    _require_serving_topology_schema,
    _sprint_context,
    _validate_candidate,
)

SPRINT_LINKAGE_OPERATIONS = ("attach_master", "detach_master", "retire_master", "linkage_report")


@dataclass(frozen=True)
class _LinkagePublication:
    topology: TaskDocumentTopology
    sprint_ref: TaskDocumentRef
    overrides: dict[TaskDocumentRef, TaskDocument]
    documents: list[tuple[TaskDocumentRef, Path, TaskDocument]]
    source_snapshots: tuple[TaskDocSourceSnapshot, ...]


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
    if operation == "retire_master":
        return retire_master(request)
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

    if not doc.is_sprint:
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
            for master in repository_master_documents(topology, sprint_ref.repository)
            if master.ref != sprint_ref and not master.document.is_sprint
        ]
    except (TaskDocumentRefError, OSError, ValueError) as exc:
        return [{"kind": "sprint-scan-failed", "detail": str(exc)}]
    membership = _commanded_membership(sprint, masters)
    facts = [
        _unresolved_entry_fact(topology, sprint_ref, entry, matches)
        for entry, (master, matches) in membership.items()
        if master is None
    ]
    referenced = _row_facts(sprint, membership, facts)
    facts.extend(_membership_facts(membership, referenced))
    facts.extend(_uncommanded_facts(sprint, masters, membership))
    return facts


def _unresolved_entry_fact(
    topology: TaskDocumentTopology, sprint_ref: TaskDocumentRef, entry: str, matches: int
) -> dict[str, Any]:
    """An ``orchestrates`` entry that names no single live master, with its repair when it is gone."""

    fact: dict[str, Any] = {
        "kind": "orchestrates-entry-unresolved",
        "entry": entry,
        "matches": matches,
    }
    if matches == 0:
        # The same text every topology consumer refuses with: the master, where it was archived,
        # and the operation that removes it from the sprint.
        fact["archivePaths"] = [
            path.as_posix()
            for path in archived_master_paths(topology.coordination_root, sprint_ref, entry)
        ]
        fact["detail"] = missing_master_detail(topology.coordination_root, sprint_ref, entry)
    return fact


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
    if master.document.is_sprint:
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


# --- shared validation + publication ------------------------------------------


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
        if row.retirement is not None:
            if row.file:
                facts.append(_retired_seat_fact(sprint, row))
            continue
        master_ref = row.masterRef
        if master_ref is None and SEAT_DOC_FILE.match(row.file or ""):
            master_ref = correlate_seat_row(sprint.path.parent, sprint.ref.repository, row)
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


def _retired_seat_fact(sprint: ResolvedTaskDocument, row: SubTaskRef) -> dict[str, Any]:
    """The seat documents a retired master left in the sprint's folder, reachable from its row.

    A retirement that takes the place of a legacy seat row keeps that row's ``file`` cell. The
    seat coordinated a master that is retired, so it is no linkage drift; the fact says whose seat
    documents these are and which of them are there.
    """

    assert row.retirement is not None
    seat = sprint.path.parent / row.file
    return {
        "kind": "retired-master-seat-documents",
        "number": row.number,
        "file": row.file,
        "master": row.retirement.masterRef.key,
        "documents": [
            document.name
            for document in (seat.with_suffix(".json"), seat.with_suffix(".md"))
            if document.is_file()
        ],
    }


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
