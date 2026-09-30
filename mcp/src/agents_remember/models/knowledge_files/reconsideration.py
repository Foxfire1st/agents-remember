"""Reconsideration candidates (MIK-R14@v2): the subject, the row and the satisfying rule.

A decision's ``reconsider_on`` link belongs to one ``rejected`` or ``deferred`` alternative by its
index (MIK-R13 rule 3). When a leaf changes a linked target, the worklist raises a
``reconsideration_candidate`` item whose subject -- and the subject of the history row that answers
it -- is ``reconsider:<DEC-ID>#<alternative index>`` (:func:`reconsider_subject`, the same spelling
as :attr:`.decisions.ReconsiderLink.subject`).

**Triggers (rule 1).** A target counts as changed when its record file differs between K_B and K_C,
when a row of this leaf gives it one of :data:`TRIGGERING_DISPOSITIONS`, when it is an anchor
MIK-R08 definition 4 classifies ``touched`` or ``moved_or_absent``, or when it is a requirement
endpoint whose owning task has a newer approved version. A link anchor classified ``stale_at_base``
fires as ``anchor_stale`` (review ruling F3), so a stale link anchor is never silent.
:data:`TRIGGERS` names them. A decision target never triggers: a change to a decision does not
chain on to other decisions (rule 3, one hop).

**Rows (rule 4).** ``still_rejected`` (the rejection holds, with a reason) or ``raise`` (the
alternative goes to the developer: the decision's ``status`` becomes ``under_reconsideration`` in
K_C and a question is appended to the leaf's task-document ``openQuestions``). The curator never
reverses a decision.

:func:`reconsideration_item_open` is the kind's satisfying rule over a stored item, for the gate
(MIK-R09); the worklist's ``satisfiedBy`` applies the same rule, so the two never disagree.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from agents_remember.models.knowledge_files.ids import RECORD_PREFIXES, id_pattern

__all__ = [
    "RECONSIDERATION_ITEM_KIND",
    "RECONSIDER_DISPOSITIONS",
    "RECONSIDER_SUBJECT_PATTERN",
    "TRIGGERING_DISPOSITIONS",
    "TRIGGERS",
    "link_target_key",
    "parse_reconsider_subject",
    "question_key",
    "reconsider_subject",
    "reconsideration_item_open",
]

RECONSIDERATION_ITEM_KIND: Final = "reconsideration_candidate"
STILL_REJECTED: Final = "still_rejected"
RAISE: Final = "raise"
RECONSIDER_DISPOSITIONS: Final = (STILL_REJECTED, RAISE)

# The history-row dispositions that count as a change of the row's subject (rule 1).
TRIGGERING_DISPOSITIONS: Final = frozenset({"changed", "moved", "deleted", "rerouted", "retired"})

RECORD_FILE: Final = "record_file"
HISTORY_ROW: Final = "history_row"
ANCHOR: Final = "anchor"
ANCHOR_STALE: Final = "anchor_stale"
REQUIREMENT_VERSION: Final = "requirement_version"
TRIGGERS: Final = (RECORD_FILE, HISTORY_ROW, ANCHOR, ANCHOR_STALE, REQUIREMENT_VERSION)
# The triggers a ``still_rejected`` answer refreshes the link for (rulings Q2/Q3, F1, F3).
REFRESHED_TRIGGERS: Final = frozenset({ANCHOR, ANCHOR_STALE, REQUIREMENT_VERSION})

_DECISION: Final = id_pattern(RECORD_PREFIXES["decision"]).removeprefix("^").removesuffix("$")
RECONSIDER_SUBJECT_PATTERN: Final = (
    rf"^reconsider:(?P<decision>{_DECISION})#(?P<index>0|[1-9][0-9]*)$"
)


def reconsider_subject(decision: str, index: int) -> str:
    """``reconsider:<DEC-ID>#<alternative index>``: the item's and its row's subject."""

    return f"reconsider:{decision}#{index}"


def parse_reconsider_subject(value: str) -> tuple[str, int] | None:
    """The ``(decision ID, alternative index)`` a subject names, or ``None``."""

    matched = re.match(RECONSIDER_SUBJECT_PATTERN, value)
    return None if matched is None else (matched["decision"], int(matched["index"]))


def question_key(subject: str, leaf: str) -> str:
    """The prefix that identifies a ``raise`` row's question in the leaf's ``openQuestions``.

    One question per subject and leaf: a rerun of the same ``raise`` finds its question by this
    prefix and appends nothing.
    """

    return f"[{subject} raised by {leaf}]"


def link_target_key(target: Any) -> str:
    """The one spelling of a ``reconsider_on`` target in the worklist's facts (``changed[].target``).

    A record ID or ``route:`` string is itself; an anchor is ``<path>@<blob>#<content>``; a
    requirement is ``<repository>/<task path>#<id>@<version>``. The writer compares a K_C link with
    the fired target through it before a refresh (review R2 N1).
    """

    if isinstance(target, str):
        return target
    if not isinstance(target, Mapping):
        return repr(target)
    if "locator" in target:
        return f"{target.get('path')}@{target.get('blob')}#{target.get('content')}"
    found = target.get("task")
    task: Mapping[str, Any] = found if isinstance(found, Mapping) else {}
    return f"{task.get('repository')}/{task.get('path')}#{target.get('id')}@{target.get('version')}"


def reconsideration_item_open(item: Mapping[str, Any], rows_by_subject: Mapping[str, str]) -> bool:
    """The ``reconsideration_candidate`` satisfying rule over a stored item, for the gate (MIK-R09).

    Only the leaf's row with the item's subject answers it; the row kind admits only
    ``still_rejected`` and ``raise``, so any such row is a satisfying one (rule 5).
    ``rows_by_subject`` maps each history-row subject of the leaf to its row ID.
    """

    subject = item.get("subject")
    return not isinstance(subject, str) or subject not in rows_by_subject
