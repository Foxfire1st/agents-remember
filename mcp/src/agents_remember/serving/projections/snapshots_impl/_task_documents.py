"""Canonical task summaries, full bodies and their content revisions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, overload

from agents_remember.kernel.direct_execution_grants import (
    ARCHITECT_GRANTER,
    DEVELOPER_GRANTER,
    DirectExecutionGrant,
    grant_store_dir,
    grants_for_task,
    iter_grant_files,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_intent import (
    AcceptanceObligationQuestion,
    ApprovedRequirementPacketRef,
)
from agents_remember.observer.projection import (
    DirectGrantNode,
    DiscardedSubTaskNode,
    TaskCodeExampleNode,
    TaskDecisionNode,
    TaskDocNode,
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
    TaskExecutionGraphView,
)
from agents_remember.observer.task_entries import (
    DeclaredTaskSource,
    TaskDocumentSource,
    relative_task_reference,
)
from agents_remember.serving.projections.snapshots_impl._common import (
    _canonical_task_json_candidates,
    _file_age_seconds,
    _iter_task_document_payloads,
    _read_json,
)
from agents_remember.serving.projections.task_body_generation import (
    body_input,
    capturing_body_generation,
)
from agents_remember.tasks import (
    TASK_DOCUMENT_SCHEMA,
    TaskDocument,
    current_step,
    is_product_record_decision,
    step_done,
    step_total,
)

from ._task_document_parse import projected_document as _projected_document
from ._task_graph_view import declared_task_reference, documents_by_ref, execution_graph_view
from ._task_order import OrderFields, build_order_fields, read_order_fields


def read_task_documents(coordination_root: Path, *, now: datetime) -> list[TaskDocNode]:
    """Standalone document summaries; the served projection consumes the sources once."""
    return [source.summary for source in read_task_sources(coordination_root, now=now)]


def read_task_sources(coordination_root: Path, *, now: datetime) -> list[TaskDocumentSource]:
    """Read every canonical task summary; full bodies use the on-demand reader."""
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return []
    return project_task_sources(
        _iter_task_document_payloads(tasks_root, now=now),
        coordination_root=coordination_root,
        now=now,
    )


def project_task_sources(
    payloads: list[tuple[Path, dict[str, object]]], *, coordination_root: Path, now: datetime
) -> list[TaskDocumentSource]:
    """Project full and path-directed reads through the same canonical source builder."""
    docs = _validated_task_documents(payloads)
    master_docs = documents_by_ref(docs)
    order_fields = build_order_fields(dict(docs))
    nodes: list[TaskDocumentSource] = []
    for path, doc in docs:
        reference = TaskDocumentRef(
            repository=path.parents[1].name, path=f"{path.parent.name}/{path.name}"
        )
        children = []
        for row in doc.subTasks:
            child_ref = declared_task_reference(reference, row)
            children.append(
                DeclaredTaskSource(child_ref, row.number, row.name, row.status, row.scope)
            )
        nodes.append(
            TaskDocumentSource(
                path,
                _task_doc_node(
                    doc,
                    path,
                    now,
                    _TaskDocProjectionOptions(
                        include_body=False,
                        master_docs=master_docs,
                        order_fields=order_fields,
                        coordination_root=coordination_root,
                    ),
                ),
                children=tuple(children),
                declared_parent=relative_task_reference(reference, doc.master),
            )
        )
    return nodes


def read_task_document_body(  # pragma: no cover
    coordination_root: Path,
    *,
    doc_path: str,
    now: datetime,
) -> TaskDocNode | None:
    """Read one full task-document body for the on-demand dashboard endpoint.

    The read touches the requested document and only the masters its projection consumes
    (:func:`_graph_master_docs`); it never enumerates the task corpus.
    """
    with body_input(coordination_root / "tasks"):
        tasks_root = (coordination_root / "tasks").resolve()
    path = Path(doc_path)
    candidates = [path] if path.is_absolute() else [coordination_root / path, tasks_root / path]
    resolved: Path | None = None
    for candidate in candidates:
        try:
            with body_input(candidate):
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
        now,
        _TaskDocProjectionOptions(
            include_body=True,
            master_docs=_graph_master_docs(tasks_root, doc),
            order_fields=read_order_fields(resolved, doc),
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
    master_documents = documents_by_ref(
        _validated_task_documents(
            [
                (path, payload)
                for path in paths
                if (payload := _read_json(path)) is not None
                and payload.get("schema") == TASK_DOCUMENT_SCHEMA
            ]
        )
    )
    children = [
        ref.path
        for parent, master in master_documents.items()
        for row in master.subTasks
        if (ref := declared_task_reference(parent, row)) is not None
    ]
    for path in _canonical_task_json_candidates(tasks_root, children) if children else ():
        payload = _read_json(path)
        if payload is not None and payload.get("schema") == TASK_DOCUMENT_SCHEMA:
            master_documents.update(documents_by_ref(_validated_task_documents([(path, payload)])))
    return master_documents


def _grant_giver_label(grant: DirectExecutionGrant) -> str:
    """Who gave the grant, as the dashboard shows it (MIK-R94 rule 4)."""
    if grant.granted_by == DEVELOPER_GRANTER:
        return DEVELOPER_GRANTER
    if grant.granter_agent_id:
        return f"architect {grant.granter_agent_id}"
    return ARCHITECT_GRANTER


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


def _validated_task_documents(
    payloads: list[tuple[Path, dict[str, object]]],
) -> list[tuple[Path, TaskDocument]]:
    return [
        (path, document)
        for path, payload in payloads
        if (document := _projected_document(payload)) is not None
    ]


@dataclass(frozen=True)
class _TaskDocProjectionOptions:
    """Keyword-only extras for one task-document projection.

    ``include_body`` controls the reader-body fields; ``master_docs`` is the
    title/status/nature join table the render-ready sprint graph view (L12-R4)
    needs. Bundled so the projection call stays within the argument budget.
    """

    include_body: bool = False
    master_docs: Mapping[TaskDocumentRef, TaskDocument] | None = None
    order_fields: Mapping[Path, OrderFields] | None = None
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
        execution_graph_view(doc.executionGraph, master_docs or {}),
    )


def _task_doc_node(
    doc: TaskDocument,
    path: Path,
    now: datetime,
    options: _TaskDocProjectionOptions,
) -> TaskDocNode:
    """Project document facts; runtime activity belongs to its qualified task entry."""
    order = (options.order_fields or {}).get(path)
    body_revision = _task_doc_body_revision(doc)
    if order is not None:
        derived = json.dumps(
            {
                "graph": order.graph.model_dump(mode="json") if order.graph else None,
                "waits": [item.model_dump(mode="json") for item in order.waits_for],
                "needed": [item.model_dump(mode="json") for item in order.needed_by],
            },
            sort_keys=True,
        )
        body_revision = hashlib.sha256((body_revision + derived).encode()).hexdigest()
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
        completedAt=_completion_time(doc),
        waitsFor=doc.waitsFor,
        waitsForTasks=list(order.waits_for) if order else None,
        neededByTasks=list(order.needed_by) if order else None,
        nonGoals=doc.nonGoals if options.include_body else None,
        acceptance=doc.acceptance if options.include_body else None,
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
                masterRef=ref.masterRef,
            )
            for ref in doc.subTasks
        ],
        discardedCount=len(doc.discardedSubTasks) if doc.kind == "master" else None,
        discardedSubTasks=_discarded_subtask_nodes(doc) if doc.kind == "master" else None,
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
        executionGraphView=order.graph if order and order.graph else execution_graph_view,
        directGrant=direct_grant,
    )


def _completion_time(doc: TaskDocument) -> str | None:
    if doc.completedAt:
        return doc.completedAt
    stamps: list[tuple[datetime, str]] = []
    for item in doc.decisions:
        if item.decision != "Finalize task lifecycle.":
            continue
        try:
            stamp = datetime.fromisoformat(item.at)
        except ValueError:
            continue
        stamps.append((stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp, item.at))
    return max(stamps, key=lambda pair: pair[0])[1] if stamps else None


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
        with ExitStack() as inputs:
            if capturing_body_generation():
                store = grant_store_dir(coordination_root)
                inputs.enter_context(body_input(store))
                for grant_path in iter_grant_files(
                    store, repository=doc.repo, master=path.parent.name
                ):
                    inputs.enter_context(body_input(grant_path))
            grants = grants_for_task(coordination_root, doc.repo, path.parent.name)
    except (OSError, ValueError):
        return None
    grant = grants.get(f"{path.parent.name}/{path.name}")
    if grant is None:
        return None
    return DirectGrantNode(grantedBy=_grant_giver_label(grant), grantedAt=grant.granted_at)


def _task_doc_body_revision(doc: TaskDocument) -> str:
    # Every field an open reader displays joins the revision: a change to any
    # of them refetches an already-open document instead of letting a cached
    # body override the newer summary. The task model is only read, never edited.
    payload = {
        "objective": doc.objective,
        "requirements": [_task_intent_body_value(value) for value in doc.requirements],
        "design": doc.design,
        "nonGoals": doc.nonGoals,
        "acceptance": doc.acceptance,
        "waitsFor": doc.waitsFor,
        "completedAt": doc.completedAt,
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
