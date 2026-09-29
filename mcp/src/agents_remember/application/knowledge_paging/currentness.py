"""Currentness for the pages of one walk, computed once and cut to each candidate (MIK-R02/R03).

A bounded page is found by rendering candidate pages until one fits, and a page that carries a
``currentness`` block has to be measured *with* it. Recomputing the block for every candidate
would open the index and observe the code again on each try, so this computes the state of every
invariant and family the walk's candidate rows name once, at the walk's code tree, and then renders
each candidate's block as the subset its own rows name. The subset is exactly the block
:func:`~agents_remember.application.knowledge_currentness.read_currentness` would compute for that
answer: the same records, the same order, and each family's stale members counted over all of its
members.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_currentness.observe import CodeTree
from agents_remember.application.knowledge_currentness.state import Currentness
from agents_remember.application.knowledge_currentness.surface import (
    CURRENTNESS_FAILURES,
    evaluate_answer,
    failure_document,
    named_uuids,
)

__all__ = ["WalkCurrentness"]


class WalkCurrentness:
    """The currentness of every record a walk's candidate rows name, at one code tree."""

    def __init__(
        self, index_path: Path, tree_key: str, code_tree: CodeTree | None, candidates: Any
    ) -> None:
        self._code_tree = code_tree
        self._failure: dict[str, Any] | None = None
        self._records: dict[str, tuple[str, str]] = {}
        self._full: Currentness | None = None
        try:
            self._records, self._full = evaluate_answer(index_path, tree_key, code_tree, candidates)
        except CURRENTNESS_FAILURES as error:
            self._failure = failure_document(code_tree, error)

    def document(self, answer: Any) -> dict[str, Any]:
        """The ``currentness`` block of one candidate answer."""

        if self._failure is not None or self._full is None:
            return dict(self._failure or failure_document(self._code_tree, ValueError("none")))
        full = self._full
        named = [self._records[value] for value in named_uuids(answer) if value in self._records]
        families = {record for kind, record in named if kind == "family"}
        wanted = {record for kind, record in named if kind == "invariant"}
        for family in full.families:
            if family.id in families:
                wanted.update(family.members)
        return Currentness(
            code_tree=full.code_tree,
            problem=full.problem,
            invariants=tuple(one for one in full.invariants if one.id in wanted),
            families=tuple(one for one in full.families if one.id in families),
        ).to_document()
