"""A rule's title at the writer: required for a new rule, noted when absent.

Every rule has a title, a short name of at most
:data:`~agents_remember.models.knowledge_files.shapes.INVARIANT_TITLE_MAX_LENGTH` characters on one
line. A new rule is refused without one, and the refusal names the field and its bound. A rule
written before titles existed keeps none: a new revision of it is taken without a title, and the
write's notes say that the rule has none, so the author can name it in a later write. No stored
record is changed for this.

The title is a name and no part of the rule's meaning, so a write that changes it alone raises no
revision (:func:`~agents_remember.models.knowledge_files.history_product.record_meaning`).
The record is still written with the
new title at its unchanged revision.

What this does not cover: a rule's display name where it has no title (the first words of its
statement) belongs to the readers that show it, and whether a title names the rule well is the
author's and the reviewer's judgment, not a mechanical check.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from pydantic import TypeAdapter, ValidationError

from agents_remember.models.knowledge_files.shapes import (
    INVARIANT_TITLE_MAX_LENGTH,
    InvariantTitle,
)

TITLE_FIELD: Final = "title"
# The record's own shape, so the writer refuses exactly what the record model would.
_TITLE: Final = TypeAdapter(InvariantTitle)
_SHOWN_MAX: Final = 100
_BOUND: Final = f"a name of at most {INVARIANT_TITLE_MAX_LENGTH} characters on one line"
MISSING_TITLE: Final = (
    f"a new rule needs a `{TITLE_FIELD}`: {_BOUND}; add it beside the rule's statement"
)


def title_problem(value: Any) -> str | None:
    """Why ``value`` is not a title, or ``None`` when it is one."""

    try:
        _TITLE.validate_python(value, strict=True)
    except ValidationError:
        shown = repr(value)
        shown = (
            shown
            if len(shown) <= _SHOWN_MAX
            else f"{shown[:_SHOWN_MAX]}… ({len(shown)} characters)"
        )
        return f"`{TITLE_FIELD}` is {_BOUND}, without surrounding spaces; got {shown}"
    return None


def read_title(raw: Mapping[str, Any], *, new_rule: bool) -> tuple[str | None, str | None]:
    """The authored title of a rule and the problem with it, if any.

    A new rule without a title is refused (:data:`MISSING_TITLE`); an existing rule may be revised
    without one.
    """

    value = raw.get(TITLE_FIELD)
    if value is None:
        return None, MISSING_TITLE if new_rule else None
    problem = title_problem(value)
    return (None, problem) if problem is not None else (value, None)


def record_title_problems(
    kind: str, record_id: str | None, fields: Mapping[str, Any]
) -> tuple[str, ...]:
    """The title problems of a ``records`` section row: none for a kind other than an invariant."""

    if kind != "invariant":
        return ()
    problem = read_title(fields, new_rule=record_id is None)[1]
    return () if problem is None else (problem,)


def untitled_note(document: Mapping[str, Any]) -> str | None:
    """The write's note for a revised rule that has no title, or ``None`` when it has one."""

    if document.get(TITLE_FIELD) is not None:
        return None
    return (
        f"{document.get('id')} has no title: it is shown by the first words of its statement "
        f"until a write names it with `{TITLE_FIELD}` ({_BOUND})"
    )


__all__ = [
    "MISSING_TITLE",
    "TITLE_FIELD",
    "read_title",
    "record_title_problems",
    "title_problem",
    "untitled_note",
]
