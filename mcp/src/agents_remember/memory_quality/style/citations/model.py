"""Shared citation model and grammar for tables and prose.

A citation contains one or more anchors and one or more ``path:start-end`` sources. Anchor
kinds are: a backticked identifier (including supported TypeScript call/generic syntax),
a backticked ``#`` heading, or a double-quoted literal. Identifiers match whole names;
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
from pathlib import Path, PurePosixPath

from agents_remember.memory_quality.style.citations import grammars
from agents_remember.memory_quality.style.document_shape import inline_scan

SEGMENT_SEPARATORS = ";·"
# Measured over the memory root: 839 rows mark "nothing to cite" with an em dash, 246 with
# `n/a`, 44 with `N/A`, 8 with a hyphen, and no table mixes two. All four are the empty
# state of a table, not a citation, so all four are read as one. The dashes are written as
# escapes because an en dash reads as a hyphen at a glance.
NO_CITATION_MARKERS = frozenset({"", "-", "\u2013", "\u2014", "n/a"})

IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
HEADING_MARK = "#"
QUOTE_PAIRS = {'"': '"', "\u201c": "\u201d"}
QUOTE_PATTERN = re.compile(r'"((?:\\.|[^"\\])+)"|\u201c((?:\\.|[^\u201d\\])+)\u201d')
SOURCE_PATTERN = re.compile(r"(?P<path>\S+):(?P<start>\d+)(?:-(?P<end>\d+))?")

SYMBOL = "symbol"
HEADING = "heading"
QUOTE = "quote"


@dataclass(frozen=True)
class Anchor:
    """One thing a claim asserts the cited lines contain, and how it is matched."""

    kind: str
    text: str

    @property
    def written(self) -> str:
        if self.kind == QUOTE:
            escaped = self.text.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        return f"`{self.text}`"


def documents_in(onboarding_root: Path, only: str | None = None) -> list[Path]:
    """Every document to walk, or just the one named -- refusing a name that matches nothing.

    The scope exists because a curator wave shares one memory worktree: a tree-wide ``--fix``
    rewrites documents anywhere in it, so one curator's run can rewrite another's document
    mid-edit. Scoping the DOCUMENT set is the fix; repository-wide anchor completeness comes
    from the shared immutable source index rather than another source-tree parse.

    An unmatched name RAISES rather than yielding an empty walk. A filter that matches
    nothing and then reports clean is the defect this master found six times over -- a gate
    reporting success over a scope nobody stated.
    """
    if only is None:
        return sorted(path for path in onboarding_root.rglob("*.md") if path.is_file())
    relative = PurePosixPath(only)
    if (
        not only
        or relative.is_absolute()
        or relative.suffix != ".md"
        or relative.as_posix() != only
        or any(part in {"", ".", ".."} for part in only.split("/"))
    ):
        raise ValueError(
            f"--document {only!r} must be one canonical relative .md path under {onboarding_root}"
        )
    root = onboarding_root.resolve()
    lexical = root.joinpath(*relative.parts)
    try:
        selected = lexical.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError(
            f"--document {only!r} names no document under {onboarding_root}"
        ) from error
    if selected != lexical or root not in selected.parents or not selected.is_file():
        raise ValueError(
            f"--document {only!r} must name one regular canonical document confined under "
            f"{onboarding_root}"
        )
    return [selected]


def normalised(text: str) -> str:
    """``text`` with every run of whitespace collapsed, so a wrapped quote still matches."""
    return " ".join(text.split())


def quote_normalised(text: str) -> str:
    """Quoted-anchor source with only a leading TypeScript line-comment mark removed."""
    lines = [re.sub(r"^\s*//\s?", "", line) for line in text.splitlines()]
    return normalised("\n".join(lines))


def whole_identifier(symbol: str) -> re.Pattern[str]:
    """``symbol`` as a complete identifier -- ``SERVED`` never inside ``SERVED_LIFECYCLE``."""
    return re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(symbol)}(?![A-Za-z0-9_$])")


def occurs_in(anchor: Anchor, body: str) -> bool:
    """Whether the anchor's text is inside ``body``, by the rule its kind implies.

    The one rule the whole check turns on, so it lives beside the grammar it implements:
    the range generator has to satisfy exactly what the checker tests for, and two copies
    of "does this range hold this anchor" would eventually disagree.
    """
    if anchor.kind == SYMBOL:
        return whole_identifier(anchor.text).search(body) is not None
    if anchor.kind == HEADING:
        return any(line.strip() == anchor.text for line in body.splitlines())
    return normalised(anchor.text) in quote_normalised(body)


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


def anchors_in(text: str) -> tuple[tuple[Anchor, ...], int]:
    """``(anchors, count of backticked spans that are not anchors)``."""
    found: list[Anchor] = []
    skipped = 0
    for span in code_span_texts(text):
        if IDENTIFIER_PATTERN.fullmatch(span):
            found.append(Anchor(kind=SYMBOL, text=span))
        elif span.startswith(HEADING_MARK):
            found.append(Anchor(kind=HEADING, text=span))
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


def citations_in(text: str) -> tuple[tuple[Citation, ...], tuple[str, ...]]:
    """``(citations, segments that are not a repo-relative ``path:start-end``)``."""
    found: list[Citation] = []
    malformed: list[str] = []
    for piece in split_segments(text):
        written = unwrapped(piece)
        if written.lower() in NO_CITATION_MARKERS:
            continue
        match = SOURCE_PATTERN.fullmatch(written)
        if match is None or not repo_relative(match.group("path")):
            malformed.append(written)
            continue
        start = int(match.group("start"))
        end = int(match.group("end") or match.group("start"))
        found.append(
            Citation(text=written, path=match.group("path"), start=start, end=max(start, end))
        )
    return tuple(found), tuple(malformed)


def skip_quoted(text: str, index: int) -> int:
    """The index just past the quoted literal opening at ``index``, or just past the mark."""
    closing = QUOTE_PAIRS[text[index]]
    cursor = index + 1
    while cursor < len(text):
        if text[cursor] == "\\":
            cursor += 2
            continue
        if text[cursor] == closing:
            return cursor + 1
        cursor += 1
    return index + 1


def matching(text: str, opener: int, spans: list[tuple[int, int]], closer: str) -> int | None:
    """The index of the bracket closing the one at ``opener``, or ``None``.

    Code spans and quoted literals are stepped over whole, so a bracket inside
    ``"exit(1)"`` or inside ``` `f(x)` ``` does not change the depth.
    """
    depth = 0
    index = opener
    while index < len(text):
        span_end = inline_scan.enclosing_span_end(index, spans)
        if span_end is not None:
            index = span_end
            continue
        character = text[index]
        if character in QUOTE_PAIRS:
            index = skip_quoted(text, index)
            continue
        if character == text[opener]:
            depth += 1
        elif character == closer:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None
