"""The ordinary publication route: the declared destination, and what a read of it finds (ICR-R20@v1).

The write plane already had a real writer and a real publication owner before this module existed.
What it did not have was a *route*: ``--publish-to`` was whatever path the caller typed, so the
curator's ordinary run either invented a destination or published nowhere, and the repository's
published knowledge was a location two conventions had to agree on by luck. This module is the
missing decision, and it owns exactly three of them.

* **Which location the ordinary route publishes to.** The one location the ordinary read route
  declares -- :func:`~agents_remember.application.published_intent.published_dataset_path`, resolved
  for this enclosure through the same context owner the read side uses
  (:func:`~agents_remember.worktrees.modules.context.contract_context`). One spelling, owned once:
  this module computes no path of its own and names no file name of its own, so the location the
  writer reaches and the location a later read selects cannot drift apart by a second convention
  agreeing today. Inside an enclosure that location is the contract's memory **worktree** -- this
  task's own memory line -- which is the only place a task's knowledge can be written and still
  land with the rest of its memory.

* **What the run admits is already at that location.** A publication may never overwrite a dataset
  the caller never admitted, and the ordinary route has no caller-typed identity to offer. It does
  not need one: the run read its own baseline before anything could replace it, so when that
  baseline *is* the declared location, the identity those captured bytes hold is exactly the
  identity at the destination. That is what makes the ordinary update explicit rather than a
  hopeful overwrite, and every other case is stated as an absence the publication owner then refuses
  by name if the location turns out to hold something.

* **What a reader finds afterwards.** The publication owner's own result says what it installed;
  this route reads the location back through
  :func:`~agents_remember.application.published_intent.resolve_published_intent`, the owner the
  ordinary read route uses, and reports whether the dataset a *reader* will select is the one the
  write reported. A successful exit is not that proof, and neither is the writer's own return value:
  the read-back is an independent read of the file, in the same scope, through the reader's owner.

Nothing here writes, publishes, or decides an outcome. The batch and publication authorities stay
with :mod:`agents_remember.application.knowledge_curator_ingest` and the snapshot publication owner
it calls; this module resolves a destination, admits what is there, and reads back what a reader
sees.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.application.knowledge_baseline_generation import CapturedBaseline
from agents_remember.application.knowledge_before_half import read_captured_dataset_identity
from agents_remember.application.published_intent import (
    PublishedIntentUnavailable,
    published_dataset_path,
    resolve_published_intent,
)
from agents_remember.kernel.coordination_context.models import CoordinationContext
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.worktrees.modules.context import contract_context
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "DeclaredPublicationLocation",
    "DestinationAdmission",
    "PublishedIdentityReadBack",
    "admitted_destination",
    "declared_publication_location",
    "published_identity_read_back",
]


@dataclass(frozen=True)
class DeclaredPublicationLocation:
    """The declared location for one enclosure, together with the context it was resolved in.

    The two travel as one value because the read-back has to be made in the scope the write was made
    in: a path re-derived from a second context could name the official memory repository while the
    publication landed on this leaf's memory line, and the read-back would then report an absence
    the write never caused. Keeping the pair is what makes "the reader sees what the writer wrote" a
    fact about one scope rather than about two lookups that happen to agree.
    """

    context: CoordinationContext
    path: Path


@dataclass(frozen=True)
class DestinationAdmission:
    """What the ordinary route admits is at the destination before it publishes there.

    ``identity`` is the exact logical identity the run read at that path -- so the destination is
    expected to hold it -- or ``None`` for "nothing is admitted there". ``detail`` states which of
    the two it is and on what fact, because "expected to be absent" has several causes and a caller
    that has to guess which one is being given a message rather than a result.
    """

    identity: SnapshotIdentity | None
    detail: str


@dataclass(frozen=True)
class PublishedIdentityReadBack:
    """What an independent read of the published location found after a publication reported one.

    ``confirmed`` is the location holding exactly the dataset the publication reported; ``mismatch``
    is it holding a dataset that is a different one; ``unavailable`` is nothing readable being there
    at all, with the shipped refusal code for the exact input carried beside the reader's own
    sentence. A caller branches on ``state`` and, for the third, on ``refusal_code`` -- never on the
    prose.
    """

    state: Literal["confirmed", "mismatch", "unavailable"]
    dataset_path: Path
    identity: SnapshotIdentity | None
    detail: str
    refusal_code: str | None = None


def declared_publication_location(contract: WorktreeContract) -> DeclaredPublicationLocation:
    """The one location the ordinary route publishes to, resolved exactly as the read side resolves it.

    The resolution goes through the enclosure's own coordination context, so which memory root this
    is follows the same rule the read route follows: the contract's memory worktree inside a leaf
    enclosure, the canonical external memory root with no enclosure in scope. A repository whose
    memory layer cannot be resolved is refused by name rather than defaulted to a path this module
    guessed -- an unowned destination is the one thing a write must never acquire by falling back.
    """

    try:
        context = contract_context(contract)
    except (ValueError, OSError) as error:
        raise ValueError(
            "the repository's declared published dataset location could not be resolved for "
            f"{contract.contract_path}: {error}"
        ) from error
    return DeclaredPublicationLocation(context=context, path=published_dataset_path(context))


def admitted_destination(
    destination: Path, captured: CapturedBaseline | str | None
) -> DestinationAdmission:
    """What this run admits is at the destination, read from the baseline it captured itself.

    The four cases are the four facts a run can hold before it writes anything, and each states
    only what it established:

    * no baseline was named -- nothing is admitted at the destination;
    * the named baseline could not be captured -- the run holds that reason and no identity, so the
      destination is admitted as holding nothing and the publication owner refuses if it does not;
    * the named baseline is another path -- this run forks from elsewhere, so what the destination
      holds is not something it admitted;
    * the named baseline IS the destination -- the bytes read at the top of the run are the dataset
      standing there, and their identity is the one the publication may replace.

    What no case states is that anything *was* published. An admission is made before the list is
    read, and whether a publication happened is a fact only the run's own report holds: a planning run
    and a batch that committed no entry both select a destination and publish nothing. A detail
    reading "this is a first publication on this line" would therefore be the one field in the report
    that overstated the run, which is the failure this whole route exists to prevent -- the caller's
    report completes the line from the report instead.

    The last case is the ordinary update, and it is derived rather than typed: a caller-typed
    identity can be stale or wrong, while these bytes were read from this path before the run could
    publish over it (the capture ordering ``--baseline`` already documents).
    """

    if captured is None:
        return DestinationAdmission(
            identity=None,
            detail="no baseline was named, so the destination is admitted as holding nothing",
        )
    if isinstance(captured, str):
        return DestinationAdmission(
            identity=None,
            detail=(
                f"the named baseline was not captured ({captured}), so no identity at the "
                "destination was admitted and the destination is expected to hold nothing"
            ),
        )
    if captured.origin.resolve() != Path(destination).resolve():
        return DestinationAdmission(
            identity=None,
            detail=(
                f"the run forks from {captured.origin}, which is not this destination, so nothing "
                "at the destination was admitted and it is expected to hold nothing"
            ),
        )
    identity = read_captured_dataset_identity(captured.payload, captured.origin)
    if isinstance(identity, str):
        return DestinationAdmission(
            identity=None,
            detail=(
                f"the bytes captured from {captured.origin} are not a dataset this code can read "
                f"({identity}), so no identity at the destination was admitted"
            ),
        )
    return DestinationAdmission(
        identity=identity,
        detail=(
            "the destination holds the dataset this run forked from "
            f"({identity.logical_digest}), read from that path before this run could publish over it"
        ),
    )


def published_identity_read_back(
    location: DeclaredPublicationLocation, published: SnapshotIdentity
) -> PublishedIdentityReadBack:
    """Read the published location back through the owner the ordinary read route itself uses.

    The comparison is against the identity the publication reported, so a write that claimed a
    dataset the file does not hold is caught here instead of by the next task's planner. The reader
    is :func:`resolve_published_intent`, which answers with a named absence or a named unusable
    state rather than raising, so the read-back can never cost a run the report it already has; its
    own authority-home check is what keeps a location holding another repository's dataset from
    being reported as this run's publication.
    """

    resolved = resolve_published_intent(location.context)
    if isinstance(resolved, PublishedIntentUnavailable):
        return PublishedIdentityReadBack(
            state="unavailable",
            dataset_path=resolved.dataset_path,
            identity=None,
            refusal_code=resolved.code,
            detail=(
                f"the publication reported {published.logical_digest} at {location.path}, and a "
                f"read of that location found nothing to confirm it: {resolved.detail}"
            ),
        )
    identity = SnapshotIdentity(
        repository_id=resolved.repository_id,
        schema_version=resolved.schema_version,
        logical_digest=resolved.logical_digest,
    )
    if identity == published:
        return PublishedIdentityReadBack(
            state="confirmed",
            dataset_path=resolved.database_path,
            identity=identity,
            detail=(
                "a read of the published location holds exactly the dataset the publication "
                f"reported ({identity.logical_digest})"
            ),
        )
    return PublishedIdentityReadBack(
        state="mismatch",
        dataset_path=resolved.database_path,
        identity=identity,
        detail=(
            f"a read of the published location holds {identity.logical_digest}, which is not the "
            f"dataset the publication reported ({published.logical_digest})"
        ),
    )
