"""Add the next seat action to a recorded review's answer, without changing review state."""

from __future__ import annotations

from typing import Any

from agents_remember.kernel.agentic_settings import AgenticSettingsError, load_agentic_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.tasks.route_review import ReviewState
from agents_remember.worktrees.review_history import failed_review_tries, review_count_unavailable


def with_review_step_up(config: McpRuntimeConfig, answer: dict[str, Any]) -> dict[str, Any]:
    if answer.get("operation") not in {
        "task_doc.record_review",
        "task_doc.record_route_review",
    } or not answer.get("ok"):
        return answer
    memory_review = answer.get("reviewKind") == "memory"
    state_key = "memoryReviewState" if memory_review else "reviewState"
    state = ReviewState.model_validate(answer.get(state_key) or {})
    if not state.remainingFindingIds:
        return answer
    try:
        settings = load_agentic_settings(config.coordination_root)
    except (AgenticSettingsError, OSError) as error:
        answer["warning"] = " ".join(
            filter(
                None,
                (
                    answer.get("warning"),
                    f"The review was recorded, but step-up advice could not read the settings: {error}",
                ),
            )
        )
        answer["nextAction"] = " ".join(
            filter(
                None,
                (
                    answer.get("nextAction"),
                    "Repair the settings and inspect this task's recorded review before starting a seat. Do not repeat the review recording to recover this advice.",
                ),
            )
        )
        return answer
    next_steps = []
    for role in ("curator",) if memory_review else ("worker",):
        step = settings.resolved_role_knobs(role).step_up
        tries = failed_review_tries(state, role)
        if step is not None and tries is None:
            next_steps.append(
                f"The {role} review count could not be read: {review_count_unavailable(state)} No counted step-up is selected."
            )
        elif (
            step is not None
            and step.after_tries is not None
            and tries is not None
            and tries >= step.after_tries
        ):
            next_steps.append(
                f"The {role} seat steps up after {tries} tries; its starter calls role_start anew on this task. This opens no review round and grants none."
            )
    if next_steps:
        answer["nextAction"] = " ".join(next_steps)
    return answer
