"""Decide the outcome a tool call's log line records: ``ok`` and ``code``.

The line's outcome is not always the answer's own ``ok``. Refusals spell
themselves three ways: knowledge tools answer ``ok: true`` with
``state: "refused"`` and a ``refusalCode``; role tools answer
``status: "refused"`` with the code in ``refusal``; the explainer answers
``state: "refused"`` with ``refusal`` and no ``ok`` at all. The line
makes every one of them read as a refusal on the line: ``ok: false`` and the
refusal's code. The answer the caller receives is not changed.

Not covered: an answer that refuses under another word (a worktree refusal
whose ``status`` names its own code, say) keeps its own ``ok`` and ``code``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

REFUSED = "refused"
_REFUSAL_CODE_FIELDS = ("refusalCode", "refusal", "code")


@dataclass(frozen=True)
class CallOutcome:
    """What one call's log line says about how it ended."""

    ok: bool
    code: str | None


def call_outcome(answer: Mapping[str, Any]) -> CallOutcome:
    """The ``(ok, code)`` a log line records for one finalized answer."""

    if answer.get("state") == REFUSED or answer.get("status") == REFUSED:
        return CallOutcome(ok=False, code=_refusal_code(answer))
    code = answer.get("code")
    return CallOutcome(
        ok=bool(answer.get("ok", False)), code=code if isinstance(code, str) else None
    )


def _refusal_code(answer: Mapping[str, Any]) -> str:
    for name in _REFUSAL_CODE_FIELDS:
        value = answer.get(name)
        if isinstance(value, str) and value:
            return value
    return REFUSED
