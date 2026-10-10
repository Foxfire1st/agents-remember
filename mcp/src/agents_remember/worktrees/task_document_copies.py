"""Rendered, read-only leaf documents inside the enclosure, outside its Git trees."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.tasks.task_paths import iter_leaf_enclosure_contracts
from agents_remember.worktrees.worktree_contract import load_contract

TASK_DOCUMENTS_DIRECTORY = "task-documents"


def prepare_task_document_copies(
    coordination_root: Path, group: Path, reference: TaskDocumentRef
) -> list[dict[str, Any]]:
    """Publish real rendered files and one index before the launch's first turn."""
    writes, rows = _copy_writes(coordination_root, group, reference)
    _publish_copy_writes(writes)
    root = group / TASK_DOCUMENTS_DIRECTORY
    return [{**row, "path": (root / row["path"]).as_posix()} for row in rows]


def _copy_writes(
    coordination_root: Path, group: Path, reference: TaskDocumentRef
) -> tuple[dict[Path, bytes], list[dict[str, Any]]]:
    root = group / TASK_DOCUMENTS_DIRECTORY
    _require_real_destination(root, group)
    topology = TaskDocumentTopology(coordination_root)
    writes: dict[Path, bytes] = {}
    rows = []
    timestamp = datetime.now(UTC).isoformat()
    source = topology.path_for_ref(reference)
    destination = root / Path(reference.path).with_suffix(".md")
    _require_real_destination(destination, root)
    raw = source.with_suffix(".md").read_bytes()
    writes[destination] = raw
    rows.append(
        {
            "reference": reference.model_dump(mode="json"),
            "canonicalJsonSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "copySha256": hashlib.sha256(raw).hexdigest(),
            "path": destination.relative_to(root).as_posix(),
            "copiedAt": timestamp,
        }
    )
    index = root / "INDEX.json"
    _require_real_destination(index, root)
    writes[index] = (
        json.dumps({"schema": "ar-task-documents/v1", "documents": rows}, indent=2) + "\n"
    ).encode()
    return writes, rows


def _publish_copy_writes(writes: dict[Path, bytes]) -> None:
    originals = {path: path.read_bytes() if path.exists() else None for path in writes}
    try:
        for path, raw in writes.items():
            atomic_write_bytes(path, raw)
    except BaseException as publish_error:
        try:
            for path, raw in originals.items():
                if raw is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(path, raw)
        except BaseException as rollback_error:
            raise RuntimeError(
                f"task document copy publication and rollback both failed: {rollback_error}"
            ) from publish_error
        raise


def refresh_task_document_copies(
    coordination_root: Path, repository: str, written: list[tuple[Path, Path]]
) -> None:
    """A rendered leaf publication refreshes only that leaf in its existing enclosures."""
    repository_tasks = coordination_root / "tasks" / repository
    changed = {path.resolve() for path, _markdown in written}
    writes: dict[Path, bytes] = {}
    for contract_path in iter_leaf_enclosure_contracts(repository_tasks):
        contract = load_contract(contract_path)
        group = contract.worktree_group
        if not group.is_dir():
            continue
        leaf_path = contract.task_artifact.with_suffix(".json")
        leaf = TaskDocumentRef(
            repository=repository, path=leaf_path.relative_to(repository_tasks).as_posix()
        )
        if leaf_path.resolve() not in changed:
            continue
        root = group / TASK_DOCUMENTS_DIRECTORY
        for worktree in (contract.code_worktree, contract.memory_worktree):
            if worktree is not None and root.resolve().is_relative_to(worktree.resolve()):
                raise ValueError(f"Task document copies must be outside Git worktrees: {root}")
        group_writes, _rows = _copy_writes(coordination_root, group, leaf)
        writes.update(group_writes)
    _publish_copy_writes(writes)


def _require_real_destination(path: Path, root: Path) -> None:
    if not path.is_relative_to(root):
        raise ValueError(f"Task document copy escapes its working folder: {path}")
    current = path
    while current.is_relative_to(root):
        if current.is_symlink():
            raise ValueError(f"Task document copies require real files/directories: {current}")
        if current == root:
            break
        current = current.parent
