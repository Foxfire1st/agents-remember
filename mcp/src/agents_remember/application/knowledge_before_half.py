"""The review's before half: the dataset it holds, the generation it records, and how to read both.

A comparison is *between* two datasets, so whatever a review is opened on must have a before side.
That side is one directory in the leaf's disposable knowledge root holding exactly two files: the
dataset the comparison opens, and the origin record that says which generation the dataset is. This
module owns that layout, the reads that decide what the half currently *is*, and the record of its
provenance. Establishing a first generation is the separate act in
:mod:`agents_remember.application.knowledge_first_generation`, which is the caller of the readers
here.

Three facts are worth telling apart, because a writer that confuses them writes the wrong thing:

* **Absent is not empty.** A half with no dataset is a pair that cannot be compared; it is not a
  measured-empty history, and it must never be answered with a freshly created dataset unless the
  caller's own act says that this repository's knowledge begins here.
* **Identified is not unidentified.** A dataset with no recorded origin is a fork point some
  ``--baseline`` run placed; a dataset with a matching record is an explicitly identified first
  generation. A later run keeps the first and never restates the second.
* **Damaged is neither.** Bytes that cannot be read as a dataset of this code, a record that cannot
  be read at all, or a record whose identity no longer matches the bytes beside it: each is a state
  that has to be *named* with its reason, and left exactly as it is, rather than reported as an
  identified generation the half no longer holds.

Every read here answers with a reason instead of raising, because a file that is not a dataset is an
*input* fact in all three of the callers -- the ingest admission reading a selected fork point, the
ingest CLI inspecting the half it is about to fill, and the review refusing a comparison whose side
cannot be opened -- and each of them states that fact in its own voice.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import apsw
from pydantic import Field, ValidationError

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.canonical_json import canonical_json_bytes, decoded_json
from agents_remember.memory.knowledge.logical import dataset_identity
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
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.snapshot import CANDIDATE_DATABASE_NAME

__all__ = [
    "BASELINE_ORIGIN_NAME",
    "BaselineOrigin",
    "BeforeHalf",
    "baseline_database_path",
    "baseline_origin_path",
    "damaged_before_half_reason",
    "read_baseline_origin",
    "read_before_half",
    "read_captured_dataset_identity",
    "read_dataset_identity",
    "unreadable_half_refusal",
    "write_baseline_origin",
]

# The one file name the origin record occupies inside a before half. It sits *beside* the dataset
# rather than inside it, and it is not knowledge: the comparison reads the dataset, and "there was
# no before-generation and here is why" is a fact about the run, not a recorded invariant. Keeping
# it out of the database is what keeps it out of every read the knowledge plane answers.
BASELINE_ORIGIN_NAME = "baseline-origin.json"
ORIGIN_VERSION: Literal["ar-knowledge-baseline-origin/v1"] = "ar-knowledge-baseline-origin/v1"
FIRST_GENERATION: Literal["first-generation"] = "first-generation"
# The one value pre-feature history has here, spelled as the state it is: code that predates any
# recorded intent has no intent history that was measured, so nothing about it is recorded.
NOT_RECORDED: Literal["not-recorded"] = "not-recorded"

BeforeHalfState = Literal["absent", "identified", "unidentified", "damaged"]


class BaselineOrigin(KnowledgeModel):
    """The recorded origin of one before half: an explicitly identified empty first generation.

    Every field is a fact about *this act* rather than a second copy of anything the dataset says
    for itself. ``repository_id``, ``schema_version`` and ``logical_digest`` are read back from the
    dataset that was created, so the record cannot claim an identity the bytes do not hold; the
    rest name the run, the fork point it was not given, the code base it observed and the one state
    this record exists to distinguish -- an empty first generation, as against a measured-empty
    history or an unknown.
    """

    origin_version: Literal["ar-knowledge-baseline-origin/v1"] = ORIGIN_VERSION
    state: Literal["first-generation"] = FIRST_GENERATION
    # What this run was handed: no fork point. It is recorded because the two facts are different --
    # "this run selected no prior dataset" is what the run observed, while "the repository never
    # published one" is a claim about the repository that no caller input here can establish, and a
    # half that asserted the second from the first would be inventing a fact about other tasks.
    selected_baseline: Literal["none"] = "none"
    repository_id: str = Field(pattern=UUID_PATTERN)
    authority_home: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    schema_version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    logical_digest: str = Field(pattern=SHA256_PATTERN)
    pre_feature_history: Literal["not-recorded"] = NOT_RECORDED
    # The code base the establishing run observed, recorded as an observation and not as a source
    # endpoint: the review's source side is resolved by the review's own resolution, and this field
    # exists so the record shows an existing code base standing beside an intent history that was
    # never recorded for it.
    code_base_commit: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    recorded_at: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    contract_path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    authorization_ref: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)


@dataclass(frozen=True)
class BeforeHalf:
    """The state of the review's before half, read from the half rather than assumed.

    The four states are the four answers a caller must tell apart *before it writes anything*: there
    is no side yet (``absent``); the side is the identified first generation this module records
    (``identified``); the side is a dataset with no recorded generation, which is the fork point a
    ``--baseline`` run places (``unidentified``); or the half holds bytes that are not the side they
    claim to be (``damaged``), which is named and left exactly as it is.
    """

    state: BeforeHalfState
    database: Path
    detail: str
    origin: BaselineOrigin | None = None


@dataclass(frozen=True)
class _HalfReading:
    """One before side read: the identity it holds, the origin record beside it, or the damage."""

    identity: SnapshotIdentity | None = None
    origin: BaselineOrigin | None = None
    damage: str | None = None


# -- the half's own layout ---------------------------------------------------------------------


def baseline_database_path(baseline_directory: Path) -> Path:
    """The dataset path inside one before half.

    The half holds its dataset under the name the review resolves -- one local layout fixed by
    :mod:`agents_remember.models.knowledge.snapshot` -- so the dataset a run creates is the dataset
    the comparison opens, by name rather than by two conventions agreeing.
    """

    return Path(baseline_directory) / CANDIDATE_DATABASE_NAME


def baseline_origin_path(baseline_directory: Path) -> Path:
    """The origin record's path inside one before half."""

    return Path(baseline_directory) / BASELINE_ORIGIN_NAME


def write_baseline_origin(baseline_directory: Path, origin: BaselineOrigin) -> None:
    """Write one origin record as canonical bytes, atomically and durably.

    Canonical rather than pretty-printed for the reason the candidate receipt gives: the same
    record always has the same file content, so a digest over the file is a digest over the facts.
    """

    atomic_write_bytes(
        baseline_origin_path(baseline_directory),
        canonical_json_bytes(origin.model_dump(mode="json")),
    )


def read_baseline_origin(baseline_directory: Path) -> BaselineOrigin | None:
    """The origin record one before half carries, or ``None`` when it carries none.

    A record that is present but does not validate is a storage error rather than ``None``: a half
    whose origin cannot be read is not an identified first generation, and answering "no record"
    would let a damaged record read as an older, unrecorded half.
    """

    path = baseline_origin_path(baseline_directory)
    if not path.is_file():
        return None
    try:
        decoded = decoded_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise KnowledgeStorageError(
            f"the baseline origin record {path} could not be read: {error}"
        ) from error
    try:
        return BaselineOrigin.model_validate(decoded)
    except ValidationError as error:
        raise KnowledgeStorageError(
            f"the baseline origin record {path} is not a valid record: {error}"
        ) from error


# -- reading a dataset, whichever path shape the caller has ------------------------------------


def read_dataset_identity(database: Path) -> SnapshotIdentity | str:
    """One dataset file's logical identity, or the reason it cannot be read as one.

    A file at a path is not a dataset until it is read as one, and every way that read fails -- gone,
    not SQLite, written by another code generation, bound to no namespace -- is a fact about an
    *input* rather than a defect of the run that was handed it. Answering with the reason instead of
    raising is what lets each caller state it in its own voice: the admission refuses a selected fork
    point by name, the before half names a damaged side, and the review refuses a comparison whose
    side cannot be opened at all.
    """

    if not database.is_file():
        return f"the dataset {database} is not a file"
    return _dataset_identity_of(database, described_by=database)


def read_captured_dataset_identity(payload: bytes, origin: Path) -> SnapshotIdentity | str:
    """The identity an already-captured baseline payload holds, or why those bytes are not a dataset.

    The bytes are what a run will place in the before half, and the path they were read from may
    already have been replaced by that same run's own publication, so the question has to be asked of
    the bytes themselves: they are written to one private file, read through the same reader every
    other side uses, and the private file is discarded. A payload that is not a dataset is refused
    here rather than copied into the half, where it would become a before side no comparison can
    open -- which is how a corrupt expected dataset would otherwise come to be reported as *placed*.
    """

    with tempfile.TemporaryDirectory(prefix="ar-before-half-") as staged:
        staged_path = Path(staged) / CANDIDATE_DATABASE_NAME
        staged_path.write_bytes(payload)
        return _dataset_identity_of(staged_path, described_by=origin)


def _dataset_identity_of(database: Path, *, described_by: Path) -> SnapshotIdentity | str:
    """One file read as a dataset, with the path a report should name kept separate from the read.

    The two differ for bytes that were captured earlier: those are read from a private staged file,
    and a reason that named that path would tell an operator about this module's temporary directory
    instead of the dataset they handed over.
    """

    try:
        return dataset_identity(database)
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        return f"the dataset {described_by} could not be read as a dataset of this code ({error})"


def read_before_half(baseline_directory: Path) -> BeforeHalf:
    """What the before half currently holds, as one of the four states a writer must tell apart.

    The dataset is *read*, not merely found: a half whose dataset is present but unreadable, or whose
    recorded origin disagrees with the bytes beside it, is a damaged half rather than an identified
    one. Telling those apart is what keeps "a first generation is already recorded here" from being
    the answer for bytes that are no longer that generation.
    """

    database = baseline_database_path(baseline_directory)
    if not database.is_file():
        return BeforeHalf(state="absent", database=database, detail=f"no dataset at {database}")
    reading = _read_half(database)
    if reading.damage is not None:
        return BeforeHalf(state="damaged", database=database, detail=reading.damage)
    if reading.origin is None:
        return BeforeHalf(
            state="unidentified",
            database=database,
            detail=f"the dataset at {database} carries no recorded generation",
        )
    return BeforeHalf(
        state="identified",
        database=database,
        detail=(
            f"the first generation recorded at {baseline_origin_path(baseline_directory)} matches "
            "the dataset beside it"
        ),
        origin=reading.origin,
    )


def damaged_before_half_reason(baseline_database: Path) -> str | None:
    """Why the dataset at this exact path is not the before side it claims to be, or ``None``.

    The path-shaped sibling of :func:`read_before_half`, for callers that name a *file* rather than
    the half's directory -- the review resolves its two sides as paths. An absent file is ``None``:
    absence is a different state with its own refusal, and it is not this reader's answer.
    """

    if not baseline_database.is_file():
        return None
    return _read_half(baseline_database).damage


def _read_half(database: Path) -> _HalfReading:
    """One before side read: the identity, the origin record beside it, or the damage."""

    reading = read_dataset_identity(database)
    if isinstance(reading, str):
        return _HalfReading(damage=reading)
    try:
        origin = read_baseline_origin(database.parent)
    except KnowledgeStorageError as error:
        return _HalfReading(identity=reading, damage=str(error))
    if origin is None:
        return _HalfReading(identity=reading)
    mismatch = _origin_mismatch(origin, reading, database=database)
    if mismatch is not None:
        return _HalfReading(identity=reading, origin=origin, damage=mismatch)
    return _HalfReading(identity=reading, origin=origin)


def _origin_mismatch(
    origin: BaselineOrigin, observed: SnapshotIdentity, *, database: Path
) -> str | None:
    """How the recorded origin disagrees with the bytes beside it, or ``None`` when they agree.

    The record states the identity of the dataset it was written for, so a half whose bytes no longer
    hold that identity is not the generation the record names -- it is a damaged half that must say
    so rather than keep reporting a generation that is no longer there. Both paths are named, because
    those are the two files a reader has to look at to see the disagreement for themselves.
    """

    for field, recorded, observed_value in (
        ("repository_id", origin.repository_id, observed.repository_id),
        ("schema_version", origin.schema_version, observed.schema_version),
        ("logical_digest", origin.logical_digest, observed.logical_digest),
    ):
        if recorded != observed_value:
            return (
                f"the recorded origin at {baseline_origin_path(database.parent)} names {field} "
                f"{recorded}, while the dataset at {database} holds {observed_value}"
            )
    return None


# -- the refusal a review answers an unreadable side with --------------------------------------


def unreadable_half_refusal(
    baseline_database: Path, candidate_database: Path
) -> ReviewRefusal | None:
    """The refusal for a resolved pair with a side that is present but cannot be read, or ``None``.

    A comparison is between two dataset *files*, so a file that is not a dataset makes SQLite raise
    from inside the read: the caller then receives a storage error where this surface promises a
    typed refusal naming the side and the action that yields a reviewable pair. Both sides are
    preflighted with the readers above -- the baseline through its recorded origin as well as its
    bytes -- so "the before side is unreadable" and "the before side is absent" stay two named states
    instead of one named state and one traceback.

    An absent side is deliberately not this function's business: absence has its own refusal already,
    and it is the more actionable answer when nothing was placed at all.

    The refusal reuses the shipped ``candidate_dataset_absent`` code, which is this vocabulary's code
    for a pair input that cannot be opened, rather than widening a shared refusal vocabulary the
    transport and the renderer both read; the *state* -- present but unreadable -- is carried in the
    detail and the offending input, which is where a caller distinguishing the two reads it.
    """

    baseline = Path(baseline_database)
    damage = damaged_before_half_reason(baseline)
    if damage is not None:
        return _unreadable_half_refusal("baseline", baseline, damage)
    candidate = Path(candidate_database)
    if candidate.is_file():
        reading = read_dataset_identity(candidate)
        if isinstance(reading, str):
            return _unreadable_half_refusal("candidate", candidate, reading)
    return None


def _unreadable_half_refusal(half: str, database: Path, reason: str) -> ReviewRefusal:
    """One refusal for a side whose bytes are there and cannot be read as a dataset."""

    return ReviewRefusal(
        code="candidate_dataset_absent",
        detail=(
            f"the resolved {half} dataset is present but cannot be read as a dataset of this code, "
            f"so the pair has nothing to compare ({reason})"
        ),
        next_action=(
            "repair or replace that side of the leaf's disposable knowledge root -- author the "
            "candidate's knowledge again, or place the dataset this leaf forks from -- then reopen "
            "the review; the surface substitutes no other dataset"
        ),
        offending_input=database.name,
    )
