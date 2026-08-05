"""Read the superseded citation format without writing it.

Old tables use ``Finding | Citations | Source Path`` (or omit Citations), Markdown links,
and bare ``L`` ranges. Source links are resolved across the live repo-relative, card-
relative, repo-name-prefixed, and sidecar-target spellings.

An old range is only a verified hint for choosing among identical anchors. Output ranges
always come from current anchor extents; an unverified old range contributes nothing.
Finding text is preserved and may supply the sole unambiguous anchor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agents_remember.memory_quality.style.citations import model
from agents_remember.memory_quality.style.citations.resolution import Trees

LINK = re.compile(r"\[(?P<label>[^\]]*)\]\((?P<target>[^)]*)\)")
OLD_RANGE = re.compile("L(?P<start>\\d+)\\s*[-\u2013\u2014]\\s*L?(?P<end>\\d+)|L(?P<one>\\d+)")
MARKDOWN_SUFFIX = ".md"
URL_MARK = "://"

CITATIONS_COLUMN = "citations"
SOURCE_PATH_COLUMN = "source path"
FINDING_COLUMN = "finding"


@dataclass(frozen=True)
class Span:
    """One range read out of an old document, in the file it was written about."""

    start: int
    end: int


def link_targets(cell: str) -> list[str]:
    """Every path this Source Path cell names, in order, however it spelled them.

    A cell holding no link at all but wrapped in backticks is a bare path, which two rows
    in this tree use. A cell holding neither is not a source and yields nothing.
    """
    found = [match.group("target").strip() for match in LINK.finditer(cell)]
    if found:
        return found
    bare = model.unwrapped(cell)
    return [bare] if bare and bare != cell.strip() else []


def path_candidates(
    target: str, document: Path, onboarding_root: Path, repository: str
) -> list[str]:
    """Every repo-relative spelling of ``target`` worth trying, most specific first.

    Nothing here decides anything: :func:`resolved_path` picks the one that names a file
    that exists, so a spelling that resolves to nothing costs a stat and is dropped.
    """
    found: list[str] = []
    if not target.startswith("/"):
        found.extend(_mirrored(target, document, onboarding_root))
    found.append(target.lstrip("/"))
    if repository and target.startswith(f"{repository}/"):
        found.append(target[len(repository) + 1 :])
    # A SIDECAR TARGET NAMES THE FILE IT DOCUMENTS, NOT ITSELF. This is the second gate on
    # the rule `resolution` states in full -- a memory document is never what an anchor
    # resolves to -- and `symbol_index.walk` is the first. Change either and change both.
    # A genuine memory document such as `overview.md` is unaffected: `overview` names
    # nothing, so its own spelling is still reached.
    stripped = [one[: -len(MARKDOWN_SUFFIX)] for one in found if one.endswith(MARKDOWN_SUFFIX)]
    return stripped + found


def _mirrored(target: str, document: Path, onboarding_root: Path) -> list[str]:
    """``../src/x.ts`` read against the card's own place in the mirrored tree.

    The memory tree mirrors the code tree, so a link that climbs out of one card's
    directory lands on another card's -- and the same relative path under the code root is
    the file that card documents. Both spellings are returned because a memory-tree target
    is cited as ``onboarding/...`` while a code-tree one is not.
    """
    try:
        inside = (document.parent / target).resolve().relative_to(onboarding_root.resolve())
    except (ValueError, OSError):
        return []
    return [inside.as_posix(), (Path(onboarding_root.name) / inside).as_posix()]


def resolved_path(
    target: str, document: Path, onboarding_root: Path, repository: str, trees: Trees
) -> str | None:
    """The one spelling of ``target`` that names a file in either tree, or ``None``.

    A URL is never a candidate: ``path:start-end`` cannot express one, so a row citing only
    a URL is reported rather than converted.
    """
    if URL_MARK in target:
        return None
    return next(
        (
            one
            for one in path_candidates(target, document, onboarding_root, repository)
            if trees.resolve(one) is not None
        ),
        None,
    )


def old_span(cell: str) -> Span | None:
    """The first ``L`` range in a Citations cell, as numbers. A tiebreaker, never an output."""
    match = OLD_RANGE.search(cell)
    if match is None:
        return None
    start = match.group("start") or match.group("one")
    end = match.group("end") or match.group("one")
    return Span(start=int(start), end=int(end))


def verified_hint(
    anchors: tuple[model.Anchor, ...], span: Span | None, lines: list[str]
) -> Span | None:
    """``span`` if EVERY anchor really occurs inside it in ``path``, otherwise ``None``.

    Only a verified span may disambiguate repeated anchor occurrences.
    """
    if span is None or not anchors or span.end > len(lines) or span.start < 1:
        return None
    body = "\n".join(lines[span.start - 1 : span.end])
    return span if all(model.occurs_in(anchor, body) for anchor in anchors) else None


def is_marker(cell: str) -> bool:
    """Whether a cell is one of the four spellings this tree uses for 'nothing to cite'."""
    return model.unwrapped(cell).lower() in model.NO_CITATION_MARKERS


def marker_of(cells: list[str]) -> str:
    """The no-citation marker a table already uses, so a padded row does not invent a fifth.

    Measured over the memory root: 1,135 tables use one and NOT ONE mixes two, so the first
    marker found in the table is the table's marker.
    """
    return next((model.unwrapped(one) for one in cells if is_marker(one) and one.strip()), "")
