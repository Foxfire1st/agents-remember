"""Exact historic schema-1 envelope and durable archive receipt models."""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LegacyGateRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    delegatedRole: str | None = None
    requireReviewerVerdict: bool = False


class LegacyRecoveryCommits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codeCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memoryContentCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    ledgerCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")


class LegacyCloseoutInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["closeout"] = "closeout"
    configPath: str
    contractPath: str
    codeCommitMessage: str
    memoryCommitMessage: str = ""
    ledgerCommitMessage: str = ""
    approvalNote: str
    gatePolicy: list[LegacyGateRule] = Field(default_factory=list)


class LegacySchemaOneRecord(BaseModel):
    """The exact historic schema-1 envelope; never imported by the normal store."""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["1.0"] = "1.0"
    taskId: str
    taskName: str
    contractPath: str
    operationKind: Literal["closeout", "integrate"]
    candidateState: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidateTree: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    operationKey: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Terminal archive does not interpret historic input: exact contract/Git output,
    # absent worker authority, and preserved original bytes are its complete proof.
    # Migration separately validates this cell as LegacyCloseoutInput before use.
    input: dict[str, Any]
    status: Literal["queued", "running", "input-required", "completed", "failed", "cancelled"]
    phase: str
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
    recoveryCommits: LegacyRecoveryCommits | None = None
    attempt: int = Field(default=1, ge=1)
    workerPid: int | None = Field(default=None, ge=1)


class LegacyArchive(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["legacy-archive-1.0"] = "legacy-archive-1.0"
    originalBytesBase64: str
    originalSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operationKind: Literal["closeout", "integrate"]
    taskId: str
    taskName: str
    contractPath: str
    status: str
    phase: str
    auditReason: str
    terminalEvidence: dict[str, object]
    archivedAt: str

    @model_validator(mode="after")
    def _require_preserved_original(self) -> LegacyArchive:
        try:
            original = base64.b64decode(self.originalBytesBase64, validate=True)
        except ValueError as exc:
            raise ValueError("legacy archive contains invalid original bytes") from exc
        if hashlib.sha256(original).hexdigest() != self.originalSha256:
            raise ValueError("legacy archive digest does not match original bytes")
        return self
