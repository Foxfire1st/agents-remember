"""The durable comparison generation: one manifest, its layout, and how it is read back.

A comparison is only worth what can be reopened. The datasets it read live in a leaf's *disposable*
knowledge root, the candidate it bound is a tree that exists in no commit, and the task worktree that
held both is removed by cleanup. Once that has happened, a reader holding only "a comparison was
made" has nothing: it cannot re-read the source it reviewed, the knowledge it compared against, or
the evidence it cited.

This module owns the durable record that changes that -- and only the record:

* **The manifest.** :class:`ComparisonGenerationManifest` is one immutable, canonical-JSON record of
  what a comparison bound: both code objects, both knowledge sides (or the typed state that says a
  side legitimately has none), the selected scope and the exact inventory measured over it, the record
  collections the composition supplied, the evidence references it cited, every owner-declared policy
  version in play, and the lineage of the generation itself. It stores **references to owner-produced
  content and no semantic judgment of its own** -- the one digest it computes for itself is a seal
  over its own fields, so a manifest edited in place is detectable without trusting the file.
* **The layout.** One directory per generation under ``<task_root>/notes/reports/`` -- the durable
  task-artifact location :mod:`agents_remember.memory.knowledge.durable_evidence` fixes -- holding the
  manifest, the retained knowledge snapshots beside it, and the explicit history-deletion records.
  The whole directory is published by one rename, so a generation exists or it does not.
* **The deletion record.** :class:`ComparisonHistoryDeletion` is the unavailable-history record an
  explicit release or discard writes *before* it deletes, which is what later lets a reader tell a
  deliberate deletion from an accidental loss instead of reporting both as missing bytes.

**Reopening is the separate responsibility next door.**
:mod:`agents_remember.application.review_comparison_reopen` measures a record this module published
against the repository and the task artifacts it names, and reports one state per channel. It is
separate because reading a record and *resolving what it points at* are different acts: this module
never touches the code repository, and that one never writes.

Known absence, unavailability and deletion are three states, never one:

* ``not-recorded`` is R05's typed historical absence -- the leaf's knowledge side was never recorded,
  which is a fact about the repository's history rather than a failure;
* ``not-selected`` is this comparison's own statement that it selected no knowledge operand at all;
* ``missing``, ``corrupt`` and ``unavailable-history`` are the three ways an expected input fails to
  resolve, and each is measured on the channel that reports it.

Nothing here re-measures a comparison, re-derives an identity another owner produced, or decides what
a channel's content means.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    model_serializer,
    model_validator,
)

from agents_remember.application.knowledge_before_half import NOT_RECORDED
from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.canonical_json import canonical_json_bytes, decoded_json, sha256_digest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.review_records import ReviewRecordChannel
from agents_remember.worktrees.modules.code_object_retention import (
    CUSTODY_RETAINED,
    CodeObjectCustody,
    CodeObjectObservation,
    RetainedCodeObject,
)
from agents_remember.worktrees.modules.future_code_candidate import FutureCodeCandidateIdentity
from agents_remember.worktrees.task_resolver import slugify

__all__ = [
    "COMPARISON_DELETIONS_DIRECTORY",
    "COMPARISON_GENERATIONS_DIRECTORY",
    "COMPARISON_GENERATION_VERSION",
    "COMPARISON_KNOWLEDGE_DIRECTORY",
    "COMPARISON_MANIFEST_NAME",
    "COMPARISON_SNAPSHOT_NAME",
    "KNOWLEDGE_NOT_SELECTED",
    "TYPED_ABSENCE_STATES",
    "ComparisonArtifactReference",
    "ComparisonGenerationManifest",
    "ComparisonGenerationRef",
    "ComparisonHistoryDeletion",
    "ComparisonKnowledgeBinding",
    "ComparisonPolicyStamp",
    "ComparisonPublicationLineage",
    "ComparisonRecordBinding",
    "ComparisonScopeBinding",
    "ComparisonSnapshotArtifact",
    "ComparisonSourceBinding",
    "KnowledgeSide",
    "assemble_manifest",
    "comparison_generations_root",
    "deletion_record_path",
    "generation_directories",
    "generation_directory",
    "generation_identity",
    "leaf_generation_root",
    "manifest_path",
    "read_generation_refs",
    "read_history_deletion",
    "read_manifest",
    "snapshot_path",
    "task_root_for_review",
    "write_history_deletion",
]


# The record's own version. A literal of this module's rather than a package version read at run
# time: two manifests produced by different layouts must be distinguishable from the manifests.
COMPARISON_GENERATION_VERSION: Literal["ar-review-comparison-generation/v1"] = (
    "ar-review-comparison-generation/v1"
)

# The layout, named once. Everything below derives its path from these and the task root, so the
# freeze and the reopen cannot come to disagree about where a generation lives.
COMPARISON_GENERATIONS_DIRECTORY = "comparison-generations"
COMPARISON_MANIFEST_NAME = "manifest.json"
COMPARISON_KNOWLEDGE_DIRECTORY = "knowledge"
COMPARISON_SNAPSHOT_NAME = "snapshot.sqlite"
COMPARISON_DELETIONS_DIRECTORY = "deletions"

# The two states a knowledge side may carry instead of an identity. ``NOT_RECORDED`` is R05's own
# spelling, imported rather than restated so the vocabulary has one owner; ``not-selected`` is this
# comparison's statement that it selected no knowledge operand, which is a different fact from the
# repository never having recorded one.
KNOWLEDGE_NOT_SELECTED: Literal["not-selected"] = "not-selected"
KnowledgeSideState = Literal["retained", "not-recorded", "not-selected"]
KnowledgeSide = Literal["before", "after"]

# The two states that are a *statement* rather than a failure, named through R05's own constant so
# the spelling this module accepts and the spelling the before-half owner records cannot drift: a
# reader that treated ``not-recorded`` as an unavailability would discard a generation for a fact
# about the repository's history, and one that treated a failure as ``not-recorded`` would claim
# intent was never recorded when its bytes were simply gone.
TYPED_ABSENCE_STATES: frozenset[str] = frozenset({NOT_RECORDED, KNOWLEDGE_NOT_SELECTED})

# The namespace every generation id is derived under. A literal, because an id names one local
# generation rather than anything the knowledge plane stores.
_GENERATION_NAMESPACE = UUID("6f1c8ad4-9b25-5e73-9d0c-2f4a7e18c3b6")

# The fields a manifest's own seal covers: everything except the seal, the id derived from it, and
# the time the record was written. ``recorded_at`` is excluded so that an exact retry of one freeze --
# same comparison, same bytes, a later clock -- converges on the same binding digest, the same
# generation id and the same published record instead of refusing against itself; ``generation_id`` is
# excluded because it is the digest's own derived value, so sealing it would be a cycle.
_UNSEALED_FIELDS = ("binding_digest", "generation_id", "recorded_at")


class ComparisonSnapshotArtifact(KnowledgeModel):
    """One retained knowledge snapshot: where it is, what it holds, and who may delete it.

    ``relative_path`` is relative to the generation directory and never absolute, so a generation
    stays addressable after the coordination root is mounted somewhere else -- the same discipline a
    portable knowledge record follows. ``sha256`` is over the bytes that were written, measured after
    the freeze proved the copy closed and complete, so a reopen compares the file against what was
    published rather than against what it was meant to contain.
    """

    relative_path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    sha256: str = Field(pattern=SHA256_PATTERN)
    byte_count: int = Field(ge=0)
    deletion_owner: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    cleanup_scope: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)


class ComparisonKnowledgeBinding(KnowledgeModel):
    """One knowledge side of the comparison: the dataset it was, or the typed state that says why not.

    ``retained`` carries both the owner's dataset identity and the retained copy of its bytes; a side
    that is not retained carries neither and states its reason instead. An identity without bytes is
    the shape the packet's non-conforming example names -- a manifest that stores only a digest of
    already-deleted SQLite bytes -- so the two travel together by construction rather than by care.
    """

    side: KnowledgeSide
    state: KnowledgeSideState
    identity: SnapshotIdentity | None = None
    artifact: ComparisonSnapshotArtifact | None = None
    reason: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _retained_means_identity_and_bytes(self) -> ComparisonKnowledgeBinding:
        retained = self.state == "retained"
        if retained and (self.identity is None or self.artifact is None):
            raise ValueError(
                f"the {self.side} side is recorded as retained, so it names both the dataset "
                "identity it was and the retained copy of its bytes; a digest with no bytes is how "
                "a deleted dataset reads as a retained one"
            )
        if not retained and (self.identity is not None or self.artifact is not None):
            raise ValueError(
                f"the {self.side} side is recorded as {self.state}, so it carries no identity and "
                "no artifact: an identity beside an absent side is an invented selection"
            )
        if not retained and not (self.reason or "").strip():
            raise ValueError(
                f"the {self.side} side is recorded as {self.state} and must state the reason it "
                "carries that state"
            )
        return self


class ComparisonSourceBinding(KnowledgeModel):
    """Both bound code objects, the capture that produced one of them, and their explicit custody.

    ``candidate_capture`` is the capture owner's own identity value, carried verbatim rather than
    restated in this module's field names: the three Git facts are the owner's observation and a
    second spelling of them would be a second place for them to drift. ``retained`` is the pin this
    feature created when durable history did not already hold the tree, and ``custody`` is the
    measurement taken when the record was written -- ``retained`` meaning the pin is the only thing
    keeping the tree alive, ``committed-history`` meaning a branch already holds it.
    """

    code_repository_root: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    baseline_code_tree_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    candidate_code_tree_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    candidate_capture: FutureCodeCandidateIdentity
    custody: CodeObjectCustody
    # The durable history the custody measurement examined: the refs (the leaf's protected source
    # branch) and the commits a task record landed. Recorded rather than implied, because "committed
    # history already holds this tree" is only a fact about the names that were asked -- and a later
    # reader, including the release owner, has to ask the same ones to get a comparable answer.
    custody_refs: tuple[str, ...] = ()
    custody_commits: tuple[str, ...] = ()
    retained: RetainedCodeObject | None = None
    # Named exactly when a pin exists: the operation that may delete it, and the bounded space that
    # deletion may touch -- here, the one ref. A pin nobody owns is an unbounded durable object.
    deletion_owner: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    cleanup_scope: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)

    @model_validator(mode="after")
    def _custody_and_the_pin_agree(self) -> ComparisonSourceBinding:
        if (self.custody == CUSTODY_RETAINED) != (self.retained is not None):
            raise ValueError(
                "a source binding records the pin exactly when custody is 'retained': a retained "
                "tree with no reference is a tree no reclamation is holding, and a pin beside "
                "'committed-history' claims a custody the measurement did not find"
            )
        if (self.retained is None) != (self.deletion_owner is None or self.cleanup_scope is None):
            raise ValueError(
                "every pin this feature creates names its deletion owner and the bounded space that "
                "deletion may touch, and neither is named when there is no pin to reclaim"
            )
        if self.retained is None:
            return self
        if (
            self.retained.tree != self.candidate_code_tree_id
            or self.retained.base_commit != self.baseline_code_tree_id
        ):
            raise ValueError(
                "the recorded pin must keep exactly the two bound objects: it names tree "
                f"{self.retained.tree} based on {self.retained.base_commit}, while this binding "
                f"bound {self.candidate_code_tree_id} based on {self.baseline_code_tree_id}"
            )
        return self


class ComparisonScopeBinding(KnowledgeModel):
    """What was selected, and the exact owner-produced inventory measured over the bound pair.

    The inventory's digest is a reference to R02's measurement, not a second measurement: it binds
    the whole owner-produced value -- its state, its entries and its declared limits -- so a reopen
    can state which inventory this generation froze without re-deriving one from trees that may no
    longer exist. ``changed_path_count`` is the owner's own ``listed_total``, carried so a reader can
    see the population size without opening the digest.
    """

    selected: Literal["task-context", "subject"]
    selector_kind: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    selector_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    inventory_state: Literal["measured", "unavailable"]
    inventory_digest: str = Field(pattern=SHA256_PATTERN)
    changed_path_count: int = Field(ge=0)
    inventory_partial: bool
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _the_selection_and_its_selectors_agree(self) -> ComparisonScopeBinding:
        named = self.selector_kind is not None and self.selector_id is not None
        if (self.selected == "subject") != named:
            raise ValueError(
                "a scope is 'subject' exactly when it names the selector it selected: a subject "
                "scope without one selected nothing, and a task context that names one is a "
                "selection wearing the context's name"
            )
        return self


class ComparisonArtifactReference(KnowledgeModel):
    """One owner-produced artifact this generation cites, as a resolvable task-relative reference.

    ``relative_path`` is relative to the task root -- the durable location the evidence owner
    published into -- so nothing here is absolute and nothing can point outside the task artifact
    plane. ``sha256`` is over the referenced bytes as they were when the generation was frozen, which
    is what makes the reference checkable at reopen rather than merely recorded.
    """

    owner: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    relative_path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    sha256: str = Field(pattern=SHA256_PATTERN)
    byte_count: int = Field(ge=0)


class ComparisonRecordBinding(KnowledgeModel):
    """The record collections the composition supplied, as counts and one digest over their content.

    This is a binding of *inputs*, not a claim about the world: ``supplied`` means the composition
    handed these collections over, and ``not-supplied`` means it handed none while this record was
    written. The optional assessment channel carries R14's actual availability answer separately
    from these counts. Its absence means that availability was not captured, not that no assessment
    ever existed; it contributes no field to the canonical encoding of older sealed manifests.
    """

    state: Literal["supplied", "not-supplied"]
    assessments: int = Field(ge=0)
    signals: int = Field(ge=0)
    observations: int = Field(ge=0)
    current_measured: bool
    collection_digest: str = Field(pattern=SHA256_PATTERN)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    assessment_channel: ReviewRecordChannel | None = None

    @model_serializer(mode="wrap")
    def _canonical_availability(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """An uncaptured channel adds no field to the existing immutable manifest encoding."""

        result = handler(self)
        if self.assessment_channel is None:
            result.pop("assessment_channel", None)
        return result

    @model_validator(mode="after")
    def _assessment_availability_matches_collection(self) -> ComparisonRecordBinding:
        channel = self.assessment_channel
        if channel is not None and (
            channel.records != "assessments"
            or (channel.record_count is not None and channel.record_count != self.assessments)
        ):
            raise ValueError(
                "recorded assessment availability must describe the bound assessment collection"
            )
        return self

    @property
    def record_total(self) -> int:
        """Return how many records the composition supplied in total."""

        return self.assessments + self.signals + self.observations


class ComparisonPolicyStamp(KnowledgeModel):
    """One owner's declared policy version, named by the owner that declares it.

    No version is derived here. Every stamp names a constant its owner publishes -- the comparison
    policy, the surface version, R05's origin version, R18's generation version and this record's own
    -- so a reader can tell which generations of which policies a reopen is subject to without this
    module claiming a version nobody declared.
    """

    owner: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    version: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)


class ComparisonPublicationLineage(KnowledgeModel):
    """Where this generation came from: the one it supersedes, and the inputs it descends from.

    A later generation points at exactly one predecessor, by identity *and* by that predecessor's
    manifest digest, so "the comparison this one replaced" is a value a reader can resolve rather
    than an assertion. The two optional digests are the comparison owner's own binding digest and the
    candidate dataset's admission receipt digest, carried when they exist and absent when they do
    not -- never filled with a plausible value.
    """

    parent_generation_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    parent_manifest_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    comparison_binding_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    candidate_receipt_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _a_predecessor_is_named_by_identity_and_digest(self) -> ComparisonPublicationLineage:
        if (self.parent_generation_id is None) != (self.parent_manifest_digest is None):
            raise ValueError(
                "a lineage names its predecessor's generation id and that generation's manifest "
                "digest together, or neither: one alone cannot be resolved back to a record"
            )
        return self


class ComparisonGenerationManifest(KnowledgeModel):
    """One immutable comparison generation, as it is stored and as it is read back.

    The record answers exactly one question -- "what did this comparison bind, and can it still be
    resolved?" -- and it answers it with identities rather than descriptions. Its own seal,
    ``binding_digest``, covers every field except the seal and ``recorded_at``, so the record
    validates itself on every read: bytes edited in place no longer agree with the digest they carry,
    and a reader that recomputed the digest would find a value the record never held.
    """

    manifest_version: Literal["ar-review-comparison-generation/v1"] = COMPARISON_GENERATION_VERSION
    generation_id: str = Field(pattern=UUID_PATTERN)
    # 1 is the comparison's first recorded generation; every later value supersedes one predecessor.
    # Recorded rather than counted, because "how many times has this comparison been re-frozen" is a
    # fact the reader must see without trusting a directory to still hold its history.
    generation_index: int = Field(ge=1)
    binding_digest: str = Field(pattern=SHA256_PATTERN)
    recorded_at: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    repository_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    master: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    task_root: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    # The bounded space this feature stages inside, relative to the task root: the only temporary
    # entries a freeze creates live here, hidden, beside the generation they are about to become.
    temporary_storage_scope: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    contract_path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    source: ComparisonSourceBinding
    knowledge: tuple[ComparisonKnowledgeBinding, ...]
    scope: ComparisonScopeBinding
    records: ComparisonRecordBinding
    evidence: tuple[ComparisonArtifactReference, ...] = ()
    policies: tuple[ComparisonPolicyStamp, ...]
    lineage: ComparisonPublicationLineage

    @model_validator(mode="after")
    def _the_record_agrees_with_itself(self) -> ComparisonGenerationManifest:
        sides = [side.side for side in self.knowledge]
        if sorted(sides) != ["after", "before"]:
            raise ValueError(
                "a comparison generation binds exactly one knowledge side per half; it recorded "
                f"{sides}"
            )
        if (self.generation_index == 1) != (self.lineage.parent_generation_id is None):
            raise ValueError(
                "a comparison generation is its comparison's first exactly when it records no "
                f"predecessor, and this record claims index {self.generation_index} with parent "
                f"{self.lineage.parent_generation_id}"
            )
        if self.binding_digest != self.compute_binding_digest():
            raise ValueError(
                "the manifest's seal does not cover its own fields: recorded "
                f"{self.binding_digest}, recomputed {self.compute_binding_digest()}. A record whose "
                "seal does not describe it is not the record that was published"
            )
        derived = generation_identity(self.binding_digest)
        if self.generation_id != derived:
            raise ValueError(
                "the manifest's id is not the one its seal derives: recorded "
                f"{self.generation_id}, derived {derived}. Sealing a record and then renaming it "
                "would describe a generation that was never published, so the id is re-derived "
                "rather than trusted"
            )
        return self

    def binding_payload(self) -> dict[str, Any]:
        """Return every field the seal covers: all of them but the seal and the record time."""

        dumped = self.model_dump(mode="json")
        for field in _UNSEALED_FIELDS:
            dumped.pop(field, None)
        return dumped

    def compute_binding_digest(self) -> str:
        """Return the digest of this record's own bindings, recomputed from its fields."""

        return sha256_digest(self.binding_payload())

    def manifest_bytes(self) -> bytes:
        """Return the canonical bytes this record is published as."""

        return canonical_json_bytes(self.model_dump(mode="json"))

    def manifest_digest(self) -> str:
        """Return the sha256 of the bytes a read-back compares against."""

        return sha256_digest(self.model_dump(mode="json"))

    def knowledge_side(self, side: KnowledgeSide) -> ComparisonKnowledgeBinding:
        """Return the binding of one half; a manifest without it is refused by construction."""

        for binding in self.knowledge:
            if binding.side == side:
                return binding
        raise KnowledgeStorageError(f"the manifest records no {side} knowledge side")


class ComparisonHistoryDeletion(KnowledgeModel):
    """One explicit deletion this feature performed, kept as the unavailable-history record.

    The record exists so that a later reopen can say *why* a channel no longer resolves. Without it,
    a deleted snapshot and a snapshot somebody removed by accident are the same observation, and the
    packet's requirement -- explicit retained-history deletion that leaves an unavailable-history
    record rather than aliasing today's data -- has nowhere to live. ``deleted_digest`` is the digest
    of the bytes that were removed, and ``released_custody`` is the custody measured *before* a code
    pin was released, because neither can be recovered after the fact.
    """

    target: Literal["code-object", "knowledge-before", "knowledge-after"]
    deletion_owner: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    cleanup_scope: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    reason: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    recorded_at: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    deleted_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    released_custody: CodeObjectObservation | None = None


# -- the layout ---------------------------------------------------------------------------------


def comparison_generations_root(task_root: Path) -> Path:
    """Return the directory every comparison generation of one task is published under."""

    return durable_reports_root(task_root) / COMPARISON_GENERATIONS_DIRECTORY


def leaf_generation_root(task_root: Path, leaf_id: str) -> Path:
    """Return the directory one leaf's generations live under, named by the leaf's own slug."""

    return comparison_generations_root(task_root) / slugify(leaf_id)


def generation_directory(task_root: Path, leaf_id: str, generation_id: str) -> Path:
    """Return the one directory one generation is published as."""

    return leaf_generation_root(task_root, leaf_id) / generation_id


def manifest_path(task_root: Path, leaf_id: str, generation_id: str) -> Path:
    """Return one generation's manifest path."""

    return generation_directory(task_root, leaf_id, generation_id) / COMPARISON_MANIFEST_NAME


def snapshot_path(task_root: Path, leaf_id: str, generation_id: str, side: KnowledgeSide) -> Path:
    """Return the retained snapshot path of one half of one generation."""

    return generation_directory(task_root, leaf_id, generation_id) / (
        f"{COMPARISON_KNOWLEDGE_DIRECTORY}/{side}/{COMPARISON_SNAPSHOT_NAME}"
    )


def deletion_record_path(task_root: Path, leaf_id: str, generation_id: str, target: str) -> Path:
    """Return the path the unavailable-history record for one target is written to."""

    return generation_directory(task_root, leaf_id, generation_id) / (
        f"{COMPARISON_DELETIONS_DIRECTORY}/{target}.json"
    )


def task_root_for_review(config: McpRuntimeConfig, repository_id: str, master: str) -> Path:
    """Return the task-artifact root one review's generation is published under.

    Derived from the coordination root exactly as the review's own resolution derives it, so the
    directory a freeze writes into and the directory a reopen reads from are one expression rather
    than two conventions that agree today.
    """

    return config.coordination_root / "tasks" / repository_id / master


def generation_identity(binding_digest: str) -> str:
    """Return the deterministic id one manifest's bindings always produce.

    Derived from the seal rather than from a clock or a counter, so an exact retry of one freeze
    addresses the same directory and converges, while a different comparison can never be published
    under an id another comparison already holds.
    """

    return str(uuid5(_GENERATION_NAMESPACE, binding_digest))


def assemble_manifest(payload: dict[str, Any], *, recorded_at: str) -> ComparisonGenerationManifest:
    """Seal one assembled field set and return the validated manifest it describes.

    The seal is computed here, once, over exactly the fields the record will carry, and the model
    then re-derives it on validation -- so a caller cannot publish a manifest whose digest was taken
    over a different field set, which is the whole reason the two steps are one function.

    ``payload`` must already be JSON-ready: every nested value is the ``model_dump(mode="json")`` of
    the owner's own model rather than the model itself. That is what makes the seal a digest over the
    *stored* encoding, and it is idempotent because every model this record nests is built from
    strings, integers and tuples alone -- so the digest a writer computes and the digest a reader
    recomputes are the same value rather than two encodings that happen to agree today.

    The generation id is derived from that same seal rather than supplied by the caller, because an id
    a caller could choose is an id a caller could reuse for a different comparison.
    """

    body = dict(payload)
    body["recorded_at"] = recorded_at
    body["binding_digest"] = sha256_digest(
        {key: value for key, value in body.items() if key not in _UNSEALED_FIELDS}
    )
    body["generation_id"] = generation_identity(str(body["binding_digest"]))
    return ComparisonGenerationManifest.model_validate(body)


# -- reading the record -------------------------------------------------------------------------


def read_manifest(path: Path) -> ComparisonGenerationManifest:
    """Read one published manifest, refusing bytes that are not the record they claim to be.

    Every failure is a storage error rather than a returned state, because the callers state it in
    their own voices: the reopen reports it as an unreadable generation, and a discovery pass skips a
    generation it cannot read rather than guessing which fields it held.

    The record is also required to *be* the generation whose directory holds it. The id is derived
    from the seal, so a record edited in place fails its own validation -- but a record resealed
    *around* edited fields would carry a consistent new seal under the old directory name, and the
    address a reader resolved would then describe a comparison nobody published. The directory name is
    therefore the second half of the check: the same identity, stated twice, from two files.
    """

    if not path.is_file():
        raise KnowledgeStorageError(f"the comparison manifest {path} is not present")
    try:
        decoded = decoded_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise KnowledgeStorageError(
            f"the comparison manifest {path} could not be read as canonical JSON: {error}"
        ) from error
    try:
        manifest = ComparisonGenerationManifest.model_validate(decoded)
    except ValidationError as error:
        raise KnowledgeStorageError(
            f"the comparison manifest {path} is not a valid comparison generation record: {error}"
        ) from error
    if path.parent.name != manifest.generation_id:
        raise KnowledgeStorageError(
            f"the comparison manifest at {path} names generation {manifest.generation_id}, which is "
            f"not the directory it was found in ({path.parent.name}); a record resealed around edited "
            "fields would otherwise be read as the generation a reader had resolved"
        )
    return manifest


def read_history_deletion(
    task_root: Path, leaf_id: str, generation_id: str, target: str
) -> ComparisonHistoryDeletion | None:
    """Read one target's unavailable-history record, or ``None`` when no deletion was recorded.

    A record that is present but unreadable is a storage error rather than ``None``: answering "no
    deletion was recorded" for a record that exists but cannot be read is how a deliberate deletion
    comes to be reported as an accidental loss.
    """

    path = deletion_record_path(task_root, leaf_id, generation_id, target)
    if not path.is_file():
        return None
    try:
        decoded = decoded_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise KnowledgeStorageError(
            f"the history-deletion record {path} could not be read: {error}"
        ) from error
    try:
        return ComparisonHistoryDeletion.model_validate(decoded)
    except ValidationError as error:
        raise KnowledgeStorageError(
            f"the history-deletion record {path} is not a valid record: {error}"
        ) from error


def write_history_deletion(
    task_root: Path,
    leaf_id: str,
    generation_id: str,
    deletion: ComparisonHistoryDeletion,
) -> Path:
    """Publish one unavailable-history record atomically and return where it was written.

    Canonical bytes, for the reason the manifest uses them: the same deletion always has the same
    file content, so a digest over the file is a digest over the facts. A repeated deletion converges
    on the same bytes rather than accumulating records.
    """

    path = deletion_record_path(task_root, leaf_id, generation_id, deletion.target)
    atomic_write_bytes(path, canonical_json_bytes(deletion.model_dump(mode="json")))
    return path


def generation_directories(task_root: Path, leaf_id: str) -> tuple[Path, ...]:
    """Every *published-looking* generation directory of one leaf, hidden stages excluded.

    One list, so discovery and the unreadable-record report cannot come to disagree about which
    directories exist. A hidden entry is a freeze's own unpublished stage and is not a generation.
    """

    root = leaf_generation_root(task_root, leaf_id)
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            entry for entry in root.iterdir() if entry.is_dir() and not entry.name.startswith(".")
        )
    )


@dataclass(frozen=True)
class ComparisonGenerationRef:
    """One generation as a reader addresses it, before any of its content is validated."""

    generation_id: str
    generation_index: int
    binding_digest: str
    manifest_digest: str
    directory: Path


def read_generation_refs(task_root: Path, leaf_id: str) -> tuple[ComparisonGenerationRef, ...]:
    """Every readable generation one leaf has published, ordered by its own recorded index.

    A directory whose manifest is absent or unreadable is **skipped**, not guessed at: this pass
    exists to let a caller address an exact generation, and a discovery step that inferred fields
    from a damaged record would be inventing the identity the reopen is supposed to check. The
    directory is still visible to an explicit reopen, which reports it as unreadable.

    Hidden entries are skipped by name. A freeze stages one unpublished generation under a hidden
    directory beside its destination, so a crash between writing the record and renaming the stage
    would otherwise leave a *complete-looking* generation that no publication ever made.
    """

    refs: list[ComparisonGenerationRef] = []
    for directory in generation_directories(task_root, leaf_id):
        try:
            manifest = read_manifest(directory / COMPARISON_MANIFEST_NAME)
        except KnowledgeStorageError:
            continue
        refs.append(
            ComparisonGenerationRef(
                generation_id=manifest.generation_id,
                generation_index=manifest.generation_index,
                binding_digest=manifest.binding_digest,
                manifest_digest=manifest.manifest_digest(),
                directory=directory,
            )
        )
    return tuple(sorted(refs, key=lambda ref: (ref.generation_index, ref.generation_id)))
