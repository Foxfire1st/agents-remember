"""Exact task-document publication under short integration authority."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    SprintGraphTitles,
    SubTaskRef,
    TaskDocSourceReadError,
    TaskDocSourceSnapshot,
    TaskDocument,
    current_task_doc_source,
    json_path_for,
    read_graph_titles,
    write_task_docs,
)
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.integration.integration_branch_authority import (
    require_topology_publication_authority,
)

from .task_doc_queue_scope import (
    QueuePublicationScope,
    QueueScopeError,
    QueueScopePreparation,
    prepare_governing_queue_scope,
)
from .task_doc_route_review import TaskDocError
from .task_execution_topology import require_commanded_masters_completed


class TaskDocPublicationConflict(TaskDocError):
    """A selected JSON/Markdown source changed after the candidate was prepared."""

    def __init__(
        self,
        *,
        expected: dict[str, object],
        observed: dict[str, object],
    ) -> None:
        self.status = "task-document-publication-conflict"
        self.expected = expected
        self.observed = observed
        super().__init__(
            "task-document-publication-conflict: selected task JSON or Markdown changed "
            "after this edit was prepared; re-read and retry the task-addressed edit"
        )


@dataclass(frozen=True)
class TaskDocPublication:
    config: McpRuntimeConfig
    target_repo_id: str
    task_root: Path
    original: TaskDocument | None
    candidate: TaskDocument
    documents: list[TaskDocument]
    source_snapshots: tuple[TaskDocSourceSnapshot, ...]
    publisher: Callable[[], list[tuple[Path, Path]]] | None = None
    queue_scope: QueuePublicationScope | None = field(init=False)

    def __post_init__(self) -> None:
        try:
            prepared = prepare_governing_queue_scope(
                QueueScopePreparation(
                    coordination_root=self.config.coordination_root,
                    repo_id=self.target_repo_id,
                    task_root=self.task_root,
                    original=self.original,
                    candidate=self.candidate,
                    source_snapshots=self.source_snapshots,
                )
            )
        except QueueScopeError as exc:
            raise TaskDocError(str(exc)) from exc
        object.__setattr__(self, "source_snapshots", prepared.source_snapshots)
        object.__setattr__(self, "queue_scope", prepared.scope)


@dataclass(frozen=True)
class TaskDocPublicationTransaction:
    """One exact source-pair CAS and publication under short integration authority."""

    coordination_root: Path
    target_repo_id: str
    source_snapshots: tuple[TaskDocSourceSnapshot, ...]
    queue_scope: QueuePublicationScope | None
    authority: Callable[[], None]
    publisher: Callable[[], list[tuple[Path, Path]]]


def publish_task_doc_set(context: TaskDocPublication) -> list[tuple[Path, Path]]:
    """Publish one prepared set only if all accepted source bytes remain exact."""

    transaction = task_doc_publication_transaction(context)

    def publication() -> list[tuple[Path, Path]]:
        return publish_task_doc_transaction(transaction)

    scope = transaction.queue_scope
    if scope is None:
        return publication()
    queue = CloseoutQueueStore(context.config.coordination_root, scope.sprint_ref)
    if context.candidate.kind != "master" or not context.candidate.orchestrates:
        if scope.owning_master is None:
            raise TaskDocError("governed master/leaf edit has no owning master queue scope")
        return queue.publish_task_facts_update(
            publication,
            owning_master=scope.owning_master,
            topology_stable=_task_topology_stable(context.original, context.candidate),
        )
    return cast(
        list[tuple[Path, Path]],
        queue.publish_sprint_update(
            publication,
            completed=context.candidate.status == "Completed",
            recorded_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
            validate_completion=lambda: require_commanded_masters_completed(
                TaskDocumentTopology(context.config.coordination_root),
                scope.sprint_ref,
                {scope.sprint_ref: context.candidate},
            ),
        ),
    )


def task_doc_publication_transaction(
    context: TaskDocPublication,
) -> TaskDocPublicationTransaction:
    """Build the sole exact transaction for one ordinary/remove task-doc candidate."""

    return TaskDocPublicationTransaction(
        context.config.coordination_root,
        context.target_repo_id,
        context.source_snapshots,
        context.queue_scope,
        lambda: validate_task_doc_publication_authority(context),
        context.publisher
        or (
            lambda: write_task_docs(
                context.task_root,
                context.documents,
                graph_titles=_batch_graph_titles(context.task_root, context.documents),
            )
        ),
    )


def publish_task_doc_transaction(
    transaction: TaskDocPublicationTransaction,
) -> list[tuple[Path, Path]]:
    """CAS and publish one prepared task-document set through the sole transaction."""

    with integration_authority_lock(
        transaction.coordination_root,
        transaction.target_repo_id,
    ):
        require_task_doc_sources_current(transaction.source_snapshots)
        transaction.authority()
        return transaction.publisher()


def validate_task_doc_transaction(transaction: TaskDocPublicationTransaction) -> None:
    """Read-only dry-run preflight through the same exact source-pair transaction."""

    with integration_authority_lock(
        transaction.coordination_root,
        transaction.target_repo_id,
        create=False,
    ):
        require_task_doc_sources_current(transaction.source_snapshots)
        transaction.authority()


def validate_task_doc_publication_authority(context: TaskDocPublication) -> None:
    repository = context.config.repositories[context.target_repo_id]
    try:
        require_topology_publication_authority(
            context.config.coordination_root,
            context.target_repo_id,
            repository.path,
            repository.memory_root,
            _task_doc_publication_overrides(context),
        )
    except RuntimeError as exc:
        raise TaskDocError(str(exc)) from exc


def require_task_doc_sources_current(
    snapshots: tuple[TaskDocSourceSnapshot, ...],
) -> None:
    """Exact-CAS precondition shared by preview and protected publication."""

    for accepted in snapshots:
        try:
            current = current_task_doc_source(accepted)
        except TaskDocSourceReadError as exc:
            raise TaskDocPublicationConflict(
                expected=accepted.evidence(),
                observed={"readFailure": exc.evidence()},
            ) from exc
        if current != accepted:
            raise TaskDocPublicationConflict(
                expected=accepted.evidence(),
                observed=current.evidence(),
            )


def _task_doc_publication_overrides(
    context: TaskDocPublication,
) -> dict[TaskDocumentRef, TaskDocument]:
    root = (context.config.coordination_root / "tasks" / context.target_repo_id).resolve(
        strict=False
    )
    overrides: dict[TaskDocumentRef, TaskDocument] = {}
    for document in context.documents:
        path = json_path_for(context.task_root, document).resolve(strict=False)
        if not path.is_relative_to(root):
            raise TaskDocError(f"task document publication escapes tasks root: {path}")
        ref = TaskDocumentRef(
            repository=context.target_repo_id,
            path=path.relative_to(root).as_posix(),
        )
        overrides[ref] = document
    return overrides


def _task_topology_stable(
    original: TaskDocument | None,
    candidate: TaskDocument,
) -> bool:
    if original is None or original.kind != candidate.kind:
        return False
    stable_fields = ("id", "slug", "title", "repo", "orchestrates", "executionNature")
    if any(getattr(original, field) != getattr(candidate, field) for field in stable_fields):
        return False
    if candidate.kind != "master":
        return True

    def identity(row: SubTaskRef) -> tuple[str, str, str | None]:
        return row.number, row.name, row.file

    return [identity(row) for row in original.subTasks] == [
        identity(row) for row in candidate.subTasks
    ]


def _batch_graph_titles(
    task_root: Path,
    documents: list[TaskDocument],
) -> SprintGraphTitles | None:
    for document in documents:
        if document.executionGraph is not None:
            return read_graph_titles(task_root.parents[1], document.executionGraph)
    return None
