"""The ``ar-task-document/v1`` schema: the JSON-primary task document.

This is the persisted source of truth for a task's plan and progress. ``task.md``
(or ``<slug>.md`` for a sub-task) is a deterministic *render* of it (see
``render.py``); the JSON is never produced by parsing markdown back. It is the
peer of ``observer.projection`` -- a persisted/served Pydantic contract, **not**
an MCP response model.

Scope (slice 3c): ``light`` standalone tasks and ``subTask`` slices of a series
-- the lifecycle-keyed work-content documents. Series *master* files stay
hand-authored markdown for now (they carry bespoke sections a generic render
would drop), so ``master`` is deliberately absent from ``DocKind``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TASK_DOCUMENT_SCHEMA = "ar-task-document/v1"

# Step/substep status carries the dashboard's granularity; the markdown render
# only has a binary checkbox, so the richer state lives in the JSON.
StepStatus = Literal["pending", "inProgress", "blocked", "done"]
# Document status stays in the ``w-02-light-task-workflow`` template vocabulary so
# the rendered ``**Status:**`` line is always a valid template value.
DocStatus = Literal["planning", "inProgress", "Completed"]
DocKind = Literal["light", "subTask"]


class _Doc(BaseModel):
    """Strict base: unknown keys are a schema error; field name or alias accepts."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SubStep(_Doc):
    id: str
    title: str
    status: StepStatus = "pending"
    note: str | None = None


class Step(_Doc):
    id: str
    title: str
    status: StepStatus = "pending"
    substeps: list[SubStep] = Field(default_factory=list)


class Decision(_Doc):
    at: str
    decision: str
    rationale: str


class CodeExample(_Doc):
    id: str
    title: str
    distinctChange: str
    why: str
    language: str = ""
    snippet: str = ""


class TaskDocument(_Doc):
    schema_: Literal["ar-task-document/v1"] = Field(
        default=TASK_DOCUMENT_SCHEMA, alias="schema"
    )
    id: str
    slug: str
    title: str
    kind: DocKind
    status: DocStatus = "planning"
    repo: str
    type: str = ""
    createdAt: str
    master: str | None = None
    contractPath: str | None = None
    lifecycleId: str | None = None
    objective: str = ""
    requirements: list[str] = Field(default_factory=list)
    design: str | None = None
    steps: list[Step] = Field(default_factory=list)
    codeExamples: list[CodeExample] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    openQuestions: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)


def _leaf_statuses(doc: TaskDocument) -> list[StepStatus]:
    """The progress-bearing units: a step's substeps if it has any, else the step."""
    statuses: list[StepStatus] = []
    for step in doc.steps:
        if step.substeps:
            statuses.extend(sub.status for sub in step.substeps)
        else:
            statuses.append(step.status)
    return statuses


def step_total(doc: TaskDocument) -> int:
    return len(_leaf_statuses(doc))


def step_done(doc: TaskDocument) -> int:
    return sum(1 for status in _leaf_statuses(doc) if status == "done")


def current_step(doc: TaskDocument) -> str | None:
    """The active step for the dashboard: first in-progress/blocked, else first unfinished."""
    for step in doc.steps:
        if step.status in ("inProgress", "blocked"):
            return f"{step.id} — {step.title}"
    for step in doc.steps:
        if step.status != "done":
            return f"{step.id} — {step.title}"
    return None
