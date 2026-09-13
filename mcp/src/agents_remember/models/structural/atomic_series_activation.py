"""Strict durable vocabulary for contract-scoped atomic-series activation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.models.task_document_ref import TaskDocumentRef

AtomicSeriesActivationState = Literal["vacant", "reconciling", "active"]
AtomicSeriesSelectionState = Literal["reconciling", "active"]
AtomicSeriesObservedState = Literal["vacant", "unreadable", "reconciling", "active"]


class AtomicSeriesActivationRecord(BaseModel):
    """The one replace-in-place activation snapshot for a canonical series contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["2.0"] = "2.0"
    contractFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    selectedMaster: TaskDocumentRef
    contractPath: str = Field(min_length=1, max_length=4096)
    state: AtomicSeriesActivationState
    revision: int = Field(ge=1)
    selectedAt: str = Field(min_length=1, max_length=128)


class AtomicSeriesActivationArchiveEvidence(BaseModel):
    """Evidence preserved when an exact selecting operation repairs a corrupt snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["2.0"] = "2.0"
    contractFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    activationPath: str = Field(min_length=1, max_length=4096)
    archiveKind: Literal["raw-bytes", "opaque-entry", "absence"]
    snapshotPath: str | None = Field(default=None, max_length=4096)
    snapshotSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshotSize: int = Field(ge=0)
    errorType: str = Field(min_length=1, max_length=256)
    detail: str = Field(min_length=1, max_length=8192)
    replacementMaster: TaskDocumentRef
    archivedAt: str = Field(min_length=1, max_length=128)
