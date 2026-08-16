"""Wire and durable vocabularies for task-addressed lifecycle operations."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_remember.models.base import StrictResponseModel

LifecycleOperationKind = Literal["closeout", "integrate"]
IntegrateStrategy = Literal["ff-only", "replay"]
LifecycleOperationStatus = Literal[
    "queued", "running", "input-required", "completed", "failed", "cancelled"
]
LifecycleOperationPhase = Literal[
    "queued",
    "preflight",
    "memory-preflight",
    "quality",
    "approval-claim",
    "recovering-after-claim",
    "code-commit",
    "memory-refresh",
    "memory-commit",
    "ledger-commit",
    "integration-replay",
    "integration-quality",
    "source-merge",
    "contract-finalization",
    "completed",
    "failed",
    "cancelled",
]


class LifecycleOperationRecoveryCommits(BaseModel):
    """Exact irreversible outputs persisted before contract finalization."""

    model_config = ConfigDict(extra="forbid")

    codeCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memoryContentCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    ledgerCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")


class IntegrationConflictTransaction(BaseModel):
    """A durable, non-mutating handoff back to the exact leaf worktree."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["resolution-required"] = "resolution-required"
    codeReplayRequired: bool
    memoryReplayRequired: bool
    codeSourceRef: str = Field(pattern=r"^refs/heads/.+$", max_length=4096)
    codeSourceCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    codeCandidateCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memorySourceRef: str = Field(default="", pattern=r"^$|^refs/heads/.+$", max_length=4096)
    memorySourceCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    memoryContentCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    ledgerCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    codeWorktree: str = Field(min_length=1, max_length=4096)
    memoryWorktree: str = Field(default="", max_length=4096)
    resolutionOwner: Literal["leaf-closeout"] = "leaf-closeout"


class IntegrationOperationAuthority(BaseModel):
    """Exact source tips and closed candidate accepted by one integration operation."""

    model_config = ConfigDict(extra="forbid")

    targetKind: Literal["sprint-super", "atomic-integration"]
    codeRepository: str = Field(min_length=1, max_length=4096)
    codeSourceBranch: str = Field(min_length=1, max_length=4096)
    codeSourceRef: str = Field(pattern=r"^refs/heads/.+$", max_length=4096)
    codeSourceCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    codeCandidateCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memoryRepository: str = Field(default="", max_length=4096)
    memorySourceBranch: str = Field(default="", max_length=4096)
    memorySourceRef: str = Field(default="", pattern=r"^$|^refs/heads/.+$", max_length=4096)
    memorySourceCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    memoryContentCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    ledgerCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    conflictTransaction: IntegrationConflictTransaction | None = None


class GatePolicyRuleSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    delegatedRole: str | None = None
    requireReviewerVerdict: bool = False


class CloseoutOperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["closeout"] = "closeout"
    configPath: str
    contractPath: str
    codeCommitMessage: str
    memoryCommitMessage: str = ""
    ledgerCommitMessage: str = ""
    approvalNote: str
    gatePolicy: list[GatePolicyRuleSnapshot] = Field(default_factory=list)


class IntegrateOperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["integrate"] = "integrate"
    configPath: str
    contractPath: str
    strategy: IntegrateStrategy = "ff-only"
    ledgerCommitMessage: str = ""
    gatePolicy: list[GatePolicyRuleSnapshot] = Field(default_factory=list)
    autoCompleteSeats: bool = True


LifecycleOperationInput = Annotated[
    CloseoutOperationInput | IntegrateOperationInput, Field(discriminator="kind")
]


class LifecycleOperationRecord(BaseModel):
    """The validated, internal operation snapshot stored in an enclosure."""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["2.0"] = "2.0"
    taskId: str
    taskName: str
    contractPath: str
    operationKind: LifecycleOperationKind
    candidateState: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidateTree: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    operationKey: str = Field(pattern=r"^[0-9a-f]{64}$")
    integrationAuthority: IntegrationOperationAuthority | None = None
    input: LifecycleOperationInput
    status: LifecycleOperationStatus
    phase: LifecycleOperationPhase
    queuedAt: str
    startedAt: str | None = None
    heartbeatAt: str | None = None
    finishedAt: str | None = None
    currentCommand: str = ""
    reportPath: str
    result: dict[str, Any] | None = None
    failure: str | None = None
    guidance: str | None = None
    cancelRequested: bool = False
    irreversibleBoundaryEntered: bool = False
    approvalClaimed: bool = False
    recoveryCommits: LifecycleOperationRecoveryCommits | None = None
    attempt: int = Field(default=1, ge=1)
    workerPid: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _require_altitude_authority(self) -> LifecycleOperationRecord:
        if self.operationKind == "integrate" and self.integrationAuthority is None:
            raise ValueError("integrate operation requires exact integrationAuthority")
        if self.operationKind == "closeout" and self.integrationAuthority is not None:
            raise ValueError("closeout operation has no integrationAuthority")
        return self


class LifecycleOperationProjection(StrictResponseModel):
    """Public task-addressed view; process and resume identities never cross the wire."""

    kind: LifecycleOperationKind
    status: LifecycleOperationStatus
    phase: LifecycleOperationPhase
    startedAt: str | None = None
    heartbeatAt: str | None = None
    finishedAt: str | None = None
    elapsedSeconds: float
    currentCommand: str = ""
    reportPath: str
    result: dict[str, Any] | None = None
    failure: str | None = None
    guidance: str | None = None
    cancellable: bool = False
