"""Shared citation model and grammar for tables and prose.

A citation contains one or more anchors and one or more ``path:start-end`` sources. Anchor
kinds are: a backticked identifier (including supported TypeScript call/generic syntax),
a backticked ``#`` heading, or a double-quoted literal. Rows that cite Lean also accept
extended Lean names scoped to the cited Lean files. Identifiers match whole names;
member calls and broken syntax are not reduced to plausible names. Quoted matching
collapses whitespace and removes only leading TypeScript line-comment prefixes.

``L101-L203`` and parenthesized ``(L...)`` forms are superseded. Backticked spans that are
neither identifiers nor headings are unchecked spans, not anchors.

False-positive boundaries for TypeScript are pinned in the grammar suite: member/broken
calls remain unchecked, URLs retain embedded ``//``, and escaped inner quotes remain one
anchor. Markdown span parsing is shared with ``document_shape.inline_scan``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from agents_remember.memory_quality.style.citations import grammars
from agents_remember.memory_quality.style.citations.lean_names import is_lean_name
from agents_remember.memory_quality.style.document_shape import inline_scan
from agents_remember.models.knowledge_files.shapes import require_repository_path

SEGMENT_SEPARATORS = ";·"
# Measured over the memory root: 839 rows mark "nothing to cite" with an em dash, 246 with
# `n/a`, 44 with `N/A`, 8 with a hyphen, and no table mixes two. All four are the empty
# state of a table, not a citation, so all four are read as one. The dashes are written as
# escapes because an en dash reads as a hyphen at a glance.
NO_CITATION_MARKERS = frozenset({"", "-", "\u2013", "\u2014", "n/a"})

IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
HEADING_MARK = "#"
QUOTE_PATTERN = re.compile(r'"((?:\\.|[^"\\])+)"|\u201c((?:\\.|[^\u201d\\])+)\u201d')
SOURCE_PATTERN = re.compile(r"(?P<path>\S+):(?P<start>\d+)(?:-(?P<end>\d+))?")

SYMBOL = "symbol"
HEADING = "heading"
QUOTE = "quote"


class CitationReader(Enum):
    """Language readers for current cards and the byte-pinned legacy converter."""

    LIVE = "live"
    FORMAT_1 = "format-1"

    def grammar_of(self, path: str) -> str | None:
        grammar = grammars.grammar_of(path)
        if self is CitationReader.FORMAT_1 and grammar == grammars.LEAN:
            return None
        return grammar


@dataclass(frozen=True)
class Anchor:
    """One thing a claim asserts the cited lines contain, and how it is matched."""

    kind: str
    text: str
    grammar: str | None = None
    """When set, only files read by this grammar can satisfy the anchor."""


@dataclass(frozen=True)
class Citation:
    """One ``path:start-end``, as written and as numbers."""

    text: str
    path: str
    start: int
    end: int


@dataclass(frozen=True)
class Claim:
    """One citation as parsed, wherever it was written."""

    line: int
    anchors: tuple[Anchor, ...]
    citations: tuple[Citation, ...]
    malformed: tuple[str, ...]
    unchecked_spans: int
    source_paths: tuple[str, ...]
    """Local paths in citation order, including whole-file sources."""


def masked(text: str) -> str:
    """``text`` with every code span blanked, so a scan outside them cannot see in."""
    characters = list(text)
    for start, end in inline_scan.code_span_ranges(text):
        characters[start:end] = " " * (end - start)
    return "".join(characters)


def code_span_texts(text: str) -> list[str]:
    """The contents of each code span, delimiters stripped whatever their run length."""
    found: list[str] = []
    for start, end in inline_scan.code_span_ranges(text):
        run = 0
        while start + run < end and text[start + run] == inline_scan.BACKTICK:
            run += 1
        found.append(text[start + run : end - run].strip())
    return found


def anchors_in(
    text: str, *, cited_grammars: frozenset[str | None] = frozenset()
) -> tuple[tuple[Anchor, ...], int]:
    """``(anchors, count of backticked spans that are not anchors)``."""
    found: list[Anchor] = []
    skipped = 0
    for span in code_span_texts(text):
        if IDENTIFIER_PATTERN.fullmatch(span):
            found.append(Anchor(kind=SYMBOL, text=span))
        elif span.startswith(HEADING_MARK):
            found.append(Anchor(kind=HEADING, text=span))
        elif grammars.LEAN in cited_grammars and is_lean_name(span):
            found.append(Anchor(kind=SYMBOL, text=span, grammar=grammars.LEAN))
        else:
            identifier = grammars.typescript_anchor_identifier(span)
            if identifier is None:
                skipped += 1
            else:
                found.append(Anchor(kind=SYMBOL, text=identifier))
    for match in QUOTE_PATTERN.finditer(masked(text)):
        raw = (match.group(1) or match.group(2)).strip()
        found.append(Anchor(kind=QUOTE, text=unescape_quote(raw)))
    return tuple(dict.fromkeys(found)), skipped


def unescape_quote(text: str) -> str:
    """Unescape only quote-grammar escapes; leave paths and ``\n``-like text literal."""
    return re.sub(r'\\([\\"\u201d])', r"\1", text)


def split_segments(text: str) -> list[str]:
    """The segments of a source list, splitting only on separators OUTSIDE a code span."""
    spans = inline_scan.code_span_ranges(text)
    pieces: list[str] = []
    previous = 0
    index = 0
    while index < len(text):
        span_end = inline_scan.enclosing_span_end(index, spans)
        if span_end is not None:
            index = span_end
            continue
        if text[index] in SEGMENT_SEPARATORS:
            pieces.append(text[previous:index])
            previous = index + 1
        index += 1
    pieces.append(text[previous:])
    return pieces


def unwrapped(piece: str) -> str:
    """``piece`` with an enclosing code span removed, if it is entirely one.

    Backticks around a source are tolerated because they change nothing about what the
    citation denotes. Nothing else is: a markdown link, an absolute path or a ``../`` step
    is a source in the superseded format and is reported as malformed.
    """
    stripped = piece.strip()
    spans = inline_scan.code_span_ranges(stripped)
    if len(spans) == 1 and spans[0] == (0, len(stripped)):
        return code_span_texts(stripped)[0]
    return stripped


def repo_relative(path: str) -> bool:
    return not path.startswith("/") and ".." not in path.split("/") and "://" not in path


def local_path(value: str) -> str | None:
    """A valid repository path, as written, or no path."""
    try:
        return require_repository_path(value)
    except ValueError:
        return None


def citations_in(text: str) -> tuple[Citation | str, ...]:
    """Ranged citations and other source segments in the order the author wrote them."""
    found: list[Citation | str] = []
    for piece in split_segments(text):
        written = unwrapped(piece)
        if written.lower() in NO_CITATION_MARKERS:
            continue
        match = SOURCE_PATTERN.fullmatch(written)
        if match is None or not repo_relative(match.group("path")):
            found.append(written)
            continue
        start = int(match.group("start"))
        end = int(match.group("end") or match.group("start"))
        found.append(
            Citation(text=written, path=match.group("path"), start=start, end=max(start, end))
        )
    return tuple(found)
