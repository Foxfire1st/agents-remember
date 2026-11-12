"""Launch-only recovery of unreadable review evidence, never of task identity."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from pydantic import SerializationInfo, ValidationError

# Context-local: a start may read its assignment despite broken review evidence.
# Ordinary task/review readers still validate the entire document strictly.
launch_review_read: ContextVar[bool] = ContextVar("launch_review_read", default=False)


def launch_review_fallback(
    value: Any, error: ValidationError
) -> tuple[dict[str, Any], dict[str, tuple[Any, str]]] | None:
    """Separate unreadable review lanes while validating all other task facts strictly."""
    lanes = {"reviewState", "memoryReviewState"}
    errors = error.errors()
    if (
        not launch_review_read.get()
        or not isinstance(value, dict)
        or any(not item["loc"] or item["loc"][0] not in lanes for item in errors)
    ):
        return None
    broken = {str(item["loc"][0]) for item in errors}
    unreadable = {key: (value[key], str(error)) for key in broken}
    return {**value, **dict.fromkeys(broken)}, unreadable


def preserve_unreadable_review(
    data: dict[str, Any], unreadable: dict[str, tuple[Any, str]], info: SerializationInfo
) -> dict[str, Any]:
    """Keep broken evidence verbatim in incidental launch publication, never erase it."""
    for key, (raw, _) in unreadable.items():
        if (info.include is None or key in info.include) and (
            info.exclude is None or key not in info.exclude
        ):
            data[key] = raw
    return data


@contextmanager
def tolerate_unreadable_review() -> Iterator[None]:
    token = launch_review_read.set(True)
    try:
        yield
    finally:
        launch_review_read.reset(token)
