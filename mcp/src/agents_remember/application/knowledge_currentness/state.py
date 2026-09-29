"""Invariant currentness at one code tree: the one state function (MIK-R03 rules 2 to 4).

:func:`invariant_currentness` is a function of (code tree, memory tree): the memory tree is given as
its derived index (MIK-R23), which every read is served from, and the code tree as the tree the
caller resolved (or ``None`` when none was requested). ``knowledge_read`` and the published-intent
block call it; the reviewer (MIK-R25) calls it once per side and the path-based reader (MIK-R29) at
its selected commit.

**Invariant state.** The first rule that applies wins:

1. ``stale`` if any entry, realization or proof, is ``stale``;
2. otherwise ``unverifiable`` if any entry is ``unverifiable``;
3. otherwise ``unrealized`` if it has no realization entries (proof entries do not count here);
4. otherwise ``current``.

**Visibility.** A stale invariant stays in the answer with every entry that is not ``current``, each
with its state and reason; nothing is withheld (D9). Invariants are counted by state, and each
family header counts its stale members. The statement and relationships stay in the read's own
payload, which this module never edits.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final, Literal

from agents_remember.application.knowledge_currentness.observe import (
    OBSERVATIONS,
    CodeTree,
    EntryObservation,
    ObservationKey,
    observe_entry,
    open_code_tree,
)
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo
from agents_remember.memory.knowledge_index import KnowledgeIndex

__all__ = [
    "INVARIANT_STATES",
    "Currentness",
    "FamilyCurrentness",
    "InvariantCurrentness",
    "InvariantState",
    "invariant_currentness",
    "invariant_state",
]

InvariantState = Literal["stale", "unverifiable", "unrealized", "current"]
INVARIANT_STATES: Final[tuple[InvariantState, ...]] = (
    "stale",
    "unverifiable",
    "unrealized",
    "current",
)


def invariant_state(observations: Iterable[EntryObservation]) -> InvariantState:
    """The state of one invariant from its entries' observations (rule 2, in order)."""

    observed = tuple(observations)
    if any(one.state == "stale" for one in observed):
        return "stale"
    if any(one.state == "unverifiable" for one in observed):
        return "unverifiable"
    if not any(one.kind == "realization" for one in observed):
        return "unrealized"
    return "current"


@dataclass(frozen=True)
class InvariantCurrentness:
    """One invariant's state, and each of its entries that is not ``current``."""

    id: str
    state: InvariantState
    entries: tuple[EntryObservation, ...]

    @property
    def differing(self) -> tuple[EntryObservation, ...]:
        return tuple(entry for entry in self.entries if entry.state != "current")

    def to_document(self, *, compact: bool = False) -> dict[str, Any]:
        """``compact`` names each differing entry by ID, kind, path and state only: used when one
        read-wide reason (``unverifiableReason``) already explains every entry (review N4)."""

        return {
            "id": self.id,
            "state": self.state,
            "entries": [
                {"id": e.entry_id, "kind": e.kind, "path": e.path, "state": e.state}
                if compact
                else e.to_document()
                for e in self.differing
            ],
        }


@dataclass(frozen=True)
class FamilyCurrentness:
    """A family header: its members and how many of them are stale."""

    id: str
    members: tuple[str, ...]
    stale_members: tuple[str, ...]

    def to_document(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "members": len(self.members),
            "staleMembers": len(self.stale_members),
            "stale": list(self.stale_members),
        }


@dataclass(frozen=True)
class Currentness:
    """The currentness of a read's invariants and families at one code tree."""

    code_tree: CodeTree | None
    problem: str | None
    invariants: tuple[InvariantCurrentness, ...]
    families: tuple[FamilyCurrentness, ...]

    def of(self, invariant_id: str) -> InvariantCurrentness | None:
        return next((one for one in self.invariants if one.id == invariant_id), None)

    def counts(self) -> dict[str, int]:
        return {
            state: sum(1 for one in self.invariants if one.state == state)
            for state in INVARIANT_STATES
        }

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "codeTree": None if self.code_tree is None else self.code_tree.to_document(),
            "counts": self.counts(),
            "invariants": [
                one.to_document(compact=self.problem is not None) for one in self.invariants
            ],
            "families": [one.to_document() for one in self.families],
        }
        if self.problem is not None:
            document["unverifiableReason"] = self.problem
        return document


def invariant_currentness(
    code_tree: CodeTree | None,
    index: KnowledgeIndex,
    invariants: Iterable[str],
    families: Iterable[str] = (),
    *,
    cache: BoundedMemo[ObservationKey, str | None] = OBSERVATIONS,
) -> Currentness:
    """The state of each named invariant, and of every member of each named family, at ``code_tree``.

    ``index`` is the memory tree's derived index. An ID the index does not hold as a record is left
    out rather than guessed at. A family's members are evaluated whether or not the read named them,
    because its header counts its stale members. Reads only: nothing is written or re-anchored.
    """

    code = open_code_tree(code_tree)
    family_members = {
        family: index.family(family).value.members
        for family in sorted(set(families))
        if _is_record(index, family, "family")
    }
    wanted = set(invariants).union(*family_members.values())
    evaluated: dict[str, InvariantCurrentness] = {}
    for invariant in sorted(wanted):
        if not _is_record(index, invariant, "invariant"):
            continue
        knowledge = index.invariant(invariant).value
        observations = tuple(
            observe_entry(entry, code, cache=cache)
            for entry in (*knowledge.realizations, *knowledge.proofs)
        )
        evaluated[invariant] = InvariantCurrentness(
            id=invariant, state=invariant_state(observations), entries=observations
        )
    return Currentness(
        code_tree=code_tree,
        problem=code.problem,
        invariants=tuple(evaluated.values()),
        families=tuple(
            FamilyCurrentness(
                id=family,
                members=members,
                stale_members=tuple(
                    member
                    for member in members
                    if member in evaluated and evaluated[member].state == "stale"
                ),
            )
            for family, members in family_members.items()
        ),
    )


def _is_record(index: KnowledgeIndex, record_id: str, kind: str) -> bool:
    record = index.record(record_id).value
    return record is not None and record.kind == kind
