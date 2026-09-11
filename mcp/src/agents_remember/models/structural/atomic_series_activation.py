"""Strict durable vocabulary for source-pair-scoped atomic-series activation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents_remember.models.task_document_ref import TaskDocumentRef

AtomicSeriesActivationState = Literal["vacant", "reconciling", "active"]
AtomicSeriesSelectionState = Literal["reconciling", "active"]
AtomicSeriesObservedState = Literal["vacant", "unreadable", "reconciling", "active"]


class AtomicSeriesSourceRef(BaseModel):
    """One exact normalized repository/source-branch identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repositoryIdentity: str = Field(min_length=1, max_length=4096)
    sourceBranch: str = Field(min_length=1, max_length=4096)

    @field_validator("repositoryIdentity", "sourceBranch")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("atomic-series source identity must not be blank")
        return cleaned


class AtomicSeriesSourcePair(BaseModel):
    """The protected source pair whose atomic work admits one selected master."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: AtomicSeriesSourceRef
    memory: AtomicSeriesSourceRef | None = None


class AtomicSeriesActivationRecord(BaseModel):
    """The one replace-in-place selection snapshot for a source pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["1.0"] = "1.0"
    sourcePairFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    sourcePair: AtomicSeriesSourcePair
    selectedMaster: TaskDocumentRef
    contractPath: str = Field(min_length=1, max_length=4096)
    state: AtomicSeriesActivationState
    revision: int = Field(ge=1)
    selectedAt: str = Field(min_length=1, max_length=128)


class AtomicSeriesActivationArchiveEvidence(BaseModel):
    """Evidence preserved when an exact selecting operation repairs a corrupt snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["1.0"] = "1.0"
    sourcePairFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    activationPath: str = Field(min_length=1, max_length=4096)
    archiveKind: Literal["raw-bytes", "opaque-entry", "absence"]
    snapshotPath: str | None = Field(default=None, max_length=4096)
    snapshotSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshotSize: int = Field(ge=0)
    errorType: str = Field(min_length=1, max_length=256)
    detail: str = Field(min_length=1, max_length=8192)
    replacementMaster: TaskDocumentRef
    archivedAt: str = Field(min_length=1, max_length=128)
