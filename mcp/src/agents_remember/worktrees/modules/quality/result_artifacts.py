"""Cross-validation for artifact references in the authoritative quality result."""

from __future__ import annotations

import json
from pathlib import Path

from agents_remember.worktrees.modules.quality.published_manifest import (
    is_safe_relative_report_path,
)


def validate_result_artifact_references(source: Path, exported_names: set[str]) -> None:
    """Reject result claims that do not name files in the exported generation."""

    try:
        payload = json.loads((source / "clean-quality-results.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Dagger exported no valid authoritative result") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Dagger authoritative result must be an object")
    completed = _completed_steps(payload)
    references = _artifact_references(payload)
    missing = sorted(reference for reference in references if reference not in exported_names)
    if missing:
        raise RuntimeError(f"Dagger result references missing report artifacts: {missing}")
    if completed is not None:
        _validate_step_owned_references(payload, completed)


def _completed_steps(payload: dict[str, object]) -> frozenset[str] | None:
    raw = payload.get("completedSteps")
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise RuntimeError("Dagger result completedSteps must be a string list")
    return frozenset(raw)


def _artifact_references(payload: dict[str, object]) -> set[str]:
    references: set[str] = set()
    for field in ("causalFailureReport", "causalFailureSummary"):
        value = payload.get(field)
        if value is not None:
            references.add(_reference(value, field))
    ambient = payload.get("ambientRoleChatEvidence")
    if ambient is None:
        return references
    if not isinstance(ambient, dict):
        raise RuntimeError("Dagger result ambientRoleChatEvidence must be an object")
    summary = ambient.get("summary")
    if summary is not None:
        references.add(_reference(summary, "ambientRoleChatEvidence.summary"))
    runs = ambient.get("runs", [])
    if not isinstance(runs, list):
        raise RuntimeError("Dagger result ambientRoleChatEvidence.runs must be a list")
    references.update(_reference(value, "ambientRoleChatEvidence.runs") for value in runs)
    return references


def _reference(value: object, field: str) -> str:
    if not isinstance(value, str) or not is_safe_relative_report_path(value):
        raise RuntimeError(f"Dagger result {field} contains an invalid artifact reference")
    return value


def _validate_step_owned_references(
    payload: dict[str, object],
    completed: frozenset[str],
) -> None:
    causal_fields = {"causalFailureReport", "causalFailureSummary"}
    present = causal_fields & payload.keys()
    wrapper_completed = "quality-wrapper" in completed
    if wrapper_completed and present != causal_fields:
        raise RuntimeError("completed quality-wrapper omitted its causal artifact references")
    if not wrapper_completed and present:
        raise RuntimeError("incomplete quality-wrapper claimed causal artifact references")
