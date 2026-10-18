"""The receipts one bounded evidence read answers with (MIK-R102 rules 3, 4, 5, 7, 8 and 12).

A receipt is the whole answer, and it separates the facts a reader has to tell apart:

* the **side and tree actually read** -- for a memory base with a conversion applied that is the
  *effective* converted tree, with the stored tree named beside it (rule 1, rule 3);
* the **object** the entry names, its full size where measured, and the byte window that was
  examined and returned (rule 4);
* the **range** asked for and the range returned, the continuation that reaches the rest, and
  whether the remainder is known or merely unexamined (rule 4, rule 12);
* the **state**: ``present``, ``present`` with zero bytes (measured empty), ``absent`` for a range
  the scanned object really does not hold, ``binary``/``symlink``/``submodule`` for what the entry
  is, ``unavailable`` for a lookup that did not answer or a tree Git no longer has, and ``refused``
  for an operand this surface will not accept (rule 3, rule 12).

Nothing here claims that an unread remainder is empty, that a truncated window is the whole object,
or that a stored memory tree is the converted one that was compared.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

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
    "EVIDENCE_SIDES",
    "ClaimResolution",
    "EvidenceReadReceipt",
    "EvidenceRelationshipRead",
    "RelationshipEdge",
    "SupplementalRead",
]

# The four operands a bound read may name. A code side is a code tree; a memory side is a memory
# tree. Nothing else -- a branch, HEAD, a working checkout, another comparison -- is an operand.
EVIDENCE_SIDES: tuple[str, ...] = (
    "code-base",
    "code-candidate",
    "memory-base",
    "memory-candidate",
)
_MEMORY_SIDES: frozenset[str] = frozenset({"memory-base", "memory-candidate"})

EvidenceReadState = Literal[
    "present",
    "absent",
    "binary",
    "symlink",
    "submodule",
    "truncated",
    "unavailable",
    "refused",
]

# Whether what the receipt did not return is known or merely unexamined. A receipt that cannot say
# which of the two it is has not measured the remainder and does not pretend to.
RemainderState = Literal["known", "unknown", "none"]


class EvidenceReadReceipt(KnowledgeModel):
    """One bounded read of one bound side: what came back, and every limit that shaped it."""

    state: EvidenceReadState
    side: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    comparison_number: int | None = Field(default=None, ge=0)
    comparison_reference: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    repository: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    tree: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    # The stored tree, present only when the effective operand is a converted base (rule 1).
    stored_tree: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    path: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    object_id: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    object_size: int | None = Field(default=None, ge=0)
    examined_bytes: int = Field(default=0, ge=0)
    returned_bytes: int = Field(default=0, ge=0)
    requested_range: tuple[int, int] | None = None
    returned_range: tuple[int, int] | None = None
    start_byte: int = Field(default=0, ge=0)
    next_byte: int | None = Field(default=None, ge=0)
    page: int = Field(default=0, ge=0)
    pages: int | None = Field(default=None, ge=0)
    continuation: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    remaining_state: RemainderState = "none"
    remaining_lines: int | None = Field(default=None, ge=0)
    limit: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    text: str | None = None
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    refusal_code: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)

    @model_validator(mode="after")
    def _a_refusal_names_its_reason(self) -> EvidenceReadReceipt:
        if self.state == "refused" and not (self.refusal_code or "").strip():
            raise ValueError("a refused read names the refusal it earned")
        if self.state == "truncated" and not self.continuation:
            raise ValueError("a truncated read names the continuation that reaches the rest")
        if self.state == "truncated" and self.remaining_state == "none":
            raise ValueError("a truncated read states whether the remainder is known or unexamined")
        if self.remaining_state == "known" and self.remaining_lines is None:
            raise ValueError("a known remainder states how many lines are left")
        if self.returned_bytes and self.next_byte is None:
            raise ValueError("a read that returned bytes states where the next read starts")
        return self

    @property
    def source_read(self) -> bool:
        """Whether this receipt is evidence of a bound source at all (a refusal never is)."""

        return self.state in {"present", "truncated", "symlink", "binary", "submodule"}


class RelationshipEdge(KnowledgeModel):
    """One recorded relationship at a bound memory side, from its own owner's semantics (rule 5).

    ``relation`` is the owner's own word for the edge (``members`` on a family, ``realizes`` on a
    sidecar, ``links:<relation>`` on a record, ``history`` on a judgment row). ``direction`` says
    whether the edge was recorded on this record or found by looking at the other owner that holds
    it, so an inverse edge is never presented as a field this record carries. The target's revision,
    origin leaf and attempt travel where the owner records them; a target that was not found is
    ``absent`` while a lookup that did not answer is ``unavailable``.
    """

    record_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    relation: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    direction: Literal["owns", "recorded-by"]
    path: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    depth: int = Field(ge=0)
    state: Literal["present", "absent", "unavailable", "unexamined", "invalid"]
    record_revision: int | None = Field(default=None, ge=1)
    origin_leaf: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    attempt: int | None = Field(default=None, ge=1)
    row_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    # What the target itself says when the walk resolved it at this side's tree, rather than trusting
    # the filename that named it: its own revision, origin leaf and lookup state.
    target_revision: int | None = Field(default=None, ge=1)
    target_origin_leaf: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    lookup_state: Literal["resolved", "absent", "unreadable", "not-followed"] = "not-followed"
    detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _unread_edge_is_named(self) -> RelationshipEdge:
        if self.state != "present" and not (self.detail or "").strip():
            raise ValueError("an edge that was not resolved states why")
        return self


class EvidenceRelationshipRead(KnowledgeModel):
    """A bounded relationship read from one memory record, with its depth and its cut."""

    state: Literal["records", "refused", "unavailable"]
    side: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    comparison_number: int | None = Field(default=None, ge=0)
    comparison_reference: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    repository: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    tree: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    stored_tree: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    from_path: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    from_record: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    from_revision: int | None = Field(default=None, ge=1)
    edges: tuple[RelationshipEdge, ...] = ()
    depth: int = Field(default=0, ge=0)
    listed_paths: int = Field(default=0, ge=0)
    listing_truncated: bool = False
    listing_cursor: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    examined_bytes: int = Field(default=0, ge=0)
    continuation: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    resolved_targets: int = Field(default=0, ge=0)
    limit: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    refusal_code: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)

    @model_validator(mode="after")
    def _refusal_is_named(self) -> EvidenceRelationshipRead:
        if self.state == "refused" and not (self.refusal_code or "").strip():
            raise ValueError("a refused relationship read names the refusal it earned")
        if self.listing_truncated and not self.continuation:
            raise ValueError("a truncated listing names where the same-depth continuation starts")
        return self

    @property
    def memory_side(self) -> bool:
        """Whether the read named a memory side; a code side holds no knowledge relationships."""

        return self.side in _MEMORY_SIDES


class ClaimResolution(KnowledgeModel):
    """Whether every basis of one claim resolves against the packet it belongs to (rule 7, rule 8).

    ``resolvable`` is mechanical: each basis names an object the packet's own comparison,
    collections or receipts actually hold, and that object belongs to the packet's comparison. It
    never claims the claim's prose is true or faithful.
    """

    claim_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    resolvable: bool
    checked: int = Field(ge=0)
    unresolved: tuple[str, ...] = ()
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _unresolved_is_named(self) -> ClaimResolution:
        if not self.resolvable and not self.unresolved:
            raise ValueError("an unresolved claim names the bases that did not resolve")
        return self


class SupplementalRead(KnowledgeModel):
    """One supplemental source resolved through its owner: the bytes, or the refusal (rule 6)."""

    state: Literal["present", "refused", "unavailable"]
    canonical_source: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    admitted_root: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    byte_size: int = Field(default=0, ge=0)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    refusal_code: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
