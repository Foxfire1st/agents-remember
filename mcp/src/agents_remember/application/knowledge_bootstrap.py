"""One taskless bootstrap run: admit, write through the one write plane, read it back, retain progress.

This is the composition the bootstrap requirement is about. It owns no write, no identity, no
namespace and no snapshot: it admits a context
(:mod:`~agents_remember.application.knowledge_bootstrap_admission`), hands the curator's list to the
existing ingest operation as **one** admitted batch, and then reports what the repository actually
holds afterwards. Everything in between -- the candidate, the namespace, the identity allocation, the
first generation, the batch, the snapshot and the publication -- stays with the shipped owners, which
is why a bootstrap and a leaf's ordinary authoring cannot drift apart: they are the same operation
with different admissions.

**Three decisions are made here, and only three.**

* **What this run forks from.** The destination the ordinary read route selects is read BEFORE
  anything is written, through that route's own owner. A location that holds a dataset is the baseline
  this run forks from and the exact identity the publication may replace -- the explicit update. A
  location that holds nothing is the cold start. A location that holds something which is not a
  dataset of this code is neither, and the run refuses by name instead of publishing over it or
  starting a second store beside it.
* **What is published back.** The publication owner's own result is read back through
  :func:`~agents_remember.application.knowledge_publication_route.published_identity_read_back`, so
  the report says whether the dataset a *reader* will select is the one the write reported. Exit
  status is never that proof.
* **What remains.** The remaining-work manifest is derived from a read of the published dataset
  through the shipped view API, keyed on the identities the candidate's allocation journal holds --
  never from the plan and never from the run's own hopes -- and unioned with the owed work the record
  this run inherited named, each carried entry re-derived against this run's own store read
  (:func:`_carried_forward`). A store read that cannot be completed produces a named limitation, and
  an entry whose revision is not in the dataset is `absent` while one whose revision could not be
  looked for at all is `unmeasured`. The two are not the same fact and are never merged into one
  shorter list.

**The record is written by any run that was given the commit word.** ``commit`` is the developer's
commit word and the only write condition: a run that was given it persists the record even when its
batch wrote nothing, because every field in the record is a *read* -- of the store and of that run's
own outcome -- and a run that wrote nothing still has something true to retain, including the owed
work it re-derived. A **planning** run persists nothing at all: without the commit word the record is
not written, and a manifest claiming a publication a planning run never made would be exactly the
false sentence about the store this path exists to avoid. A published-not run -- the batch committed
but the publication refused -- writes a record whose ``batchState``, ``publicationState`` and
per-entry rows say that, instead of borrowing the success of the write half.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agents_remember.application.knowledge_bootstrap_admission import AdmittedKnowledgeBootstrap
from agents_remember.application.knowledge_bootstrap_staging import (
    BOOTSTRAP_PROGRESS_SCHEMA,
    BootstrapProgress,
    EntryProgress,
    EntryStoreState,
    ProgressRetention,
    observed_now,
    read_progress,
    staged_candidate_directory,
    write_progress,
)
from agents_remember.application.knowledge_curator_ingest import (
    HeldOperation,
    IngestPublication,
    IngestReport,
    IngestSelection,
    ingest_curator_list,
)
from agents_remember.application.knowledge_dataset_contents import (
    DatasetContents,
    dataset_revisions,
)
from agents_remember.application.knowledge_publication_route import (
    DeclaredPublicationLocation,
    PublishedIdentityReadBack,
    published_identity_read_back,
)
from agents_remember.application.published_intent import (
    PublishedIntentSelection,
    PublishedIntentUnavailable,
    resolve_published_intent,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity

__all__ = [
    "BootstrapRunRefusal",
    "BootstrapRunResult",
    "DatasetContents",
    "DestinationReading",
    "bootstrap_knowledge",
]

DestinationState = Literal["not-recorded", "recorded", "unusable"]
ContentsState = Literal["complete", "partial", "unavailable"]


@dataclass(frozen=True)
class BootstrapRunRefusal:
    """Why a bootstrap run did not begin, and the route that re-observes the condition."""

    code: str
    detail: str
    next_action: str


@dataclass(frozen=True)
class DestinationReading:
    """What the ordinary read route found at the destination before this run wrote anything.

    ``baseline`` is the path this run forks from and ``expected`` is the exact identity its
    publication may replace; both are ``None`` when the location holds nothing, which is the cold
    start rather than a failure. They are derived from the same read, so a run cannot fork from one
    dataset and publish over another.
    """

    state: DestinationState
    identity: SnapshotIdentity | None
    detail: str
    baseline: Path | None
    expected: SnapshotIdentity | None


@dataclass(frozen=True)
class _ObservedRun:
    """Everything one run measured, grouped so the retained record is assembled from one value."""

    retention: ProgressRetention
    before: DestinationReading
    readback: PublishedIdentityReadBack | None
    report: IngestReport
    contents: DatasetContents
    entries: tuple[EntryProgress, ...]
    remaining: tuple[str, ...]
    unmeasured: tuple[str, ...]
    carried: tuple[str, ...]


@dataclass(frozen=True)
class BootstrapRunResult:
    """One bootstrap run's whole result: the admission, the batch, the readback and what remains."""

    admitted: AdmittedKnowledgeBootstrap
    retention: ProgressRetention
    destination_before: DestinationReading
    report: IngestReport
    readback: PublishedIdentityReadBack | None
    contents: DatasetContents
    entries: tuple[EntryProgress, ...]
    remaining: tuple[str, ...]
    unmeasured: tuple[str, ...]
    carried: tuple[str, ...]
    progress: BootstrapProgress
    progress_path: Path | None


def bootstrap_knowledge(
    admitted: AdmittedKnowledgeBootstrap,
    entries: Sequence[Mapping[str, Any]] | str | Path,
    *,
    authorization_ref: str,
    commit: bool,
) -> BootstrapRunResult | BootstrapRunRefusal:
    """Run one taskless bootstrap through the ordinary write plane, or name why it did not begin."""

    destination = admitted.destination_path
    retention = read_progress(
        admitted.staging_root, scope=admitted.admission.scope, destination_path=destination
    )
    if retention.state == "moved":
        return BootstrapRunRefusal(
            code="staging_belongs_to_another_operation",
            detail=retention.detail,
            next_action=(
                "re-observe the staging root and reconcile it before resuming: one staging directory "
                "belongs to exactly one bootstrap operation"
            ),
        )
    before = _read_destination(admitted)
    if before.state == "unusable":
        return BootstrapRunRefusal(
            code="destination_unusable",
            detail=before.detail,
            next_action=(
                "repair or relocate the object standing at the declared dataset location, then "
                "re-observe the destination; nothing was written and the previous dataset is intact"
            ),
        )
    report = ingest_curator_list(
        admitted.admission,
        entries,
        IngestSelection(
            candidate_directory=staged_candidate_directory(admitted.staging_root),
            authorization_ref=authorization_ref,
            dry_run=not commit,
            baseline=before.baseline,
            publication=IngestPublication(
                destination_path=destination, expected_destination=before.expected
            ),
        ),
    )
    readback = _read_back(admitted, report)
    contents = dataset_revisions(admitted.destination_path, _dataset_namespace(before, report))
    examined = _entry_progress(report, contents)
    carried = _carried_forward(retention, examined, contents)
    per_entry = (*examined, *carried)
    remaining = tuple(
        one.entry_id for one in per_entry if one.store_state in ("absent", "not-attempted")
    )
    unmeasured = tuple(one.entry_id for one in per_entry if one.store_state == "unmeasured")
    progress = _progress(
        admitted,
        _ObservedRun(
            retention=retention,
            before=before,
            readback=readback,
            report=report,
            contents=contents,
            entries=per_entry,
            remaining=remaining,
            unmeasured=unmeasured,
            carried=tuple(one.entry_id for one in carried),
        ),
    )
    # A run the developer committed writes the record even when its batch wrote nothing: every field
    # in the record is a *read* of the store and of this run's own outcome, so a run that wrote
    # nothing has something true to retain -- the owed work it re-derived -- and leaving the previous
    # record standing is how a manifest goes stale exactly when the candidate binding moved. A
    # planning run still writes nothing at all.
    written = write_progress(admitted.staging_root, progress) if commit else None
    return BootstrapRunResult(
        admitted=admitted,
        retention=retention,
        destination_before=before,
        report=report,
        readback=readback,
        contents=contents,
        entries=per_entry,
        remaining=remaining,
        unmeasured=unmeasured,
        carried=tuple(one.entry_id for one in carried),
        progress=progress,
        progress_path=written,
    )


def _carried_forward(
    retention: ProgressRetention,
    examined: tuple[EntryProgress, ...],
    contents: DatasetContents,
) -> tuple[EntryProgress, ...]:
    """The owed work an earlier record named that this run's own list does not mention.

    A bootstrap is resumed by handing over the work that is still owed, and a curator who narrows the
    list -- or hands over only the part that failed -- must not thereby delete the rest of the debt
    from the manifest. So every entry the retained record left owed is carried into this record, and
    its state is **re-derived against this run's own store read** rather than copied: an entry whose
    revision the dataset now holds is no longer owed, an entry whose absence this read establishes
    still is, and an entry this read could not decide stays ``unmeasured``. Nothing is carried from a
    record this run did not inherit: a planning run, a run that refused before the write (a ``moved``
    record or an unusable destination) and a run with no retained record all carry nothing.
    """

    inherited = retention.progress
    if inherited is None:
        return ()
    known = {one.entry_id for one in examined}
    owed = set(inherited.remaining) | set(inherited.unmeasured)
    carried: list[EntryProgress] = []
    for row in inherited.entries:
        if row.entry_id in known or row.entry_id not in owed:
            continue
        held = (
            None
            if row.allocated_revision_id is None
            else HeldOperation(
                entry_id=row.entry_id,
                invariant_id="",
                revision_id=row.allocated_revision_id,
                content_digest="",
            )
        )
        state, detail, revision = _store_state(held, contents)
        carried.append(
            EntryProgress(
                entry_id=row.entry_id,
                outcome="carried",
                detail=(
                    f"owed by the record retained at {inherited.observed_at or 'an unrecorded time'} "
                    f"and not on this run's list; its state below is this run's own store read"
                ),
                allocated_revision_id=revision,
                store_state=state,
                store_detail=detail,
            )
        )
    return tuple(carried)


def _read_destination(admitted: AdmittedKnowledgeBootstrap) -> DestinationReading:
    """What the ordinary read route finds at the declared location, as this run's fork decision."""

    resolved = resolve_published_intent(admitted.context)
    if isinstance(resolved, PublishedIntentUnavailable):
        if resolved.state == "not-recorded":
            return DestinationReading(
                state="not-recorded",
                identity=None,
                detail=resolved.detail,
                baseline=None,
                expected=None,
            )
        return DestinationReading(
            state="unusable",
            identity=None,
            detail=resolved.detail,
            baseline=None,
            expected=None,
        )
    identity = _identity_of(resolved)
    return DestinationReading(
        state="recorded",
        identity=identity,
        detail=(
            f"the declared location holds the repository's published dataset "
            f"{identity.logical_digest}, read through the ordinary read route before this run wrote"
        ),
        baseline=admitted.destination_path,
        expected=identity,
    )


def _identity_of(resolved: PublishedIntentSelection) -> SnapshotIdentity:
    return SnapshotIdentity(
        repository_id=resolved.repository_id,
        schema_version=resolved.schema_version,
        logical_digest=resolved.logical_digest,
    )


def _read_back(
    admitted: AdmittedKnowledgeBootstrap, report: IngestReport
) -> PublishedIdentityReadBack | None:
    """Read the published location back, or report that this run published nothing to read back.

    The condition is the run's own report: a publication the owner refused established nothing about
    the destination, and a run whose batch did not commit published nothing at all. In both cases the
    honest answer is that there is nothing to read back, not a read of whatever happens to be there.
    """

    publication = report.publication
    if publication is None or publication.identity is None:
        return None
    location = DeclaredPublicationLocation(context=admitted.context, path=admitted.destination_path)
    return published_identity_read_back(location, publication.identity)


def _dataset_namespace(before: DestinationReading, report: IngestReport) -> str | None:
    """The namespace a contents read must address, read from the dataset rather than assumed.

    A view read refuses a namespace the dataset is not bound to, so this value has to come from a read
    of the file: the destination read made before the run, or -- when this run published into a
    location that held nothing -- the identity this run's own publication reported. It is never the
    repository's display name, which is not an identity.
    """

    published = report.publication
    if published is not None and published.identity is not None:
        return published.identity.repository_id
    return None if before.identity is None else before.identity.repository_id


def _entry_progress(report: IngestReport, contents: DatasetContents) -> tuple[EntryProgress, ...]:
    """One entry's outcome beside the store's own answer about the revision that operation holds."""

    outcomes = _outcomes(report)
    held = {one.entry_id: one for one in report.held_operations}
    return tuple(
        _one_entry(entry_id, outcomes.get(entry_id), held.get(entry_id), contents)
        for entry_id in report.entries_read
    )


def _outcomes(report: IngestReport) -> dict[str, tuple[str, str]]:
    """Every entry's outcome from the run's own report, keyed by entry id.

    The report promises an entry appears in exactly one of committed, rulings and refused; an entry in
    none of them is reported as ``unaccounted`` here rather than being given the benefit of any of the
    three, because a list that lost an entry is a different fact from a list that wrote one.
    """

    outcomes: dict[str, tuple[str, str]] = {}
    for label, group in (
        ("committed", report.committed),
        ("skipped", report.rulings),
        ("refused", report.refused),
    ):
        for one in group:
            outcomes[one.entry_id] = (label, one.refusal or one.state)
    return outcomes


def _one_entry(
    entry_id: str,
    outcome: tuple[str, str] | None,
    held: HeldOperation | None,
    contents: DatasetContents,
) -> EntryProgress:
    """One entry's row: the run's outcome, and the store's answer about the held revision."""

    label, detail = outcome if outcome is not None else ("unaccounted", "")
    state, store_detail, revision = _store_state(held, contents)
    return EntryProgress(
        entry_id=entry_id,
        outcome=label,
        detail=detail,
        allocated_revision_id=revision,
        store_state=state,
        store_detail=store_detail,
    )


def _store_state(
    held: HeldOperation | None, contents: DatasetContents
) -> tuple[EntryStoreState, str, str | None]:
    """Whether the revision one entry's operation holds is in the published dataset.

    Presence is a measurement in every case. *Absence* is only a measurement when the contents read
    established it -- a completed walk, or a location holding no dataset file at all -- which is why a
    held revision that was not found under a partial or unreadable read is ``unmeasured`` rather than
    ``absent``: the two are different facts, and merging them would let a bounded read manufacture
    remaining work that may not exist. A publication the owner refused is the clearest case: the
    location holds no dataset, the committed revision is therefore measurably absent from it, and the
    entry IS outstanding work rather than an unknown.
    """

    if held is None:
        return (
            "not-attempted",
            "the candidate's allocation journal holds no creation operation for this entry, so this "
            "run wrote no revision for it and there is none to look for",
            None,
        )
    if held.revision_id in contents.revisions:
        return (
            "stored",
            f"the published dataset holds revision {held.revision_id} ({contents.detail})",
            held.revision_id,
        )
    if contents.absence_established:
        return (
            "absent",
            (
                f"the published dataset does not hold revision {held.revision_id}, and the contents "
                f"read established absence: {contents.detail}"
            ),
            held.revision_id,
        )
    return (
        "unmeasured",
        (
            f"whether the published dataset holds revision {held.revision_id} was not established: "
            f"{contents.detail}"
        ),
        held.revision_id,
    )


def _progress(admitted: AdmittedKnowledgeBootstrap, observed: _ObservedRun) -> BootstrapProgress:
    """The retained record, assembled only from what this run measured."""

    retention, before, report = observed.retention, observed.before, observed.report
    contents, entries = observed.contents, observed.entries
    remaining, unmeasured = observed.remaining, observed.unmeasured
    return BootstrapProgress(
        schema=BOOTSTRAP_PROGRESS_SCHEMA,
        scope=admitted.admission.scope,
        repo_id=admitted.repo_id,
        authority_source=admitted.authority.source,
        admission_source=str(admitted.admission.source_ref),
        destination_path=admitted.destination_path,
        destination_state=_destination_state(before, observed.readback),
        destination_identity=_destination_identity(before, observed.readback),
        destination_read_back=_read_back_state(observed.readback),
        destination_detail=_destination_detail(before, observed.readback),
        run_mode="planned" if report.dry_run else "committed",
        batch_state=report.batch_state,
        publication_state=(
            "not-selected" if report.publication is None else report.publication.state
        ),
        entries=entries,
        remaining=remaining,
        unmeasured=unmeasured,
        carried=observed.carried,
        remaining_basis=_remaining_basis(
            retention, contents, remaining, unmeasured, observed.carried
        ),
        observed_at=observed_now(),
    )


def _destination_state(
    before: DestinationReading, readback: PublishedIdentityReadBack | None
) -> str:
    """The location's state as a READ of it established: the read-back's, or the pre-run read's."""

    if readback is None:
        return before.state
    return "recorded" if readback.identity is not None else before.state


def _destination_identity(
    before: DestinationReading, readback: PublishedIdentityReadBack | None
) -> SnapshotIdentity | None:
    """The dataset a reader selects there: the read-back's own answer, else the pre-run read's."""

    if readback is None:
        return before.identity
    return readback.identity if readback.identity is not None else before.identity


def _read_back_state(readback: PublishedIdentityReadBack | None) -> str:
    """Which read established the destination fields, or that this run published nothing to read."""

    return "not-published" if readback is None else readback.state


def _destination_detail(
    before: DestinationReading, readback: PublishedIdentityReadBack | None
) -> str:
    """The read's own sentence about the location, so a disagreement is readable in the record."""

    return before.detail if readback is None else readback.detail


def _remaining_basis(
    retention: ProgressRetention,
    contents: DatasetContents,
    remaining: tuple[str, ...],
    unmeasured: tuple[str, ...],
    carried: tuple[str, ...],
) -> str:
    """How the remaining list was derived, and what it could not measure.

    It names all four inputs because a reader has to be able to tell a measured remaining list from
    one produced by a walk that stopped: the run's own per-entry outcome, the candidate's allocation
    journal, the contents read at the dataset identity the record carries, and the record this run
    inherited, whose owed entries are carried and re-derived.
    """

    retained = (
        "no progress was retained before this run"
        if retention.state == "absent"
        else f"progress retained before this run: {retention.state}"
    )
    inherited = (
        ""
        if not carried
        else (
            f" {len(carried)} of them ({', '.join(carried)}) were owed by the record this run "
            "inherited and are not on this run's own list; each was re-derived against this run's "
            "store read rather than copied."
        )
    )
    named = "entry is" if len(remaining) == 1 else "entries are"
    left = "entry is" if len(unmeasured) == 1 else "entries are"
    return (
        f"{len(remaining)} {named} named remaining: the published dataset measurably does not hold "
        f"the revision their creation operation holds, or this run recorded no creation operation "
        f"for them. Basis: the run's own per-entry outcomes, the candidate's allocation journal, and "
        f"a read of the published dataset through the invariant view ({contents.state}; "
        f"{contents.detail}). {retained}. "
        + (
            "No entry was left unmeasured."
            if not unmeasured
            else (
                f"{len(unmeasured)} {left} named UNMEASURED rather than remaining "
                f"({', '.join(unmeasured)}): the contents read did not establish absence for them."
            )
        )
        + inherited
    )
