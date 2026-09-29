"""Turn one legacy citation row into one reference (MIK-R24 rules 1 and 2).

A row is anchored in the code tree of its card's anchor commit (the card's
``lastVerifiedCommitHash``, or the conversion's paired code tree when that commit is missing). Its
targets, in order:

1. **Symbols.** Each backticked identifier anchor that the shipped extractor binds exactly once in
   one of the row's cited files (tried in citation order) becomes a ``symbol`` target in that file.
2. **Ranges.** Each ``path:start-end`` source becomes its own ``line_range`` target, unless a symbol
   target of the row in the same file lies inside the range or contains it: that range is covered
   and adds nothing. A path the tree does not hold, or a range past the file's end, becomes an
   ``unresolved`` target carrying the source text.
3. **Other sources.** A source segment that is not ``path:start-end``: a whole file the tree holds
   becomes a ``file`` target, a URL an ``external`` document, anything else ``unresolved``.

Anchors that are not symbols (quoted literals, headings) and identifiers the extractor does not bind
add no target of their own: the ranges they sat in carry the location (rule 2's "otherwise"). A row
that yields no target at all keeps its citation text as one ``unresolved`` target, so no row is
silently dropped. The row's finding is the reference's ``note``.

Every anchor records the blob it was resolved in and ``content`` computed through
:mod:`...models.knowledge_files.anchor_content`, the one definition the writer uses.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Final

from agents_remember.memory.conversion.cards import CitationRow
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory_quality.style.citations import model
from agents_remember.models.knowledge_files.shapes import require_repository_path

_REASON: Final = "_reason"
_TEST_DIRECTORIES: Final = frozenset({"tests", "test", "__tests__", "e2e"})
_TEST_NAME: Final = re.compile(
    r"^(?:test_.+\.py|.+_test\.py|.+\.(?:test|spec)\.[A-Za-z0-9]+|conftest\.py)$"
)


def is_test_path(path: str) -> bool:
    """A test file: under a ``tests``-like directory, or named like a test module."""

    parts = PurePosixPath(path).parts
    return bool(_TEST_DIRECTORIES & set(parts[:-1])) or bool(_TEST_NAME.match(parts[-1]))


@dataclass
class TargetTally:
    """Counts of the targets written, by kind, and every unresolved one with its reason."""

    by_kind: Counter[str] = field(default_factory=Counter)
    unresolved: list[dict[str, str]] = field(default_factory=list)
    covered_ranges: int = 0
    anchor_texts_kept: int = 0


@dataclass(frozen=True)
class RowContext:
    """What a row is resolved against: the card, its anchor commit and tree, and the objects."""

    card: str
    own_path: str | None
    tree: Mapping[str, str]
    objects: CodeObjects
    route_sidecar: bool


def _plain_path(value: str) -> str | None:
    try:
        return require_repository_path(value)
    except ValueError:
        return None


def _anchor(context: RowContext, path: str, locator: dict[str, Any]) -> dict[str, Any] | None:
    blob = context.tree[path]
    content = context.objects.content(blob, locator, path)
    if content is None:
        return None
    anchor: dict[str, Any] = {"locator": locator, "blob": blob, "content": content}
    if context.route_sidecar or path != context.own_path:
        anchor = {"path": path, **anchor}
    return {"kind": "test" if is_test_path(path) else "code", "anchor": anchor}


def _unresolved(text: str, reason: str) -> dict[str, Any]:
    return {"kind": "unresolved", "text": text, _REASON: reason}


def _symbol_targets(
    row: CitationRow, context: RowContext
) -> list[tuple[str, tuple[int, int], dict[str, Any]]]:
    cited = list(
        dict.fromkeys(
            citation.path for citation in row.claim.citations if citation.path in context.tree
        )
    )
    found: list[tuple[str, tuple[int, int], dict[str, Any]]] = []
    for anchor in row.claim.anchors:
        if anchor.kind != model.SYMBOL:
            continue
        for path in cited:
            span = context.objects.symbol_span(path, context.tree[path], anchor.text)
            if span is None:
                continue
            target = _anchor(context, path, {"kind": "symbol", "name": anchor.text})
            if target is not None:
                found.append((path, span, target))
            break
    return found


def _covered(start: int, end: int, span: tuple[int, int]) -> bool:
    inside = start <= span[0] and span[1] <= end
    contains = span[0] <= start and end <= span[1]
    return inside or contains


def _source_targets(
    row: CitationRow,
    context: RowContext,
    symbols: list[tuple[str, tuple[int, int], dict[str, Any]]],
    tally: TargetTally,
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for citation in row.claim.citations:
        path = _plain_path(citation.path)
        if path is None or path not in context.tree:
            targets.append(_unresolved(citation.text, "path-absent"))
            continue
        if any(
            where == path and _covered(citation.start, citation.end, span)
            for where, span, _ in symbols
        ):
            tally.covered_ranges += 1
            continue
        locator = {"kind": "line_range", "start": citation.start, "end": citation.end}
        target = _anchor(context, path, locator)
        if target is None:
            targets.append(_unresolved(citation.text, "range-outside-file"))
        else:
            targets.append(target)
    for segment in row.claim.malformed:
        path = _plain_path(segment)
        if path is not None and path in context.tree:
            target = _anchor(context, path, {"kind": "file"})
            if target is not None:
                targets.append(target)
                continue
        if "://" in segment and not any(character.isspace() for character in segment):
            targets.append({"kind": "external", "document": {"document": segment}})
            continue
        targets.append(_unresolved(segment, "not-a-source"))
    return targets


def row_reference(row: CitationRow, context: RowContext, tally: TargetTally) -> dict[str, Any]:
    """Return the reference ``{targets, note?}`` for one real citation row."""

    symbols = _symbol_targets(row, context)
    targets: list[dict[str, Any]] = [target for _, _, target in symbols]
    targets.extend(_source_targets(row, context, symbols, tally))
    unique: list[dict[str, Any]] = []
    for target in targets:
        if target not in unique:
            unique.append(target)
    if not unique:
        text = " ".join(part for part in (row.anchor_cell, row.source_cell) if part) or row.finding
        unique.append(_unresolved(text, "no-target"))
    for target in unique:
        reason = target.pop(_REASON, None)
        if reason is not None:
            tally.unresolved.append(
                {"card": context.card, "text": target["text"], "reason": reason}
            )
        tally.by_kind[_tally_kind(target)] += 1
    names = {
        target["anchor"]["locator"]["name"]
        for target in unique
        if target["kind"] in {"code", "test"} and target["anchor"]["locator"]["kind"] == "symbol"
    }
    lost = unbound_anchor_text(row.anchor_cell, names)
    tally.anchor_texts_kept += bool(lost)
    note = reference_note(row.finding, lost)
    reference: dict[str, Any] = {"targets": unique}
    if note:
        reference["note"] = note
    return reference


def unbound_anchor_text(anchor_cell: str, symbol_names: set[str]) -> str:
    """The anchor cell's text that no symbol target carries, byte for byte as written.

    A quoted literal, a heading, an identifier the extractor does not bind, and any other anchor
    text has no target field of its own; its location survives as a ``line_range`` target and its
    text is kept in the reference's ``note`` (the architect's ruling on rule 1), so no citation text
    is lost. Only the segments that are just a bound symbol's ``name`` (carried by its target) and
    empty markers are cut out; everything else keeps its original characters and separators, even
    where a separator sits inside a quoted literal.
    """

    kept: list[str] = []
    position = 0
    for piece in model.split_segments(anchor_cell):
        end = position + len(piece)
        separator = anchor_cell[end : end + 1]
        text = piece.strip()
        carried = (
            text.startswith("`") and text.endswith("`") and text.strip("`").strip() in symbol_names
        )
        if (
            text
            and text.strip("`").strip().lower() not in model.NO_CITATION_MARKERS
            and not carried
        ):
            kept.append(piece + separator)
        position = end + 1
    return "".join(kept).strip().rstrip(model.SEGMENT_SEPARATORS).strip()


ANCHOR_NOTE_LABEL: Final = "Anchor: "


def reference_note(finding: str, anchor_text: str) -> str:
    """``<finding>``, then ``Anchor: <text>`` on its own paragraph when anchor text is kept."""

    parts = [finding.strip()] if finding.strip() else []
    if anchor_text:
        parts.append(ANCHOR_NOTE_LABEL + anchor_text)
    return "\n\n".join(parts)


def _tally_kind(target: Mapping[str, Any]) -> str:
    kind = str(target["kind"])
    if kind in {"code", "test"}:
        return f"{kind}:{target['anchor']['locator']['kind']}"
    return kind
