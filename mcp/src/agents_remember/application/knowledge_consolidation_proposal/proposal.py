"""Which families and loose rules one consolidation pass could take, in the three ways.

Every function here works on the facts already read (:class:`.store.ProposalStore` and the history
facts), so a proposal costs one lookup per family, member and folder:

* **after a finished master** -- the families holding a rule the master wrote, the family with the
  most such rules first; beside them the rules the master wrote that are in no family;
* **for a family or a folder** -- that family, or every family with a member realized at or under
  the folder, by id;
* **with nothing named** -- every family, those never consolidated first in the order their records
  were first written, then the others by their last consolidation, oldest first.

Each proposed family carries the rules in no family realized in a file where one of its members
is realized (:meth:`.store.ProposalStore.attached_rules`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .store import ProposalStore

ProposalWay = Literal["master", "family", "folder", "oldest"]


@dataclass(frozen=True)
class ProposedFamily:
    """One family a pass could take, with the loose rules of its members' files."""

    family: str
    title: str
    members: int
    attached_rules: tuple[str, ...]
    touched_members: int | None = None
    """For a master: how many of its members the master wrote."""
    first_written: datetime | None = None
    last_consolidated: datetime | None = None


@dataclass(frozen=True)
class Proposal:
    """One way's answer: the named subject, the proposed families and the rules in no family."""

    way: ProposalWay
    named: str | None
    families: tuple[ProposedFamily, ...]
    rules_in_no_family: tuple[str, ...]
    """The master's written rules that are in no family; empty for the other ways."""


@dataclass(frozen=True)
class PassTimes:
    """When each family was first written and last consolidated."""

    first_written: dict[str, datetime]
    last_consolidated: dict[str, datetime]


def for_master(store: ProposalStore, master: str, written_rules: set[str]) -> Proposal:
    """The families holding a rule the master wrote, and its written rules in no family."""

    touched: dict[str, int] = {}
    for rule in written_rules & store.rules:
        for family in store.rule_families.get(rule, ()):
            touched[family] = touched.get(family, 0) + 1
    order = sorted(touched, key=lambda family: (-touched[family], family))
    loose = sorted(rule for rule in written_rules & store.rules if rule not in store.rule_families)
    families = tuple(_proposed(store, family, touched_members=touched[family]) for family in order)
    return Proposal("master", master, families, tuple(loose))


def for_family(store: ProposalStore, family: str) -> Proposal:
    """The named family alone."""

    return Proposal("family", family, (_proposed(store, family),), ())


def for_folder(store: ProposalStore, folder: str) -> Proposal:
    """Every family with a member realized at or under ``folder``, by id."""

    prefix = "" if folder in ("", ".") else f"{folder}/"
    families = sorted(
        family
        for family in store.families
        if any(_under(one, folder, prefix) for one in store.member_folders(family))
    )
    return Proposal(
        "folder", folder or ".", tuple(_proposed(store, family) for family in families), ()
    )


def oldest_first(store: ProposalStore, times: PassTimes) -> Proposal:
    """Every family: never consolidated first, by first writing; then by last consolidation."""

    never = [family for family in store.families if family not in times.last_consolidated]
    once = [family for family in store.families if family in times.last_consolidated]
    never.sort(key=lambda family: _first_written_key(store, times, family))
    once.sort(key=lambda family: (times.last_consolidated[family], family))
    families = tuple(
        _proposed(
            store,
            family,
            first_written=times.first_written.get(family),
            last_consolidated=times.last_consolidated.get(family),
        )
        for family in (*never, *once)
    )
    return Proposal("oldest", None, families, ())


def attached_total(families: Iterable[ProposedFamily]) -> int:
    """The distinct loose rules attached to any of ``families``."""

    return len({rule for family in families for rule in family.attached_rules})


def _first_written_key(
    store: ProposalStore, times: PassTimes, family: str
) -> tuple[int, float, str, str]:
    """Committed families by their first commit; then working-tree-only ones by stamp, then id."""

    committed = times.first_written.get(family)
    if committed is not None:
        return (0, committed.timestamp(), "", family)
    return (1, 0.0, store.families[family].stamped_at or "", family)


def _under(folder: str, named: str, prefix: str) -> bool:
    return not prefix or folder == named or folder.startswith(prefix)


def _proposed(
    store: ProposalStore,
    family: str,
    *,
    touched_members: int | None = None,
    first_written: datetime | None = None,
    last_consolidated: datetime | None = None,
) -> ProposedFamily:
    facts = store.families[family]
    return ProposedFamily(
        family=family,
        title=facts.title,
        members=len(facts.members),
        attached_rules=tuple(store.attached_rules(family)),
        touched_members=touched_members,
        first_written=first_written,
        last_consolidated=last_consolidated,
    )
