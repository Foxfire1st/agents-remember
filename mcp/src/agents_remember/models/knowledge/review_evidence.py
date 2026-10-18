"""An explanation's evidence packet: one existing comparison, its collections and its claim bases.

MIK-R102 fixes what a canvas explanation is *given* and what it may *say*, and nothing about how a
presenter works. This module is the shape of that input and output, so every rule of the packet has
one place it can be read and refused:

* **the comparison is the existing one** (:class:`EvidenceComparisonBinding`): the canonical
  repository, task and leaf, the comparison number and owner, each code and memory repository/tree
  pair, the converted-base inputs where conversion applied, and -- stated apart -- which memory-base
  tree is *stored* and which one was *compared*. Nothing here makes a comparison; it names one.
* **every collection states its own producer, population and read state**
  (:class:`EvidenceCollection`): a collection preparation skipped is ``not-selected`` or
  ``not-read`` with its reason, and a measured empty population is ``measured-empty`` -- three
  different statements that a bare empty list would blur into one.
* **every claim has a basis and a classification** (:class:`EvidenceClaim`): a recorded fact, a
  mechanical signal and an authored judgment each need an inspectable basis that was actually read;
  a presenter inference needs its author and its supporting inputs; an unresolved question keeps its
  limit instead of borrowing an authority it has not earned.
* **scope, omissions and counted populations travel with the claims** (:class:`EvidenceMap`): a
  count states its denominator, filters and limits, and unassessed items are never counted as passed.

The packet is a value: it grants no write authority, starts nothing, and changes no comparison,
judgment, gate or task record (rule 13).
"""

from __future__ import annotations

from typing import Any, Literal

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

__all__ = [
    "CLAIM_CLASSIFICATIONS",
    "EVIDENCE_COLLECTIONS",
    "EVIDENCE_PACKET_SCHEMA",
    "ClaimBasis",
    "ClaimMap",
    "CoverageCount",
    "EvidenceClaim",
    "EvidenceCodeSide",
    "EvidenceCollection",
    "EvidenceComparisonBinding",
    "EvidenceConvertedBase",
    "EvidenceLimitMeasurement",
    "EvidenceLimits",
    "EvidenceMap",
    "EvidenceMemorySide",
    "EvidenceOmission",
    "EvidencePacket",
    "SupplementalReceipt",
    "packet_errors",
]

EVIDENCE_PACKET_SCHEMA: Literal["ar-review-evidence-packet/v1"] = "ar-review-evidence-packet/v1"

# The collections rule 2 names. All of them appear in a packet, each with its own state: a
# collection preparation did not build is named as not selected, never left out and never empty.
EVIDENCE_COLLECTIONS: tuple[str, ...] = (
    "subject-wording",
    "conditions-and-exclusions",
    "family-guarantees-and-membership",
    "recorded-effects",
    "judgments",
    "proofs",
    "currentness",
    "worklist",
    "source-inventory-and-linked-locations",
)

# Rule 12's states. They are deliberately more than "present / absent": a consumer acts on the
# difference between a measured absence, a read that was not made and a population that was cut.
EvidenceState = Literal[
    "present",
    "measured-empty",
    "absent",
    "not-read",
    "not-selected",
    "unresolved",
    "partial",
    "truncated",
    "unavailable",
    "unexamined",
]

# States that must carry a reason. Each of them says something the packet could not measure, and a
# reasonless one would read as a quiet zero.
_REASONED_STATES: frozenset[str] = frozenset(
    {
        "absent",
        "not-read",
        "not-selected",
        "unresolved",
        "partial",
        "truncated",
        "unavailable",
        "unexamined",
    }
)

ClaimClassification = Literal[
    "recorded-fact",
    "mechanical-signal",
    "authored-judgment",
    "presenter-inference",
    "unresolved-question",
    "unavailable-evidence",
]
CLAIM_CLASSIFICATIONS: tuple[str, ...] = (
    "recorded-fact",
    "mechanical-signal",
    "authored-judgment",
    "presenter-inference",
    "unresolved-question",
    "unavailable-evidence",
)

# The classifications that assert something was established by an owner's record or measurement.
# Without a basis that was actually read they would be an interpretation wearing an authority.
_BASIS_REQUIRED: frozenset[str] = frozenset(
    {"recorded-fact", "mechanical-signal", "authored-judgment"}
)

OmissionKind = Literal[
    "condition",
    "exclusion",
    "exception",
    "unexamined-sibling",
    "unresolved-question",
    "contradictory-judgment",
    "unavailable-history",
    "partial-index",
    "truncated-read",
    "not-selected-collection",
]

EvidenceSide = Literal["code-base", "code-candidate", "memory-base", "memory-candidate"]
KindOfBasis = Literal[
    "comparison-field",
    "tree-blob",
    "knowledge-record",
    "judgment-row",
    "proof-entry",
    "history-event",
    "worklist-row",
    "supplemental-receipt",
]
ExecutionBasis = Literal["established", "claimed", "none"]
_ALL_BASIS_KINDS: tuple[str, ...] = (
    "comparison-field",
    "tree-blob",
    "knowledge-record",
    "judgment-row",
    "proof-entry",
    "history-event",
    "worklist-row",
    "supplemental-receipt",
)
# Which basis kinds can establish a classification. A proof entry only establishes anything when an
# execution result was admitted; a mechanical linkage cannot establish an authored judgment; a
# judgment row is the only thing that establishes one (rule 7, rule 9).
_BASIS_KINDS: dict[str, frozenset[str]] = {
    "recorded-fact": frozenset(
        {
            "tree-blob",
            "knowledge-record",
            "comparison-field",
            "worklist-row",
            "history-event",
            "proof-entry",
            "supplemental-receipt",
        }
    ),
    "mechanical-signal": frozenset({"tree-blob", "comparison-field", "worklist-row"}),
    "authored-judgment": frozenset({"judgment-row", "history-event"}),
    "presenter-inference": frozenset(_ALL_BASIS_KINDS),
    "unresolved-question": frozenset(_ALL_BASIS_KINDS),
    "unavailable-evidence": frozenset(_ALL_BASIS_KINDS),
}

BasisKind = Literal[
    "comparison-field",
    "tree-blob",
    "knowledge-record",
    "judgment-row",
    "proof-entry",
    "history-event",
    "worklist-row",
    "supplemental-receipt",
]


class EvidenceLimits(KnowledgeModel):
    """The finite limits one packet and its reads declare (rule 4).

    ``measurement`` is what keeps a new number from becoming an implementation default: rule 4 makes
    an unmeasured limit an approval gap, and :attr:`approval_gap` is the field a consumer reads to
    say so instead of presenting the bound as settled.
    """

    bytes_per_read: int = Field(gt=0)
    paths_per_packet: int = Field(gt=0)
    records_per_collection: int = Field(gt=0)
    relationship_depth: int = Field(ge=0)
    pages_per_read: int = Field(gt=0)
    measurement: EvidenceLimitMeasurement | None = None

    @property
    def approval_gap(self) -> bool:
        """Whether these numbers are unmeasured and therefore still an approval gap."""

        return self.measurement is None


class EvidenceLimitMeasurement(KnowledgeModel):
    """What the limits were measured against (rule 4, expected evidence 8).

    At least two input sizes, so the numbers are a measured tradeoff rather than one reading of one
    file. ``elapsed_ms`` is the read cost at the larger input.
    """

    inputs: tuple[tuple[str, int], ...]
    observed_bytes: int = Field(ge=0)
    observed_paths: int = Field(ge=0)
    observed_records: int = Field(ge=0)
    observed_depth: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _two_sizes(self) -> EvidenceLimitMeasurement:
        if len(self.inputs) < 2:
            raise ValueError("a limit measurement states at least two input sizes")
        for name, size in self.inputs:
            if not name.strip() or size < 0:
                raise ValueError("a measured input states its name and a non-negative size")
        return self


class EvidenceCodeSide(KnowledgeModel):
    """One code side of the comparison as the comparison owner reports it."""

    side: Literal["base", "candidate"]
    repository: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    tree: str = Field(pattern=GIT_OBJECT_PATTERN)
    commit: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    state: Literal["available", "unavailable-history"]
    detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _unavailable_is_named(self) -> EvidenceCodeSide:
        if self.state == "unavailable-history" and not (self.detail or "").strip():
            raise ValueError("an unavailable-history code side names why Git no longer has it")
        return self


class EvidenceMemorySide(KnowledgeModel):
    """One memory side: its tree, its state and its derived index's state."""

    side: Literal["before", "after"]
    repository: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    tree: str = Field(pattern=GIT_OBJECT_PATTERN)
    commit: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    state: Literal["available", "unavailable-history", "legacy-unavailable"]
    index_state: Literal["complete", "partial"] | None = None
    detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _unread_side_is_named(self) -> EvidenceMemorySide:
        if self.state != "available" and not (self.detail or "").strip():
            raise ValueError("a memory side that is not available names why it could not be read")
        if self.index_state == "partial" and not (self.detail or "").strip():
            raise ValueError("a partial index is named beside the side it belongs to")
        return self


class EvidenceConvertedBase(KnowledgeModel):
    """The conversion an unconverted memory base was read as (MIK-R24 rule 7)."""

    commit: str = Field(pattern=GIT_OBJECT_PATTERN)
    version: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    code_commit: str = Field(pattern=GIT_OBJECT_PATTERN)
    tree: str = Field(pattern=GIT_OBJECT_PATTERN)


class EvidenceComparisonBinding(KnowledgeModel):
    """The one existing comparison this packet describes (rule 1).

    ``stored_memory_base_tree`` is the memory-base tree the record holds; ``compared_memory_base_tree``
    is the tree the review actually compared, which differs exactly when a conversion applied. Both
    are stated because a consumer that only saw one of them would read a converted comparison as a
    comparison of the stored tree.
    """

    schema_: Literal["ar-review-evidence-packet/v1"] = Field(
        default=EVIDENCE_PACKET_SCHEMA, alias="schema"
    )
    comparison_owner: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    repository_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    task_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    leaf_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    comparison_number: int = Field(ge=0)
    comparison_reference: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    code_base: EvidenceCodeSide
    code_candidate: EvidenceCodeSide
    memory_base: EvidenceMemorySide
    memory_candidate: EvidenceMemorySide
    converted_base: EvidenceConvertedBase | None = None
    stored_memory_base_tree: str = Field(pattern=GIT_OBJECT_PATTERN)
    compared_memory_base_tree: str = Field(pattern=GIT_OBJECT_PATTERN)
    subject_binding_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    recorded_at: str = Field(min_length=1, max_length=64)

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _sides_are_the_ones_named(self) -> EvidenceComparisonBinding:
        if (self.code_base.side, self.code_candidate.side) != ("base", "candidate"):
            raise ValueError("the two code sides keep their own names")
        if (self.memory_base.side, self.memory_candidate.side) != ("before", "after"):
            raise ValueError("the two memory sides keep their own names")
        if self.memory_base.tree != self.stored_memory_base_tree:
            raise ValueError("the stored memory-base tree is the one the record holds")
        compared = (
            self.converted_base.tree if self.converted_base is not None else self.memory_base.tree
        )
        if compared != self.compared_memory_base_tree:
            raise ValueError(
                "the compared memory-base tree is the converted tree when a conversion applied, "
                "and the stored tree otherwise"
            )
        return self


class SupplementalReceipt(KnowledgeModel):
    """One admitted source outside the four trees, bound to the bytes that were examined (rule 6).

    A receipt binds bytes, not truth and not currentness: a mutable document is not made immutable by
    naming its path, so a later read states new bytes as a new input.
    """

    receipt_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    canonical_source: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    producer: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    authority: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    byte_size: int = Field(default=0, ge=0)
    read_state: Literal["present", "changed", "unavailable"]
    capture_identity: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    source_version: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    establishes: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    limitation: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    supplemental: Literal[True] = True

    @model_validator(mode="after")
    def _receipt_states_its_limits(self) -> SupplementalReceipt:
        if self.read_state != "present" and not (self.limitation or "").strip():
            raise ValueError(
                "a receipt that is not present states why and what stays unestablished"
            )
        if self.read_state == "unavailable" and self.digest is not None:
            raise ValueError("an unavailable source has no examined bytes to digest")
        if self.read_state != "unavailable" and self.digest is None:
            raise ValueError("a receipt of examined bytes states their digest")
        if self.read_state == "present" and self.byte_size == 0 and not self.limitation:
            raise ValueError("a zero-byte receipt states what it establishes")
        return self


class EvidenceCollection(KnowledgeModel):
    """One supplied collection: its producer, its population and the state it was read to (rule 2)."""

    name: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    producer: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    population: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    state: EvidenceState
    count: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    items: tuple[dict[str, Any], ...] = ()
    truncated: bool = False
    continuation: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    limit: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)

    @model_validator(mode="after")
    def _state_and_count_agree(self) -> EvidenceCollection:
        if self.state in _REASONED_STATES and not (self.reason or "").strip():
            raise ValueError(f"a collection in state {self.state!r} states its reason")
        if self.state == "measured-empty" and self.count != 0:
            raise ValueError("a measured-empty collection has a count of zero")
        if self.state == "present" and not self.count:
            raise ValueError("a present collection states a positive count")
        if self.state not in {"present", "measured-empty"} and self.count:
            raise ValueError(
                f"a collection in state {self.state!r} states no count: an unmeasured "
                "population is not a zero"
            )
        if self.count is not None and len(self.items) > self.count:
            raise ValueError("a collection cannot carry more items than its stated count")
        if self.truncated and not self.state == "truncated":
            raise ValueError("a truncated collection says so in its state")
        if self.state == "truncated" and not self.continuation:
            raise ValueError("a truncated collection names its continuation")
        return self


class ClaimBasis(KnowledgeModel):
    """One inspected basis of a claim: its typed identity, its read state and its limit.

    ``reference`` is the human address a presenter shows; the typed fields are what a mechanised
    check resolves. A ``tree-blob`` names the side, tree and path it was read at; a
    ``knowledge-record`` names the record's id, revision and file; a ``judgment-row`` names the
    row's id, attempt and subject; a ``proof-entry`` names the entry and whether an execution result
    was established; a ``supplemental-receipt`` names the receipt. A basis that carries only text is
    not resolvable, and a claim that rests on it cannot wear a recorded authority (rule 7).
    """

    kind: KindOfBasis
    reference: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    state: EvidenceState
    limitation: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    # The identity the reference is checked against.
    comparison_number: int | None = Field(default=None, ge=0)
    side: EvidenceSide | None = None
    tree: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    path: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    record_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    record_revision: int | None = Field(default=None, ge=1)
    row_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    attempt: int | None = Field(default=None, ge=1)
    proof_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    execution: ExecutionBasis | None = None
    receipt_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)

    @model_validator(mode="after")
    def _the_kind_carries_its_own_identity(self) -> ClaimBasis:
        if self.state != "present" and not (self.limitation or "").strip():
            raise ValueError("a basis that was not read states its limitation")
        if self.start_line and self.end_line and self.end_line < self.start_line:
            raise ValueError("a basis' range ends before it starts")
        missing = _missing_identity(self)
        if missing:
            raise ValueError(
                f"a {self.kind} basis names {', '.join(missing)}; a reference without a typed "
                "identity cannot be resolved against the comparison"
            )
        return self


def _missing_identity(basis: ClaimBasis) -> list[str]:
    """The typed fields one basis kind requires and does not carry."""

    required: dict[str, tuple[str, ...]] = {
        "comparison-field": (),
        "tree-blob": ("side", "tree", "path"),
        "knowledge-record": ("record_id", "record_revision", "path"),
        "judgment-row": ("record_id", "row_id", "attempt"),
        "proof-entry": ("proof_id", "record_id", "execution"),
        "history-event": ("path", "row_id"),
        "worklist-row": (),
        "supplemental-receipt": ("receipt_id",),
    }
    return [name for name in required[basis.kind] if getattr(basis, name) in (None, "")]


# The basis kinds that report something the packet inspected rather than the claim's own prose.
_MECHANICAL_KINDS = frozenset(
    {
        "tree-blob",
        "knowledge-record",
        "judgment-row",
        "proof-entry",
        "history-event",
        "worklist-row",
        "supplemental-receipt",
    }
)


class EvidenceClaim(KnowledgeModel):
    """One explanatory claim with its own local identity, classification and bases (rule 7)."""

    claim_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    authoring_revision: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    classification: ClaimClassification
    subject: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    applicability: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    bases: tuple[ClaimBasis, ...] = ()
    limit: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _classification_earns_its_bases(self) -> EvidenceClaim:
        """A recorded authority is earned by the kind of basis it rests on (rule 7).

        A present label is not enough: a ``recorded-fact`` rests on a mechanical basis that the
        packet can resolve, an ``authored-judgment`` on a judgment row, and a proof entry only when
        its execution result was established. A basis type that cannot support the label is refused
        here, before the claim can be shown at all.
        """

        present = [basis for basis in self.bases if basis.state == "present"]
        for basis in present:
            if basis.kind == "proof-entry" and basis.execution != "established":
                raise ValueError(
                    f"claim {self.claim_id!r} rests on proof {basis.proof_id!r} whose execution "
                    "result was not established; an unexecuted proof supports no recorded claim"
                )
        if self.classification == "recorded-fact" and not any(
            basis.kind in _MECHANICAL_KINDS for basis in present
        ):
            raise ValueError(
                f"a recorded-fact claim needs a present mechanical basis "
                f"({', '.join(sorted(_MECHANICAL_KINDS))}); a comparison field or an unread "
                "reference is not an inspected fact"
            )
        if self.classification == "authored-judgment" and not any(
            basis.kind == "judgment-row" for basis in present
        ):
            raise ValueError(
                f"an authored-judgment claim {self.claim_id!r} names the judgment row it rests on; "
                "another kind of basis does not carry an author's judgment"
            )
        if self.classification in _BASIS_REQUIRED and not present:
            raise ValueError(
                f"a {self.classification} claim needs an inspected basis; unsupported "
                "interpretation cannot wear a recorded authority"
            )
        if self.classification == "presenter-inference" and not self.bases:
            raise ValueError("a presenter inference names its supporting inputs")
        if (
            self.classification in {"unresolved-question", "unavailable-evidence"}
            and not (self.limit or "").strip()
        ):
            raise ValueError(f"a {self.classification} claim states its limit")
        return self


class ClaimMap(KnowledgeModel):
    """The packet's claims, each with a stable local identity (rule 7)."""

    claims: tuple[EvidenceClaim, ...] = ()

    @model_validator(mode="after")
    def _one_entry_per_identity(self) -> ClaimMap:
        seen: set[str] = set()
        for claim in self.claims:
            if claim.claim_id in seen:
                raise ValueError(f"the claim id {claim.claim_id!r} is used twice")
            seen.add(claim.claim_id)
        return self


class EvidenceOmission(KnowledgeModel):
    """One relevant thing the explanation does not cover, and what kind of gap it is (rule 10)."""

    item: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    kind: OmissionKind
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    witness: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)


class CoverageCount(KnowledgeModel):
    """One quantitative claim with its own denominator, filters and limits (rule 10).

    ``unassessed`` is the part of the population nothing measured; it is subtracted before
    ``counted`` can be read as a share of the population, so unknown items are never counted as
    passed.
    """

    subject: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    counted: int = Field(ge=0)
    population: int = Field(ge=0)
    denominator: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    filters: tuple[str, ...] = ()
    limits: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    unassessed: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _unknowns_are_not_passed(self) -> CoverageCount:
        if self.counted + self.unassessed > self.population:
            raise ValueError(
                "a count's passed plus unassessed part cannot exceed its population, and an "
                "unassessed item is never counted as passed"
            )
        return self


class EvidenceMap(KnowledgeModel):
    """What the explanation covers, what it omits and what its counts mean (rule 10)."""

    included: tuple[str, ...] = ()
    omissions: tuple[EvidenceOmission, ...] = ()
    counts: tuple[CoverageCount, ...] = ()
    edge_meanings: tuple[tuple[str, str], ...] = ()
    coverage: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _edge_meanings_are_distinct(self) -> EvidenceMap:
        names = [name for name, _meaning in self.edge_meanings]
        if len(names) != len(set(names)):
            raise ValueError("each graph edge meaning is named once")
        return self


class EvidencePacket(KnowledgeModel):
    """One comparison, one question, every named collection and every claim's basis (rules 1-13)."""

    schema_: Literal["ar-review-evidence-packet/v1"] = Field(
        default=EVIDENCE_PACKET_SCHEMA, alias="schema"
    )
    comparison: EvidenceComparisonBinding
    scope: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    question: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    collections: tuple[EvidenceCollection, ...]
    supplemental: tuple[SupplementalReceipt, ...] = ()
    claims: ClaimMap = ClaimMap()
    evidence_map: EvidenceMap
    limits: EvidenceLimits
    limitations: tuple[str, ...] = ()

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @model_validator(mode="after")
    def _every_collection_is_accounted_for(self) -> EvidencePacket:
        names = [collection.name for collection in self.collections]
        if len(names) != len(set(names)):
            raise ValueError("each collection name appears once")
        missing = [name for name in EVIDENCE_COLLECTIONS if name not in names]
        if missing:
            raise ValueError(
                "a collection the packet does not carry is named not-selected or not-read with "
                f"its reason; missing: {', '.join(missing)}"
            )
        for collection in self.collections:
            if (
                collection.count is not None
                and collection.count > self.limits.records_per_collection
            ):
                raise ValueError(
                    f"collection {collection.name!r} holds {collection.count} record(s), beyond the "
                    f"admitted {self.limits.records_per_collection} per collection"
                )
        paths = {
            str(item["path"])
            for collection in self.collections
            for item in collection.items
            if isinstance(item.get("path"), str)
        } | {basis.path for claim in self.claims.claims for basis in claim.bases if basis.path}
        if len(paths) > self.limits.paths_per_packet:
            raise ValueError(
                f"the packet names {len(paths)} distinct path(s), beyond the admitted "
                f"{self.limits.paths_per_packet} per packet"
            )
        receipts = {receipt.receipt_id for receipt in self.supplemental}
        for claim in self.claims.claims:
            for basis in claim.bases:
                if basis.kind == "supplemental-receipt" and basis.reference not in receipts:
                    raise ValueError(
                        f"claim {claim.claim_id!r} cites the unknown receipt {basis.reference!r}"
                    )
        return self

    def collection(self, name: str) -> EvidenceCollection | None:
        """One named collection, or ``None`` when the packet does not carry it."""

        return next((entry for entry in self.collections if entry.name == name), None)


def packet_errors(packet: EvidencePacket) -> tuple[str, ...]:
    """Everything this packet still owes a reader, as sentences (empty when it owes nothing).

    The models refuse the individual lies. These are the *cross-entry* ones: a collection left
    silently out of the evidence map, an omission the map never names, and a limit presented as
    settled while it is still an approval gap.
    """

    problems: list[str] = []
    omitted = {omission.item for omission in packet.evidence_map.omissions}
    for collection in packet.collections:
        if collection.state in {"not-selected", "not-read"} and collection.name not in omitted:
            problems.append(
                f"collection {collection.name!r} is {collection.state} and the evidence map does "
                "not name it as an omission"
            )
    if packet.limits.approval_gap:
        problems.append(
            "the packet's read limits are unmeasured, so they remain an approval gap rather than "
            "an admitted bound"
        )
    for count in packet.evidence_map.counts:
        if count.population == 0 and count.counted == 0 and "empty" not in count.limits.lower():
            problems.append(
                f"count {count.subject!r} has an empty population and does not state that it was "
                "measured empty"
            )
    return tuple(problems)
