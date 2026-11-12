"""Typed contracts for the final full memory-coherence certification (Gate 5)."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"
_VERSION_PATTERN = (
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)

FinalItemStatus = Literal["pass", "fail", "blocked", "not-applicable"]


class FinalCertificationModel(BaseModel):
    """Closed immutable base for one final-certification contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FinalCatalogItemIdentity(FinalCertificationModel):
    """One closed final-catalog member; identity never implies execution."""

    itemId: str = Field(pattern=_ID_PATTERN, max_length=256)
    version: str = Field(pattern=_VERSION_PATTERN)

    @property
    def key(self) -> tuple[str, str]:
        return (self.itemId, self.version)


class FinalCatalogItemResult(FinalCertificationModel):
    """One typed Gate-5 catalog result with its content-addressed subresult."""

    item: FinalCatalogItemIdentity
    status: FinalItemStatus
    findingCount: int = Field(ge=0, le=1_000_000_000)
    subresultDigest: str = Field(pattern=_DIGEST_PATTERN)
    blockedBy: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    reference: str | None = Field(default=None, max_length=16384)

    @model_validator(mode="after")
    def _require_status_shape(self) -> Self:
        if (self.status == "blocked") != bool(self.blockedBy):
            raise ValueError("a blocked final-catalog item requires a nonempty blockedBy")
        if self.status == "pass" and self.findingCount != 0:
            raise ValueError("a passing final-catalog item cannot carry findings")
        if self.status == "not-applicable" and self.findingCount != 0:
            raise ValueError("a not-applicable final-catalog item cannot carry findings")
        return self


__all__ = ["FinalCatalogItemIdentity", "FinalCatalogItemResult", "FinalItemStatus"]
