"""Durable direct-landing accepted input and ledger mutation intent."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_remember.models.closeout.input import EffectiveCloseoutInput
from agents_remember.models.lifecycles.mutation_evidence import GitMutationSnapshot
from agents_remember.models.lifecycles.policy import GatePolicyRuleSnapshot


class DirectLandingOperationInput(BaseModel):
    """Every immutable fact accepted before a branch-direct mutation may start."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["direct-landing"] = "direct-landing"
    configPath: str
    contractPath: str
    effectiveInput: EffectiveCloseoutInput
    approvalNote: str
    gatePolicy: list[GatePolicyRuleSnapshot] = Field(default_factory=list)
    codeCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    codeTree: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    candidateTree: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memoryRepository: str
    memoryBranch: str
    memoryRef: str = Field(pattern=r"^refs/heads/.+$")
    memoryBefore: GitMutationSnapshot
    ledgerPath: str
    ledgerBeforeText: str = Field(max_length=2_000_000)
    ledgerBeforeSha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _accepted_direct_plan_is_exact(self) -> DirectLandingOperationInput:
        if self.effectiveInput.route != "direct-landing":
            raise ValueError("direct landing requires the direct-landing effective input")
        if not self.approvalNote.strip():
            raise ValueError("direct landing requires accepted approval intent")
        if hashlib.sha256(self.ledgerBeforeText.encode("utf-8")).hexdigest() != (
            self.ledgerBeforeSha256
        ):
            raise ValueError("direct landing ledger input digest does not match its bytes")
        return self


class DirectLandingLedgerIntent(BaseModel):
    """Exact ledger bytes and mapping intended before the ledger Git command."""

    model_config = ConfigDict(extra="forbid")

    codeCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memoryCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    beforeText: str = Field(max_length=2_000_000)
    beforeSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    intendedText: str = Field(max_length=2_000_000)
    intendedSha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _ledger_bytes_match_digests(self) -> DirectLandingLedgerIntent:
        if hashlib.sha256(self.beforeText.encode("utf-8")).hexdigest() != self.beforeSha256:
            raise ValueError("direct landing ledger before digest does not match its bytes")
        if hashlib.sha256(self.intendedText.encode("utf-8")).hexdigest() != self.intendedSha256:
            raise ValueError("direct landing intended ledger digest does not match its bytes")
        return self
