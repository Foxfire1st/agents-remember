"""Planned invariant effects reconciliation (MIK-R11@v2): declared effects against delivered rows.

A leaf's task document may declare the effects it expects (``expectedKnowledgeEffects``). The run
reconciles each declaration against the rows of the leaf's own history file in K_C (MIK-R07):

* ``invariant:<ID>`` with effect E matches only a ``changed`` row about that invariant whose
  ``effect`` is E; for E = ``retire``, a ``deleted`` row that retires the invariant (its effect is
  ``retire``) matches as well;
* ``family:<ID>`` matches only a ``changed`` family row;
* ``new:<label>`` matches an invariant first created in this leaf -- absent from K_B, present in
  K_C, its ``origin.leaf`` this leaf -- whose ``origin.handoffEntry`` equals the label;
* any other row (``no_impact``, ``moved``, a ``changed`` row with another effect ...) leaves the
  declaration unmatched.

**Classification.** Every invariant item (``touched_invariant``, ``stale_invariant``) and every
``reached_family`` item carries ``planning``: ``planned`` when its subject is declared (with any
effect), ``unplanned`` otherwise. Every unmatched declaration raises one ``planned_untouched`` item
whose subject is the planned subject key ``planned:<declared subject>#<effect>``; a declaration
naming an ID that K_C does not hold carries the fact ``subject_unknown``. Without a declaration
every item is ``unplanned`` and no ``planned_untouched`` item exists (rule 6).

**Satisfying row.** A ``planned_untouched`` item is answered only by the leaf's planned row with the
same subject (``realized_elsewhere`` | ``deferred`` | ``dropped``, MIK-R07 row shape plus ``ref``);
``satisfiedBy`` names it, and :func:`~agents_remember.models.knowledge_files.planned.
planned_item_open` applies the same rule to a stored item. Nothing here reads requirement prose
(Exclusions): the declaration is the only plan.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.registry import (
    ItemKind,
    item_id,
    register_item_kind,
    subject_row,
)
from agents_remember.models.knowledge_files.history import (
    FamilyRow,
    HistoryFile,
    HistoryRow,
    InvariantRow,
)
from agents_remember.models.knowledge_files.planned import (
    PLANNED_ITEM_KIND,
    SUBJECT_UNKNOWN,
    planned_subject,
)

__all__ = [
    "PLANNED_UNTOUCHED_KIND",
    "Declaration",
    "PlannedReconciliation",
    "declarations_from",
    "reconcile_planned_effects",
]

UNMATCHED: Final = "no_matching_row"
PLANNED: Final = "planned"
UNPLANNED: Final = "unplanned"
_MARKED_KINDS: Final = frozenset({"touched_invariant", "stale_invariant", "reached_family"})

PLANNED_UNTOUCHED_KIND: Final = register_item_kind(
    ItemKind(
        name=PLANNED_ITEM_KIND,
        subject="planned subject key (planned:<declared subject>#<effect>)",
        subject_pattern=r"^planned:",
        facts=("declared", "unmatched", "rows"),
        satisfying_row=(
            "the leaf's planned row with this subject: realized_elsewhere (ref: the row or "
            "invariant that delivered it), deferred (ref: the follow-up requirement or leaf) or "
            "dropped (ref: a decision entry of the leaf's task document) (MIK-R11 rule 5)"
        ),
        row_lookup=subject_row("planned"),
        owner="MIK-R11",
    )
)


@dataclass(frozen=True)
class Declaration:
    """One ``expectedKnowledgeEffects`` entry of the leaf's task document."""

    subject: str
    effect: str
    requirement_ref: str

    @property
    def key(self) -> str:
        return planned_subject(self.subject, self.effect)

    @property
    def record_id(self) -> str | None:
        """The declared invariant or family ID; ``None`` for ``new:<label>``."""

        kind, _, value = self.subject.partition(":")
        return value if kind in {"invariant", "family"} else None

    def to_document(self) -> dict[str, str]:
        return {
            "subject": self.subject,
            "effect": self.effect,
            "requirementRef": self.requirement_ref,
        }


def declarations_from(effects: Iterable[Any] | None) -> tuple[Declaration, ...] | None:
    """The task document's declarations (``ExpectedKnowledgeEffect`` models or their documents)."""

    if effects is None:
        return None
    declared = []
    for one in effects:
        document = one if isinstance(one, Mapping) else one.model_dump(mode="json")
        declared.append(
            Declaration(
                subject=str(document["subject"]),
                effect=str(document["effect"]),
                requirement_ref=str(document["requirementRef"]),
            )
        )
    return tuple(declared)


@dataclass(frozen=True)
class PlannedReconciliation:
    """The marks, the ``planned_untouched`` items and the per-declaration summary of one run."""

    declared: bool
    planned_subjects: frozenset[str] = frozenset()
    items: tuple[dict[str, Any], ...] = ()
    entries: tuple[dict[str, Any], ...] = field(default=())

    def mark(self, item: dict[str, Any]) -> dict[str, Any]:
        """``item`` with its ``planning`` mark, for the invariant and family kinds (rule 4)."""

        if item.get("kind") not in _MARKED_KINDS:
            return item
        return {
            **item,
            "planning": PLANNED if item["subject"] in self.planned_subjects else UNPLANNED,
        }

    def summary(self) -> dict[str, Any]:
        if not self.declared:
            return {"declared": False}
        return {"declared": True, "entries": list(self.entries)}


def reconcile_planned_effects(
    declarations: Sequence[Declaration] | None,
    base: KnowledgeSide,
    candidate: KnowledgeSide,
    owner: str | None,
) -> PlannedReconciliation:
    """Reconcile the leaf's declarations against its history rows in K_C (rules 3-6)."""

    if declarations is None:
        return PlannedReconciliation(declared=False)
    history = None if owner is None else candidate.history(owner)
    items: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for declaration in declarations:
        matched_by, unmatched = _match(declaration, base, candidate, history, owner)
        entry: dict[str, Any] = {"key": declaration.key, **declaration.to_document()}
        if matched_by is not None:
            entries.append({**entry, "matched": True, "matchedBy": matched_by})
            continue
        item = _untouched_item(declaration, unmatched, history)
        entries.append({**entry, "matched": False, "unmatched": unmatched, "item": item["id"]})
        items.append(item)
    planned = frozenset(
        declaration.record_id for declaration in declarations if declaration.record_id is not None
    )
    return PlannedReconciliation(
        declared=True, planned_subjects=planned, items=tuple(items), entries=tuple(entries)
    )


def _match(
    declaration: Declaration,
    base: KnowledgeSide,
    candidate: KnowledgeSide,
    history: HistoryFile | None,
    owner: str | None,
) -> tuple[str | None, str]:
    """What delivered ``declaration`` (a row or invariant ID), or ``None`` and why not."""

    kind, _, value = declaration.subject.partition(":")
    if kind == "new":
        return _new_invariant(value, base, candidate, owner), UNMATCHED
    known = candidate.invariants if kind == "invariant" else candidate.families
    if value not in known:
        return None, SUBJECT_UNKNOWN
    row = None if history is None else history.row_about(value)
    return (row.id if row is not None and _delivers(row, declaration.effect) else None), UNMATCHED


def _delivers(row: HistoryRow, effect: str) -> bool:
    if isinstance(row, FamilyRow):
        return row.disposition == "changed"
    if not isinstance(row, InvariantRow):
        return False
    if row.disposition == "changed":
        return row.effect == effect
    return effect == "retire" and row.disposition == "deleted" and row.effect == "retire"


def _new_invariant(
    label: str, base: KnowledgeSide, candidate: KnowledgeSide, owner: str | None
) -> str | None:
    for invariant_id, record in sorted(candidate.invariants.items()):
        if invariant_id in base.invariants or record.origin.handoff_entry != label:
            continue
        if owner is None or record.origin.leaf == owner:
            return invariant_id
    return None


def _untouched_item(
    declaration: Declaration, unmatched: str, history: HistoryFile | None
) -> dict[str, Any]:
    record_id = declaration.record_id
    rows = [
        {
            "id": row.id,
            "disposition": row.disposition,
            **({"effect": row.effect} if isinstance(row, InvariantRow) and row.effect else {}),
        }
        for row in (history.rows if history is not None else ())
        if record_id is not None and row.subject == record_id
    ]
    answer = None if history is None else history.row_about(declaration.key)
    subject = declaration.key
    return {
        "id": item_id(PLANNED_ITEM_KIND, subject, [declaration.requirement_ref, unmatched]),
        "kind": PLANNED_ITEM_KIND,
        "subject": subject,
        "facts": {"declared": declaration.to_document(), "unmatched": unmatched, "rows": rows},
        "satisfiedBy": None if answer is None else answer.id,
    }
