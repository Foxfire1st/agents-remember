"""Case-insensitive id-or-label matching against a supplied choice catalog."""

from __future__ import annotations

from typing import Any


def named(rows: Any, value: str) -> dict[str, Any] | None:
    folded = value.casefold()
    for key in ("id", "label"):
        match = next((row for row in rows if str(row.get(key, "")).casefold() == folded), None)
        if match is not None:
            return match
    return None
