"""D645/D646 trial labels; rubric validation remains the unchanged grader's job."""

from __future__ import annotations

from typing import Any

from agents_remember.errors import AuthorityError

from .adapters.execution import ACTOR_PROFILES
from .adapters.judges import judge_profile
from .adapters.models import NativeCapture
from .graders.cases import (
    RubricSubmission,
    parse_native_verdict,
    resolve_rubric,
    verdict_parse_provenance,
)
from .graders.models import Grade


def grading_exception_cause(report: dict[str, Any], error: Exception) -> str:
    """Classify existing caught grading errors without relabeling earlier phases.

    Bridge failures keep their existing capture-absent caller path. This helper
    adds neither a catch nor a new cause class.
    """
    cause = f"{type(error).__name__}: {error}"
    if report.get("executionPhase") != "grading":
        return cause
    if isinstance(error, (AuthorityError, OSError)):
        category = "binding-unavailable"
    elif isinstance(error, (ValueError, KeyError)):
        category = "schema-incompatible"
    else:
        return cause
    report["unknown_class"] = category
    report["unknown_cause"] = f"{category}: executionPhase=grading; {cause}"
    return report["unknown_cause"]


def _complete_unknown(capture: NativeCapture) -> bool:
    verdict, path = parse_native_verdict(capture)
    rows = verdict.get("criteria")
    if path == "invalid" or not isinstance(rows, list):
        return False
    return all(
        isinstance(row.get("evidence"), str) and row["evidence"].strip() for row in rows
    ) and any(row.get("outcome") == "unknown" for row in rows)


def resolve_judge_outcome(code: Grade, submission: RubricSubmission) -> str:
    """Keep capture failures separate from a valid judge's unanswerable criterion.

    The grader owns criterion order, evidence and outcome validation, including
    fail precedence. No altered capture or second judgment is used to label it.
    """
    outcome = resolve_rubric(code, submission)
    capture = submission.judge_capture
    if outcome != "missing_capture" or code.status in ("fail", "unknown") or capture.unknowns:
        return outcome
    actor = next(
        (profile for profile in ACTOR_PROFILES if profile.model == submission.actor_model),
        None,
    )
    if actor is None:
        return outcome
    observed = {
        event.payload["model"]
        for event in capture.events
        if isinstance(event.payload.get("model"), str)
    }
    if observed != {judge_profile(actor).model}:
        return outcome
    if _complete_unknown(capture):
        return "semantic_unknown"
    return outcome


def unknown_verdict_reason(capture: NativeCapture) -> str:
    verdict, path = parse_native_verdict(capture)
    if path == "invalid":
        return verdict_parse_provenance(capture)["reason"]
    rows = verdict.get("criteria", ())
    if not isinstance(rows, (list, tuple)):
        return "judge model identity or complete criterion verdict absent"
    unknown = [
        row["criterion"]
        for row in rows
        if isinstance(row, dict)
        and row.get("outcome") == "unknown"
        and isinstance(row.get("criterion"), str)
    ]
    if path != "invalid" and unknown:
        return "judge explicitly reported unknown criteria: " + "; ".join(unknown)
    return "judge model identity or complete criterion verdict absent"
