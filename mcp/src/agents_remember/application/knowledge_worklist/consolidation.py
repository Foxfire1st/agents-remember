"""The items of a consolidation pass's list, and how a row answers them.

A consolidation task's document names the list of its pass: families and rules by their ids
(``consolidationList``). Whenever the worklist is computed it adds, beside the items the task's own
changes raise, one ``consolidation_target`` item for each listed family and one for each rule that
is listed or is a member of a listed family -- a set, so a rule two listed families share, or a
listed rule of a listed family, gets one item. Membership is the family's members on either side
(K_B and K_C), as the run reads it for a reached family. A record that is neither listed nor
changed raises no item. The list has no size limit.

**Built beside ``expectedKnowledgeEffects``.** A declared effect expects a change and is answered
by a ``planned:`` row (:mod:`.planned_effects`); a listed record asks to be read again and is
answered by the row about the record itself, so the list is its own declaration on the same input
path.

**Item identity.** An item's subject is the record id and its identity is the record's revision in
K_C, so a new revision of a listed record makes a new item that the old row no longer answers.

**Satisfying row.** The leaf's row about the subject (a rule row for a rule, a family row for a
family) at the subject's K_C revision: any of today's dispositions, ``stands`` or ``unresolved``. A
family row whose examined members moved since is not current. So the one row that today's item asks
for (a touched rule's, a reached family's) answers the list's item of the same subject too.

**Today's items of a listed subject.** ``stands`` and ``unresolved`` answer items of the list only.
Every item of today's invariant and family kinds whose subject is on the list is marked
``consolidationListed`` (:meth:`ConsolidationPass.mark`), so the gate can apply the two exceptions:
an ``unresolved`` row answers today's item of a listed subject (the subject was left as it was, and
the pass reports it up the chain), and a ``stands`` row answers today's rule item whose only change
is a meaning-neutral edit of the record at an unchanged revision -- a title, which is no part of a
rule's meaning -- with no entry to cover.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from agents_remember.application.knowledge_worklist.classify import COVERING_CLASSES
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.registry import (
    ItemKind,
    item_id,
    register_item_kind,
)
from agents_remember.models.knowledge_files.history import (
    FamilyRow,
    HistoryFile,
    HistoryRow,
    InvariantRow,
)
from agents_remember.models.knowledge_files.history_consolidation import STANDS, UNRESOLVED

__all__ = [
    "CONSOLIDATION_KIND",
    "LISTED_MARK",
    "ConsolidationPass",
    "Listing",
    "consolidation_pass",
    "consolidation_target_open",
    "listing_from",
    "new_disposition_reason",
]

CONSOLIDATION_KIND: Final = "consolidation_target"
LISTED_MARK: Final = "consolidationListed"
_MARKED_KINDS: Final = frozenset({"touched_invariant", "stale_invariant", "reached_family"})
_REMEDY: Final = (
    "name it in the hand-off's 'history': `stands` with the revision read and why it stands, "
    "`unresolved` with what contradicts what, or the row of the change made"
)


def _row_about(subject: str, history: HistoryFile | None) -> HistoryRow | None:
    return None if history is None else history.row_about(subject)


register_item_kind(
    ItemKind(
        name=CONSOLIDATION_KIND,
        subject="invariant or family ID on the pass's list (or a member of a listed family)",
        subject_pattern=r"^(?:INV|FAM)-",
        facts=("record", "revision", "families", "listed"),
        satisfying_row=(
            "the leaf's rule or family row about the subject at its current revision: stands "
            "(why it stands), unresolved (what contradicts what), or the row of today's "
            "dispositions for the change made"
        ),
        row_lookup=_row_about,
        owner="KNO-R05",
    )
)


@dataclass(frozen=True)
class Listing:
    """The pass's list as the task document names it."""

    families: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()


def listing_from(listed: Any) -> Listing | None:
    """The task document's ``consolidationList`` (its model or JSON object); ``None`` without one."""

    if listed is None:
        return None
    document = listed if isinstance(listed, Mapping) else listed.model_dump(mode="json")
    listing = Listing(
        families=tuple(document.get("families") or ()), rules=tuple(document.get("rules") or ())
    )
    return listing if listing.families or listing.rules else None


@dataclass(frozen=True)
class ConsolidationPass:
    """The list's items of one run, and the subjects they reach."""

    items: tuple[dict[str, Any], ...] = ()
    subjects: frozenset[str] = field(default=frozenset())

    def mark(self, item: dict[str, Any]) -> dict[str, Any]:
        """``item`` marked ``consolidationListed`` when today's item is about a listed subject."""

        if item.get("kind") in _MARKED_KINDS and item.get("subject") in self.subjects:
            return {**item, LISTED_MARK: True}
        return item


def consolidation_pass(
    listing: Listing | None, base: KnowledgeSide, candidate: KnowledgeSide
) -> ConsolidationPass:
    """One item per listed family and per rule listed or a member of a listed family."""

    if listing is None:
        return ConsolidationPass()
    reach = _Reach(listing, base, candidate)
    for family in listing.families:
        for member in reach.members(family):
            reach.families_of.setdefault(member, set()).add(family)
    items = [reach.item(family, "family") for family in listing.families]
    items += [reach.item(rule, "invariant") for rule in reach.families_of]
    ordered = sorted(items, key=lambda item: item["subject"])
    return ConsolidationPass(
        items=tuple(ordered), subjects=frozenset(item["subject"] for item in ordered)
    )


@dataclass
class _Reach:
    """What the list reaches: each rule with the listed families that reach it."""

    listing: Listing
    base: KnowledgeSide
    candidate: KnowledgeSide
    families_of: dict[str, set[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.families_of = {rule: set() for rule in self.listing.rules}

    def members(self, family: str) -> set[str]:
        """The family's members on either side, as the run reads a reached family's."""

        members: set[str] = set()
        for side in (self.base, self.candidate):
            record = side.families.get(family)
            if record is not None:
                members.update(record.members)
        return members

    def item(self, subject: str, record: str) -> dict[str, Any]:
        revision = self.candidate.revision(subject)
        facts: dict[str, Any] = {
            "record": record,
            "revision": revision,
            "families": sorted(self.families_of.get(subject, ())),
            "listed": subject in self.listing.families or subject in self.listing.rules,
        }
        if revision is None and self.base.revision(subject) is None:
            facts["unknown"] = True
        return {
            "id": item_id(CONSOLIDATION_KIND, subject, [revision]),
            "kind": CONSOLIDATION_KIND,
            "subject": subject,
            "facts": facts,
        }


class _GateView(Protocol):
    @property
    def history(self) -> HistoryFile | None: ...

    @property
    def candidate(self) -> Any: ...


def consolidation_target_open(item: Mapping[str, Any], context: _GateView) -> str | None:
    """Why a list item is open, or ``None`` when the subject's current row answers it."""

    subject = str(item.get("subject"))
    facts = item.get("facts") or {}
    if facts.get("unknown"):
        return (
            f"{subject} is on the task document's consolidationList, but the memory holds no "
            f"record {subject}; correct the list with task_doc (set_field consolidationList)"
        )
    row = _row_about(subject, context.history)
    if not isinstance(row, InvariantRow | FamilyRow):
        return f"the leaf's history file holds no row about {subject}; {_REMEDY}"
    revision_of: Callable[[str], int | None] = context.candidate.revision
    current = revision_of(subject)
    if row.revision != current:
        return (
            f"row {row.id} records revision {row.revision}, but {subject} is at revision "
            f"{current} in K_C; name {subject} again in the hand-off's 'history' so the writer "
            "records the revision read"
        )
    if isinstance(row, FamilyRow):
        moved = sorted(
            member.id
            for member in row.examined
            if (now := revision_of(member.id)) is not None and now != member.revision
        )
        if moved:
            return (
                f"row {row.id} examined members whose revision changed since: "
                f"{', '.join(moved)}; name {subject} again in the hand-off's 'history'"
            )
    return None


def new_disposition_reason(item: Mapping[str, Any], row: HistoryRow) -> str | None:
    """Whether a ``stands`` or ``unresolved`` row answers an item of today's kinds.

    ``None`` when it does: an ``unresolved`` row about a listed subject, or a ``stands`` row about
    a listed rule whose item asks for nothing but a row at the unchanged revision (a title). Else
    the reason the item stays open, naming the row the item asks for.
    """

    listed = item.get(LISTED_MARK) is True
    if row.disposition == UNRESOLVED and listed:
        return None
    if row.disposition == STANDS and listed and _meaning_neutral(item, row):
        return None
    return (
        f"row {row.id} is a {row.disposition} row, which answers an item of the consolidation "
        f"list only; {item.get('subject')}'s {item.get('kind')} item asks for today's row for "
        "the change (for example no_impact, extended or changed); name it so in the hand-off's "
        "'history'"
    )


def _meaning_neutral(item: Mapping[str, Any], row: HistoryRow) -> bool:
    """A rule item whose record changed at an unchanged revision, with no entry to cover."""

    if item.get("kind") != "touched_invariant" or not isinstance(row, InvariantRow):
        return False
    facts = item.get("facts") or {}
    record = facts.get("record") or {}
    entries = facts.get("entries") or ()
    return (
        not any(facts.get(key) for key in ("added", "retired", "reanchored"))
        and not any(
            isinstance(one, Mapping) and one.get("class") in COVERING_CLASSES for one in entries
        )
        and record.get("baseRevision") == record.get("candidateRevision") == row.revision
    )
