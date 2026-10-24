"""Task-document stage changes prepared for worktree task-fact transactions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.tasks import TaskDocument, read_task_doc
from agents_remember.tasks.document import intent_review_refusal
from agents_remember.tasks.leaf_doc import (
    require_task_document_in_place,
    resolve_terminal_leaf_doc,
)
from agents_remember.tasks.master_sync import plan_master_sync
from agents_remember.tasks.store import (
    TaskDocSourceSnapshot,
    capture_task_doc_source,
    current_task_doc_source,
    restore_task_doc_sources,
    write_task_doc_batch,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class TaskStageChange:
    documents: tuple[TaskDocument, ...] = ()
    sources: tuple[TaskDocSourceSnapshot, ...] = ()

    @property
    def candidates(self) -> tuple[tuple[Path, TaskDocument], ...]:
        return tuple(
            (source.json_path, document)
            for source, document in zip(self.sources, self.documents, strict=True)
        )

    def validate(self) -> None:
        for source in self.sources:
            if current_task_doc_source(source) != source:
                raise ValueError(
                    f"task document changed before stage publication: {source.json_path}"
                )

    def publish(self) -> None:
        self.validate()
        write_task_doc_batch(
            [
                (source.json_path.parent, doc)
                for source, doc in zip(self.sources, self.documents, strict=True)
            ]
        )

    def restore(self) -> None:
        restore_task_doc_sources(self.sources)


def plan_task_stage(
    contract: WorktreeContract,
    status: Literal["inProgress", "abandoned"] | None,
    *,
    source: tuple[Path, TaskDocument] | None = None,
) -> TaskStageChange:
    """Prepare one document and its mirrored master row without inventing absent documents.

    Start promotes planning or an admitted abandoned successor. Attaching binds while
    preserving stage; completed documents retain their reopen/finalize gates. Abandon records its own
    terminal decision. Every batch uses the existing rollback-safe task document writer.
    """
    if source is None:
        source = _task_source(contract)
    if source is None:
        return TaskStageChange()
    path, document = source
    require_task_document_in_place(path, document, ValueError)
    if status == "inProgress" and document.status in {"planning", "abandoned"}:
        # MIK-R189 rule 2, the last of its four places: while the intent review is open no move
        # to ``inProgress`` is written, the abandoned -> inProgress successor included.
        refusal = intent_review_refusal(document)
        if refusal is not None:
            raise ValueError(refusal)
    original = capture_task_doc_source(path)
    updated = document
    if status == "abandoned" or (
        status == "inProgress" and document.status in {"planning", "abandoned"}
    ):
        updated = TaskDocument.model_validate({**document.model_dump(), "status": status})
    documents = []
    sources = []
    if (
        original.json_bytes is None
        or TaskDocument.model_validate_json(original.json_bytes) != updated
    ):
        documents.append(updated)
        sources.append(original)
    master = plan_master_sync(path.parent, updated)
    if master.changed:
        assert master.master is not None and master.source_snapshot is not None
        require_task_document_in_place(master.source_snapshot.json_path, master.master, ValueError)
        documents.append(master.master)
        sources.append(master.source_snapshot)
    return TaskStageChange(tuple(documents), tuple(sources))


def _task_source(contract: WorktreeContract) -> tuple[Path, TaskDocument] | None:
    if contract.kind == "leaf" and contract.leaf_id:
        return resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
    path = contract.task_root / "task.json"
    return (path, read_task_doc(path)) if path.exists() else None
