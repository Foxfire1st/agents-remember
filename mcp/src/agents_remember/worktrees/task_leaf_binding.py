"""Canonical master-row to leaf-task binding for lifecycle admission."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import SubTaskRef, TaskDocument, read_task_doc
from agents_remember.tasks.leaf_binding import (
    CanonicalLeafBindingError,
    canonical_leaf_source,
    require_canonical_leaf_binding,
    require_leaf_parent_row,
)
from agents_remember.tasks.leaf_doc import (
    LeafEnclosureRegistrationPlan,
    plan_leaf_doc_enclosure_registration,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.task_resolver import leaf_enclosure_path

if TYPE_CHECKING:
    from agents_remember.worktrees.worktree_contract import WorktreeContract


class TaskLeafBindingError(ValueError):
    """The parent row and exact child source do not form one canonical leaf identity."""

    def __init__(
        self,
        detail: str,
        *,
        status: str = "task-leaf-binding-invalid",
        facts: dict[str, object] | None = None,
    ) -> None:
        self.status = status
        self.detail = detail
        self.facts = facts or {}
        super().__init__(detail)


@dataclass(frozen=True)
class LeafTaskBinding:
    coordination_root: Path
    repo_id: str
    task_name: str
    task_root: Path
    parent_path: Path
    parent: TaskDocument
    row: SubTaskRef
    leaf_json_path: Path
    leaf_markdown_path: Path
    leaf: TaskDocument | None
    task_ref: TaskDocumentRef

    @property
    def contract_path(self) -> Path:
        return leaf_enclosure_path(self.task_root, self.row.number)


def resolve_leaf_task_binding(
    coordination_root: Path,
    repo_id: str,
    task_root: Path,
    leaf_id: str,
    *,
    task_name: str | None = None,
) -> LeafTaskBinding:
    """Resolve one parent row to its exact JSON-primary child without a directory scan."""

    root = task_root.resolve(strict=False)
    parent_path, parent = _load_leaf_parent(root)
    parent_ref = _task_ref_for_path(coordination_root, repo_id, parent_path)
    try:
        row = require_leaf_parent_row(parent, leaf_id)
        source = canonical_leaf_source(parent_ref, row)
    except CanonicalLeafBindingError as exc:
        raise TaskLeafBindingError(exc.detail, status=exc.status) from exc
    repository_root = (coordination_root / "tasks" / repo_id).resolve(strict=False)
    markdown = repository_root / source.markdown_path
    leaf_json = repository_root / source.json_ref.path
    leaf = _read_leaf_source(leaf_json, markdown)
    if leaf is not None:
        try:
            require_canonical_leaf_binding(parent_ref, parent, source.json_ref, leaf)
        except CanonicalLeafBindingError as exc:
            raise TaskLeafBindingError(exc.detail, status=exc.status) from exc
    return LeafTaskBinding(
        coordination_root=coordination_root.resolve(strict=False),
        repo_id=repo_id,
        task_name=(task_name or parent.id).strip(),
        task_root=root,
        parent_path=parent_path,
        parent=parent,
        row=row,
        leaf_json_path=leaf_json,
        leaf_markdown_path=markdown,
        leaf=leaf,
        task_ref=source.json_ref,
    )


def _load_leaf_parent(root: Path) -> tuple[Path, TaskDocument]:
    parent_path = root / "task.json"
    try:
        parent = read_task_doc(parent_path)
    except (OSError, ValueError) as exc:
        raise TaskLeafBindingError(
            f"canonical parent task document is missing or unreadable: {parent_path}: {exc}"
        ) from exc
    if parent.kind != "master":
        raise TaskLeafBindingError("discard-unstarted requires a master-owned leaf row")
    return parent_path, parent


def _read_leaf_source(
    leaf_json: Path,
    markdown: Path,
) -> TaskDocument | None:
    json_mode = _source_mode(leaf_json)
    markdown_mode = _source_mode(markdown)
    if json_mode is not None and not stat.S_ISREG(json_mode):
        raise TaskLeafBindingError(f"canonical leaf JSON source is not a regular file: {leaf_json}")
    if markdown_mode is not None and not stat.S_ISREG(markdown_mode):
        raise TaskLeafBindingError(
            f"canonical leaf Markdown source is not a regular file: {markdown}"
        )
    if json_mode is not None:
        try:
            leaf = read_task_doc(leaf_json)
        except (OSError, ValueError) as exc:
            raise TaskLeafBindingError(
                f"canonical leaf task document is unreadable: {leaf_json}: {exc}"
            ) from exc
        return leaf
    if markdown_mode is not None:
        raise TaskLeafBindingError(
            "the rendered leaf Markdown is present without its JSON-primary task source"
        )
    return None


def _task_ref_for_path(
    coordination_root: Path,
    repo_id: str,
    source_path: Path,
) -> TaskDocumentRef:
    repository_root = (coordination_root / "tasks" / repo_id).resolve(strict=False)
    if not source_path.is_relative_to(repository_root):
        raise TaskLeafBindingError("canonical task source escapes the repository task root")
    return TaskDocumentRef(
        repository=repo_id,
        path=source_path.relative_to(repository_root).as_posix(),
    )


def require_current_start_task_binding(
    coordination_root: Path,
    repo_id: str,
    task_root: Path,
    leaf_id: str,
    *,
    task_name: str | None = None,
) -> None:
    """Re-prove the task identity immediately before the start locator reservation."""
    _resolve_exact_leaf_document(
        coordination_root,
        repo_id,
        task_root,
        leaf_id,
        task_name=task_name,
    )


def plan_current_leaf_enclosure_registration(  # noqa: PLR0913
    coordination_root: Path,
    repo_id: str,
    task_root: Path,
    leaf_id: str,
    enclosure_path: Path,
    *,
    lifecycle_id: str | None = None,
    task_name: str | None = None,
) -> LeafEnclosureRegistrationPlan:
    """Prepare one exact leaf document candidate from canonical task topology."""

    document_path, _document = _resolve_exact_leaf_document(
        coordination_root,
        repo_id,
        task_root,
        leaf_id,
        task_name=task_name,
    )
    return plan_leaf_doc_enclosure_registration(
        document_path,
        leaf_id,
        enclosure_path,
        lifecycle_id=lifecycle_id,
    )


def require_current_leaf_enclosure_binding(  # noqa: PLR0913
    coordination_root: Path,
    repo_id: str,
    task_root: Path,
    leaf_id: str,
    enclosure_path: Path,
    *,
    task_name: str | None = None,
) -> LeafEnclosureRegistrationPlan:
    """Require the exact persisted leaf/enclosure binding before closeout work."""

    plan = plan_current_leaf_enclosure_registration(
        coordination_root,
        repo_id,
        task_root,
        leaf_id,
        enclosure_path,
        task_name=task_name,
    )
    if plan.candidate is not None:
        task_facts = _enclosure_binding_facts(plan, task_name=task_name)
        recovery = (
            "run task_doc.replace against this exact leaf contract, then re-run "
            "worktree_start/worktree_attach before closeout"
        )
        status = (
            "task-enclosure-binding-missing"
            if plan.state == "missing"
            else "task-enclosure-binding-mismatched"
        )
        raise TaskLeafBindingError(
            "addressed leaf enclosure binding is "
            f"{plan.state}: leaf {plan.leaf_id!r}, task document {plan.doc_path}, "
            f"contract {plan.enclosure_path}; recovery: {recovery}",
            status=status,
            facts={**task_facts, "recoveryOperation": recovery},
        )
    return plan


def leaf_enclosure_binding_refusal(
    contract: WorktreeContract,
) -> WorktreeCommandResult | None:
    """Return the actionable refusal before closeout preparation, if binding is stale."""

    try:
        require_current_leaf_enclosure_binding(
            contract.coordination_root,
            contract.repo_name,
            contract.task_root,
            contract.leaf_id,
            contract.contract_path,
            task_name=contract.task_name,
        )
    except TaskLeafBindingError as error:
        recovery = error.facts.get(
            "recoveryOperation",
            "publish the exact leaf/enclosure binding through task_doc.replace, then retry closeout",
        )
        return WorktreeCommandResult(
            2,
            {
                "state": error.status,
                "status": error.status,
                "summary": (f"closeout refused for leaf {contract.leaf_id!r}: {error.detail}"),
                "detail": error.detail,
                "leafId": contract.leaf_id,
                "taskName": contract.task_name,
                "taskDocument": error.facts.get("taskDocument", ""),
                "contractPath": contract.contract_path.as_posix(),
                "recoveryOperation": recovery,
                "nextAction": "repair-task-enclosure-binding",
                "nextTool": "task_doc",
                "nextArgs": {
                    "operation": "get",
                    "contract_path": contract.contract_path.as_posix(),
                },
            },
        )
    return None


def _resolve_exact_leaf_document(
    coordination_root: Path,
    repo_id: str,
    task_root: Path,
    leaf_id: str,
    *,
    task_name: str | None = None,
) -> tuple[Path, TaskDocument]:
    """Resolve one JSON-primary leaf through the parent row, never by sibling scan."""

    root_path = task_root.resolve(strict=False) / "task.json"
    try:
        root = read_task_doc(root_path)
    except (OSError, ValueError) as exc:
        raise TaskLeafBindingError(
            f"canonical task authority is missing or unreadable: {root_path}: {exc}",
            status="task-leaf-binding-document-unreadable",
            facts={"leafId": leaf_id, "taskDocument": root_path.as_posix()},
        ) from exc
    if root.kind == "master":
        binding = resolve_leaf_task_binding(
            coordination_root,
            repo_id,
            task_root,
            leaf_id,
            task_name=task_name,
        )
        if binding.leaf is None:
            raise TaskLeafBindingError(
                "canonical leaf task document is missing: "
                f"leaf {leaf_id!r}, task document {binding.leaf_json_path}",
                status="task-leaf-binding-document-missing",
                facts={
                    "leafId": leaf_id,
                    "taskDocument": binding.leaf_json_path.as_posix(),
                },
            )
        return binding.leaf_json_path, binding.leaf
    if root.kind != "subTask" or root.id != leaf_id:
        raise TaskLeafBindingError(
            f"canonical task identity changed: expected leaf {leaf_id!r}, observed "
            f"{root.id!r} ({root.kind}) in {root_path}",
            status="task-leaf-binding-identity-mismatch",
            facts={
                "leafId": leaf_id,
                "taskDocument": root_path.as_posix(),
                "observedTaskId": root.id,
            },
        )
    return root_path, root


def _enclosure_binding_facts(
    plan: LeafEnclosureRegistrationPlan,
    *,
    task_name: str | None,
) -> dict[str, object]:
    return {
        "leafId": plan.leaf_id,
        "taskName": task_name or "",
        "taskDocument": plan.doc_path.as_posix(),
        "contractPath": plan.enclosure_path,
        "bindingState": plan.state,
    }


def _source_mode(path: Path) -> int | None:
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise TaskLeafBindingError(
            f"canonical leaf source cannot be inspected: {path}: {type(exc).__name__}"
        ) from exc
