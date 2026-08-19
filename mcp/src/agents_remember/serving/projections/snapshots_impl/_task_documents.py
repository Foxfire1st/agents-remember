"""Task-document and series readers: summaries, full bodies, lifecycle binding.

Task JSON is the source of truth (never the rendered markdown). These readers
project task documents and the series checklist, resolve cross-folder lifecycle
links, and hash full bodies for the on-demand body endpoint.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from agents_remember.observer.projection import (
    EnclosureNode,
    SeriesNode,
    SeriesSubTaskNode,
    TaskCodeExampleNode,
    TaskDecisionNode,
    TaskDocNode,
    TaskExecutionGraphNode,
    TaskExecutionNode,
    TaskSectionNode,
    TaskStepDispositionNode,
    TaskStepNode,
    TaskSubStepNode,
    TaskSubTaskRefNode,
)
from agents_remember.serving.projections.snapshots_impl._common import (
    SERIES_DOCUMENT_SUMMARY_LIMIT,
    TASK_DOCUMENT_SUMMARY_LIMIT,
    _bounded_task_document_payloads,
    _file_age_seconds,
    _iter_task_document_payloads,
    _read_json,
    _TaskDocumentLifecycleMaps,
)
from agents_remember.tasks import (
    TASK_DOCUMENT_SCHEMA,
    TaskDocument,
    current_step,
    series_done,
    series_total,
    step_done,
    step_total,
)


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
    nodes: list[TaskDocNode] = []
    for path, payload in _bounded_task_document_payloads(
        _iter_task_document_payloads(tasks_root, now=now),
        limit=TASK_DOCUMENT_SUMMARY_LIMIT,
    ):
        try:
            doc = TaskDocument.model_validate(payload)
        except ValueError:
            continue
        nodes.append(_task_doc_node(doc, path, lifecycle_maps, now, include_body=False))
    return nodes


def read_task_document_body(  # pragma: no cover
    coordination_root: Path,
    *,
    doc_path: str,
    enclosures: list[EnclosureNode],
    now: datetime,
) -> TaskDocNode | None:
    """Read one full task-document body for the on-demand dashboard endpoint."""
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
    try:
        doc = TaskDocument.model_validate(payload)
    except ValueError:
        return None
    lifecycle_maps = _task_document_lifecycle_maps(enclosures)
    return _task_doc_node(doc, resolved, lifecycle_maps, now, include_body=True)


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
    folder-keyed series checklist where each subtask is one checkbox and ``doneCount``
    counts the *declared* ``Completed`` subtasks, authoritative over a slice's own internal
    steps.
    """
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return []
    nodes: list[SeriesNode] = []
    for path, payload in _bounded_task_document_payloads(
        _iter_task_document_payloads(tasks_root, now=now),
        limit=SERIES_DOCUMENT_SUMMARY_LIMIT,
    ):
        if payload.get("kind") != "master":
            continue
        try:
            doc = TaskDocument.model_validate(payload)
        except ValueError:
            continue
        nodes.append(
            SeriesNode(
                seriesId=path.parent.name,
                repository=doc.repo,
                title=doc.title,
                status=doc.status,
                createdAt=doc.createdAt,
                objective="",
                subTasks=_series_subtask_nodes(path, doc),
                doneCount=series_done(doc),
                totalCount=series_total(doc),
                sections=[],
                decisions=[],
                docPath=path.as_posix(),
                ageSeconds=_file_age_seconds(path, now),
            )
        )
    return nodes


def _series_subtask_nodes(path: Path, doc: TaskDocument) -> list[SeriesSubTaskNode]:
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
        )
        for _, sub, created_at in indexed
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
    try:
        doc = TaskDocument.model_validate(payload)
    except ValueError:
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


def _task_step_nodes(doc: TaskDocument) -> list[TaskStepNode]:
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
                )
                for sub in step.substeps
            ],
        )
        for step in doc.steps
    ]


def _task_doc_node(
    doc: TaskDocument,
    path: Path,
    maps: _TaskDocumentLifecycleMaps,
    now: datetime,
    *,
    include_body: bool,
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
    return TaskDocNode(
        id=doc.id,
        lifecycleId=lifecycle_id,
        repository=doc.repo,
        title=doc.title,
        status=doc.status,
        kind=doc.kind,
        stepsDone=step_done(doc),
        stepsTotal=step_total(doc),
        currentStep=current_step(doc),
        docPath=path.as_posix(),
        bodyRevision=body_revision,
        createdAt=doc.createdAt,
        ageSeconds=_file_age_seconds(path, now),
        steps=_task_step_nodes(doc),
        objective=doc.objective if include_body else "",
        requirements=list(doc.requirements) if include_body else [],
        design=doc.design if include_body else None,
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
        ]
        if include_body
        else [],
        decisions=[
            TaskDecisionNode(at=item.at, decision=item.decision, rationale=item.rationale)
            for item in doc.decisions
        ]
        if include_body
        else [],
        openQuestions=list(doc.openQuestions) if include_body else [],
        references=list(doc.references) if include_body else [],
        subTasks=[
            TaskSubTaskRefNode(
                number=ref.number,
                name=ref.name,
                file=ref.file,
                status=ref.status,
                scope=ref.scope,
                linkedLifecycleId=_ref_lifecycle(base_dir, ref.file, lifecycle_by_dir),
            )
            for ref in doc.subTasks
        ],
        sections=[
            TaskSectionNode(kind=section.kind, heading=section.heading, body=section.body)
            for section in doc.sections
        ]
        if include_body
        else [],
        masterLifecycleId=parent_lifecycle,
        orchestrates=list(doc.orchestrates),
        executionNature=doc.executionNature,
        executionGraph=(
            TaskExecutionGraphNode.model_validate(doc.executionGraph.model_dump(mode="json"))
            if doc.executionGraph is not None
            else None
        ),
        executionWaves=(
            [
                [TaskExecutionNode.model_validate(node.model_dump(mode="json")) for node in wave]
                for wave in doc.executionGraph.derived_waves()
            ]
            if doc.executionGraph is not None
            else []
        ),
    )


def _task_doc_body_revision(doc: TaskDocument) -> str:
    payload = {
        "objective": doc.objective,
        "requirements": list(doc.requirements),
        "design": doc.design,
        "codeExamples": [example.model_dump(mode="json") for example in doc.codeExamples],
        "decisions": [item.model_dump(mode="json") for item in doc.decisions],
        "openQuestions": list(doc.openQuestions),
        "references": list(doc.references),
        "sections": [section.model_dump(mode="json") for section in doc.sections],
        "executionNature": doc.executionNature,
        "executionGraph": (
            doc.executionGraph.model_dump(mode="json") if doc.executionGraph is not None else None
        ),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
