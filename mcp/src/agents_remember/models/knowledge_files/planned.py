"""Planned knowledge effects: the declared subjects, the planned-row subject key and its predicate.

A leaf's task document may declare, before implementation, the effects it expects
(``expectedKnowledgeEffects``, MIK-R11 rule 1). Each declaration names

* a **subject** -- ``invariant:<INV-ID>``, ``family:<FAM-ID>`` or ``new:<hand-off label>``;
* an **effect** -- one of the nine effect labels (:data:`ADMITTED_EFFECT_LABELS`, unchanged);
* a ``requirementRef``.

The worklist reconciles each declaration against the leaf's history rows (MIK-R07). A declaration
no row delivers raises a ``planned_untouched`` item whose subject -- and the subject of the history
row that answers it -- is the **planned subject key** ``planned:<declared subject>#<effect>``
(rule 5). The key is built from the declaration itself, never from its position in the list, so
editing the list never shifts it.

This module is the one spelling of those forms, shared by the task plane (which refuses a malformed
declaration and never reads knowledge), the history row model, the worklist and the closeout gate.
:func:`planned_item_open` is the kind's satisfying rule over a stored item, for the gate
(MIK-R09), in agreement with the worklist's own ``satisfiedBy``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from agents_remember.models.knowledge.effect import ADMITTED_EFFECT_LABELS
from agents_remember.models.knowledge_files.ids import RECORD_PREFIXES, id_pattern

__all__ = [
    "DECLARED_SUBJECT_PATTERN",
    "PLANNED_DISPOSITIONS",
    "PLANNED_ITEM_KIND",
    "PLANNED_SUBJECT_PATTERN",
    "REFS_BY_DISPOSITION",
    "REQUIREMENT_REF_PATTERN",
    "SUBJECT_UNKNOWN",
    "parse_planned_subject",
    "planned_item_open",
    "planned_subject",
]

PLANNED_ITEM_KIND: Final = "planned_untouched"
SUBJECT_UNKNOWN: Final = "subject_unknown"
PLANNED_DISPOSITIONS: Final = ("realized_elsewhere", "deferred", "dropped")

# Which ``ref`` a planned row's disposition carries (rule 5): the row or invariant that delivered
# the effect, the follow-up requirement or leaf, or the decision entry of the leaf's task document.
REFS_BY_DISPOSITION: Final[Mapping[str, frozenset[str]]] = {
    "realized_elsewhere": frozenset({"row", "invariant"}),
    "deferred": frozenset({"requirement", "leaf"}),
    "dropped": frozenset({"decision"}),
}


def _unanchored(pattern: str) -> str:
    return pattern.removeprefix("^").removesuffix("$")


_INVARIANT: Final = _unanchored(id_pattern(RECORD_PREFIXES["invariant"]))
_FAMILY: Final = _unanchored(id_pattern(RECORD_PREFIXES["family"]))
# A hand-off label is one line with no surrounding whitespace (``origin.handoffEntry``).
_DECLARED: Final = rf"(?:invariant:{_INVARIANT}|family:{_FAMILY}|new:\S(?:[^\r\n]*\S)?)"
_EFFECT: Final = "|".join(ADMITTED_EFFECT_LABELS)

DECLARED_SUBJECT_PATTERN: Final = rf"^{_DECLARED}$"
PLANNED_SUBJECT_PATTERN: Final = rf"^planned:(?P<subject>{_DECLARED})#(?P<effect>{_EFFECT})$"
# ``<stable ID>@v<n>``: the packet identity the task's own requirement list declares.
REQUIREMENT_REF_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._-]*@v[1-9][0-9]*$"


def planned_subject(subject: str, effect: str) -> str:
    """The planned subject key of one declaration: ``planned:<declared subject>#<effect>``."""

    return f"planned:{subject}#{effect}"


def parse_planned_subject(value: str) -> tuple[str, str] | None:
    """The ``(declared subject, effect)`` a planned subject key names, or ``None``."""

    matched = re.match(PLANNED_SUBJECT_PATTERN, value)
    return None if matched is None else (matched["subject"], matched["effect"])


def planned_item_open(item: Mapping[str, Any], rows_by_subject: Mapping[str, str]) -> bool:
    """The ``planned_untouched`` satisfying rule over a stored item, for the gate (MIK-R09).

    A stored item is a declaration no row delivered when the worklist was computed; only the leaf's
    planned row with the item's subject answers it (``rows_by_subject`` maps each history-row
    subject of the leaf to its row ID). A ``no_impact`` or any other row about the declared
    invariant never does: it is not a planned row. The worklist's ``satisfiedBy`` applies the same
    rule, so the two never disagree.
    """

    subject = item.get("subject")
    return not isinstance(subject, str) or subject not in rows_by_subject
