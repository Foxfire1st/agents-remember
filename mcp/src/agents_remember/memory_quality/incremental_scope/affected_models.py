"""Immutable contracts for R07 affected-closure execution and reuse."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from .models import ScopeEdge, _StrictModel, canonical_digest

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Hash = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
UnitStatus = Literal["pass", "fail", "blocked"]


class ClosureDependencyIdentity(_StrictModel):
    """One exact transitive input to an affected checker unit."""

    nodeId: str = Field(min_length=1)
    contentDigest: Sha256
    authorityNamespace: str = Field(min_length=1)
    validatorVersion: str = Field(min_length=1)


class AffectedUnitPlan(_StrictModel):
    """One checker/document unit whose identity permits only dependency-exact reuse."""

    checker: str = Field(min_length=1)
    document: str = Field(min_length=1)
    node: ClosureDependencyIdentity
    dependencies: tuple[ClosureDependencyIdentity, ...] = Field(max_length=16384)
    dependencyEdges: tuple[ScopeEdge, ...] = Field(max_length=16384)
    extractorVersion: str = Field(min_length=1)
    validatorVersion: str = Field(min_length=1)
    runtimeVersion: str = Field(min_length=1)
    correctiveOwner: str = Field(min_length=1)
    checkerRegistryVersion: Sha256
    executionRegistryVersion: Sha256
    sourceIndexSnapshot: Sha256
    codeTree: Hash
    unitDigest: Sha256

    @field_validator("document")
    @classmethod
    def _canonical_document(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or path.suffix != ".md"
            or path.as_posix() != value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("affected-unit document must be one canonical relative .md path")
        return value

    @model_validator(mode="after")
    def _require_canonical_unit(self) -> Self:
        dependency_ids = tuple(item.nodeId for item in self.dependencies)
        if dependency_ids != tuple(sorted(set(dependency_ids))):
            raise ValueError("affected-unit dependencies must be unique and canonical")
        edge_keys = tuple(
            (item.source, item.target, item.edgeClass) for item in self.dependencyEdges
        )
        if edge_keys != tuple(sorted(set(edge_keys))):
            raise ValueError("affected-unit dependency edges must be unique and canonical")
        if self.node.nodeId in set(dependency_ids):
            raise ValueError("affected-unit dependencies must exclude the checked node")
        payload = self.model_dump(mode="json", by_alias=True, exclude={"unitDigest"})
        if self.unitDigest != canonical_digest(payload):
            raise ValueError("affected-unit digest does not match its exact inputs")
        return self


class AffectedUnitResult(_StrictModel):
    """Content-addressed terminal result for one exact affected unit."""

    schemaVersion: Literal["memory-affected-unit-result/v1"] = "memory-affected-unit-result/v1"
    unit: AffectedUnitPlan
    status: UnitStatus
    code: str = Field(min_length=1)
    correctiveOwner: str = Field(min_length=1)
    filesChecked: int = Field(ge=0)
    findingCount: int = Field(ge=0)
    evidence: JsonValue
    evidenceDigest: Sha256
    resultDigest: Sha256

    @model_validator(mode="after")
    def _verify_result(self) -> Self:
        if (self.status == "pass" and self.findingCount != 0) or (
            self.status == "fail" and self.findingCount == 0
        ):
            raise ValueError("affected-unit status must match its finding count")
        if (self.status == "blocked" and self.filesChecked != 0) or (
            self.status != "blocked" and self.filesChecked != 1
        ):
            raise ValueError("affected-unit status must match its selected-document count")
        if self.evidenceDigest != canonical_digest(self.evidence):
            raise ValueError("affected-unit evidence digest does not match its content")
        payload = self.model_dump(mode="json", by_alias=True, exclude={"resultDigest"})
        if self.resultDigest != canonical_digest(payload):
            raise ValueError("affected-unit result digest does not match its content")
        return self


__all__ = ["AffectedUnitPlan", "AffectedUnitResult", "ClosureDependencyIdentity", "UnitStatus"]
