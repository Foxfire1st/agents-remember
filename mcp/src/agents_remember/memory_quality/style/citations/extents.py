"""Generate citation ranges for anchors inside one file.

Identifier anchors use binding extents, headings run to the next equal/higher heading, and
quoted literals use their occupied lines. Direct JavaScript/TypeScript string arguments
widen to the enclosing call. Every candidate is returned so the resolver can refuse
ambiguity.

Parsed-language ownership comes from ``grammars``. Other suffixes use occurrence ranges;
a mention is then indistinguishable from a declaration, so occurrence matching cannot
prove a pure move.

Use ``FileView`` for batches so parsing, words, and heading levels are derived once per
file. TypeScript false-positive boundaries: strip ``//`` only at line starts, widen only a
direct call argument, and match the exact byte occurrence so identical callback,
assignment, comment, and same-line strings remain separate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agents_remember.memory_quality.style.citations import grammars, model
from agents_remember.memory_quality.style.document_shape import inline_scan

DEFINITION = "definition"
OCCURRENCE = "occurrence"
SECTION = "section"
QUOTED = "quoted"
CALL = "call"

HEADING_LINE = re.compile(r"^(#{1,6})\s")

# Re-exported so nothing outside :mod:`grammars` has to know a suffix to ask whether a
# definition in a file is distinguishable from a mention of it.
parsed = grammars.parsed


@dataclass(frozen=True)
class Extent:
    """One range an anchor occupies in a file, and how it was found."""

    start: int
    end: int
    kind: str

    def holds(self, start: int, end: int) -> bool:
        """Whether this extent overlaps the range ``start``-``end`` at all."""
        return self.start <= end and start <= self.end


@dataclass(frozen=True)
class WordMark:
    """One collapsed word and the byte position it retains from the source."""

    collapsed_start: int
    collapsed_end: int
    line: int
    source_byte_start: int
    text: str


@dataclass(frozen=True)
class CollapsedText:
    """Whitespace-collapsed text whose word marks still identify source bytes."""

    text: str
    marks: tuple[WordMark, ...]


@dataclass(frozen=True)
class QuoteMatch:
    """One exact quote occurrence before line-range rendering merges equal extents."""

    start: int
    end: int
    source_byte_start: int
    source_byte_end: int

    @property
    def extent(self) -> Extent:
        return Extent(start=self.start, end=self.end, kind=QUOTED)


def anchor_extents(anchor: model.Anchor, path: str, lines: list[str]) -> tuple[Extent, ...]:
    """Every range in ``lines`` that satisfies ``anchor``, by the rule its kind implies."""
    if anchor.kind == model.SYMBOL:
        return symbol_extents(anchor.text, path, lines)
    if anchor.kind == model.HEADING:
        return heading_extents(anchor.text, lines)
    return quote_extents_for_path(anchor.text, path, lines)


def symbol_extents(name: str, path: str, lines: list[str]) -> tuple[Extent, ...]:
    """The constructs binding ``name``, or -- failing that -- the lines that mention it."""
    bound = definitions(path, lines).get(name, ())
    if bound:
        return tuple(bound)
    return occurrence_runs(model.whole_identifier(name), lines)


def definitions(path: str, lines: list[str]) -> dict[str, list[Extent]]:
    """Every name this file binds at any depth, and the extent of the construct binding it.

    Assignments and declarators count, not only ``def`` and ``class``: an earlier
    measurement of this surface collected definitions only and read badly, because these
    documents cite module-level constants constantly. A file in a language with no grammar,
    and a construct too broken to parse, bind nothing and drop to occurrence matching
    rather than failing the run -- the tree holds source written for other dialects and
    none of it is this check's business. A grammar that will not LOAD is the other case
    entirely and raises; see :mod:`grammars`.
    """
    return {
        name: [Extent(start=start, end=end, kind=DEFINITION) for start, end in spans]
        for name, spans in grammars.definitions(path, lines).items()
    }


def occurrence_runs(pattern: re.Pattern[str], lines: list[str]) -> tuple[Extent, ...]:
    """Consecutive lines holding the pattern, grouped -- two mentions ten lines apart are
    two ranges, because one range spanning them would quote eight lines that say nothing."""
    hits = [index + 1 for index, line in enumerate(lines) if pattern.search(line)]
    found: list[Extent] = []
    for line in hits:
        if found and found[-1].end == line - 1:
            found[-1] = Extent(start=found[-1].start, end=line, kind=OCCURRENCE)
            continue
        found.append(Extent(start=line, end=line, kind=OCCURRENCE))
    return tuple(found)


def heading_extents(heading: str, lines: list[str]) -> tuple[Extent, ...]:
    """The section a heading opens: its own line to the line before the next heading of
    equal or higher level, or to the end of the document."""
    return heading_extents_in(heading, lines, heading_levels(inline_scan.unfenced_lines(lines)))


def heading_extents_in(
    heading: str, lines: list[str], levels: dict[int, int]
) -> tuple[Extent, ...]:
    """:func:`heading_extents` with the file's heading levels already derived."""
    opening = len(heading) - len(heading.lstrip("#"))
    found: list[Extent] = []
    for index in sorted(levels):
        if lines[index].strip() != heading:
            continue
        closing = next(
            (later for later in sorted(levels) if later > index and levels[later] <= opening),
            len(lines),
        )
        found.append(Extent(start=index + 1, end=closing, kind=SECTION))
    return tuple(found)


def heading_levels(unfenced: list[tuple[int, str]]) -> dict[int, int]:
    """Each unfenced heading line's index and its ``#`` depth."""
    matched = ((index, HEADING_LINE.match(line.strip())) for index, line in unfenced)
    return {index: len(match.group(1)) for index, match in matched if match is not None}


def quote_extents(quote: str, lines: list[str]) -> tuple[Extent, ...]:
    """The lines a quoted literal occupies, matched with whitespace collapsed so a source
    that wraps the sentence still yields the window that holds it.

    An EMPTY quote is satisfied by every range and so is never a finding; returning nothing
    for it keeps the search from matching at every offset in the file.
    """
    return quote_match_extents(
        all_quote_matches(quote, collapsed(lines), line_comment_blocks(lines))
    )


def quote_extents_for_path(quote: str, path: str, lines: list[str]) -> tuple[Extent, ...]:
    """Quoted extents with parsed-language call-argument widening."""
    matches = all_quote_matches(quote, collapsed(lines), line_comment_blocks(lines))
    return widened_quotes(quote, matches, grammars.call_argument_literals(path, lines))


def all_quote_matches(
    quote: str,
    words: CollapsedText,
    comments: tuple[CollapsedText, ...],
) -> tuple[QuoteMatch, ...]:
    found = list(quote_matches_in(quote, words))
    for block in comments:
        found.extend(quote_matches_in(quote, block))
    return tuple(
        dict.fromkeys(
            sorted(
                found,
                key=lambda one: (
                    one.source_byte_start,
                    one.source_byte_end,
                    one.start,
                    one.end,
                ),
            )
        )
    )


def quote_match_extents(matches: tuple[QuoteMatch, ...]) -> tuple[Extent, ...]:
    """Render exact occurrences as the unique line extents the citation format stores."""
    return tuple(dict.fromkeys(match.extent for match in matches))


def widened_quotes(
    quote: str,
    matches: tuple[QuoteMatch, ...],
    calls: tuple[grammars.CallLiteral, ...],
) -> tuple[Extent, ...]:
    widened: list[Extent] = []
    covered: set[tuple[int, int]] = set()
    anchor = model.Anchor(kind=model.QUOTE, text=quote)
    for call in calls:
        if not model.occurs_in(anchor, call.text):
            continue
        widened.append(Extent(start=call.start, end=call.end, kind=CALL))
        covered.update(
            (match.source_byte_start, match.source_byte_end)
            for match in matches
            if call.argument_start_byte <= match.source_byte_start
            and match.source_byte_end <= call.argument_end_byte
        )
    widened.extend(
        match.extent
        for match in matches
        if (match.source_byte_start, match.source_byte_end) not in covered
    )
    return tuple(dict.fromkeys(sorted(widened, key=lambda one: (one.start, one.end, one.kind))))


def quote_matches_in(quote: str, stream: CollapsedText) -> tuple[QuoteMatch, ...]:
    """Exact occurrences in one collapsed stream, with source-byte identity retained."""
    target = model.normalised(quote)
    if not target:
        return ()
    found: list[QuoteMatch] = []
    at = stream.text.find(target)
    while at >= 0:
        end = at + len(target)
        first = word_mark_at(stream.marks, at)
        last = word_mark_at(stream.marks, end - 1)
        found.append(
            QuoteMatch(
                start=first.line,
                end=last.line,
                source_byte_start=(
                    first.source_byte_start
                    + len(first.text[: at - first.collapsed_start].encode("utf-8"))
                ),
                source_byte_end=(
                    last.source_byte_start
                    + len(last.text[: end - last.collapsed_start].encode("utf-8"))
                ),
            )
        )
        at = stream.text.find(target, at + 1)
    return tuple(found)


def collapsed(lines: list[str]) -> CollapsedText:
    """The file as whitespace-collapsed text with each word's line and byte position."""
    words: list[str] = []
    marks: list[WordMark] = []
    width = 0
    line_starts = source_line_starts(lines)
    for index, line in enumerate(lines):
        for matched in re.finditer(r"\S+", line):
            word = matched.group()
            marks.append(
                WordMark(
                    collapsed_start=width,
                    collapsed_end=width + len(word),
                    line=index + 1,
                    source_byte_start=(
                        line_starts[index] + len(line[: matched.start()].encode("utf-8"))
                    ),
                    text=word,
                )
            )
            words.append(word)
            width += len(word) + 1
    return CollapsedText(" ".join(words), tuple(marks))


def line_comment_blocks(
    lines: list[str],
) -> tuple[CollapsedText, ...]:
    """Contiguous ``//`` blocks with syntax prefixes removed and source lines retained."""
    found: list[CollapsedText] = []
    words: list[str] = []
    marks: list[WordMark] = []
    width = 0
    line_starts = source_line_starts(lines)
    for index, line in enumerate(lines):
        matched = re.match(r"^\s*//\s?", line)
        if matched is None:
            if words:
                found.append(CollapsedText(" ".join(words), tuple(marks)))
            words, marks, width = [], [], 0
            continue
        for word_match in re.finditer(r"\S+", line[matched.end() :]):
            word = word_match.group()
            source_start = matched.end() + word_match.start()
            marks.append(
                WordMark(
                    collapsed_start=width,
                    collapsed_end=width + len(word),
                    line=index + 1,
                    source_byte_start=(
                        line_starts[index] + len(line[:source_start].encode("utf-8"))
                    ),
                    text=word,
                )
            )
            words.append(word)
            width += len(word) + 1
    if words:
        found.append(CollapsedText(" ".join(words), tuple(marks)))
    return tuple(found)


def word_mark_at(marks: tuple[WordMark, ...], offset: int) -> WordMark:
    """The source word containing a non-whitespace collapsed-text offset."""
    low = 0
    high = len(marks)
    while low < high:
        middle = (low + high) // 2
        if marks[middle].collapsed_start <= offset:
            low = middle + 1
        else:
            high = middle
    if low == 0:
        raise StopIteration
    found = marks[low - 1]
    if offset >= found.collapsed_end:
        raise StopIteration
    return found


def source_line_starts(lines: list[str]) -> list[int]:
    """UTF-8 byte offset of every line in the exact source tree-sitter parses."""
    starts: list[int] = []
    width = 0
    for line in lines:
        starts.append(width)
        width += len(line.encode("utf-8")) + 1
    return starts


@dataclass
class FileView:
    """One file, matched against many anchors, with each whole-file derivation done once.

    Hold one per file for as long as the batch lasts. Every derivation is lazy, so a file
    no quoted anchor is asked about never builds its word stream.
    """

    path: str
    lines: list[str]
    _names: set[str] | None = None
    _defined: dict[str, list[Extent]] | None = None
    _collapsed: CollapsedText | None = None
    _comments: tuple[CollapsedText, ...] | None = None
    _levels: dict[int, int] | None = None
    _calls: tuple[grammars.CallLiteral, ...] | None = None

    def extents(self, anchor: model.Anchor) -> tuple[Extent, ...]:
        """Every range in this file that satisfies ``anchor`` -- :func:`anchor_extents`."""
        if anchor.kind == model.SYMBOL:
            return self._symbol(anchor.text)
        if anchor.kind == model.HEADING:
            return heading_extents_in(anchor.text, self.lines, self.headings())
        return widened_quotes(
            anchor.text,
            all_quote_matches(anchor.text, self.words(), self.comments()),
            self.calls(),
        )

    def words(self) -> CollapsedText:
        if self._collapsed is None:
            self._collapsed = collapsed(self.lines)
        return self._collapsed

    def headings(self) -> dict[int, int]:
        if self._levels is None:
            self._levels = heading_levels(inline_scan.unfenced_lines(self.lines))
        return self._levels

    def comments(self) -> tuple[CollapsedText, ...]:
        if self._comments is None:
            self._comments = line_comment_blocks(self.lines)
        return self._comments

    def calls(self) -> tuple[grammars.CallLiteral, ...]:
        if self._calls is None:
            self._calls = grammars.call_argument_literals(self.path, self.lines)
        return self._calls

    def _symbol(self, name: str) -> tuple[Extent, ...]:
        """The name test first: it rejects most files without parsing or scanning them."""
        if self._names is None:
            self._names = set(model.IDENTIFIER_PATTERN.findall("\n".join(self.lines)))
        if name not in self._names:
            return ()
        if self._defined is None:
            self._defined = definitions(self.path, self.lines)
        bound = self._defined.get(name, ())
        if bound:
            return tuple(bound)
        return occurrence_runs(model.whole_identifier(name), self.lines)


def merged(spans: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """``spans`` in order with overlapping and adjacent ones fused into one range."""
    found: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if found and start <= found[-1][1] + 1:
            found[-1] = (found[-1][0], max(found[-1][1], end))
            continue
        found.append((start, end))
    return tuple(found)
