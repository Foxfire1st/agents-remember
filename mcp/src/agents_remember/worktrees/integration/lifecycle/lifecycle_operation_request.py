"""Typed validation for the public lifecycle-control request envelope."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class LifecycleControlRequestShape:
    """Authority-free facts needed to validate one public control request."""

    action: str
    expected_generation: int
    intent_note: str
    commit_messages: Mapping[str, str | None]
    has_grade: bool = False
    has_admission: bool = False


class LifecycleControlRequestError(ValueError):
    """Bounded pre-authority refusal for a malformed control request."""

    status = "lifecycle-control-request-invalid"
    detail = "the lifecycle operation control request is invalid"

    def __init__(
        self,
        *,
        expected: Mapping[str, object],
        observed: Mapping[str, object],
    ) -> None:
        self.expected = dict(expected)
        self.observed = dict(observed)
        super().__init__(self.detail)


def validate_lifecycle_control_request(
    request: LifecycleControlRequestShape,
) -> None:
    """Validate reachable request-shape rules before any durable authority read."""

    if request.expected_generation < 1:
        raise LifecycleControlRequestError(
            expected={"field": "expected_generation", "minimum": 1},
            observed={"field": "expected_generation", "value": request.expected_generation},
        )
    if not request.intent_note.replace("\n", " ").strip():
        raise LifecycleControlRequestError(
            expected={"field": "intent_note", "state": "nonblank"},
            observed={"field": "intent_note", "state": "blank"},
        )
    present = sorted(name for name, value in request.commit_messages.items() if value is not None)
    if request.action != "revise" and present:
        raise LifecycleControlRequestError(
            expected={"action": request.action, "commitMessageFields": "absent"},
            observed={"action": request.action, "presentFields": present},
        )
    source_payload = request.has_grade and request.has_admission
    if request.has_grade != request.has_admission or source_payload != (
        request.action == "supersede"
    ):
        raise LifecycleControlRequestError(
            expected={
                "action": request.action,
                "gradeAndAdmission": ("required" if request.action == "supersede" else "forbidden"),
            },
            observed={
                "hasGrade": request.has_grade,
                "hasAdmission": request.has_admission,
            },
        )
