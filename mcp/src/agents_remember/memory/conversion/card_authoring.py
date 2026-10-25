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

**The note-only revision (MIK-R198 rule 6a).** Re-authoring a reference through a table row resolves its
targets again, so a note cannot be revised that way without also re-resolving what the reference points
at. :func:`revise_reference_note` is that one missing act: it replaces one reference's ``note`` and
nothing else, keyed by ``(sidecar path, references key [n])``. Every target, anchor and other reference
stays byte-identical, no reference number is minted, and the repairing leaf is recorded as the author. It
refuses a missing key, an input whose base digest is not the sidecar's current bytes, an empty note, an
absent sidecar, and unconverted memory -- and, after the write, re-reads the file and refuses rather than
report success if anything other than that note changed.
"""

from __future__ import annotations

import copy
import hashlib
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

__all__ = [
    "NoteRevision",
    "NoteRevisionRefused",
    "author_card_references",
    "revise_reference_note",
]

_NAMED: Final = re.compile(r"^(?P<finding>.*?)\s*\[(?P<number>[0-9]+)\]\s*$")
_LIST_ITEM: Final = re.compile(r"^\s*[-*+]\s")
_REMOVED: Final = None


@dataclass
class _Code:
    """The code working tree, captured once, as the rows' anchors are resolved against it."""

    root: Path
    _context: tuple[CodeObjects, dict[str, str], frozenset[str]] | None = None

    def objects(self) -> tuple[CodeObjects, dict[str, str], frozenset[str]]:
        if self._context is None:
            with TemporaryDirectory(prefix="ar-card-authoring-") as scratch:
                tree = worktree_candidate_tree(self.root, Path(scratch) / "index")
            objects = CodeObjects(self.root)
            self._context = (
                objects,
                dict(objects.tree(tree)),
                CodeObjects.link_paths(objects.tree_modes(tree)),
            )
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
            number = int(named["number"])
            if number in self.reauthored:
                # One run re-authors a reference once: a second row would overwrite the first
                # row's reference and leave two evidence lines citing one number.
                raise _Refused(
                    f"lines {self.reauthored[number] + 1} and {line + 1} both re-author "
                    f"reference [{number}]: keep one row, or give the other no number so it "
                    "becomes a new reference"
                )
            self.reauthored[number] = line
            return named["finding"].strip(), number
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
    objects, tree, symlinks = code.objects()
    own = None if is_overview(card.path) else card.sidecar["path"]
    return RowContext(
        card=card.path,
        own_path=own,
        tree=tree,
        objects=objects,
        route_sidecar=own is None,
        symlinks=symlinks,
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
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(memory_root).parts)
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


class NoteRevisionRefused(ValueError):
    """Why one note-only revision is refused; nothing of the card is written.

    ``code`` is the machine reason a caller branches on; the message names the object and the fix, so
    the refusal is actionable without reading source.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _bytes_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _kept(document: dict[str, Any], key: str) -> str:
    """Everything a revision must keep, canonically: the sidecar without that reference's note."""

    stripped = copy.deepcopy(document)
    stripped["references"][key].pop("note", None)
    return canonical_text(stripped)


def _confined_operand(root: Path, relative: PurePosixPath, *, code: str, subject: str) -> Path:
    """``relative`` inside ``root`` or a named refusal; no write authority leaves the admitted tree.

    Scope admission names the tree; it does not name the operand. A relative path with ``..``, an
    absolute path, or a symlink whose target lies outside the admitted tree is refused *before* any
    read is trusted or any byte is written, so a card operand cannot address a sibling memory tree
    that the caller's contract never admitted.
    """

    if relative.is_absolute() or ".." in relative.parts:
        raise NoteRevisionRefused(
            code,
            f"{relative.as_posix()!r} leaves the admitted memory tree {root}: a note revision "
            f"addresses a {subject} inside it by its onboarding-relative path",
        )
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise NoteRevisionRefused(
            code,
            f"{relative.as_posix()!r} resolves to {resolved.as_posix()}, outside the admitted "
            f"memory tree {root}: a note revision writes only inside the tree its contract names",
        )
    return resolved


@dataclass(frozen=True)
class NoteRevision:
    """One note-only revision: the reference identity, and the claim that replaces its note.

    The identity is ``(sidecar path, references key [n])`` -- locator and blob are not identity, so a
    mechanical re-point preserves it and this revision addresses it. ``base_digest`` is the sidecar's
    SHA-256 as the caller read it: a revision written against other bytes is refused rather than
    applied to a document it never saw, and ``author`` is the repairing leaf the revision is recorded
    under.
    """

    memory_root: Path
    card: str
    number: int
    note: str
    base_digest: str
    author: str


def _card_operand(root: Path, card: str) -> Path:
    """The card ``card`` names inside ``root``, or a named refusal; no read leaves the tree."""

    own = PurePosixPath(card)
    path = _confined_operand(root, own, code="card-escapes-memory", subject="card")
    if (
        own.suffix != ".md"
        or not own.parts
        or own.parts[0] != ONBOARDING_ROOT
        or len(own.parts) < 2
    ):
        raise NoteRevisionRefused(
            "card-absent",
            f"{card!r} names no converted card under {root / ONBOARDING_ROOT}",
        )
    if not path.is_relative_to((root / ONBOARDING_ROOT).resolve()):
        raise NoteRevisionRefused(
            "card-escapes-memory",
            f"{card!r} resolves outside {root / ONBOARDING_ROOT}: a note revision addresses a card "
            "of the admitted memory tree only",
        )
    if not path.is_file():
        raise NoteRevisionRefused(
            "card-absent",
            f"{card!r} names no converted card under {root / ONBOARDING_ROOT}",
        )
    return path


def _sidecar_operand(root: Path, card: str) -> tuple[str, Path]:
    """``(sidecar path, resolved path)`` for ``card``, confined to ``root`` or refused by name."""

    sidecar_path = card_sidecar_path(card)
    path = _confined_operand(
        root,
        PurePosixPath(sidecar_path),
        code="sidecar-escapes-memory",
        subject="sidecar",
    )
    if not path.is_relative_to((root / ONBOARDING_ROOT).resolve()):
        raise NoteRevisionRefused(
            "sidecar-escapes-memory",
            f"the sidecar of {card!r} resolves outside {root / ONBOARDING_ROOT}",
        )
    if not path.is_file():
        raise NoteRevisionRefused(
            "sidecar-absent",
            f"{card!r} has no sidecar {sidecar_path}, so it holds no reference to revise",
        )
    return sidecar_path, path


def _revision_context(
    revision: NoteRevision,
) -> tuple[Path, dict[str, Any], str, str, str]:
    """``(sidecar path, parsed sidecar, key, digest, kept)`` or a named refusal; nothing is written."""

    memory_root = revision.memory_root
    if not (memory_root / LAYOUT_MARKER_PATH).is_file():
        raise NoteRevisionRefused(
            "card-not-converted",
            f"{memory_root} is not converted: its cards are not authored this way",
        )
    if not revision.author.strip():
        raise NoteRevisionRefused(
            "author-absent",
            "a note revision records the leaf that repairs it: name the repairing leaf in author",
        )
    if not revision.note.strip():
        raise NoteRevisionRefused(
            "note-empty",
            "a note revision replaces one claim with another: an empty note is a removal, which is a "
            "different act on the card",
        )
    root = memory_root.resolve()
    _card_operand(root, revision.card)
    sidecar_path, path = _sidecar_operand(root, revision.card)
    digest = _bytes_digest(path)
    if revision.base_digest.strip().lower() != digest:
        raise NoteRevisionRefused(
            "stale-input",
            f"the sidecar {sidecar_path} now holds {digest}, not the base "
            f"{revision.base_digest.strip()} the revision was prepared from: read the sidecar again "
            "and revise against its current bytes",
        )
    try:
        sidecar = parse_json(path.read_text(encoding="utf-8"))
        parse_document(sidecar)
    except (ValueError, TypeError) as error:
        raise NoteRevisionRefused(
            "sidecar-unreadable", f"{sidecar_path} does not parse: {error}"
        ) from error
    key = str(revision.number)
    references = sidecar.get("references") or {}
    if key not in references:
        raise NoteRevisionRefused(
            "reference-missing",
            f"{sidecar_path} holds no reference [{key}]: its references are "
            f"{', '.join(sorted(references, key=int)) or 'none'}",
        )
    return path, sidecar, key, digest, _kept(sidecar, key)


def revise_reference_note(revision: NoteRevision, *, dry_run: bool = False) -> dict[str, Any]:
    """Replace one reference's ``note`` and nothing else (MIK-R198 rule 6a).

    Every target, anchor and other reference of the card keeps its exact value, no reference number is
    minted, and the repairing leaf is recorded in the result. Refused by name, with nothing written:
    unconverted memory, an absent card or sidecar, a key the sidecar does not hold, a base digest that
    is not the sidecar's current bytes (a stale input), an empty note, a sidecar that does not parse,
    and any revision whose written bytes would change something other than that note.
    """

    path, sidecar, key, digest, kept = _revision_context(revision)
    note = revision.note.strip()
    revised = copy.deepcopy(sidecar)
    previous = revised["references"][key].get("note")
    revised["references"][key]["note"] = note
    written = canonical_text(revised)
    # The guarantee is checked on the bytes that will land, not on the dict in memory: what is written
    # must re-read as the same card with one note replaced.
    reread = parse_json(written)
    parse_document(reread)
    if _kept(reread, key) != kept:
        raise NoteRevisionRefused(
            "targets-would-change",
            f"revision of {path.name} reference [{key}] would change more than its note: it was "
            "refused before writing",
        )
    if reread["references"][key].get("note") != note:
        raise NoteRevisionRefused(
            "note-not-written",
            f"revision of {path.name} reference [{key}] would not carry the note given",
        )
    after = hashlib.sha256(written.encode("utf-8")).hexdigest()
    if not dry_run:
        path.write_text(written, encoding="utf-8")
        after = _bytes_digest(path)
        if _kept(parse_json(path.read_text(encoding="utf-8")), key) != kept:
            raise NoteRevisionRefused(
                "targets-changed-on-write",
                f"revision of {path.name} reference [{key}] changed more than its note on disk; "
                "restore the sidecar from Git and revise again",
            )
    return {
        "schema": "ar-reference-note-revision/v1",
        "card": revision.card,
        "sidecar": card_sidecar_path(revision.card),
        "reference": key,
        "author": revision.author.strip(),
        "dryRun": dry_run,
        "changed": previous != note,
        "targetCount": len(sidecar["references"][key].get("targets") or []),
        "referenceCount": len(sidecar["references"]),
        "digestBefore": digest,
        "digestAfter": after,
    }
