"""The ``currentness`` block beside leaf pages, cut to what each page returns (MIK-R03 with MIK-R01).

A leaf page states each entry's and member's state in its own rows; the response also carries the
``currentness`` block every read of a converted tree carries (MIK-R03 rule 4), with the reasons of
the entries that are not current. Each prepared leaf already computed its selection's currentness
once, at the walk's code tree, so the block of one candidate page is the subset its rows name --
the same rule :class:`~agents_remember.application.knowledge_paging.currentness.WalkCurrentness`
applies to rows that name records by projected UUID:

* an invariant is named by its ``member`` or ``member_reference`` row, or by an entry row;
* a family is named by its ``family_header`` row or the page's ``family_header_reference`` row, and
  a named family brings all of its members' states (its header counts its stale members).

An advertised family is not selected, so it names nothing here. A block that also carries scope
pages (identity seeds) merges their subset in.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, Final

from agents_remember.application.knowledge_currentness.observe import CodeTree
from agents_remember.application.knowledge_currentness.state import (
    Currentness,
    FamilyCurrentness,
    InvariantCurrentness,
)
from agents_remember.application.knowledge_leaf.pages import PreparedLeaf
from agents_remember.application.knowledge_paging.currentness import WalkCurrentness

__all__ = ["LeafCurrentness"]

_INVARIANT_ROWS: Final = frozenset({"member", "member_reference"})
_ENTRY_ROWS: Final = frozenset({"realization", "proof"})
_FAMILY_ROWS: Final = frozenset({"family_header", "family_header_reference"})


class LeafCurrentness:
    """The currentness of every leaf a response carries, cut to each candidate answer."""

    def __init__(self, code_tree: CodeTree | None, leaves: Sequence[PreparedLeaf]) -> None:
        self._code_tree = code_tree
        self._any = bool(leaves)
        self._problem = next(
            (one.currentness.problem for one in leaves if one.currentness.problem), None
        )
        self._invariants: dict[str, InvariantCurrentness] = {}
        self._families: dict[str, FamilyCurrentness] = {}
        for leaf in leaves:
            for invariant in leaf.currentness.invariants:
                self._invariants.setdefault(invariant.id, invariant)
            for family in leaf.currentness.families:
                self._families.setdefault(family.id, family)

    def subset(self, answer: Any) -> Currentness:
        """The currentness of the records the leaf rows of ``answer`` name."""

        invariants, families = _named(answer)
        for family in families:
            known = self._families.get(family)
            if known is not None:
                invariants.update(known.members)
        return Currentness(
            code_tree=self._code_tree,
            problem=self._problem,
            invariants=tuple(self._invariants[i] for i in sorted(invariants & {*self._invariants})),
            families=tuple(self._families[f] for f in sorted(families & {*self._families})),
        )

    def document(self, answer: Any, beside: WalkCurrentness | None = None) -> dict[str, Any]:
        """The ``currentness`` block of one answer, merged with ``beside``'s scope subset if any."""

        if not self._any and beside is not None:
            return beside.document(answer)
        own = self.subset(answer)
        other = None if beside is None else beside.subset(answer)
        if other is None or not (other.invariants or other.families):
            return own.to_document()
        return _merged(own, other).to_document()


def _merged(own: Currentness, other: Currentness) -> Currentness:
    """The leaf subset and the scope subset of one block as one currentness, by ID."""

    invariants = {one.id: one for one in (*other.invariants, *own.invariants)}
    families = {one.id: one for one in (*other.families, *own.families)}
    return Currentness(
        code_tree=own.code_tree,
        problem=own.problem or other.problem,
        invariants=tuple(invariants[key] for key in sorted(invariants)),
        families=tuple(families[key] for key in sorted(families)),
    )


def _named(answer: Any) -> tuple[set[str], set[str]]:
    invariants: set[str] = set()
    families: set[str] = set()
    for row in _rows(answer):
        kind = row.get("kind")
        if kind in _INVARIANT_ROWS:
            invariants.add(str(row.get("id")))
        elif kind in _ENTRY_ROWS and "invariant" in row:
            invariants.add(str(row["invariant"]))
        elif kind in _FAMILY_ROWS:
            families.add(str(row.get("id")))
    return invariants, families


def _rows(answer: Any) -> Iterator[dict[str, Any]]:
    """Every row dictionary inside ``answer``'s leaf pages (their ``rows`` lists)."""

    if isinstance(answer, dict):
        rows = answer.get("rows")
        if isinstance(rows, list):
            yield from (row for row in rows if isinstance(row, dict))
        for key, value in answer.items():
            if key != "rows":
                yield from _rows(value)
    elif isinstance(answer, list | tuple):
        for item in answer:
            yield from _rows(item)
