"""The supporting-record vocabulary: the verification observation (the evidence claim is retired).

``KS-R12@v1`` chartered **two** record kinds; the evidence-claim payload and its subject and coverage
models were retired with the canonical database (MIK-R26), and this module keeps the typed shape of
the verification observation, which the reviewer still reads. They are
declared here, under the shipped :class:`KnowledgeModel` base (``extra="forbid"``, ``frozen=True``),
so a payload is a validated value rather than the untyped properties bag ``design/storage-design.md``
refuses, and so a field that is not declared here has nowhere to be stored.

Two records rather than one "evidence" table, and the separation is the leaf's reason for existing.
`Doc13:92` records an authored claim about what evidence *covers*; `Doc13:93` records a mechanical
fact about what *ran*. Collapsing them would put a human's coverage assertion in the same row as a
machine's exit status, and the first consumer that reads the row would reasonably treat the whole row
as machine-produced. The separation is also what makes the packet's C4 enforceable: if a passing run
lives in a different record from the claim, there is no single row in which "passed" could be read as
"sufficient".

Five boundaries are load-bearing and each is stated once, here, rather than in prose around the code:

* **No semantic conclusion anywhere.** Nothing in either payload asks or answers whether the evidence
  is adequate, sufficient, convincing, relevant or "supporting"; nothing grades, ranks, scores or
  summarises a claim. The only things a caller can store are the facts the author wrote and the facts
  the run produced. There is deliberately no field in either model for a verdict, a confidence, a
  severity or an endorsement, so there is nothing for a well-meaning writer to fill in.
* **No second identity authority.** Neither record carries a content address, a logical digest or a
  fingerprint column of its own. A revision's seal is ``record_revision.content_digest`` on the
  envelope's own aggregate; the one digest this module declares is the sha256 of an **external
  artifact's bytes**, which is an identity of something that lives outside the database.
* **The artifact reference is a reference, never the bytes.** The substrate has no second content
  store and this leaf does not add one: the reference is a confined repository-relative POSIX path,
  the sha256 of the artifact's bytes and its byte size. Retention is therefore an obligation with its
  own acceptance evidence rather than an accident of where the file happened to be written, and
  :class:`VerificationObservationPayload` carries the publication reference that names where the
  interpretable manifest was durably published.
* **The execution result is closed and is never a sufficiency verdict.** ``not run`` is a member and
  is reported as itself, never as passed. The members name what a run *did*; none of them says what
  the result *means*.
* **The environment is the run's, recorded at write time.** Nothing here is re-derived from the
  reading machine, and the model has no field for a reader's environment to land in.

**Assessment references are an explicit opaque reference clause.** ``ReviewAssessment`` is owned by
``KS-R15@v1``, which is scheduled in a later wave, so ``assessment_refs`` is present, typed and
bounded here and code does **not** validate that the referent exists. When ``KS-R15@v1`` lands the
reference becomes a resolved association; the transition changes this clause's *resolution*
behaviour and not the stored meaning of an already-sealed claim. No code path may drop, default or
synthesise the field.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    KnowledgeModel,
    KnowledgeState,
    require_consistent_acceptance,
    require_plain_git_path,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity

# ---------------------------------------------------------------------------
# The record kinds, their frozen payload schemas and the one closed vocabularies' names. The
# ``(kind, record_schema)`` pair is the envelope seam's key, so the pair is declared here beside the
# models rather than spelled a second time at the registry.


# ---------------------------------------------------------------------------
# The evidence anchor and the claimed coverage: one endpoint kind per table, so the table IS the
# kind check.
#
# The shape the packet forbids is ``(endpoint_kind, endpoint_id)`` with an unconstrained target:
# a misspelled kind, a dangling id and a wrong-kind target all store successfully in it. Each
# endpoint here is therefore its own typed model over its own column and its own foreign key, and
# there is no ``endpoint_id`` column anywhere in this leaf's tables for an unchecked identity to
# land in.


# ---------------------------------------------------------------------------
# The claim's subject: exactly one of two kinds, each its own typed model and its own join table.


# ---------------------------------------------------------------------------
# The result artifact: a reference, never the bytes.


class ResultArtifactReference(KnowledgeModel):
    """One recorded reference to the artifact a run produced.

    The path obeys the shipped confinement and plain-path rule (``models/knowledge/source.py``'s
    own validator, applied here to a result artifact): no absolute root, no drive or UNC form, no
    backslash escape, no NUL, no ``..``, no Git pathspec magic. ``digest_checked_against_bytes`` is
    the packet's §7.3 field: it records **which** of the two admissible things happened, so a digest
    that was only asserted is never presented at read time as one that was checked.
    """

    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(ge=0)
    digest_checked_against_bytes: bool

    @field_validator("path")
    @classmethod
    def _require_confined_relative_posix_path(cls, value: str) -> str:
        """Refuse anything that is not a repository-relative POSIX path."""

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("result artifact path must not be blank")
        if cleaned.startswith(("/", "\\")) or cleaned.startswith("~"):
            raise ValueError(f"result artifact path must be repository-relative: {value!r}")
        if "\\" in cleaned:
            raise ValueError(f"result artifact path must use POSIX separators: {value!r}")
        if "\x00" in cleaned:
            raise ValueError("result artifact path must not contain a NUL byte")
        if ":" in cleaned:
            raise ValueError(
                f"result artifact path must not name a drive or a Git pathspec: {value!r}"
            )
        parts = cleaned.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError(
                f"result artifact path must not contain empty, '.' or '..' segments: {value!r}"
            )
        return require_plain_git_path(cleaned, what="result artifact path")


# ---------------------------------------------------------------------------
# The durable publication reference.
#
# ``KS-R12@v1`` §9.3 requires the record itself to carry an explicit publication reference, "so a
# later reader does not have to guess". It is a *reference to the published manifest* and not a
# second archive: the manifest is the leaf's own evidence document, and the destination is the
# coordination task root's ``notes/reports/`` directory -- the shipped curator-coherence route --
# which is outside the worktree group and therefore outside the cleanup removal set.


class PublicationReference(KnowledgeModel):
    """Where the manifest a reader needs in order to interpret this record was durably published.

    ``sha256`` is the digest of the **published manifest bytes**, taken at publication. It is not the
    artifact digest and not a digest of this record: it identifies the published document so the
    post-cleanup read-back is a comparison of identified bytes rather than of a path that happens to
    still exist.
    """

    destination: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    sha256: str = Field(pattern=SHA256_PATTERN)
    published_at: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)

    @field_validator("destination")
    @classmethod
    def _require_confined_relative_posix_path(cls, value: str) -> str:
        """Refuse a publication destination that is not a coordination-root-relative POSIX path."""

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("publication destination must not be blank")
        if cleaned.startswith(("/", "\\")) or cleaned.startswith("~"):
            raise ValueError(f"publication destination must be relative: {value!r}")
        if "\\" in cleaned:
            raise ValueError(f"publication destination must use POSIX separators: {value!r}")
        if "\x00" in cleaned:
            raise ValueError("publication destination must not contain a NUL byte")
        if ":" in cleaned:
            raise ValueError(f"publication destination must not name a drive: {value!r}")
        parts = cleaned.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError(
                f"publication destination must not contain empty, '.' or '..' segments: {value!r}"
            )
        return cleaned


# ---------------------------------------------------------------------------
# The closed execution-result vocabulary.
#
# The packet fixes the shape and not the member list: the set is closed (an unlisted value is
# refused), it is stored as data, "not run" is never reported as passed, and no member is a
# sufficiency verdict. These five members are the recorded-decision implementation of that shape,
# chosen so that the four things a run can do are distinguishable without any of them naming what
# the result means:
#
# * ``passed``   -- the command ran and reported success;
# * ``failed``   -- the command ran and reported failure;
# * ``error``    -- the command could not complete (a tool fault, not a result about the subject);
# * ``skipped``  -- the run deliberately did not execute the command;
# * ``not_run``  -- the command was never executed, and the record says so.
#
# ``skipped`` and ``not_run`` are two facts, not one: "this run chose not to run it" is a property of
# the run, while "no run has run it" is a property of the command. Neither is ever reported as
# passed. What is *not* here is anything like ``inconclusive``, ``sufficient`` or ``verified`` --
# every one of those would be a reading of the evidence, and the reading belongs to the curator's own
# authored record.
ExecutionResult = Literal["passed", "failed", "error", "skipped", "not_run"]


# ---------------------------------------------------------------------------
# The environment identity: the run's environment, recorded at write time.


class RunEnvironment(KnowledgeModel):
    """The environment one run observed, recorded by the run.

    It names the run's environment and never the reader's: every field here is written at write time
    from what the producer reported, and no read path re-derives any of them. The three fields are
    the minimum that makes "which machine, which interpreter, which toolchain" answerable from the
    record alone, and they are deliberately *not* a free-form bag -- an environment identity that
    could carry arbitrary extra fields is one a reader would have to guess the meaning of.

    ``host`` and ``toolchain`` are opaque recorded references on purpose. A hostname is environment
    configuration rather than portable identity, and this module does not resolve, normalise or
    compare one; it records what the run said. ``toolchain`` is a tuple of ``(tool, version)`` pairs
    rather than a mapping so that the frozen model is deeply frozen and its canonical JSON text is a
    function of the recorded pairs in the order the run reported them.
    """

    host: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    interpreter: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    toolchain: tuple[tuple[str, str], ...] = ()

    @field_validator("toolchain")
    @classmethod
    def _require_bounded_toolchain(
        cls, value: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        """Refuse an unbounded, blank or duplicated toolchain component."""

        if len(value) > 16:
            raise ValueError("a run environment names at most sixteen toolchain components")
        seen: set[str] = set()
        for name, version in value:
            if not name.strip() or not version.strip():
                raise ValueError("a toolchain component must name a tool and a version")
            if len(name) > LABEL_MAX_LENGTH or len(version) > LABEL_MAX_LENGTH:
                raise ValueError("a toolchain component name or version is longer than the bound")
            if name in seen:
                raise ValueError(f"toolchain component {name!r} is listed twice")
            seen.add(name)
        return value


# ---------------------------------------------------------------------------
# The two payloads.


class VerificationObservationPayload(KnowledgeModel):
    """The frozen recorded content of one verification observation.

    It records a run that **already happened**: this leaf never executes the command it records, adds
    no command-execution surface, schedules nothing and interprets no output.

    The exact tested candidate is recorded as data and is never re-derived at read time. A run that
    tested a knowledge dataset records that dataset's logical snapshot identity -- the shipped
    :class:`SnapshotIdentity`, so no candidate registry is invented here -- and a run that tested code
    records the code candidate's tree identity. At least one of the two must be present: an
    observation that names no candidate is an observation about nothing in particular, which is a
    shape error rather than a record.

    ``command_identity`` and ``command_name`` are a recorded *string set*. They are what the run
    reported, stored verbatim; the read path does not resolve either into a different command, and
    there is no field here for one to resolve through.

    ``publication`` is the packet's §9.3 reference: it says where the manifest a reader needs in order
    to interpret this observation was durably published, and whether that record is relying on the
    memory repo's own committed content or on an external artifact. Its absence is the explicit
    "nothing was published for this record" state, which is a fact and not an omission to be filled
    in later.
    """

    command_name: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    command_identity: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    knowledge_candidate: SnapshotIdentity | None = None
    code_candidate_tree_id: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{40}$|^[0-9a-f]{64}$"
    )
    result_artifact: ResultArtifactReference | None = None
    execution_result: ExecutionResult
    environment: RunEnvironment
    publication: PublicationReference | None = None
    state_at_origin: KnowledgeState = "proposed"
    acceptance_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_a_recorded_candidate(self) -> VerificationObservationPayload:
        """Refuse an observation that names no tested candidate at all."""

        if self.knowledge_candidate is None and self.code_candidate_tree_id is None:
            raise ValueError(
                "a verification observation records the exact tested candidate: name the knowledge "
                "snapshot identity, the code candidate tree identity, or both. An observation that "
                "names neither is a record of no candidate, which is a different statement from a "
                "record of one."
            )
        return self

    @model_validator(mode="after")
    def _require_consistent_origin(self) -> VerificationObservationPayload:
        require_consistent_acceptance(self.state_at_origin, self.acceptance_ref)
        return self


# ---------------------------------------------------------------------------
# The two authored commands.
#
# They live beside the vocabulary they carry, exactly as the facet commands do, and the *closed
# union* stays declared in ``models.knowledge.candidate`` -- which is where the operation's whole
# reach is. Appending is not widening: each member is one typed authored act, there is still no
# free-form member, no arbitrary table or column target, and no member that could promote, approve,
# execute or infer something the caller wrote.


__all__ = [
    "ExecutionResult",
    "PublicationReference",
    "ResultArtifactReference",
    "RunEnvironment",
    "VerificationObservationPayload",
]
