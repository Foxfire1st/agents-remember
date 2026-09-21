"""Which comparison generation the review's before half holds, and how one admitted run fills it.

A comparison is *between* two datasets, so the before side is not a scratch copy of whatever the
latest run happened to be handed: it is the baseline that comparison was **opened on**. Two facts
have to stay apart for that to survive a repeated ingest, and this module owns both of them.

* **The first admitted baseline is the comparison's original one.** ``--baseline`` and
  ``--publish-to`` may name one path, so the second successful run's captured bytes are the *first*
  run's publication -- a dataset that already contains the addition. Placing those bytes is how the
  review comes to compare a dataset against itself: the addition is present on both sides and the
  delta is empty. The half is therefore filled **once**, and a later run keeps the before side it was
  first handed instead of restating it.
* **A deliberate new baseline is a new generation, with lineage.** The way out is explicit and
  recorded, never silent: :func:`rebase_comparison_baseline` publishes the replacement dataset and a
  record beside it naming the generation it began from *and* that generation's exact identity. A
  comparison is never quietly re-pointed at a different fork point behind the identity it already
  had.

The record is a *local operation fact* about the run that filled the half -- the same kind of object
the first generation's origin record and the candidate receipt are -- which is why it lives beside
its dataset in the leaf's disposable knowledge root. It is not knowledge, it decides no authority,
and it is never a second source of authored truth: it says which dataset the comparison opens on and
where that dataset came from, and nothing else.

Reading the half and its datasets is the separate concern of
:mod:`agents_remember.application.knowledge_before_half` -- the layout, the four states a half can be
in, and the first-generation origin record. Establishing a repository's *first* generation is the
separate act in :mod:`agents_remember.application.knowledge_first_generation`. This module composes
those owners for the one decision neither of them makes: whether this run may fill the half at all,
and as which generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid5

from pydantic import Field, model_validator

from agents_remember.application.knowledge_before_half import (
    BeforeHalf,
    baseline_database_path,
    baseline_origin_path,
    read_before_half,
    read_captured_dataset_identity,
    read_dataset_identity,
)
from agents_remember.application.knowledge_first_generation import (
    FirstGenerationRun,
    establish_first_generation,
)
from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.canonical_json import canonical_json_bytes, decoded_json
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity

__all__ = [
    "BASELINE_GENERATION_NAME",
    "AdmittedBaseline",
    "BaselineGeneration",
    "BaselinePlacement",
    "BaselineRun",
    "CapturedBaseline",
    "StandingGeneration",
    "baseline_generation_path",
    "fill_admitted_before_half",
    "generation_identity",
    "place_original_baseline",
    "read_admitted_baseline",
    "read_baseline_generation",
    "read_standing_generation",
    "rebase_comparison_baseline",
    "write_baseline_generation",
]

# The one file name the generation record occupies inside a before half. It sits *beside* the dataset
# for the reason the first-generation origin record does: the comparison reads the dataset, and
# "which fork point this comparison was opened on" is a fact about the run, not a recorded invariant.
BASELINE_GENERATION_NAME = "baseline-generation.json"
GENERATION_VERSION: Literal["ar-knowledge-baseline-generation/v1"] = (
    "ar-knowledge-baseline-generation/v1"
)
# The one state this record exists to distinguish from the first generation's: the dataset here is a
# baseline the run was *handed*, not an empty history the repository began from.
SELECTED_BASELINE: Literal["selected-baseline"] = "selected-baseline"

# The namespace every comparison generation id is derived under. A literal and not a repository
# value, because the id names one *generation* -- a local fact about one leaf's before half -- rather
# than anything the knowledge plane stores.
_GENERATION_NAMESPACE = UUID("3c9e5b12-7d4a-5f60-8e21-b6c47a0d9f38")
# The lineage placeholder for a generation that began from nothing. Spelled rather than left empty so
# a derived id is a function of facts that are all visible in the record beside it.
_NO_PARENT = "no-parent"


class BaselineGeneration(KnowledgeModel):
    """The recorded generation of one before half: the admitted baseline this comparison opens on.

    Every identity field is read back from the dataset that was actually published, so the record
    cannot claim an identity the bytes do not hold -- the same discipline the first-generation origin
    record follows. ``selected_baseline`` names the path the bytes were captured from *before* the run
    could publish over it, which is the provenance a reader needs to see that the dataset this
    comparison opens on is no longer the one at that path.
    """

    generation_version: Literal["ar-knowledge-baseline-generation/v1"] = GENERATION_VERSION
    state: Literal["selected-baseline"] = SELECTED_BASELINE
    generation_id: str = Field(pattern=UUID_PATTERN)
    # 1 is the comparison's original baseline; every later value is a deliberate rebase. The index is
    # recorded rather than counted, because "how many times has this comparison been re-pointed" is a
    # fact the reader must be able to see without trusting a directory to still hold its history.
    generation_index: int = Field(ge=1)
    selected_baseline: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    repository_id: str = Field(pattern=UUID_PATTERN)
    schema_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    logical_digest: str = Field(pattern=SHA256_PATTERN)
    # The lineage. A first generation of this comparison has neither; a rebase has both, and the
    # identity is what a reader needs to say which dataset was replaced rather than only which
    # generation number preceded this one.
    parent_generation_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    parent_identity: SnapshotIdentity | None = None
    code_base_commit: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    recorded_at: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    contract_path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    authorization_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)

    @model_validator(mode="after")
    def _lineage_agrees_with_the_index(self) -> BaselineGeneration:
        """Refuse a record whose lineage and generation number contradict each other.

        A record is read by a verifier that was not present at the write, so its own fields have to
        agree: the first generation of a comparison began from nothing and therefore carries no
        parent, and a later one carries both halves of the parent fact or neither.
        """

        if (self.generation_index == 1) != (self.parent_generation_id is None):
            raise ValueError(
                "a comparison generation is its comparison's first exactly when it records no "
                f"parent, and this record claims index {self.generation_index} with parent "
                f"{self.parent_generation_id}"
            )
        if (self.parent_generation_id is None) != (self.parent_identity is None):
            raise ValueError(
                "a recorded lineage names both the generation it began from and that generation's "
                "dataset identity, or neither"
            )
        return self


@dataclass(frozen=True)
class BaselineRun:
    """The admitted run that fills one before half, as its record names it.

    The same four facts the first-generation record carries, and for the same reason: they are what
    the write is admitted under (the leaf, the enclosure, the authorization) plus the code base the
    run observed, so a later reader can tell which run placed this before side.
    """

    leaf_id: str
    contract_path: str
    authorization_ref: str
    code_base_commit: str | None = None


@dataclass(frozen=True)
class CapturedBaseline:
    """The admitted baseline bytes, read before the run could publish over the path they came from.

    ``--baseline`` and ``--publish-to`` may name one path, and publication replaces that file in
    place, so the bytes have to be carried rather than the path: a read of the same path afterwards
    is a read of the *after* state. ``origin`` is named in the report and recorded in the generation
    record, because "which dataset this came from" is a fact the reader cannot recover from bytes.
    """

    origin: Path
    payload: bytes


@dataclass(frozen=True)
class AdmittedBaseline:
    """An admitted baseline whose bytes have been read as a dataset of this code.

    The pair travels together because they are one fact by the time anything is written: the bytes
    are what would be published, and the identity is what those bytes *are*, read through the same
    reader every other side uses rather than re-derived from the dataset a record claims. Keeping
    them one value is what stops the write from being handed bytes whose identity nobody read.
    """

    captured: CapturedBaseline
    identity: SnapshotIdentity


StandingState = Literal["absent", "recorded", "adopted", "damaged"]
PlacementState = Literal["placed", "present", "not-placed"]


@dataclass(frozen=True)
class StandingGeneration:
    """The comparison baseline the before half currently holds, as one of four states.

    Three of them are facts a writer must tell apart before it writes anything, and the fourth says
    the half cannot be trusted to name its own before side. ``adopted`` is the one that is easy to
    get wrong: a dataset with no generation record beside it is still the before side this comparison
    was opened on -- it was placed by a run from before this record existed -- so it is *adopted* as
    the original baseline rather than treated as an empty slot a later run may fill.
    """

    state: StandingState
    database: Path
    detail: str
    # The dataset identity the half holds, whenever its bytes could be read at all -- including when
    # they disagree with the record beside them. It is what lets a refusal name the bytes on disk
    # rather than only the state they are in, and it is absent only when there was nothing readable.
    identity: SnapshotIdentity | None = None
    record: BaselineGeneration | None = None

    @property
    def generation_id(self) -> str | None:
        """This half's generation id: the recorded one, or the id its own dataset facts derive.

        An adopted half has no record to read an id from, so its id is derived from the dataset that
        is actually there -- the same derivation a record would have used -- which is what lets a
        rebase from it record an exact parent instead of a placeholder.
        """

        if self.record is not None:
            return self.record.generation_id
        if self.identity is None:
            return None
        return generation_identity(
            generation_index=1, identity=self.identity, parent_generation_id=None
        )

    @property
    def generation_index(self) -> int:
        """How many generations this comparison has had, as the half itself records it."""

        return 1 if self.record is None else self.record.generation_index


@dataclass(frozen=True)
class BaselinePlacement:
    """What one run left in the before half: its state, the report's line about it, and the record."""

    state: PlacementState
    detail: str
    record: BaselineGeneration | None = None


# -- the half's own generation record -----------------------------------------------------------


def baseline_generation_path(baseline_directory: Path) -> Path:
    """The generation record's path inside one before half."""

    return Path(baseline_directory) / BASELINE_GENERATION_NAME


def write_baseline_generation(baseline_directory: Path, record: BaselineGeneration) -> None:
    """Write one generation record as canonical bytes, atomically and durably.

    Canonical rather than pretty-printed for the reason the origin record gives: the same record
    always has the same file content, so a digest over the file is a digest over the facts.
    """

    atomic_write_bytes(
        baseline_generation_path(baseline_directory),
        canonical_json_bytes(record.model_dump(mode="json")),
    )


def read_baseline_generation(baseline_directory: Path) -> BaselineGeneration | None:
    """The generation record one before half carries, or ``None`` when it carries none.

    A record that is present but does not validate is a storage error rather than ``None``: a half
    whose generation cannot be read is not a half that records no generation, and answering "no
    record" would silently adopt whatever bytes are beside it as the original baseline. For the same
    reason a record *path* that is occupied by something which is not a record file at all -- a
    directory, say, left where the record belongs -- is that same storage error and not an absent
    record: reading it as absent is how an obstruction becomes invisible and the half goes on calling
    itself an adopted baseline.
    """

    path = baseline_generation_path(baseline_directory)
    if not path.is_file():
        if path.exists() or path.is_symlink():
            raise KnowledgeStorageError(
                f"the baseline generation record path {path} is occupied by something that is not a "
                "record file, so this half's generation cannot be read"
            )
        return None
    try:
        decoded = decoded_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise KnowledgeStorageError(
            f"the baseline generation record {path} could not be read: {error}"
        ) from error
    try:
        return BaselineGeneration.model_validate(decoded)
    except ValueError as error:
        raise KnowledgeStorageError(
            f"the baseline generation record {path} is not a valid record: {error}"
        ) from error


def generation_identity(
    *,
    generation_index: int,
    identity: SnapshotIdentity,
    parent_generation_id: str | None,
) -> str:
    """The deterministic id of one generation, derived from the facts the record itself names.

    Deterministic rather than freshly minted so that a reader can *recompute* it: an id that does not
    belong to the generation beside it is then a fact a verifier falsifies instead of a value it has
    to take on trust. The dataset identity and the parent are the whole of what distinguishes one
    generation of a comparison from another, and both are recorded, so nothing enters this
    derivation that a reader of the record cannot see.
    """

    return str(
        uuid5(
            _GENERATION_NAMESPACE,
            ":".join(
                (
                    GENERATION_VERSION,
                    str(generation_index),
                    identity.repository_id,
                    identity.schema_version,
                    identity.logical_digest,
                    parent_generation_id or _NO_PARENT,
                )
            ),
        )
    )


# -- reading what the half currently holds ------------------------------------------------------


def read_standing_generation(baseline_directory: Path) -> StandingGeneration:
    """Which baseline the before half holds, as one of the four states its writer must tell apart.

    The generation record is read first, because it is the half's own statement of what it holds: a
    record that cannot be read is damage even when the bytes beside it are a perfectly good dataset,
    and answering "adopted" there would adopt whatever is on disk as the original baseline.
    """

    half = Path(baseline_directory)
    database = baseline_database_path(half)
    try:
        record = read_baseline_generation(half)
    except KnowledgeStorageError as error:
        return StandingGeneration(state="damaged", database=database, detail=str(error))
    if not database.is_file():
        return _standing_without_a_dataset(half, database, record)
    reading = read_dataset_identity(database)
    if isinstance(reading, str):
        return StandingGeneration(state="damaged", database=database, detail=reading, record=record)
    return _standing_with_a_dataset(half, database, record, reading)


def _standing_with_a_dataset(
    half: Path,
    database: Path,
    record: BaselineGeneration | None,
    reading: SnapshotIdentity,
) -> StandingGeneration:
    """One readable dataset: this comparison's recorded generation, or the one it was adopted as."""

    if record is None:
        return StandingGeneration(
            state="adopted",
            database=database,
            identity=reading,
            detail=(
                f"the dataset at {database} carries no recorded generation, so it is the baseline "
                "this comparison already opened on"
            ),
        )
    mismatch = _record_mismatch(record, reading, database=database)
    if mismatch is not None:
        # The dataset WAS read -- that is how the disagreement was found -- so the identity travels
        # with the damage: a reader told "damaged" still has to be able to name the bytes that are
        # actually on disk beside the record that disagrees with them.
        return StandingGeneration(
            state="damaged",
            database=database,
            identity=reading,
            detail=mismatch,
            record=record,
        )
    return StandingGeneration(
        state="recorded",
        database=database,
        identity=reading,
        record=record,
        detail=(
            f"generation {record.generation_id} (index {record.generation_index}) recorded at "
            f"{baseline_generation_path(half)} matches the dataset beside it"
        ),
    )


def _standing_without_a_dataset(
    half: Path, database: Path, record: BaselineGeneration | None
) -> StandingGeneration:
    """One half with no dataset: the comparison's start, or a record that lost the side it names."""

    if record is None:
        return StandingGeneration(
            state="absent", database=database, detail=f"no dataset at {database}"
        )
    return StandingGeneration(
        state="damaged",
        database=database,
        record=record,
        detail=(
            f"the generation record at {baseline_generation_path(half)} names generation "
            f"{record.generation_id}, while no dataset is at {database}"
        ),
    )


def _record_mismatch(
    record: BaselineGeneration, observed: SnapshotIdentity, *, database: Path
) -> str | None:
    """How the recorded generation disagrees with the bytes beside it, or ``None`` when they agree.

    Both paths are named, because those are the two files a reader has to look at to see the
    disagreement for themselves, and the check covers the whole identity rather than the dataset
    digest alone: a namespace or schema change is the same kind of fact.
    """

    for field, recorded, observed_value in (
        ("repository_id", record.repository_id, observed.repository_id),
        ("schema_version", record.schema_version, observed.schema_version),
        ("logical_digest", record.logical_digest, observed.logical_digest),
    ):
        if recorded != observed_value:
            return (
                f"the recorded generation at {baseline_generation_path(database.parent)} names "
                f"{field} {recorded}, while the dataset at {database} holds {observed_value}"
            )
    return None


# -- the one decision: whether this run may fill the half, and as which generation ----------------


def fill_admitted_before_half(
    *,
    half: Path,
    candidate_directory: Path,
    captured: CapturedBaseline | None,
    run: BaselineRun,
    rebase: bool,
) -> str:
    """Fill the review's before half for one admitted run, or say exactly why nothing was placed.

    The caller owns *whether* this happens -- the run that committed the candidate, and only that run
    -- and this function owns *what the half is afterwards*. The two arguments the run may bring are
    answered by two owners, and the order matters:

    * **no baseline was named at all**: the repository's first generation is the act in
      :mod:`agents_remember.application.knowledge_first_generation`, which is handed the whole
      decision -- a half it already finds keeps its own four-state answer, including the
      ``not-established``/``present`` wording of a half that cannot be established here;
    * **a baseline was named**: the half's *generation* decides, and each state answers with the
      reason it placed nothing rather than with a placement -- damaged is named and left exactly as
      it is; the identified first generation this leaf began from is kept whatever the run named; a
      standing baseline is kept unless the caller explicitly asked for a rebase.

    ``rebase`` is the caller's deliberate word, and it is the only input that may replace a standing
    baseline. It never overrides the first generation's rule and it never repairs a damaged half:
    both of those are named states with their own owners, and a flag that silently overrode them
    would be a second, quieter way to rewrite what a comparison is *of*.
    """

    if captured is None:
        return _establish_first_generation(Path(half), Path(candidate_directory), run)
    standing_half = read_before_half(Path(half))
    if standing_half.state == "damaged":
        return _not_placed(standing_half.detail)
    if standing_half.state == "identified":
        return _identified_first_generation(standing_half, rebase=rebase)
    return _place_admitted_baseline(Path(half), captured, run=run, rebase=rebase)


def _establish_first_generation(half: Path, candidate_directory: Path, run: BaselineRun) -> str:
    """Establish the identified empty first generation, through the owner that creates it."""

    outcome = establish_first_generation(
        baseline_directory=half,
        candidate_directory=candidate_directory,
        run=FirstGenerationRun(
            leaf_id=run.leaf_id,
            contract_path=run.contract_path,
            authorization_ref=run.authorization_ref,
            code_base_commit=run.code_base_commit,
        ),
    )
    return outcome.detail


def read_admitted_baseline(captured: CapturedBaseline) -> AdmittedBaseline | str:
    """The identity one admitted baseline's bytes hold, or the reason they are not a dataset.

    The bytes are read as a dataset *before* anything is written, because placing bytes that are not
    a dataset hands the review a before side no comparison can open -- a corrupt expected dataset
    reported as *placed*. The read is of the bytes rather than of the path they came from: by the
    time a run reaches this, that path may already hold this same run's publication.
    """

    reading = read_captured_dataset_identity(captured.payload, captured.origin)
    if isinstance(reading, str):
        return reading
    return AdmittedBaseline(captured=captured, identity=reading)


def _place_admitted_baseline(
    half: Path, captured: CapturedBaseline, *, run: BaselineRun, rebase: bool
) -> str:
    """Place an admitted baseline as the original generation, keep the standing one, or rebase.

    The answer is one of three, and which one it is depends on the half this run found: the half
    already holds exactly this dataset, so nothing is restated; the half holds a different baseline
    and this is not a rebase, so the original is kept and the rebase action is named; or this is the
    comparison's first placement, or a deliberate rebase, and a generation is written.
    """

    admitted = read_admitted_baseline(captured)
    if isinstance(admitted, str):
        return f"not-placed: {admitted}, so nothing was placed in the before half"
    return _place_or_keep(half, admitted, read_standing_generation(half), run=run, rebase=rebase)


def _place_or_keep(
    half: Path,
    admitted: AdmittedBaseline,
    standing: StandingGeneration,
    *,
    run: BaselineRun,
    rebase: bool,
) -> str:
    """Keep the standing baseline, or write a generation, now that the bytes read as a dataset.

    The order of the four answers is the contract: a damaged half is named before anything else; the
    half's own dataset being the one this run was handed is a no-op rather than a placement (so a
    retry restates nothing); an empty half gets the comparison's first generation; and a standing
    baseline that differs is kept unless the caller's deliberate ``--rebase-baseline`` word says
    otherwise.
    """

    if standing.state == "damaged":
        return _not_placed(standing.detail)
    if standing.identity == admitted.identity:
        return _present(standing, admitted.captured)
    if standing.state == "absent":
        return place_original_baseline(half, admitted, run=run).detail
    if not rebase:
        return _conflicting_baseline(standing, admitted.captured)
    return rebase_comparison_baseline(half, admitted, standing=standing, run=run).detail


def place_original_baseline(
    half: Path,
    admitted: AdmittedBaseline,
    *,
    run: BaselineRun,
) -> BaselinePlacement:
    """Fill an empty before half with generation 1 of this comparison, and record where it came from.

    This is the one placement that is not a rebase: there is no earlier baseline to name as a parent
    because the half held none, and that is also why the dataset lands **first** here. A half that
    ends up holding these bytes without the record that names them reads as an *adopted* baseline --
    "the dataset this comparison already opened on" -- which is exactly what it is when there was
    nothing there before: the original baseline, present, with no generation claimed for it. The
    opposite order would leave a record naming bytes that are not on disk, which is the louder but
    unnecessary damage of claiming a generation the half does not hold.
    """

    record = _generation_record(
        admitted,
        run,
        generation_index=1,
        parent_generation_id=None,
        parent_identity=None,
    )
    return _publish_generation(
        half,
        admitted.captured,
        record,
        record_first=False,
        detail=(
            f"placed: {baseline_database_path(half)} (generation {record.generation_id}, the first "
            f"of this comparison, captured from {admitted.captured.origin} before this run)"
        ),
    )


def rebase_comparison_baseline(
    half: Path,
    admitted: AdmittedBaseline,
    *,
    standing: StandingGeneration,
    run: BaselineRun,
) -> BaselinePlacement:
    """Begin an explicitly new comparison generation from this baseline, recording its lineage.

    A rebase is the deliberate answer to a baseline the comparison no longer forks from, and its
    whole difference from the silent replacement it replaces is the record it writes: the generation
    it began from is named by id and by dataset identity, and the new generation's own id is derived
    from that lineage. The dataset it replaces is therefore *superseded with a receipt*, not
    overwritten behind an identity that keeps claiming to be the original one.

    The receipt is durable **before** the bytes it names, because this act replaces a baseline that is
    already on disk. Landing the replacement first would mean that a record that then failed to write
    left a half holding a dataset no record describes -- and for a half whose original was never
    recorded, that state reads *adopted*, which would relabel the replacement as the comparison's
    original baseline and lose the one the comparison was actually opened on. With the record first,
    the one window that remains is the previous bytes on disk beside a record that disagrees with
    them, which is the named damage a reader can act on.
    """

    record = _generation_record(
        admitted,
        run,
        generation_index=standing.generation_index + 1,
        parent_generation_id=standing.generation_id,
        parent_identity=standing.identity,
    )
    return _publish_generation(
        half,
        admitted.captured,
        record,
        record_first=True,
        detail=(
            f"placed: {baseline_database_path(half)} (generation {record.generation_id}, rebased "
            f"from generation {record.parent_generation_id} "
            f"({_identity_text(standing.identity)}); captured from {admitted.captured.origin} "
            "before this run)"
        ),
    )


def _publish_generation(
    half: Path,
    captured: CapturedBaseline,
    record: BaselineGeneration,
    *,
    record_first: bool,
    detail: str,
) -> BaselinePlacement:
    """Publish one generation's two legs in the order the half requires, or say which leg refused.

    The two legs are the dataset and the record that names it, and their order is the failure contract
    rather than an implementation detail:

    * ``record_first`` -- a **rebase**, where bytes are being *replaced*. The record must be durable
      before the replacement lands, so the window that can remain is "previous bytes, record disagrees"
      (``damaged``), never "replacement bytes, no record" (which would read ``adopted`` and mislabel
      the replacement as the original).
    * dataset first -- the **first placement** into an empty half, where no earlier baseline exists to
      lose: a dataset that lands without its record is an adopted baseline, which is exactly what it
      is, namely the one this comparison opened on.

    A failure names the leg that failed and then reads the half back, because the two legs land
    separately: "which leg refused" and "what the half holds now" are two facts, and the second is
    read from the half rather than inferred from the leg that raised.
    """

    if record_first:
        refusal = _publish_record_leg(half, record)
        if refusal is None:
            refusal = _publish_dataset_leg(half, captured)
    else:
        refusal = _publish_dataset_leg(half, captured)
        if refusal is None:
            refusal = _publish_record_leg(half, record)
    if refusal is not None:
        return _failed_placement(half, refusal)
    return BaselinePlacement(state="placed", detail=detail, record=record)


def _publish_dataset_leg(half: Path, captured: CapturedBaseline) -> str | None:
    """Put the admitted bytes at the half's dataset path, or say the call did not report success.

    The write owner publishes the bytes and *then* flushes the directory that names them, so a failure
    it raises can arrive after the rename has already put them on disk. "Was not replaced" would
    therefore be a claim this caller never measured, and the report's own appended read-back is where
    that fact is measured; the leg says only what it knows -- the call did not return success.
    """

    try:
        atomic_write_bytes(baseline_database_path(half), captured.payload)
    except OSError as error:
        return f"the dataset leg did not report success at {baseline_database_path(half)} ({error})"
    return None


def _publish_record_leg(half: Path, record: BaselineGeneration) -> str | None:
    """Write the generation record and read it back, or say the call did not report success.

    The read-back is what makes "the record is on disk" a fact about the file rather than about the
    write call having returned, and it is the leg that must hold before any rebased bytes land. The
    refusal is worded for that same reason: the write owner's directory flush can fail *after* the
    record already landed, so "was not written" is not a claim this caller can make -- only that the
    call did not report success, with the report's appended read-back naming what the half holds.
    """

    try:
        write_baseline_generation(half, record)
        written = read_baseline_generation(half)
    except (KnowledgeStorageError, OSError) as error:
        return (
            "the generation record leg did not report success at "
            f"{baseline_generation_path(half)} ({error})"
        )
    if written != record:
        return (
            "the generation record written at "
            f"{baseline_generation_path(half)} did not read back unchanged"
        )
    return None


def _failed_placement(half: Path, refusal: str) -> BaselinePlacement:
    """One publication's refusal, with the state the half is actually in read back and named.

    A line that only says the publication failed is a message rather than a result: a reader has to
    know which leg refused *and* what the half holds afterwards, because "nothing was claimed" is not
    the same fact as "nothing changed" -- and because a leg can fail *after* its bytes landed (the
    write owner flushes the directory after the rename), the outcome is only ever stated by this
    read-back. The dataset identity is named with the state so the line answers the question the leg
    clause deliberately does not: which bytes are on disk.
    """

    standing = read_standing_generation(half)
    holding = "" if standing.identity is None else f", holding {_identity_text(standing.identity)}"
    return BaselinePlacement(
        state="not-placed",
        detail=(
            f"not-placed: {refusal}; the half now reads {standing.state} "
            f"({standing.detail}{holding}), so this run claims no generation"
        ),
    )


def _generation_record(
    admitted: AdmittedBaseline,
    run: BaselineRun,
    *,
    generation_index: int,
    parent_generation_id: str | None,
    parent_identity: SnapshotIdentity | None,
) -> BaselineGeneration:
    """One generation record built from the dataset that was actually published and the run's facts."""

    identity = admitted.identity
    return BaselineGeneration(
        generation_id=generation_identity(
            generation_index=generation_index,
            identity=identity,
            parent_generation_id=parent_generation_id,
        ),
        generation_index=generation_index,
        selected_baseline=str(admitted.captured.origin),
        repository_id=identity.repository_id,
        schema_version=identity.schema_version,
        logical_digest=identity.logical_digest,
        parent_generation_id=parent_generation_id,
        parent_identity=parent_identity,
        code_base_commit=run.code_base_commit,
        recorded_at=datetime.now(UTC).isoformat(),
        leaf_id=run.leaf_id,
        contract_path=run.contract_path,
        authorization_ref=run.authorization_ref,
    )


# -- the report's lines -------------------------------------------------------------------------


def _not_placed(detail: str) -> str:
    """The line for a half this run may not write to, with the state it is in named.

    It takes the state's own sentence rather than the object that carries it, because both readers
    of a half produce one -- the generation reader and the before-half layout reader -- and the
    report says the same thing about either.
    """

    return f"not-placed: {detail}, and this run left the half exactly as it is"


def _present(standing: StandingGeneration, captured: CapturedBaseline) -> str:
    """The line for a run whose admitted baseline is the dataset the half already holds."""

    return (
        f"present: {standing.database} (generation {standing.generation_id} already holds "
        f"{_identity_text(standing.identity)} captured from {captured.origin}; nothing was restated)"
    )


def _conflicting_baseline(standing: StandingGeneration, captured: CapturedBaseline) -> str:
    """The line for a baseline that differs from the one this comparison was opened on.

    It has to carry three facts, because a caller that only learns "not placed" cannot tell a
    refusal from a no-op: which generation the half holds and what its dataset identity is, that the
    standing baseline is *this comparison's original* rather than a stale artifact, and the exact
    action that begins a new generation deliberately. The state clause is the fourth: a half that
    carries no recorded generation is kept for the same reason a recorded one is, and a reader has to
    be able to see which of the two it is looking at.
    """

    state = (
        "which carries no recorded generation"
        if standing.state == "adopted"
        else f"recorded at {baseline_generation_path(standing.database.parent)}"
    )
    return (
        f"not-placed: the before half at {standing.database} already holds this comparison's "
        f"original baseline -- generation {standing.generation_id} "
        f"({_identity_text(standing.identity)}; {state}) -- and a later run does not replace it; "
        f"this run left it exactly as it is. A deliberate new baseline is an explicit rebase: "
        f"re-run with --rebase-baseline to begin a new comparison generation from {captured.origin}"
    )


def _identified_first_generation(standing_half: BeforeHalf, *, rebase: bool) -> str:
    """The line for a half that records the identified first generation this leaf began from."""

    override = (
        "; --rebase-baseline begins a new generation from a *selected* baseline and does not "
        "replace the generation this comparison began from"
        if rebase
        else ""
    )
    return (
        f"not-placed: the before half at {standing_half.database} already records its first "
        f"generation at {baseline_origin_path(standing_half.database.parent)}; a selected "
        f"baseline does not replace the before side this leaf began from{override}"
    )


def _identity_text(identity: SnapshotIdentity | None) -> str:
    """One dataset identity as a report line names it, or the absence this half records."""

    if identity is None:
        return "no recorded dataset identity"
    return f"{identity.repository_id}/{identity.schema_version}/{identity.logical_digest}"
