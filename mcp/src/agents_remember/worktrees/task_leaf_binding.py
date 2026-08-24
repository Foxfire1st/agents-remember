"""Canonical master-row to leaf-task binding for lifecycle admission."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import SubTaskRef, TaskDocument, read_task_doc
from agents_remember.worktrees.task_resolver import leaf_enclosure_path


class TaskLeafBindingError(ValueError):
    """The parent row and exact child source do not form one canonical leaf identity."""

    status = "task-leaf-binding-invalid"

    def __init__(self, detail: str) -> None:
        self.detail = detail
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
    row = _require_leaf_row(parent, leaf_id)
    markdown, leaf_json = _leaf_source_paths(root, row)
    leaf = _read_leaf_source(leaf_json, markdown, row)
    task_ref = _leaf_task_ref(coordination_root, repo_id, leaf_json)
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
        task_ref=task_ref,
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


def _require_leaf_row(parent: TaskDocument, leaf_id: str) -> SubTaskRef:
    rows = [row for row in parent.subTasks if row.number == leaf_id]
    if len(rows) != 1:
        raise TaskLeafBindingError(
            f"parent task must contain exactly one live row for leaf {leaf_id!r}"
        )
    row = rows[0]
    if row.masterRef is not None:
        raise TaskLeafBindingError("discard-unstarted is leaf-only, not a sprint master detach")
    return row


def _leaf_source_paths(root: Path, row: SubTaskRef) -> tuple[Path, Path]:
    file_value = row.file.strip()
    if not file_value:
        raise TaskLeafBindingError("the live leaf row has no exact child source path")
    relative = Path(file_value)
    if relative.is_absolute() or relative.parent != Path() or relative.suffix != ".md":
        raise TaskLeafBindingError(
            f"the live leaf row must name one direct Markdown child of {root}: {file_value!r}"
        )
    markdown = root / relative
    return markdown, markdown.with_suffix(".json")


def _read_leaf_source(
    leaf_json: Path,
    markdown: Path,
    row: SubTaskRef,
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
        if leaf.kind != "subTask" or leaf.id != row.number:
            raise TaskLeafBindingError(
                "the parent row and JSON-primary child disagree on leaf identity"
            )
        return leaf
    if markdown_mode is not None:
        raise TaskLeafBindingError(
            "the rendered leaf Markdown is present without its JSON-primary task source"
        )
    return None


def _leaf_task_ref(
    coordination_root: Path,
    repo_id: str,
    leaf_json: Path,
) -> TaskDocumentRef:
    repository_root = (coordination_root / "tasks" / repo_id).resolve(strict=False)
    if not leaf_json.is_relative_to(repository_root):
        raise TaskLeafBindingError("canonical leaf task source escapes the repository task root")
    return TaskDocumentRef(
        repository=repo_id,
        path=leaf_json.relative_to(repository_root).as_posix(),
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

    root_path = task_root.resolve(strict=False) / "task.json"
    try:
        root = read_task_doc(root_path)
    except (OSError, ValueError) as exc:
        raise TaskLeafBindingError(
            f"worktree_start task authority is missing or unreadable: {root_path}: {exc}"
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
                "worktree_start requires the exact JSON-primary leaf task source"
            )
        return
    if root.id != leaf_id:
        raise TaskLeafBindingError(
            f"worktree_start task identity changed: expected {leaf_id!r}, observed {root.id!r}"
        )


def _source_mode(path: Path) -> int | None:
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise TaskLeafBindingError(
            f"canonical leaf source cannot be inspected: {path}: {type(exc).__name__}"
        ) from exc
