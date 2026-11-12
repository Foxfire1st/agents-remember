"""Carry a tool call's installed-build observation through threaded response shaping."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from agents_remember.models.build_currentness import BuildCurrentnessPayload

_CURRENTNESS: ContextVar[BuildCurrentnessPayload | None] = ContextVar(
    "tool_build_currentness", default=None
)


def current_tool_build_currentness() -> BuildCurrentnessPayload | None:
    """Only stale installed calls annotate ordinary answers; status inspects independently."""
    reading = _CURRENTNESS.get()
    if reading is not None and reading.mode == "installed" and reading.state == "stale":
        return reading
    return None


@contextmanager
def tool_build_currentness(reading: BuildCurrentnessPayload | None) -> Iterator[None]:
    token = _CURRENTNESS.set(reading)
    try:
        yield
    finally:
        _CURRENTNESS.reset(token)
