"""Read Lean identifiers while retaining the spelling of each name component."""

from __future__ import annotations

import re
from dataclasses import dataclass

# These are Lean's isLetterLike and isSubScriptAlnum ranges. The gaps exclude
# the multiplication and division signs, lowercase lambda, and uppercase Pi and Sigma.
_LETTER_LIKE = (
    r"\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u00ff\u0100-\u017f"
    r"\u0391-\u039f\u03a1-\u03a2\u03a4-\u03a9"
    r"\u03b1-\u03ba\u03bc-\u03c9\u03ca-\u03fb\u1f00-\u1ffe"
    r"\u2100-\u214f\U0001d49c-\U0001d59f"
)
_SUBSCRIPTS = r"\u2080-\u2089\u2090-\u209c\u1d62-\u1d6a\u2c7c"
_FIRST = rf"A-Za-z_{_LETTER_LIKE}"
_REST = rf"{_FIRST}0-9'!?{_SUBSCRIPTS}"
_BARE_PART = re.compile(rf"[{_FIRST}][{_REST}]*")
_PART_HEAD = re.compile(rf"[«{_FIRST}]")
_BARE_CONTINUATION = re.compile(rf"[{_REST}]")


@dataclass(frozen=True)
class LeanName:
    """A name's written parts and its exclusive end in the input text."""

    parts: tuple[str, ...]
    end: int

    @property
    def text(self) -> str:
        return ".".join(self.parts)


def continues_bare_name(character: str) -> bool:
    return _BARE_CONTINUATION.fullmatch(character) is not None


def _part_end(text: str, start: int) -> int | None:
    if text[start : start + 1] == "«":
        closing = text.find("»", start + 1)
        return closing + 1 if closing >= 0 else None
    part = _BARE_PART.match(text, start)
    return part.end() if part else None


def read_name(text: str, start: int = 0) -> LeanName | None:
    """Read a name from a source offset, leaving any universe suffix unconsumed."""

    parts: list[str] = []
    while True:
        end = _part_end(text, start)
        if end is None:
            return None
        parts.append(text[start:end])
        if text[end : end + 1] != "." or _PART_HEAD.match(text, end + 1) is None:
            return LeanName(tuple(parts), end)
        start = end + 1


def is_lean_name(text: str) -> bool:
    """Test whether a complete span is a Lean name."""

    name = read_name(text)
    return name is not None and name.end == len(text)
