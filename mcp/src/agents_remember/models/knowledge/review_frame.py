"""A canvas frame, its immutable revisions, its drafts and its reclamation receipts (MIK-R103).

A *frame* is one presentation lineage inside an admitted repository/task/leaf review scope; a
*revision* is one immutable publication inside it. This module fixes the shapes the store writes and
the reader reopens, and it keeps apart identities that one "current" pointer would blur:

* the **frame** (``frame_id``): stable across revisions, one lineage;
* the **revision** (``revision``, ``parent_revision``): a number and its predecessor, never reused,
  with the **asset ancestry** it was regenerated from (rule 6);
* the **binding** (:class:`FrameBinding`): the existing comparison the revision explains, in the
  comparison owner's full identity -- task, leaf, number, each side's repository, tree, commit and
  ref, and the complete conversion inputs. Equal tree hashes in another repository are another
  comparison (rule 1, rule 5);
* the **evidence**: the admitted R102 packet itself, durable with the revision, so a page reopened
  after its authoring scratch is gone can still show its claim bases (rule 2, rule 9);
* the **artifact integrity digest**: over the revision's own asset bytes only. It verifies bytes and
  cannot stand in for a comparison identity.

A draft is not a ready revision: :class:`FrameDraft` records the authoring identity, its state and
its lease, and a failed or cancelled attempt keeps its bounded result (rule 9). A reclamation keeps a
tombstone (:class:`FrameReclamation`) so a later reopen can say *why* a page is gone instead of
calling it never generated (failure and recovery).
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.review_evidence import (
    EvidenceComparisonBinding,
    EvidencePacket,
    SupplementalReceipt,
)
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord

__all__ = [
    "COMPARISON_OWNER",
    "FRAME_DRAFT_SCHEMA",
    "FRAME_MANIFEST_SCHEMA",
    "FRAME_POINTER_SCHEMA",
    "FRAME_RECLAMATION_SCHEMA",
    "PLANNED_OWNER",
    "FrameAsset",
    "FrameAuthoring",
    "FrameBinding",
    "FrameDraft",
    "FrameEntrypoint",
    "FrameLimits",
    "FrameManifest",
    "FrameOpening",
    "FramePart",
    "FramePointer",
    "FrameReclamation",
    "FrameRegeneration",
    "FrameStalePart",
    "FrameStaleness",
    "artifacts_digest",
    "comparison_reference",
    "four_tree_digest",
    "planned_reference",
]

COMPARISON_OWNER = "agents_remember.application.review_tree_comparison"
PLANNED_OWNER = "planned"


def comparison_reference(record: ReviewTreeComparisonRecord) -> str:
    """The stable reference of one existing comparison: task, leaf and its own number."""

    return f"review-comparison:{record.task_id}/{record.leaf_id}/{record.number}"


def planned_reference(task_id: str, leaf_id: str) -> str:
    """The planned comparison's own reference; it never reads as a closed leaf's number 0."""

    return f"review-comparison:{task_id}/{leaf_id}/planned"


FRAME_MANIFEST_SCHEMA: Literal["ar-review-frame-manifest/v2"] = "ar-review-frame-manifest/v2"
FRAME_POINTER_SCHEMA: Literal["ar-review-frame-pointer/v1"] = "ar-review-frame-pointer/v1"
FRAME_DRAFT_SCHEMA: Literal["ar-review-frame-draft/v1"] = "ar-review-frame-draft/v1"
FRAME_RECLAMATION_SCHEMA: Literal["ar-review-frame-reclamation/v1"] = (
    "ar-review-frame-reclamation/v1"
)

# The admitted ceilings (rule 8). They are design ceilings, not measured performance claims; a leaf
# that proves one too tight or too loose proposes a change with its evidence.
MAX_FILES = 64
MAX_REVISION_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_TASK_BYTES = 512 * 1024 * 1024
MAX_DRAFT_BYTES = 16 * 1024 * 1024

_MEDIA_BY_SUFFIX: dict[str, str] = {
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".png": "image/png",
    ".woff2": "font/woff2",
}


class FrameLimits(KnowledgeModel):
    """The finite ceilings one frame store enforces (rule 8)."""

    files_per_revision: int = Field(default=MAX_FILES, gt=0)
    bytes_per_revision: int = Field(default=MAX_REVISION_BYTES, gt=0)
    manifest_bytes: int = Field(default=MAX_MANIFEST_BYTES, gt=0)
    task_bytes: int = Field(default=MAX_TASK_BYTES, gt=0)
    draft_bytes: int = Field(default=MAX_DRAFT_BYTES, gt=0)
    draft_lease_seconds: int = Field(default=900, gt=0)


class FrameAsset(KnowledgeModel):
    """One admitted asset: its relative path, media type, exact size and digest."""

    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    media_type: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    byte_size: int = Field(ge=0)
    digest: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _path_is_contained(self) -> FrameAsset:
        if (
            self.path.startswith("/")
            or "\\" in self.path
            or ".." in self.path.split("/")
            or self.path.startswith(".")
        ):
            raise ValueError(
                f"the asset path {self.path!r} is not one relative path inside the revision's "
                "artifact area"
            )
        if _MEDIA_BY_SUFFIX.get(_suffix(self.path)) is None:
            raise ValueError(f"the asset {self.path!r} has no admitted media type")
        return self


def _suffix(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if name == "LICENSE":
        return ".txt"
    _stem, dot, suffix = name.rpartition(".")
    return f".{suffix.lower()}" if dot else ""


class FrameEntrypoint(KnowledgeModel):
    """The page a ready frame opens: one HTML asset the manifest also lists."""

    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    media_type: Literal["text/html"] = "text/html"


class FrameAuthoring(KnowledgeModel):
    """Who authored this revision, in which execution and session, and when (rule 1)."""

    request_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    execution_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    session_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    model: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    started_at: str = Field(min_length=1, max_length=64)
    finished_at: str | None = Field(default=None, max_length=64)


class FrameBinding(KnowledgeModel):
    """The existing comparison one revision binds, in its owner's full identity (rule 1).

    Each side keeps its repository, tree, commit and ref; the conversion keeps every input the
    comparison owner recorded. ``four_tree_digest`` is a convenience over the eight repository/tree
    pairs plus the conversion tree; it is not an identity of its own, and equality of it never makes
    two different repositories one comparison.
    """

    comparison_owner: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    task_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    comparison_number: int = Field(ge=0)
    comparison_reference: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    code_base_repository: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    code_base_tree: str = Field(pattern=GIT_OBJECT_PATTERN)
    code_base_commit: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    code_base_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    code_candidate_repository: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    code_candidate_tree: str = Field(pattern=GIT_OBJECT_PATTERN)
    code_candidate_commit: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    code_candidate_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    memory_base_repository: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    memory_base_tree: str = Field(pattern=GIT_OBJECT_PATTERN)
    memory_base_commit: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    memory_base_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    memory_candidate_repository: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    memory_candidate_tree: str = Field(pattern=GIT_OBJECT_PATTERN)
    memory_candidate_commit: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    memory_candidate_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    converted_base_commit: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    converted_base_version: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    converted_base_code_commit: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    converted_base_tree: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    compared_memory_base_tree: str = Field(pattern=GIT_OBJECT_PATTERN)
    four_tree_digest: str = Field(pattern=SHA256_PATTERN)

    @classmethod
    def from_record(
        cls, record: ReviewTreeComparisonRecord, *, comparison_owner: str, comparison_reference: str
    ) -> FrameBinding:
        """The binding one existing comparison record states, with no new authority made here."""

        converted = record.converted_base
        return cls(
            comparison_owner=comparison_owner,
            task_id=record.task_id,
            leaf_id=record.leaf_id,
            comparison_number=record.number,
            comparison_reference=comparison_reference,
            code_base_repository=record.code_base.repository,
            code_base_tree=record.code_base.tree,
            code_base_commit=record.code_base.commit,
            code_base_ref=record.code_base.ref,
            code_candidate_repository=record.code_candidate.repository,
            code_candidate_tree=record.code_candidate.tree,
            code_candidate_commit=record.code_candidate.commit,
            code_candidate_ref=record.code_candidate.ref,
            memory_base_repository=record.memory_base.repository,
            memory_base_tree=record.memory_base.tree,
            memory_base_commit=record.memory_base.commit,
            memory_base_ref=record.memory_base.ref,
            memory_candidate_repository=record.memory_candidate.repository,
            memory_candidate_tree=record.memory_candidate.tree,
            memory_candidate_commit=record.memory_candidate.commit,
            memory_candidate_ref=record.memory_candidate.ref,
            converted_base_commit=converted.commit if converted else None,
            converted_base_version=converted.version if converted else None,
            converted_base_code_commit=converted.code_commit if converted else None,
            converted_base_tree=converted.tree if converted else None,
            compared_memory_base_tree=converted.tree if converted else record.memory_base.tree,
            four_tree_digest=four_tree_digest(
                record.code_base.repository,
                record.code_base.tree,
                record.code_candidate.repository,
                record.code_candidate.tree,
                record.memory_base.repository,
                record.memory_base.tree,
                record.memory_candidate.repository,
                record.memory_candidate.tree,
                converted.tree if converted else None,
            ),
        )

    @classmethod
    def from_packet(cls, comparison: EvidenceComparisonBinding) -> FrameBinding:
        """The binding an admitted evidence packet already states, with no second derivation."""

        return cls(
            comparison_owner=comparison.comparison_owner,
            task_id=comparison.task_id,
            leaf_id=comparison.leaf_id,
            comparison_number=comparison.comparison_number,
            comparison_reference=comparison.comparison_reference,
            code_base_repository=comparison.code_base.repository,
            code_base_tree=comparison.code_base.tree,
            code_base_commit=comparison.code_base.commit,
            code_base_ref=comparison.code_base.ref,
            code_candidate_repository=comparison.code_candidate.repository,
            code_candidate_tree=comparison.code_candidate.tree,
            code_candidate_commit=comparison.code_candidate.commit,
            code_candidate_ref=comparison.code_candidate.ref,
            memory_base_repository=comparison.memory_base.repository,
            memory_base_tree=comparison.memory_base.tree,
            memory_base_commit=comparison.memory_base.commit,
            memory_base_ref=comparison.memory_base.ref,
            memory_candidate_repository=comparison.memory_candidate.repository,
            memory_candidate_tree=comparison.memory_candidate.tree,
            memory_candidate_commit=comparison.memory_candidate.commit,
            memory_candidate_ref=comparison.memory_candidate.ref,
            converted_base_commit=(
                comparison.converted_base.commit if comparison.converted_base else None
            ),
            converted_base_version=(
                comparison.converted_base.version if comparison.converted_base else None
            ),
            converted_base_code_commit=(
                comparison.converted_base.code_commit if comparison.converted_base else None
            ),
            converted_base_tree=(
                comparison.converted_base.tree if comparison.converted_base else None
            ),
            compared_memory_base_tree=comparison.compared_memory_base_tree,
            four_tree_digest=four_tree_digest(
                comparison.code_base.repository,
                comparison.code_base.tree,
                comparison.code_candidate.repository,
                comparison.code_candidate.tree,
                comparison.memory_base.repository,
                comparison.memory_base.tree,
                comparison.memory_candidate.repository,
                comparison.memory_candidate.tree,
                comparison.converted_base.tree if comparison.converted_base else None,
            ),
        )

    @model_validator(mode="after")
    def _conversion_and_effective_tree_agree(self) -> FrameBinding:
        if self.converted_base_tree is None:
            if any(
                value is not None
                for value in (
                    self.converted_base_commit,
                    self.converted_base_version,
                    self.converted_base_code_commit,
                )
            ):
                raise ValueError("a conversion states all of its inputs or none of them")
            if self.compared_memory_base_tree != self.memory_base_tree:
                raise ValueError(
                    "without a conversion the compared memory base is the stored memory base"
                )
        elif (
            self.converted_base_commit is None
            or self.converted_base_version is None
            or self.converted_base_code_commit is None
        ):
            raise ValueError("a conversion states its commit, version and code commit")
        elif self.compared_memory_base_tree != self.converted_base_tree:
            raise ValueError("a converted comparison compares the converted tree")
        return self

    def side_identity(self) -> tuple[tuple[str, str, str | None, str | None], ...]:
        """The four sides' repository, tree, commit and ref, in comparison order."""

        return (
            (
                self.code_base_repository,
                self.code_base_tree,
                self.code_base_commit,
                self.code_base_ref,
            ),
            (
                self.code_candidate_repository,
                self.code_candidate_tree,
                self.code_candidate_commit,
                self.code_candidate_ref,
            ),
            (
                self.memory_base_repository,
                self.memory_base_tree,
                self.memory_base_commit,
                self.memory_base_ref,
            ),
            (
                self.memory_candidate_repository,
                self.memory_candidate_tree,
                self.memory_candidate_commit,
                self.memory_candidate_ref,
            ),
        )

    def conversion_identity(self) -> tuple[str | None, ...]:
        """Every recorded conversion input, so equality covers the conversion itself."""

        return (
            self.converted_base_commit,
            self.converted_base_version,
            self.converted_base_code_commit,
            self.converted_base_tree,
        )

    def same_comparison(self, other: FrameBinding) -> bool:
        """Whether two bindings name the same comparison's full identity (rules 1, 5).

        The owner is part of it: number 0 already marks a closed leaf's committed range, so a
        planned binding is never taken as current against a landed leaf's number-0 comparison
        (MIK-R103 v2 item 3).
        """

        return (
            self.comparison_owner == other.comparison_owner
            and (self.task_id, self.leaf_id, self.comparison_number)
            == (other.task_id, other.leaf_id, other.comparison_number)
            and self.side_identity() == other.side_identity()
            and self.conversion_identity() == other.conversion_identity()
            and self.compared_memory_base_tree == other.compared_memory_base_tree
        )


def four_tree_digest(  # noqa: PLR0913, PLR0917 - the comparison's own four pairs and its tree
    code_base_repository: str,
    code_base_tree: str,
    code_candidate_repository: str,
    code_candidate_tree: str,
    memory_base_repository: str,
    memory_base_tree: str,
    memory_candidate_repository: str,
    memory_candidate_tree: str,
    converted_base_tree: str | None,
) -> str:
    """A digest over the eight repository/tree pairs and the conversion tree, for convenience."""

    payload = json.dumps(
        [
            code_base_repository,
            code_base_tree,
            code_candidate_repository,
            code_candidate_tree,
            memory_base_repository,
            memory_base_tree,
            memory_candidate_repository,
            memory_candidate_tree,
            converted_base_tree,
        ],
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def artifacts_digest(assets: tuple[FrameAsset, ...]) -> str:
    """The artifact integrity digest: over the asset list's paths, sizes and digests (bytes only)."""

    payload = json.dumps(
        sorted([asset.path, asset.byte_size, asset.digest] for asset in assets),
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


class FramePart(KnowledgeModel):
    """One part of a published page, and the claims nested in it (MIK-R103 v2 rule 2).

    The product writes this map; the explainer never does. Its ids are the ones readiness admitted,
    and every claim id in it resolves in the revision's own claim map, so per-part staleness can name
    a part without reading the page again.
    """

    part: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    claims: tuple[str, ...] = ()


def _parts_are_true(part_map: tuple[FramePart, ...], evidence: EvidencePacket) -> None:
    """The part map names each part once and carries each revision claim at most once."""

    part_ids = [part.part for part in part_map]
    if len(part_ids) != len(set(part_ids)):
        raise ValueError("a part id appears once in one revision's part map")
    claims = {claim.claim_id for claim in evidence.claims.claims}
    seen_claims: dict[str, str] = {}
    for part in part_map:
        unknown = [claim for claim in part.claims if claim not in claims]
        if unknown:
            raise ValueError(
                f"part {part.part!r} carries claim(s) the revision's claim map does not hold: "
                + ", ".join(sorted(unknown)[:5])
            )
        if len(part.claims) != len(set(part.claims)):
            raise ValueError(f"part {part.part!r} lists a claim id more than once")
        for claim_id in part.claims:
            owner = seen_claims.get(claim_id)
            if owner is not None:
                raise ValueError(
                    f"claim {claim_id!r} is claimed by both part {owner!r} and part {part.part!r}; "
                    "every claim belongs to at most one part"
                )
            seen_claims[claim_id] = part.part
    if any(part.claims for part in part_map) and set(seen_claims) != claims:
        missing = sorted(claims - set(seen_claims))[:5]
        raise ValueError(
            "every claim of the revision belongs to one part in the part map; unmapped: "
            + ", ".join(missing)
        )


def _supplemental_is_true(
    supplemental: tuple[SupplementalReceipt, ...],
    held: tuple[SupplementalReceipt, ...],
) -> None:
    if len({r.receipt_id for r in supplemental}) != len(supplemental) or len(
        {r.canonical_source for r in supplemental}
    ) != len(supplemental):
        raise ValueError("each supplemental receipt id and canonical source appears once")
    receipts = {(r.receipt_id, r.canonical_source, r.digest, r.read_state) for r in supplemental}
    expected = {(r.receipt_id, r.canonical_source, r.digest, r.read_state) for r in held}
    if receipts != expected or len(supplemental) != len(held):
        raise ValueError(
            "the revision's supplemental receipts are the evidence packet's own receipts; a "
            "second copy would be a second identity for the same bytes"
        )


class FrameManifest(KnowledgeModel):
    """One immutable ready revision: its binding, its evidence, its assets and its authoring."""

    schema_: Literal["ar-review-frame-manifest/v2"] = Field(
        default=FRAME_MANIFEST_SCHEMA, alias="schema"
    )
    frame_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    revision: int = Field(ge=1)
    parent_revision: int | None = Field(default=None, ge=1)
    binding: FrameBinding
    focus: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    authoring: FrameAuthoring
    created_at: str = Field(min_length=1, max_length=64)
    entrypoint: FrameEntrypoint
    assets: tuple[FrameAsset, ...]
    artifact_digest: str = Field(pattern=SHA256_PATTERN)
    # The admitted R102 evidence, durable with the revision (rule 2, rule 9).
    evidence: EvidencePacket
    supplemental: tuple[SupplementalReceipt, ...] = ()
    asset_ancestry: tuple[int, ...] = ()
    # The page's parts and their claims, derived from the page's own document at publication. An
    # empty map is a page without parts: it is one part, and its staleness is whole-page.
    part_map: tuple[FramePart, ...] = ()

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _manifest_is_internally_true(self) -> FrameManifest:
        paths = [asset.path for asset in self.assets]
        if len(paths) != len(set(paths)):
            raise ValueError("an asset path appears once in one revision")
        if self.entrypoint.path not in paths:
            raise ValueError("the entrypoint is one of the revision's own assets")
        if self.parent_revision is not None and self.parent_revision >= self.revision:
            raise ValueError("a parent revision is older than the revision that names it")
        if self.artifact_digest != artifacts_digest(self.assets):
            raise ValueError(
                "the artifact digest and the asset list disagree: the digest verifies these bytes "
                "and not another revision's"
            )
        if self.revision in self.asset_ancestry:
            raise ValueError("a revision is not its own asset ancestor")
        if self.evidence.comparison.task_id != self.binding.task_id:
            raise ValueError("the evidence packet and the binding describe different tasks")
        if self.evidence.comparison.comparison_number != self.binding.comparison_number:
            raise ValueError("the evidence packet describes another comparison than the binding")
        _parts_are_true(self.part_map, self.evidence)
        _supplemental_is_true(self.supplemental, self.evidence.supplemental)
        return self

    def evidence_digest(self) -> str:
        """The digest of the admitted packet's canonical bytes, for a receipt or a comparison."""

        payload = json.dumps(
            json.loads(self.evidence.model_dump_json(by_alias=True)),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()


class FramePointer(KnowledgeModel):
    """The one atomic ready pointer of a lineage: which revision is ready now."""

    schema_: Literal["ar-review-frame-pointer/v1"] = Field(
        default=FRAME_POINTER_SCHEMA, alias="schema"
    )
    frame_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    revision: int = Field(ge=1)
    manifest_digest: str = Field(pattern=SHA256_PATTERN)
    published_at: str = Field(min_length=1, max_length=64)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class FrameDraft(KnowledgeModel):
    """One authoring attempt inside a lineage: its identity, state and lease (rules 9, 12).

    A draft is ``active`` while its authoring turn holds it, and ``failed`` or ``cancelled`` when
    that turn ends without a publication. Its lease is the wall-clock instant after which an
    abandoned draft may be reclaimed; a live draft is never removed, and a draft that published
    keeps its outcome so a retry can be told apart from a new attempt.
    """

    schema_: Literal["ar-review-frame-draft/v1"] = Field(default=FRAME_DRAFT_SCHEMA, alias="schema")
    frame_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    request_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    state: Literal["active", "published", "failed", "cancelled"]
    authoring: FrameAuthoring
    working_directory: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    assets: tuple[FrameAsset, ...]
    focus: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    updated_at: str = Field(min_length=1, max_length=64)
    lease_until: str = Field(min_length=1, max_length=64)
    published_revision: int | None = Field(default=None, ge=1)
    detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _state_carries_its_outcome(self) -> FrameDraft:
        if self.state == "published" and self.published_revision is None:
            raise ValueError("a published draft names the revision it published")
        if self.state in {"failed", "cancelled"} and not (self.detail or "").strip():
            raise ValueError(f"a {self.state} draft states its outcome")
        return self


class FrameReclamation(KnowledgeModel):
    """One durable reclamation record: what was removed, why, and whether it succeeded (rule 10).

    It is written before the bytes are removed (``state: intent``) and rewritten with the outcome
    (``deleted``, ``partial`` or ``failed``), so a reopen after a crash or a retry can distinguish a
    reclaimed revision from one that never existed, and can say which removal failed.
    """

    schema_: Literal["ar-review-frame-reclamation/v1"] = Field(
        default=FRAME_RECLAMATION_SCHEMA, alias="schema"
    )
    frame_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    state: Literal["intent", "deleted", "partial", "failed"]
    recorded_at: str = Field(min_length=1, max_length=64)
    revisions: tuple[int, ...] = ()
    drafts: tuple[str, ...] = ()
    manifest_digests: tuple[str, ...] = ()
    reason: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    failures: tuple[str, ...] = ()

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _outcome_matches_its_failures(self) -> FrameReclamation:
        if self.state in {"partial", "failed"} and not self.failures:
            raise ValueError("a partial or failed reclamation names what it could not remove")
        return self


class FrameOpening(KnowledgeModel):
    """What reopening one ready revision reports, and whether it is usable (rule 4)."""

    state: Literal["ready", "unavailable"]
    frame_id: str
    revision: int | None = Field(default=None, ge=1)
    manifest: FrameManifest | None = None
    asset_paths: tuple[str, ...] = ()
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _unavailable_is_named(self) -> FrameOpening:
        if self.state == "unavailable" and self.manifest is not None:
            raise ValueError("an unavailable opening names no manifest as if it opened one")
        return self


class FrameStalePart(KnowledgeModel):
    """One stale part of a retained page, with the title a reader sees and why it is stale.

    MIK-R187 rule 8 makes staleness per part; this is the shape the answer carries so the trusted
    Explanation view can list a part without reading the page again. The reasons name the basis that
    changed and how, so a reader can check the claim rather than take the word "stale".
    """

    part: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    reasons: tuple[str, ...] = ()


class FrameStaleness(KnowledgeModel):
    """Whether a retained revision still describes the selected comparison (rule 5)."""

    state: Literal["current", "stale", "unavailable"]
    frame_id: str
    revision: int = Field(ge=1)
    retained: FrameBinding | None = None
    selected: FrameBinding | None = None
    changed: tuple[str, ...] = ()
    supplemental_state: Literal["current", "changed", "unread"] = "unread"
    regeneration_offered: bool = False
    # The per-part answer (MIK-R187 rule 8). Empty means no part is stale, or that this answer is
    # the whole-page one an older revision or an older caller gives: `changed` names the sides then.
    stale_parts: tuple[FrameStalePart, ...] = ()
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _stale_names_both_sides(self) -> FrameStaleness:
        if self.state == "stale" and not (self.retained and self.selected and self.changed):
            raise ValueError(
                "a stale revision shows the identity it retains and the identity now selected, "
                "and which sides moved"
            )
        return self

    @model_validator(mode="after")
    def _only_a_stale_revision_names_stale_parts(self) -> FrameStaleness:
        if self.stale_parts and self.state != "stale":
            raise ValueError(
                "a revision names stale parts only when it is stale; a current one has none"
            )
        return self


class FrameRegeneration(KnowledgeModel):
    """The starting artifact one explicit regeneration request hands an authoring attempt (rule 6)."""

    state: Literal["ready", "refused"]
    frame_id: str
    stale_revision: int | None = Field(default=None, ge=1)
    selected: FrameBinding | None = None
    retained: FrameBinding | None = None
    entrypoint: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    assets: tuple[FrameAsset, ...] = ()
    evidence_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    supplemental: tuple[SupplementalReceipt, ...] = ()
    asset_ancestry: tuple[int, ...] = ()
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
