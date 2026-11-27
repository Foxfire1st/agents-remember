"""A rule's short name.

A rule written with a ``title`` is named by it. A rule without one -- every rule written before the
title existed, and a new revision of such a rule -- is named by a readable first-clause fallback of
its statement (up to ~80 characters), marked as derived, until a later write gives it a title. No
record is changed for this.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from agents_remember.application.file_briefs.model import RuleTitle

FALLBACK_MAX_CHARS: Final = 80
_MIN_CLAUSE_CHARS: Final = 20
_CUT: Final = " …"
_SENTENCE_SEPARATORS: Final = (". ", "? ", "! ")
_CLAUSE_SEPARATORS: Final = ("; ", ": ", " — ", " -- ", ", ")


def clause_title(statement: str, *, max_chars: int = FALLBACK_MAX_CHARS) -> str:
    """Derive a readable first-clause title within ``max_chars`` without mutating the record."""

    text = " ".join(statement.split())
    if len(text) <= max_chars:
        return text
    sentence_cut = _first_boundary(text, _SENTENCE_SEPARATORS, max_chars=max_chars)
    if sentence_cut is not None:
        return text[:sentence_cut].rstrip()
    clause_cut = _last_boundary(text, _CLAUSE_SEPARATORS, max_chars=max_chars)
    if clause_cut is not None:
        return text[:clause_cut].rstrip() + _CUT
    space_cut = text.rfind(" ", 0, max_chars + 1)
    if space_cut > 0:
        return text[:space_cut].rstrip(",;:") + _CUT
    return text[:max_chars] + _CUT


def _first_boundary(text: str, separators: tuple[str, ...], *, max_chars: int) -> int | None:
    best: int | None = None
    for sep in separators:
        idx = text.find(sep, _MIN_CLAUSE_CHARS, max_chars + 1)
        if idx != -1 and (best is None or idx < best):
            best = idx
    return best


def _last_boundary(text: str, separators: tuple[str, ...], *, max_chars: int) -> int | None:
    best: int | None = None
    for sep in separators:
        idx = text.rfind(sep, _MIN_CLAUSE_CHARS, max_chars + 1)
        if idx != -1 and (best is None or idx > best):
            best = idx
    return best


def rule_title(document: Mapping[str, Any]) -> RuleTitle:
    """The recorded title, else a readable first-clause fallback from the statement."""

    title = document.get("title")
    if isinstance(title, str) and title.strip():
        return RuleTitle(text=" ".join(title.split()), given=True)
    return RuleTitle(text=clause_title(str(document.get("statement") or "")), given=False)


__all__ = ["FALLBACK_MAX_CHARS", "clause_title", "rule_title"]
