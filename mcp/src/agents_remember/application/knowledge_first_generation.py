"""Establishing a repository's first knowledge generation as an identified, empty before side.

A comparison is *between* two datasets, so the first knowledge a repository ever records has to be
reviewed against a before side. The ingest CLI places that side from the dataset the task forks from
(``--baseline``); this module owns the other, equally explicit case: a repository's **first
generation**, where there is no earlier dataset to fork from and the truthful before side is an empty
one that says so.

Three properties are load-bearing, and each is a way an empty before side could become a lie:

* **Empty is created, never copied.** The dataset is built by the shipped initialization owner
  (:func:`~agents_remember.application.knowledge_snapshot.create_knowledge_candidate`) under the
  namespace and the exact input pair the committed candidate's own record names, so it is
  schema-valid and belongs to the repository. It is never a copy of the candidate: a populated
  candidate used as its own before side would display the first addition as present on both sides.
* **The generation is identified.** The dataset alone cannot say whether it is an empty *first*
  generation or a history that was measured and found empty, and those are different facts. The
  origin record written beside it names which one this is, the namespace it belongs to, the code base
  the run observed, and that pre-feature history is **not recorded** -- an existing code repository
  whose earlier commits carry no recorded intent never had that intent measured as empty, so an empty
  dataset must not be read as a measurement of it.
* **Initialization is all-or-nothing.** The dataset and its record are built in a private stage and
  exposed as one directory, exactly as candidate creation does. A failure therefore leaves either a
  complete, identified before half or nothing at all -- never a dataset a later reader could accept
  as a valid baseline without knowing what it is.

Nothing here decides authority, and nothing here is knowledge. The record is a *local operation fact*
about the run that wrote the half -- the same kind of object the candidate receipt and the ingest's
own allocation journal are -- which is why it lives beside its dataset in the leaf's disposable
knowledge root rather than as a row anything can query. Reading the half and its datasets is the
separate concern of :mod:`agents_remember.application.knowledge_before_half`, whose readers decide
what is already there before this module writes anything.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import apsw

from agents_remember.application.knowledge_before_half import (
    BaselineOrigin,
    BeforeHalf,
    baseline_database_path,
    baseline_origin_path,
    read_baseline_origin,
    read_before_half,
    write_baseline_origin,
)
from agents_remember.application.knowledge_snapshot import (
    admitted_candidate_destination,
    create_knowledge_candidate,
)
from agents_remember.kernel.atomic_write import atomic_replace
from agents_remember.memory.knowledge.candidate_receipt import (
    read_candidate_receipt,
    resolution_from_receipt,
)
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.logical import bound_repository, dataset_identity
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.candidate import CandidateResolution, SnapshotIdentity
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.snapshot import (
    candidate_database_path,
    candidate_receipt_path,
)

__all__ = [
    "BeforeGeneration",
    "FirstGenerationRun",
    "establish_first_generation",
]

BeforeGenerationState = Literal["established", "present", "not-established"]


@dataclass(frozen=True)
class FirstGenerationRun:
    """The admitted run that establishes one first generation, as its record names it."""

    leaf_id: str
    contract_path: str
    authorization_ref: str
    code_base_commit: str | None = None


@dataclass(frozen=True)
class BeforeGeneration:
    """What one run left in the before half: its state, the report's line about it, and the record."""

    state: BeforeGenerationState
    detail: str
    origin: BaselineOrigin | None = None


def establish_first_generation(
    baseline_directory: Path,
    candidate_directory: Path,
    run: FirstGenerationRun,
) -> BeforeGeneration:
    """Establish one before half as an explicitly identified empty first generation.

    The caller owns *whether* this happens -- the run that committed the candidate, and only that
    run -- and this operation owns *how*: the admission is read from the candidate's own record, the
    half is built privately, and it is exposed only complete. Nothing is written when the half
    already holds a dataset: a retry keeps the before side it was first handed rather than restating
    it, and an existing dataset is never rewritten, relabelled or replaced by this operation. A half
    whose own bytes cannot be read as the generation its record names is reported ``not-established``
    with the damage, and is left exactly as it is.
    """

    half = Path(baseline_directory)
    standing = read_before_half(half)
    if standing.state != "absent":
        return _standing_before_half(standing, baseline_directory=half)
    admission = _candidate_admission(Path(candidate_directory))
    if isinstance(admission, str):
        return BeforeGeneration(state="not-established", detail=admission)
    repository, resolution = admission
    return _expose_first_generation(half, repository, resolution, run)


def _standing_before_half(standing: BeforeHalf, *, baseline_directory: Path) -> BeforeGeneration:
    """The report line for a half that already holds something, and the damage it names."""

    if standing.state == "damaged":
        return BeforeGeneration(
            state="not-established",
            detail=(
                f"not-established: {standing.detail}, so nothing was established here and the half "
                "is left exactly as it is"
            ),
        )
    if standing.state == "identified":
        return BeforeGeneration(
            state="present",
            detail=(
                f"present: {standing.database} (first generation already recorded at "
                f"{baseline_origin_path(baseline_directory)}, so this run restated nothing)"
            ),
            origin=standing.origin,
        )
    return BeforeGeneration(
        state="present",
        detail=(
            f"present: {standing.database} (no recorded origin; this half was not established here)"
        ),
    )


def _candidate_admission(
    candidate_directory: Path,
) -> tuple[RepositoryIdentity, CandidateResolution] | str:
    """The admission the committed candidate records, or the reason it could not be read.

    The before half is created under the namespace and the exact input pair the candidate itself
    records -- its receipt for the admission, its own repository row for the namespace -- rather
    than under values this module re-derives. Two halves bound by two derivations is exactly the
    drift a comparison would display as a difference that never happened, and the candidate's
    record is the one authority for both.
    """

    database = candidate_database_path(candidate_directory)
    if not database.is_file():
        return f"not-established: the committed candidate dataset {database} is not a file"
    try:
        receipt = read_candidate_receipt(candidate_receipt_path(candidate_directory))
    except KnowledgeStorageError as error:
        return f"not-established: the committed candidate's receipt could not be read ({error})"
    repository = _recorded_namespace(database)
    if repository is None:
        return (
            f"not-established: the committed candidate dataset {database} records no repository "
            "namespace, so the before half would be bound to nothing"
        )
    if repository.repository_id != receipt.repository_id:
        return (
            f"not-established: the candidate's receipt names namespace {receipt.repository_id} "
            f"while its dataset is bound to {repository.repository_id}"
        )
    return repository, resolution_from_receipt(receipt)


def _recorded_namespace(database: Path) -> RepositoryIdentity | None:
    """The namespace one dataset records, or ``None`` when it records none this code can read."""

    try:
        connection = open_read_only_database(database)
    except (apsw.Error, OSError):
        return None
    try:
        return bound_repository(connection)
    except (KnowledgeStorageError, apsw.Error):
        return None
    finally:
        connection.close()


def _expose_first_generation(
    half: Path,
    repository: RepositoryIdentity,
    resolution: CandidateResolution,
    run: FirstGenerationRun,
) -> BeforeGeneration:
    """Build the identified empty half privately and expose it as one complete directory."""

    try:
        stage = _stage_directory(half)
    except OSError as error:
        return BeforeGeneration(
            state="not-established",
            detail=(
                f"not-established: the private stage for {half} could not be created, so no "
                f"before half was established ({error})"
            ),
        )
    try:
        return _promote_first_generation(stage, half, repository, resolution, run)
    finally:
        # A promoted stage has been renamed onto the half, so this removes only a stage that never
        # became one: the expose is the last step, and nothing partial is ever left at the half.
        shutil.rmtree(stage, ignore_errors=True)


def _promote_first_generation(
    stage: Path,
    half: Path,
    repository: RepositoryIdentity,
    resolution: CandidateResolution,
    run: FirstGenerationRun,
) -> BeforeGeneration:
    """Promote one complete staged half onto the before half, or claim nothing at all."""

    try:
        origin = _build_first_generation(stage, repository, resolution, run)
    except (KnowledgeStorageError, apsw.Error, OSError, ValueError) as error:
        return BeforeGeneration(
            state="not-established",
            detail=(
                "not-established: the first-generation dataset could not be built, so nothing was "
                f"established at {half} ({error})"
            ),
        )
    if isinstance(origin, str):
        return BeforeGeneration(state="not-established", detail=origin)
    try:
        atomic_replace(stage, half)
    except OSError as error:
        return BeforeGeneration(
            state="not-established",
            detail=(
                f"not-established: the completed first generation could not be exposed at {half}, "
                f"so nothing is claimed there ({error})"
            ),
        )
    return BeforeGeneration(
        state="established",
        detail=(
            f"established: {baseline_database_path(half)} (first generation, empty and schema "
            f"valid, in namespace {origin.repository_id}; origin recorded at "
            f"{baseline_origin_path(half)})"
        ),
        origin=origin,
    )


def _build_first_generation(
    stage: Path,
    repository: RepositoryIdentity,
    resolution: CandidateResolution,
    run: FirstGenerationRun,
) -> BaselineOrigin | str:
    """Create the empty dataset in the stage and write the origin record that identifies it."""

    created = create_knowledge_candidate(
        admitted_candidate_destination(stage, repository, resolution)
    )
    if created.state != "created":
        refusal = created.refusal
        reason = (
            "no refusal was returned" if refusal is None else f"{refusal.code}: {refusal.detail}"
        )
        return f"not-established: the empty first-generation dataset was refused ({reason})"
    # The identity is read back from the dataset that was created rather than taken from the
    # admission's answer, so the record cannot state an identity the bytes do not hold.
    identity = dataset_identity(baseline_database_path(stage))
    origin = _origin_record(identity, repository, run)
    write_baseline_origin(stage, origin)
    if read_baseline_origin(stage) != origin:
        return "not-established: the written origin record did not read back unchanged"
    return origin


def _origin_record(
    identity: SnapshotIdentity,
    repository: RepositoryIdentity,
    run: FirstGenerationRun,
) -> BaselineOrigin:
    """One origin record built from the dataset that was actually created and the run that made it."""

    return BaselineOrigin(
        repository_id=identity.repository_id,
        authority_home=repository.authority_home,
        schema_version=identity.schema_version,
        logical_digest=identity.logical_digest,
        code_base_commit=run.code_base_commit,
        recorded_at=datetime.now(UTC).isoformat(),
        leaf_id=run.leaf_id,
        contract_path=run.contract_path,
        authorization_ref=run.authorization_ref,
    )


def _stage_directory(half: Path) -> Path:
    """A fresh private sibling path for one first-generation build, which does not exist yet.

    The path is beside the half, so the promote step is a rename inside one filesystem: a stage in
    another directory could be promoted only by a copy, which is exactly the window this exists to
    close. The path is *returned uncreated* because the dataset is built by the shipped creation
    owner, and that owner's own first act is to refuse an occupied destination -- a stage this
    function had already created would be refused as a resume attempt rather than built.
    """

    parent = half.parent
    parent.mkdir(parents=True, exist_ok=True)
    return parent / f".{half.name}.{uuid4().hex}.first-generation"
