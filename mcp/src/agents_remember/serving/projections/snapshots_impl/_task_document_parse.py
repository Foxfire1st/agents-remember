"""Backward-readable projection parsing, separate from strict task authoring."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from agents_remember.tasks import TaskDocument
from pydantic import ValidationError

# All unknown fields are reported together. A fixed retry bound also covers
# normalized union shapes without letting an unaddressable location loop.
_UNKNOWN_FIELD_PASSES = 4


def projected_document(payload: dict[str, object]) -> TaskDocument | None:
    """Ignore unknown-version fields, never malformed known task content."""
    candidate: Any = payload
    for _ in range(_UNKNOWN_FIELD_PASSES):
        try:
            return TaskDocument.model_validate(candidate)
        except ValidationError as error:
            unknown = [item["loc"] for item in error.errors() if item["type"] == "extra_forbidden"]
            if not unknown:
                return None
            candidate = _without_paths(candidate, unknown)
    return None


def _without_paths(payload: Any, paths: list[tuple[Any, ...]]) -> Any:
    """Copy a payload before removing each unknown-version field."""
    pruned = deepcopy(payload)
    for loc in paths:
        target = pruned
        for key in loc[:-1]:
            try:
                target = target[key]
            except (KeyError, IndexError, TypeError):
                break
        else:
            if isinstance(target, dict):
                target.pop(loc[-1], None)
            elif isinstance(target, list) and isinstance(loc[-1], int):
                target.pop(loc[-1])
    return pruned
