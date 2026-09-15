"""Durable direct-landing accepted code and memory input."""

from __future__ import annotations

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

    @model_validator(mode="after")
    def _accepted_direct_plan_is_exact(self) -> DirectLandingOperationInput:
        if self.effectiveInput.route != "direct-landing":
            raise ValueError("direct landing requires the direct-landing effective input")
        if not self.approvalNote.strip():
            raise ValueError("direct landing requires accepted approval intent")
        return self
