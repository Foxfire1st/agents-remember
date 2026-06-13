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
    """A standalone ``light`` task is ``task.{json,md}``; a ``subTask`` keeps its slug."""
    return "task" if doc.kind == "light" else doc.slug


def json_path_for(task_root: Path, doc: TaskDocument) -> Path:
    return task_root / f"{doc_stem(doc)}.json"


def markdown_path_for(task_root: Path, doc: TaskDocument) -> Path:
    return task_root / f"{doc_stem(doc)}.md"


def read_task_doc(json_path: Path) -> TaskDocument:
    return TaskDocument.model_validate_json(json_path.read_text(encoding="utf-8"))


def write_task_doc(task_root: Path, doc: TaskDocument) -> tuple[Path, Path]:
    json_path = json_path_for(task_root, doc)
    markdown_path = markdown_path_for(task_root, doc)
    task_root.mkdir(parents=True, exist_ok=True)
    payload = doc.model_dump_json(by_alias=True, exclude_none=True, indent=2)
    _atomic_write(json_path, f"{payload}\n")
    _atomic_write(markdown_path, render_markdown(doc))
    return json_path, markdown_path


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
