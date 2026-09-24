"""The bootstrap's temporary staging: its retained progress record and its bounded cleanup owner.

An interrupted bootstrap is the case this module exists for. The candidate database and the
allocation journal already survive an interruption -- they are the shipped candidate owner's files,
written into a directory this bootstrap names -- and what was missing is the *third* fact a resume
needs: which of the entries the hand-off list carried have actually reached the published dataset,
and which are still owed. Without it an operator can see that a bootstrap stopped but not what
remains, and the honest answer "we do not know" is exactly the state a resumed run must not be in.

**The record is a projection, never a store.** Every field here is derived from four things a run
already has or can read: the run's own report (the batch's own outcome), the candidate's allocation
journal (which identities the operation holds), a read of the **published destination** through the
ordinary read route's owner, and -- since owed work is carried across runs -- the record this run
inherited. Nothing in this module decides what is true; the dataset does. A resume **reads the old
record back** (:func:`_progress_from_record`) so the entries it left owed can be carried forward by
operation identity and re-derived against the resuming run's own store read, and it refuses outright
when the record describes a different operation -- a different scope or a different destination --
which is the explicit re-observation condition rather than a silent continuation.

**Cleanup has one owner and one guard.** A bootstrap's staging holds authored work that may not have
reached the dataset yet, so removing it is the one irreversible act on this path: "bootstrap writes an
unbound scratch SQLite file, loses it during cleanup" is the packet's own non-conforming example.
:func:`discard_bootstrap_staging` therefore removes the staging root only when the destination
provably holds the dataset the staged candidate holds now -- the candidate file is read, the
destination is read through the reader route's own owner, and the two identities must be equal. Every
other state refuses by name and leaves the bytes exactly as they are.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from agents_remember.application.knowledge_before_half import read_dataset_identity
from agents_remember.application.knowledge_dataset_contents import (
    DatasetContents,
    dataset_revisions,
)
from agents_remember.application.published_intent import (
    PublishedIntentSelection,
    PublishedIntentUnavailable,
    resolve_published_intent,
)
from agents_remember.kernel.coordination_context.models import CoordinationContext
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.snapshot import candidate_database_path

__all__ = [
    "BOOTSTRAP_CANDIDATE_DIRECTORY",
    "BOOTSTRAP_PROGRESS_NAME",
    "BOOTSTRAP_PROGRESS_SCHEMA",
    "BootstrapProgress",
    "BootstrapProgressConflict",
    "EntryProgress",
    "EntryStoreState",
    "ProgressRetention",
    "StagingCleanup",
    "discard_bootstrap_staging",
    "progress_path",
    "read_progress",
    "write_progress",
]

# The one directory name a bootstrap's working candidate occupies inside its staging root, and the
# one file name its retained progress record occupies beside it. Both are constants here so the
# writer, the resume path and the cleanup owner name one place by construction.
BOOTSTRAP_CANDIDATE_DIRECTORY = "candidate"
BOOTSTRAP_PROGRESS_NAME = "bootstrap-progress.json"
BOOTSTRAP_PROGRESS_SCHEMA = "ar-knowledge-bootstrap-progress/v1"

# What a store read established about one entry's revision. These are four distinct facts and are
# never collapsed: ``stored`` and ``absent`` are measurements, ``unmeasured`` is a read this run could
# not make, and ``not-attempted`` is the candidate's journal holding no creation operation for the
# entry at all -- nothing was written for it, so there is nothing in a dataset to look for. Every
# value here is produced by ``knowledge_bootstrap._store_state`` and consumed by its ``remaining``
# predicate; the vocabulary carries no state a later writer could emit that the consumer ignores.
EntryStoreState = Literal["stored", "absent", "unmeasured", "not-attempted"]

# The same four facts as a runtime tuple, so a *reader* of a retained record validates against the
# one declaration instead of a second copy of the vocabulary spelled somewhere else.
_ENTRY_STORE_STATES: tuple[str, ...] = ("stored", "absent", "unmeasured", "not-attempted")


class BootstrapProgressConflict(ValueError):
    """Raised when a staging root already retains progress for a different operation.

    One staging directory belongs to one bootstrap operation. A record naming another scope or
    another destination is not stale data to be overwritten: it is a different operation's retained
    work, and replacing it would destroy progress this run knows nothing about.
    """


@dataclass(frozen=True)
class EntryProgress:
    """One entry of the hand-off list, and what the run and the store established about it.

    ``outcome`` is the run's own report of this entry -- ``committed``, ``refused`` or ``skipped``
    -- and is the operation's statement about the batch. ``store_state`` is the *independent* read
    of the published destination: whether the revision this entry's operation holds is in the
    dataset a reader selects. The two are recorded side by side on purpose, because a run whose
    report and whose store disagree is a reconciliation condition and not a success, and a record
    that kept only one of them could not express that at all.
    """

    entry_id: str
    outcome: str
    detail: str
    allocated_revision_id: str | None
    store_state: EntryStoreState
    store_detail: str

    def as_record(self) -> dict[str, Any]:
        return {
            "entryId": self.entry_id,
            "outcome": self.outcome,
            "detail": self.detail,
            "allocatedRevisionId": self.allocated_revision_id,
            "storeState": self.store_state,
            "storeDetail": self.store_detail,
        }


@dataclass(frozen=True)
class BootstrapProgress:
    """The retained progress of one bootstrap operation, derived from the store and the report.

    ``remaining`` is the named remaining-work manifest: the entries this run established are **not**
    in the published dataset, in the order the hand-off list carried them. ``unmeasured`` is the
    separate list of entries whose absence the run could **not** establish -- a bounded or refused
    contents read -- and it is a field of its own rather than a longer ``remaining`` because a
    measured absence and an unread location are different facts, and merging them would let a
    truncated walk manufacture remaining work. ``remaining_basis`` states how both lists were derived
    and what the read could not reach.
    """

    schema: str
    scope: str
    repo_id: str
    authority_source: str
    admission_source: str
    destination_path: Path
    destination_state: str
    destination_identity: SnapshotIdentity | None
    destination_read_back: str
    destination_detail: str
    run_mode: str
    batch_state: str
    publication_state: str
    entries: tuple[EntryProgress, ...]
    remaining: tuple[str, ...]
    unmeasured: tuple[str, ...]
    carried: tuple[str, ...]
    remaining_basis: str
    observed_at: str

    def as_record(self) -> dict[str, Any]:
        """The exact JSON object this record stores, with every path as its posix spelling."""

        identity = self.destination_identity
        return {
            "schema": self.schema,
            "scope": self.scope,
            "repositoryId": self.repo_id,
            "authoritySource": self.authority_source,
            "admissionSource": self.admission_source,
            "destinationPath": self.destination_path.as_posix(),
            "destinationState": self.destination_state,
            "destinationIdentity": (
                None
                if identity is None
                else {
                    "repositoryId": identity.repository_id,
                    "schemaVersion": identity.schema_version,
                    "logicalDigest": identity.logical_digest,
                }
            ),
            "destinationReadBack": self.destination_read_back,
            "destinationDetail": self.destination_detail,
            "runMode": self.run_mode,
            "batchState": self.batch_state,
            "publicationState": self.publication_state,
            "entries": [entry.as_record() for entry in self.entries],
            "remaining": list(self.remaining),
            "unmeasured": list(self.unmeasured),
            "carried": list(self.carried),
            "remainingBasis": self.remaining_basis,
            "observedAt": self.observed_at,
        }


@dataclass(frozen=True)
class ProgressRetention:
    """What a staging root held before this run, and whether it still describes this operation.

    ``retained`` is a record for this exact operation and is read back so its owed entries can be
    carried forward; ``moved`` is a record for another operation or another destination -- the
    re-observation condition, and the run refuses -- ``unreadable`` is a file whose bytes are not this
    schema at all, and ``absent`` is no record at all. The four are separate answers because they call
    for four different acts, and a resume that treated ``moved`` as ``absent`` would quietly begin a
    second operation's progress inside the first one's staging.
    """

    state: Literal["absent", "retained", "moved", "unreadable"]
    path: Path
    progress: BootstrapProgress | None
    detail: str


@dataclass(frozen=True)
class StagingCleanup:
    """The outcome of one cleanup request: what was removed, or the fact that refused it."""

    state: Literal["discarded", "refused", "absent"]
    staging_root: Path
    code: str
    detail: str


def progress_path(staging_root: Path) -> Path:
    """The retained progress record's exact path inside one staging root."""

    return Path(staging_root) / BOOTSTRAP_PROGRESS_NAME


def staged_candidate_directory(staging_root: Path) -> Path:
    """The working candidate directory this staging root holds."""

    return Path(staging_root) / BOOTSTRAP_CANDIDATE_DIRECTORY


def observed_now() -> str:
    """The instant this observation is recorded at, in the shipped normalized-UTC spelling."""

    return datetime.now(UTC).isoformat()


def _recorded_text(value: object) -> str:
    """One recorded string field, or ``""`` for a value the record does not actually carry.

    A missing or null field is reported as absent rather than rendered as the word "None": a detail
    sentence naming ``scope 'None'`` would be a false statement about a record that simply did not
    carry one.
    """

    return value if isinstance(value, str) else ""


def read_progress(staging_root: Path, *, scope: str, destination_path: Path) -> ProgressRetention:
    """Read what a staging root retained, and whether it belongs to this operation.

    A record whose scope or destination is not this run's is ``moved`` rather than stale: it is
    another operation's retained progress, and the caller must reconcile rather than overwrite. A
    file that exists and does not carry this schema, or whose entry rows cannot be read as entries,
    is ``unreadable``; an absent file is ``absent``. None of the four is ever reported as "no
    progress", which would be a measurement this function did not make.
    """

    path = progress_path(staging_root)
    if not path.is_file():
        return ProgressRetention(
            state="absent",
            path=path,
            progress=None,
            detail=f"no bootstrap progress is retained at {path.as_posix()}",
        )
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return ProgressRetention(
            state="unreadable",
            path=path,
            progress=None,
            detail=f"the retained progress at {path.as_posix()} could not be read: {error}",
        )
    if not isinstance(record, dict) or record.get("schema") != BOOTSTRAP_PROGRESS_SCHEMA:
        return ProgressRetention(
            state="unreadable",
            path=path,
            progress=None,
            detail=(
                f"the file at {path.as_posix()} is not a {BOOTSTRAP_PROGRESS_SCHEMA} record, so "
                "nothing about this operation is retained in it"
            ),
        )
    recorded_destination = _recorded_text(record.get("destinationPath"))
    recorded_scope = _recorded_text(record.get("scope"))
    if recorded_scope != scope or Path(recorded_destination) != Path(destination_path):
        return ProgressRetention(
            state="moved",
            path=path,
            progress=None,
            detail=(
                f"the retained progress at {path.as_posix()} describes scope {recorded_scope!r} at "
                f"{recorded_destination}, not scope {scope!r} at {destination_path.as_posix()}"
            ),
        )
    parsed = _progress_from_record(record)
    if parsed is None:
        return ProgressRetention(
            state="unreadable",
            path=path,
            progress=None,
            detail=(
                f"the file at {path.as_posix()} carries the {BOOTSTRAP_PROGRESS_SCHEMA} schema but "
                "its per-entry rows are not readable as entries, so the work it owes cannot be "
                "carried forward and it is reported as unreadable rather than as absent"
            ),
        )
    return ProgressRetention(
        state="retained",
        path=path,
        progress=parsed,
        detail=(
            f"progress for scope {scope!r} is retained at {path.as_posix()}, recorded at "
            f"{parsed.observed_at}; this run re-derives every entry's state from the store rather "
            "than trusting the retained copy"
        ),
    )


def _progress_from_record(record: Mapping[str, Any]) -> BootstrapProgress | None:
    """One retained record read back as the value it was written from, or ``None`` if unreadable.

    Only the fields a *later run* needs are reconstructed, and each entry row is validated against the
    shipped vocabulary: a ``storeState`` this code does not know is not read as "not owed" -- it
    becomes ``unmeasured``, because the honest reading of a state the reader cannot interpret is that
    absence was not established.
    """

    entries: list[EntryProgress] = []
    raw_entries = record.get("entries")
    if not isinstance(raw_entries, list):
        return None
    for raw in raw_entries:
        if not isinstance(raw, dict):
            return None
        entry_id = raw.get("entryId")
        if not isinstance(entry_id, str) or not entry_id:
            return None
        state = raw.get("storeState")
        entries.append(
            EntryProgress(
                entry_id=entry_id,
                outcome=_text(raw.get("outcome")),
                detail=_text(raw.get("detail")),
                allocated_revision_id=(
                    raw.get("allocatedRevisionId")
                    if isinstance(raw.get("allocatedRevisionId"), str)
                    else None
                ),
                store_state=(state if state in _ENTRY_STORE_STATES else "unmeasured"),
                store_detail=_text(raw.get("storeDetail")),
            )
        )
    return BootstrapProgress(
        schema=BOOTSTRAP_PROGRESS_SCHEMA,
        scope=_text(record.get("scope")),
        repo_id=_text(record.get("repositoryId")),
        authority_source=_text(record.get("authoritySource")),
        admission_source=_text(record.get("admissionSource")),
        destination_path=Path(_text(record.get("destinationPath"))),
        destination_state=_text(record.get("destinationState")),
        destination_identity=None,
        destination_read_back=_text(record.get("destinationReadBack")),
        destination_detail=_text(record.get("destinationDetail")),
        run_mode=_text(record.get("runMode")),
        batch_state=_text(record.get("batchState")),
        publication_state=_text(record.get("publicationState")),
        entries=tuple(entries),
        remaining=tuple(_text_list(record.get("remaining"))),
        unmeasured=tuple(_text_list(record.get("unmeasured"))),
        carried=tuple(_text_list(record.get("carried"))),
        remaining_basis=_text(record.get("remainingBasis")),
        observed_at=_text(record.get("observedAt")),
    )


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [one for one in value if isinstance(one, str) and one]


def write_progress(staging_root: Path, progress: BootstrapProgress) -> Path:
    """Write the record, refusing to overwrite another operation's retained progress.

    The write is atomic -- one private temporary file, then a replace -- so an interruption during it
    leaves either the previous record or this one and never a half-written file a later resume would
    read as a truncated manifest. ``BootstrapProgressConflict`` is raised, not returned: a caller
    that reached here with the wrong staging root has a defect, and a refusal value would let it
    continue and publish a manifest over another operation's work.
    """

    path = progress_path(staging_root)
    retained = read_progress(
        staging_root, scope=progress.scope, destination_path=progress.destination_path
    )
    if retained.state == "moved":
        raise BootstrapProgressConflict(retained.detail)
    staging_root.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f"{path.name}.writing")
    staged.write_text(
        json.dumps(progress.as_record(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    staged.replace(path)
    return path


def discard_bootstrap_staging(staging_root: Path, context: CoordinationContext) -> StagingCleanup:
    """Remove one bootstrap's staging, and only when nothing unpublised would be lost.

    The guard is the whole function, and it is made of two **reads** rather than an inference. A
    staging root holds a working candidate whose committed rows may not have reached the repository's
    published dataset, and a cleanup that removed it on the strength of "the run finished" is exactly
    how an authored foundation is lost -- the packet's own non-conforming example. So the staged
    candidate file is read, the location the ordinary read route selects is read through that route's
    own owner, and the staging is removed only when one of two measured facts holds:

    * the published location holds exactly the staged dataset, so the work is already in the
      repository; or
    * the staged candidate holds **no** invariant revision and its walk completed. The batch is
      all-or-nothing, so a candidate with no authored revision holds no committed authored row, and
      the only thing removal discards is an empty draft. That second branch is what keeps the cleanup
      owner reachable for a bootstrap that never got as far as publishing, and it is a measurement of
      zero rather than an assumption of emptiness.

    Every other state -- no candidate at all, a candidate that is not a readable dataset, a
    destination holding nothing, a destination holding a different dataset while the candidate holds
    authored rows, or a contents walk that did not complete -- refuses by name and leaves the bytes
    exactly as they are.
    """

    root = Path(staging_root)
    if not root.exists():
        return StagingCleanup(
            state="absent",
            staging_root=root,
            code="staging_absent",
            detail=f"no bootstrap staging exists at {root.as_posix()}, so nothing was removed",
        )
    candidate = candidate_database_path(staged_candidate_directory(root))
    staged = read_dataset_identity(candidate)
    if isinstance(staged, str):
        return _cleanup_refusal(
            root,
            "staged_candidate_unreadable",
            (
                f"the staged candidate's dataset could not be read ({staged}), so whether anything "
                "unpublished is in this staging root is unknown and it is left exactly as it is"
            ),
        )
    published = resolve_published_intent(context)
    if _destination_holds(published, staged):
        shutil.rmtree(root)
        return _discarded(
            root,
            f"the staged candidate {staged.logical_digest} is the dataset the published location "
            "holds",
        )
    contents = dataset_revisions(candidate, staged.repository_id)
    if contents.measured_empty:
        shutil.rmtree(root)
        return _discarded(
            root,
            (
                f"the staged candidate {staged.logical_digest} holds no invariant revision at all "
                f"({contents.detail}), so there is no authored work to lose"
            ),
        )
    return _cleanup_refusal(root, *_unpublished(root, staged, published, contents))


def _destination_holds(
    published: PublishedIntentSelection | PublishedIntentUnavailable, staged: SnapshotIdentity
) -> bool:
    """Whether the ordinary read route's own read found exactly the staged dataset at the location."""

    return (
        not isinstance(published, PublishedIntentUnavailable)
        and published.logical_digest == staged.logical_digest
    )


def _unpublished(
    root: Path,
    staged: SnapshotIdentity,
    published: PublishedIntentSelection | PublishedIntentUnavailable,
    contents: DatasetContents,
) -> tuple[str, str]:
    """The code and detail of a refused cleanup: which way the staging still holds work nobody has."""

    if isinstance(published, PublishedIntentUnavailable):
        return (
            "destination_does_not_hold_the_staged_dataset",
            (
                f"the published destination reports {published.state} ({published.code}: "
                f"{published.detail}), so the staged dataset {staged.logical_digest} is not "
                "confirmably in the repository and removing "
                f"{root.as_posix()} would discard work no reader can select"
            ),
        )
    return (
        "destination_holds_another_dataset",
        (
            f"the published destination holds {published.logical_digest} while the staged candidate "
            f"holds {staged.logical_digest} and {len(contents.revisions)} authored invariant "
            f"revision(s) of its own ({contents.state}: {contents.detail}), so removing "
            f"{root.as_posix()} could discard unpublised authored work"
        ),
    )


def _discarded(root: Path, reason: str) -> StagingCleanup:
    return StagingCleanup(
        state="discarded",
        staging_root=root,
        code="staging_discarded",
        detail=f"nothing unpublised was in {root.as_posix()}: {reason}",
    )


def _cleanup_refusal(root: Path, code: str, detail: str) -> StagingCleanup:
    return StagingCleanup(state="refused", staging_root=root, code=code, detail=detail)
