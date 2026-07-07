"""Pure projection diffing -- the per-entity SSE delta computation (slice 04).

A transport-side concern kept out of the reducer: ``project_and_write`` produces full
:class:`WorkspaceProjection` snapshots, and this module turns two consecutive snapshots
into the minimal set of per-entity change events the ``state`` SSE channel emits. The
flat, id-keyed schema (lifecycles/enclosures/providers cross-referenced by id --
North-Star #2) makes the diff a simple by-key compare; the client merges each named
event into its store by the same key. Output ordering is deterministic (upserts in
projection order, removals sorted) so replay/sim fixtures compare byte-for-byte.

The change gate (260703-L15): the projection carries *volatile* now-relative age fields
(``staleSeconds`` / ``ageSeconds`` / ``waitSeconds`` / ``snapshotStaleSeconds`` /
``heartbeatAgeSeconds``) that are recomputed from the tick clock on every projection, so
a naive whole-value compare re-emits ~the full payload every tick even when nothing real
changed (measured live: ~780 KB/tick, ~96% of it the ``analytics`` whole-object -- the
dashboard-tab OOM driver). The diff therefore compares *stable forms*: model dumps with
the volatile age keys stripped recursively. A node whose only change is its ages emits
nothing; a node with a real change emits in full (fresh ages ride along). The client
mirrors the same key set (``dashboard/src/data/servedAges.ts``) and advances displayed
ages locally, so staleness surfaces stay truthful between emissions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from agents_remember.observer.projection import WorkspaceProjection

#: Now-relative age fields recomputed every tick (never content). Stripped recursively from
#: the stable forms the diff compares. Mirrored client-side in
#: ``dashboard/src/data/servedAges.ts`` (VOLATILE_AGE_FIELDS) -- keep the two sets in lockstep.
VOLATILE_AGE_FIELDS = frozenset(
    {"staleSeconds", "snapshotStaleSeconds", "ageSeconds", "waitSeconds", "heartbeatAgeSeconds"}
)


def _strip_volatile(value: Any) -> Any:
    """Recursively drop the volatile age keys from a dumped payload."""
    if isinstance(value, dict):
        return {
            key: _strip_volatile(item)
            for key, item in value.items()
            if key not in VOLATILE_AGE_FIELDS
        }
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def _stable_dump(model: BaseModel) -> Any:
    """One node's stable form: the wire dump (by-alias, none-free) minus volatile ages."""
    return _strip_volatile(model.model_dump(by_alias=True, exclude_none=True))


@dataclass(frozen=True)
class StableProjectionState:
    """One projection's stable forms, precomputed once per tick.

    The :class:`~agents_remember.serving.projector.Projector` caches the previous tick's
    instance so each tick pays for exactly ONE stable dump (~4 ms on a ~800 KB live
    payload) instead of re-dumping both sides of the compare.
    """

    lifecycles: dict[str, Any]
    enclosures: dict[str, Any]
    providers: dict[str, Any]
    active_worktree_groups: list[str]
    metrics: Any
    analytics: Any


def stable_projection_state(projection: WorkspaceProjection) -> StableProjectionState:
    """Compute the volatile-free compare forms for one projection."""
    return StableProjectionState(
        lifecycles={item.id: _stable_dump(item) for item in projection.lifecycles},
        enclosures={item.enclosure: _stable_dump(item) for item in projection.enclosures},
        providers={item.id: _stable_dump(item) for item in projection.providers},
        active_worktree_groups=list(projection.activeWorktreeGroups),
        metrics=_stable_dump(projection.metrics),
        analytics=_stable_dump(projection.analytics),
    )


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
    previous: WorkspaceProjection | None,
    current: WorkspaceProjection,
    *,
    previous_state: StableProjectionState | None = None,
    current_state: StableProjectionState | None = None,
) -> list[DeltaEvent]:
    """The per-entity changes between two projections (empty on the first tick).

    The first projection is delivered as the connection snapshot, not as deltas, so
    ``previous is None`` yields nothing here. Comparison is over the *stable forms*
    (volatile age fields stripped -- see :data:`VOLATILE_AGE_FIELDS`): a tick where only
    ages advanced emits NOTHING, which is what keeps an idle dashboard's state channel
    silent. The optional ``*_state`` arguments accept precomputed stable forms (the
    projector's per-tick cache); when omitted they are computed here, preserving the
    pure two-argument call form.
    """
    if previous is None:
        return []
    prev = previous_state if previous_state is not None else stable_projection_state(previous)
    cur = current_state if current_state is not None else stable_projection_state(current)
    deltas: list[DeltaEvent] = []
    deltas += _collection_deltas(
        "lifecycle", current.lifecycles, prev.lifecycles, cur.lifecycles, key="id"
    )
    deltas += _collection_deltas(
        "enclosure", current.enclosures, prev.enclosures, cur.enclosures, key="enclosure"
    )
    deltas += _collection_deltas(
        "provider", current.providers, prev.providers, cur.providers, key="id"
    )
    if prev.active_worktree_groups != cur.active_worktree_groups:
        # A bare list isn't a node with a key, so it rides as a whole-value replacement
        # (like metrics/analytics) wrapped in a marker dict the client unwraps.
        deltas.append(
            DeltaEvent("activeWorktreeGroups", {"activeWorktreeGroups": current.activeWorktreeGroups})
        )
    if prev.metrics != cur.metrics:
        deltas.append(DeltaEvent("metrics", current.metrics))
    if prev.analytics != cur.analytics:
        deltas.append(DeltaEvent("analytics", current.analytics))
    return deltas


def _collection_deltas(
    name: str,
    current_nodes: Sequence[BaseModel],
    previous_stable: dict[str, Any],
    current_stable: dict[str, Any],
    *,
    key: str,
) -> list[DeltaEvent]:
    """Upserts (stable-form inequality) in projection order, then sorted removals."""
    current_by = {getattr(item, key): item for item in current_nodes}
    deltas = [
        DeltaEvent(name, item)
        for ident, item in current_by.items()
        if previous_stable.get(ident) != current_stable[ident]
    ]
    removed = sorted(previous_stable.keys() - current_stable.keys(), key=str)
    deltas += [DeltaEvent(f"{name}.removed", {key: ident}) for ident in removed]
    return deltas
