"""Task-document and series readers: summaries, full bodies, lifecycle binding.

Task JSON is the source of truth (never the rendered markdown). These readers
project task documents and the series checklist, resolve cross-folder lifecycle
links, and hash full bodies for the on-demand body endpoint.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, overload

from agents_remember.kernel.direct_execution_grants import (
    ARCHITECT_GRANTER,
    DEVELOPER_GRANTER,
    DirectExecutionGrant,
    grants_for_task,
)
from agents_remember.models.task_intent import (
    AcceptanceObligationQuestion,
    ApprovedRequirementPacketRef,
)
from agents_remember.observer.projection import (
    DirectGrantNode,
    DiscardedSubTaskNode,
    EnclosureNode,
    SeriesNode,
    SeriesSubTaskNode,
    TaskCodeExampleNode,
    TaskDecisionNode,
    TaskDocNode,
    TaskDocumentRef,
    TaskExecutionGraphNode,
    TaskExecutionNode,
    TaskHeaderNoteNode,
    TaskReviewFindingNode,
    TaskReviewStateNode,
    TaskSeatNode,
    TaskSectionNode,
    TaskStepDispositionNode,
    TaskStepNode,
    TaskSubStepNode,
    TaskSubTaskRefNode,
)
from agents_remember.observer.projection_closeout import DiscardUnstartedProofNode
from agents_remember.observer.projection_graph import (
    GraphNodeLike,
    GraphPredecessorFacts,
    MasterGraphFacts,
    TaskExecutionGraphView,
    build_execution_graph_view,
)
from agents_remember.serving.projections.snapshots_impl._common import (
    _canonical_task_json_candidates,
    _file_age_seconds,
    _iter_task_document_payloads,
    _read_json,
    _TaskDocumentLifecycleMaps,
)
from agents_remember.tasks import (
    TASK_DOCUMENT_SCHEMA,
    SprintExecutionGraph,
    TaskDocument,
    build_graph_titles,
    current_step,
    is_product_record_decision,
    series_abandoned,
    series_done,
    series_total,
    step_done,
    step_total,
)
from pydantic import ValidationError

# A small fixed bound, not one pass per nesting level: pydantic reports ``extra_forbidden`` at
# every nesting level in a single error report, so one pruning pass clears a document carrying
# unknown keys simultaneously at document, step, substep, section, decision and sub-task level.
# The bound is what stops a loc this reader cannot address from looping -- a loc step that is not
# a key of the raw payload (the ``ref`` ``SprintExecutionNode._lift_legacy_ref`` invents for a
# legacy bare node, or a union branch label in an edge endpoint) prunes nothing, so such a
# document is withheld after the bound exactly as it was before this helper.
_UNKNOWN_FIELD_PASSES = 4


def _projected_document(payload: dict[str, object]) -> TaskDocument | None:
    """Parse one durable task document for projection, ignoring keys this reader cannot know.

    ``payload`` is durable truth written by *another* process -- the MCP server, a worktree
    seat, a build older or newer than this reader -- so it may carry a field this reader's
    ``TaskDocument`` version has never heard of. The authoring model is deliberately strict
    (``extra="forbid"``) so ``task_doc`` refuses a typo, but re-applying that strictness here
    deleted the whole document instead: a master's sub-task row then rendered "not authored as
    a task document yet" instead of drilling in, and the on-demand body endpoint 404'd, with no
    diagnostic anywhere. A reader must not let one unknown field erase a document.

    Keys pydantic rejects as ``extra_forbidden`` are dropped and the document is validated
    again; every other failure (missing required field, bad enum, malformed nested value) still
    yields ``None`` exactly as before.
    """
    candidate: Any = payload
    for _ in range(_UNKNOWN_FIELD_PASSES):
        try:
            return TaskDocument.model_validate(candidate)
        except ValidationError as error:
            unknown = [item["loc"] for item in error.errors() if item["type"] == "extra_forbidden"]
            if not unknown:
                return None
            candidate = _without_paths(candidate, unknown)
    return None


def _without_paths(payload: Any, paths: list[tuple[Any, ...]]) -> Any:
    """A deep copy of ``payload`` with every addressed key removed."""
    pruned = deepcopy(payload)
    for loc in paths:
        target = pruned
        for key in loc[:-1]:
            try:
                target = target[key]
            except (KeyError, IndexError, TypeError):
                break
        else:
            if isinstance(target, dict):
                target.pop(loc[-1], None)
            elif isinstance(target, list) and isinstance(loc[-1], int):
                target.pop(loc[-1])
    return pruned


def read_task_documents(
    coordination_root: Path, *, enclosures: list[EnclosureNode], now: datetime
) -> list[TaskDocNode]:
    """Surface 7 (slices 3c + 6g): active task-document summaries.

    Reads each ``ar-task-document/v1`` JSON under ``tasks/<repo>/<task>/`` -- the
    source of truth, never the rendered markdown. Full reader bodies are intentionally
    omitted from this always-on projection and served by :func:`read_task_document_body`.
    ``lifecycleId`` is optional
    runtime context: a ``light``/``subTask`` doc uses its own lifecycle id or the
    lifecycle of its matching leaf enclosure when present, but planning docs are
    still projected before an enclosure exists. Master docs are projected here as
    task documents and still projected on the series surface for compatibility.
    """
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return []
    lifecycle_maps = _task_document_lifecycle_maps(enclosures)
    docs = _iter_task_document_payloads(tasks_root, now=now)
    master_docs = _master_docs_by_ref(docs)
    nodes: list[TaskDocNode] = []
    for path, payload in docs:
        doc = _projected_document(payload)
        if doc is None:
            continue
        nodes.append(
            _task_doc_node(
                doc,
                path,
                lifecycle_maps,
                now,
                _TaskDocProjectionOptions(
                    include_body=False,
                    master_docs=master_docs,
                    coordination_root=coordination_root,
                ),
            )
        )
    return nodes


def read_task_document_body(  # pragma: no cover
    coordination_root: Path,
    *,
    doc_path: str,
    enclosures: list[EnclosureNode],
    now: datetime,
) -> TaskDocNode | None:
    """Read one full task-document body for the on-demand dashboard endpoint.

    The read touches the requested document and only the masters its projection consumes
    (:func:`_graph_master_docs`); it never enumerates the task corpus.
    """
    tasks_root = (coordination_root / "tasks").resolve()
    path = Path(doc_path)
    candidates = [path] if path.is_absolute() else [coordination_root / path, tasks_root / path]
    resolved: Path | None = None
    for candidate in candidates:
        try:
            candidate_resolved = candidate.resolve()
        except OSError:
            continue
        if candidate_resolved.is_file() and candidate_resolved.is_relative_to(tasks_root):
            resolved = candidate_resolved
            break
    if resolved is None:
        return None
    payload = _read_json(resolved)
    if payload is None or payload.get("schema") != TASK_DOCUMENT_SCHEMA:
        return None
    doc = _projected_document(payload)
    if doc is None:
        return None
    return _task_doc_node(
        doc,
        resolved,
        _task_document_lifecycle_maps(enclosures),
        now,
        _TaskDocProjectionOptions(
            include_body=True,
            master_docs=_graph_master_docs(tasks_root, doc),
            coordination_root=coordination_root,
        ),
    )


def _graph_master_docs(tasks_root: Path, doc: TaskDocument) -> dict[TaskDocumentRef, TaskDocument]:
    """The master join table one document's projection reads, found without the corpus.

    Only the sprint execution-graph view consumes master documents, and it looks them up
    solely by the refs its nodes carry. A document without a graph therefore needs none, and
    a sprint needs only the masters its nodes name: the same entries the corpus-wide table
    holds for those refs, read from their own paths.
    """
    if doc.executionGraph is None:
        return {}
    paths = _canonical_task_json_candidates(
        tasks_root, [ref.path for ref in doc.executionGraph.master_refs()]
    )
    return _master_docs_by_ref(
        [
            (path, payload)
            for path in paths
            if (payload := _read_json(path)) is not None
            and payload.get("schema") == TASK_DOCUMENT_SCHEMA
        ]
    )


def _task_document_lifecycle_maps(enclosures: list[EnclosureNode]) -> _TaskDocumentLifecycleMaps:
    lifecycle_by_enclosure = {
        enclosure.enclosure: enclosure.lifecycleId
        for enclosure in enclosures
        if enclosure.lifecycleId
    }
    lifecycle_by_dir = {
        Path(enclosure.taskRoot).resolve(): enclosure.lifecycleId
        for enclosure in enclosures
        if enclosure.lifecycleId and enclosure.taskRoot
    }
    lifecycle_by_root_doc = {
        Path(enclosure.taskRoot).resolve(): enclosure.lifecycleId
        for enclosure in enclosures
        if enclosure.lifecycleId
        and enclosure.taskRoot
        and enclosure.lifecycleId in {enclosure.taskId, enclosure.taskName}
    }
    # Enclosure leaf ids are lowercase directory names (enclosures/260628-l7/) while doc ids are
    # uppercase (260628-L7), and series leaf docs carry no enclosures[] refs in practice — so this
    # join is keyed case-insensitively on the doc's own id (the durable, human-stable key), with the
    # filename stem kept as a legacy alternative. Suffixed reopen enclosures (…-r1) deliberately do
    # not bind here; the sidebar admits those through its lifecycle-guarded suffix rule.
    lifecycle_by_leaf_doc = {
        (Path(enclosure.taskRoot).resolve(), enclosure.leafId.lower()): enclosure.lifecycleId
        for enclosure in enclosures
        if enclosure.lifecycleId and enclosure.taskRoot and enclosure.leafId
    }
    return _TaskDocumentLifecycleMaps(
        lifecycle_by_enclosure=lifecycle_by_enclosure,
        lifecycle_by_dir=lifecycle_by_dir,
        lifecycle_by_root_doc=lifecycle_by_root_doc,
        lifecycle_by_leaf_doc=lifecycle_by_leaf_doc,
    )


def _task_doc_lifecycle_id(
    doc: TaskDocument, path: Path, maps: _TaskDocumentLifecycleMaps
) -> str | None:
    if doc.kind == "master":
        return maps.lifecycle_by_root_doc.get(path.parent.resolve())
    return (
        doc.lifecycleId
        or _doc_enclosure_lifecycle(doc, maps.lifecycle_by_enclosure)
        or maps.lifecycle_by_leaf_doc.get((path.parent.resolve(), doc.id.lower()))
        or maps.lifecycle_by_leaf_doc.get((path.parent.resolve(), path.stem.lower()))
    )


def _doc_enclosure_lifecycle(  # pragma: no cover
    doc: TaskDocument, lifecycle_by_enclosure: dict[str, str]
) -> str | None:
    for enclosure in doc.enclosures:
        lifecycle_id = lifecycle_by_enclosure.get(enclosure.enclosurePath)
        if lifecycle_id:
            return lifecycle_id
    return None


def read_series_documents(
    coordination_root: Path, *, now: datetime
) -> list[SeriesNode]:  # pragma: no cover
    """Series surface (R1): per-master series progress, keyed by the task FOLDER.

    Reads each ``ar-task-document/v1`` JSON with ``kind == "master"`` under
    ``tasks/<repo>/<task>/``. Masters are also projected by :func:`read_task_documents`
    so direct task-document selection can render them; this companion surface keeps the
    folder-keyed series checklist where each subtask that will run is one checkbox and
    ``doneCount`` counts the *declared* ``Completed`` subtasks, authoritative over a slice's own
    internal steps. Abandoned rows are neither done nor part of ``totalCount``;
    ``abandonedCount`` names them, so a master whose remaining rows are all ``Completed`` reads
    complete.
    """
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return []
    nodes: list[SeriesNode] = []
    docs = list(_iter_task_document_payloads(tasks_root, now=now))
    leaf_ids_by_dir: dict[str, dict[str, str]] = {}
    for leaf_path, leaf_payload in docs:
        leaf_id = leaf_payload.get("id")
        if leaf_payload.get("kind") == "subTask" and isinstance(leaf_id, str):
            leaf_ids_by_dir.setdefault(leaf_path.parent.as_posix(), {})[leaf_id] = (
                leaf_path.as_posix()
            )
    for path, payload in docs:
        if payload.get("kind") != "master":
            continue
        doc = _projected_document(payload)
        if doc is None:
            continue
        nodes.append(
            SeriesNode(
                seriesId=path.parent.name,
                repository=doc.repo,
                title=doc.title,
                status=doc.status,
                createdAt=doc.createdAt,
                objective="",
                subTasks=_series_subtask_nodes(
                    path,
                    doc,
                    direct_grants=_subtask_grant_nodes(
                        coordination_root, tasks_root, path, doc, leaf_ids_by_dir
                    ),
                ),
                discardedCount=len(doc.discardedSubTasks),
                discardedSubTasks=_discarded_subtask_nodes(doc),
                doneCount=series_done(doc),
                totalCount=series_total(doc),
                abandonedCount=series_abandoned(doc),
                sections=[],
                decisions=[],
                docPath=path.as_posix(),
                ageSeconds=_file_age_seconds(path, now),
            )
        )
    return nodes


def _grant_giver_label(grant: DirectExecutionGrant) -> str:
    """Who gave the grant, as the dashboard shows it (MIK-R94 rule 4)."""
    if grant.granted_by == DEVELOPER_GRANTER:
        return DEVELOPER_GRANTER
    if grant.granter_agent_id:
        return f"architect {grant.granter_agent_id}"
    return ARCHITECT_GRANTER


def _subtask_grant_nodes(
    coordination_root: Path,
    tasks_root: Path,
    path: Path,
    doc: TaskDocument,
    leaf_ids_by_dir: Mapping[str, Mapping[str, str]],
) -> dict[str, DirectGrantNode]:
    """One master's subtask grants, keyed by subtask number (MIK-R94 rule 4).

    Matched by leaf id against the payloads already read -- never by filename
    guess. Unreadable grant files read as no grant (gates still refuse them).
    """
    try:
        grants = grants_for_task(coordination_root, doc.repo, path.parent.name)
    except OSError:
        return {}
    repo_root = tasks_root / doc.repo
    nodes: dict[str, DirectGrantNode] = {}
    for leaf_id, leaf_posix in leaf_ids_by_dir.get(path.parent.as_posix(), {}).items():
        try:
            leaf_rel = Path(leaf_posix).relative_to(repo_root).as_posix()
        except ValueError:
            continue
        grant = grants.get(leaf_rel)
        if grant is not None:
            nodes[leaf_id] = DirectGrantNode(
                grantedBy=_grant_giver_label(grant), grantedAt=grant.granted_at
            )
    return nodes


def _series_subtask_nodes(
    path: Path,
    doc: TaskDocument,
    *,
    direct_grants: Mapping[str, DirectGrantNode] | None = None,
) -> list[SeriesSubTaskNode]:
    indexed = [
        (index, sub, _series_subtask_created_at(path.parent, sub.file))
        for index, sub in enumerate(doc.subTasks)
    ]
    if all(created_at for _, _, created_at in indexed):
        indexed.sort(key=lambda item: (item[2] or "", item[0]))
    return [
        SeriesSubTaskNode(
            number=sub.number,
            name=sub.name,
            file=sub.file,
            status=sub.status,
            scope=sub.scope,
            createdAt=created_at,
            directGrant=(direct_grants or {}).get(sub.number),
        )
        for _, sub, created_at in indexed
    ]


def _discarded_subtask_nodes(doc: TaskDocument) -> list[DiscardedSubTaskNode]:
    return [
        DiscardedSubTaskNode(
            number=item.number,
            name=item.name,
            file=item.file,
            scope=item.scope,
            disposition=item.disposition,
            reason=item.reason,
            discardedAt=item.discardedAt,
            proof=DiscardUnstartedProofNode.model_validate(item.proof.model_dump(mode="json")),
        )
        for item in doc.discardedSubTasks
    ]


def _series_subtask_created_at(base_dir: Path, ref_file: str) -> str | None:  # pragma: no cover
    if not ref_file:
        return None
    ref_path = (base_dir / ref_file).with_suffix(".json")
    payload = _read_json(ref_path)
    if payload is None or payload.get("schema") != TASK_DOCUMENT_SCHEMA:
        return None
    if payload.get("kind") == "master":
        return None
    doc = _projected_document(payload)
    if doc is None:
        return None
    return doc.createdAt


def _ref_lifecycle(
    base_dir: Path, ref_file: str | None, lifecycle_by_dir: dict[Path, str]
) -> str | None:
    """A cross-folder ref (``../<task>/task.md``) resolves to that folder's contract-paired lifecycle;
    a bare slug (a same-folder slice) or empty ref does not (slice 6g cross-master link)."""
    if not ref_file or "/" not in ref_file:
        return None
    return lifecycle_by_dir.get((base_dir / ref_file).resolve().parent)


def _task_step_nodes(doc: TaskDocument, *, preview: bool = False) -> list[TaskStepNode]:
    """Project one document's steps, bounding long prose for summaries (rule 9).

    The on-demand body (``preview=False``) carries whole strings with clear
    flags; the always-on summary carries bounded previews so one task document
    cannot grow every snapshot by its longest prose. Full details stay reachable
    through the existing body read; nothing reachable is capped away.
    """
    return [
        TaskStepNode(
            id=step.id,
            title=step.title,
            status=step.status,
            disposition=(
                TaskStepDispositionNode.model_validate(step.disposition.model_dump())
                if step.disposition is not None
                else None
            ),
            substeps=[
                TaskSubStepNode(
                    id=sub.id,
                    title=sub.title,
                    status=sub.status,
                    disposition=(
                        TaskStepDispositionNode.model_validate(sub.disposition.model_dump())
                        if sub.disposition is not None
                        else None
                    ),
                    note=_summary_preview(sub.note) if preview else sub.note,
                )
                for sub in step.substeps
            ],
            outcome=_summary_preview(step.outcome) if preview else step.outcome,
            note=_summary_preview(step.note) if preview else step.note,
            outcomeTruncated=preview and _is_truncated(step.outcome),
            noteTruncated=preview and _is_truncated(step.note),
        )
        for step in doc.steps
    ]


# Display-aligned summary boundaries (MIK-R127 rule 9): the always-on summary
# carries what the page blocks draw — six head notes with the whole count, the
# first review findings with whole descriptions previewed — never the complete
# prose. Full details stay reachable through the existing on-demand body read.
_SUMMARY_TEXT_PREVIEW_CHARS = 400
_SUMMARY_HEADER_NOTES = 6
_SUMMARY_REVIEW_FINDINGS = 25


def _is_truncated(value: str | None) -> bool:
    return value is not None and len(value) > _SUMMARY_TEXT_PREVIEW_CHARS


@overload
def _summary_preview(value: None) -> None: ...


@overload
def _summary_preview(value: str) -> str: ...


def _summary_preview(value: str | None) -> str | None:
    if value is None or len(value) <= _SUMMARY_TEXT_PREVIEW_CHARS:
        return value
    return value[:_SUMMARY_TEXT_PREVIEW_CHARS]


def _task_doc_form(doc: TaskDocument) -> Literal["sprint", "master", "task"]:
    """The page form of one document by the model's own rule (MIK-R127 rule 1)."""
    if doc.is_sprint:
        return "sprint"
    if doc.kind == "master":
        return "master"
    return "task"


def _task_review_state_node(
    doc: TaskDocument, *, preview: bool = False
) -> TaskReviewStateNode | None:
    """The recorded review round, bounded as the Review block shows it (rule 9).

    Summaries carry the first ``_SUMMARY_REVIEW_FINDINGS`` findings with whole
    descriptions previewed; ``totalFindings`` always names the whole round for
    the counted link, ``openFindings`` always counts the open findings of the
    whole round, and the on-demand body carries every finding whole.
    """
    state = doc.reviewState
    if state is None:
        return None
    remaining = set(state.remainingFindingIds)
    findings = [
        TaskReviewFindingNode(
            findingId=item.findingId,
            description=_summary_preview(item.description) if preview else item.description,
            open=item.findingId in remaining,
            descriptionTruncated=preview and _is_truncated(item.description),
        )
        for item in state.baselineFindings
    ]
    # Counted before the cut: an open finding beyond the summary's first ones
    # is still open.
    open_findings = sum(1 for item in findings if item.open)
    if preview:
        findings = findings[:_SUMMARY_REVIEW_FINDINGS]
    return TaskReviewStateNode(
        round=state.round,
        pending=state.pending,
        additionalRounds=state.additionalRounds,
        findings=findings,
        totalFindings=len(state.baselineFindings),
        openFindings=open_findings,
        developerApproval=state.developerApproval,
    )


def _execution_graph_view(
    graph: SprintExecutionGraph,
    master_docs: Mapping[TaskDocumentRef, TaskDocument],
) -> TaskExecutionGraphView:
    """Walk one persisted graph and build its render-ready per-node view.

    The observer's graph-view builder is primitives-only (layering: the
    observer package must not import tasks), so this serving-layer seam does
    the tasks-domain work -- derived waves, resolved edge endpoints, joined
    titles, and per-master facts -- and feeds the builder plain data.
    """

    titles = build_graph_titles(graph, master_docs)
    facts = {
        ref: MasterGraphFacts(
            status=doc.status,
            executionNature=doc.executionNature,
            leaf_statuses={row.number: row.status for row in doc.subTasks},
        )
        for ref, doc in master_docs.items()
    }
    predecessor_edges: dict[GraphNodeLike, list[GraphPredecessorFacts]] = {
        node: [] for node in graph.nodes
    }
    for edge in graph.edges:
        predecessor = graph.resolve_endpoint(edge.predecessor)
        successor = graph.resolve_endpoint(edge.successor)
        predecessor_edges[successor].append(
            GraphPredecessorFacts(
                predecessor=predecessor,
                reason=edge.reason,
                judgmentId=edge.judgmentId,
            )
        )
    return build_execution_graph_view(
        graph.nodes,
        graph.derived_waves(),
        predecessor_edges,
        facts,
        titles,
    )


def _master_docs_by_ref(
    docs: list[tuple[Path, dict[str, object]]],
) -> dict[TaskDocumentRef, TaskDocument]:
    """Index every valid master document by its task-document reference.

    The render-ready sprint graph view (L12-R4) joins master titles, leaf
    titles, natures, and statuses from the commanded master documents; this map
    is that join table. No bound evicts any document any more, so every master
    under ``tasks/<repo>/<task>/`` is present and a sprint's commanded masters
    are always in the table (see the removal rationale in ``_common.py``).
    """

    masters: dict[TaskDocumentRef, TaskDocument] = {}
    for path, payload in docs:
        if payload.get("kind") != "master":
            continue
        doc = _projected_document(payload)
        if doc is None:
            continue
        repository = doc.repo
        # Task payloads are always depth-3 under the tasks root (``_iter_task_json``), so the
        # repo-relative path is ``parents[1]``-relative by construction; no fallback needed.
        relative = path.relative_to(path.parents[1]).as_posix()
        ref = TaskDocumentRef(repository=repository, path=relative)
        masters[ref] = doc
    return masters


@dataclass(frozen=True)
class _TaskDocProjectionOptions:
    """Keyword-only extras for one task-document projection.

    ``include_body`` controls the reader-body fields; ``master_docs`` is the
    title/status/nature join table the render-ready sprint graph view (L12-R4)
    needs. Bundled so the projection call stays within the argument budget.
    """

    include_body: bool = False
    master_docs: Mapping[TaskDocumentRef, TaskDocument] | None = None
    coordination_root: Path | None = None


@dataclass(frozen=True)
class _TaskDocReaderFields:
    """The reader-body fields of one task-document projection.

    Every field is gated by ``include_body``; bundling them keeps the
    projection call within the complexity budget.
    """

    objective: str
    requirements: list[str]
    design: str | None
    codeExamples: list[TaskCodeExampleNode]
    decisions: list[TaskDecisionNode]
    openQuestions: list[str]
    references: list[str]
    sections: list[TaskSectionNode]


def _reader_fields(doc: TaskDocument, *, include_body: bool) -> _TaskDocReaderFields:
    """The include_body-gated reader content of one task-document projection."""
    if not include_body:
        return _TaskDocReaderFields(
            objective="",
            requirements=[],
            design=None,
            codeExamples=[],
            decisions=[],
            openQuestions=[],
            references=[],
            sections=[],
        )
    return _TaskDocReaderFields(
        objective=doc.objective,
        requirements=[_requirement_reader_text(item) for item in doc.requirements],
        design=doc.design,
        codeExamples=[
            TaskCodeExampleNode(
                id=example.id,
                title=example.title,
                distinctChange=example.distinctChange,
                why=example.why,
                language=example.language,
                snippet=example.snippet,
            )
            for example in doc.codeExamples
        ],
        decisions=[
            TaskDecisionNode(
                at=item.at,
                decision=item.decision,
                rationale=item.rationale,
                product_record=is_product_record_decision(item.decision),
            )
            for item in doc.decisions
        ],
        openQuestions=[_question_reader_text(item) for item in doc.openQuestions],
        references=list(doc.references),
        sections=[
            TaskSectionNode(kind=section.kind, heading=section.heading, body=section.body)
            for section in doc.sections
        ],
    )


def _requirement_reader_text(value: str | ApprovedRequirementPacketRef) -> str:
    if isinstance(value, str):
        return value
    return f"{value.stableId}@{value.version} — {value.path}"


def _question_reader_text(value: str | AcceptanceObligationQuestion) -> str:
    if isinstance(value, str):
        return value
    return f"Acceptance obligation {value.id}: {value.question}"


def _execution_graph_fields(
    doc: TaskDocument,
    master_docs: Mapping[TaskDocumentRef, TaskDocument] | None,
) -> tuple[
    TaskExecutionGraphNode | None, list[list[TaskExecutionNode]], TaskExecutionGraphView | None
]:
    """The sprint-only execution-graph fields of one projection (L12-R4).

    A non-sprint doc projects ``(None, [], None)``.
    """
    if doc.executionGraph is None:
        return None, [], None
    return (
        TaskExecutionGraphNode.model_validate(doc.executionGraph.model_dump(mode="json")),
        [
            [TaskExecutionNode.model_validate(node.model_dump(mode="json")) for node in wave]
            for wave in doc.executionGraph.derived_waves()
        ],
        _execution_graph_view(doc.executionGraph, master_docs or {}),
    )


def _task_doc_node(
    doc: TaskDocument,
    path: Path,
    maps: _TaskDocumentLifecycleMaps,
    now: datetime,
    options: _TaskDocProjectionOptions,
) -> TaskDocNode:
    """Project one task document, carrying the resolved lifecycle id and (for a master) its index.

    Cross-master links resolve here: a subTask whose ``file`` points at another master, and the doc's
    own ``master`` parent ref, each resolve to the linked lifecycle (None when same-series/in-folder).
    The lifecycle maps arrive whole: the doc's own id and the cross-folder link resolution are two
    reads of the same index, and passing the id separately let them disagree.
    """
    lifecycle_id = _task_doc_lifecycle_id(doc, path, maps)
    lifecycle_by_dir = maps.lifecycle_by_dir
    base_dir = path.parent
    parent_lifecycle = _ref_lifecycle(base_dir, doc.master, lifecycle_by_dir)
    body_revision = _task_doc_body_revision(doc)
    reader = _reader_fields(doc, include_body=options.include_body)
    execution_graph, execution_waves, execution_graph_view = _execution_graph_fields(
        doc, options.master_docs
    )
    direct_grant = _task_doc_grant_node(options.coordination_root, path, doc)
    # The always-on summary carries bounded previews (rule 9); the on-demand
    # body carries every note and whole string. Totals always name the whole
    # collection for the page's counted links.
    preview = not options.include_body
    header_notes = [
        TaskHeaderNoteNode(label=note.label, value=note.value) for note in doc.headerNotes
    ]
    return TaskDocNode(
        id=doc.id,
        lifecycleId=lifecycle_id,
        repository=doc.repo,
        title=doc.title,
        status=doc.status,
        kind=doc.kind,
        form=_task_doc_form(doc),
        type=doc.type,
        integrationBranch=doc.integrationBranch,
        statusNote=(_summary_preview(doc.statusNote) if preview else doc.statusNote),
        statusNoteTruncated=preview and _is_truncated(doc.statusNote),
        headerNotes=(header_notes[:_SUMMARY_HEADER_NOTES] if preview else header_notes),
        headerNotesTotal=len(header_notes),
        reviewState=_task_review_state_node(doc, preview=preview),
        stepsDone=step_done(doc),
        stepsTotal=step_total(doc),
        currentStep=current_step(doc),
        docPath=path.as_posix(),
        bodyRevision=body_revision,
        createdAt=doc.createdAt,
        ageSeconds=_file_age_seconds(path, now),
        steps=_task_step_nodes(doc, preview=preview),
        objective=reader.objective,
        requirements=reader.requirements,
        design=reader.design,
        codeExamples=reader.codeExamples,
        decisions=reader.decisions,
        openQuestions=reader.openQuestions,
        references=reader.references,
        sections=reader.sections,
        subTasks=[
            TaskSubTaskRefNode(
                number=ref.number,
                name=ref.name,
                file=ref.file,
                status=ref.status,
                scope=ref.scope,
                linkedLifecycleId=_ref_lifecycle(base_dir, ref.file, lifecycle_by_dir),
                masterRef=ref.masterRef,
            )
            for ref in doc.subTasks
        ],
        discardedCount=len(doc.discardedSubTasks) if doc.kind == "master" else None,
        discardedSubTasks=_discarded_subtask_nodes(doc) if doc.kind == "master" else None,
        masterLifecycleId=parent_lifecycle,
        orchestrates=list(doc.orchestrates),
        seats=[
            TaskSeatNode(
                role=seat.role,
                label=seat.label,
                identity=seat.identity,
                state=seat.state,
            )
            for seat in doc.seats
        ],
        executionNature=doc.executionNature,
        executionGraph=execution_graph,
        executionWaves=execution_waves,
        executionGraphView=execution_graph_view,
        directGrant=direct_grant,
    )


def _task_doc_grant_node(
    coordination_root: Path | None, path: Path, doc: TaskDocument
) -> DirectGrantNode | None:
    """One leaf's grant node for its document projection (MIK-R94 rule 4).

    ``None`` for non-leaves, without a coordination root, and when no readable
    grant exists: a projection never breaks on a missing or broken grant file.
    """
    if coordination_root is None or doc.kind != "subTask":
        return None
    try:
        grants = grants_for_task(coordination_root, doc.repo, path.parent.name)
    except (OSError, ValueError):
        return None
    grant = grants.get(f"{path.parent.name}/{path.name}")
    if grant is None:
        return None
    return DirectGrantNode(
        grantedBy=_grant_giver_label(grant), grantedAt=grant.granted_at
    )


def _task_doc_body_revision(doc: TaskDocument) -> str:
    # Every field an open reader displays joins the revision: a change to any
    # of them refetches an already-open document instead of letting a cached
    # body override the newer summary. The task model is only read, never edited.
    payload = {
        "objective": doc.objective,
        "requirements": [_task_intent_body_value(value) for value in doc.requirements],
        "design": doc.design,
        "codeExamples": [example.model_dump(mode="json") for example in doc.codeExamples],
        "decisions": [item.model_dump(mode="json") for item in doc.decisions],
        "openQuestions": [_task_intent_body_value(value) for value in doc.openQuestions],
        "references": list(doc.references),
        "sections": [section.model_dump(mode="json") for section in doc.sections],
        "statusNote": doc.statusNote,
        "headerNotes": [note.model_dump(mode="json") for note in doc.headerNotes],
        "type": doc.type,
        "steps": [step.model_dump(mode="json") for step in doc.steps],
        "reviewState": doc.reviewState.model_dump(mode="json") if doc.reviewState else None,
        # Sprint structure (L14): the sub-task index rows (typed masterRef links included) and the
        # first-class seats ride the always-on summary, but an OPEN reader renders the fetched body
        # — so both join the revision and a linkage/seat edit refetches an already-open sprint doc.
        "subTasks": [ref.model_dump(mode="json") for ref in doc.subTasks],
        "discardedSubTasks": [item.model_dump(mode="json") for item in doc.discardedSubTasks],
        "seats": [seat.model_dump(mode="json") for seat in doc.seats],
        "executionNature": doc.executionNature,
        "executionGraph": (
            doc.executionGraph.model_dump(mode="json") if doc.executionGraph is not None else None
        ),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _task_intent_body_value(
    value: str | ApprovedRequirementPacketRef | AcceptanceObligationQuestion,
) -> str | dict[str, object]:
    if isinstance(value, str):
        return value
    return value.model_dump(mode="json", by_alias=True)
