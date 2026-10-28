"""Mechanical route context through the leaf-chain selector, over the already parsed K_C.

The adapter supplies only select_chain's three index queries. It opens no second store and
uses the same parsed records as the worklist; context never changes item identity or reach.
Each path's chain is walked once per side (:attr:`KnowledgeSide.route_chains`) and every item
naming the path gets its own copy of it. A chain is a function of the memory tree and the path, so
on a side that is the whole parse of a Git tree (:attr:`KnowledgeSide.whole`) it is also kept per
process by that tree's id (:data:`ROUTE_CHAINS`): the next comparison over the same memory walks
none again.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any, cast

from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo, is_complete_object_id
from agents_remember.memory.knowledge_index import KnowledgeIndex, Record
from agents_remember.memory.knowledge_index.query import Answer, FamilyKnowledge, IndexState
from agents_remember.models.knowledge_files.records import record_status


class _ChainLookups:
    def __init__(self, side: KnowledgeSide) -> None:
        self.side = side
        self.state = IndexState(side.key, "complete", side.converted, ())

    def record(self, record_id: str) -> Answer[Record | None]:
        asked = self.side.chain_records
        if record_id not in asked:
            asked[record_id] = self._record(record_id)
        return asked[record_id]

    def _record(self, record_id: str) -> Answer[Record | None]:
        indexed = self.side.parsed.records.get(record_id)
        if indexed is None:
            return Answer(None, self.state)
        record = indexed.record
        document = record.to_document()
        return Answer(
            Record(
                record.id,
                str(document["schema"]).removeprefix("ar-").removesuffix("/v1"),
                indexed.path,
                record.revision,
                record_status(record),
                document,
            ),
            self.state,
        )

    def family(self, family_id: str) -> Answer[FamilyKnowledge]:
        family = self.side.families[family_id]
        return Answer(
            FamilyKnowledge(
                self.record(family_id).value, tuple(family.members), tuple(family.routes)
            ),
            self.state,
        )

    def families_governing(self, path: str) -> Answer[tuple[tuple[str, str], ...]]:
        directory = PurePosixPath(path)
        ancestors = {
            directory.as_posix(),
            *(parent.as_posix() for parent in directory.parents),
            ".",
        }
        return Answer(
            tuple(
                sorted(
                    (key, route)
                    for key, family in self.side.families.items()
                    for route in family.routes
                    if route in ancestors
                )
            ),
            self.state,
        )


Chain = tuple[tuple[str, Any, tuple[str, ...]], ...]
"""One path's chain: each family's ID, title and routes, nearest route first."""

ROUTE_CHAINS: BoundedMemo[tuple[str, str], Chain] = BoundedMemo(65_536)
"""``(memory tree id, path)`` -> the path's chain on the whole parse of that tree (D569)."""


def route_chain(side: KnowledgeSide, paths: Iterable[str]) -> dict[str, Any]:
    # The leaf package imports currentness, which imports this worklist package.
    from agents_remember.application.knowledge_leaf.chain import select_chain  # noqa: PLC0415

    # select_chain consumes these lookups, not the on-disk index's lifecycle/storage methods.
    index = cast(KnowledgeIndex, _ChainLookups(side))
    walked = side.route_chains
    kept = side.whole and is_complete_object_id(side.key)

    def families(path: str) -> Chain:
        if path in walked:
            return walked[path]
        remembered = ROUTE_CHAINS.get((side.key, path)) if kept else None
        if remembered is not None:
            walked[path] = remembered[0]
            return walked[path]
        walked[path] = tuple(
            (family.id, deepcopy(family.record.document.get("title")), tuple(family.routes))
            for family in select_chain(index, path, ())
        )
        if kept:
            ROUTE_CHAINS.put((side.key, path), walked[path])
        return walked[path]

    return {
        path: {
            "label": "mechanical",
            "families": [
                {"id": family, "title": deepcopy(title), "routes": list(routes)}
                for family, title, routes in families(path)
            ],
        }
        for path in sorted(set(paths))
    }


def _paths(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        named = {
            str(value[key]) for key in ("path", "renamedTo") if isinstance(value.get(key), str)
        }
        return named | set().union(*(_paths(part) for part in value.values()))
    if isinstance(value, (list, tuple)):
        return set().union(*(_paths(part) for part in value))
    return set()


def with_route_context(item: dict[str, Any], side: KnowledgeSide) -> dict[str, Any]:
    facts = item["facts"]
    paths = _paths(facts)
    paths.update((facts.get("sourceBlobs") or {}).keys())
    return {
        **item,
        "facts": {
            **facts,
            "context": {
                **facts.get("context", {}),
                "routeChain": route_chain(side, paths),
            },
        },
    }
