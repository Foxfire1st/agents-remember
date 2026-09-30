"""MIK-R13's content rules for decision records, and the reads they make possible.

The shape of a decision (``ar-decision/v1``) is MIK-R21 rule 4 (:class:`.records.DecisionRecord`).
This module adds what a well-formed shape can still get wrong, as pure functions over one record, so
the validator (``memory_quality/knowledge_validator/rules_decisions.py``) refuses it and every reader
applies the same reading:

* **Alternatives** (rule 1). At least two, and exactly one ``chosen``. Every ``rejected`` or
  ``deferred`` alternative carries a ``reconsider_when``; the model already refuses a blank one, so
  absence is what is checked here. ``reconsider_when`` is prose and is **never evaluated**: a
  decision comes back up only through its ``reconsider_on`` links (MIK-R14).
* **Superseded is derived** (rule 2). A decision is superseded exactly when another decision's
  ``supersedes`` names it. :data:`.records.DecisionStatus` has no ``superseded`` spelling, so a
  stored one never parses; :func:`stored_superseded_fields` names where a raw document stores it,
  and :func:`superseded_by` / :func:`derived_status` derive it.
* **Links** (rule 3). ``explains``, ``constrains`` and ``motivated_change_to`` are the *governs*
  relations: the records, routes, anchors and requirements the decision governs, from which it is
  read. ``reconsider_on`` links belong to one alternative by its index in ``alternatives`` -- the
  authored order, which the canonical formatter keeps -- and that alternative must be ``rejected``
  or ``deferred``: only those have a condition to reconsider. :func:`reconsider_links` is the read
  MIK-R14's worklist triggers take, one ``(DEC-ID, index)`` subject per link.

Requirement endpoints are resolved by the requirement owner, not here
(``memory/knowledge/requirement_endpoint.py``); an unresolved one is reported, never refused.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from agents_remember.models.knowledge_files.records import Alternative, DecisionRecord
from agents_remember.models.knowledge_files.shapes import Link

GOVERNS_RELATIONS: Final = frozenset({"explains", "constrains", "motivated_change_to"})
RECONSIDER_ON: Final = "reconsider_on"
RECONSIDERABLE: Final = frozenset({"rejected", "deferred"})
SUPERSEDED: Final = "superseded"
MINIMUM_ALTERNATIVES: Final = 2

DerivedDecisionStatus = Literal["active", "under_reconsideration", "superseded"]


@dataclass(frozen=True)
class ContentProblem:
    """One broken content rule: the record field it sits in and why."""

    field: str
    message: str


@dataclass(frozen=True)
class ReconsiderLink:
    """A ``reconsider_on`` link with the alternative it reopens.

    ``subject`` is MIK-R14's worklist subject, ``reconsider:<DEC-ID>#<index>``.
    """

    decision: str
    index: int
    alternative: Alternative
    link: Link
    link_index: int

    @property
    def subject(self) -> str:
        return f"reconsider:{self.decision}#{self.index}"


def alternative_problems(record: DecisionRecord) -> list[ContentProblem]:
    """How many alternatives there are, and how many are chosen (rule 1)."""

    problems: list[ContentProblem] = []
    count = len(record.alternatives)
    if count < MINIMUM_ALTERNATIVES:
        problems.append(
            ContentProblem(
                "alternatives",
                f"a decision records at least {MINIMUM_ALTERNATIVES} considered alternatives; "
                f"this one has {count}",
            )
        )
    chosen = [index for index, one in enumerate(record.alternatives) if one.status == "chosen"]
    if len(chosen) != 1:
        where = ", ".join(str(index) for index in chosen) or "none"
        problems.append(
            ContentProblem(
                "alternatives",
                f"exactly one alternative is chosen; chosen: {where}",
            )
        )
    return problems


def reconsider_when_problems(record: DecisionRecord) -> list[ContentProblem]:
    """Every rejected or deferred alternative says when to reconsider it (rule 1)."""

    return [
        ContentProblem(
            f"alternatives.{index}.reconsider_when",
            f"the {one.status} alternative {one.option!r} states the condition under which to "
            "reconsider it",
        )
        for index, one in enumerate(record.alternatives)
        if one.status in RECONSIDERABLE and one.reconsider_when is None
    ]


def reconsider_link_problems(record: DecisionRecord) -> list[ContentProblem]:
    """Each ``reconsider_on`` link names a rejected or deferred alternative that exists (rule 3)."""

    problems: list[ContentProblem] = []
    count = len(record.alternatives)
    for link_index, link in enumerate(record.links):
        if link.relation != RECONSIDER_ON or link.alternative is None:
            continue
        where = f"links.{link_index}.alternative"
        if link.alternative >= count:
            problems.append(
                ContentProblem(
                    where,
                    f"reconsider_on names alternative {link.alternative}, but the decision has "
                    f"{count} (indexes 0 to {count - 1})",
                )
            )
            continue
        status = record.alternatives[link.alternative].status
        if status not in RECONSIDERABLE:
            problems.append(
                ContentProblem(
                    where,
                    f"reconsider_on names alternative {link.alternative}, which is {status}; only "
                    "a rejected or deferred alternative is reconsidered",
                )
            )
    return problems


def content_problems(record: DecisionRecord) -> list[ContentProblem]:
    """Every refusing content problem of one decision record."""

    return [
        *alternative_problems(record),
        *reconsider_when_problems(record),
        *reconsider_link_problems(record),
    ]


def governs_links(record: DecisionRecord) -> tuple[Link, ...]:
    """The links naming what the decision governs: it is read from each of their targets."""

    return tuple(link for link in record.links if link.relation in GOVERNS_RELATIONS)


def reconsider_links(record: DecisionRecord) -> tuple[ReconsiderLink, ...]:
    """The decision's ``reconsider_on`` links that name a rejected or deferred alternative."""

    found: list[ReconsiderLink] = []
    for link_index, link in enumerate(record.links):
        index = link.alternative
        if link.relation != RECONSIDER_ON or index is None or index >= len(record.alternatives):
            continue
        alternative = record.alternatives[index]
        if alternative.status in RECONSIDERABLE:
            found.append(ReconsiderLink(record.id, index, alternative, link, link_index))
    return tuple(found)


def superseded_by(records: Iterable[DecisionRecord]) -> dict[str, tuple[str, ...]]:
    """Per decision ID, the decisions whose ``supersedes`` name it (rule 2), sorted."""

    found: dict[str, set[str]] = {}
    for record in records:
        for superseded in record.supersedes:
            found.setdefault(superseded, set()).add(record.id)
    return {decision: tuple(sorted(by)) for decision, by in found.items()}


def derived_status(
    record: DecisionRecord, superseding: Mapping[str, tuple[str, ...]]
) -> DerivedDecisionStatus:
    """The status a reader shows: ``superseded`` when a later decision supersedes this one."""

    return SUPERSEDED if superseding.get(record.id) else record.status


def stored_superseded_fields(document: Mapping[str, Any]) -> list[str]:
    """Where a raw decision document stores ``superseded``: its status or an alternative's."""

    fields = ["status"] if document.get("status") == SUPERSEDED else []
    alternatives = document.get("alternatives")
    for index, one in enumerate(alternatives if isinstance(alternatives, list) else ()):
        if isinstance(one, Mapping) and one.get("status") == SUPERSEDED:
            fields.append(f"alternatives.{index}.status")
    return fields
