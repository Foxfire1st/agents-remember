"""Wire and durable vocabularies for task-addressed lifecycle operations."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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

    schemaVersion: Literal["1.0"] = "1.0"
    taskId: str
    taskName: str
    contractPath: str
    operationKind: LifecycleOperationKind
    candidateState: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidateTree: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    operationKey: str = Field(pattern=r"^[0-9a-f]{64}$")
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
