"""Typed validation for the public lifecycle-control request envelope."""

from __future__ import annotations

from collections.abc import Mapping


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
    *,
    action: str,
    expected_generation: int,
    intent_note: str,
    commit_messages: Mapping[str, str | None],
) -> None:
    """Validate reachable request-shape rules before any durable authority read."""

    if expected_generation < 1:
        raise LifecycleControlRequestError(
            expected={"field": "expected_generation", "minimum": 1},
            observed={"field": "expected_generation", "value": expected_generation},
        )
    if not intent_note.replace("\n", " ").strip():
        raise LifecycleControlRequestError(
            expected={"field": "intent_note", "state": "nonblank"},
            observed={"field": "intent_note", "state": "blank"},
        )
    present = sorted(name for name, value in commit_messages.items() if value is not None)
    if action != "revise" and present:
        raise LifecycleControlRequestError(
            expected={"action": action, "commitMessageFields": "absent"},
            observed={"action": action, "presentFields": present},
        )
