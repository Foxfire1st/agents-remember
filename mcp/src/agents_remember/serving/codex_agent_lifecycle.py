"""Authority ordering for the Codex adapter's bounded child registry."""

from __future__ import annotations

_TERMINAL = frozenset({"completed", "failed", "interrupted", "cancelled", "errored"})


def merge_agent_status(
    current: str,
    candidate: str,
    *,
    explicit_turn_start: bool = False,
) -> str:
    """Merge one child status without historical lifecycle regression.

    ``turn/started`` is the native proof that a terminal child began a later
    lifecycle. Collab/history observations and generic thread status may enrich
    a non-terminal child but cannot reopen a terminal one.
    """

    if current in _TERMINAL and candidate not in _TERMINAL and not explicit_turn_start:
        return current
    return candidate


def completed_turn_status(raw_status: object) -> str:
    """Normalize a terminal turn status into the registry vocabulary."""

    if raw_status in {"interrupted", "cancelled"}:
        return "interrupted"
    if raw_status in {"failed", "errored"}:
        return "failed"
    return "completed"
