"""Report Markdown table rows whose cell count differs from the header.

A table starts with a header followed immediately by a same-width delimiter row. It ends
at a blank line, fence, or line with no cell divider. Long rows lose surplus cells when
rendered; short rows are padded.

False-positive boundaries:

1. Code spans ending in a backslash are parsed before escapes.
2. Escaped pipes outside code spans and pipes inside code spans are not dividers.
3. Multi-backtick spans match only delimiters of the same length.
4. Tables quoted inside fenced blocks are skipped.
5. Prose directly below a table with no blank line is not treated as a one-cell row.

For a long row, repair the header, delimiter, and every short row together, or remove the
surplus cell. For a short row, add the missing cells or remove the row. Every finding
carries the complete applicable remediation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import rel
from agents_remember.memory_quality.style.document_shape import inline_scan
from agents_remember.memory_quality.style.finding import QualityFinding, check_result

CHECK_NAME = "style.document_shape.tables"
DELIMITER_CELL_PATTERN = re.compile(r"^:?-+:?$")


@dataclass(frozen=True)
class Row:
    """One line of a candidate table: where it was, and how many cells it holds."""

    index: int
    line: str
    cells: list[str]


def check_onboarding_root(onboarding_root: Path) -> dict[str, Any]:
    findings: list[QualityFinding] = []
    files_checked = 0
    for path in sorted(onboarding_root.rglob("*.md")):
        if not path.is_file():
            continue
        files_checked += 1
        findings.extend(check_file(path, onboarding_root))
    return check_result(check=CHECK_NAME, files_checked=files_checked, findings=findings)


def check_file(path: Path, onboarding_root: Path) -> list[QualityFinding]:
    lines = path.read_text(encoding="utf-8").splitlines()
    findings: list[QualityFinding] = []
    for header, body in tables(inline_scan.unfenced_lines(lines)):
        findings.extend(ragged_findings(header, body, path, onboarding_root))
    return findings


def rows_of(unfenced: list[tuple[int, str]]) -> list[Row]:
    return [
        Row(index=index, line=line, cells=inline_scan.split_row(line)) for index, line in unfenced
    ]


def tables(unfenced: list[tuple[int, str]]) -> list[tuple[Row, list[Row]]]:
    """Every GFM table in the file, as its header row and its body rows.

    The delimiter row is consumed rather than returned: it has already been checked
    against the header, and a mismatch there means there is no table at all.
    """
    rows = rows_of(unfenced)
    found: list[tuple[Row, list[Row]]] = []
    position = 0
    while position + 1 < len(rows):
        header = rows[position]
        delimiter = rows[position + 1]
        if not starts_table(header, delimiter):
            position += 1
            continue
        body, position = read_body(rows, position + 2)
        found.append((header, body))
    return found


def starts_table(header: Row, delimiter: Row) -> bool:
    if delimiter.index != header.index + 1:
        return False
    if not inline_scan.cell_boundaries(header.line):
        return False
    if len(delimiter.cells) != len(header.cells) or not delimiter.cells:
        return False
    return all(DELIMITER_CELL_PATTERN.match(cell) for cell in delimiter.cells)


def read_body(rows: list[Row], start: int) -> tuple[list[Row], int]:
    body: list[Row] = []
    position = start
    previous_index = rows[start - 1].index if start else -1
    while position < len(rows):
        row = rows[position]
        if row.index != previous_index + 1:
            break
        if not row.line.strip() or not inline_scan.cell_boundaries(row.line):
            break
        body.append(row)
        previous_index = row.index
        position += 1
    return body, position


NO_CITATION_MARKER = "n/a"
"""Fallback for padded cells when the table has no existing no-citation marker."""


def ragged_findings(
    header: Row,
    body: list[Row],
    path: Path,
    onboarding_root: Path,
) -> list[QualityFinding]:
    expected = len(header.cells)
    findings: list[QualityFinding] = []
    for row in body:
        if len(row.cells) == expected:
            continue
        findings.append(
            QualityFinding(
                check=CHECK_NAME,
                path=rel(path, onboarding_root),
                line=row.index + 1,
                severity="warning",
                code="table_row_cell_count_mismatch",
                message=ragged_message(len(row.cells), expected, header.index + 1),
                previous_line=header.index + 1,
            )
        )
    return findings


def ragged_message(found: int, expected: int, header_line: int) -> str:
    """Return the complete repair for a short or long table row.

    Short rows require missing cells. Long rows require the header, delimiter, and every
    short row in the same table to be widened together.
    """
    counts = f"Table row has {found} cells but its header (line {header_line}) has {expected}."
    if found <= expected:
        return (
            f"{counts} Nothing is lost -- GFM pads a short row -- but the row is "
            f"misaligned against its header. Add the missing cell(s), using the "
            f"no-citation marker this table already uses (or '{NO_CITATION_MARKER}' if it "
            f"has none); never leave the cell empty."
        )
    return (
        f"{counts} THIS ROW IS LOSING DATA: GFM truncates the extra cell, so its content "
        f"is absent from the rendered document with no warning. If the extra cell is "
        f"content the header is missing, widen the table -- and do all three parts "
        f"together, because the first two alone break it: (1) widen the header row; "
        f"(2) widen the DELIMITER row to the same count, since GFM stops treating the "
        f"construct as a table at all when they disagree; (3) pad every SHORT row in the "
        f"same table to the new width, using the no-citation marker this table already "
        f"uses (or '{NO_CITATION_MARKER}' if it has none). If instead the cell is an "
        f"unescaped literal pipe, escape it as '\\|' or wrap it in a code span."
    )
