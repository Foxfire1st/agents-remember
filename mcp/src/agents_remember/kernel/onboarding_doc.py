"""Shared onboarding-document parsing and body/history change helpers.

Owns the markdown-table metadata parsing and the section-aware body/history
change classification used by closeout gates and
carryover planning. The "meaningful body" of an onboarding document is its
content minus the verification metadata rows and the Update History section,
so metadata stamps and history appendices never count as content updates.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from agents_remember.kernel import filesystem

ROUTE_OVERVIEW_DOC_TYPES = {"repo-overview", "route-local-overview"}
METADATA_FIELDS = {"lastUpdated", "lastVerifiedCommitHash", "lastVerifiedCommitDate"}
UPDATE_HISTORY_HEADING = "## update history"
NO_IMPACT_MARKER_PATTERN = re.compile(r"\bno (?:content|route) impact:", re.IGNORECASE)


def markdown_table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def table_metadata(path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in filesystem.read_text(path, encoding="utf-8").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = markdown_table_cells(line)
        if len(cells) < 2:
            continue
        field = cells[0].strip()
        value = cells[1].strip().strip("`")
        if not field or set(field) <= {"-"} or field.lower() == "field":
            continue
        metadata[field] = value
    return metadata


def fence_delimiter(line: str) -> tuple[str, int] | None:
    """``(character, length)`` if this line opens or closes a fenced code block.

    A fence is three or more backticks or tildes at an indent of at most three spaces.
    Document scanners skip fenced regions: a fence is where a document quotes
    somebody else's text, so a diff hunk or a broken table shown inside one is the
    document working correctly.
    """
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return None
    for character in ("`", "~"):
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


def _is_update_history_heading(line: str) -> bool:
    return line.strip().lower() == UPDATE_HISTORY_HEADING


def _section_lines(lines: list[str]) -> Iterable[tuple[str, bool, bool]]:
    """Line, history membership, and real H2 boundary under one fence-aware scope."""
    unfenced = {index for index, _line in unfenced_lines(lines)}
    in_history = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        heading = index in unfenced and stripped.startswith("## ")
        if heading:
            in_history = _is_update_history_heading(stripped)
        yield line, in_history, heading


def meaningful_body(text: str) -> str:
    """Document content minus verification metadata rows and Update History."""
    kept: list[str] = []
    for line, in_history, _heading in _section_lines(text.splitlines()):
        if in_history:
            continue
        cells = markdown_table_cells(line) if line.strip().startswith("|") else []
        if cells and cells[0] in METADATA_FIELDS:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def update_history_section(text: str) -> list[str]:
    """Stripped, non-empty historical lines, excluding real H2 boundaries."""
    return [
        line.strip()
        for line, in_history, heading in _section_lines(text.splitlines())
        if in_history and not heading and line.strip()
    ]


def new_history_lines(old_text: str | None, new_text: str) -> list[str]:
    """Update History lines present in new_text but absent from old_text."""
    old_lines = set(update_history_section(old_text)) if old_text is not None else set()
    return [line for line in update_history_section(new_text) if line not in old_lines]
