"""The brief's order of rules and where a weakness stands.

* **The brief's order.** The rule with the most places in this file comes first; rules with the
  same number go by their ID.
* **Moved first.** Where an answer must show what moved after edits, the rules with an anchor the
  currentness check no longer finds current stand first, in the brief's order, and the brief's
  order holds for the rest.
* **A weakness beside its rule.** A weakness that names the rule it burdens stands in that rule's
  block. One that names none stands with the first rule, in the brief's order, that is realized at
  the weakness's own symbol; every later rule realized there names it by its ID. Any other weakness
  stands in the part for weaknesses that belong to no rule. A ``burdens`` link to a rule that is not
  one of this file's own does not place the weakness here.

Plain inputs and plain outputs: nothing here reads the memory.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agents_remember.application.file_briefs.model import RuleHead


@dataclass(frozen=True)
class RulePlaces:
    """What ordering and placement need of one rule: its head and the symbols it is realized at here."""

    head: RuleHead
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class WeaknessSite:
    """What placement needs of one weakness: its ID, its symbol and the rules it says it burdens."""

    id: str
    symbol: str
    burdens: tuple[str, ...]


@dataclass(frozen=True)
class Placement:
    """Per rule, the weaknesses that stand in its block and those it names by ID; the loose rest."""

    standing: Mapping[str, tuple[str, ...]]
    named: Mapping[str, tuple[str, ...]]
    loose: tuple[str, ...]


def brief_order(rules: Sequence[RulePlaces]) -> list[RulePlaces]:
    """Most places in this file first; the same number of places by rule ID."""

    return sorted(rules, key=lambda one: (-len(one.symbols), one.head.id))


def moved_first(rules: Sequence[RulePlaces]) -> list[RulePlaces]:
    """The brief's order with every moved rule in front of the rest."""

    ordered = brief_order(rules)
    return [one for one in ordered if one.head.moved] + [
        one for one in ordered if not one.head.moved
    ]


def place_weaknesses(rules: Sequence[RulePlaces], weaknesses: Sequence[WeaknessSite]) -> Placement:
    """Stand each weakness beside the rule it burdens, else at its symbol's first rule, else loose."""

    ordered = brief_order(rules)
    standing: dict[str, list[str]] = {one.head.id: [] for one in ordered}
    named: dict[str, list[str]] = {one.head.id: [] for one in ordered}
    loose: list[str] = []
    for weakness in sorted(weaknesses, key=lambda one: one.id):
        stands_with, names_it = _seats(weakness, ordered)
        if not stands_with:
            loose.append(weakness.id)
            continue
        for rule_id in stands_with:
            standing[rule_id].append(weakness.id)
        for rule_id in names_it:
            named[rule_id].append(weakness.id)
    return Placement(
        standing={key: tuple(value) for key, value in standing.items()},
        named={key: tuple(value) for key, value in named.items()},
        loose=tuple(loose),
    )


def _seats(weakness: WeaknessSite, ordered: Sequence[RulePlaces]) -> tuple[list[str], list[str]]:
    """The rules ``weakness`` stands with and the rules that name it, in the brief's order.

    Every rule it burdens takes it; else the first rule realized at its symbol takes it and every
    later one there names it. Neither list holds a rule when the weakness is loose.
    """

    burdened = [rule.head.id for rule in ordered if rule.head.id in weakness.burdens]
    if burdened:
        return burdened, []
    at_symbol = [rule.head.id for rule in ordered if weakness.symbol in rule.symbols]
    return at_symbol[:1], at_symbol[1:]


__all__ = [
    "Placement",
    "RulePlaces",
    "WeaknessSite",
    "brief_order",
    "moved_first",
    "place_weaknesses",
]
