"""Find the ``[n]`` reference markers of a Markdown file (MIK-R21 rule 6).

A marker is an unescaped ``[`` digits ``]`` outside code. Code is a fenced block (a line opening with
three or more backticks or tildes, indented at most three spaces, up to the closing fence of the same
character and at least the same length) or an inline code span (a run of backticks up to the next
run of the same length within the same paragraph). A ``[`` preceded by an odd number of backslashes
is escaped. So ``signals[0]`` in prose is a marker, and ``\\[0]`` or `` `signals[0]` `` is not.

A marker whose digits are not a reference number (``[0]``, ``[01]``) is still a marker: it can never
have a reference, so the validator reports it and names the two ways to write it as text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from agents_remember.models.knowledge_files.shapes import REFERENCE_NUMBER_PATTERN

_FENCE: Final = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_MARKER: Final = re.compile(r"\[([0-9]+)\]")
_BACKTICKS: Final = re.compile(r"`+")
_REFERENCE_NUMBER: Final = re.compile(REFERENCE_NUMBER_PATTERN)


@dataclass(frozen=True)
class Marker:
    number: str
    line: int

    @property
    def valid(self) -> bool:
        return _REFERENCE_NUMBER.match(self.number) is not None


def _closes(fence: str, opening: re.Match[str] | None, line: str) -> bool:
    """A closing fence repeats the opening character at least as often, and nothing else."""

    return (
        opening is not None
        and opening.group(1)[0] == fence[0]
        and len(opening.group(1)) >= len(fence)
        and not line.strip().lstrip(fence[0])
    )


def _paragraphs(text: str) -> list[list[tuple[int, str]]]:
    """Split ``text`` into paragraphs of (line number, line) outside fenced code blocks."""

    paragraphs: list[list[tuple[int, str]]] = [[]]
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        opening = _FENCE.match(line)
        if fence is not None:
            if _closes(fence, opening, line):
                fence = None
            continue
        if opening:
            fence = opening.group(1)
            paragraphs.append([])
            continue
        if not line.strip():
            paragraphs.append([])
            continue
        paragraphs[-1].append((number, line))
    return [paragraph for paragraph in paragraphs if paragraph]


def _prose_spans(joined: str) -> list[tuple[int, int]]:
    """Return the (start, end) spans of ``joined`` that are outside inline code."""

    spans: list[tuple[int, int]] = []
    position = 0
    runs = list(_BACKTICKS.finditer(joined))
    index = 0
    while index < len(runs):
        opening = runs[index]
        closing = next(
            (later for later in runs[index + 1 :] if len(later.group(0)) == len(opening.group(0))),
            None,
        )
        if closing is None:
            index += 1
            continue
        spans.append((position, opening.start()))
        position = closing.end()
        index = runs.index(closing) + 1
    spans.append((position, len(joined)))
    return spans


def _escaped(text: str, bracket: int) -> bool:
    backslashes = 0
    position = bracket - 1
    while position >= 0 and text[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def find_markers(text: str) -> list[Marker]:
    """Return every marker of the Markdown ``text``, in order."""

    markers: list[Marker] = []
    for paragraph in _paragraphs(text):
        joined = "\n".join(line for _, line in paragraph)
        starts = []
        offset = 0
        for number, line in paragraph:
            starts.append((offset, number))
            offset += len(line) + 1
        for start, end in _prose_spans(joined):
            for match in _MARKER.finditer(joined, start, end):
                if _escaped(joined, match.start()):
                    continue
                line = next(number for begin, number in reversed(starts) if begin <= match.start())
                markers.append(Marker(match.group(1), line))
    return markers
