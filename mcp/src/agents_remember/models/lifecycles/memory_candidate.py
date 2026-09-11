"""Exact lifecycle-owned code/memory pair identity."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MemoryCandidatePairIdentity(BaseModel):
    """Every contract cell that selects one worktree-backed memory candidate pair.

    ``contractDigest`` hashes the canonical pair-authority projection represented by
    this model, excluding the digest itself. Unrelated mutable lifecycle cells do not
    invalidate a pair; every path, branch, or base change does.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["ar-memory-candidate-pair/v1"] = "ar-memory-candidate-pair/v1"
    repoId: str = Field(min_length=1, max_length=4096)
    contractPath: str = Field(min_length=1, max_length=8192)
    contractDigest: str = Field(pattern=r"^[0-9a-f]{64}$")
    codeRoot: str = Field(min_length=1, max_length=8192)
    memoryRoot: str = Field(min_length=1, max_length=8192)
    codeSourceBranch: str = Field(min_length=1, max_length=4096)
    codeWorkBranch: str = Field(min_length=1, max_length=4096)
    codeBaseCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memorySourceBranch: str = Field(min_length=1, max_length=4096)
    memoryWorkBranch: str = Field(min_length=1, max_length=4096)
    memoryBaseCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    onboardingRoot: str = Field(min_length=1, max_length=8192)
    ledgerPath: str = Field(min_length=1, max_length=8192)


__all__ = ["MemoryCandidatePairIdentity"]
