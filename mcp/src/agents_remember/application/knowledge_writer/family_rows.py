"""Family history-row authoring and reached-family worklist facts.

A family row answers a ``reached_family`` worklist item, whose ``facts.members`` lists every
member in K_B and K_C with its base and candidate revisions. An empty family (no members on either
side) needs ``examined: []``; a member that left K_C stays required in ``examined`` at its base
revision.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agents_remember.application.knowledge_writer.family_effect import family_effect_refusal
from agents_remember.application.knowledge_writer.handoff import RowRequest

if TYPE_CHECKING:
    from agents_remember.application.knowledge_writer.authoring_rows import HistoryRowAuthoring


@dataclass(frozen=True)
class ReachedFamilyFacts:
    """Member facts read from one persisted ``reached_family`` worklist item."""

    empty: bool
    base_revisions: Mapping[str, int]


def reached_family_facts(worklist: Mapping[str, Any] | None) -> dict[str, ReachedFamilyFacts]:
    """Read ``facts.members`` from each persisted ``reached_family`` item, by family ID."""

    found: dict[str, ReachedFamilyFacts] = {}
    for item in (worklist or {}).get("items") or ():
        if not isinstance(item, Mapping) or item.get("kind") != "reached_family":
            continue
        subject = item.get("subject")
        facts = item.get("facts")
        members = facts.get("members") if isinstance(facts, Mapping) else None
        if not (isinstance(subject, str) and isinstance(members, list)):
            continue
        found[subject] = ReachedFamilyFacts(
            empty=not members,
            base_revisions=_base_revisions(members),
        )
    return found


def _base_revisions(members: list[Any]) -> dict[str, int]:
    """Map each member ID in ``facts.members`` to its base revision when it is 1 or more."""

    revisions: dict[str, int] = {}
    for entry in members:
        if not isinstance(entry, Mapping):
            continue
        member_id = entry.get("id")
        base = entry.get("base")
        if isinstance(member_id, str) and type(base) is int and base >= 1:
            revisions[member_id] = base
    return revisions


def build_family_row(
    authoring: HistoryRowAuthoring,
    request: RowRequest,
    row: dict[str, Any],
    record: Mapping[str, Any],
    where: str,
) -> dict[str, Any] | None:
    """Populate a family history row's examined members, own revision and effect.

    Returns ``None`` after recording a problem when the effect or empty examined list is refused.
    """

    refusal = family_effect_refusal(request, row["subject"])
    if refusal is not None:
        authoring.problem(f"{where}.effect", refusal)
        return None
    family_facts = authoring.reached_family_members.get(row["subject"])
    if not request.examined and not _allows_empty_examined(record, family_facts):
        authoring.problem(where, "a family row names every member the curator examined")
        return None
    row["examined"] = _resolve_examined(authoring, request, family_facts, where)
    row["revision"] = record.get("revision")
    if request.effect is not None:
        row["effect"] = request.effect
    return row


def _allows_empty_examined(
    record: Mapping[str, Any], family_facts: ReachedFamilyFacts | None
) -> bool:
    """An empty examined list is admitted only when both the worklist item and produced record have
    no members, so a member added in the same batch cannot hide behind a stale worklist."""

    members = record.get("members")
    return (
        family_facts is not None
        and family_facts.empty
        and isinstance(members, (list, tuple))
        and len(members) == 0
    )


def _resolve_examined(
    authoring: HistoryRowAuthoring,
    request: RowRequest,
    family_facts: ReachedFamilyFacts | None,
    where: str,
) -> list[dict[str, Any]]:
    """Resolve each examined member from K_C first, falling back to its K_B revision only when
    the member left K_C and the family's worklist item names it at a base revision of 1 or more."""

    examined: list[dict[str, Any]] = []
    for one in request.examined:
        member = authoring.resolve_id(one, where)
        found = None if member is None else authoring.state.record(member)
        if member is not None and found is not None and found[1] == "invariant":
            examined.append({"id": member, "revision": found[2].get("revision")})
            continue
        if member is not None and found is None and family_facts is not None:
            base_revision = family_facts.base_revisions.get(member)
            if base_revision is not None:
                examined.append({"id": member, "revision": base_revision})
                continue
        authoring.problem(where, f"examined member {one!r} is not a stored invariant")
    return examined
