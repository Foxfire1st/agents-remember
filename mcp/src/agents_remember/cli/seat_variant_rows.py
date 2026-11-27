"""Which settings rows a seat's launch reads: its role's row, refined for one kind of task.

A Reviewer started on a consolidation task reads ``consolidation-reviewer`` laid over the
``reviewer`` row field by field, each at the seat's level: a field the refining row leaves empty
takes the Reviewer's, so with no such row the seat takes the Reviewer's row unchanged. The task
kind comes from the leaf's task document, which names its list before any seat starts, so the
Reviewer finds this row whether it starts before or after the Consolidator. A start's own agent,
model and effort still rank above both rows where the launch picks its choices.
"""

from __future__ import annotations

from dataclasses import replace

from agents_remember.application.role_launch_context import RoleLaunchContext
from agents_remember.kernel._agentic_settings_core import (
    CONSOLIDATION_REVIEWER_ROW,
    overlay_role_knobs,
)
from agents_remember.kernel.agentic_settings import AgenticSettings, RoleKnobs
from agents_remember.models.role_identity import ROLE_LEVELS
from agents_remember.tasks.consolidation_task import names_consolidation_list


def seat_variant_row(context: RoleLaunchContext) -> str | None:
    """The row that refines this seat's role row for its task, or ``None`` when none applies."""

    if (
        context.role == "reviewer"
        and context.task is not None
        and names_consolidation_list(context.task.document)
    ):
        return CONSOLIDATION_REVIEWER_ROW
    return None


def seat_launch_knobs(settings: AgenticSettings, context: RoleLaunchContext) -> RoleKnobs:
    """The settings choices this seat's launch takes, before a start's own choices."""

    level = ROLE_LEVELS[context.role]
    own = settings.resolved_role_knobs(context.role, level)
    row = seat_variant_row(context)
    if row is None:
        return own
    return overlay_role_knobs(own, _bound_over(own, settings.resolved_role_knobs(row, level)))


def _bound_over(own: RoleKnobs, variant: RoleKnobs) -> RoleKnobs:
    """``variant`` with each choice bound to the harness and model it is laid over.

    A launch takes an effort or a feature only for the provider and model its row was authored
    for. The refining row is read as one more layer over the seat's row, as a per-level row is
    over its flat row: where it names no harness or model, its choices belong to the seat row's.
    Without this, an effort the refining row names beside the Reviewer's model would read as
    authored for no model and be dropped.
    """

    own_harness = own.origins.get("harness")
    own_model = own.origins.get("model")
    provider = variant.harness or (own_harness.provider if own_harness else None)
    model = variant.model or (
        own_model.model if own_model and own_model.provider in (None, provider) else None
    )
    origins = {
        key: replace(origin, provider=provider or origin.provider, model=model)
        for key, origin in variant.origins.items()
    }
    return replace(variant, origins=origins)


__all__ = ["seat_launch_knobs", "seat_variant_row"]
