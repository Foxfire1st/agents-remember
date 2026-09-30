"""Route-chain families of a seed path, appended to its leaf read (MIK-R05).

A path sees every family whose territory it lies in, even when it realizes none of the family's
members: a new file is the typical case. Families are listed only at their own routes (MIK-R04), so
the read walks upward from the path.

* **The chain** is the path's directory and every ancestor directory, up to the repository root
  ``.``. It is computed at read time from the path alone and labelled ``mechanical``. Nothing walks
  down into child or sibling directories, and no ownership is inferred.
* **A chain entry** is one compact ``chain_family`` row per live family with a route on the chain:
  its ID, revision, title, guarantee, routes and member count; ``via``, the family's routes on the
  chain, nearest first; ``memberAtSeed``, whether one of its members has an entry at the seed path
  (the family-complete read then already expanded it above, and the row does not repeat that
  content); and ``expand``, the ``knowledge_read`` arguments of the family seed that returns the
  full MIK-R01 family content.
* **Order.** Chain entries come after the MIK-R01 content, nearest route first, then by family ID.
  Each is one indivisible row of MIK-R02, so they count toward the same threshold and resume
  through the same continuation.
* **No governing family.** When no family route covers the path, the read states
  ``no_governing_family``. It is a state, not an error.

**``served_earlier`` is a rendering of ``read_ar_files`` only** (:func:`shorten_served`). The
selection always holds every chain entry; the mounted read may shorten an entry already served in
the same lifecycle to a reference row, which keeps its position and so still counts.
``knowledge_read`` never shortens.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Final

from agents_remember.kernel.canonical_json import prefixed_sha256_digest
from agents_remember.memory.knowledge_index import KnowledgeIndex, Record
from agents_remember.models.knowledge_files.sidecars import ROOT_ROUTE_PATH

__all__ = [
    "CHAIN_DERIVATION",
    "CHAIN_ROW",
    "NO_GOVERNING_FAMILY",
    "SERVED_EARLIER",
    "ChainFamily",
    "chain_directory",
    "chain_row",
    "route_chain_block",
    "select_chain",
    "served_hash",
    "shorten_served",
]

# The chain is derived from the path, never authored (MIK-R05 rule 1).
CHAIN_DERIVATION: Final = "mechanical"
CHAIN_ROW: Final = "chain_family"
GOVERNED: Final = "governed"
NO_GOVERNING_FAMILY: Final = "no_governing_family"
# The reference row ``read_ar_files`` shortens an already-served chain entry to (rule 4).
SERVED_EARLIER: Final = "served_earlier"

_RETIRED: Final = "retired"
# The fields of a chain entry that depend on the seed path, not on the family: a served entry is
# recognised by the family's own content only.
_SEED_FIELDS: Final = ("memberAtSeed", "via")


@dataclass(frozen=True)
class ChainFamily:
    """One live family with a route on the seed path's chain."""

    record: Record
    members: tuple[str, ...]
    routes: tuple[str, ...]
    via: tuple[str, ...]
    member_at_seed: bool

    @property
    def id(self) -> str:
        return self.record.id


def chain_directory(path: str) -> str:
    """The seed path's directory: the first link of its chain (``.`` at the repository root)."""

    return PurePosixPath(path.strip("/")).parent.as_posix()


def select_chain(
    index: KnowledgeIndex, path: str, seed: Collection[str]
) -> tuple[ChainFamily, ...]:
    """Every live family with a route on ``path``'s chain, nearest route first, then by ID.

    ``seed`` are the live invariants with an entry at ``path``: a family containing one of them has
    a member at the seed path.
    """

    directory = chain_directory(path)
    distance = {link: depth for depth, link in enumerate(_links(directory))}
    via: dict[str, list[str]] = {}
    for family_id, route in index.families_governing(directory).value:
        via.setdefault(family_id, []).append(route)
    chain: list[ChainFamily] = []
    for family_id, routes_on_chain in via.items():
        knowledge = index.family(family_id).value
        record = knowledge.record
        if record is None or record.kind != "family" or record.status == _RETIRED:
            continue
        members = tuple(m for m in knowledge.members if _live_invariant(index, m))
        chain.append(
            ChainFamily(
                record=record,
                members=members,
                routes=knowledge.routes,
                via=tuple(sorted(routes_on_chain, key=distance.__getitem__)),
                member_at_seed=any(member in seed for member in members),
            )
        )
    return tuple(sorted(chain, key=lambda one: (distance[one.via[0]], one.id)))


def _links(directory: str) -> list[str]:
    """The chain of ``directory``: itself, then each ancestor, ending with the root ``.``."""

    current = PurePosixPath(directory)
    links = [current.as_posix(), *(p.as_posix() for p in current.parents)]
    return links if links[-1] == ROOT_ROUTE_PATH else [*links, ROOT_ROUTE_PATH]


def _live_invariant(index: KnowledgeIndex, invariant: str) -> bool:
    record = index.record(invariant).value
    return record is not None and record.kind == "invariant" and record.status != _RETIRED


def chain_row(family: ChainFamily) -> dict[str, Any]:
    """The compact chain entry of one family (MIK-R05 rule 2)."""

    document = family.record.document
    return {
        "kind": CHAIN_ROW,
        "id": family.id,
        "revision": family.record.revision,
        "title": document.get("title"),
        "guarantee": document.get("guarantee"),
        "routes": list(family.routes),
        "memberCount": len(family.members),
        "via": list(family.via),
        "memberAtSeed": family.member_at_seed,
        # Rule 3: the family seed that returns the full MIK-R01 family content.
        "expand": {
            "operation": "knowledge_read",
            "view": "source_context",
            "familyRevisionId": family.id,
        },
    }


def route_chain_block(path: str, chain: Collection[ChainFamily]) -> dict[str, Any]:
    """The page's statement of the chain it walked and what it found (rule 1, Failure).

    The chain is the directory and every ancestor, so the directory and its depth name it whole;
    listing each ancestor would grow with the square of the path's depth.
    """

    directory = chain_directory(path)
    return {
        "directory": directory,
        "links": len(_links(directory)),
        "derivation": CHAIN_DERIVATION,
        "state": GOVERNED if chain else NO_GOVERNING_FAMILY,
        "families": len(chain),
    }


# --------------------------------------------------------------------------------------------------
# The read_ar_files rendering (rule 4)
# --------------------------------------------------------------------------------------------------

# ``should_serve(family_id, content_hash)``: serve the entry in full (and record it), or not.
ShouldServe = Callable[[str, str], bool]


def shorten_served(block: dict[str, Any], should_serve: ShouldServe) -> dict[str, Any]:
    """``block`` with each chain entry already served in this session as a ``served_earlier`` row.

    Only the rendering changes: the row keeps its position, so the page's counts and its
    continuation are those of the full selection.
    """

    for seed in block.get("seeds", ()):
        rows = seed.get("rows") if isinstance(seed, dict) else None
        if not isinstance(rows, list):
            continue
        seed["rows"] = [_rendered(row, should_serve) for row in rows]
    return block


def served_hash(row: dict[str, Any]) -> str:
    """The content hash a chain entry is served under: the family's own content only."""

    return prefixed_sha256_digest({k: v for k, v in row.items() if k not in _SEED_FIELDS})


def _rendered(row: Any, should_serve: ShouldServe) -> Any:
    if not isinstance(row, dict) or row.get("kind") != CHAIN_ROW:
        return row
    if should_serve(str(row["id"]), served_hash(row)):
        return row
    return {
        "kind": SERVED_EARLIER,
        "servedKind": CHAIN_ROW,
        "id": row["id"],
        "revision": row["revision"],
        "title": row["title"],
        "via": row["via"],
        "memberAtSeed": row["memberAtSeed"],
    }
