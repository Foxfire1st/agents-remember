"""Authoring a converted card's references: citation rows in, resolved sidecar references out.

A converted card's evidence is ``- <finding> [n]`` lines whose ``[n]`` names a reference in the card's
sidecar, each target an anchor (``blob``, ``content`` and a locator) the validator checks (MIK-R21
rule 1, MIK-R22). A curator never writes that JSON by hand. They write the evidence in the grammar
they already use, a citation table:

    | Finding | Anchor | Source |
    | --- | --- | --- |
    | The journal is replayed on resume. | `replay_journal` | src/pkg/journal.py:10-40 |

and the fixer (``citation_fix`` / ``memory-citations --fix`` on a converted tree) authors it:

* every real row becomes ``- <finding> [n]`` where the table stood, ``n`` the next free reference
  number of the card's sidecar, and ``references[n]`` is resolved against the **code working tree**
  by the conversion's own rules (:func:`.citations.row_reference`: symbols the extractor binds once,
  ``path:start-end`` ranges, whole files, URLs; anything else ``unresolved``, reported);
* a row whose finding ends in an existing ``[n]`` re-authors reference ``n`` instead: its targets are
  resolved again at the working tree and its note replaced. This is how a curator refreshes a
  reference after the code changed;
* a placeholder row (anchor and source both empty or ``n/a``) keeps its finding as prose;
* a card without a sidecar gets one: ``ar-onboarding-file/v1`` (``path`` the card's source,
  ``realizes: []``) or, for an ``overview.md``, ``ar-onboarding-route/v1`` (``path`` the route);
* when one card is named (``only``), the references its Markdown no longer cites are removed from its
  sidecar. This is how a curator removes a reference: delete its line, then run the fixer on the card.

A new card for a new source file is therefore Markdown the curator writes (title, governing overview
link, prose and a citation table) and one fixer run.

**Refusals, by name, and nothing of the card is written:** a sidecar that is not valid JSON or does not
parse as a sidecar, before or after the authoring; a row that re-authors ``[n]`` while another
evidence line (``- <finding> [n]``) still cites it, which would silently re-point that line; and a
table delimiter row inside a table one of whose headers is a citation header, which is two tables
with no blank line between them (the table reader ends a table only at a blank line, so the second
table's header and delimiter would be authored as findings, or a citation table below another table
would be skipped). The refusal names the line; a blank line above the second header fixes it. **Every
card is checked before anything is written**, so a run never stops with some cards written and others
not: the cards that pass are written, the refused ones are named, and the run reports ``refused``.
Cards without a citation table are never touched (except the named card's unused references).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any, Final

from agents_remember.kernel.onboarding_doc import unfenced_lines
from agents_remember.memory.conversion.cards import CitationRow
from agents_remember.memory.conversion.citations import RowContext, TargetTally, row_reference
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory_quality.converted_cards import card_sidecar_path, is_overview
from agents_remember.memory_quality.knowledge_validator.markers import escape_markers, find_markers
from agents_remember.memory_quality.style.citations import cells, model
from agents_remember.memory_quality.style.document_shape import tables
from agents_remember.models.knowledge_files.canonical import canonical_text, parse_json
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    ONBOARDING_ROOT,
    parse_document,
)
from agents_remember.models.knowledge_files.sidecars import (
    FILE_SIDECAR_SCHEMA,
    ROOT_ROUTE_PATH,
    ROUTE_SIDECAR_SCHEMA,
)
from agents_remember.worktrees.modules.git import worktree_candidate_tree

__all__ = ["author_card_references"]

_NAMED: Final = re.compile(r"^(?P<finding>.*?)\s*\[(?P<number>[0-9]+)\]\s*$")
_LIST_ITEM: Final = re.compile(r"^\s*[-*+]\s")
_REMOVED: Final = None


@dataclass
class _Code:
    """The code working tree, captured once, as the rows' anchors are resolved against it."""

    root: Path
    _context: tuple[CodeObjects, dict[str, str]] | None = None

    def objects(self) -> tuple[CodeObjects, dict[str, str]]:
        if self._context is None:
            with TemporaryDirectory(prefix="ar-card-authoring-") as scratch:
                tree = worktree_candidate_tree(self.root, Path(scratch) / "index")
            objects = CodeObjects(self.root)
            self._context = (objects, dict(objects.tree(tree)))
        return self._context


@dataclass
class _Report:
    cards: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    authored: int = 0
    reauthored: int = 0
    removed: int = 0
    refused: list[dict[str, str]] = field(default_factory=list)
    tally: TargetTally = field(default_factory=TargetTally)

    def document(self, dry_run: bool) -> dict[str, Any]:
        return {
            "dryRun": dry_run,
            "authoredReferences": self.authored,
            "reauthoredReferences": self.reauthored,
            "removedReferences": self.removed,
            "authoredCards": self.cards,
            "createdSidecars": self.created,
            "unresolvedTargets": self.tally.unresolved,
            "refused": self.refused,
        }


def _citation_columns(header: tables.Row) -> tuple[int, int, int] | None:
    columns = [cell.strip().lower() for cell in header.cells]
    wanted = (cells.FINDING_COLUMN, cells.ANCHOR_COLUMN, cells.SOURCE_COLUMN)
    if not set(wanted) <= set(columns):
        return None
    finding, anchor, source = (columns.index(name) for name in wanted)
    return finding, anchor, source


_DELIMITER_CELL = re.compile(r"^:?-{3,}:?$")
"""A Markdown delimiter cell: three dashes or more, with optional alignment colons. The table
reader's own pattern takes a single dash, which a body cell may hold (L37 review R5-6)."""


def _is_delimiter(row: tables.Row) -> bool:
    return bool(row.cells) and all(_DELIMITER_CELL.match(cell) for cell in row.cells)


def _merged_tables(found: list[tuple[tables.Row, list[tables.Row]]]) -> str | None:
    """Why a card is refused: a second table starts inside one, and one of the two holds citations.

    A table whose headers are no citation headers is not this fixer's business and is left alone,
    so no card without a citation table is ever refused here.
    """

    for header, body in found:
        for position, row in enumerate(body):
            if not _is_delimiter(row):
                continue
            second = body[position - 1] if position else header
            if _citation_columns(header) is None and _citation_columns(second) is None:
                continue
            return (
                f"line {row.index + 1} is a table delimiter row inside the table that starts at "
                f"line {header.index + 1}: two tables with no blank line between them read as "
                f"one table. Put a blank line above line {second.index + 1}"
            )
    return None


def _new_sidecar(card: str) -> dict[str, Any]:
    relative = card.removeprefix(f"{ONBOARDING_ROOT}/")
    if is_overview(card):
        parent = PurePosixPath(relative).parent.as_posix()
        route = ROOT_ROUTE_PATH if parent in {"", "."} else parent
        return {"schema": ROUTE_SIDECAR_SCHEMA, "path": route, "references": {}}
    own = relative.removesuffix(".md")
    return {"schema": FILE_SIDECAR_SCHEMA, "path": own, "references": {}, "realizes": []}


@dataclass
class _Card:
    """One card being authored: its lines, its sidecar, and the next free reference number."""

    path: str
    lines: list[str | None]
    sidecar: dict[str, Any]
    created: bool
    next_number: int = 1
    authored: int = 0
    removed: int = 0
    reauthored: dict[int, int] = field(default_factory=dict)
    """Each re-authored reference number, to the (0-based) line of the row that re-authors it."""

    def number_for(self, finding: str, line: int) -> tuple[str, int]:
        """``(finding without its marker, the reference number)``; a re-authoring is recorded."""

        named = _NAMED.match(finding)
        references = self.sidecar["references"]
        if named is not None and named["number"] in references:
            self.reauthored[int(named["number"])] = line
            return named["finding"].strip(), int(named["number"])
        number = self.next_number
        self.next_number += 1
        self.authored += 1
        return finding, number

    @property
    def changed(self) -> bool:
        return bool(self.authored or self.reauthored or self.removed) or None in self.lines

    def text(self) -> str:
        return "\n".join(line for line in self.lines if line is not None) + "\n"


def _context(card: _Card, code: _Code) -> RowContext:
    objects, tree = code.objects()
    own = None if is_overview(card.path) else card.sidecar["path"]
    return RowContext(
        card=card.path, own_path=own, tree=tree, objects=objects, route_sidecar=own is None
    )


def _is_placeholder(anchor_cell: str, source_cell: str) -> bool:
    return all(
        cell.strip("`").strip().lower() in model.NO_CITATION_MARKERS
        for cell in (anchor_cell, source_cell)
    )


def _author_row(
    card: _Card, row: tables.Row, columns: tuple[int, int, int], code: _Code, tally: TargetTally
) -> None:
    padded = [*row.cells, *[""] * max(0, max(columns) + 1 - len(row.cells))]
    finding, anchor_cell, source_cell = (padded[at].strip() for at in columns)
    if _is_placeholder(anchor_cell, source_cell):
        card.lines[row.index] = finding or _REMOVED
        return
    finding, number = card.number_for(finding, row.index)
    citation = CitationRow(
        finding=finding,
        anchor_cell=anchor_cell,
        source_cell=source_cell,
        claim=cells.parse_row(row.index + 1, anchor_cell, source_cell),
        section="",
    )
    card.sidecar["references"][str(number)] = row_reference(citation, _context(card, code), tally)
    shown = escape_markers(finding)
    card.lines[row.index] = f"- {shown} [{number}]" if shown else f"- [{number}]"


class _Refused(ValueError):
    """Why one card is not authored; nothing of it is written."""


def _existing_sidecar(memory_root: Path, card: str) -> dict[str, Any] | None:
    """The card's sidecar, parsed and valid, or ``None`` when it has none."""

    path = memory_root / card_sidecar_path(card)
    if not path.is_file():
        return None
    try:
        sidecar = parse_json(path.read_text(encoding="utf-8"))
        parse_document(sidecar)
    except (ValueError, TypeError) as error:
        raise _Refused(f"its sidecar {card_sidecar_path(card)} does not parse: {error}") from error
    return sidecar


def _load(memory_root: Path, card: str, *, named: bool) -> _Card | None:
    """The card when it holds a citation table (or is the one named), else ``None``: untouched."""

    text = (memory_root / card).read_text(encoding="utf-8")
    source = text[:-1] if text.endswith("\n") else text
    lines = source.split("\n")
    found = tables.tables(unfenced_lines(lines))
    merged = _merged_tables(found)
    if merged is not None:
        raise _Refused(merged)
    tabled = any(_citation_columns(header) for header, _ in found)
    if not tabled and not named:
        return None
    existing = _existing_sidecar(memory_root, card)
    if existing is None and not tabled:
        return None
    sidecar = _new_sidecar(card) if existing is None else existing
    numbers = [int(key) for key in sidecar["references"]]
    return _Card(
        card, list(lines), sidecar, existing is None, next_number=max(numbers, default=0) + 1
    )


def _leftover(card: _Card, source: list[str]) -> str | None:
    """Why a re-authoring is refused: another evidence line still cites the re-authored number."""

    for marker in find_markers("\n".join(source)):
        number = int(marker.number) if marker.valid else None
        line = source[marker.line - 1]
        if (
            number in card.reauthored
            and card.reauthored[number] != marker.line - 1
            and _LIST_ITEM.match(line)
            and line.rstrip().endswith(f"[{number}]")
        ):
            return (
                f"line {card.reauthored[number] + 1} re-authors reference [{number}], but line "
                f"{marker.line} still cites it and would be re-pointed: replace that line with "
                "the row, or remove it"
            )
    return None


def _remove_unused(card: _Card) -> None:
    """Drop the references the card's Markdown no longer cites (the named card only)."""

    cited = {marker.number for marker in find_markers(card.text())}
    unused = [number for number in card.sidecar["references"] if number not in cited]
    for number in unused:
        del card.sidecar["references"][number]
    card.removed = len(unused)


def _author_card(memory_root: Path, path: str, code: _Code, report: _Report, named: bool) -> _Card:
    """The card with its rows authored in memory; raises :class:`_Refused` by name."""

    card = _load(memory_root, path, named=named)
    if card is None:
        return _Card(path, [], {}, created=False)
    source = [line or "" for line in card.lines]
    for header, body in tables.tables(unfenced_lines(source)):
        columns = _citation_columns(header)
        if columns is None:
            continue
        card.lines[header.index] = _REMOVED
        card.lines[header.index + 1] = _REMOVED
        for row in body:
            _author_row(card, row, columns, code, report.tally)
    leftover = _leftover(card, source)
    if leftover is not None:
        raise _Refused(leftover)
    if named:
        _remove_unused(card)
    try:
        parse_document(card.sidecar)
    except ValueError as error:
        raise _Refused(f"the authored sidecar would not parse: {error}") from error
    return card


def _cards(memory_root: Path, only: str | None) -> list[str]:
    onboarding = memory_root / ONBOARDING_ROOT
    if only is not None:
        named = onboarding / only
        if named.suffix != ".md" or not named.is_file():
            raise ValueError(f"{only!r} names no converted card under {onboarding}")
        return [named.relative_to(memory_root).as_posix()]
    return [
        path.relative_to(memory_root).as_posix()
        for path in sorted(onboarding.rglob("*.md"))
        if path.is_file() and not any(part.startswith(".") for part in path.parts)
    ]


def author_card_references(
    memory_root: Path, code_root: Path, *, only: str | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """Author every citation-table row of the converted cards (or of the one ``only`` names).

    Every card is authored in memory first; only then are the cards that passed written.
    """

    if not (memory_root / LAYOUT_MARKER_PATH).is_file():
        raise ValueError(f"{memory_root} is not converted: its cards are not authored this way")
    report = _Report()
    code = _Code(code_root)
    authored: list[_Card] = []
    for path in _cards(memory_root, only):
        try:
            card = _author_card(memory_root, path, code, report, only is not None)
        except _Refused as refused:
            report.refused.append({"card": path, "reason": str(refused)})
            continue
        if card.changed:
            authored.append(card)
    for card in authored:
        report.cards.append(card.path)
        report.authored += card.authored
        report.reauthored += len(card.reauthored)
        report.removed += card.removed
        sidecar = card_sidecar_path(card.path)
        if card.created:
            report.created.append(sidecar)
        if not dry_run:
            (memory_root / card.path).write_text(card.text(), encoding="utf-8")
            (memory_root / sidecar).write_text(canonical_text(card.sidecar), encoding="utf-8")
    return report.document(dry_run)
