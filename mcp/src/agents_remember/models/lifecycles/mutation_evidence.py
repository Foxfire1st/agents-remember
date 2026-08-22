"""Durable evidence for one closeout Git mutation attempt."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CloseoutMutationLeg = Literal["code", "memory", "ledger"]
MutationEvidenceState = Literal[
    "pre-mutation",
    "mutation-intent",
    "reconciled-unchanged",
    "commit-proven",
]


class GitMutationSnapshot(BaseModel):
    """Bounded Git facts needed to attribute a launched commit command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    headRef: str = Field(pattern=r"^refs/heads/[^\s]+$")
    head: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    headTree: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    refLogFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    indexTree: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    candidateTree: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    statusFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class GitMutationEvidence(BaseModel):
    """Monotonic mutation intent and output proof for one enabled leg."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    leg: CloseoutMutationLeg
    repository: str
    state: MutationEvidenceState = "pre-mutation"
    before: GitMutationSnapshot | None = None
    observed: GitMutationSnapshot | None = None
    expectedOutputTree: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")

    @model_validator(mode="after")
    def _require_state_evidence(self) -> GitMutationEvidence:
        if self.state != "commit-proven" and self.commit is not None:
            raise ValueError(f"{self.state} evidence cannot name a commit")
        if self.state == "pre-mutation":
            _require_empty_pre_mutation(self)
            return self
        if self.before is None:
            raise ValueError(f"{self.state} evidence requires the pre-command Git snapshot")
        if self.state == "commit-proven":
            _require_commit_proof(self)
        elif self.state == "reconciled-unchanged":
            if self.observed != self.before:
                raise ValueError("reconciled-unchanged evidence requires exact observed pre-state")
        elif self.observed == self.before:
            raise ValueError("an exactly unchanged observation must be reconciled-unchanged")
        return self


def _require_empty_pre_mutation(evidence: GitMutationEvidence) -> None:
    if any(
        value is not None
        for value in (
            evidence.before,
            evidence.observed,
            evidence.expectedOutputTree,
            evidence.commit,
        )
    ):
        raise ValueError("pre-mutation evidence cannot claim Git attempt facts")


def _require_commit_proof(evidence: GitMutationEvidence) -> None:
    observed = evidence.observed
    before = evidence.before
    expected_tree = evidence.expectedOutputTree
    commit = evidence.commit
    if observed is None or before is None or expected_tree is None or commit is None:
        raise ValueError("commit-proven evidence requires observed output tree and commit")
    if (
        observed.headRef != before.headRef
        or observed.head != commit
        or observed.headTree != expected_tree
    ):
        raise ValueError("commit-proven evidence must bind the observed ref and tree")
