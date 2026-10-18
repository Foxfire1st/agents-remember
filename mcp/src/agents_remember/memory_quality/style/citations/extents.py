"""Resolve symbol bindings shared by conversion, reference reads and the writer."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agents_remember.memory_quality.style.citations import grammars

DEFINITION = "definition"


# Re-exported so nothing outside :mod:`grammars` has to know a suffix to ask whether a
# definition in a file is distinguishable from a mention of it.


@dataclass(frozen=True)
class Extent:
    """One range an anchor occupies in a file, and how it was found."""

    start: int
    end: int
    kind: str
    # For a DEFINITION extent, the line the declaration itself begins on. It differs from
    # ``start`` for a decorated definition, whose extent is widened to cover its decorator: the
    # anchor names the declaration, and a citation that starts at the declaration's own first
    # line is about that construct. ``None`` for every other kind, where the two coincide.
    declaration: int | None = None
    test_case: bool = False


def definitions(path: str, lines: Sequence[str]) -> dict[str, list[Extent]]:
    """Every name this file binds at any depth, and the extent of the construct binding it.

    Assignments and declarators count, not only ``def`` and ``class``: an earlier
    measurement of this surface collected definitions only and read badly, because these
    documents cite module-level constants constantly. A file in a language with no grammar,
    and a construct too broken to parse, bind nothing and drop to occurrence matching
    rather than failing the run -- the tree holds source written for other dialects and
    none of it is this check's business. A grammar that will not LOAD is the other case
    entirely and raises; see :mod:`grammars`.
    """
    return {
        name: [
            Extent(
                start=binding.span[0],
                end=binding.span[1],
                kind=DEFINITION,
                declaration=binding.declaration,
                test_case=binding.test_case,
            )
            for binding in items
        ]
        for name, items in grammars.bindings(path, lines).items()
    }


def qualified_spans(name: str, bound: dict[str, list[Extent]]) -> tuple[tuple[int, int], ...]:
    """The distinct extents binding ``name`` in one file's :func:`definitions`.

    ``Holder.method`` is the ``method`` defined inside a ``Holder`` definition, so a same-named
    method of another class in the file does not make the name ambiguous. A symbol anchor names one
    construct only when exactly one span comes back; the curator writer (MIK-R12) and the
    conversion (MIK-R24) both bind symbols through this one rule.
    """
    if name in bound:
        spans = tuple((extent.start, extent.end) for extent in bound[name])
        return (
            spans
            if any(extent.test_case for extent in bound[name])
            else tuple(dict.fromkeys(spans))
        )
    parts = [part for part in name.split(".") if part]
    if not parts or any(part not in bound for part in parts):
        return ()
    candidates = list(bound[parts[-1]])
    for parent in parts[:-1]:
        holders = bound[parent]
        candidates = [
            one
            for one in candidates
            if any(holder.start <= one.start and one.end <= holder.end for holder in holders)
        ]
    return tuple(dict.fromkeys((extent.start, extent.end) for extent in candidates))
