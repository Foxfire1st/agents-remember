"""The one canonical formatting of every knowledge JSON file (MIK-R21 rule 8).

Canonical text is: UTF-8; two-space indentation; object keys sorted; arrays of identified entries
sorted by ``id``; one trailing newline. An *array of identified entries* is an array held under one
of the schema positions in :data:`IDENTIFIED_ENTRY_KEYS` (``realizes``, ``proves``, ``rows``) whose
every element is an object with a string ``id``. Every other array keeps its authored order: a
reference's ``targets`` are ordered by the author even though ID targets carry an ``id``, and a
decision's ``alternatives`` are addressed by index, so their order is content.

The formatter changes formatting only. It refuses input it cannot reformat without changing
content: invalid UTF-8 or JSON, a byte-order mark, ``NaN``/``Infinity``, and an object that
repeats a key (a plain JSON parser would silently keep the last value). ``agents-remember
knowledge-format`` is the command that applies it; the validator (MIK-R22) reports a file that is
not canonical and names that command.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Final

FORMATTER_COMMAND: Final = "agents-remember knowledge-format"

# The schema positions that hold identified entries: a file sidecar's realization and proof entries,
# and a history file's rows (MIK-R07). An array is sorted only in one of these positions.
IDENTIFIED_ENTRY_KEYS: Final = frozenset({"realizes", "proves", "rows"})


class CanonicalFormatError(ValueError):
    """The input is not a JSON document the formatter may reformat."""


def _refuse_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    counts = Counter(key for key, _ in pairs)
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    if duplicates:
        raise CanonicalFormatError(f"object repeats key(s): {duplicates}")
    return dict(pairs)


def _refuse_constant(name: str) -> Any:
    raise CanonicalFormatError(f"{name} is not a JSON value")


def parse_json(text: str) -> Any:
    """Parse ``text`` strictly: no duplicate keys, no ``NaN``/``Infinity``, no byte-order mark."""

    if text.startswith("﻿"):
        raise CanonicalFormatError("a knowledge file must not start with a byte-order mark")
    try:
        return json.loads(
            text, object_pairs_hook=_refuse_duplicate_keys, parse_constant=_refuse_constant
        )
    except json.JSONDecodeError as error:
        raise CanonicalFormatError(f"invalid JSON: {error}") from error


def _is_identified_entry_array(value: list[Any]) -> bool:
    return bool(value) and all(
        isinstance(item, dict) and isinstance(item.get("id"), str) for item in value
    )


def canonical_value(value: Any, *, key: str | None = None) -> Any:
    """Return ``value`` with each identified-entry array sorted by ``id``, recursively.

    Only an array held under one of :data:`IDENTIFIED_ENTRY_KEYS` whose every element carries a
    string ``id`` is sorted. Every other array keeps its authored order, including a reference's
    ``targets`` (whose ID targets also carry ``id``) and a decision's ``alternatives``.
    """

    if isinstance(value, dict):
        return {name: canonical_value(item, key=name) for name, item in value.items()}
    if isinstance(value, list):
        items = [canonical_value(item) for item in value]
        if key in IDENTIFIED_ENTRY_KEYS and _is_identified_entry_array(items):
            items.sort(key=lambda item: item["id"])
        return items
    return value


def canonical_text(value: Any) -> str:
    """Serialize a JSON value in the canonical formatting."""

    rendered = json.dumps(
        canonical_value(value), indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False
    )
    return f"{rendered}\n"


def format_text(text: str) -> str:
    """Return the canonical formatting of the JSON document ``text``."""

    return canonical_text(parse_json(text))


def format_bytes(data: bytes) -> bytes:
    """Return the canonical formatting of the UTF-8 JSON document ``data``."""

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CanonicalFormatError(f"a knowledge file must be UTF-8: {error}") from error
    return format_text(text).encode("utf-8")


def is_canonical(data: bytes) -> bool:
    """Answer whether ``data`` is already canonically formatted (refuses unparseable input)."""

    return format_bytes(data) == data
