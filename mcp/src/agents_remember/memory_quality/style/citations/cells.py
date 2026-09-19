"""Parse the table citation form ``| Finding | Anchor | Source |``.

``Anchor`` uses the shared anchor grammar; ``Source`` is one or more repo-relative
``path:start-end`` tokens in plain text. Multiple anchors and sources are legal and pool
for containment rather than pairing positionally. This module identifies evidence tables
and reads their three cells; validation belongs to ``range_resolution``.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.kernel.onboarding_doc import active_claim_lines
from agents_remember.memory_quality.style.citations import model
from agents_remember.memory_quality.style.document_shape import inline_scan, tables

FINDING_COLUMN = "finding"
ANCHOR_COLUMN = "anchor"
SOURCE_COLUMN = "source"
SUPERSEDED_COLUMNS = frozenset({"citations", "source path"})
EVIDENCE_COLUMNS = frozenset({ANCHOR_COLUMN, SOURCE_COLUMN}) | SUPERSEDED_COLUMNS
REQUIRED_COLUMNS = frozenset({FINDING_COLUMN, ANCHOR_COLUMN, SOURCE_COLUMN})


@dataclass(frozen=True)
class CitationTable:
    """One table this check claims, whether or not it is in the current format."""

    line: int
    columns: tuple[str, ...]
    rows: tuple[model.Claim, ...]
    row_count: int

    @property
    def conforming(self) -> bool:
        columns = set(self.columns)
        return columns >= REQUIRED_COLUMNS and not (SUPERSEDED_COLUMNS & columns)


def unescaped(cell: str) -> str:
    r"""One table cell's text with GFM's ``\|`` escape resolved to a literal pipe.

    GFM requires a literal pipe inside a table cell to be escaped, and a renderer shows the
    single character; this scanner reads the RAW cell text, so an anchor written as
    `` `dict \| None` `` arrived at the anchor grammar as ``dict \| None`` and could never
    match the source's ``dict | None``. The construct was then either reported as an absent
    anchor or, worse, satisfied by some other occurrence in the range -- which is why the
    escape is resolved here, where cell text becomes anchor text, rather than by asking the
    author to spell the code differently. Only ``\|`` is touched: every other backslash in a
    cell is content the anchor grammar or the source path owns (``\d``, ``\n``, a Windows
    separator), so a general markdown unescaper would corrupt the very spans this reads.
    """
    return cell.replace("\\|", "|")


def parse_row(line: int, anchor_cell: str, source_cell: str) -> model.Claim:
    anchors, skipped = model.anchors_in(unescaped(anchor_cell))
    citations, malformed = model.citations_in(unescaped(source_cell))
    return model.Claim(
        line=line,
        anchors=anchors,
        citations=citations,
        malformed=malformed,
        unchecked_spans=skipped,
    )


def scan_tables(lines: list[str]) -> list[tuple[tables.Row, list[tables.Row]]]:
    """Parse every GFM table once for reuse by citation checks."""
    return tables.tables(inline_scan.unfenced_lines(active_claim_lines(lines)))


def table_lines(
    lines: list[str], found: list[tuple[tables.Row, list[tables.Row]]] | None = None
) -> set[int]:
    """Every zero-based line index a table occupies -- what :mod:`prose` must not read."""
    occupied: set[int] = set()
    for header, body in found if found is not None else scan_tables(lines):
        occupied.update({header.index, header.index + 1})
        occupied.update(row.index for row in body)
    return occupied


def citation_tables(
    lines: list[str], found: list[tuple[tables.Row, list[tables.Row]]] | None = None
) -> list[CitationTable]:
    """Every evidence table in one document, current format or not.

    A table is claimed when it has a ``Finding`` column beside any of ``Anchor``,
    ``Source``, ``Citations`` or ``Source Path``. Claiming the superseded spellings is what
    makes the format change enforceable rather than silently vacating the check over the
    2,437 tables that still carry them.

    The table finder is the ragged-row check's, not a second one: what counts as a table --
    a header, a delimiter row of matching width directly beneath it, body rows to the first
    blank line or fence -- is one definition for this package, and two readings of it would
    eventually disagree about which rows exist.
    """
    claimed: list[CitationTable] = []
    for header, body in found if found is not None else scan_tables(lines):
        columns = tuple(cell.strip().lower() for cell in header.cells)
        if FINDING_COLUMN not in columns or not EVIDENCE_COLUMNS & set(columns):
            continue
        table = CitationTable(line=header.index + 1, columns=columns, rows=(), row_count=len(body))
        if not table.conforming:
            claimed.append(table)
            continue
        anchor_at = columns.index(ANCHOR_COLUMN)
        source_at = columns.index(SOURCE_COLUMN)
        rows = tuple(
            parse_row(row.index + 1, row.cells[anchor_at], row.cells[source_at])
            for row in body
            if len(row.cells) > max(anchor_at, source_at)
        )
        claimed.append(
            CitationTable(line=table.line, columns=columns, rows=rows, row_count=len(body))
        )
    return claimed
