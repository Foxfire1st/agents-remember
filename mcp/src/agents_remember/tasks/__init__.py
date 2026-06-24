"""JSON-primary task documents (``ar-task-document/v1``) and their markdown render.

The JSON document is the source of truth; ``task.md`` is a deterministic render
of it. This package owns the schema (``document``), the renderer (``render``),
and the read/write store (``store``); the ``task_doc`` MCP tool authors them and
the observer projects them (keyed by lifecycle).
"""

from __future__ import annotations

from .document import (
    TASK_DOCUMENT_SCHEMA,
    CodeExample,
    Decision,
    DocKind,
    DocStatus,
    HeaderNote,
    Section,
    Step,
    StepStatus,
    SubStep,
    SubTaskRef,
    TaskDocument,
    TaskEnclosureRef,
    current_step,
    series_done,
    series_total,
    step_done,
    step_total,
)
from .render import render_markdown
from .store import (
    doc_stem,
    json_path_for,
    markdown_path_for,
    read_task_doc,
    write_task_doc,
)

__all__ = [
    "TASK_DOCUMENT_SCHEMA",
    "CodeExample",
    "Decision",
    "DocKind",
    "DocStatus",
    "HeaderNote",
    "Section",
    "Step",
    "StepStatus",
    "SubStep",
    "SubTaskRef",
    "TaskDocument",
    "TaskEnclosureRef",
    "current_step",
    "doc_stem",
    "json_path_for",
    "markdown_path_for",
    "read_task_doc",
    "render_markdown",
    "series_done",
    "series_total",
    "step_done",
    "step_total",
    "write_task_doc",
]
