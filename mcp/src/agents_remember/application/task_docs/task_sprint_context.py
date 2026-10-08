"""Shared source capture, validation and publication for sprint linkage operations."""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    TaskDocSourceSnapshot,
    TaskDocument,
    json_path_for,
    markdown_path_for,
    read_graph_titles,
    read_task_doc_with_source,
    render_markdown,
)
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.tasks.serving_preflight import (
    TopologyServingBuildError,
    require_serving_retirement_schema,
    require_serving_topology_schema,
)

from .task_doc_publication import TaskDocPublicationTransaction, task_doc_scope_changes
from .task_sprint_candidates import SprintLinkageError


def _require_serving_topology_schema() -> None:
    """Wrap the served-build preflight in the linkage error family (L15-R4)."""

    try:
        require_serving_topology_schema()
    except TopologyServingBuildError as exc:
        raise SprintLinkageError(str(exc)) from exc


def _require_serving_retirement_schema() -> None:
    try:
        require_serving_retirement_schema()
    except TopologyServingBuildError as exc:
        raise SprintLinkageError(str(exc)) from exc


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


def _parse_payload[PayloadT: _Payload](
    model: type[PayloadT], fields: dict[str, Any], operation: str
) -> PayloadT:
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
    # A graph-less sprint whose last master was retired commands nothing any more. It stays a
    # sprint: it holds the retirement rows, and a master can be attached to it again.
    if not sprint.is_sprint:
        raise SprintLinkageError(f"task_doc.{operation} requires an orchestration sprint document")
    topology = TaskDocumentTopology(request.coordination_root)
    try:
        sprint_ref = topology.canonical_ref(request.repo_id, json_path)
    except TaskDocumentRefError as exc:
        raise SprintLinkageError(f"{exc.status}: {exc}") from exc
    return topology, sprint_ref, sprint, source


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
