"""Typed direct-input identities for immutable lifecycle evidence.

Evidence records are downstream consumers of semantic and candidate identities.  This
module deliberately models only that one-way relation: it does not select a current
record, infer domain semantics, or place evidence back into topology or task intent.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EVIDENCE_DEPENDENCY_SCHEMA = "ar-evidence-dependencies/v1"
EVIDENCE_DEPENDENCY_VALIDATOR = "evidence-dependency-validator/v1"

EvidenceRecordType = Literal[
    "memory-quality-attestation/v1",
    "route-review/v1",
    "curator-coherence/v1",
    "review-assessment/v1",
    "quality-report/v2",
    "closeout-door/v1",
    "lifecycle-closeout-operation/v3",
    "lifecycle-direct-landing-operation/v3",
    "lifecycle-integration-operation/v3",
]
EvidenceDependencyKind = Literal[
    "admission",
    "candidate-state",
    "code-tree",
    "coherence-record",
    "door-generation",
    "evidence-bytes",
    "gate-certificate",
    "memory-attestation",
    "memory-tree",
    "operation-input",
    "predecessor-record",
    "rail-execution",
    "rail-plan",
    "review-record",
    "scheduling",
    "semantic-topology",
    "task-intent",
    "validator",
]
DigestAlgorithm = Literal["git-object", "sha256"]

MAX_CURATOR_SOURCE_CANDIDATES: Final = 2048
# The assessment collection's own bound. It is deliberately far below the candidate bound: an
# assessment is an authored act a human writes, not a machine-produced row per changed file, so a
# thousand of them on one leaf is already a defect rather than a workload.
MAX_CURATOR_REVIEW_ASSESSMENTS: Final = 256

# MIK-R52 rule 12: the dependency capacity follows from the coherence record's own bounds, so no
# bare number stands in the field below. The coherence record builder
# (``worktrees/integration/closeout/curator_coherence_publication.py::_record``) declares at most:
# its fixed dependencies (code-tree, memory-tree, semantic-topology, task-intent,
# memory-attestation, the memory-quality-report evidence-bytes edge, and at most one
# predecessor-record edge), one evidence-bytes edge for each of at most
# ``MAX_CURATOR_SOURCE_CANDIDATES`` judgments, one review-record edge for each of at most
# ``MAX_CURATOR_REVIEW_ASSESSMENTS`` assessments, and its validator edges. The bound covers every
# record builder whose model bounds its payload, and it is coherence-derived: 7 + 2048 + 256 + 1
# = 2312. The master-scope route review has no bound of its own: its model sets no upper bound on
# its lists, so no cap is imposed on it here — but it is still limited by this shared constant,
# and its refusal names the bound and the count.
_COHERENCE_FIXED_DEPENDENCIES: Final = 7
_COHERENCE_VALIDATOR_DEPENDENCIES: Final = 1
MAX_EVIDENCE_DEPENDENCIES: Final = (
    _COHERENCE_FIXED_DEPENDENCIES
    + MAX_CURATOR_SOURCE_CANDIDATES
    + MAX_CURATOR_REVIEW_ASSESSMENTS
    + _COHERENCE_VALIDATOR_DEPENDENCIES
)


class EvidenceDependencyError(ValueError):
    """One record's direct dependency declaration is incomplete or invalid."""

    def __init__(self, status: str, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"{status}: {detail}")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceDependency(_StrictModel):
    """One named direct input consumed by an evidence record."""

    kind: EvidenceDependencyKind
    name: str = Field(min_length=1, max_length=8192)
    algorithm: DigestAlgorithm
    digest: str = Field(pattern=r"^[0-9a-f]{40,64}$")

    @field_validator("name")
    @classmethod
    def _canonical_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or cleaned != value:
            raise ValueError("evidence dependency name must be canonical nonblank text")
        return cleaned

    @model_validator(mode="after")
    def _algorithm_matches_digest(self) -> Self:
        if self.algorithm == "sha256" and len(self.digest) != 64:
            raise ValueError("sha256 evidence dependencies require a 64-hex digest")
        return self

    @property
    def identity(self) -> tuple[str, str]:
        return self.kind, self.name

    @property
    def sort_key(self) -> tuple[str, str, str, str]:
        return self.kind, self.name, self.algorithm, self.digest


class EvidenceDependencies(_StrictModel):
    """Canonical versioned direct-input declaration for one record type."""

    schemaVersion: Literal["ar-evidence-dependencies/v1"] = EVIDENCE_DEPENDENCY_SCHEMA
    recordType: EvidenceRecordType
    validatorVersion: Literal["evidence-dependency-validator/v1"] = EVIDENCE_DEPENDENCY_VALIDATOR
    edges: tuple[EvidenceDependency, ...] = Field(min_length=1, max_length=MAX_EVIDENCE_DEPENDENCIES)

    @model_validator(mode="after")
    def _edges_are_unique_and_canonical(self) -> Self:
        identities = [edge.identity for edge in self.edges]
        if len(identities) != len(set(identities)):
            raise ValueError("evidence dependency identities must be unique")
        if tuple(sorted(self.edges, key=lambda edge: edge.sort_key)) != self.edges:
            raise ValueError("evidence dependencies must use canonical order")
        return self

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json", by_alias=True)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceDependencyPolicy:
    """One record owner's versioned allowlist of direct dependency kinds."""

    required: frozenset[EvidenceDependencyKind]
    permitted: frozenset[EvidenceDependencyKind]


def _policy(
    *,
    required: Iterable[EvidenceDependencyKind],
    optional: Iterable[EvidenceDependencyKind] = (),
) -> EvidenceDependencyPolicy:
    required_set = frozenset(required)
    return EvidenceDependencyPolicy(
        required=required_set,
        permitted=required_set | frozenset(optional),
    )


EVIDENCE_DEPENDENCY_POLICIES: Mapping[EvidenceRecordType, EvidenceDependencyPolicy] = {
    "memory-quality-attestation/v1": _policy(
        required=(
            "candidate-state",
            "code-tree",
            "memory-tree",
            "evidence-bytes",
            "validator",
        ),
    ),
    "route-review/v1": _policy(
        required=("code-tree", "task-intent", "evidence-bytes", "validator"),
    ),
    "curator-coherence/v1": _policy(
        required=(
            "code-tree",
            "memory-tree",
            "semantic-topology",
            "task-intent",
            "memory-attestation",
            "evidence-bytes",
            "validator",
        ),
        # ``review-record`` is optional and is present exactly when the record stores one or more
        # ``ReviewAssessment`` rows (``KS-R15@v1`` §8.1). The kind already existed in this closed
        # vocabulary (``KS-R15@v1`` §5.1 measures it at ``:45``); what this leaf adds is the
        # permission for the coherence record to use it, which is what lets a reader resolve an
        # assessment from the record without parsing a payload. The record's *own* edge to the
        # assessment is the only direction: the assessment does not declare the record it lives in.
        optional=("predecessor-record", "review-record"),
    ),
    # One ``ReviewAssessment``'s binding to the exact inputs it examined (``KS-R15@v1`` §5.1). The
    # required set is that clause's own enumeration and nothing else: ``candidate-state`` is the exact
    # knowledge snapshot identity, ``code-tree`` and the optional ``memory-tree`` are the source
    # trees, ``semantic-topology`` is the registered-scope manifest, ``task-intent`` is the
    # task/requirement identity pair, ``evidence-bytes`` is the cited bytes, and ``validator`` is the
    # resolver/policy version pair. ``review-record`` is deliberately NOT required here: the edge
    # that carries it is the *coherence record's* edge to the assessment, so requiring it inside the
    # assessment would be a record citing itself. Nothing about the vocabulary is widened --
    # ``evidence-bytes`` and ``review-record`` already existed (``KS-R15@v1`` §5.1) -- so this entry
    # adds one policy and no kind, and the policy is the additive kind of change a new record type is
    # supposed to make here.
    "review-assessment/v1": _policy(
        required=(
            "candidate-state",
            "code-tree",
            "task-intent",
            "semantic-topology",
            "evidence-bytes",
            "validator",
        ),
        optional=("memory-tree", "predecessor-record", "review-record"),
    ),
    "quality-report/v2": _policy(
        required=("code-tree", "rail-execution", "evidence-bytes", "validator"),
        optional=("rail-plan", "admission"),
    ),
    "closeout-door/v1": _policy(
        required=(
            "code-tree",
            "semantic-topology",
            "task-intent",
            "review-record",
            "coherence-record",
            "admission",
            "scheduling",
            "validator",
        ),
        optional=("memory-tree", "predecessor-record"),
    ),
    "lifecycle-closeout-operation/v3": _policy(
        required=(
            "candidate-state",
            "code-tree",
            "task-intent",
            "door-generation",
            "operation-input",
            "rail-plan",
            "validator",
        ),
        optional=("gate-certificate", "predecessor-record"),
    ),
    "lifecycle-direct-landing-operation/v3": _policy(
        required=(
            "candidate-state",
            "code-tree",
            "task-intent",
            "door-generation",
            "operation-input",
            "rail-plan",
            "validator",
        ),
        optional=("gate-certificate", "predecessor-record"),
    ),
    "lifecycle-integration-operation/v3": _policy(
        required=("candidate-state", "operation-input", "rail-plan", "validator"),
        optional=("gate-certificate", "predecessor-record"),
    ),
}


def dependency(
    kind: EvidenceDependencyKind,
    name: str,
    digest: str,
    *,
    algorithm: DigestAlgorithm = "sha256",
) -> EvidenceDependency:
    """Build one strictly typed edge without inferring an owning record policy."""

    return EvidenceDependency(kind=kind, name=name, algorithm=algorithm, digest=digest)


def build_evidence_dependencies(
    record_type: EvidenceRecordType,
    edges: Iterable[EvidenceDependency],
) -> EvidenceDependencies:
    """Validate an owner's declared direct inputs against its exact versioned policy."""

    ordered = tuple(sorted(edges, key=lambda edge: edge.sort_key))
    declaration = EvidenceDependencies(recordType=record_type, edges=ordered)
    require_evidence_dependencies(declaration, record_type=record_type)
    return declaration


def require_evidence_dependencies(
    declaration: EvidenceDependencies | None,
    *,
    record_type: EvidenceRecordType,
) -> EvidenceDependencies:
    """Refuse missing, extra, or wrong-version direct dependencies at currentness edges."""

    if declaration is None:
        raise EvidenceDependencyError(
            "evidence-dependencies-missing",
            f"{record_type} has no typed direct dependency declaration",
        )
    if declaration.recordType != record_type:
        raise EvidenceDependencyError(
            "evidence-dependencies-record-type-mismatch",
            f"expected {record_type}, found {declaration.recordType}",
        )
    try:
        policy = EVIDENCE_DEPENDENCY_POLICIES[record_type]
    except KeyError as exc:  # pragma: no cover - closed Literal and registry are tested together
        raise EvidenceDependencyError(
            "evidence-dependencies-policy-unsupported",
            f"no dependency policy owns {record_type}",
        ) from exc
    found = frozenset(edge.kind for edge in declaration.edges)
    if missing := policy.required - found:
        raise EvidenceDependencyError(
            "evidence-dependencies-required-missing",
            f"{record_type} is missing direct dependency kinds {sorted(missing)}",
        )
    if unsupported := found - policy.permitted:
        raise EvidenceDependencyError(
            "evidence-dependencies-undeclared-extra",
            f"{record_type} declares unowned dependency kinds {sorted(unsupported)}",
        )
    return declaration


_RECORD_EDGE_KINDS = frozenset(
    {
        "coherence-record",
        "door-generation",
        "gate-certificate",
        "predecessor-record",
        "review-record",
    }
)


def validate_evidence_dependency_graph(
    records: Mapping[str, EvidenceDependencies],
) -> None:
    """Reject a cycle among the supplied content-addressed evidence records.

    Edges to records outside ``records`` are external roots.  This lets each owning
    journal validate a bounded closure without scanning for historical files.
    """

    adjacency = {
        digest: tuple(
            edge.digest
            for edge in declaration.edges
            if edge.kind in _RECORD_EDGE_KINDS and edge.digest in records
        )
        for digest, declaration in records.items()
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(digest: str) -> None:
        if digest in visiting:
            raise EvidenceDependencyError(
                "evidence-dependency-cycle",
                f"evidence dependency cycle reaches {digest}",
            )
        if digest in visited:
            return
        visiting.add(digest)
        for successor in adjacency[digest]:
            visit(successor)
        visiting.remove(digest)
        visited.add(digest)

    for digest in sorted(records):
        visit(digest)


def canonical_sha256(value: object) -> str:
    """Content-address one JSON-compatible direct input with canonical serialization."""

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "EVIDENCE_DEPENDENCY_POLICIES",
    "EVIDENCE_DEPENDENCY_SCHEMA",
    "EVIDENCE_DEPENDENCY_VALIDATOR",
    "MAX_CURATOR_REVIEW_ASSESSMENTS",
    "MAX_CURATOR_SOURCE_CANDIDATES",
    "MAX_EVIDENCE_DEPENDENCIES",
    "EvidenceDependencies",
    "EvidenceDependency",
    "EvidenceDependencyError",
    "EvidenceDependencyKind",
    "EvidenceDependencyPolicy",
    "EvidenceRecordType",
    "build_evidence_dependencies",
    "canonical_sha256",
    "dependency",
    "require_evidence_dependencies",
    "validate_evidence_dependency_graph",
]
