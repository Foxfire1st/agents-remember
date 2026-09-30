"""Each registered worklist kind's satisfying rule over a stored item (MIK-R09 rules 1, 2 and 7).

The gate decides every item of the recomputed worklist through **its own kind's predicate**,
registered here beside the kind (:func:`register_gate_predicate`), never through a generic row
lookup: kinds differ in what satisfies them (carried from L06, L10, L14 and L30). A predicate takes
the item document and the :class:`GateContext` -- the leaf's parsed history file from K_C, K_C
itself and the code tree C -- and returns why the item is still open, or ``None`` when it is
satisfied. An item whose kind has no predicate is open: nothing that cannot be decided passes.

* ``touched_invariant`` / ``stale_invariant`` -- the leaf's **invariant row** about the invariant is
  current (rule 2): its ``covers`` include every entry the facts list as ``touched``,
  ``moved_or_absent`` or ``stale_at_base`` and every added, retired or re-anchored entry; each
  covered entry that still exists in K_C has the MIK-R03 state ``current`` at C; its ``revision``
  is the invariant's revision in K_C.
* ``reached_family`` -- the leaf's **family row** is current: ``examined`` names exactly the union
  of the family's K_B and K_C members; every examined revision is that member's K_C revision (or the
  member is absent from K_C), so a sibling whose meaning changed after the row reopens it (D7); and
  no member has an open invariant item.
* ``family_route_condition`` -- :func:`family_route_item_open` over the parsed history file, decided
  by the item's **subject** ``<FAM-ID>#<condition>``, so an item whose ID changed between recomputes
  (L06 review N2) is answered by the same row.
* ``onboarding_trace`` -- :func:`onboarding_item_open`: a counted change, or the ``no_impact`` row.
* ``unexplained_hunk`` / ``unexplained_file`` -- :func:`unexplained_item_open`.
* ``planned_untouched`` -- :func:`planned_item_open`.
* ``reconsideration_candidate`` -- :func:`reconsideration_item_open`: an unanswered candidate
  blocks closeout (D29).

Only authored rows satisfy items (rule 5); nothing here writes, and there is no waiver. An entry
whose state cannot be observed because a Git read failed or timed out raises
:class:`GitReadFailed`: the gate makes the whole run ``incomplete`` rather than deciding an item on
a read that did not happen (L09 review R1, finding 9).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Final

from agents_remember.application.knowledge_currentness.observe import (
    EntryObservation,
    OpenedCodeTree,
    observe_entry,
)
from agents_remember.application.knowledge_worklist.classify import COVERING_CLASSES
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.registry import ITEM_KINDS
from agents_remember.application.knowledge_worklist.route_conditions import (
    family_route_item_open,
)
from agents_remember.memory.knowledge_index import Entry
from agents_remember.models.knowledge_files.history import FamilyRow, HistoryFile, InvariantRow
from agents_remember.models.knowledge_files.planned import planned_item_open
from agents_remember.models.knowledge_files.reconsideration import reconsideration_item_open
from agents_remember.models.knowledge_files.sidecars import ProofEntry
from agents_remember.models.knowledge_files.unexplained import unexplained_item_open
from agents_remember.worktrees.modules.onboarding_trace import onboarding_item_open

__all__ = [
    "GATE_PREDICATES",
    "INVARIANT_KINDS",
    "GateContext",
    "GitReadFailed",
    "ItemPredicate",
    "item_open_reason",
    "register_gate_predicate",
]

INVARIANT_KINDS: Final = ("touched_invariant", "stale_invariant")


class GitReadFailed(Exception):
    """An entry's MIK-R03 state could not be observed: a Git read failed or timed out."""


@dataclass(frozen=True)
class GateContext:
    """What a predicate reads: the leaf's history in K_C, K_C itself, and the code tree C."""

    owner: str | None
    history: HistoryFile | None
    candidate: KnowledgeSide
    code: OpenedCodeTree
    open_invariants: frozenset[str] = field(default=frozenset())
    """Invariants with an open ``touched_invariant`` or ``stale_invariant`` item (the family rule)."""

    @cached_property
    def rows_by_subject(self) -> dict[str, str]:
        """Each row subject of the leaf's history file, to its row ID."""

        return {} if self.history is None else {row.subject: row.id for row in self.history.rows}

    def entry_state(self, entry_id: str) -> EntryObservation | None:
        """The MIK-R03 state at C of an entry of K_C; ``None`` when K_C no longer holds it.

        Raises :class:`GitReadFailed` when the state is ``unverifiable`` because a read failed.
        """

        indexed = self.candidate.entries.get(entry_id)
        if indexed is None:
            return None
        entry = Entry(
            id=entry_id,
            kind="proof" if isinstance(indexed.entry, ProofEntry) else "realization",
            invariant=indexed.entry.invariant,
            path=indexed.path,
            sidecar=indexed.sidecar,
            document={"anchor": indexed.entry.anchor.to_document()},
        )
        observed = observe_entry(entry, self.code)
        if observed.read_failed:
            raise GitReadFailed(f"entry {entry_id} at {indexed.path}: {observed.reason}")
        return observed


ItemPredicate = Callable[[Mapping[str, Any], GateContext], str | None]
"""``(item, context) -> why the item is open``, or ``None`` when a current row satisfies it."""

GATE_PREDICATES: Final[dict[str, ItemPredicate]] = {}


def register_gate_predicate(kind: str, predicate: ItemPredicate) -> ItemPredicate:
    """Register ``kind``'s stored-item predicate; the kind must be registered, and only once."""

    if kind not in ITEM_KINDS:
        raise ValueError(f"no worklist item kind {kind!r} is registered")
    if kind in GATE_PREDICATES:
        raise ValueError(f"a gate predicate for {kind!r} is already registered")
    GATE_PREDICATES[kind] = predicate
    return predicate


def item_open_reason(item: Mapping[str, Any], context: GateContext) -> str | None:
    """Why ``item`` is open, through its kind's own predicate; an undecidable item is open."""

    kind = str(item.get("kind"))
    predicate = GATE_PREDICATES.get(kind)
    if predicate is None:
        return (
            f"no gate predicate is registered for the item kind {kind!r}, so it cannot be answered"
        )
    return predicate(item, context)


# --------------------------------------------------------------------------------------------------
# Invariant rows (rule 2)
# --------------------------------------------------------------------------------------------------


def _ids(facts: Iterable[Any]) -> set[str]:
    return {str(one["id"]) for one in facts if isinstance(one, Mapping) and "id" in one}


def required_covers(item: Mapping[str, Any]) -> set[str]:
    """The entries an invariant row must cover: the raising and stale entries, and every added,
    retired or re-anchored one."""

    facts = item.get("facts") or {}
    entries = facts.get("entries") or ()
    if item.get("kind") == "stale_invariant":
        required = _ids(entries)  # every entry a stale item names is stale at base
    else:
        required = _ids(
            one
            for one in entries
            if isinstance(one, Mapping) and one.get("class") in COVERING_CLASSES
        )
    for key in ("added", "retired", "reanchored"):
        required |= _ids(facts.get(key) or ())
    return required


def _not_current(row: InvariantRow, context: GateContext) -> list[str]:
    reasons = []
    for cover in row.covers:
        observed = context.entry_state(cover.id)
        if observed is not None and observed.state != "current":
            reasons.append(f"{cover.id} is {observed.state} ({observed.reason})")
    return reasons


def invariant_row_open(item: Mapping[str, Any], context: GateContext) -> str | None:
    """Rule 2's invariant-row currentness for a ``touched_invariant`` or ``stale_invariant``."""

    subject = str(item.get("subject"))
    row = None if context.history is None else context.history.row_about(subject)
    if not isinstance(row, InvariantRow):
        return f"the leaf's history file holds no invariant row about {subject}"
    missing = sorted(required_covers(item) - {cover.id for cover in row.covers})
    if missing:
        return f"row {row.id} does not cover {', '.join(missing)}"
    revision = context.candidate.revision(subject)
    if row.revision != revision:
        return (
            f"row {row.id} records revision {row.revision}, but {subject} is at revision "
            f"{revision} in K_C"
        )
    stale = _not_current(row, context)
    if stale:
        return f"row {row.id} covers entries not current at C: {'; '.join(stale)}"
    return None


# --------------------------------------------------------------------------------------------------
# Family rows (rule 2)
# --------------------------------------------------------------------------------------------------


def family_row_open(item: Mapping[str, Any], context: GateContext) -> str | None:
    """Rule 2's family-row currentness for a ``reached_family`` item."""

    subject = str(item.get("subject"))
    row = None if context.history is None else context.history.row_about(subject)
    if not isinstance(row, FamilyRow):
        return f"the leaf's history file holds no family row about {subject}"
    members = _ids((item.get("facts") or {}).get("members") or ())
    return (
        _examined_set_reason(row, members)
        or _moved_members_reason(row, context)
        or _uncovered_members_reason(members, context)
    )


def _examined_set_reason(row: FamilyRow, members: set[str]) -> str | None:
    """``examined`` must name exactly the union of the family's K_B and K_C members."""

    examined = {member.id for member in row.examined}
    unexamined, extra = sorted(members - examined), sorted(examined - members)
    parts = [f"does not examine {', '.join(unexamined)}"] if unexamined else []
    parts += [f"examines non-members {', '.join(extra)}"] if extra else []
    if not parts:
        return None
    return f"row {row.id} {' and '.join(parts)} (examined must be the K_B and K_C members)"


def _moved_members_reason(row: FamilyRow, context: GateContext) -> str | None:
    """Each examined revision is the member's K_C revision, or the member left K_C (D7)."""

    moved = sorted(
        f"{member.id} (examined at {member.revision}, now {now})"
        for member in row.examined
        if (now := context.candidate.revision(member.id)) is not None and now != member.revision
    )
    if not moved:
        return None
    return f"row {row.id} examined members whose revision changed since: {', '.join(moved)}"


def _uncovered_members_reason(members: set[str], context: GateContext) -> str | None:
    """No member may have an open invariant item."""

    uncovered = sorted(members & context.open_invariants)
    return f"members without a current invariant row: {', '.join(uncovered)}" if uncovered else None


# --------------------------------------------------------------------------------------------------
# The registrants' own predicates
# --------------------------------------------------------------------------------------------------


def _by_rows(
    predicate: Callable[[Mapping[str, Any], Mapping[str, str]], bool],
) -> ItemPredicate:
    def decide(item: Mapping[str, Any], context: GateContext) -> str | None:
        if not predicate(item, context.rows_by_subject):
            return None
        return "no satisfying row or change answers it"

    return decide


def _route_condition_open(item: Mapping[str, Any], context: GateContext) -> str | None:
    if not family_route_item_open(item, context.history):
        return None
    facts = item.get("facts") or {}
    if facts.get("recordSatisfiesRoutes") is not True:
        return "the family record in K_C does not satisfy MIK-R04 (routes), or no family row answers it"
    return "no family row about the family has a satisfying disposition"


register_gate_predicate("touched_invariant", invariant_row_open)
register_gate_predicate("stale_invariant", invariant_row_open)
register_gate_predicate("reached_family", family_row_open)
register_gate_predicate("family_route_condition", _route_condition_open)
register_gate_predicate("onboarding_trace", _by_rows(onboarding_item_open))
register_gate_predicate("unexplained_hunk", _by_rows(unexplained_item_open))
register_gate_predicate("unexplained_file", _by_rows(unexplained_item_open))
register_gate_predicate("planned_untouched", _by_rows(planned_item_open))
register_gate_predicate("reconsideration_candidate", _by_rows(reconsideration_item_open))
