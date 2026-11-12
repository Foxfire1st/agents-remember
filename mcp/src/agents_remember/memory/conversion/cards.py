"""The Markdown half of converting one onboarding card (MIK-R24 rules 1 and 3).

:func:`split_card` reads a legacy card or route overview into its parts and :func:`render_card`
writes the converted Markdown once every real citation row has a reference number. The rules:

* **Metadata.** The ``| Field | Value |`` table before the first ``##`` heading loses ``path``,
  ``repository``, ``doc_type``, ``governingOverview``, ``lastUpdated``, ``lastVerifiedCommitHash``
  and ``lastVerifiedCommitDate``. Any other row (``sourceRoute``, ``generated``, ...) is prose the
  packet does not name, so it stays in a smaller table; a table left with no row is removed.
* **Update History.** Every ``## Update History`` section is removed (Git keeps the text).
* **Citation tables.** Every ``| Finding | Anchor | Source |`` table outside fenced code is replaced
  in place: a real row becomes ``- <finding> [n]``; a placeholder row (anchor and source both empty
  or ``—``/``n/a``) produces no reference and its finding stays as a prose line.
* **One Evidence section.** Every ``##`` section whose heading ends in ``References`` or ``Evidence``
  (``Docs References``, ``Repo-Internal References``, ``Cross-Repo References``, ...) moves, in
  document order and one heading level lower, into one ``## Evidence`` section standing where the
  first of them stood. Their prose is unchanged. A citation table in any other section is replaced
  where it stands.
* **Escaping.** Marker-shaped text outside code (``signals[0]``) is written ``\\[0]``
  (:func:`markers.escape_markers`, the validator's own grammar), so the only markers of the
  converted Markdown are the reference numbers the conversion wrote.

Nothing else changes. No marker is placed in prose: the conversion authors no knowledge.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from agents_remember.memory_quality.knowledge_validator.markers import escape_markers, find_markers
from agents_remember.memory_quality.style.citations import cells, model
from agents_remember.memory_quality.style.document_shape import inline_scan, tables

DROPPED_METADATA: Final = frozenset(
    {
        "path",
        "repository",
        "doc_type",
        "governingOverview",
        "lastUpdated",
        "lastVerifiedCommitHash",
        "lastVerifiedCommitDate",
    }
)
EVIDENCE_HEADING: Final = "## Evidence"
UPDATE_HISTORY: Final = "## update history"
_REFERENCE_SECTION: Final = re.compile(r"^##\s+.*(?:references|evidence)\s*$", re.IGNORECASE)
_DEMOTABLE: Final = re.compile(r"^(#{2,5})(\s)")
_METADATA_HEADER: Final = ("field", "value")
_SENTINEL: Final = "\ue000{}\ue001"
_SENTINEL_PATTERN: Final = re.compile("\ue000([0-9]+)\ue001")


@dataclass(frozen=True)
class CitationRow:
    """One real row of a citation table: its finding and the citation the old format read."""

    finding: str
    anchor_cell: str
    source_cell: str
    claim: model.Claim
    section: str


@dataclass(frozen=True)
class SplitCard:
    """A legacy card taken apart: metadata, rows in output order, and the output template."""

    metadata: Mapping[str, str]
    rows: tuple[CitationRow, ...]
    placeholders: int
    residual_metadata: tuple[str, ...]
    update_history_bytes: int
    template: tuple[str | int, ...]
    """Output lines; an ``int`` is the index into ``rows`` of a ``- <finding> [n]`` line."""
    stray_rows: tuple[int, ...] = ()
    """1-based lines that look like citation rows but belong to no table (left as they are)."""


def _cell_text(cell: str) -> str:
    return cell.strip()


def _is_placeholder(anchor_cell: str, source_cell: str) -> bool:
    return all(
        cell.strip().strip("`").strip().lower() in model.NO_CITATION_MARKERS
        for cell in (anchor_cell, source_cell)
    )


def _headings(lines: Sequence[str], unfenced: set[int]) -> list[int]:
    return [index for index in sorted(unfenced) if lines[index].startswith("## ")]


def _metadata_table(
    found: Sequence[tuple[tables.Row, list[tables.Row]]], first_h2: int
) -> tuple[tables.Row, list[tables.Row]] | None:
    for header, body in found:
        if header.index >= first_h2:
            return None
        if tuple(cell.strip().lower() for cell in header.cells) == _METADATA_HEADER:
            return header, body
    return None


def _metadata_value(cell: str) -> str:
    value = cell.strip()
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        value = value[1:-1].strip()
    return value


def _replace_metadata(
    lines: list[str | int], table: tuple[tables.Row, list[tables.Row]] | None
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Drop the named metadata rows (in ``lines``, by blanking to ``None`` markers)."""

    if table is None:
        return {}, ()
    header, body = table
    metadata: dict[str, str] = {}
    residual: list[str] = []
    for row in body:
        key = row.cells[0].strip() if row.cells else ""
        value = _metadata_value(row.cells[1]) if len(row.cells) > 1 else ""
        metadata.setdefault(key, value)
        if key in DROPPED_METADATA:
            lines[row.index] = _DROP
        else:
            residual.append(key)
    if not residual:
        lines[header.index] = _DROP
        lines[header.index + 1] = _DROP
        after = (body[-1].index if body else header.index + 1) + 1
        if after < len(lines) and lines[after] == "":
            lines[after] = _DROP
    return metadata, tuple(residual)


_DROP: Final = -1


def _section_ranges(headings: Sequence[int], total: int) -> list[tuple[int, int]]:
    if not headings:
        return []
    ends = [*headings[1:], total]
    return list(zip(headings, ends, strict=True))


def _drop_history(
    lines: list[str | int], source: Sequence[str], sections: Sequence[tuple[int, int]]
) -> int:
    """Drop every Update History section; return how many bytes it held."""

    removed = 0
    for start, end in sections:
        if source[start].strip().lower() == UPDATE_HISTORY:
            removed += len("\n".join(source[start:end]).encode("utf-8"))
            for index in range(start, end):
                lines[index] = _DROP
    return removed


@dataclass
class _Rows:
    rows: list[CitationRow]
    placeholders: int = 0


def _citation_columns(header: tables.Row) -> tuple[int, int, int] | None:
    columns = [cell.strip().lower() for cell in header.cells]
    wanted = (cells.FINDING_COLUMN, cells.ANCHOR_COLUMN, cells.SOURCE_COLUMN)
    if not set(wanted) <= set(columns):
        return None
    finding_at, anchor_at, source_at = (columns.index(name) for name in wanted)
    return finding_at, anchor_at, source_at


def _take_table(
    lines: list[str | int],
    table: tuple[tables.Row, list[tables.Row]],
    columns: tuple[int, int, int],
    section: str,
    found: _Rows,
) -> None:
    """Replace one citation table in ``lines``: a row index per real row, a sentence per placeholder."""

    header, body = table
    lines[header.index] = _DROP
    lines[header.index + 1] = _DROP
    finding_at, anchor_at, source_at = columns
    for row in body:
        padded = [*row.cells, *[""] * max(0, max(columns) + 1 - len(row.cells))]
        finding = _cell_text(padded[finding_at])
        anchor_cell, source_cell = padded[anchor_at], padded[source_at]
        if _is_placeholder(anchor_cell, source_cell):
            found.placeholders += 1
            lines[row.index] = finding if finding else _DROP
            continue
        found.rows.append(
            CitationRow(
                finding=finding,
                anchor_cell=anchor_cell.strip(),
                source_cell=source_cell.strip(),
                claim=cells.parse_row(row.index + 1, anchor_cell, source_cell),
                section=section,
            )
        )
        lines[row.index] = len(found.rows) - 1


def _section_of(index: int, source: Sequence[str], sections: Sequence[tuple[int, int]]) -> str:
    for start, end in sections:
        if start <= index < end:
            return source[start].removeprefix("## ").strip()
    return ""


def split_card(text: str) -> SplitCard:
    """Take a legacy card apart; :func:`render_card` writes its converted Markdown."""

    body = text[:-1] if text.endswith("\n") else text
    source = body.split("\n")
    lines: list[str | int] = list(source)
    unfenced_pairs = inline_scan.unfenced_lines(source)
    unfenced = {index for index, _ in unfenced_pairs}
    found = tables.tables(unfenced_pairs)
    headings = _headings(source, unfenced)
    first_h2 = headings[0] if headings else len(source)
    metadata, residual = _replace_metadata(lines, _metadata_table(found, first_h2))
    sections = _section_ranges(headings, len(source))
    history_bytes = _drop_history(lines, source, sections)
    taken = _Rows(rows=[])
    for table in found:
        columns = _citation_columns(table[0])
        if columns is not None and lines[table[0].index] != _DROP:
            _take_table(lines, table, columns, _section_of(table[0].index, source, sections), taken)
    template = _assemble(lines, source, sections, unfenced)
    strays = _stray_citation_rows(source, unfenced, found, lines)
    ordered = [item for item in template if isinstance(item, int)]
    remap = {old: new for new, old in enumerate(ordered)}
    return SplitCard(
        metadata=metadata,
        rows=tuple(taken.rows[old] for old in ordered),
        placeholders=taken.placeholders,
        residual_metadata=residual,
        update_history_bytes=history_bytes,
        template=tuple(remap[item] if isinstance(item, int) else item for item in template),
        stray_rows=strays,
    )


def _stray_citation_rows(
    source: Sequence[str],
    unfenced: set[int],
    found: Sequence[tuple[tables.Row, list[tables.Row]]],
    lines: Sequence[str | int],
) -> tuple[int, ...]:
    """Pipe rows with a ``path:line`` source outside every table, in kept (non-dropped) text.

    The table parser, like the legacy checker, does not read them as table rows (typically rows
    appended after a blank line below a table), so they stay in the Markdown unchanged; the report
    lists them so a curator or the migration can fold them in.
    """

    in_tables = {row.index for header, body in found for row in (header, *body)}
    in_tables |= {header.index + 1 for header, _ in found}
    return tuple(
        index + 1
        for index in sorted(unfenced)
        if index not in in_tables
        and lines[index] != _DROP
        and source[index].lstrip().startswith("|")
        and model.SOURCE_PATTERN.search(source[index])
    )


def _demoted(line: str | int, index: int, unfenced: set[int]) -> str | int:
    if isinstance(line, int) or index not in unfenced:
        return line
    return _DEMOTABLE.sub(lambda match: f"#{match.group(1)}{match.group(2)}", line, count=1)


class _Output:
    """Output lines, collapsing only the blank runs that a removal created."""

    def __init__(self) -> None:
        self.lines: list[str | int] = []
        self._dropped = False

    def drop(self) -> None:
        self._dropped = True

    def keep(self, line: str | int) -> None:
        if line == "" and self._dropped and (not self.lines or self.lines[-1] == ""):
            return
        if line != "":
            self._dropped = False
        self.lines.append(line)

    def finished(self) -> tuple[str | int, ...]:
        while self.lines and self.lines[-1] == "":
            self.lines.pop()
        return tuple(self.lines)


def _assemble(
    lines: Sequence[str | int],
    source: Sequence[str],
    sections: Sequence[tuple[int, int]],
    unfenced: set[int],
) -> tuple[str | int, ...]:
    """Move the reference sections into one ``## Evidence`` section; drop removed lines."""

    reference = [
        (start, end)
        for start, end in sections
        if _REFERENCE_SECTION.match(source[start]) and lines[start] != _DROP
    ]
    moved = {index for start, end in reference for index in range(start, end)}
    evidence_at = reference[0][0] if reference else None
    output = _Output()
    for index, line in enumerate(lines):
        if index == evidence_at:
            output.keep(EVIDENCE_HEADING)
            output.keep("")
            for inner in sorted(moved):
                if lines[inner] == _DROP:
                    output.drop()
                else:
                    output.keep(_demoted(lines[inner], inner, unfenced))
        if index in moved:
            continue
        if line == _DROP:
            output.drop()
            continue
        output.keep(line)
    return output.finished()


def render_card(split: SplitCard, numbers: Sequence[int | None]) -> tuple[str, int]:
    """Write the converted Markdown: row ``i`` becomes ``- <finding> [numbers[i]]``.

    A row whose number is ``None`` produced no reference and keeps its finding as prose. Returns the
    Markdown and how many marker-shaped legacy texts were escaped.
    """

    rendered: list[str] = []
    for item in split.template:
        if isinstance(item, int):
            row = split.rows[item]
            number = numbers[item]
            if number is None:
                rendered.append(row.finding)
            else:
                marker = _SENTINEL.format(number)
                rendered.append(f"- {row.finding} {marker}" if row.finding else f"- {marker}")
        else:
            rendered.append(item)
    text = "\n".join(rendered) + "\n"
    escaped = len(find_markers(text))
    return _SENTINEL_PATTERN.sub(lambda match: f"[{match.group(1)}]", escape_markers(text)), escaped
