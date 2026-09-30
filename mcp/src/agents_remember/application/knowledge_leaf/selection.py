"""The family-complete leaf read's selection and its ordered rows (MIK-R01 rules 1 to 5 and 8).

One read seeded with a source path selects, from the derived index of one memory tree (MIK-R23):

* the entries at the path -- realization *and* proof entries -- and their invariants (the seed's own
  invariants);
* every family containing one of those invariants, with its guarantee, routes and members;
* every member's entries.

Traversal is one family hop: a member's other families are *advertised* (named, never expanded).
Retired records are not live and are never selected (MIK-R23 ruling Q4).

**Rows, in order** (rule 4), each one indivisible row of MIK-R02:

1. the seed's own invariants, by ID, each a ``member`` row followed by its ``realization`` and
   ``proof`` entry rows;
2. each family, by ID: its ``family_header`` row (which lists every member), then its remaining
   members, by ID, each with its entries. A seed invariant is not a remaining member and is not
   repeated; a member already returned under an earlier family appears again only as a
   ``member_reference`` row (rule 5), titled by its statement's first sentence -- derived, and
   labelled ``titleDerivedFrom: "statement"``, because an invariant record has no title;
3. the advertised frontier: one ``advertised_family`` row per family a selected member belongs to
   outside the selection, by family, listing in ``via`` every selected member that reaches it;
4. the route chain (MIK-R05, :mod:`.chain`): one compact ``chain_family`` row per live family with a
   route on the path's directory or one of its ancestors, nearest route first. A path with no entry
   but a governing family still selects its chain.

A *family seed* (MIK-R05 rule 3, :func:`select_family`) selects the full family content instead:
the family's header, every live member with its entries, then its advertised frontier.

Every row of a family carries the family as its paging ``group``, and the header carries the
reference row a page that continues the family starts with (MIK-R02 rule 4).

The selection is computed in two steps so that the selection manifest never depends on the code
tree: :func:`select_leaf` reads the structure (which records and entries, in which order), and
:func:`leaf_rows` renders it with each entry's and invariant's currentness at the walk's code tree
(MIK-R03). The manifest digest is over the structure's row identities only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Final

from agents_remember.application.knowledge_currentness.state import (
    INVARIANT_STATES,
    Currentness,
    InvariantState,
)
from agents_remember.application.knowledge_leaf.chain import (
    CHAIN_ROW,
    ChainFamily,
    chain_row,
    select_chain,
)
from agents_remember.application.knowledge_paging.pager import PageRow
from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory.knowledge_index import Entry, KnowledgeIndex, Record

__all__ = [
    "DERIVED_TITLE_LENGTH",
    "LEAF_POLICY",
    "LEAF_POLICY_VERSION",
    "LeafStructure",
    "derived_title",
    "family_names",
    "leaf_counts",
    "leaf_rows",
    "select_family",
    "select_leaf",
]

LEAF_POLICY: Final = "family-complete-leaf"
LEAF_POLICY_VERSION: Final = "v2"

# The longest derived reference title, in characters (L01 ruling Q1).
DERIVED_TITLE_LENGTH: Final = 80
# A sentence ends at ``.``, ``!`` or ``?`` followed by whitespace or the end of the statement.
_SENTENCE_END: Final = re.compile(r"[.!?](?=\s|$)")

_RETIRED: Final = "retired"
# The section a row that belongs to no family is returned under (the seed's own invariants).
_SEED_SECTION: Final = "seed"


@dataclass(frozen=True)
class _Member:
    record: Record
    entries: tuple[Entry, ...]
    families: tuple[str, ...]


@dataclass(frozen=True)
class _Family:
    record: Record
    members: tuple[str, ...]
    routes: tuple[str, ...]


@dataclass(frozen=True)
class LeafStructure:
    """What one seed path selects, before any code tree is consulted.

    ``order`` is the row list as ``(kind, subject, group)`` triples; :func:`leaf_rows` renders each
    one. ``advertised`` pairs each frontier family with the selected members it is reached through.
    ``chain`` are the route-chain families of a path seed (MIK-R05); ``family_seed`` names the family
    a family seed selected, and its ``path`` is then empty.
    """

    path: str
    seed: tuple[str, ...]
    families: Mapping[str, _Family]
    members: Mapping[str, _Member]
    advertised: tuple[tuple[str, tuple[str, ...]], ...]
    titles: Mapping[str, str | None]
    order: tuple[tuple[str, str, str | None], ...] = field(default=())
    chain: tuple[ChainFamily, ...] = ()
    family_seed: str | None = None

    @property
    def manifest_digest(self) -> str:
        """The digest of the ordered selection: each row's identity under this policy version."""

        seed: str | dict[str, str] = (
            self.path if self.family_seed is None else {"kind": "family", "id": self.family_seed}
        )
        return sha256_digest(
            {
                "policy": f"{LEAF_POLICY}/{LEAF_POLICY_VERSION}",
                "seed": seed,
                "rows": [list(one) for one in self.order],
            }
        )

    @property
    def invariants(self) -> tuple[str, ...]:
        return tuple(sorted(self.members))


def select_leaf(index: KnowledgeIndex, path: str) -> LeafStructure | None:
    """The family-complete selection of ``path`` with its route chain (MIK-R05), or ``None`` when
    no live entry is recorded there and no family route covers it."""

    members: dict[str, _Member] = {}
    seed = _seed_invariants(index, path, members)
    chain = select_chain(index, path, seed)
    if not seed and not chain:
        return None
    families = _families_of(index, seed, members)
    return _structure(index, path, seed, families, members, chain=chain)


def select_family(index: KnowledgeIndex, family_id: str) -> LeafStructure | None:
    """A family seed's selection: the full MIK-R01 content of one live family (MIK-R05 rule 3).

    Every live member is returned under the family's header, with its entries, then the advertised
    frontier of those members. ``None`` when the tree holds no live family ``family_id``.
    """

    family = _family(index, family_id)
    if family is None:
        return None
    members: dict[str, _Member] = {}
    live = tuple(m for m in family.members if _member(index, m, members) is not None)
    families = {family_id: replace(family, members=live)}
    return _structure(index, "", (), families, members, family_seed=family_id)


def _structure(
    index: KnowledgeIndex,
    path: str,
    seed: tuple[str, ...],
    families: dict[str, _Family],
    members: dict[str, _Member],
    **extra: Any,
) -> LeafStructure:
    advertised = _advertised(members, families)
    titles = {
        family_id: _title(index, family_id)
        for family_id in {*families, *(one for one, _ in advertised)}
    }
    structure = LeafStructure(
        path=path,
        seed=seed,
        families=families,
        members=members,
        advertised=advertised,
        titles=titles,
        **extra,
    )
    return replace(structure, order=_order(structure))


def _seed_invariants(
    index: KnowledgeIndex, path: str, members: dict[str, _Member]
) -> tuple[str, ...]:
    """The live invariants of every realization and proof entry at ``path``, by ID."""

    at = index.entries_at_path(path).value
    named = {entry.invariant for entry in (*at.realizations, *at.proofs)}
    return tuple(sorted(one for one in named if _member(index, one, members) is not None))


def _families_of(
    index: KnowledgeIndex, seed: Iterable[str], members: dict[str, _Member]
) -> dict[str, _Family]:
    """The live families containing a seed invariant, each with its live members only."""

    family_ids = sorted(
        {family_id for invariant in seed for family_id in members[invariant].families}
    )
    families: dict[str, _Family] = {}
    for family_id in family_ids:
        family = _family(index, family_id)
        if family is not None:
            live = tuple(m for m in family.members if _member(index, m, members) is not None)
            families[family_id] = replace(family, members=live)
    return families


def _advertised(
    members: Mapping[str, _Member], families: Mapping[str, _Family]
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Each frontier family once, with the selected members it is reached through (by ID)."""

    reached: dict[str, set[str]] = {}
    for invariant, member in members.items():
        for family_id in member.families:
            if family_id not in families:
                reached.setdefault(family_id, set()).add(invariant)
    return tuple((family_id, tuple(sorted(reached[family_id]))) for family_id in sorted(reached))


def _member(index: KnowledgeIndex, invariant: str, cache: dict[str, _Member]) -> _Member | None:
    """One live invariant with its entries and live families, read once per selection."""

    if invariant in cache:
        return cache[invariant]
    knowledge = index.invariant(invariant).value
    record = knowledge.record
    if record is None or record.kind != "invariant" or record.status == _RETIRED:
        return None
    families = tuple(one for one in knowledge.families if _live(index, one, "family"))
    member = _Member(record, (*knowledge.realizations, *knowledge.proofs), families)
    cache[invariant] = member
    return member


def _family(index: KnowledgeIndex, family_id: str) -> _Family | None:
    knowledge = index.family(family_id).value
    record = knowledge.record
    if record is None or record.kind != "family" or record.status == _RETIRED:
        return None
    return _Family(record, knowledge.members, knowledge.routes)


def _live(index: KnowledgeIndex, record_id: str, kind: str) -> bool:
    record = index.record(record_id).value
    return record is not None and record.kind == kind and record.status != _RETIRED


def _title(index: KnowledgeIndex, family_id: str) -> str | None:
    record = index.record(family_id).value
    title = None if record is None else record.document.get("title")
    return None if title is None else str(title)


def _order(structure: LeafStructure) -> tuple[tuple[str, str, str | None], ...]:
    """The row identities in the declared order (rule 4); ties broken by stable ID."""

    order: list[tuple[str, str, str | None]] = []
    returned: set[str] = set()

    def member_rows(invariant: str, group: str | None) -> None:
        order.append(("member", invariant, group))
        order.extend(
            (entry.kind, entry.id, group)
            for entry in _ordered(structure.members[invariant].entries)
        )
        returned.add(invariant)

    for invariant in structure.seed:
        member_rows(invariant, None)
    for family_id in sorted(structure.families):
        order.append(("family_header", family_id, family_id))
        for invariant in sorted(structure.families[family_id].members):
            if invariant in structure.seed:
                continue
            if invariant in returned:
                order.append(("member_reference", invariant, family_id))
            else:
                member_rows(invariant, family_id)
    order.extend(("advertised_family", family_id, None) for family_id, _ in structure.advertised)
    # MIK-R05 rule 5: the route chain comes after the MIK-R01 content.
    order.extend((CHAIN_ROW, family.id, None) for family in structure.chain)
    return tuple(order)


def _ordered(entries: Iterable[Entry]) -> list[Entry]:
    """A member's entries: realizations, then proofs, each by path then entry ID."""

    return sorted(entries, key=lambda one: (one.kind != "realization", one.path, one.id))


# --------------------------------------------------------------------------------------------------
# Rendering the rows at one code tree
# --------------------------------------------------------------------------------------------------


@dataclass
class _Rendering:
    """What rendering one selection's rows needs, and which section each member was returned in."""

    structure: LeafStructure
    states: Mapping[str, Any]
    observed: Mapping[str, Any]
    entries: Mapping[str, Entry]
    returned_under: dict[str, str] = field(default_factory=dict)


def leaf_rows(structure: LeafStructure, currentness: Currentness) -> tuple[PageRow, ...]:
    """The selection as page rows, each entry and invariant stated at ``currentness``'s tree."""

    rendering = _Rendering(
        structure=structure,
        states={one.id: one for one in currentness.invariants},
        observed=_observed(currentness),
        entries={e.id: e for member in structure.members.values() for e in member.entries},
    )
    return tuple(
        _RENDERERS[kind](rendering, subject, group) for kind, subject, group in structure.order
    )


def _observed(currentness: Currentness) -> dict[str, Any]:
    """Each entry's observation; a read-wide reason (no code tree was requested) is stated once, in
    the ``currentness`` block, rather than on every entry row."""

    return {
        e.entry_id: e if e.reason != currentness.problem else replace(e, reason=None)
        for one in currentness.invariants
        for e in one.entries
    }


def _render_member(rendering: _Rendering, subject: str, group: str | None) -> PageRow:
    rendering.returned_under.setdefault(subject, group or _SEED_SECTION)
    return PageRow(_member_row(rendering.structure, subject, rendering.states), group=group)


def _render_entry(rendering: _Rendering, subject: str, group: str | None) -> PageRow:
    return PageRow(_entry_row(rendering.entries[subject], rendering.observed), group=group)


def _render_header(rendering: _Rendering, subject: str, group: str | None) -> PageRow:
    body, reference = _header_row(rendering.structure, subject, rendering.states)
    return PageRow(body, group=group, reference=reference)


def _render_reference(rendering: _Rendering, subject: str, group: str | None) -> PageRow:
    record = rendering.structure.members[subject].record
    body = {
        "kind": "member_reference",
        "id": subject,
        "revision": record.revision,
        # An invariant has no title; the reference names it by its statement's first sentence,
        # cut to a fixed length, and says so (L01 ruling Q1).
        "title": derived_title(str(record.document.get("statement", ""))),
        "titleDerivedFrom": "statement",
        "returnedUnder": rendering.returned_under.get(subject, _SEED_SECTION),
    }
    return PageRow(body, group=group)


def _render_advertised(rendering: _Rendering, subject: str, group: str | None) -> PageRow:
    via = dict(rendering.structure.advertised)[subject]
    body = {
        "kind": "advertised_family",
        "id": subject,
        "title": rendering.structure.titles.get(subject),
        "via": list(via),
    }
    return PageRow(body, group=group)


def _render_chain(rendering: _Rendering, subject: str, group: str | None) -> PageRow:
    family = next(one for one in rendering.structure.chain if one.id == subject)
    return PageRow(chain_row(family), group=group)


_RENDERERS: Final = {
    "member": _render_member,
    "realization": _render_entry,
    "proof": _render_entry,
    "family_header": _render_header,
    "member_reference": _render_reference,
    "advertised_family": _render_advertised,
    CHAIN_ROW: _render_chain,
}


def derived_title(statement: str) -> str:
    """A reference title for an invariant: its statement's first sentence, at most
    :data:`DERIVED_TITLE_LENGTH` characters (an ellipsis marks a cut)."""

    text = " ".join(statement.split())
    match = _SENTENCE_END.search(text)
    sentence = text if match is None else text[: match.end()].rstrip()
    if len(sentence) <= DERIVED_TITLE_LENGTH:
        return sentence
    return sentence[: DERIVED_TITLE_LENGTH - 1].rstrip() + "…"


def _member_row(
    structure: LeafStructure, invariant: str, states: Mapping[str, Any]
) -> dict[str, Any]:
    record = structure.members[invariant].record
    document = record.document
    state = states.get(invariant)
    return {
        "kind": "member",
        "id": invariant,
        "revision": record.revision,
        "status": record.status,
        "admission": document.get("admission"),
        "statement": document.get("statement"),
        "applicability": document.get("applicability"),
        "conditions": list(document.get("conditions", ())),
        "exclusions": list(document.get("exclusions", ())),
        "state": None if state is None else state.state,
        # Rule 7: every family containing the invariant, the selected and the advertised alike.
        "families": list(structure.members[invariant].families),
    }


def _entry_row(entry: Entry, observed: Mapping[str, Any]) -> dict[str, Any]:
    document = entry.document
    anchor = document.get("anchor") or {}
    row: dict[str, Any] = {
        "kind": entry.kind,
        "id": entry.id,
        "invariant": entry.invariant,
        "path": entry.path,
        "locator": anchor.get("locator"),
    }
    if entry.kind == "realization":
        row["role"] = document.get("role")
    else:
        row["facet"] = document.get("facet")
    observation = observed.get(entry.id)
    row["state"] = None if observation is None else observation.state
    if observation is not None and observation.reason is not None:
        row["reason"] = observation.reason
    return row


def _header_row(
    structure: LeafStructure, family_id: str, states: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    family = structure.families[family_id]
    record, document = family.record, family.record.document
    stale = [m for m in family.members if getattr(states.get(m), "state", None) == "stale"]
    body = {
        "kind": "family_header",
        "id": family_id,
        "revision": record.revision,
        "status": record.status,
        "admission": document.get("admission"),
        "title": document.get("title"),
        "guarantee": document.get("guarantee"),
        "routes": list(family.routes),
        "memberCount": len(family.members),
        "members": list(family.members),
        "staleMembers": len(stale),
    }
    reference = {
        "kind": "family_header_reference",
        "id": family_id,
        "revision": record.revision,
        "title": document.get("title"),
    }
    return body, reference


def leaf_counts(structure: LeafStructure, currentness: Currentness) -> dict[str, Any]:
    """The selection's counts (rule 8), without the walk's position."""

    entries = [e for member in structure.members.values() for e in member.entries]
    return {
        "families": len(structure.families),
        "members": len(structure.members),
        "entries": len(entries),
        "realizations": sum(1 for e in entries if e.kind == "realization"),
        "proofs": sum(1 for e in entries if e.kind == "proof"),
        "distinctPaths": len({e.path for e in entries}),
        "invariantsByState": _by_state(structure, currentness),
        "advertisedFamilies": len(structure.advertised),
        "chainFamilies": len(structure.chain),
        "rowsTotal": len(structure.order),
    }


def _by_state(structure: LeafStructure, currentness: Currentness) -> dict[InvariantState, int]:
    tally = dict.fromkeys(INVARIANT_STATES, 0)
    for one in currentness.invariants:
        if one.id in structure.members:
            tally[one.state] += 1
    return tally


def family_names(index: KnowledgeIndex, invariant: str) -> list[dict[str, Any]]:
    """The live families containing ``invariant``, each by ID and title (rule 7)."""

    families = index.invariant(invariant).value.families
    return [
        {"id": one, "title": _title(index, one)} for one in families if _live(index, one, "family")
    ]
