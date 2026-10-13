"""The relationships one snapshot's union items record, read from that snapshot (ICR-R08@v1).

The traversal this serves reads the union both snapshots record, so its inputs are two facts per
side and this module owns both: the shipped comparison's own union items (each carrying the side
payloads that selected it) and the identity each association revision belongs to -- read from the
snapshot's own revision rows, because the selection read does not project it.

Nothing here selects, pairs or concludes. A relationship is read exactly as the two records state it,
including the three cases a reader must not see merged: a claim whose anchor row could not be read
keeps that fact, a membership carries the member revision it holds as well as the family revision it
sits in, and an item that is not a relationship at all -- a revision is one of the two things a
relationship relates -- is reported as no relationship rather than as an empty one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import apsw

from agents_remember.memory.knowledge.read_queries import (
    fetch_family_revisions,
    fetch_invariant_revisions,
)
from agents_remember.models.knowledge.diff import DiffCoverage, KnowledgeDiffItem
from agents_remember.models.knowledge.read import ReadItem

__all__ = [
    "RecordedRelationship",
    "RecordedSnapshot",
    "read_snapshot_relationships",
    "recorded_change_state",
    "recorded_snapshot",
    "side_payload",
]

# The union item kinds that are recorded relationships, with the kind of identity each one sits
# under. A claim and a membership cite a revision and are recorded under an identity; an advertised
# frontier link is a membership the page advertises rather than traverses, and it is recorded under
# its family's identity like the membership it is. A revision item is not a relationship: it is one
# of the two things a relationship relates.
_RELATIONSHIP_KINDS: Mapping[str, Literal["invariant", "family"]] = {
    "realization": "invariant",
    "membership": "family",
    "advertised_family": "family",
}


@dataclass(frozen=True)
class RecordedSnapshot:
    """One snapshot as this traversal reads it: its side name, its file and a connection."""

    side: str
    database: Path
    connection: apsw.Connection


@dataclass(frozen=True)
class RecordedRelationship:
    """One recorded side of one relationship, as the store reports it and this read reached it."""

    kind: str
    item_id: str
    item_coverage: DiffCoverage
    side: str
    relationship_id: str
    record_kind: Literal["invariant", "family"]
    record_id: str | None
    revision_id: str | None
    member_revision_id: str | None
    path: str | None
    role: str | None
    rationale: str | None
    resolution: str | None
    resolution_detail: str | None
    recorded_source_identity: str | None
    observed_source_identity: str | None
    anchor_readable: bool
    change_state: Literal["changed", "unchanged", "not_selected"] | None
    reached_via: tuple[str, ...]


@dataclass(frozen=True)
class RecordedIdentityIndex:
    """The identity each association revision belongs to, per snapshot, read from that snapshot."""

    invariants: Mapping[str, str]
    families: Mapping[str, str]


def recorded_snapshot(side: str, database: Path, connection: apsw.Connection) -> RecordedSnapshot:
    """Hold one snapshot's file and connection beside its side name."""

    return RecordedSnapshot(
        side=side,
        database=Path(database),
        connection=connection,
    )


def side_payload(item: KnowledgeDiffItem, side: str) -> ReadItem | None:
    """Return one union item's payload for one side, or ``None`` when that side holds no record."""

    return item.before if side == "before" else item.after


def read_snapshot_relationships(
    items: Sequence[KnowledgeDiffItem], snapshot: RecordedSnapshot, repository_id: str
) -> tuple[RecordedRelationship, ...]:
    """Read every relationship one snapshot's union items record, in the union's own order."""

    payloads = tuple(
        (item, payload)
        for item in items
        if item.kind in _RELATIONSHIP_KINDS and (payload := side_payload(item, snapshot.side))
    )
    identities = _identity_index(
        snapshot, repository_id, tuple(payload for _item, payload in payloads)
    )
    return tuple(
        relationship
        for item, payload in payloads
        if (relationship := _relationship(item, payload, snapshot.side, identities)) is not None
    )


def _identity_index(
    snapshot: RecordedSnapshot,
    repository_id: str,
    payloads: Sequence[ReadItem],
) -> RecordedIdentityIndex:
    """Resolve the identity each association revision belongs to, from that snapshot's own rows.

    A realization item's payload carries the revision its claim cites but not the identity that
    revision belongs to (the selection read does not project it), and a membership's payload carries
    its family identity only when the family was selected. Both are read here from the snapshot's own
    revision rows, so the identity displayed is the one the snapshot records rather than one inferred
    from the item's shape.
    """

    invariant_revisions = sorted(
        {
            str(payload.invariant_revision_id)
            for payload in payloads
            if payload.kind == "realization_claim" and payload.invariant_revision_id is not None
        }
    )
    family_revisions = sorted(
        {
            str(payload.family_revision_id)
            for payload in payloads
            if payload.kind in ("family_membership", "advertised_family")
            and payload.family_revision_id is not None
        }
    )
    return RecordedIdentityIndex(
        invariants={
            str(row["revision_id"]): str(row["invariant_id"])
            for row in fetch_invariant_revisions(
                snapshot.connection, repository_id, invariant_revisions
            )
        },
        families={
            str(row["revision_id"]): str(row["family_id"])
            for row in fetch_family_revisions(snapshot.connection, repository_id, family_revisions)
        },
    )


def _relationship(
    item: KnowledgeDiffItem,
    payload: ReadItem,
    side: str,
    identities: RecordedIdentityIndex,
) -> RecordedRelationship | None:
    """Read one union item's payload for one side as a recorded relationship, or ``None``.

    ``None`` is returned for an item whose payload is not a relationship at all -- the union carries
    revisions beside relationships, and a revision is one of the two things a relationship relates.
    """

    kind = _RELATIONSHIP_KINDS.get(item.kind)
    if kind is None:
        return None
    revision_id = (
        payload.invariant_revision_id if kind == "invariant" else payload.family_revision_id
    )
    identity = _association_identity(kind, payload, revision_id, identities)
    anchor = payload.anchor if kind == "invariant" else None
    return RecordedRelationship(
        kind=item.kind,
        item_id=item.item_id,
        item_coverage=item.coverage,
        side=side,
        relationship_id=_relationship_id(item, payload),
        record_kind=kind,
        record_id=identity,
        revision_id=None if revision_id is None else str(revision_id),
        member_revision_id=_member_revision(payload, kind),
        path=None if anchor is None else anchor.path,
        role=payload.role if kind == "invariant" else None,
        rationale=payload.rationale if kind == "invariant" else None,
        resolution=None if anchor is None else anchor.resolution,
        resolution_detail=None if anchor is None else anchor.detail,
        recorded_source_identity=None if anchor is None else anchor.recorded_source_identity,
        observed_source_identity=None if anchor is None else anchor.observed_source_identity,
        anchor_readable=kind != "invariant" or anchor is not None,
        change_state=recorded_change_state(item) if kind == "invariant" else None,
        reached_via=tuple(item.reached_via),
    )


def _association_identity(
    kind: str,
    payload: ReadItem,
    revision_id: str | None,
    identities: RecordedIdentityIndex,
) -> str | None:
    """Return the identity one association sits under, as that snapshot records it."""

    if kind == "invariant":
        return None if revision_id is None else identities.invariants.get(str(revision_id))
    return payload.family_id or (
        None if revision_id is None else identities.families.get(str(revision_id))
    )


def _relationship_id(item: KnowledgeDiffItem, payload: ReadItem) -> str:
    """Return the recorded relationship's own identity: the claim, the member, or the pair."""

    return str(payload.claim_id or payload.member_id or item.item_id)


def _member_revision(payload: ReadItem, kind: str) -> str | None:
    """Return the member revision a family association holds, and nothing for a claim."""

    if kind == "invariant" or payload.invariant_revision_id is None:
        return None
    return str(payload.invariant_revision_id)


def recorded_change_state(
    item: KnowledgeDiffItem,
) -> Literal["changed", "unchanged", "not_selected"]:
    """The comparison's own statement about one claim's source observation, carried verbatim."""

    change = item.source_change
    if change is None:
        return "not_selected"
    if change.source_observation_changed or change.source_change_only:
        return "changed"
    return "unchanged"
