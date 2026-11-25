"""Make command text visible while keeping Lean comments and literals opaque."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Literal

from agents_remember.memory_quality.style.citations.lean_names import continues_bare_name

type DocComment = Literal["declaration", "module"]

_WHITESPACE = " \t\r\n"
_DOC_COMMENTS: dict[str, DocComment] = {"/--": "declaration", "/-!": "module"}
_INTERPOLATION_HEADS = ("s!", "m!", "f!", "dbg_trace", "throwError", "println!")
_CHARACTER = r"""'(?:\\(?:[\\\"'rnt]|x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4})|[^\\\n])'"""
_CODE_TOKENS = (
    r'--|/-[!-]?|«|r#*"|"|' + _CHARACTER + "|" + "|".join(map(re.escape, _INTERPOLATION_HEADS))
)
_CODE_EVENT = re.compile(_CODE_TOKENS)
_INTERPOLANT_EVENT = re.compile(_CODE_TOKENS + r"|[{}]")
_COMMENT_EVENT = re.compile(r"/-|-/")
_STRING_EVENT = re.compile(r'\\.|"')
_INTERPOLATION_EVENT = re.compile(r'\\.|"|{')


@dataclass(frozen=True)
class LeanString:
    """Source coordinates of an ordinary or raw string outside interpolation."""

    start_line: int
    start_column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class LeanLine:
    """One physical line, with code columns and lexical continuation state."""

    number: int
    code: str
    indent: int
    significant: bool
    comment: DocComment | None
    starts_in_literal: bool
    starts_in_quoted_name: bool
    ends_in_quoted_name: bool
    strings: tuple[LeanString, ...] = ()


@dataclass
class _Line:
    number: int
    text: str
    significant: bool
    comment: DocComment | None = None
    replacements: list[tuple[int, int, str]] = field(default_factory=list)
    strings: list[LeanString] = field(default_factory=list)

    def mask(self, start: int, end: int, marker: str = "") -> None:
        """Blank a source interval while retaining its columns."""

        if end > start:
            self.replacements.append((start, end, marker))

    def code(self) -> str:
        if not self.replacements:
            return self.text
        chunks: list[str] = []
        offset = 0
        for start, end, marker in self.replacements:
            chunks.extend((self.text[offset:start], marker, " " * (end - start - len(marker))))
            offset = end
        chunks.append(self.text[offset:])
        return "".join(chunks)


@dataclass(frozen=True)
class _String:
    kind: Literal["plain", "raw", "interpolated"]
    closing: str = '"'
    start: tuple[int, int] | None = None


@dataclass
class _Interpolant:
    braces: int = 1


def _within_name(text: str, start: int, end: int) -> bool:
    left = text[start - 1] if start else ""
    if left == "." and start >= 2:
        left = text[start - 2]
    return left == "»" or continues_bare_name(left) or continues_bare_name(text[end : end + 1])


def _resume_after_name_overlap(text: str, event: re.Match[str]) -> int | None:
    if event[0].startswith("'") and _within_name(text, event.start(), event.end()):
        # The apostrophe belongs to the name. The rest of the match may contain
        # the opening quote of an actual character literal.
        return event.start() + 1
    if event[0].startswith("r") and _within_name(text, event.start(), event.end() - 1):
        return event.end() - 1
    return None


class _Lexer:
    def __init__(self) -> None:
        self._comment_depth = 0
        self._quoted_name = False
        self._awaiting_interpolation = False
        self._literals: list[_String | _Interpolant] = []

    def scan(self, number: int, text: str) -> LeanLine:
        starts_in_quoted_name = self._quoted_name and not self._literals
        starts_in_literal = self._quoted_name or bool(self._literals)
        line = _Line(number, text, starts_in_literal and bool(text))
        offset = 0
        while offset < len(text):
            offset = self._advance(line, offset)
        return LeanLine(
            number=number,
            code=line.code(),
            indent=len(text) - len(text.lstrip(_WHITESPACE)),
            significant=line.significant,
            comment=line.comment,
            starts_in_literal=starts_in_literal,
            starts_in_quoted_name=starts_in_quoted_name,
            ends_in_quoted_name=self._quoted_name and not self._literals,
            strings=tuple(line.strings),
        )

    def _advance(self, line: _Line, offset: int) -> int:
        if self._comment_depth:
            return self._block_comment(line, offset)
        if self._quoted_name:
            return self._quoted_part(line, offset)
        literal = self._literals[-1] if self._literals else None
        if isinstance(literal, _String):
            return self._string(line, offset, literal)
        return self._code(line, offset, literal)

    def _block_comment(self, line: _Line, offset: int) -> int:
        for event in _COMMENT_EVENT.finditer(line.text, offset):
            self._comment_depth += 1 if event[0] == "/-" else -1
            if self._comment_depth == 0:
                line.mask(offset, event.end())
                return event.end()
        line.mask(offset, len(line.text))
        return len(line.text)

    def _quoted_part(self, line: _Line, offset: int) -> int:
        closing = line.text.find("»", offset)
        self._quoted_name = closing < 0
        end = len(line.text) if self._quoted_name else closing + 1
        if self._literals:
            line.mask(offset, end)
        return end

    def _string(self, line: _Line, offset: int, literal: _String) -> int:
        if literal.kind == "raw":
            closing = line.text.find(literal.closing, offset)
            end = len(line.text) if closing < 0 else closing + len(literal.closing)
            if closing >= 0:
                self._close_string(line, end, literal)
            line.mask(offset, end)
            return end
        pattern = _INTERPOLATION_EVENT if literal.kind == "interpolated" else _STRING_EVENT
        event = pattern.search(line.text, offset)
        end = event.end() if event else len(line.text)
        line.mask(offset, end)
        if event and event[0] == '"':
            self._close_string(line, end, literal)
        if event and event[0] == "{":
            self._literals.append(_Interpolant())
        return end

    def _close_string(self, line: _Line, end: int, literal: _String) -> None:
        self._literals.pop()
        if literal.start is not None:
            line.strings.append(LeanString(*literal.start, line.number, end))

    def _code(self, line: _Line, offset: int, interpolant: _Interpolant | None) -> int:
        pattern = _INTERPOLANT_EVENT if interpolant else _CODE_EVENT
        event = pattern.search(line.text, offset)
        next_token = event.start() if event else len(line.text)
        if line.text[offset:next_token].strip(_WHITESPACE):
            self._awaiting_interpolation = False
            line.significant = True
        if interpolant:
            line.mask(offset, next_token)
        if event is None:
            return next_token
        if event[0] == "--":
            line.mask(event.start(), len(line.text))
            return len(line.text)
        if event[0].startswith("/-"):
            self._open_block_comment(line, event)
        else:
            return self._code_token(line, event, interpolant)
        return event.end()

    def _open_block_comment(self, line: _Line, event: re.Match[str]) -> None:
        self._comment_depth = 1
        comment = _DOC_COMMENTS.get(event[0])
        if comment is not None and not line.significant and not self._literals:
            line.comment = comment
        line.mask(event.start(), event.end())

    def _code_token(
        self, line: _Line, event: re.Match[str], interpolant: _Interpolant | None
    ) -> int:
        token = event[0]
        line.significant = True
        if token in _INTERPOLATION_HEADS:
            self._awaiting_interpolation = not _within_name(line.text, event.start(), event.end())
            if interpolant:
                line.mask(event.start(), event.end())
            return event.end()
        if (end := _resume_after_name_overlap(line.text, event)) is not None:
            self._awaiting_interpolation = False
            if interpolant:
                line.mask(event.start(), end)
            return end
        if token == "«":
            self._quoted_name = True
            if interpolant:
                line.mask(event.start(), event.end())
        elif interpolant and token in "{}":
            self._interpolant_brace(line, event, interpolant)
        else:
            self._open_literal(line, event, interpolant is not None)
        self._awaiting_interpolation = False
        return event.end()

    def _interpolant_brace(
        self, line: _Line, event: re.Match[str], interpolant: _Interpolant
    ) -> None:
        line.mask(event.start(), event.end())
        interpolant.braces += 1 if event[0] == "{" else -1
        if interpolant.braces == 0:
            self._literals.pop()

    def _open_literal(self, line: _Line, event: re.Match[str], hidden: bool) -> None:
        token = event[0]
        start = None if hidden or self._awaiting_interpolation else (line.number, event.start())
        if token == '"':
            kind = "interpolated" if self._awaiting_interpolation else "plain"
            self._literals.append(_String(kind, start=start))
        elif token.startswith("r"):
            self._literals.append(_String("raw", '"' + token[1:-1], start))
        line.mask(event.start(), event.end(), "" if hidden else '"')


def code_lines(lines: Sequence[str]) -> Iterator[LeanLine]:
    """Read source lines in order, carrying only unfinished lexical state."""

    lexer = _Lexer()
    for number, line in enumerate(lines, 1):
        yield lexer.scan(number, line)
