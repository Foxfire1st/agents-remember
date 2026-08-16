"""Application rules for explicit sprint execution topology and its finite migration."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.errors import AgentsRememberError
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    MasterExecutionNature,
    SprintExecutionGraph,
    TaskDocument,
    completion_blockers,
    json_path_for,
    markdown_path_for,
    read_task_doc,
    render_markdown,
    write_task_doc_batch,
)
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.integration_branch_authority import (
    require_topology_migration_authority,
)


class ExecutionTopologyError(AgentsRememberError):
    """An execution-topology edit or migration is structurally invalid."""


class _ExecutionMigrationMaster(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taskDocumentRef: TaskDocumentRef
    executionNature: MasterExecutionNature


class _ExecutionTopologyMigration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    masters: list[_ExecutionMigrationMaster] = Field(min_length=1)
    executionGraph: SprintExecutionGraph

    @model_validator(mode="after")
    def _check_exact_master_entries(self) -> _ExecutionTopologyMigration:
        refs = [entry.taskDocumentRef for entry in self.masters]
        if len(refs) != len(set(refs)):
            raise ValueError("migration master taskDocumentRef values must be unique")
        if set(refs) != set(self.executionGraph.nodes):
            raise ValueError("migration masters must exactly match executionGraph nodes")
        return self


@dataclass(frozen=True)
class ExecutionTopologyMigrationRequest:
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


def migrate_execution_topology(request: ExecutionTopologyMigrationRequest) -> dict[str, Any]:
    """Explicitly cut one legacy sprint and all commanded masters to the new contract."""

    try:
        migration = _ExecutionTopologyMigration.model_validate(request.fields)
    except ValidationError as exc:
        raise ExecutionTopologyError(f"invalid execution-topology migration: {exc}") from exc
    sprint_path = _existing_json(request.task_root, request.slug)
    sprint = read_task_doc(sprint_path)
    if sprint.kind != "master" or not sprint.orchestrates:
        raise ExecutionTopologyError(
            "migrate_execution_topology requires an orchestration sprint document"
        )
    topology = TaskDocumentTopology(request.coordination_root)
    try:
        sprint_ref = topology.canonical_ref(request.repo_id, sprint_path)
    except TaskDocumentRefError as exc:
        raise ExecutionTopologyError(f"{exc.status}: {exc}") from exc
    sprint_data = sprint.model_dump(by_alias=True)
    sprint_data["executionGraph"] = migration.executionGraph.model_dump(mode="json")
    candidate_sprint = TaskDocument.model_validate(sprint_data)
    overrides: dict[TaskDocumentRef, TaskDocument] = {sprint_ref: candidate_sprint}
    documents: list[tuple[TaskDocumentRef, Path, TaskDocument]] = [
        (sprint_ref, request.task_root, candidate_sprint)
    ]
    for entry in migration.masters:
        try:
            resolved = topology.resolve(entry.taskDocumentRef)
        except TaskDocumentRefError as exc:
            raise ExecutionTopologyError(f"{exc.status}: {exc}") from exc
        if resolved.document.kind != "master" or resolved.document.orchestrates:
            raise ExecutionTopologyError(
                f"migration target is not a commanded master: {entry.taskDocumentRef.key}"
            )
        data = resolved.document.model_dump(by_alias=True)
        data["executionNature"] = entry.executionNature
        candidate = TaskDocument.model_validate(data)
        overrides[entry.taskDocumentRef] = candidate
        documents.append((entry.taskDocumentRef, resolved.path.parent, candidate))
    _validate_topology(topology, sprint_ref, overrides)

    result: dict[str, Any] = {
        "ok": True,
        "operation": "task_doc.migrate_execution_topology",
        "state": "would-migrate" if request.dry_run else "migrated",
        "sprintTaskDocumentRef": sprint_ref.model_dump(mode="json"),
        "migratedMasters": [
            {
                "taskDocumentRef": entry.taskDocumentRef.model_dump(mode="json"),
                "executionNature": entry.executionNature,
            }
            for entry in migration.masters
        ],
        "executionWaves": [
            [ref.model_dump(mode="json") for ref in wave]
            for wave in migration.executionGraph.derived_waves()
        ],
    }
    if request.dry_run:
        with integration_authority_lock(request.coordination_root, request.repo_id):
            _require_migration_publication_authority(request, overrides)
        result["dryRun"] = True
        result["documents"] = [
            _migration_preview(ref, root, document) for ref, root, document in documents
        ]
        return result

    def publication() -> list[tuple[Path, Path]]:
        with integration_authority_lock(request.coordination_root, request.repo_id):
            _require_migration_publication_authority(request, overrides)
            return write_task_doc_batch([(root, document) for _ref, root, document in documents])

    queue = CloseoutQueueStore(request.coordination_root, sprint_ref)
    written = queue.publish_sprint_update(
        publication,
        completed=candidate_sprint.status == "Completed",
        recorded_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        validate_completion=lambda: require_commanded_masters_completed(
            topology,
            sprint_ref,
            overrides,
        ),
    )
    result["documents"] = [
        {
            "taskDocumentRef": ref.model_dump(mode="json"),
            "docPath": json_path.as_posix(),
            "renderedPath": markdown_path.as_posix(),
        }
        for (ref, _root, _document), (json_path, markdown_path) in zip(
            documents, written, strict=True
        )
    ]
    return result


def _require_migration_publication_authority(
    request: ExecutionTopologyMigrationRequest,
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
                f"task_doc.{request.operation}; use an explicit migration"
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


def _migration_preview(
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
