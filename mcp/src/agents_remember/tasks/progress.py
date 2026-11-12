"""Presentation progress for task documents; completion readiness lives separately."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .document import TaskDocument


def step_total(doc: TaskDocument) -> int:
    """Count major steps only, independent of their nested work units."""
    return len(doc.steps)


def step_done(doc: TaskDocument) -> int:
    return sum(step.status == "done" for step in doc.steps)


def current_step(doc: TaskDocument) -> str | None:
    """First active/blocked work unit, else first unfinished unit."""
    for step in doc.steps:
        if step.status in ("inProgress", "blocked"):
            return f"{step.id} — {step.title}"
        for sub in step.substeps:
            if sub.status in ("inProgress", "blocked"):
                return f"{step.id}/{sub.id} — {sub.title}"
    for step in doc.steps:
        if step.status != "done":
            return f"{step.id} — {step.title}"
        for sub in step.substeps:
            if sub.status != "done":
                return f"{step.id}/{sub.id} — {sub.title}"
    return None
