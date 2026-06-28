"""Read and write task documents: the JSON is the source, the markdown a render.

Every write persists the JSON (the source of truth) **and** its rendered
markdown atomically (temp file + ``os.replace``), the same idiom the observer
drift snapshot uses. Reading goes through ``model_validate_json`` -- the markdown
is never parsed back into a document.
"""

from __future__ import annotations

import os
from pathlib import Path

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
    """Write several task docs after every JSON/render payload is prepared."""
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
    for path, text in writes:
        _atomic_write(path, text)
    return paths


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
