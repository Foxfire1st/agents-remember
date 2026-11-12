"""MIK-R80's overview order rules: purpose first, no leaf or date sections, citations at the end.

An overview says first what its directory is for, then how it is built, then what must
hold there; all its citations stand together at its end (MIK-R80 rule 1). No overview
carries a section named after a leaf or a date (rule 2). The three checks below refuse
exactly what rule 7 names: an overview whose first section after the title and the
governing overview is not its purpose, one that holds a section named after a leaf or a
date, and one whose citation lines are not together at its end. A closeout holding such
an overview is refused with the file and the rule, through the registry's normal
refusal: none of these rules is report-only.

What counts: an *overview* is ``onboarding/overview.md`` or any ``*/overview.md`` under
``onboarding/``. A *heading* is actual Markdown ATX syntax at any level (one to six
``#``, up to three leading spaces, closing ``#`` runs stripped): ``###`` subsections
and indented headings are headings, and the document title (the first level-one
heading) is not a section. A *section* for the order and the citation region is a
``##`` section. A *citation line* is any line carrying an ``[n]`` marker (the
:mod:`.markers` grammar: outside code, unescaped). The trailing citation region of the
last ``##`` section runs from its first citation line to the end of the file: the only
legal lines there are citation lines, blank lines and lines inside a fenced code
block. Every other line is reported, whatever it says -- no heading, prose, list
form, shown bracket or named word exempts it. An example of marker syntax (an
escaped bracket, a code span, a fenced block) is ordinary text of the section that
explains it; inside the region only the fenced form is legal.

Whether the validator can tell the purpose section by its heading alone is the packet's
open truth gap, settled here by worker ruling: it cannot -- overviews in the tree name
it "What This Repo Is", "What This Area Is" or "Purpose" -- so the check accepts exactly
those three fixed headings, and the Curator's instruction texts (rule 8) state them. A
first section with any other heading is refused, so a buried purpose can never pass
under a paraphrased heading.

A standalone conversion (MIK-R24) carries legacy prose unchanged -- it may invent no
purpose section and rewrite no leaf section (MIK-R80 rule 4) -- so these checks do not
run on a conversion (``context.conversion``, like the anchor-path check). They run
only where the packet enforces the order: a commit that publishes a leaf
(``context.leaf_publication``: closeout, direct landing, a leaf's recorded landing --
the closeout gate hands that flag, like the history-row rule MIK-R09). Baseline
adoption and status, census and writer worlds are not leaf publications, so a freshly
scaffolded stub overview (a title with no sections, or a first section that is not
yet the purpose) adopts cleanly and is brought to the order by curation instead
(rule 7 refuses the closeout, not the cutover or the adoption).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Final

from agents_remember.memory_quality.knowledge_validator.markers import find_markers
from agents_remember.memory_quality.knowledge_validator.registry import (
    Finding,
    ValidationContext,
    ValidationRule,
    register_rule,
)
from agents_remember.models.knowledge_files.documents import ONBOARDING_ROOT

#: The fixed purpose headings the order accepts (MIK-R80 rule 1, worker ruling).
PURPOSE_HEADINGS: Final = frozenset({"What This Repo Is", "What This Area Is", "Purpose"})
_GOVERNING_HEADING: Final = "Governing Overview"
_ROOT_OVERVIEW: Final = f"{ONBOARDING_ROOT}/overview.md"
_OVERVIEW_SUFFIX: Final = "/overview.md"
_FENCE: Final = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX: Final = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*$")
_CLOSING: Final = re.compile(r"[ \t]+#+$")
_LEAF_SECTION: Final = re.compile(r"[A-Z]+-L\d+")
_DATE_SECTION: Final = re.compile(r"20\d\d-\d\d-\d\d")


def is_overview(path: str) -> bool:
    """Answer whether ``path`` is an overview of the onboarding tree."""

    return path == _ROOT_OVERVIEW or (
        path.startswith(f"{ONBOARDING_ROOT}/") and path.endswith(_OVERVIEW_SUFFIX)
    )


def _closes(fence: str, opening: re.Match[str] | None, line: str) -> bool:
    """A closing fence repeats the opening character at least as often, and nothing else."""

    return (
        opening is not None
        and opening.group(1)[0] == fence[0]
        and len(opening.group(1)) >= len(fence)
        and not line.strip().lstrip(fence[0])
    )


def _fenced_lines(text: str) -> set[int]:
    """The 1-based lines that open, close, or sit inside a fenced code block."""

    fenced: set[int] = set()
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        opening = _FENCE.match(line)
        if fence is not None:
            fenced.add(number)
            if _closes(fence, opening, line):
                fence = None
            continue
        if opening:
            fence = opening.group(1)
            fenced.add(number)
    return fenced


def _atx_heading(line: str) -> tuple[int, str] | None:
    """The (level, name) when ``line`` is an ATX heading, else ``None``.

    Up to three leading spaces are legal; a tab indent is code, not a heading. A
    ``\\#`` escape is prose. A closing ``#`` run preceded by a blank is syntax, so
    ``## Purpose ##`` names ``Purpose``.
    """

    if line.lstrip(" ").startswith("\\#"):
        return None
    match = _ATX.match(line)
    if match is None:
        return None
    name = _CLOSING.sub("", match.group(2))
    if not name:
        return None
    return len(match.group(1)), name


def overview_headings(text: str) -> list[tuple[int, int, str]]:
    """Every ATX heading of an overview as (1-based line, level, name).

    Fenced code blocks hold no headings: a template example may show ``#`` text.
    """

    fenced = _fenced_lines(text)
    found: list[tuple[int, int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if number in fenced:
            continue
        parsed = _atx_heading(line)
        if parsed is not None:
            found.append((number, *parsed))
    return found


def _overviews(context: ValidationContext) -> Iterator[tuple[str, str]]:
    for path in sorted(context.parsed.markdown):
        if is_overview(path):
            yield path, context.parsed.markdown[path]


def check_purpose_first(context: ValidationContext) -> Iterator[Finding]:
    if context.conversion or not context.leaf_publication:
        return
    for path, text in _overviews(context):
        sections = [(number, name) for number, level, name in overview_headings(text) if level == 2]
        if sections and sections[0][1] == _GOVERNING_HEADING:
            sections = sections[1:]
        if not sections:
            yield Finding(
                path,
                "",
                "this overview has no purpose section (MIK-R80 rule 1): the first section "
                "after the title and the governing overview says what the area is for, under "
                "one of '## What This Repo Is', '## What This Area Is' or '## Purpose'",
            )
        elif sections[0][1] not in PURPOSE_HEADINGS:
            name = sections[0][1]
            yield Finding(
                path,
                f"## {name}",
                f"the first section after the title and the governing overview is '## {name}', "
                "not the purpose (MIK-R80 rule 1): the purpose section carries one of '## What "
                "This Repo Is', '## What This Area Is' or '## Purpose'",
            )


def _leaf_or_date(name: str) -> str | None:
    if _LEAF_SECTION.search(name):
        return "a leaf"
    if _DATE_SECTION.search(name):
        return "a date"
    return None


def check_no_leaf_sections(context: ValidationContext) -> Iterator[Finding]:
    if context.conversion or not context.leaf_publication:
        return
    for path, text in _overviews(context):
        headings = overview_headings(text)
        if headings and headings[0][1] == 1:
            headings = headings[1:]  # the document title is not a section
        for _, level, name in headings:
            named = _leaf_or_date(name)
            if named is not None:
                yield Finding(
                    path,
                    f"{'#' * level} {name}",
                    f"this section is named after {named} (MIK-R80 rule 2): a change goes into "
                    "the section its change belongs to, and no section is named after a leaf "
                    "or a date",
                )


def _region_offenders(
    text: str,
    first: int,
    marker_lines: set[int],
    fenced: set[int],
) -> Iterator[int]:
    """The 1-based lines from ``first`` that break the trailing citation region.

    Mechanical rule (architect ruling): the only legal lines are citation lines,
    blank lines and lines inside a fenced code block. Every other line is
    reported, whatever it says.
    """

    lines = text.splitlines()
    for number in range(first, len(lines) + 1):
        if number in fenced or number in marker_lines:
            continue
        if not lines[number - 1].strip():
            continue
        yield number


def check_citations_at_end(context: ValidationContext) -> Iterator[Finding]:
    if context.conversion or not context.leaf_publication:
        return
    for path, text in _overviews(context):
        level_two = [
            (number, name) for number, level, name in overview_headings(text) if level == 2
        ]
        if not level_two:
            continue  # the purpose rule already reports an overview with no sections
        last_line, last_name = level_two[-1]
        markers = find_markers(text)
        for marker in markers:
            if marker.line < last_line:
                yield Finding(
                    path,
                    f"line {marker.line}",
                    f"citation [{marker.number}] stands before the last section '## {last_name}' "
                    "(MIK-R80 rule 7): all citation lines stand together at the overview's end",
                )
        first = min(
            (marker.line for marker in markers if marker.line > last_line),
            default=None,
        )
        if first is None:
            continue  # no citations in the last section
        marker_lines = {marker.line for marker in markers}
        for number in _region_offenders(text, first, marker_lines, _fenced_lines(text)):
            yield Finding(
                path,
                f"line {number}",
                f"line {number} breaks the trailing citation region of '## {last_name}' "
                "(MIK-R80 rule 7): all citation lines stand together at the overview's end",
            )


OVERVIEW_RULES = (
    ValidationRule(
        "R80.1-purpose-first",
        "MIK-R80 rule 1",
        "an overview's first section is its purpose",
        check_purpose_first,
    ),
    ValidationRule(
        "R80.2-no-leaf-sections",
        "MIK-R80 rule 2",
        "no overview section is named after a leaf or a date",
        check_no_leaf_sections,
    ),
    ValidationRule(
        "R80.7-citations-at-end",
        "MIK-R80 rule 7",
        "an overview's citation lines stand together at its end",
        check_citations_at_end,
    ),
)

for _rule in OVERVIEW_RULES:
    register_rule(_rule)
