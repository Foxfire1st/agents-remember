"""Pure projection diffing -- the per-entity SSE delta computation (slice 04).

A transport-side concern kept out of the reducer: ``project_and_write`` produces full
:class:`WorkspaceProjection` snapshots, and this module turns two consecutive snapshots
into the minimal set of per-entity change events the ``state`` SSE channel emits. The
flat, id-keyed schema (lifecycles/enclosures/providers cross-referenced by id --
North-Star #2) makes the diff a simple by-key compare; the client merges each named
event into its store by the same key. Output ordering is deterministic (upserts in
projection order, removals sorted) so replay/sim fixtures compare byte-for-byte.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from agents_remember.observer.projection import WorkspaceProjection


@dataclass(frozen=True)
class DeltaEvent:
    """One named SSE delta: an upserted projection node, or a removal marker.

    ``event`` is the SSE event name the client listens on (``lifecycle``,
    ``lifecycle.removed``, ``enclosure``, ``provider``, ``metrics``, ``analytics``).
    ``data`` is the upserted node (a projection ``BaseModel``) or, for a removal, a
    ``{key: id}`` marker.
    """

    event: str
    data: BaseModel | dict[str, Any]


def diff_projection(
    previous: WorkspaceProjection | None, current: WorkspaceProjection
) -> list[DeltaEvent]:
    """The per-entity changes between two projections (empty on the first tick).

    The first projection is delivered as the connection snapshot, not as deltas, so
    ``previous is None`` yields nothing here.
    """
    if previous is None:
        return []
    deltas: list[DeltaEvent] = []
    deltas += _collection_deltas("lifecycle", previous.lifecycles, current.lifecycles, key="id")
    deltas += _collection_deltas(
        "enclosure", previous.enclosures, current.enclosures, key="enclosure"
    )
    deltas += _collection_deltas("provider", previous.providers, current.providers, key="id")
    if previous.activeWorktreeGroups != current.activeWorktreeGroups:
        # A bare list isn't a node with a key, so it rides as a whole-value replacement
        # (like metrics/analytics) wrapped in a marker dict the client unwraps.
        deltas.append(
            DeltaEvent("activeWorktreeGroups", {"activeWorktreeGroups": current.activeWorktreeGroups})
        )
    if previous.metrics != current.metrics:
        deltas.append(DeltaEvent("metrics", current.metrics))
    if previous.analytics != current.analytics:
        deltas.append(DeltaEvent("analytics", current.analytics))
    return deltas


def _collection_deltas(
    name: str, previous: Sequence[BaseModel], current: Sequence[BaseModel], *, key: str
) -> list[DeltaEvent]:
    previous_by = {getattr(item, key): item for item in previous}
    current_by = {getattr(item, key): item for item in current}
    deltas = [
        DeltaEvent(name, item)
        for ident, item in current_by.items()
        if previous_by.get(ident) != item
    ]
    removed = sorted(previous_by.keys() - current_by.keys(), key=str)
    deltas += [DeltaEvent(f"{name}.removed", {key: ident}) for ident in removed]
    return deltas
