"""The requirement record group's derived views: pure functions of the stored rows, and nothing else.

Requirement 7 makes every view this record group serves *derived and regenerable from the owner's
packet plus the stored revisions*, disposable, and forbidden from becoming an authority. The
strongest way to hold that is not to store a view at all, and this module stores none: each builder
below is a pure function of the rows a caller already read, so

* deleting a view changes no obligation, no state and no acceptance -- there is nothing to delete;
* rebuilding from the same rows reproduces the view byte for byte, because its only inputs are those
  rows;
* a view that *could* disagree with the records is not expressible, because there is no second place
  a value could live.

Three of the four views are also where the record group's **no-winner** rules become readable:

* the current-state view reports a **fork** -- more than one head revision -- as more than one head.
  It does not pick one, because picking one is the substrate choosing which stored revision
  supersedes which, an authority it does not hold;
* the currentness facts carry *both* recorded states with their provenance and derive only whether
  they agree. The substrate never resolves a disagreement, so the derived value *is* the
  disagreement;
* the governing-route view reports the explicit **ungoverned** state for a ``NULL`` route. It never
  derives a route from the packet's directory, the repository's name or a path prefix, because a
  requirement packet is a task-relative document and its own location is not a code path.

Reading a payload is also where the record group proves the forbidden set is absent at the *stored*
plane: a view is built from a validated payload model, so a stored row carrying a task-status,
seat-ownership or lifecycle-gate field could not have been decoded into one at all.

Every builder is total over the rows it is handed. A record that holds no revision is a store
damaged outside the operation -- no write path here produces one, because a record and its first
revision are written in one transaction -- and the views say which parts are therefore absent
instead of inventing a placeholder revision to stand in for one.
"""

from __future__ import annotations

from collections.abc import Sequence

from agents_remember.memory.knowledge.requirement_records import (
    StoredRequirementRecord,
    StoredRequirementRevision,
)
from agents_remember.models.knowledge.requirement import (
    CurrentnessBasis,
    RequirementCurrentness,
    RequirementCurrentStateView,
    RequirementGoverningRouteView,
    RequirementPredecessorChainView,
    RequirementRecordedState,
    RequirementRevisionScope,
    RequirementRevisionView,
)

# How far a predecessor walk is followed before it is reported as truncated. No operation of this
# record group can build a cycle -- every write refuses one in-transaction -- so this bound is not a
# cycle guard: it is what keeps a store damaged outside the operation from hanging a read.
CHAIN_BOUND = 512

# The two distinct facts that leave a currentness fact with nothing to compare. They are reported
# separately because the remedies differ: one is a reference the owner could not resolve, the other
# is a comparison nobody asked for.
UNRESOLVED_OWNER_DETAIL = (
    "the record's owner resolution is unresolved, so the owner's recorded state is not available to "
    "compare against; the refusal the owner returned is carried on the revision's owner_resolution"
)
NOT_COMPARED_DETAIL = (
    "no owner-recorded state was consumed for this revision, so there is nothing to compare; the "
    "record reports its own stored state and its owner resolution, and claims no agreement"
)

# What a record with no stored revision reports. No write path of this record group produces one.
NO_REVISION_DETAIL = "the record holds no stored revision, so it serves no obligation"


def revision_view(revision: StoredRequirementRevision) -> RequirementRevisionView:
    """Return one stored revision as a derived view value."""

    payload = revision.payload
    return RequirementRevisionView(
        revision_id=revision.revision_id,
        owner=payload.owner,
        owner_resolution=payload.owner_resolution,
        explanation=payload.explanation,
        state_at_origin=payload.state_at_origin,
        acceptance_ref=payload.acceptance_ref,
        predecessor_revision_id=revision.predecessor_revision_id,
        content_digest=revision.content_digest,
        provenance=revision.provenance,
    )


def head_revision_ids(revisions: Sequence[StoredRequirementRevision]) -> tuple[str, ...]:
    """Return the revisions no stored revision names as its predecessor, in stored order.

    A head is a fact about the stored edges, so this reads them rather than a recorded designation:
    there is no mutable "current revision" column in this record group, and adding one would be the
    second authority requirement 7 forbids.
    """

    named = {
        revision.predecessor_revision_id
        for revision in revisions
        if revision.predecessor_revision_id is not None
    }
    return tuple(
        revision.revision_id for revision in revisions if revision.revision_id not in named
    )


def predecessor_chain_view(
    revisions: Sequence[StoredRequirementRevision], head_revision_id: str
) -> RequirementPredecessorChainView:
    """Walk one head revision's predecessor chain, nearest predecessor first."""

    by_id = {revision.revision_id: revision for revision in revisions}
    chain: list[str] = []
    current = by_id.get(head_revision_id)
    while current is not None and current.predecessor_revision_id is not None:
        if len(chain) >= CHAIN_BOUND:
            return RequirementPredecessorChainView(
                head_revision_id=head_revision_id,
                chain=tuple(chain),
                truncated=True,
                detail=(
                    f"the predecessor walk reached its bound of {CHAIN_BOUND} edges without "
                    "reaching a root revision; the chain is reported as truncated rather than "
                    "followed further"
                ),
            )
        parent = current.predecessor_revision_id
        chain.append(parent)
        current = by_id.get(parent)
    return RequirementPredecessorChainView(
        head_revision_id=head_revision_id,
        chain=tuple(chain),
        detail=(
            "the head revision is its own root: it records no predecessor"
            if not chain
            else f"the chain reaches a root revision after {len(chain)} predecessor edge(s)"
        ),
    )


def governing_route_view(record: StoredRequirementRecord) -> RequirementGoverningRouteView:
    """Return the recorded scope axis: the named route, or the explicit ungoverned state."""

    route = record.governing_route_id
    return RequirementGoverningRouteView(
        record_id=record.record_id,
        state="governed" if route is not None else "ungoverned",
        governing_route_id=route,
    )


def recorded_state_of(revision: StoredRequirementRevision) -> RequirementRecordedState:
    """Return one stored revision's recorded state with the provenance that records it."""

    payload = revision.payload
    return RequirementRecordedState(
        state_at_origin=payload.state_at_origin,
        acceptance_ref=payload.acceptance_ref,
        provenance=revision.provenance,
    )


def currentness_fact(
    revision: StoredRequirementRevision,
    owner_recorded: RequirementRecordedState | None = None,
    basis: CurrentnessBasis = "not-declared",
) -> RequirementCurrentness:
    """Return one currentness fact comparing this record's state with the owner's recorded state.

    ``owner_recorded`` is a *consumed* value: the state the owner itself records, handed in by a
    caller that obtained it from the owner. When there is nothing to compare the fact says which of
    the two reasons applies rather than reporting agreement it cannot show. When there is, and the
    two values differ, the fact carries both with their provenance and names no winner.
    """

    stored = recorded_state_of(revision)
    if revision.payload.owner_resolution.state == "unresolved":
        return RequirementCurrentness(
            revision_id=revision.revision_id,
            state="unresolved-owner",
            stored=stored,
            basis=basis,
            detail=UNRESOLVED_OWNER_DETAIL,
        )
    if owner_recorded is None:
        return RequirementCurrentness(
            revision_id=revision.revision_id,
            state="unresolved-owner",
            stored=stored,
            basis=basis,
            detail=NOT_COMPARED_DETAIL,
        )
    agrees = (stored.state_at_origin, stored.acceptance_ref) == (
        owner_recorded.state_at_origin,
        owner_recorded.acceptance_ref,
    )
    if agrees:
        detail = (
            f"the stored state {stored.state_at_origin!r} and the owner's recorded state "
            f"{owner_recorded.state_at_origin!r} are the same recorded value"
        )
    else:
        detail = (
            f"the stored state {stored.state_at_origin!r} and the owner's recorded state "
            f"{owner_recorded.state_at_origin!r} disagree; both are carried with their provenance "
            f"and the consumed basis is {basis!r}. This record group resolves no disagreement and "
            "chooses no winner"
        )
    return RequirementCurrentness(
        revision_id=revision.revision_id,
        state="aligned" if agrees else "disagreement",
        stored=stored,
        owner=owner_recorded,
        basis=basis,
        detail=detail,
    )


def current_state_view(
    record: StoredRequirementRecord,
    revisions: Sequence[StoredRequirementRevision],
    owner_recorded: RequirementRecordedState | None = None,
    basis: CurrentnessBasis = "not-declared",
) -> RequirementCurrentStateView:
    """Return the current-state view: the record's heads, and one currentness fact per head."""

    heads = head_revision_ids(revisions)
    by_id = {revision.revision_id: revision for revision in revisions}
    return RequirementCurrentStateView(
        record_id=record.record_id,
        head_revision_ids=heads,
        owner=by_id[heads[0]].payload.owner if heads else None,
        currentness=tuple(
            currentness_fact(by_id[head], owner_recorded, basis) for head in heads if head in by_id
        ),
        detail=(
            NO_REVISION_DETAIL
            if not heads
            else f"{len(heads)} head revision(s) over {len(revisions)} stored revision(s)"
        ),
    )


def revision_scope(
    record: StoredRequirementRecord,
    revisions: Sequence[StoredRequirementRevision],
    owner_recorded: RequirementRecordedState | None = None,
    basis: CurrentnessBasis = "not-declared",
) -> RequirementRevisionScope:
    """Build the whole scope view for one record from its stored rows.

    Everything the record group makes readable is in this one value, and every part of it is derived
    here from the rows passed in. That is what makes the scope disposable: a caller may drop it and
    call this again, and a rebuild over unchanged rows is byte-identical by construction.
    """

    heads = head_revision_ids(revisions)
    return RequirementRevisionScope(
        repository_id=record.repository_id,
        record_id=record.record_id,
        kind=record.kind,
        record_schema=record.record_schema,
        record_provenance=record.provenance,
        revisions=tuple(revision_view(revision) for revision in revisions),
        current_state=current_state_view(record, revisions, owner_recorded, basis),
        predecessor_chain=(predecessor_chain_view(revisions, heads[0]) if heads else None),
        governing_route=governing_route_view(record),
    )
