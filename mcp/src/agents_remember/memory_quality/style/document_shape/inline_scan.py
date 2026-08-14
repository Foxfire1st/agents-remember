"""Locate Markdown code spans, fences, and table-cell boundaries.

Code-span delimiters are resolved before backslash escapes. Backslashes inside a code
span are literal, so a closing backtick preceded by a backslash still closes the span.
Outside spans, ``\\|`` does not divide a table cell.

Known divergence: GFM performs table block splitting before inline parsing and can treat
a raw pipe inside a code span as a divider. This scanner does not. The current memory
corpus contains no such row; adding one requires choosing and testing the intended table
contract.
"""

from __future__ import annotations

BACKTICK = "`"
BACKSLASH = "\\"
PIPE = "|"


def backtick_runs(line: str) -> list[tuple[int, int]]:
    """Every maximal run of backticks, as ``(start, length)``.

    No backslash is consulted. A run is delimited only by non-backtick characters, which
    is what makes ``` `\\\\?\\` ``` close.
    """
    runs: list[tuple[int, int]] = []
    index = 0
    while index < len(line):
        if line[index] != BACKTICK:
            index += 1
            continue
        end = index
        while end < len(line) and line[end] == BACKTICK:
            end += 1
        runs.append((index, end - index))
        index = end
    return runs


def code_span_ranges(line: str) -> list[tuple[int, int]]:
    """Half-open ``(start, end)`` ranges covering each code span, delimiters included.

    An opening run is matched to the next run of EQUAL length, per CommonMark, which is
    what makes a multi-backtick span such as ```` ``ariaLabel={`x`}`` ```` -- present in
    ``onboarding/dashboard/src/panels/RailChat.tsx.md`` -- one span rather than three. A run with no
    equal-length partner later in the line is literal text and opens nothing.
    """
    runs = backtick_runs(line)
    ranges: list[tuple[int, int]] = []
    opener = 0
    while opener < len(runs):
        start, length = runs[opener]
        closer = next(
            (index for index in range(opener + 1, len(runs)) if runs[index][1] == length),
            None,
        )
        if closer is None:
            opener += 1
            continue
        close_start, close_length = runs[closer]
        ranges.append((start, close_start + close_length))
        opener = closer + 1
    return ranges


def cell_boundaries(line: str) -> list[int]:
    """Indexes of the pipes that divide this line into table cells."""
    spans = code_span_ranges(line)
    boundaries: list[int] = []
    index = 0
    while index < len(line):
        span_end = enclosing_span_end(index, spans)
        if span_end is not None:
            index = span_end
            continue
        character = line[index]
        if character == BACKSLASH:
            index += 2
            continue
        if character == PIPE:
            boundaries.append(index)
        index += 1
    return boundaries


def enclosing_span_end(index: int, spans: list[tuple[int, int]]) -> int | None:
    for start, end in spans:
        if start <= index < end:
            return end
    return None


def cell_spans(line: str) -> list[tuple[int, int]]:
    """Where each cell of a table row sits, with GFM's optional outer pipes removed.

    GFM drops one leading and one trailing pipe if present, so ``| a | b |``, ``a | b``
    and ``| a | b`` are all two cells, while ``| a | b ||`` is three -- the second
    trailing pipe closes a genuinely empty third cell.

    Offsets rather than text, because a row is EDITED here as well as read: the citation
    fixer replaces one cell and must leave every other character of the row alone.
    """
    boundaries = cell_boundaries(line)
    if not boundaries:
        return [(0, len(line))]
    spans: list[tuple[int, int]] = []
    previous = 0
    for boundary in boundaries:
        spans.append((previous, boundary))
        previous = boundary + 1
    spans.append((previous, len(line)))
    if not line[spans[0][0] : spans[0][1]].strip():
        spans = spans[1:]
    if spans and not line[spans[-1][0] : spans[-1][1]].strip():
        spans = spans[:-1]
    return spans


def split_row(line: str) -> list[str]:
    """The cells of a table row, stripped."""
    return [line[start:end].strip() for start, end in cell_spans(line)]


def fence_delimiter(line: str) -> tuple[str, int] | None:
    """``(character, length)`` if this line opens or closes a fenced code block.

    A fence is three or more backticks or tildes at an indent of at most three spaces.
    Both checks in this package skip fenced regions: a fence is where a document quotes
    somebody else's text, so a diff hunk or a broken table shown inside one is the
    document working correctly.
    """
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return None
    for character in (BACKTICK, "~"):
        if not stripped.startswith(character * 3):
            continue
        length = len(stripped) - len(stripped.lstrip(character))
        return character, length
    return None


def unfenced_lines(lines: list[str]) -> list[tuple[int, str]]:
    """``(zero-based index, line)`` for every line outside a fenced code block.

    The opening and closing fence lines are themselves excluded. A closing fence must use
    the same character and be at least as long as the opener, which is what keeps a
    ````` ````markdown ````` block containing a ```` ``` ```` from ending three lines early --
    ``notes/installer-design`` holds exactly that nesting.
    """
    kept: list[tuple[int, str]] = []
    open_fence: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        delimiter = fence_delimiter(line)
        if open_fence is None:
            if delimiter is not None:
                open_fence = delimiter
                continue
            kept.append((index, line))
            continue
        character, length = open_fence
        if delimiter is not None and delimiter[0] == character and delimiter[1] >= length:
            open_fence = None
    return kept
