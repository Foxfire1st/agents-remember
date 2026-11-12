"""Read newly authored history items without dropping or normalizing tokens."""

from __future__ import annotations

from typing import Any


def read_row_items(value: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return verbatim tokens and refusal messages for the handoff's item field."""

    if value is None:
        return (), ()
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list):
        return (), (f"'items' is a list of worklist item IDs, not {value!r}",)
    items: list[str] = []
    refusals: list[str] = []
    for item in values:
        if not isinstance(item, str):
            refusals.append(f"invalid item {item!r}: a worklist item ID is text")
            continue
        items.append(item)
        if not item.strip():
            refusals.append(
                f"invalid item {item!r}: a worklist item ID is not blank; "
                "name the worklist's item ID instead"
            )
    return tuple(items), tuple(refusals)
