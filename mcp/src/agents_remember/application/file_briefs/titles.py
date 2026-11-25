"""A rule's short name.

A rule written with a ``title`` is named by it. A rule without one -- every rule written before the
title existed, and a new revision of such a rule -- is named by the first six words of its
statement, marked as derived, until a later write gives it a title. No record is changed for this.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from agents_remember.application.file_briefs.model import RuleTitle

FALLBACK_WORDS: Final = 6
_CUT: Final = " …"


def rule_title(document: Mapping[str, Any]) -> RuleTitle:
    """The recorded title, else the statement's first six words followed by an ellipsis."""

    title = document.get("title")
    if isinstance(title, str) and title.strip():
        return RuleTitle(text=" ".join(title.split()), given=True)
    words = str(document.get("statement") or "").split()
    text = " ".join(words[:FALLBACK_WORDS])
    if len(words) > FALLBACK_WORDS:
        text += _CUT
    return RuleTitle(text=text, given=False)


__all__ = ["FALLBACK_WORDS", "rule_title"]
