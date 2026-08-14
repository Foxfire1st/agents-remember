"""Read and write task documents: the JSON is the source, the markdown a render.

Every write persists the JSON (the source of truth) **and** its rendered
markdown through :mod:`agents_remember.kernel.atomic_write`, the package's one atomic
publish -- the same call the observer drift snapshot makes. Reading goes through
``model_validate_json``; the markdown is never parsed back into a document.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_bytes, atomic_write_text

from .document import TaskDocument
from .render import render_markdown


def doc_stem(doc: TaskDocument) -> str:
    """``light`` and ``master`` docs are ``task.{json,md}``; a ``subTask`` keeps its slug."""
    return doc.slug if doc.kind == "subTask" else "task"


def json_path_for(task_root: Path, doc: TaskDocument) -> Path:
    return task_root / f"{doc_stem(doc)}.json"


def markdown_path_for(task_root: Path, doc: TaskDocument) -> Path:
    return task_root / f"{doc_stem(doc)}.md"


def read_task_doc(json_path: Path) -> TaskDocument:
    return TaskDocument.model_validate_json(json_path.read_text(encoding="utf-8"))


def write_task_doc(task_root: Path, doc: TaskDocument) -> tuple[Path, Path]:
    return write_task_docs(task_root, [doc])[0]


def write_task_docs(task_root: Path, docs: list[TaskDocument]) -> list[tuple[Path, Path]]:
    """Write a prepared document set, restoring every prior file if publication fails.

    Each destination replacement is individually atomic. The snapshot-and-restore around
    the set supplies failure atomicity for composite leaf/master transitions: a failure on
    a later destination cannot leave earlier task-document files at the new generation.
    """
    task_root.mkdir(parents=True, exist_ok=True)
    writes: list[tuple[Path, str]] = []
    paths: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    for doc in docs:
        json_path = json_path_for(task_root, doc)
        markdown_path = markdown_path_for(task_root, doc)
        if json_path in seen or markdown_path in seen:
            raise ValueError(f"duplicate task document write target: {json_path}")
        seen.update({json_path, markdown_path})
        payload = doc.model_dump_json(by_alias=True, exclude_none=True, indent=2)
        writes.append((json_path, f"{payload}\n"))
        writes.append((markdown_path, render_markdown(doc)))
        paths.append((json_path, markdown_path))
    originals = {path: path.read_bytes() if path.exists() else None for path, _text in writes}
    try:
        for path, text in writes:
            atomic_write_text(path, text)
    except BaseException as publish_error:
        try:
            _restore_task_doc_batch(originals)
        except BaseException as rollback_error:
            raise RuntimeError(
                f"task-document batch publication and rollback both failed: {rollback_error}"
            ) from publish_error
        raise
    return paths


def _restore_task_doc_batch(originals: dict[Path, bytes | None]) -> None:
    """Restore the exact pre-batch bytes, including absence for newly created files."""
    for path, payload in originals.items():
        if payload is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_bytes(path, payload)
