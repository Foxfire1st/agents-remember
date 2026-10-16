"""User role defaults and their provenance, independent of capsule compilation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from agents_remember.application.role_launch_context import ROLE_LEVELS, RoleLaunchContext
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig


def role_defaults(
    config: McpRuntimeConfig, context: RoleLaunchContext, host_role: dict[str, Any]
) -> tuple[dict[str, Any], tuple[str, ...]]:
    repository = context.effective_task.ref.repository if context.effective_task else None
    scope = config.repositories.get(repository) if repository else None
    settings = load_agentic_settings(
        config.coordination_root, repo_root=scope.path if scope else None
    )
    knobs = settings.resolved_role_knobs(context.role, ROLE_LEVELS[context.role])
    step = knobs.step_up
    return {
        "agent": knobs.harness or settings.spawn_harness,
        "model": knobs.model,
        "effort": knobs.effort,
        "serviceTier": knobs.service_tier,
        "mode": knobs.mode,
        "featureValues": dict(knobs.feature_values),
        "notes": knobs.notes,
        "role": context.role,
        "notesKey": knobs.origins["notes"].key
        if "notes" in knobs.origins
        else f"orchestration.roles.{context.role}.notes",
        "hostRole": host_role,
        "settingsSources": [path.as_posix() for path in settings.sources],
        "fieldOrigins": {key: asdict(origin) for key, origin in knobs.origins.items()},
        "stepUp": {
            **{
                key: value
                for key, value in {
                    "harness": step.choices.harness,
                    "model": step.choices.model,
                    "effort": step.choices.effort,
                    "serviceTier": step.choices.service_tier,
                    "mode": step.choices.mode,
                    "featureValues": step.choices.feature_values or None,
                    "notes": step.choices.notes,
                    "afterTries": step.after_tries,
                }.items()
                if value is not None
            },
        }
        if step
        else None,
    }, settings.harness_ids
